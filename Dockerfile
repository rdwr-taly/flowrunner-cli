FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Ensure container time is set to UTC
ENV TZ=UTC
RUN ln -snf /usr/share/zoneinfo/UTC /etc/localtime && echo "UTC" > /etc/timezone

# Install system packages for traffic control and monitoring (optional)
RUN apt-get update && apt-get install -y --no-install-recommends \
      iproute2 iptables sudo curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Create non-root user for running applications
ARG APP_USER=flowrunner
RUN useradd -ms /bin/bash ${APP_USER} && \
    echo "${APP_USER} ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

# Set work directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install additional dependencies for Container Control Core
RUN pip install --no-cache-dir fastapi uvicorn psutil ruamel.yaml

# Copy Container Control Core files
COPY container_control_core.py .
COPY app_adapter.py .
COPY flowrunner_adapter.py .
COPY config.yaml .

# Copy FlowRunner application code
COPY flow_runner.py .

# Expose port 8080 for the Container Control API
EXPOSE 8080

# Run Container Control Core with FlowRunner adapter
CMD ["python", "-m", "uvicorn", "container_control_core:app", "--host", "0.0.0.0", "--port", "8080"]
