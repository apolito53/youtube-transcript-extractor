import pytest

from youtube_transcript_extractor.errors import FormatterUnavailable
from youtube_transcript_extractor.formatter import FORMATTER_INSTRUCTIONS, OpenAIFormatter


class FakeResponses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        fence = chr(96) * 3
        output = fence + "markdown\n# Formatted\n\nHello world.\n" + fence
        return type("Response", (), {"output_text": output})()


class FakeClient:
    def __init__(self):
        self.responses = FakeResponses()


def test_formatter_uses_responses_api_and_strips_code_fences():
    client = FakeClient()
    formatter = OpenAIFormatter(api_key="test-key", model="test-model", client=client)

    output, warnings = formatter.format("# Transcript\n\nHello world.")

    assert output == "# Formatted\n\nHello world."
    assert warnings == []
    call = client.responses.calls[0]
    assert call["model"] == "test-model"
    assert call["store"] is False
    assert call["instructions"] == FORMATTER_INSTRUCTIONS
    assert "<transcript>" in call["input"]


def test_formatter_without_key_is_optional():
    # Pass an explicit empty key so a developer's local .env cannot turn this
    # unit test into a real API request.
    formatter = OpenAIFormatter(api_key="", client=None)
    with pytest.raises(FormatterUnavailable):
        formatter.format("Hello")
