Okay, here's a comprehensive Markdown document mapping out unit and end-to-end (E2E) tests for the dockerized `flow_runner.py` and its `container_control.py` API. This aims for robustness, covering features, edge cases, and integration points.

---

# Test Plan: `flow_runner.py` (Dockerized FlowRunner) & `container_control.py`

**Version:** 1.1
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
*   [x] Test: Initialization with valid `ContainerConfig` and `FlowMap`.
*   [x] Test: Initialization with `flow_target_dns_override` set, verify `self.target_ip` and `self.original_host` are correctly derived.
*   [x] Test: Initialization with default `override_step_url_host` (should be `True`).
*   [x] Test: Initialization with `override_step_url_host` explicitly set to `False`.
*   [x] Test: Initialization with invalid `flow_target_url` (e.g., not absolute) - should raise ValueError (or handle gracefully as per Pydantic).
*   [x] Test: Initialization with `min_sleep_ms > max_sleep_ms` - should raise ValueError (Pydantic validator).

### 3.2. `get_value_from_context` (Standalone or implicitly via other tests)
*   [x] Test: Retrieve top-level key.
*   [x] Test: Retrieve nested key (e.g., `data.user.id`).
*   [x] Test: Retrieve array element by index (e.g., `data.items[0]`).
*   [x] Test: Retrieve nested key within an array element (e.g., `data.items[0].name`).
*   [x] Test: Path with multiple array indices (e.g., `data.matrix[0][1].value`).
*   [x] Test: Non-existent top-level key (should return `_MISSING` sentinel or `None` after handling).
*   [x] Test: Non-existent nested key.
*   [x] Test: Array index out of bounds.
*   [x] Test: Attempting key access on a list (e.g., `myList.someKey`).
*   [x] Test: Attempting index access on a dictionary (e.g., `myDict[0]` unless key "0" exists).
*   [x] Test: Empty path string.
*   [x] Test: Path evaluating to `None`, `0`, `False`, empty string.
*   [x] Test: Context is not a dict/list (e.g., `None` or a primitive).

### 3.3. `set_value_in_context` (Standalone or implicitly via other tests)
*   [x] Test: Set top-level key.
*   [x] Test: Set nested key, creating intermediate dicts.
*   [x] Test: Set value in an existing list at a valid index.
*   [x] Test: Attempt to set value in a list at an out-of-bounds index (should fail or not extend).
*   [x] Test: Attempt to set value requiring an intermediate list (e.g., `data.items[0].name` when `items` doesn't exist or isn't a list) - should fail gracefully.
*   [x] Test: Empty key string.
*   [x] Test: Context is not a dict.

### 3.4. `_substitute_variables`
*   **General String Substitution `{{var}}`:**
    *   [x] Test: Simple variable `{{myVar}}`.
    *   [x] Test: Nested path `{{data.user.name}}`.
    *   [x] Test: Variable not found (should return original `{{varName}}` or empty string based on desired behavior, UI FlowRunner typically returns empty string for `{{}}` if not found).
    *   [x] Test: Variable value is `None`, `0`, `False`.
    *   [x] Test: Variable value is an object/array (should be JSON stringified).
    *   [x] Test: Multiple variables in one string `{{var1}} and {{var2}}`.
    *   [x] Test: No variables in string (should return original string).
*   **`##VAR:string:varName##` Marker:**
    *   [x] Test: Marker resolves to a string value.
    *   [x] Test: Marker resolves to a numeric value (should be converted to string).
    *   [x] Test: Marker resolves to `None` (should be "None" or "" string).
    *   [x] Test: Variable for marker not found (should be empty string or "None").
*   **`##VAR:unquoted:varName##` Marker:**
    *   [x] Test: Marker resolves to an integer.
    *   [x] Test: Marker resolves to a float.
    *   [x] Test: Marker resolves to a boolean (`True`/`False`).
    *   [x] Test: Marker resolves to `None`.
    *   [x] Test: Marker resolves to a list.
    *   [x] Test: Marker resolves to a dictionary.
    *   [x] Test: Variable for marker not found (should be `None`).
