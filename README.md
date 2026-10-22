# Reelcall

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
uv tool install git+https://github.com/bamiboy237/reelcall
reelcall
```

Install Docker and FFmpeg first. On macOS, Reelcall starts Docker Desktop or
OrbStack when needed. On Linux, start Docker Engine before launching the app.

`reelcall` builds the worker image on first launch, starts PostgreSQL and the
workers, and opens the library. Later launches reuse the image and database.
The library stays in the current folder's `data/` directory. Use
`reelcall ui --data-dir /path/to/library` to choose another library.

The database listens on a free localhost port. Reelcall keeps its generated
password in a private file under your user state directory, outside the library.
Closing the app leaves the workers running.

```sh
reelcall stop       # stop this library's services without deleting its data
reelcall -h         # commands for scripts
```

Set `HF_TOKEN` before launch if the embedding model requires your Hugging Face
access token. Reelcall passes it to the indexing worker.

If you set `VIDEO_PIPELINE_DATABASE_URL`, Reelcall uses that connection without
starting local containers. `reelcall ui --no-services` opens the app without
Docker startup.

## Update an older install

If `uv tool list` shows `video-pipeline`, remove the old tool before installing
Reelcall:

```sh
uv tool uninstall video-pipeline
uv tool install git+https://github.com/bamiboy237/reelcall
```

Then run `reelcall` from your library's folder. The library files and Docker
database stay in place.

For an existing Git checkout, pull the changes and refresh its environment:

```sh
uv sync --locked --reinstall-package reelcall
uv run reelcall
```
