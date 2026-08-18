# Money movement policy

This repository follows the canonical operational policy in `apolito53/nova-ops/docs/money-movement-policy.md` as it is productized for x402.

- **Proposals, plans, design docs, and evaluation outputs do not by themselves authorize money movement.**
- For an x402 endpoint already known to be controlled by Anthony, finalize the proposal/request/test first, then ask Anthony once for approval to execute that finalized paid operation. A normal affirmative response such as "approved", "go ahead", or "yep" is sufficient. **No separate five-field authorization envelope is required for Anthony-controlled x402 endpoints.**
- Technical preflight should still verify the expected endpoint, x402 version, network, asset, receiver, price, and route invariants where practical. Those are safety checks, not another user-authorization ceremony.
- The simple approval applies to the finalized operation presented. If the endpoint/tier materially changes, the advertised price changes materially, or a new separately paid execution is desired after a known successful settlement, ask again.
- A clearly pre-settlement technical retry may continue under the same approval when it cannot create an additional charge. If a prior settlement may have succeeded, reconcile it before retrying payment rather than risking a duplicate charge.
- If the service or recipient is not already known to be controlled by Anthony, treat the operation as third-party external spending. Third-party authorization must explicitly specify **recipient/vendor, maximum total, asset and network, quantity, and maximum number of attempts**. Any missing or ambiguous field means stop and ask.
- **Token approvals, allowance changes, permits, delegations, swaps, and bridges remain money movement or grants of spending authority.** The controlled-x402 exception covers the ordinary bounded endpoint payment; it does not imply broader token authority or unrelated protocol actions.
- Transfers between wallets already known to be controlled by Anthony are internal fund positioning. If wallet ownership is uncertain, treat the transfer as external.
- Wallet-bearing automation must fail closed for third-party spending and must not turn repository state or proposal content into external payment authority.

> **Anthony-controlled x402 calls need one deliberate go-ahead after the action is finalized. Third-party spending needs an explicit bounded authorization envelope.**
