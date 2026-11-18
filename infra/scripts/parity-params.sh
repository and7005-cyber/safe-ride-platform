#!/usr/bin/env bash
# Live-parity parameters of the backend and frontend stacks, sourced by
# deploy-backend.sh and deploy-frontend.sh (infra/README.md, "Deploy safety").
# backend/tests/core/test_deploy_params.py runs build_parity_params on its own.
#
# The rule: the live stack runs each parameter's template Default unless the
# operator exports an override for this deploy. SAM CLI and `aws
# cloudformation deploy` both send UsePreviousValue for every parameter a
# deploy does not pass, so on an existing stack a changed template Default
# never reached it and a one-off export stuck until another export.
# build_parity_params therefore passes every listed parameter that has a
# non-empty template Default on EVERY deploy: the exported variable when it is
# set and non-empty, otherwise the Default read from the template. A parameter
# whose Default is empty (the alert addresses, MailReplyTo) is passed only when
# its variable is set, because SAM refuses an empty override: its previous
# value stays until a new one is exported, and no deploy can clear it.
#
# Each pair is TemplateParameter:ENV_NAME.

# Backend stack (infra/backend/template.yaml), passed by deploy-backend.sh.
PARITY_PAIRS=(
  # GPS tracking system defaults (GPS plan U11), under the names the app's
  # Settings read. Schools override the first five per school in School
  # Settings; the last two are system-wide.
  GpsCustodyThresholdM:GPS_CUSTODY_THRESHOLD_M
  GpsVicinityRadiusM:GPS_VICINITY_RADIUS_M
  GpsFixAccuracyCapM:GPS_FIX_ACCURACY_CAP_M
  GpsPositionRetentionDays:GPS_POSITION_RETENTION_DAYS
  GpsPingIntervalS:GPS_PING_INTERVAL_S
  GpsStaleAfterS:GPS_STALE_AFTER_S
  GpsFixWaitBudgetS:GPS_FIX_WAIT_BUDGET_S
  # API-stage throttling (GPS plan U14; sign-in and school lookup, hardening
  # D6). These are API Gateway settings, not app Settings — no GPS_ prefix,
  # and the app never reads them.
  ApiDefaultBurstLimit:API_DEFAULT_BURST_LIMIT
  ApiDefaultRateLimit:API_DEFAULT_RATE_LIMIT
  PingsRouteBurstLimit:API_PINGS_BURST_LIMIT
  PingsRouteRateLimit:API_PINGS_RATE_LIMIT
  PinLoginRouteBurstLimit:API_PIN_LOGIN_BURST_LIMIT
  PinLoginRouteRateLimit:API_PIN_LOGIN_RATE_LIMIT
  SchoolLookupRouteBurstLimit:API_SCHOOL_LOOKUP_BURST_LIMIT
  SchoolLookupRouteRateLimit:API_SCHOOL_LOOKUP_RATE_LIMIT
  # Transactional email settings (hardening U4) under the names the app's
  # Settings read. Release 1b's sender reads them; MAIL_ENABLED stays false
  # until then. MailConfigurationSet names a create-only resource, so the
  # change-set gate refuses a rename (replacement). MailReplyTo has an empty
  # Default.
  MailFromAddress:MAIL_FROM_ADDRESS
  MailReplyTo:MAIL_REPLY_TO
  MailConfigurationSet:MAIL_CONFIGURATION_SET
  MailEnabled:MAIL_ENABLED
  # Alarm recipients and the concurrency alarm (hardening U4, D16, D19). Each
  # address set becomes one email subscription to the ops topic, confirmed
  # from that inbox; both have empty Defaults, so export them on the first
  # deploy that should alert and they stay (alert_address_guard below refuses
  # a deploy that would leave no primary address). ConcurrencyAlarmThreshold (Default
  # 8, 80% of the account's 10) follows the full rule: export
  # CONCURRENCY_ALARM_THRESHOLD on every deploy once the quota is raised,
  # until a release raises the Default.
  AlertEmailPrimary:ALERT_EMAIL_PRIMARY
  AlertEmailSecondary:ALERT_EMAIL_SECONDARY
  ConcurrencyAlarmThreshold:CONCURRENCY_ALARM_THRESHOLD
)

# Frontend stack (infra/frontend/template.yaml), passed by deploy-frontend.sh.
# DOMAIN and HOSTED_ZONE_ID come from config.sh; deploy-frontend.sh sets
# API_ORIGIN from the URL the build calls and CSP_MODE (report-only unless
# exported) after validating it. MonthlyBudgetUsd follows the full rule:
# export MONTHLY_BUDGET_USD on every deploy, or change the Default in a
# release. The alert addresses are the backend's, passed when exported.
# CertificateArn is not here: deploy-frontend.sh passes it only when a cert
# ARN is known, like the backend's ApiCertificateArn.
FRONTEND_PARAMS=(
  DomainName:DOMAIN
  HostedZoneId:HOSTED_ZONE_ID
  ApiOrigin:API_ORIGIN
  CspMode:CSP_MODE
  MonthlyBudgetUsd:MONTHLY_BUDGET_USD
  AlertEmailPrimary:ALERT_EMAIL_PRIMARY
  AlertEmailSecondary:ALERT_EMAIL_SECONDARY
)

