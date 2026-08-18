# Gate A — Frozen x402 product and lifecycle contract

Status: **frozen for Phase 0**

Gate A does not add x402 payment handling, wallet logic, facilitator calls, or a
paid production endpoint. Its purpose is to make the assumptions that Phase 0
must prove explicit and testable before provider work is coupled to payment.

The executable companion to this document is
`src/youtube_transcript_extractor/paid_contract.py`.

## 1. Existing application baseline

The current human application already has:

- versioned SQLite transcript caching;
- cached formatted outputs keyed by source content plus formatter identity;
- explicit refreshes that create transcript v2, v3, and later versions;
- prompt versioning through `OpenAIFormatter.cache_key`;
- a Fly deployment with one Machine and a persistent `/data` volume;
- optional `YTX_PROXY_URL` routing for YouTube caption and metadata traffic.

`TranscriptCache` is **application content cache/history only**. It is not and
must never become proof of payment authorization, payment settlement,
idempotency ownership, or paid replay eligibility merely because it also uses
SQLite.

The future paid-operation store is a distinct source of truth.

## 2. Product surfaces

### Existing human surfaces

`POST /api/extract` currently performs transcript acquisition/cache lookup and,
when AI formatting is configured, may also dispatch the formatter. It returns
both deterministic clean Markdown and optional formatted Markdown.

`POST /api/format` remains a directly callable formatter surface.

Therefore `/api/extract` is **not** a zero-variable-cost deterministic route in
its current form.

### Paid V1 surface

The intended machine product remains:

`POST /v1/transcript`

Input contract:

- `url`
- optional `language`
- optional `include_timestamps`

Public paid V1 does not expose caller-supplied caption segments, batch work,
playlists, uploads, summarization, translation, diarization, embeddings, or
RAG.

A successful paid result contains at minimum:

- stable result ID;
- video/source metadata;
- final polished Markdown;
- exact result content hash;
- source transcript version and content hash;
- formatter model and prompt version;
- usage/cost metadata available from the provider;
- warnings and normalized acquisition metadata.

## 3. Initial Mainnet pilot boundary

The first Mainnet pilot is deliberately **paid-only**:

- public free extraction: disabled;
- public free AI formatting: disabled;
- Bazaar discovery: disabled.

Local/development human use is not removed by this contract.

Before the pilot, runtime configuration must prove that the public deployment
cannot dispatch the formatter through an unpaid route. Free-AI budget-ledger
work is deferred until after paid viability is demonstrated because no free
model dispatch exists during the pilot.

Public free extraction may return later only after its effect on paid
acquisition is isolated or explicitly accepted as an experimental dependency.

## 4. Canonical paid request identity

A logical paid request fingerprint binds:

- paid route version;
- canonical YouTube video ID;
- normalized requested language;
- timestamp mode;
- formatter model;
- formatter prompt version;
- payment-requirement version.

The fingerprint is deterministic canonical JSON hashed with SHA-256.

A payment/idempotency identity must be bound to this fingerprint and the payer
identity. Reuse of the same identity with a materially different fingerprint is
a conflict: no provider work and no settlement.

The current formatter prompt is `v2`; a prompt or model change creates a new
paid request identity.

## 5. Paid state machine

Frozen states:

1. `AUTHORIZED`
2. `EXTRACTING`
3. `FORMAT_INTENT`
4. `EXECUTION_UNKNOWN`
5. `RESULT_READY`
6. `RESULT_DURABLE`
7. `SETTLEMENT_INTENT`
8. `SETTLEMENT_UNKNOWN`
9. `SETTLED`
10. `FAILED_UNPAID`

### Normal successful path

`AUTHORIZED -> EXTRACTING -> FORMAT_INTENT -> RESULT_READY -> RESULT_DURABLE -> SETTLEMENT_INTENT -> SETTLED`

### Formatter dispatch rule

`FORMAT_INTENT` is persisted **before** dispatching the external formatter.

If the formatter definitely fails before usable output and the service can
prove there is no ambiguous accepted external call, the operation may become
`FAILED_UNPAID`.

If the formatter may have accepted work but the service loses certainty about
the result, the operation becomes terminal `EXECUTION_UNKNOWN`.

There is **no automatic redispatch** from `EXECUTION_UNKNOWN`.

This preserves the V1 invariant that one logical paid operation dispatches the
expensive formatter at most once. Phase 0 or later provider evidence may only
relax this if a provider-native idempotency/retrieval mechanism is proven and
the frozen contract is intentionally revised.

### Settlement rule

Settlement is forbidden before `RESULT_DURABLE`.

`SETTLEMENT_INTENT` may become:

- `SETTLED` when the transfer is authoritatively confirmed;
- `SETTLEMENT_UNKNOWN` when remote success is possible but not yet provable;
- `FAILED_UNPAID` only when the payment path authoritatively proves no transfer
  occurred.

`SETTLEMENT_UNKNOWN` may only converge to `SETTLED` through reconciliation of
the **same payment identity**. It must not create a fresh blind payment attempt.

Mainnet is blocked unless Phase 0 proves that every successful transfer is
independently discoverable after timeout, response loss, or restart and can
converge to `SETTLED`.

Permanent ambiguity about a possibly successful transfer is not an acceptable
production state.

## 6. Pre-settlement durability barrier

`RESULT_READY` means a usable result exists locally but settlement is still
forbidden.

Before entering `RESULT_DURABLE`, secondary durable storage must contain:

1. the purchased result blob; and
2. a recovery manifest sufficient to authenticate replay and reconstruct
   settlement after complete loss of the local Fly volume.

Required recovery-manifest fields are frozen in
`RECOVERY_MANIFEST_REQUIRED_FIELDS` and include at least:

