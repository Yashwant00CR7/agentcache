#!/usr/bin/env bash
# Note: no set -e — sync failures must not kill the container

# Create agentmemory data directories
mkdir -p /home/user/.agentmemory

# =============================================================================
# Persistent storage via HF Dataset repo
# Secrets to set in HF Space settings:
#   HF_TOKEN              — write access to the dataset repo
#   GEMINI_API_KEY        — powers graph, embeddings, compression, crystals
#   AGENTMEMORY_DATASET_REPO — optional override (default: Yash030/agentmemory-python-data)
# =============================================================================
export AGENTMEMORY_DATASET_REPO="${AGENTMEMORY_DATASET_REPO:-Yash030/agentmemory-python-data}"

echo "[start] Restoring data from HF Dataset..."
python3 /app/sync.py restore

# Background sync loop — backs up every 5 minutes
(
  while true; do
    sleep 300
    python3 /app/sync.py backup
  done
) &

# Generate HMAC secret on first boot, persist it so it survives dataset restore
HMAC_FILE="/home/user/.agentmemory/.hmac"
if [ ! -s "$HMAC_FILE" ]; then
  SECRET="$(openssl rand -hex 32)"
  printf '%s\n' "$SECRET" > "$HMAC_FILE"
  chmod 600 "$HMAC_FILE"
  echo "================================================================"
  echo "agentmemory: generated HMAC secret on first boot"
  echo "AGENTMEMORY_SECRET=$SECRET"
  echo "Copy this to your Space secrets as AGENTMEMORY_SECRET."
  echo "It will not be printed again."
  echo "================================================================"
fi
export AGENTMEMORY_SECRET="${AGENTMEMORY_SECRET:-$(cat "$HMAC_FILE")}"

# Write .env config for the daemon so it is loaded by src/app.py
cat > /home/user/.agentmemory/.env <<EOF
GEMINI_API_KEY=${GEMINI_API_KEY}
AGENTMEMORY_SECRET=${AGENTMEMORY_SECRET}
AGENTMEMORY_URL=http://localhost:7860
III_ENGINE_URL=ws://localhost:49134
GEMINI_MODEL=${GEMINI_MODEL:-gemini-2.5-flash}
EMBEDDING_PROVIDER=gemini
CONSOLIDATION_ENABLED=true
GRAPH_EXTRACTION_ENABLED=true
AGENTMEMORY_REFLECT=true
AGENTMEMORY_AUTO_COMPRESS=true
EOF

# Set the port for Flask application to run on (Hugging Face Space expects 7860)
export PORT=7860
export III_REST_PORT=7860

# Start Flask application in the foreground
echo "[start] Starting Flask application on port 7860..."
python3 src/app.py
