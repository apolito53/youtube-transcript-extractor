from fastapi.testclient import TestClient

from youtube_transcript_extractor.app import create_app
from youtube_transcript_extractor.config import Settings
from youtube_transcript_extractor.models import CaptionSegment, TranscriptDocument


class FakeTranscriptClient:
    def __init__(self):
        self.fetch_calls = 0

    def fetch(self, url, language=None):
        self.fetch_calls += 1
        return TranscriptDocument(
            video_id="dQw4w9WgXcQ",
            source_url=url,
            title="Test Video",
            author="Tester",
            language_code=language or "en",
            language_name="English",
            is_generated=False,
            segments=[
                CaptionSegment(
                    text="Hello there.",
                    start_seconds=0,
                    duration_seconds=1,
                )
            ],
        )


class FakeFormatter:
    available = True
    cache_key = "fake-formatter:v1"

    def __init__(self):
        self.format_calls = 0

    def format(self, clean_markdown):
        self.format_calls += 1
        return clean_markdown + "\n\n## Formatted\n", []


def make_client(tmp_path, formatter=None, transcript_client=None, cache_path=None):
    settings = Settings(
        openai_api_key="test",
        openai_model="test-model",
        host="127.0.0.1",
        port=8000,
        max_transcript_chars=500000,
        cache_path=str(cache_path or tmp_path / "transcripts.sqlite3"),
    )
    app = create_app(
        settings=settings,
        transcript_client=transcript_client or FakeTranscriptClient(),
        formatter=formatter or FakeFormatter(),
    )
    return TestClient(app)


