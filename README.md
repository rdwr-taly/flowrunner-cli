
# FlowRunner (Automated API Flow Execution Engine)

**Version:** 1.1.0
**Status:** Stable

## 1. Overview

FlowRunner is a powerful, UI-less engine designed for the automated execution of API call sequences, known as "flows." It operates within a Docker container and is managed via a simple HTTP API. This component is a core part of the **ShowRunner** orchestration platform, enabling continuous, repeatable execution of complex API interactions for purposes such as:

*   **Automated Legitimate Traffic Generation:** Simulate realistic user and application behavior against target systems.
*   **API Health & Endpoint Monitoring:** Continuously verify the availability and correctness of API endpoints.
*   **API Integration Testing:** Run sequences of API calls to test multi-step processes.
*   **Security Testing Support:** Execute predefined flows to probe for vulnerabilities or validate security controls under load.

FlowRunner is designed to execute flows exported from a companion graphical flow authoring application, ensuring consistency between flow design and automated execution.

## 2. Key Features (Version 1.1.0)

*   **Flow Execution:**
    *   Runs multi-step API flows defined in a JSON format.
    *   Supports **Request Steps** (GET, POST, PUT, PATCH, DELETE, etc.), **Condition Steps** (if/then/else), and **Loop Steps** (for-each).
*   **Variable Management:**
    *   **Static Variables:** Define global key-value pairs for a flow run.
    *   **Dynamic Extraction:** Extract status codes, headers, and body values (including implicit `body.` paths) into variables.
    *   **Substitution:** Use `{{variableName}}` syntax in URLs, headers, and request bodies. `##VAR:string:name##` and `##VAR:unquoted:name##` allow precise JSON value injection.
*   **URL Handling:**
    *   **Global Target URL:** Configure a primary base URL for all flow operations.
    *   **DNS Override:** Optionally override DNS resolution for the global target URL.
    *   **Override Step URL Host:** By default the host of every request comes from `flow_target_url`, with the step providing only path/query. Disable with `override_step_url_host: false`.
*   **Context Management:**
    *   Maintains an execution context that evolves as the flow progresses.
    *   Ensures context isolation for loop iterations.
*   **Traffic Simulation Features:**
    *   Randomized source IP injection (via configurable header, e.g., `X-Forwarded-For`).
    *   Rotation of common `User-Agent` strings and other HTTP headers to mimic diverse clients.
*   **Control API (`container_control.py`):**
    *   Simple HTTP API for starting, stopping, and monitoring flow execution (always continuous when started).
    *   Endpoints for health checks and detailed metrics (JSON and Prometheus formats).
*   **Continuous Operation:** Flows automatically repeat until a stop request is issued.
*   **Resource Management:**
    *   Configurable memory limits for the container process to prevent run-away resource consumption.

## 3. Architecture & Components

The FlowRunner system consists of two main Python files typically run within a Docker container:

1.  **`flow_runner.py`:**
    *   Contains the core `FlowRunner` class responsible for parsing and executing the flow logic.
    *   Handles step-by-step execution, variable management, conditional evaluation, and loop processing.
    *   Manages `aiohttp` sessions for making asynchronous HTTP requests.
2.  **`container_control.py`:**
    *   Provides a FastAPI-based HTTP API to manage and interact with the `FlowRunner` instance.
    *   Endpoints:
        *   `/api/start`: To initiate flow execution with a given configuration and flowmap.
        *   `/api/stop`: To immediately halt any ongoing flow execution.
        *   `/api/health`: Basic health check.
        *   `/api/metrics`: Detailed operational metrics in JSON format.
        *   `/metrics`: Metrics in Prometheus exposition format.
    *   Manages the lifecycle of the `FlowRunner` in a background thread.

## 4. API Reference (`container_control.py`)

All endpoints are prefixed with `/api` unless otherwise noted.

### 4.1. `POST /api/start`

Starts (or restarts) the FlowRunner with the provided configuration and flowmap. If a flow is already running, it will be forcibly stopped before the new one begins.

**Request Body (JSON):**

