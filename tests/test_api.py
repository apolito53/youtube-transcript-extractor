from fastapi.testclient import TestClient

from youtube_transcript_extractor.app import create_app
from youtube_transcript_extractor.config import Settings
from youtube_transcript_extractor.models import CaptionSegment, TranscriptDocument


class FakeTranscriptClient:
    def fetch(self, url, language=None):
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

    def format(self, clean_markdown):
        return clean_markdown + "\n\n## Formatted\n", []


def make_client(formatter=None):
    settings = Settings(
        openai_api_key="test",
        openai_model="test-model",
        host="127.0.0.1",
        port=8000,
        max_transcript_chars=500000,
    )
    app = create_app(
        settings=settings,
        transcript_client=FakeTranscriptClient(),
        formatter=formatter or FakeFormatter(),
    )
    return TestClient(app)


def test_extract_endpoint_returns_clean_transcript():
    response = make_client().post(
        "/api/extract",
        json={"url": "https://youtu.be/dQw4w9WgXcQ"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["transcript"]["video_id"] == "dQw4w9WgXcQ"
    assert "# Test Video" in payload["clean_markdown"]


def test_index_endpoint_serves_the_local_ui():
    response = make_client().get("/")
    assert response.status_code == 200
    assert "Transcript Formatter" in response.text


def test_format_endpoint_returns_formatted_transcript():
    response = make_client().post(
        "/api/format",
        json={"clean_markdown": "# Transcript\n\nHello."},
    )
    assert response.status_code == 200
    assert response.json()["available"] is True
    assert "## Formatted" in response.json()["formatted_markdown"]


def test_format_endpoint_keeps_clean_fallback_when_unconfigured():
    class MissingFormatter:
        available = False

        def format(self, clean_markdown):
            raise AssertionError("should not be called")

    response = make_client(formatter=MissingFormatter()).post(
        "/api/format",
        json={"clean_markdown": "# Transcript\n\nHello."},
    )
    assert response.status_code == 200
    assert response.json()["available"] is False
    assert "OPENAI_API_KEY" in response.json()["message"]
