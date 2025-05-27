Okay, here's a comprehensive Markdown document mapping out unit and end-to-end (E2E) tests for the dockerized `flow_runner.py` and its `container_control.py` API. This aims for robustness, covering features, edge cases, and integration points.

---

# Test Plan: `flow_runner.py` (Dockerized FlowRunner) & `container_control.py`

**Version:** 1.0
**Date:** August 2nd, 2024
**Purpose:** To outline a comprehensive suite of unit and end-to-end (E2E) tests for the `flow_runner.py` module and its `container_control.py` API interface. This plan aims to ensure functional correctness, robustness, and alignment with specified behaviors, including recent enhancements.

## 1. Testing Objectives

*   Verify that `flow_runner.py` correctly executes all defined flow step types (Request, Condition, Loop).
*   Validate accurate variable substitution, extraction, and context management.
*   Ensure conditional logic and loop processing match specified behaviors.
*   Confirm the new URL override mechanism (`override_step_url_host`) functions as designed under all scenarios.
*   Test the robustness of `flow_runner.py` against various valid and invalid flow definitions and configurations.
*   Verify the `container_control.py` API endpoints (`/api/start`, `/api/stop`, `/api/metrics`, `/api/health`, `/metrics`) function correctly and manage the `FlowRunner` lifecycle.
*   Cover edge cases, error handling, and resource management (like memory limits, though harder to unit test directly).

## 2. Testing Scope

*   **Unit Tests:** Focus on individual functions and methods within `flow_runner.py` (e.g., `_execute_request_step`, `_evaluate_structured_condition`, `_substitute_variables`, `get_value_from_context`). Mocks will be used for external dependencies like `aiohttp`.
*   **Integration Tests (within `flow_runner.py` context):** Test the interaction of different components within `FlowRunner`, e.g., how context flows from one step to another, how extraction feeds into conditions.
*   **E2E Tests (API Level via `container_control.py`):** Test the complete system by sending HTTP requests to the `container_control.py` API endpoints and verifying the overall behavior and metrics. This will involve constructing valid and invalid flowmap/config payloads.

## 3. Unit Tests for `flow_runner.py`

**Target Module:** `flow_runner.py` (specifically the `FlowRunner` class and its helper functions)

### 3.1. `FlowRunner.__init__`
*   [ ] Test: Initialization with valid `ContainerConfig` and `FlowMap`.
*   [ ] Test: Initialization with `flow_target_dns_override` set, verify `self.target_ip` and `self.original_host` are correctly derived.
*   [ ] Test: Initialization with default `override_step_url_host` (should be `True`).
*   [ ] Test: Initialization with `override_step_url_host` explicitly set to `False`.
*   [ ] Test: Initialization with invalid `flow_target_url` (e.g., not absolute) - should raise ValueError (or handle gracefully as per Pydantic).
*   [ ] Test: Initialization with `min_sleep_ms > max_sleep_ms` - should raise ValueError (Pydantic validator).

### 3.2. `get_value_from_context` (Standalone or implicitly via other tests)
*   [ ] Test: Retrieve top-level key.
*   [ ] Test: Retrieve nested key (e.g., `data.user.id`).
*   [ ] Test: Retrieve array element by index (e.g., `data.items[0]`).
*   [ ] Test: Retrieve nested key within an array element (e.g., `data.items[0].name`).
*   [ ] Test: Path with multiple array indices (e.g., `data.matrix[0][1].value`).
*   [ ] Test: Non-existent top-level key (should return `_MISSING` sentinel or `None` after handling).
*   [ ] Test: Non-existent nested key.
*   [ ] Test: Array index out of bounds.
*   [ ] Test: Attempting key access on a list (e.g., `myList.someKey`).
*   [ ] Test: Attempting index access on a dictionary (e.g., `myDict[0]` unless key "0" exists).
*   [ ] Test: Empty path string.
*   [ ] Test: Path evaluating to `None`, `0`, `False`, empty string.
*   [ ] Test: Context is not a dict/list (e.g., `None` or a primitive).

### 3.3. `set_value_in_context` (Standalone or implicitly via other tests)
*   [ ] Test: Set top-level key.
*   [ ] Test: Set nested key, creating intermediate dicts.
*   [ ] Test: Set value in an existing list at a valid index.
*   [ ] Test: Attempt to set value in a list at an out-of-bounds index (should fail or not extend).
*   [ ] Test: Attempt to set value requiring an intermediate list (e.g., `data.items[0].name` when `items` doesn't exist or isn't a list) - should fail gracefully.
*   [ ] Test: Empty key string.
*   [ ] Test: Context is not a dict.

