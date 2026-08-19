#!/usr/bin/env bash
set -euo pipefail

APP_NAME="youtube-transcript-extractor-gtphbw"
EXPECTED_BUYER="0x60d46F6b4c420b6405EEeB09dB07D92E0BD0DcEa"
NETWORK="eip155:84532"
TEST_USDC="0x036CbD53842c5426634e7929541eC2318f3dCF7e"
EXPECTED_AMOUNT="1000"
FACILITATOR_URL="https://facilitator.xpay.sh"

: "${FLY_API_TOKEN:?FLY_API_TOKEN is required}"
: "${PROVIDER_ADDRESS:?PROVIDER_ADDRESS is required}"

if ! [[ "$PROVIDER_ADDRESS" =~ ^0x[0-9A-Fa-f]{40}$ ]]; then
  echo "PROVIDER_ADDRESS must be an EVM address" >&2
  exit 1
fi
if [ "${PROVIDER_ADDRESS,,}" = "${EXPECTED_BUYER,,}" ]; then
  echo "provider address must not equal the Phase 0 buyer" >&2
  exit 1
fi

flyctl secrets set --app "$APP_NAME" \
  YTX_PHASE0_X402_ENABLED=true \
  YTX_PHASE0_NETWORK="$NETWORK" \
  YTX_PHASE0_PRICE='$0.001' \
  YTX_PHASE0_FACILITATOR_URL="$FACILITATOR_URL" \
  YTX_PHASE0_PAY_TO_ADDRESS="$PROVIDER_ADDRESS" \
  YTX_PHASE0_EXPECTED_BUYER_ADDRESS="$EXPECTED_BUYER" \
  --stage

flyctl deploy --app "$APP_NAME" --ha=false --remote-only

origin="https://${APP_NAME}.fly.dev"
health_code=""
for _ in $(seq 1 30); do
  health_code="$(curl -sS -o /tmp/ytx-health.json -w '%{http_code}' "$origin/api/health" || true)"
  if [ "$health_code" = 200 ]; then break; fi
  sleep 2
done
[ "$health_code" = 200 ]
[ "$(jq -r '.phase0_x402_enabled' /tmp/ytx-health.json)" = true ]

phase0_code="$(curl -sS -o /tmp/phase0-health.json -w '%{http_code}' "$origin/internal/phase0/x402/health")"
[ "$phase0_code" = 200 ]
[ "$(jq -r '.network' /tmp/phase0-health.json)" = "$NETWORK" ]
[ "$(jq -r '.price' /tmp/phase0-health.json)" = '$0.001' ]
[ "$(jq -r '.expected_buyer_configured' /tmp/phase0-health.json)" = true ]

challenge_code="$(curl -sS -o /tmp/phase0-challenge.json -D /tmp/phase0-headers.txt -w '%{http_code}' \
  -H 'accept: application/json' \
  -H 'content-type: application/json' \
  --data '{"mode":"success","marker":"deploy-preflight"}' \
  "$origin/internal/phase0/x402")"
[ "$challenge_code" = 402 ]
encoded="$(awk 'BEGIN{IGNORECASE=1} /^payment-required:/ {sub(/^[^:]+:[[:space:]]*/,""); gsub(/\r/,""); print; exit}' /tmp/phase0-headers.txt)"
[ -n "$encoded" ]

PAYMENT_REQUIRED="$encoded" PROVIDER="$PROVIDER_ADDRESS" NETWORK="$NETWORK" TEST_USDC="$TEST_USDC" EXPECTED_AMOUNT="$EXPECTED_AMOUNT" python - <<'PY'
import base64
import json
import os

payload = json.loads(base64.b64decode(os.environ["PAYMENT_REQUIRED"]).decode())
assert payload.get("x402Version") == 2
accepted = payload.get("accepts") or []
matches = [
    item for item in accepted
    if item.get("scheme") == "exact"
    and item.get("network") == os.environ["NETWORK"]
    and str(item.get("asset", "")).lower() == os.environ["TEST_USDC"].lower()
    and str(item.get("amount")) == os.environ["EXPECTED_AMOUNT"]
    and str(item.get("payTo", "")).lower() == os.environ["PROVIDER"].lower()
]
assert matches, payload
PY

mkdir -p .ops
jq -n \
  --arg provider "$PROVIDER_ADDRESS" \
  --arg network "$NETWORK" \
  --arg asset "$TEST_USDC" \
  --arg amount_usdc "0.001" \
  --arg facilitator "$FACILITATOR_URL" \
  '{status:"ok",phase0_enabled:true,provider_address:$provider,network:$network,asset:$asset,amount_usdc:$amount_usdc,facilitator_url:$facilitator,unpaid_challenge_http:402}' \
  > .ops/phase0-deploy-result.json
cat .ops/phase0-deploy-result.json
