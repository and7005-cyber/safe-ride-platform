#!/usr/bin/env bash
# Request + DNS-validate the two ACM certificates SafeRide needs:
#   - CloudFront cert (us-east-1): apex + www
#   - API cert     (af-south-1): api subdomain
# Validation CNAMEs are written into the Route 53 hosted zone, then we wait for
# ISSUED. Idempotent: an existing ISSUED cert for the same domains is reused.
# Writes ARNs to infra/.state/certs.env.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"

# request_validated_cert REGION PRIMARY_DOMAIN [SAN ...] -> prints cert ARN
request_validated_cert() {
  local region="$1" primary="$2"; shift 2
  local sans=("$@")

  # Reuse an existing issued/pending cert for this primary domain if present.
  local arn
  arn="$(aws acm list-certificates --region "$region" \
    --query "CertificateSummaryList[?DomainName=='${primary}'].CertificateArn | [0]" \
    --output text 2>/dev/null)"

  if [ "$arn" = "None" ] || [ -z "$arn" ]; then
    local args=(--domain-name "$primary" --validation-method DNS --region "$region")
    if [ "${#sans[@]}" -gt 0 ]; then
      args+=(--subject-alternative-names "${sans[@]}")
    fi
    arn="$(aws acm request-certificate "${args[@]}" --query CertificateArn --output text)"
    echo "  requested $primary -> $arn" >&2
    sleep 8 # let ACM populate the validation records
  else
    echo "  reusing $primary -> $arn" >&2
  fi

  # Upsert each (deduplicated) DNS validation record into Route 53.
  local seen=""
  while read -r name value; do
    [ -z "$name" ] && continue
    case "$seen" in *"|$name|"*) continue;; esac
    seen="${seen}|$name|"
    aws route53 change-resource-record-sets --hosted-zone-id "$HOSTED_ZONE_ID" \
      --change-batch "{\"Changes\":[{\"Action\":\"UPSERT\",\"ResourceRecordSet\":{\"Name\":\"${name}\",\"Type\":\"CNAME\",\"TTL\":300,\"ResourceRecords\":[{\"Value\":\"${value}\"}]}}]}" \
      >/dev/null
    echo "  validation record: $name" >&2
  done < <(aws acm describe-certificate --region "$region" --certificate-arn "$arn" \
            --query 'Certificate.DomainValidationOptions[].ResourceRecord.[Name,Value]' \
            --output text)

  echo "  waiting for ISSUED ($primary, region $region)..." >&2
  aws acm wait certificate-validated --region "$region" --certificate-arn "$arn"
  echo "$arn"
}

echo "==> CloudFront cert (us-east-1): ${DOMAIN} + www.${DOMAIN}"
CF_CERT_ARN="$(request_validated_cert "$CF_REGION" "$DOMAIN" "www.${DOMAIN}")"

echo "==> API cert (${BACKEND_REGION}): ${API_DOMAIN}"
API_CERT_ARN="$(request_validated_cert "$BACKEND_REGION" "$API_DOMAIN")"

cat > "$STATE_DIR/certs.env" <<EOF
CF_CERT_ARN=$CF_CERT_ARN
API_CERT_ARN=$API_CERT_ARN
EOF

echo ""
echo "Certificates issued:"
echo "  CF_CERT_ARN=$CF_CERT_ARN"
echo "  API_CERT_ARN=$API_CERT_ARN"
echo "Saved to $STATE_DIR/certs.env"
