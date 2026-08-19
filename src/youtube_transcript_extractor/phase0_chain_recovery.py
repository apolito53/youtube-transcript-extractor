"""On-chain reconciliation for ambiguous Phase 0 x402 settlements.

This module is deliberately limited to the synthetic Base Sepolia Phase 0
harness.  It proves that a paid result can be recovered after a lost facilitator
acknowledgement without issuing another settlement request.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from .phase0_x402 import (
    Phase0Request,
    _default_payload_decoder,
    _model_dump,
    _payment_key,
)

BASE_SEPOLIA_NETWORK = "eip155:84532"
BASE_SEPOLIA_USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
DEFAULT_RPC_URLS = (
    "https://base-sepolia-rpc.publicnode.com",
    "https://sepolia.base.org",
)

AUTHORIZATION_STATE_ABI = [
    {
        "type": "function",
        "name": "authorizationState",
        "stateMutability": "view",
        "inputs": [
            {"name": "authorizer", "type": "address"},
            {"name": "nonce", "type": "bytes32"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    }
]

TRANSFER_WITH_AUTHORIZATION_ABI = [
    {
        "type": "function",
        "name": "transferWithAuthorization",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "from", "type": "address"},
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "validAfter", "type": "uint256"},
            {"name": "validBefore", "type": "uint256"},
            {"name": "nonce", "type": "bytes32"},
            {"name": "v", "type": "uint8"},
            {"name": "r", "type": "bytes32"},
            {"name": "s", "type": "bytes32"},
        ],
        "outputs": [],
    }
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _topic_address(address: str) -> str:
    return "0x" + ("0" * 24) + address.lower().removeprefix("0x")


def _same_address(left: str, right: str) -> bool:
    return left.lower() == right.lower()


def _authorization_from_payload(payment_payload: Any) -> dict[str, Any]:
    dumped = _model_dump(payment_payload)
    accepted = dumped.get("accepted") or {}
    inner = dumped.get("payload") or {}
    authorization = inner.get("authorization") or {}

    required = {
        "network": accepted.get("network"),
        "asset": accepted.get("asset"),
        "pay_to": accepted.get("payTo") or accepted.get("pay_to"),
        "amount": accepted.get("amount"),
        "from_address": authorization.get("from"),
        "to_address": authorization.get("to"),
        "value": authorization.get("value"),
        "nonce": authorization.get("nonce"),
    }
    if not all(value not in (None, "") for value in required.values()):
        raise ValueError("payment payload is missing EIP-3009 reconciliation fields")

    if required["network"] != BASE_SEPOLIA_NETWORK:
        raise ValueError("Phase 0 chain reconciliation only supports Base Sepolia")
    if str(required["asset"]).lower() != BASE_SEPOLIA_USDC.lower():
        raise ValueError("Phase 0 chain reconciliation only supports Base Sepolia USDC")
    if not _same_address(str(required["pay_to"]), str(required["to_address"])):
        raise ValueError("signed authorization recipient does not match payment requirement")
    if int(str(required["amount"])) != int(str(required["value"])):
        raise ValueError("signed authorization amount does not match payment requirement")
    nonce = str(required["nonce"])
    if not nonce.startswith("0x") or len(nonce) != 66:
        raise ValueError("EIP-3009 authorization nonce must be 32 bytes")

    return {
        "network": str(required["network"]),
        "asset": str(required["asset"]),
        "from_address": str(required["from_address"]),
        "to_address": str(required["to_address"]),
        "amount": str(required["amount"]),
        "authorization_nonce": nonce,
    }


class Phase0ChainRecovery:
    """Persistent reconciliation anchor plus authoritative chain lookup."""

    def __init__(self, state_path: str, rpc_urls: Optional[tuple[str, ...]] = None):
        self.state_path = state_path
        configured = tuple(
            item.strip()
            for item in os.getenv("YTX_PHASE0_RPC_URLS", "").split(",")
            if item.strip()
        )
        self.rpc_urls = rpc_urls or configured or DEFAULT_RPC_URLS

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.state_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS phase0_reconciliation (
                payment_key TEXT PRIMARY KEY,
                network TEXT NOT NULL,
                asset TEXT NOT NULL,
                from_address TEXT NOT NULL,
                to_address TEXT NOT NULL,
                amount TEXT NOT NULL,
                authorization_nonce TEXT NOT NULL,
                from_block INTEGER NOT NULL,
                recovered_transaction TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        return connection

    @staticmethod
    def _web3(rpc_url: str):
        from web3 import Web3

        return Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))

    def _with_rpc(self, callback: Callable[[Any], Any]) -> Any:
        errors = []
        for rpc_url in self.rpc_urls:
            try:
                web3 = self._web3(rpc_url)
                if not web3.is_connected():
                    raise RuntimeError("RPC is not connected")
                return callback(web3)
            except Exception as exc:  # pragma: no cover - exact RPC errors are external state
                errors.append("{}: {}".format(rpc_url, exc.__class__.__name__))
        raise RuntimeError("all Phase 0 reconciliation RPCs failed: {}".format(", ".join(errors)))

    def _current_block(self) -> int:
        return int(self._with_rpc(lambda web3: web3.eth.block_number))

    def ensure_anchor(self, payment_key: str, payment_payload: Any) -> None:
        identity = _authorization_from_payload(payment_payload)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM phase0_reconciliation WHERE payment_key = ?",
                (payment_key,),
            ).fetchone()
            if existing is not None:
                for field in (
                    "network",
                    "asset",
                    "from_address",
                    "to_address",
                    "amount",
                    "authorization_nonce",
                ):
                    if str(existing[field]).lower() != str(identity[field]).lower():
                        raise ValueError("reconciliation identity conflict for {}".format(field))
                return

            from_block = self._current_block()
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO phase0_reconciliation (
                    payment_key, network, asset, from_address, to_address,
                    amount, authorization_nonce, from_block, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payment_key,
                    identity["network"],
                    identity["asset"],
                    identity["from_address"],
                    identity["to_address"],
                    identity["amount"],
                    identity["authorization_nonce"],
                    from_block,
                    now,
                    now,
                ),
            )

    def get_status(self, payment_key: str) -> Optional[str]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT status FROM phase0_operations WHERE payment_key = ?",
                (payment_key,),
            ).fetchone()
        return str(row["status"]) if row else None

    def _find_exact_transfer(self, web3: Any, row: sqlite3.Row) -> Optional[str]:
        from web3 import Web3

        token = Web3.to_checksum_address(row["asset"])
        from_address = Web3.to_checksum_address(row["from_address"])
        to_address = Web3.to_checksum_address(row["to_address"])
        nonce = bytes.fromhex(str(row["authorization_nonce"]).removeprefix("0x"))
        amount = int(row["amount"])

        contract = web3.eth.contract(
            address=token,
            abi=AUTHORIZATION_STATE_ABI + TRANSFER_WITH_AUTHORIZATION_ABI,
        )
        used = bool(contract.functions.authorizationState(from_address, nonce).call())
        if not used:
            return None

        transfer_topic = Web3.keccak(text="Transfer(address,address,uint256)").hex()
        logs = web3.eth.get_logs(
            {
                "address": token,
                "fromBlock": int(row["from_block"]),
                "toBlock": "latest",
                "topics": [
                    transfer_topic,
                    _topic_address(from_address),
                    _topic_address(to_address),
                ],
            }
        )

        for log in logs:
            if int.from_bytes(bytes(log["data"]), "big") != amount:
                continue
            transaction_hash = log["transactionHash"]
            transaction = web3.eth.get_transaction(transaction_hash)
            try:
                function, arguments = contract.decode_function_input(transaction["input"])
            except Exception:
                continue
            if getattr(function, "fn_name", "") != "transferWithAuthorization":
                continue
            tx_nonce = arguments.get("nonce")
            if isinstance(tx_nonce, str):
                tx_nonce = bytes.fromhex(tx_nonce.removeprefix("0x"))
            if bytes(tx_nonce) != nonce:
                continue
            if not _same_address(str(arguments.get("from")), from_address):
                continue
            if not _same_address(str(arguments.get("to")), to_address):
                continue
            if int(arguments.get("value")) != amount:
                continue
            return transaction_hash.hex()
        return None

    def reconcile_and_mark(self, payment_key: str) -> Optional[dict[str, Any]]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM phase0_reconciliation WHERE payment_key = ?",
                (payment_key,),
            ).fetchone()
        if row is None:
            return None

        transaction = self._with_rpc(lambda web3: self._find_exact_transfer(web3, row))
        if not transaction:
            return None

        settlement = {
            "success": True,
            "errorReason": None,
            "errorMessage": None,
            "payer": row["from_address"],
            "transaction": transaction,
            "network": row["network"],
        }
        payload = json.dumps(settlement, sort_keys=True, separators=(",", ":"))
        now = _utc_now()
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE phase0_operations
                SET status = 'SETTLED', settlement_json = ?, updated_at = ?
                WHERE payment_key = ? AND status = 'SETTLEMENT_UNKNOWN'
                """,
                (payload, now, payment_key),
            )
            connection.execute(
                """
                UPDATE phase0_reconciliation
                SET recovered_transaction = ?, updated_at = ?
                WHERE payment_key = ?
                """,
                (transaction, now, payment_key),
            )
        return settlement


