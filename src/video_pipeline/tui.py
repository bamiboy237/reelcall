"""The interactive app: a library of videos, their moments and their progress."""

import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import cast
from uuid import UUID

import psycopg
from PIL import Image
from psycopg_pool import PoolTimeout
from pydantic import TypeAdapter, ValidationError
from rich.color import Color
from rich.segment import Segment, Segments
from rich.style import Style
from rich.table import Table
from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen, Screen
from textual.suggester import Suggester
from textual.timer import Timer
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option
from textual.worker import get_current_worker
from tqdm import tqdm

from video_pipeline import term
from video_pipeline.cli import explain, shown
from video_pipeline.embeddings import Embedder
from video_pipeline.jobs import (
    ClipOperation,
    IndexOperation,
    JobRecord,
    JobStore,
    ProcessingOperation,
    TranscodeOperation,
    database_url,
    outputs_dir,
)
from video_pipeline.playback import Playback
from video_pipeline.search import SearchHit, SearchRequest, SearchStore
from video_pipeline.storage import VideoRecord, VideoStore

STRONG = Style.parse("bold color(255)")
TEXT = Style.parse("color(252)")
MUTED = Style.parse("color(248)")
FAINT = Style.parse("color(244)")
GHOST = Style.parse("color(238)")
ERROR = Style.parse("color(203)")

STAGES = {
    "queued": "waiting",
    "inspecting": "preparing",
    "transcoding": "resizing",
    "thumbnail": "thumbnail",
    "clipping": "saving clip",
    "indexing": "indexing",
    "completed": "done",
}
FAILURES = {
    "index": "Couldn't make this video searchable.",
    "transcode": "Couldn't resize this video.",
    "thumbnail": "Couldn't extract a thumbnail.",
    "clip": "Couldn't save this clip.",
}
SECRETS = (
    (re.compile(r"(postgres(?:ql)?://[^:/@\s]+:)[^@\s]+@"), r"\1•••@"),
    (re.compile(r"hf_[A-Za-z0-9]{8,}"), "hf_•••"),
)
START_SERVICE = "docker compose up -d"
NO_PREVIEW = "No preview."
MIN_SCORE = TypeAdapter(SearchRequest.model_fields["min_score"].rebuild_annotation())


def redact(text: str) -> str:
    for pattern, replacement in SECRETS:
        text = pattern.sub(replacement, text)
    return text


def ago(moment: datetime) -> str:
    seconds = (datetime.now(UTC) - moment).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60:.0f} min ago"
    if seconds < 86_400:
        return f"{seconds // 3600:.0f} h ago"
    return f"{moment:%b} {moment.day}"


def describe(operation: ProcessingOperation) -> str:
    if isinstance(operation, TranscodeOperation):
        return f"{operation.height}p video"
    if isinstance(operation, ClipOperation):
        start = term.timecode(operation.start_seconds)
        return f"clip {start} – {term.timecode(operation.end_seconds)}"
    if isinstance(operation, IndexOperation):
        return "search index"
    return "thumbnail"


def span(hit: SearchHit) -> str:
    return f"{term.timecode(hit.start_seconds)} – {term.timecode(hit.end_seconds)}"


def seconds(text: str) -> float:
    total = 0.0
    for part in text.strip().split(":"):
        total = total * 60 + float(part)
    return total


def bar(fraction: float | None, cells: int, frame: int) -> Text:
    """A progress bar, or a sweeping segment when the amount is unknown."""
    if fraction is None:
        travel = cells - 3
        position = travel - abs(travel - frame % (2 * travel))
        return Text.assemble(
            ("━" * position, GHOST), ("━━━", TEXT), ("━" * (travel - position), GHOST)
        )
    filled = round(max(0.0, min(1.0, fraction)) * cells)
    return Text.assemble(("━" * filled, TEXT), ("━" * (cells - filled), GHOST))


def progress(job: JobRecord | None, frame: int, cells: int, stage: bool = True) -> Text:
    """Stage label, bar and percentage; no job means a file is being added."""
    percent = job.progress.percent if job and job.status == "running" else None
    text = Text()
    if stage:
        text.append(
            term.pad(STAGES[job.progress.stage] if job else "adding", 12), MUTED
        )
    text.append_text(bar(None if percent is None else percent / 100, cells, frame))
    if percent is not None:
        text.append(f" {percent:3.0f}%", TEXT)
    return text


def pixel_segments(
    data: bytes | Image.Image, width: int = 36, rows: int = 10
) -> list[Segment]:
    """Render an image with half blocks: two pixels per cell."""
    source = Image.open(BytesIO(data)) if isinstance(data, bytes) else data
    image = source.convert("RGB")
    image.thumbnail((width, rows * 2), Image.Resampling.LANCZOS)
    columns, height = image.size
    raw = image.tobytes()

    def color(x: int, y: int) -> Color:
        offset = (y * columns + x) * 3
        return Color.from_rgb(raw[offset], raw[offset + 1], raw[offset + 2])

    segments: list[Segment] = []
    for y in range(0, height, 2):
        if y:
            segments.append(Segment.line())
        for x in range(columns):
            bottom = color(x, y + 1) if y + 1 < height else None
            segments.append(Segment("▀", Style(color=color(x, y), bgcolor=bottom)))
    return segments


def pixels(data: bytes, width: int = 36, rows: int = 10) -> Text:
    return Text.assemble(
        *(
            (segment.text, segment.style) if segment.style is not None else segment.text
            for segment in pixel_segments(data, width, rows)
        )
    )


def player(path: Path, start: float = 0, end: float | None = None) -> list[str] | None:
    if shutil.which("mpv"):
        window = [f"--start={start}"] + ([f"--end={end}"] if end else [])
        return ["mpv", *window, "--really-quiet", str(path)]
    if shutil.which("ffplay"):
        window = ["-ss", f"{start}"] + (["-t", f"{end - start}"] if end else [])
        return ["ffplay", "-v", "quiet", "-autoexit", *window, str(path)]
    for opener in ("open", "xdg-open"):
        if shutil.which(opener):
            return [opener, str(path)]
    return None


def fill(rows: OptionList, options: list[Option]) -> None:
    """Show new options, keeping the highlighted row when it still exists."""
    ids = [option.id for option in options]
    if ids == [rows.get_option_at_index(i).id for i in range(rows.option_count)]:
        for index, option in enumerate(options):
            rows.replace_option_prompt_at_index(index, option.prompt)
        return
    selected = rows.highlighted_option.id if rows.highlighted_option else None
    rows.set_options(options)
    if selected is not None and selected in ids:
        rows.highlighted = ids.index(selected)
    else:
        rows.highlighted = next((index for index, key in enumerate(ids) if key), None)


def keys(pairs: list[tuple[str, str]]) -> Text:
    text = Text()
    for index, (key, label) in enumerate(pairs):
        text.append("   " if index else "")
        text.append(key, TEXT)
        text.append(f" {label}", FAINT)
    return text


