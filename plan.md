# Build plan

Build the video processing foundation first. Extend the same system with search
by description. Start with one local video and one worker.

## Working rules

- Build one usable slice at a time. Stop for review after each slice.
- Keep code direct. Add abstractions only when the current code needs them.
- Before each slice, search for maintained SDKs, libraries, abstractions, and
  open-source services that could reduce custom code or improve the system.
- Check primary documentation before adopting a tool. Use Context7 when available.
  Compare the benefit with setup, dependencies, operational cost, and maintenance.
- Record consequential tool choices and their reasons. Do not add a dependency
  only because it exists or claim an improvement without evidence.
- Do not add automated tests unless a specific risk makes them necessary.
  Discuss that exception before adding them.
- Do not add smoke scripts or run separate smoke checks.
- Verify through the real workflow. Run the command, inspect its output, and
  open generated media when the slice produces it.
- Avoid speculative checks, silent fallbacks, and broad exception handlers.
  Report expected failures clearly.
- Add infrastructure only when the next slice needs it.
- Keep changes within the current slice.

## Initial technical direction

- Use Python and manage the project with `uv`.
- Start with a command-line interface.
- Use `ffprobe` to inspect videos and `ffmpeg` to process them.
- Call these tools directly at first. Reconsider a wrapper if research shows
  a clear benefit for the current slice.
- Defer database, queue, HTTP framework, and model choices until they are needed.

## Build sequence

The processing foundation and timestamped clips are merged. The user approved
the local search and service integration batches together on 2026-10-21.

| Slice | Outcome | Review workflow |
| --- | --- | --- |
| 1. Inspect one local video | Report duration, dimensions, codecs, frame rate, and audio streams. | Run the command against a local video and inspect its properties. |
| 2. Process one local video | Convert to a selected resolution and extract a thumbnail. Preserve the original. | Play the converted video and open the thumbnail. |
| 3. Accept and store uploads | Add an HTTP API, local file storage, and persistent video records. Reuse inspection code. | Upload a video and retrieve its stored properties. |
| 4. Process queued jobs | Add a persistent queue and one background worker. Reuse conversion code. | Submit a video, follow its job state, and retrieve the outputs. |
| 5. Make jobs recoverable | Add processing progress, bounded retries, and recovery after a worker stops. | Interrupt processing, restart the worker, and inspect the outcome. |
| 6. Create timestamped clips | Create a requested start-to-end clip without changing the original. Merged in PR #2. | Open clips and compare their timestamps with the original. |
| 7. Search local videos | Index speech, pictures, and sound with EmbeddingGemma 2. Preserve timestamps without rendering a file for every window. | Compare all three input views against the user's 30 reference queries. |
| 8. Integrate search with the service | Queue indexing and expose search, thumbnails, and timestamped playback through the existing API. | Upload, index, search, play a result, and request a downloadable clip. |

## Slice 1: Inspect one local video

Status: locally verified and approved after user review. Slice 1 is complete.

### Deliverables

- A minimal Python project managed with `uv`.
- A command that accepts a local video path and invokes `ffprobe`.
- Readable output for duration, dimensions, video codec, frame rate, and audio
  streams, including their codecs.
- Setup and usage instructions in `README.md`.

### Acceptance criteria

- Run the documented command through `uv` against a user-selected local video.
- Compare the reported properties with `ffprobe` output for that video.
- Report a video with no audio without inventing an audio stream.
- A missing or unreadable input produces a clear error and a nonzero exit code.
- Leave the original video unchanged.
- Review the code and actual command output before starting slice 2.

### Not included

- Conversion or thumbnail extraction.
- Uploads, an HTTP API, or a frontend.
- A database, a queue, workers, or Docker.
- Search, transcripts, or models.
- Automated tests or smoke scripts.

### Tool decision

Use Python's `subprocess.run` to call `ffprobe` and read its JSON output.
Keep inspection separate from command-line presentation.