*   **Recursive Substitution:**
    *   [x] Test: Dictionary with variable strings in keys and values.
    *   [x] Test: List with variable strings as items.
    *   [x] Test: Nested structures with mixed variable types.
*   **Malformed `##VAR##` tokens:** (e.g. `##VAR:name##`, `##VAR:type:name:extra##`) - should return literal token.

### 3.5. `_evaluate_structured_condition`
For each operator:
    *   **`equals` / `not_equals`:**
        *   [x] Test: String vs String (equal, not equal).
        *   [x] Test: Number vs Number (equal, not equal).
        *   [x] Test: Boolean vs Boolean (equal, not equal).
        *   [x] Test: Number (context) vs String-Number (condition value) (e.g., `200` vs `"200"`).
        *   [x] Test: Boolean (context) vs String-Boolean (condition value) (e.g., `True` vs `"true"`).
        *   [x] Test: Context value `None` vs `""` (empty string) in condition.
        *   [x] Test: Context value `None` vs `"null"` or `"none"` (string) in condition.
        *   [x] Test: Different types that are not "smart" equal (e.g., `0` vs `False` if strictness is desired, or ensure they *are* equal if Python's `==` behavior is mirrored).
    *   **Numeric (`greater_than`, `less_than`, `greater_equals`, `less_equals`):**
        *   [x] Test: Valid numeric comparisons.
        *   [x] Test: Context value is number, condition value is string-number.
        *   [x] Test: Context value is string-number, condition value is number.
        *   [x] Test: One or both values are not coercible to numbers (should be `False`).
        *   [x] Test: Comparison with `None` (should be `False`).
    *   **String (`contains`, `starts_with`, `ends_with`):**
        *   [x] Test: Valid operations on string context values.
        *   [x] Test: Context value is number/boolean (should be converted to string for comparison).
        *   [x] Test: Context value is `None` (should be `False` or empty string behavior).
    *   **`matches_regex`:**
        *   [x] Test: Valid regex match.
        *   [x] Test: No match.
        *   [x] Test: Invalid regex pattern in `conditionData.value` (should be `False`, log error).
        *   [x] Test: Context value is not a string (should be converted, or fail gracefully).
    *   **`exists` / `not_exists`:**
        *   [x] Test: Context variable exists and is not `None`.
        *   [x] Test: Context variable exists and is `None`.
        *   [x] Test: Context variable does not exist (evaluates as `None`).
        *   [x] Test: Context variable is empty string, `0`, `False`.
    *   **Type Checks (`is_number`, `is_text`, `is_boolean`, `is_array`):**
        *   [x] Test: Correct type matches.
        *   [x] Test: Incorrect type mismatches.
        *   [x] Test: `is_number` with `bool` input (should be `False`).
        *   [x] Test: `is_number` with `float('nan')` input (should be `False`).
    *   **Boolean Value Checks (`is_true`, `is_false`):**
        *   [x] Test: Context value is `True`.
        *   [x] Test: Context value is `False`.
        *   [x] Test: Context value is non-boolean (e.g., string `"true"`, number `1`) - should be `False`.
*   **General Condition Edge Cases:**
    *   [x] Test: `conditionData` is `None` or incomplete (missing `variable` or `operator`).
    *   [x] Test: `conditionData.variable` path does not resolve (value becomes `None`).

### 3.6. `_extract_data`
*   [x] Test: Extraction of `.status`.
*   [x] Test: Extraction of `headers.Content-Type` (case-insensitive check for `Content-Type`).
*   [x] Test: Extraction of `body.user.id`.
*   [x] Test: Extraction of implicit body path `user.id`.
*   [x] Test: Extraction of `body` (full body).
*   [x] Test: Extraction rule with empty variable name (should be skipped/logged).
*   [x] Test: Extraction rule with empty path expression (should be skipped/logged, variable set to `None`).
*   [x] Test: Extraction path for header not found (variable set to `None`).
*   [x] Test: Extraction path for body not found (variable set to `None`).
*   [x] Test: Response body is not a dict/list (e.g., plain text) and path is `user.id` (should fail, var `None`).
*   [x] Test: Multiple extraction rules, some succeeding, some failing.
*   [x] Test: Context `onContextUpdate` callback is triggered if context changed.

### 3.7. `_execute_request_step`
*   **URL Construction (`override_step_url_host` = True - Default):**
    *   [x] Test: Step URL is `"/path/only"`, `config.flow_target_url="http://base.com"`. Expected: `http://base.com/path/only`.
    *   [x] Test: Step URL is `"path/only"`, `config.flow_target_url="http://base.com"`. Expected: `http://base.com/path/only`.
    *   [x] Test: Step URL is `"http://ignored.com/actual/path?q=1#f"`, `config.flow_target_url="https://newbase.io"`. Expected: `https://newbase.io/actual/path?q=1#f`.
    *   [x] Test: Step URL is `"{{baseURL}}/actual/path"` where `context.baseURL="http://ignore.this"`, `config.flow_target_url="https://newbase.io"`. Expected: `https://newbase.io/actual/path`.
    *   [x] Test: Step URL is `""` or `{{emptyVar}}`, `config.flow_target_url="http://base.com"`. Expected: `http://base.com/`.
    *   [x] Test: With `config.flow_target_dns_override` (e.g., `"1.2.3.4"` for `"target.com"` in `flow_target_url`):
        *   Request URL uses `1.2.3.4` for netloc.
        *   `Host` header is `"target.com"`.
*   **URL Construction (`override_step_url_host` = False):**
    *   [x] Test: Step URL `"/relative/path"`, `config.flow_target_url="http://base.com"`. Expected: `http://base.com/relative/path`.
    *   [x] Test: Step URL `"http://absolute.com/path"`, `config.flow_target_url="http://base.com"`. Expected: `http://absolute.com/path`.
    *   [x] Test: Step URL `"{{baseURL}}/path"` where `context.baseURL="http://stepbase.com"`. Expected: `http://stepbase.com/path`.
    *   [x] Test: With `config.flow_target_dns_override` (e.g., `"1.2.3.4"` for `"target.com"` in `flow_target_url`):
        *   If step URL is absolute `"http://target.com/path"`: request uses `1.2.3.4`, `Host` header is `target.com`.
        *   If step URL is absolute `"http://other.com/path"`: request uses `other.com`, no DNS override.
        *   If step URL is relative: base uses `1.2.3.4`, `Host` header is `target.com`.
*   **Request Body Handling:**
    *   [x] Test: GET request (body should be ignored).
    *   [x] Test: POST with JSON body (dict from `_substitute_variables`), verify `Content-Type: application/json`.
    *   [x] Test: POST with body as `##VAR:unquoted:myObject##`, verify correct JSON serialization.
    *   [x] Test: POST with string body and `Content-Type: application/json` (valid JSON string).
    *   [x] Test: POST with string body and `Content-Type: application/json` (invalid JSON string, should send as raw string or error based on design).
    *   [x] Test: POST with string body and `Content-Type: text/plain`.
    *   [x] Test: Body substitution results in non-string/dict/list (e.g., number), verify it's stringified.
*   **Headers:**
    *   [x] Test: `base_headers` (session), `flow_headers` (global), `step_headers` correctly merged (step overrides flow, flow overrides session).
    *   [x] Test: `Host` header correctly set/overridden when DNS override is active for various URL scenarios.
*   **Response Handling & `onFailure`:**
    *   [x] Test: Successful request (2xx), `onFailure="stop"`.
    *   [x] Test: Client error (4xx), `onFailure="stop"` (should stop flow).
    *   [x] Test: Server error (5xx), `onFailure="stop"` (should stop flow).
    *   [x] Test: Client error (4xx), `onFailure="continue"` (should continue flow, log warning).
    *   [x] Test: Server error (5xx), `onFailure="continue"` (should continue flow, log warning, potential retry logic already present).
    *   [x] Test: Network error (e.g., connection refused), `onFailure="stop"`.
    *   [x] Test: Network error, `onFailure="continue"`.
    *   [x] Test: Data extraction occurs for 2xx.
    *   [x] Test: Data extraction occurs for non-2xx if `onFailure="continue"`.
    *   [x] Test: `context_prefix_error` correctly set on various failures.
*   **Retries:** (Existing logic)
    *   [x] Test: Retries on 5xx errors.
    *   [x] Test: Retries on connection errors.
    *   [x] Test: No retries on 4xx errors.
*   **Metrics:**
    *   [x] Test: `metrics.increment()` called on successful request completion (even if non-2xx and `onFailure="continue"`).
    *   [x] Test: `metrics.increment()` NOT called on pre-request preparation errors or connection failures after max retries.

### 3.8. `_execute_condition_step`
*   [x] Test: `_evaluate_condition` returns `True`, `then` branch steps are pushed to execution path.
*   [x] Test: `_evaluate_condition` returns `False`, `else_` branch steps are pushed.
*   [x] Test: `_evaluate_condition` returns `False`, `else_` branch is empty/None, no steps pushed.
*   [x] Test: `then` branch is empty/None, no steps pushed.
*   [x] Test: Context passed to `_execute_steps` for the branch is a copy of the condition step's context.
*   [x] Test: Evaluation error in `_evaluate_condition` results in step error, no branch execution.

### 3.9. `_execute_loop_step`
*   [x] Test: `source` resolves to empty list (loop body skipped).
*   [x] Test: `source` resolves to non-list (loop body skipped, warning logged).
*   [x] Test: `source` resolves to `None` (loop body skipped, warning logged).
*   [x] Test: Loop iterates N times for N items in `source`.
*   [x] Test: `loopVariable` and `loopVariable_index` correctly set in each iteration's context.
*   [x] Test: Context isolation between iterations (modification in one iter doesn't affect next).
*   [x] Test: Context from before loop is correctly base for iteration contexts.
*   [x] Test: Error within a loop iteration stops that iteration and potentially the whole flow (if not caught).

### 3.10. `FlowRunner.start_generating` & `FlowRunner.stop_generating`
*   [x] Test: `start_generating` creates `sim_users` number of tasks.
*   [x] Test: `running` flag set to `True` on start, `False` on stop.
*   [x] Test: `_stopped_event` is waited on by `start_generating` and set by `stop_generating`.
*   [x] Test: `stop_generating` cancels and gathers active user tasks.
*   [x] Test: Calling `start_generating` when already running is a no-op or handled gracefully.
*   [x] Test: Calling `stop_generating` when not running is a no-op.
*   [x] Test: `_active_users_count` correctly incremented/decremented.

### 3.11. `FlowRunner.simulate_user_lifecycle`
*   [x] Test: Loop continues while `self.running` is `True`.
*   [x] Test: Loop terminates when `self.running` becomes `False`.
*   [x] Test: Random IP, User-Agent, headers are generated per flow iteration.
*   [x] Test: Context is initialized with `staticVars` and flow iteration data.
*   [x] Test: `_execute_steps` is called with the main flow steps.
*   [x] Test: `metrics.record_flow_duration` called on successful flow completion.
*   [x] Test: Sleep between flow iterations occurs.
*   [x] Test: `aiohttp.ClientSession` is created and closed per flow iteration.
*   [x] Test: `aiohttp.TCPConnector` (with/without DNS override) is created and closed when the user task ends.
*   [x] Test: Exception handling within the lifecycle (e.g., if `_execute_steps` throws).

### 3.12. Flow Execution (`_execute_steps`)
*   [x] Test: Stops execution if `self.running` becomes `False`.
*   [x] Test: Stops execution if `context['flow_error']` is set.
*   [x] Test: Correctly dispatches to `_execute_request_step`, `_execute_condition_step`, `_execute_loop_step` based on step type.
*   [x] Test: Inter-step sleep is applied.
*   [x] Test: Handles empty steps list.
*   [x] Test: Pydantic validation of step data at runtime if steps are passed as dicts (current logic seems to validate at `FlowMap` parsing time, which is good).

## 4. E2E Tests for `container_control.py` API

**Target:** FastAPI application in `container_control.py`

### 4.1. `/api/health`
*   [x] Test: Returns `{"status": "healthy", "app_status": "initializing"}` before any start.
*   [x] Test: Returns `app_status: "running"` after `/api/start`.
*   [x] Test: Returns `app_status: "stopped"` after `/api/stop`.

### 4.2. `/api/start`
*   [x] Test: Start with a minimal valid flow (e.g., one GET request). Verify 200 OK response.
*   [x] Test: Start with `config.override_step_url_host = True` (and relevant flow to test its effect).
*   [x] Test: Start with `config.override_step_url_host = False` (and relevant flow).
*   [x] Test: Start with `config.debug = True` (verify logs if possible, or at least no error).
*   [x] Test: Start with `config.flow_target_dns_override` set.
*   [x] Test: Start request with missing `flowmap` (should be 400 Bad Request due to Pydantic).
*   [x] Test: Start request with invalid `config` (e.g., `sim_users=0`) (should be 400).
*   [x] Test: Start request with invalid `flowmap` structure (e.g., step missing `type`) (should be 400).
*   [x] Test: Calling `/api/start` when a flow is already running (should stop old, start new).
*   [x] Test: `_ensure_config_flowmap_structure` correctly handles various input JSON structures.

### 4.3. `/api/stop`
*   [x] Test: Stop a running flow. Verify 200 OK and `app_status` becomes "stopped".
*   [x] Test: Stop when no flow is running. Verify appropriate message and status.
*   [x] Test: Stop when app is in "error" state.

### 4.4. `/api/metrics` (JSON response) & `/metrics` (Prometheus format)
*   **While No Flow Running:**
    *   [x] Test: `app_status` is "initializing" or "stopped".
    *   [x] Test: `flow_runner_rps`, `flow_runner_active_users`, `flow_runner_average_duration_ms` are 0 or default.
    *   [x] Test: System metrics (CPU, mem, network) are present.
*   **While Flow Running:**
    *   [x] Test: `app_status` is "running".
    *   [x] Test: `flow_runner_rps` > 0 (may need a flow that generates sustained requests).
    *   [x] Test: `flow_runner_active_users` matches `sim_users`.
    *   [x] Test: `flow_runner_average_duration_ms` > 0 after some flows complete.
    *   [x] Test: Prometheus output format is valid.
*   **After Flow Stopped:**
    *   [x] Test: Metrics reflect the stopped state (e.g., RPS drops to 0).

### 4.5. Memory Limits (Manual/Observational or Specialized Test Environment)
*   [x] Test: Observe container behavior when approaching `MEMORY_SOFT_LIMIT` (if possible to induce).
*   [x] Test: Observe container behavior when approaching `MEMORY_HARD_LIMIT`. This is harder to deterministically test without specific load profiles and might require OOM killer observation.

### 4.6. Signal Handling (SIGTERM, SIGINT)
*   [x] Test: Send SIGTERM to the container process, verify graceful shutdown (logs indicate flow stopping, process exits 0).
*   [x] Test: Send SIGINT, verify graceful shutdown.

### 4.7. Complex Flow Execution via API
*   [x] Test: Submit a flow with nested conditions and loops via `/api/start`.
*   [x] Test: Verify (e.g., via external target server logs or specific metrics) that the flow executed as expected.
*   [x] Test: Submit a flow that uses advanced variable extraction and substitution, including `##VAR:unquoted##`.

## 5. Test Environment & Tooling

*   **Unit Tests:** Pytest with `pytest-asyncio` for async functions. Mocks using `unittest.mock` or `pytest-mock`.
*   **E2E Tests:** HTTP client library (e.g., `httpx`, `requests`) to interact with `container_control.py` API. A mock HTTP server (e.g., `aiohttp.web` test server, WireMock, or httpbin.org for external calls) to act as the target for API flows. Docker for running the containerized application.
*   **Python Version:** As specified in Dockerfile (Python 3.11-slim).

## 6. Non-Functional Test Considerations

*   **Performance:** While not strictly unit/E2E, design some E2E tests with larger/more complex flows and higher `sim_users` to observe general performance characteristics and stability.
*   **Logging:** Manually inspect logs during E2E tests (especially with `debug: True`) to ensure clarity and usefulness of log messages.

This test plan provides a robust foundation for ensuring the quality and correctness of the `flow_runner.py` and `container_control.py` components. It should be treated as a living document and updated as new features are added or existing ones modified.