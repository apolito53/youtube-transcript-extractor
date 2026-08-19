# Gate C — Price-bearing value smoke

Status: **ready to execute**

Gate B established protocol feasibility. Gate C answers a different question
before the durable paid pipeline is built:

> Does the polished transcript remove enough downstream work that real target
> developers would choose it at a disclosed price of $0.05?

This is a product-value gate, not another architecture review.

## Pass criteria

Gate C passes only when all of the following are true:

1. at least 3 independent target developers complete the test;
2. they are not the product owner, implementation agent, or people who helped
   design the service;
3. each tester demonstrates familiarity with a standard x402 buyer flow rather
   than requiring bespoke integration code written for them;
4. each tester evaluates blinded deterministic-versus-polished transcript
   artifacts in the context of a real or credibly described downstream workflow;
5. at least 2 testers make a price-bearing `$0.05` choice for the polished
   artifact.

A price-bearing choice means at least one of:

- an actual `$0.05` purchase;
- a real committed test budget at that price;
- an explicit choice of the disclosed `$0.05` polished result over the
  deterministic alternative for a real workflow.

Generic statements such as “B looks nicer,” “I would probably use this,” or
zero-price preferences do not pass the gate.

## Test artifact

The test uses three small representative deterministic transcript fixtures:

1. technical/tutorial content;
2. interview/discussion content;
3. long-form explanatory content with numbers, qualifiers, and non-speech cues.

The deterministic fixture represents the output of the non-AI cleanup stage,
not raw YouTube caption windows.

Each deterministic fixture is sent through the **actual deployed prompt-v2
formatter** exactly once when the test pack is generated. No human-written
“ideal polished version” is substituted.

The resulting pair is blinded per tester:

- one option is deterministic clean Markdown;
- one option is the actual prompt-v2 polished Markdown;
- A/B placement is deterministic from tester ID plus fixture ID, but hidden from
  the tester;
- proposed price is revealed as `$0.05` when the tester makes the price-bearing
  decision.

The answer key and generated polished fixtures are operational research data and
are kept outside the public product repository while the blind test is active.

## Tester questions

For each fixture, collect:

- preferred option: A or B;
- what the tester would do with the transcript next;
- whether either version would require additional cleanup before that next step;
- what work the preferred version saves, if any;
- whether the difference is material or merely aesthetic.

After the artifact comparison, disclose the candidate price and ask:

> For the workflow you just described, would you choose the polished result at
> $0.05 per transcript over the deterministic alternative?

Record the answer verbatim enough to distinguish an actual price-bearing choice
from politeness or hypothetical enthusiasm.

## x402 flow check

The artifact-value judgment and payment-protocol judgment are separate signals.
A tester must demonstrate that they can use standard x402 tooling without
service-specific handholding. A Base Sepolia synthetic purchase may be used for
this mechanics check; it does not itself count as the `$0.05` value decision.

The product owner may provide the endpoint, expected network, and a normal API
example. The implementation agent must not write custom buyer code uniquely for
an individual tester and then count that as independent integration evidence.

## Avoiding a rigged test

Do not:

- tell testers which option is AI-polished before they choose;
- lead with claims that the polished version is superior;
- count visual preference alone as workflow value;
- select only fixtures where prompt v2 obviously performs well;
- discard a tester because they prefer deterministic output;
- treat testnet willingness as equivalent to willingness to pay five cents.

Negative evidence is a valid Gate C result.

## Decision

### Pass

If at least 3 independent target developers complete the flow and at least 2
make a credible price-bearing `$0.05` polished-output choice, Gate C passes and
the durable paid-operation/result/replay implementation may begin.

### Fail / pivot

If the polished SKU fails the gate, do not build the durable AI-polish pipeline
by inertia. Test a simpler accountless deterministic transcript-acquisition SKU
or materially revise the transformation/value proposition first.

### Inconclusive

If testers cannot complete the mechanics because the test environment itself is
broken, fix the test environment and repeat. Protocol friction is evidence too,
but infrastructure failure must not be mislabeled as lack of product demand.

## Evidence record

For each tester, preserve:

- anonymous/stable tester ID;
- date;
- x402 mechanics result;
- fixture choices;
- described downstream workflow;
- cleanup/work-saved notes;
- disclosed `$0.05` decision;
- whether the decision qualifies as price-bearing;
- optional qualitative comments.

Gate C should end with a short aggregate decision document, not a collection of
cherry-picked quotes.
