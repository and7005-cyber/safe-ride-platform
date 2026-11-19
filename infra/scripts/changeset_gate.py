#!/usr/bin/env python3
"""Change-set gate for infra/scripts/deploy-backend.sh and deploy-frontend.sh
(hardening decisions D7 and E3).

`sam deploy --no-execute-changeset` (backend) and `aws cloudformation deploy
--no-execute-changeset` (frontend) only create the change set; the deploy
scripts (through the changeset_* helpers in config.sh) feed the AWS CLI's JSON
to this module and execute the change set only when `review` exits 0. System
python3 (3.9 on macOS), stdlib only. backend/tests/core/test_changeset_gate.py
runs it on table-driven payloads and runs both deploy scripts end to end
against fake `aws`, `sam`, `git` and `npm`.

  changeset_gate.py review [--allow-replace Id1,Id2]
      stdin: `aws cloudformation describe-change-set --output json`.
      Prints the change table on stdout, then WARN and ABORT lines on stderr.
      Exit 0 to execute, 2 to abort. It aborts when
        - the change set is not CREATE_COMPLETE / AVAILABLE;
        - the Database resource (backend stack) lists a MasterUserPassword
          change (the /saferide/db-password copy the deploy read does not
          match the live master password), whatever its Replacement value;
        - any resource has Replacement True, except
          * the stateless types in STATELESS_TYPES, recreated from the
            template with nothing to lose: a WARN line;
          * the logical ids named in --allow-replace (the deploys pass
            $ALLOW_REPLACE): a loud WARN block, for this deploy only. It never
            applies to the types in NEVER_REPLACEABLE_TYPES (a database, the
            CloudFront distribution, an S3 bucket), which always abort.
      Replacement Conditional prints a WARN line and passes.

  changeset_gate.py newest-since EPOCH [--prefix NAME_PREFIX]
      stdin: `aws cloudformation list-change-sets --output json`.
      Prints the id of the newest change set whose name starts with
      NAME_PREFIX (default samcli-deploy, SAM's; deploy-frontend.sh passes
      awscli-cloudformation-package-deploy-, the AWS CLI's) created at or
      after EPOCH minus CLOCK_SKEW_S seconds that is CREATE_COMPLETE and
      AVAILABLE, or nothing. Used when the deploy command's output did not
      name the change set.
"""

import argparse
import json
import re
import sys
from datetime import datetime

ABORT = 2
# Recreating one of these loses no data and no identity anything else holds:
# an email subscription is re-sent its confirmation, an alarm or a metric
# filter is rebuilt from the template.
STATELESS_TYPES = frozenset({
    "AWS::SNS::Subscription",
    "AWS::CloudWatch::Alarm",
    "AWS::Logs::MetricFilter",
})
DATABASE_ID = "Database"
# Never replaceable through ALLOW_REPLACE, type -> what the abort line calls it.
# A replaced database is a new, empty instance (DeletionPolicy only snapshots
# the old one; restores follow infra/RESTORE.md instead). A replaced
# distribution is a new *.cloudfront.net domain the alias records and the
# certificate would have to follow; a replaced bucket is a new, empty one.
NEVER_REPLACEABLE_TYPES = {
    "AWS::RDS::DBInstance": "a database",
    "AWS::CloudFront::Distribution": "a CloudFront distribution",
    "AWS::S3::Bucket": "an S3 bucket",
}
DEFAULT_NAME_PREFIX = "samcli-deploy"
CLOCK_SKEW_S = 5
BANNER = "!" * 76


def _changed_names(resource):
    names = []
    for detail in resource.get("Details") or []:
        target = detail.get("Target") or {}
        name = target.get("Name") if target.get("Attribute") == "Properties" else target.get("Attribute")
        if name and name not in names:
            names.append(name)
    return names


def _lists_master_password(resource):
    return any(
        "MasterUserPassword" in ((detail.get("Target") or {}).get("Name") or "")
        for detail in resource.get("Details") or []
    )


