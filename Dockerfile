FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    openssl \
    ca-certificates \
    tini \
    && rm -rf /var/lib/apt/lists/*

# Set up HF Spaces user (uid 1000 required)
RUN useradd -m -u 1000 user

# Set up workdir
WORKDIR /app

# Copy all application files first to support package installation
COPY --chown=user:user . /app

# Install the package directly in non-editable mode for production deployment
RUN pip install --no-cache-dir /app

# Give permissions
RUN chmod +x /app/start.sh && chown -R user:user /app /home/user

# Switch to the non-root user
USER user

# Set environment
ENV HOME=/home/user

# Expose the standard HF port
EXPOSE 7860

ENTRYPOINT ["/usr/bin/tini", "--", "/app/start.sh"]
