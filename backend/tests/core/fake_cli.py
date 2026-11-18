"""Fake `aws`, `sam` and `git` for the deploy-script tests — no AWS call, build
or deploy ever runs (the FakeAws pattern of test_copy_ssm_script.py).

FakeCli copies the scripts under test into a private repository layout
(tmp/repo: infra/scripts, both templates, scripts/provider-bootstrap.sh and
empty backend/app, backend/db/{migrations,seeds}), so STATE_DIR, backend/.env
and every path a script derives from its own location are the test's own.
Stubs named aws, sam and git go first on PATH. Each reads the JSON "world"
(FakeCli.world, saved before every run) and appends its argv, tagged with the
tool name, to one call log, so a test can assert what ran and in which order.

World keys (all optional):
  default_region  region of an aws call without --region (the CLI config);
                  AWS_REGION / AWS_DEFAULT_REGION win, as in the real CLI
  ssm             {region: {name: value | {"error": "AccessDeniedException"}}}
  stack           {"status": ..., "outputs": {...}}; absent = no such stack
  change_set      the describe-change-set answer
  change_sets     the list-change-sets answer
  lambda          {function name: {"payload": {...}, "function_error": str|None}}
  wait_fails      true: `aws cloudformation wait` fails
  sam_deploy      "changeset" (default) | "no-changes" | "unnamed"
  git             {"head", "origin_main", "dirty", "staged", "tags"}
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
# The operator's shell on macOS is /bin/bash 3.2; pin it where it exists.
BASH = "/bin/bash" if Path("/bin/bash").exists() else shutil.which("bash")
CHANGESET_ARN = (
    "arn:aws:cloudformation:af-south-1:000000000000:changeSet/samcli-deploy1763460000/3f2a5b7c"
)
HEAD = "0123456789abcdef0123456789abcdef01234567"

_COMMON = r'''
import json, os, re, stat, sys

root = os.environ["FAKE_CLI_ROOT"]
args = sys.argv[1:]
world_path = os.path.join(root, "world.json")
with open(world_path, encoding="utf-8") as handle:
    world = json.load(handle)


def log(tool):
    with open(os.path.join(root, "calls.jsonl"), "a", encoding="utf-8") as handle:
        handle.write(json.dumps([tool] + args) + "\n")


def save():
    with open(world_path, "w", encoding="utf-8") as handle:
        json.dump(world, handle)


def opt(flag):
    return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else None


def fail(code, error, operation, message=""):
    sys.stderr.write("\nAn error occurred (%s) when calling the %s operation: %s\n" % (error, operation, message))
    sys.exit(code)
'''

AWS_STUB = "#!/usr/bin/env python3\n" + _COMMON + r'''
log("aws")
VALUE_FLAGS = {"--region", "--function-name", "--cli-read-timeout", "--cli-binary-format", "--payload",
               "--query", "--output", "--name", "--stack-name", "--change-set-name", "--max-items"}


def region():
    found = (opt("--region") or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
             or world.get("default_region"))
    if not found:
        sys.stderr.write("You must specify a region. You can also configure your region by running \"aws configure\".\n")
        sys.exit(253)
    return found


def positional():
    rest, skip = [], False
    for arg in args[2:]:
        if skip:
            skip = False
        elif arg in VALUE_FLAGS:
            skip = True
        elif not arg.startswith("--"):
            rest.append(arg)
    return rest


if args[:2] == ["ssm", "get-parameter"]:
    name, where = opt("--name"), region()
    entry = world.get("ssm", {}).get(where, {}).get(name)
    if entry is None:
        fail(254, "ParameterNotFound", "GetParameter")
    if isinstance(entry, dict):
        fail(254, entry["error"], "GetParameter", "not authorized to perform ssm:GetParameter")
    if opt("--query") == "Parameter.Value":
        print(entry)
    elif opt("--query") == "Parameter.Name":
        print(name)
    else:
        json.dump({"Parameter": {"Name": name, "Type": "SecureString", "Value": entry}}, sys.stdout)
    sys.exit(0)

if args[:2] == ["ssm", "put-parameter"]:
    where, source = region(), opt("--cli-input-json")
    if source:
        path = source[len("file://"):]
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        record = {"via": "file", "keys": sorted(data), "file": path,
                  "file_mode": stat.S_IMODE(os.stat(path).st_mode),
                  "dir_mode": stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode)}
    else:
        data = {"Name": opt("--name"), "Value": opt("--value"), "Type": opt("--type"),
                "Overwrite": "--overwrite" in args}
        record = {"via": "argv"}
    record.update(region=where, name=data["Name"], type=data.get("Type"))
    with open(os.path.join(root, "puts.jsonl"), "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    store = world.setdefault("ssm", {}).setdefault(where, {})
    if data["Name"] in store and not data.get("Overwrite"):
        fail(254, "ParameterAlreadyExists", "PutParameter", "The parameter already exists.")
    store[data["Name"]] = data["Value"]
    save()
    print(json.dumps({"Version": 1, "Tier": "Standard"}))
    sys.exit(0)

if args[:2] == ["cloudformation", "describe-stacks"]:
    stack = world.get("stack")
    if stack is None:
        fail(254, "ValidationError", "DescribeStacks", "Stack with id %s does not exist" % opt("--stack-name"))
    query = opt("--query") or ""
    output = re.search(r"OutputKey=='(\w+)'", query)
    if output:
        print(stack.get("outputs", {}).get(output.group(1), ""))
    elif query == "Stacks[0].StackStatus":
        print(stack["status"])
    sys.exit(0)

if args[:2] == ["cloudformation", "describe-change-set"]:
    json.dump(world["change_set"], sys.stdout)
    sys.exit(0)

if args[:2] == ["cloudformation", "list-change-sets"]:
    json.dump(world.get("change_sets", {"Summaries": []}), sys.stdout)
    sys.exit(0)

if args[:2] in (["cloudformation", "delete-change-set"], ["cloudformation", "execute-change-set"],
                ["cloudformation", "describe-stack-events"]):
    sys.exit(0)

if args[:2] == ["cloudformation", "wait"]:
    sys.exit(255 if world.get("wait_fails") else 0)

if args[:2] == ["cloudformation", "deploy"]:
    print("Successfully created/updated stack - %s" % opt("--stack-name"))
    sys.exit(0)

if args[:2] in (["s3", "sync"], ["s3", "cp"]):
    sys.exit(0)

if args[:2] == ["cloudfront", "create-invalidation"]:
    print("I2FAKEINVALIDATION")
    sys.exit(0)

if args[:2] == ["lambda", "invoke"]:
    function = opt("--function-name")
    answer = world.get("lambda", {}).get(function)
    if answer is None:
        fail(254, "ResourceNotFoundException", "Invoke", "Function not found: %s" % function)
    with open(positional()[0], "w", encoding="utf-8") as handle:
        json.dump(answer["payload"], handle)
    print(answer.get("function_error") or "None")
    sys.exit(0)

sys.stderr.write("fake aws: unsupported call %r\n" % (args,))
sys.exit(2)
'''

SAM_STUB = "#!/usr/bin/env python3\n" + _COMMON + r'''
log("sam")
if args[:1] == ["build"]:
    print("Build Succeeded")
    sys.exit(0)
if args[:1] == ["deploy"]:
    mode = world.get("sam_deploy", "changeset")
    print("\n\tDeploying with following values\n\t===============================")
    if mode == "no-changes":
        print("No changes to deploy. Stack saferide-backend is up to date")
    elif mode == "unnamed":
        print("Waiting for changeset to be created..")
    else:
        print("Changeset created successfully. %s\n" % os.environ["FAKE_CHANGESET_ARN"])
    sys.exit(0)
sys.stderr.write("fake sam: unsupported call %r\n" % (args,))
sys.exit(2)
'''

GIT_STUB = "#!/usr/bin/env python3\n" + _COMMON + r'''
log("git")
while args[:1] == ["-C"]:
    args = args[2:]
git = world.get("git", {})
head = git.get("head", os.environ["FAKE_HEAD"])
if args[:1] == ["fetch"]:
    sys.exit(0)
if args[:2] == ["diff", "--quiet"]:
    sys.exit(1 if git.get("dirty") else 0)
if args[:3] == ["diff", "--cached", "--quiet"]:
    sys.exit(1 if git.get("staged") else 0)
if args[:2] == ["rev-parse", "--short"]:
    print(head[:7])
    sys.exit(0)
if args[:1] == ["rev-parse"]:
    print(git.get("origin_main", head) if args[1] == "origin/main" else head)
    sys.exit(0)
if args[:1] == ["tag"]:
    for tag in git.get("tags", []):
        print(tag)
    sys.exit(0)
if args[:1] in (["ls-files"], ["status"]):
    sys.exit(0)
if args[:1] == ["symbolic-ref"]:
    print("feat/x")
    sys.exit(0)
sys.stderr.write("fake git: unsupported call %r\n" % (args,))
sys.exit(2)
'''

NPM_STUB = "#!/usr/bin/env python3\n" + _COMMON + r'''
log("npm")
sys.exit(0)
'''

# The scripts each test may run, copied at their repository paths.
_TREE = [
    "infra/scripts/config.sh",
    "infra/scripts/parity-params.sh",
    "infra/scripts/deploy-backend.sh",
    "infra/scripts/deploy-frontend.sh",
    "infra/scripts/verify-db.sh",
    "infra/backend/template.yaml",
    "infra/frontend/template.yaml",
    "scripts/provider-bootstrap.sh",
]


class FakeCli:
    def __init__(self, tmp_path: Path, stubs=("aws", "sam", "git", "npm")):
        self.root = tmp_path / "cli"
        self.bin = tmp_path / "bin"
        self.tmpdir = tmp_path / "tmp"
        self.repo = tmp_path / "repo"
        for directory in (self.root, self.bin, self.tmpdir):
            directory.mkdir()
        sources = {"aws": AWS_STUB, "sam": SAM_STUB, "git": GIT_STUB, "npm": NPM_STUB}
        for name in stubs:
            stub = self.bin / name
            stub.write_text(sources[name], encoding="utf-8")
            stub.chmod(0o755)
        for rel in _TREE:
            target = self.repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / rel, target)
        for rel in ("backend/app", "backend/db/migrations", "backend/db/seeds", "frontend"):
            (self.repo / rel).mkdir(parents=True, exist_ok=True)
        self.world: dict = {"default_region": "eu-central-1", "ssm": {}}

    # --- world helpers -------------------------------------------------------
    def put(self, region: str, name: str, value) -> None:
        self.world.setdefault("ssm", {}).setdefault(region, {})[name] = value

    def deny(self, region: str, name: str, error: str = "AccessDeniedException") -> None:
        self.put(region, name, {"error": error})

    def ssm_value(self, region: str, name: str):
        return self.world.get("ssm", {}).get(region, {}).get(name)

    def calls(self, tool: str | None = None) -> list[list[str]]:
        """Every call as [tool, *argv] in order; with a tool, that tool's argvs."""
        log = self.root / "calls.jsonl"
        if not log.exists():
            return []
        entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        if tool is None:
            return entries
        return [entry[1:] for entry in entries if entry[0] == tool]

    def puts(self) -> list[dict]:
        log = self.root / "puts.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    # --- running ---------------------------------------------------------------
    def env(self, **extra: str) -> dict[str, str]:
        """A minimal environment: nothing from the test runner's shell (no
        AWS_*, no exported parity variable) leaks into the script."""
        env = {
            "PATH": f"{self.bin}{os.pathsep}/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(self.root),
            "TMPDIR": str(self.tmpdir),
            "LANG": "en_US.UTF-8",
            "FAKE_CLI_ROOT": str(self.root),
            "FAKE_CHANGESET_ARN": CHANGESET_ARN,
            "FAKE_HEAD": HEAD,
        }
        env.update(extra)
        return env

    def run(self, script: str, *args: str, stdin: str | None = None, **env: str):
        (self.root / "world.json").write_text(json.dumps(self.world), encoding="utf-8")
        result = subprocess.run(
            [BASH, str(self.repo / script), *args], env=self.env(**env), input=stdin,
            capture_output=True, text=True, timeout=120,
        )
        self.world = json.loads((self.root / "world.json").read_text(encoding="utf-8"))
        return result

    def bash(self, script: str, *args: str, **env: str):
        """Run a bash snippet (for sourcing a script's functions)."""
        (self.root / "world.json").write_text(json.dumps(self.world), encoding="utf-8")
        result = subprocess.run(
            [BASH, "-c", script, "bash", *args], env=self.env(**env),
            capture_output=True, text=True, timeout=60,
        )
        self.world = json.loads((self.root / "world.json").read_text(encoding="utf-8"))
        return result


