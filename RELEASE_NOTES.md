# Release Notes: FlowRunner CLI (Unreleased)

## ⚠️ Cross-app schema gate — severity: HIGH — unknown MAJOR `schemaVersion` is now rejected loudly

**What changed.** The shared `.flow.json` format carries an OPTIONAL top-level `schemaVersion` string `"MAJOR.MINOR"` (absence ⇒ `"1.0"`, HAR `log.version` precedent). The CLI's `FlowMap` parser now **version-gates** on it:

- **Absent / `"1.0"` / any `"1.x"`** ⇒ accepted and run unchanged. An unknown **MINOR** (e.g. `"1.5"`) is **tolerated with a warning**; any unrecognized construct still degrades gracefully (skip-with-warning) exactly as before.
- **Unknown MAJOR (`>= 2`, e.g. `"2.0"`)** ⇒ **rejected loudly** with a `ValidationError` attributable to `schemaVersion` (naming the offending version), instead of best-effort mis-executing a genuinely newer format against live customer traffic.
- A non-string value (e.g. integer `2`) is **coerced-and-warned** (`2` ⇒ `2.0`), then gated on its MAJOR like any other value — never a silent crash.

This converts *silent wrong-execution* — the single most damaging failure for a "what you see is what actually ran" demo tool — into a principled, auditable refusal. It is additive and backward-compatible: a golden conformance test (`tests/unit/test_golden_old_flow.py`) proves a real pre-sprint flow parses to an **identical** execution model with no `schemaVersion`, with `"1.0"`, and with an unknown MINOR. See the FlowRunner UI repo's `docs/schema-versioning.md`.

## ✨ Additive request-step fields honored: `retries` and `assertions`

- **`step.retries = {count, delayMs}`** (severity: LOW — additive, opt-in). Per-request retry policy mirroring the FlowRunner UI JS engine: an outer retry loop re-issues the whole request on a non-2xx status **or** a network/fetch error, sleeping `delayMs` between attempts and issuing a fresh request each pass. `count` defaults to `0` ⇒ a single attempt, **IDENTICAL** to prior behavior. A user-requested stop is **never** retried. This wraps — and is orthogonal to — the built-in connection/5xx resilience loop.
- **`step.assertions[]`** (severity: LOW — additive, diagnostic-only). Declarative assertions evaluated against the response after each request, **reusing the frozen `conditionData` operator vocabulary** (same operators, same coercion, same missing-target handling). Results are recorded into the execution context under `response_<id>_assertions` (a per-assertion `{name, variable, operator, value, passed}` list) and `response_<id>_assertions_passed` (aggregate boolean). Assertions are diagnostic: they **never** change flow control and **never** crash. Unknown operators / missing targets degrade to a FAILED assertion with a warning.

Both fields are ignored by older CLIs (`extra='ignore'`), so files that use them still run everywhere. This is part of the cross-app FlowMap additive-evolution strategy (see the FlowRunner UI repo's `docs/flowmap-evolution.md`).

---

# Release Notes: FlowRunner CLI v1.2.0

## Highlights

- **Transform step support** for ordered operations (base64/JWT, JSON set, math, and conversions).
- **New special variables** `RANDOM_INT` and `RANDOM_STRING` (cached per flow iteration).
- **Updated flow compatibility** with FlowRunner UI v1.2.0 exports.
- **Documentation updates** and refreshed Docker image tag.

Use the published image: `razor29/flowrunner-cli:v1.2.0`. See the README for detailed usage instructions.