def preview(searches: SearchStore, video_id: UUID, window: int | None) -> Text:
    """A thumbnail from the search index, or why there isn't one."""
    if window is None:
        record = searches.get(video_id)
        if record is None:
            return Text(NO_PREVIEW, FAINT)
        window = record.windows // 2
    image = searches.thumbnail(video_id, window)
    return pixels(image) if image else Text(NO_PREVIEW, FAINT)


@dataclass
class Snapshot:
    """Everything the screens show, read together from the stores."""

    videos: list[VideoRecord] = field(default_factory=list)  # Newest first.
    ready: set[UUID] = field(default_factory=set)
    jobs: list[JobRecord] = field(default_factory=list)  # Oldest first.

    def video(self, video_id: UUID) -> VideoRecord | None:
        return next((video for video in self.videos if video.id == video_id), None)

    def job(self, job_id: str) -> JobRecord:
        return next(job for job in self.jobs if str(job.id) == job_id)

    def latest(self, video_id: UUID, kind: str) -> JobRecord | None:
        matches = [
            job
            for job in self.jobs
            if job.video_id == video_id and job.operation.type == kind
        ]
        return matches[-1] if matches else None

    def state(self, video: VideoRecord) -> tuple[str, JobRecord | None]:
        job = self.latest(video.id, "index")
        if job is not None and job.status in ("queued", "running"):
            return "indexing", job
        if video.id in self.ready:
            return "ready", job
        if job is not None and job.status == "failed":
            return "failed", job
        return "uploaded", None

    def active(self) -> list[JobRecord]:
        return [job for job in self.jobs if job.status in ("queued", "running")]

    def failed(self) -> list[JobRecord]:
        latest = {(job.video_id, job.operation.type): job for job in self.jobs}
        return [job for job in latest.values() if job.status == "failed"]

    def finished(self) -> list[JobRecord]:
        done = [job for job in self.jobs if job.status == "succeeded"]
        return sorted(done, key=lambda job: job.finished_at or job.created_at)[::-1]


@dataclass
class Failure:
    """A failed add. It has no job record, so the app keeps it for the session."""

    name: str
    summary: str
    diagnostics: str


GLYPHS = {
    "ready": ("●", TEXT),
    "uploaded": ("○", FAINT),
    "indexing": ("◐", TEXT),
    "failed": ("✕", ERROR),
}


class KeyBar(Static):
    """Status on the left, the keys that work right now on the right."""

    def show(self, message: Text, pairs: list[tuple[str, str]]) -> None:
        room = self.size.width - message.cell_len - 3
        while len(pairs) > 1 and keys(pairs).cell_len > room:
            pairs = pairs[:-2] + pairs[-1:]  # The last key is the way out; keep it.
        grid = Table.grid(expand=True)
        grid.add_column()
        grid.add_column(justify="right")
        grid.add_row(message, keys(pairs))
        self.update(grid)


class Choice(Static, can_focus=True):
    """A labelled on/off choice: space or a click toggles it."""

    BINDINGS = [Binding("space", "flip", show=False)]
    value = reactive(True)

    def __init__(self, label: str, value: bool, disabled: bool) -> None:
        super().__init__(disabled=disabled)
        self.label = label
        self.value = value

    def render(self) -> Text:
        mark = "[✓]" if self.value else "[ ]"
        style = FAINT if self.disabled else TEXT
        return Text.assemble((mark, style), "  ", (self.label, style))

    def action_flip(self) -> None:
        self.value = not self.value

    def on_click(self) -> None:
        self.action_flip()


class PathCompleter(Suggester):
    """Ghost-text completion for file paths."""

    def __init__(self) -> None:
        super().__init__(use_cache=False, case_sensitive=True)

    async def get_suggestion(self, value: str) -> str | None:
        if not value:
            return None
        base = Path(value).expanduser()
        folder, prefix = (base, "") if value.endswith("/") else (base.parent, base.name)
        try:
            entries = sorted(
                entry
                for entry in folder.iterdir()
                if entry.name.startswith(prefix) and not entry.name.startswith(".")
            )
        except OSError:
            return None
        if not entries or entries[0].name == prefix:
            return None
        name = entries[0].name + ("/" if entries[0].is_dir() else "")
        return value + name[len(prefix) :]


class View(Screen):
    """A screen that redraws from the latest snapshot."""

    @property
    def pipeline(self) -> "Pipeline":
        return cast(Pipeline, self.app)

    def keys(self) -> list[tuple[str, str]]:
        return []

    def crumb(self) -> str:
        """How this screen is named in the breadcrumb of screens opened from it."""
        return ""

    def update_view(self) -> None:
        self.set_class(self.size.width < 100, "-narrow")
        self.query_one(KeyBar).show(self.pipeline.message(), self.keys())

    def on_resize(self, event: events.Resize) -> None:
        self.update_view()

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        self.update_view()


