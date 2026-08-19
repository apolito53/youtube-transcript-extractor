"""Event-correlated Phase 0 settlement reconciliation.

Circle's EIP-3009 implementation emits AuthorizationUsed(authorizer, nonce) when
an authorization is consumed for transfer and emits AuthorizationCanceled for a
cancellation. Requiring AuthorizationUsed and the exact ERC-20 Transfer in the
same transaction binds recovery to the signed payment without depending on the
facilitator's outer transaction calldata shape.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from .phase0_chain_recovery import (
    AUTHORIZATION_STATE_ABI,
    Phase0ChainRecovery,
    _topic_address,
    install_phase0_chain_recovery,
)


class Phase0EventChainRecovery(Phase0ChainRecovery):
    """Recover a settlement from correlated AuthorizationUsed + Transfer logs."""

    POLL_ATTEMPTS = 8
    POLL_INTERVAL_SECONDS = 0.75

    def _find_exact_transfer_once(self, web3: Any, row: Any) -> Optional[str]:
        from web3 import Web3

        token = Web3.to_checksum_address(row["asset"])
        from_address = Web3.to_checksum_address(row["from_address"])
        to_address = Web3.to_checksum_address(row["to_address"])
        nonce_hex = str(row["authorization_nonce"])
        nonce = bytes.fromhex(nonce_hex.removeprefix("0x"))
        amount = int(row["amount"])

        contract = web3.eth.contract(address=token, abi=AUTHORIZATION_STATE_ABI)
        used = bool(contract.functions.authorizationState(from_address, nonce).call())
        if not used:
            return None

        authorization_used_topic = Web3.keccak(
            text="AuthorizationUsed(address,bytes32)"
        ).hex()
        authorization_logs = web3.eth.get_logs(
            {
                "address": token,
                "fromBlock": int(row["from_block"]),
                "toBlock": "latest",
                "topics": [
                    authorization_used_topic,
                    _topic_address(from_address),
                    nonce_hex,
                ],
            }
        )
        if not authorization_logs:
            return None
        authorization_transactions = {
            bytes(log["transactionHash"]) for log in authorization_logs
        }

        transfer_topic = Web3.keccak(text="Transfer(address,address,uint256)").hex()
        transfer_logs = web3.eth.get_logs(
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
        for log in transfer_logs:
            if bytes(log["transactionHash"]) not in authorization_transactions:
                continue
            if int.from_bytes(bytes(log["data"]), "big") != amount:
                continue
            return "0x" + bytes(log["transactionHash"]).hex()
        return None

    def _find_exact_transfer(self, web3: Any, row: Any) -> Optional[str]:
        """Poll briefly for RPC/indexing propagation of a just-settled transfer."""

        for attempt in range(self.POLL_ATTEMPTS):
            transaction = self._find_exact_transfer_once(web3, row)
            if transaction:
                return transaction
            if attempt + 1 < self.POLL_ATTEMPTS:
                time.sleep(self.POLL_INTERVAL_SECONDS)
        return None


def install_phase0_event_chain_recovery(app: Any, settings: Any) -> None:
    """Install event-correlated reconciliation for the synthetic Phase 0 route."""

    if not settings.phase0_x402_enabled:
        return
    recovery = Phase0EventChainRecovery(settings.phase0_x402_state_path)
    install_phase0_chain_recovery(app, settings, recovery=recovery)
