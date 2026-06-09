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

# Copy python dependencies and install
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy application files
COPY --chown=user:user src /app/src
COPY --chown=user:user start.sh /app/start.sh
COPY --chown=user:user sync.py /app/sync.py

# Give permissions
RUN chmod +x /app/start.sh && chown -R user:user /app /home/user

# Switch to the non-root user
USER user

# Set environment
ENV HOME=/home/user

# Expose the standard HF port
EXPOSE 7860

ENTRYPOINT ["/usr/bin/tini", "--", "/app/start.sh"]
