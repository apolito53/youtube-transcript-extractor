from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from youtube_transcript_extractor.phase0_chain_recovery import (
    _authorization_from_payload,
    install_phase0_chain_recovery,
)
from youtube_transcript_extractor.phase0_x402 import Phase0Request


def test_authorization_identity_is_bound_to_requirement():
    payload = {
        "x402Version": 2,
        "accepted": {
            "scheme": "exact",
            "network": "eip155:84532",
            "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
            "amount": "1000",
            "payTo": "0x9632c7eD958612C79DdBEF7EA9798D0950b207f9",
            "maxTimeoutSeconds": 300,
        },
        "payload": {
            "authorization": {
                "from": "0x60d46F6b4c420b6405EEeB09dB07D92E0BD0DcEa",
                "to": "0x9632c7eD958612C79DdBEF7EA9798D0950b207f9",
                "value": "1000",
                "validAfter": "0",
                "validBefore": "9999999999",
                "nonce": "0x" + ("11" * 32),
            },
            "signature": "redacted-for-test",
        },
    }

    identity = _authorization_from_payload(payload)

    assert identity["network"] == "eip155:84532"
    assert identity["amount"] == "1000"
    assert identity["from_address"].lower().startswith("0x60d46")
    assert identity["authorization_nonce"] == "0x" + ("11" * 32)


def _app_with_wrapper(recovery):
    app = FastAPI()
    calls = {"original": 0}

    @app.post("/internal/phase0/x402")
    async def original(request: Request, body: Phase0Request):
        calls["original"] += 1
        return JSONResponse(content={"ok": True, "marker": body.marker})

    settings = SimpleNamespace(
        phase0_x402_enabled=True,
        phase0_x402_state_path="unused.sqlite3",
    )
    install_phase0_chain_recovery(
        app,
        settings,
        recovery=recovery,
        payload_decoder=lambda _header: {"payload": "fake"},
        payment_key_fn=lambda _payload: "payment-key",
    )
    return TestClient(app), calls


class FakeRecovery:
    def __init__(self, status="SETTLEMENT_UNKNOWN", settlement=None):
        self.status = status
        self.settlement = settlement
        self.anchors = 0
        self.reconciles = 0

    def ensure_anchor(self, payment_key, payment_payload):
        assert payment_key == "payment-key"
        self.anchors += 1

    def get_status(self, payment_key):
        assert payment_key == "payment-key"
        return self.status

    def reconcile_and_mark(self, payment_key):
        assert payment_key == "payment-key"
        self.reconciles += 1
        return self.settlement


def test_unknown_settlement_fails_closed_without_chain_evidence():
    recovery = FakeRecovery(settlement=None)
    client, calls = _app_with_wrapper(recovery)

    response = client.post(
        "/internal/phase0/x402",
        headers={"payment-signature": "opaque"},
        json={"mode": "success", "marker": "no-chain-proof"},
    )

    assert response.status_code == 503
    assert response.json()["error"] == "settlement_still_unknown_chain_unconfirmed"
    assert calls["original"] == 0
    assert recovery.anchors == 1
    assert recovery.reconciles == 1


def test_confirmed_chain_reconciliation_reenters_original_replay_path():
    recovery = FakeRecovery(
        settlement={
            "success": True,
            "transaction": "0xabc",
            "network": "eip155:84532",
            "payer": "0xdef",
        }
    )
    client, calls = _app_with_wrapper(recovery)

    response = client.post(
        "/internal/phase0/x402",
        headers={"payment-signature": "opaque"},
        json={"mode": "success", "marker": "chain-proof"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "marker": "chain-proof"}
    assert calls["original"] == 1
    assert recovery.anchors == 1
    assert recovery.reconciles == 1


def test_non_unknown_operation_uses_original_endpoint_without_reconcile():
    recovery = FakeRecovery(status="VERIFIED", settlement=None)
    client, calls = _app_with_wrapper(recovery)

    response = client.post(
        "/internal/phase0/x402",
        headers={"payment-signature": "opaque"},
        json={"mode": "success", "marker": "normal"},
    )

    assert response.status_code == 200
    assert calls["original"] == 1
    assert recovery.anchors == 1
    assert recovery.reconciles == 0
