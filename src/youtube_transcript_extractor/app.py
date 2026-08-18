from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .cache import CachedTranscript, TranscriptCache, TranscriptCacheError
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
    AnalysisInfo,
    ExtractRequest,
    ExtractResponse,
    FormatRequest,
    FormatResponse,
)
from .normalize import render_document
from .youtube import YouTubeTranscriptClient, extract_video_id

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _http_error(error: Exception, status_code: int) -> HTTPException:
    code = getattr(error, "code", "transcript_error")
    message = getattr(error, "message", str(error))
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def _formatter_cache_key(formatter: object) -> str:
    cache_key = getattr(formatter, "cache_key", None)
    if cache_key:
        return str(cache_key)
    formatter_type = formatter.__class__
    return "{}:{}".format(formatter_type.__module__, formatter_type.__qualname__)


def create_app(
    settings: Optional[Settings] = None,
    transcript_client=None,
    formatter=None,
    transcript_cache=None,
) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="YouTube Transcript Extractor", version="0.1.0")
    app.state.settings = settings
    app.state.transcript_client = (
        transcript_client
        if transcript_client is not None
        else YouTubeTranscriptClient(proxy_url=settings.proxy_url)
    )
    app.state.formatter = (
        formatter
        if formatter is not None
        else OpenAIFormatter(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
        )
    )
    app.state.transcript_cache = (
        transcript_cache
        if transcript_cache is not None
        else TranscriptCache(settings.cache_path)
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
            "proxy_configured": bool(settings.proxy_url),
        }

    @app.post("/api/extract", response_model=ExtractResponse)
    def extract(request: ExtractRequest):
        warnings = []
        try:
            video_id = extract_video_id(request.url)
        except InvalidYouTubeUrl as exc:
            raise _http_error(exc, 400)

        cached = None
        if not request.refresh:
            try:
                cached = app.state.transcript_cache.get_latest(
                    video_id,
                    request.language,
                )
            except TranscriptCacheError:
                warnings.append(
                    "The local cache could not be read; this transcript was analyzed again."
                )

        transcript_from_cache = cached is not None
        if cached is None:
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

            try:
                cached = app.state.transcript_cache.store_new_version(
                    document,
                    request.language,
                )
            except TranscriptCacheError:
                warnings.append(
                    "The local cache could not be updated; this result will not be reused."
                )
                cached = CachedTranscript(
                    cache_id=0,
                    document=document,
                    version=1,
                    analyzed_at=datetime.now(timezone.utc),
                )
        document = cached.document
        analyzed_date = cached.analyzed_at.date().isoformat()
        clean_markdown = render_document(
            document,
            include_timestamps=request.include_timestamps,
            version=cached.version,
            analyzed_at=analyzed_date,
        )

        formatted_markdown = None
        formatting_message = None
        formatted_from_cache = False
        formatter_warnings = []
        if len(clean_markdown) > settings.max_transcript_chars:
            warnings.append(
                "This transcript is too large for the v1 AI formatter; clean output is still available."
            )
            formatting_message = (
                "This transcript is too large for the v1 AI formatter. "
                "Use the clean transcript instead."
            )
        else:
            formatter_key = _formatter_cache_key(app.state.formatter)
            cached_format = None
            if cached.cache_id:
                try:
                    cached_format = app.state.transcript_cache.get_formatted(
                        cached.cache_id,
                        clean_markdown,
                        formatter_key,
                    )
                except TranscriptCacheError:
                    warnings.append(
                        "The cached formatted transcript could not be read; it was formatted again."
                    )

            if cached_format is not None:
                formatted_markdown, formatter_warnings = cached_format
                formatted_from_cache = True
            elif not app.state.formatter.available:
                formatting_message = (
                    "AI formatting is not configured. Set OPENAI_API_KEY and restart the app."
                )
            else:
                try:
                    formatted_markdown, formatter_warnings = app.state.formatter.format(
                        clean_markdown
                    )
                except FormatterUnavailable:
                    formatting_message = (
                        "AI formatting is not configured. Set OPENAI_API_KEY and restart the app."
                    )
                except FormatterError as exc:
                    formatting_message = str(exc)
                else:
                    if cached.cache_id:
                        try:
                            app.state.transcript_cache.store_formatted(
                                cached.cache_id,
                                clean_markdown,
                                formatter_key,
                                formatted_markdown,
                                formatter_warnings,
                            )
                        except TranscriptCacheError:
                            warnings.append(
                                "The formatted transcript could not be cached for next time."
                            )

        warnings.extend(formatter_warnings)
        return ExtractResponse(
            transcript=document,
            clean_markdown=clean_markdown,
            formatted_markdown=formatted_markdown,
            formatting_available=formatted_markdown is not None,
            formatting_message=formatting_message,
            analysis=AnalysisInfo(
                version=cached.version,
                analyzed_at=cached.analyzed_at,
                transcript_from_cache=transcript_from_cache,
                formatted_from_cache=formatted_from_cache,
            ),
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
