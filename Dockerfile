FROM python:3.14-alpine

# Add security labels
LABEL org.opencontainers.image.vendor="TeslaMate-ABRP" \
      org.opencontainers.image.title="TeslaMate MQTT to ABRP" \
      org.opencontainers.image.description="A slightly convoluted way of getting your vehicle data from TeslaMate to A Better Route Planner."

# Set working directory and environment variables
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install security updates and create non-root user
RUN apk update && \
    apk upgrade && \
    adduser -D -h /app toor && \
    chown -R toor:toor /app

# Copy requirements first to leverage Docker cache
COPY --chown=toor:toor requirements.txt .

# Install dependencies directly (no virtual environment)
RUN pip install --no-cache-dir -r requirements.txt

# Copy only necessary application code
COPY --chown=toor:toor teslamate_mqtt2abrp.py .
COPY --chown=toor:toor LICENSE .

# Use non-root user
USER toor

# Run the app (and use the absolute path to the script to do it)
CMD ["python", "-u", "/app/teslamate_mqtt2abrp.py"]