# --- shared worlds ---------------------------------------------------------------
BACKEND_REGION = "af-south-1"
VERIFY_FN = "saferide-verify"
MIGRATE_FN = "saferide-migrate"
# The values deploy-backend.sh requires, chosen to break naive quoting.
SECRETS = {
    "/saferide/db-password": "db-Pa55 'single' \"double\" $HOME `id` 0001",
    "/saferide/pin-pepper": "pin-pepper-0002",
    "/saferide/db-app-password": "app-role-password-0003",
    "/saferide/totp-pepper": "totp-pepper-$(echo nope)-0004",
    "/saferide/google-maps-api-key": "AIzaFAKEKEYFORTESTS0005",
}
# base64url of {"version": 2, "providers": [{"email": "ops@example.org"}]}, unpadded.
BOOTSTRAP = "eyJ2ZXJzaW9uIjogMiwgInByb3ZpZGVycyI6IFt7ImVtYWlsIjogIm9wc0BleGFtcGxlLm9yZyJ9XX0"


def resource_change(logical, rtype, action="Modify", replacement="False", names=()):
    """One describe-change-set Changes[] entry (Replacement None: an Add row)."""
    resource = {
        "Action": action, "LogicalResourceId": logical, "ResourceType": rtype,
        "Details": [
            {"Target": {"Attribute": "Properties", "Name": name, "RequiresRecreation": "Never"},
             "Evaluation": "Static", "ChangeSource": "DirectModification"}
            for name in names
        ],
    }
    if replacement is not None:
        resource["Replacement"] = replacement
    return {"Type": "Resource", "ResourceChange": resource}


