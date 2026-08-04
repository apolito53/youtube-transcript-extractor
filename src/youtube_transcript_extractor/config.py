import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    openai_api_key: Optional[str]
    openai_model: str
    host: str
    port: int
    max_transcript_chars: int


def get_settings() -> Settings:
    load_dotenv()
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-5.6-luna"),
        host=os.getenv("YTX_HOST", "127.0.0.1"),
        port=int(os.getenv("YTX_PORT", "8765")),
        max_transcript_chars=int(os.getenv("YTX_MAX_TRANSCRIPT_CHARS", "500000")),
    )
