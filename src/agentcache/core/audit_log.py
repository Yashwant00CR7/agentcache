"""Audit log — record, query, and safe-fire audit entries."""

import datetime
from typing import Any, Dict, List, Optional

from ..db import StateKV
from ..storage.paths import generate_id
from .kv_scopes import KV


def record_audit(
    kv: StateKV,
    operation: str,
    function_id: str,
    target_ids: List[str],
    details: Dict[str, Any] = {},
    quality_score: Optional[float] = None,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    entry = {
        "id": generate_id("aud"),
        "timestamp": datetime.datetime.now(datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "operation": operation,
        "userId": user_id,
        "functionId": function_id,
        "targetIds": target_ids,
        "details": details,
        "qualityScore": quality_score,
    }
    kv.set(KV.audit, entry["id"], entry)
    return entry


def safe_audit(
    kv: StateKV,
    operation: str,
    function_id: str,
    target_ids: List[str],
    details: Dict[str, Any] = {},
    quality_score: Optional[float] = None,
    user_id: Optional[str] = None,
) -> None:
    try:
        record_audit(
            kv, operation, function_id, target_ids, details, quality_score, user_id
        )
    except Exception as e:
        print(f"[audit] Failed to write audit: {e}")


def query_audit(
    kv: StateKV, filter_opts: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    all_entries = kv.list(KV.audit)
    entries = sorted(all_entries, key=lambda x: x.get("timestamp", ""), reverse=True)
    if not filter_opts:
        return entries[:100]

    op = filter_opts.get("operation")
    if op:
        entries = [e for e in entries if e.get("operation") == op]

    import dateutil.parser

    date_from = filter_opts.get("dateFrom")
    if date_from:
        try:
            dt_from = dateutil.parser.parse(date_from).replace(tzinfo=None)
            filtered_entries = []
            for e in entries:
                ts = e.get("timestamp")
                if ts:
                    try:
                        dt_ts = dateutil.parser.parse(ts).replace(tzinfo=None)
                        if dt_ts >= dt_from:
                            filtered_entries.append(e)
                    except Exception:
                        pass
            entries = filtered_entries
        except Exception:
            pass

    date_to = filter_opts.get("dateTo")
    if date_to:
        try:
            dt_to = dateutil.parser.parse(date_to).replace(tzinfo=None)
            filtered_entries = []
            for e in entries:
                ts = e.get("timestamp")
                if ts:
                    try:
                        dt_ts = dateutil.parser.parse(ts).replace(tzinfo=None)
                        if dt_ts <= dt_to:
                            filtered_entries.append(e)
                    except Exception:
                        pass
            entries = filtered_entries
        except Exception:
            pass

    limit = filter_opts.get("limit", 100)
    return entries[:limit]