class Library(View):
    PAGE = 20
    BINDINGS = [
        Binding("a", "add"),
        Binding("slash", "search"),
        Binding("exclamation_mark", "activity"),
        Binding("escape", "leave_search", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.shown = self.PAGE
        self.select: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="header"):
            yield Static(Text("Library", STRONG), classes="title")
            yield Static(classes="summary")
        with Horizontal(classes="field"):
            yield Static("⌕", classes="glyph")
            yield Input(compact=True, id="query")
        yield OptionList(compact=True, classes="rows")
        yield Static(
            Text.assemble(
                ("No videos yet\n\n", MUTED), ("a", TEXT), ("  Add video", STRONG)
            ),
            classes="empty",
        )
        yield KeyBar()

    def on_mount(self) -> None:
        self.query_one(".rows").focus()

    def on_screen_resume(self) -> None:
        self.query_one(".rows").focus()

    def crumb(self) -> str:
        return "Library"

    def keys(self) -> list[tuple[str, str]]:
        if self.focused is self.query_one("#query"):
            return [("⏎", "search"), ("esc", "cancel")]
        return [("a", "add"), ("/", "search"), ("⏎", "open"), ("!", "activity")]

    def row(self, icon: Text, name: str, length: str, state: Text) -> Text:
        wide = self.size.width >= 80
        length_width = 12 if wide else 0
        name_width = min(
            44, max(12, self.size.width - 12 - length_width - (17 if wide else 13))
        )
        return Text.assemble(
            icon,
            " ",
            (term.pad(term.middle(name, name_width), name_width + 2), TEXT),
            (term.pad(length, length_width) if wide else "", MUTED),
            state,
        )

    def update_view(self) -> None:
        app = self.pipeline
        snapshot = app.snapshot
        cells = 10 if self.size.width >= 80 else 6
        options = [
            Option(
                self.row(
                    Text("◌", FAINT), name, "", progress(None, app.frame, cells, False)
                ),
                id=f"adding:{path}",
            )
            for path, name in app.adding.items()
        ]
        for video in snapshot.videos[: self.shown]:
            state, job = snapshot.state(video)
            glyph, glyph_style = GLYPHS[state]
            label = (
                progress(job, app.frame, cells, False)
                if state == "indexing"
                else Text("")
            )
            length = (
                term.timecode(video.duration_seconds)
                if video.duration_seconds is not None
                else "–"
            )
            name = video.filename or "untitled"
            options.append(
                Option(
                    self.row(Text(glyph, glyph_style), name, length, label),
                    id=str(video.id),
                )
            )
        remaining = len(snapshot.videos) - self.shown
        if remaining > 0:
            more = f"{min(self.PAGE, remaining)} more"
            options.append(
                Option(Text.assemble(("  Load ", FAINT), (more, TEXT)), id="more")
            )
        rows = self.query_one(".rows", OptionList)
        fill(rows, options)
        ids = [option.id for option in options]
        if self.select in ids:
            rows.highlighted = ids.index(self.select)
            self.select = None

        self.query_one(".title", Static).update(
            Text.assemble(("Library", STRONG), (f"  {len(snapshot.videos):,}", FAINT))
        )
        running = sum(1 for job in snapshot.active() if job.operation.type == "index")
        failed = len(snapshot.failed()) + len(app.failures)
        summary = Text()
        for amount, state in ((running, "indexing"), (failed, "failed")):
            if amount:
                glyph, style = GLYPHS[state]
                summary.append(f"   {glyph} {amount:,}", style)
        if app.offline:
            summary.append("   job service offline", ERROR)
        self.query_one(".summary", Static).update(summary)
        self.set_class(not options, "-empty")
        super().update_view()

    def action_add(self) -> None:
        def chosen(result: tuple[Path, bool] | None) -> None:
            if result is not None:
                self.pipeline.add(*result)

        self.pipeline.push_screen(AddSheet(self.pipeline.jobs is not None), chosen)

    def action_search(self) -> None:
        self.query_one("#query").focus()

    def action_leave_search(self) -> None:
        self.query_one("#query", Input).clear()
        self.query_one(".rows").focus()

    def action_activity(self) -> None:
        self.pipeline.push_screen(Activity())

    @on(Input.Changed, "#query")
    def typing(self, event: Input.Changed) -> None:
        if (
            event.value
            and not event.value.startswith("/")
            and self.pipeline.snapshot.ready
        ):
            self.pipeline.warm()

    @on(Input.Submitted, "#query")
    def submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        self.action_leave_search()
        if query == "/add":
            self.action_add()
        elif query == "/activity":
            self.action_activity()
        elif query and query != "/videos":
            self.pipeline.push_screen(Results(query))

    @on(OptionList.OptionSelected, ".rows")
    def selected(self, event: OptionList.OptionSelected) -> None:
        key = event.option.id or ""
        if key == "more":
            self.shown += self.PAGE
            self.update_view()
        elif not key.startswith("adding:"):
            self.pipeline.push_screen(VideoScreen(UUID(key)))


class PlayerScreen(View):
    BINDINGS = [
        Binding("space", "pause"),
        Binding("left", "seek(-5)"),
        Binding("right", "seek(5)"),
        Binding("home", "restart", show=False),
        Binding("m", "mute"),
        Binding("o", "external"),
        Binding("d", "details", show=False),
        Binding("escape", "app.pop_screen"),
    ]

    def __init__(
        self,
        path: Path,
        start: float = 0,
        end: float | None = None,
        label: str = "",
    ) -> None:
        super().__init__()
        self.path, self.start, self.end = path, start, end
        self.label = label or path.name
        self.playback: Playback | None = None
        self.frame: Image.Image | None = None
        self.timer: Timer | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="header"):
            yield Static(Text(self.label, STRONG), classes="title")
        yield Static(Text("Loading…", FAINT), classes="video-frame")
        yield Static(classes="player-position")
        yield KeyBar()

    def on_mount(self) -> None:
        self.playback = Playback(self.path, self.start, self.end)
        self.timer = self.set_interval(1 / 30, self.tick_frame)
        self.update_view()

    def on_unmount(self) -> None:
        if self.timer is not None:
            self.timer.stop()
        if self.playback is not None:
            self.playback.close()

    def keys(self) -> list[tuple[str, str]]:
        playback = self.playback
        if playback is None:
            return [("esc", "back")]
        if playback.error:
            return [("d", "details"), ("o", "open outside"), ("esc", "back")]
        if not playback.ready:
            return [("esc", "back")]
        action = "replay" if playback.ended else "play" if playback.paused else "pause"
        return [
            ("space", action),
            ("←/→", "seek 5s"),
            ("m", "unmute" if playback.muted else "mute"),
            ("o", "open outside"),
            ("esc", "back"),
        ]

    def draw_frame(self) -> None:
        if self.frame is not None:
            picture = self.query_one(".video-frame", Static)
            width, height = picture.content_size
            picture.update(
                Segments(pixel_segments(self.frame, max(1, width), max(1, height)))
            )

    def tick_frame(self) -> None:
        playback = self.playback
        if playback is None:
            return
        width, height = self.query_one(".video-frame").content_size
        playback.resize(max(1, width), max(1, height))
        frame = playback.poll()
        if frame is not None:
            self.frame = frame
            self.draw_frame()
        self.update_view()

    def on_resize(self, event: events.Resize) -> None:
        self.draw_frame()
        super().on_resize(event)

    def update_view(self) -> None:
        playback = self.playback
        if playback is not None:
            if playback.error:
                self.query_one(".video-frame", Static).update(
                    Text("Playback failed.", ERROR)
                )
            position = Text(term.timecode(playback.position), TEXT)
            if playback.end is not None:
                position.append(f" / {term.timecode(playback.end)}", FAINT)
            if playback.ended:
                position.append("   ended", MUTED)
            elif playback.paused:
                position.append("   paused", MUTED)
            if playback.muted:
                position.append("   muted", MUTED)
            self.query_one(".player-position", Static).update(position)
        super().update_view()

    def action_pause(self) -> None:
        if self.playback is not None:
            self.playback.toggle_pause()
            self.update_view()

    def action_seek(self, seconds: float) -> None:
        if self.playback is not None:
            self.playback.seek(self.playback.position + seconds)

    def action_restart(self) -> None:
        if self.playback is not None:
            self.playback.seek(self.start)

    def action_mute(self) -> None:
        if self.playback is not None:
            self.playback.toggle_mute()
            self.update_view()

    def action_external(self) -> None:
        playback = self.playback
        if playback is None or not (playback.ready or playback.error):
            return
        if not playback.paused and not playback.ended:
            playback.toggle_pause()
        start = playback.start if playback.ended else playback.position
        self.pipeline.open_external(self.path, start, playback.end, self.label)
        self.update_view()

    def action_details(self) -> None:
        if self.playback is not None and self.playback.error:
            self.pipeline.push_screen(
                ErrorSheet("Playback failed.", self.playback.diagnostics)
            )


