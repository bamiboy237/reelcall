"""Let agents search a local Reelcall library over the Model Context Protocol."""

import io
import os
import subprocess
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from threading import Lock, Thread
from typing import Annotated, Any
from uuid import UUID

import psycopg
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from video_pipeline import term
from video_pipeline.embeddings import Embedder, read_frames
from video_pipeline.jobs import (
    ClipOperation,
    IndexOperation,
    JobRecord,
    JobStore,
    database_url,
)
from video_pipeline.search import SearchRequest, SearchStore
from video_pipeline.storage import VideoRecord, VideoStore

VIEW_SIZE = 768
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
INSTRUCTIONS = """\
Reelcall searches videos stored in the user's local library. Nothing leaves \
the user's machine.

Search returns candidate moments ranked by similarity. Each candidate is a \
window of about eight seconds. Scores are not confidence values, and the top \
candidate is often near the moment rather than on it. Before you state where \
something happens, call view_moment on the best candidates and look at the \
frames. Cite the video filename and the timestamps of the frames that show it.

Indexing and clips run as background jobs. Follow them with job_status."""


class LibraryVideo(BaseModel):
    id: UUID
    filename: str | None
    duration_seconds: float | None
    has_audio: bool
    indexed: bool


class JobView(BaseModel):
    job: JobRecord
    output_path: str | None = None


@contextmanager
def reported() -> Generator[None, None, None]:
    """Show the agent expected failures, which it can often correct."""
    try:
        yield
    except subprocess.CalledProcessError as error:
        raise ToolError(str(error.stderr or error).strip()) from error
    except (OSError, ValueError) as error:
        raise ToolError(str(error)) from error
    except psycopg.Error as error:
        raise ToolError(f"The job service is unavailable: {error}") from error


def span(start: float, end: float) -> str:
    return f"{term.timecode(start)}–{term.timecode(end)} ({start:.3f}–{end:.3f} s)"


def jpeg(image: Any) -> Image:
    data = io.BytesIO()
    image.save(data, format="JPEG", quality=85)
    return Image(data=data.getvalue(), format="jpeg")


