import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn
from uuid import UUID

from pydantic import ValidationError

from video_pipeline import term
from video_pipeline.term import dot, err, out

COMMANDS = (
    ("inspect", "video", "streams and length"),
    ("process", "video", "resize and thumbnail"),
    ("index", "video…", "make searchable"),
    ("search", "query", "find a moment"),
    ("evaluate", "json…", "measure search"),
    ("serve", "", "upload api"),
    ("worker", "", "job worker"),
    ("init-jobs", "", "job database"),
    ("ui", "", "interactive"),
)
METRICS = (
    ("video_at_1", "video@1"),
    ("overlap_at_1", "hit@1"),
    ("overlap_at_5", "hit@5"),
    ("iou_at_1", "iou@1"),
    ("iou_0_5_at_1", "iou≥.5"),
)


class Parser(argparse.ArgumentParser):
    def format_help(self) -> str:
        return "\n".join(
            root_help() if self.prog == "video-pipeline" else help_for(self)
        )

    def format_usage(self) -> str:
        return self.format_help()

    def error(self, message: str) -> NoReturn:
        hint = f"{self.prog} -h"
        name, _, detail = message.removeprefix("argument ").partition(": ")
        choice = re.fullmatch(r"invalid choice: '(.*)' \(choose from (.*)\)", detail)
        if message.startswith("the following arguments are required: "):
            message = "missing " + detail
        elif choice and name == "command":
            message = f"unknown command  {choice[1]}"
        elif choice:
            options = " · ".join(re.findall(r"'([^']*)'", choice[2]))
            message, hint = f"{name}  {options}", None
        elif message.startswith("unrecognized arguments: "):
            message = "unknown " + message.removeprefix("unrecognized arguments: ")
        elif message.startswith("argument "):
            value = re.fullmatch(r"invalid (?:int|float) value: '(.*)'", detail)
            detail = f"not a number  {value[1]}" if value else detail
            message = f"{name}  {detail}" if name.startswith("-") else detail
            hint = None
        fail(message, hint)


def options(parser: argparse.ArgumentParser) -> list[argparse.Action]:
    return [
        action
        for action in parser._actions
        if not isinstance(action, argparse._HelpAction | argparse._SubParsersAction)
    ]


def root_help() -> list[str]:
    name = max(len(command) for command, _, _ in COMMANDS) + 3
    hint = max(len(arguments) for _, arguments, _ in COMMANDS) + 3
    return [
        "",
        *(f"  {line}" for line in term.wordmark("video pipeline")),
        "",
        *(
            f"  {out.text(term.pad(command, name))}"
            f"{out.faint(term.pad(arguments, hint))}{out.muted(blurb)}"
            for command, arguments, blurb in COMMANDS
        ),
        "",
        f"  {out.faint('video-pipeline <command> -h')}",
        "",
    ]


def help_for(parser: argparse.ArgumentParser) -> list[str]:
    actions = options(parser)
    positionals = [action for action in actions if not action.option_strings]
    flags = [action for action in actions if action.option_strings]
    arguments = " ".join(str(action.metavar or action.dest) for action in positionals)
    size = max((len(action.option_strings[-1]) for action in flags), default=0) + 3
    lines = ["", f"  {out.strong(parser.prog)} {out.muted(arguments)}".rstrip()]
    if parser.description:
        lines.append(f"  {out.faint(parser.description)}")
    lines.append("")
    for action in flags:
        flag = term.pad(action.option_strings[-1], size)
        lines.append(f"  {out.text(flag)}{describe(action)}".rstrip())
    return [*lines, ""]


def describe(action: argparse.Action) -> str:
    if action.choices:
        return out.faint(" · ").join(
            out.text(str(choice)) if choice == action.default else out.faint(choice)
            for choice in action.choices
        )
    notes = []
    if action.default not in (None, False, [], argparse.SUPPRESS):
        notes.append(out.muted(str(action.default)))
    if action.help:
        notes.append(out.faint(action.help))
    if action.required:
        notes.append(out.muted("required"))
    return "  ".join(notes)


