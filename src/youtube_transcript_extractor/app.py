import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
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
from .phase0_event_recovery import install_phase0_event_chain_recovery
from .phase0_x402 import attach_phase0_x402_routes
from .youtube import YouTubeTranscriptClient, extract_video_id

STATIC_DIR = Path(__file__).resolve().parent / "static"
LOGGER = logging.getLogger("uvicorn.error")


def _exception_types(error: Exception):
    types = []
    seen = set()
    current = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        types.append(current.__class__.__name__)
        current = current.__cause__ or current.__context__
    return types


def _request_context(request: Request) -> dict:
    return dict(getattr(request.state, "log_context", {}))


def _mark_request_failure(
    request: Request,
    error: Exception,
    status_code: int,
) -> None:
    request.state.failure = {
        "status_code": status_code,
        "error_code": getattr(error, "code", "transcript_error"),
        "exception_types": _exception_types(error),
    }


def _record_dependency_failure(
    request: Request,
    operation: str,
    error: Exception,
) -> None:
    failures = getattr(request.state, "dependency_failures", None)
    if failures is None:
        failures = []
        request.state.dependency_failures = failures
    failures.append(
        {
            "operation": operation,
            "error_code": getattr(error, "code", error.__class__.__name__),
            "exception_types": _exception_types(error),
        }
    )


def _log_event(
    request: Request,
    event: str,
    duration_ms: float,
    level: int = logging.WARNING,
    exc_info=None,
    **fields,
) -> None:
    payload = {
        "event": event,
        "request_id": request.state.request_id,
        "method": request.method,
        "path": request.url.path,
        "duration_ms": round(duration_ms, 1),
    }
    payload.update(_request_context(request))
    payload.update(fields)
    LOGGER.log(
        level,
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        exc_info=exc_info,
    )


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
    attach_phase0_x402_routes(app, settings)
    install_phase0_event_chain_recovery(app, settings)

    @app.middleware("http")
    async def log_failed_requests(request: Request, call_next):
        request.state.request_id = uuid4().hex
        started_at = perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            _log_event(
                request,
                "request_failed",
                (perf_counter() - started_at) * 1000,
                level=logging.ERROR,
                exc_info=(type(exc), exc, exc.__traceback__),
                status_code=500,
                error_code="unhandled_exception",
                exception_types=_exception_types(exc),
            )
            raise

        duration_ms = (perf_counter() - started_at) * 1000
        for failure in getattr(request.state, "dependency_failures", []):
            _log_event(
                request,
                "dependency_failed",
                duration_ms,
                status_code=response.status_code,
                **failure,
            )

        if request.url.path.startswith("/api/") and response.status_code >= 400:
            failure = getattr(
                request.state,
                "failure",
                {
                    "status_code": response.status_code,
                    "error_code": "http_error",
                    "exception_types": [],
                },
            )
            _log_event(request, "request_failed", duration_ms, **failure)

        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "ai_formatting_configured": app.state.formatter.available,
            "proxy_configured": bool(settings.proxy_url),
            "phase0_x402_enabled": settings.phase0_x402_enabled,
        }

    @app.post("/api/extract", response_model=ExtractResponse)
    def extract(request: ExtractRequest, http_request: Request):
        warnings = []
        try:
            video_id = extract_video_id(request.url)
        except InvalidYouTubeUrl as exc:
            _mark_request_failure(http_request, exc, 400)
            raise _http_error(exc, 400) from exc

        http_request.state.log_context = {
            "video_id": video_id,
            "language": request.language or "auto",
            "refresh": request.refresh,
            "include_timestamps": request.include_timestamps,
        }

        cached = None
        if not request.refresh:
            try:
                cached = app.state.transcript_cache.get_latest(
                    video_id,
                    request.language,
                )
            except TranscriptCacheError as exc:
                _record_dependency_failure(http_request, "cache_read", exc)
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
                _mark_request_failure(http_request, exc, 400)
                raise _http_error(exc, 400) from exc
            except TranscriptUnavailable as exc:
                _mark_request_failure(http_request, exc, 422)
                raise _http_error(exc, 422) from exc
            except TranscriptBlocked as exc:
                _mark_request_failure(http_request, exc, 503)
                raise _http_error(exc, 503) from exc

            try:
                cached = app.state.transcript_cache.store_new_version(
                    document,
                    request.language,
                )
            except TranscriptCacheError as exc:
                _record_dependency_failure(http_request, "cache_write", exc)
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
                except TranscriptCacheError as exc:
                    _record_dependency_failure(
                        http_request,
                        "formatted_cache_read",
                        exc,
                    )
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
                except FormatterUnavailable as exc:
                    _record_dependency_failure(http_request, "formatter", exc)
                    formatting_message = (
                        "AI formatting is not configured. Set OPENAI_API_KEY and restart the app."
                    )
                except FormatterError as exc:
                    _record_dependency_failure(http_request, "formatter", exc)
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
                        except TranscriptCacheError as exc:
                            _record_dependency_failure(
                                http_request,
                                "formatted_cache_write",
                                exc,
                            )
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
    def format_transcript(request: FormatRequest, http_request: Request):
        http_request.state.log_context = {"transcript_chars": len(request.clean_markdown)}
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
        except FormatterUnavailable as exc:
            _record_dependency_failure(http_request, "formatter", exc)
            return FormatResponse(
                available=False,
                message=(
                    "AI formatting is not configured. Set OPENAI_API_KEY and restart the app."
                ),
            )
        except FormatterError as exc:
            _record_dependency_failure(http_request, "formatter", exc)
            return FormatResponse(available=False, message=str(exc))
        return FormatResponse(
            available=True,
            formatted_markdown=formatted,
            warnings=warnings,
        )

    return app


app = create_app()