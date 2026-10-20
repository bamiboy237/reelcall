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

Only the next slice receives a detailed design. Later slices describe outcomes,
not a fixed architecture.

| Slice | Outcome | Review workflow |
| --- | --- | --- |
| 1. Inspect one local video | Report duration, dimensions, codecs, frame rate, and audio streams. | Run the command against a local video and inspect its properties. |
| 2. Process one local video | Convert to a selected resolution and extract a thumbnail. Preserve the original. | Play the converted video and open the thumbnail. |
| 3. Accept and store uploads | Add an HTTP API, local file storage, and persistent video records. Reuse inspection code. | Upload a video and retrieve its stored properties. |
| 4. Process queued jobs | Add a persistent queue and one background worker. Reuse conversion code. | Submit a video, follow its job state, and retrieve the outputs. |
| 5. Make jobs recoverable | Add processing progress, bounded retries, and recovery after a worker stops. | Interrupt processing, restart the worker, and inspect the outcome. |
| 6. Create timestamped clips | Divide videos into clips. Preserve original timestamps and generate thumbnails. | Open clips and compare their timestamps with the original. |
| 7. Search speech | Add transcripts and text embeddings. Evaluate EmbeddingGemma after checking its documented input support. | Search spoken content and jump to the matching moment. |
| 8. Search visuals and sound | Add analysis with models that support the required visual and audio inputs. | Try the example queries and measure search quality and processing cost. |

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
- Verified readable terminal JSON with `uv run python -m json.tool`. Updated
  the README curl examples to use this formatter without changing API responses.
- Stopped the review server after the manual workflow. Added no tests or smoke
  scripts. These changes have not run on GitHub CI yet.

### Limits

The API is local-only and has no authentication or configured upload-size limit.
Inspection runs during the upload request. Automatic conversion and job handling
remain deferred. A process crash between the file move and database commit can
leave an unreferenced original. Crash recovery is not part of this slice.

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