def fail(message: str, hint: str | None = None, code: int = 2) -> NoReturn:
    lines = message.strip().splitlines() or ["failed"]
    first = lines[0].rstrip(".")
    if first[:1].isupper() and first[1:2].islower():
        first = first[0].lower() + first[1:]
    term.emit("", err.error(first), stream=sys.stderr)
    for line in lines[1:]:
        term.emit(f"  {err.faint(line)}", stream=sys.stderr)
    if hint:
        term.emit(f"  {err.faint(hint)}", stream=sys.stderr)
    term.emit("", stream=sys.stderr)
    sys.exit(code)


def explain(error: Exception) -> str:
    if isinstance(error, subprocess.CalledProcessError):
        return str(error.stderr or error).strip()
    if isinstance(error, ValidationError):
        problem = error.errors()[0]
        detail = str(problem["msg"]).removeprefix("Value error, ")
        field = str(problem["loc"][0]) if problem["loc"] else "query"
        flag = "" if field == "query" else "--" + field.replace("_", "-")
        return f"{flag}  {detail.lower()}" if flag else detail
    if isinstance(error, OSError) and error.strerror:
        detail = error.strerror.lower()
        return f"{detail}  {shown(Path(error.filename))}" if error.filename else detail
    return str(error)


def video_file(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"no such file  {value}")
    return path


