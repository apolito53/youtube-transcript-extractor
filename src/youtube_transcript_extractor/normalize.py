import html
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import CaptionSegment, TranscriptDocument

WORD_PATTERN = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?")
WHITESPACE_PATTERN = re.compile(r"\s+")
MARKDOWN_METADATA_PREFIXES = (
    "# ",
    "Source:",
    "Language:",
    "Captions:",
    "Version:",
    "Analyzed:",
)


@dataclass(frozen=True)
class TranscriptParagraph:
    text: str
    start_seconds: float


def clean_caption_text(text: str) -> str:
    text = html.unescape(text)
    return WHITESPACE_PATTERN.sub(" ", text).strip()


def _word_keys(text: str) -> List[str]:
    return [match.group(0).lower().replace("’", "'") for match in WORD_PATTERN.finditer(text)]


def _drop_leading_words(text: str, count: int) -> str:
    if count <= 0:
        return text
    remaining = text
    for _ in range(count):
        remaining = re.sub(r"^\s*\S+", "", remaining, count=1)
    return remaining.strip()


def deduplicate_segments(segments: Sequence[CaptionSegment]) -> List[CaptionSegment]:
    """Remove repeated rolling-caption prefixes while preserving timings."""
    result: List[CaptionSegment] = []
    previous_words: List[str] = []

    for segment in segments:
        text = clean_caption_text(segment.text)
        if not text:
            continue
        current_words = _word_keys(text)
        if not current_words:
            continue

        overlap = 0
        maximum = min(12, len(previous_words), len(current_words))
        for size in range(maximum, 2, -1):
            if previous_words[-size:] == current_words[:size]:
                overlap = size
                break

        if overlap == len(current_words):
            # The entire caption is a repeated window.
            continue
        if overlap:
            text = _drop_leading_words(text, overlap)

        text = clean_caption_text(text)
        if not text:
            continue
        result.append(
            CaptionSegment(
                text=text,
                start_seconds=segment.start_seconds,
                duration_seconds=segment.duration_seconds,
            )
        )
        previous_words = _word_keys(text)

    return result


def group_paragraphs(
    segments: Sequence[CaptionSegment],
    max_chars: int = 850,
    pause_seconds: float = 1.8,
) -> List[TranscriptParagraph]:
    paragraphs: List[TranscriptParagraph] = []
    current: List[str] = []
    current_start = 0.0
    previous_end = None

    def flush():
        nonlocal current
        if current:
            paragraphs.append(
                TranscriptParagraph(
                    text=clean_caption_text(" ".join(current)),
                    start_seconds=current_start,
                )
            )
            current = []

    for segment in segments:
        text = clean_caption_text(segment.text)
        if not text:
            continue
        gap = (
            segment.start_seconds - previous_end
            if previous_end is not None
            else 0.0
        )
        if current and gap >= pause_seconds:
            flush()
        if not current:
            current_start = segment.start_seconds
        current.append(text)
        previous_end = segment.end_seconds

        joined = clean_caption_text(" ".join(current))
        if len(joined) >= max_chars and re.search(r"[.!?][\"'”’)]?$", text):
            flush()

    flush()
    return paragraphs


def format_timestamp(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return "{}:{:02d}:{:02d}".format(hours, minutes, seconds)
    return "{}:{:02d}".format(minutes, seconds)


def timestamp_url(source_url: str, seconds: float) -> str:
    parsed = urlsplit(source_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["t"] = str(max(0, int(round(seconds))))
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def render_body(
    paragraphs: Iterable[TranscriptParagraph],
    source_url: str = "",
    include_timestamps: bool = False,
) -> str:
    rendered = []
    for paragraph in paragraphs:
        text = paragraph.text
        if include_timestamps and source_url:
            label = format_timestamp(paragraph.start_seconds)
            text = "[{}]({}) {}".format(
                label,
                timestamp_url(source_url, paragraph.start_seconds),
                text,
            )
        rendered.append(text)
    return "\n\n".join(rendered)


def render_document(
    document: TranscriptDocument,
    include_timestamps: bool = False,
    version: Optional[int] = None,
    analyzed_at: Optional[str] = None,
) -> str:
    title = document.title or "YouTube Transcript"
    paragraphs = group_paragraphs(deduplicate_segments(document.segments))
    body = render_body(paragraphs, document.source_url, include_timestamps)
    lines = [
        "# {}".format(title.replace("\n", " ").strip()),
        "",
        "Source: {}".format(document.source_url),
        "Language: {} ({})".format(document.language_name, document.language_code),
        "Captions: {}".format(
            "autogenerated" if document.is_generated else "manual"
        ),
    ]
    if version is not None:
        lines.append("Version: v{}".format(version))
    if analyzed_at:
        lines.append("Analyzed: {}".format(analyzed_at))
    lines.extend(("", body))
    return "\n".join(lines).strip() + "\n"


def strip_markdown_metadata(markdown: str) -> str:
    body_lines = []
    for line in markdown.splitlines():
        if line.startswith(MARKDOWN_METADATA_PREFIXES):
            continue
        body_lines.append(line)
    return "\n".join(body_lines)


def fidelity_warnings(source: str, formatted: str) -> List[str]:
    source_body = strip_markdown_metadata(source)
    formatted_body = strip_markdown_metadata(formatted)
    source_tokens = _word_keys(source_body)
    formatted_tokens = _word_keys(formatted_body)
    if not source_tokens:
        return []

    source_counts = Counter(source_tokens)
    formatted_counts = Counter(formatted_tokens)
    retained = sum(
        min(count, formatted_counts.get(token, 0))
        for token, count in source_counts.items()
    )
    retention_ratio = retained / float(len(source_tokens))
    length_ratio = len(formatted_tokens) / float(len(source_tokens))
    warnings = []
    if retention_ratio < 0.9:
        warnings.append(
            "The formatted output appears to omit a noticeable amount of transcript text."
        )
    if length_ratio < 0.7 or length_ratio > 1.6:
        warnings.append(
            "The formatted output is much shorter or longer than the clean transcript."
        )

    source_timestamps = set(re.findall(r"\]\(([^)]+[?&]t=\d+[^)]*)\)", source))
    if source_timestamps:
        formatted_timestamps = set(
            re.findall(r"\]\(([^)]+[?&]t=\d+[^)]*)\)", formatted)
        )
        if not source_timestamps.issubset(formatted_timestamps):
            warnings.append("One or more timestamp links were not preserved.")
    return warnings
