# Crystalized memory

The hosted app runs on Fly with a volume-backed SQLite cache; the configured single-Machine topology matters because the cache is not shared between hosts. See [deployment details](README.md#flyio).

The public release history is maintained separately from Git's technical history in `src/youtube_transcript_extractor/static/release-notes.js`. The dialog leaves the current transcript in place. The FastAPI static mount serves that file as part of the packaged application.