def create_server(data_dir: Path, device: str | None = None) -> MCPServer:
    videos = VideoStore(data_dir)
    videos.initialize()
    search = SearchStore(videos.database_path)
    search.initialize()
    embedder = Embedder(device, text_only=True)
    jobs: JobStore | None = None
    jobs_lock = Lock()

    def job_store() -> JobStore:
        """Connect to the job service on first use, starting it when needed."""
        nonlocal jobs
        with jobs_lock:
            if jobs is None:
                if not os.environ.get("VIDEO_PIPELINE_DATABASE_URL"):
                    from video_pipeline.services import LocalService

                    os.environ["VIDEO_PIPELINE_DATABASE_URL"] = LocalService(
                        data_dir
                    ).start()
                store = JobStore(videos.database_path, database_url())
                store.open()
                try:
                    store.require_schema()
                except BaseException:
                    store.close()
                    raise
                jobs = store
            return jobs

    def stored_video(video_id: UUID) -> VideoRecord:
        video = videos.get(video_id)
        if video is None:
            raise ToolError(f"No video {video_id} in this library. Call list_videos.")
        return video

    def job_view(job: JobRecord) -> JobView:
        output = None
        if job.output is not None:
            output = str(job_store().output_path(job.id, job.output.filename))
        return JobView(job=job, output_path=output)

    @asynccontextmanager
    async def lifespan(server: MCPServer) -> AsyncGenerator[None, None]:
        # Load the query model while the agent starts up, so the first search
        # does not pay for it. Early searches wait for this load.
        Thread(target=embedder.load, daemon=True).start()
        try:
            yield
        finally:
            if jobs is not None:
                jobs.close()

    server = MCPServer(
        "reelcall", instructions=INSTRUCTIONS, lifespan=lifespan, log_level="WARNING"
    )

    @server.tool(annotations=READ_ONLY)
    def list_videos() -> list[LibraryVideo]:
        """List the videos in the library, with their IDs and indexing state."""
        with reported():
            indexed = {record.video_id for record in search.indexes()}
            return [
                LibraryVideo(
                    id=video.id,
                    filename=video.filename,
                    duration_seconds=video.duration_seconds,
                    has_audio=bool(video.audio_streams),
                    indexed=video.id in indexed,
                )
                for video in videos.list()
            ]

    @server.tool(annotations=READ_ONLY, structured_output=False)
    def search_videos(
        query: Annotated[str, Field(description="An everyday description.")],
        video_ids: Annotated[
            list[UUID] | None, Field(description="Search only these videos.")
        ] = None,
        limit: Annotated[int, Field(ge=1, le=10)] = 5,
    ) -> list[str | Image]:
        """Find moments that match a description, with a thumbnail for each.

        Describe what is seen or heard, for example "someone drops a red cup".
        Check the best candidates with view_moment before you answer.
        """
        with reported():
            for video_id in video_ids or []:
                stored_video(video_id)
            request = SearchRequest(query=query, limit=limit, video_ids=video_ids)
            result = search.search(request, embedder)
            if not result.matches:
                if not search.indexes():
                    return ["Nothing is indexed yet. Call add_video first."]
                return ["No indexed moments matched."]
            content: list[str | Image] = []
            for rank, hit in enumerate(result.matches, start=1):
                content.append(
                    f"{rank}. {hit.filename or 'untitled'}  "
                    f"{span(hit.start_seconds, hit.end_seconds)}  "
                    f"score {hit.score:.3f} by {hit.modality}  "
                    f"video_id {hit.video_id}"
                )
                thumbnail = search.thumbnail(hit.video_id, hit.window)
                if thumbnail is not None:
                    content.append(Image(data=thumbnail, format="jpeg"))
            return content

    @server.tool(annotations=READ_ONLY, structured_output=False)
    def view_moment(
        video_id: UUID,
        start_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)],
        end_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)],
        frames: Annotated[int, Field(ge=1, le=12)] = 6,
    ) -> list[str | Image]:
        """Look at frames spread evenly across part of a video.

        Each frame is labeled with its exact timestamp. Use a short span to
        find the precise second something happens.
        """
        with reported():
            video = stored_video(video_id)
            if end_seconds <= start_seconds:
                raise ToolError("end_seconds must be after start_seconds.")
            duration = video.duration_seconds
            if duration is not None and start_seconds >= duration:
                raise ToolError(f"The video ends at {duration:.3f} s.")
            if duration is not None:
                end_seconds = min(end_seconds, duration)
            step = (end_seconds - start_seconds) / frames
            targets = [start_seconds + (index + 0.5) * step for index in range(frames)]
            decoded = read_frames(videos.original_path(video.id), targets, VIEW_SIZE)
            if not decoded:
                raise ToolError("No frames exist in that span.")
            content: list[str | Image] = [
                f"{video.filename or 'untitled'}  {span(start_seconds, end_seconds)}"
            ]
            for seconds, image in decoded:
                content.append(f"frame at {term.timecode(seconds)} ({seconds:.3f} s)")
                content.append(jpeg(image))
            return content

    @server.tool()
    def add_video(
        path: Annotated[str, Field(description="Absolute path to a video file.")],
    ) -> JobView:
        """Copy a video into the library and queue it for indexing.

        The original file is left unchanged. Indexing can take minutes for a
        long video. Follow it with job_status.
        """
        with reported():
            source = Path(path).expanduser()
            if not source.is_file():
                raise ToolError(f"No file at {source}.")
            store = job_store()
            with source.open("rb") as file:
                video = videos.add(file, source.name)
            return job_view(store.create(video.id, IndexOperation(type="index")))

    @server.tool()
    def make_clip(
        video_id: UUID,
        start_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)],
        end_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)],
    ) -> JobView:
        """Queue a playable MP4 clip of part of a video.

        The original is left unchanged. When the job succeeds, job_status
        returns the clip's file path.
        """
        with reported():
            video = stored_video(video_id)
            operation = ClipOperation(
                type="clip", start_seconds=start_seconds, end_seconds=end_seconds
            )
            operation.validate_source_duration(video.duration_seconds)
            return job_view(job_store().create(video.id, operation))

    @server.tool(annotations=READ_ONLY)
    def job_status(job_id: UUID) -> JobView:
        """Report a job's progress, its output file, or why it failed."""
        with reported():
            job = job_store().get(job_id)
            if job is None:
                raise ToolError(f"No job {job_id}.")
            return job_view(job)

    return server
