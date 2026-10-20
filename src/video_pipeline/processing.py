import subprocess
from dataclasses import dataclass
from pathlib import Path

from video_pipeline.inspection import inspect_video


@dataclass
class ProcessingResult:
    video_path: Path
    thumbnail_path: Path


def process_video(path: Path, height: int, output_dir: Path) -> ProcessingResult:
    if height <= 0 or height % 2:
        raise ValueError("Height must be a positive even number.")

    source = inspect_video(path)
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True)
    video_path = output_dir / "video.mp4"
    thumbnail_path = output_dir / "thumbnail.jpg"

    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-n",
            "-i",
            str(source.path),
            "-map",
            f"0:{source.video_streams[0].index}",
            "-map",
            "0:a?",
            "-vf",
            f"scale=-2:{height}",
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
            str(video_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-n",
            "-i",
            str(video_path),
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
        capture_output=True,
        text=True,
        check=True,
    )
    return ProcessingResult(video_path=video_path, thumbnail_path=thumbnail_path)
