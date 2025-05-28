
## Document: Aligning the Automated Flow Executor (`flow_runner.py`) with External Flow Definitions

**Version:** 1.4
**Date:** August 2nd, 2024
**Purpose:** To provide a highly detailed specification for updating the automated flow executor (`flow_runner.py` and its control interface `container_control.py`). The goal is to enable it to accurately and consistently execute API flow definitions that are designed and exported from a separate, feature-rich graphical flow authoring application. This alignment ensures that flows designed for sophisticated testing and simulation can be seamlessly transitioned into a containerized environment for continuous operation, such as automated traffic generation or ongoing API health checks.

### 1. Introduction & Motivation

**Why These Changes Are Necessary:**

The automated flow executor (`flow_runner.py`) is designed to run sequences of API calls (flows) in a headless, containerized environment. There is a need to execute flows that are created and validated in a more feature-rich, interactive application. This interactive application has advanced capabilities for defining how variables are extracted from API responses, how conditional logic is applied, how loops are processed, and how URLs are constructed.

To ensure that flows designed in this external application can be reliably executed by `flow_runner.py` without modification, the executor's internal logic must be upgraded to match the sophisticated behaviors of the authoring tool. This alignment is crucial for:

*   **Interoperability:** Allowing users to design complex flows visually and then deploy them for automated execution.
*   **Consistency:** Ensuring a flow behaves identically in both the design environment and the automated execution environment.
*   **Reusability:** Maximizing the value of authored flows by enabling their use in different contexts (design vs. continuous operation).
*   **Enhanced Capabilities:** Bringing powerful features like flexible URL overrides and nuanced variable handling to the automated executor.

**The Goal for the AI Agent:**

The updated `flow_runner.py` should be able to take a flow definition (as a JSON object) exported from the external authoring tool and execute it step-by-step, faithfully reproducing the intended logic for:

1.  Extracting data from API response status, headers, and body.
2.  Evaluating conditional branches (if/then/else).
3.  Iterating through loops (for-each).
4.  Constructing request URLs, especially with a new mechanism to override the base URL/FQDN at runtime.
5.  Handling variable substitutions within request components (URLs, headers, body).

### 2. Core Guiding Principles for Implementation

1.  **Behavioral Equivalence:** The executor must strive for identical operational behavior to the reference authoring application when processing the same flow definition and context (excluding UI interactions).
2.  **Robust Parsing of External Flow Definitions:** The executor must correctly parse and interpret the structure and fields of the imported flow JSON, as defined by the external tool.
3.  **Ignore UI-Specific Data:** Any fields in the imported flow JSON that are solely for graphical rendering or UI state in the authoring tool (e.g., `visualLayout`) must be gracefully ignored by the executor.
4.  **Clarity in Logging:** The executor should provide clear, detailed logging (especially at DEBUG level) about its decisions and actions at each step, facilitating troubleshooting.
5.  **Adherence to Pydantic Models:** All incoming configurations and flow definitions must be validated against the Pydantic models within `flow_runner.py`. Changes might be needed in these models.

### 3. Detailed Logical Alignments and Implementation Guidance

---
**FILE: `flow_runner.py`**
---

#### 3.1. Pydantic Model Enhancements

*   **`ContainerConfig` Model:**
    *   **New Field:** Introduce a boolean field named `override_step_url_host`.
        *   **Purpose:** This flag will control the new URL construction behavior.
        *   **Default Value:** `True`. This means the new URL override logic will be active by default if this flag is not explicitly provided by the API caller.
        *   **Description for Pydantic:** "If true, 'flow_target_url' (from the main config) will exclusively form the base (scheme, host, port) for all step URLs. Only the path, query, and fragment from the individual `step.url` will be used. Any host information in `step.url` itself will be ignored. If false, the executor will use its standard logic where `step.url` can specify its own host or use variables like `{{baseURL}}`."

*   **`FlowMap` Model:**
    *   **UI-Data Handling:** To explicitly ensure that fields like `visualLayout` (or any other non-defined fields from the imported JSON) are ignored:
        *   **Action:** Add `model_config = ConfigDict(extra="ignore")` to the `FlowMap` Pydantic model. (Requires `from pydantic import ConfigDict`). This makes the desired behavior explicit and guards against future changes in Pydantic's default behavior.

