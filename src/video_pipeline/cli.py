import argparse
import subprocess
from pathlib import Path

from video_pipeline.inspection import VideoInfo, inspect_video
from video_pipeline.processing import process_video


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
        prog="video-pipeline", description="Inspect and process local videos."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Inspect a local video.")
    inspect.add_argument("path", type=Path, help="Path to the local video.")
    process = commands.add_parser(
        "process", help="Resize a video and save a thumbnail."
    )
    process.add_argument("path", type=Path, help="Path to the local video.")
    process.add_argument(
        "--height",
        type=int,
        required=True,
        help="Output height in pixels (positive even).",
    )
    process.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="New directory for the output files.",
    )
    args = parser.parse_args()

    try:
        if args.command == "inspect":
            print_video_info(inspect_video(args.path))
        else:
            print(f"Processing: {args.path}", flush=True)
            result = process_video(args.path, args.height, args.output_dir)
            print(f"Video: {result.video_path}")
            print(f"Thumbnail: {result.thumbnail_path}")
    except subprocess.CalledProcessError as error:
        parser.exit(1, f"error: {error.stderr.strip()}\n")
    except (OSError, ValueError) as error:
        parser.exit(1, f"error: {error}\n")
