"""Synthetic x402 lifecycle harness for Gate B / Phase 0.

This module deliberately does not call YouTube or OpenAI. It exists to prove
x402 verify/work/settle/replay behavior on Base Sepolia before payment is coupled
to the real transcript pipeline.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


Phase0Mode = Literal[
    "success",
    "fail_after_verify",
    "fail_after_work",
    "slow_success",
    "response_loss",
    "settlement_ack_loss",
    "duplicate_settlement_probe",
]


class Phase0Request(BaseModel):
    mode: Phase0Mode = "success"
    delay_ms: int = Field(default=0, ge=0, le=300_000)
    marker: str = Field(default="phase0", min_length=1, max_length=128)


class Phase0Store:
    """Small persistent probe ledger; never a production payment source of truth."""

    def __init__(
        self,
        path: str,
    ):
        self.path = Path(path).expanduser()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS phase0_operations (
                payment_key TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                verify_count INTEGER NOT NULL DEFAULT 0,
                work_count INTEGER NOT NULL DEFAULT 0,
                settle_count INTEGER NOT NULL DEFAULT 0,
                result_json TEXT,
                settlement_json TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        return connection

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def get(self, payment_key: str) -> Optional[dict[str, Any]]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM phase0_operations WHERE payment_key = ?",
                (payment_key,),
            ).fetchone()
        return dict(row) if row else None

    def ensure(self, payment_key: str, mode: str) -> dict[str, Any]:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM phase0_operations WHERE payment_key = ?",
                (payment_key,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO phase0_operations (
                        payment_key, mode, status, updated_at
                    ) VALUES (?, ?, 'NEW', ?)
                    """,
                    (payment_key, mode, self._now()),
                )
            elif row["mode"] != mode:
                raise ValueError("payment identity reused with a different Phase 0 mode")
        return self.get(payment_key) or {}

    def mark_verified(self, payment_key: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                UPDATE phase0_operations
                SET verify_count = verify_count + 1,
                    status = 'VERIFIED', updated_at = ?
                WHERE payment_key = ?
                """,
                (self._now(), payment_key),
            )

    def record_work_once(self, payment_key: str, result: dict[str, Any]) -> dict[str, Any]:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT work_count, result_json FROM phase0_operations WHERE payment_key = ?",
                (payment_key,),
            ).fetchone()
            if row is None:
                raise KeyError(payment_key)
            if int(row["work_count"]) == 0:
                payload = json.dumps(result, sort_keys=True, separators=(",", ":"))
                connection.execute(
                    """
                    UPDATE phase0_operations
                    SET work_count = 1, result_json = ?,
                        status = 'WORK_READY', updated_at = ?
                    WHERE payment_key = ?
                    """,
                    (payload, self._now(), payment_key),
                )
                return result
            if not row["result_json"]:
                raise RuntimeError("phase0 work_count is nonzero without a stored result")
            return json.loads(row["result_json"])

    def mark_status(self, payment_key: str, status: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE phase0_operations SET status = ?, updated_at = ? WHERE payment_key = ?",
                (status, self._now(), payment_key),
            )

    def record_settlement_attempt(
        self,
        payment_key: str,
        status: str,
        settlement: Optional[dict[str, Any]] = None,
    ) -> None:
        payload = (
            json.dumps(settlement, sort_keys=True, separators=(",", ":"))
            if settlement is not None
            else None
        )
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                UPDATE phase0_operations
                SET settle_count = settle_count + 1,
                    settlement_json = COALESCE(?, settlement_json),
                    status = ?, updated_at = ?
                WHERE payment_key = ?
                """,
                (payload, status, self._now(), payment_key),
            )


def _model_dump(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True)
    if hasattr(value, "dict"):
        return value.dict(by_alias=True)
    raise TypeError("value cannot be serialized as an x402 model")


def _encode_header(value: Any) -> str:
    payload = json.dumps(_model_dump(value), separators=(",", ":")).encode("utf-8")
    return base64.b64encode(payload).decode("ascii")


def _default_payload_decoder(payment_header: str) -> Any:
    from x402.schemas import PaymentPayload

    data = json.loads(base64.b64decode(payment_header).decode("utf-8"))
    return PaymentPayload.model_validate(data)