### 3.4. `_substitute_variables`
*   **General String Substitution `{{var}}`:**
    *   [ ] Test: Simple variable `{{myVar}}`.
    *   [ ] Test: Nested path `{{data.user.name}}`.
    *   [ ] Test: Variable not found (should return original `{{varName}}` or empty string based on desired behavior, UI FlowRunner typically returns empty string for `{{}}` if not found).
    *   [ ] Test: Variable value is `None`, `0`, `False`.
    *   [ ] Test: Variable value is an object/array (should be JSON stringified).
    *   [ ] Test: Multiple variables in one string `{{var1}} and {{var2}}`.
    *   [ ] Test: No variables in string (should return original string).
*   **`##VAR:string:varName##` Marker:**
    *   [ ] Test: Marker resolves to a string value.
    *   [ ] Test: Marker resolves to a numeric value (should be converted to string).
    *   [ ] Test: Marker resolves to `None` (should be "None" or "" string).
    *   [ ] Test: Variable for marker not found (should be empty string or "None").
*   **`##VAR:unquoted:varName##` Marker:**
    *   [ ] Test: Marker resolves to an integer.
    *   [ ] Test: Marker resolves to a float.
    *   [ ] Test: Marker resolves to a boolean (`True`/`False`).
    *   [ ] Test: Marker resolves to `None`.
    *   [ ] Test: Marker resolves to a list.
    *   [ ] Test: Marker resolves to a dictionary.
    *   [ ] Test: Variable for marker not found (should be `None`).
*   **Recursive Substitution:**
    *   [ ] Test: Dictionary with variable strings in keys and values.
    *   [ ] Test: List with variable strings as items.
    *   [ ] Test: Nested structures with mixed variable types.
*   **Malformed `##VAR##` tokens:** (e.g. `##VAR:name##`, `##VAR:type:name:extra##`) - should return literal token.

