"""Terminal rendering: a quiet, monochrome visual language."""

import os
import shutil
import sys
import threading
from collections.abc import Iterable
from typing import TextIO

from video_pipeline.inspection import VideoInfo

RESET = "\033[0m"

# Grayscale ramp, from strongest to faintest.
STRONG = "1;38;5;255"
TEXT = "38;5;252"
MUTED = "38;5;248"
FAINT = "38;5;244"
GHOST = "38;5;238"
ERROR = "38;5;203"

WORDMARK_FONT = {
    "V": ("╻ ╻", "┃┏┛", "┗┛ "),
    "I": ("╻", "┃", "╹"),
    "D": ("╺┳┓", " ┃┃", "╺┻┛"),
    "E": ("┏━╸", "┣╸ ", "┗━╸"),
    "O": ("┏━┓", "┃ ┃", "┗━┛"),
    "P": ("┏━┓", "┣━┛", "╹  "),
    "L": ("╻  ", "┃  ", "┗━╸"),
    "N": ("┏┓╻", "┃┗┫", "╹ ╹"),
    " ": ("  ", "  ", "  "),
}
SHIMMER = (255, 253, 251, 249)


def color_enabled(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return stream.isatty() and os.environ.get("TERM") != "dumb"


class Painter:
    def __init__(self, stream: TextIO) -> None:
        self.stream = stream
        self.enabled = color_enabled(stream)

    def __call__(self, text: str, style: str) -> str:
        if not self.enabled or not text:
            return text
        return f"\033[{style}m{text}{RESET}"

    def strong(self, text: str) -> str:
        return self(text, STRONG)

    def text(self, text: str) -> str:
        return self(text, TEXT)

    def muted(self, text: str) -> str:
        return self(text, MUTED)

    def faint(self, text: str) -> str:
        return self(text, FAINT)

    def ghost(self, text: str) -> str:
        return self(text, GHOST)

    def error(self, text: str) -> str:
        return self(text, ERROR)


out = Painter(sys.stdout)
err = Painter(sys.stderr)


def width() -> int:
    return shutil.get_terminal_size((80, 24)).columns


def emit(*lines: str, stream: TextIO = sys.stdout) -> None:
    for line in lines:
        print(f"  {line}" if line else "", file=stream, flush=True)


def pad(text: str, size: int) -> str:
    return text + " " * max(0, size - len(text))


def clip(text: str, size: int) -> str:
    return text if len(text) <= size else text[: max(0, size - 1)] + "…"


def dot(paint: Painter = out) -> str:
    return paint.faint(" · ")


def timecode(seconds: float) -> str:
    tenths = round(seconds * 10)
    hours, rest = divmod(tenths, 36_000)
    minutes, rest = divmod(rest, 600)
    whole, fraction = divmod(rest, 10)
    clock = f"{hours}:{minutes:02}:{whole:02}" if hours else f"{minutes}:{whole:02}"
    return f"{clock}.{fraction}" if fraction else clock


def elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, whole = divmod(round(seconds), 60)
    return f"{minutes}m{whole:02}s"


def filesize(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1000 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    raise AssertionError


def score(value: float) -> str:
    text = f"{value:.2f}"
    return text.replace("0.", ".", 1) if 0 <= value < 1 else text


def shimmer(label: str, frame: int) -> list[tuple[str, int]]:
    """Each character with a gray shade; a highlight sweeps across the word."""
    center = frame % (len(label) + 8) - 4
    return [
        (char, SHIMMER[distance] if distance < len(SHIMMER) else 246)
        for char, distance in ((c, abs(i - center)) for i, c in enumerate(label))
    ]


def facts(info: VideoInfo) -> list[tuple[str, list[str]]]:
    """A video's essentials as labelled parts, for any renderer."""
    rows = [
        (
            "length",
            [timecode(info.duration_seconds)]
            if info.duration_seconds is not None
            else [],
        )
    ]
    for number, stream in enumerate(info.video_streams, 1):
        parts = [stream.codec or "?", f"{stream.width}×{stream.height}"]
        if stream.average_frame_rate is not None:
            rate = f"{float(stream.average_frame_rate):.2f}".rstrip("0").rstrip(".")
            parts.append(f"{rate} fps")
        label = "video" if len(info.video_streams) == 1 else f"video {number}"
        rows.append((label, parts))
    rows.append(("audio", [stream.codec or "?" for stream in info.audio_streams]))
    return rows


def meter(fraction: float, cells: int, paint: Painter = out) -> str:
    filled = round(max(0.0, min(1.0, fraction)) * cells)
    return paint.text("━" * filled) + paint.ghost("━" * (cells - filled))


def wordmark(text: str, paint: Painter = out) -> list[str]:
    """Thin box-drawn letters, fading from white to gray."""
    glyphs = [WORDMARK_FONT[letter] for letter in text.upper()]
    rows = ["".join(glyph[row] for glyph in glyphs) for row in range(3)]
    span = max(len(rows[0]) - 1, 1)
    return [
        "".join(
            paint(char, f"38;5;{255 - round(column / span * 12)}")
            if char != " "
            else char
            for column, char in enumerate(row)
        )
        for row in rows
    ]


def columns(rows: Iterable[tuple[str, str]], paint: Painter = out) -> list[str]:
    rows = list(rows)
    size = max((len(label) for label, _ in rows), default=0) + 3
    return [paint.muted(pad(label, size)) + value for label, value in rows]


class Live:
    """A single self-erasing status line on stderr."""

    def __init__(self) -> None:
        self.enabled = sys.stderr.isatty()
        self._lock = threading.Lock()
        self._line = ""

    def show(self, line: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._line = line
            sys.stderr.write(f"\r\033[2K  {line}")
            sys.stderr.flush()

    def clear(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            if self._line:
                sys.stderr.write("\r\033[2K")
                sys.stderr.flush()
            self._line = ""


class Activity(Live):
    """An animated status line, optionally with a progress bar."""

    def __init__(self, label: str, total: float | None = None) -> None:
        super().__init__()
        self.label = clip(label, 32)
        self.cells = max(8, min(28, width() - len(self.label) - 36))
        self.total = total
        self.seconds: float | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._animate, daemon=True)

    def update(self, seconds: float) -> None:
        self.seconds = seconds

    def _render(self, frame: int) -> str:
        line = "".join(
            err(c, f"38;5;{shade}") for c, shade in shimmer(self.label, frame)
        )
        if self.seconds is None:
            return line
        if not self.total:
            return f"{line}  {err.faint(timecode(self.seconds))}"
        fraction = min(1.0, self.seconds / self.total)
        return (
            f"{line}  {meter(fraction, self.cells, err)}  "
            f"{err.text(f'{fraction * 100:3.0f}%')}  "
            f"{err.faint(f'{timecode(self.seconds)} / {timecode(self.total)}')}"
        )

    def _animate(self) -> None:
        frame_index = 0
        while not self._stop.is_set():
            self.show(self._render(frame_index))
            frame_index += 1
            self._stop.wait(0.05)

    def __enter__(self) -> "Activity":
        if self.enabled:
            self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join()
        self.clear()
