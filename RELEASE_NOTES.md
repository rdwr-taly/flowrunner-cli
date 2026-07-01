# Release Notes: FlowRunner CLI (Unreleased)

## ⚠️ Behavior fix — severity: HIGH — unknown transform ops no longer silently mis-execute

**What changed.** An unknown/unsupported transform `op` was previously **silently rewritten to `base64_decode`** and executed (`_normalize_transform_op`), so a flow authored with a newer/unrecognized op ran the WRONG operation against live traffic and produced plausible-but-wrong output. It is now **skipped with a machine-readable warning** rather than executed:
- `execute_transform_ops` logs a machine-readable `TRANSFORM_OP_UNSUPPORTED op=... set=... index=...` marker at ERROR level and records a structured entry in its returned `warnings[]` (`{"type": "unsupported_transform_op", "op": <name>, "set": <var>, "index": <i>, "status": "skipped"}`), which the step handler surfaces to the run log. The op's output variable is left unset and the remaining ops run; the skip itself does not halt the flow. (If a *later* op or step hard-references the unset variable via `{{var}}`, that still resolves as an undefined reference and halts as usual, exactly as before — the skip neither invents new failures nor papers over genuinely missing data.)
- `_normalize_transform_op` now **raises** on an unknown op (defense-in-depth) instead of downgrading to `base64_decode`.
- `_execute_transform_step` surfaces each warning at WARNING level in the run log.

**Audit note.** Run artifacts produced by earlier CLI versions may contain values from an unintended `base64_decode` of an unknown op's first argument. To audit, review flows using transform ops outside the supported set (`base64_encode/decode`, `jwt_encode/decode`, `json_set`, `math_add/sub/mul/div`, `to_number/string/boolean`, `boolean_not`); any output variable whose value looks like a base64 decode of an unexpected input may have been affected.

This is part of the cross-app FlowMap graceful-degradation strategy (unknown step type / transform op / operator ⇒ skip-with-warning, never crash, never mis-execute). See the FlowRunner UI repo's `docs/flowmap-evolution.md` and `gotchas.md` "Cross-app FlowMap contract".

---

# Release Notes: FlowRunner CLI v1.2.0

## Highlights

- **Transform step support** for ordered operations (base64/JWT, JSON set, math, and conversions).
- **New special variables** `RANDOM_INT` and `RANDOM_STRING` (cached per flow iteration).
- **Updated flow compatibility** with FlowRunner UI v1.2.0 exports.
- **Documentation updates** and refreshed Docker image tag.

Use the published image: `razor29/flowrunner-cli:v1.2.0`. See the README for detailed usage instructions.
