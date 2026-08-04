class TranscriptError(Exception):
    """Base class for user-facing transcript failures."""

    code = "transcript_error"
    public_message = "The transcript could not be retrieved."

    def __init__(self, message=None):
        super().__init__(message or self.public_message)
        self.message = message or self.public_message


class InvalidYouTubeUrl(TranscriptError):
    code = "invalid_url"
    public_message = "Enter a valid YouTube video URL."


class TranscriptUnavailable(TranscriptError):
    code = "unavailable"
    public_message = "This video does not have an accessible transcript."


class TranscriptBlocked(TranscriptError):
    code = "blocked"
    public_message = (
        "YouTube blocked the transcript request. Try again later or use the "
        "transcript shown in YouTube."
    )


class FormatterUnavailable(Exception):
    """Raised when AI formatting is not configured."""


class FormatterError(Exception):
    """Raised when the AI formatter fails after configuration is present."""
