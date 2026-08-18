import json

import pytest
import youtube_transcript_api

from youtube_transcript_extractor.errors import TranscriptUnavailable
from youtube_transcript_extractor.youtube import (
    CaptionTrack,
    YouTubeTranscriptClient,
    choose_caption_track,
    fetch_oembed_metadata,
)


def track(code, generated=False):
    return CaptionTrack(
        language_code=code,
        language_name=code,
        is_generated=generated,
        track=object(),
    )


def test_auto_selection_prefers_manual_english():
    selected = choose_caption_track(
        [
            track("es", generated=False),
            track("en", generated=True),
            track("en", generated=False),
        ]
    )
    assert selected.language_code == "en"
    assert selected.is_generated is False


def test_auto_selection_prefers_english_over_manual_other_language():
    selected = choose_caption_track(
        [track("es", generated=False), track("en", generated=True)]
    )
    assert selected.language_code == "en"


def test_requested_language_wins_over_english():
    selected = choose_caption_track(
        [track("en", generated=False), track("de", generated=True)],
        language="de",
    )
    assert selected.language_code == "de"


def test_missing_requested_language_is_actionable():
    with pytest.raises(TranscriptUnavailable, match="language 'ja'"):
        choose_caption_track([track("en")], language="ja")


class FakeTranscript:
    language_code = "en"
    language = "English"
    is_generated = True

    def fetch(self):
        return [
            {"text": "Hello", "start": 0, "duration": 1},
            {"text": "world.", "start": 1, "duration": 1},
        ]


class FakeApi:
    def list(self, video_id):
        assert video_id == "dQw4w9WgXcQ"
        return [FakeTranscript()]


def test_client_adapts_library_objects_and_adds_best_effort_metadata():
    client = YouTubeTranscriptClient(
        api_factory=lambda: FakeApi(),
        metadata_fetcher=lambda url: {"title": "A Video", "author": "A Creator"},
    )

    result = client.fetch("https://youtu.be/dQw4w9WgXcQ")

    assert result.title == "A Video"
    assert result.author == "A Creator"
    assert result.language_code == "en"
    assert [segment.text for segment in result.segments] == ["Hello", "world."]


def test_client_uses_direct_youtube_api_without_proxy(monkeypatch):
    calls = []

    class CapturingApi:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(youtube_transcript_api, "YouTubeTranscriptApi", CapturingApi)

    YouTubeTranscriptClient()._make_api()

    assert calls == [{}]


def test_client_routes_youtube_api_through_configured_proxy(monkeypatch):
    calls = []

    class CapturingApi:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(youtube_transcript_api, "YouTubeTranscriptApi", CapturingApi)

    YouTubeTranscriptClient(
        proxy_url="http://user:password@proxy.example.com:8080"
    )._make_api()

    proxy_config = calls[0]["proxy_config"]
    assert proxy_config.to_requests_dict() == {
        "http": "http://user:password@proxy.example.com:8080",
        "https": "http://user:password@proxy.example.com:8080",
    }


def test_oembed_metadata_uses_configured_proxy(monkeypatch):
    captured = {}

    class FakeResponse:
        status = 200

        def read(self):
            return json.dumps(
                {"title": "Proxy Video", "author_name": "Proxy Creator"}
            ).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    class FakeOpener:
        def open(self, request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse()

    def fake_build_opener(handler):
        captured["proxies"] = handler.proxies
        return FakeOpener()

    monkeypatch.setattr(
        "youtube_transcript_extractor.youtube.build_opener",
        fake_build_opener,
    )

    metadata = fetch_oembed_metadata(
        "https://youtu.be/dQw4w9WgXcQ",
        proxy_url="http://proxy.example.com:8080",
    )

    assert metadata == {"title": "Proxy Video", "author": "Proxy Creator"}
    assert captured["proxies"] == {
        "http": "http://proxy.example.com:8080",
        "https": "http://proxy.example.com:8080",
    }
    assert captured["timeout"] == 5


def test_oembed_metadata_keeps_direct_connection_without_proxy(monkeypatch):
    captured = {}

    class FakeResponse:
        status = 200

        def read(self):
            return json.dumps(
                {"title": "Direct Video", "author_name": "Direct Creator"}
            ).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse()

    def unexpected_build_opener(*args):
        raise AssertionError("direct requests should not build a proxy opener")

    monkeypatch.setattr(
        "youtube_transcript_extractor.youtube.urlopen",
        fake_urlopen,
    )
    monkeypatch.setattr(
        "youtube_transcript_extractor.youtube.build_opener",
        unexpected_build_opener,
    )

    metadata = fetch_oembed_metadata("https://youtu.be/dQw4w9WgXcQ")

    assert metadata == {"title": "Direct Video", "author": "Direct Creator"}
    assert captured["timeout"] == 5
