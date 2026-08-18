"""Frozen Gate A contracts for the future paid x402 transcript operation.

This module intentionally contains no x402 SDK, wallet, facilitator, provider, or
storage integration.  It makes the pre-implementation lifecycle and launch
constraints executable so later phases have to change the contract explicitly
rather than reinterpret it ad hoc.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
import hashlib
import json
from typing import FrozenSet, Mapping


ROUTE_VERSION = "v1"
PAYMENT_REQUIREMENT_VERSION = "exact-v1"
REPLAY_SLA_DAYS = 30
MIN_SETTLEMENT_RESERVE_SECONDS = 30

CANDIDATE_PRICE_USD = Decimal("0.05")
P95_ALL_IN_COST_LIMIT_USD = Decimal("0.02")
MAX_ACCEPTED_ALL_IN_COST_EXCLUSIVE_USD = Decimal("0.035")

VALUE_SMOKE_MIN_PARTICIPANTS = 3
VALUE_SMOKE_MIN_PRICE_BEARING_SELECTIONS = 2

# The first paid Mainnet pilot is paid-only.  These constants describe the
# launch contract, not the current local/human application behavior.
PUBLIC_FREE_EXTRACTION_ENABLED_DURING_PAID_PILOT = False
PUBLIC_FREE_AI_ENABLED_DURING_PAID_PILOT = False
BAZAAR_ENABLED_DURING_PAID_PILOT = False


class PaidOperationState(str, Enum):
    AUTHORIZED = "AUTHORIZED"
    EXTRACTING = "EXTRACTING"
    FORMAT_INTENT = "FORMAT_INTENT"
    EXECUTION_UNKNOWN = "EXECUTION_UNKNOWN"
    RESULT_READY = "RESULT_READY"
    RESULT_DURABLE = "RESULT_DURABLE"
    SETTLEMENT_INTENT = "SETTLEMENT_INTENT"
    SETTLEMENT_UNKNOWN = "SETTLEMENT_UNKNOWN"
    SETTLED = "SETTLED"
    FAILED_UNPAID = "FAILED_UNPAID"


# RESULT_DURABLE is a mandatory barrier before SETTLEMENT_INTENT.  An uncertain
# formatter dispatch is never automatically redispatched.  An uncertain
# settlement can only converge through reconciliation to SETTLED; it cannot
# create a new payment attempt under a different identity.
ALLOWED_TRANSITIONS: Mapping[PaidOperationState, FrozenSet[PaidOperationState]] = {
    PaidOperationState.AUTHORIZED: frozenset(
        {PaidOperationState.EXTRACTING, PaidOperationState.FAILED_UNPAID}
    ),
    PaidOperationState.EXTRACTING: frozenset(
        {PaidOperationState.FORMAT_INTENT, PaidOperationState.FAILED_UNPAID}
    ),
    PaidOperationState.FORMAT_INTENT: frozenset(
        {
            PaidOperationState.RESULT_READY,
            PaidOperationState.EXECUTION_UNKNOWN,
            PaidOperationState.FAILED_UNPAID,
        }
    ),
    PaidOperationState.EXECUTION_UNKNOWN: frozenset(),
    PaidOperationState.RESULT_READY: frozenset({PaidOperationState.RESULT_DURABLE}),
    PaidOperationState.RESULT_DURABLE: frozenset(
        {PaidOperationState.SETTLEMENT_INTENT}
    ),
    PaidOperationState.SETTLEMENT_INTENT: frozenset(
        {
            PaidOperationState.SETTLED,
            PaidOperationState.SETTLEMENT_UNKNOWN,
            PaidOperationState.FAILED_UNPAID,
        }
    ),
    PaidOperationState.SETTLEMENT_UNKNOWN: frozenset({PaidOperationState.SETTLED}),
    PaidOperationState.SETTLED: frozenset(),
    PaidOperationState.FAILED_UNPAID: frozenset(),
}


TERMINAL_STATES = frozenset(
    {
        PaidOperationState.EXECUTION_UNKNOWN,
        PaidOperationState.SETTLED,
        PaidOperationState.FAILED_UNPAID,
    }
)


# This manifest must exist in secondary durable storage before settlement is
# attempted.  It is intentionally sufficient to recover identity and reconcile
# a transfer after complete loss of the local Fly volume.
RECOVERY_MANIFEST_REQUIRED_FIELDS = frozenset(
    {
        "result_id",
        "request_fingerprint",
        "payer_identity",
        "payment_identity",
        "payment_requirement_version",
        "settlement_reconciliation_identity",
        "source_video_id",
        "source_transcript_version",
        "source_content_hash",
        "result_content_hash",
        "formatter_model",
        "formatter_prompt_version",
        "replay_deadline",
    }
)


class InvalidPaidTransition(ValueError):
    """Raised when code attempts a transition outside the frozen Gate A graph."""


@dataclass(frozen=True)
class PaidRequestFingerprintInput:
    video_id: str
    language: str | None
    include_timestamps: bool
    formatter_model: str
    formatter_prompt_version: str
    route_version: str = ROUTE_VERSION
    payment_requirement_version: str = PAYMENT_REQUIREMENT_VERSION

    def canonical_payload(self) -> dict:
        return {
            "formatter_model": self.formatter_model,
            "formatter_prompt_version": self.formatter_prompt_version,
            "include_timestamps": bool(self.include_timestamps),
            "language": (self.language or "").strip().lower(),
            "payment_requirement_version": self.payment_requirement_version,
            "route_version": self.route_version,
            "video_id": self.video_id.strip(),
        }


def canonical_request_fingerprint(identity: PaidRequestFingerprintInput) -> str:
    payload = json.dumps(
        identity.canonical_payload(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def transition_allowed(
    current: PaidOperationState,
    target: PaidOperationState,
) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def require_transition(
    current: PaidOperationState,
    target: PaidOperationState,
) -> None:
    if not transition_allowed(current, target):
        raise InvalidPaidTransition(
            "Illegal paid operation transition: {} -> {}".format(
                current.value,
                target.value,
            )
        )