class VideoScreen(View):
    BINDINGS = [
        Binding("p", "play"),
        Binding("v", "play_video"),
        Binding("i", "index"),
        Binding("r", "resize"),
        Binding("c", "clip"),
        Binding("d", "details"),
        Binding("y", "copy"),
        Binding("escape", "app.pop_screen"),
    ]

    def __init__(self, video_id: UUID, moment: SearchHit | None = None) -> None:
        super().__init__()
        self.video_id = video_id
        self.moment = moment
        self.preview: Text | None = None
        self.came_from = ""

    def on_mount(self) -> None:
        below = self.app.screen_stack[-2]
        self.came_from = below.crumb() if isinstance(below, View) else "Library"
        self.update_view()

    @property
    def video(self) -> VideoRecord:
        video = self.pipeline.snapshot.video(self.video_id)
        assert video is not None
        return video

    def compose(self) -> ComposeResult:
        with Horizontal(classes="header"):
            yield Static(classes="title")
        with Horizontal(classes="hero"):
            yield Static(classes="preview")
            with Vertical(classes="about"):
                yield Static(classes="status")
                yield Static(classes="facts")
                yield Static(classes="moment")
        yield Static(classes="progress")
        yield Static(Text("Outputs", FAINT), classes="label")
        yield OptionList(compact=True, classes="rows outputs")
        with VerticalScroll(classes="details"):
            yield Static(classes="technical")
        yield KeyBar()

    def keys(self) -> list[tuple[str, str]]:
        state, _ = self.pipeline.snapshot.state(self.video)
        pairs = [("⏎", "open")] if self.focused is self.query_one(".outputs") else []
        pairs.append(("p", "play moment" if self.moment else "play"))
        if self.moment:
            pairs.append(("v", "play video"))
        if state in ("uploaded", "failed"):
            pairs.append(("i", "make searchable" if state == "uploaded" else "retry"))
        pairs += [("r", "resize"), ("c", "clip"), ("d", "details")]
        if self.has_class("-details") and state == "failed":
            pairs.append(("y", "copy error"))
        return [*pairs, ("esc", "back")]

    def update_view(self) -> None:
        app = self.pipeline
        snapshot = app.snapshot
        video = self.video
        state, job = snapshot.state(video)
        self.query_one(".title", Static).update(
            Text.assemble(
                (f"{self.came_from}  ›  ", FAINT),
                (video.filename or "untitled", STRONG),
            )
        )

        if state == "ready":
            status = Text("Ready to search", TEXT)
        elif state == "uploaded":
            status = Text("Not searchable yet", MUTED)
        elif state == "failed" and job is not None:
            status = Text(term.clip(FAILURES[job.operation.type], 36), ERROR)
        else:
            status = progress(job, app.frame, 16)
        self.query_one(".status", Static).update(status)

        parts = [
            term.timecode(video.duration_seconds)
            if video.duration_seconds is not None
            else "unknown length"
        ]
        if video.video_streams:
            stream = video.video_streams[0]
            parts.append(f"{stream.width}×{stream.height}")
        original = app.videos.original_path(video.id)
        parts.append(term.filesize(original.stat().st_size))
        self.query_one(".facts", Static).update(Text(" · ".join(parts), MUTED))

        if self.moment is not None:
            self.query_one(".moment", Static).update(
                Text.assemble(("\nmoment  ", FAINT), (span(self.moment), STRONG))
            )

        if self.preview is None and state == "ready":
            window = self.moment.window if self.moment else None
            self.preview = preview(app.searches, video.id, window)
        self.query_one(".preview", Static).update(
            self.preview or Text(NO_PREVIEW, FAINT)
        )

        running = [  # Indexing shows in the status line.
            job
            for job in snapshot.active()
            if job.video_id == video.id
            and not isinstance(job.operation, IndexOperation)
        ]
        lines = Text()
        for index, active in enumerate(running):
            lines.append("\n" if index else "")
            lines.append(term.pad(describe(active.operation), 22), TEXT)
            lines.append_text(progress(active, app.frame, 20))
        self.query_one(".progress", Static).update(lines)
        self.query_one(".progress").display = bool(running)

        outputs = [
            done
            for done in snapshot.finished()[::-1]
            if done.video_id == video.id
            and done.output is not None
            and not isinstance(done.operation, IndexOperation)
        ]
        fill(
            self.query_one(".outputs", OptionList),
            [
                Option(
                    Text.assemble(
                        (term.pad(describe(done.operation), 22), TEXT),
                        (ago(done.finished_at or done.created_at), FAINT),
                    ),
                    id=str(done.id),
                )
                for done in outputs
            ],
        )
        for selector in (".label", ".outputs"):
            self.query_one(selector).display = bool(outputs)
        if outputs and self.focused is None:
            self.query_one(".outputs").focus()
        elif not outputs and self.focused is not None:
            self.set_focus(None)

        if self.has_class("-details"):
            self.query_one(".technical", Static).update(self.technical(state, job))
        super().update_view()

    def technical(self, state: str, job: JobRecord | None) -> Text:
        video = self.video
        rows = [
            ("file", [video.filename or "untitled"]),
            ("id", [str(video.id)]),
            ("added", [f"{video.created_at:%Y-%m-%d %H:%M} UTC"]),
            *term.facts(video),
        ]
        if video.format_name:
            rows.append(("format", [video.format_name]))
        record = self.pipeline.searches.get(video.id)
        if record is not None:
            window = f"{record.settings.window_seconds:g}s"
            stride = f"every {record.settings.stride_seconds:g}s"
            rows += [
                ("index", [term.count(record.windows, "window")]),
                ("windows", [window, stride]),
                ("indexed", [f"{record.indexed_at:%Y-%m-%d %H:%M} UTC"]),
                ("model", [str(record.profile["model"])]),
            ]
        for done in self.pipeline.snapshot.finished()[::-1]:
            if done.video_id == video.id and done.output is not None:
                if not isinstance(done.operation, IndexOperation):
                    rows.append(
                        (describe(done.operation), [shown(self.pipeline.output(done))])
                    )
        size = max(len(label) for label, _ in rows) + 3
        text = Text()
        for label, parts in rows:
            text.append(term.pad(label, size), FAINT)
            text.append(" · ".join(parts) + "\n" if parts else "none\n", MUTED)
        if state == "failed" and job is not None:
            text.append("\nerror\n", FAINT)
            text.append(redact(job.error or "No details were recorded."), MUTED)
        text.rstrip()
        return text

    def action_play(self) -> None:
        if self.moment is None:
            self.action_play_video()
            return
        path = self.pipeline.videos.original_path(self.video_id)
        moment = self.moment
        self.pipeline.play(
            path,
            moment.start_seconds,
            moment.end_seconds,
            self.video.filename or "Video",
        )

    def action_play_video(self) -> None:
        path = self.pipeline.videos.original_path(self.video_id)
        self.pipeline.play(path, label=self.video.filename or "video")

    def action_index(self) -> None:
        if self.pipeline.snapshot.state(self.video)[0] in ("uploaded", "failed"):
            self.pipeline.submit(self.video, IndexOperation(type="index"))

    def action_resize(self) -> None:
        def chosen(height: str | None) -> None:
            if height is None:
                return
            try:
                operation = TranscodeOperation(type="transcode", height=int(height))
            except (ValueError, ValidationError):
                message = "Height must be an even number of pixels."
                self.pipeline.notice(Text(message, ERROR), 4)
                return
            self.pipeline.submit(self.video, operation)

        self.pipeline.push_screen(Ask("Resize", "height in pixels", "720"), chosen)

    def action_clip(self) -> None:
        def chosen(text: str | None) -> None:
            if text is None:
                return
            try:
                start, end = (seconds(part) for part in re.split("[–-]", text, 1))
                operation = ClipOperation(
                    type="clip", start_seconds=start, end_seconds=end
                )
            except (ValueError, ValidationError):
                self.pipeline.notice(Text("Enter a range like 0:42 – 0:50.", ERROR), 4)
                return
            self.pipeline.submit(self.video, operation)

        moment = span(self.moment) if self.moment else ""
        self.pipeline.push_screen(Ask("Save clip", "start – end", moment), chosen)

    def action_details(self) -> None:
        self.toggle_class("-details")
        self.update_view()

    def action_copy(self) -> None:
        state, job = self.pipeline.snapshot.state(self.video)
        if self.has_class("-details") and state == "failed" and job is not None:
            self.pipeline.copy_error(job.error or "")

    @on(OptionList.OptionSelected, ".outputs")
    def open_output(self, event: OptionList.OptionSelected) -> None:
        job = self.pipeline.snapshot.job(event.option.id or "")
        self.pipeline.play(self.pipeline.output(job), label=describe(job.operation))


