import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


@dataclass
class VideoStream:
    index: int
    codec: str | None
    width: int
    height: int
    average_frame_rate: Fraction | None


@dataclass
class AudioStream:
    index: int
    codec: str | None


@dataclass
class VideoInfo:
    path: Path
    duration_seconds: float | None
    video_streams: list[VideoStream]
    audio_streams: list[AudioStream]


def inspect_video(path: Path) -> VideoInfo:
    path = path.expanduser().resolve()
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:"
            "stream=index,codec_type,codec_name,width,height,avg_frame_rate",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    metadata = json.loads(result.stdout)
    video_streams = []
    audio_streams = []

    for stream in metadata["streams"]:
        if stream["codec_type"] == "video":
            rate = stream.get("avg_frame_rate")
            video_streams.append(
                VideoStream(
                    index=stream["index"],
                    codec=stream.get("codec_name"),
                    width=stream["width"],
                    height=stream["height"],
                    average_frame_rate=(
                        Fraction(rate) if rate not in (None, "0/0") else None
                    ),
                )
            )
        elif stream["codec_type"] == "audio":
            audio_streams.append(
                AudioStream(index=stream["index"], codec=stream.get("codec_name"))
            )

    if not video_streams:
        raise ValueError(f"{path}: no video stream found")

    duration = metadata["format"].get("duration")
    return VideoInfo(
        path=path,
        duration_seconds=float(duration) if duration is not None else None,
        video_streams=video_streams,
        audio_streams=audio_streams,
    )
