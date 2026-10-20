import subprocess
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse

from video_pipeline.storage import VideoRecord, VideoStore


def create_app(data_dir: Path) -> FastAPI:
    store = VideoStore(data_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        store.initialize()
        yield

    app = FastAPI(title="Video Processing Pipeline", lifespan=lifespan)

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

    return app
