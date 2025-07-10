# FlowRunner Container Control Integration

This document explains how FlowRunner has been integrated with the Container Control Core v2.0 system for improved container management and monitoring.

## Overview

The integration consists of:

1. **Container Control Core** (`container_control_core.py`) - The main FastAPI application that provides lifecycle management
2. **FlowRunner Adapter** (`flowrunner_adapter.py`) - Custom adapter that integrates FlowRunner with the core
3. **Configuration** (`config.yaml`) - YAML configuration file specifying adapter and service settings
4. **Application Interface** (`app_adapter.py`) - Abstract base class defining the adapter contract

## Architecture

```
┌─────────────────────────────────────────┐
│ Container Control Core (FastAPI)        │
│ • HTTP API (/api/*, /metrics)           │
│ • Lifecycle management                  │
│ • Optional services (metrics, TC, etc.) │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│ FlowRunner Adapter                      │
│ • Manages FlowRunner lifecycle          │
│ • Handles async event loop in thread    │
│ • Exposes metrics                       │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│ FlowRunner Engine                       │
│ • Load generation                       │
│ • Flow execution                        │
│ • HTTP request simulation               │
└─────────────────────────────────────────┘
```

## API Endpoints

### Health Check
```http
GET /api/health
```
Returns the current status of the container and FlowRunner.

### Start FlowRunner
```http
POST /api/start
Content-Type: application/json

{
  "config": {
    "flow_target_url": "http://example.com",
    "sim_users": 10,
    "min_sleep_ms": 100,
    "max_sleep_ms": 1000,
    "debug": false
  },
  "flowmap": {
    "steps": [
      {
        "id": "get_page",
        "name": "Get homepage",
        "type": "request",
        "method": "GET",
        "url": "/",
        "headers": {},
        "extract": {},
        "onFailure": "continue"
      }
    ]
  }
}
```

### Stop FlowRunner
```http
POST /api/stop
```
Gracefully stops the currently running FlowRunner instance.

### Get Metrics
```http
GET /api/metrics
```
Returns JSON metrics including FlowRunner-specific metrics:
```json
{
  "timestamp": "2025-01-10T12:00:00Z",
  "app_status": "running",
  "container_status": "running",
  "flow_runner": {
    "running": true,
    "rps": 12.5,
    "total_requests": 1250,
    "flow_count": 125,
    "avg_flow_duration_ms": 850.5,
    "active_users": 10
  }
}
```

### Prometheus Metrics
```http
GET /metrics
```
Returns Prometheus-formatted metrics for monitoring systems.

## Configuration

The `config.yaml` file configures the Container Control Core and FlowRunner adapter:

### Basic Configuration
```yaml
adapter:
  class: flowrunner_adapter.FlowRunnerAdapter
  primary_payload_key: config
  run_as_user: null

process_management:
  enabled: false  # FlowRunner manages its own processes

metrics:
  network_monitoring:
    enabled: true
    interface: "eth0"
```

### Optional Services

#### Traffic Control
Enable network shaping for testing network conditions:
```yaml
traffic_control:
  enabled: true
  interface: "eth0"
  bandwidth_mbps_key: "bandwidth_limit_mbps"
  default_bandwidth_mbps: 100
  latency_ms_key: "latency_ms"
  default_latency_ms: 0
```

#### Privileged Commands
Run system commands at lifecycle events:
```yaml
privileged_commands:
  pre_start:
    - ["sysctl", "-w", "net.core.somaxconn=8192"]
  post_stop:
    - ["sysctl", "-w", "net.core.somaxconn=128"]
```

## Building and Running

### Using Docker
```bash
# Build the image
docker build -t flowrunner-control .

# Run the container
docker run -d --name flowrunner-control -p 8080:8080 flowrunner-control
```

### Using the convenience script
```bash
./run_container.sh
```

### Testing the integration
```bash
python test_container_control.py
```

## FlowRunner Configuration

The FlowRunner configuration is passed in the `config` field of the start payload:

### Required Fields
- `flow_target_url`: Base URL for the target application
- `sim_users`: Number of concurrent simulated users

### Optional Fields
- `min_sleep_ms`/`max_sleep_ms`: Sleep time between requests
- `debug`: Enable debug logging
- `flow_target_dns_override`: Override DNS for the target
- `xff_header_name`: Header name for source IP injection
- `override_step_url_host`: Whether to use target URL host for all requests
- `flow_cycle_delay_ms`: Fixed delay between flow iterations

### Flowmap Structure
The `flowmap` field contains the flow definition with steps:

```json
{
  "steps": [
    {
      "id": "unique_id",
      "name": "Human readable name",
      "type": "request",
      "method": "GET|POST|PUT|DELETE|...",
      "url": "/path/to/endpoint",
      "headers": {"Custom-Header": "value"},
      "body": {"key": "value"},
      "extract": {
        "variable_name": "body.path.to.value",
        "status": ".status"
      },
      "onFailure": "stop|continue"
    }
  ]
}
```

## Monitoring and Metrics

### FlowRunner Metrics
- `running`: Whether FlowRunner is currently active
- `rps`: Current requests per second
- `total_requests`: Total number of HTTP requests made
- `flow_count`: Number of complete flow iterations
- `avg_flow_duration_ms`: Average time to complete a full flow
- `active_users`: Number of currently active simulated users

### Container Metrics (if enabled)
- Network I/O statistics
- Container resource usage
- Process information

### Prometheus Integration
All metrics are available in Prometheus format at `/metrics` for integration with monitoring systems like Grafana.

## Error Handling

The adapter includes comprehensive error handling:

- Graceful shutdown on stop requests
- Timeout handling for long-running operations
- Automatic cleanup of resources
- Detailed logging for troubleshooting

## Migration from Old Container Control

If migrating from the old `container_control.py` system:

1. Replace the old container control with the new core files
2. Update your configuration to use the new YAML format
3. Update API calls to use the new endpoint structure
4. Update Docker images to use the new entry point

The new system is more robust and provides better monitoring and lifecycle management capabilities.