Reviewed the [ffprobe documentation](https://ffmpeg.org/ffprobe.html) and
[ffmpeg-python through Context7](https://github.com/kkroening/ffmpeg-python).
The wrapper still requires the external binary and returns the same metadata.
It does not remove enough work to justify a runtime dependency for this slice.
Reconsider media libraries when a later slice needs more than command execution.

### Local verification

- Ran the inspection command against the user-selected video.
- Matched the output with `ffprobe`: 540.781 seconds, AV1 video, 640 x 480,
  25 fps, and Opus audio.
- Created a temporary video-only copy without re-encoding. Inspection reported
  `Audio: none`.
- Confirmed that a missing input and a text file produce clear errors and exit
  code 1.
- Compared the original video's SHA-256 checksum before and after inspection.
  The file content did not change.
- Passed `uv run ruff format --check .`, `uv run ruff check .`, and
  `uv run ty check`.
- Added no automated tests or smoke scripts.

## Slice 2: Process one local video

Status: locally verified and approved after user review. Slice 2 is complete.

### Deliverables

- A `process` command with a local video path, target height, and output directory.
- An H.264 MP4 at the selected height, with the original aspect ratio.
- AAC audio for every source audio stream, when present.
- A JPEG thumbnail from the first frame of the converted video.
- Setup and usage instructions in `README.md`, without build commentary.

### Behavior

- Accept a positive even height. Calculate an even width with FFmpeg's scale filter.
- Reuse inspection to read the source and select its first video stream.
- Create a new output directory containing `video.mp4` and `thumbnail.jpg`.
- Refuse an existing output directory. Do not overwrite the source or other files.
- Run conversion and thumbnail extraction in sequence.
- Report output paths only after both operations succeed.
- Report failures without silent fallbacks. Leave partial outputs for inspection.

### Tool decision

Reviewed [ffmpeg-python's conversion and thumbnail examples](https://github.com/kkroening/ffmpeg-python/tree/master/examples)
and [PyAV's scope](https://pyav.org/docs/stable/). Both can handle media processing,
but this slice needs only a scale filter and single-frame extraction.
Keep direct FFmpeg calls rather than add a Python runtime dependency.

Checked [FFmpeg's scale documentation](https://ffmpeg.org/ffmpeg-filters.html#scale)
through Context7. `scale=-2:height` preserves display aspect ratio and calculates
an even width. Use H.264 with `yuv420p` and AAC in MP4 for common playback support.
Use two sequential commands so thumbnail extraction reads the finished video.
No parallel processing, hardware encoder selection, or model services are needed.

### Acceptance criteria

- Process the user-selected video at 360 pixels high.
- Inspect the converted video for 480 x 360 dimensions, H.264 video, AAC audio,
  and the expected duration and frame rate.
- Play the converted video and open its thumbnail for visual review.
- Process a video without audio and retain the absence of audio.
- Confirm that invalid heights and an existing output directory fail clearly.
- Compare source and output checksums to confirm that failed reruns do not overwrite
  them.
- Run formatting, linting, and type checks.
- Add no automated tests or smoke scripts. Stop for user review before slice 3.

### Local verification

- Processed the full review video at 360 pixels high in about 10 seconds.
- Inspected the output: 540.781 seconds, H.264 video, 480 x 360, 25 fps, and AAC
  audio. The original and converted video have the same reported duration.
- Opened the JPEG with the image reader. It is 480 x 360 and shows the first frame.
- Played the first five seconds with `ffplay`. Playback exited successfully.
  Did not watch the full video.
- Desktop window inspection was unavailable: `/bin/sh: orca: command not found`.
  User review of visible playback remains.
- Processed a three-second video-only copy at 240 pixels high. The result was
  320 x 240 H.264 video with `Audio: none`. Paths containing spaces worked.
- Confirmed that odd and zero heights fail with exit code 1.
- Confirmed that an existing output directory fails with exit code 1.
- Compared SHA-256 checksums. The source remained unchanged after processing.
  The source, converted video, and thumbnail remained unchanged after the failed
  rerun against an existing directory.
- Built the source distribution and wheel with `uv build`.
- Passed `uv run ruff format --check .`, `uv run ruff check .`, and
  `uv run ty check`.
- Added no automated tests or smoke scripts.

## Slice 3: Accept and store uploads

Status: locally verified and approved after user review. Slice 3 is complete.

### Deliverables

- A `serve` command for an HTTP API on `127.0.0.1`.
- `POST /videos` to upload, preserve, and inspect an original.
- `GET /videos/{id}` to retrieve persistent video properties.
- `GET /videos/{id}/original` to download the unchanged original.
- SQLite records and local original-file storage under a selected data directory.
- Factual setup and usage documentation in `README.md`.

### Tool decision

Reviewed [FastAPI's file upload support](https://fastapi.tiangolo.com/tutorial/request-files/)
and [Pydantic's supported types](https://docs.pydantic.dev/latest/api/standard_library_types/)
through Context7. Use `UploadFile` rather than load the entire upload into a byte
string. Use Pydantic to serialize the existing inspection dataclasses, including
exact fraction frame rates. Uvicorn runs the local server.

Reviewed [FastAPI's SQLModel example](https://fastapi.tiangolo.com/tutorial/sql-databases/)
and [Python's SQLite API](https://docs.python.org/3.12/library/sqlite3.html).
One table needs only inserts and lookups by ID. Use built-in SQLite rather than
add SQLModel and SQLAlchemy. Store each typed record as JSON with an indexed ID.
Open and close a database connection per operation. Do not share connections
between request threads.

### Behavior

- Accept a multipart `file` field. Inspect actual content rather than trust its
  filename or declared content type.
- Copy uploads through file objects into temporary storage. Publish the original
  under a generated UUID only after inspection succeeds.
- Keep the supplied filename as metadata. Never use it as a filesystem path.
- Return `201` with the stored record and a `Location` header.
- Return `422` for unreadable media or an input without video, and `404` for an
  unknown ID. Remove temporary files from rejected uploads.
- Run blocking file work, inspection, and SQLite operations in synchronous
  endpoints so FastAPI runs them outside the main async event loop.
- Keep existing inspect and process commands unchanged in behavior.
- Do not add authentication, remote hosting, queues, workers, automatic conversion,
  a frontend, tests, or smoke checks.

### Acceptance criteria

- Start the documented server command and upload the user-selected video with curl.
- Inspect the real response shape, status code, and stored properties.
- Retrieve metadata and download the original. Compare its bytes with the source.
- Restart the server with the same data directory and retrieve the same record.
- Upload a second video with the same filename without replacing the first.
- Verify no-audio input, invalid media, missing upload field, and unknown IDs.
- Confirm rejected media leaves no record or original, and supplied filenames
  cannot control the storage path.
- Run the existing static checks and package build.
- Stop for user review before slice 4. Do not commit or push without approval.

### Local verification

- Started `uv run video-pipeline serve` and uploaded the review video with curl.
  The server returned `201`, a `Location` header, and the expected JSON fields.
- Confirmed 540.781 seconds, 640 x 480 AV1 video at 25 fps, and Opus audio.
- Retrieved the record with `200`. Its JSON matched the upload response.
- Downloaded the original with `200` and compared its bytes with the source.
  The files matched exactly.
- Uploaded the same filename again. It received a different ID and did not
  replace the first original.
- Rejected text labeled as MP4 and audio-only media with `422`. The database
  record count and stored-file count did not change. No temporary upload remained.
- Confirmed `422` for a missing upload field or malformed UUID, and `404` for an
  unknown UUID.
- Uploaded a video-only clip with the supplied filename `../../outside.mp4`.
  The server stored it under a UUID inside `originals`, retained the supplied name
  only as metadata, and reported an empty audio-stream list.
- Restarted the server and retrieved the same metadata and original bytes with
  `200`. Both still matched the source.
- Retrieved `/docs` and `/openapi.json` with `200`. Checked the documented routes.
  Did not verify the documentation page in a browser.
- Passed locked dependency installation, Ruff formatting, Ruff linting, ty checks,
  and the package build.
- Made metadata and error responses indented JSON by default, with a trailing
  newline. No flag or client-side formatter is required.
- Verified raw upload and retrieval responses, `404` and `422` error responses,
  and unchanged original download bytes. Removed formatter pipes from the README.
- Stopped the review server after the manual workflow. Added no tests or smoke
  scripts. These changes have not run on GitHub CI yet.

### Limits

The API is local-only and has no authentication or configured upload-size limit.
Inspection runs during the upload request. Automatic conversion and job handling
remain deferred. A process crash between the file move and database commit can
leave an unreferenced original. Crash recovery is not part of this slice.

## Slice 4: Queue and process editing requests

Status: initial custom-queue implementation superseded by the correction below.

### Scope and reasons

- Accept an operation and its parameters at `POST /videos/{id}/jobs`.
  The job format describes an edit, rather than making resolution the only
  possible kind of request.
- Start with the existing transcode and thumbnail operations. One job creates
  one output; clients can submit several jobs for different outputs.
  Additional edit types can be added without changing queue behavior.
- Use the job table itself as a persistent queue. One separately started worker
  processes it in submission order. Closing a client does not stop work.
- Expose job listing, status, real FFmpeg progress, and errors.
  Clients can disconnect and later retrieve the current state.
- Provide both viewing and download URLs. Video viewing supports byte ranges
  so players can seek.
- Preserve existing direct inspect and process commands.
- Keep cloud integration and UI work deferred. The earlier Typer changes remain
  in the subagent's worktree, not this worktree.
- Keep `README.md` limited to the project description. Put build details and
  review instructions in this plan and the thread, not README command sections.
- Keep automatic retries and worker-crash recovery for slice 5.

### Initial tool decision (superseded)

Reviewed [Huey's current SQLite and Postgres support](https://huey.readthedocs.io/en/stable/installation.html).
A task framework would still need our job records and progress reporting.
For this local, one-worker slice, use one SQLite job record as both the queue
entry and visible status, rather than coordinate a framework queue with a
second state store. No new queue service or dependency is required.

Use SQLite's [JSON functions](https://sqlite.org/json1.html) and
[UPDATE RETURNING](https://sqlite.org/lang_returning.html) to claim a queued
record and return it in one atomic statement. Index the status expression used
by that claim. Database connections close before FFmpeg runs.

Use FFmpeg's documented `-progress pipe:1` output. Save its processed timestamp
at FFmpeg's reporting interval, not once per video frame. Calculate conversion
percentage only when duration is known. Reuse one FFmpeg execution helper for
the direct command and worker.

### Latency

The user's target is upload-to-edit-ready latency, not model response time.
Measure upload and inspection separately from requested editing work.
Do not make every upload wait for a full conversion. A future editor may need
additional preparation; this slice does not claim to measure that UI.

### Acceptance criteria

- Submit multiple jobs before starting a worker and observe `queued` states.
- Observe a conversion's real intermediate progress while a later job waits.
- Stop and restart the API while the worker continues to completion.
- Confirm queued records survive an API restart.
- View and download completed video and thumbnail outputs. Check media types,
  inline versus attachment behavior, and a partial-content video request.
- Confirm a failed edit does not prevent the next queued job from running.
- Verify no-audio processing and the unchanged source.
- Check invalid operations and heights, unknown IDs, and output-not-ready errors.
- Run the existing formatter, linter, type checker, and build.
- Add no tests or smoke scripts. Stop for user review before committing or pushing.

### Local verification

- Uploaded the 10.3 MB review video. The local upload-and-inspection request
  returned `201` in about 0.23 seconds. This is not a cloud or editor-ready
  benchmark.
- Submitted a transcode job and a thumbnail job with `202` responses.
  Both remained queued until a separate worker started.
- Observed conversion progress at about 4.8% and 7.0% while the thumbnail job
  remained queued. Progress came from FFmpeg's processed timestamps.
- Stopped the review API during conversion. The worker finished that job,
  then the thumbnail, while the API was stopped. Results were available after
  the API restarted.
- The queued full-video conversion took about 19.8 seconds under the current
  machine load. The direct process command took about 20.0 seconds in a nearby
  run. These observations do not establish a controlled performance comparison.
- Retrieved and inspected the completed 480 x 360 H.264/AAC output, retaining
  the 540.781-second duration and 25 fps.
- Confirmed inline `video/mp4` viewing, attachment downloads, and identical
  view/download bytes. A byte-range request returned `206` and 1,024 bytes.
- Played three seconds directly from the output URL with `ffplay`. Opened
  the 640 x 480 thumbnail with the image reader. Did not check a browser player.
- Restarted the API with two more jobs queued. Both records remained queued.
- Temporarily moved a review upload to check a missing-source failure. That job
  became `failed`; the next job completed successfully. Restored the upload
  and confirmed it still matched the original bytes.
- Processed a video-only clip into 320 x 240 H.264 with no audio.
- Confirmed `409` before an output was ready, `404` for an unknown video, and
  `422` for an unsupported operation or invalid height.
- Passed Ruff formatting, Ruff linting, ty checks, and the source/wheel build.
  No automated tests or smoke scripts were added. GitHub CI has not run for
  these unpushed changes.

### Limits

Run one worker and keep the API and worker on the same machine with the same
data directory. The local queue is not a deployed shared cloud database.
Abrupt worker termination or a database failure can leave a job marked
`running`; automatic recovery and retries remain deferred. Graceful Ctrl+C
handling was not exercised in an interactive terminal during this review.
The current edit implementations are transcode and thumbnail. The job format
can gain further explicitly supported operation types; it is not a general
editing pipeline yet.

## Queue correction: Use Procrastinate

Status: merged. The startup correction and timestamped clip feature are also
merged. The verification notes below describe the earlier review stages.

### Approved scope

- Run locally first, using PostgreSQL through Docker.
- Use a maintained queue rather than our own claim query and polling worker.
- Support one request containing several jobs. Reject the whole batch if any
  item is invalid, and commit job records and queued tasks together.
- Verify two workers executing separate jobs.
- Recover unfinished jobs after a worker crash or planned shutdown, up to three
  total attempts.
  Do not automatically repeat ordinary edit errors or invalid inputs.
- Preserve existing public job IDs, completed results, and viewing/download URLs.
- Keep the README description-only. UI and cloud deployment remain deferred.
- Complete independent review before any commit or push. Muse Wonder was
  unavailable. Sonnet and Factory Droid with Kimi K3 completed source reviews.

### Tool choice and responsibilities

Use [Procrastinate](https://procrastinate.readthedocs.io/en/stable/) with
PostgreSQL. It owns task registration, selection, worker coordination,
heartbeats, and retry operations. Our application owns video operations,
progress, public job records, and publishing finished files.

Its documented stalled-job recovery requires enabling a periodic task; it is
not automatic merely because the dependency is installed. Our maintenance task
uses the library's `get_stalled_jobs`, `retry_job`, and `finish_job` APIs.
It checks once per minute, using the library's 30-second heartbeat threshold.
Recovery requires an available worker; when all workers are stopped, work
waits until a worker starts again.

Docker's init process runs as PID 1. The worker is its direct child. A worker
crash ends the container and therefore stops its FFmpeg child process.
A file lock protects each job's attempt directory.
Recovery acquires that lock and rechecks the queue attempt before removing
partial files or retrying work. It skips attempts that still hold the lock.

On a planned shutdown, workers allow 50 seconds for running jobs to finish.
Procrastinate then asks unfinished jobs to abort. A task retry strategy permits
another attempt only when the public attempt count is below three. That count
includes imported attempts. The SDK excludes user-requested aborts from this
shutdown retry path. Ordinary processing errors do not receive a retry.
Each retry starts processing again from the original, not from a partial frame.

Huey was considered, but its documented hard-kill task-loss behavior does not
meet this requirement alone. RQ requires Redis/Valkey as another service.
Procrastinate provides the required PostgreSQL-based queue and batch support.

### Data and configuration

PostgreSQL holds queue tasks, job records, and progress. Existing uploaded-video
metadata remains in SQLite, and video bytes remain on disk.

`init-jobs` initializes the library schema and imports legacy job records in a
transaction. Completed jobs are not re-enqueued. Unfinished records retain
their IDs and are re-enqueued; a previously started attempt counts toward the
three-attempt limit. Re-running the import does not duplicate existing jobs.
The old SQLite records and output files are not deleted or rewritten.

Each execution attempt writes to its own temporary directory. A successful
edit moves its completed file into place. Viewing and download endpoints expose
it only after the queue reports success.

The local Compose service is PostgreSQL 17, bound to `127.0.0.1:15432`, with a
named volume. `POSTGRES_PASSWORD` and `VIDEO_PIPELINE_DATABASE_URL` come from
the environment; no credential file was written or committed. Retain the same
PostgreSQL password when recreating a container that reuses the volume.

Compose runs one `init-jobs` service after PostgreSQL becomes healthy. Both workers
wait for that service to complete successfully. The setup service and workers
share the same database configuration, user ID, and data directory.

The Dockerfile installs locked dependencies before copying source code. Source
changes therefore do not invalidate the dependency-installation layer.

### Initial queue verification

- Imported the four existing development jobs, preserving IDs and terminal
  states. Repeated initialization imported zero duplicates.
- Submitted invalid batches with an invalid height or missing video. Neither
  created application records or queue tasks.
- Submitted a valid three-job batch with `202`. Two workers claimed distinct
  conversion jobs concurrently while the third waited.
- Killed a worker during conversion at about 1.9%. The output returned `409`,
  and the surviving worker later completed the same public job on attempt two.
- Killed a separate job's worker on attempts one, two, and three. Recovery
  stopped with `failed`, three attempts, and no published output.
- Added two explicitly synthetic queued/running legacy records in the isolated
  review dataset. Both imported and completed under their original IDs; the
  previously running record retained its earlier attempt count.
- Existing direct inspection remained functional, and zero concurrency was
  rejected with exit code 2.
- Validation used a separate `video_pipeline_review` PostgreSQL database and
  `data/queue-review` files. No original source video was modified.
- No automated tests or smoke scripts were added. GitHub CI has not run for
  these uncommitted changes.

### Review and follow-up verification

- Sonnet identified orphan FFmpeg processes after a native worker crash and
  maintenance recovery issues. Workers now run in Docker, recovery rechecks the
  current attempt under a file lock, and maintenance uses a queueing lock.
  Maintenance retention deletes only old maintenance tasks, not user job history.
- Kimi K3 reviewed the source without executing it. The proposed attempt-count
  defect was rejected after checking the installed SDK, PostgreSQL records, and
  HTTP responses. First-attempt success reported one attempt, recovered success
  reported two, and failure at the limit reported three.
- Accepted Kimi's startup dependency and Docker build-layer findings. The user
  also approved recovery after planned shutdowns. Deleted the temporary review
  report after discussion. Did not change encoder settings or add handling for
  manually deleted database rows or output files.
- Started a separate Compose project with a new PostgreSQL volume and
  `data/startup-review`. Database setup exited with code zero before either
  worker started. Both workers remained running with zero restarts.
- Used a 2,163.124-second development copy containing the review video four
  times. Stopped its worker during conversion. Shutdown waited about 50.7 seconds
  and exited with code zero. The same public job completed on attempt two.
- Stopped another conversion on attempts one, two, and three. It became failed
  at three attempts with no output and no remaining attempt directories.
- A missing-source edit failed on attempt one with a recorded error. Restored
  the source immediately after the check.
- Confirmed that a second container could not acquire the running attempt's
  file lock. The recovered job left no first-attempt directory.
- Retrieved the recovered output through both viewing and download URLs.
  Both returned `200` with identical bytes. `ffprobe` reported 480 x 360 H.264
  video, AAC audio, and the expected duration. Source checksums remained unchanged.
- Rebuilt the images after a source-only change. Docker reused the dependency
  installation layer and installed only the project in the final sync step.
- Repeated Compose startup with the existing database and files. Initialization
  imported zero duplicate jobs. All public job records and the completed output
  retained their checksums.
- Passed `uv sync --locked`, `uv run ruff format --check .`,
  `uv run ruff check .`, `uv run ty check`, `uv build`, Compose configuration
  validation, image builds, and `git diff --check`.
- Stopped the new review API and Compose services. Kept their database volume
  and review files. Stopping idle workers immediately after recreation required
  Docker's forced stop with exit code 137. No edit was running. The cause of
  that startup-window behavior has not been confirmed.
- The earlier three-crash limit check used native workers. A Docker hard-crash
  check confirmed that FFmpeg stopped with its worker and the job later
  succeeded on attempt two. A full three-crash Docker run and a crash during
  maintenance recovery have not been repeated after the review fixes.
- Added no automated tests or smoke scripts. These follow-up changes have not
  received a separate external review or run on GitHub CI.

## Search implementation

Status: both implementation batches are coded. The local model calls and the
focused service workflow below are verified. The full five-video, 30-query
quality comparison is not complete. Publication for review does not wait for
that long-running check. No completed model work needs to run again.

The user authorized a feature-branch push and a PR with an `@droid review`
request. The user did not authorize a merge.

### Scope

- Finish local indexing and search, then connect both to the existing service.
- Use the five local corpus videos and the user's latest 30 reference moments.
- Keep the Desktop source files unchanged. Normalize the single invalid JSON
  escape in the Sekiro reference in an ignored working copy.
- Keep reference queries and evidence out of the media index.
- Add no automated tests, smoke scripts, UI, cloud service, or vector database.
- Include this plan in the PR so Droid can review the decisions and evidence.
  Leave `context.md` unchanged.

### Model and media decisions

Use the official Sentence Transformers interface for `google/embeddinggemma-2`,
pinned to model revision `914f7f89142e33e77833254d9c9b90c3cef7303b`.
The model returns normalized 768-dimensional vectors, not timestamp answers.
The application owns window boundaries and retains the original file clock.

Use float32. The model documentation prohibits float16 because it can produce
invalid or degraded vectors. Let the SDK choose the device unless the caller
selects one. Query-only processes omit the vision and audio encoders.
Release unused MPS allocator buffers after each indexed window. A real
eight-second call retained about 1.1 GiB of reusable buffers after inference.
The release leaves live model tensors in place and avoids keeping unused
memory between windows.

Use the `SearchQuery` prompt for text queries. Do not add text prompts, source
filenames, reference answers, or generated captions to media inputs.

Start with eight-second windows and a four-second stride. Decode visual frames
at one frame per second with PyAV. Keep their actual presentation timestamps.
Apply the source frame's display rotation before creating its image.
Use a 140-token vision budget per frame and images no larger than 448 pixels
per side. Decode the first audio stream with FFmpeg as mono float32 at 16 kHz.
Pass frames and waveforms directly to the SDK. Do not create per-window MP4
or WAV files.

Extend the final window when the next window would contain less than one
sampling interval. This avoids a separate millisecond-long tail caused by
container durations that do not divide evenly into the stride.

Store visual-only, audio-only, and combined vectors separately. This supports
comparison and explicit search modes. A source without audio has only a
visual view. Do not invent audio or silently switch to another model.

These bounded windows fit the model's shared 8,192-token context. They are
initial retrieval intervals, not exact event boundaries. Brief events can
produce low temporal overlap even when the returned window contains the event.

### Storage and retrieval decisions

Reuse the local SQLite database for search metadata, vectors, and JPEG thumbnails.
The current corpus does not justify another database or a vector extension.
Use exact cosine ranking over normalized float32 vectors.

Publish each video's complete index in one SQLite transaction. Failed work
leaves the last complete index available. A per-video file lock prevents two
workers from publishing competing indexes for the same source.
Reuse an existing index only when its source hash, model profile, and window
settings match.

The `all` search mode uses each window's highest visual, audio, or combined
score. Explicit modes expose each view separately. Suppress matches on the
same video when they overlap at least half of the shorter interval.
Scores are similarities, not confidence percentages. The API has an optional
minimum score, but no claimed universal no-match threshold.

### Service integration decisions

Add an `index` job operation. Keep Procrastinate in charge of the queue,
attempt counts, worker heartbeats, and bounded recovery.
Use a dedicated `search` queue and one search worker so model work does not
block the existing edit workers or load a model in every edit worker.
The search worker uses the same shutdown and recovery rules as edit workers.

Persist the model cache under the shared data directory in Compose.
Use PyTorch's CPU wheels for Linux. Do not install CUDA dependencies in the
local Docker workers. macOS uses the normal PyTorch package and can use MPS.

The API exposes index metadata, search, stored JPEG thumbnails, and inline
original playback. Playback URLs carry a media time fragment.
The new playback route identifies MP4 from inspected container metadata.
Other containers remain downloadable as bytes, without a false MP4 type.
Browser codec support is still required.
Reuse the existing clip operation for requested downloads instead of rendering
every search result in advance.

### Commands and API

- `video-pipeline index PATH ... --data-dir DATA`: copy local videos into storage
  and index them. Output includes their video IDs.
- `video-pipeline index --video-id ID --data-dir DATA`: index an existing upload
  or reuse an unchanged index. The flag can be repeated.
- `video-pipeline search "QUERY" --data-dir DATA`: return ranked JSON matches.
- `video-pipeline evaluate-search REFERENCE.json ... --data-dir DATA`: compare
  all search modes with known reference moments. This is a quality measurement
  command, not a pass/fail test suite.
- `POST /videos/{id}/jobs` with `{"type":"index"}`: queue indexing. Window and
  stride settings are optional.
- `GET /videos/{id}/index`: retrieve the last complete index metadata.
- `POST /search` with `{"query":"...", "mode":"all", "limit":5}`: search indexed
  videos. Optional `video_ids` and `min_score` restrict results.
- `GET /videos/{id}/index/thumbnails/{window}`: retrieve a result thumbnail.
- `GET /videos/{id}/play`: stream the original with byte-range support.

Local index and search commands do not require PostgreSQL. Queued indexing
requires the existing database setup and the new Compose `search-worker`.

### Verification plan

1. Run real text, video, audio, and combined model calls on the M1 Mac.
2. Index all five videos and compare all views against the 30 user-reviewed
   moments. Report video selection and temporal overlap separately.
3. Measure indexing cost, query latency, and memory use. Do not infer local
   performance from published mobile benchmarks.
4. Run the real HTTP workflow through an isolated PostgreSQL database and
   Compose project. Preserve existing services and data.
5. Verify no-audio input, rejected parameters, an indexing failure, unchanged
   originals, persistent results, and reuse of an unchanged index.
6. Retrieve thumbnails and byte ranges. Request, retrieve, inspect, and play a
   clip from a search result.
7. Run Ruff formatting and linting, ty, the package build, Docker builds, and
   diff review. Add no automated tests or smoke checks.

### Primary references

- [EmbeddingGemma 2 model card](https://huggingface.co/google/embeddinggemma-2):
  model revision, precision, prompts, dimensions, and context limits.
- [Official multimodal guide](https://gemma.ai/models/embeddinggemma/multimodal/):
  frame lists, waveform inputs, and combined inputs through Sentence Transformers.
- [Google's Video Moments Finder](https://github.com/google-ai-edge/gallery/tree/main/Android/src/app/src/main/java/com/google/ai/edge/gallery/customtasks/videomomentfinder):
  a reference for timestamped local retrieval, not a copied Python backend.
- [PyAV container API](https://pyav.org/docs/stable/api/container.html):
  seek to a previous keyframe, then decode forward to the requested timestamp.
- [QVHighlights](https://proceedings.neurips.cc/paper/2021/file/62e0973455fd26eb03e91d5741a4a3bb-Paper.pdf):
  measure moment overlap, not only whether the correct video appears.

### Local verification

- Real text, audio, frame-list video, and combined inputs returned finite,
  normalized 768-dimensional vectors on the M1 with float32.
- Measured one eight-second window with the model already loaded:
  visual 7.48 seconds, audio 1.12 seconds, and combined 7.49 seconds.
  These figures are not a controlled hardware benchmark.
- The same run used about 2,646 MiB peak process RSS. Metal driver allocation
  reached about 4,869 MiB, then fell to about 3,748 MiB after releasing unused
  buffers. These measures overlap and must not be added.
- The Docker CPU search worker reached about 3.9 GiB resident memory during
  indexing. Concurrent Docker and MPS model work caused memory pressure.
  Subsequent model workflows run one at a time.
- Uploaded a real six-second H.264/AAC MP4 through HTTP. Submission returned
  `202`, progress changed during indexing, and the completed job exposed its
  index JSON. A video-only copy also indexed successfully without audio vectors.
- A mixed batch routed thumbnail work to the video queue and indexing work to
  the search queue. An invalid batch returned `404` without creating its jobs.
- Invalid stride, nonfinite window duration, blank query, zero result limit,
  and a stride below 0.5 seconds returned `422`.
- Found and fixed a real boundary failure: a 6.006-second source with a
  two-second stride attempted to index an empty 6 ms tail. The final window
  now includes that tail. The same source indexed successfully afterward.
- Search returned video IDs, time ranges, scores, thumbnails, and playback URLs.
  Measured one cold query at 9.69 seconds and the repeated warm query at
  0.072 seconds on the CPU API process.
- Retrieved a JPEG thumbnail and inspected its pixels. Original playback
  returned `206` for a 1,024-byte range with an inline MP4 content type.
  Downloaded original bytes matched the uploaded file.
- Submitted the top title-search result to the existing clip operation.
  Downloaded and inspected the resulting 2.006-second H.264/AAC MP4.
  Played it through `ffplay`. A browser's media-fragment seeking was not checked.
- Repeated indexing reused the same index in about 0.6 seconds. Temporarily
  moved the isolated stored copy to exercise a missing-source failure.
  That job failed once, the previous index remained unchanged, and the next
  index request succeeded. Restored the copy immediately after the failure.
- Restarted the API and retrieved identical index metadata.
- Checked display rotation on an MP4 with a real 90-degree display matrix.
  The sampled image had the expected portrait dimensions.
- Re-read all 59 windows from the first two completed corpus indexes after
  finalizing frame sampling. Their stored frame timestamps matched exactly.
- Compared all four search modes against six reference queries in the first
  indexed video. The combined and all modes returned overlapping top results
  for all six queries. Mean interval IoU was 0.2924. Only one of six top
  results reached IoU 0.5. This single-video comparison does not measure
  selection across the complete corpus.
- A separate two-second-window workflow ranked a title card above the girl
  described in the query. Search quality is not perfect. Neither this miss
  nor the single-video overlap results establish complete corpus accuracy.
- Passed the final locked dependency sync, Ruff formatting and linting, ty,
  package build, and diff whitespace checks before publication.
  Docker image builds and the queued workflow also passed during implementation.
- Added no automated tests or smoke scripts.

### Limits at publication

- Two of the five full corpus indexes are complete. The remaining local
  indexing continues from existing stored video IDs. The 30-query comparison
  is not yet available. Do not treat the PR as proof of complete search quality.
- No cloud GPU was provisioned. No media or source code was sent to a cloud
  inference provider.
- Search returns sampled time windows, not exact event boundaries.
  One-frame-per-second sampling can miss a brief event.
- Scores are not calibrated confidence values. An unrelated query can still
  receive ranked results unless the caller supplies a suitable minimum score.
- Native model calls and the real queued workflow are verified. A full
  three-crash recovery run was not repeated for the new indexing operation.
  It uses the existing shared retry and stalled-task recovery paths.
- The API restart retained the index and its metadata. Browser playback and
  media-fragment seeking were not verified.

## Review loop

1. Confirm the current slice and its acceptance criteria.
2. Research relevant tools and select the smallest suitable approach.
3. Implement the slice.
4. Run the real workflow and inspect the result.
5. Run configured formatting and static checks when applicable.
6. Summarize changes, verification evidence, and remaining limits.
7. Wait for review before starting the next slice.

## Search evaluation

When search is available, use videos with known target moments. Record whether
queries find the correct moments, how precise the timestamps are, and how much
processing time and money each video requires.
