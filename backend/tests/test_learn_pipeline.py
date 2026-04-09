from tasks.learn_pipeline import (
    _binary_search_steps,
    _extract_formula_expression,
    _normalize_visual_plan,
    _parse_array_values,
    _parse_target_value,
    _select_lesson_rag_context,
    _select_template,
)


def test_complexity_segment_prefers_graph_template():
    segment = {
        "title": "Why Binary Search Is Efficient",
        "narration": "Each comparison halves the search space, so the runtime is O(log n).",
        "on_screen": "Graph comparing O(n) and O(log n).",
        "visual_plan": {
            "template": "graph",
            "layout": "Axes with growth comparison",
            "elements": ["O(n)", "O(log n)"],
            "sequence": ["plot curves"],
            "emphasis": ["O(log n)"],
            "example": "n=1024 takes about 10 checks",
        },
    }
    visual_plan = _normalize_visual_plan(segment)
    assert _select_template(segment["title"], segment["narration"], segment["on_screen"], visual_plan) == "graph"


def test_pseudocode_segment_prefers_pseudocode_template():
    segment = {
        "title": "Binary Search Pseudocode",
        "narration": "Compute mid and adjust low or high until found.",
        "on_screen": "Pseudocode block with low mid high updates.",
        "visual_plan": {
            "template": "pseudocode",
            "layout": "Code panel",
            "elements": ["while loop", "mid update"],
            "sequence": ["compute mid", "compare"],
            "emphasis": ["low", "mid", "high"],
            "example": "mid = (low + high) // 2",
        },
    }
    visual_plan = _normalize_visual_plan(segment)
    assert _select_template(segment["title"], segment["narration"], segment["on_screen"], visual_plan) == "pseudocode"


def test_binary_walkthrough_uses_actual_example_data():
    narration = "Imagine a sorted array [2,4,7,10,13,16,19,22]. We look for 13."
    values = _parse_array_values(narration)
    target = _parse_target_value(narration, values=values)
    steps = _binary_search_steps(values, target)

    assert values == [2, 4, 7, 10, 13, 16, 19, 22]
    assert target == 13
    assert steps[0]["mid"] == 3
    assert steps[-1]["mid"] == 4


def test_extract_formula_expression_prefers_explicit_equation():
    expr = _extract_formula_expression(
        "Use the update rule: w_new = w - eta * dL/dw",
        "Gradient descent update",
    )
    assert expr == "w_new = w - eta * dL/dw"


def test_extract_formula_expression_normalizes_log_complexity():
    expr = _extract_formula_expression(
        "Time complexity is O(log n) for binary search.",
    )
    assert expr == r"O(\log n)"


def test_extract_formula_expression_photosynthesis_uses_reaction_equation():
    expr = _extract_formula_expression(
        "Rules for photosynthesis",
        "Overall equation: 6CO2 + 6H2O + light -> C6H12O6 + 6O2",
    )
    assert "C_6H_{12}O_6" in expr
    assert r"\rightarrow" in expr


def test_lesson_rag_skips_on_topic_hint_mismatch(monkeypatch):
    monkeypatch.setattr(
        "tasks.learn_pipeline.query_collection_with_sources",
        lambda **_: {
            "context": "Old topic notes",
            "sources": [{"source": "old.txt", "chunk_id": "1", "score": "0.9000"}],
            "top_score": 0.9,
            "num_hits": 2,
            "overlap_ratio": 0.5,
        },
    )
    monkeypatch.setattr(
        "tasks.learn_pipeline._read_rag_collection_metadata",
        lambda _cid: {"topic_hint": "cell biology", "force_topic_mismatch": False},
    )

    context, sources, reason, gate = _select_lesson_rag_context(
        collection_id="c1",
        topic="binary search",
        goal="learn quickly",
    )

    assert context == ""
    assert sources == []
    assert reason == "skipped: topic_hint mismatch"
    assert gate["topic_hint_overlap"] == 0.0


def test_lesson_rag_skips_when_relevance_is_low(monkeypatch):
    monkeypatch.setattr(
        "tasks.learn_pipeline.query_collection_with_sources",
        lambda **_: {
            "context": "Very weak match",
            "sources": [{"source": "notes.txt", "chunk_id": "1", "score": "0.0100"}],
            "top_score": 0.01,
            "num_hits": 1,
            "overlap_ratio": 0.01,
        },
    )
    monkeypatch.setattr(
        "tasks.learn_pipeline._read_rag_collection_metadata",
        lambda _cid: {"topic_hint": "binary search", "force_topic_mismatch": False},
    )

    context, sources, reason, _ = _select_lesson_rag_context(
        collection_id="c1",
        topic="binary search",
        goal="learn quickly",
    )

    assert context == ""
    assert sources == []
    assert reason == "skipped: low relevance"


def test_generate_lesson_video_without_rag(monkeypatch, tmp_path):
    import tasks.learn_pipeline as lp
    from contextlib import contextmanager

    @contextmanager
    def fake_session_scope():
        yield object()

    audio_path = tmp_path / "seg_01.mp3"
    audio_path.write_text("audio")

    monkeypatch.setattr(lp, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(
        lp,
        "_generate_segments",
        lambda **_: [
            {
                "id": 1,
                "title": "Intro",
                "narration": "Binary search intro",
                "on_screen": "Binary search concept",
                "duration_sec": 5,
                "visual_plan": {
                    "template": "concept",
                    "layout": "Concept map",
                    "elements": ["core idea", "support"],
                    "sequence": ["show core idea"],
                    "emphasis": ["intuition"],
                    "example": "Divide and conquer",
                },
            }
        ],
    )
    monkeypatch.setattr(
        lp,
        "build_segment_audio_assets",
        lambda **_: [
            {
                "segment_index": 1,
                "title": "Intro",
                "on_screen": "Binary search concept",
                "audio_duration_sec": 1.0,
                "audio_path": str(audio_path),
            }
        ],
    )
    monkeypatch.setattr(lp, "_write_segment_scene_file", lambda **_: (str(tmp_path / "scene.py"), "concept"))
    monkeypatch.setattr(lp, "_render_segment", lambda **_: str(tmp_path / "raw.mp4"))
    monkeypatch.setattr(lp, "ensure_video_duration", lambda *args, **kwargs: None)
    monkeypatch.setattr(lp, "merge_audio_with_video", lambda *args, **kwargs: None)
    monkeypatch.setattr(lp, "concat_media_files", lambda *args, **kwargs: None)
    monkeypatch.setattr(lp, "upsert_lesson_job_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(lp, "session_scope", fake_session_scope)

    payload = lp.generate_lesson_video.run(job_id="job-no-rag", topic="Binary Search", rag_collection_id=None)

    assert payload["status"] == "COMPLETED"
    assert payload["used_rag_for_lesson"] is False
    assert payload["rag_collection_id"] is None