- result ID;
- request fingerprint;
- payer identity;
- payment identity;
- payment-requirement version;
- settlement-reconciliation identity;
- source video ID;
- source transcript version;
- source content hash;
- final result content hash;
- formatter model;
- formatter prompt version;
- replay deadline.

Only after both blob and manifest are durably acknowledged may the operation
enter `RESULT_DURABLE` and then `SETTLEMENT_INTENT`.

After settlement, settlement proof must be durably attached to recovery state.
The pre-settlement reconciliation identity must still be sufficient to discover
the transfer if the process or local volume disappears after remote settlement
but before that proof is attached.

## 7. Replay SLA

Successful paid results have a **30-day replay SLA**.

Throughout that period a settled request must be recoverable without:

- reacquiring the transcript;
- redispatching the formatter;
- requesting a new payment;
- depending solely on the original Fly volume.

The operation/payment tombstone must live at least as long as the replay SLA.

After the replay deadline the service must return an explicit expired-result
contract; it must never silently recompute a settled purchase or ask the same
payment identity to settle again.

Complete local-volume-loss restore is a required durability test, not merely a
process-restart test.

## 8. Relationship to the existing transcript cache

The existing `TranscriptCache` remains useful and should be reused for source
acquisition where safe.

For a paid operation:

- a fresh accepted cached transcript may avoid another YouTube call;
- the paid result records the exact source transcript version and source hash;
- staleness/freshness policy is explicit configuration and must be measured;
- the existing human `formatted_outputs` cache is not the paid replay store;
- a new logical paid operation does not inherit settlement or idempotency merely
  because equivalent formatted Markdown exists in the human cache.

During the validation period, new paid logical operations should dispatch the
paid formatter path rather than using human formatted-cache hits so latency,
provider cost, and uncertainty rates are measured cleanly. Idempotent paid
replay comes from the paid result store and performs no provider work.

## 9. Formatter fidelity contract

The frozen initial paid formatter is one model plus prompt version `v2`.

Prompt v2 intentionally removes bracketed non-speech cue tags such as
`[music]`, `[applause]`, and `[laughter]`, regardless of capitalization.
Removing those tags is therefore an allowed transformation, not a fidelity
violation.

The launch fidelity corpus still treats these as material failures:

- substantive omission;
- invention;
- changed numbers, dates, or negation;
- fabricated speaker attribution;
- meaning-changing reordering;
- interpretation-changing structure or headings.

## 10. Acquisition and paid/free isolation

`YTX_PROXY_URL` is currently a single YouTube-facing path for captions and
metadata. Local semaphores cannot isolate the reputation of a shared external
egress/proxy.

Therefore the first paid Mainnet pilot disables public free extraction.

Free extraction may be enabled later only when either:

- it uses a genuinely separate provider/egress/quota failure domain from paid
  acquisition; or
- testing demonstrates acceptable shared risk and the contract is explicitly
  weakened for a bounded experiment.

A reactive kill switch is not considered proof of reputation isolation because
YouTube blocking may persist after traffic stops.

## 11. Economics contract

`$0.05` is a candidate exact price, not a production promise.

All-in variable cost includes:

- formatter/model usage;
- transcript acquisition or proxy/provider cost;
- facilitator/network/settlement cost;
- marginal Fly compute/network cost;
- secondary durability/storage/egress;
- variable cost from failed, unpaid, `EXECUTION_UNKNOWN`, and settlement-unknown
  attempts allocated across successful settled operations.

Before freezing price/size:

- p95 all-in successful cost must be `<= $0.02`;
- conservative all-in maximum for an accepted request must be `< $0.035`;
- cost per settled operation is measured as total variable spend for all
  attempts in the measurement window divided by successful settled operations;
- cached and uncached acquisition paths and failure-state rates are reported
  separately.

If these do not hold, change request limits, formatter/acquisition strategy,
price, or product shape before broad Mainnet rollout.

## 12. Early price-bearing value gate

Immediately after the Phase 0 Sepolia protocol spike, run a small independent
value smoke test before funding the durable product build.

Minimum evidence:

- at least 3 independent target developers complete the standard buyer flow
  without bespoke server-specific integration help; and
- at least 2 make a **price-bearing `$0.05` choice** for the polished artifact.

Price-bearing evidence means an actual purchase, a real committed test budget,
or an explicit selection of the disclosed `$0.05` polished result over the
deterministic alternative for a real workflow.

Repeated zero-cost Sepolia calls or generic preference for prettier output do
not satisfy the value gate.

Where practical, compare deterministic and polished fixtures blind before
revealing which path produced them.

If the polished SKU fails this gate, test the simpler accountless deterministic
acquisition SKU instead of continuing the durable AI-polish build by inertia.

## 13. Gate A exit criteria

Gate A is complete when all of the following are true in-repository:

- paid states and allowed transitions are executable and unit-tested;
- direct `RESULT_READY -> SETTLEMENT_INTENT` is impossible in the frozen graph;
- `EXECUTION_UNKNOWN` cannot automatically redispatch formatting;
- `SETTLEMENT_UNKNOWN` cannot create a fresh payment attempt;
- canonical request fingerprinting is executable and version-sensitive;
- the 30-day replay SLA and recovery-manifest fields are frozen;
- the paid-only initial Mainnet pilot boundary is explicit;
- `$0.05` economics thresholds and the 3-person/2-price-bearing value gate are
  explicit;
- the project runtime baseline is Python 3.11;
- repository documentation accurately distinguishes deterministic cue handling
  from AI-formatted cue removal.

After Gate A, proceed to **Gate B / Phase 0**: a synthetic Base Sepolia x402
lifecycle spike with no YouTube or OpenAI coupling.
