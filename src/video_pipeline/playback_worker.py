"""Isolated FFmpeg decoder. Player methods stay on this process's main thread."""

import argparse
import base64
import json
import math
import sys
import time
from collections import deque
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Thread
from typing import Any

# The compiled tools module has no type stubs.
from ffpyplayer import tools  # ty: ignore[unresolved-import]
from ffpyplayer.player import MediaPlayer
from PIL import Image


class Decoder:
    def __init__(self, path: Path, start: float = 0, end: float | None = None) -> None:
        self.path = path
        self.start = start
        self.end = end
        self.position = start
        self.paused = False
        self.ended = False
        self.muted = False
        self.error: str | None = None
        self.closed = False
        self.ready = False
        self._events: SimpleQueue[tuple[str, str]] = SimpleQueue()
        self._logs: deque[str] = deque(maxlen=200)
        self._pending: tuple[Any, float] | None = None
        self._last_frame: tuple[float, tuple[int, int]] | None = None
        self._due = 0.0
        self._refresh = False
        self._size = (0, 0)
        self._frame_step = 1 / 30
        self._player: Any = None
        self._loglevel = tools.get_loglevel()
        self._log_callback = tools.set_log_callback(self._log)
        tools.set_loglevel("error")
        self._open(start)

    def _open(self, position: float) -> None:
        self._position_start = position
        trim = f"start={position}"
        options: dict[str, Any] = {
            "ss": position,
            "paused": self.paused,
            "out_fmt": "rgb24",
            "x": 96,
            "y": -1,
        }
        if self.end is not None:
            trim += f":end={self.end}"
            options["t"] = self.end - position
        # FFPyPlayer takes af as a single string, unlike vf's list form.
        options.update(vf=[f"trim={trim}"], af=f"atrim={trim}")
        try:
            self._player = MediaPlayer(
                str(self.path), ff_opts=options, callback=self._event, loglevel="error"
            )
            self._player.set_mute(self.muted)
        except Exception as error:
            # FFPyPlayer raises plain Exception for SDL setup failures.
            self.error = str(error)
            self.close()

    def _log(self, message: str, level: str) -> None:
        self._logs.append(message)

    def _event(self, kind: str, value: Any) -> None:
        self._events.put((kind, str(value)))

    @property
    def diagnostics(self) -> str:
        return "".join(self._logs).strip() or self.error or "Playback failed."

    def resize(self, columns: int, rows: int) -> None:
        if self.closed:
            return
        width, height = self._player.get_metadata()["src_vid_size"]
        if not width or not height:
            return
        scale = min(min(columns, 160) / width, rows * 2 / height)
        size = (max(2, int(width * scale)), max(2, int(height * scale)))
        if size != self._size:
            self._size = size
            self._player.set_size(*size)
            self._refresh = True

    def poll(self) -> Image.Image | None:
        if self.closed:
            return None
        while True:
            try:
                kind, value = self._events.get_nowait()
            except Empty:
                break
            if kind.endswith(":error"):
                self.error = value or "Playback failed."
                self.close()
                return None
            if kind == "eof":
                self.ended = True
        metadata = self._player.get_metadata()
        if not metadata["src_vid_size"][0]:
            return None
        if not self.ready:
            self._player.set_pause(self.paused)
        self.ready = True
        numerator, denominator = metadata["frame_rate"]
        if numerator:
            self._frame_step = denominator / numerator
        duration = metadata["duration"]
        if duration is not None:
            self.end = min(self.end, duration) if self.end is not None else duration
        clock = self._player.get_pts()
        if math.isfinite(clock):
            self.position = max(self._position_start, clock)
            if self.end is not None:
                self.position = min(self.position, self.end)
        if self.ended:
            if not self.paused:
                self.paused = True
                self._player.set_pause(True)
            if self.end is not None:
                self.position = self.end
            return None
        now = time.monotonic()
        if self._pending is None:
            frame, delay = self._player.get_frame(
                force_refresh=self.paused or self._refresh
            )
            if delay == "eof":
                self.ended = True
                self.paused = True
                self._player.set_pause(True)
                if self.end is not None:
                    self.position = self.end
                return None
            if frame is None:
                return None
            self._pending = frame
            self._due = now + max(0.0, float(delay))
            self._refresh = False
        if now < self._due:
            return None
        image, pts = self._pending
        self._pending = None
        identity = pts, image.get_size()
        if identity == self._last_frame:
            return None
        self._last_frame = identity
        return Image.frombytes("RGB", image.get_size(), bytes(image.to_bytearray()[0]))

    def toggle_pause(self) -> None:
        if self.closed:
            return
        if self.ended:
            self.seek(self.start)
            return
        self.paused = not self.paused
        if self.ready:
            self._player.set_pause(self.paused)
        self._pending = None
        self._refresh = True

    def seek(self, position: float) -> None:
        if self.closed:
            return
        target = max(self.start, position)
        if self.end is not None:
            target = min(target, max(self.start, self.end - self._frame_step))
        if self.ended:
            self.paused = False
        self._player.close_player()
        self._player = None
        self._events = SimpleQueue()
        self.ready = False
        self.ended = False
        self._pending = None
        self._last_frame = None
        self._size = (0, 0)
        self._refresh = True
        self.position = target
        self._open(target)

    def toggle_mute(self) -> None:
        if self.ready:
            self.muted = not self.muted
            self._player.set_mute(self.muted)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.ready = False
        if self._player is not None:
            self._player.close_player()
            self._player = None
        tools.set_log_callback(self._log_callback)
        tools.set_loglevel(self._loglevel)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--end", type=float)
    args = parser.parse_args()
    decoder = Decoder(args.path, args.start, args.end)
    commands: SimpleQueue[dict[str, Any]] = SimpleQueue()

    def receive() -> None:
        for line in sys.stdin:
            commands.put(json.loads(line))
        commands.put({"action": "close"})

    Thread(target=receive, daemon=True).start()
    columns, rows = 96, 27
    previous: dict[str, Any] = {}
    running = True
    try:
        while running:
            while True:
                try:
                    command = commands.get_nowait()
                except Empty:
                    break
                action = command["action"]
                if action == "close":
                    running = False
                    break
                if action == "resize":
                    columns, rows = command["columns"], command["rows"]
                elif action == "pause":
                    decoder.toggle_pause()
                elif action == "seek":
                    decoder.seek(command["position"])
                elif action == "mute":
                    decoder.toggle_mute()
            if not running:
                break
            decoder.resize(columns, rows)
            frame = decoder.poll()
            state = {
                name: getattr(decoder, name)
                for name in (
                    "position",
                    "end",
                    "paused",
                    "ended",
                    "muted",
                    "ready",
                    "error",
                )
            }
            if decoder.error:
                state["diagnostics"] = decoder.diagnostics
            if frame is not None or state != previous:
                previous = state.copy()
                if frame is not None:
                    state["size"] = frame.size
                    state["pixels"] = base64.b64encode(frame.tobytes()).decode("ascii")
                print(json.dumps(state), flush=True)
            time.sleep(1 / 60)
    finally:
        decoder.close()


if __name__ == "__main__":
    main()