```json
{
  "config": {
    // --- Required ---
    "flow_target_url": "string (absolute URL, e.g., https://api.example.com)",
    "sim_users": "integer (>=1, number of concurrent simulated users/flows)",

    // --- Optional (with defaults) ---
    "flow_target_dns_override": "string (optional IP address, e.g., '1.2.3.4')",
    "xff_header_name": "string (default: 'X-Forwarded-For')",
    "min_sleep_ms": "integer (default: 100, min ms between steps)",
    "max_sleep_ms": "integer (default: 1000, max ms between steps)",
    "debug": "boolean (default: false, enables verbose logging)"
    "override_step_url_host": "boolean (default: true, ignore host in step URLs)"
    // Any other fields defined in ContainerConfig Pydantic model
  },
  "flowmap": {
    // JSON object representing the flow definition. See Section 5 for FlowMap structure.
  }
}
```

**Responses:**

*   **`200 OK`**:
    ```json
    {
      "message": "Flow runner started with the provided flowmap"
    }
    ```
*   **`400 Bad Request`**: If the request body is invalid or fails Pydantic validation.
    ```json
    {
      "detail": "Invalid request body: <validation error details>"
    }
    ```

### 4.2. `POST /api/stop`

Immediately stops any currently running flow execution.

**Request Body:** Empty

**Responses:**

*   **`200 OK`**:
    ```json
    {
      "message": "Flow runner forcibly stopped."
      // or "Flow runner is already stopped."
      // or "No running flow runner to stop (status=...)."
    }
    ```

### 4.3. `GET /api/health`

Provides a basic health status of the container and the FlowRunner application.

**Responses:**

*   **`200 OK`**:
    ```json
    {
      "status": "healthy",
      "app_status": "string ('initializing' | 'running' | 'stopped' | 'error')"
    }
    ```

### 4.4. `GET /api/metrics`

Returns detailed operational and system metrics in JSON format.

**Responses:**

*   **`200 OK`**:
    ```json
    {
      "timestamp": "string (ISO 8601 UTC)",
      "app_status": "string",
      "container_status": "string ('running')",
      "network": {
        "bytes_sent": "integer",
        "bytes_recv": "integer",
        // ... other network counters
      },
      "system": {
        "cpu_percent": "float",
        "memory_percent": "float",
        "memory_available_mb": "float",
        "memory_used_mb": "float"
      },
      "metrics": { // FlowRunner specific metrics
        "rps": "float (requests per second)",
        "active_simulated_users": "integer",
        "average_flow_duration_ms": "float"
      }
    }
    ```

### 4.5. `GET /metrics`

Returns metrics in Prometheus exposition format for scraping. Includes container system metrics and FlowRunner application metrics.

**Responses:**

*   **`200 OK`**: Plain text Prometheus metrics. Example lines:
    ```
    # HELP container_cpu_percent CPU usage percent.
    # TYPE container_cpu_percent gauge
    container_cpu_percent 10.5
    # HELP flow_runner_rps Current requests-per-second generated by flows.
    # TYPE flow_runner_rps gauge
    flow_runner_rps 150.7
    # HELP app_status Application status (initializing=0, running=1, stopped=2, error=3).
    # TYPE app_status gauge
    app_status 1
    ...
    ```

## 5. FlowMap Definition (`flowmap` Object)

The `flowmap` object defines the sequence of operations. It's based on the Pydantic models in `flow_runner.py`.

### 5.1. Top-Level FlowMap Structure

```json
{
  "id": "string (optional, unique identifier for the flow)",
  "name": "string (required, human-readable name)",
  "description": "string (optional, description of the flow)",
  "headers": {
    // Optional: Key-value pairs for global HTTP headers
    // Applied to all Request steps unless overridden at the step level.
    // Values can use {{variable}} substitution.
    // "X-Global-Header": "GlobalValue"
  },
  "staticVars": {
    // Optional: Key-value pairs for static variables available throughout the flow.
    // Values can be strings, numbers, booleans.
    // "baseUrl": "https://api.internal",
    // "timeout": 5000
  },
  "steps": [
    // Required: Array of step objects (see below)
  ]
  // Note: Any "visualLayout" field from an imported flow will be ignored by default by Pydantic.
}
```

