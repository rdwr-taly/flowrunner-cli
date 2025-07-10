#!/usr/bin/env python3
"""
Test script for FlowRunner Container Control integration.

This script demonstrates how to interact with the new Container Control API
to start and manage FlowRunner instances.
"""

import json
import requests
import time
from typing import Dict, Any

# Container Control API base URL
API_BASE = "http://localhost:8080"

def test_health():
    """Test the health endpoint."""
    print("Testing health endpoint...")
    try:
        response = requests.get(f"{API_BASE}/api/health")
        print(f"Health status: {response.status_code}")
        print(f"Response: {response.json()}")
        return response.status_code == 200
    except Exception as e:
        print(f"Health check failed: {e}")
        return False

def test_start_flowrunner():
    """Test starting FlowRunner with a sample configuration."""
    print("\nTesting FlowRunner start...")
    
    # Sample payload for FlowRunner
    payload = {
        "config": {
            "flow_target_url": "http://httpbin.org",
            "sim_users": 2,
            "min_sleep_ms": 500,
            "max_sleep_ms": 1000,
            "debug": True
        },
        "flowmap": {
            "steps": [
                {
                    "id": "get_status",
                    "name": "Check status",
                    "type": "request",
                    "method": "GET",
                    "url": "/status/200",
                    "headers": {},
                    "extract": {
                        "status_code": ".status"
                    },
                    "onFailure": "continue"
                },
                {
                    "id": "get_json",
                    "name": "Get JSON data",
                    "type": "request", 
                    "method": "GET",
                    "url": "/json",
                    "headers": {},
                    "extract": {},
                    "onFailure": "continue"
                }
            ]
        }
    }
    
    try:
        response = requests.post(f"{API_BASE}/api/start", json=payload)
        print(f"Start status: {response.status_code}")
        print(f"Response: {response.json()}")
        return response.status_code == 200
    except Exception as e:
        print(f"Start failed: {e}")
        return False

def test_metrics():
    """Test retrieving metrics."""
    print("\nTesting metrics endpoint...")
    try:
        response = requests.get(f"{API_BASE}/api/metrics")
        print(f"Metrics status: {response.status_code}")
        print(f"Response: {json.dumps(response.json(), indent=2)}")
        return response.status_code == 200
    except Exception as e:
        print(f"Metrics failed: {e}")
        return False

def test_prometheus_metrics():
    """Test Prometheus metrics endpoint."""
    print("\nTesting Prometheus metrics endpoint...")
    try:
        response = requests.get(f"{API_BASE}/metrics")
        print(f"Prometheus metrics status: {response.status_code}")
        print("Sample metrics:")
        lines = response.text.split('\n')[:10]  # Show first 10 lines
        for line in lines:
            if line.strip():
                print(f"  {line}")
        return response.status_code == 200
    except Exception as e:
        print(f"Prometheus metrics failed: {e}")
        return False

def test_stop_flowrunner():
    """Test stopping FlowRunner."""
    print("\nTesting FlowRunner stop...")
    try:
        response = requests.post(f"{API_BASE}/api/stop")
        print(f"Stop status: {response.status_code}")
        print(f"Response: {response.json()}")
        return response.status_code == 200
    except Exception as e:
        print(f"Stop failed: {e}")
        return False

def main():
    """Run all tests."""
    print("FlowRunner Container Control Integration Test")
    print("=" * 50)
    
    # Test health first
    if not test_health():
        print("❌ Health check failed. Is the container running?")
        return
    
    print("✅ Health check passed")
    
    # Test starting FlowRunner
    if test_start_flowrunner():
        print("✅ FlowRunner started successfully")
        
        # Wait a bit for it to start generating traffic
        print("\nWaiting 5 seconds for FlowRunner to generate some traffic...")
        time.sleep(5)
        
        # Test metrics
        if test_metrics():
            print("✅ Metrics retrieved successfully")
        else:
            print("❌ Metrics test failed")
        
        # Test Prometheus metrics
        if test_prometheus_metrics():
            print("✅ Prometheus metrics retrieved successfully")
        else:
            print("❌ Prometheus metrics test failed")
        
        # Test stopping
        if test_stop_flowrunner():
            print("✅ FlowRunner stopped successfully")
        else:
            print("❌ Stop test failed")
    else:
        print("❌ FlowRunner start failed")
    
    print("\nTest completed!")

if __name__ == "__main__":
    main()
