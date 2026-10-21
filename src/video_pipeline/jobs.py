import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from uuid import UUID, uuid4

import procrastinate
from procrastinate.jobs import Job as QueueJob
from procrastinate.types import JSONDict
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, ConfigDict, Field, model_validator

EDIT_TASK = "video_pipeline.edit"
EDIT_QUEUE = "video"
MAX_ATTEMPTS = 3
JobStatus = Literal["queued", "running", "succeeded", "failed"]


def database_url() -> str:
    value = os.environ.get("VIDEO_PIPELINE_DATABASE_URL")
    if not value:
        raise ValueError("Set VIDEO_PIPELINE_DATABASE_URL to the local PostgreSQL URL.")
    return value


class TranscodeOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["transcode"]
    height: Annotated[int, Field(gt=0, multiple_of=2)]


class ThumbnailOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["thumbnail"]


class ClipOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["clip"]
    start_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    end_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Clip end must be after its start.")
        return self

    def validate_source_duration(self, duration_seconds: float | None) -> None:
        if duration_seconds is None:
            raise ValueError("Clipping requires a video with a known duration.")
        if self.end_seconds > duration_seconds:
            raise ValueError("Clip end must not exceed the video duration.")


ProcessingOperation = Annotated[
    TranscodeOperation | ThumbnailOperation | ClipOperation, Field(discriminator="type")
]


class JobProgress(BaseModel):
    stage: Literal[
        "queued", "inspecting", "transcoding", "thumbnail", "clipping", "completed"
    ] = "queued"
    processed_seconds: float | None = None
    duration_seconds: float | None = None
    percent: float | None = None


class JobOutput(BaseModel):
    filename: str
    media_type: str
    view_url: str
    download_url: str


class JobRecord(BaseModel):
    id: UUID
    video_id: UUID
    operation: ProcessingOperation
    status: JobStatus = "queued"
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    progress: JobProgress = Field(default_factory=JobProgress)
    output: JobOutput | None = None
    error: str | None = None
    attempts: int = 0
    max_attempts: int = MAX_ATTEMPTS


class JobSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_id: UUID
    operation: ProcessingOperation


class BatchJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobs: list[JobSubmission] = Field(min_length=1)


