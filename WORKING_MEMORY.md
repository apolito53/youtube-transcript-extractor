# Working memory

The source interface has a small What's new dialog, pending a safe production deployment. Its user-facing release history is maintained in `src/youtube_transcript_extractor/static/release-notes.js`: add meaningful shipped changes in plain language, newest first, whenever the production experience changes. Use dates only when known. It is separate from technical Git history.

Before deploying, establish the live Fly image's exact source and an authorized app-specific deployment path. The public JavaScript and health response observed during the release-notes work lacked the request-ID changes present in Git commit `7ea6f34`; deploying current `master` without reconciling that difference would also ship unrelated API and logging changes. The Phase 0 resolver workflow has separate payment-related side effects and is not a deployment path for this feature.

The app has API failure logging, but does not yet have the centrally required local bounded debug/logging collector and retrieval workflow. Follow the project development-services guidance when addressing that gap.
