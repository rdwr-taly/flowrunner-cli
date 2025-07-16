# flow_runner.py

import asyncio
import aiohttp
import json
import random
import time
import psutil # Although imported, psutil is not used in the provided code. Keep for consistency.
from typing import List, Dict, Any, Optional, Union, Literal
import logging
import re
from pydantic import (
    BaseModel,
    Field,
    validator,
    RootModel,
    field_validator,
    model_validator,
    ConfigDict,
)
from ipaddress import ip_address, AddressValueError
from urllib.parse import (
    urlparse,
    urlunparse,
    quote,
    unquote,
    parse_qsl,
    urlencode,
)
from collections import deque
import traceback
import copy  # For deep copying the execution context in loops
import math # Needed for is_number check (isNaN)

# Needed for the discriminated union fix
from typing import Annotated

# --- Logging Setup ---
logger = logging.getLogger("FlowRunner")
if not logger.hasHandlers():
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    # Using the "Z" suffix per your second version:
    formatter = logging.Formatter('%(asctime)sZ - %(levelname)s - %(name)s - %(message)s')
    formatter.converter = time.gmtime # Ensure UTC timestamps in formatter
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
logger.propagate = False # Prevent duplicate logs if root logger is configured

# --- Exports for flow_container_control ---
__all__ = [
    "asyncio", "logger", "StartRequest", "FlowRunner", "Metrics"
]

# ---------------------------
# Flowmap Pydantic Models
# ---------------------------

# Forward reference for nested types (or use UpdateForwardRefs later)
FlowStep = Any  # Placeholder for recursive type hint

class BaseStep(BaseModel):
    id: str = Field(..., description="Unique identifier for the step")
    name: Optional[str] = Field(None, description="Human-readable name for the step")
    # type will be defined in subclasses using Literal

class RequestStep(BaseStep):
    type: Literal['request'] = Field(..., description="Specifies the step type as 'request'")
    method: str = Field(..., description="HTTP method (GET, POST, PUT, etc.)")
    url: str = Field(..., description="URL path (relative to target) or full URL. Can contain {{variables}}.")
    headers: Optional[Dict[str, str]] = Field(default_factory=dict, description="Headers specific to this request. Can contain {{variables}}.")
    body: Optional[Union[Dict[str, Any], str]] = Field(None, description="Request body (JSON object or raw string). Can contain {{variables}}.")
    extract: Optional[Dict[str, str]] = Field(default_factory=dict, description="Mapping of variable names to extract from response using path notation (e.g., 'token': 'body.data.sessionToken', 'firstId': 'body.data.items[0].id', 'status_code': '.status', 'header_val': 'headers.Content-Type')") # Updated description with prefixes
    onFailure: Literal['stop', 'continue'] = Field(..., description="Action on request failure (status >= 300): 'stop' or 'continue'.") # Added onFailure field

    @field_validator('method')
    def validate_method(cls, v):
        allowed_methods = ['GET', 'POST', 'PUT', 'DELETE', 'HEAD', 'PATCH', 'OPTIONS']
        method_upper = v.upper()
        if method_upper not in allowed_methods:
            raise ValueError(f"method must be one of {allowed_methods}, got '{v}'")
        return method_upper

class ConditionData(BaseModel):
    """
    Structured condition data for UI-friendly condition builder. Matches JS 'conditionData'.
    """
    variable: str = Field("", description="Variable name or path (e.g., data.values[0].id, response_step_123_status) to evaluate")
    operator: str = Field("", description="Operation to perform (e.g., 'equals', 'exists', 'is_number', 'greater_than')")
    value: Optional[str] = Field("", description="Value to compare against (for operators that need it)")

class ConditionStep(BaseStep):
    type: Literal['condition'] = Field(..., description="Specifies the step type as 'condition'")
    condition: Optional[str] = Field(None, description="DEPRECATED/LEGACY: Original JavaScript-like condition string. Use conditionData instead.")
    conditionData: Optional[ConditionData] = Field(None, description="Structured condition data for evaluation (preferred)")
    then: List["FlowStep"] = Field(default_factory=list, description="Steps to execute if condition is true")
    else_: Optional[List["FlowStep"]] = Field(default_factory=list, alias="else", description="Steps to execute if condition is false")

    # Validator to ensure at least one condition method is present
    @model_validator(mode='after')
    def check_condition_presence(self) -> 'ConditionStep':
        has_legacy_condition = bool(self.condition)
        has_valid_structured_condition = bool(self.conditionData and self.conditionData.variable and self.conditionData.operator)

        if not has_legacy_condition and not has_valid_structured_condition:
            # Neither legacy nor valid structured condition is present
             raise ValueError(f"ConditionStep (ID: {self.id}, Name: {self.name or 'N/A'}) requires either legacy 'condition' string or valid structured 'conditionData' (variable and operator must be set)")
        # Log a warning if only the legacy condition is used
        elif has_legacy_condition and not has_valid_structured_condition:
             logger.warning(f"ConditionStep (ID: {self.id}, Name: {self.name or 'N/A'}) is using a legacy 'condition' string ('{self.condition}'). Consider migrating to structured 'conditionData' for better reliability.")
        elif not has_legacy_condition and has_valid_structured_condition:
             # This is the preferred state, no warning needed.
             pass
        # else: both are present - we will prioritize structured data, maybe log debug?
        elif has_legacy_condition and has_valid_structured_condition:
            logger.debug(f"ConditionStep (ID: {self.id}, Name: {self.name or 'N/A'}) has both legacy 'condition' and structured 'conditionData'. Structured data will be prioritized.")

        return self

class LoopStep(BaseStep):
    type: Literal['loop'] = Field(..., description="Specifies the step type as 'loop'")
    source: str = Field(..., description="Variable name or path (e.g., '{{items}}' or '{{data.results[0].list}}') resolving to an iterable list in the context.")
    loopVariable: str = Field(..., description="Name for the variable representing each item in the loop (e.g., 'item')")
    steps: List["FlowStep"] = Field(default_factory=list, description="Steps to execute for each item in the loop")

# ------------------------------------------------------------------
# Make FlowStep a discriminated union using Annotated
# ------------------------------------------------------------------
FlowStep = Annotated[
    Union[RequestStep, ConditionStep, LoopStep],
    Field(discriminator='type')
]

# Update nested references in ConditionStep and LoopStep
ConditionStep.model_rebuild()
LoopStep.model_rebuild()

class FlowMap(BaseModel):
    id: Optional[str | int] = Field(
        None,
        description=(
            "Unique ID of the flowmap (optional, used for updates). "
            "May be numeric when supplied by external tooling."
        ),
    )
    name: str = Field(..., description="Name of the flow")
    description: Optional[str] = Field(None, description="Description of the flow")
    headers: Optional[Dict[str, str]] = Field(default_factory=dict, description="Global headers applied to all requests in the flow. Can contain {{variables}}.")
    steps: List[FlowStep] = Field(..., description="The sequence of steps defining the flow")
    staticVars: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Global static variables accessible anywhere in the flow (referenced as {{varName}}). Values can be strings, numbers, booleans.") # Allow Any type

    # Ignore any extra fields when parsing flow definitions
    model_config = ConfigDict(extra="ignore")

# Ensure FlowMap uses the updated FlowStep
FlowMap.model_rebuild()

# ---------------------------
# Configuration Models (Container & Start Request)
# ---------------------------
class ContainerConfig(BaseModel):
    """Runtime configuration for FlowRunner."""
    flow_target_url: str = Field(..., description="Base URL for the target application of the flow")
    flow_target_dns_override: Optional[str] = Field(None, description="IP address to use instead of DNS lookup for the target hostname")
    xff_header_name: str = Field(default="X-Forwarded-For", description="Header name for injecting the fake source IP")
    sim_users: int = Field(..., ge=1, description="Number of simultaneous users executing the flow")
    min_sleep_ms: int = Field(default=100, ge=0, description="Minimum sleep time (ms) between steps in a flow")
    max_sleep_ms: int = Field(default=1000, ge=0, description="Maximum sleep time (ms) between steps in a flow")
    debug: bool = Field(default=False, description="Enable debug logging for flow generator")
    override_step_url_host: bool = Field(
        default=True,
        description=(
            "If true, 'flow_target_url' exclusively forms the request base (scheme, host, port), "
            "using only path/query/fragment from step.url. Step.url's host info is ignored. "
            "If false, step.url logic (honoring its own host or {{baseURL}}) is prioritized."
        ),
    )
    flow_cycle_delay_ms: Optional[int] = Field(
        default=None,
        ge=0,
        description=(
            "Fixed delay in milliseconds between flow iterations. "
            "If not set, a random delay between min_sleep_ms and max_sleep_ms is used."
        ),
    )

    class Config:
        populate_by_name = True
        alias_generator = lambda field_name: {
            'flow_target_url': 'Flow Target URL',
            'flow_target_dns_override': 'Flow Target DNS Override',
            'xff_header_name': 'XFF Header Name',
            'sim_users': 'Simulated Users',
            'min_sleep_ms': 'Minimum Step Sleep MS',
            'max_sleep_ms': 'Maximum Step Sleep MS',
            'debug': 'Debug',
            'override_step_url_host': 'Override Step URL Host',
            'flow_cycle_delay_ms': 'Flow Cycle Delay MS'
        }.get(field_name, field_name)
        extra = "allow" # Allow extra fields but ignore them

    @field_validator('flow_target_dns_override')
    def validate_dns_override(cls, v):
        if v is not None and v != "":
            try:
                ip_address(v)
            except AddressValueError:
                raise ValueError(f"Invalid IP address provided for DNS Override: {v}")
        elif v == "":
            return None # Treat empty string as None
        return v

    @field_validator('flow_cycle_delay_ms')
    def validate_cycle_delay(cls, v):
        if v == "":
            return None
        return v

    @model_validator(mode='after')
    def check_sleep_times(self) -> 'ContainerConfig':
        if self.min_sleep_ms > self.max_sleep_ms:
            raise ValueError(f"min_sleep_ms ({self.min_sleep_ms}) cannot be greater than max_sleep_ms ({self.max_sleep_ms})")
        return self

class StartRequest(BaseModel):
    config: ContainerConfig
    flowmap: FlowMap

# ---------------------------
# Metrics Tracking
# ---------------------------
class Metrics:
    """
    Tracks RPS using a rolling window and computes average flow duration.
    Thread-safe using asyncio.Lock.
    """
    def __init__(self):
        self.lock = asyncio.Lock()
        self.request_timestamps = deque()
        self.last_rps_update_time = 0
        self.last_rps_value = 0.0 # Use float for consistency

        # --- For average flow duration ---
        self.flow_duration_sum = 0.0
        self.flow_count = 0

    async def increment(self):
        """Record that a request was made (for RPS)."""
        now = time.monotonic()
        async with self.lock:
            self.request_timestamps.append(now)
            # Optimization: Only prune when necessary (e.g., during get_rps) or periodically?
            # For simplicity, prune here.
            one_second_ago = now - 1.0
            while self.request_timestamps and self.request_timestamps[0] < one_second_ago:
                self.request_timestamps.popleft()

    async def get_rps(self) -> float:
        """Return the approximate RPS over the last 1 second."""
        now = time.monotonic()
        # Cache result briefly to avoid excessive lock contention if called rapidly
        if now - self.last_rps_update_time < 0.1:
            return self.last_rps_value

        async with self.lock:
            one_second_ago = now - 1.0
            # Prune older timestamps
            while self.request_timestamps and self.request_timestamps[0] < one_second_ago:
                self.request_timestamps.popleft()
            # Count remaining timestamps (within the last second)
            current_rps = float(len(self.request_timestamps))
            self.last_rps_value = current_rps
            self.last_rps_update_time = now
            return current_rps

    async def record_flow_duration(self, duration_seconds: float):
        """Record the duration of a completed flow instance."""
        if duration_seconds < 0:
             logger.warning(f"Attempted to record negative flow duration: {duration_seconds:.3f}s. Ignoring.")
             return
        async with self.lock:
            self.flow_duration_sum += duration_seconds
            self.flow_count += 1

    async def get_average_flow_duration_ms(self) -> float:
        """Return the average duration of completed flows in milliseconds."""
        async with self.lock:
            if self.flow_count == 0:
                return 0.0
            # Ensure division by zero is not possible (already checked flow_count)
            average_duration_s = self.flow_duration_sum / self.flow_count
            return average_duration_s * 1000.0

# ---------------------------
# Context Helper Functions
# ---------------------------

# Pre-compile regex for efficiency (slightly improved to handle keys starting with numbers, though less common)
_context_path_regex = re.compile(r'\[(\d+)\]|([a-zA-Z_]\w*)|\.?([a-zA-Z_]\w*)')
# Simpler Regex (original): Matches indices or sequences of non-dot/bracket characters
# _context_path_regex = re.compile(r'$$(\d+)$$|([^.$$$$]+)')

# --- Sentinel Object for Missing Keys ---
_MISSING = object()