class Results(View):
    BINDINGS = [
        Binding("right", "open_moment"),
        Binding("slash", "edit"),
        Binding("r", "retry"),
        Binding("f", "filter"),
        Binding("d", "details"),
        Binding("escape", "back"),
    ]

    def __init__(self, query: str) -> None:
        super().__init__()
        self.query_text = query
        self.min_score: float | None = None
        self.names: list[VideoRecord] = []
        self.hits: list[SearchHit] = []
        self.thumbnails: list[bytes | None] = []
        self.problem: tuple[str, str] | None = None
        self.searching = True
        self.generation = 0  # Which search the screen is waiting for.

    def compose(self) -> ComposeResult:
        with Horizontal(classes="field"):
            yield Static("⌕", classes="glyph")
            yield Input(self.query_text, compact=True, id="query")
        yield Static(classes="search-filter")
        with Horizontal(classes="body"):
            yield OptionList(compact=True, classes="rows")
            yield Static(classes="preview")
        yield KeyBar()

    def on_mount(self) -> None:
        self.query_one(".rows").focus()
        self.run_search()

    def run_search(self) -> None:
        needle = self.query_text.casefold()
        self.names = [
            video
            for video in self.pipeline.snapshot.videos
            if needle in (video.filename or "").casefold()
        ][:5]
        self.hits, self.thumbnails, self.problem = [], [], None
        self.searching = True
        self.generation += 1
        self.update_view()
        self.search(self.generation, self.query_text, self.min_score)

    @work(thread=True, exclusive=True)
    def search(self, generation: int, query: str, min_score: float | None) -> None:
        app = self.pipeline
        key = id(get_current_worker())
        app.call_from_thread(app.working, key, "searching")
        try:
            response = app.searches.search(
                SearchRequest(query=query, limit=12, min_score=min_score),
                app.text_embedder(),
            )
            thumbnails = [
                app.searches.thumbnail(hit.video_id, hit.window)
                for hit in response.matches
            ]
            app.call_from_thread(
                self.show, generation, response.matches, thumbnails, None
            )
        except (OSError, ValueError) as error:
            summary = "Search failed."
            if isinstance(error, ValueError) and "Reindex" in str(error):
                summary = "Reindex these videos."
            problem = (summary, explain(error))
            app.call_from_thread(self.show, generation, [], [], problem)
        finally:
            app.call_from_thread(app.done, key)

    def show(
        self,
        generation: int,
        hits: list[SearchHit],
        thumbnails: list[bytes | None],
        problem: tuple[str, str] | None,
    ) -> None:
        if generation != self.generation or not self.is_attached:
            return  # A newer search replaced this one, or the screen closed.
        self.hits, self.thumbnails, self.problem = hits, thumbnails, problem
        self.searching = False
        self.update_view()

    def selected(self) -> tuple[str, int] | None:
        option = self.query_one(".rows", OptionList).highlighted_option
        if option is None or option.id is None:
            return None
        kind, index = option.id.split(":")
        return kind, int(index)

    def keys(self) -> list[tuple[str, str]]:
        if self.focused is self.query_one("#query"):
            return [("⏎", "search"), ("esc", "results")]
        choice = self.selected()
        if choice and choice[0] == "moment":
            return [
                ("⏎", "play"),
                ("→", "open video"),
                ("f", "filter"),
                ("/", "edit"),
                ("esc", "back"),
            ]
        if self.problem:
            return [
                ("r", "retry"),
                ("d", "details"),
                ("f", "filter"),
                ("/", "edit"),
                ("esc", "back"),
            ]
        pairs = [("⏎", "open")] if choice else []
        return [*pairs, ("f", "filter"), ("/", "edit"), ("esc", "back")]

    def update_view(self) -> None:
        snapshot = self.pipeline.snapshot
        filter_label = self.query_one(".search-filter", Static)
        filter_label.display = self.min_score is not None
        if self.min_score is not None:
            filter_label.update(Text(f"Minimum score: {self.min_score:g}", FAINT))
        width = self.size.width - (10 if self.has_class("-narrow") else 52)
        options: list[Option] = []
        if self.names:
            options.append(Option(Text("Videos", FAINT), disabled=True))
            for index, video in enumerate(self.names):
                glyph, glyph_style = GLYPHS[snapshot.state(video)[0]]
                name = term.middle(video.filename or "untitled", width - 2)
                options.append(
                    Option(
                        Text.assemble((glyph + " ", glyph_style), (name, TEXT)),
                        id=f"video:{index}",
                    )
                )
        options.append(Option(Text("Moments", FAINT), disabled=True))
        if self.searching:
            options.append(Option(Text("searching…", FAINT), disabled=True))
        elif self.problem:
            options.append(Option(Text(self.problem[0], ERROR), disabled=True))
        elif not self.hits:
            if not snapshot.ready:
                message = "No indexed videos."
            elif self.min_score is not None:
                message = "No results. Press f to lower the minimum score."
            else:
                message = "No results."
            options.append(Option(Text(message, FAINT), disabled=True))
        starts = [term.timecode(hit.start_seconds) for hit in self.hits]
        ends = [term.timecode(hit.end_seconds) for hit in self.hits]
        start_width = max(map(len, starts), default=0)
        span_width = start_width + max(map(len, ends), default=0) + 6
        wide = width >= 70
        name_width = width - span_width - (17 if wide else 0)
        for index, hit in enumerate(self.hits):
            name = term.middle(hit.filename or "untitled", name_width)
            when = f"{starts[index]:>{start_width}} – {ends[index]}"
            row = Text.assemble(
                (term.pad(when, span_width), TEXT),
                (term.pad(name, name_width + 2), MUTED),
            )
            if wide:
                row.append(term.pad(hit.modality, 10), FAINT)
                row.append(f"{term.score(hit.score):>5}", FAINT)
            options.append(Option(row, id=f"moment:{index}"))
        fill(self.query_one(".rows", OptionList), options)
        self.show_preview()
        super().update_view()

    def show_preview(self) -> None:
        choice = self.selected()
        image = Text("")
        if choice and choice[0] == "moment":
            hit = self.hits[choice[1]]
            image = preview(self.pipeline.searches, hit.video_id, hit.window)
        elif choice and choice[0] == "video":
            image = preview(self.pipeline.searches, self.names[choice[1]].id, None)
        self.query_one(".preview", Static).update(image)

    @on(OptionList.OptionHighlighted, ".rows")
    def highlighted(self) -> None:
        self.show_preview()
        super().update_view()

    @on(OptionList.OptionSelected, ".rows")
    def chosen(self) -> None:
        choice = self.selected()
        if choice is None:
            return
        kind, index = choice
        if kind == "video":
            self.pipeline.push_screen(VideoScreen(self.names[index].id))
            return
        hit = self.hits[index]
        path = self.pipeline.videos.original_path(hit.video_id)
        self.pipeline.play(
            path, hit.start_seconds, hit.end_seconds, hit.filename or "Video"
        )

    def action_open_moment(self) -> None:
        choice = self.selected()
        if choice and choice[0] == "moment":
            hit = self.hits[choice[1]]
            self.pipeline.push_screen(VideoScreen(hit.video_id, hit))

    def crumb(self) -> str:
        return f"⌕ {term.clip(self.query_text, 32)}"

    def action_edit(self) -> None:
        self.query_one("#query").focus()

    def action_retry(self) -> None:
        if self.problem:
            self.run_search()

    def action_filter(self) -> None:
        def chosen(value: str | None) -> None:
            if value is None:
                return
            try:
                score = MIN_SCORE.validate_python(
                    float(value) if value.strip() else None
                )
            except ValueError:
                self.pipeline.notice(
                    Text("Enter a score from -1 to 1, or leave it blank.", ERROR), 4
                )
                return
            if score != self.min_score:
                self.min_score = score
                self.run_search()

        value = "" if self.min_score is None else repr(self.min_score)
        self.pipeline.push_screen(
            Ask("Minimum score", "-1 to 1; blank shows all", value),
            chosen,
        )

    def action_details(self) -> None:
        if self.problem:
            self.pipeline.push_screen(ErrorSheet(*self.problem))

    def action_back(self) -> None:
        if self.focused is self.query_one("#query"):
            self.query_one(".rows").focus()
        else:
            self.pipeline.pop_screen()

    @on(Input.Submitted, "#query")
    def submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        self.query_one(".rows").focus()
        if query and (query != self.query_text or self.problem):
            self.query_text = query
            self.run_search()