def positive_integer(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def shown(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def stores(data_dir: Path) -> tuple[Any, Any]:
    from video_pipeline.search import SearchStore
    from video_pipeline.storage import VideoStore

    videos = VideoStore(data_dir)
    videos.initialize()
    search = SearchStore(videos.database_path)
    search.initialize()
    return videos, search


def inspect(args: argparse.Namespace) -> None:
    from video_pipeline.inspection import inspect_video

    info = inspect_video(args.path)
    if args.json:
        print(json.dumps(asdict(info), default=str, indent=2))
        return

    rows = [
        (label, dot().join(map(out.text, parts)) if parts else out.faint("none"))
        for label, parts in term.facts(info)
    ]
    size = term.filesize(info.path.stat().st_size)
    term.emit(
        "",
        f"{out.strong(info.path.name)}  {out.faint(size)}",
        "",
        *term.columns(rows),
        "",
    )


def process(args: argparse.Namespace) -> None:
    from video_pipeline.inspection import inspect_video
    from video_pipeline.processing import process_video

    started = time.monotonic()
    source = inspect_video(args.path)
    with term.Activity("transcoding", source.duration_seconds) as activity:
        result = process_video(args.path, args.height, args.output_dir, activity.update)
    term.emit(
        "",
        f"{out.strong(source.path.name)} "
        f"{out.faint('→')} {out.text(f'{args.height}p')}  "
        f"{out.faint(term.elapsed(time.monotonic() - started))}",
        "",
        *term.columns(
            [
                ("video", out.text(shown(result.video_path))),
                ("thumbnail", out.text(shown(result.thumbnail_path))),
            ]
        ),
        "",
    )


def index(args: argparse.Namespace) -> None:
    from video_pipeline.embeddings import Embedder
    from video_pipeline.search import IndexSettings

    if not args.paths and not args.video_id:
        fail("nothing to index", "video-pipeline index <video>")
    settings = IndexSettings(
        window_seconds=args.window_seconds, stride_seconds=args.stride_seconds
    )
    videos, search = stores(args.data_dir)
    records = []
    for video_id in args.video_id:
        record = videos.get(video_id)
        if record is None:
            raise ValueError(f"no stored video {video_id}")
        records.append(record)
    for path in args.paths:
        with term.Activity(f"storing {path.name}"):
            with path.expanduser().open("rb") as source:
                records.append(videos.add(source, path.name))

    embedder = Embedder(args.device)
    names = [term.middle(video.filename or "untitled", 32) for video in records]
    size = max(term.cell_len(name) for name in names) + 3
    if not args.json:
        term.emit("")
    for video, name in zip(records, names, strict=True):
        started = datetime.now(UTC)
        with term.Activity(name, video.duration_seconds) as activity:
            result = search.index(video, videos, settings, embedder, activity.update)
        if args.json:
            print(result.model_dump_json(indent=2), flush=True)
            continue
        timing = (
            out.faint("unchanged")
            if result.indexed_at < started
            else out.faint(term.elapsed(result.elapsed_seconds))
        )
        term.emit(
            f"{out.text(term.pad(name, size))}"
            f"{out.muted(term.count(result.windows, 'window'))}{dot()}{timing}",
            out.faint(str(video.id)),
        )
    if not args.json:
        term.emit("")


def search(args: argparse.Namespace) -> None:
    from video_pipeline.embeddings import Embedder
    from video_pipeline.search import SearchRequest

    _, store = stores(args.data_dir)
    request = SearchRequest(
        query=args.query,
        mode=args.mode,
        limit=args.limit,
        video_ids=args.video_id,
        min_score=args.min_score,
    )
    with term.Activity("searching"):
        result = store.search(request, Embedder(args.device, text_only=True))
    if args.json:
        print(result.model_dump_json(indent=2))
        return

    query = term.clip(result.query, term.width() - 6)
    lines = ["", f"{out.faint('›')} {out.strong(query)}", ""]
    if not result.matches:
        if store.indexes():
            lines.append(f"  {out.muted('no matches')}")
        else:
            lines += [
                f"  {out.muted('nothing indexed yet')}",
                f"  {out.faint('video-pipeline index <video>')}",
            ]
        term.emit(*lines, "")
        return

    starts = [term.timecode(hit.start_seconds) for hit in result.matches]
    ends = [term.timecode(hit.end_seconds) for hit in result.matches]
    start_size = max(len(start) for start in starts)
    end_size = max(len(end) for end in ends) + 3
    spans = [
        f"{start:>{start_size}} – {term.pad(end, end_size)}"
        for start, end in zip(starts, ends, strict=True)
    ]
    names = [term.middle(hit.filename or "untitled", 28) for hit in result.matches]
    name_size = max(term.cell_len(name) for name in names) + 3
    for rank, (hit, span, name) in enumerate(
        zip(result.matches, spans, names, strict=True)
    ):
        paint = out.strong if rank == 0 else out.text
        lines.append(
            f"  {paint(span)}"
            f"{out.muted(term.pad(name, name_size))}"
            f"{out.faint(term.pad(hit.modality, 10))}"
            f"{out.faint(term.score(hit.score))}"
        )
    term.emit(*lines, "")


def evaluate(args: argparse.Namespace) -> None:
    from video_pipeline.embeddings import Embedder
    from video_pipeline.evaluation import evaluate_search

    _, store = stores(args.data_dir)
    with term.Activity("evaluating"):
        report = evaluate_search(
            args.references, store, Embedder(args.device, text_only=True)
        )
    if args.json:
        print(json.dumps(report, indent=2, allow_nan=False))
        return

    summary = {mode: groups["total"] for mode, groups in report["summary"].items()}
    queries = next(iter(summary.values()))["queries"]
    videos = len({result["expected_video_id"] for result in report["results"]})
    best = {key: max(total[key] for total in summary.values()) for key, _ in METRICS}
    label = max(len(mode) for mode in summary) + 4
    lines = [
        "",
        f"{out.muted(term.count(queries, 'query', 'queries'))}"
        f"{dot()}{out.muted(term.count(videos, 'video'))}",
        "",
        " " * label + "".join(out.faint(f"{header:>9}") for _, header in METRICS),
    ]
    for mode, total in summary.items():
        cells = []
        for key, _ in METRICS:
            text = f"{term.score(total[key]):>9}"
            cells.append(
                out.strong(text) if total[key] == best[key] else out.muted(text)
            )
        lines.append(out.text(term.pad(mode, label)) + "".join(cells))
    term.emit(*lines, "")


def serve(args: argparse.Namespace) -> None:
    import uvicorn

    from video_pipeline.api import create_app

    app = create_app(args.data_dir, args.device)
    term.emit(
        "",
        f"{out.text('serving')}  {out.muted(f'http://127.0.0.1:{args.port}')}",
        "",
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


def worker(args: argparse.Namespace) -> None:
    from video_pipeline.worker import run_worker

    run_worker(args.data_dir, args.concurrency, args.queue, args.device)


def ui(args: argparse.Namespace) -> None:
    from video_pipeline.tui import run

    run(args.data_dir, args.device)


def init_jobs(args: argparse.Namespace) -> None:
    from video_pipeline.jobs import JobStore, database_url
    from video_pipeline.storage import VideoStore

    videos = VideoStore(args.data_dir)
    videos.initialize()
    jobs = JobStore(videos.database_path, database_url())
    jobs.open()
    try:
        count = jobs.initialize()
    finally:
        jobs.close()
    term.emit(
        "",
        f"{out.text('jobs ready')}{dot()}{out.muted(f'{count} imported')}",
        "",
    )


def build_parser() -> Parser:
    parser = Parser(prog="video-pipeline")
    commands = parser.add_subparsers(dest="command", parser_class=Parser)

    def command(name: str, handler: Any, *aliases: str) -> Parser:
        blurb = next(blurb for key, _, blurb in COMMANDS if key == name)
        sub = commands.add_parser(name, aliases=list(aliases), description=blurb)
        sub.set_defaults(handler=handler)
        return sub

    def data_dir(sub: Parser) -> None:
        sub.add_argument("--data-dir", type=Path, default=Path("data"))

    def device(sub: Parser) -> None:
        sub.add_argument("--device", choices=["cpu", "mps", "cuda"])

    def as_json(sub: Parser) -> None:
        sub.add_argument("--json", action="store_true", help="machine output")

    sub = command("inspect", inspect)
    sub.add_argument("path", type=video_file, metavar="video")
    as_json(sub)

    sub = command("process", process)
    sub.add_argument("path", type=video_file, metavar="video")
    sub.add_argument("--height", type=int, required=True, help="even pixels")
    sub.add_argument("--output-dir", type=Path, required=True, help="new directory")

    sub = command("index", index)
    sub.add_argument("paths", nargs="*", type=video_file, metavar="video…")
    sub.add_argument(
        "--video-id", type=UUID, action="append", default=[], help="repeatable"
    )
    sub.add_argument("--window-seconds", type=float, default=8)
    sub.add_argument("--stride-seconds", type=float, default=4)
    data_dir(sub)
    device(sub)
    as_json(sub)

    sub = command("search", search)
    sub.add_argument("query")
    sub.add_argument(
        "--mode", choices=["all", "visual", "audio", "combined"], default="all"
    )
    sub.add_argument("--limit", type=int, default=5)
    sub.add_argument("--min-score", type=float, help="-1 to 1")
    sub.add_argument("--video-id", type=UUID, action="append", help="repeatable")
    data_dir(sub)
    device(sub)
    as_json(sub)

    sub = command("evaluate", evaluate, "evaluate-search")
    sub.add_argument("references", nargs="+", type=Path, metavar="json…")
    data_dir(sub)
    device(sub)
    as_json(sub)

    sub = command("serve", serve)
    sub.add_argument("--port", type=int, default=8000)
    data_dir(sub)
    device(sub)

    sub = command("worker", worker)
    sub.add_argument("--queue", choices=["video", "search"], default="video")
    sub.add_argument("--concurrency", type=positive_integer, default=1)
    data_dir(sub)
    device(sub)

    sub = command("init-jobs", init_jobs)
    data_dir(sub)

    sub = command("ui", ui)
    data_dir(sub)
    device(sub)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command is None:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            parser.print_help()
            return
        args = parser.parse_args(["ui"])
    try:
        args.handler(args)
    except KeyboardInterrupt:
        term.emit("", stream=sys.stderr)
        sys.exit(130)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        fail(explain(error), code=1)
