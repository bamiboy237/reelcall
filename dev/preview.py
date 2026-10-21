"""Dev-only: run the interactive app against fixed datasets.

    uv run python dev/preview.py [demo|worst|empty|one|huge]

F2 cycles the dataset. Data is swapped at the store boundary, so the app
renders it exactly as it would real records. Never shipped: the package
is built from src/ only.
"""

import io
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from PIL import Image, ImageDraw
from textual.binding import Binding

from video_pipeline import tui
from video_pipeline.search import SearchHit, SearchResponse, SearchStore
from video_pipeline.storage import VideoRecord, VideoStore

DATASETS = ("demo", "worst", "empty", "one", "huge")
QUERIES = {
    "demo": "someone drops the red cup",
    "worst": (
        "the part near the end where Aleksandra Wiśniewska-Kowalczyk explains why "
        "the Q3 numbers moved, right after the fire alarm goes off"
    ),
}
FFMPEG_ERROR = """\
[mov,mp4,m4a,3gp,3g2,mj2 @ 0x5581c2a3b440] moov atom not found
[mov,mp4,m4a,3gp,3g2,mj2 @ 0x5581c2a3b440] stream 0, offset 0x30: partial file
[in#0 @ 0x5581c2a3a1c0] Error opening input: Invalid data found when processing input
Error opening input file \
/home/aleksandra/Videos/IMG_20250914_183022_HDR_portrait_edited_edited.MOV.
Error opening input files: Invalid data found when processing input"""


def frame(width: int, height: int, shade: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (width, height), (24, 24, 24))
    draw = ImageDraw.Draw(image)
    draw.ellipse((width * 0.3, height * 0.25, width * 0.7, height * 0.75), fill=shade)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def video(number: int, filename: str | None, seconds: float | None) -> VideoRecord:
    return VideoRecord(
        id=UUID(int=number),
        filename=filename,
        created_at=datetime(2026, 10, 1, tzinfo=UTC) + timedelta(minutes=number),
        duration_seconds=seconds,
        video_streams=[],
        audio_streams=[],
    )


def dataset(name: str) -> tuple[list[VideoRecord], list[tuple], set[UUID]]:
    """Videos, hits as (video, start, end, score, modality, thumbnail), indexed."""
    if name == "demo":
        videos = [
            video(1, "kitchen.mp4", 312.4),
            video(2, "databases-lecture.mp4", 3605),
            video(3, "fire-drill.mov", 140),
        ]
        hits = [
            (0, 42, 50, 0.62, "visual", frame(448, 252, (200, 60, 50))),
            (0, 130, 138, 0.48, "combined", frame(448, 252, (90, 140, 200))),
            (1, 1204.5, 1212.5, 0.41, "audio", frame(448, 252, (220, 200, 120))),
        ]
        return videos, hits, {videos[0].id, videos[1].id}
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
        hits = [
            (0, 5392.5, 5400.5, 1.0, "combined", frame(448, 252, (200, 60, 50))),
            (1, 0, 8, 0.62, "visual", frame(252, 448, (90, 140, 200))),
            (2, 4.3, 12.3, 0.61, "visual", None),
            (3, 7314, 7322, 0.47, "audio", frame(448, 126, (220, 200, 120))),
            (4, 59.95, 61, 0.33, "visual", frame(8, 8, (160, 160, 160))),
            (5, 35999.5, 36007.5, 0.08, "audio", frame(448, 252, (80, 80, 80))),
            (6, 0, 4, -0.04, "visual", frame(448, 252, (120, 90, 160))),
            (7, 0, 1, -0.31, "combined", frame(448, 252, (60, 160, 90))),
        ]
        return videos, hits, {item.id for item in videos[:6]}
    if name == "one":
        videos = [video(1, "a.mp4", 1)]
        return (
            videos,
            [(0, 0, 1, 0.5, "visual", frame(448, 252, (200, 60, 50)))],
            {videos[0].id},
        )
    if name == "huge":
        videos = [
            video(number, f"lecture-{number:04}.mp4", 2700 + number)
            for number in range(1, 1285)
        ]
        hits = [
            (index, 60.0 * index, 60.0 * index + 8, 0.5 - index / 50, "visual", None)
            for index in range(8)
        ]
        return videos, hits, {item.id for item in videos}
    return [], [], set()


class Preview(tui.Pipeline):
    BINDINGS = [Binding("f2", "cycle", show=False, priority=True)]

    def __init__(self, name: str) -> None:
        super().__init__(Path("/tmp/video-pipeline-preview"))
        self.dataset = name
        self.install(name)

    def install(self, name: str) -> None:
        videos, hits, indexed = dataset(name)
        thumbnails = {(videos[v].id, i): t for i, (v, *_, t) in enumerate(hits)}

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
                    modality=modality,
                    thumbnail_url="",
                    playback_url="",
                )
                for index, (v, start, end, score, modality, _) in enumerate(hits)
            ]
            return SearchResponse(
                query=request.query, mode=request.mode, matches=matches
            )

        stub = {
            (SearchStore, "search"): search,
            (SearchStore, "thumbnail"): lambda store, video_id, window: thumbnails.get(
                (video_id, window)
            ),
            (SearchStore, "indexes"): lambda store: [
                SimpleNamespace(video_id=item) for item in indexed
            ],
            (VideoStore, "list"): lambda store: list(videos),
            (tui.Pipeline, "warm"): lambda app: None,
        }
        for (owner, attribute), replacement in stub.items():
            setattr(owner, attribute, replacement)

    async def action_cycle(self) -> None:
        self.dataset = DATASETS[(DATASETS.index(self.dataset) + 1) % len(DATASETS)]
        self.install(self.dataset)
        self.library = self.videos.list()
        self.indexed = {record.video_id for record in self.searches.indexes()}
        self.action_clear()
        self.sub_title = self.dataset
        self.notice(tui.Text(f"dataset  {self.dataset}", tui.MUTED), 1.5)


if __name__ == "__main__":
    Preview(sys.argv[1] if len(sys.argv) > 1 else "demo").run()
