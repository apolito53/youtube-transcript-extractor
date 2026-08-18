from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from youtube_transcript_extractor.config import Settings
from youtube_transcript_extractor.phase0_x402 import (
    Phase0Store,
    attach_phase0_x402_routes,
)


class FakeModel:
    def __init__(self, payload):
        self.payload = payload

    def model_dump(self, by_alias=False):
        return dict(self.payload)


class FakeServer:
    def __init__(self, *, payer="0xBuyer", settlements=None):
        self.payer = payer
        self.verify_calls = 0
        self.settle_calls = 0
        self.initialize_calls = 0
        self.settlements = list(
            settlements
            or [
                FakeModel(
                    {
                        "success": True,
                        "payer": payer,
                        "transaction": "0xtx1",
                        "network": "eip155:84532",
                    }
                )
            ]
        )
        for settlement in self.settlements:
            settlement.success = bool(settlement.payload.get("success"))

    def initialize(self):
        self.initialize_calls += 1

    def create_payment_required_response(self, requirements, resource):
        return FakeModel(
            {
                "x402Version": 2,
                "resource": resource,
                "accepts": [requirements[0].model_dump()],
            }
        )

    async def verify_payment(self, payment_payload, requirements):
        self.verify_calls += 1
        return SimpleNamespace(is_valid=True, payer=self.payer)

    async def settle_payment(self, payment_payload, requirements):
        self.settle_calls += 1
        index = min(self.settle_calls - 1, len(self.settlements) - 1)
        return self.settlements[index]


def make_settings(tmp_path, *, expected_buyer="0xBuyer"):
    return Settings(
        openai_api_key=None,
        openai_model="gpt-5.6-luna",
        host="127.0.0.1",
        port=8765,
        max_transcript_chars=500000,
        cache_path=str(tmp_path / "transcripts.sqlite3"),
        phase0_x402_enabled=True,
        phase0_x402_pay_to_address="0xProvider",
        phase0_x402_expected_buyer_address=expected_buyer,
        phase0_x402_state_path=str(tmp_path / "phase0.sqlite3"),
    )


def make_client(tmp_path, server, *, expected_buyer="0xBuyer"):
    app = FastAPI()
    settings = make_settings(tmp_path, expected_buyer=expected_buyer)
    requirements = FakeModel(
        {
            "scheme": "exact",
            "network": "eip155:84532",
            "amount": "1000",
            "payTo": "0xProvider",
        }
    )
    store = Phase0Store(settings.phase0_x402_state_path)
    attach_phase0_x402_routes(
        app,
        settings,
        resource_server=server,
        requirements=requirements,
        store=store,
        payload_decoder=lambda header: FakeModel({"signature": header}),
    )
    return TestClient(app), store


def paid_post(client, mode="success", signature="sig-1", **extra):
    return client.post(
        "/internal/phase0/x402",
        json={"mode": mode, **extra},
        headers={"payment-signature": signature},
    )


def only_operation(store):
    import sqlite3

    with sqlite3.connect(str(store.path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM phase0_operations").fetchone()
    assert row is not None
    return dict(row)


def test_unpaid_request_is_challenged_without_verify_or_settle(tmp_path):
    server = FakeServer()
    client, _ = make_client(tmp_path, server)

    response = client.post("/internal/phase0/x402", json={"mode": "success"})

    assert response.status_code == 402
    assert response.headers["payment-required"]
    assert server.verify_calls == 0
    assert server.settle_calls == 0


def test_fail_after_verify_never_executes_work_or_settlement(tmp_path):
    server = FakeServer()
    client, store = make_client(tmp_path, server)

    response = paid_post(client, "fail_after_verify")
    operation = only_operation(store)

    assert response.status_code == 503
    assert server.verify_calls == 1
    assert server.settle_calls == 0
    assert operation["verify_count"] == 1
    assert operation["work_count"] == 0
    assert operation["settle_count"] == 0


def test_fail_after_work_does_not_settle_and_work_is_single_flight(tmp_path):
    server = FakeServer()
    client, store = make_client(tmp_path, server)

    first = paid_post(client, "fail_after_work")
    second = paid_post(client, "fail_after_work")
    operation = only_operation(store)

    assert first.status_code == 503
    assert second.status_code == 503
    assert operation["work_count"] == 1
    assert operation["settle_count"] == 0
    assert server.settle_calls == 0


def test_success_verifies_works_and_settles_once(tmp_path):
    server = FakeServer()
    client, store = make_client(tmp_path, server)

    response = paid_post(client, "success")
    operation = only_operation(store)

    assert response.status_code == 200
    assert response.headers["payment-response"]
    assert server.verify_calls == 1
    assert server.settle_calls == 1
    assert operation["work_count"] == 1
    assert operation["settle_count"] == 1
    assert operation["status"] == "SETTLED"


def test_response_loss_replays_cached_result_without_new_external_calls(tmp_path):
    server = FakeServer()
    client, store = make_client(tmp_path, server)

    lost = paid_post(client, "response_loss")
    replay = paid_post(client, "response_loss")
    operation = only_operation(store)

    assert lost.status_code == 503
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert server.verify_calls == 1
    assert server.settle_calls == 1
    assert operation["work_count"] == 1
    assert operation["settle_count"] == 1


def test_settlement_ack_loss_retries_same_settlement_identity_without_rework(tmp_path):
    settlements = [
        FakeModel(
            {
                "success": True,
                "payer": "0xBuyer",
                "transaction": "0xtx1",
                "network": "eip155:84532",
            }
        ),
        FakeModel(
            {
                "success": True,
                "payer": "0xBuyer",
                "transaction": "0xtx1",
                "network": "eip155:84532",
            }
        ),
    ]
    server = FakeServer(settlements=settlements)
    client, store = make_client(tmp_path, server)

    lost = paid_post(client, "settlement_ack_loss")
    reconciled = paid_post(client, "settlement_ack_loss")
    operation = only_operation(store)

    assert lost.status_code == 503
    assert reconciled.status_code == 200
    assert reconciled.json()["reconciled"] is True
    assert server.verify_calls == 1
    assert server.settle_calls == 2
    assert operation["work_count"] == 1
    assert operation["settle_count"] == 2
    assert operation["status"] == "SETTLED"


def test_duplicate_settlement_probe_records_two_attempts_without_reworking(tmp_path):
    server = FakeServer()
    client, store = make_client(tmp_path, server)

    response = paid_post(client, "duplicate_settlement_probe")
    operation = only_operation(store)

    assert response.status_code == 200
    assert "duplicate_settlement_probe" in response.json()
    assert server.verify_calls == 1
    assert server.settle_calls == 2
    assert operation["work_count"] == 1
    assert operation["settle_count"] == 2


def test_expected_buyer_allowlist_blocks_work_and_settlement(tmp_path):
    server = FakeServer(payer="0xSomeoneElse")
    client, store = make_client(tmp_path, server, expected_buyer="0xBuyer")

    response = paid_post(client, "success")
    operation = only_operation(store)

    assert response.status_code == 403
    assert operation["verify_count"] == 0
    assert operation["work_count"] == 0
    assert operation["settle_count"] == 0
    assert server.settle_calls == 0


def test_payment_identity_cannot_be_reused_for_a_different_fault_mode(tmp_path):
    server = FakeServer()
    client, _ = make_client(tmp_path, server)

    first = paid_post(client, "fail_after_verify", signature="same-signature")
    conflict = paid_post(client, "success", signature="same-signature")

    assert first.status_code == 503
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "payment_identity_conflict"
