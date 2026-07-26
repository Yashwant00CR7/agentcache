"""
Viewer HTML rendering helpers.

Separated from app.py so the factory stays under 150 lines.
"""

import base64
import os
import secrets

from flask import make_response


def make_viewer_response(base_dir: str):
    """
    Read src/viewer/index.html, inject nonce + version + token, set CSP headers.

    Returns a Flask Response object ready to send to the client.
    Raises an exception if the template file is missing.
    """
    template_path = os.path.join(base_dir, "viewer", "index.html")
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    nonce = (
        base64.urlsafe_b64encode(secrets.token_bytes(16)).decode("utf-8").rstrip("=")
    )

    # D2.2: Never embed the raw AGENTCACHE_SECRET in page source.
    # Replace the placeholder with an empty string — the viewer authenticates
    # via the Authorization header set programmatically after load.
    html = (
        template.replace("__AGENTCACHE_VIEWER_NONCE__", nonce)
        .replace("__AGENTCACHE_VERSION__", "0.9.11")
        .replace("__AGENTCACHE_AUTO_TOKEN__", "")
    )

    csp = "; ".join(
        [
            "default-src 'none'",
            "base-uri 'none'",
            "frame-ancestors 'self' https://huggingface.co https://*.hf.space",
            "object-src 'none'",
            "form-action 'none'",
            f"script-src 'nonce-{nonce}'",
            "script-src-attr 'none'",
            "style-src 'unsafe-inline'",
            (
                "connect-src 'self' https: http://localhost:* http://127.0.0.1:* "
                "wss: ws://localhost:* ws://127.0.0.1:* wss://localhost:* wss://127.0.0.1:*"
            ),
            "img-src 'self' data:",
            "font-src 'self'",
        ]
    )

    res = make_response(html)
    res.headers["Content-Type"] = "text/html; charset=utf-8"
    res.headers["Content-Security-Policy"] = csp
    res.headers["Cache-Control"] = "no-cache"
    return res


def make_inkwell_response(base_dir: str):
    """
    Serve the Inkwell alternate viewer theme with a nonced inline script.

    Same posture as ``make_viewer_response`` — nonce for the inline
    ``<script>`` block, strict CSP — but relaxes ``style-src`` and
    ``font-src`` to permit Google Fonts, which the Inkwell aesthetic
    (Caveat / Inter / JetBrains Mono) depends on.
    """
    template_path = os.path.join(base_dir, "viewer", "inkwell.html")
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    nonce = (
        base64.urlsafe_b64encode(secrets.token_bytes(16)).decode("utf-8").rstrip("=")
    )

    html = template.replace("__INKWELL_NONCE__", nonce)

    csp = "; ".join(
        [
            "default-src 'none'",
            "base-uri 'none'",
            "frame-ancestors 'self' https://huggingface.co https://*.hf.space",
            "object-src 'none'",
            "form-action 'none'",
            f"script-src 'nonce-{nonce}'",
            "script-src-attr 'none'",
            "style-src 'unsafe-inline' https://fonts.googleapis.com",
            (
                "connect-src 'self' https: http://localhost:* http://127.0.0.1:* "
                "wss: ws://localhost:* ws://127.0.0.1:* wss://localhost:* wss://127.0.0.1:*"
            ),
            "img-src 'self' data:",
            "font-src 'self' https://fonts.gstatic.com",
        ]
    )

    res = make_response(html)
    res.headers["Content-Type"] = "text/html; charset=utf-8"
    res.headers["Content-Security-Policy"] = csp
    res.headers["Cache-Control"] = "no-cache"
    return res
