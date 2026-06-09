#!/usr/bin/env bash
# Note: no set -e — sync failures must not kill the container

# Create agentmemory data directories
mkdir -p /home/user/.agentmemory/dolt

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
DOLT_HOST=127.0.0.1
DOLT_PORT=3306
DOLT_DATABASE=agentmemory
EOF

# Start Dolt SQL server
echo "[start] Starting Dolt SQL Server..."
dolt sql-server --host 127.0.0.1 --port 3306 --data-dir /home/user/.agentmemory/dolt &

# Wait for Dolt server to boot
echo "[start] Waiting for Dolt server to be ready..."
sleep 5

# One-time migration: import legacy state_store.db binary files into Dolt SQL
STATE_STORE="/home/user/.agentmemory/state_store.db"
MIGRATION_DONE="/home/user/.agentmemory/.migration_done"
if [ -d "$STATE_STORE" ] && [ ! -f "$MIGRATION_DONE" ]; then
  echo "[start] Found legacy state_store.db — running one-time migration to Dolt SQL..."
  python3 -c "
import sys, os
sys.path.insert(0, '/app/src')
from db import StateKV
from import_data import import_old_data
kv = StateKV()
import_old_data('$STATE_STORE', kv)
" && touch "$MIGRATION_DONE" && echo "[start] Migration complete."
fi

# Set the port for Flask application to run on (Hugging Face Space expects 7860)
export PORT=7860
export III_REST_PORT=7860

# Start Flask application in the foreground
echo "[start] Starting Flask application on port 7860..."
python3 src/app.py