def get_value_from_context(context: Dict[str, Any], key: str) -> Any:
    """
    Safely retrieve a value from a nested context dictionary using dot notation
    for keys and bracket notation for list indices (e.g., 'data.values[0].id').
    Uses a sentinel object to distinguish missing keys from None values.
    Returns the sentinel _MISSING if the path is invalid or the key is not found.
    """
    if not key:
        logger.debug("Attempted to get value from context with empty key.")
        return _MISSING
    if not isinstance(context, (dict, list)):
        logger.debug(f"Context is not a dictionary or list (type: {type(context).__name__}). Cannot retrieve path '{key}'.")
        # Special case: If context isn't dict/list, but key is simple (no . or []), maybe allow direct access?
        # For consistency with path traversal, let's return _MISSING unless context is dict/list.
        # This might need adjustment if accessing attributes of objects becomes necessary.
        return _MISSING


    current_value = context
    # Use finditer to handle sequences like key[0].key[1] correctly
    matches = list(re.finditer(r'\[(\d+)\]|\.?([^.\[\]]+)', key)) # Simplified regex for keys/indices
    processed_path = "" # Keep track of the path traversed for logging

    if not matches:
         # If no matches from regex, assume it's a simple top-level key
         if isinstance(context, dict):
             # Use .get with sentinel for direct dictionary access
             logger.debug(f"Retrieving top-level key '{key}' directly.")
             return context.get(key, _MISSING)
         elif isinstance(context, list):
              logger.debug(f"Cannot retrieve simple key '{key}' from a list context.")
              return _MISSING
         else: # Should be unreachable due to initial check, but for safety
              logger.debug(f"Path '{key}' did not match expected format and context is not dict.")
              return _MISSING


    try:
        for match in matches:
            index_str = match.group(1)
            part_name = match.group(2)

            if index_str is not None:
                # Handle list index access: [index]
                processed_path += f"[{index_str}]"
                try:
                    index = int(index_str)
                except ValueError:
                    # This should not happen with the regex \d+, but safety first.
                    logger.debug(f"Invalid non-integer index '{index_str}' in key '{key}' at path '{processed_path}'.")
                    return _MISSING

                if not isinstance(current_value, list):
                    logger.debug(f"Attempted list index access on non-list type '{type(current_value).__name__}' for key '{key}' at path '{processed_path}'.")
                    return _MISSING
                if 0 <= index < len(current_value):
                    current_value = current_value[index]
                else:
                    logger.debug(f"Index {index} out of bounds (length {len(current_value)}) for key '{key}' at path '{processed_path}'.")
                    return _MISSING

            elif part_name is not None:
                # Handle dictionary key access: .key or key at start
                original_part_name = part_name # Keep original for error messages
                if processed_path and not processed_path.endswith(']'): # Add dot separator if not the first part or after index
                    processed_path += "."
                processed_path += part_name

                # Attempt access using the sentinel
                if isinstance(current_value, dict):
                    current_value = current_value.get(part_name, _MISSING) # Use sentinel default
                    if current_value is _MISSING: # Key truly didn't exist
                        # Do NOT log the debug message here, let the caller decide based on _MISSING return.
                        # logger.debug(f"Key '{original_part_name}' not found in dictionary for key '{key}' at path '{processed_path}'.")
                        return _MISSING # Key truly missing, return sentinel
                    # else: Key existed, current_value is updated (could be None)
                else:
                    # Path tried to access a key on something that wasn't a dict
                    logger.debug(f"Attempted key access ('{original_part_name}') on non-dictionary type '{type(current_value).__name__}' for key '{key}' at path '{processed_path}'.")
                    return _MISSING
            else:
                 # This case should ideally not be reached with the current regex
                 logger.warning(f"Unexpected regex match state for key '{key}' at path '{processed_path}'. Match groups: {match.groups()}")
                 return _MISSING

        # If loop completes, current_value holds the final result (could be None or a valid value)
        return current_value

    except Exception as e:
        # Catch-all for any unexpected issues during traversal
        logger.warning(f"Unexpected error accessing context key '{key}' at path '{processed_path}': {e}", exc_info=False) # Keep log brief
        return _MISSING


def set_value_in_context(context: Dict[str, Any], key: str, value: Any):
    """
    Safely set a value in a nested context dictionary using dot and bracket notation,
    creating intermediate dictionaries if necessary. Does NOT create intermediate lists.
    Logs errors if path is invalid or context is not a dictionary.
    """
    if not key:
        logger.warning("Attempted to set value in context with empty key.")
        return
    if not isinstance(context, dict):
        # Allow setting if context is None and key is simple? No, enforce dict.
        logger.error(f"Cannot set value for key '{key}': Context is not a dictionary (type: {type(context).__name__}).")
        return

    target = context
    # Use the same regex as get_value_from_context
    matches = list(re.finditer(r'\[(\d+)\]|\.?([^.\[\]]+)', key))
    if not matches: # Handle simple top-level key assignment
         if re.match(r'^[^.\[\]]+$', key):# Ensure it's a simple key
             logger.debug(f"Setting top-level key '{key}'.")
             context[key] = value
             return # Added return here for clarity
         else:
              logger.error(f"Invalid key format for setting value: '{key}'. Cannot parse path.")
         return

    processed_path = ""

    try:
        for i, match in enumerate(matches):
            is_last_part = (i == len(matches) - 1)
            index_str = match.group(1)
            part_name = match.group(2)

            if index_str is not None:
                # List index access: [index]
                processed_path += f"[{index_str}]"
                try:
                    index = int(index_str)
                except ValueError:
                    logger.error(f"Invalid index '{index_str}' in key '{key}' at path '{processed_path}'. Cannot set value.")
                    return

                if not isinstance(target, list):
                    logger.error(f"Cannot set value at index [{index}]: target is not a list (type: {type(target).__name__}) for key '{key}' at path '{processed_path}'.")
                    return

                if 0 <= index < len(target):
                    if is_last_part:
                        target[index] = value
                        logger.debug(f"Successfully set value for key '{key}' at path '{processed_path}'.")
                        return # Value set successfully
                    else:
                        # Move deeper into the list element for subsequent parts
                        target = target[index]
                else:
                    # Do not create/extend lists automatically, only allow setting existing indices
                    logger.error(f"Index {index} out of bounds (length {len(target)}) for key '{key}' at path '{processed_path}'. Cannot set value.")
                    return

            elif part_name is not None:
                # Dictionary key access: .key or key
                original_part_name = part_name
                if processed_path and not processed_path.endswith(']'): # Add dot separator
                    processed_path += "."
                processed_path += part_name

                if is_last_part:
                    # Last part, set the value directly in the current dictionary target
                    if isinstance(target, dict):
                        target[part_name] = value
                        logger.debug(f"Successfully set value for key '{key}' at path '{processed_path}'.")
                        return # Value set successfully
                    else:
                         logger.error(f"Cannot set final key '{part_name}': target is not a dictionary (type: {type(target).__name__}) for key '{key}' at path '{processed_path}'.")
                         return
                else:
                    # Intermediate part, ensure it's a dict and traverse/create
                    if not isinstance(target, dict):
                         logger.error(f"Cannot traverse path: '{part_name}' expected in a dictionary, but found type {type(target).__name__} for key '{key}' at path '{processed_path}'.")
                         return

                    # Determine if the *next* segment requires a list or dict based on its format
                    next_match = matches[i+1]
                    next_is_list_index = next_match.group(1) is not None
                    # next_is_dict_key = next_match.group(2) is not None # We assume next is key if not index

                    next_value = target.get(part_name, _MISSING)

                    if next_value is _MISSING:
                        # Key doesn't exist, create the necessary structure
                        if next_is_list_index:
                             # Cannot automatically create lists. Path is invalid if list doesn't exist.
                             logger.error(f"Path requires a list at intermediate key '{part_name}' for key '{key}', but the key does not exist. Cannot set value.")
                             return
                        else: # Assume next part needs a dict
                             # Create a new dict for the intermediate key
                             logger.debug(f"Creating nested dictionary for '{part_name}' in context key '{key}' at path '{processed_path}'")
                             target[part_name] = {}
                             target = target[part_name] # Traverse into the new dict
                    elif next_is_list_index and not isinstance(next_value, list):
                        # Key exists, but next part needs a list, and it's not a list
                        logger.error(f"Path requires a list at intermediate key '{part_name}' for key '{key}', but found type {type(next_value).__name__}. Cannot set value.")
                        return
                    elif not next_is_list_index and not isinstance(next_value, dict):
                         # Key exists, but next part needs a dict, and it's not a dict
                         logger.error(f"Path requires a dictionary at intermediate key '{part_name}' for key '{key}', but found type {type(next_value).__name__}. Cannot set value.")
                         return
                    else:
                         # Key exists and has the correct type (or it's the last part where type doesn't matter for traversal)
                         target = next_value # Traverse deeper


            else:
                 logger.error(f"Unexpected regex match state while setting key '{key}' at path '{processed_path}'.")
                 return

    except Exception as e:
        # Use self.config if available, otherwise assume False for debug logging here
        # Note: 'self' is not directly available in this static function context.
        # We might need to pass the debug flag if detailed exc_info is needed here.
        # For now, keep exc_info=False for brevity in production.
        logger.error(f"Unexpected error setting context key '{key}' at path '{processed_path}': {e}", exc_info=False)


# ---------------------------
# Flow Runner Class
# ---------------------------
from typing import Callable