### 5.2. Step Object Structure

Each object in the `steps` array must have `id`, `name` (optional), and `type`.

#### 5.2.1. `type: "request"`

Defines an HTTP request.

```json
{
  "id": "string (unique step ID)",
  "name": "string (optional, human-readable name)",
  "type": "request",
  "method": "string (GET, POST, PUT, DELETE, PATCH, OPTIONS, HEAD)",
  "url": "string (URL for the request; can use {{variable}} substitution)",
  "headers": {
    // Optional: Step-specific headers. Override global headers.
    // "X-Step-Header": "StepValue {{someVar}}"
  },
  "body": "object | string (optional, request body; can use {{vars}} or ##VAR## markers)",
  "extract": {
    // Optional: Defines how to extract data from the response.
    // "variableName": "path.to.data (e.g., body.id, headers.Location, .status)"
  },
  "onFailure": "string ('stop' or 'continue', default: 'stop')"
}
```

*   **`body` Field with Variables:**
    *   Standard `{{variable}}`: Embedded in strings, e.g., `"Message: {{userMsg}}"`.
    *   `"##VAR:string:varName##"`: Value of `varName` from context, converted to a string.
    *   `"##VAR:unquoted:varName##"`: Raw value of `varName` (number, boolean, object, array) injected directly into the JSON structure. For this to work best, the `body` itself in the flowmap should be a JSON object structure, not a single string containing these markers.
        *   Example: `"body": {"count": "##VAR:unquoted:itemCount##", "isEnabled": "##VAR:unquoted:activeFlag##"}`
*   **`extract` Paths:**
    *   `.status`: HTTP status code (integer).
    *   `headers.Header-Name`: Value of `Header-Name` (case-insensitive lookup).
    *   `body`: Entire parsed response body.
    *   `body.path.to.value`: Value from JSON body using dot/bracket notation.
    *   `path.to.value` (implicit body): Same as `body.path.to.value`.
*   **`onFailure`:** Action if request results in a network error or HTTP status code >= 300.
    *   `"stop"` (default): Halts the entire flow execution.
    *   `"continue"`: Logs the error/non-2xx status but allows the flow to proceed to the next step. Extraction will still be attempted.

#### 5.2.2. `type: "condition"`

Defines conditional branching.

```json
{
  "id": "string",
  "name": "string (optional)",
  "type": "condition",
  "conditionData": { // Preferred structured format
    "variable": "string (path to context variable, e.g., extractedStatusCode or myVar.count)",
    "operator": "string (e.g., 'equals', 'contains', 'is_number', 'exists')",
    "value": "string (value to compare against; can use {{vars}})"
  },
  "condition": "string (optional, DEPRECATED legacy JS-like condition string)",
  "then": [ /* Array of step objects for the 'true' branch */ ],
  "else": [ /* Optional: Array of step objects for the 'false' branch */ ]
}
```

*   **`conditionData` Operators:**
    *   Comparison: `equals`, `not_equals`, `greater_than`, `less_than`, `greater_equals`, `less_equals`.
    *   Text: `contains`, `starts_with`, `ends_with`, `matches_regex`.
    *   Existence: `exists` (not null/undefined), `not_exists` (null/undefined).
    *   Type: `is_number`, `is_text`, `is_boolean`, `is_array`.
    *   Boolean: `is_true`, `is_false`.
    *See `flow_runner.py`'s `_evaluate_structured_condition` for detailed semantics including type coercion.*

#### 5.2.3. `type: "loop"`

Defines iteration over a list.

```json
{
  "id": "string",
  "name": "string (optional)",
  "type": "loop",
  "source": "string (path to context variable resolving to a list, e.g., userList or {{apiResult.data.ids}})",
  "loopVariable": "string (name for the current item in context, e.g., 'item', default: 'item')",
  "steps": [ /* Array of step objects to execute for each item */ ]
}
```

*   Inside the loop `steps`, `{{loopVariable}}` (e.g., `{{item}}`) and `{{loopVariable_index}}` will be available in the context.

### 5.3. URL Construction Logic (Version 1.1.0 Behavior)

The final URL for each Request step depends on `override_step_url_host`:

1.  **Variable Substitution:** `{{variable}}` placeholders within `step.url` are resolved first.
2.  **When `override_step_url_host` is `true` (default):**
    *   The scheme/host/port always come from `config.flow_target_url`.
    *   The path, query, and fragment come from the (substituted) `step.url`.
    *   Any host information in the step is ignored.
3.  **When `override_step_url_host` is `false`:**
    *   If the substituted `step.url` is absolute, it is used as-is (with optional DNS override when the hostname matches `flow_target_url`).
    *   If it is relative, it is appended to `flow_target_url`.
4.  **DNS Override:** When `flow_target_dns_override` is set, requests are directed to that IP while the `Host` header reflects the original hostname.

**URL Override Update (v1.1.0):** `config.override_step_url_host` now controls how final request URLs are built. When `true` (default) the scheme/host/port come exclusively from `flow_target_url` and the step only provides the path/query. Set to `false` to allow absolute step URLs as in v1.0.0.

## 6. Deployment & Usage (Docker)

Refer to the provided `Dockerfile` and `requirements.txt`.

1.  **Build the Docker Image:**
    ```bash
    docker build -t flowrunner-engine:1.1.0 .
    ```
2.  **Run the Container:**
    ```bash
    docker run -d -p 8080:8080 --name my-flowrunner flowrunner-engine:1.1.0
    ```
    *   The API will be available on `http://localhost:8080`.
    *   Consider volume mounting for persistent configurations or logs if needed.

3.  **Interact with the API:**
    *   Use `curl` or any HTTP client (like Postman) to send requests to the `/api/start`, `/api/stop`, etc. endpoints.
    *   Example: Start a flow (assuming `my_flow.json` contains the full request body structure shown in Section 4.1):
        ```bash
        curl -X POST -H "Content-Type: application/json" -d @my_flow.json http://localhost:8080/api/start
        ```

## 7. Local Development / Testing

For quick local debugging of `flow_runner.py` without Docker, use the provided `run_local_flow.sh` script:

```bash
./run_local_flow.sh path/to/flow.json [target_url] [sim_users] [DEBUG|INFO]
```

This executes the flow directly with a local Python interpreter. Stop with `Ctrl+C` when finished.

## 8. Logging

*   FlowRunner uses Python's standard `logging` module.
*   Log output goes to `stdout/stderr` within the container.
*   The log level for `flow_runner.py`'s internal operations can be set to `DEBUG` by including `"debug": true` in the `config` object of the `/api/start` request. This provides highly verbose output.
*   The `container_control.py` API layer logs at `INFO` level by default.
*   Timestamps are in UTC, formatted as `YYYY-MM-DD HH:MM:SSZ`.

## 9. Troubleshooting

*   **Container Won't Start:** Check Docker logs (`docker logs my-flowrunner`) for Python errors, port conflicts, or missing dependencies.
*   **API Errors (400 Bad Request):** Usually indicates an issue with the JSON payload sent to `/api/start`. Validate your `config` and `flowmap` against the Pydantic models and specifications in this README.
*   **Flow Not Behaving as Expected:**
    *   Enable debug logging (`"debug": true` in `/api/start` config) and inspect the detailed logs from `flow_runner.py`.
    *   Check variable substitutions: Are variables resolving to the expected values?
    *   Verify conditional logic: Is the `conditionData` correct and evaluating as intended?
    *   Inspect loop sources: Is the `source` variable resolving to a valid list?
    *   Examine extraction rules: Are paths correct? Are there extraction failure warnings in logs or metrics?
    *   Review URL construction logic.
*   **High Resource Usage:**
    *   If `sim_users` is very high or flows are extremely complex/long-running, resource usage can increase.
    *   Ensure memory limits in `container_control.py` (or Docker Compose/Kubernetes) are appropriate for your workload.
    *   Profile flows in the authoring tool to identify performance bottlenecks within the flow itself.

## 10. Contributing & Development

(Placeholder for future contribution guidelines or development setup.)

*   **Dependencies:** See `requirements.txt`.
*   **Testing:** A comprehensive test suite should be maintained (details in a separate test plan document).

