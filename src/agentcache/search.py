import array
import base64
import json
import math
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Set, Tuple

# =====================================================================
# Custom Porter-like Stemmer (Ported from stemmer.ts)
# =====================================================================

step2map = {
    "ational": "ate",
    "tional": "tion",
    "enci": "ence",
    "anci": "ance",
    "izer": "ize",
    "iser": "ise",
    "abli": "able",
    "alli": "al",
    "entli": "ent",
    "eli": "e",
    "ousli": "ous",
    "ization": "ize",
    "isation": "ise",
    "ation": "ate",
    "ator": "ate",
    "alism": "al",
    "iveness": "ive",
    "fulness": "ful",
    "ousness": "ous",
    "aliti": "al",
    "iviti": "ive",
    "biliti": "ble",
}

step3map = {
    "icate": "ic",
    "ative": "",
    "alize": "al",
    "alise": "al",
    "iciti": "ic",
    "ical": "ic",
    "ful": "",
    "ness": "",
}


def _has_vowel(s: str) -> bool:
    return any(c in "aeiou" for c in s)


def _measure(s: str) -> int:
    # Reduce non-vowels (excluding y) to C, vowels (+y) to V
    reduced = ""
    for c in s:
        if c in "aeiouy":
            if not reduced or reduced[-1] != "V":
                reduced += "V"
        else:
            if not reduced or reduced[-1] != "C":
                reduced += "C"
    # count "VC" patterns
    return len(re.findall(r"VC", reduced))


def _ends_double_consonant(s: str) -> bool:
    return len(s) >= 2 and s[-1] == s[-2] and s[-1] not in "aeiou"


def _ends_cvc(s: str) -> bool:
    if len(s) < 3:
        return False
    c1, v, c2 = s[-3], s[-2], s[-1]
    return c1 not in "aeiou" and v in "aeiou" and c2 not in "aeiouwxy"


def stem(word: str) -> str:
    if len(word) <= 2:
        return word

    w = word

    # Step 1a
    if w.endswith("sses"):
        w = w[:-2]
    elif w.endswith("ies"):
        w = w[:-2]
    elif not w.endswith("ss") and w.endswith("s"):
        w = w[:-1]

    # Step 1b
    if w.endswith("eed"):
        if _measure(w[:-3]) > 0:
            w = w[:-1]
    elif w.endswith("ed") and _has_vowel(w[:-2]):
        w = w[:-2]
        if w.endswith("at") or w.endswith("bl") or w.endswith("iz"):
            w += "e"
        elif _ends_double_consonant(w) and not w.endswith(("l", "s", "z")):
            w = w[:-1]
        elif _measure(w) == 1 and _ends_cvc(w):
            w += "e"
    elif w.endswith("ing") and _has_vowel(w[:-3]):
        w = w[:-3]
        if w.endswith("at") or w.endswith("bl") or w.endswith("iz"):
            w += "e"
        elif _ends_double_consonant(w) and not w.endswith(("l", "s", "z")):
            w = w[:-1]
        elif _measure(w) == 1 and _ends_cvc(w):
            w += "e"

    # Step 1c
    if w.endswith("y") and _has_vowel(w[:-1]):
        w = w[:-1] + "i"

    # Step 2
    for suffix, replacement in step2map.items():
        if w.endswith(suffix):
            base = w[: -len(suffix)]
            if _measure(base) > 0:
                w = base + replacement
            break

    # Step 3
    for suffix, replacement in step3map.items():
        if w.endswith(suffix):
            base = w[: -len(suffix)]
            if _measure(base) > 0:
                w = base + replacement
            break

    # Step 4
    suffixes_step4 = (
        "al",
        "ance",
        "ence",
        "er",
        "ic",
        "able",
        "ible",
        "ant",
        "ement",
        "ment",
        "ent",
        "tion",
        "sion",
        "ou",
        "ism",
        "ate",
        "iti",
        "ous",
        "ive",
        "ize",
        "ise",
    )
    if w.endswith(suffixes_step4):
        # find matching suffix length
        match = re.search(
            r"(ement|ment|tion|sion|ance|ence|able|ible|ism|ate|iti|ous|ive|ize|ise|ant|ent|al|er|ic|ou)$",
            w,
        )
        if match:
            suffix_len = len(match.group(1))
            base = w[:-suffix_len]
            if _measure(base) > 1:
                w = base

    # Step 5a
    if w.endswith("e"):
        base = w[:-1]
        if _measure(base) > 1 or (_measure(base) == 1 and not _ends_cvc(base)):
            w = base

    # Step 5b
    if _ends_double_consonant(w) and w.endswith("l") and _measure(w[:-1]) > 1:
        w = w[:-1]

    return w


