import os
from typing import List, Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from pydantic import BaseModel, Field

from services.rag_service import create_collection_from_texts, query_collection, list_collections

router = APIRouter()


class RagQueryRequest(BaseModel):
    collection_id: str = Field(..., min_length=6)
    question: str = Field(..., min_length=2)
    top_k: int = 5
    max_chars: int = 1800


@router.get("/rag/collections")
def rag_collections():
    return {"collections": list_collections()}


@router.post("/rag/upload")
async def rag_upload(
    files: List[UploadFile] = File(...),
    collection_id: Optional[str] = Form(None),
):
    """
    Upload .txt / .md (MVP). Builds a collection index.
    Returns collection_id to pass into /learn/start.
    """
    cid = collection_id or os.urandom(16).hex()

    texts = []
    bad_files = []

    for f in files:
        name = f.filename or "unknown"
        ext = (name.rsplit(".", 1)[-1] if "." in name else "").lower()

        if ext not in ("txt", "md"):
            bad_files.append(name)
            continue

        raw = await f.read()
        text = raw.decode("utf-8", errors="ignore")

        texts.append((name, text))

    if bad_files and not texts:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported files (only .txt/.md supported in MVP): {bad_files}",
        )

    if not texts:
        raise HTTPException(status_code=400, detail="No valid text files provided")

    info = create_collection_from_texts(collection_id=cid, texts=texts)
    return {
        "collection_id": info["collection_id"],
        "num_chunks": info["num_chunks"],
        "uploaded_files": [t[0] for t in texts],
        "skipped_files": bad_files,
    }


@router.post("/rag/query")
def rag_query(req: RagQueryRequest):
    try:
        ctx = query_collection(
            collection_id=req.collection_id,
            question=req.question,
            top_k=req.top_k,
            max_chars=req.max_chars,
        )
        return {"collection_id": req.collection_id, "context": ctx}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Collection not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