def _payment_key(payment_payload: Any) -> str:
    canonical = json.dumps(
        _model_dump(payment_payload),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _build_real_server(settings: Any) -> tuple[Any, Any]:
    from x402.http import FacilitatorConfig, HTTPFacilitatorClient
    from x402.mechanisms.evm.exact import ExactEvmServerScheme
    from x402.schemas import ResourceConfig
    from x402.server import x402ResourceServer

    facilitator = HTTPFacilitatorClient(
        FacilitatorConfig(url=settings.phase0_x402_facilitator_url)
    )
    server = x402ResourceServer(facilitator).register(
        settings.phase0_x402_network,
        ExactEvmServerScheme(),
    )
    # x402 2.17 requires initialization before payment requirements can be
    # constructed. Initialization discovers the facilitator's supported kinds.
    server.initialize()
    requirements = server.build_payment_requirements(
        ResourceConfig(
            scheme="exact",
            price=settings.phase0_x402_price,
            network=settings.phase0_x402_network,
            pay_to=settings.phase0_x402_pay_to_address,
        )
    )
    if not requirements:
        raise RuntimeError("x402 did not build Phase 0 payment requirements")
    return server, requirements[0]


def _settlement_success(settlement: Any) -> bool:
    return bool(
        getattr(settlement, "success", False)
        if not isinstance(settlement, dict)
        else settlement.get("success")
    )


def _payer(verify_result: Any) -> Optional[str]:
    value = getattr(verify_result, "payer", None)
    return str(value) if value else None


def _is_valid(verify_result: Any) -> bool:
    if isinstance(verify_result, dict):
        return bool(verify_result.get("is_valid", verify_result.get("isValid")))
    return bool(getattr(verify_result, "is_valid", False))


def attach_phase0_x402_routes(
    app: FastAPI,
    settings: Any,
    *,
    resource_server: Any = None,
    requirements: Any = None,
    store: Optional[Phase0Store] = None,
    payload_decoder: Optional[Callable[[str], Any]] = None,
) -> None:
    """Attach the synthetic paid route when Gate B probing is enabled."""

    if not settings.phase0_x402_enabled:
        return
    if not settings.phase0_x402_pay_to_address:
        raise ValueError("YTX_PHASE0_PAY_TO_ADDRESS is required when Phase 0 x402 is enabled")

    if resource_server is None or requirements is None:
        resource_server, requirements = _build_real_server(settings)

    state_store = store or Phase0Store(settings.phase0_x402_state_path)
    decode_payload = payload_decoder or _default_payload_decoder

    @app.get("/internal/phase0/x402/health")
    async def phase0_health() -> dict[str, Any]:
        return {
            "status": "ok",
            "enabled": True,
            "network": settings.phase0_x402_network,
            "price": settings.phase0_x402_price,
            "expected_buyer_configured": bool(
                settings.phase0_x402_expected_buyer_address
            ),
        }

    @app.get("/internal/phase0/x402/state/{payment_key}")
    async def phase0_state(payment_key: str) -> JSONResponse:
        row = state_store.get(payment_key)
        if not row:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        return JSONResponse(
            content={
                "payment_key": payment_key,
                "mode": row["mode"],
                "status": row["status"],
                "verify_count": row["verify_count"],
                "work_count": row["work_count"],
                "settle_count": row["settle_count"],
                "transaction": (
                    (json.loads(row["settlement_json"]) or {}).get("transaction")
                    if row.get("settlement_json")
                    else None
                ),
            }
        )

    @app.post("/internal/phase0/x402")
    async def phase0_paid_probe(request: Request, body: Phase0Request) -> JSONResponse:
        payment_header = request.headers.get("payment-signature")
        if not payment_header:
            payment_required = resource_server.create_payment_required_response(
                [requirements],
                resource={
                    "url": str(request.url),
                    "description": "Synthetic YouTube Transcript Extractor Phase 0 lifecycle probe",
                    "mime_type": "application/json",
                },
            )
            return JSONResponse(
                status_code=402,
                content={"error": "payment_required"},
                headers={"PAYMENT-REQUIRED": _encode_header(payment_required)},
            )

        try:
            payment_payload = decode_payload(payment_header)
            payment_key = _payment_key(payment_payload)
            operation = state_store.ensure(payment_key, body.mode)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return JSONResponse(
                status_code=409,
                content={"error": "payment_identity_conflict", "message": str(exc)},
            )

        if operation.get("status") == "SETTLED":
            result = json.loads(operation["result_json"])
            settlement = json.loads(operation["settlement_json"])
            return JSONResponse(
                content={**result, "replayed": True, "payment_key": payment_key},
                headers={"PAYMENT-RESPONSE": _encode_header(settlement)},
            )

        if operation.get("status") == "SETTLEMENT_UNKNOWN":
            try:
                settlement = await resource_server.settle_payment(
                    payment_payload,
                    requirements,
                )
                settlement_data = _model_dump(settlement)
                if _settlement_success(settlement):
                    state_store.record_settlement_attempt(
                        payment_key,
                        "SETTLED",
                        settlement_data,
                    )
                    current = state_store.get(payment_key) or {}
                    result = json.loads(current["result_json"])
                    return JSONResponse(
                        content={
                            **result,
                            "reconciled": True,
                            "payment_key": payment_key,
                        },
                        headers={"PAYMENT-RESPONSE": _encode_header(settlement)},
                    )
                state_store.record_settlement_attempt(
                    payment_key,
                    "SETTLEMENT_UNKNOWN",
                )
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": "settlement_still_unknown",
                        "payment_key": payment_key,
                    },
                )
            except Exception:
                state_store.record_settlement_attempt(
                    payment_key,
                    "SETTLEMENT_UNKNOWN",
                )
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": "settlement_still_unknown",
                        "payment_key": payment_key,
                    },
                )

        verify_result = await resource_server.verify_payment(payment_payload, requirements)
        if not _is_valid(verify_result):
            return JSONResponse(status_code=402, content={"error": "invalid_payment"})

        expected_buyer = settings.phase0_x402_expected_buyer_address
        payer = _payer(verify_result)
        if expected_buyer and (not payer or payer.lower() != expected_buyer.lower()):
            return JSONResponse(status_code=403, content={"error": "unexpected_buyer"})

        state_store.mark_verified(payment_key)

        if body.mode == "fail_after_verify":
            state_store.mark_status(payment_key, "FAILED_AFTER_VERIFY_UNSETTLED")
            return JSONResponse(
                status_code=503,
                content={
                    "error": "synthetic_fail_after_verify",
                    "payment_key": payment_key,
                },
            )

        if body.delay_ms:
            await asyncio.sleep(body.delay_ms / 1000)

        result = state_store.record_work_once(
            payment_key,
            {
                "ok": True,
                "mode": body.mode,
                "marker": body.marker,
                "synthetic": True,
            },
        )

        if body.mode == "fail_after_work":
            state_store.mark_status(payment_key, "FAILED_AFTER_WORK_UNSETTLED")
            return JSONResponse(
                status_code=503,
                content={
                    "error": "synthetic_fail_after_work",
                    "payment_key": payment_key,
                },
            )

        try:
            settlement = await resource_server.settle_payment(payment_payload, requirements)
            settlement_data = _model_dump(settlement)
        except Exception:
            state_store.record_settlement_attempt(
                payment_key,
                "SETTLEMENT_UNKNOWN",
            )
            return JSONResponse(
                status_code=503,
                content={"error": "settlement_unknown", "payment_key": payment_key},
            )

        if not _settlement_success(settlement):
            state_store.record_settlement_attempt(
                payment_key,
                "SETTLEMENT_UNKNOWN",
            )
            return JSONResponse(
                status_code=503,
                content={"error": "settlement_unknown", "payment_key": payment_key},
            )

        if body.mode == "settlement_ack_loss":
            # Deliberately discard the successful response to simulate losing the
            # facilitator acknowledgement after remote settlement.
            state_store.record_settlement_attempt(
                payment_key,
                "SETTLEMENT_UNKNOWN",
            )
            return JSONResponse(
                status_code=503,
                content={
                    "error": "synthetic_settlement_ack_loss",
                    "payment_key": payment_key,
                },
            )

        state_store.record_settlement_attempt(
            payment_key,
            "SETTLED",
            settlement_data,
        )

        duplicate_probe: Optional[dict[str, Any]] = None
        if body.mode == "duplicate_settlement_probe":
            try:
                duplicate = await resource_server.settle_payment(
                    payment_payload,
                    requirements,
                )
                duplicate_probe = _model_dump(duplicate)
                state_store.record_settlement_attempt(
                    payment_key,
                    "SETTLED",
                    settlement_data,
                )
            except Exception as exc:
                state_store.record_settlement_attempt(
                    payment_key,
                    "SETTLED",
                    settlement_data,
                )
                duplicate_probe = {"raised": exc.__class__.__name__}

        if body.mode == "response_loss":
            return JSONResponse(
                status_code=503,
                content={
                    "error": "synthetic_response_loss",
                    "payment_key": payment_key,
                },
            )

        response_body: dict[str, Any] = {
            **result,
            "payment_key": payment_key,
            "replayed": False,
        }
        if duplicate_probe is not None:
            response_body["duplicate_settlement_probe"] = duplicate_probe

        return JSONResponse(
            content=response_body,
            headers={"PAYMENT-RESPONSE": _encode_header(settlement)},
        )