# =====================================================================
# Synonym Map (Ported from synonyms.ts)
# =====================================================================

SYNONYM_GROUPS = [
    ["auth", "authentication", "authn", "authenticating"],
    ["authz", "authorization", "authorizing"],
    ["db", "database", "datastore"],
    ["perf", "performance", "latency", "throughput", "slow", "bottleneck"],
    ["optim", "optimization", "optimizing", "optimise", "query-optimization"],
    ["k8s", "kubernetes", "kube"],
    ["config", "configuration", "configuring", "setup"],
    ["deps", "dependencies", "dependency"],
    ["env", "environment"],
    ["fn", "function"],
    ["impl", "implementation", "implementing"],
    ["msg", "message", "messaging"],
    ["repo", "repository"],
    ["req", "request"],
    ["res", "response"],
    ["ts", "typescript"],
    ["js", "javascript"],
    ["pg", "postgres", "postgresql"],
    ["err", "error", "errors"],
    ["api", "endpoint", "endpoints"],
    ["ci", "continuous-integration"],
    ["cd", "continuous-deployment"],
    ["test", "testing", "tests"],
    ["doc", "documentation", "docs"],
    ["infra", "infrastructure"],
    ["deploy", "deployment", "deploying"],
    ["cache", "caching", "cached"],
    ["log", "logging", "logs"],
    ["monitor", "monitoring"],
    ["observe", "observability"],
    ["sec", "security", "secure"],
    ["validate", "validation", "validating"],
    ["migrate", "migration", "migrations"],
    ["debug", "debugging"],
    ["container", "containerization", "docker"],
    ["crash", "crashloop", "crashloopbackoff"],
    ["webhook", "webhooks", "callback"],
    ["middleware", "mw"],
    ["paginate", "pagination"],
    ["serialize", "serialization"],
    ["encrypt", "encryption"],
    ["hash", "hashing"],
]

synonymMap: Dict[str, Set[str]] = {}
for group in SYNONYM_GROUPS:
    stemmed = [stem(t.lower()) for t in group]
    for s in stemmed:
        if s not in synonymMap:
            synonymMap[s] = set()
        for other in stemmed:
            if other != s:
                synonymMap[s].add(other)


def get_synonyms(stemmed_term: str) -> List[str]:
    return list(synonymMap.get(stemmed_term, []))


# =====================================================================
# CJK Segmenter (Ported from cjk-segmenter.ts)
# =====================================================================

CJK_RE = re.compile(
    r"[\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uff9f\u4e00-\u9faf\uac00-\ud7a3]"
)
CJK_RUN_RE = re.compile(
    r"[\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uff9f\u4e00-\u9faf\uac00-\ud7a3]+"
)
HANGUL_RE = re.compile(r"[\uac00-\ud7a3]")
KANA_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
HANGUL_BLOCK_RE = re.compile(r"[가-힯]+")

jieba_loaded = False
jieba_instance = None


def get_jieba():
    global jieba_loaded, jieba_instance
    if jieba_loaded:
        return jieba_instance
    jieba_loaded = True
    try:
        import jieba

        jieba_instance = jieba
    except ImportError:
        print(
            "[search] Install jieba to improve Chinese word segmentation (pip install jieba)"
        )
    return jieba_instance


def has_cjk(text: str) -> bool:
    return bool(CJK_RE.search(text))


def segment_cjk(text: str) -> List[str]:
    if not has_cjk(text):
        return [text]

    out: List[str] = []
    cursor = 0

    for match in CJK_RUN_RE.finditer(text):
        start = match.start()
        run = match.group(0)
        end = match.end()

        if start > cursor:
            piece = text[cursor:start].strip()
            if piece:
                out.append(piece)

        if HANGUL_RE.search(run):
            # Hangul: split by blocks
            out.extend(HANGUL_BLOCK_RE.findall(run))
        elif KANA_RE.search(run):
            # Japanese Kana fallback: split every character
            out.extend(list(run))
        else:
            # Chinese Han: use jieba if available
            jb = get_jieba()
            if jb:
                out.extend([t.strip() for t in jb.cut(run, cut_all=False) if t.strip()])
            else:
                out.extend(list(run))

        cursor = end

    if cursor < len(text):
        trailing = text[cursor:].strip()
        if trailing:
            out.append(trailing)

    return out


