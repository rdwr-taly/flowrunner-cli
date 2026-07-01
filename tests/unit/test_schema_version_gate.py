"""Version-gate tests for the additive, OPTIONAL ``schemaVersion`` field.

Contract (see the FlowRunner UI repo's docs/schema-versioning.md):

- ``schemaVersion`` is an OPTIONAL top-level string ``"MAJOR.MINOR"``.
- ABSENCE means ``"1.0"``. Absent and ``"1.0"`` are byte-equivalent in meaning.
- Unknown **MINOR** (same MAJOR the CLI supports, e.g. ``"1.5"``) => TOLERATE:
  accept and run, degrade gracefully on any unknown construct, warn once.
- Unknown **MAJOR** (``>= 2``, e.g. ``"2.0"``) => REJECT LOUDLY: refuse the flow
  with a clear error rather than best-effort mis-executing it.
- A non-string (e.g. integer ``2``) is coerced-and-warned, then gated on its
  MAJOR like any other value — never a silent crash.

The gate must NEVER reject an old flow: the golden conformance suite stays green.
"""

import logging

import pytest
from pydantic import ValidationError

from flow_runner import FlowMap


def _base_flow(**extra):
    data = {
        "name": "gate-flow",
        "steps": [
            {
                "id": "s1",
                "name": "req",
                "type": "request",
                "method": "GET",
                "url": "/ping",
                "onFailure": "continue",
            }
        ],
    }
    data.update(extra)
    return data


# --- Accept: absent / current MAJOR / unknown MINOR ------------------------

@pytest.mark.parametrize("version", [None, "1.0", "1.1", "1.5", "1.99"])
def test_accepts_absent_and_known_major(version):
    data = _base_flow()
    if version is not None:
        data["schemaVersion"] = version
    flowmap = FlowMap.model_validate(data)  # must not raise
    assert flowmap.name == "gate-flow"


def test_unknown_minor_warns_but_accepts(caplog):
    data = _base_flow(schemaVersion="1.7")
    with caplog.at_level(logging.WARNING):
        FlowMap.model_validate(data)
    assert any("schemaVersion" in rec.message for rec in caplog.records), (
        "an unknown MINOR should emit a degrade-gracefully warning"
    )


def test_known_minor_1_0_does_not_warn(caplog):
    data = _base_flow(schemaVersion="1.0")
    with caplog.at_level(logging.WARNING):
        FlowMap.model_validate(data)
    assert not any("schemaVersion" in rec.message for rec in caplog.records)


# --- Reject: unknown MAJOR --------------------------------------------------

@pytest.mark.parametrize("version", ["2.0", "2.3", "3.0", "10.0"])
def test_unknown_major_rejected_loudly(version):
    data = _base_flow(schemaVersion=version)
    with pytest.raises(ValidationError) as exc:
        FlowMap.model_validate(data)
    # The error must be attributable to schemaVersion and mention the version.
    msg = str(exc.value)
    assert "schemaVersion" in msg
    assert version in msg


# --- Coercion: non-string values never crash silently -----------------------

def test_integer_major_coerced_and_gated(caplog):
    # Integer 1 should coerce to "1.0"-equivalent and be accepted.
    with caplog.at_level(logging.WARNING):
        flowmap = FlowMap.model_validate(_base_flow(schemaVersion=1))
    assert flowmap.name == "gate-flow"


def test_integer_unknown_major_still_rejected():
    with pytest.raises(ValidationError):
        FlowMap.model_validate(_base_flow(schemaVersion=2))


def test_malformed_version_string_does_not_crash():
    # A garbage value must not raise a raw exception type other than a clean
    # validation rejection; the parser degrades to a spec'd rejection.
    with pytest.raises(ValidationError):
        FlowMap.model_validate(_base_flow(schemaVersion="not-a-version"))