def test_extract_endpoint_returns_clean_transcript(tmp_path):
    response = make_client(tmp_path).post(
        "/api/extract",
        json={"url": "https://youtu.be/dQw4w9WgXcQ"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["transcript"]["video_id"] == "dQw4w9WgXcQ"
    assert "# Test Video" in payload["clean_markdown"]
    assert "Version: v1" in payload["clean_markdown"]
    assert "Analyzed: " in payload["clean_markdown"]
    assert payload["formatting_available"] is True
    assert payload["analysis"]["version"] == 1
    assert payload["analysis"]["transcript_from_cache"] is False


def test_index_endpoint_serves_the_local_ui(tmp_path):
    response = make_client(tmp_path).get("/")
    assert response.status_code == 200
    assert "Transcript Formatter" in response.text
    assert "Refresh cached transcript" in response.text


def test_health_reports_proxy_configuration_without_exposing_url(tmp_path):
    proxy_url = "http://user:secret@proxy.example.com:8080"
    settings = Settings(
        openai_api_key="test",
        openai_model="test-model",
        host="127.0.0.1",
        port=8000,
        max_transcript_chars=500000,
        cache_path=str(tmp_path / "transcripts.sqlite3"),
        proxy_url=proxy_url,
    )
    app = create_app(settings=settings, formatter=FakeFormatter())

    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json()["proxy_configured"] is True
    assert proxy_url not in response.text
    assert app.state.transcript_client._proxy_url == proxy_url


def test_format_endpoint_returns_formatted_transcript(tmp_path):
    response = make_client(tmp_path).post(
        "/api/format",
        json={"clean_markdown": "# Transcript\n\nHello."},
    )
    assert response.status_code == 200
    assert response.json()["available"] is True
    assert "## Formatted" in response.json()["formatted_markdown"]


def test_format_endpoint_keeps_clean_fallback_when_unconfigured(tmp_path):
    class MissingFormatter:
        available = False

        def format(self, clean_markdown):
            raise AssertionError("should not be called")

    response = make_client(tmp_path, formatter=MissingFormatter()).post(
        "/api/format",
        json={"clean_markdown": "# Transcript\n\nHello."},
    )
    assert response.status_code == 200
    assert response.json()["available"] is False
    assert "OPENAI_API_KEY" in response.json()["message"]


def test_extract_reuses_cached_transcript_and_formatted_output(tmp_path):
    transcript_client = FakeTranscriptClient()
    formatter = FakeFormatter()
    client = make_client(
        tmp_path,
        transcript_client=transcript_client,
        formatter=formatter,
    )

    first = client.post(
        "/api/extract",
        json={"url": "https://youtu.be/dQw4w9WgXcQ"},
    )
    second = client.post(
        "/api/extract",
        json={"url": "https://youtu.be/dQw4w9WgXcQ"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert transcript_client.fetch_calls == 1
    assert formatter.format_calls == 1
    assert second.json()["analysis"] == {
        "version": 1,
        "analyzed_at": first.json()["analysis"]["analyzed_at"],
        "transcript_from_cache": True,
        "formatted_from_cache": True,
    }


def test_refresh_fetches_again_and_saves_the_next_version(tmp_path):
    transcript_client = FakeTranscriptClient()
    formatter = FakeFormatter()
    client = make_client(
        tmp_path,
        transcript_client=transcript_client,
        formatter=formatter,
    )

    client.post(
        "/api/extract",
        json={"url": "https://youtu.be/dQw4w9WgXcQ"},
    )
    refreshed = client.post(
        "/api/extract",
        json={
            "url": "https://youtu.be/dQw4w9WgXcQ",
            "refresh": True,
        },
    )

    assert refreshed.status_code == 200
    payload = refreshed.json()
    assert transcript_client.fetch_calls == 2
    assert formatter.format_calls == 2
    assert payload["analysis"]["version"] == 2
    assert payload["analysis"]["transcript_from_cache"] is False
    assert payload["analysis"]["formatted_from_cache"] is False
    assert "Version: v2" in payload["clean_markdown"]
    assert "Analyzed: {}".format(payload["analysis"]["analyzed_at"][:10]) in payload[
        "clean_markdown"
    ]


def test_cache_survives_a_new_app_instance(tmp_path):
    cache_path = tmp_path / "persistent.sqlite3"
    first_transcript_client = FakeTranscriptClient()
    first_formatter = FakeFormatter()
    first_client = make_client(
        tmp_path,
        transcript_client=first_transcript_client,
        formatter=first_formatter,
        cache_path=cache_path,
    )
    first_client.post(
        "/api/extract",
        json={"url": "https://youtu.be/dQw4w9WgXcQ"},
    )

    second_transcript_client = FakeTranscriptClient()
    second_formatter = FakeFormatter()
    second_client = make_client(
        tmp_path,
        transcript_client=second_transcript_client,
        formatter=second_formatter,
        cache_path=cache_path,
    )
    response = second_client.post(
        "/api/extract",
        json={"url": "https://youtu.be/dQw4w9WgXcQ"},
    )

    assert response.status_code == 200
    assert response.json()["analysis"]["transcript_from_cache"] is True
    assert response.json()["analysis"]["formatted_from_cache"] is True
    assert second_transcript_client.fetch_calls == 0
    assert second_formatter.format_calls == 0


def test_equivalent_youtube_url_reuses_the_full_cached_analysis(tmp_path):
    transcript_client = FakeTranscriptClient()
    formatter = FakeFormatter()
    client = make_client(
        tmp_path,
        transcript_client=transcript_client,
        formatter=formatter,
    )
    first_url = "https://youtu.be/dQw4w9WgXcQ"

    first = client.post("/api/extract", json={"url": first_url})
    second = client.post(
        "/api/extract",
        json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    )

    assert second.status_code == 200
    assert second.json()["analysis"]["transcript_from_cache"] is True
    assert second.json()["analysis"]["formatted_from_cache"] is True
    assert second.json()["transcript"]["source_url"] == first_url
    assert second.json()["formatted_markdown"] == first.json()["formatted_markdown"]
    assert transcript_client.fetch_calls == 1
    assert formatter.format_calls == 1


def test_timestamp_option_reuses_transcript_version_but_caches_its_own_format(tmp_path):
    transcript_client = FakeTranscriptClient()
    formatter = FakeFormatter()
    client = make_client(
        tmp_path,
        transcript_client=transcript_client,
        formatter=formatter,
    )
    url = "https://youtu.be/dQw4w9WgXcQ"

    client.post("/api/extract", json={"url": url})
    timestamped = client.post(
        "/api/extract",
        json={"url": url, "include_timestamps": True},
    )
    timestamped_again = client.post(
        "/api/extract",
        json={"url": url, "include_timestamps": True},
    )

    assert timestamped.status_code == 200
    assert timestamped.json()["analysis"]["version"] == 1
    assert timestamped.json()["analysis"]["transcript_from_cache"] is True
    assert timestamped.json()["analysis"]["formatted_from_cache"] is False
    assert timestamped_again.json()["analysis"]["formatted_from_cache"] is True
    assert transcript_client.fetch_calls == 1
    assert formatter.format_calls == 2
