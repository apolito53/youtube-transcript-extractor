import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .models import TranscriptDocument


class TranscriptCacheError(Exception):
    """Raised when the local transcript cache cannot be read or written."""


@dataclass(frozen=True)
class CachedTranscript:
    cache_id: int
    document: TranscriptDocument
    version: int
    analyzed_at: datetime


def _document_to_json(document: TranscriptDocument) -> str:
    if hasattr(document, "model_dump_json"):
        return document.model_dump_json()
    return document.json()


def _document_from_json(payload: str) -> TranscriptDocument:
    if hasattr(TranscriptDocument, "model_validate_json"):
        return TranscriptDocument.model_validate_json(payload)
    return TranscriptDocument.parse_raw(payload)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TranscriptCache:
    """Persistent versioned transcript and formatter-result cache."""

    def __init__(
        self,
        path: str,
        now: Optional[Callable[[], datetime]] = None,
    ):
        self.path = Path(path).expanduser()
        self._now = now or _utc_now

    def _connect(self) -> sqlite3.Connection:
        connection = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(str(self.path), timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS transcript_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL,
                    language_key TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    analyzed_at TEXT NOT NULL,
                    transcript_json TEXT NOT NULL,
                    UNIQUE(video_id, language_key, version)
                );

                CREATE INDEX IF NOT EXISTS transcript_versions_latest
                    ON transcript_versions(video_id, language_key, version DESC);

                CREATE TABLE IF NOT EXISTS formatted_outputs (
                    transcript_version_id INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    formatter_key TEXT NOT NULL,
                    formatted_markdown TEXT NOT NULL,
                    warnings_json TEXT NOT NULL,
                    PRIMARY KEY (
                        transcript_version_id,
                        content_hash,
                        formatter_key
                    ),
                    FOREIGN KEY (transcript_version_id)
                        REFERENCES transcript_versions(id)
                        ON DELETE CASCADE
                );
                """
            )
            return connection
        except (OSError, sqlite3.Error) as exc:
            if connection is not None:
                connection.close()
            raise TranscriptCacheError("The transcript cache is unavailable.") from exc

    @staticmethod
    def language_key(language: Optional[str]) -> str:
        return (language or "").strip().lower()

    @staticmethod
    def _row_to_transcript(row: sqlite3.Row) -> CachedTranscript:
        try:
            analyzed_at = datetime.fromisoformat(row["analyzed_at"])
            document = _document_from_json(row["transcript_json"])
        except (TypeError, ValueError) as exc:
            raise TranscriptCacheError("A cached transcript is invalid.") from exc
        return CachedTranscript(
            cache_id=row["id"],
            document=document,
            version=row["version"],
            analyzed_at=analyzed_at,
        )

    def get_latest(
        self,
        video_id: str,
        language: Optional[str],
    ) -> Optional[CachedTranscript]:
        try:
            with closing(self._connect()) as connection, connection:
                row = connection.execute(
                    """
                    SELECT id, version, analyzed_at, transcript_json
                    FROM transcript_versions
                    WHERE video_id = ? AND language_key = ?
                    ORDER BY version DESC
                    LIMIT 1
                    """,
                    (video_id, self.language_key(language)),
                ).fetchone()
        except sqlite3.Error as exc:
            raise TranscriptCacheError("The transcript cache could not be read.") from exc
        if row is None:
            return None
        return self._row_to_transcript(row)

    def store_new_version(
        self,
        document: TranscriptDocument,
        language: Optional[str],
    ) -> CachedTranscript:
        analyzed_at = self._now()
        if analyzed_at.tzinfo is None:
            analyzed_at = analyzed_at.replace(tzinfo=timezone.utc)
        language_key = self.language_key(language)
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT COALESCE(MAX(version), 0) AS latest_version
                    FROM transcript_versions
                    WHERE video_id = ? AND language_key = ?
                    """,
                    (document.video_id, language_key),
                ).fetchone()
                version = int(row["latest_version"]) + 1
                cursor = connection.execute(
                    """
                    INSERT INTO transcript_versions (
                        video_id,
                        language_key,
                        version,
                        analyzed_at,
                        transcript_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        document.video_id,
                        language_key,
                        version,
                        analyzed_at.isoformat(),
                        _document_to_json(document),
                    ),
                )
                cache_id = int(cursor.lastrowid)
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            raise TranscriptCacheError("The transcript cache could not be updated.") from exc
        return CachedTranscript(
            cache_id=cache_id,
            document=document,
            version=version,
            analyzed_at=analyzed_at,
        )

    @staticmethod
    def _content_hash(clean_markdown: str) -> str:
        return hashlib.sha256(clean_markdown.encode("utf-8")).hexdigest()

    def get_formatted(
        self,
        transcript_version_id: int,
        clean_markdown: str,
        formatter_key: str,
    ) -> Optional[Tuple[str, List[str]]]:
        try:
            with closing(self._connect()) as connection, connection:
                row = connection.execute(
                    """
                    SELECT formatted_markdown, warnings_json
                    FROM formatted_outputs
                    WHERE transcript_version_id = ?
                      AND content_hash = ?
                      AND formatter_key = ?
                    """,
                    (
                        transcript_version_id,
                        self._content_hash(clean_markdown),
                        formatter_key,
                    ),
                ).fetchone()
            if row is None:
                return None
            warnings = json.loads(row["warnings_json"])
            if not isinstance(warnings, list) or not all(
                isinstance(warning, str) for warning in warnings
            ):
                raise ValueError("Invalid cached warnings")
            return row["formatted_markdown"], warnings
        except (OSError, sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise TranscriptCacheError(
                "The cached formatted transcript could not be read."
            ) from exc

    def store_formatted(
        self,
        transcript_version_id: int,
        clean_markdown: str,
        formatter_key: str,
        formatted_markdown: str,
        warnings: List[str],
    ) -> None:
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO formatted_outputs (
                        transcript_version_id,
                        content_hash,
                        formatter_key,
                        formatted_markdown,
                        warnings_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        transcript_version_id,
                        self._content_hash(clean_markdown),
                        formatter_key,
                        formatted_markdown,
                        json.dumps(warnings),
                    ),
                )
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            raise TranscriptCacheError(
                "The formatted transcript could not be cached."
            ) from exc
