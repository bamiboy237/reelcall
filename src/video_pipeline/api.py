import json
import subprocess
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from video_pipeline.jobs import (
    BatchJobRequest,
    JobRecord,
    JobStore,
    ProcessingOperation,
    database_url,
)
from video_pipeline.storage import VideoRecord, VideoStore


class PrettyJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return (
            json.dumps(content, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        ).encode("utf-8")


def create_app(data_dir: Path) -> FastAPI:
    store = VideoStore(data_dir)
    jobs = JobStore(store.database_path, database_url())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        store.initialize()
        jobs.open()
        try:
            jobs.require_schema()
            yield
        finally:
            jobs.close()

    app = FastAPI(
        title="Video Processing Pipeline",
        lifespan=lifespan,
        default_response_class=PrettyJSONResponse,
    )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> Response:
        return PrettyJSONResponse(
            {"detail": error.detail},
            status_code=error.status_code,
            headers=error.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, error: RequestValidationError
    ) -> Response:
        return PrettyJSONResponse(
            {"detail": jsonable_encoder(error.errors())}, status_code=422
        )

    @app.post("/videos", status_code=201)
    def upload_video(file: UploadFile, response: Response) -> VideoRecord:
        try:
            video = store.add(file.file, file.filename)
        except (subprocess.CalledProcessError, ValueError) as error:
            raise HTTPException(
                status_code=422, detail="Upload must be a readable video."
            ) from error
        response.headers["Location"] = f"/videos/{video.id}"
        return video

    @app.get("/videos/{video_id}")
    def get_video(video_id: UUID) -> VideoRecord:
        video = store.get(video_id)
        if video is None:
            raise HTTPException(status_code=404, detail="Video not found.")
        return video

    @app.get("/videos/{video_id}/original", response_class=FileResponse)
    def download_original(video_id: UUID) -> FileResponse:
        video = get_video(video_id)
        return FileResponse(
            store.original_path(video.id),
            filename=video.filename,
            media_type="application/octet-stream",
        )

    @app.post("/videos/{video_id}/jobs", status_code=202)
    def create_job(
        video_id: UUID, operation: ProcessingOperation, response: Response
    ) -> JobRecord:
        get_video(video_id)
        job = jobs.create(video_id, operation)
        response.headers["Location"] = f"/jobs/{job.id}"
        return job

    @app.get("/jobs")
    def list_jobs() -> list[JobRecord]:
        return jobs.list()

    @app.post("/jobs/batch", status_code=202)
    def create_batch(request: BatchJobRequest) -> list[JobRecord]:
        for item in request.jobs:
            get_video(item.video_id)
        return jobs.create_many(request.jobs)

    @app.get("/jobs/{job_id}")
    def get_job(job_id: UUID) -> JobRecord:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        return job

    def job_file(
        job_id: UUID, disposition: Literal["inline", "attachment"]
    ) -> FileResponse:
        job = get_job(job_id)
        if job.output is None:
            raise HTTPException(status_code=409, detail="Job output is not ready.")
        return FileResponse(
            jobs.output_path(job.id, job.output.filename),
            media_type=job.output.media_type,
            filename=job.output.filename,
            content_disposition_type=disposition,
        )

    @app.get("/jobs/{job_id}/output", response_class=FileResponse)
    def view_output(job_id: UUID) -> FileResponse:
        return job_file(job_id, "inline")

    @app.get("/jobs/{job_id}/download", response_class=FileResponse)
    def download_output(job_id: UUID) -> FileResponse:
        return job_file(job_id, "attachment")

    return app
