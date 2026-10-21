# Video Processing Pipeline

A video processing service with search by description.

## Video processing

Upload and preserve original videos, inspect their properties, and run editing
requests as background jobs. Track progress, then view or download the results.

## Search by description

Planned search lets users find specific moments using everyday language:

- “The part where someone drops the red cup.”
- “Where she explains database indexes.”
- “When the alarm starts.”

Search results will include matching clips, thumbnails, original timestamps,
and playback links.

## Use

```sh
docker compose up -d      # job service: processing and indexing
video-pipeline            # library, search and progress
video-pipeline -h         # one-shot commands for scripts (add --json)
```

The app needs `VIDEO_PIPELINE_DATABASE_URL` pointing at the job service's
database (`postgresql://video_pipeline:…@127.0.0.1:15432/video_pipeline`).
Without it, the library and search still work; adding work waits for the
service.
