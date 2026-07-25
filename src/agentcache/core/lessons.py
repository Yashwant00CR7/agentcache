"""Lessons learned — save, recall, reinforce, decay."""

import datetime
from typing import Any, Dict

from ..db import StateKV
from ..storage.paths import fingerprint_id
from .audit_log import safe_audit
from .config import commit_if_enabled, get_agent_id
from .kv_scopes import KV
from .privacy import strip_private_data


def reinforce_lesson(lesson: Dict[str, Any]) -> None:
    now = (
        datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    )
    lesson["reinforcements"] = lesson.get("reinforcements", 0) + 1
    conf = lesson.get("confidence", 0.5)
    lesson["confidence"] = min(1.0, conf + 0.1 * (1 - conf))
    lesson["lastReinforcedAt"] = now
    lesson["updatedAt"] = now


def lesson_save(kv: StateKV, data: Dict[str, Any]) -> Dict[str, Any]:
    content = data.get("content")
    if not content or not content.strip():
        return {"success": False, "error": "content is required"}
    content = strip_private_data(content)
    context_str = strip_private_data(data.get("context") or "")

    agent_id = data.get("agentId") or get_agent_id()
    fp = fingerprint_id("lsn", content)
    existing = kv.get(KV.lessons, fp)

    if existing and not existing.get("deleted"):
        reinforce_lesson(existing)
        if context_str and not existing.get("context"):
            existing["context"] = context_str
        kv.set(KV.lessons, existing["id"], existing)
        safe_audit(kv, "lesson_strengthen", "mem::lesson-save", [existing["id"]])

        commit_if_enabled(
            kv, f"Strengthen lesson: {existing.get('content', '')[:60]}", agent_id
        )

        return {"success": True, "action": "strengthened", "lesson": existing}

    confidence = data.get("confidence")
    if not isinstance(confidence, (int, float)) or confidence < 0 or confidence > 1:
        confidence = 0.5

    now = (
        datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    )
    lesson = {
        "id": fp,
        "content": content.strip(),
        "context": context_str.strip(),
        "confidence": confidence,
        "reinforcements": 0,
        "source": data.get("source") or "manual",
        "sourceIds": data.get("sourceIds") or [],
        "project": data.get("project"),
        "tags": data.get("tags") or [],
        "createdAt": now,
        "updatedAt": now,
        "decayRate": 0.05,
    }
    kv.set(KV.lessons, lesson["id"], lesson)
    safe_audit(kv, "lesson_save", "mem::lesson-save", [lesson["id"]])

    commit_if_enabled(kv, f"Create lesson: {lesson['content'][:60]}", agent_id)

    return {"success": True, "action": "created", "lesson": lesson}


def lesson_list(kv: StateKV, data: Dict[str, Any]) -> Dict[str, Any]:
    limit = data.get("limit") or 50
    min_confidence = data.get("minConfidence") or 0.0
    all_lessons = kv.list(KV.lessons)

    lessons = [
        les
        for les in all_lessons
        if not les.get("deleted") and les.get("confidence", 0.5) >= min_confidence
    ]

    project = data.get("project")
    if project:
        lessons = [les for les in lessons if les.get("project") == project]
    source = data.get("source")
    if source:
        lessons = [les for les in lessons if les.get("source") == source]

    lessons.sort(key=lambda x: x.get("confidence", 0.5), reverse=True)
    return {"success": True, "lessons": lessons[:limit]}


