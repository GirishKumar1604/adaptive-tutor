from services import rag_service


def test_rag_query_with_sources(tmp_path):
    rag_service.RAG_DIR = str(tmp_path)

    info = rag_service.create_collection_from_texts(
        collection_id="c1",
        texts=[("note.txt", "Binary search works on sorted arrays.")],
    )
    assert info["num_chunks"] >= 1

    out = rag_service.query_collection_with_sources(collection_id="c1", question="sorted arrays", top_k=1, max_chars=500)
    assert "sorted" in out["context"].lower()
    assert out["sources"]
