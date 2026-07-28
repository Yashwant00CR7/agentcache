#!/usr/bin/env python
"""Optional confirm UX for the memory-flush pipeline (#41/#49).

By default the flush pipeline (Stop / PreCompact hooks) runs silently and always
proceeds. Setting ``AGENTCACHE_FLUSH_CONFIRM=true`` turns on an interactive
confirmation so the user sees the moment happening and can Save or Skip it:

    macOS   -> osascript dialog with Save / Skip buttons (choice is captured)
    Linux   -> notify-send heads-up only (notify-send cannot reliably capture
               action-button clicks across desktop environments), so it defaults
               to Save/proceed
    other   -> silent, proceed

``confirm_flush`` returns whether the flush should proceed:

    True  -> proceed (Save, or any fallback: disabled, unsupported platform,
             missing notifier, timeout, error)
    False -> the user explicitly chose Skip; the caller must suppress the flush

It never blocks compaction and never raises — any failure falls back to the
silent-push behaviour (proceed) rather than stopping the pipeline.
"""
import os
import shutil
import subprocess
import sys

_TITLE = "agentcache"


def _is_enabled():
    return os.environ.get("AGENTCACHE_FLUSH_CONFIRM", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _prompt_macos(label):
    """Show a Save/Skip dialog. Returns False only if the user picks Skip."""
    if not shutil.which("osascript"):
        return True  # no dialog mechanism -> silent proceed
    script = (
        f'display dialog "Flush session memory for {label}?" '
        f'with title "{_TITLE}" buttons {{"Skip", "Save"}} '
        f'default button "Save" giving up after 30'
    )
    proc = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=35,
        check=False,
    )
    # osascript echoes e.g. "button returned:Skip, gave up:false".
    return "button returned:Skip" not in (proc.stdout or "")


def _prompt_linux(label):
    """Surface a heads-up. notify-send can't capture a choice, so proceed."""
    if shutil.which("notify-send"):
        subprocess.run(
            ["notify-send", _TITLE, f"Flushing session memory for {label}…"],
            check=False,
            timeout=3,
        )
    return True


def confirm_flush(project=None):
    """Return True if the flush should proceed, False only on an explicit Skip.

    Never blocks and never raises — every failure path returns True (proceed).
    """
    if not _is_enabled():
        return True  # no confirmation UX -> silent auto-push

    label = project or "this project"
    try:
        if sys.platform == "darwin":
            return _prompt_macos(label)
        if sys.platform.startswith("linux"):
            return _prompt_linux(label)
    except Exception:
        # Missing binary, timeout, permissions, dismissal → silent fallback.
        return True

    return True  # unsupported platform -> silent proceed


if __name__ == "__main__":
    proj = sys.argv[1] if len(sys.argv) > 1 else None
    # Exit 0 = proceed/Save, exit 1 = Skip (useful for shell-level hooks).
    sys.exit(0 if confirm_flush(proj) else 1)
