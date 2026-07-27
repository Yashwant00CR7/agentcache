"""LLM integration — Gemini calls for the consolidate pipeline."""

import datetime
import json
import os
import re
from typing import Any, Dict, List, Optional

from ..db import StateKV
from ..storage.paths import generate_id
from .audit_log import safe_audit
from .config import commit_if_enabled
from .context_builder import get_xml_children, get_xml_tag, strip_xml_wrappers
from .infer import vector_index_add_guarded
from .kv_scopes import KV
from .memory_store import memory_to_observation
from .session_store import list_sessions


def generate_content(system_instruction: str, prompt: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("No Gemini/Google API key found")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "generationConfig": {"temperature": 0.2},
    }

    req_data = json.dumps(payload).encode("utf-8")
    import urllib.request

    req = urllib.request.Request(
        url, data=req_data, headers={"Content-Type": "application/json"}, method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=60.0) as response:  # nosec B310
            resp_data = json.loads(response.read().decode("utf-8"))

        candidates = resp_data.get("candidates", [])
        if not candidates:
            raise RuntimeError("Gemini generateContent returned no candidates")

        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            raise RuntimeError("Gemini generateContent candidate content had no parts")

        return parts[0].get("text", "")
    except Exception as e:
        raise RuntimeError(f"Gemini generateContent call failed: {e}")


