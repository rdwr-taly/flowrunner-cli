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

# Clone Container Control Core v2.0 from GitHub
RUN apt-get update && apt-get install -y --no-install-recommends git && \
    git clone --branch v1.0.0 --depth 1 https://github.com/rdwr-taly/container-control.git /tmp/container-control && \
    cp /tmp/container-control/container_control_core.py . && \
    cp /tmp/container-control/app_adapter.py . && \
    rm -rf /tmp/container-control && \
    apt-get remove -y git && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# Copy application-specific files
COPY flowrunner_adapter.py .
COPY config.yaml .

# Copy FlowRunner application code
COPY flow_runner.py .

# Expose port 8080 for the Container Control API
EXPOSE 8080

# Run Container Control Core with FlowRunner adapter
CMD ["python", "-m", "uvicorn", "container_control_core:app", "--host", "0.0.0.0", "--port", "8080"]