def change_set(*changes, status="CREATE_COMPLETE", execution="AVAILABLE", reason=""):
    payload = {"ChangeSetId": CHANGESET_ARN, "Status": status, "ExecutionStatus": execution,
               "Changes": list(changes)}
    if reason:
        payload["StatusReason"] = reason
    return payload


def verify_answer(results, status="ok", function_error=None, **extra):
    return {"payload": {"status": status, **extra, "results": results}, "function_error": function_error}


def preflight(open_runs):
    """The tenancy-preflight answer of the deployed verify Lambda."""
    return verify_answer([
        {"label": "school-rows", "rows": [{"id": "5cae0000-0000-0000-0000-000000000001", "name": "School"}]},
        {"label": "open-runs", "rows": [{"open_runs": open_runs}]},
    ], checks="tenancy-preflight")


def backend_deploy_world(fake: FakeCli, open_runs=0) -> None:
    """Everything deploy-backend.sh needs for a clean, gated, migrated deploy."""
    for name, value in SECRETS.items():
        fake.put(BACKEND_REGION, name, value)
    fake.put(BACKEND_REGION, "/saferide/provider-bootstrap", BOOTSTRAP)
    fake.world.update({
        "stack": {"status": "UPDATE_COMPLETE", "outputs": {
            "VerifyFunctionName": VERIFY_FN, "MigrateFunctionName": MIGRATE_FN,
            "HttpApiUrl": "https://abc123.execute-api.af-south-1.amazonaws.com",
            "DbEndpoint": "db.example.internal", "ApiCustomDomainUrl": "None",
        }},
        "change_set": change_set(
            resource_change("Database", "AWS::RDS::DBInstance", names=["BackupRetentionPeriod"]),
            resource_change("OpsTopic", "AWS::SNS::Topic", action="Add", replacement=None),
        ),
        "lambda": {
            VERIFY_FN: preflight(open_runs),
            MIGRATE_FN: {"payload": {"migrations": {"applied": []}}, "function_error": None},
        },
    })
