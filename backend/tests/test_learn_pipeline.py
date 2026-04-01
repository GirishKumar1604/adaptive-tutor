from tasks.learn_pipeline import (
    _binary_search_steps,
    _normalize_visual_plan,
    _parse_array_values,
    _parse_target_value,
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
