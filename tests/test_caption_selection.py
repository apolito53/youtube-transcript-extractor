import pytest

from youtube_transcript_extractor.errors import TranscriptUnavailable
from youtube_transcript_extractor.youtube import (
    CaptionTrack,
    YouTubeTranscriptClient,
    choose_caption_track,
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