# =====================================================================
# SearchIndex (BM25 - Ported from search-index.ts)
# =====================================================================


class SearchIndex:
    def __init__(self):
        self.entries: Dict[str, Dict[str, Any]] = {}
        self.inverted_index: Dict[str, Set[str]] = {}
        self.doc_term_counts: Dict[str, Dict[str, int]] = {}
        self.total_doc_length = 0
        self.sorted_terms: Optional[List[str]] = None
        self._dirty: bool = False  # A4.2

        self.k1 = 1.2
        self.b = 0.75

    def add(self, obs: Dict[str, Any]) -> None:
        obs_id = obs.get("id")
        if not obs_id:
            return

        terms = self.extract_terms(obs)
        term_freq: Dict[str, int] = {}
        term_count = 0

        for term in terms:
            term_freq[term] = term_freq.get(term, 0) + 1
            term_count += 1

        self.entries[obs_id] = {
            "obsId": obs_id,
            "sessionId": obs.get("sessionId", ""),
            "termCount": term_count,
        }
        self.doc_term_counts[obs_id] = term_freq
        self.total_doc_length += term_count

        for term in term_freq.keys():
            if term not in self.inverted_index:
                self.inverted_index[term] = set()
            self.inverted_index[term].add(obs_id)

        self.sorted_terms = None
        self._dirty = True  # A4.2

    def has(self, id: str) -> bool:
        return id in self.entries

    def remove(self, id: str) -> None:
        entry = self.entries.get(id)
        if not entry:
            return

        term_freq = self.doc_term_counts.get(id)
        if term_freq:
            for term in term_freq.keys():
                posting_list = self.inverted_index.get(term)
                if posting_list:
                    posting_list.discard(id)
                    if not posting_list:
                        self.inverted_index.pop(term, None)
            self.doc_term_counts.pop(id, None)

        self.total_doc_length = max(0, self.total_doc_length - entry["termCount"])
        self.entries.pop(id, None)
        self.sorted_terms = None
        self._dirty = True  # A4.2

    def search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        raw_terms = self.tokenize(query.lower())
        if not raw_terms:
            return []

        N = len(self.entries)
        if N == 0:
            return []
        avg_doc_len = self.total_doc_length / N

        query_terms: List[Dict[str, Any]] = []
        seen = set()
        for term in raw_terms:
            if term not in seen:
                seen.add(term)
                query_terms.append({"term": term, "weight": 1.0})
            for syn in get_synonyms(term):
                if syn not in seen:
                    seen.add(syn)
                    query_terms.append({"term": syn, "weight": 0.7})

        scores: Dict[str, float] = {}
        sorted_terms = self.get_sorted_terms()

        for q_item in query_terms:
            term = q_item["term"]
            weight = q_item["weight"]

            matching_docs = self.inverted_index.get(term)
            if matching_docs:
                df = len(matching_docs)
                idf = math.log((N - df + 0.5) / (df + 0.5) + 1)

                for obs_id in matching_docs:
                    entry = self.entries[obs_id]
                    doc_terms = self.doc_term_counts.get(obs_id, {})
                    tf = doc_terms.get(term, 0)
                    doc_len = entry["termCount"]

                    numerator = tf * (self.k1 + 1)
                    denominator = tf + self.k1 * (
                        1 - self.b + self.b * (doc_len / avg_doc_len)
                    )
                    bm25_score = idf * (numerator / denominator) * weight

                    scores[obs_id] = scores.get(obs_id, 0.0) + bm25_score

            # Prefix matching (binary search)
            start_idx = self.lower_bound(sorted_terms, term)
            for si in range(start_idx, len(sorted_terms)):
                index_term = sorted_terms[si]
                if not index_term.startswith(term):
                    break
                if index_term == term:
                    continue

                obs_ids = self.inverted_index.get(index_term, set())
                prefix_df = len(obs_ids)
                prefix_idf = (
                    math.log((N - prefix_df + 0.5) / (prefix_df + 0.5) + 1) * 0.5
                )

                for obs_id in obs_ids:
                    entry = self.entries[obs_id]
                    doc_terms = self.doc_term_counts.get(obs_id, {})
                    tf = doc_terms.get(index_term, 0)
                    doc_len = entry["termCount"]
                    numerator = tf * (self.k1 + 1)
                    denominator = tf + self.k1 * (
                        1 - self.b + self.b * (doc_len / avg_doc_len)
                    )
                    scores[obs_id] = (
                        scores.get(obs_id, 0.0)
                        + prefix_idf * (numerator / denominator) * weight
                    )

        results = []
        for obs_id, score in scores.items():
            entry = self.entries[obs_id]
            results.append(
                {"obsId": obs_id, "sessionId": entry["sessionId"], "score": score}
            )

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    @property
    def size(self) -> int:
        return len(self.entries)

    def clear(self) -> None:
        self.entries.clear()
        self.inverted_index.clear()
        self.doc_term_counts.clear()
        self.total_doc_length = 0
        self.sorted_terms = None

    def restore_from_data(self, data: Dict[str, Any]) -> None:
        self.clear()
        if not data:
            return

        for k, v in data.get("entries", []):
            self.entries[k] = v
        for term, ids in data.get("inverted", []):
            self.inverted_index[term] = set(ids)
        for id_, counts in data.get("docTerms", []):
            self.doc_term_counts[id_] = dict(counts)
        self.total_doc_length = int(data.get("totalDocLength", 0))
        self._dirty = False  # A4.2 — freshly loaded, not dirty

    def serialize_data(self) -> Dict[str, Any]:
        entries = list(self.entries.items())
        inverted = [(term, list(ids)) for term, ids in self.inverted_index.items()]
        doc_terms = [
            (id_, list(counts.items())) for id_, counts in self.doc_term_counts.items()
        ]
        return {
            "v": 2,
            "entries": entries,
            "inverted": inverted,
            "docTerms": doc_terms,
            "totalDocLength": self.total_doc_length,
        }

    def extract_terms(self, obs: Dict[str, Any]) -> List[str]:
        parts = [
            obs.get("title", ""),
            obs.get("subtitle", "") or "",
            obs.get("narrative", "") or "",
            " ".join(obs.get("facts", []) or []),
            " ".join(obs.get("concepts", []) or []),
            " ".join(obs.get("files", []) or []),
            obs.get("type", ""),
        ]
        return self.tokenize(" ".join(parts).lower())

    def tokenize(self, text: str) -> List[str]:
        # Strip special characters except valid separators
        cleaned = re.sub(r"[^\w\s/.\\-_]", " ", text)
        out = []
        for raw in cleaned.split():
            if len(raw) < 2:
                continue
            if has_cjk(raw):
                for seg in segment_cjk(raw):
                    if len(seg) >= 1:
                        out.append(seg)
            else:
                out.append(stem(raw))
        return out

    def get_sorted_terms(self) -> List[str]:
        if not self.sorted_terms:
            self.sorted_terms = sorted(self.inverted_index.keys())
        return self.sorted_terms

    def lower_bound(self, arr: List[str], target: str) -> int:
        lo = 0
        hi = len(arr)
        while lo < hi:
            mid = (lo + hi) // 2
            if arr[mid] < target:
                lo = mid + 1
            else:
                hi = mid
        return lo


