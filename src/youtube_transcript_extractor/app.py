from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings, get_settings
from .errors import (
    FormatterError,
    FormatterUnavailable,
    InvalidYouTubeUrl,
    TranscriptBlocked,
    TranscriptUnavailable,
)
from .formatter import OpenAIFormatter
from .models import (
    ExtractRequest,
    ExtractResponse,
    FormatRequest,
    FormatResponse,
)
from .normalize import render_document
from .youtube import YouTubeTranscriptClient

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _http_error(error: Exception, status_code: int) -> HTTPException:
    code = getattr(error, "code", "transcript_error")
    message = getattr(error, "message", str(error))
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def create_app(
    settings: Optional[Settings] = None,
    transcript_client=None,
    formatter=None,
) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="YouTube Transcript Extractor", version="0.1.0")
    app.state.settings = settings
    app.state.transcript_client = transcript_client or YouTubeTranscriptClient()
    app.state.formatter = formatter or OpenAIFormatter(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "ai_formatting_configured": app.state.formatter.available,
        }

    @app.post("/api/extract", response_model=ExtractResponse)
    def extract(request: ExtractRequest):
        try:
            document = app.state.transcript_client.fetch(
                request.url,
                language=request.language or None,
            )
        except InvalidYouTubeUrl as exc:
            raise _http_error(exc, 400)
        except TranscriptUnavailable as exc:
            raise _http_error(exc, 422)
        except TranscriptBlocked as exc:
            raise _http_error(exc, 503)

        clean_markdown = render_document(
            document,
            include_timestamps=request.include_timestamps,
        )
        warnings = []
        if len(clean_markdown) > settings.max_transcript_chars:
            warnings.append(
                "This transcript is too large for the v1 AI formatter; clean output is still available."
            )
        return ExtractResponse(
            transcript=document,
            clean_markdown=clean_markdown,
            warnings=warnings,
        )

    @app.post("/api/format", response_model=FormatResponse)
    def format_transcript(request: FormatRequest):
        if len(request.clean_markdown) > settings.max_transcript_chars:
            return FormatResponse(
                available=False,
                message=(
                    "This transcript is too large for the v1 AI formatter. "
                    "Use the clean transcript instead."
                ),
            )
        if not app.state.formatter.available:
            return FormatResponse(
                available=False,
                message=(
                    "AI formatting is not configured. Set OPENAI_API_KEY and restart the app."
                ),
            )
        try:
            formatted, warnings = app.state.formatter.format(request.clean_markdown)
        except FormatterUnavailable:
            return FormatResponse(
                available=False,
                message=(
                    "AI formatting is not configured. Set OPENAI_API_KEY and restart the app."
                ),
            )
        except FormatterError as exc:
            return FormatResponse(available=False, message=str(exc))
        return FormatResponse(
            available=True,
            formatted_markdown=formatted,
            warnings=warnings,
        )

    return app


app = create_app()
