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