# template_parameter_defaults template name... -> one "Name=Default" line per
# name, the Default unquoted ("Name=" when it is empty). Fails, naming it, when
# a name is not in the template's Parameters section or has no Default line.
# Fixed-indentation text reading, like the parity tests
# (backend/tests/core/test_gps_settings.py): system python3, no YAML library,
# no CloudFormation tag handling. A Default must be a plain or quoted one-line
# scalar ("" for empty); anything else stops the deploy.
template_parameter_defaults() {
  python3 - "$@" <<'PY'
import re
import sys

path, names = sys.argv[1], sys.argv[2:]
with open(path, encoding="utf-8") as handle:
    text = handle.read()
section = re.search(r"^Parameters:\n(.*?)(?=^\S|\Z)", text, re.M | re.S)
if not section:
    sys.exit("ERROR: {} has no Parameters section".format(path))
body = section.group(1)


def scalar(raw, name):
    """A plain or quoted one-line scalar. Anything else — an empty or null
    value, a value on the next line, a block, flow, tag, anchor or alias —
    stops the deploy: write the value on the Default line, "" for empty."""
    def refuse():
        sys.exit("ERROR: template parameter {} has a Default this reader cannot parse: {!r}"
                 " (write a plain one-line value, or \"\" for empty)".format(name, raw))
    if raw in ("", "~", "null", "Null", "NULL") or raw[0] in ">|[{!&*%@`#":
        refuse()
    if raw[0] == '"':
        if len(raw) < 2 or raw[-1] != '"' or '"' in raw[1:-1] or "\\" in raw:
            refuse()
        return raw[1:-1]
    if raw[0] == "'":
        inner = raw[1:-1]
        if len(raw) < 2 or raw[-1] != "'" or "'" in inner.replace("''", ""):
            refuse()
        return inner.replace("''", "'")
    return re.sub(r"\s+#.*$", "", raw)


for name in names:
    entry = re.search(r"^  " + re.escape(name) + r":\n((?:    .*\n)+)", body, re.M)
    if not entry:
        sys.exit("ERROR: template parameter {} is not in {}".format(name, path))
    default = re.search(r"^    Default:[ \t]*(.*?)[ \t]*$", entry.group(1), re.M)
    if not default:
        sys.exit("ERROR: template parameter {} has no Default in {}".format(name, path))
    print("{}={}".format(name, scalar(default.group(1), name)))
PY
}

# build_parity_params template pair... -> appends "Name=value" for every
# TemplateParameter:ENV_NAME pair to the caller's DEPLOY_PARAMS array, by the
# rule at the top, and says on stdout which values were exported and which are
# template Defaults. Returns non-zero (nothing appended) when no pair is given
# or the template cannot be read.
#   build_parity_params "$INFRA_DIR/backend/template.yaml" "${PARITY_PAIRS[@]}"
#   build_parity_params "$INFRA_DIR/frontend/template.yaml" "${FRONTEND_PARAMS[@]}"
build_parity_params() {
  local template="${1:-}" defaults pair param var default line
  local names=() at_default=()
  if [ "$#" -lt 2 ]; then
    echo "ERROR: build_parity_params needs a template and at least one Parameter:ENV pair" >&2
    return 1
  fi
  shift
  for pair in "$@"; do
    names+=("${pair%%:*}")
  done
  defaults="$(template_parameter_defaults "$template" "${names[@]}")" || return 1
  for pair in "$@"; do
    param="${pair%%:*}"; var="${pair##*:}"
    default=""
    while IFS= read -r line; do
      case "$line" in
        "${param}="*) default="${line#*=}"; break ;;
      esac
    done <<EOF
$defaults
EOF
    if [ -n "${!var:-}" ]; then
      DEPLOY_PARAMS+=("${param}=${!var}")
      echo "==> Parameter override: ${param}=${!var} (from \$${var})"
    elif [ -n "$default" ]; then
      DEPLOY_PARAMS+=("${param}=${default}")
      at_default+=("${param}=${default}")
    fi
  done
  if [ "${#at_default[@]}" -gt 0 ]; then
    echo "==> Live-parity parameters at their template Default: ${at_default[*]}"
  fi
}

# alert_address_guard stack region (hardening E1) -> 0 when this deploy leaves
# the stack with a primary alert address, or ALLOW_NO_ALERTS=1 waives it (loud
# warning); 1, saying why on stderr, otherwise. Both deploys call it after
# build_parity_params and before anything is built; it needs cfn_parameter
# (config.sh). The address is the exported ALERT_EMAIL_PRIMARY (which
# build_parity_params passes), else the value the stack holds now, which is
# then appended to DEPLOY_PARAMS explicitly. SAM and `aws cloudformation
# deploy` keep a previous value only for a parameter the stack already has:
# after a rollback to a template without AlertEmailPrimary (release/0) the
# stack holds none, and the empty Default would deploy alarms that email no
# one. AlertEmailSecondary keeps the previous-value rule.
alert_address_guard() {
  local stack="$1" region="$2" held
  if [ -n "${ALERT_EMAIL_PRIMARY:-}" ]; then
    return 0
  fi
  held="$(cfn_parameter "$stack" "$region" AlertEmailPrimary)"
  if [ -n "$held" ]; then
    DEPLOY_PARAMS+=("AlertEmailPrimary=${held}")
    echo "==> Alert address kept: AlertEmailPrimary=${held} (held by ${stack}; export ALERT_EMAIL_PRIMARY to change it)"
    return 0
  fi
  if [ "${ALLOW_NO_ALERTS:-}" = "1" ]; then
    {
      echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
      echo "WARNING: ALLOW_NO_ALERTS=1 — deploying ${stack} (${region}) with no alert address:"
      echo "its alarms email no one until a deploy exports ALERT_EMAIL_PRIMARY."
      echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    } >&2
    return 0
  fi
  echo "ERROR: no alert address: alarms would email no one — export ALERT_EMAIL_PRIMARY." >&2
  echo "       Stack ${stack} (${region}) holds no AlertEmailPrimary (a first deploy, or after a rollback);" >&2
  echo "       ALLOW_NO_ALERTS=1 deploys without one." >&2
  return 1
}
