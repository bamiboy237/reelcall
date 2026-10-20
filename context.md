# Video processing service with search by description

Build a video processing service, then extend it with search by description.

## Video processing

- Inspect a local video's properties.
- Convert videos into different resolutions and extract thumbnails.
- Accept uploads and store the original files.
- Process jobs through a queue and background workers.
- Report progress, retry failed jobs, and recover from worker failures.

## Search by description

- Divide videos into clips and preserve their original timestamps.
- Process visual content, audio, and speech transcripts.
- Create embeddings, numeric representations of meaning, that let users search by description.
- Return matching clips with thumbnails, timestamps, and playback links.

Example queries:

- “The part where someone drops the red cup.”
- “Where she explains database indexes.”
- “When the alarm starts.”

Evaluate EmbeddingGemma for retrieval. Verify which version supports the required inputs before choosing it.

Build the processing foundation first, then extend the same system with search. Start with one local video and one worker. Add complexity as the requirements demand it.

Measure whether searches find the correct moments, how precise their timestamps are, and how much processing time and money each video requires.
