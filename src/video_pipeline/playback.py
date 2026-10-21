"""Playback client. Native FFmpeg and SDL run in a separate process."""

import base64
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryFile
from threading import Lock, Thread
from typing import Any

from PIL import Image


class Playback:
    def __init__(self, path: Path, start: float = 0, end: float | None = None) -> None:
        self.start, self.end = start, end
        self.position = start
        self.paused = False
        self.ended = False
        self.muted = False
        self.ready = False
        self.closed = False
        self.error: str | None = None
        self.diagnostics = ""
        self._lock = Lock()
        self._state: dict[str, Any] = {}
        self._frame: Image.Image | None = None
        self._size = (0, 0)
        self._process: subprocess.Popen[str] | None = None
        self._reader: Thread | None = None
        self._stderr = TemporaryFile(mode="w+t", encoding="utf-8")
        command = [
            sys.executable,
            "-m",
            "video_pipeline.playback_worker",
            str(path),
            "--start",
            str(start),
        ]
        if end is not None:
            command += ["--end", str(end)]
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._stderr,
                text=True,
                encoding="utf-8",
                bufsize=1,
                start_new_session=True,
            )
        except OSError as error:
            self.error = self.diagnostics = str(error)
            self.closed = True
            self._stderr.close()
            return
        self._reader = Thread(target=self._receive, daemon=True)
        self._reader.start()

    def _receive(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        failure = ""
        for line in process.stdout:
            try:
                state = json.loads(line)
                frame = (
                    Image.frombytes(
                        "RGB", tuple(state["size"]), base64.b64decode(state["pixels"])
                    )
                    if "pixels" in state
                    else None
                )
            except (ValueError, KeyError) as error:
                failure = str(error)
                process.terminate()
                break
            with self._lock:
                self._state = state
                if frame is not None:
                    self._frame = frame
        process.wait()
        if not self.closed:
            self._stderr.seek(0)
            with self._lock:
                self._state = {
                    "error": "Playback failed.",
                    "ready": False,
                    "diagnostics": self._stderr.read().strip()
                    or failure
                    or f"Player exited with status {process.returncode}.",
                }

    def _send(self, action: str, **values: Any) -> None:
        if self.closed:
            return
        process = self._process
        assert process is not None and process.stdin is not None
        try:
            process.stdin.write(json.dumps({"action": action, **values}) + "\n")
            process.stdin.flush()
        except BrokenPipeError:
            pass  # The reader reports the process exit and its diagnostics.

    def resize(self, columns: int, rows: int) -> None:
        if (columns, rows) != self._size:
            self._size = columns, rows
            self._send("resize", columns=columns, rows=rows)

    def poll(self) -> Image.Image | None:
        if self.closed:
            return None
        with self._lock:
            state = self._state
            frame, self._frame = self._frame, None
        for name in (
            "position",
            "end",
            "paused",
            "ended",
            "muted",
            "ready",
            "error",
        ):
            if name in state:
                setattr(self, name, state[name])
        if self.error:
            self.diagnostics = state.get("diagnostics", self.error)
            self.close()
        return frame

    def toggle_pause(self) -> None:
        if self.ready:
            self._send("pause")

    def seek(self, position: float) -> None:
        if self.ready:
            self._send("seek", position=position)

    def toggle_mute(self) -> None:
        if self.ready:
            self._send("mute")

    def close(self) -> None:
        if self.closed:
            return
        self._send("close")
        self.closed = True
        self.ready = False
        process = self._process
        assert process is not None
        if process.stdin is not None:
            try:
                process.stdin.close()
            except BrokenPipeError:
                pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        if self._reader is not None:
            self._reader.join()
        if process.stdout is not None:
            process.stdout.close()
        self._stderr.close()
