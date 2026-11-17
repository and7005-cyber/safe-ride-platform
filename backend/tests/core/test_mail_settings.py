"""Transactional email settings (hardening plan U4) — live parity.

Release 1a wires the mail settings end to end so Release 1b's sender reads
values someone set: each Settings field is a template Parameter whose Default
equals the Settings default, lands in the API function's environment under
the alias Settings reads, is passable from deploy-backend.sh under that same
name, and is documented in backend/.env.example. The live-parity rule of
test_gps_settings.py, for string parameters. No code sends mail yet, so
MAIL_ENABLED is false everywhere. Pure text inspection of the template.
"""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings

REPO = Path(__file__).resolve().parents[3]
TEMPLATE = (REPO / "infra" / "backend" / "template.yaml").read_text(encoding="utf-8")
# The parity maps deploy-backend.sh sources (test_deploy_params.py runs them).
PARITY_SCRIPT = (REPO / "infra" / "scripts" / "parity-params.sh").read_text(encoding="utf-8")
ENV_EXAMPLE = (REPO / "backend" / ".env.example").read_text(encoding="utf-8")

# Settings field -> (env alias, template parameter, default as text)
MAIL_FIELDS = {
    "mail_from_address": ("MAIL_FROM_ADDRESS", "MailFromAddress", "no-reply@saferidelive.co.ke"),
    "mail_reply_to": ("MAIL_REPLY_TO", "MailReplyTo", ""),
    "mail_configuration_set": (
        "MAIL_CONFIGURATION_SET", "MailConfigurationSet", "saferide-transactional",
    ),
    "mail_enabled": ("MAIL_ENABLED", "MailEnabled", "false"),
}
# The env value is the parameter itself, except the configuration set: the API
# names the SesConfigSet resource, whose Ref is its name (MailConfigurationSet).
ENV_VALUES = {
    "MAIL_FROM_ADDRESS": "!Ref MailFromAddress",
    "MAIL_REPLY_TO": "!Ref MailReplyTo",
    "MAIL_CONFIGURATION_SET": "!Ref SesConfigSet",
    "MAIL_ENABLED": "!Ref MailEnabled",
}


def _settings(**env) -> Settings:
    # No dotenv: the repo's backend/.env must not leak into these assertions.
    return Settings(_env_file=None, **env)


def _as_template_text(value) -> str:
    """A Settings value as the template's String Default spells it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _unquote(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _template_parameter(name: str) -> dict[str, str]:
    """The `Key: value` lines of one Parameters entry, by indentation."""
    match = re.search(rf"^  {name}:\n((?:    .*\n)+)", TEMPLATE, re.M)
    assert match, f"template Parameter {name} missing"
    return dict(re.findall(r"^    (\w+): (.+?)\s*$", match.group(1), re.M))


def _resource_block(logical_id: str) -> str:
    start = TEMPLATE.index(f"\n  {logical_id}:\n")
    tail = TEMPLATE[start + 1:]
    end = re.search(r"^  \w+:\n", tail[1:], re.M)
    return tail[: end.start() + 1] if end else tail


def _function_env(logical_id: str) -> dict[str, str]:
    variables = re.search(
        r"^      Environment:\n        Variables:\n((?:          .*\n)+)",
        _resource_block(logical_id),
        re.M,
    )
    assert variables, f"{logical_id} has no Environment.Variables block"
    return dict(re.findall(r"^          (\w+): (.+?)\s*$", variables.group(1), re.M))


# --- Settings fields -----------------------------------------------------------


def test_defaults_are_the_release_1a_values():
    s = _settings()
    assert s.mail_from_address == "no-reply@saferidelive.co.ke"
    assert s.mail_reply_to == ""
    assert s.mail_configuration_set == "saferide-transactional"
    assert s.mail_enabled is False


@pytest.mark.parametrize(
    "field,alias", [(f, v[0]) for f, v in MAIL_FIELDS.items() if f != "mail_enabled"]
)
def test_each_string_field_is_overridable_from_its_env_alias(field, alias):
    assert getattr(_settings(**{alias: "x@example.org"}), field) == "x@example.org"


def test_mail_enabled_reads_the_strings_the_template_passes():
    assert _settings(MAIL_ENABLED="true").mail_enabled is True
    assert _settings(MAIL_ENABLED="false").mail_enabled is False
    with pytest.raises(ValidationError):
        _settings(MAIL_ENABLED="maybe")


# --- live parity ----------------------------------------------------------------


@pytest.mark.parametrize("field,alias,param,default", [(f, *v) for f, v in MAIL_FIELDS.items()])
def test_template_parameter_default_equals_settings_default(field, alias, param, default):
    entry = _template_parameter(param)
    assert entry["Type"] == "String"
    assert _unquote(entry["Default"]) == default == _as_template_text(getattr(_settings(), field))


def test_mail_enabled_parameter_allows_only_true_or_false():
    assert _template_parameter("MailEnabled")["AllowedValues"] == '["true", "false"]'


@pytest.mark.parametrize("alias,expected", ENV_VALUES.items())
def test_api_function_env_carries_every_alias(alias, expected):
    assert _function_env("ApiFunction")[alias] == expected


def test_the_configuration_set_is_named_by_the_parameter():
    """The env names the resource; the resource is named by the parameter,
    so the three can never disagree."""
    block = _resource_block("SesConfigSet")
    assert re.search(r"^    Type: AWS::SES::ConfigurationSet$", block, re.M)
    assert re.search(r"^      Name: !Ref MailConfigurationSet$", block, re.M)


@pytest.mark.parametrize("alias,param", [(v[0], v[1]) for v in MAIL_FIELDS.values()])
def test_deploy_script_maps_each_alias_to_its_parameter(alias, param):
    assert f"{param}:{alias}" in PARITY_SCRIPT
    # And the loop actually iterates the array the pair lives in.
    loop = r'^\s*for pair in "\$\{GPS_PARAMS\[@\]\}".*"\$\{MAIL_PARAMS\[@\]\}"'
    assert re.search(loop, PARITY_SCRIPT, re.M)


@pytest.mark.parametrize("alias,default", [(v[0], v[2]) for v in MAIL_FIELDS.values()])
def test_env_example_documents_each_alias_with_its_default(alias, default):
    assert re.search(rf"^# {alias}={re.escape(default)}$", ENV_EXAMPLE, re.M), alias


def test_send_permission_is_scoped_to_the_identity_the_set_and_the_from_address():
    block = _resource_block("ApiFunction")
    grant = re.search(
        r"^            - Effect: Allow\n              Action: ses:SendEmail\n((?:              .*\n)+)",
        block,
        re.M,
    )
    assert grant, "ApiFunction has no ses:SendEmail statement"
    body = grant.group(1)
    assert ":identity/${SesIdentity}" in body
    assert ":configuration-set/${SesConfigSet}" in body
    assert "ses:FromAddress: !Ref MailFromAddress" in body
    assert TEMPLATE.count("Action: ses:") == 1  # no other function or action can send
