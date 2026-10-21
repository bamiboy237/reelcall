import argparse
import json
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import uvicorn

from video_pipeline.api import create_app
from video_pipeline.embeddings import Embedder
from video_pipeline.evaluation import evaluate_search
from video_pipeline.inspection import VideoInfo, inspect_video
from video_pipeline.jobs import JobStore, database_url
from video_pipeline.processing import process_video
from video_pipeline.search import IndexSettings, SearchRequest, SearchStore
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
        prog="video-pipeline", description="Inspect, process, and search local videos."
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
    index = commands.add_parser(
        "index", help="Index local or uploaded videos for search."
    )
    index.add_argument(
        "paths", nargs="*", type=Path, help="Videos to copy into storage and index."
    )
    index.add_argument(
        "--video-id",
        type=UUID,
        action="append",
        default=[],
        help="Index an existing stored video. Can be repeated.",
    )
    index.add_argument("--data-dir", type=Path, default=Path("data"))
    index.add_argument("--window-seconds", type=float, default=8)
    index.add_argument("--stride-seconds", type=float, default=4)
    search = commands.add_parser("search", help="Find moments by description.")
    search.add_argument("query")
    search.add_argument("--data-dir", type=Path, default=Path("data"))
    search.add_argument(
        "--mode", choices=["all", "visual", "audio", "combined"], default="all"
    )
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--min-score", type=float)
    search.add_argument("--video-id", type=UUID, action="append")
    evaluate = commands.add_parser(
        "evaluate-search", help="Compare search results with reference moments."
    )
    evaluate.add_argument("references", nargs="+", type=Path)
    evaluate.add_argument("--data-dir", type=Path, default=Path("data"))
    worker.add_argument("--queue", choices=["video", "search"], default="video")
    for command in (serve, worker, index, search, evaluate):
        command.add_argument(
            "--device",
            choices=["cpu", "mps", "cuda"],
            help="Model device. Default: let PyTorch select it.",
        )
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
            uvicorn.run(
                create_app(args.data_dir, args.device), host="127.0.0.1", port=args.port
            )
        elif args.command == "worker":
            run_worker(args.data_dir, args.concurrency, args.queue, args.device)
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
        elif args.command == "index":
            if not args.paths and not args.video_id:
                parser.error("index requires a path or --video-id")
            settings = IndexSettings(
                window_seconds=args.window_seconds,
                stride_seconds=args.stride_seconds,
            )
            videos = VideoStore(args.data_dir)
            videos.initialize()
            search_store = SearchStore(videos.database_path)
            search_store.initialize()
            embedder = Embedder(args.device)
            records = []
            for video_id in args.video_id:
                record = videos.get(video_id)
                if record is None:
                    raise ValueError(f"Video not found: {video_id}")
                records.append(record)
            for path in args.paths:
                with path.expanduser().open("rb") as source:
                    records.append(videos.add(source, path.name))
            for video in records:

                def progress(seconds: float) -> None:
                    print(
                        f"{video.id}: indexed through {seconds:.3f}s",
                        file=sys.stderr,
                        flush=True,
                    )

                record = search_store.index(video, videos, settings, embedder, progress)
                print(record.model_dump_json(indent=2), flush=True)
        elif args.command == "search":
            videos = VideoStore(args.data_dir)
            videos.initialize()
            search_store = SearchStore(videos.database_path)
            search_store.initialize()
            result = search_store.search(
                SearchRequest(
                    query=args.query,
                    mode=args.mode,
                    limit=args.limit,
                    video_ids=args.video_id,
                    min_score=args.min_score,
                ),
                Embedder(args.device, text_only=True),
            )
            print(result.model_dump_json(indent=2))
        elif args.command == "evaluate-search":
            videos = VideoStore(args.data_dir)
            videos.initialize()
            search_store = SearchStore(videos.database_path)
            search_store.initialize()
            report = evaluate_search(
                args.references, search_store, Embedder(args.device, text_only=True)
            )
            print(json.dumps(report, indent=2, allow_nan=False))
    except subprocess.CalledProcessError as error:
        parser.exit(1, f"error: {error.stderr.strip()}\n")
    except (OSError, ValueError) as error:
        parser.exit(1, f"error: {error}\n")
