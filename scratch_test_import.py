import os
import requests

def load_env():
    home = os.path.expanduser("~")
    env_path = os.path.join(home, ".agentmemory", ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")

load_env()
token = os.getenv("AGENTMEMORY_SECRET")
headers = {"Authorization": f"Bearer {token}"} if token else {}

print(f"Testing route with token: {token[:4]}..." if token else "Testing route with no token...")
r = requests.post("http://127.0.0.1:3111/agentmemory/replay/import-jsonl", json={}, headers=headers)
print("Status code:", r.status_code)
try:
    print("Response JSON:", r.json())
except Exception:
    print("Response text:", r.text[:300])
