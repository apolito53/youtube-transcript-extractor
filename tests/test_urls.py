import pytest

from youtube_transcript_extractor.errors import InvalidYouTubeUrl
from youtube_transcript_extractor.youtube import extract_video_id


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?t=42",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/live/dQw4w9WgXcQ?feature=share",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ&list=abc",
    ],
)
def test_extract_video_id_accepts_common_url_shapes(url):
    assert extract_video_id(url) == "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch",
        "https://www.youtube.com/watch?v=too-short",
        "not a url",
        "ftp://www.youtube.com/watch?v=dQw4w9WgXcQ",
    ],
)
def test_extract_video_id_rejects_invalid_urls(url):
    with pytest.raises(InvalidYouTubeUrl):
        extract_video_id(url)
