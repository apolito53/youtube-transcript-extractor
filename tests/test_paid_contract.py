from decimal import Decimal

import pytest

from youtube_transcript_extractor.paid_contract import (
    ALLOWED_TRANSITIONS,
    BAZAAR_ENABLED_DURING_PAID_PILOT,
    CANDIDATE_PRICE_USD,
    MAX_ACCEPTED_ALL_IN_COST_EXCLUSIVE_USD,
    P95_ALL_IN_COST_LIMIT_USD,
    PUBLIC_FREE_AI_ENABLED_DURING_PAID_PILOT,
    PUBLIC_FREE_EXTRACTION_ENABLED_DURING_PAID_PILOT,
    RECOVERY_MANIFEST_REQUIRED_FIELDS,
    REPLAY_SLA_DAYS,
    TERMINAL_STATES,
    VALUE_SMOKE_MIN_PARTICIPANTS,
    VALUE_SMOKE_MIN_PRICE_BEARING_SELECTIONS,
    InvalidPaidTransition,
    PaidOperationState,
    PaidRequestFingerprintInput,
    canonical_request_fingerprint,
    require_transition,
    transition_allowed,
)


def test_happy_path_requires_durability_before_settlement():
    path = [
        PaidOperationState.AUTHORIZED,
        PaidOperationState.EXTRACTING,
        PaidOperationState.FORMAT_INTENT,
        PaidOperationState.RESULT_READY,
        PaidOperationState.RESULT_DURABLE,
        PaidOperationState.SETTLEMENT_INTENT,
        PaidOperationState.SETTLED,
    ]

    for current, target in zip(path, path[1:]):
        require_transition(current, target)

    assert not transition_allowed(
        PaidOperationState.RESULT_READY,
        PaidOperationState.SETTLEMENT_INTENT,
    )


def test_uncertain_formatter_dispatch_is_terminal_and_cannot_redispatch():
    require_transition(
        PaidOperationState.FORMAT_INTENT,
        PaidOperationState.EXECUTION_UNKNOWN,
    )
    assert PaidOperationState.EXECUTION_UNKNOWN in TERMINAL_STATES
    assert ALLOWED_TRANSITIONS[PaidOperationState.EXECUTION_UNKNOWN] == frozenset()

    with pytest.raises(InvalidPaidTransition):
        require_transition(
            PaidOperationState.EXECUTION_UNKNOWN,
            PaidOperationState.FORMAT_INTENT,
        )


def test_ambiguous_settlement_can_only_converge_to_settled():
    require_transition(
        PaidOperationState.SETTLEMENT_INTENT,
        PaidOperationState.SETTLEMENT_UNKNOWN,
    )
    require_transition(
        PaidOperationState.SETTLEMENT_UNKNOWN,
        PaidOperationState.SETTLED,
    )

    assert ALLOWED_TRANSITIONS[PaidOperationState.SETTLEMENT_UNKNOWN] == frozenset(
        {PaidOperationState.SETTLED}
    )


def test_terminal_states_cannot_restart_work_or_payment():
    for state in TERMINAL_STATES:
        assert ALLOWED_TRANSITIONS[state] == frozenset()


def test_every_state_has_an_explicit_transition_contract():
    assert set(ALLOWED_TRANSITIONS) == set(PaidOperationState)


def test_recovery_manifest_can_reconstruct_identity_after_volume_loss():
    assert REPLAY_SLA_DAYS == 30
    assert {
        "result_id",
        "request_fingerprint",
        "payment_identity",
        "settlement_reconciliation_identity",
        "result_content_hash",
        "replay_deadline",
    }.issubset(RECOVERY_MANIFEST_REQUIRED_FIELDS)


def test_fingerprint_normalizes_language_but_versions_change_identity():
    base = PaidRequestFingerprintInput(
        video_id="dQw4w9WgXcQ",
        language=" EN ",
        include_timestamps=False,
        formatter_model="gpt-5.6-luna",
        formatter_prompt_version="v2",
    )
    equivalent = PaidRequestFingerprintInput(
        video_id="dQw4w9WgXcQ",
        language="en",
        include_timestamps=False,
        formatter_model="gpt-5.6-luna",
        formatter_prompt_version="v2",
    )
    prompt_change = PaidRequestFingerprintInput(
        video_id="dQw4w9WgXcQ",
        language="en",
        include_timestamps=False,
        formatter_model="gpt-5.6-luna",
        formatter_prompt_version="v3",
    )
    timestamp_change = PaidRequestFingerprintInput(
        video_id="dQw4w9WgXcQ",
        language="en",
        include_timestamps=True,
        formatter_model="gpt-5.6-luna",
        formatter_prompt_version="v2",
    )

    assert canonical_request_fingerprint(base) == canonical_request_fingerprint(equivalent)
    assert canonical_request_fingerprint(base) != canonical_request_fingerprint(prompt_change)
    assert canonical_request_fingerprint(base) != canonical_request_fingerprint(timestamp_change)


def test_initial_mainnet_pilot_is_paid_only_and_bazaar_off():
    assert PUBLIC_FREE_EXTRACTION_ENABLED_DURING_PAID_PILOT is False
    assert PUBLIC_FREE_AI_ENABLED_DURING_PAID_PILOT is False
    assert BAZAAR_ENABLED_DURING_PAID_PILOT is False


def test_candidate_economics_and_value_gate_are_frozen():
    assert CANDIDATE_PRICE_USD == Decimal("0.05")
    assert P95_ALL_IN_COST_LIMIT_USD == Decimal("0.02")
    assert MAX_ACCEPTED_ALL_IN_COST_EXCLUSIVE_USD == Decimal("0.035")
    assert VALUE_SMOKE_MIN_PARTICIPANTS == 3
    assert VALUE_SMOKE_MIN_PRICE_BEARING_SELECTIONS == 2
