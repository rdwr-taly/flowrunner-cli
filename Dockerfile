FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Ensure container time is set to UTC
ENV TZ=UTC
RUN ln -snf /usr/share/zoneinfo/UTC /etc/localtime && echo "UTC" > /etc/timezone

# Install system packages for traffic control and monitoring (optional)
RUN apt-get update && apt-get install -y --no-install-recommends \
      iproute2 iptables sudo curl git && \
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

# Copy application code
COPY flow_runner.py .
COPY main.py .

# Create config mount point
RUN mkdir -p /config

# Expose port 9090 for Prometheus metrics and health endpoint
EXPOSE 9090

# Health check against ShowRunner SDK metrics/health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:9090/healthz || exit 1

# Set ownership and switch to non-root user
RUN chown -R ${APP_USER}:${APP_USER} /app /config
USER ${APP_USER}

# App owns its own process — no CC framework wrapping
CMD ["python", "main.py"]
