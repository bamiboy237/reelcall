"""The interactive app: type to find a moment, or / for commands."""

import shlex
import shutil
import subprocess
import time
from io import BytesIO
from pathlib import Path

from PIL import Image
from rich.color import Color
from rich.style import Style
from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.suggester import Suggester
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option
from textual.worker import get_current_worker

from video_pipeline import term
from video_pipeline.cli import explain, shown
from video_pipeline.embeddings import Embedder
from video_pipeline.search import (
    IndexSettings,
    SearchHit,
    SearchMode,
    SearchRequest,
    SearchStore,
)
from video_pipeline.storage import VideoRecord, VideoStore

STRONG = Style.parse("bold color(255)")
TEXT = Style.parse("color(252)")
MUTED = Style.parse("color(248)")
FAINT = Style.parse("color(244)")
GHOST = Style.parse("color(238)")
ERROR = Style.parse("color(203)")

MODES: tuple[SearchMode, ...] = ("all", "visual", "audio", "combined")
SLASH = (
    ("/index", "path…", "make searchable"),
    ("/inspect", "path", "streams and length"),
    ("/process", "path height", "resize and thumbnail"),
    ("/save", "", "keep the highlighted moment"),
    ("/videos", "", "library"),
    ("/mode", "kind", "sight, sound, or both"),
    ("/clear", "", "start over"),
    ("/quit", "", "leave"),
)
PATH_COMMANDS = ("/index", "/inspect", "/process")


def wordmark() -> Text:
    rows = [
        "".join(term.WORDMARK_FONT[c][row] for c in "VIDEO PIPELINE")
        for row in range(3)
    ]
    span = len(rows[0]) - 1
    text = Text()
    for row in rows:
        for column, char in enumerate(row):
            text.append(
                char, Style(color=Color.from_ansi(255 - round(column / span * 12)))
            )
        text.append("\n")
    return text


def meter(fraction: float, cells: int) -> Text:
    filled = round(max(0.0, min(1.0, fraction)) * cells)
    return Text.assemble(("━" * filled, TEXT), ("━" * (cells - filled), GHOST))


def menu_rows(rows: tuple[tuple[str, str, str], ...]) -> list[Text]:
    name_size = max(len(name) for name, _, _ in rows) + 3
    argument_size = max(len(arguments) for _, arguments, _ in rows) + 3
    return [
        Text.assemble(
            (term.pad(name, name_size), TEXT),
            (term.pad(arguments, argument_size), FAINT),
            (blurb, MUTED),
        )
        for name, arguments, blurb in rows
    ]


def pixels(data: bytes, width: int = 36, rows: int = 10) -> Text:
    """Render an image with half blocks: two pixels per cell."""
    image = Image.open(BytesIO(data)).convert("RGB")
    image.thumbnail((width, rows * 2), Image.Resampling.LANCZOS)
    columns, height = image.size
    raw = image.tobytes()

    def color(x: int, y: int) -> Color:
        offset = (y * columns + x) * 3
        return Color.from_rgb(raw[offset], raw[offset + 1], raw[offset + 2])

    text = Text()
    for y in range(0, height, 2):
        for x in range(columns):
            bottom = color(x, y + 1) if y + 1 < height else None
            text.append("▀", Style(color=color(x, y), bgcolor=bottom))
        text.append("\n")
    return text


def span(hit: SearchHit) -> str:
    return f"{term.timecode(hit.start_seconds)} – {term.timecode(hit.end_seconds)}"


def player(path: Path, start: float, end: float) -> list[str] | None:
    if shutil.which("mpv"):
        return ["mpv", f"--start={start}", f"--end={end}", "--really-quiet", str(path)]
    if shutil.which("ffplay"):
        return [
            "ffplay", "-v", "quiet", "-autoexit",
            "-ss", f"{start}", "-t", f"{end - start}", str(path),
        ]  # fmt: skip
    for opener in ("open", "xdg-open"):
        if shutil.which(opener):
            return [opener, str(path)]
    return None


def facts(info_rows: list[tuple[str, list[str]]]) -> Text:
    size = max(len(label) for label, _ in info_rows) + 3
    text = Text()
    for label, parts in info_rows:
        text.append(term.pad(label, size), MUTED)
        if parts:
            text.append(" · ".join(parts), TEXT)
        else:
            text.append("none", FAINT)
        text.append("\n")
    text.rstrip()
    return text


