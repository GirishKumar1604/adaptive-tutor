import os
from typing import List, Optional

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from db import SessionLocal
from services.api_response import fail, ok
from services.persistence_service import replace_rag_chunks, save_rag_collection
from services.rag_service import create_collection_from_texts, list_collections, query_collection_with_sources

router = APIRouter()


class RagQueryRequest(BaseModel):
    collection_id: str = Field(..., min_length=6)
    question: str = Field(..., min_length=2)
    top_k: int = 5
    max_chars: int = 1800


@router.get("/rag/collections")
def rag_collections():
    return ok(result={"collections": list_collections()})


def _extract_pdf_text(raw: bytes) -> str:
    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise RuntimeError("PDF parsing requires pypdf dependency") from exc

    from io import BytesIO

    reader = PdfReader(BytesIO(raw))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n\n".join(pages).strip()


@router.post("/rag/upload")
async def rag_upload(
    files: List[UploadFile] = File(...),
    collection_id: Optional[str] = Form(None),
):
    cid = collection_id or os.urandom(16).hex()

    texts = []
    bad_files = []

    for f in files:
        name = f.filename or "unknown"
        ext = (name.rsplit(".", 1)[-1] if "." in name else "").lower()

        if ext not in ("txt", "md", "pdf"):
            bad_files.append(name)
            continue

        raw = await f.read()
        text = _extract_pdf_text(raw) if ext == "pdf" else raw.decode("utf-8", errors="ignore")
        texts.append((name, text))

    if bad_files and not texts:
        return fail(error=f"Unsupported files (only .txt/.md/.pdf supported): {bad_files}")

    if not texts:
        return fail(error="No valid text files provided")

    try:
        info = create_collection_from_texts(collection_id=cid, texts=texts)
        with SessionLocal() as db:
            _persist_rag_metadata(db, info["collection_id"], info.get("chunks") or [])
            db.commit()
    except Exception as exc:
        return fail(error=f"Failed to create collection: {type(exc).__name__}: {exc}")

    return ok(
        result={
            "collection_id": info["collection_id"],
            "num_chunks": info["num_chunks"],
            "uploaded_files": [t[0] for t in texts],
            "skipped_files": bad_files,
        }
    )


def _persist_rag_metadata(db: Session, collection_id: str, chunks: list[dict]) -> None:
    save_rag_collection(db, collection_id=collection_id, metadata_payload={"num_chunks": len(chunks)})
    replace_rag_chunks(db, collection_id=collection_id, chunks=chunks)


@router.post("/rag/query")
def rag_query(req: RagQueryRequest):
    try:
        data = query_collection_with_sources(
            collection_id=req.collection_id,
            question=req.question,
            top_k=req.top_k,
            max_chars=req.max_chars,
        )
        return ok(result={"collection_id": req.collection_id, **data})
    except FileNotFoundError:
        return fail(error="Collection not found")
    except Exception as e:
        return fail(error=f"{type(e).__name__}: {e}")