def consolidate(
    kv: StateKV, project: Optional[str] = None, min_observations: int = 10
) -> Dict[str, Any]:
    from .. import legacy as _legacy

    sessions = list_sessions(kv)
    if project:
        sessions = [s for s in sessions if s.get("project") == project]

    all_obs = []
    for s in sessions:
        obs_list = kv.list(KV.observations(s["id"]))
        for o in obs_list:
            if o.get("title") and o.get("importance", 5) >= 5:
                all_obs.append((o, s["id"]))

    if len(all_obs) < min_observations:
        return {
            "consolidated": 0,
            "reason": "insufficient_observations",
            "success": True,
        }

    concept_groups: Dict[str, List] = {}
    for obs, sid in all_obs:
        concepts = obs.get("concepts") or []
        for c in concepts:
            key = c.lower().strip()
            if not key:
                continue
            if key not in concept_groups:
                concept_groups[key] = []
            concept_groups[key].append((obs, sid))

    sorted_groups = sorted(
        [(k, g) for k, g in concept_groups.items() if len(g) >= 3],
        key=lambda x: len(x[1]),
        reverse=True,
    )

    consolidated_count = 0
    existing_memories = kv.list(KV.memories)

    MAX_LLM_CALLS = 10
    llm_calls = 0

    CONSOLIDATION_SYSTEM = """You are a memory consolidation engine. Given a set of related observations from coding sessions, synthesize them into a single long-term memory.

    Output XML:
    <memory>
      <type>pattern|preference|architecture|bug|workflow|fact</type>
      <title>Concise memory title (max 80 chars)</title>
      <content>2-4 sentence description of the learned insight</content>
      <concepts>
        <concept>key term</concept>
      </concepts>
      <files>
        <file>relevant/file/path</file>
      </files>
      <strength>1-10 how confident/important this memory is</strength>
    </memory>"""

    for concept, obs_group in sorted_groups:
        if llm_calls >= MAX_LLM_CALLS:
            break

        top = sorted(obs_group, key=lambda x: x[0].get("importance", 5), reverse=True)[
            :8
        ]
        session_ids = list(set([x[1] for x in top]))
        obs_ids = list(set([x[0]["id"] for x in top]))

        prompt_parts = []
        for obs, sid in top:
            prompt_parts.append(
                f"[{obs.get('type')}] {obs.get('title')}\n{obs.get('narrative') or ''}\nFiles: {', '.join(obs.get('files') or [])}\nImportance: {obs.get('importance', 5)}"
            )
        obs_prompt = "\n\n".join(prompt_parts)

        try:
            response = generate_content(
                CONSOLIDATION_SYSTEM,
                f'Concept: "{concept}"\n\nObservations:\n{obs_prompt}',
            )
            llm_calls += 1

            cleaned = strip_xml_wrappers(response)
            m_type = get_xml_tag(cleaned, "type") or "fact"
            m_title = get_xml_tag(cleaned, "title")
            m_content = get_xml_tag(cleaned, "content")

            if not m_title or not m_content:
                continue

            m_strength_str = get_xml_tag(cleaned, "strength") or "5"
            try:
                m_strength = max(1, min(10, int(m_strength_str)))
            except Exception:
                m_strength = 5

            concepts_list = get_xml_children(cleaned, "concepts", "concept")
            files_list = get_xml_children(cleaned, "files", "file")

            now = (
                datetime.datetime.now(datetime.timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )

            existing_match = None
            for mem in existing_memories:
                if (
                    mem.get("title", "").lower() == m_title.lower()
                    and mem.get("isLatest") is not False
                ):
                    if (
                        not project
                        or not mem.get("project")
                        or mem.get("project") == project
                    ):
                        existing_match = mem
                        break

            if existing_match:
                existing_match["isLatest"] = False
                kv.set(KV.memories, existing_match["id"], existing_match)

                evolved = {
                    "id": generate_id("mem"),
                    "createdAt": now,
                    "updatedAt": now,
                    "type": m_type,
                    "title": m_title,
                    "content": m_content,
                    "concepts": concepts_list,
                    "files": files_list,
                    "sessionIds": session_ids,
                    "strength": m_strength,
                    "version": (existing_match.get("version") or 1) + 1,
                    "parentId": existing_match["id"],
                    "supersedes": [existing_match["id"]]
                    + (existing_match.get("supersedes") or []),
                    "sourceObservationIds": obs_ids,
                    "isLatest": True,
                }
                if project:
                    evolved["project"] = project
                kv.set(KV.memories, evolved["id"], evolved)
                if _legacy._search_service:
                    try:
                        _legacy._search_service.bm25.add(memory_to_observation(evolved))
                        if existing_match:
                            _legacy._search_service.bm25.remove(existing_match["id"])
                    except Exception:
                        pass
                comb_text = evolved["title"] + " " + evolved["content"]
                vector_index_add_guarded(
                    evolved["id"],
                    "memory",
                    comb_text,
                    {"kind": "memory", "logId": evolved["id"]},
                )
                if (
                    _legacy._search_service
                    and _legacy._search_service.vector
                    and existing_match
                ):
                    try:
                        _legacy._search_service.vector.remove(existing_match["id"])
                    except Exception:
                        pass
                consolidated_count += 1
            else:
                memory = {
                    "id": generate_id("mem"),
                    "createdAt": now,
                    "updatedAt": now,
                    "type": m_type,
                    "title": m_title,
                    "content": m_content,
                    "concepts": concepts_list,
                    "files": files_list,
                    "sessionIds": session_ids,
                    "strength": m_strength,
                    "version": 1,
                    "sourceObservationIds": obs_ids,
                    "isLatest": True,
                }
                if project:
                    memory["project"] = project
                kv.set(KV.memories, memory["id"], memory)
                if _legacy._search_service:
                    try:
                        _legacy._search_service.bm25.add(memory_to_observation(memory))
                    except Exception:
                        pass
                comb_text = memory["title"] + " " + memory["content"]
                vector_index_add_guarded(
                    memory["id"],
                    "memory",
                    comb_text,
                    {"kind": "memory", "logId": memory["id"]},
                )
                consolidated_count += 1

        except Exception as e:
            print(f"[consolidate] Concept '{concept}' failed: {e}")

    # === Semantic Memory Fact Merger ===
    summaries = kv.list(KV.summaries)
    new_facts_count = 0
    if len(summaries) >= 5:
        recent_summaries = sorted(
            summaries, key=lambda s: s.get("createdAt", ""), reverse=True
        )[:20]

        SEMANTIC_MERGE_SYSTEM = """You are a memory consolidation engine. Given overlapping episodic memories (session summaries), extract stable factual knowledge.

        Output format (XML):
        <facts>
          <fact confidence="0.0-1.0">Concise factual statement</fact>
        </facts>

        Rules:
        - Extract only facts that appear in 2+ episodes or are highly confident
        - Confidence reflects how well-supported the fact is across episodes
        - Combine overlapping information into single concise facts
        - Skip ephemeral details (specific error messages, temporary states)"""

        prompt_parts = []
        for i, s in enumerate(recent_summaries):
            prompt_parts.append(
                f"[Episode {i + 1}]\nTitle: {s.get('title')}\nNarrative: {s.get('narrative') or ''}\nConcepts: {', '.join(s.get('concepts') or [])}"
            )
        merge_prompt = (
            "Consolidate these episodic memories into stable facts:\n\n"
            + "\n\n".join(prompt_parts)
        )

        try:
            response = generate_content(SEMANTIC_MERGE_SYSTEM, merge_prompt)
            fact_matches = re.findall(
                r'<fact\s+confidence="([^"]+)">([^<]+)</fact>', response, re.DOTALL
            )

            existing_semantic = kv.list(KV.semantic)
            now = (
                datetime.datetime.now(datetime.timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )

            for conf_str, fact_text in fact_matches:
                fact_text = fact_text.strip()
                try:
                    confidence = float(conf_str)
                except Exception:
                    confidence = 0.5

                existing = None
                for es in existing_semantic:
                    if es.get("fact", "").lower() == fact_text.lower():
                        existing = es
                        break

                if existing:
                    existing["accessCount"] = (existing.get("accessCount") or 0) + 1
                    existing["lastAccessedAt"] = now
                    existing["updatedAt"] = now
                    existing["confidence"] = max(
                        existing.get("confidence", 0.5), confidence
                    )
                    kv.set(KV.semantic, existing["id"], existing)
                else:
                    sem = {
                        "id": generate_id("sem"),
                        "fact": fact_text,
                        "confidence": confidence,
                        "sourceSessionIds": [
                            s["sessionId"] for s in recent_summaries if "sessionId" in s
                        ],
                        "sourceMemoryIds": [],
                        "accessCount": 1,
                        "lastAccessedAt": now,
                        "strength": confidence,
                        "createdAt": now,
                        "updatedAt": now,
                    }
                    kv.set(KV.semantic, sem["id"], sem)
                    new_facts_count += 1
        except Exception as e:
            print(f"[consolidate] Semantic merge failed: {e}")

    # === Procedural Memory Extraction ===
    memories = kv.list(KV.memories)
    new_procs_count = 0
    patterns = []
    for m in memories:
        if m.get("isLatest") is not False and m.get("type") == "pattern":
            freq = len(m.get("sessionIds") or [])
            if freq >= 2:
                patterns.append({"content": m.get("content", ""), "frequency": freq})

    if len(patterns) >= 2:
        PROCEDURAL_EXTRACTION_SYSTEM = """You are a procedural memory extractor. Given repeated patterns and workflows observed across sessions, extract reusable procedures.

        Output format (XML):
        <procedures>
          <procedure name="short descriptive name" trigger="when to use this procedure">
            <step>Step 1 description</step>
            <step>Step 2 description</step>
          </procedure>
        </procedures>

        Rules:
        - Only extract procedures observed 2+ times
        - Steps should be concrete and actionable
        - Trigger condition should be specific enough to match automatically"""

        prompt_parts = []
        for i, p in enumerate(patterns):
            prompt_parts.append(
                f"[Pattern {i + 1}] (seen {p['frequency']}x)\n{p['content']}"
            )
        proc_prompt = (
            "Extract reusable procedures from these recurring patterns:\n\n"
            + "\n\n".join(prompt_parts)
        )

        try:
            response = generate_content(PROCEDURAL_EXTRACTION_SYSTEM, proc_prompt)
            proc_matches = re.findall(
                r'<procedure\s+name="([^"]+)"\s+trigger="([^"]+)">([\s\S]*?)</procedure>',
                response,
                re.DOTALL,
            )

            existing_procs = kv.list(KV.procedural)
            now = (
                datetime.datetime.now(datetime.timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )

            for name, trigger, steps_block in proc_matches:
                steps = [
                    s.strip()
                    for s in re.findall(r"<step>([^<]+)</step>", steps_block, re.DOTALL)
                ]

                existing = None
                for ep in existing_procs:
                    if ep.get("name", "").lower() == name.lower():
                        existing = ep
                        break

                if existing:
                    existing["frequency"] = (existing.get("frequency") or 1) + 1
                    existing["updatedAt"] = now
                    existing["strength"] = min(
                        1.0, (existing.get("strength") or 0.5) + 0.1
                    )
                    kv.set(KV.procedural, existing["id"], existing)
                else:
                    proc = {
                        "id": generate_id("proc"),
                        "name": name,
                        "steps": steps,
                        "triggerCondition": trigger,
                        "frequency": 1,
                        "sourceSessionIds": [],
                        "strength": 0.5,
                        "createdAt": now,
                        "updatedAt": now,
                    }
                    kv.set(KV.procedural, proc["id"], proc)
                    new_procs_count += 1
        except Exception as e:
            print(f"[consolidate] Procedural extraction failed: {e}")

    res_summary = {
        "success": True,
        "consolidated": consolidated_count,
        "totalObservations": len(all_obs),
        "semantic": {"newFacts": new_facts_count, "totalSummaries": len(summaries)},
        "procedural": {
            "newProcedures": new_procs_count,
            "patternsAnalyzed": len(patterns),
        },
    }
    if _legacy._search_service and consolidated_count > 0:
        _legacy._search_service.schedule_persist()
    safe_audit(kv, "consolidate", "mem::consolidate-pipeline", [], res_summary)
    commit_if_enabled(
        kv,
        f"Consolidation complete: consolidated={consolidated_count}, facts={new_facts_count}, procs={new_procs_count}",
        "system",
    )
    return res_summary
