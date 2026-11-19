"""Fixed-indentation reading of the CloudFormation templates for the text tests.

The stack and live-parity tests pin decisions in infra/backend/template.yaml
and infra/frontend/template.yaml without a YAML library or CloudFormation tag
handling: top-level sections at column 0, Parameters and Resources entries at
two spaces, their keys at four, Properties at six. Each template is read and
split once per test session (`load`).

The resource split checks itself: every `    Type:` line of the Resources
section must land in exactly one resource block, so a logical id the split
cannot see fails loudly instead of folding into its neighbour and letting a
negative assertion ("X not in block") pass vacuously.
"""

import functools
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TEMPLATES = {
    "backend": REPO / "infra" / "backend" / "template.yaml",
    "frontend": REPO / "infra" / "frontend" / "template.yaml",
}


def unquote(text: str) -> str:
    """A one-line scalar without its surrounding quotes."""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _scalars(text: str, indent: int) -> dict[str, str]:
    """The `Key: value` lines at exactly this indentation (value as written)."""
    return dict(re.findall(rf"^{' ' * indent}(\w+): (.+?)\s*$", text, re.M))


class Template:
    def __init__(self, text: str):
        self.text = text
        resources = self.section("Resources")
        parts = re.split(r"^  (\w+):\n", resources, flags=re.M)
        self.resources: dict[str, str] = dict(zip(parts[1::2], parts[2::2]))  # id -> block
        self._check_split(parts[0], resources)

    def _check_split(self, preamble: str, resources: str) -> None:
        assert not re.search(r"^    Type: ", preamble, re.M), "a Type line sits above the first resource"
        for logical_id, block in self.resources.items():
            count = len(re.findall(r"^    Type: ", block, re.M))
            assert count == 1, f"{logical_id} holds {count} Type lines: the resource split missed an entry"
        assert len(re.findall(r"^    Type: ", resources, re.M)) == len(self.resources)

    def section(self, name: str) -> str:
        """The text under a top-level key, up to the next one."""
        match = re.search(rf"^{name}:\n(.*?)(?=^[^\s#]|\Z)", self.text, re.M | re.S)
        assert match, f"template has no {name} section"
        return match.group(1)

    def parameter(self, name: str) -> dict[str, str]:
        """The `Key: value` lines of one Parameters entry."""
        match = re.search(rf"^  {name}:\n((?:    .*\n)+)", self.section("Parameters"), re.M)
        assert match, f"template Parameter {name} missing"
        return _scalars(match.group(1), 4)

    def block(self, logical_id: str) -> str:
        assert logical_id in self.resources, f"no resource {logical_id}"
        return self.resources[logical_id]

    def type_of(self, logical_id: str) -> str:
        return re.search(r"^    Type: (\S+)$", self.block(logical_id), re.M).group(1)

    def of_type(self, resource_type: str) -> list[str]:
        return [lid for lid in self.resources if self.type_of(lid) == resource_type]

    def properties(self, logical_id: str) -> dict[str, str]:
        """The scalar `Key: value` lines directly under Properties."""
        return _scalars(self.block(logical_id), 6)

    def function_env(self, logical_id: str) -> dict[str, str]:
        """The Environment.Variables of one function resource, as text pairs."""
        variables = re.search(
            r"^      Environment:\n        Variables:\n((?:          .*\n)+)",
            self.block(logical_id),
            re.M,
        )
        assert variables, f"{logical_id} has no Environment.Variables block"
        return _scalars(variables.group(1), 10)


@functools.cache
def load(stack: str) -> Template:
    """The "backend" or "frontend" template, read and split once."""
    return Template(TEMPLATES[stack].read_text(encoding="utf-8"))