# =====================================================================
# VectorIndex (Cosine Similarity - Ported from vector-index.ts)
# =====================================================================


def float32_to_base64(floats: List[float]) -> str:
    arr = array.array("f", floats)
    return base64.b64encode(arr.tobytes()).decode("utf-8")


def base64_to_float32(b64: str) -> List[float]:
    arr = array.array("f")
    arr.frombytes(base64.b64decode(b64))
    return list(arr)


def cosine_similarity(a: List[float], b: List[float]) -> float:
    if len(a) != len(b) or len(a) == 0:
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    denom = math.sqrt(norm_a) * math.sqrt(norm_b)
    return dot / denom if denom != 0.0 else 0.0


class VectorIndex:
    def __init__(self):
        self.vectors: Dict[str, Dict[str, Any]] = {}
        self._dirty: bool = False  # A4.2

    def add(self, obs_id: str, session_id: str, embedding: List[float]) -> None:
        self.vectors[obs_id] = {"embedding": embedding, "sessionId": session_id}
        self._dirty = True  # A4.2

    def remove(self, obs_id: str) -> None:
        if obs_id in self.vectors:
            self.vectors.pop(obs_id, None)
            self._dirty = True  # A4.2

    def search(self, query: List[float], limit: int = 20) -> List[Dict[str, Any]]:
        results = []
        for obs_id, entry in self.vectors.items():
            score = cosine_similarity(query, entry["embedding"])
            results.append(
                {"obsId": obs_id, "sessionId": entry["sessionId"], "score": score}
            )

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    @property
    def size(self) -> int:
        return len(self.vectors)

    def validate_dimensions(
        self, expected: int
    ) -> Tuple[List[Dict[str, Any]], Set[int]]:
        mismatches = []
        seen_dimensions = set()
        for obs_id, entry in self.vectors.items():
            dim = len(entry["embedding"])
            seen_dimensions.add(dim)
            if dim != expected:
                mismatches.append({"obsId": obs_id, "dim": dim})
        return mismatches, seen_dimensions

    def clear(self) -> None:
        self.vectors.clear()

    def serialize_data(self) -> List[Any]:
        data = []
        for obs_id, entry in self.vectors.items():
            data.append(
                [
                    obs_id,
                    {
                        "embedding": float32_to_base64(entry["embedding"]),
                        "sessionId": entry["sessionId"],
                    },
                ]
            )
        return data

    def restore_from_data(self, data: List[Any]) -> None:
        self.clear()
        if not isinstance(data, list):
            return
        for row in data:
            try:
                if not isinstance(row, list) or len(row) < 2:
                    continue
                obs_id, entry = row
                if not isinstance(obs_id, str) or not isinstance(entry, dict):
                    continue
                emb_b64 = entry.get("embedding")
                sess_id = entry.get("sessionId")
                if not isinstance(emb_b64, str) or not isinstance(sess_id, str):
                    continue
                self.vectors[obs_id] = {
                    "embedding": base64_to_float32(emb_b64),
                    "sessionId": sess_id,
                }
            except Exception:
                continue