class Completer(Suggester):
    """Ghost-text completion for commands, paths and modes."""

    def __init__(self) -> None:
        super().__init__(use_cache=False, case_sensitive=True)

    async def get_suggestion(self, value: str) -> str | None:
        if not value.startswith("/"):
            return None
        command, space, rest = value.partition(" ")
        if not space:
            for name, _, _ in SLASH:
                if name.startswith(command) and name != command:
                    return name + " "
            return None
        token = rest.rsplit(" ", 1)[-1]
        if command == "/mode":
            return next(
                (
                    value + mode[len(token) :]
                    for mode in MODES
                    if mode.startswith(token)
                ),
                None,
            )
        if command not in PATH_COMMANDS or not token:
            return None
        base = Path(token).expanduser()
        folder, prefix = (base, "") if token.endswith("/") else (base.parent, base.name)
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


class Moments(OptionList, can_focus=False):
    pass


class Results(Horizontal):
    """Search matches with a live preview of the highlighted moment."""

    def __init__(self, hits: list[SearchHit], thumbnails: list[bytes | None]) -> None:
        super().__init__(classes="results")
        self.hits = hits
        self.thumbnails = thumbnails
        self._previews: dict[int, Text] = {}

    def compose(self) -> ComposeResult:
        spans = [span(hit) for hit in self.hits]
        names = [
            term.clip(hit.filename or str(hit.video_id)[:8], 28) for hit in self.hits
        ]
        span_size = max(map(len, spans)) + 3
        name_size = max(map(len, names)) + 3
        options = []
        for hit, when, name in zip(self.hits, spans, names, strict=True):
            options.append(
                Option(
                    Text.assemble(
                        (term.pad(when, span_size), TEXT),
                        (term.pad(name, name_size), MUTED),
                        (term.pad(hit.modality, 10), FAINT),
                        (term.score(hit.score), FAINT),
                    )
                )
            )
        yield Moments(*options, compact=True)
        yield Static(classes="preview")

    def on_mount(self) -> None:
        self.query_one(Moments).highlighted = 0

    @property
    def selected(self) -> SearchHit | None:
        index = self.query_one(Moments).highlighted
        return self.hits[index] if index is not None else None

    def move(self, delta: int) -> None:
        moments = self.query_one(Moments)
        current = moments.highlighted or 0
        moments.highlighted = max(0, min(len(self.hits) - 1, current + delta))

    def retire(self) -> None:
        self.add_class("-stale")
        self.query_one(Moments).disabled = True

    @on(OptionList.OptionHighlighted)
    def preview(self, event: OptionList.OptionHighlighted) -> None:
        index = event.option_index
        if index not in self._previews:
            image = self.thumbnails[index]
            self._previews[index] = pixels(image) if image else Text("")
        self.query_one(".preview", Static).update(self._previews[index])

    @on(OptionList.OptionSelected)
    def play(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        app = self.app
        if isinstance(app, Pipeline):
            app.play(self.hits[event.option_index])


class Pipeline(App[None]):
    TITLE = "video pipeline"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { background: #0b0b0b; color: #d0d0d0; }
    #log { height: 1fr; padding: 1 3 0 3; scrollbar-size-vertical: 0; }
    #welcome { margin: 1 0 2 0; }
    .turn { height: auto; margin: 0 0 1 0; }
    .echo { margin: 0 0 1 0; }
    .body { height: auto; padding: 0 0 0 2; }
    .results { height: auto; }
    .results Moments {
        width: auto; max-width: 1fr; background: #0b0b0b; padding: 0;
        & > .option-list--option { padding: 0 1; }
        & > .option-list--option-highlighted { background: #1a1a1a; text-style: none; }
        & > .option-list--option-hover { background: #141414; }
    }
    .results .preview { width: 40; height: auto; padding: 0 0 0 3; }
    .results.-stale { opacity: 45%; }
    Screen.-narrow .results .preview { display: none; }
    .results.-stale .preview { display: none; }
    .results.-stale Moments > .option-list--option-highlighted { background: #0b0b0b; }
    #menu {
        display: none; width: auto; height: auto; max-height: 9;
        margin: 1 3 0 3; padding: 0; background: #0b0b0b;
        & > .option-list--option { padding: 0 1; }
        & > .option-list--option-highlighted { background: #1a1a1a; text-style: none; }
    }
    #menu.-open { display: block; }
    #prompt {
        height: 3; margin: 0 2; padding: 0 1;
        border: round #3a3a3a; background: #0b0b0b;
    }
    #prompt:focus-within { border: round #767676; }
    #caret { width: 2; color: #808080; }
    #input {
        background: #0b0b0b; color: #eeeeee; padding: 0;
        &:focus { background-tint: #0b0b0b 0%; }
        & > .input--placeholder, & > .input--suggestion { color: #808080; }
        & > .input--cursor { background: #eeeeee; color: #0b0b0b; }
    }
    #state { height: 1; margin: 0 4 1 4; }
    """
    BINDINGS = [
        Binding("up", "move(-1)", show=False, priority=True),
        Binding("down", "move(1)", show=False, priority=True),
        Binding("tab", "complete", show=False, priority=True),
        Binding("escape", "dismiss", show=False, priority=True),
        Binding("ctrl+s", "export", show=False, priority=True),
        Binding("ctrl+l", "clear", show=False, priority=True),
        Binding("ctrl+c,ctrl+q", "quit", show=False, priority=True),
    ]

    def __init__(self, data_dir: Path, device: str | None = None) -> None:
        super().__init__()
        self.videos = VideoStore(data_dir)
        self.searches = SearchStore(self.videos.database_path)
        self.device = device
        self.mode: SearchMode = "all"
        self.active: Results | None = None
        self.busy: dict[int, str] = {}
        self.frame = 0
        self.flash: tuple[Text, float] | None = None
        self.history: list[str] = []
        self.recall: int | None = None
        self._text_embedder: Embedder | None = None
        self._media_embedder: Embedder | None = None

    # Layout

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="log"):
            yield Static(wordmark(), id="welcome")
        yield OptionList(id="menu", compact=True)
        with Horizontal(id="prompt"):
            yield Static("›", id="caret")
            yield Input(
                placeholder="describe a moment",
                suggester=Completer(),
                id="input",
                compact=True,
            )
        yield Static(id="state")

    def on_mount(self) -> None:
        self.videos.initialize()
        self.searches.initialize()
        self.library = self.videos.list()
        self.indexed = {record.video_id for record in self.searches.indexes()}
        self.query_one(Input).focus()
        self.set_interval(0.05, self.tick)
        self.refresh_status()

    def follow(self) -> None:
        self.call_after_refresh(self.transcript.scroll_end, animate=False)

    @property
    def transcript(self) -> VerticalScroll:
        return self.query_one("#log", VerticalScroll)

    async def turn(self, prompt: str) -> Vertical:
        body = Vertical(classes="body")
        turn = Vertical(
            Static(Text.assemble(("› ", FAINT), (prompt, STRONG)), classes="echo"),
            body,
            classes="turn",
        )
        await self.transcript.mount(turn)
        self.follow()
        return body

    def say(self, body: Vertical, content: Text) -> Static:
        widget = Static(content)
        body.mount(widget)
        self.follow()
        return widget

    def fail(self, body: Vertical, error: Exception) -> None:
        message = explain(error).splitlines() or ["failed"]
        self.say(
            body,
            Text.assemble(
                (message[0], ERROR),
                *(("\n" + line, FAINT) for line in message[1:]),
            ),
        )

    # Status line

    def on_resize(self, event: events.Resize) -> None:
        self.screen.set_class(event.size.width < 100, "-narrow")

    def tick(self) -> None:
        if self.busy:
            self.frame += 1
            self.refresh_status()
        elif self.flash is not None and self.flash[1] < time.monotonic():
            self.flash = None
            self.refresh_status()

    def notice(self, text: Text, seconds: float = 1.6) -> None:
        self.flash = (text, time.monotonic() + seconds)
        self.refresh_status()

    def refresh_status(self) -> None:
        if not self.query("#state"):
            return  # Shutting down.
        state = Text()
        if self.busy:
            label = next(reversed(self.busy.values()))
            if self.animation_level == "none":
                state.append(label, MUTED)
            else:
                for char, shade in term.shimmer(label, self.frame):
                    state.append(char, Style(color=Color.from_ansi(shade)))
        elif self.flash is not None:
            state = self.flash[0]
        else:
            state.append(f"{len(self.library)} videos", MUTED)
            state.append(" · ", FAINT)
            state.append(f"{len(self.indexed)} indexed", MUTED)
            if self.mode != "all":
                state.append(" · ", FAINT)
                state.append(self.mode, TEXT)
        self.query_one("#state", Static).update(state)
        self.query_one(Input).placeholder = (
            "" if self.query(".turn") else "describe a moment"
        )

    def working(self, key: int, label: str) -> None:
        self.busy[key] = label
        self.refresh_status()

    def done(self, key: int) -> None:
        self.busy.pop(key, None)
        self.refresh_status()

    # Input

    @on(Input.Changed)
    def changed(self, event: Input.Changed) -> None:
        menu = self.query_one("#menu", OptionList)
        value = event.value
        if value.startswith("/") and " " not in value:
            matches = [row for row in SLASH if row[0].startswith(value)]
            menu.clear_options()
            menu.add_options(
                Option(row, id=name)
                for (name, _, _), row in zip(matches, menu_rows(tuple(matches)))
            )
            menu.set_class(bool(matches), "-open")
            if matches:
                menu.highlighted = 0
        else:
            menu.remove_class("-open")
        if value and not value.startswith("/"):
            self.warm()
        self.refresh_status()

    @on(Input.Submitted)
    async def submitted(self, event: Input.Submitted) -> None:
        menu = self.query_one("#menu", OptionList)
        value = event.value.strip()
        if menu.has_class("-open") and menu.highlighted is not None:
            option = menu.get_option_at_index(menu.highlighted)
            if option.id and value != option.id and option.id.startswith(value):
                value = option.id
        event.input.clear()
        menu.remove_class("-open")
        if value:
            self.history.append(value)
        self.recall = None
        if not value:
            if self.active is not None and self.active.selected is not None:
                self.play(self.active.selected)
            return
        if value.startswith("/"):
            await self.command(value)
        else:
            self.search(value, await self.turn(value))

    def action_move(self, delta: int) -> None:
        menu = self.query_one("#menu", OptionList)
        if menu.has_class("-open"):
            current = menu.highlighted or 0
            menu.highlighted = max(0, min(menu.option_count - 1, current + delta))
        elif self.active is not None:
            self.active.move(delta)
        elif self.history:
            field = self.query_one(Input)
            last = len(self.history)
            self.recall = max(
                0, min(last, (last if self.recall is None else self.recall) + delta)
            )
            field.value = self.history[self.recall] if self.recall < last else ""
            field.cursor_position = len(field.value)

    def action_complete(self) -> None:
        field = self.query_one(Input)
        menu = self.query_one("#menu", OptionList)
        if menu.has_class("-open") and menu.highlighted is not None:
            option = menu.get_option_at_index(menu.highlighted)
            field.value = f"{option.id} "
        elif field._suggestion:
            field.value = field._suggestion
        field.cursor_position = len(field.value)

    def action_dismiss(self) -> None:
        menu = self.query_one("#menu", OptionList)
        if menu.has_class("-open"):
            menu.remove_class("-open")
        else:
            self.query_one(Input).clear()

    def action_clear(self) -> None:
        self.active = None
        for turn in self.query(".turn"):
            turn.remove()
        self.refresh_status()

    async def action_export(self) -> None:
        if self.active is not None and self.active.selected is not None:
            hit = self.active.selected
            self.export(hit, await self.turn(f"save {span(hit)}"))

    # Commands

    async def command(self, line: str) -> None:
        try:
            name, *args = shlex.split(line)
        except ValueError as error:
            self.fail(await self.turn(line), error)
            return
        if name == "/quit":
            self.exit()
            return
        if name == "/clear":
            self.action_clear()
            return
        if name == "/save":
            if self.active is None:
                self.say(await self.turn(line), Text("search first", MUTED))
            else:
                await self.action_export()
            return
        body = await self.turn(line)
        if name == "/mode":
            if args and args[0] in MODES:
                self.mode = MODES[MODES.index(args[0])]
                self.refresh_status()
            self.say(
                body,
                Text(" · ").join(
                    Text(mode, STRONG if mode == self.mode else FAINT) for mode in MODES
                ),
            )
        elif name == "/videos":
            self.show_library(body)
        elif (
            name in ("/inspect", "/process")
            and args
            and not Path(args[0]).expanduser().is_file()
        ):
            self.say(body, Text(f"no such file  {args[0]}", ERROR))
        elif name == "/inspect" and len(args) == 1:
            self.inspect(Path(args[0]).expanduser(), body)
        elif name == "/index" and args:
            self.index(args, body)
        elif name == "/process" and len(args) == 2 and args[1].isdigit():
            self.process(Path(args[0]).expanduser(), int(args[1]), body)
        else:
            known = {row[0]: row for row in SLASH}
            help_rows = [known[name]] if name in known else SLASH
            self.say(body, Text("\n").join(menu_rows(tuple(help_rows))))

    def show_library(self, body: Vertical) -> None:
        if not self.library:
            self.say(body, Text.assemble(("empty", MUTED), ("  /index <path>", FAINT)))
            return
        names = [term.clip(video.filename or "untitled", 32) for video in self.library]
        size = max(map(len, names)) + 3
        text = Text()
        for video, name in zip(self.library, names, strict=True):
            indexed = video.id in self.indexed
            length = term.timecode(video.duration_seconds or 0)
            text.append("● " if indexed else "○ ", TEXT if indexed else FAINT)
            text.append(term.pad(name, size), TEXT)
            text.append(f"{length}\n", MUTED)
        text.rstrip()
        self.say(body, text)

    # Work

    def text_embedder(self) -> Embedder:
        if self._media_embedder is not None:
            return self._media_embedder
        if self._text_embedder is None:
            self._text_embedder = Embedder(self.device, text_only=True)
        return self._text_embedder

    def media_embedder(self) -> Embedder:
        if self._media_embedder is None:
            self._media_embedder = Embedder(self.device)
        return self._media_embedder

    @work(thread=True, exclusive=True, group="warm")
    def warm(self) -> None:
        if self._text_embedder is not None or self._media_embedder is not None:
            return
        embedder = self.text_embedder()
        key = id(embedder)
        self.call_from_thread(self.working, key, "loading model")
        try:
            embedder.queries(["warm up"])
        except Exception:
            pass  # Reported by the search that needs it.
        finally:
            self.call_from_thread(self.done, key)

    @work(thread=True, exclusive=True, group="search")
    def search(self, query: str, body: Vertical) -> None:
        key = id(body)
        self.call_from_thread(self.working, key, "searching")
        try:
            response = self.searches.search(
                SearchRequest(query=query, mode=self.mode, limit=8),
                self.text_embedder(),
            )
            thumbnails = [
                self.searches.thumbnail(hit.video_id, hit.window)
                for hit in response.matches
            ]
            if get_current_worker().is_cancelled:
                return  # A newer search took over.
            self.call_from_thread(self.show_results, body, response.matches, thumbnails)
        except Exception as error:
            self.call_from_thread(self.fail, body, error)
        finally:
            self.call_from_thread(self.done, key)

    def show_results(
        self, body: Vertical, hits: list[SearchHit], thumbnails: list[bytes | None]
    ) -> None:
        if not hits:
            empty = (
                Text("no matches", MUTED)
                if self.indexed
                else Text.assemble(
                    ("nothing indexed yet", MUTED), ("  /index <path>", FAINT)
                )
            )
            self.say(body, empty)
            return
        if self.active is not None:
            self.active.retire()
        results = Results(hits, thumbnails)
        body.mount(results)
        self.follow()
        self.active = results
        self.refresh_status()

    @work(thread=True)
    def inspect(self, path: Path, body: Vertical) -> None:
        from video_pipeline.inspection import inspect_video

        try:
            info = inspect_video(path)
            text = Text.assemble(
                (info.path.name, STRONG),
                ("  " + term.filesize(info.path.stat().st_size), FAINT),
                "\n\n",
                facts(term.facts(info)),
            )
            self.call_from_thread(self.say, body, text)
        except Exception as error:
            self.call_from_thread(self.fail, body, error)

    def progress(self, name: str, seconds: float | None, total: float | None) -> Text:
        text = Text.assemble((term.pad(term.clip(name, 28), 30), TEXT))
        if seconds is None or not total:
            return text
        fraction = min(1.0, seconds / total)
        return text.append_text(meter(fraction, 24)).append_text(
            Text.assemble(
                (f"  {fraction * 100:3.0f}%", TEXT),
                (f"  {term.timecode(seconds)} / {term.timecode(total)}", FAINT),
            )
        )

    @work(thread=True, group="index")
    def index(self, targets: list[str], body: Vertical) -> None:
        key = id(body)
        try:
            records: list[VideoRecord] = []
            for target in targets:
                path = Path(target).expanduser()
                if path.is_file():
                    self.call_from_thread(self.working, key, f"storing {path.name}")
                    with path.open("rb") as source:
                        records.append(self.videos.add(source, path.name))
                    continue
                matches = [
                    video
                    for video in self.videos.list()
                    if video.filename == target or str(video.id).startswith(target)
                ]
                if len(matches) != 1:
                    problem = "ambiguous" if matches else "no such file or video"
                    raise ValueError(f"{problem}  {target}")
                records.extend(matches)
            self.library = self.videos.list()

            embedder = self.media_embedder()
            for video in records:
                name = video.filename or str(video.id)[:8]
                total = video.duration_seconds
                line = self.call_from_thread(
                    self.say, body, self.progress(name, 0, total)
                )

                def update(
                    seconds: float,
                    name: str = name,
                    total: float | None = total,
                    line=line,
                ) -> None:
                    self.call_from_thread(
                        line.update, self.progress(name, seconds, total)
                    )
                    status = f"indexing {name}"
                    if total:
                        status += f"  {min(100, seconds / total * 100):.0f}%"
                    self.call_from_thread(self.working, key, status)

                self.call_from_thread(self.working, key, f"indexing {name}")
                result = self.searches.index(
                    video, self.videos, IndexSettings(), embedder, update
                )
                self.indexed.add(video.id)
                self.call_from_thread(
                    line.update,
                    Text.assemble(
                        (term.pad(term.clip(name, 28), 28), TEXT),
                        (f"{result.windows} windows", MUTED),
                        (" · ", FAINT),
                        (term.elapsed(result.elapsed_seconds), FAINT),
                    ),
                )
        except Exception as error:
            self.call_from_thread(self.fail, body, error)
        finally:
            self.call_from_thread(self.done, key)

    @work(thread=True)
    def process(self, path: Path, height: int, body: Vertical) -> None:
        from video_pipeline.inspection import inspect_video
        from video_pipeline.processing import process_video

        key = id(body)
        try:
            source = inspect_video(path)
            output = Path("outputs") / f"{path.stem}-{height}p"
            suffix = 1
            while output.exists():
                suffix += 1
                output = Path("outputs") / f"{path.stem}-{height}p-{suffix}"
            line = self.call_from_thread(
                self.say, body, self.progress("transcoding", 0, source.duration_seconds)
            )

            def update(seconds: float) -> None:
                self.call_from_thread(
                    line.update,
                    self.progress("transcoding", seconds, source.duration_seconds),
                )

            self.call_from_thread(self.working, key, f"transcoding {path.name}")
            result = process_video(path, height, output, update)
            self.call_from_thread(
                line.update,
                facts(
                    [
                        ("video", [shown(result.video_path)]),
                        ("thumbnail", [shown(result.thumbnail_path)]),
                    ]
                ),
            )
        except Exception as error:
            self.call_from_thread(self.fail, body, error)
        finally:
            self.call_from_thread(self.done, key)

    @work(thread=True)
    def export(self, hit: SearchHit, body: Vertical) -> None:
        from video_pipeline.inspection import inspect_video
        from video_pipeline.processing import clip_video

        key = id(body)
        try:
            stem = Path(hit.filename or str(hit.video_id)).stem
            moment = f"{hit.start_seconds:.0f}-{hit.end_seconds:.0f}"
            output = Path("outputs") / "clips" / f"{stem}-{moment}.mp4"
            output.parent.mkdir(parents=True, exist_ok=True)
            self.call_from_thread(self.working, key, "saving clip")
            source = inspect_video(self.videos.original_path(hit.video_id))
            clip_video(source, hit.start_seconds, hit.end_seconds, output)
            self.call_from_thread(self.say, body, Text(shown(output), TEXT))
        except Exception as error:
            self.call_from_thread(self.fail, body, error)
        finally:
            self.call_from_thread(self.done, key)

    def play(self, hit: SearchHit) -> None:
        command = player(
            self.videos.original_path(hit.video_id), hit.start_seconds, hit.end_seconds
        )
        if command is None:
            self.notice(Text("install mpv or ffplay to play", ERROR), 3)
            return
        try:
            subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as error:
            self.notice(Text(explain(error), ERROR), 3)
            return
        self.notice(Text.assemble(("playing ", MUTED), (span(hit), TEXT)))


def run(data_dir: Path, device: str | None = None) -> None:
    Pipeline(data_dir, device).run()
