# Contributor Guide for OpenAI Codex: FlowRunner Dockerized Component

## 1. Project Overview & Current Task

**Project:** FlowRunner (Dockerized Automated API Flow Execution Engine)
**Core Files:** `flow_runner.py` (core engine), `container_control.py` (FastAPI wrapper)
**Current Task (Single, Comprehensive Mandate):**
Your mission is to perform a significant update and testing phase for this project. This involves:
1.  **Aligning Core Logic:** Update `flow_runner.py` to precisely match the operational logic of an external, feature-rich flow authoring application. This alignment covers variable extraction, conditional evaluation, loop processing, and specific JSON body value injection techniques, as detailed in the main specification document you have been provided (the "Aligning the Automated Flow Executor (`flow_runner.py`) with External Flow Definitions" document, Version 1.4 or similar).
2.  **Implementing URL Override Feature:** Introduce a new, flexible URL construction mechanism in `flow_runner.py`. This will be controlled by a `ContainerConfig` boolean field named `override_step_url_host` (defaulting to `True`), as specified in the main specification document.
3.  **Refining Always-Continuous Operation:** The `FlowRunner` class in `flow_runner.py` and its management in `container_control.py` **already operate in a continuous loop by design** when a flow is started via `/api/start`. Your task is to ensure this continuous operation is robust:
    *   After each full flow iteration completes successfully, and if `self.state.stopRequested` is `False`, the `FlowRunner` must use `asyncio.sleep(self.delay)` (using the existing `self.delay` for inter-step delay, which also serves as the delay between full flow iterations) before automatically starting the next iteration.
    *   The context for each new continuous iteration must be correctly reset to the initial `staticVars` of the flow.
    *   The `onIterationStart` callback should be invoked before each subsequent iteration (i.e., not the very first one).
    *   The `stop()` method must reliably terminate this continuous loop and cancel any pending inter-iteration sleep.
    *   The `reset()` method must correctly handle context resets for new iterations versus a full stop.
    *   **No `continuous_run` boolean parameter is needed in `ContainerConfig` or the `/api/start` payload for this component; continuous operation is inherent.**
4.  **Enhancing Validation & Logging:** Implement improved input validation, clearer error handling in API responses, and more detailed logging for debugging, as outlined in the main specification document.
5.  **Developing a Comprehensive Test Suite:** Create a full suite of unit tests for `flow_runner.py` and end-to-end (E2E) tests for the `container_control.py` API. These tests must strictly adhere to the "no external network access during test execution" constraint, utilizing mocks and a local mock HTTP server. Refer to `testplan.md` for specific test cases to cover.

## 2. Key Files for Modification & Reference

*   **Primary Files for Your Code Changes:**
    *   `flow_runner.py`: This is the heart of the execution engine. Most logical changes for alignment, URL override, and continuous operation will occur here within the `FlowRunner` class.
    *   `container_control.py`: This FastAPI application wraps `flow_runner.py`. Changes here will primarily involve updating Pydantic models in conjunction with `flow_runner.py` (e.g., `ContainerConfig`) and ensuring the `FlowRunner` instance is managed correctly for always-continuous operation.
*   **Essential Reference Documents (Assume these are in your workspace):**
    *   **Main Specification Document:** (The "Aligning the Automated Flow Executor..." document, Version 1.4). This is your primary guide for detailed logical requirements.
    *   `testplan.md`: Outlines the specific unit and E2E test cases you need to generate.
    *   `README.md` (for v1.0.0): Provides context on the baseline functionality of the application before these significant updates.
*   **Supporting Files (For Your Understanding):**
    *   `Dockerfile`: Defines the Docker image used for *deploying* the application. Note that *you (Codex)* operate within OpenAI's `universal` image (or similar), which is prepared by the "Setup script" you've been provided for the Codex UI environment.
    *   `requirements.txt`: Lists the Python dependencies for the *application itself*.

## 3. Development Environment & Conventions (Within Your Agent Session)

*   **Language:** Python 3.11 (ensure your generated code is compatible).
*   **Key Frameworks/Libraries:**
    *   FastAPI (for `container_control.py`)
    *   Pydantic (for data models in both files)
    *   `aiohttp` (used by `flow_runner.py` for making HTTP requests)
    *   `asyncio` (for all asynchronous operations)
*   **Pydantic Models:** All configuration objects (`ContainerConfig`) and flow definitions (`FlowMap`, `RequestStep`, etc.) **must** be strictly defined and validated by Pydantic models. Update these models as needed to incorporate new fields (e.g., `override_step_url_host`, `continuous_run`).
*   **Logging:** Utilize the existing Python `logging` framework.
    *   Implement detailed `DEBUG` level logs for all significant decision points, variable states before/after substitution, URL construction steps, and internal operations.
    *   Use `INFO` for major lifecycle events (e.g., flow start, iteration start/end, stop).
    *   Use `WARNING` for recoverable issues or non-critical deviations (e.g., loop source not a list, extraction path not found).
    *   Use `ERROR` for issues that disrupt a step or flow execution.