class FlowRunner:
    """Executes flows continuously using asynchronous HTTP requests."""
    def __init__(
        self,
        config: ContainerConfig,
        flowmap: FlowMap,
        metrics: Metrics,
        *,
        on_iteration_start: Optional[Callable[[int, Dict[str, Any]], Any]] = None,
        run_once: bool = False,
    ):
        self.config = config
        self.flowmap = flowmap # This should be the validated Pydantic model instance
        self.metrics = metrics
        self.running = False # Indicates if the generator should actively run flows
        self.user_tasks: List[asyncio.Task] = []
        self._stopped_event: Optional[asyncio.Event] = None # To signal the main loop to stop
        # _active_users_count tracks actively running simulate_user_lifecycle coroutines
        self._active_users_count = 0
        self.lock = asyncio.Lock()  # Lock for managing user_tasks and _active_users_count
        self.on_iteration_start = on_iteration_start
        self.run_once = run_once

        self.configure_logging(self.config.debug)

        # --- Header/User-Agent Setup (Restored actual lists) ---
        self.user_agents_web = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 12_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.6 Safari/605.1.15",
            "Mozilla/5.0 (X11; Linux x86_64; rv:102.0) Gecko/20100101 Firefox/102.0",
            "Mozilla/5.0 (iPad; CPU OS 15_5 like Mac OS X) AppleWebKit/606.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/605.1.15",
            "Mozilla/5.0 (Android 12; Mobile; rv:102.0) Gecko/102.0 Firefox/102.0",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/606.1.15 (KHTML, like Gecko) Version/15.6 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv=109.0) Gecko/20100101 Firefox/115.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 13.5; rv=109.0) Gecko/20100101 Firefox/115.0",
            "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv=109.0) Gecko/20100101 Firefox/115.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36 Edg/116.0.1938.69",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15",
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Mobile Safari/537.36"
        ]
        self.user_agents_api = [
            "PostmanRuntime/7.29.0",
            "Python-requests/2.27.1",
            "curl/7.79.1",
            "Go-http-client/1.1",
            "Wget/1.20.3 (linux-gnu)",
            "Apache-HttpClient/4.5.13 (Java/11.0.15)",
            "axios/0.21.1 Node.js/v14.17.0",
            "Java/1.8.0_281",
            "libwww-perl/6.31",
            "HTTPie/2.5.0",
            "okhttp/4.9.1",
            "Faraday v2.7.10",
            "Dart/2.17 (dart:io)",
            "Xamarin/3.0.0 (Xamarin.Android; Android 13; SDK 33)",
            "Insomnia/2023.5.8",
            "Nodejs-v16.16.0",
            "Dalvik/2.1.0 (Linux; U; Android 13; SM-S918B Build/TP1A.220624.014)",
            "aws-sdk-js-2.1395.0",
            "Swift-URLSession",
            "ruby rest-client/2.1.0"
        ]
        self.headers_web_options = [
            {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.5", "Connection": "keep-alive", "Upgrade-Insecure-Requests": "1", "DNT": "1"},
            {"Accept": "application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8", "Accept-Language": "en-GB,en;q=0.5", "Connection": "keep-alive", "Upgrade-Insecure-Requests": "1", "DNT": "1", "Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "navigate"},
            {"Accept": "text/html,application/xhtml+xml", "Accept-Language": "fr-FR,fr;q=0.5", "Connection": "keep-alive", "Upgrade-Insecure-Requests": "1", "Cache-Control": "no-cache"},
            {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8", "Accept-Language": "de-DE,de;q=0.5", "Connection": "keep-alive", "Pragma": "no-cache", "Sec-Fetch-User": "?1"},
            {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9", "Accept-Language": "es-ES,es;q=0.5", "Connection": "keep-alive", "DNT": "1", "Sec-Fetch-Site": "cross-site"},
            {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9", "Accept-Language": "it-IT,it;q=0.5", "Connection": "keep-alive", "Upgrade-Insecure-Requests": "1", "Cache-Control": "max-age=0"},
            {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "ja-JP,ja;q=0.5", "Connection": "keep-alive", "Upgrade-Insecure-Requests": "1"},
            {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8", "Accept-Language": "ko-KR,ko;q=0.5", "Connection": "keep-alive", "Upgrade-Insecure-Requests": "1", "Sec-Fetch-Dest": "document"},
            {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9", "Accept-Language": "zh-CN,zh;q=0.5", "Connection": "keep-alive", "Pragma": "no-cache"},
            {"Accept": "application/xhtml+xml,application/xml,*/*;q=0.8", "Accept-Language": "ru-RU,ru;q=0.5", "Connection": "keep-alive", "DNT": "1"},
            {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-AU,en;q=0.5", "Connection": "keep-alive", "Upgrade-Insecure-Requests": "1", "Sec-Fetch-Mode": "navigate"},
            {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9", "Accept-Language": "en-CA,en;q=0.5", "Connection": "keep-alive", "Upgrade-Insecure-Requests": "1"},
            {"Accept": "application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8", "Accept-Language": "en-IE,en;q=0.5", "Connection": "keep-alive", "Sec-Fetch-Site": "none", "Cache-Control": "max-age=0"},
            {"Accept": "text/html,application/xhtml+xml", "Accept-Language": "sv-SE,sv;q=0.5", "Connection": "keep-alive", "DNT": "1", "Sec-Fetch-Dest": "document"},
            {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9", "Accept-Language": "pt-PT,pt;q=0.5", "Connection": "keep-alive", "Pragma": "no-cache", "Sec-Fetch-Mode": "navigate"},
            {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "nl-NL,nl;q=0.5", "Connection": "keep-alive", "Sec-Fetch-Site": "same-origin"},
            {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9", "Accept-Language": "pl-PL,pl;q=0.5", "Connection": "keep-alive", "Upgrade-Insecure-Requests": "1"},
            {"Accept": "application/json", "Accept-Language": "en-US,en;q=0.5", "Connection": "keep-alive", "X-Requested-With": "XMLHttpRequest"}
        ]
        self.headers_api_options = [
            {"Accept": "application/json", "Connection": "keep-alive", "Accept-Encoding": "gzip, deflate, br", "DNT": "1", "Cache-Control": "no-cache", "Pragma": "no-cache"},
            {"Accept": "application/xml", "Connection": "keep-alive", "Accept-Encoding": "gzip, deflate", "DNT": "1", "X-Requested-With": "XMLHttpRequest"},
            {"Accept": "*/*", "Connection": "keep-alive", "Accept-Encoding": "gzip, deflate", "Cache-Control": "no-cache", "X-Forwarded-Proto": "https"},
            {"Accept": "application/json, text/plain, */*", "Connection": "keep-alive", "Accept-Encoding": "gzip, deflate, br", "X-Real-IP": "192.0.2.123"},
            {"Accept": "application/json", "Connection": "keep-alive", "Accept-Encoding": "gzip, deflate", "User-Token": "randomtoken123456", "Forwarded": "for=198.51.100.50;proto=https"},
            {"Accept": "application/json", "Connection": "keep-alive", "Accept-Language": "en-US,en;q=0.5", "X-Trace-ID": "trace-56789", "X-Device-ID": "device-98765"},
            {"Accept": "application/vnd.api+json", "Connection": "keep-alive", "Authorization": "Bearer random_api_token", "X-API-Version": "2.0", "Accept-Encoding": "gzip, deflate, br"},
            {"Accept": "application/ld+json", "Connection": "keep-alive", "X-Correlation-ID": "some_correlation_id", "Content-Type": "application/json", "Accept-Encoding": "gzip, deflate"},
            {"Accept": "text/csv", "Connection": "keep-alive", "X-Auth-Token": "some_auth_token", "Accept-Encoding": "gzip, deflate, br", "Content-Type": "text/csv"},
            {"Accept": "application/x-www-form-urlencoded", "Connection": "keep-alive", "Accept-Encoding": "gzip, deflate", "X-Client-Version": "1.1.3", "Content-Type": "application/x-www-form-urlencoded"},
            {"Accept": "application/protobuf", "Connection": "keep-alive", "Content-Type": "application/protobuf", "Accept-Encoding": "gzip, deflate"},
            {"Accept": "application/octet-stream", "Connection": "keep-alive", "Content-Type": "application/octet-stream", "Accept-Encoding": "gzip, deflate, br"},
            {"Accept": "application/graphql", "Connection": "keep-alive", "Content-Type": "application/graphql", "Accept-Encoding": "gzip, deflate"},
            {"Accept": "text/plain", "Connection": "keep-alive", "Content-Type": "text/plain", "Accept-Encoding": "gzip, deflate, br"},
            {"Accept": "application/jwt", "Connection": "keep-alive", "Authorization": "Bearer some_jwt_token", "Accept-Encoding": "gzip, deflate"},
            {"Accept": "application/vnd.ms-excel", "Connection": "keep-alive", "Accept-Encoding": "gzip, deflate, br", "Content-Type": "application/vnd.ms-excel"},
            {"Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "Connection": "keep-alive", "Accept-Encoding": "gzip, deflate", "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
            {"Accept": "image/png", "Connection": "keep-alive", "Accept-Encoding": "gzip, deflate"},
            {"Accept": "image/jpeg", "Connection": "keep-alive", "Accept-Encoding": "gzip, deflate, br"},
            {"Accept": "image/gif", "Connection": "keep-alive", "Accept-Encoding": "gzip, deflate"},
            {"Accept": "application/pdf", "Connection": "keep-alive", "Accept-Encoding": "gzip, deflate, br"}
        ]
        # --- End of Header/User-Agent Setup ---


        # --- URL Parsing & DNS Override Setup ---
        try:
            self.parsed_url = urlparse(self.config.flow_target_url)
            if not self.parsed_url.scheme or not self.parsed_url.netloc:
                 raise ValueError("flow_target_url must be an absolute URL (e.g., 'http://example.com')")
        except Exception as e:
             logger.critical(f"Invalid flow_target_url: {self.config.flow_target_url}. Error: {e}")
             # Handle fatal configuration error - maybe raise exception?
             raise ValueError(f"Invalid flow_target_url: {self.config.flow_target_url}") from e

        self.original_host = self.parsed_url.hostname
        self.original_scheme = self.parsed_url.scheme
        self.default_port = 443 if self.original_scheme == 'https' else 80
        self.target_ip = self.config.flow_target_dns_override # Already validated by Pydantic

        logger.info(f"Flow Runner Initialized: Target='{self.config.flow_target_url}', Target Sim Users={self.config.sim_users}, DNS Override={self.target_ip or 'None'}, Debug={self.config.debug}")
        flow_name = getattr(self.flowmap, 'name', 'N/A')
        num_steps = len(self.flowmap.steps) if self.flowmap and self.flowmap.steps else 0
        logger.info(f"Flow Loaded: {flow_name} ({num_steps} top-level steps)")

    def configure_logging(self, debug: bool):
        """Configures the logger level based on the debug flag."""
        log_level = logging.DEBUG if debug else logging.INFO
        # Configure our specific logger
        logger.setLevel(log_level)
        # Also configure handlers attached to our logger
        for handler in logger.handlers:
            handler.setLevel(log_level)
        logger.info(f"Flow Generator logging level set to {logging.getLevelName(log_level)}")

    def create_aiohttp_connector(self) -> aiohttp.BaseConnector:
        """Creates an aiohttp connector, applying DNS override if configured."""
        resolver = None
        if self.target_ip:
            try:
                # Setup custom DNS resolver
                custom_resolver = aiohttp.resolver.AsyncResolver()
                override_host = self.original_host
                # Determine port correctly (handle default ports)
                override_port = self.parsed_url.port if self.parsed_url.port else self.default_port

                # Ensure target_ip is valid (already validated by Pydantic, but belt-and-suspenders)
                ip_address(self.target_ip)

                # Add the override for the specific host/port pair
                custom_resolver.add_override(override_host, override_port, self.target_ip)
                logger.info(f"DNS override configured: {override_host}:{override_port} -> {self.target_ip}")
                resolver = custom_resolver
            except Exception as e:
                 logger.error(f"Failed to configure DNS override for {self.original_host}:{self.parsed_url.port or self.default_port} -> {self.target_ip}. Error: {e}. Using default DNS.")
                 resolver = None # Fallback to default

        # Determine SSL context based on target scheme
        # Use None for default SSL context (recommended), False to disable SSL checks (use with caution)
        ssl_context = None if self.original_scheme == 'https' else False
        if self.original_scheme != 'https' and ssl_context is None:
            logger.warning(f"Target scheme is '{self.original_scheme}', but default SSL context is being used. Consider setting ssl=False in TCPConnector if HTTPS is not intended.")
        elif self.original_scheme == 'https':
             logger.debug("Using default SSL context for HTTPS target.")
        elif ssl_context is False:
             logger.debug("SSL verification disabled for HTTP target.")


        # Create the connector
        # limit_per_host: Max connections per host pooled by this connector instance.
        # limit: Total max connections pooled by this connector instance.
        # Increase limits if many users hit the same target simultaneously.
        connector_limit = max(100, self.config.sim_users * 2) # Example: allow more connections
        connector_limit_per_host = max(50, self.config.sim_users) # Example: allow more per host
        logger.debug(f"Creating TCPConnector: limit={connector_limit}, limit_per_host={connector_limit_per_host}, ssl={ssl_context is None}, resolver={'Custom' if resolver else 'Default'}")
        return aiohttp.TCPConnector(
            resolver=resolver,
            ssl=ssl_context,
            limit=connector_limit,
            limit_per_host=connector_limit_per_host,
            enable_cleanup_closed=True # Help clean up closed connections faster
            )

    def create_session(self, connector: aiohttp.BaseConnector) -> aiohttp.ClientSession:
        """Creates a new aiohttp ClientSession using the provided connector."""
        # Set reasonable timeouts
        timeout = aiohttp.ClientTimeout(
            total=60,       # Max total time for the entire request/response operation
            connect=10,     # Max time to establish connection
            sock_connect=5, # Max time to connect socket (part of connect timeout)
            sock_read=30    # Max time between receiving data chunks
        )
        # Create session WITHOUT cookie jar to ensure user isolation between flow runs
        # Connector lifecycle is managed externally (in simulate_user_lifecycle)
        return aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            cookie_jar=None, # Explicitly disable automatic cookie handling per session
            connector_owner=False # Important: Connector is shared and managed outside
        )

    async def start_generating(self):
        """Start all simulated user tasks and run continuously until stopped."""
        async with self.lock: # Protect access to running flag and user_tasks list
            if self.running:
                logger.warning("Flow generation is already running.")
                return
            self.running = True
            self._active_users_count = 0 # Reset counter
            self.user_tasks = []
            self._stopped_event = asyncio.Event() # Initialize the stop event

        logger.info(f"Starting {self.config.sim_users} simulated user tasks...")
        for i in range(self.config.sim_users):
            # Create task for each user
            task = asyncio.create_task(self.simulate_user_lifecycle(user_id=i))
            async with self.lock: # Protect appending to list
                 self.user_tasks.append(task)

        logger.info(f"{self.config.sim_users} user tasks created and started.")

        # Wait for the stop signal
        if self._stopped_event:
            logger.info("Flow runner main task waiting for stop signal...")
            try:
                await self._stopped_event.wait()
                logger.info("Flow runner main task received stop signal via event.")
            except asyncio.CancelledError:
                logger.info("Flow runner main task wait cancelled.")
                # Ensure event is set if cancelled externally, so stop_generating doesn't hang
                if self._stopped_event and not self._stopped_event.is_set():
                    self._stopped_event.set()
                # Don't re-raise cancellation here, let stop_generating handle cleanup
        else:
             logger.error("Stop event was not initialized correctly. Cannot wait for stop.")

        logger.debug("Flow runner start_generating coroutine finished.")
        # Cleanup and stopping is handled by stop_generating

    async def stop_generating(self):
        """Stops the flow generation process gracefully."""
        async with self.lock: # Protect access to running flag and user_tasks list
            if not self.running:
                logger.warning("Flow generation not running or already stopping.")
                # Ensure event is set just in case start_generating is somehow waiting
                if hasattr(self, '_stopped_event') and self._stopped_event and not self._stopped_event.is_set():
                    self._stopped_event.set()
                return

            logger.info("Stopping flow generation...")
            self.running = False # Signal loops within user tasks to stop

            # --- Signal the start_generating loop to stop waiting ---
            if hasattr(self, '_stopped_event') and self._stopped_event:
                 if not self._stopped_event.is_set():
                      logger.info("Setting stop event to unblock main generator task.")
                      self._stopped_event.set()
                 else:
                      logger.debug("Stop event was already set.")
            else:
                 logger.error("Stop called but _stopped_event was not initialized properly.")


            # --- Cancel Active User Tasks ---
            tasks_to_cancel = list(self.user_tasks) # Create copy for iteration
            self.user_tasks = [] # Clear the main list
            cancelled_count = 0
            tasks_to_wait_for = []

        logger.debug(f"Found {len(tasks_to_cancel)} tasks associated with this generator instance.")

        # Cancel tasks outside the lock to avoid holding it during cancellation/waiting
        for task in tasks_to_cancel:
            if task and not task.done():
                tasks_to_wait_for.append(task)
                if not task.cancelled(): # Avoid cancelling again if already cancelled
                    try:
                        task.cancel() # Request cancellation
                        cancelled_count += 1
                    except Exception as e:
                        logger.error(f"Error requesting cancellation for task {task}: {e}")
            elif task and task.done():
                 logger.debug(f"Task {task} was already done.")


        logger.info(f"Requested cancellation for {cancelled_count} active user tasks.")

        if tasks_to_wait_for:
            logger.debug(f"Waiting for {len(tasks_to_wait_for)} tasks to complete cancellation...")
            # Wait for tasks to finish (handle CancelledError or complete normally)
            results = await asyncio.gather(*tasks_to_wait_for, return_exceptions=True)
            logger.info(f"User tasks cancellation acknowledged or tasks finished gathering (Count: {len(results)}).")

            # Log any unexpected exceptions during shutdown
            for i, result in enumerate(results):
                 task_ref = tasks_to_wait_for[i] # Reference the original task if needed
                 if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                      logger.error(f"Task {i} (Ref: {task_ref}) finished with unexpected error during stop: {result}", exc_info=result if self.config.debug else False)
                 elif isinstance(result, asyncio.CancelledError):
                      logger.debug(f"Task {i} (Ref: {task_ref}) confirmed cancelled.")
                 # else: Task finished normally or was already done

        else:
            logger.info("No active user tasks needed cancellation or waiting.")

        # Reset active count explicitly after ensuring tasks are stopped/gathered
        final_count_before_reset = self.get_active_user_count() # Use getter for thread safety if needed elsewhere
        self._active_users_count = 0 # Direct assignment ok since we are managing stop sequence
        logger.info(f"Flow generation stopped. Reset active user count from {final_count_before_reset} to 0.")
        # Note: self.running flag is already False

    # ------------------------------------------------------------------
    # Compatibility helpers for continuous-run API
    # ------------------------------------------------------------------
    async def run(self):
        """Alias for start_generating to match older interface."""
        await self.start_generating()

    async def stop(self):
        """Alias for stop_generating for API clarity."""
        await self.stop_generating()

    async def reset(self):
        """Reset internal state without starting a new run."""
        await self.stop_generating()
        async with self.lock:
            self.user_tasks = []
            self._active_users_count = 0

    def get_active_user_count(self) -> int:
        """Returns the current count of active user simulation tasks."""
        # If accessed from multiple threads (e.g., metrics endpoint), might need lock,
        # but currently only accessed internally or via control thread which should be safe enough.
        return self._active_users_count

    def _substitute_variables(self, data: Union[str, Dict, List], context: Dict[str, Any]) -> Union[str, Dict, List, Any]:
        """
        Recursively substitutes variables in strings, dict keys/values, or list items.
        Handles {{varName.or[0].path}}, ##VAR:string:varName##, and ##VAR:unquoted:varName## formats.
        Uses the updated get_value_from_context which handles complex paths and returns _MISSING sentinel.
        """
        if isinstance(data, str):
            # Special Token Substitution (##VAR:##) - Primarily for body construction
            if data.startswith("##VAR:") and data.endswith("##"):
                try:
                    # Unpack the type and path, use maxsplit=1
                    parts = data[len("##VAR:"):-len("##")].split(":", 1)
                    if len(parts) != 2:
                        raise ValueError("Invalid ##VAR format, expected type:path")
                    var_type, var_path = parts

                    # Get value using the robust getter
                    value = get_value_from_context(context, var_path)

                    if value is _MISSING:
                        logger.warning(f"Variable path '{var_path}' in ##VAR token '{data}' not found in context. Substituting with empty string/None.")
                        return "" if var_type == "string" else None # Return None for unquoted missing var

                    # Perform substitution based on type
                    if var_type == "string":
                        # Substitute as a string representation
                        logger.debug(f"Substituting ##VAR:string:{var_path}## with string value: {str(value)}")
                        return str(value)
                    elif var_type == "unquoted":
                        # Return the raw Python value (int, float, bool, list, dict, str, None)
                        # The JSON encoder in _execute_request_step handles Python types.
                        logger.debug(f"Substituting ##VAR:unquoted:{var_path}## with raw value: {value} (type: {type(value).__name__})")
                        return value
                    else:
                        logger.warning(f"Unsupported ##VAR type: '{var_type}' in token '{data}'. Treating as string.")
                        return str(value) # Fallback to string representation
                except ValueError as e:
                    logger.warning(f"Malformed ##VAR token: {data}. Error: {e}. Returning as literal string.")
                    return data # Return original token on format error
                except Exception as e:
                     logger.error(f"Error processing ##VAR token '{data}': {e}", exc_info=self.config.debug)
                     return "" # Return empty string on unexpected error

            # Regular {{variable.or[0].path}} Substitution - For URLs, headers, string parts of body
            # This always results in a string substitution.
            pattern = r"\{\{([\w\.\[\]]+?)\}\}" # Non-greedy match inside braces
            new_string = data
            try:
                # Use finditer for non-overlapping matches and correct replacement
                matches = list(re.finditer(pattern, data))
                if not matches:
                     return data # No substitutions needed

                result_parts = []
                last_end = 0
                for match in matches:
                    var_path = match.group(1).strip() # Get path and strip whitespace
                    start, end = match.span()

                    # Append the literal text before the match
                    result_parts.append(data[last_end:start])

                    # Get the value from context
                    value = get_value_from_context(context, var_path)

                    # Determine the string representation for substitution
                    if value is _MISSING:
                         logger.warning(f"Variable '{{{{{var_path}}}}}' not found in context. Substituting with empty string.")
                         value_str = ""
                    elif value is None:
                         value_str = "" # Substitute None as empty string in {{}} context
                    else:
                         value_str = str(value) # Convert other types to string

                    # Append the substituted value string
                    result_parts.append(value_str)
                    last_end = end

                # Append any remaining literal text after the last match
                result_parts.append(data[last_end:])
                new_string = "".join(result_parts)

            except re.error as e:
                 logger.error(f"Regex error during variable substitution on '{data}': {e}")
                 return data # Return original on regex failure
            except Exception as e:
                 # Catch potential errors from get_value_from_context or string conversion
                 logger.error(f"Unexpected error during variable substitution on '{data}': {e}", exc_info=self.config.debug)
                 return data # Return original on other failures

            # Log substitution only if it changed and debug is enabled
            if logger.isEnabledFor(logging.DEBUG) and new_string != data:
                original_preview = data[:100] + ('...' if len(data) > 100 else '')
                new_preview = new_string[:100] + ('...' if len(new_string) > 100 else '')
                logger.debug(f"Substituted: '{original_preview}' -> '{new_preview}'")
            return new_string

        elif isinstance(data, dict):
            # Recursively substitute in dictionary keys and values
            # Note: Substituting keys might have unintended consequences if keys become non-strings.
            # Let's assume keys remain strings after substitution for simplicity.
            return {
                self._substitute_variables(key, context): self._substitute_variables(val, context)
                for key, val in data.items()
            }
        elif isinstance(data, list):
            # Recursively substitute in list items
            return [self._substitute_variables(item, context) for item in data]
        else:
            # Return non-substitutable types as is (int, float, bool, None, etc.)
            return data

    # ==========================================================================
    # == Condition Evaluation Logic (UPDATED FOR ROBUSTNESS & TYPE SAFETY) ==
    # ==========================================================================

    def _evaluate_structured_condition(self, condition_data: ConditionData, context: Dict[str, Any]) -> bool:
        """
        Evaluates a condition based on the structured ConditionData model.
        Prioritizes safe type coercion for comparisons, especially for status codes.
        Uses the updated get_value_from_context (returns _MISSING sentinel).
        """
        if not condition_data:
            logger.warning("Structured condition data is missing, defaulting to false")
            return False

        variable_path = condition_data.variable.strip()
        operator = condition_data.operator.strip()
        # value_str is the comparison value from the flowmap (always string initially)
        value_str = condition_data.value if condition_data.value is not None else "" # Ensure string or ""

        if not variable_path or not operator:
            logger.warning(f"Missing required condition fields: variable='{variable_path}', operator='{operator}'. Defaulting to False.")
            return False

        # Get the actual value from context using the path
        left_value = get_value_from_context(context, variable_path)

        # --- Handle Case: Variable Not Found in Context ---
        if left_value is _MISSING:
            logger.warning(f"Condition variable '{variable_path}' not found in context.")
            # How to evaluate if variable doesn't exist?
            # 'exists' -> False, 'not_exists' -> True. Others depend... Let's treat as None for comparison.
            # This matches JS behavior where undefined acts like null in some comparisons.
            left_value = None # Treat missing variable as None for evaluation consistency

        log_left_repr = repr(left_value)[:100] + ('...' if len(repr(left_value)) > 100 else '')
        logger.debug(f"Evaluating structured condition: ContextVar '{variable_path}' (Value: {log_left_repr}, Type: {type(left_value).__name__}) | Operator: '{operator}' | ComparisonValue: '{value_str}'")

        # --- Evaluate based on operator ---
        try:
            result = False # Default result

            # --- Existence Checks (Robust to _MISSING via None conversion above) ---
            if operator == 'exists': # Equivalent to JS `!= null` (checks not null/undefined)
                result = left_value is not None
            elif operator == 'not_exists': # Equivalent to JS `== null` (checks null/undefined)
                result = left_value is None

            # --- Type Checks ---
            elif operator == 'is_number':
                 # Exclude bools, check for NaN floats
                 result = isinstance(left_value, (int, float)) and not isinstance(left_value, bool) and not (isinstance(left_value, float) and math.isnan(left_value))
            elif operator == 'is_text':
                 result = isinstance(left_value, str)
            elif operator == 'is_boolean':
                 result = isinstance(left_value, bool)
            elif operator == 'is_array': # Python list corresponds to JS array
                 result = isinstance(left_value, list)
            # Optional: is_object (Python dict)
            # elif operator == 'is_object': result = isinstance(left_value, dict)

            # --- Boolean Value Checks ---
            elif operator == 'is_true': # Strict check
                 result = left_value is True
            elif operator == 'is_false': # Strict check
                 result = left_value is False

            # --- Operators Requiring Comparison Value (value_str) ---
            else:
                # --- Comparison Logic with Type Coercion ---
                # Try to coerce right side (value_str) towards left_value's type for comparison
                coerced_right = _MISSING # Use sentinel for failed coercion attempt
                can_compare_numerically = False

                if isinstance(left_value, (int, float)) and not isinstance(left_value, bool):
                    # Try converting value_str to number (int first, then float)
                    try: coerced_right = int(value_str)
                    except (ValueError, TypeError):
                        try: coerced_right = float(value_str)
                        except (ValueError, TypeError): pass # Coercion failed
                    if coerced_right is not _MISSING:
                         can_compare_numerically = True
                         logger.debug(f"Coerced comparison value '{value_str}' to numeric type {type(coerced_right).__name__}")

                elif isinstance(left_value, bool):
                    # Try converting value_str to boolean ('true'/'false')
                    val_str_lower = value_str.lower()
                    if val_str_lower == 'true': coerced_right = True
                    elif val_str_lower == 'false': coerced_right = False
                    # else: coercion failed

                # String/List/Dict comparisons typically use value_str directly

                # --- Perform Comparison ---
                if operator == 'equals':
                    # 1. Try comparing with coerced type if successful
                    if coerced_right is not _MISSING and type(left_value) is type(coerced_right):
                        result = (left_value == coerced_right)
                    # 2. Fallback: Compare string representations (useful for int 200 vs str "200")
                    #    Only do this if coercion didn't produce the same type.
                    #    Avoid for list/dict comparisons.
                    elif isinstance(left_value, (int, float, bool, str, type(None))):
                         result = (str(left_value) == value_str)
                    # 3. Final check for None equality if left_value is None
                    elif left_value is None:
                         result = (value_str.lower() == 'null' or value_str.lower() == 'none' or value_str == "") # Check common None representations
                    # else: Mismatched types where coercion/string comp doesn't apply -> False

                elif operator == 'not_equals':
                    # Similar logic to equals, but inverted
                    if coerced_right is not _MISSING and type(left_value) is type(coerced_right):
                        result = (left_value != coerced_right)
                    elif isinstance(left_value, (int, float, bool, str, type(None))):
                         result = (str(left_value) != value_str)
                    elif left_value is None:
                         result = not (value_str.lower() == 'null' or value_str.lower() == 'none' or value_str == "")
                    else: # Mismatched types -> True
                        result = True

                # --- Numeric Comparisons (Requires successful numeric coercion) ---
                elif operator in ('greater_than', 'less_than', 'greater_equals', 'less_equals'):
                    if can_compare_numerically:
                        # We already established left_value is number and coerced_right is number
                        if operator == 'greater_than': result = left_value > coerced_right
                        elif operator == 'less_than': result = left_value < coerced_right
                        elif operator == 'greater_equals': result = left_value >= coerced_right
                        elif operator == 'less_equals': result = left_value <= coerced_right
                    else:
                        logger.warning(f"Cannot perform numeric comparison '{operator}' because context value ({type(left_value).__name__}) or comparison value ('{value_str}') is not a compatible number.")
                        result = False # Comparison fails if types aren't numeric

                # --- String/Collection Operators ---
                elif operator == 'contains':
                    if isinstance(left_value, str): result = value_str in left_value
                    elif isinstance(left_value, list): result = value_str in left_value or (coerced_right is not _MISSING and coerced_right in left_value) # Check raw string or coerced value
                    elif isinstance(left_value, dict): result = value_str in left_value # Check if value_str is a key
                    else: result = False # Cannot perform contains on numbers, bools, None

                elif operator == 'starts_with':
                    result = isinstance(left_value, str) and left_value.startswith(value_str)
                elif operator == 'ends_with':
                    result = isinstance(left_value, str) and left_value.endswith(value_str)

                elif operator == 'matches_regex':
                    if isinstance(left_value, str) and value_str:
                         try: result = bool(re.search(value_str, left_value))
                         except re.error as e: logger.error(f"Invalid regex pattern '{value_str}' in condition: {e}"); result = False
                    else: result = False # Regex only applicable to strings

                # --- Unknown Operator ---
                else:
                    logger.warning(f"Unknown structured condition operator: '{operator}'. Defaulting to False.")
                    result = False


            logger.debug(f"Condition evaluated to: {result}")
            return result

        except Exception as e:
            # Catch-all for unexpected errors during evaluation
            logger.error(f"Error evaluating structured condition: {variable_path} {operator} {value_str}. Error: {e}", exc_info=self.config.debug)
            return False


    def _evaluate_condition(self, condition_str: Optional[str], context: Dict[str, Any], condition_data: Optional[ConditionData] = None) -> bool:
        """
        Evaluates a condition. Prefers structured data (conditionData) if available and valid,
        otherwise falls back to parsing the legacy condition string (basic comparison only).
        """
        # --- Preferred Method: Structured Data ---
        # Check for valid structured data (variable and operator must be present)
        if condition_data and condition_data.variable and condition_data.operator:
            logger.debug(f"Using structured condition data for evaluation (Variable: '{condition_data.variable}', Operator: '{condition_data.operator}').")
            return self._evaluate_structured_condition(condition_data, context)

        # --- Fallback Method: Legacy String Parsing ---
        if not condition_str:
             # If no structured data and no legacy string, condition is effectively false/cannot be evaluated
             logger.warning("Condition evaluation failed: No valid structured data and no legacy condition string provided. Defaulting to False.")
             return False

        # Proceed with legacy parsing only if structured data was unusable
        logger.warning(f"Evaluating condition using legacy string parsing (less reliable): '{condition_str}'. Consider updating flow to use structured 'conditionData'.")
        # Substitute variables within the legacy string first
        # Note: Legacy substitution might be less robust than structured evaluation
        try:
            substituted_condition = self._substitute_variables(condition_str, context)
            if not isinstance(substituted_condition, str):
                 logger.error(f"Legacy condition '{condition_str}' substitution resulted in non-string type: {type(substituted_condition)}. Cannot evaluate.")
                 return False
            logger.debug(f"Substituted legacy condition: '{substituted_condition}'")
        except Exception as e:
             logger.error(f"Error substituting variables in legacy condition '{condition_str}': {e}. Cannot evaluate.")
             return False


        # Simplified Regex: Looks for operand, operator, operand. Handles optional quotes.
        # WARNING: This is fragile. Structured conditions are strongly preferred.
        match = re.match(r"""^\s*  # Start of string, optional whitespace
               (.*?)\s*   # Left operand (non-greedy)
               (?:(===|==|!==|!=|>|<|>=|<=)\s*(.*?))?  # Optional: Operator and Right operand
               \s*$       # End of string, optional whitespace""",
               substituted_condition, re.VERBOSE | re.DOTALL)


        if not match:
            logger.error(f"Could not parse legacy condition structure: '{substituted_condition}'. Defaulting to False.")
            return False

        left_str = match.group(1) # Always present
        op = match.group(2)       # Optional: Operator
        right_str = match.group(3) # Optional: Right operand

        # --- Evaluate Truthiness if no operator found ---
        if op is None:
            val_str = left_str.strip().lower()
            if val_str in ('false', 'null', 'none', '', 'undefined', '0'): # Consider '0' as falsy
                result = False
            else:
                # Attempt numeric conversion for non-zero numbers
                try: result = bool(float(val_str)) # '0.0' is False
                except ValueError: result = True # Non-empty, non-keyword, non-numeric string is true
            logger.debug(f"Legacy condition '{substituted_condition}' evaluated as simple truthiness: {result}")
            return result

        # --- Evaluate Comparison if operator exists ---
        # Basic interpretation function (highly simplified)
        def interpret_legacy(operand_str):
            operand_str = operand_str.strip()
            if (operand_str.startswith('"') and operand_str.endswith('"')) or \
               (operand_str.startswith("'") and operand_str.endswith("'")):
                return operand_str[1:-1] # String literal
            op_lower = operand_str.lower()
            if op_lower == 'true': return True
            if op_lower == 'false': return False
            if op_lower in ('null', 'none', 'undefined'): return None
            try: return int(operand_str) # Try int
            except ValueError:
                try: return float(operand_str) # Try float
                except ValueError: return operand_str # Default to string

        left_val = interpret_legacy(left_str)
        right_val = interpret_legacy(right_str)

        logger.debug(f"Legacy interpreted: Left='{left_val}' ({type(left_val).__name__}), Op='{op}', Right='{right_val}' ({type(right_val).__name__})")

        try:
            if op == '===': result = (type(left_val) is type(right_val)) and (left_val == right_val)
            elif op == '==': result = (left_val == right_val) # Python's loose equality
            elif op == '!==': result = (type(left_val) is not type(right_val)) or (left_val != right_val)
            elif op == '!=': result = (left_val != right_val)
            # For numeric comparisons, rely on Python's type checking (will raise TypeError if incompatible)
            elif op == '>': result = left_val > right_val
            elif op == '<': result = left_val < right_val
            elif op == '>=': result = left_val >= right_val
            elif op == '<=': result = left_val <= right_val
            else: logger.warning(f"Unknown legacy operator '{op}'. Defaulting to False."); result = False
            logger.debug(f"Legacy condition comparison result: {result}")
            return result
        except TypeError:
            # Incompatible types for >, <, >=, <=. For ==, !=, Python handles some coercion.
            # Let's default to False for comparison errors in legacy mode.
            logger.warning(f"Type mismatch or incompatible types for legacy operator '{op}' between {type(left_val).__name__} and {type(right_val).__name__}. Comparison fails (False).")
            return False
        except Exception as e:
            logger.error(f"Unexpected error evaluating legacy comparison '{substituted_condition}': {e}", exc_info=self.config.debug)
            return False

    # ==========================================================================
    # == End of Updated Condition Evaluation Logic ==
    # ==========================================================================

    def _extract_data(self, response_data: Any, extract_rules: Dict[str, str], context: Dict[str, Any], response_status: int, response_headers: Dict[str, Any]):
        """
        Extracts data from response body, status, or headers based on rules and updates context.
        Uses get_value_from_context (returns _MISSING sentinel).
        Logs success/failure clearly.
        MODIFIED: Extracts status code only on literal ".status", treats "status" as body path.
        """
        if not extract_rules:
            return

        # Case-insensitive lookup dictionary for headers
        ci_headers = {k.lower(): v for k, v in response_headers.items()}

        for var_name, path_expr in extract_rules.items():
            if not var_name: logger.warning("Skipping extraction rule with empty variable name."); continue
            if not path_expr: logger.warning(f"Skipping extraction rule for '{var_name}' with empty path expression."); continue

            extracted_value = _MISSING # Use sentinel for initial state
            source_description = "unknown" # For logging

            try:
                # --- Determine Source and Path ---
                path_lower = path_expr.lower() # Use lowercase for special keyword checks
                effective_path = path_expr # Default path

                # CHANGE 1: Check for literal ".status" for status code extraction
                if path_expr == '.status': # Check exact literal ".status"
                    source_description = "status code"
                    extracted_value = response_status
                    effective_path = '.status' # For clarity
                elif path_lower.startswith("headers."):
                    source_description = "headers"
                    effective_path = path_expr[len("headers."):]
                    if not effective_path:
                         logger.warning(f"Invalid extraction path '{path_expr}' for variable '{var_name}'. Needs key after 'headers.'.")
                         extracted_value = None # Explicitly set to None on path error
                    else:
                         # Use case-insensitive lookup on our prepared dict
                         extracted_value = ci_headers.get(effective_path.lower(), _MISSING) # Use _MISSING if header not found
                elif path_lower == "body":
                     source_description = "body"
                     extracted_value = response_data
                     effective_path = "body"
                elif path_lower.startswith("body."):
                     source_description = "body"
                     effective_path = path_expr[len("body."):]
                     if not effective_path:
                          logger.warning(f"Invalid extraction path '{path_expr}' for variable '{var_name}'. Needs key after 'body.'.")
                          extracted_value = None
                     else:
                          # Use get_value_from_context on the response body
                          extracted_value = get_value_from_context(response_data, effective_path)
                else:
                     # Default: Assume path refers to the response body
                     # This now correctly handles the literal "status" (not ".status") as a body path
                     source_description = "body (default)"
                     effective_path = path_expr
                     extracted_value = get_value_from_context(response_data, effective_path)

                # --- Log and Update Context ---
                if extracted_value is not _MISSING:
                    log_val_repr = repr(extracted_value)
                    log_val_display = f"{log_val_repr[:100]}{'...' if len(log_val_repr) > 100 else ''}"
                    logger.debug(f"Extracted '{path_expr}' (from {source_description}) into context variable '{var_name}': {log_val_display} ({type(extracted_value).__name__})")
                    set_value_in_context(context, var_name, extracted_value) # Set the actual extracted value
                else:
                    # Log failure clearly: path not found within the specified source
                    source_type = type(response_data).__name__ if source_description.startswith("body") else \
                                  type(response_headers).__name__ if source_description == "headers" else \
                                  type(response_status).__name__ if source_description == "status code" else "N/A"
                    logger.warning(f"Extraction failed: Path '{effective_path}' for variable '{var_name}' not found in response {source_description} (source type: {source_type}) or source was invalid.")
                    # Set context variable to None when extraction path fails
                    set_value_in_context(context, var_name, None)

            except Exception as e:
                logger.error(f"Unexpected error during extraction for variable '{var_name}' with path '{path_expr}': {e}", exc_info=self.config.debug)
                # Set context variable to None on unexpected error
                set_value_in_context(context, var_name, None)


    async def _execute_request_step(
        self,
        step: RequestStep,
        session: aiohttp.ClientSession,
        base_headers: Dict[str, str], # User session headers
        flow_headers: Dict[str, str], # Global flow headers (already substituted)
        context: Dict[str, Any]       # Current execution context
    ) -> bool: # Return True if step executed successfully (request sent/received), False if skipped/failed internally before request
        """
        Executes a single RequestStep. Handles request preparation, execution, retries,
        status code checking based on onFailure, context updates, and data extraction.
        Returns True on successful execution (request sent/received, including >=300 if onFailure='continue'),
        False if skipped due to pre-request errors or stopped due to onFailure='stop'.
        MODIFIED: Implements onFailure logic for status codes >= 300.
        """
        step_identifier = f"'{step.name}' ({step.id})" if step.name else f"({step.id})"
        context_prefix = f'response_{step.id}' # Prefix for storing response info

        # --- Prepare Request ---
        try:
            method = step.method
            # Substitute variables in URL path, step-specific headers, and body
            # Global flow headers are assumed to be already substituted by _execute_steps
            url_path_substituted = self._substitute_variables(step.url, context)
            step_headers_substituted = self._substitute_variables(step.headers or {}, context)
            step_body_substituted = self._substitute_variables(step.body, context) # Handles ##VAR tokens

            if not isinstance(url_path_substituted, str):
                logger.error(f"Step {step_identifier}: URL substitution resulted in non-string: {type(url_path_substituted)}. Skipping request.")
                set_value_in_context(context, f'{context_prefix}_status', 599)
                set_value_in_context(context, f'{context_prefix}_error', f"URL substitution failed: type {type(url_path_substituted)}")
                return False # Indicate internal failure

            final_url = ""
            host_header_override = None

            # --- Build Final URL & Handle DNS Override ---
            try:
                parsed_substituted_url = urlparse(url_path_substituted)
            except ValueError as e:
                logger.error(f"Step {step_identifier}: Invalid URL format after substitution: '{url_path_substituted}'. Error: {e}. Skipping request.")
                set_value_in_context(context, f'{context_prefix}_status', 599)
                set_value_in_context(context, f'{context_prefix}_error', f"Invalid URL format: {url_path_substituted}")
                return False

            if self.config.override_step_url_host:
                step_path = parsed_substituted_url.path or '/'
                if not step_path.startswith('/'):
                    step_path = '/' + step_path
                step_query = parsed_substituted_url.query
                step_fragment = parsed_substituted_url.fragment

                if self.target_ip:
                    port = self.parsed_url.port or self.default_port
                    ip_part = f"[{self.target_ip}]" if ':' in self.target_ip else self.target_ip
                    netloc = (
                        f"{ip_part}:{port}" if (self.original_scheme == 'https' and port != 443) or (self.original_scheme == 'http' and port != 80) else ip_part
                    )
                    host_header_override = self.original_host
                else:
                    netloc = self.parsed_url.netloc

                final_url = urlunparse((self.original_scheme, netloc, step_path, '', step_query, step_fragment))
                logger.debug(
                    f"Step {step_identifier}: URL Override Active. Final URL: {final_url}" +
                    (f" (Host header: {host_header_override})" if host_header_override else "")
                )
            else:
                if parsed_substituted_url.scheme and parsed_substituted_url.netloc:
                    final_url = url_path_substituted
                    step_host = parsed_substituted_url.hostname
                    if self.target_ip and step_host == self.original_host:
                        port = parsed_substituted_url.port or (443 if parsed_substituted_url.scheme == 'https' else 80)
                        ip_part = f"[{self.target_ip}]" if ':' in self.target_ip else self.target_ip
                        netloc = (
                            f"{ip_part}:{port}" if (parsed_substituted_url.scheme == 'https' and port != 443) or (parsed_substituted_url.scheme == 'http' and port != 80) else ip_part
                        )
                        final_url = urlunparse(
                            (parsed_substituted_url.scheme, netloc, parsed_substituted_url.path or '/', parsed_substituted_url.params, parsed_substituted_url.query, parsed_substituted_url.fragment)
                        )
                        host_header_override = step_host
                        logger.debug(
                            f"Step {step_identifier}: DNS override applied to absolute URL -> {final_url} (Host: {host_header_override})"
                        )
                    else:
                        logger.debug(f"Step {step_identifier}: Using absolute URL '{final_url}' directly (override inactive).")
                else:
                    base_url_config = self.config.flow_target_url.rstrip('/')
                    path_part = url_path_substituted.lstrip('./')

                    required_params = re.findall(r"\{\{([\w\.\[\]]+?)\}\}", step.url)
                    if required_params and path_part.endswith('/') and not step.url.rstrip('/').endswith('/'):
                        missing_param_found = False
                        for param in required_params:
                            param_val = get_value_from_context(context, param)
                            if param_val is _MISSING or param_val is None or param_val == "":
                                if step.url.rstrip('/').endswith(f"{{{{{param}}}}}"):
                                    logger.error(
                                        f"Step {step_identifier}: URL path parameter '{{{{{param}}}}}' is missing or empty after substitution ('{url_path_substituted}'). Skipping request."
                                    )
                                    set_value_in_context(context, f'{context_prefix}_status', 599)
                                    set_value_in_context(context, f'{context_prefix}_error', f"Missing URL path parameter '{param}' in '{url_path_substituted}'")
                                    missing_param_found = True
                                    break
                        if missing_param_found:
                            return False

                    if self.target_ip:
                        port = self.parsed_url.port or self.default_port
                        ip_part = f"[{self.target_ip}]" if ':' in self.target_ip else self.target_ip
                        netloc = (
                            f"{ip_part}:{port}" if (self.original_scheme == 'https' and port != 443) or (self.original_scheme == 'http' and port != 80) else ip_part
                        )
                        final_url = urlunparse((self.original_scheme, netloc, path_part, '', '', ''))
                        host_header_override = self.original_host
                        logger.debug(
                            f"Step {step_identifier}: Applying DNS override to relative path '{path_part}' -> {final_url} (Host: {host_header_override})"
                        )

                    else:
                        final_url = f"{base_url_config}/{path_part}"
                        logger.debug(
                            f"Step {step_identifier}: Using relative path '{path_part}' with base URL -> {final_url}"
                        )

            # --- Encode Query Parameter Values to Avoid WAF Issues ---
            try:
                if self.config.debug:
                    logger.debug(
                        f"Step {step_identifier}: URL before query re-encoding: {final_url}"
                    )
                parsed_final = urlparse(final_url)
                if parsed_final.query:
                    safe_qs = parsed_final.query.replace('+', '%2B')
                    pairs = parse_qsl(safe_qs, keep_blank_values=True)
                    encoded_query = urlencode(pairs, doseq=True, quote_via=quote)
                    final_url = urlunparse(
                        (
                            parsed_final.scheme,
                            parsed_final.netloc,
                            parsed_final.path,
                            parsed_final.params,
                            encoded_query,
                            parsed_final.fragment,
                        )
                    )
                    if self.config.debug:
                        logger.debug(
                            f"Step {step_identifier}: URL after query re-encoding: {final_url}"
                        )
            except Exception as enc_err:
                logger.error(
                    f"Step {step_identifier}: Error re-encoding query parameters: {enc_err}",
                    exc_info=self.config.debug,
                )


            # --- Combine Headers ---
            final_headers = base_headers.copy() # Start with user session headers
            final_headers.update(flow_headers) # Add/override with global flow headers
            # Ensure step headers are dicts before updating
            if isinstance(step_headers_substituted, dict):
                final_headers.update(step_headers_substituted) # Add/override with step-specific headers
            elif step_headers_substituted:
                 logger.warning(f"Step {step_identifier}: Step headers substitution resulted in non-dict: {type(step_headers_substituted)}. Ignoring step headers.")

            if host_header_override:
                final_headers['Host'] = host_header_override # Apply Host override if needed


            # --- Prepare Request Body ---
            data_payload = None
            json_payload = None
            if step_body_substituted is not None:
                content_type = final_headers.get('Content-Type', '').lower()
                is_json_content_type = 'application/json' in content_type

                # If body is dict/list (likely from ##VAR:unquoted##), treat as JSON
                if isinstance(step_body_substituted, (dict, list)):
                    json_payload = step_body_substituted
                    if not is_json_content_type:
                        final_headers['Content-Type'] = 'application/json; charset=utf-8'
                        logger.debug(f"Step {step_identifier}: Automatically set Content-Type to application/json for dict/list body.")
                elif isinstance(step_body_substituted, str):
                    # Body is string. Check Content-Type.
                    if is_json_content_type:
                        # Try to parse string as JSON if Content-Type suggests it
                        try:
                            json_payload = json.loads(step_body_substituted)
                            logger.debug(f"Step {step_identifier}: Parsed string body as JSON based on Content-Type.")
                        except json.JSONDecodeError:
                            logger.warning(f"Step {step_identifier}: Content-Type is JSON, but body is not valid JSON. Sending as raw string data.")
                            data_payload = step_body_substituted.encode('utf-8', errors='replace')
                    else:
                        # Content-Type is not JSON, send string as raw data
                        data_payload = step_body_substituted.encode('utf-8', errors='replace')
                        # Set default Content-Type if missing? Maybe application/x-www-form-urlencoded?
                        # Let's avoid setting default here unless explicitly needed.
                        # if 'Content-Type' not in final_headers:
                        #    final_headers['Content-Type'] = 'text/plain; charset=utf-8' # Safer default?
                else:
                    # Body is some other type after substitution (e.g., int, bool). This is usually unexpected.
                    logger.warning(f"Step {step_identifier}: Unsupported body type after substitution: {type(step_body_substituted)}. Sending as string representation.")
                    data_payload = str(step_body_substituted).encode('utf-8', errors='replace')


            if logger.isEnabledFor(logging.DEBUG):
                log_headers = {k: ('********' if isinstance(v, str) and (k.lower() == 'authorization' or k.lower() == 'cookie') and v else v) for k, v in final_headers.items()}
                log_payload_summary = "None"
                if json_payload is not None:
                     try: payload_str = json.dumps(json_payload); log_payload_summary = f"JSON: {payload_str[:200]}{'...' if len(payload_str) > 200 else ''}"
                     except Exception: log_payload_summary = "JSON: (serialization error)"
                elif data_payload:
                     try: payload_str = data_payload.decode('utf-8', errors='replace'); log_payload_summary = f"Data[{len(data_payload)} bytes]: {payload_str[:200]}{'...' if len(payload_str) > 200 else ''}"
                     except Exception: log_payload_summary = f"Data[{len(data_payload)} bytes]: (binary or decode error)"
                logger.debug(f"\n--- REQUEST START ---\n"
                             f"Step ID: {step.id} Name: {step.name or 'N/A'}\n"
                             f"URL: {method} {final_url}\n"
                             f"Headers: {log_headers}\n"
                             f"Payload: {log_payload_summary}\n"
                             f"---------------------")

        except Exception as prep_err:
             logger.error(f"Step {step_identifier}: Unexpected error during request preparation: {prep_err}", exc_info=self.config.debug)
             set_value_in_context(context, f'{context_prefix}_status', 599)
             set_value_in_context(context, f'{context_prefix}_error', f"Request preparation error: {prep_err}")
             return False # Indicate internal failure


        # --- Execute Request with Retries ---
        response_body = None
        response_headers_dict = {}
        response_status = -1 # Default for unexpected failure before getting status
        error_message = None
        request_succeeded = False # Track if request completed (even with 4xx/5xx)

        max_retries = 3 # Retries for connection errors or 5xx server errors
        base_retry_delay = 0.5 # seconds

        for attempt in range(max_retries):
            request_start_time = time.monotonic()
            try:
                async with session.request(
                    method,
                    final_url,
                    headers=final_headers,
                    json=json_payload,
                    data=data_payload
                    # Note: ssl handling is done via connector settings
                ) as resp:
                    # --- Process Response ---
                    response_status = resp.status
                    # Convert response headers (CIMultiDict) to a simple dict for context storage
                    # Handle multiple Set-Cookie headers if needed later, for now just last value.
                    response_headers_dict = {k: v for k, v in resp.headers.items()}
                    request_duration_s = time.monotonic() - request_start_time
                    request_succeeded = True # Mark that we got a response

                    # --- Read Response Body ---
                    response_body = None # Reset for this attempt
                    try:
                        resp_content_type = resp.headers.get('Content-Type', '').lower()
                        if 'application/json' in resp_content_type:
                             try: response_body = await resp.json(encoding='utf-8')
                             except (json.JSONDecodeError, UnicodeDecodeError, aiohttp.ContentTypeError) as json_err:
                                 logger.warning(f"Step {step_identifier}: Failed to decode JSON response ({resp.status}) despite Content-Type. Error: {json_err}. Reading as text.")
                                 # Fallback: read as text
                                 response_body = await resp.text(encoding='utf-8', errors='replace')
                        elif resp_content_type.startswith('text/'):
                             response_body = await resp.text(encoding='utf-8', errors='replace')
                        else:
                             # Read non-text types as bytes, store placeholder
                             raw_bytes = await resp.read()
                             limit = 100
                             if len(raw_bytes) > limit: response_body = f"[Body Binary Data - Type: {resp_content_type}, Size: {len(raw_bytes)} bytes, Starts: {raw_bytes[:limit]!r}...]"
                             else: response_body = f"[Body Binary Data - Type: {resp_content_type}, Size: {len(raw_bytes)} bytes, Data: {raw_bytes!r}]"
                             logger.debug(f"Step {step_identifier}: Read {len(raw_bytes)} bytes for Content-Type: {resp_content_type}")

                    except aiohttp.ClientPayloadError as payload_err:
                        logger.error(f"Step {step_identifier}: Payload error reading response body ({resp.status}): {payload_err}")
                        response_body = f"Error reading response body: {payload_err}"
                    except Exception as body_err:
                        logger.error(f"Step {step_identifier}: Generic error reading response body ({resp.status}): {body_err}", exc_info=self.config.debug)
                        response_body = f"Generic error reading response body: {body_err}"


                    # --- Log Response ---
                    log_level = logging.WARNING if response_status >= 400 else logging.INFO
                    logger.log(log_level, f"Step {step_identifier} received: {response_status} {method} {final_url} ({request_duration_s*1000:.2f} ms)")

                    if logger.isEnabledFor(logging.DEBUG):
                        log_body_repr = repr(response_body)
                        log_body_display = f"{log_body_repr[:250]}{'...' if len(log_body_repr) > 250 else ''}"
                        log_resp_headers = {k: ('********' if k.lower() == 'set-cookie' and v else v) for k, v in response_headers_dict.items()}
                        logger.debug(f"  Response Headers: {log_resp_headers}")
                        logger.debug(f"  Response Body ({type(response_body).__name__}): {log_body_display}")

                    # --- Retry Logic (Retry on 5xx server errors) ---
                    if response_status >= 500 and attempt < max_retries - 1:
                        retry_delay = base_retry_delay * (2 ** attempt) # Exponential backoff
                        logger.warning(f"Step {step_identifier}: Server error {response_status} on attempt {attempt+1}/{max_retries}. Retrying in {retry_delay:.2f}s...")
                        await asyncio.sleep(retry_delay)
                        request_succeeded = False # Reset success flag for retry
                        continue # Go to next attempt

                    # If not retrying (success, 4xx, or 5xx on last attempt), break the loop
                    break

            # --- Handle Connection/Timeout Errors ---
            except (aiohttp.ClientConnectionError, aiohttp.ClientConnectorError, asyncio.TimeoutError) as conn_err:
                 request_duration_s = time.monotonic() - request_start_time
                 logger.warning(f"Step {step_identifier}: Attempt {attempt+1}/{max_retries} failed: {type(conn_err).__name__}: {conn_err} ({request_duration_s*1000:.2f} ms)")
                 if attempt < max_retries - 1:
                     retry_delay = base_retry_delay * (2 ** attempt)
                     logger.warning(f"Step {step_identifier}: Retrying connection after {retry_delay:.2f}s...")
                     await asyncio.sleep(retry_delay)
                     continue # Go to next attempt
                 else:
                     # Max retries reached for connection error
                     error_message = f"Connection/Timeout Error after {max_retries} attempts: {conn_err}"
                     logger.error(f"Step {step_identifier}: {error_message}")
                     response_status = 598 # Custom status for connection errors
                     break # Exit retry loop

            # --- Handle Other Client Errors ---
            except aiohttp.ClientError as client_err:
                 request_duration_s = time.monotonic() - request_start_time
                 error_message = f"HTTP Client Error: {client_err}"
                 logger.error(f"Step {step_identifier}: {error_message} ({request_duration_s*1000:.2f} ms)", exc_info=self.config.debug)
                 response_status = 597 # Custom status for other client errors
                 break # Exit retry loop (usually not retriable)

            # --- Handle Unexpected Errors ---
            except Exception as e:
                 request_duration_s = time.monotonic() - request_start_time
                 error_message = f"Unexpected error during request execution: {e}"
                 logger.error(f"Step {step_identifier}: {error_message} ({request_duration_s*1000:.2f} ms)", exc_info=self.config.debug)
                 response_status = 596 # Custom code for unexpected errors
                 break # Exit retry loop


        # --- Post-Request Processing ---
        # Update context with final status, headers, body, and error message (ALWAYS do this)
        set_value_in_context(context, f'{context_prefix}_status', response_status)
        set_value_in_context(context, f'{context_prefix}_headers', response_headers_dict)
        set_value_in_context(context, f'{context_prefix}_body', response_body)

        # Set or clear the error message in context
        error_key_path = f'{context_prefix}_error'
        if error_message:
            set_value_in_context(context, error_key_path, error_message)
        else:
            # Clear previous error only if it exists (using robust getter)
            # This prevents setting the key to None if it never existed.
            if get_value_from_context(context, error_key_path) is not _MISSING:
                 set_value_in_context(context, error_key_path, None)

        # --- NEW: Check for Failure Handling (onFailure) ---
        flow_should_stop = False
        if request_succeeded and response_status >= 300:
             # Access step.onFailure (made mandatory in Pydantic model)
             failure_action = step.onFailure
             logger.warning(f"Step {step_identifier} failed with status {response_status}. Failure action: '{failure_action}'.")
             if failure_action == "stop":
                  error_msg = f"Step {step_identifier} failed with status {response_status} and onFailure=stop"
                  # Set the main flow error to halt subsequent steps
                  set_value_in_context(context, 'flow_error', error_msg)
                  logger.warning(f"{error_msg}. Halting flow sequence after this step.")
                  flow_should_stop = True # Signal to skip extraction etc. in *this* step
             elif failure_action == "continue":
                  logger.info(f"Step {step_identifier} failed with status {response_status}, but onFailure=continue. Proceeding.")
             # No else needed as Literal['stop', 'continue'] ensures only these values


        # Data Extraction (only if request completed AND flow is not stopping due to onFailure=stop)
        if request_succeeded and not flow_should_stop and step.extract:
             logger.debug(f"Step {step_identifier}: Performing data extraction.")
             self._extract_data(response_body, step.extract, context, response_status, response_headers_dict)
        elif flow_should_stop:
             logger.debug(f"Step {step_identifier}: Skipping extraction due to onFailure=stop.")
        elif not request_succeeded:
             logger.debug(f"Step {step_identifier}: Skipping extraction due to request execution failure.")


        # Increment metrics only if the request was actually sent and received a response status
        if request_succeeded:
             await self.metrics.increment()
             return True # Indicate step executed (the _execute_steps loop will check flow_error)
        else:
             # Set flow error if request failed internally (connection, timeout, prep)
             # Only set if flow_error isn't already set (e.g., by onFailure=stop)
             final_error_msg = error_message if error_message else "Request failed before completion"
             current_flow_error = get_value_from_context(context, 'flow_error')
             if current_flow_error is _MISSING or current_flow_error is None:
                 set_value_in_context(context, 'flow_error', f"Step {step_identifier} failed internally: {final_error_msg}")
             return False # Indicate step failed internally


    async def _execute_loop_step(
        self,
        step: LoopStep,
        session: aiohttp.ClientSession,
        base_headers: Dict[str, str],
        flow_headers: Dict[str, str],
        context: Dict[str, Any],
        depth: int,
        user_id_log: str,
    ) -> None:
        """Executes a LoopStep with isolated iteration contexts."""
        indent = "  " * depth
        step_identifier = f"'{step.name}' ({step.id})" if step.name else f"({step.id})"

        source_path = step.source
        if source_path.startswith("{{") and source_path.endswith("}}"):
            source_path = source_path[2:-2].strip()
        else:
            source_path = source_path.strip()

        if not source_path:
            logger.error(f"{indent}User {user_id_log}: Loop {step_identifier}: Source path is empty ('{step.source}'). Skipping loop.")
            return

        iterable_source = get_value_from_context(context, source_path)
        if iterable_source is _MISSING:
            logger.warning(f"{indent}User {user_id_log}: Loop {step_identifier}: Source variable '{source_path}' not found in context. Skipping loop.")
            return
        if not isinstance(iterable_source, list):
            logger.warning(
                f"{indent}User {user_id_log}: Loop {step_identifier}: Source '{source_path}' is not a list (type: {type(iterable_source).__name__}). Skipping loop."
            )
            return
        if not iterable_source:
            logger.info(f"{indent}User {user_id_log}: Loop {step_identifier}: Source '{source_path}' is an empty list. Skipping loop execution.")
            return

        item_count = len(iterable_source)
        loop_var_name = step.loopVariable
        logger.info(
            f"{indent}User {user_id_log}: Loop {step_identifier}: Starting loop over {item_count} items from '{source_path}', loop var '{loop_var_name}'."
        )

        for index, item in enumerate(iterable_source):
            if not self.running:
                logger.info(f"{indent}  User {user_id_log}: Stop signal received, breaking loop {step_identifier}.")
                break

            loop_error_check = get_value_from_context(context, 'flow_error')
            if loop_error_check is not _MISSING and loop_error_check is not None:
                logger.warning(
                    f"{indent}  User {user_id_log}: Flow error detected before loop iteration {index+1} ('{loop_error_check}'), breaking loop {step_identifier}."
                )
                break

            logger.debug(f"{indent}  User {user_id_log}: Loop {step_identifier} - Iteration {index+1}/{item_count}")

            try:
                loop_context = copy.deepcopy(context)
            except Exception as copy_err:
                logger.error(
                    f"{indent}  User {user_id_log}: Failed to deepcopy context for loop {step_identifier} iteration {index+1}: {copy_err}. Using shallow copy (RISKY)."
                )
                loop_context = context.copy()

            set_value_in_context(loop_context, loop_var_name, item)
            set_value_in_context(loop_context, f"{loop_var_name}_index", index)

            await self._execute_steps(step.steps, session, base_headers, flow_headers, loop_context, depth + 1)

            iteration_error = get_value_from_context(loop_context, 'flow_error')
            if iteration_error is not _MISSING and iteration_error is not None:
                logger.warning(
                    f"{indent}  User {user_id_log}: Error detected within loop {step_identifier} iteration {index+1}: {iteration_error}"
                )
                set_value_in_context(context, 'flow_error', f"Error in loop {step_identifier} iter {index+1}: {iteration_error}")
                break


    async def _execute_steps(
        self,
        steps: List[Union[FlowStep, Dict]], # Input list might contain dicts or models
        session: aiohttp.ClientSession,
        base_headers: Dict[str, str], # Passed down from user lifecycle (user-specific)
        flow_headers: Dict[str, str], # Passed down (global flow definition, unsubstituted)
        context: Dict[str, Any],      # The context for this specific sequence execution
        depth: int = 0 # Recursion depth tracking
    ):
        """
        Recursively executes a list of flow steps. Dynamically validates dict steps.
        Halts execution of the current sequence if self.running becomes False or flow_error is set in context.
        """
        sequence_start_time = time.monotonic()
        user_id_log = context.get('userId', 'Unknown')
        total_steps = len(steps)
        indent = "  " * depth # Indentation for logging nested structures
        logger.debug(f"{indent}User {user_id_log}: Executing sequence of {total_steps} steps...")

        # Substitute global headers once at the start of this sequence using current context
        current_flow_headers_substituted = self._substitute_variables(flow_headers, context)
        if not isinstance(current_flow_headers_substituted, dict):
             logger.error(f"{indent}User {user_id_log}: Global header substitution failed, resulted in {type(current_flow_headers_substituted)}. Using empty headers.")
             current_flow_headers_substituted = {}

        for i, step_data in enumerate(steps): # Rename loop var to step_data as it might be dict or model
            # --- Check Running Status and Flow Errors ---
            if not self.running:
                logger.info(f"{indent}User {user_id_log}: Stop signal received, halting step execution sequence.")
                return # Stop this sequence
            flow_error_check = get_value_from_context(context, 'flow_error')
            if flow_error_check is not _MISSING and flow_error_check is not None:
                 logger.warning(f"{indent}User {user_id_log}: Flow error detected ('{flow_error_check}'), halting step execution sequence.")
                 return # Stop this sequence

            # --- FIX: Dynamic Validation of Steps ---
            step_instance = None # Holds the validated Pydantic model instance
            if isinstance(step_data, (RequestStep, ConditionStep, LoopStep)):
                 # Already a validated model (likely from top-level parsing)
                 step_instance = step_data
            elif isinstance(step_data, dict):
                 # Attempt to validate the dictionary into a FlowStep model
                 step_id_for_log = step_data.get('id', 'Unknown ID')
                 step_type_for_log = step_data.get('type', 'Unknown Type')
                 logger.debug(f"{indent}User {user_id_log}: Step {i+1}/{total_steps} is dict (ID: {step_id_for_log}, Type: {step_type_for_log}). Attempting dynamic validation.")

                 try:
                     # Determine which concrete model to use based on 'type'
                     step_type = step_data.get('type')
                     if not step_type:
                         raise ValueError(f"Step is missing required 'type' field")

                     # Validate against the appropriate concrete model
                     if step_type == 'request':
                         step_instance = RequestStep.model_validate(step_data)
                     elif step_type == 'condition':
                         step_instance = ConditionStep.model_validate(step_data)
                     elif step_type == 'loop':
                         step_instance = LoopStep.model_validate(step_data)
                     else:
                         raise ValueError(f"Unknown step type: {step_type}")

                     logger.debug(f"{indent}User {user_id_log}: Dynamically validated step dict {step_id_for_log} into {type(step_instance).__name__}")
                 except Exception as val_err:
                     # Enhanced error handling with detailed diagnostics
                     error_detail = str(val_err)
                     if hasattr(val_err, '__cause__') and val_err.__cause__:
                         error_detail += f" Caused by: {val_err.__cause__}"

                     logger.error(f"{indent}User {user_id_log}: Failed to validate step dict (ID: {step_id_for_log}) into a FlowStep model: {error_detail}. Halting sequence.")
                     if self.config.debug:
                         logger.debug(f"Validation traceback:\n{traceback.format_exc()}")

                     set_value_in_context(context, 'flow_error', f"Validation error for step ID {step_id_for_log}: {val_err}")
                     return # Stop this sequence
            else:
                 # Unexpected type in the steps list
                 logger.error(f"{indent}User {user_id_log}: Encountered unexpected item type '{type(step_data).__name__}' in steps list at index {i}. Halting sequence.")
                 set_value_in_context(context, 'flow_error', f"Unexpected item type {type(step_data).__name__} at step index {i}")
                 return # Stop this sequence

            # If validation failed above, step_instance would be None and the function returned.
            # So here, step_instance *should* be a valid Pydantic model.

            step_identifier = f"'{step_instance.name}' ({step_instance.id})" if step_instance.name else f"({step_instance.id})"

            # --- Inter-step Sleep ---
            if i > 0: # Don't sleep before the first step
                sleep_duration_ms = random.randint(self.config.min_sleep_ms, self.config.max_sleep_ms)
                if sleep_duration_ms > 0:
                    sleep_duration_sec = sleep_duration_ms / 1000.0
                    logger.debug(f"{indent}User {user_id_log}: Sleeping for {sleep_duration_sec:.3f}s before step {step_identifier} ({i+1}/{total_steps})")
                    try:
                        await asyncio.sleep(sleep_duration_sec)
                    except asyncio.CancelledError:
                         logger.info(f"{indent}User {user_id_log}: Sleep interrupted by cancellation. Halting.")
                         self.running = False # Signal stop
                         raise # Re-raise to stop higher levels immediately

            # Re-check running status after sleep
            if not self.running: logger.info(f"{indent}User {user_id_log}: Stop signal received after sleep, halting."); return
            # Re-check flow error status after sleep
            flow_error_check_after_sleep = get_value_from_context(context, 'flow_error')
            if flow_error_check_after_sleep is not _MISSING and flow_error_check_after_sleep is not None:
                logger.warning(f"{indent}User {user_id_log}: Flow error detected after sleep ('{flow_error_check_after_sleep}'), halting."); return


            # --- Execute Step based on Type ---
            step_type = step_instance.type
            logger.debug(f"{indent}User {user_id_log}: Processing Step {i+1}/{total_steps}: {step_identifier} (Type: {step_type})")
            step_start_time = time.monotonic()

            try:
                # --- Request Step ---
                if isinstance(step_instance, RequestStep):
                    # Execute request and check internal success (True if request happened)
                    # _execute_request_step now handles onFailure logic internally
                    step_executed = await self._execute_request_step(
                        step=step_instance,
                        session=session,
                        base_headers=base_headers,
                        flow_headers=current_flow_headers_substituted, # Pass substituted global headers
                        context=context
                    )
                    # If step failed internally (e.g., bad URL param) or stopped due to onFailure=stop,
                    # flow_error should be set in the context.
                    # The check at the start of the *next* loop iteration will handle sequence halting.
                    if not step_executed:
                         logger.warning(f"{indent}User {user_id_log}: Request step {step_identifier} failed internal execution checks. Flow error should be set.")
                         # Rely on the check at the top of the loop to halt if needed.

                # --- Condition Step ---
                elif isinstance(step_instance, ConditionStep):
                    condition_data_model = step_instance.conditionData # Might be None
                    condition_result = self._evaluate_condition(
                        condition_str=step_instance.condition, # Legacy string (optional)
                        context=context,
                        condition_data=condition_data_model # Structured data (preferred)
                    )

                    # Log evaluation result
                    cond_desc = "N/A"
                    if condition_data_model and condition_data_model.variable:
                        cond_desc = f"'{condition_data_model.variable}' {condition_data_model.operator} '{condition_data_model.value}'"
                    elif step_instance.condition:
                        cond_desc = f"Legacy: '{step_instance.condition}'"
                    logger.info(f"{indent}User {user_id_log}: Condition {step_identifier}: {cond_desc} -> {condition_result}")

                    branch_to_execute_data = step_instance.then if condition_result else step_instance.else_
                    branch_name = "then" if condition_result else "else"

                    # branch_to_execute_data might be a list of dicts or models here
                    if branch_to_execute_data: # Ensure list is not empty
                        logger.debug(f"{indent}User {user_id_log}: Executing '{branch_name}' branch for {step_identifier} ({len(branch_to_execute_data)} steps)...")
                        # Recurse: Pass the list (containing dicts/models), original flow_headers, context, increased depth
                        await self._execute_steps(branch_to_execute_data, session, base_headers, flow_headers, context, depth + 1)
                        # Error propagation: If the sub-sequence set flow_error, the check at the top of the next loop iteration will catch it.
                    else:
                        logger.debug(f"{indent}User {user_id_log}: No steps found in '{branch_name}' branch for {step_identifier}.")

                # --- Loop Step ---
                elif isinstance(step_instance, LoopStep):
                    await self._execute_loop_step(
                        step_instance,
                        session,
                        base_headers,
                        flow_headers,
                        context,
                        depth,
                        user_id_log,
                    )

                else: # Should be unreachable with validated models
                    logger.error(f"{indent}User {user_id_log}: Encountered unknown step instance type '{type(step_instance).__name__}' for {step_identifier}. Halting sequence.")
                    set_value_in_context(context, 'flow_error', f"Unknown step type {type(step_instance).__name__}")
                    return # Stop sequence

            except asyncio.CancelledError:
                logger.info(f"{indent}User {user_id_log}: Step execution cancelled during step {step_identifier}.")
                self.running = False # Ensure signal propagates
                raise # Propagate cancellation upwards

            except Exception as e:
                logger.error(f"{indent}User {user_id_log}: Unhandled error processing step {step_identifier}: {e}", exc_info=self.config.debug)
                if self.config.debug and not isinstance(e, asyncio.CancelledError): # Don't print traceback for cancellation
                    traceback.print_exc()
                # Set flow error to halt further execution in this sequence
                set_value_in_context(context, 'flow_error', f"Error in step {step_identifier}: {e}")
                logger.error(f"{indent}User {user_id_log}: Halting flow sequence due to error.")
                return # Stop this sequence

            finally:
                step_end_time = time.monotonic()
                step_duration = step_end_time - step_start_time
                logger.debug(f"{indent}User {user_id_log}: Finished Step {step_identifier} ({i+1}/{total_steps}) in {step_duration:.3f} seconds.")


        sequence_end_time = time.monotonic()
        sequence_duration = sequence_end_time - sequence_start_time
        logger.debug(f"{indent}User {user_id_log}: Finished executing sequence of {total_steps} steps in {sequence_duration:.3f} seconds.")


    async def simulate_user_lifecycle(self, user_id: int):
        """
        Manages the continuous lifecycle of a single simulated user task.
        Includes setup, flow execution loop, rest periods, and cleanup.
        """
        user_log_prefix = f"User {user_id}"
        connector = None # Initialize connector reference
        flow_iteration = 0

        try: # Top-level try/finally for reliable active user count decrement
            # --- Task Startup ---
            async with self.lock: # Protect counter increment
                self._active_users_count += 1
            flow_name_log = getattr(self.flowmap, 'name', 'N/A')
            logger.info(f"{user_log_prefix}: Task started for flow '{flow_name_log}'. Active users: {self._active_users_count}")

            # Create connector once for the lifetime of this user task (allows connection pooling)
            connector = self.create_aiohttp_connector()

            # --- Main Loop ---
            while self.running:
                flow_iteration += 1
                flow_instance_start_time = time.monotonic()
                flow_epoch_start_time = time.time()  # Wall clock time

                # --- Generate Per-Flow State ---
                fake_ip = self.generate_random_ip()
                is_web_like = random.choice([True, False])
                # Ensure random.choice returns a dict before calling .copy()
                chosen_headers_template = random.choice(self.headers_web_options if is_web_like else self.headers_api_options)
                if not isinstance(chosen_headers_template, dict):
                    logger.error(f"{user_log_prefix} (Iter {flow_iteration}): Invalid header template found: {chosen_headers_template}. Using empty headers.")
                    base_session_headers = {}
                else:
                    base_session_headers = chosen_headers_template.copy()

                chosen_ua_template = random.choice(self.user_agents_web if is_web_like else self.user_agents_api)
                if not isinstance(chosen_ua_template, str):
                     logger.error(f"{user_log_prefix} (Iter {flow_iteration}): Invalid user agent template found: {chosen_ua_template}. Using default UA.")
                     ua = "FlowRunner/1.0" # Default fallback UA
                else:
                     ua = chosen_ua_template
                base_session_headers["User-Agent"] = ua

                if self.config.xff_header_name:
                    base_session_headers[self.config.xff_header_name] = fake_ip
                logger.debug(f"{user_log_prefix} (Iter {flow_iteration}): New session state (IP: {fake_ip}, UA: {ua[:30]}..., Profile: {'Web' if is_web_like else 'API'})")

                # --- Initialize Context for this Flow Instance ---
                context = {
                    "userId": user_id,
                    "userFakeIp": fake_ip,
                    "flowInstance": flow_iteration,
                    "flowStartTimeEpoch": flow_epoch_start_time,
                    "flow_error": None # Initialize error state explicitly
                }
                # Add static variables (use deepcopy for isolation)
                static_vars = getattr(self.flowmap, 'staticVars', {})
                if static_vars:
                    try: context.update(copy.deepcopy(static_vars))
                    except Exception as copy_err:
                         logger.warning(f"{user_log_prefix} (Iter {flow_iteration}): Could not deepcopy staticVars: {copy_err}. Using shallow copy.")
                         context.update(static_vars)

                # Get global flow headers definition (unsubstituted)
                global_flow_headers_def = getattr(self.flowmap, 'headers', {}) or {}

                if self.on_iteration_start and flow_iteration > 1:
                    logger.debug(
                        f"Calling on_iteration_start callback for iteration {flow_iteration} with context keys: {list(context.keys())}"
                    )
                    try:
                        self.on_iteration_start(flow_iteration, context)
                    except Exception as cb_err:
                        logger.error(
                            f"Error during on_iteration_start callback for iteration {flow_iteration}: {cb_err}"
                        )

                # --- Execute Flow within a Session ---
                session = None
                flow_completed_successfully = False # Track if flow finished without internal errors
                try: # Ensure session is always closed after one flow iteration
                    session = self.create_session(connector)
                    logger.info(f"{user_log_prefix} (Iter {flow_iteration}): Starting flow instance.")

                    # Execute the top-level steps (pass the list of models/dicts)
                    await self._execute_steps(
                        steps=self.flowmap.steps,
                        session=session,
                        base_headers=base_session_headers,
                        flow_headers=global_flow_headers_def, # Pass definition, substitution happens inside
                        context=context,
                        depth=0
                    )

                    # Check final error state in context
                    final_flow_error_val = get_value_from_context(context, 'flow_error')
                    if final_flow_error_val is _MISSING or final_flow_error_val is None:
                        flow_completed_successfully = True
                    else:
                         logger.warning(f"{user_log_prefix} (Iter {flow_iteration}): Flow instance finished with error: {final_flow_error_val}")


                except asyncio.CancelledError:
                    logger.info(f"{user_log_prefix} (Iter {flow_iteration}): Flow instance cancelled during execution.")
                    self.running = False # Ensure loop terminates
                    # Do not re-raise, let finally close session and outer loop check self.running
                except Exception as e:
                    logger.error(f"{user_log_prefix} (Iter {flow_iteration}): Unhandled error during flow execution block: {e}", exc_info=self.config.debug)
                    # Record error state, flow did not complete successfully
                    set_value_in_context(context, 'flow_error', f"Unhandled flow error: {e}")
                    # flow_completed_successfully remains False

                finally:
                    # --- Cleanup Session for this Iteration ---
                    if session and not session.closed:
                        await session.close()
                        logger.debug(f"{user_log_prefix} (Iter {flow_iteration}): Session closed.")

                    # --- Record Metrics and Log Duration ---
                    flow_instance_end_time = time.monotonic()
                    flow_duration = flow_instance_end_time - flow_instance_start_time

                    # Record duration only if flow completed without internal errors and runner is still running
                    if flow_completed_successfully and self.running:
                        await self.metrics.record_flow_duration(flow_duration)
                        logger.info(f"{user_log_prefix} (Iter {flow_iteration}): Flow instance finished successfully in {flow_duration:.3f} seconds.")
                    elif not self.running:
                         logger.info(f"{user_log_prefix} (Iter {flow_iteration}): Flow instance ended (stopped/cancelled) after {flow_duration:.3f} seconds.")
                    # else: Error already logged


                # --- Inter-Flow Rest Period ---
                if self.running:
                    if self.run_once:
                        logger.info(f"{user_log_prefix}: run_once enabled - stopping after first iteration.")
                        self.running = False
                        if hasattr(self, '_stopped_event') and self._stopped_event and not self._stopped_event.is_set():
                            self._stopped_event.set()
                        break
                    if self.config.flow_cycle_delay_ms is not None:
                        rest_duration_s = max(self.config.flow_cycle_delay_ms / 1000.0, 0.001)
                    else:
                        min_rest_s = self.config.min_sleep_ms / 1000.0
                        max_rest_s = self.config.max_sleep_ms / 1000.0
                        if min_rest_s > max_rest_s:
                            min_rest_s = max_rest_s
                        rest_duration_s = random.uniform(min_rest_s, max_rest_s)
                        if rest_duration_s <= 0.001:
                            rest_duration_s = 0.001

                    logger.info(
                        f"{user_log_prefix}: Flow iteration {flow_iteration} complete, next in {rest_duration_s:.2f}s"
                    )
                    try:
                        await asyncio.sleep(rest_duration_s)
                    except asyncio.CancelledError:
                         logger.info(f"{user_log_prefix}: Task cancelled during rest period.")
                         self.running = False # Ensure loop terminates

            # --- End of Main While Loop (self.running is False or task cancelled) ---

        except asyncio.CancelledError:
            # Catch cancellation signal targeting the task itself (e.g., from stop_running)
            logger.info(f"{user_log_prefix}: Task received cancellation signal.")
            self.running = False # Ensure state consistency

        except Exception as e:
             # Catch unexpected errors in the main loop structure or connector setup/cleanup
            logger.critical(f"{user_log_prefix}: Task exiting due to unhandled outer error: {e}", exc_info=True)
            self.running = False # Ensure state consistency

        finally:
            # --- Task Cleanup ---
            logger.info(f"{user_log_prefix}: Task stopping.")
            # Decrement active user count reliably
            async with self.lock: # Protect counter decrement
                if self._active_users_count > 0:
                    self._active_users_count -= 1
                else:
                    logger.warning(f"{user_log_prefix}: Task exiting, but active user count was already {self._active_users_count}.")

            # --- Cleanup Connector ---
            if connector and not connector.closed:
                logger.debug(f"{user_log_prefix}: Closing user task connector.")
                try:
                    # Use timeout for connector closing to prevent hangs
                    await asyncio.wait_for(connector.close(), timeout=5.0)
                    logger.debug(f"{user_log_prefix}: User task connector closed.")
                except asyncio.TimeoutError:
                     logger.warning(f"{user_log_prefix}: Timeout closing user task connector.")
                except Exception as conn_close_err:
                     logger.error(f"{user_log_prefix}: Error closing user task connector: {conn_close_err}")
            elif connector:
                 logger.debug(f"{user_log_prefix}: Connector was already closed.")

            logger.info(f"{user_log_prefix}: Task finished cleanup. Final active users: {self._active_users_count}")


    def generate_random_ip(self) -> str:
        """Generates a random, plausible public IPv4 address string, avoiding reserved/special ranges."""
        # Use existing implementation (seems reasonable)
        while True:
            octets = [random.randint(1, 223)] + [random.randint(0, 255) for _ in range(3)]

            # Quick checks for common private ranges
            if octets[0] == 10: continue # 10.0.0.0/8
            if octets[0] == 127: continue # 127.0.0.0/8 (Loopback)
            if octets[0] == 172 and 16 <= octets[1] <= 31: continue # 172.16.0.0/12
            if octets[0] == 192 and octets[1] == 168: continue # 192.168.0.0/16

            # Check other reserved ranges (can be simplified/combined)
            if octets[0] == 0: continue # 0.0.0.0/8 (Current network)
            if octets[0] == 100 and 64 <= octets[1] <= 127: continue # 100.64.0.0/10 (Shared Address Space)
            if octets[0] == 169 and octets[1] == 254: continue # 169.254.0.0/16 (Link-local)
            if octets[0] == 192 and octets[1] == 0 and octets[2] == 0: continue # 192.0.0.0/24 (IETF Assignment)
            if octets[0] == 192 and octets[1] == 0 and octets[2] == 2: continue # 192.0.2.0/24 (TEST-NET-1)
            if octets[0] == 192 and octets[1] == 88 and octets[2] == 99: continue # 192.88.99.0/24 (6to4 Relay)
            if octets[0] == 198 and 18 <= octets[1] <= 19: continue # 198.18.0.0/15 (Benchmark Testing)
            if octets[0] == 198 and octets[1] == 51 and octets[2] == 100: continue # 198.51.100.0/24 (TEST-NET-2)
            if octets[0] == 203 and octets[1] == 0 and octets[2] == 113: continue # 203.0.113.0/24 (TEST-NET-3)
            # Skip multicast (224.0.0.0 to 239.255.255.255) - covered by first octet < 224
            # Skip Broadcast (255.255.255.255) - covered by first octet < 224

            ip_str = f"{octets[0]}.{octets[1]}.{octets[2]}.{octets[3]}"

            # Final check using ipaddress module flags (optional belt-and-suspenders)
            # try:
            #     addr = ip_address(ip_str)
            #     if addr.is_private or addr.is_reserved or addr.is_loopback or addr.is_link_local or addr.is_multicast:
            #         continue # Should be caught by checks above
            # except ValueError: continue # Should not happen

            return ip_str


# --- Final Pydantic Model Rebuild ---
# Ensures forward references within models are resolved correctly after all classes defined.
FlowMap.model_rebuild()
ConditionStep.model_rebuild()
LoopStep.model_rebuild()
StartRequest.model_rebuild()
# Also rebuild RequestStep explicitly in case forward refs were missed? Not strictly needed here.
RequestStep.model_rebuild() # Add just in case, though not strictly necessary here