#!/usr/bin/env bash
# Check whether saferidelive.co.ke is delegated to the Route 53 nameservers.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"

echo "Route 53 nameservers for the zone:"
R53_NS="$(aws route53 get-hosted-zone --id "$HOSTED_ZONE_ID" \
  --query 'DelegationSet.NameServers' --output text)"
echo "  $R53_NS"

echo ""
echo "Public NS currently resolving for ${DOMAIN}:"
LIVE_NS="$(dig +short NS "$DOMAIN" @8.8.8.8 | sort | tr '\n' ' ')"
echo "  ${LIVE_NS:-<none yet>}"

if echo "$LIVE_NS" | grep -qi "awsdns"; then
  echo ""
  echo "✅ Delegation is LIVE (awsdns nameservers detected). Ready to issue certs + deploy."
else
  echo ""
  echo "⏳ Not delegated yet. Set these 4 nameservers at Truehost for ${DOMAIN}:"
  for ns in $R53_NS; do echo "    $ns"; done
fi
