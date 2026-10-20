# Video Processing Pipeline

A video processing service with search by description.

## Video processing

Upload videos, preserve the originals, inspect their properties, convert them into different resolutions, and extract thumbnails. A queue and background workers handle processing, report progress, retry failed jobs, and recover from worker failures.

## Search by description

Find specific moments using everyday language:

- “The part where someone drops the red cup.”
- “Where she explains database indexes.”
- “When the alarm starts.”

The service divides videos into clips and analyzes visual content, audio, and speech transcripts. Embeddings capture meaning so searches can match relevant moments. Results include thumbnails, original timestamps, and playback links.

EmbeddingGemma will be evaluated for retrieval after verifying which version supports the required inputs.

The project starts with one local video and one worker. The processing foundation comes first, then search extends the same system. Success is measured by search accuracy, timestamp precision, and processing time and cost per video.

## Inspect a local video

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and
[FFmpeg](https://ffmpeg.org/download.html). The `ffprobe` command must be on `PATH`.
The project uses Python 3.12. `uv` can download it if needed.

From the repository root, run:

```sh
uv sync
uv run video-pipeline inspect "path/to/video.mp4"
```

The command reports duration, video codecs, dimensions, average frame rates, and
audio streams with their codecs. A video without audio reports `Audio: none`.
Unavailable duration or frame rate values appear as `not reported`.
Inspection does not change the original file.

A missing file, unreadable media, or an input without video produces an error
and exit code 1.

## Resize a video and extract a thumbnail

FFmpeg must include the `libx264` encoder.

```sh
uv run video-pipeline process "path/to/video.mp4" \
    --height 360 \
    --output-dir outputs/video-360p
```

Set `--height` to a positive even number of pixels. The width adjusts to preserve
the aspect ratio. A height larger than the source enlarges the video.

The command creates two files:

- `video.mp4`: the first video stream encoded as H.264, with all audio streams
  encoded as AAC when present. Subtitles and other streams are not included.
- `thumbnail.jpg`: the first frame of the converted video.

The output directory must not exist. The command creates it and prints the
output paths after both files are ready. It does not overwrite existing files
or change the original.

If processing fails, the command reports the error and exits with code 1.
Partial outputs remain in the output directory. Use a new directory to retry.

## Upload and retrieve videos

Start the local API:

```sh
uv run video-pipeline serve
```

The server listens on `http://127.0.0.1:8000`. Open
`http://127.0.0.1:8000/docs` for interactive API documentation.
Use `--port` to change the port and `--data-dir` to change the storage directory.
The API has no authentication and is for local use only.

In another terminal, upload a video:

```sh
curl --fail-with-body -sS \
    -F "file=@path/to/video.mp4" \
    http://127.0.0.1:8000/videos | uv run python -m json.tool
```

The response has status `201` and includes the video's `id`, `filename`,
`created_at`, `duration_seconds`, `video_streams`, and `audio_streams`.
Frame rates are exact fraction strings such as `"25"` or `"30000/1001"`.
Unavailable properties are `null`. A video without audio has an empty
`audio_streams` list.

Replace `paste-the-returned-id` below with the response's `id` value.
Retrieve its properties or download its original bytes:

```sh
VIDEO_ID="paste-the-returned-id"
curl --fail-with-body -sS "http://127.0.0.1:8000/videos/$VIDEO_ID" \
    | uv run python -m json.tool
curl --fail-with-body -sS "http://127.0.0.1:8000/videos/$VIDEO_ID/original" \
    --output downloaded-video.mp4
```

The server stores originals under generated IDs in `data/originals/` and records
their properties in `data/videos.sqlite3`. Supplied filenames are metadata, not
storage paths. Keep both the database and original files when moving the data
directory. Records and files remain available after a server restart.

Uploads run inspection before returning. They do not start conversion or
thumbnail extraction. A file without readable video returns `422` and is not
stored. An unknown video ID returns `404`. A malformed ID or missing upload
field returns `422`.

## Development checks

```sh
uv run ruff format --check .
uv run ruff check .
uv run ty check
```
