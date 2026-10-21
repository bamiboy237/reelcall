import asyncio
import logging
import os
import shutil
import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID

import procrastinate
from filelock import Timeout, UnixFileLock
from procrastinate import JobContext, exceptions
from procrastinate.jobs import Job as QueueJob
from procrastinate.jobs import Status
from procrastinate.middleware import SyncCallNext
from procrastinate.worker import Worker as QueueWorker

from video_pipeline.embeddings import Embedder
from video_pipeline.inspection import inspect_video
from video_pipeline.jobs import (
    EDIT_QUEUE,
    EDIT_TASK,
    INDEX_QUEUE,
    INDEX_TASK,
    ClipOperation,
    IndexOperation,
    JobOutput,
    JobProgress,
    JobStore,
    ThumbnailOperation,
    TranscodeOperation,
    database_url,
)
from video_pipeline.processing import clip_video, extract_thumbnail, transcode_video
from video_pipeline.search import IndexSettings, SearchStore
from video_pipeline.storage import VideoStore

RECOVERY_TASK = "video_pipeline.recover_stalled"


class ShutdownRetry(procrastinate.BaseRetryStrategy):
    """Include imported attempts when Procrastinate retries a shutdown abort."""

    def __init__(self, jobs: JobStore) -> None:
        self.jobs = jobs

    def get_retry_decision(
        self, *, exception: BaseException, job: QueueJob
    ) -> procrastinate.RetryDecision | None:
        if not isinstance(exception, exceptions.JobAborted):
            return None
        record = self.jobs.get(UUID(str(job.task_kwargs["job_id"])))
        if record is not None and record.attempts < record.max_attempts:
            return procrastinate.RetryDecision()
        return None


def attempt_lock(jobs: JobStore, job_id: UUID) -> UnixFileLock:
    directory = jobs.outputs_dir / str(job_id)
    directory.mkdir(parents=True, exist_ok=True)
    return UnixFileLock(directory / ".attempt.lock")


def remove_abandoned_attempts(jobs: JobStore, job_id: UUID) -> None:
    for directory in (jobs.outputs_dir / str(job_id)).glob("attempt-*"):
        shutil.rmtree(directory)


def process_job(
    context: JobContext,
    job_id: str,
    videos: VideoStore,
    jobs: JobStore,
    search: SearchStore,
    embedder: Embedder,
) -> None:
    job = jobs.get(UUID(job_id))
    if job is None:
        raise ValueError(f"Job metadata not found: {job_id}")
    job.started_at = datetime.now(UTC)
    job.finished_at = None
    job.error = None
    job.output = None
    job.progress = JobProgress(stage="inspecting")
    jobs.save(job)

    source = inspect_video(videos.original_path(job.video_id))
    output_dir = jobs.outputs_dir / str(job.id)

    def save_progress(seconds: float) -> None:
        if context.should_abort():
            raise exceptions.JobAborted("Worker shutdown requested.")
        job.progress.processed_seconds = seconds
        duration = job.progress.duration_seconds
        if duration is not None and duration > 0:
            job.progress.percent = round(min(100, seconds / duration * 100), 2)
        jobs.save(job)

    with TemporaryDirectory(dir=output_dir, prefix=f"attempt-{job.attempts}-") as temp:
        if isinstance(job.operation, TranscodeOperation):
            job.progress = JobProgress(
                stage="transcoding", duration_seconds=source.duration_seconds
            )
            jobs.save(job)

            filename = "video.mp4"
            media_type = "video/mp4"
            transcode_video(
                source,
                job.operation.height,
                Path(temp) / filename,
                save_progress,
            )
        elif isinstance(job.operation, ThumbnailOperation):
            job.progress = JobProgress(stage="thumbnail")
            jobs.save(job)
            filename = "thumbnail.jpg"
            media_type = "image/jpeg"
            extract_thumbnail(source.path, Path(temp) / filename)
        elif isinstance(job.operation, ClipOperation):
            job.operation.validate_source_duration(source.duration_seconds)
            duration = job.operation.end_seconds - job.operation.start_seconds
            job.progress = JobProgress(stage="clipping", duration_seconds=duration)
            jobs.save(job)
            filename = "clip.mp4"
            media_type = "video/mp4"
            clip_video(
                source,
                job.operation.start_seconds,
                job.operation.end_seconds,
                Path(temp) / filename,
                save_progress,
            )
        elif isinstance(job.operation, IndexOperation):
            video = videos.get(job.video_id)
            if video is None:
                raise ValueError(f"Video metadata not found: {job.video_id}")
            job.progress = JobProgress(
                stage="indexing", duration_seconds=source.duration_seconds
            )
            jobs.save(job)
            record = search.index(
                video,
                videos,
                IndexSettings(
                    window_seconds=job.operation.window_seconds,
                    stride_seconds=job.operation.stride_seconds,
                ),
                embedder,
                save_progress,
            )
            filename = "index.json"
            media_type = "application/json"
            (Path(temp) / filename).write_text(
                record.model_dump_json(indent=2) + "\n", encoding="utf-8"
            )

        if context.should_abort():
            raise exceptions.JobAborted("Worker shutdown requested.")
        (Path(temp) / filename).replace(jobs.output_path(job.id, filename))

    job.output = JobOutput(
        filename=filename,
        media_type=media_type,
        view_url=f"/jobs/{job.id}/output",
        download_url=f"/jobs/{job.id}/download",
    )
    job.status = "succeeded"
    job.progress.stage = "completed"
    job.progress.percent = 100
    job.finished_at = datetime.now(UTC)
    jobs.save(job)


