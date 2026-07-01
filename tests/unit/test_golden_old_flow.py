"""Golden cross-app conformance test for the shared .flow.json contract.

INVARIANT (mirrors the FlowRunner UI repo's __tests__/goldenOldFlow.test.js):
a real pre-sprint flow MUST parse to an IDENTICAL execution model whether it
carries no ``schemaVersion`` at all, ``"1.0"``, or an unknown MINOR such as
``"1.5"``. Absence of ``schemaVersion`` means ``"1.0"``. This is the guard that
keeps the 24/7 CLI alive when it meets a slightly-newer file: an additive MINOR
bump must never change how an old flow executes, and must never be rejected.

These tests are written to pass on the *current* parser (before the version gate
lands) and must keep passing after it — old flows are never rejected.
"""

import copy
import json
import os

import pytest

from flow_runner import FlowMap


FIXTURE = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "golden_old_flow.json"
)


def _load_raw():
    with open(FIXTURE, "r", encoding="utf-8") as f:
        return json.load(f)


def _execution_model(flowmap: FlowMap) -> dict:
    """A normalized dump of the parsed model that reflects execution semantics.

    ``schemaVersion`` is a diagnostic, not part of the execution model, so it is
    excluded from the comparison: two files that differ only by an additive
    ``schemaVersion`` MINOR must produce byte-identical execution models.
    """
    dump = flowmap.model_dump(by_alias=True)
    dump.pop("schemaVersion", None)
    return dump


def test_golden_old_flow_accepted_without_schema_version():
    """A real pre-sprint flow with NO schemaVersion parses successfully."""
    raw = _load_raw()
    assert "schemaVersion" not in raw  # the base fixture is a genuine old flow
    flowmap = FlowMap.model_validate(raw)
    assert flowmap.name == "Golden Old Flow (pre-sprint)"
    assert len(flowmap.steps) == 2
    # staticVars / extract / conditionData / nested then/else/loop survived intact.
    assert flowmap.staticVars["maxItems"] == 5
    login = flowmap.steps[0]
    assert login.type == "request"
    assert login.extract["token"] == "body.data.sessionToken"


@pytest.mark.parametrize("version", [None, "1.0", "1.5"])
def test_golden_old_flow_parses_identically_across_minor_versions(version):
    """Absent / "1.0" / unknown MINOR "1.5" all yield the SAME execution model."""
    baseline = _execution_model(FlowMap.model_validate(_load_raw()))

    raw = _load_raw()
    if version is not None:
        raw["schemaVersion"] = version
    flowmap = FlowMap.model_validate(raw)

    assert _execution_model(flowmap) == baseline, (
        f"schemaVersion={version!r} changed the execution model; additive MINOR "
        "bumps must be behavior-preserving for old flows."
    )


def test_golden_old_flow_unknown_minor_not_rejected():
    """An unknown MINOR must be tolerated (accepted), never rejected."""
    raw = _load_raw()
    raw["schemaVersion"] = "1.99"
    # Must not raise.
    flowmap = FlowMap.model_validate(raw)
    assert flowmap.name == "Golden Old Flow (pre-sprint)"