class JobStore:
    def __init__(self, database_path: Path, conninfo: str) -> None:
        self.database_path = database_path
        self.outputs_dir = database_path.parent / "jobs"
        self.pool = ConnectionPool[Connection[dict[str, Any]]](
            conninfo,
            kwargs={"row_factory": dict_row},
            min_size=1,
            max_size=8,
            open=False,
        )
        self.queue = procrastinate.App(
            connector=procrastinate.SyncPsycopgConnector(conninfo=conninfo)
        )

    def open(self) -> None:
        self.pool.open(wait=True, timeout=10)
        self.queue.open(self.pool)

    def close(self) -> None:
        self.queue.close()
        self.pool.close()

    def require_schema(self) -> None:
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT to_regclass('video_jobs') AS table_name"
            ).fetchall()[0]
        if row["table_name"] is None:
            raise ValueError(
                "Initialize the job database with 'video-pipeline init-jobs'."
            )

    def initialize(self) -> int:
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT to_regclass('procrastinate_jobs') AS table_name"
            ).fetchall()[0]
        if row["table_name"] is None:
            self.queue.schema_manager.apply_schema()

        with self.pool.connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS video_jobs (
                    id UUID PRIMARY KEY,
                    task_id BIGINT UNIQUE REFERENCES procrastinate_jobs(id),
                    attempt_offset INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL,
                    record JSONB NOT NULL
                )
                """
            )
        return self.import_legacy_jobs()

    def create(self, video_id: UUID, operation: ProcessingOperation) -> JobRecord:
        return self.create_many(
            [JobSubmission(video_id=video_id, operation=operation)]
        )[0]

    def create_many(self, submissions: list[JobSubmission]) -> list[JobRecord]:
        records = [
            JobRecord(
                id=uuid4(),
                video_id=item.video_id,
                operation=item.operation,
                created_at=datetime.now(UTC),
            )
            for item in submissions
        ]
        with self.pool.connection() as connection:
            payloads: list[JSONDict] = [{"job_id": str(job.id)} for job in records]
            task_ids = self.queue.configure_task(
                EDIT_TASK, queue=EDIT_QUEUE, connection=connection
            ).batch_defer(*payloads)
            task_rows = connection.execute(
                """
                SELECT id, args->>'job_id' AS job_id
                FROM procrastinate_jobs WHERE id = ANY(%s)
                """,
                (task_ids,),
            ).fetchall()
            tasks_by_job = {UUID(row["job_id"]): row["id"] for row in task_rows}
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO video_jobs (id, task_id, created_at, record)
                    VALUES (%s, %s, %s, %s)
                    """,
                    [
                        (
                            job.id,
                            tasks_by_job[job.id],
                            job.created_at,
                            Jsonb(job.model_dump(mode="json")),
                        )
                        for job in records
                    ],
                )
        return records

    def get(self, job_id: UUID) -> JobRecord | None:
        with self.pool.connection() as connection:
            row = connection.execute(
                """
                SELECT v.record, v.attempt_offset, p.status, p.attempts
                FROM video_jobs v
                LEFT JOIN procrastinate_jobs p ON p.id = v.task_id
                WHERE v.id = %s
                """,
                (job_id,),
            ).fetchone()
        return self._record(row) if row is not None else None

    def list(self) -> list[JobRecord]:
        with self.pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT v.record, v.attempt_offset, p.status, p.attempts
                FROM video_jobs v
                LEFT JOIN procrastinate_jobs p ON p.id = v.task_id
                ORDER BY v.created_at, v.id
                """
            ).fetchall()
        return [self._record(row) for row in rows]

    def save(self, job: JobRecord) -> None:
        with self.pool.connection() as connection:
            connection.execute(
                "UPDATE video_jobs SET record = %s WHERE id = %s",
                (Jsonb(job.model_dump(mode="json")), job.id),
            )

    def is_current_attempt(self, task: QueueJob) -> bool:
        with self.pool.connection() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM procrastinate_jobs
                WHERE id = %s AND status = 'doing' AND attempts = %s
                    AND worker_id IS NOT DISTINCT FROM %s
                """,
                (task.id, task.attempts, task.worker_id),
            ).fetchone()
        return row is not None

    @staticmethod
    def _record(row: dict[str, Any]) -> JobRecord:
        job = JobRecord.model_validate(row["record"])
        if row["status"] is not None:
            saved_attempts = job.attempts
            status: dict[str, JobStatus] = {
                "todo": "queued",
                "doing": "running",
                "succeeded": "succeeded",
                "failed": "failed",
                "cancelled": "failed",
                "aborting": "running",
                "aborted": "failed",
            }
            job.status = status[row["status"]]
            job.attempts = row["attempts"] + row["attempt_offset"]
            if job.status == "running":
                job.attempts += 1
                job.finished_at = None
                if saved_attempts != job.attempts:
                    job.progress = JobProgress(stage="inspecting")
                    job.started_at = None
                    job.error = None
            if job.status == "queued":
                job.progress = JobProgress()
                job.error = None
                job.started_at = None
                job.finished_at = None
            if job.status != "succeeded":
                job.output = None
        return job

    def import_legacy_jobs(self) -> int:
        with closing(
            sqlite3.connect(self.database_path.as_uri() + "?mode=ro", uri=True)
        ) as legacy:
            exists = legacy.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
            ).fetchone()
            if exists is None:
                return 0
            rows = legacy.execute("SELECT record FROM jobs ORDER BY rowid").fetchall()

        imported = 0
        with self.pool.connection() as connection:
            for row in rows:
                job = JobRecord.model_validate_json(row[0])
                job.attempts = max(job.attempts, int(job.started_at is not None))
                unfinished = job.status in ("queued", "running")
                if unfinished and job.attempts >= job.max_attempts:
                    job.status = "failed"
                    job.error = "The three-attempt limit was reached before migration."
                    job.finished_at = datetime.now(UTC)
                    job.output = None
                    unfinished = False
                elif unfinished:
                    job.status = "queued"
                    job.progress = JobProgress()
                    job.error = None
                    job.finished_at = None
                inserted = connection.execute(
                    """
                    INSERT INTO video_jobs (id, attempt_offset, created_at, record)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    RETURNING id
                    """,
                    (
                        job.id,
                        job.attempts,
                        job.created_at,
                        Jsonb(job.model_dump(mode="json")),
                    ),
                ).fetchone()
                if inserted is None:
                    continue
                if unfinished:
                    task_id = self.queue.configure_task(
                        EDIT_TASK, queue=EDIT_QUEUE, connection=connection
                    ).defer(job_id=str(job.id))
                    connection.execute(
                        "UPDATE video_jobs SET task_id = %s WHERE id = %s",
                        (task_id, job.id),
                    )
                imported += 1
        return imported

    def output_path(self, job_id: UUID, filename: str) -> Path:
        return self.outputs_dir / str(job_id) / filename
