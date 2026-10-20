import argparse
import subprocess
from pathlib import Path

import uvicorn

from video_pipeline.api import create_app
from video_pipeline.inspection import VideoInfo, inspect_video
from video_pipeline.jobs import JobStore, database_url
from video_pipeline.processing import process_video
from video_pipeline.storage import VideoStore
from video_pipeline.worker import run_worker


def positive_integer(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


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
        prog="video-pipeline", description="Inspect, process, and store local videos."
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
    serve = commands.add_parser("serve", help="Run the local upload API.")
    serve.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Directory for stored videos.",
    )
    serve.add_argument("--port", type=int, default=8000, help="Local HTTP port.")
    worker = commands.add_parser("worker", help="Process queued video jobs.")
    worker.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Directory shared with the API.",
    )
    worker.add_argument(
        "--concurrency", type=positive_integer, default=1, help="Concurrent edits."
    )
    initialize = commands.add_parser(
        "init-jobs", help="Initialize PostgreSQL jobs and import existing job history."
    )
    initialize.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()

    try:
        if args.command == "inspect":
            print_video_info(inspect_video(args.path))
        elif args.command == "process":
            print(f"Processing: {args.path}", flush=True)
            result = process_video(args.path, args.height, args.output_dir)
            print(f"Video: {result.video_path}")
            print(f"Thumbnail: {result.thumbnail_path}")
        elif args.command == "serve":
            uvicorn.run(create_app(args.data_dir), host="127.0.0.1", port=args.port)
        elif args.command == "worker":
            run_worker(args.data_dir, args.concurrency)
        elif args.command == "init-jobs":
            videos = VideoStore(args.data_dir)
            videos.initialize()
            jobs = JobStore(videos.database_path, database_url())
            jobs.open()
            try:
                count = jobs.initialize()
                print(f"Job database initialized. Imported {count} existing jobs.")
            finally:
                jobs.close()
    except subprocess.CalledProcessError as error:
        parser.exit(1, f"error: {error.stderr.strip()}\n")
    except (OSError, ValueError) as error:
        parser.exit(1, f"error: {error}\n")