#### 3.2. Variable Extraction Logic (Targeting `_extract_data` and its helper `get_value_from_context` in `FlowRunner` class)

*   **Reference Application's Behavior (to be mirrored):**
    1.  **Extraction Sources & Paths:**
        *   **Status Code:** If an extraction rule's path is the exact string `".status"`, the executor must extract the integer HTTP status code of the response.
        *   **Headers:** If a path is `"headers.Header-Name"`, it must extract the value of the HTTP response header named `Header-Name`. The lookup for `Header-Name` must be case-insensitive. (Clarify how multiple headers with the same name are handled by the reference app – e.g., first, last, or comma-separated string – and replicate that).
        *   **Full Parsed Body:** If a path is exactly `"body"`, it must extract the entire parsed response body (e.g., a Python dictionary if JSON, or a string).
        *   **Specific Body Paths (JSON assumed):** For paths like `"body.data.items[0].id"`, it must navigate the parsed JSON response body. This requires support for dot-notation for object properties and bracket-notation `[index]` for array elements.
        *   **Implicit Body Path:** If an extraction path *does not* explicitly start with `.status`, `headers.`, or `body.`, it is *implicitly* treated as a path within the response body. (e.g., `user.id` should be treated as `body.user.id` if the `body` key exists in the response data structure).
    2.  **Path Resolution Engine (`get_value_from_context` must mirror this):**
        *   The core function resolving these paths must be highly robust.
        *   When an implicit body path (e.g., `user.id`) is evaluated, and the data being searched is the complete response object (containing status, headers, body parts), the search must correctly begin within the `body` part of that object. If the data being searched is *already* just the parsed body content, then it searches from that root.
        *   It must correctly handle: nested objects, arrays, out-of-bounds array indices (should yield `None`/undefined), and attempts to access properties on non-object/non-array types (should yield `None`/undefined).
    3.  **Extraction Failure:** If any extraction path is invalid, does not exist, or leads to an error during evaluation, the context variable being assigned *must* be set to Python `None` (Python's equivalent of `null` or `undefined`). A warning should be logged detailing the variable name, the problematic path, and the reason for the failure (e.g., "Path not found", "Index out of bounds").

*   **Implementation Notes for `flow_runner.py`:**
    *   The existing `_extract_data` and `get_value_from_context` (within the `FlowRunner` class) provide a good foundation.
    *   **Focus for AI Agent:** Verify and refine the path parsing within `get_value_from_context` to ensure it precisely matches the reference application's handling of complex paths, array indices, and edge cases. The regex-based parsing might need adjustment or replacement with a more iterative token-based approach if it doesn't cover all nuances.
    *   Ensure the `_MISSING` sentinel from `get_value_from_context` is consistently translated to `None` in the context by `_extract_data` upon failure.

#### 3.3. Conditional Step Evaluation (Targeting `_evaluate_structured_condition` method in `FlowRunner` class)

*   **Reference Application's Behavior (to be mirrored):**
    *   **Input:** Processes a structured `conditionData` object (containing `variable`, `operator`, `value` fields) against the current execution `context`.
    *   **Variable Resolution:** The `conditionData.variable` (e.g., `myVar.status_code`) is resolved against the `context` using the same robust path evaluation logic as in variable extraction. If the variable's path is not found in the context, its value is treated as `None` (Python equivalent) for the purpose of the comparison.
    *   **Operator Semantics (Critical for AI Agent to implement precisely):**
        *   **`equals` / `not_equals`:**
            *   These must perform "smart" comparisons.
            *   Numeric context value vs. string condition value: `200` (int) `equals` `"200"` (str) should be `True`.
            *   Boolean context value vs. string condition value: `True` (bool) `equals` `"true"` (str) should be `True`. `False` (bool) `equals` `"false"` (str) should be `True`.
            *   Null/None context value vs. empty string condition value: `None` `equals` `""` should be `True` (or align with reference app's specific behavior for this common case).
            *   For other types, Python's `==` and `!=` (which handle some type coercion) are generally appropriate.
        *   **Numeric Operators (`greater_than`, `less_than`, `greater_equals`, `less_equals`):**
            *   Attempt to convert both `context_value` and `conditionData.value` (which is a string) to numbers (Python `float` or `int`).
            *   If both conversions succeed, perform the numeric comparison.
            *   If either conversion fails (e.g., trying to convert "abc" to a number), the entire comparison must evaluate to `False`, and a warning should be logged.
        *   **String Operators (`contains`, `starts_with`, `ends_with`):**
            *   Convert `context_value` to its string representation (Python `str()`).
            *   Perform the respective string operation against `conditionData.value` (which is already a string).
        *   **`matches_regex`:**
            *   `conditionData.value` contains the regular expression pattern as a string.
            *   The resolved context variable's value is converted to a string.
            *   The Python `re.search()` function should be used. If `re.search()` finds a match, the condition is `True`.
            *   If the regex pattern in `conditionData.value` is invalid and causes `re.compile()` or `re.search()` to raise an error, the condition must evaluate to `False`, and an error should be logged.
        *   **`exists`:** Evaluates to `True` if the resolved context variable's value is *not* `None`.
        *   **`not_exists`:** Evaluates to `True` if the resolved context variable's value *is* `None`.
        *   **Type Checks (`is_number`, `is_text`, `is_boolean`, `is_array`):**
            *   `is_number`: `True` if the Python type is `int` or `float` (and not a `NaN` float). Must explicitly exclude `bool` since `isinstance(True, int)` is true in Python.
            *   `is_text`: `True` if Python type is `str`.
            *   `is_boolean`: `True` if Python type is `bool`.
            *   `is_array`: `True` if Python type is `list`.
        *   **Boolean Value Checks (`is_true`, `is_false`):**
            *   `is_true`: `True` only if the resolved context variable's value is the Python boolean `True`.
            *   `is_false`: `True` only if the resolved context variable's value is the Python boolean `False`.
    *   **`conditionData.value` Field:** This is always a string in the input JSON. The evaluation logic must attempt to coerce it to the appropriate type (number, boolean) based on the operator and the type of the resolved context variable.

*   **Implementation Notes for `flow_runner.py`:**
    *   The current `_evaluate_structured_condition` in the `FlowRunner` class is a good starting point.
    *   **Focus for AI Agent:** Systematically review *every operator case*. The logic for type coercion before comparison is paramount. Ensure Python's `None` is handled as the equivalent of JavaScript's `null`/`undefined` in these comparisons.
    *   For `is_number`, ensure `isinstance(value, bool)` is checked and results in `False`. Use `math.isnan()` for floats.
    *   Log warnings clearly if a comparison cannot be performed due to incompatible types (e.g., numeric operator on non-numeric data), defaulting the condition to `False`.
    *   The existing Pydantic model and validator in `flow_runner.py` correctly prioritizes `conditionData` over the legacy `condition` string, which is good.

#### 3.4. Loop Step Execution (Targeting `_execute_loop_step` method in `FlowRunner` class)

*   **Reference Application's Behavior (to be mirrored):**
    1.  **Source Resolution:** The `source` field (e.g., `{{myArray}}` or direct key `myArray`) is resolved from the current context using the standard path evaluation logic.
    2.  **Source Validation (Critical):**
        *   If the resolved `source` is **not a list** in Python (e.g., it's `None`, a string, int, dict), the loop body **must not execute**. An informative log message should indicate that the loop is being skipped due to an invalid source type.
        *   If the resolved `source` **is an empty list**, the loop body **must not execute**. An informative log message should indicate this.
    3.  **Iteration Context Management:**
        *   For each item in the resolved source array:
            *   A **new, completely isolated execution context** must be created for that iteration. This is best achieved using a **deep copy** (e.g., Python's `copy.deepcopy()`) of the context that was active just before the loop started.
            *   In this new iteration-specific context, the current item from the list is assigned to the variable name specified by `loopVariable` (e.g., if `loopVariable` is `"loopItem"`, then `iteration_context["loopItem"] = current_array_item`).
            *   A 0-based iteration index variable must also be made available in this iteration context. The name should be consistent (e.g., if `loopVariable` is `"loopItem"`, the index could be `"loopItem_index"`).
    4.  **Execution:** The `steps` defined within the loop's body are executed sequentially for each item, using its unique, isolated iteration context.
    5.  **Context Integrity:** Changes made to variables *within* an iteration's context *must not* affect any other iteration's context or the context of the parent scope (the scope outside the loop). The deep copy is essential for this.

*   **Implementation Notes for `flow_runner.py`:**
    *   The current `_execute_loop_step` in the `FlowRunner` class uses `copy.deepcopy()` and sets the loop variable and an index variable (e.g., `loopVariable_index`).
    *   **Focus for AI Agent:**
        *   **Source Validation:** Strengthen the validation. Before attempting to iterate, explicitly check if the resolved `source` is a Python `list`. If not, log a warning (e.g., "Loop source '{{...}}' did not resolve to a list, was type X. Skipping loop.") and do not proceed with iteration.
        *   **Deep Copy Robustness:** While `copy.deepcopy()` is generally effective, be mindful of any extremely complex or unusual (e.g., un-pickleable) objects that might appear in the context, though this is rare for JSON-derived data. For typical flow data, it should be fine.
        *   **Index Variable Naming:** Ensure the naming convention for the index variable (e.g., `loopVarName_index`) is consistently applied and documented if the reference app has a specific standard.

#### 3.5. Request Step - Body Variable Substitution and Serialization (Targeting `_substitute_variables` and `_execute_request_step` methods in `FlowRunner` class)

This concerns how dynamic values are inserted into JSON request bodies, specifically distinguishing between string-embedding and raw JSON value injection.

*   **Reference Application's Behavior (to be mirrored):**
    *   The external authoring tool uses `{{varName}}` for general templating, which usually results in string conversion. For JSON bodies where native JSON types (numbers, booleans, objects, arrays) are needed from variables, it uses special markers:
        *   `"key": "##VAR:string:varName##"`: `varName`'s value is converted to a string and becomes the JSON string value.
        *   `"key": "##VAR:unquoted:varName##"`: `varName`'s value (which could be a Python `int`, `bool`, `dict`, `list`, or `str`) is intended to be inserted as its native JSON equivalent. For example, if `varName` is Python `True`, the resulting JSON should be `"key": true` (boolean true), not `"key": "True"` (string "True").
    *   This implies that the body definition itself, when containing these markers, is often a dictionary-like structure in the flow model, not a single large string template.

*   **Implementation Notes for `flow_runner.py`:**
    *   The current `_substitute_variables` in the `FlowRunner` class correctly handles `##VAR:string:name##` (returns Python `str`) and `##VAR:unquoted:name##` (returns raw Python type from context).
    *   The current `_execute_request_step` passes the result of `_substitute_variables` to `aiohttp`. If this result is a Python `dict` (because the original `step.body` was a dict with markers as string values), `aiohttp`'s `json=` parameter serializes it correctly, preserving native Python types as their JSON equivalents.
    *   **Focus for AI Agent:**
        *   **Confirm Preferred Input:** The current Python approach is efficient if the input flow JSON defines `step.body` as a JSON object (parsed into a Python `dict`) where values can be these `##VAR:...##` marker strings. This is the recommended path for the AI agent to ensure and test.
            *   Example Flow JSON `step.body`: `{"count": "##VAR:unquoted:itemCount##", "name": "##VAR:string:itemName##"}`
            *   After `json.loads` (external) and then processing by `_substitute_variables(body_dict, context)`:
                *   If `itemCount` is `10` (int) and `itemName` is `"Gadget"` (str), the `body_dict` becomes `{"count": 10, "name": "Gadget"}`. This Python `dict` is then correctly serialized to JSON by `aiohttp`.
        *   **String-Based Body (Less Ideal but Possible):** If the reference application exports `step.body` as a single, large JSON *string* that itself contains these `##VAR:unquoted:name##` markers (e.g., `"body": "{\"count\": ##VAR:unquoted:itemCount##, \"name\": \"Test\"}"`), then `flow_runner.py`'s `_substitute_variables` would return a string with parts that are not valid for direct `json.loads` if the unquoted vars were non-strings. In this specific, less common scenario, `_execute_request_step` would need to:
            1.  Detect that the `step_body_substituted` is a string.
            2.  If `Content-Type` is JSON, it should NOT just try `json.loads()`.
            3.  Instead, it would need a more complex mechanism to produce a valid final JSON string, potentially by replacing the `##VAR:unquoted:name##` markers within the string with their properly JSON-formatted (but not double-quoted if not strings) literal values. This is hard to do robustly with simple string replacement.
            *   **Guidance for AI:** For now, assume the primary and robust path is that flow definitions will use structured JSON objects for bodies when unquoted dynamic values are needed. If the string-template scenario is confirmed as a strict requirement, a separate, more complex parsing/reconstruction strategy for string bodies will be needed in `_execute_request_step`.
        *   **Clarity in `_substitute_variables`:**
            *   The logic for `{{var}}` (simple template) should result in string values.
            *   The logic for `##VAR:string:name##` should result in string values.
            *   The logic for `##VAR:unquoted:name##` (when its input `data` is this string marker itself) should return the raw Python type from context.

#### 3.6. Request Step - URL Construction & New Base URL Override (Targeting `_execute_request_step` method in `FlowRunner` class)

This is a **new feature** for `flow_runner.py` to enhance portability of flows, allowing the runtime environment to dictate the target FQDN/IP and scheme.

*   **Controlling Configuration Field:** The new `override_step_url_host: bool` field in `ContainerConfig` (defaulting to `True`) dictates the behavior.

*   **Logic to Implement in `_execute_request_step` (to be implemented by AI Agent):**

    1.  **Always perform `{{variable}}` substitution on the `step.url` string first.** This yields the `substituted_step_url`.
    2.  **Parse `substituted_step_url`** using Python's `urllib.parse.urlparse`. Let the result be `parsed_step_url_components`.
    3.  **Decision Point: `if self.config.override_step_url_host:`**

        *   **If `True` (Override is Active - Default Behavior):**
            *   The **base** for the request (scheme, hostname, port) is *exclusively* determined by `self.config.flow_target_url` (which is pre-parsed into `self.parsed_url`, `self.original_scheme`, `self.original_host`, `self.default_port` in `FlowRunner.__init__`).
            *   The **path, query, and fragment** for the request are taken *only* from the `parsed_step_url_components`.
                *   Any scheme, netloc (hostname/port) components from `parsed_step_url_components` are **completely ignored**.
            *   **Path Normalization (Crucial):**
                *   Let `step_path = parsed_step_url_components.path`.
                *   If `step_path` is empty or `None` (e.g., `step.url` resolved to just `http://domain.com`), the path component for the final URL should be `/`.
                *   If `step_path` does *not* start with a `/` (e.g., `api/resource`), a `/` must be prepended to it.
            *   **Final URL Assembly:**
                *   **Scheme:** Use `self.original_scheme`.
                *   **Netloc (Hostname & Port):**
                    *   If `self.target_ip` (from `config.flow_target_dns_override`) is set:
                        *   The request will target `self.target_ip`. The port will be the one specified in `self.config.flow_target_url`, or the default for the scheme (80 for http, 443 for https).
                        *   The `Host` HTTP header *must* be explicitly set in the outgoing request to `self.original_host` (the actual hostname from `self.config.flow_target_url`).
                    *   If `self.target_ip` is *not* set:
                        *   The request will target `self.parsed_url.netloc` (which includes hostname and potentially port from `self.config.flow_target_url`).
                        *   The `Host` header can be derived by `aiohttp` or explicitly set to `self.original_host`.
                *   **Path, Query, Fragment:** Use the (normalized) `step_path`, `parsed_step_url_components.query`, and `parsed_step_url_components.fragment`.
                *   Use `urllib.parse.urlunparse` to construct the `final_url`.
            *   **Logging:** Log "URL Override Active. Base from global config, path from step. Final URL: ..., Host Header (if set): ...".

        *   **If `False` (Override is Inactive - Legacy/Smart Behavior):**
            *   The executor must use its current logic, which is:
                *   If `parsed_step_url_components` indicates an **absolute URL** (has `scheme` and `netloc`):
                    *   This `substituted_step_url` is the primary target.
                    *   **Conditional DNS Override:** If `self.target_ip` is set AND `parsed_step_url_components.hostname` is *identical* to `self.original_host` (the hostname from `self.config.flow_target_url`), then:
                        *   The request targets `self.target_ip` with the port from `parsed_step_url_components`.
                        *   The `Host` header is set to `parsed_step_url_components.hostname`.
                    *   Else (no global DNS override, or hosts don't match), the `substituted_step_url` is used directly.
                *   If `parsed_step_url_components` indicates a **relative URL**:
                    *   The `substituted_step_url` (path part) is appended to `self.config.flow_target_url` (after ensuring the path part starts with `/`).
                    *   If `self.target_ip` is set, it applies to the base part (`self.config.flow_target_url`), and the `Host` header is set to `self.original_host`.
            *   **Logging:** Log "URL Override Inactive. Using step URL logic. Final URL: ..., Host Header (if set): ...".

*   **Implementation Notes for `flow_runner.py`:**
    *   This logic will reside in `_execute_request_step` of the `FlowRunner` class.
    *   The Python `urllib.parse` module is essential here.
    *   Carefully manage the `host_header_override` variable and pass it to `aiohttp` if set.

---
**FILE: `container_control.py`**
---

#### 3.7. API Endpoint Configuration (`/api/start`)

*   **Target Behavior:** The API endpoint that starts the flow execution must allow the caller to specify the new URL override behavior (`override_step_url_host`) as part of the configuration payload.
*   **Implementation:**
    *   The `config` object within the JSON payload sent to `/api/start` can now optionally include the `override_step_url_host: bool` field.
    *   **Pydantic's Role:** The `StartRequest` Pydantic model in `flow_runner.py` (which is used to validate the incoming data in `container_control.py` after the `_ensure_config_flowmap_structure` helper function) already defines its `config` attribute as an instance of `ContainerConfig` (from `flow_runner.py`).
    *   Since `ContainerConfig` in `flow_runner.py` will be updated to include `override_step_url_host` with a `default=True`, Pydantic will automatically:
        *   Accept this field if provided in the API request's `config` object.
        *   Use the default value (`True`) if the field is *not* provided in the API request.
*   **Required Actions & Nuances for the AI Agent:**
    *   **No direct code changes are anticipated for `container_control.py`'s endpoint function signature or the `_ensure_config_flowmap_structure` helper regarding this new parameter.** The existing structure leverages Pydantic's validation and default value handling effectively.
    *   The key is that the `ContainerConfig` model (defined in `flow_runner.py` and used by `StartRequest`) is correctly updated with the new field and its default.
    *   The API documentation for the `/api/start` endpoint should be updated to clearly describe this new optional `override_step_url_host` configuration parameter within the `config` object.

### 4. Summary of Implementation Focus for AI Agent

*   **Primary Task in `flow_runner.py`:** Refactor `FlowRunner._execute_request_step` to implement the new URL override logic controlled by `config.override_step_url_host`.
*   **Verification Tasks in `flow_runner.py`:**
    *   Minutely review and align `_evaluate_structured_condition` with the target conditional logic, especially type coercions and operator semantics.
    *   Verify `get_value_from_context` for precise path resolution parity.
    *   Confirm robust loop source validation and context isolation in `_execute_loop_step`.
    *   Ensure `_substitute_variables` and `_execute_request_step` correctly handle `##VAR:unquoted:name##` markers for direct JSON value injection, particularly if the input `step.body` is a dictionary-like structure in the flow JSON.
*   **Model Task in `flow_runner.py`:** Add `override_step_url_host` to `ContainerConfig`. Add `model_config = ConfigDict(extra="ignore")` to `FlowMap`.
*   **No Change (but confirm) in `container_control.py`:** This file should automatically support the new config field via Pydantic, once `ContainerConfig` in `flow_runner.py` is updated.
*   **Logging:** Ensure all new logic paths and important decisions are clearly logged.

By meticulously addressing these points, the AI agent can successfully upgrade the automated flow executor to meet the new requirements for interoperability and advanced feature support.

---

## Project Roadmap

### v1.1.0 (Current)

- [x] **Continuous Flow Runner (Simplified):** FlowRunner now runs indefinitely when started via the API and resets context between iterations.
- [x] **Validation & Error Handling Improvements:** API responses and internal logging provide clearer details on invalid configurations and flow errors.
- [x] **UI/UX Tweaks:** Logging levels can be adjusted via configuration; metrics endpoints expose detailed runtime information.
- [x] **Automated Testing Framework:** Unit and end-to-end test suites exercise the FlowRunner core and API.
