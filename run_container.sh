#!/bin/bash
# Build and run FlowRunner with Container Control Core

set -e

echo "Building FlowRunner Container Control image..."
docker build -t flowrunner-control .

echo "Running FlowRunner Container Control..."
docker run -d \
  --name flowrunner-control \
  --rm \
  -p 8080:8080 \
  flowrunner-control

echo "Container started. Waiting for it to be ready..."
sleep 3

echo "Testing the container..."
curl -f http://localhost:8080/api/health || {
  echo "Health check failed. Container logs:"
  docker logs flowrunner-control
  exit 1
}

echo "✅ FlowRunner Container Control is running on http://localhost:8080"
echo ""
echo "Available endpoints:"
echo "  - Health: http://localhost:8080/api/health"
echo "  - Start:  POST http://localhost:8080/api/start"
echo "  - Stop:   POST http://localhost:8080/api/stop"
echo "  - Metrics: http://localhost:8080/api/metrics"
echo "  - Prometheus: http://localhost:8080/metrics"
echo ""
echo "Run 'python test_container_control.py' to test the integration"
echo "Run 'docker logs flowrunner-control' to see container logs"
echo "Run 'docker stop flowrunner-control' to stop the container"
