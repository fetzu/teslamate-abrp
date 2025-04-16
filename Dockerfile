FROM python:3.13-alpine AS builder

# Set working directory and environment variables
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Copy only requirements first to leverage Docker cache
COPY requirements.txt .

# Install dependencies into a virtual environment
RUN python -m venv /venv && \
    /venv/bin/pip install --no-cache-dir -r requirements.txt

# Final slim image
FROM python:3.13-alpine

# Add security labels
LABEL org.opencontainers.image.vendor="TeslaMate-ABRP" \
      org.opencontainers.image.title="TeslaMate MQTT to ABRP Bridge" \
      org.opencontainers.image.description="Bridge between TeslaMate and ABRP"

# Set working directory and environment variables
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/venv/bin:$PATH"

# Install security updates and create non-root user
RUN apk update && \
    apk upgrade && \
    apk add --no-cache tini && \
    adduser -D -h /app toor && \
    chown -R toor:toor /app

# Copy virtual environment from builder stage
COPY --from=builder /venv /venv

# Copy application code
COPY --chown=toor:toor . .

# Use non-root user
USER toor

# Use Tini as init process to handle signals properly
ENTRYPOINT ["/sbin/tini", "--"]

# Run the application
CMD ["python", "-u", "teslamate_mqtt2abrp.py"]