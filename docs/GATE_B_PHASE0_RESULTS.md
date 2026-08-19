# Gate B — Phase 0 x402 lifecycle results

Status: **complete**

Completed: 2026-08-19

This document records the evidence produced by the synthetic Base Sepolia x402
lifecycle spike required by `GATE_A_CONTRACT.md`.

Phase 0 deliberately did **not** call YouTube or OpenAI. Its only purpose was to
prove payment verification, delayed settlement, replay, ambiguity recovery,
restart recovery, and duplicate-settlement behavior before payment is coupled to
real provider work.

## 1. Frozen test contract

- x402 Python SDK: `2.17.0`
- scheme: `exact`
- network: Base Sepolia (`eip155:84532`)
- asset: Base Sepolia USDC (`0x036CbD53842c5426634e7929541eC2318f3dCF7e`)
- synthetic price: `$0.001` / 1,000 micro-USDC
- provider receiver: `0x9632c7eD958612C79DdBEF7EA9798D0950b207f9`
- test buyer: `0x60d46F6b4c420b6405EEeB09dB07D92E0BD0DcEa`
- deployed facilitator used for the successful matrix: `https://facilitator.xpay.sh`
- payment authorization window advertised by the tested requirement: 300 seconds

The deployed synthetic endpoint is `/internal/phase0/x402`. It is not the future
public paid transcript API.

## 2. Facilitator finding

The first spike used `https://x402.org/facilitator`. Verification succeeded, but
settlement repeatedly returned an `invalid_exact_evm_transaction_failed` error
whose underlying relayer transaction had a stale Ethereum account nonce. This
was a facilitator-relayer operational failure, not an EIP-3009 buyer
authorization failure.

Phase 0 therefore switched to XPay for the remaining test matrix. XPay produced
successful Base Sepolia settlements and was sufficient to test the resource
server lifecycle.

This result does **not** establish that any facilitator is permanently reliable.
Production design must treat facilitator availability and settlement response as
external state and retain independent reconciliation.

## 3. Baseline challenge and settlement

The deployed route returned a valid x402 v2 `402 Payment Required` challenge with
exactly the reviewed network, asset, amount, and receiver.

A normal paid synthetic request then produced:

- HTTP 200;
- a successful x402 settlement response;
- buyer balance delta: `-0.001 USDC`;
- receiver balance delta: `+0.001 USDC`;
- exactly one matching USDC transfer.

This proves the basic `challenge -> authorize -> verify -> work -> settle ->
response` path.

## 4. Failure before settlement

### Failure after verification

The harness verified a valid signed payment and then intentionally returned a
synthetic failure before doing work or attempting settlement.

Observed result:

- HTTP 503;
- no settlement response;
- buyer delta: `0`;
- receiver delta: `0`;
- zero matching transfers.

### Failure after work

The harness verified the payment, performed and persisted synthetic work, then
failed before settlement.

Observed result:

- HTTP 503;
- no settlement response;
- buyer delta: `0`;
- receiver delta: `0`;
- zero matching transfers.

These cases support the Gate A invariant that verification is not a charge and
that pre-settlement provider/application failure remains unpaid.

## 5. Response loss after successful settlement

The `response_loss` mode intentionally completed settlement and then returned a
synthetic 503 instead of the purchased result.

The buyer replayed the **exact same `PAYMENT-SIGNATURE` header**.

Observed result:

- first response: HTTP 503 after settlement;
- second response: HTTP 200 with `replayed: true`;
- the persisted result was reused;
- buyer delta across both calls: `-0.001 USDC`;
- receiver delta across both calls: `+0.001 USDC`;
- exactly one matching transfer.

This proves that a locally known `SETTLED` operation can replay without provider
work or a second settlement.

## 6. Lost settlement acknowledgement exposed the real ambiguity

With XPay, `settlement_ack_loss` intentionally discarded a successful
facilitator response after remote settlement.

The first implementation then attempted to call `settle` again with the same
signed authorization. The retry could not authoritatively establish the prior
success even though the chain showed exactly one transfer.

That experiment established a key Phase 0 conclusion:

> Repeating facilitator settlement is not an acceptable reconciliation strategy.

A `SETTLEMENT_UNKNOWN` operation needs an independent authoritative lookup of
the already-signed payment identity.

## 7. EIP-3009 chain reconciliation

Phase 0 added a reconciliation anchor persisted **before settlement** containing
the signed EIP-3009 identity:

- network;
- USDC contract;
- payer;
- receiver;
- exact amount;
- EIP-3009 authorization nonce;
- lower-bound block for reconciliation.

The first chain lookup used `authorizationState(authorizer, nonce)` plus transfer
inspection. Because a facilitator may wrap the token call, production-relevant
recovery was strengthened to event correlation.

The final recovery algorithm requires:

1. the authorization to be consumed;
2. Circle USDC `AuthorizationUsed(authorizer, nonce)` for the exact signed nonce;
3. an ERC-20 `Transfer(payer, receiver, amount)`;
4. both events to occur in the same transaction.

This binds recovered settlement to the exact signed payment without depending on
the facilitator's outer transaction calldata shape.

## 8. Bounded propagation recovery

An immediate RPC query after settlement occasionally observed the transfer before
all authorization/event indexes were available. The recovery path therefore
uses a small bounded propagation window rather than declaring permanent
ambiguity immediately.

Test configuration:

- 8 attempts;
- 0.75 seconds between attempts;
- approximately 6 seconds maximum bounded polling.

With that bounded polling enabled, an acknowledgement-loss request produced:

- first response: HTTP 503 after successful remote settlement;
- second request using the exact same payment signature: HTTP 200;
- `replayed: true`;
- settlement proof recovered from chain state;
- buyer delta: `-0.001 USDC`;
- receiver delta: `+0.001 USDC`;
- exactly one matching transfer.

No blind second settlement was required.

## 9. Process-restart recovery

The strongest Phase 0 ambiguity test intentionally:

1. settled a payment;
2. discarded its acknowledgement so local state became `SETTLEMENT_UNKNOWN`;
3. scheduled process termination;
4. confirmed a new application boot identity;
5. replayed the exact same signed authorization after restart.

Observed result:

- application boot ID changed, proving a real process restart;
- the persisted reconciliation anchor survived;
- the second request recovered settlement from chain evidence;
- the purchased synthetic result replayed with HTTP 200;
- buyer delta: `-0.001 USDC`;
- receiver delta: `+0.001 USDC`;
- exactly one matching transfer.

This satisfies the Phase 0 requirement that a successful transfer can converge
to `SETTLED` after response loss and process restart without issuing a new
payment.

This is a **process restart** result, not yet the later production requirement to
restore after complete destruction of the Fly volume. Complete local-volume-loss
recovery belongs to the durable-result implementation gate because Phase 0 has no
secondary result store by design.

## 10. Authorization lifetime

The final timing probe inserted 240 seconds of synthetic work between payment
verification and settlement.

Observed result:

- HTTP 200;
- settlement success;
- exactly one `$0.001` transfer;
- no authorization-expiry failure.

The tested x402 requirement advertised `maxTimeoutSeconds = 300`, so 240 seconds
provides direct evidence that the synchronous lifecycle can tolerate substantial
work while still leaving some settlement reserve. Production request deadlines
must still be much tighter than this proof-of-capability test.

## 11. Successful duplicate-settlement attempt

A direct XPay diagnostic intentionally created one signed EIP-3009 payment
payload and submitted **the identical payload twice** to `/settle`.

Observed result:

- first settle: HTTP 200, `success: true`;
- second settle: HTTP 200, `success: false`, `invalid_transaction_state`;
- buyer delta across both attempts: `-0.001 USDC`;
- receiver delta across both attempts: `+0.001 USDC`;
- exactly one matching transfer.

Thus EIP-3009 authorization consumption prevents the same signed authorization
from transferring twice in this tested flow.

This is defense in depth, not permission to blind-retry settlement. The resource
server must still reconcile `SETTLEMENT_UNKNOWN` independently because a failed
second settlement response does not, by itself, prove what happened to the first
attempt.

## 12. Gate B conclusions

Phase 0 establishes the following for the tested Base Sepolia exact-USDC flow:

- payment verification can occur without transfer;
- work can fail after verification without transfer;
- work can complete and still remain unpaid when settlement is never attempted;
- settlement can be deliberately delayed until after usable work exists;
- a known settled result can replay with no second charge;
- a lost settlement acknowledgement creates real ambiguity and must not trigger a
  blind new payment attempt;
- EIP-3009 identity plus on-chain event correlation can independently recover a
  successful settlement;
- bounded polling handles ordinary RPC/event-index propagation;
- settlement recovery survives a real process restart;
- a 240-second delayed settlement succeeds inside the tested 300-second payment
  requirement window;
- submitting the exact same already-consumed authorization to XPay settlement a
  second time does not produce a second transfer.

The most important implementation constraint leaving Gate B is therefore:

> `SETTLEMENT_UNKNOWN` is a reconciliation state, never a retry-payment state.

For EVM exact USDC, the production implementation should preserve the signed
EIP-3009 reconciliation identity before settlement and use authoritative chain
state/event evidence to resolve ambiguity.

## 13. Gate B exit decision

**Gate B passes.**

The protocol feasibility spike no longer blocks the product. The evidence is
strong enough to proceed to the next frozen gate without coupling the full paid
transcript pipeline to settlement yet.

The next gate is the **early price-bearing value smoke** from Gate A:

- at least 3 independent target developers complete a standard buyer/value flow;
- at least 2 make a credible `$0.05` price-bearing choice for the polished output.

The durable paid-operation/result/replay implementation remains blocked until
that value gate passes.