def create_worker_app(
    videos: VideoStore, jobs: JobStore, search: SearchStore, embedder: Embedder
) -> procrastinate.App:
    app = procrastinate.App(
        connector=procrastinate.PsycopgConnector(conninfo=database_url())
    )

    def record_failures(
        call_next: SyncCallNext, context: JobContext, worker: QueueWorker
    ) -> Any:
        job_id = UUID(str(context.job.task_kwargs["job_id"]))
        try:
            with attempt_lock(jobs, job_id):
                remove_abandoned_attempts(jobs, job_id)
                return call_next()
        except Exception as error:
            job = jobs.get(job_id)
            if job is not None:
                job.error = (
                    error.stderr.strip()
                    if isinstance(error, subprocess.CalledProcessError)
                    else f"{type(error).__name__}: {error}"
                )
                job.finished_at = datetime.now(UTC)
                jobs.save(job)
            raise

    @app.task(
        name=EDIT_TASK,
        queue=EDIT_QUEUE,
        pass_context=True,
        task_middleware=[record_failures],
        retry=ShutdownRetry(jobs),
    )
    def edit(context: JobContext, job_id: str) -> None:
        process_job(context, job_id, videos, jobs, search, embedder)

    @app.task(
        name=INDEX_TASK,
        queue=INDEX_QUEUE,
        pass_context=True,
        task_middleware=[record_failures],
        retry=ShutdownRetry(jobs),
    )
    def index(context: JobContext, job_id: str) -> None:
        process_job(context, job_id, videos, jobs, search, embedder)

    @app.periodic(cron="* * * * *")
    @app.task(
        name=RECOVERY_TASK,
        queue="maintenance",
        priority=100,
        queueing_lock=RECOVERY_TASK,
        pass_context=True,
    )
    async def recover_stalled(context: JobContext, timestamp: int) -> None:
        stalled = []
        for task_name in (EDIT_TASK, INDEX_TASK):
            stalled.extend(
                await context.app.job_manager.get_stalled_jobs(task_name=task_name)
            )
        for task in stalled:
            job_id = UUID(str(task.task_kwargs["job_id"]))
            try:
                with attempt_lock(jobs, job_id).acquire(timeout=0):
                    if not await asyncio.to_thread(jobs.is_current_attempt, task):
                        continue
                    job = await asyncio.to_thread(jobs.get, job_id)
                    if job is None:
                        raise ValueError(f"Job metadata not found: {job_id}")
                    await asyncio.to_thread(remove_abandoned_attempts, jobs, job_id)
                    if job.attempts >= job.max_attempts:
                        job.error = (
                            "Worker crashed; the three-attempt limit was reached."
                        )
                        job.finished_at = datetime.now(UTC)
                        await asyncio.to_thread(jobs.save, job)
                        await context.app.job_manager.finish_job(
                            task, status=Status.FAILED, delete_job=False
                        )
                    else:
                        await context.app.job_manager.retry_job(task)
            except Timeout:
                logging.info(
                    "Job %s still has a live attempt; recovery deferred.", job_id
                )

        for task in await context.app.job_manager.get_stalled_jobs(
            queue="maintenance", task_name=RECOVERY_TASK
        ):
            await context.app.job_manager.finish_job(
                task, status=Status.FAILED, delete_job=False
            )
        await context.app.job_manager.delete_old_jobs(
            nb_hours=24,
            queue="maintenance",
            include_failed=True,
            include_cancelled=True,
            include_aborted=True,
        )

    return app


def run_worker(
    data_dir: Path,
    concurrency: int = 1,
    queue: str = EDIT_QUEUE,
    device: str | None = None,
) -> None:
    if os.getppid() != 1 or Path("/proc/1/comm").read_text().strip() != "docker-init":
        raise ValueError(
            "Run workers with Docker Compose and its init process "
            "so a worker crash also stops FFmpeg."
        )
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    videos = VideoStore(data_dir)
    videos.initialize()
    search = SearchStore(videos.database_path)
    search.initialize()
    jobs = JobStore(videos.database_path, database_url())
    jobs.open()
    try:
        jobs.require_schema()
        app = create_worker_app(videos, jobs, search, Embedder(device))
        app.run_worker(
            queues=[queue, "maintenance"],
            name=socket.gethostname(),
            concurrency=concurrency,
            delete_jobs="never",
            shutdown_graceful_timeout=50,
        )
    finally:
        jobs.close()