class Activity(ModalScreen[None]):
    """Running, failed and recently finished work, over any screen."""

    BINDINGS = [
        Binding("escape,exclamation_mark", "dismiss", show=False),
        Binding("d", "details"),
        Binding("y", "copy"),
    ]

    @property
    def pipeline(self) -> "Pipeline":
        return cast(Pipeline, self.app)

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel"):
            yield Static(Text("Activity", STRONG), classes="title")
            yield Static(classes="service")
            yield OptionList(compact=True, classes="rows")
            yield KeyBar()

    def on_mount(self) -> None:
        self.query_one(".rows").focus()
        self.update_view()

    def failure(self) -> tuple[str, str] | None:
        option = self.query_one(".rows", OptionList).highlighted_option
        if option is None or option.id is None:
            return None
        kind, _, value = option.id.partition(":")
        if kind == "add":
            failed = self.pipeline.failures[int(value)]
            return failed.summary, failed.diagnostics
        job = self.pipeline.snapshot.job(value)
        if job.status != "failed":
            return None
        return FAILURES[job.operation.type], job.error or "No details were recorded."

    def update_view(self) -> None:
        app = self.pipeline
        snapshot = app.snapshot
        service = Text()
        if app.offline:
            service.append(f"{app.offline}\n", ERROR)
            service.append("Start it with ", FAINT)
            service.append(START_SERVICE, TEXT)
        self.query_one(".service", Static).update(service)
        self.query_one(".service").display = bool(app.offline)

        def name(job: JobRecord) -> str:
            video = snapshot.video(job.video_id)
            filename = video.filename if video and video.filename else "untitled"
            return term.pad(term.middle(filename, 24), 26)

        failures = [
            Option(
                Text.assemble(
                    (term.pad(term.middle(failed.name, 24), 26), TEXT),
                    (term.clip(failed.summary, 36), ERROR),
                ),
                id=f"add:{index}",
            )
            for index, failed in enumerate(app.failures)
        ]
        running = [
            Option(
                Text(name(job), TEXT) + progress(job, app.frame, 10), id=f"job:{job.id}"
            )
            for job in snapshot.active()
        ]
        failed = failures + [
            Option(
                Text.assemble(
                    (name(job), TEXT),
                    (term.clip(FAILURES[job.operation.type], 36), ERROR),
                ),
                id=f"job:{job.id}",
            )
            for job in snapshot.failed()
        ]
        done = [
            Option(
                Text.assemble(
                    (name(job), TEXT),
                    (term.pad(describe(job.operation), 18), MUTED),
                    (ago(job.finished_at or job.created_at), FAINT),
                ),
                id=f"job:{job.id}",
            )
            for job in snapshot.finished()[:5]
        ]
        options: list[Option] = []
        for title, section in (
            ("Running", running),
            ("Failed", failed),
            ("Done", done),
        ):
            if section:
                if options:
                    options.append(Option(Text(""), disabled=True))
                options += [Option(Text(title, FAINT), disabled=True), *section]
        if not options:
            options.append(Option(Text("Nothing running.", FAINT), disabled=True))
        fill(self.query_one(".rows", OptionList), options)

        option = self.query_one(".rows", OptionList).highlighted_option
        pairs = [("⏎", "open")] if option is not None and option.id else []
        if self.failure():
            pairs += [("d", "details"), ("y", "copy error")]
        self.query_one(KeyBar).show(app.message(), [*pairs, ("esc", "close")])

    @on(OptionList.OptionHighlighted)
    def highlighted(self) -> None:
        self.update_view()

    @on(OptionList.OptionSelected)
    def chosen(self, event: OptionList.OptionSelected) -> None:
        kind, _, value = (event.option.id or "").partition(":")
        if kind == "add":
            self.action_details()
            return
        video_id = self.pipeline.snapshot.job(value).video_id
        self.dismiss()
        self.pipeline.push_screen(VideoScreen(video_id))

    def action_details(self) -> None:
        failure = self.failure()
        if failure:
            self.pipeline.push_screen(ErrorSheet(*failure))

    def action_copy(self) -> None:
        failure = self.failure()
        if failure:
            self.pipeline.copy_error(failure[1])


