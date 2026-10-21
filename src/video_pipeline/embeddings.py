import subprocess
from dataclasses import dataclass
from importlib.metadata import version
from math import floor
from pathlib import Path
from threading import Lock
from typing import Any

import av
import numpy as np
from PIL import Image

MODEL_ID = "google/embeddinggemma-2"
MODEL_REVISION = "914f7f89142e33e77833254d9c9b90c3cef7303b"
DIMENSIONS = 768
SAMPLE_RATE = 16_000
FRAME_RATE = 1
FRAME_SIZE = 448
VISION_TOKENS = 140
QUERY_PROMPT = "SearchQuery"


@dataclass
class MediaWindow:
    frames: list[Image.Image]
    frame_seconds: list[float]
    audio: np.ndarray | None
    video_ended: bool = False


def read_window(path: Path, start: float, end: float, has_audio: bool) -> MediaWindow:
    frames = []
    timestamps = []
    video_ended = False
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        # Match FFmpeg's file-relative seek clock, including stream offsets.
        origin = float(container.start_time or 0) / av.time_base
        container.seek(int((origin + start) / stream.time_base), stream=stream)
        next_sample = start
        for frame in container.decode(stream):
            seconds = float(frame.time) - origin
            if seconds >= end:
                break
            if seconds < next_sample:
                continue
            image = frame.to_image()
            if frame.rotation:
                image = image.rotate(frame.rotation, expand=True)
            image.thumbnail((FRAME_SIZE, FRAME_SIZE))
            frames.append(image)
            timestamps.append(seconds)
            next_sample = (
                start + (floor((seconds - start) * FRAME_RATE) + 1) / FRAME_RATE
            )
        else:
            video_ended = True

    if not frames:
        return MediaWindow(frames, timestamps, None, video_ended)

    audio = None
    if has_audio:
        result = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-ss",
                f"{start:.6f}",
                "-i",
                str(path),
                "-t",
                f"{end - start:.6f}",
                "-map",
                "0:a:0",
                "-ac",
                "1",
                "-ar",
                str(SAMPLE_RATE),
                "-af",
                "aresample=async=1:first_pts=0,apad",
                "-f",
                "f32le",
                "pipe:1",
            ],
            capture_output=True,
        )
        if result.returncode:
            raise subprocess.CalledProcessError(
                result.returncode,
                result.args,
                stderr=result.stderr.decode("utf-8", errors="replace"),
            )
        if result.stdout:
            audio = np.frombuffer(result.stdout, dtype="<f4").copy()
    return MediaWindow(frames, timestamps, audio, video_ended)


class Embedder:
    def __init__(self, device: str | None = None, *, text_only: bool = False) -> None:
        self.device = device
        self.text_only = text_only
        self._model: Any = None
        self._lock = Lock()

    def _load(self) -> Any:
        if self._model is None:
            import torch
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(
                MODEL_ID,
                revision=MODEL_REVISION,
                device=self.device,
                config_kwargs=(
                    {"vision_config": None, "audio_config": None}
                    if self.text_only
                    else {}
                ),
                model_kwargs={"dtype": torch.float32},
            )
        return self._model

    def _encode(self, inputs: Any, **kwargs: Any) -> np.ndarray:
        with self._lock:
            vectors = self._load().encode(
                inputs,
                batch_size=1,
                normalize_embeddings=True,
                show_progress_bar=False,
                **kwargs,
            )
        vectors = np.asarray(vectors, dtype="<f4")
        if vectors.shape[-1] != DIMENSIONS or not np.isfinite(vectors).all():
            raise ValueError("Gemma returned an invalid embedding.")
        return vectors

    def queries(self, queries: list[str]) -> np.ndarray:
        return self._encode(queries, prompt_name=QUERY_PROMPT)

    def runtime(self) -> dict[str, str]:
        return {
            "device": str(self._model.device),
            "torch": version("torch"),
            "sentence_transformers": version("sentence-transformers"),
            "transformers": version("transformers"),
        }

    def window(self, media: MediaWindow) -> dict[str, np.ndarray]:
        inputs: list[dict[str, Any]] = [{"video": media.frames}]
        modalities = ["visual"]
        if media.audio is not None:
            inputs.extend(
                [
                    {"audio": media.audio},
                    {"video": media.frames, "audio": media.audio},
                ]
            )
            modalities.extend(["audio", "combined"])
        vectors = self._encode(
            inputs,
            processing_kwargs={
                "video": {
                    "do_sample_frames": False,
                    "add_timestamps": False,
                    "max_soft_tokens": VISION_TOKENS,
                },
                "audio": {"sampling_rate": SAMPLE_RATE},
            },
        )
        if self._model.device.type == "mps":
            import torch

            torch.mps.empty_cache()
        return dict(zip(modalities, vectors, strict=True))