# =====================================================================
# Gemini Embedding Client (Urllib POST completion)
# =====================================================================


class GeminiEmbeddingProvider:
    def __init__(self, api_key: str):
        self.name = "gemini"
        self.dimensions = 768
        self.api_key = api_key
        self.model = "models/gemini-embedding-001"
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/{self.model}:batchEmbedContents"

    def embed(self, text: str) -> List[float]:
        results = self.embed_batch([text])
        return results[0]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        results: List[List[float]] = []
        batch_limit = 100

        for i in range(0, len(texts), batch_limit):
            chunk = texts[i : i + batch_limit]

            payload = {
                "requests": [
                    {
                        "model": self.model,
                        "content": {"parts": [{"text": t}]},
                        "outputDimensionality": self.dimensions,
                    }
                    for t in chunk
                ]
            }

            req_data = json.dumps(payload).encode("utf-8")
            url = f"{self.api_url}?key={self.api_key}"

            req = urllib.request.Request(
                url,
                data=req_data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            try:
                with urllib.request.urlopen(req, timeout=30.0) as response:  # nosec B310
                    resp_data = json.loads(response.read().decode("utf-8"))

                for emb in resp_data.get("embeddings", []):
                    values = emb.get("values", [])
                    results.append(self._l2_normalize(values))
            except Exception as e:
                raise RuntimeError(f"Gemini embedding batch call failed: {e}")

        return results

    def _l2_normalize(self, vec: List[float]) -> List[float]:
        sum_sq = sum(x * x for x in vec)
        norm = math.sqrt(sum_sq)
        if norm == 0:
            return vec
        return [x / norm for x in vec]


# =====================================================================
# HybridSearch (Triple Stream - Ported from hybrid-search.ts)
# =====================================================================


class HybridSearch:
    def __init__(
        self,
        bm25: SearchIndex,
        vector: Optional[VectorIndex],
        embedding_provider: Optional[GeminiEmbeddingProvider],
        kv: Any,
        bm25_weight: float = 0.4,
        vector_weight: float = 0.6,
        graph_weight: float = 0.3,
    ):
        self.bm25 = bm25
        self.vector = vector
        self.embedding_provider = embedding_provider
        self.kv = kv
        self.bm25_weight = bm25_weight
        self.vector_weight = vector_weight
        self.graph_weight = graph_weight

    def search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        # Triple-stream search combining BM25, vectors, and graph weights
        bm25_results = self.bm25.search(query, limit * 2)

        vector_results: List[Dict[str, Any]] = []
        if self.vector and self.embedding_provider and self.vector.size > 0:
            try:
                query_embedding = self.embedding_provider.embed(query)
                vector_results = self.vector.search(query_embedding, limit * 2)
            except Exception:
                pass  # Fallback to BM25

        # Build scores mapping
        scores: Dict[str, Dict[str, Any]] = {}
        RRF_K = 60

        for idx, r in enumerate(bm25_results):
            obs_id = r["obsId"]
            scores[obs_id] = {
                "bm25Rank": idx + 1,
                "vectorRank": float("inf"),
                "sessionId": r["sessionId"],
                "bm25Score": r["score"],
                "vectorScore": 0.0,
                "graphScore": 0.0,
            }

        for idx, r in enumerate(vector_results):
            obs_id = r["obsId"]
            if obs_id in scores:
                scores[obs_id]["vectorRank"] = idx + 1
                scores[obs_id]["vectorScore"] = r["score"]
            else:
                scores[obs_id] = {
                    "bm25Rank": float("inf"),
                    "vectorRank": idx + 1,
                    "sessionId": r["sessionId"],
                    "bm25Score": 0.0,
                    "vectorScore": r["score"],
                    "graphScore": 0.0,
                }

        has_vector = len(vector_results) > 0

        effective_bm25_w = self.bm25_weight
        effective_vector_w = self.vector_weight if has_vector else 0.0

        total_w = effective_bm25_w + effective_vector_w
        if total_w > 0:
            effective_bm25_w /= total_w
            effective_vector_w /= total_w

        combined = []
        for obs_id, s in scores.items():
            combined.append(
                {
                    "obsId": obs_id,
                    "sessionId": s["sessionId"],
                    "bm25Score": s["bm25Score"],
                    "vectorScore": s["vectorScore"],
                    "graphScore": s["graphScore"],
                    "combinedScore": (
                        effective_bm25_w * (1.0 / (RRF_K + s["bm25Rank"]))
                        + effective_vector_w * (1.0 / (RRF_K + s["vectorRank"]))
                    ),
                }
            )

        combined.sort(key=lambda x: x["combinedScore"], reverse=True)
        return combined[:limit]


# =====================================================================
# OpenAI Embedding Client (D5.1)
# =====================================================================


class OpenAIEmbeddingProvider:
    """OpenAI text-embedding-3-small provider (1536 dims).

    Uses urllib.request only — no new dependencies.
    Reads API key from OPENAI_API_KEY env var.
    """

    def __init__(self, api_key: str):
        self.name = "openai"
        self.dimensions = 1536
        self.api_key = api_key
        self.model = "text-embedding-3-small"
        self.api_url = "https://api.openai.com/v1/embeddings"

    def embed(self, text: str) -> List[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        results: List[List[float]] = []
        # OpenAI supports up to 2048 inputs per request; batch in chunks of 100
        batch_limit = 100
        for i in range(0, len(texts), batch_limit):
            chunk = texts[i : i + batch_limit]
            payload = json.dumps({"model": self.model, "input": chunk}).encode("utf-8")
            req = urllib.request.Request(
                self.api_url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=30.0) as response:  # nosec B310
                    resp_data = json.loads(response.read().decode("utf-8"))
                # Sort by index to preserve order
                embeddings_sorted = sorted(
                    resp_data.get("data", []), key=lambda e: e["index"]
                )
                for emb in embeddings_sorted:
                    results.append(self._l2_normalize(emb["embedding"]))
            except Exception as e:
                raise RuntimeError(f"OpenAI embedding batch call failed: {e}")
        return results

    def _l2_normalize(self, vec: List[float]) -> List[float]:
        sum_sq = sum(x * x for x in vec)
        norm = math.sqrt(sum_sq)
        if norm == 0:
            return vec
        return [x / norm for x in vec]


# =====================================================================
# SentenceTransformer Local Provider (D5.2)
# =====================================================================


class SentenceTransformerProvider:
    """Local sentence-transformers provider (optional install).

    Default model: all-MiniLM-L6-v2 (384 dims).
    Override via AGENTMEMORY_LOCAL_EMBEDDING_MODEL env var.

    Install: pip install sentence-transformers
    """

    def __init__(self, model_name: Optional[str] = None):
        model_name = model_name or "all-MiniLM-L6-v2"
        self.name = "sentence-transformers"
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            self._model = SentenceTransformer(model_name)
            self.dimensions = self._model.get_sentence_embedding_dimension()
            print(
                f"[search] SentenceTransformerProvider loaded: {model_name} ({self.dimensions} dims)"
            )
        except ImportError:
            raise ImportError(
                "sentence-transformers is not installed. "
                "Run: pip install sentence-transformers  or  pip install agentmemory[local-embeddings]"
            )

    def embed(self, text: str) -> List[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        embeddings = self._model.encode(
            texts, show_progress_bar=False, normalize_embeddings=True
        )
        return [emb.tolist() for emb in embeddings]
