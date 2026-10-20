import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO
from uuid import UUID, uuid4

from pydantic import BaseModel

from video_pipeline.inspection import AudioStream, VideoStream, inspect_video


class VideoRecord(BaseModel):
    id: UUID
    filename: str | None
    created_at: datetime
    duration_seconds: float | None
    video_streams: list[VideoStream]
    audio_streams: list[AudioStream]


class VideoStore:
    def __init__(self, data_dir: Path) -> None:
        data_dir = data_dir.expanduser().resolve()
        self.originals_dir = data_dir / "originals"
        self.database_path = data_dir / "videos.sqlite3"

    def initialize(self) -> None:
        self.originals_dir.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS videos (
                    id TEXT PRIMARY KEY NOT NULL,
                    record TEXT NOT NULL
                )
                """
            )

    def original_path(self, video_id: UUID) -> Path:
        return self.originals_dir / str(video_id)

    def add(self, source: BinaryIO, filename: str | None) -> VideoRecord:
        with TemporaryDirectory(dir=self.originals_dir, prefix=".upload-") as directory:
            upload_path = Path(directory) / "original"
            with upload_path.open("wb") as destination:
                shutil.copyfileobj(source, destination)

            info = inspect_video(upload_path)
            video = VideoRecord(
                id=uuid4(),
                filename=filename,
                created_at=datetime.now(UTC),
                duration_seconds=info.duration_seconds,
                video_streams=info.video_streams,
                audio_streams=info.audio_streams,
            )
            original_path = self.original_path(video.id)
            upload_path.rename(original_path)

            try:
                with closing(sqlite3.connect(self.database_path)) as connection:
                    with connection:
                        connection.execute(
                            "INSERT INTO videos (id, record) VALUES (?, ?)",
                            (str(video.id), video.model_dump_json()),
                        )
            except sqlite3.Error:
                original_path.unlink()
                raise

        return video

    def get(self, video_id: UUID) -> VideoRecord | None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            row = connection.execute(
                "SELECT record FROM videos WHERE id = ?", (str(video_id),)
            ).fetchone()
        if row is None:
            return None
        return VideoRecord.model_validate_json(row[0])