def review(change_set, allow_replace):
    """(rows, warnings, loud, aborts) for one describe-change-set payload."""
    rows, warnings, loud, aborts = [], [], [], []
    status, execution = change_set.get("Status"), change_set.get("ExecutionStatus")
    if status != "CREATE_COMPLETE" or execution != "AVAILABLE":
        aborts.append("change set is {}/{}: {}".format(status, execution, change_set.get("StatusReason", "")))
        return rows, warnings, loud, aborts

    replaced = set()
    for change in change_set.get("Changes") or []:
        resource = change.get("ResourceChange")
        if not resource:
            continue
        logical = resource.get("LogicalResourceId", "?")
        rtype = resource.get("ResourceType", "?")
        replacement = resource.get("Replacement") or "-"
        rows.append((resource.get("Action", "?"), logical, rtype, replacement,
                     ", ".join(_changed_names(resource)) or "-"))
        if logical == DATABASE_ID and _lists_master_password(resource):
            aborts.append("Database lists a MasterUserPassword change: SSM /saferide/db-password in this"
                          " region does not match the live master password")
        if replacement == "True":
            replaced.add(logical)
            if rtype in STATELESS_TYPES:
                warnings.append("{} ({}) will be replaced: a stateless type, recreated from the template".format(
                    logical, rtype))
            elif rtype in NEVER_REPLACEABLE_TYPES:
                aborts.append("{} ({}) would be REPLACED, and ALLOW_REPLACE never applies to {}".format(
                    logical, rtype, NEVER_REPLACEABLE_TYPES[rtype]))
            elif logical in allow_replace:
                loud.append("{} ({}) WILL BE REPLACED: allowed by ALLOW_REPLACE for this deploy".format(
                    logical, rtype))
            else:
                aborts.append("{} ({}) would be REPLACED".format(logical, rtype))
        elif replacement == "Conditional":
            warnings.append("{} ({}) may be replaced (Replacement: Conditional)".format(logical, rtype))

    for logical in sorted(set(allow_replace) - replaced):
        warnings.append("ALLOW_REPLACE names {}, which this change set does not replace".format(logical))
    return rows, warnings, loud, aborts


def print_table(rows, out):
    header = ("Action", "LogicalResourceId", "ResourceType", "Replacement", "Changed")
    widths = [max(len(row[i]) for row in rows + [header]) for i in range(4)]
    print(file=out)
    for row in [header] + rows:
        print("  " + "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row[:4])) + "  " + row[4], file=out)
    print(file=out)
    out.flush()  # the table before the verdict, even when both streams go to one log


def parse_allow_replace(raw):
    return {item.strip() for item in (raw or "").split(",") if item.strip()}


def _created(text):
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:  # fractional seconds this Python cannot parse
        return datetime.fromisoformat(re.sub(r"\.\d+", "", text)).timestamp()


def newest_since(listing, epoch, prefix=DEFAULT_NAME_PREFIX):
    since = epoch - CLOCK_SKEW_S
    best = None
    for summary in listing.get("Summaries") or []:
        if (summary.get("Status") != "CREATE_COMPLETE"
                or summary.get("ExecutionStatus") != "AVAILABLE"
                or not summary.get("ChangeSetName", "").startswith(prefix)):
            continue
        at = _created(summary["CreationTime"])
        if at >= since and (best is None or at > best[0]):
            best = (at, summary["ChangeSetId"])
    return best[1] if best else ""


def _read_stdin_json(what):
    try:
        return json.load(sys.stdin)
    except ValueError as error:
        print("ABORT: could not read the {} JSON: {}".format(what, error), file=sys.stderr)
        sys.exit(ABORT)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Change-set gate for deploy-backend.sh and deploy-frontend.sh")
    commands = parser.add_subparsers(dest="command", required=True)
    review_cmd = commands.add_parser("review", help="review describe-change-set JSON on stdin")
    review_cmd.add_argument("--allow-replace", default="",
                            help="comma-separated logical ids whose replacement is allowed (WARN)")
    newest_cmd = commands.add_parser("newest-since",
                                     help="newest executable change set by name prefix (list-change-sets JSON on stdin)")
    newest_cmd.add_argument("epoch", type=float)
    newest_cmd.add_argument("--prefix", default=DEFAULT_NAME_PREFIX,
                            help="change-set name prefix (default: %(default)s, SAM's)")
    args = parser.parse_args(argv)

    if args.command == "newest-since":
        print(newest_since(_read_stdin_json("list-change-sets"), args.epoch, args.prefix))
        return 0

    rows, warnings, loud, aborts = review(_read_stdin_json("describe-change-set"),
                                          parse_allow_replace(args.allow_replace))
    if rows or not aborts:
        print_table(rows, sys.stdout)
    for warning in warnings:
        print("WARN: " + warning, file=sys.stderr)
    for line in loud:
        print("\n".join([BANNER, "WARN: " + line, BANNER]), file=sys.stderr)
    for abort in aborts:
        print("ABORT: " + abort, file=sys.stderr)
    return ABORT if aborts else 0


if __name__ == "__main__":
    sys.exit(main())
