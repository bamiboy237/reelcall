import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryFile
from typing import TextIO, cast

from video_pipeline.inspection import VideoInfo, inspect_video

MP4_ENCODING_OPTIONS = (
    "-c:v",
    "libx264",
    "-preset",
    "fast",
    "-crf",
    "23",
    "-pix_fmt",
    "yuv420p",
    "-c:a",
    "aac",
    "-b:a",
    "128k",
    "-movflags",
    "+faststart",
)


@dataclass
class ProcessingResult:
    video_path: Path
    thumbnail_path: Path


def _run_ffmpeg(
    arguments: list[str], on_progress: Callable[[float], None] | None = None
) -> None:
    command = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-n",
        "-nostats",
        "-progress",
        "pipe:1",
        *arguments,
    ]
    with TemporaryFile(mode="w+t") as errors:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=errors, text=True
        )
        try:
            with cast(TextIO, process.stdout) as progress:
                for line in progress:
                    key, _, value = line.strip().partition("=")
                    if (
                        key == "out_time_us"
                        and value != "N/A"
                        and on_progress is not None
                    ):
                        on_progress(max(0, int(value) / 1_000_000))
            returncode = process.wait()
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait()
        if returncode:
            errors.seek(0)
            raise subprocess.CalledProcessError(
                returncode, command, stderr=errors.read()
            )


def transcode_video(
    source: VideoInfo,
    height: int,
    video_path: Path,
    on_progress: Callable[[float], None] | None = None,
) -> None:
    _run_ffmpeg(
        [
            "-i",
            str(source.path),
            "-map",
            f"0:{source.video_streams[0].index}",
            "-map",
            "0:a?",
            "-vf",
            f"scale=-2:{height}",
            *MP4_ENCODING_OPTIONS,
            str(video_path),
        ],
        on_progress,
    )


def clip_video(
    source: VideoInfo,
    start_seconds: float,
    end_seconds: float,
    video_path: Path,
    on_progress: Callable[[float], None] | None = None,
) -> None:
    duration = f"{end_seconds - start_seconds:.6f}"
    _run_ffmpeg(
        [
            "-ss",
            f"{start_seconds:.6f}",
            "-i",
            str(source.path),
            "-t",
            duration,
            "-map",
            f"0:{source.video_streams[0].index}",
            "-map",
            "0:a?",
            "-vf",
            f"trim=end={duration},setpts=PTS-STARTPTS,pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-fps_mode",
            "passthrough",
            *MP4_ENCODING_OPTIONS,
            str(video_path),
        ],
        on_progress,
    )
    inspect_video(video_path)


def extract_thumbnail(path: Path, thumbnail_path: Path) -> None:
    _run_ffmpeg(
        [
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-update",
            "1",
            str(thumbnail_path),
        ],
    )


def process_video(
    path: Path,
    height: int,
    output_dir: Path,
    on_progress: Callable[[float], None] | None = None,
) -> ProcessingResult:
    if height <= 0 or height % 2:
        raise ValueError("Height must be a positive even number.")

    source = inspect_video(path)
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True)
    video_path = output_dir / "video.mp4"
    thumbnail_path = output_dir / "thumbnail.jpg"
    transcode_video(source, height, video_path, on_progress)
    extract_thumbnail(video_path, thumbnail_path)
    return ProcessingResult(video_path=video_path, thumbnail_path=thumbnail_path)