def install_phase0_chain_recovery(
    app: FastAPI,
    settings: Any,
    *,
    recovery: Optional[Any] = None,
    payload_decoder: Optional[Callable[[str], Any]] = None,
    payment_key_fn: Optional[Callable[[Any], str]] = None,
) -> None:
    """Wrap the synthetic paid route with fail-closed on-chain reconciliation."""

    if not settings.phase0_x402_enabled:
        return

    recovery_layer = recovery or Phase0ChainRecovery(settings.phase0_x402_state_path)
    decode_payload = payload_decoder or _default_payload_decoder
    key_for = payment_key_fn or _payment_key

    target = None
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == "/internal/phase0/x402" and "POST" in route.methods:
            target = route
            break
    if target is None:
        raise RuntimeError("Phase 0 paid route was not attached before chain recovery")

    original_endpoint = target.endpoint

    async def recovered_endpoint(request: Request, body: Phase0Request):
        payment_header = request.headers.get("payment-signature")
        if not payment_header:
            return await original_endpoint(request, body)

        try:
            payment_payload = decode_payload(payment_header)
            payment_key = key_for(payment_payload)
            await asyncio.to_thread(
                recovery_layer.ensure_anchor,
                payment_key,
                payment_payload,
            )
            status = recovery_layer.get_status(payment_key)
        except Exception:
            return JSONResponse(
                status_code=503,
                content={"error": "reconciliation_anchor_unavailable"},
            )

        if status == "SETTLEMENT_UNKNOWN":
            try:
                settlement = await asyncio.to_thread(
                    recovery_layer.reconcile_and_mark,
                    payment_key,
                )
            except Exception:
                settlement = None
            if not settlement:
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": "settlement_still_unknown_chain_unconfirmed",
                        "payment_key": payment_key,
                    },
                )

        return await original_endpoint(request, body)

    # FastAPI has already built the dependency graph for this route; changing
    # both call sites preserves the original parsed Request + Phase0Request args.
    target.endpoint = recovered_endpoint
    target.dependant.call = recovered_endpoint