class AddSheet(ModalScreen[tuple[Path, bool] | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", show=False)]

    def __init__(self, can_index: bool) -> None:
        super().__init__()
        self.can_index = can_index

    def compose(self) -> ComposeResult:
        with Vertical(classes="sheet"):
            yield Static(Text("Add video", STRONG), classes="title")
            with Horizontal(classes="field"):
                yield Input(
                    placeholder="path to a video file",
                    suggester=PathCompleter(),
                    compact=True,
                )
            yield Choice(
                "Make searchable", value=self.can_index, disabled=not self.can_index
            )
            if not self.can_index:
                message = "Start the job service to make videos searchable."
                yield Static(Text(message, FAINT))
            yield Static(classes="problem")
            yield Static(
                keys(
                    [
                        ("→", "complete"),
                        ("⏎", "add"),
                        ("esc", "cancel"),
                    ]
                ),
                classes="hint",
            )

    @on(Input.Submitted)
    def submitted(self, event: Input.Submitted) -> None:
        path = Path(event.value.strip()).expanduser()
        if not path.is_file():
            self.query_one(".problem", Static).update(
                Text("No video file at that path.", ERROR)
            )
            return
        self.dismiss((path, self.query_one(Choice).value))


class Ask(ModalScreen[str | None]):
    """One question with a typed answer."""

    BINDINGS = [Binding("escape", "dismiss(None)", show=False)]

    def __init__(self, title: str, placeholder: str, value: str) -> None:
        super().__init__()
        self.heading, self.placeholder, self.value = title, placeholder, value

    def compose(self) -> ComposeResult:
        with Vertical(classes="sheet"):
            yield Static(Text(self.heading, STRONG), classes="title")
            with Horizontal(classes="field"):
                yield Input(self.value, placeholder=self.placeholder, compact=True)
            yield Static(keys([("⏎", "apply"), ("esc", "cancel")]), classes="hint")

    @on(Input.Submitted)
    def submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)


class ErrorSheet(ModalScreen[None]):
    """A short explanation, with the full diagnostics underneath."""

    BINDINGS = [
        Binding("escape", "dismiss", show=False),
        Binding("y", "copy"),
    ]

    def __init__(self, summary: str, diagnostics: str) -> None:
        super().__init__()
        self.summary, self.diagnostics = summary, redact(diagnostics)

    def compose(self) -> ComposeResult:
        with Vertical(classes="sheet wide"):
            yield Static(Text(self.summary, STRONG), classes="title")
            with VerticalScroll(classes="diagnostics"):
                yield Static(Text(self.diagnostics, MUTED))
            yield Static(keys([("y", "copy error"), ("esc", "close")]), classes="hint")

    def action_copy(self) -> None:
        cast(Pipeline, self.app).copy_error(self.diagnostics)


class Pipeline(App[None]):
    TITLE = "video pipeline"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { background: #0b0b0b; color: #d0d0d0; padding: 1 3 0 3; }
    .header { height: 1; margin: 0 0 1 0; }
    .header .title { width: 1fr; }
    .header .summary { width: auto; }
    .field {
        height: 3; padding: 0 1; margin: 0 0 1 0;
        border: round #3a3a3a; background: #0b0b0b;
    }
    .field:focus-within { border: round #767676; }
    .glyph { width: 2; color: #808080; }
    Input {
        background: #0b0b0b; color: #eeeeee; padding: 0;
        &:focus { background-tint: #0b0b0b 0%; }
        & > .input--placeholder, & > .input--suggestion { color: #808080; }
        & > .input--cursor { background: #eeeeee; color: #0b0b0b; }
    }
    OptionList.rows {
        height: 1fr; background: #0b0b0b; padding: 0;
        & > .option-list--option { padding: 0 1; }
        & > .option-list--option-highlighted {
            background: #161616; text-style: none;
        }
        &:focus { background-tint: #000000 0%; }
        &:focus > .option-list--option-highlighted { background: #222222; }
        & > .option-list--option-hover { background: #141414; }
    }
    KeyBar { dock: bottom; height: 1; margin: 1 0; }
    .empty { display: none; padding: 2 1; }
    Library.-empty .empty { display: block; }
    Library.-empty .rows { display: none; }

    .hero { height: auto; margin: 0 0 1 0; }
    .preview {
        width: 38; height: 12; border: round #262626;
        content-align: center middle; text-align: center;
    }
    .about { width: 1fr; height: auto; padding: 0 0 0 2; }
    VideoScreen.-narrow .hero .preview { display: none; }
    VideoScreen.-narrow .about { padding: 0; }
    .progress { height: auto; margin: 0 0 1 0; }
    .label { margin: 1 0 0 0; }
    OptionList.outputs { height: auto; max-height: 8; }
    .details { display: none; height: auto; max-height: 16; margin: 1 0 0 0; }
    VideoScreen.-details .details { display: block; }

    .body { height: 1fr; }
    .search-filter { height: auto; margin: 0 0 1 0; }
    .body .rows { width: 1fr; }
    .body .preview { margin: 0 0 0 2; }
    Results.-narrow .body .preview { display: none; }
    .video-frame {
        width: 1fr; height: 1fr;
        content-align: center middle; text-align: center;
    }
    .player-position { height: 1; margin: 1 0 0 0; }

    ModalScreen { background: #000000 55%; align: center middle; padding: 0; }
    .sheet {
        width: 64; height: auto; padding: 1 2;
        border: round #3a3a3a; background: #0f0f0f;
    }
    .sheet.wide { width: 90%; max-height: 80%; }
    .sheet .title { margin: 0 0 1 0; }
    .sheet .field, .sheet Input { background: #0f0f0f; }
    .sheet .hint { margin: 1 0 0 0; }
    .diagnostics { height: auto; max-height: 20; }
    Choice { height: 1; }
    Choice:focus { text-style: underline; }
    Activity { align: right top; background: #000000 45%; }
    .panel {
        width: 70; max-width: 100%; height: 100%; padding: 1 2;
        background: #0f0f0f; border-left: solid #2a2a2a;
    }
    .panel .title, .panel .service { margin: 0 0 1 0; }
    .panel .rows { background: #0f0f0f; }
    """
    BINDINGS = [Binding("ctrl+c,ctrl+q", "quit", show=False, priority=True)]

    def __init__(self, data_dir: Path, device: str | None = None) -> None:
        # On macOS this may spawn a helper that needs stderr's real descriptor.
        # Initialize before Textual captures output and model loading uses tqdm.
        tqdm.get_lock()
        super().__init__()
        self.videos = VideoStore(data_dir)
        self.searches = SearchStore(self.videos.database_path)
        self.device = device
        self.jobs: JobStore | None = None
        self.offline: str | None = None
        self.connecting = False
        self.refreshing = False
        self.snapshot = Snapshot()
        self.adding: dict[Path, str] = {}
        self.failures: list[Failure] = []
        self.busy: dict[int, str] = {}
        self.flash: tuple[Text, float] | None = None
        self.frame = 0
        self._text_embedder: Embedder | None = None

    def get_default_screen(self) -> Screen:
        return Library()

    def on_mount(self) -> None:
        self.videos.initialize()
        self.searches.initialize()
        self.reconnect()
        self.reload()
        self.set_interval(1, self.reload)
        self.set_interval(5, self.reconnect)
        self.set_interval(0.08, self.tick)

    def on_unmount(self) -> None:
        if self.jobs is not None:
            self.jobs.close()

    # Data

    def load(self) -> Snapshot:
        """The one place the app reads stored state."""
        videos = self.videos.list()[::-1]
        library = {video.id for video in videos}
        jobs = self.jobs.list() if self.jobs is not None else []
        return Snapshot(
            videos=videos,
            ready={record.video_id for record in self.searches.indexes()},
            # The job database can hold other libraries' history.
            jobs=[job for job in jobs if job.video_id in library],
        )

    def reload(self) -> None:
        if not self.refreshing:
            self.refreshing = True
            self.refresh_snapshot()

    @work(thread=True)
    def refresh_snapshot(self) -> None:
        try:
            snapshot = self.load()
        except (psycopg.Error, PoolTimeout):
            self.call_from_thread(self.disconnect)
            return
        finally:
            self.refreshing = False
        self.call_from_thread(self.apply, snapshot)

    def apply(self, snapshot: Snapshot) -> None:
        for video in snapshot.videos:
            before = self.snapshot.video(video.id)
            if before is None:
                continue
            was, now = self.snapshot.state(before)[0], snapshot.state(video)[0]
            name = video.filename or "untitled"
            if was == "indexing" and now == "ready":
                self.notice(Text(f"{name} is ready to search", TEXT), 4)
            elif was == "indexing" and now == "failed":
                message = f"Couldn't make {name} searchable · ! activity"
                self.notice(Text(message, ERROR), 6)
        self.snapshot = snapshot
        self.update_views()

    def reconnect(self) -> None:
        if self.jobs is None and not self.connecting:
            self.connecting = True
            self.connect()

    @work(thread=True)
    def connect(self) -> None:
        try:
            store = JobStore(self.videos.database_path, database_url())
        except ValueError:
            reason = "Set VIDEO_PIPELINE_DATABASE_URL to reach the job service."
            self.call_from_thread(self.go_offline, reason)
            return
        try:
            store.open()
        except (psycopg.Error, PoolTimeout):
            self.call_from_thread(self.go_offline, "The job service isn't running.")
            return
        try:
            store.require_schema()
        except ValueError:
            store.close()
            self.call_from_thread(self.go_offline, "The job database isn't set up yet.")
            return
        self.call_from_thread(self.go_online, store)

    def go_online(self, store: JobStore) -> None:
        self.jobs, self.offline, self.connecting = store, None, False
        self.reload()

    def go_offline(self, reason: str) -> None:
        self.offline, self.connecting = reason, False
        self.update_views()

    def disconnect(self) -> None:
        if self.jobs is not None:
            self.jobs.close()
            self.jobs = None
        self.go_offline("Lost the connection to the job service.")

    def output(self, job: JobRecord) -> Path:
        """Where a finished job's file is; known without the job service."""
        assert job.output is not None
        directory = outputs_dir(self.videos.database_path)
        return directory / str(job.id) / job.output.filename

    # Work

    @work(thread=True)
    def add(self, path: Path, searchable: bool) -> None:
        self.call_from_thread(self.track, path, True)
        try:
            with path.open("rb") as source:
                video = self.videos.add(source, path.name)
        except (subprocess.CalledProcessError, ValueError, OSError) as error:
            summary = "This file couldn't be read as a video."
            failure = Failure(path.name, summary, explain(error))
            self.call_from_thread(self.add_failed, failure)
            return
        finally:
            self.call_from_thread(self.track, path, False)
        self.call_from_thread(self.added, video, searchable)

    def track(self, path: Path, adding: bool) -> None:
        if adding:
            self.adding[path] = path.name
        else:
            self.adding.pop(path)
        self.update_views()

    def add_failed(self, failure: Failure) -> None:
        self.failures.append(failure)
        self.notice(Text(f"Couldn't add {failure.name} · ! activity", ERROR), 6)

    def added(self, video: VideoRecord, searchable: bool) -> None:
        library = self.screen_stack[0]
        if isinstance(library, Library):
            library.select = str(video.id)
        if searchable:
            self.submit(video, IndexOperation(type="index"))
        self.reload()

    @work(thread=True)
    def submit(self, video: VideoRecord, operation: ProcessingOperation) -> None:
        jobs = self.jobs
        if jobs is None:
            message = f"Start the job service first: {START_SERVICE}"
            self.call_from_thread(self.notice, Text(message, ERROR), 6)
            return
        try:
            if isinstance(operation, IndexOperation) and not video.duration_seconds:
                raise ValueError("Video duration is unknown.")
            if isinstance(operation, ClipOperation):
                operation.validate_source_duration(video.duration_seconds)
        except ValueError as error:
            self.call_from_thread(self.notice, Text(str(error), ERROR), 6)
            return
        try:
            jobs.create(video.id, operation)
        except (psycopg.Error, PoolTimeout):
            self.call_from_thread(self.disconnect)
            return
        self.call_from_thread(self.reload)

    def text_embedder(self) -> Embedder:
        if self._text_embedder is None:
            self._text_embedder = Embedder(self.device, text_only=True)
        return self._text_embedder

    @work(thread=True, exclusive=True, group="warm")
    def warm(self) -> None:
        if self._text_embedder is not None:
            return
        embedder = self.text_embedder()
        self.call_from_thread(self.working, id(embedder), "loading search")
        try:
            embedder.queries(["warm up"])
        except (OSError, ValueError):
            pass  # The search that needs the model reports the problem.
        finally:
            self.call_from_thread(self.done, id(embedder))

    def play(
        self, path: Path, start: float = 0, end: float | None = None, label: str = ""
    ) -> None:
        self.push_screen(PlayerScreen(path, start, end, label))

    def open_external(
        self, path: Path, start: float = 0, end: float | None = None, label: str = ""
    ) -> None:
        command = player(path, start, end)
        if command is None:
            self.notice(Text("Install mpv or ffplay to play videos.", ERROR), 4)
            return
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.notice(Text.assemble(("playing ", MUTED), (label, TEXT)))

    def copy_error(self, diagnostics: str) -> None:
        self.copy_to_clipboard(redact(diagnostics))
        self.notice(Text("Error copied", MUTED))

    # Status

    def working(self, key: int, label: str) -> None:
        self.busy[key] = label
        self.update_views()

    def done(self, key: int) -> None:
        self.busy.pop(key, None)
        self.update_views()

    def notice(self, text: Text, duration: float = 1.6) -> None:
        self.flash = (text, time.monotonic() + duration)
        self.update_views()

    def message(self) -> Text:
        if self.busy:
            label = next(reversed(self.busy.values()))
            if self.animation_level == "none":
                return Text(label, MUTED)
            text = Text()
            for char, shade in term.shimmer(label, self.frame):
                text.append(char, Style(color=Color.from_ansi(shade)))
            return text
        if self.flash is not None:
            return self.flash[0]
        return Text("")

    def tick(self) -> None:
        self.frame += 1
        if self.flash is not None and self.flash[1] < time.monotonic():
            self.flash = None
            self.update_views()
        elif self.busy or self.adding or self.snapshot.active():
            self.update_views()

    def update_views(self) -> None:
        for screen in self.screen_stack:
            if isinstance(screen, View | Activity) and screen.is_mounted:
                screen.update_view()


def run(data_dir: Path, device: str | None = None) -> None:
    Pipeline(data_dir, device).run()
