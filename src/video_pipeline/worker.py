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

from video_pipeline.inspection import inspect_video
from video_pipeline.jobs import (
    EDIT_QUEUE,
    EDIT_TASK,
    JobOutput,
    JobProgress,
    JobStore,
    ThumbnailOperation,
    TranscodeOperation,
    database_url,
)
from video_pipeline.processing import extract_thumbnail, transcode_video
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
    context: JobContext, job_id: str, videos: VideoStore, jobs: JobStore
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

    with TemporaryDirectory(dir=output_dir, prefix=f"attempt-{job.attempts}-") as temp:
        if isinstance(job.operation, TranscodeOperation):
            job.progress = JobProgress(
                stage="transcoding", duration_seconds=source.duration_seconds
            )
            jobs.save(job)

            def save_progress(seconds: float) -> None:
                if context.should_abort():
                    raise exceptions.JobAborted("Worker shutdown requested.")
                job.progress.processed_seconds = seconds
                duration = source.duration_seconds
                if duration is not None and duration > 0:
                    job.progress.percent = round(min(100, seconds / duration * 100), 2)
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


def create_worker_app(videos: VideoStore, jobs: JobStore) -> procrastinate.App:
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
        process_job(context, job_id, videos, jobs)

    @app.periodic(cron="* * * * *")
    @app.task(
        name=RECOVERY_TASK,
        queue="maintenance",
        priority=100,
        queueing_lock=RECOVERY_TASK,
        pass_context=True,
    )
    async def recover_stalled(context: JobContext, timestamp: int) -> None:
        stalled = await context.app.job_manager.get_stalled_jobs(
            queue=EDIT_QUEUE, task_name=EDIT_TASK
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


def run_worker(data_dir: Path, concurrency: int = 1) -> None:
    if os.getpid() != 1:
        raise ValueError(
            "Run workers with Docker Compose so a worker crash also stops FFmpeg."
        )
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    videos = VideoStore(data_dir)
    videos.initialize()
    jobs = JobStore(videos.database_path, database_url())
    jobs.open()
    try:
        jobs.require_schema()
        app = create_worker_app(videos, jobs)
        app.run_worker(
            queues=[EDIT_QUEUE, "maintenance"],
            name=socket.gethostname(),
            concurrency=concurrency,
            delete_jobs="never",
            shutdown_graceful_timeout=50,
        )
    finally:
        jobs.close()
