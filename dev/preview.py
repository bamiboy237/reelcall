"""Dev-only: run the interactive app against fixed datasets.

    uv run python dev/preview.py [demo|worst|empty|one|huge]

F2 cycles the dataset. The app reads its state through Pipeline.load, so
the preview replaces only that and the search store. Never shipped: the
package is built from src/ only.
"""

import io
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from PIL import Image, ImageDraw
from textual.binding import Binding

from video_pipeline import tui
from video_pipeline.embeddings import Embedder
from video_pipeline.jobs import (
    ClipOperation,
    IndexOperation,
    JobOutput,
    JobRecord,
    TranscodeOperation,
)
from video_pipeline.search import SearchHit, SearchResponse, SearchStore
from video_pipeline.storage import VideoRecord, VideoStore

DATASETS = ("demo", "worst", "empty", "one", "huge")
NOW = datetime.now(UTC)
FFMPEG_ERROR = """\
[mov,mp4,m4a,3gp,3g2,mj2 @ 0x5581c2a3b440] moov atom not found
[mov,mp4,m4a,3gp,3g2,mj2 @ 0x5581c2a3b440] stream 0, offset 0x30: partial file
[in#0 @ 0x5581c2a3a1c0] Error opening input: Invalid data found when processing input
Error opening input file \
/home/aleksandra/Videos/IMG_20250914_183022_HDR_portrait_edited_edited.MOV.
Error opening input files: Invalid data found when processing input
connection: postgresql://video_pipeline:s3cret-password@postgres:5432/video_pipeline"""


def frame(width: int, height: int, shade: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (width, height), (24, 24, 24))
    ImageDraw.Draw(image).ellipse(
        (width * 0.3, height * 0.25, width * 0.7, height * 0.75), fill=shade
    )
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def video(number: int, filename: str | None, seconds: float | None) -> VideoRecord:
    return VideoRecord(
        id=UUID(int=number),
        filename=filename,
        created_at=NOW - timedelta(minutes=number),
        duration_seconds=seconds,
        video_streams=[],
        audio_streams=[],
    )


def job(
    number: int,
    video_id: UUID,
    operation: IndexOperation | TranscodeOperation | ClipOperation,
    status: str,
    stage: str = "completed",
    percent: float | None = None,
    error: str | None = None,
) -> JobRecord:
    output = None
    if status == "succeeded" and not isinstance(operation, IndexOperation):
        output = JobOutput(
            filename="video.mp4", media_type="video/mp4", view_url="", download_url=""
        )
    return JobRecord.model_validate(
        {
            "id": UUID(int=10_000 + number),
            "video_id": video_id,
            "operation": operation.model_dump(),
            "status": status,
            "created_at": NOW - timedelta(minutes=30 - number),
            "finished_at": NOW - timedelta(minutes=20 - number)
            if status in ("succeeded", "failed")
            else None,
            "progress": {"stage": stage, "percent": percent},
            "output": output,
            "error": error,
        }
    )


INDEX = IndexOperation(type="index")


