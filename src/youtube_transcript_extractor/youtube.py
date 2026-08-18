import json
import re
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional
from urllib.parse import quote, urlparse
from urllib.request import ProxyHandler, Request, build_opener, urlopen

from .errors import (
    InvalidYouTubeUrl,
    TranscriptBlocked,
    TranscriptError,
    TranscriptUnavailable,
)
from .models import CaptionSegment, TranscriptDocument

VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}
VIDEO_PATH_PREFIXES = ("/shorts/", "/live/", "/embed/", "/v/")


@dataclass(frozen=True)
class CaptionTrack:
    language_code: str
    language_name: str
    is_generated: bool
    track: object


def extract_video_id(raw_url: str) -> str:
    """Return an 11-character YouTube video ID from a supported URL."""
    try:
        parsed = urlparse(raw_url.strip())
    except ValueError:
        raise InvalidYouTubeUrl()

    if parsed.scheme not in ("http", "https") or parsed.hostname is None:
        raise InvalidYouTubeUrl()

    hostname = parsed.hostname.lower().rstrip(".")
    if hostname not in YOUTUBE_HOSTS:
        raise InvalidYouTubeUrl()

    video_id = None
    if hostname.endswith("youtu.be"):
        video_id = parsed.path.strip("/").split("/", 1)[0]
    else:
        query_values = dict(
            part.split("=", 1) for part in parsed.query.split("&") if "=" in part
        )
        video_id = query_values.get("v")
        if not video_id:
            path = parsed.path.rstrip("/")
            for prefix in VIDEO_PATH_PREFIXES:
                if path.startswith(prefix):
                    video_id = path[len(prefix) :].split("/", 1)[0]
                    break

    if not video_id or not VIDEO_ID_PATTERN.fullmatch(video_id):
        raise InvalidYouTubeUrl()
    return video_id


def _track_value(track: object, name: str, default=None):
    value = getattr(track, name, default)
    if callable(value):
        return value()
    return value


def _caption_tracks(transcript_list: Iterable[object]) -> List[CaptionTrack]:
    tracks = []
    for track in transcript_list:
        language_code = str(_track_value(track, "language_code", "") or "")
        language_name = str(_track_value(track, "language", language_code) or language_code)
        if not language_code:
            continue
        tracks.append(
            CaptionTrack(
                language_code=language_code,
                language_name=language_name,
                is_generated=bool(_track_value(track, "is_generated", False)),
                track=track,
            )
        )
    return tracks


def choose_caption_track(
    tracks: Iterable[CaptionTrack], language: Optional[str] = None
) -> CaptionTrack:
    candidates = list(tracks)
    if not candidates:
        raise TranscriptUnavailable()

    requested = (language or "").strip().lower()
    if requested:
        exact = [
            track
            for track in candidates
            if track.language_code.lower() == requested
            or track.language_code.lower().startswith(requested + "-")
        ]
        if exact:
            candidates = exact
        else:
            raise TranscriptUnavailable(
                "No transcript is available for language '{}'.".format(language)
            )
        # Within an explicitly requested language, manual captions win.
        candidates.sort(key=lambda track: track.is_generated)
    else:
        def score(track: CaptionTrack):
            code = track.language_code.lower()
            english = code == "en" or code.startswith("en-")
            return (
                not english,
                track.is_generated,
                code != "en",
            )

        candidates.sort(key=score)
    return candidates[0]


def _snippet_value(snippet: object, name: str, default=0.0):
    if isinstance(snippet, dict):
        return snippet.get(name, default)
    return getattr(snippet, name, default)


def _segments_from_fetched(fetched: object) -> List[CaptionSegment]:
    snippets = getattr(fetched, "snippets", fetched)
    segments = []
    for snippet in snippets:
        text = str(_snippet_value(snippet, "text", "") or "").strip()
        if not text:
            continue
        try:
            start = float(_snippet_value(snippet, "start", 0.0) or 0.0)
            duration = float(_snippet_value(snippet, "duration", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        segments.append(
            CaptionSegment(
                text=text,
                start_seconds=max(0.0, start),
                duration_seconds=max(0.0, duration),
            )
        )
    if not segments:
        raise TranscriptUnavailable("The selected caption track was empty.")
    return segments


def fetch_oembed_metadata(source_url: str, proxy_url: Optional[str] = None) -> dict:
    """Best-effort title/author lookup; transcript extraction does not depend on it."""
    endpoint = "https://www.youtube.com/oembed?url={}&format=json".format(
        quote(source_url, safe="")
    )
    request = Request(endpoint, headers={"User-Agent": "ytx/0.1"})
    try:
        if proxy_url:
            opener = build_opener(
                ProxyHandler({"http": proxy_url, "https": proxy_url})
            )
            response_context = opener.open(request, timeout=5)
        else:
            response_context = urlopen(request, timeout=5)
        with response_context as response:
            if response.status < 200 or response.status >= 300:
                return {}
            payload = json.loads(response.read().decode("utf-8"))
            return {
                "title": payload.get("title"),
                "author": payload.get("author_name"),
            }
    except Exception:
        return {}


class YouTubeTranscriptClient:
    """Adapter around youtube-transcript-api with stable app-facing errors."""

    def __init__(
        self,
        api_factory: Optional[Callable[[], object]] = None,
        metadata_fetcher: Optional[Callable[[str], dict]] = None,
        proxy_url: Optional[str] = None,
    ):
        self._api_factory = api_factory
        self._proxy_url = proxy_url
        self._metadata_fetcher = metadata_fetcher

    def _make_api(self):
        if self._api_factory:
            return self._api_factory()
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            from youtube_transcript_api.proxies import GenericProxyConfig
        except ImportError as exc:
            raise TranscriptUnavailable(
                "Install the project dependencies before extracting transcripts."
            ) from exc
        if not self._proxy_url:
            return YouTubeTranscriptApi()
        return YouTubeTranscriptApi(
            proxy_config=GenericProxyConfig(http_url=self._proxy_url)
        )

    @staticmethod
    def _classify_exception(exc: Exception) -> TranscriptError:
        name = exc.__class__.__name__.lower()
        if any(
            marker in name
            for marker in (
                "blocked",
                "ipblocked",
                "requestblocked",
                "ratelimit",
            )
        ):
            return TranscriptBlocked()
        return TranscriptUnavailable()

    def fetch(self, source_url: str, language: Optional[str] = None) -> TranscriptDocument:
        video_id = extract_video_id(source_url)
        api = self._make_api()
        try:
            transcript_list = api.list(video_id)
            tracks = _caption_tracks(transcript_list)
            selected = choose_caption_track(tracks, language)
            fetched = selected.track.fetch()
            segments = _segments_from_fetched(fetched)
        except (InvalidYouTubeUrl, TranscriptUnavailable, TranscriptBlocked):
            raise
        except Exception as exc:
            raise self._classify_exception(exc) from exc

        if self._metadata_fetcher is not None:
            metadata = self._metadata_fetcher(source_url)
        else:
            metadata = fetch_oembed_metadata(source_url, self._proxy_url)
        return TranscriptDocument(
            video_id=video_id,
            source_url=source_url,
            title=metadata.get("title"),
            author=metadata.get("author"),
            language_code=selected.language_code,
            language_name=selected.language_name,
            is_generated=selected.is_generated,
            segments=segments,
        )