def lesson_recall(kv: StateKV, data: Dict[str, Any]) -> Dict[str, Any]:
    query = data.get("query")
    if not query or not query.strip():
        return {"success": False, "error": "query is required"}

    query_lower = query.lower()
    min_confidence = data.get("minConfidence") or 0.1
    limit = data.get("limit") or 10

    all_lessons = kv.list(KV.lessons)
    lessons = [
        les
        for les in all_lessons
        if not les.get("deleted") and les.get("confidence", 0.5) >= min_confidence
    ]

    project = data.get("project")
    if project:
        lessons = [les for les in lessons if les.get("project") == project]

    scored = []
    terms = [t for t in query_lower.split() if len(t) > 1]

    for les in lessons:
        text = f"{les.get('content', '')} {les.get('context', '')} {' '.join(les.get('tags') or [])}".lower()
        match_count = sum(1 for t in terms if t in text)
        if match_count == 0:
            continue

        relevance = match_count / len(terms)
        baseline = les.get("lastReinforcedAt") or les.get("createdAt")
        import dateutil.parser

        dt = dateutil.parser.parse(baseline)
        days = (
            datetime.datetime.now(datetime.timezone.utc)
            - dt.replace(tzinfo=datetime.timezone.utc)
        ).total_seconds() / (3600 * 24)
        recency_boost = 1 / (1 + days * 0.01)
        score = les.get("confidence", 0.5) * relevance * recency_boost
        scored.append({"lesson": les, "score": score})

    scored.sort(key=lambda x: x["score"], reverse=True)
    results = []
    for s in scored[:limit]:
        item = dict(s["lesson"])
        item["score"] = round(s["score"], 3)
        results.append(item)

    safe_audit(
        kv,
        "lesson_recall",
        "mem::lesson-recall",
        [],
        {"query": query, "resultCount": len(results)},
    )
    return {"success": True, "lessons": results}


def lesson_strengthen(kv: StateKV, lesson_id: str) -> Dict[str, Any]:
    lesson = kv.get(KV.lessons, lesson_id)
    if not lesson or lesson.get("deleted"):
        return {"success": False, "error": "lesson not found"}

    reinforce_lesson(lesson)
    kv.set(KV.lessons, lesson["id"], lesson)
    safe_audit(kv, "lesson_strengthen", "mem::lesson-strengthen", [lesson["id"]])

    commit_if_enabled(
        kv, f"Strengthen lesson: {lesson.get('content', '')[:60]}", get_agent_id()
    )

    return {"success": True, "lesson": lesson}


def lesson_decay_sweep(kv: StateKV) -> Dict[str, Any]:
    all_lessons = kv.list(KV.lessons)
    decayed = 0
    soft_deleted = 0
    now = datetime.datetime.now(datetime.timezone.utc)
    timestamp = now.isoformat().replace("+00:00", "Z")

    for les in all_lessons:
        if les.get("deleted"):
            continue
        baseline_str = (
            les.get("lastDecayedAt") or les.get("lastReinforcedAt") or les["createdAt"]
        )
        import dateutil.parser

        dt = dateutil.parser.parse(baseline_str)
        weeks = (now - dt.replace(tzinfo=datetime.timezone.utc)).total_seconds() / (
            3600 * 24 * 7
        )
        if weeks < 1.0:
            continue

        decay = les.get("decayRate", 0.05) * weeks
        new_conf = max(0.05, les.get("confidence", 0.5) - decay)

        if new_conf != les.get("confidence"):
            before = les.get("confidence", 0.5)
            les["confidence"] = round(new_conf, 3)
            les["lastDecayedAt"] = timestamp
            les["updatedAt"] = timestamp

            if les["confidence"] <= 0.1 and les.get("reinforcements", 0) == 0:
                les["deleted"] = True
                soft_deleted += 1
            else:
                decayed += 1

            kv.set(KV.lessons, les["id"], les)
            safe_audit(
                kv,
                "lesson_strengthen",
                "mem::lesson-decay-sweep",
                [les["id"]],
                {
                    "action": "soft-delete" if les.get("deleted") else "decay",
                    "actor": "system",
                    "reason": "decay-sweep",
                    "before": {"confidence": before, "deleted": False},
                    "after": {
                        "confidence": les["confidence"],
                        "deleted": bool(les.get("deleted")),
                    },
                },
            )

    if decayed > 0 or soft_deleted > 0:
        commit_if_enabled(
            kv,
            f"Lesson decay sweep: decayed {decayed}, soft-deleted {soft_deleted}",
            "system",
        )

    return {
        "success": True,
        "decayed": decayed,
        "softDeleted": soft_deleted,
        "total": len(all_lessons),
    }
