import hashlib
import subprocess
from collections import OrderedDict
from dataclasses import dataclass
from importlib.metadata import version
from itertools import accumulate, pairwise
from math import floor
from pathlib import Path
from threading import Lock
from typing import Any, cast

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
# Holds more than one maximum-length window of frames, so overlapping windows
# can reuse the previous window's frames. Each entry is about 280 KiB.
FRAME_CACHE_SIZE = 64


@dataclass
class MediaWindow:
    frames: list[Image.Image]
    frame_seconds: list[float]
    audio: np.ndarray | None
    video_ended: bool = False


def read_audio(path: Path) -> np.ndarray:
    """Decode the first audio stream once, as mono float32 on the file clock."""
    result = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-af",
            "aresample=async=1:first_pts=0",
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
    return np.frombuffer(result.stdout, dtype="<f4")


def read_window(
    path: Path, start: float, end: float, audio: np.ndarray | None
) -> MediaWindow:
    frames = []
    timestamps = []
    video_ended = False
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
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

    if not frames or audio is None or not audio.size:
        return MediaWindow(frames, timestamps, None, video_ended)

    # Pad with silence after a short audio track, as the window has video.
    first = round(start * SAMPLE_RATE)
    samples = np.zeros(round((end - start) * SAMPLE_RATE), dtype="<f4")
    chunk = audio[first : first + samples.size]
    samples[: chunk.size] = chunk
    return MediaWindow(frames, timestamps, samples, video_ended)


def _tensor_key(*tensors: Any) -> bytes:
    digest = hashlib.blake2b(digest_size=16)
    for tensor in tensors:
        digest.update(tensor.cpu().numpy().tobytes())
    return digest.digest()


def _reuse_media_features(model: Any) -> None:
    """Run Gemma's media encoders once per distinct input.

    The vision tower encodes each video frame independently and takes most of
    the indexing time. The visual and combined inputs share their frames, and
    overlapping windows share frames with the previous window. The audio and
    combined inputs share one waveform. Model forward passes look these
    feature methods up on the instance, so replacing them reuses earlier
    encoder output without changing the language model's inputs.
    """
    import torch
    from transformers.modeling_outputs import BaseModelOutputWithPooling

    encode_video = model.get_video_features
    encode_audio = model.get_audio_features
    frames: OrderedDict[bytes, torch.Tensor] = OrderedDict()
    audio: dict[bytes, Any] = {}

    def get_video_features(
        pixel_values_videos: torch.Tensor,
        video_position_ids: torch.Tensor,
        num_frames_per_video: torch.Tensor,
        **kwargs: Any,
    ) -> BaseModelOutputWithPooling:
        keys = [
            _tensor_key(pixels, positions)
            for pixels, positions in zip(
                pixel_values_videos, video_position_ids, strict=True
            )
        ]
        missing = {key: index for index, key in enumerate(keys) if key not in frames}
        if missing:
            indexes = list(missing.values())
            encoded = encode_video(
                pixel_values_videos[indexes],
                video_position_ids[indexes],
                torch.ones(len(indexes), dtype=torch.long),
                **kwargs,
            ).pooler_output
            frames.update(zip(missing, encoded, strict=True))
        features = [frames[key] for key in keys]
        for key in keys:
            frames.move_to_end(key)
        while len(frames) > FRAME_CACHE_SIZE:
            frames.popitem(last=False)
        bounds = pairwise(accumulate(num_frames_per_video.tolist(), initial=0))
        # Like the original method, return one feature tensor per video.
        pooled = tuple(torch.cat(features[a:b]) for a, b in bounds)
        return BaseModelOutputWithPooling(pooler_output=cast(Any, pooled))

    def get_audio_features(
        input_features: torch.Tensor, input_features_mask: torch.Tensor, **kwargs: Any
    ) -> Any:
        key = _tensor_key(input_features, input_features_mask)
        if key not in audio:
            audio.clear()
            audio[key] = encode_audio(input_features, input_features_mask, **kwargs)
        return audio[key]

    model.get_video_features = get_video_features
    model.get_audio_features = get_audio_features


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
            if not self.text_only:
                _reuse_media_features(self._model[0].model)
        return self._model

    def load(self) -> None:
        with self._lock:
            self._load()

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
