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

## Development checks

```sh
uv run ruff format --check .
uv run ruff check .
uv run ty check
```
