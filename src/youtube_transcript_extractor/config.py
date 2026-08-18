import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    openai_api_key: Optional[str]
    openai_model: str
    host: str
    port: int
    max_transcript_chars: int
    cache_path: str = str(
        Path.home() / ".cache" / "youtube-transcript-extractor" / "transcripts.sqlite3"
    )
    proxy_url: Optional[str] = None
    phase0_x402_enabled: bool = False
    phase0_x402_facilitator_url: str = "https://x402.org/facilitator"
    phase0_x402_network: str = "eip155:84532"
    phase0_x402_price: str = "$0.001"
    phase0_x402_pay_to_address: Optional[str] = None
    phase0_x402_expected_buyer_address: Optional[str] = None
    phase0_x402_state_path: str = str(
        Path.home() / ".cache" / "youtube-transcript-extractor" / "phase0.sqlite3"
    )


def get_settings() -> Settings:
    load_dotenv()
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-5.6-luna"),
        host=os.getenv("YTX_HOST", "127.0.0.1"),
        port=int(os.getenv("YTX_PORT", "8765")),
        max_transcript_chars=int(os.getenv("YTX_MAX_TRANSCRIPT_CHARS", "500000")),
        cache_path=os.getenv("YTX_CACHE_PATH")
        or str(
            Path.home()
            / ".cache"
            / "youtube-transcript-extractor"
            / "transcripts.sqlite3"
        ),
        proxy_url=os.getenv("YTX_PROXY_URL") or None,
        phase0_x402_enabled=_env_bool("YTX_PHASE0_X402_ENABLED", False),
        phase0_x402_facilitator_url=os.getenv(
            "YTX_PHASE0_FACILITATOR_URL", "https://x402.org/facilitator"
        ),
        phase0_x402_network=os.getenv("YTX_PHASE0_NETWORK", "eip155:84532"),
        phase0_x402_price=os.getenv("YTX_PHASE0_PRICE", "$0.001"),
        phase0_x402_pay_to_address=os.getenv("YTX_PHASE0_PAY_TO_ADDRESS") or None,
        phase0_x402_expected_buyer_address=os.getenv(
            "YTX_PHASE0_EXPECTED_BUYER_ADDRESS"
        )
        or None,
        phase0_x402_state_path=os.getenv("YTX_PHASE0_STATE_PATH")
        or str(
            Path.home()
            / ".cache"
            / "youtube-transcript-extractor"
            / "phase0.sqlite3"
        ),
    )