*   **Type Hinting:** All new and modified functions and methods must include comprehensive Python type hints.
*   **Syntax and Logic Constraints (CRITICAL):**
    *   You **must not** change existing syntax or core operational logic in `flow_runner.py` or `container_control.py` *unless* it is explicitly required to implement a feature described in the main specification document (v1.4 logic) or the "Always Continuous Operation" mandate.
    *   The objective is to **align and extend** the existing v1.0.0 baseline, not to fundamentally rewrite components or introduce stylistic changes unrelated to the specified tasks.
    *   Adhere to the established architectural patterns within the existing codebase. For example, maintain the separation of concerns between `flow_runner.py` (engine) and `container_control.py` (API/lifecycle).

## 4. Testing Instructions & Constraints (For Generated Tests)

*   **Test Execution Environment:**
    *   You will generate Python test scripts (`pytest`).
    *   These tests will be executed by a human later, or potentially by you in a subsequent task, within an environment prepared by the **Codex UI's "Setup script"**.
    *   **Assumed Pre-installed Test Dependencies (via Setup Script):**
        *   `pytest`
        *   `pytest-asyncio` (for `async def` test functions)
        *   `httpx` (as the HTTP client for E2E tests to call the `container_control.py` API)
        *   `aiohttp` (for the E2E mock server that `flow_runner.py` will interact with)
*   **Test File Location and Naming:**
    *   Unit tests for `flow_runner.py` logic go into `tests/unit/test_flow_runner.py`.
    *   E2E tests for `container_control.py` API go into `tests/e2e/test_container_control_api.py`.
*   **NO EXTERNAL NETWORK ACCESS DURING TEST EXECUTION (Agent's Main Task):**
    *   **Unit Tests:**
        *   All calls to `aiohttp.ClientSession.request` made by `flow_runner.py` *must* be thoroughly mocked using Python's `unittest.mock.AsyncMock`.
        *   Simulate diverse HTTP responses: successful 2xx (with JSON and plain text content), client errors (4xx), server errors (5xx).
        *   Simulate network-level errors (e.g., `aiohttp.ClientConnectionError`, `asyncio.TimeoutError`).
    *   **E2E Tests (API Level):**
        *   When your generated E2E tests involve flow execution via `/api/start` that includes HTTP Request steps:
            *   The `flow_target_url` in the test flowmaps *must* be configured to point to a local mock server (e.g., `http://localhost:12345`).
            *   You **must include the Python code for a simple mock HTTP server** as part of your E2E test solution. This can be in `tests/e2e/mock_server.py` (preferred) or embedded within `tests/e2e/test_container_control_api.py`. Use `aiohttp.web` for this mock server.
            *   This mock server must be managed by `pytest` fixtures (e.g., an `async` fixture that starts the server before tests and stops it after).
            *   The mock server should have configurable routes to return predefined responses, allowing you to test `flow_runner.py`'s response processing, variable extraction, and conditional logic based on these mocked API interactions.
            *   Optionally, the mock server can record incoming requests so tests can assert that `flow_runner.py` sent the correct method, URL, headers, and body.
*   **Test Coverage:** Your generated tests must aim for comprehensive coverage as detailed in `testplan.md`. This includes normal operation, edge cases, error conditions, and the new features (URL override, always-continuous mode).
*   **Running Tests (Agent's Perspective):** Ensure the generated tests are structured to be runnable via a single `pytest` command from the project's root directory (after the environment setup script has installed dependencies).

## 5. Code Style & Output Format

*   **Code Style:** Strictly follow the existing Python code style, naming conventions, and patterns found in `flow_runner.py` and `container_control.py`.
*   **Output - Code Changes:** Provide your changes as **diffs** against the provided versions of `flow_runner.py` and `container_control.py`. Clearly indicate the file for each diff.
*   **Output - New Test Files:** Provide the **full, complete content** for any new test files you generate:
    *   `tests/unit/test_flow_runner.py`
    *   `tests/e2e/test_container_control_api.py`
    *   `tests/e2e/mock_server.py` (if you create this as a separate helper for the E2E tests).
*   **Output - `requirements.txt`:** If any new *application* dependencies (not test-only dependencies) are required by your code changes in `flow_runner.py` or `container_control.py`, provide a diff for `requirements.txt`. (Test dependencies are handled by the Codex UI setup script.)

By following these guidelines meticulously, you will help ensure the successful upgrade and robust testing of the FlowRunner component.