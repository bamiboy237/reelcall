import hashlib
import io
import json
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, Self
from uuid import UUID

import numpy as np
from filelock import UnixFileLock
from pydantic import BaseModel, ConfigDict, Field, model_validator

from video_pipeline.embeddings import (
    DIMENSIONS,
    FRAME_RATE,
    FRAME_SIZE,
    MODEL_ID,
    MODEL_REVISION,
    QUERY_PROMPT,
    SAMPLE_RATE,
    VISION_TOKENS,
    Embedder,
    read_audio,
    read_window,
)
from video_pipeline.storage import VideoRecord, VideoStore

SearchMode = Literal["all", "visual", "audio", "combined"]
PROFILE = {
    "model": MODEL_ID,
    "revision": MODEL_REVISION,
    "dimensions": DIMENSIONS,
    "dtype": "float32",
    "query_prompt": QUERY_PROMPT,
    "frame_rate": FRAME_RATE,
    "frame_size": FRAME_SIZE,
    "vision_tokens": VISION_TOKENS,
    "sample_rate": SAMPLE_RATE,
    "sampling_version": 1,
}


class IndexSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_seconds: Annotated[float, Field(ge=1, le=30, allow_inf_nan=False)] = 8
    stride_seconds: Annotated[float, Field(ge=0.5, allow_inf_nan=False)] = 4

    @model_validator(mode="after")
    def validate_stride(self) -> Self:
        if self.stride_seconds > self.window_seconds:
            raise ValueError("Index stride must not exceed its window duration.")
        return self


class IndexRecord(BaseModel):
    video_id: UUID
    indexed_at: datetime
    settings: IndexSettings
    profile: dict[str, str | int]
    source_sha256: str
    windows: int
    has_audio: bool
    elapsed_seconds: float
    runtime: dict[str, str]


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: Annotated[str, Field(min_length=1, max_length=2000)]
    mode: SearchMode = "all"
    limit: Annotated[int, Field(ge=1, le=50)] = 5
    video_ids: list[UUID] | None = None
    min_score: Annotated[float | None, Field(ge=-1, le=1, allow_inf_nan=False)] = None

    @model_validator(mode="after")
    def validate_query(self) -> Self:
        self.query = self.query.strip()
        if not self.query:
            raise ValueError("Search query must not be blank.")
        return self


class SearchHit(BaseModel):
    video_id: UUID
    filename: str | None
    window: int
    start_seconds: float
    end_seconds: float
    score: float
    modality: Literal["visual", "audio", "combined"]
    thumbnail_url: str
    playback_url: str


class SearchResponse(BaseModel):
    query: str
    mode: SearchMode
    matches: list[SearchHit]


class SearchStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.data_dir = database_path.parent

    def initialize(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS search_indexes (
                    video_id TEXT PRIMARY KEY NOT NULL,
                    record TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS search_windows (
                    video_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    start_seconds REAL NOT NULL,
                    end_seconds REAL NOT NULL,
                    frame_seconds TEXT NOT NULL,
                    visual BLOB NOT NULL,
                    audio BLOB,
                    combined BLOB,
                    thumbnail BLOB NOT NULL,
                    PRIMARY KEY (video_id, ordinal)
                );
                """
            )

    def get(self, video_id: UUID) -> IndexRecord | None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            row = connection.execute(
                "SELECT record FROM search_indexes WHERE video_id = ?",
                (str(video_id),),
            ).fetchone()
        return IndexRecord.model_validate_json(row[0]) if row else None

    def indexes(self) -> list[IndexRecord]:
        with closing(sqlite3.connect(self.database_path)) as connection:
            rows = connection.execute(
                "SELECT record FROM search_indexes ORDER BY video_id"
            ).fetchall()
        return [IndexRecord.model_validate_json(row[0]) for row in rows]

    def thumbnail(self, video_id: UUID, window: int) -> bytes | None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            row = connection.execute(
                """
                SELECT thumbnail FROM search_windows
                WHERE video_id = ? AND ordinal = ?
                """,
                (str(video_id), window),
            ).fetchone()
        return row[0] if row else None

    def index(
        self,
        video: VideoRecord,
        videos: VideoStore,
        settings: IndexSettings,
        embedder: Embedder,
        on_progress: Callable[[float], None] | None = None,
    ) -> IndexRecord:
        if video.duration_seconds is None or video.duration_seconds <= 0:
            raise ValueError(
                "Indexing requires a video with a known positive duration."
            )
        with UnixFileLock(self.data_dir / f".index-{video.id}.lock"):
            started = time.perf_counter()
            path = videos.original_path(video.id)
            with path.open("rb") as source:
                source_hash = hashlib.file_digest(source, "sha256").hexdigest()
            previous = self.get(video.id)
            if (
                previous is not None
                and previous.source_sha256 == source_hash
                and previous.profile == PROFILE
                and previous.settings == settings
            ):
                if on_progress is not None:
                    on_progress(video.duration_seconds)
                return previous

            rows = []
            start = 0.0
            duration = video.duration_seconds
            audio = read_audio(path) if video.audio_streams else None
            while start < duration:
                end = min(start + settings.window_seconds, duration)
                next_start = start + settings.stride_seconds
                if end < duration and duration - next_start < 1 / FRAME_RATE:
                    end = duration
                media = read_window(path, start, end, audio)
                if not media.frames and media.video_ended:
                    break
                if media.frames:
                    vectors = embedder.window(media)
                    thumbnail = io.BytesIO()
                    media.frames[len(media.frames) // 2].save(
                        thumbnail, format="JPEG", quality=80
                    )
                    rows.append(
                        (
                            str(video.id),
                            len(rows),
                            start,
                            end,
                            json.dumps(media.frame_seconds),
                            vectors["visual"].tobytes(),
                            vectors["audio"].tobytes() if "audio" in vectors else None,
                            vectors["combined"].tobytes()
                            if "combined" in vectors
                            else None,
                            thumbnail.getvalue(),
                        )
                    )
                if on_progress is not None:
                    on_progress(end)
                if end == duration:
                    break
                start = round(next_start, 6)

            if not rows:
                raise ValueError("No video frames available for indexing.")
            record = IndexRecord(
                video_id=video.id,
                indexed_at=datetime.now(UTC),
                settings=settings,
                profile=PROFILE,
                source_sha256=source_hash,
                windows=len(rows),
                has_audio=bool(video.audio_streams),
                elapsed_seconds=round(time.perf_counter() - started, 3),
                runtime=embedder.runtime(),
            )
            with closing(sqlite3.connect(self.database_path)) as connection, connection:
                connection.execute(
                    "DELETE FROM search_windows WHERE video_id = ?", (str(video.id),)
                )
                connection.executemany(
                    "INSERT INTO search_windows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                connection.execute(
                    "INSERT OR REPLACE INTO search_indexes VALUES (?, ?)",
                    (str(video.id), record.model_dump_json()),
                )
            return record

    def load_windows(self, video_ids: list[UUID] | None = None) -> list[sqlite3.Row]:
        query = """
            SELECT w.video_id, w.ordinal, w.start_seconds, w.end_seconds,
                w.visual, w.audio, w.combined, i.record AS index_record,
                v.record AS video_record
            FROM search_windows w
            JOIN search_indexes i ON i.video_id = w.video_id
            JOIN videos v ON v.id = w.video_id
        """
        parameters = ()
        if video_ids is not None:
            query += " WHERE w.video_id IN (SELECT value FROM json_each(?))"
            parameters = (json.dumps([str(video_id) for video_id in video_ids]),)
        query += " ORDER BY w.video_id, w.ordinal"
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(query, parameters).fetchall()
        indexes = {row["video_id"]: row["index_record"] for row in rows}
        if any(json.loads(record)["profile"] != PROFILE for record in indexes.values()):
            raise ValueError("Search index settings have changed. Reindex the videos.")
        return rows

    def search(self, request: SearchRequest, embedder: Embedder) -> SearchResponse:
        rows = self.load_windows(request.video_ids)
        if not rows:
            return SearchResponse(query=request.query, mode=request.mode, matches=[])
        query = embedder.queries([request.query])[0]
        return self.rank(request, query, rows)

    @staticmethod
    def rank(
        request: SearchRequest, query: np.ndarray, rows: list[sqlite3.Row]
    ) -> SearchResponse:
        modalities = (
            ("visual", "audio", "combined")
            if request.mode == "all"
            else (request.mode,)
        )
        # Score each view with one matrix product. Absent views never win.
        scores = np.full((len(rows), len(modalities)), -np.inf, dtype="<f4")
        for column, modality in enumerate(modalities):
            present = [
                index for index, row in enumerate(rows) if row[modality] is not None
            ]
            if present:
                vectors = np.frombuffer(
                    b"".join(rows[index][modality] for index in present), dtype="<f4"
                ).reshape(len(present), -1)
                scores[present, column] = vectors @ query
        best = scores.argmax(axis=1)
        best_scores = scores[np.arange(len(rows)), best].astype(np.float64)
        minimum = -np.inf if request.min_score is None else request.min_score
        eligible = np.flatnonzero((best_scores > -np.inf) & (best_scores >= minimum))
        rounded_scores = np.round(best_scores[eligible], 6)

        filenames: dict[str, str | None] = {}
        matches: list[SearchHit] = []
        # Preserve rounded-score tie order before suppressing overlapping windows.
        for index in eligible[np.argsort(-rounded_scores, kind="stable")]:
            score = float(best_scores[index])
            row = rows[index]
            video_id = row["video_id"]
            if video_id not in filenames:
                filenames[video_id] = json.loads(row["video_record"])["filename"]
            start, end = row["start_seconds"], row["end_seconds"]
            hit = SearchHit(
                video_id=video_id,
                filename=filenames[video_id],
                window=row["ordinal"],
                start_seconds=start,
                end_seconds=end,
                score=round(score, 6),
                modality=modalities[best[index]],
                thumbnail_url=f"/videos/{video_id}/index/thumbnails/{row['ordinal']}",
                playback_url=f"/videos/{video_id}/play#t={start:.3f},{end:.3f}",
            )
            if any(
                other.video_id == hit.video_id
                and max(
                    0,
                    min(other.end_seconds, hit.end_seconds)
                    - max(other.start_seconds, hit.start_seconds),
                )
                >= 0.5
                * min(
                    other.end_seconds - other.start_seconds,
                    hit.end_seconds - hit.start_seconds,
                )
                for other in matches
            ):
                continue
            matches.append(hit)
            if len(matches) == request.limit:
                break
        return SearchResponse(query=request.query, mode=request.mode, matches=matches)