### 3.5. `_evaluate_structured_condition`
For each operator:
    *   **`equals` / `not_equals`:**
        *   [ ] Test: String vs String (equal, not equal).
        *   [ ] Test: Number vs Number (equal, not equal).
        *   [ ] Test: Boolean vs Boolean (equal, not equal).
        *   [ ] Test: Number (context) vs String-Number (condition value) (e.g., `200` vs `"200"`).
        *   [ ] Test: Boolean (context) vs String-Boolean (condition value) (e.g., `True` vs `"true"`).
        *   [ ] Test: Context value `None` vs `""` (empty string) in condition.
        *   [ ] Test: Context value `None` vs `"null"` or `"none"` (string) in condition.
        *   [ ] Test: Different types that are not "smart" equal (e.g., `0` vs `False` if strictness is desired, or ensure they *are* equal if Python's `==` behavior is mirrored).
    *   **Numeric (`greater_than`, `less_than`, `greater_equals`, `less_equals`):**
        *   [ ] Test: Valid numeric comparisons.
        *   [ ] Test: Context value is number, condition value is string-number.
        *   [ ] Test: Context value is string-number, condition value is number.
        *   [ ] Test: One or both values are not coercible to numbers (should be `False`).
        *   [ ] Test: Comparison with `None` (should be `False`).
    *   **String (`contains`, `starts_with`, `ends_with`):**
        *   [ ] Test: Valid operations on string context values.
        *   [ ] Test: Context value is number/boolean (should be converted to string for comparison).
        *   [ ] Test: Context value is `None` (should be `False` or empty string behavior).
    *   **`matches_regex`:**
        *   [ ] Test: Valid regex match.
        *   [ ] Test: No match.
        *   [ ] Test: Invalid regex pattern in `conditionData.value` (should be `False`, log error).
        *   [ ] Test: Context value is not a string (should be converted, or fail gracefully).
    *   **`exists` / `not_exists`:**
        *   [ ] Test: Context variable exists and is not `None`.
        *   [ ] Test: Context variable exists and is `None`.
        *   [ ] Test: Context variable does not exist (evaluates as `None`).
        *   [ ] Test: Context variable is empty string, `0`, `False`.
    *   **Type Checks (`is_number`, `is_text`, `is_boolean`, `is_array`):**
        *   [ ] Test: Correct type matches.
        *   [ ] Test: Incorrect type mismatches.
        *   [ ] Test: `is_number` with `bool` input (should be `False`).
        *   [ ] Test: `is_number` with `float('nan')` input (should be `False`).
    *   **Boolean Value Checks (`is_true`, `is_false`):**
        *   [ ] Test: Context value is `True`.
        *   [ ] Test: Context value is `False`.
        *   [ ] Test: Context value is non-boolean (e.g., string `"true"`, number `1`) - should be `False`.
*   **General Condition Edge Cases:**
    *   [ ] Test: `conditionData` is `None` or incomplete (missing `variable` or `operator`).
    *   [ ] Test: `conditionData.variable` path does not resolve (value becomes `None`).

### 3.6. `_extract_data`
*   [ ] Test: Extraction of `.status`.
*   [ ] Test: Extraction of `headers.Content-Type` (case-insensitive check for `Content-Type`).
*   [ ] Test: Extraction of `body.user.id`.
*   [ ] Test: Extraction of implicit body path `user.id`.
*   [ ] Test: Extraction of `body` (full body).
*   [ ] Test: Extraction rule with empty variable name (should be skipped/logged).
*   [ ] Test: Extraction rule with empty path expression (should be skipped/logged, variable set to `None`).
*   [ ] Test: Extraction path for header not found (variable set to `None`).
*   [ ] Test: Extraction path for body not found (variable set to `None`).
*   [ ] Test: Response body is not a dict/list (e.g., plain text) and path is `user.id` (should fail, var `None`).
*   [ ] Test: Multiple extraction rules, some succeeding, some failing.
*   [ ] Test: Context `onContextUpdate` callback is triggered if context changed.

### 3.7. `_execute_request_step`
*   **URL Construction (`override_step_url_host` = True - Default):**
    *   [ ] Test: Step URL is `"/path/only"`, `config.flow_target_url="http://base.com"`. Expected: `http://base.com/path/only`.
    *   [ ] Test: Step URL is `"path/only"`, `config.flow_target_url="http://base.com"`. Expected: `http://base.com/path/only`.
    *   [ ] Test: Step URL is `"http://ignored.com/actual/path?q=1#f"`, `config.flow_target_url="https://newbase.io"`. Expected: `https://newbase.io/actual/path?q=1#f`.
    *   [ ] Test: Step URL is `"{{baseURL}}/actual/path"` where `context.baseURL="http://ignore.this"`, `config.flow_target_url="https://newbase.io"`. Expected: `https://newbase.io/actual/path`.
    *   [ ] Test: Step URL is `""` or `{{emptyVar}}`, `config.flow_target_url="http://base.com"`. Expected: `http://base.com/`.
    *   [ ] Test: With `config.flow_target_dns_override` (e.g., `"1.2.3.4"` for `"target.com"` in `flow_target_url`):
        *   Request URL uses `1.2.3.4` for netloc.
        *   `Host` header is `"target.com"`.
*   **URL Construction (`override_step_url_host` = False):**
    *   [ ] Test: Step URL `"/relative/path"`, `config.flow_target_url="http://base.com"`. Expected: `http://base.com/relative/path`.
    *   [ ] Test: Step URL `"http://absolute.com/path"`, `config.flow_target_url="http://base.com"`. Expected: `http://absolute.com/path`.
    *   [ ] Test: Step URL `"{{baseURL}}/path"` where `context.baseURL="http://stepbase.com"`. Expected: `http://stepbase.com/path`.
    *   [ ] Test: With `config.flow_target_dns_override` (e.g., `"1.2.3.4"` for `"target.com"` in `flow_target_url`):
        *   If step URL is absolute `"http://target.com/path"`: request uses `1.2.3.4`, `Host` header is `target.com`.
        *   If step URL is absolute `"http://other.com/path"`: request uses `other.com`, no DNS override.
        *   If step URL is relative: base uses `1.2.3.4`, `Host` header is `target.com`.
*   **Request Body Handling:**
    *   [ ] Test: GET request (body should be ignored).
    *   [ ] Test: POST with JSON body (dict from `_substitute_variables`), verify `Content-Type: application/json`.
    *   [ ] Test: POST with body as `##VAR:unquoted:myObject##`, verify correct JSON serialization.
    *   [ ] Test: POST with string body and `Content-Type: application/json` (valid JSON string).
    *   [ ] Test: POST with string body and `Content-Type: application/json` (invalid JSON string, should send as raw string or error based on design).
    *   [ ] Test: POST with string body and `Content-Type: text/plain`.
    *   [ ] Test: Body substitution results in non-string/dict/list (e.g., number), verify it's stringified.
*   **Headers:**
    *   [ ] Test: `base_headers` (session), `flow_headers` (global), `step_headers` correctly merged (step overrides flow, flow overrides session).
    *   [ ] Test: `Host` header correctly set/overridden when DNS override is active for various URL scenarios.
*   **Response Handling & `onFailure`:**
    *   [ ] Test: Successful request (2xx), `onFailure="stop"`.
    *   [ ] Test: Client error (4xx), `onFailure="stop"` (should stop flow).
    *   [ ] Test: Server error (5xx), `onFailure="stop"` (should stop flow).
    *   [ ] Test: Client error (4xx), `onFailure="continue"` (should continue flow, log warning).
    *   [ ] Test: Server error (5xx), `onFailure="continue"` (should continue flow, log warning, potential retry logic already present).
    *   [ ] Test: Network error (e.g., connection refused), `onFailure="stop"`.
    *   [ ] Test: Network error, `onFailure="continue"`.
    *   [ ] Test: Data extraction occurs for 2xx.
    *   [ ] Test: Data extraction occurs for non-2xx if `onFailure="continue"`.
    *   [ ] Test: `context_prefix_error` correctly set on various failures.
*   **Retries:** (Existing logic)
    *   [ ] Test: Retries on 5xx errors.
    *   [ ] Test: Retries on connection errors.
    *   [ ] Test: No retries on 4xx errors.
*   **Metrics:**
    *   [ ] Test: `metrics.increment()` called on successful request completion (even if non-2xx and `onFailure="continue"`).
    *   [ ] Test: `metrics.increment()` NOT called on pre-request preparation errors or connection failures after max retries.

### 3.8. `_execute_condition_step`
*   [ ] Test: `_evaluate_condition` returns `True`, `then` branch steps are pushed to execution path.
*   [ ] Test: `_evaluate_condition` returns `False`, `else_` branch steps are pushed.
*   [ ] Test: `_evaluate_condition` returns `False`, `else_` branch is empty/None, no steps pushed.
*   [ ] Test: `then` branch is empty/None, no steps pushed.
*   [ ] Test: Context passed to `_execute_steps` for the branch is a copy of the condition step's context.
*   [ ] Test: Evaluation error in `_evaluate_condition` results in step error, no branch execution.

### 3.9. `_execute_loop_step`
*   [ ] Test: `source` resolves to empty list (loop body skipped).
*   [ ] Test: `source` resolves to non-list (loop body skipped, warning logged).
*   [ ] Test: `source` resolves to `None` (loop body skipped, warning logged).
*   [ ] Test: Loop iterates N times for N items in `source`.
*   [ ] Test: `loopVariable` and `loopVariable_index` correctly set in each iteration's context.
*   [ ] Test: Context isolation between iterations (modification in one iter doesn't affect next).
*   [ ] Test: Context from before loop is correctly base for iteration contexts.
*   [ ] Test: Error within a loop iteration stops that iteration and potentially the whole flow (if not caught).

### 3.10. `FlowRunner.start_generating` & `FlowRunner.stop_generating`
*   [ ] Test: `start_generating` creates `sim_users` number of tasks.
*   [ ] Test: `running` flag set to `True` on start, `False` on stop.
*   [ ] Test: `_stopped_event` is waited on by `start_generating` and set by `stop_generating`.
*   [ ] Test: `stop_generating` cancels and gathers active user tasks.
*   [ ] Test: Calling `start_generating` when already running is a no-op or handled gracefully.
*   [ ] Test: Calling `stop_generating` when not running is a no-op.
*   [ ] Test: `_active_users_count` correctly incremented/decremented.

### 3.11. `FlowRunner.simulate_user_lifecycle`
*   [ ] Test: Loop continues while `self.running` is `True`.
*   [ ] Test: Loop terminates when `self.running` becomes `False`.
*   [ ] Test: Random IP, User-Agent, headers are generated per flow iteration.
*   [ ] Test: Context is initialized with `staticVars` and flow iteration data.
*   [ ] Test: `_execute_steps` is called with the main flow steps.
*   [ ] Test: `metrics.record_flow_duration` called on successful flow completion.
*   [ ] Test: Sleep between flow iterations occurs.
*   [ ] Test: `aiohttp.ClientSession` is created and closed per flow iteration.
*   [ ] Test: `aiohttp.TCPConnector` (with/without DNS override) is created and closed when the user task ends.
*   [ ] Test: Exception handling within the lifecycle (e.g., if `_execute_steps` throws).

### 3.12. Flow Execution (`_execute_steps`)
*   [ ] Test: Stops execution if `self.running` becomes `False`.
*   [ ] Test: Stops execution if `context['flow_error']` is set.
*   [ ] Test: Correctly dispatches to `_execute_request_step`, `_execute_condition_step`, `_execute_loop_step` based on step type.
*   [ ] Test: Inter-step sleep is applied.
*   [ ] Test: Handles empty steps list.
*   [ ] Test: Pydantic validation of step data at runtime if steps are passed as dicts (current logic seems to validate at `FlowMap` parsing time, which is good).

## 4. E2E Tests for `container_control.py` API

**Target:** FastAPI application in `container_control.py`

### 4.1. `/api/health`
*   [ ] Test: Returns `{"status": "healthy", "app_status": "initializing"}` before any start.
*   [ ] Test: Returns `app_status: "running"` after `/api/start`.
*   [ ] Test: Returns `app_status: "stopped"` after `/api/stop`.

### 4.2. `/api/start`
*   [ ] Test: Start with a minimal valid flow (e.g., one GET request). Verify 200 OK response.
*   [ ] Test: Start with `config.override_step_url_host = True` (and relevant flow to test its effect).
*   [ ] Test: Start with `config.override_step_url_host = False` (and relevant flow).
*   [ ] Test: Start with `config.debug = True` (verify logs if possible, or at least no error).
*   [ ] Test: Start with `config.flow_target_dns_override` set.
*   [ ] Test: Start request with missing `flowmap` (should be 400 Bad Request due to Pydantic).
*   [ ] Test: Start request with invalid `config` (e.g., `sim_users=0`) (should be 400).
*   [ ] Test: Start request with invalid `flowmap` structure (e.g., step missing `type`) (should be 400).
*   [ ] Test: Calling `/api/start` when a flow is already running (should stop old, start new).
*   [ ] Test: `_ensure_config_flowmap_structure` correctly handles various input JSON structures.

### 4.3. `/api/stop`
*   [ ] Test: Stop a running flow. Verify 200 OK and `app_status` becomes "stopped".
*   [ ] Test: Stop when no flow is running. Verify appropriate message and status.
*   [ ] Test: Stop when app is in "error" state.

### 4.4. `/api/metrics` (JSON response) & `/metrics` (Prometheus format)
*   **While No Flow Running:**
    *   [ ] Test: `app_status` is "initializing" or "stopped".
    *   [ ] Test: `flow_runner_rps`, `flow_runner_active_users`, `flow_runner_average_duration_ms` are 0 or default.
    *   [ ] Test: System metrics (CPU, mem, network) are present.
*   **While Flow Running:**
    *   [ ] Test: `app_status` is "running".
    *   [ ] Test: `flow_runner_rps` > 0 (may need a flow that generates sustained requests).
    *   [ ] Test: `flow_runner_active_users` matches `sim_users`.
    *   [ ] Test: `flow_runner_average_duration_ms` > 0 after some flows complete.
    *   [ ] Test: Prometheus output format is valid.
*   **After Flow Stopped:**
    *   [ ] Test: Metrics reflect the stopped state (e.g., RPS drops to 0).

### 4.5. Memory Limits (Manual/Observational or Specialized Test Environment)
*   [ ] Test: Observe container behavior when approaching `MEMORY_SOFT_LIMIT` (if possible to induce).
*   [ ] Test: Observe container behavior when approaching `MEMORY_HARD_LIMIT`. This is harder to deterministically test without specific load profiles and might require OOM killer observation.

### 4.6. Signal Handling (SIGTERM, SIGINT)
*   [ ] Test: Send SIGTERM to the container process, verify graceful shutdown (logs indicate flow stopping, process exits 0).
*   [ ] Test: Send SIGINT, verify graceful shutdown.

### 4.7. Complex Flow Execution via API
*   [ ] Test: Submit a flow with nested conditions and loops via `/api/start`.
*   [ ] Test: Verify (e.g., via external target server logs or specific metrics) that the flow executed as expected.
*   [ ] Test: Submit a flow that uses advanced variable extraction and substitution, including `##VAR:unquoted##`.

## 5. Test Environment & Tooling

*   **Unit Tests:** Pytest with `pytest-asyncio` for async functions. Mocks using `unittest.mock` or `pytest-mock`.
*   **E2E Tests:** HTTP client library (e.g., `httpx`, `requests`) to interact with `container_control.py` API. A mock HTTP server (e.g., `aiohttp.web` test server, WireMock, or httpbin.org for external calls) to act as the target for API flows. Docker for running the containerized application.
*   **Python Version:** As specified in Dockerfile (Python 3.11-slim).

## 6. Non-Functional Test Considerations

*   **Performance:** While not strictly unit/E2E, design some E2E tests with larger/more complex flows and higher `sim_users` to observe general performance characteristics and stability.
*   **Logging:** Manually inspect logs during E2E tests (especially with `debug: True`) to ensure clarity and usefulness of log messages.

This test plan provides a robust foundation for ensuring the quality and correctness of the `flow_runner.py` and `container_control.py` components. It should be treated as a living document and updated as new features are added or existing ones modified.