import os
import sys
import json
import urllib.request
import urllib.error
import subprocess
import threading
from pathlib import Path

DEFAULT_BASE_URL = "http://localhost:3111"

def load_env():
    """Load ~/.agentmemory/.env or XDG_CONFIG_HOME config into os.environ (best effort)."""
    candidates = []
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE")
    if home:
        candidates.append(Path(home) / ".agentmemory" / ".env")
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        candidates.append(Path(xdg_config) / "agentmemory" / ".env")
    
    for path in candidates:
        try:
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key:
                    os.environ.setdefault(key, val)
        except Exception:
            continue
    os.environ.setdefault("AGENTMEMORY_URL", DEFAULT_BASE_URL)

# Load env variables immediately upon importing this utility
load_env()

def resolve_project(cwd=None):
    explicit = os.environ.get("AGENTMEMORY_PROJECT_NAME")
    if explicit and explicit.strip():
        return explicit.strip()
    
    directory = cwd if (cwd and cwd.strip()) else os.getcwd()
    try:
        top = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=directory,
            stderr=subprocess.DEVNULL,
            timeout=0.5
        ).decode().strip()
        if top:
            return os.path.basename(top)
    except Exception:
        pass
    
    return os.path.basename(os.path.abspath(directory))

def is_sdk_child(payload):
    if os.environ.get("AGENTMEMORY_SDK_CHILD") == "1":
        return True
    if not isinstance(payload, dict):
        return False
    return payload.get("entrypoint") == "sdk-ts" or payload.get("entrypoint") == "sdk-python"

def api_call(path, body=None, timeout=1.5):
    base_url = os.environ.get("AGENTMEMORY_URL", DEFAULT_BASE_URL)
    url = f"{base_url}/agentmemory/{path}"
    headers = {"Content-Type": "application/json"}
    secret = os.environ.get("AGENTMEMORY_SECRET", "")
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
        
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8") if body else None,
        headers=headers,
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

def api_call_bg(path, body=None):
    t = threading.Thread(target=api_call, args=(path, body), daemon=True)
    t.start()
