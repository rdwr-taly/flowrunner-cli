FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Ensure container time is set to UTC
ENV TZ=UTC
RUN ln -snf /usr/share/zoneinfo/UTC /etc/localtime && echo "UTC" > /etc/timezone


# Set work directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy in your Python code
COPY container_control.py .
COPY flow_runner.py .

# Expose port 8080 for the FastAPI app
EXPOSE 8080

# Run flow_container_control (FastAPI) on port 8080
CMD ["python", "-m", "uvicorn", "container_control:app", "--host", "0.0.0.0", "--port", "8080"]
