from __future__ import annotations

import os
import re
import json
import math
from collections import Counter, defaultdict
from typing import List, Dict, Optional, Tuple

RAG_DIR = os.getenv("RAG_DIR", "/app/app/storage/rag")


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _tokenize(text: str) -> List[str]:
    text = (text or "").lower()
    # keep words + numbers
    return re.findall(r"[a-z0-9]+", text)


def _chunk_text(text: str, *, max_chars: int = 900, overlap: int = 120) -> List[str]:
    """
    Simple chunker: split by paragraphs, then pack to max_chars with overlap.
    """
    text = (text or "").strip()
    if not text:
        return []

    paras = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    chunks: List[str] = []
    buf = ""

    for p in paras:
        if not buf:
            buf = p
        elif len(buf) + 2 + len(p) <= max_chars:
            buf = buf + "\n\n" + p
        else:
            chunks.append(buf)
            # overlap tail
            tail = buf[-overlap:] if overlap > 0 else ""
            buf = (tail + "\n\n" + p).strip()

            # if still too big, hard-split
            while len(buf) > max_chars:
                chunks.append(buf[:max_chars])
                buf = buf[max_chars - overlap :] if overlap > 0 else buf[max_chars:]

    if buf.strip():
        chunks.append(buf.strip())

    # cleanup
    return [c.strip() for c in chunks if c.strip()]


def _collection_path(collection_id: str) -> str:
    return os.path.join(RAG_DIR, collection_id)


def _chunks_path(collection_id: str) -> str:
    return os.path.join(_collection_path(collection_id), "chunks.json")


def _index_path(collection_id: str) -> str:
    return os.path.join(_collection_path(collection_id), "index.json")


def create_collection_from_texts(
    *,
    collection_id: str,
    texts: List[Tuple[str, str]],  # (source_name, text)
    max_chars: int = 900,
    overlap: int = 120,
) -> Dict:
    """
    Creates a collection:
      - chunks.json: chunk texts + metadata
      - index.json: sparse tf-idf-ish index for retrieval
    """
    cdir = _collection_path(collection_id)
    _ensure_dir(cdir)

    chunks: List[Dict] = []
    chunk_id = 1

    for source_name, text in texts:
        for ch in _chunk_text(text, max_chars=max_chars, overlap=overlap):
            chunks.append(
                {
                    "id": chunk_id,
                    "source": source_name,
                    "text": ch,
                }
            )
            chunk_id += 1

    if not chunks:
        raise ValueError("No text content found to index (all files empty?).")

    # Build DF + IDF
    N = len(chunks)
    df: Dict[str, int] = defaultdict(int)

    chunk_tfs: List[Dict] = []
    for c in chunks:
        tokens = _tokenize(c["text"])
        tf = Counter(tokens)
        chunk_tfs.append(tf)
        for term in tf.keys():
            df[term] += 1

    # idf smoothing
    idf: Dict[str, float] = {}
    for term, d in df.items():
        idf[term] = math.log((N + 1) / (d + 1)) + 1.0

    # store per-chunk sparse weights + norm
    stored_chunks = []
    for c, tf in zip(chunks, chunk_tfs):
        weights = {}
        norm_sq = 0.0
        for term, freq in tf.items():
            w = float(freq) * idf.get(term, 0.0)
            if w != 0.0:
                weights[term] = freq  # store tf only, recompute w at query time
                norm_sq += (w * w)

        stored_chunks.append(
            {
                "id": c["id"],
                "source": c["source"],
                "tf": weights,
                "norm": math.sqrt(norm_sq) if norm_sq > 0 else 1.0,
            }
        )

    # Save chunks + index
    with open(_chunks_path(collection_id), "w", encoding="utf-8") as f:
        json.dump({"collection_id": collection_id, "chunks": chunks}, f, ensure_ascii=False, indent=2)

    with open(_index_path(collection_id), "w", encoding="utf-8") as f:
        json.dump(
            {
                "collection_id": collection_id,
                "N": N,
                "idf": idf,
                "items": stored_chunks,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    return {
        "collection_id": collection_id,
        "num_chunks": N,
    }


def list_collections() -> List[str]:
    if not os.path.exists(RAG_DIR):
        return []
    return sorted([d for d in os.listdir(RAG_DIR) if os.path.isdir(os.path.join(RAG_DIR, d))])


def query_collection(
    *,
    collection_id: str,
    question: str,
    top_k: int = 5,
    max_chars: int = 1800,
) -> str:
    """
    Returns a single context string made from top_k chunks.
    """
    ipath = _index_path(collection_id)
    cpath = _chunks_path(collection_id)

    if not os.path.exists(ipath) or not os.path.exists(cpath):
        raise FileNotFoundError(f"Collection not found: {collection_id}")

    with open(ipath, "r", encoding="utf-8") as f:
        idx = json.load(f)

    with open(cpath, "r", encoding="utf-8") as f:
        chunks_doc = json.load(f)

    idf: Dict[str, float] = idx.get("idf") or {}
    items = idx.get("items") or []
    chunks = chunks_doc.get("chunks") or []

    chunks_by_id = {c["id"]: c for c in chunks}

    q_tokens = _tokenize(question)
    q_tf = Counter(q_tokens)

    # query weights
    q_norm_sq = 0.0
    q_w = {}
    for term, freq in q_tf.items():
        w = float(freq) * float(idf.get(term, 0.0))
        if w != 0.0:
            q_w[term] = w
            q_norm_sq += (w * w)

    q_norm = math.sqrt(q_norm_sq) if q_norm_sq > 0 else 1.0

    scored = []
    for it in items:
        tf = it.get("tf") or {}
        c_norm = float(it.get("norm") or 1.0)

        dot = 0.0
        for term, qw in q_w.items():
            if term in tf:
                dot += qw * (float(tf[term]) * float(idf.get(term, 0.0)))

        score = dot / (q_norm * c_norm) if (q_norm > 0 and c_norm > 0) else 0.0
        if score > 0:
            scored.append((score, int(it["id"]), it.get("source") or ""))

    scored.sort(reverse=True, key=lambda x: x[0])
    picked = scored[: max(1, top_k)]

    # build context string
    out_parts: List[str] = []
    used = 0
    for score, cid, source in picked:
        c = chunks_by_id.get(cid)
        if not c:
            continue
        block = f"[Source: {source} | Chunk: {cid}]\n{c['text']}".strip()
        if used + len(block) > max_chars:
            remaining = max_chars - used
            if remaining > 200:
                out_parts.append(block[:remaining])
            break
        out_parts.append(block)
        used += len(block) + 2

    return "\n\n---\n\n".join(out_parts).strip()
