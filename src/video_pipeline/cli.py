import argparse
import subprocess
from pathlib import Path

from video_pipeline.inspection import VideoInfo, inspect_video


def print_video_info(info: VideoInfo) -> None:
    print(f"File: {info.path}")
    if info.duration_seconds is None:
        print("Duration: not reported")
    else:
        print(f"Duration: {info.duration_seconds:.3f} seconds")

    for stream in info.video_streams:
        print(f"\nVideo stream #{stream.index}")
        print(f"  Codec: {stream.codec or 'not reported'}")
        print(f"  Dimensions: {stream.width} x {stream.height}")
        if stream.average_frame_rate is None:
            print("  Average frame rate: not reported")
        else:
            print(f"  Average frame rate: {float(stream.average_frame_rate):.3f} fps")

    if not info.audio_streams:
        print("\nAudio: none")
    for stream in info.audio_streams:
        print(f"\nAudio stream #{stream.index}")
        print(f"  Codec: {stream.codec or 'not reported'}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="video-pipeline", description="Inspect a local video's properties."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Inspect a local video.")
    inspect.add_argument("path", type=Path, help="Path to the local video.")
    args = parser.parse_args()

    try:
        info = inspect_video(args.path)
    except FileNotFoundError:
        parser.exit(1, "error: ffprobe not found. Install FFmpeg and add it to PATH.\n")
    except subprocess.CalledProcessError as error:
        parser.exit(1, f"error: {error.stderr.strip()}\n")
    except ValueError as error:
        parser.exit(1, f"error: {error}\n")

    print_video_info(info)