def dataset(name: str) -> tuple[tui.Snapshot, list[tuple]]:
    """The snapshot, and search hits as (video, start, end, score, mode, image)."""
    if name == "demo":
        videos = [
            video(1, "kitchen.mp4", 312.4),
            video(2, "databases-lecture.mp4", 3605),
            video(3, "fire-drill.mov", 140),
        ]
        jobs = [
            job(1, videos[0].id, INDEX, "succeeded"),
            job(
                2,
                videos[0].id,
                TranscodeOperation(type="transcode", height=480),
                "succeeded",
            ),
            job(3, videos[1].id, INDEX, "running", "indexing", 42),
        ]
        hits = [
            (0, 42, 50, 0.62, "visual", frame(448, 252, (200, 60, 50))),
            (0, 130, 138, 0.48, "combined", frame(448, 252, (90, 140, 200))),
        ]
        return tui.Snapshot(videos, {videos[0].id}, jobs), hits
    if name == "worst":
        videos = [
            video(
                1, "Q3 Board Deck — FINAL (revised) v12 [approved by legal].mp4", 5400.5
            ),
            video(2, "IMG_20250914_183022_HDR_portrait_edited_edited.MOV", 12.3),
            video(3, "IMG_20250914_183022_HDR_portrait_edited_final.MOV", None),
            video(4, "王秀英的生日派对.mp4", 7322),
            video(5, "👩🏽‍💻 standup recording.webm", 61),
            video(6, None, 36007.5),
            video(7, "[bold red]notes[/] & <b>draft</b>.mkv", 4),
            video(8, "a.mp4", 1),
        ]
        jobs = [
            job(1, videos[0].id, INDEX, "succeeded"),
            job(2, videos[1].id, INDEX, "failed", "indexing", error=FFMPEG_ERROR),
            job(3, videos[3].id, INDEX, "running", "indexing", 7.5),
            job(4, videos[4].id, INDEX, "running", "inspecting"),
            job(5, videos[5].id, INDEX, "queued", "queued"),
            job(
                6,
                videos[0].id,
                ClipOperation(type="clip", start_seconds=5392.5, end_seconds=5400.5),
                "succeeded",
            ),
            job(
                7,
                videos[0].id,
                TranscodeOperation(type="transcode", height=2160),
                "running",
                "transcoding",
                99.6,
            ),
        ]
        hits = [
            (0, 5392.5, 5400.5, 1.0, "combined", frame(448, 252, (200, 60, 50))),
            (3, 7314, 7322, 0.47, "audio", frame(252, 448, (90, 140, 200))),
            (4, 59.95, 61, 0.33, "visual", None),
            (5, 35999.5, 36007.5, 0.08, "audio", frame(448, 126, (220, 200, 120))),
            (6, 0, 4, -0.04, "visual", frame(8, 8, (160, 160, 160))),
        ]
        ready = {videos[0].id, videos[3].id, videos[5].id, videos[6].id}
        return tui.Snapshot(videos, ready, jobs), hits
    if name == "one":
        videos = [video(1, "a.mp4", 1)]
        jobs = [job(1, videos[0].id, INDEX, "succeeded")]
        hits = [(0, 0, 1, 0.5, "visual", frame(448, 252, (200, 60, 50)))]
        return tui.Snapshot(videos, {videos[0].id}, jobs), hits
    if name == "huge":
        videos = [
            video(number, f"lecture-{number:04}.mp4", 2700 + number)
            for number in range(1, 1285)
        ]
        hits = [
            (index, 60.0 * index, 60.0 * index + 8, 0.5, "visual", None)
            for index in range(8)
        ]
        return tui.Snapshot(videos, {item.id for item in videos}, []), hits
    return tui.Snapshot(), []


class Preview(tui.Pipeline):
    BINDINGS = [Binding("f2", "cycle", show=False, priority=True)]

    def __init__(self, name: str) -> None:
        super().__init__(Path(tempfile.mkdtemp(prefix="video-pipeline-preview-")))
        self.original = Path(__file__)
        self._text_embedder = Embedder(text_only=True)  # Search is stubbed.
        self.dataset = name
        self.install(name)

    def install(self, name: str) -> None:
        self.fixture, hits = dataset(name)
        videos = self.fixture.videos
        images = {(videos[v].id, i): image for i, (v, *_, image) in enumerate(hits)}

        def search(store: SearchStore, request, embedder) -> SearchResponse:
            if request.query.startswith("why"):
                raise subprocess.CalledProcessError(1, ["ffmpeg"], stderr=FFMPEG_ERROR)
            matches = [
                SearchHit(
                    video_id=videos[v].id,
                    filename=videos[v].filename,
                    window=index,
                    start_seconds=start,
                    end_seconds=end,
                    score=score,
                    modality=mode,
                    thumbnail_url="",
                    playback_url="",
                )
                for index, (v, start, end, score, mode, _) in enumerate(hits)
            ]
            if request.video_ids:
                wanted = set(request.video_ids)
                matches = [match for match in matches if match.video_id in wanted]
            return SearchResponse(
                query=request.query, mode=request.mode, matches=matches
            )

        stub = {
            (SearchStore, "search"): search,
            (SearchStore, "thumbnail"): lambda store, video_id, window: images.get(
                (video_id, window)
            ),
            (SearchStore, "get"): lambda store, video_id: None,
            (VideoStore, "original_path"): lambda store, video_id: self.original,
        }
        for (owner, attribute), replacement in stub.items():
            setattr(owner, attribute, replacement)

    def load(self) -> tui.Snapshot:
        return self.fixture

    def reconnect(self) -> None:
        self.offline = None

    def action_cycle(self) -> None:
        self.dataset = DATASETS[(DATASETS.index(self.dataset) + 1) % len(DATASETS)]
        self.install(self.dataset)
        self.reload()
        self.notice(tui.Text(f"dataset  {self.dataset}", tui.MUTED), 1.5)


if __name__ == "__main__":
    Preview(sys.argv[1] if len(sys.argv) > 1 else "demo").run()
