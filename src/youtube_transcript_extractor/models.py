from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class CaptionSegment(BaseModel):
    text: str
    start_seconds: float = Field(ge=0)
    duration_seconds: float = Field(ge=0)

    @property
    def end_seconds(self) -> float:
        return self.start_seconds + self.duration_seconds


class TranscriptDocument(BaseModel):
    video_id: str
    source_url: str
    title: Optional[str] = None
    author: Optional[str] = None
    language_code: str
    language_name: str
    is_generated: bool
    segments: List[CaptionSegment]

    @property
    def duration_seconds(self) -> float:
        if not self.segments:
            return 0.0
        return max(segment.end_seconds for segment in self.segments)


class ExtractRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    language: Optional[str] = Field(default=None, max_length=16)
    include_timestamps: bool = False
    refresh: bool = False


class AnalysisInfo(BaseModel):
    version: int = Field(ge=1)
    analyzed_at: datetime
    transcript_from_cache: bool
    formatted_from_cache: bool = False


class ExtractResponse(BaseModel):
    transcript: TranscriptDocument
    clean_markdown: str
    formatted_markdown: Optional[str] = None
    formatting_available: bool = False
    formatting_message: Optional[str] = None
    analysis: AnalysisInfo
    warnings: List[str] = Field(default_factory=list)


class FormatRequest(BaseModel):
    clean_markdown: str = Field(min_length=1)
    include_timestamps: bool = False


class FormatResponse(BaseModel):
    available: bool
    formatted_markdown: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    message: Optional[str] = None
