from __future__ import annotations

import glob
import json
import os
import re
import subprocess
from datetime import datetime
from typing import Any, Dict, Optional

from celery_app import celery
from db import session_scope
from models import JobStatus
from services.groq_client import chat_json
from services.persistence_service import upsert_lesson_job_result
from services.rag_service import query_collection_with_sources
from services.tts_service import (
    build_segment_audio_assets,
    concat_media_files,
    ensure_video_duration,
    merge_audio_with_video,
)

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/app/app/storage/videos")
GENERATED_DIR = "/app/app/manim_scenes/generated"
VALID_TEMPLATES = {"array", "comparison", "graph", "formula", "flow", "concept", "pseudocode", "checklist", "timeline"}
PLACEHOLDER_VISUAL_PATTERNS = [
    re.compile(r"\bgraph comparing\b", re.IGNORECASE),
    re.compile(r"\billustration of\b", re.IGNORECASE),
    re.compile(r"\bvisualization of\b", re.IGNORECASE),
    re.compile(r"\banimation of\b", re.IGNORECASE),
    re.compile(r"\bdiagram of\b", re.IGNORECASE),
    re.compile(r"\bshow\b.*\bvisually\b", re.IGNORECASE),
]
SCENE_FORBIDDEN_PATTERNS = [
    re.compile(r"\bmanimlib\b"),
    re.compile(r"\bmanimgl\b"),
    re.compile(r"\bInteractiveScene\b"),
    re.compile(r"\bSceneFileWriter\b"),
]
TEMPLATE_LIBRARY: dict[str, dict[str, Any]] = {
    "array": {
        "layout": "Array row with low mid high pointers",
        "elements": ["sorted array", "low pointer", "mid pointer", "high pointer"],
        "sequence": ["show search bounds", "compare midpoint", "shrink interval", "highlight match"],
        "emphasis": ["sorted", "low", "mid"],
        "example": "Find a target inside a sorted array",
    },
    "comparison": {
        "layout": "Side-by-side comparison",
        "elements": ["left panel", "right panel", "highlighted difference"],
        "sequence": ["introduce both sides", "animate the contrast", "highlight the faster path"],
        "emphasis": ["comparison", "difference"],
        "example": "Compare two strategies on the same input",
    },
    "graph": {
        "layout": "Axes with plotted curves and short callouts",
        "elements": ["axes", "primary curve", "secondary curve", "compact comparison table"],
        "sequence": ["draw axes", "plot values", "label the curves", "show takeaway"],
        "emphasis": ["trend", "fewer steps"],
        "example": "Compare growth rates across input sizes",
    },
    "formula": {
        "layout": "Formula card with supporting explanation",
        "elements": ["formula frame", "MathTex expression", "supporting note"],
        "sequence": ["write the expression", "highlight a term", "state the meaning"],
        "emphasis": ["formula"],
        "example": "State the core expression clearly",
    },
    "flow": {
        "layout": "Three-step process flow",
        "elements": ["input card", "process card", "output card"],
        "sequence": ["show first step", "show transition", "show result"],
        "emphasis": ["process"],
        "example": "Show how an input turns into an output",
    },
    "timeline": {
        "layout": "Sequential timeline with connected steps",
        "elements": ["step card 1", "step card 2", "step card 3"],
        "sequence": ["first", "next", "then", "finally"],
        "emphasis": ["sequence"],
        "example": "Walk through ordered steps",
    },
    "concept": {
        "layout": "Concept hub with linked support cards",
        "elements": ["core idea", "supporting reasons", "connections"],
        "sequence": ["show core idea", "link support", "restate insight"],
        "emphasis": ["intuition"],
        "example": "Explain the big picture first",
    },
    "pseudocode": {
        "layout": "Code panel with state tracker",
        "elements": ["code block", "highlight box", "state tracker"],
        "sequence": ["write code", "highlight active line", "update state"],
        "emphasis": ["state", "update"],
        "example": "Show the algorithm loop and updates",
    },
    "checklist": {
        "layout": "Checklist cards with short recap",
        "elements": ["check item 1", "check item 2", "check item 3"],
        "sequence": ["show items", "mark the key safeguard", "recap"],
        "emphasis": ["edge case", "recap"],
        "example": "Summarize caveats and safeguards",
    },
}
TEMPLATE_REQUIRED_TOKENS = {
    "array": ["Square(", "Arrow(", "SurroundingRectangle("],
    "comparison": ["Square(", "Arrow(", "SurroundingRectangle("],
    "graph": ["Axes(", "Line(", "VMobject("],
    "formula": ["MathTex(", "RoundedRectangle("],
    "flow": ["RoundedRectangle(", "Arrow("],
    "timeline": ["RoundedRectangle(", "Arrow("],
    "concept": ["Circle(", "RoundedRectangle(", "Line("],
    "pseudocode": ["RoundedRectangle(", "while low <= high:"],
    "checklist": ["RoundedRectangle("],
}

SEGMENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "minItems": 6,
            "maxItems": 10,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "minimum": 1},
                    "title": {"type": "string"},
                    "narration": {"type": "string"},
                    "on_screen": {"type": "string"},
                    "duration_sec": {"type": "number", "minimum": 2, "maximum": 16},
                    "visual_plan": {
                        "type": "object",
                        "properties": {
                            "template": {"type": "string", "enum": sorted(VALID_TEMPLATES)},
                            "layout": {"type": "string"},
                            "elements": {
                                "type": "array",
                                "minItems": 2,
                                "maxItems": 6,
                                "items": {"type": "string"},
                            },
                            "sequence": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 5,
                                "items": {"type": "string"},
                            },
                            "emphasis": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 4,
                                "items": {"type": "string"},
                            },
                            "example": {"type": "string"},
                        },
                        "required": ["template", "layout", "elements", "sequence", "emphasis", "example"],
                        "additionalProperties": False,
                    },
                },
                "required": ["id", "title", "narration", "on_screen", "duration_sec", "visual_plan"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["segments"],
    "additionalProperties": False,
}


def _py_str(s: str) -> str:
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "\\n")
    return f"\"{s}\""


def _split_on_screen_points(raw: str) -> list[str]:
    text = (raw or "").replace("\n", ";")
    parts: list[str] = []
    for chunk in text.split(";"):
        for sub in chunk.split(","):
            s = " ".join(sub.strip().split())
            if 2 <= len(s) <= 90:
                parts.append(s)
    if not parts:
        return []
    uniq: list[str] = []
    for p in parts:
        if p not in uniq:
            uniq.append(p)
    return uniq[:4]


def _clip_lines(items: list[str], limit: int) -> list[str]:
    clipped: list[str] = []
    for raw in items:
        s = " ".join((raw or "").split())
        if 2 <= len(s) <= 88 and s not in clipped:
            clipped.append(s)
        if len(clipped) >= limit:
            break
    return clipped


def _looks_like_placeholder_visual_text(text: str) -> bool:
    candidate = " ".join((text or "").split())
    if not candidate:
        return True
    return any(pattern.search(candidate) for pattern in PLACEHOLDER_VISUAL_PATTERNS)


def _template_defaults(template: str) -> dict[str, Any]:
    return TEMPLATE_LIBRARY.get(template, TEMPLATE_LIBRARY["concept"])


def _sanitize_visual_list(items: list[str], *, template: str, limit: int, fallback_key: str) -> list[str]:
    sanitized = [item for item in _clip_lines(items, limit) if not _looks_like_placeholder_visual_text(item)]
    if sanitized:
        return sanitized
    return list(_template_defaults(template)[fallback_key])[:limit]


def _fallback_visual_plan(seg_title: str, seg_narration: str, seg_on_screen: str) -> dict[str, Any]:
    lower = f"{seg_title} {seg_narration} {seg_on_screen}".lower()
    elements = _split_on_screen_points(seg_on_screen)

    if any(k in lower for k in ["pseudocode", "code", "iterative", "recursive"]):
        return {
            "template": "pseudocode",
            "layout": "Code panel with variable tracker",
            "elements": _clip_lines(elements or ["while loop", "mid update", "low high tracker"], 4),
            "sequence": ["compute mid", "compare target", "move low or high", "repeat"],
            "emphasis": ["low", "mid", "high"],
            "example": seg_on_screen or seg_narration,
        }
    if any(k in lower for k in ["edge case", "recap", "summary", "checklist"]):
        return {
            "template": "checklist",
            "layout": "Checklist cards with recap",
            "elements": _clip_lines(elements or ["empty array", "duplicates", "safe midpoint"], 4),
            "sequence": ["show edge cases", "show safeguards", "recap"],
            "emphasis": ["edge cases", "recap"],
            "example": seg_on_screen or seg_narration,
        }
    if any(k in lower for k in ["linear search vs", "compare", "side-by-side", "versus"]):
        return {
            "template": "comparison",
            "layout": "Two-column search comparison",
            "elements": _clip_lines(elements or ["linear scan", "binary split", "fewer checks"], 4),
            "sequence": ["scan items", "jump to middle", "discard half"],
            "emphasis": ["linear", "binary"],
            "example": seg_on_screen or seg_narration,
        }
    if any(k in lower for k in ["graph", "complexity", "o(", "log", "growth", "efficient"]):
        return {
            "template": "graph",
            "layout": "Axes with growth comparison",
            "elements": _clip_lines(elements or ["O(n) line", "O(log n) curve", "check-count table"], 4),
            "sequence": ["plot linear", "plot logarithmic", "compare slope"],
            "emphasis": ["O(n)", "O(log n)"],
            "example": seg_on_screen or seg_narration,
        }
    if any(k in lower for k in ["array", "binary", "pointer", "mid", "search", "index", "sorted"]):
        return {
            "template": "array",
            "layout": "Array with low mid high pointers",
            "elements": _clip_lines(elements or ["sorted array", "low pointer", "mid pointer", "high pointer"], 4),
            "sequence": ["choose middle", "compare to target", "shrink interval", "repeat"],
            "emphasis": ["sorted", "low", "mid", "high"],
            "example": seg_on_screen or seg_narration,
        }
    if any(k in lower for k in ["equation", "formula", "quadratic", "algebra", "derive", "theorem"]):
        return {
            "template": "formula",
            "layout": "Formula card",
            "elements": _clip_lines(elements or ["formula", "labelled terms"], 4),
            "sequence": ["show formula", "label terms", "explain meaning"],
            "emphasis": ["formula"],
            "example": seg_on_screen or seg_narration,
        }
    if any(k in lower for k in ["step", "process", "pipeline", "flow", "loop", "sequence"]):
        return {
            "template": "timeline" if any(k in lower for k in ["step", "sequence"]) else "flow",
            "layout": "Sequential steps" if any(k in lower for k in ["step", "sequence"]) else "Step flow",
            "elements": _clip_lines(elements or ["step one", "step two", "step three"], 4),
            "sequence": ["first", "next", "then", "finally"] if any(k in lower for k in ["step", "sequence"]) else ["input", "process", "output"],
            "emphasis": ["sequence"],
            "example": seg_on_screen or seg_narration,
        }
    return {
        "template": "concept",
        "layout": "Concept map",
        "elements": _clip_lines(elements or ["core idea", "why", "how", "when"], 4),
        "sequence": ["show core idea", "connect supporting points"],
        "emphasis": ["intuition"],
        "example": seg_on_screen or seg_narration,
    }


def _normalize_visual_plan(segment: dict[str, Any]) -> dict[str, Any]:
    fallback = _fallback_visual_plan(segment.get("title", ""), segment.get("narration", ""), segment.get("on_screen", ""))
    raw = segment.get("visual_plan") or {}
    template = raw.get("template") if raw.get("template") in VALID_TEMPLATES else fallback["template"]
    defaults = _template_defaults(template)
    return {
        "template": template,
        "layout": " ".join((raw.get("layout") or fallback["layout"] or defaults["layout"]).split())[:120],
        "elements": _sanitize_visual_list(list(raw.get("elements") or fallback["elements"] or defaults["elements"]), template=template, limit=4, fallback_key="elements"),
        "sequence": _sanitize_visual_list(list(raw.get("sequence") or fallback["sequence"] or defaults["sequence"]), template=template, limit=4, fallback_key="sequence"),
        "emphasis": _sanitize_visual_list(list(raw.get("emphasis") or fallback["emphasis"] or defaults["emphasis"]), template=template, limit=3, fallback_key="emphasis"),
        "example": " ".join((raw.get("example") or fallback["example"] or defaults["example"]).split())[:180],
    }


def _select_template(seg_title: str, seg_narration: str, seg_on_screen: str, visual_plan: Optional[dict[str, Any]] = None) -> str:
    planned = (visual_plan or {}).get("template")
    if planned in VALID_TEMPLATES:
        return planned

    lower = f"{seg_title} {seg_narration} {seg_on_screen}".lower()
    if any(k in lower for k in ["pseudocode", "code", "iterative", "recursive"]):
        return "pseudocode"
    if any(k in lower for k in ["edge case", "recap", "summary", "checklist"]):
        return "checklist"
    if any(k in lower for k in ["linear search vs", "compare", "side-by-side", "versus"]):
        return "comparison"
    if any(k in lower for k in ["graph", "complexity", "o(", "log", "curve", "growth", "efficient"]):
        return "graph"
    if any(k in lower for k in ["equation", "formula", "quadratic", "algebra", "derive", "theorem"]):
        return "formula"
    if any(k in lower for k in ["array", "binary", "pointer", "mid", "search", "index", "sorted"]):
        return "array"
    if any(k in lower for k in ["timeline", "phase", "first", "next", "then", "finally"]):
        return "timeline"
    if any(k in lower for k in ["step", "process", "pipeline", "flow", "loop", "sequence"]):
        return "flow"
    return "concept"


def _template_bullets(seg_title: str, visual_plan: dict[str, Any]) -> list[str]:
    title = " ".join((seg_title or "").split())
    if len(title) > 56:
        title = title[:53] + "..."
    bullet_pool = [title or "Key idea"] + list(visual_plan.get("emphasis") or []) + list(visual_plan.get("elements") or [])
    return _clip_lines(bullet_pool, 3)


def _parse_array_values(*texts: str) -> list[int]:
    for text in texts:
        match = re.search(r"\[([0-9,\s-]+)\]", text or "")
        if match:
            nums = [int(part.strip()) for part in match.group(1).split(",") if part.strip()]
            if len(nums) >= 4:
                return nums
    return [2, 4, 7, 10, 13, 16, 19, 22]


def _parse_target_value(*texts: str, values: list[int]) -> int:
    joined = " ".join(texts)
    for pattern in [r"look for\s+(-?\d+)", r"find\s+(-?\d+)", r"target\s*(?:=|is)?\s*(-?\d+)"]:
        match = re.search(pattern, joined, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return values[min(len(values) // 2, len(values) - 1)]


def _binary_search_steps(values: list[int], target: int) -> list[dict[str, Any]]:
    low = 0
    high = len(values) - 1
    steps: list[dict[str, Any]] = []
    while low <= high and len(steps) < 4:
        mid = (low + high) // 2
        if values[mid] == target:
            steps.append({"low": low, "mid": mid, "high": high, "label": f"{values[mid]} = {target}  found"})
            break
        if values[mid] < target:
            label = f"{values[mid]} < {target}  search right"
            next_low, next_high = mid + 1, high
        else:
            label = f"{values[mid]} > {target}  search left"
            next_low, next_high = low, mid - 1
        steps.append({"low": low, "mid": mid, "high": high, "label": label})
        low, high = next_low, next_high
    if not steps:
        steps.append({"low": 0, "mid": 0, "high": 0, "label": f"target {target}"})
    return steps


def _validate_scene_code(*, scene_code: str, template: str, visual_plan: dict[str, Any]) -> None:
    if "from manim import *" not in scene_code:
        raise RuntimeError("Scene validator: expected Manim Community import.")
    for pattern in SCENE_FORBIDDEN_PATTERNS:
        if pattern.search(scene_code):
            raise RuntimeError(f"Scene validator: forbidden CE-incompatible pattern matched: {pattern.pattern}")
    if ".to_edge(UP" not in scene_code or ".to_corner(UL" not in scene_code:
        raise RuntimeError("Scene validator: missing stable title or sidebar layout anchors.")

    rendered_text = " ".join(re.findall(r"Text\((?:f)?['\"]([^'\"]+)['\"]", scene_code))
    if _looks_like_placeholder_visual_text(rendered_text):
        raise RuntimeError("Scene validator: placeholder visual description leaked into rendered text.")

    required_tokens = TEMPLATE_REQUIRED_TOKENS.get(template, [])
    missing_tokens = [token for token in required_tokens if token not in scene_code]
    if missing_tokens:
        raise RuntimeError(f"Scene validator: template '{template}' is missing required visual tokens: {missing_tokens}")

    visual_token_count = sum(scene_code.count(token) for token in [
        "Circle(",
        "Square(",
        "Rectangle(",
        "RoundedRectangle(",
        "Arrow(",
        "Line(",
        "Axes(",
        "MathTex(",
        "VMobject(",
        "SurroundingRectangle(",
    ])
    if template not in {"formula", "pseudocode"} and visual_token_count < 3:
        raise RuntimeError(f"Scene validator: template '{template}' is too text-heavy.")


def _write_segment_scene_file(
    *,
    job_id: str,
    segment_idx: int,
    scene_class: str,
    topic: str,
    seg_title: str,
    seg_narration: str,
    seg_on_screen: str,
    visual_plan: dict[str, Any],
    target_duration: float,
) -> tuple[str, str]:
    os.makedirs(GENERATED_DIR, exist_ok=True)
    scene_file = os.path.join(GENERATED_DIR, f"lesson_{job_id.replace('-', '_')}_seg_{segment_idx:02d}.py")
    t = max(2.8, float(target_duration))

    header_rt = min(1.1, max(0.6, t * 0.11))
    bullets_rt = min(1.2, max(0.7, t * 0.13))
    visual_rt = min(2.6, max(1.3, t * 0.22))
    outro_rt = min(1.0, max(0.55, t * 0.10))
    hold = max(0.18, t - (header_rt + bullets_rt + visual_rt + outro_rt))

    template = _select_template(
        seg_title=seg_title,
        seg_narration=seg_narration,
        seg_on_screen=seg_on_screen,
        visual_plan=visual_plan,
    )
    bullet_points = _template_bullets(seg_title=seg_title, visual_plan=visual_plan)

    lines: list[str] = []
    lines.append("from manim import *")
    lines.append("")
    lines.append(f"class {scene_class}(Scene):")
    lines.append("    def construct(self):")
    lines.append("        self.camera.background_color = '#f4f7fb'")
    lines.append("        Text.set_default(color='#102240')")
    lines.append("        MathTex.set_default(color='#102240')")
    lines.append("        sidebar = RoundedRectangle(width=4.15, height=5.4, corner_radius=0.16, color=BLUE_D, stroke_opacity=0.32)")
    lines.append("        sidebar.set_fill('#ffffff', opacity=0.92)")
    lines.append("        sidebar.to_corner(UL, buff=0.36).shift(DOWN * 0.48)")
    lines.append(f"        heading = Text({_py_str(topic)}, font_size=22, color='#3564c8')")
    lines.append("        if heading.width > 11.6:")
    lines.append("            heading.scale_to_fit_width(11.6)")
    lines.append("        heading.to_edge(UP, buff=0.28)")
    lines.append(f"        seg = Text({_py_str(seg_title)}, font_size=24, color='#0f264f')")
    lines.append("        if seg.width > 10.8:")
    lines.append("            seg.scale_to_fit_width(10.8)")
    lines.append("        seg.next_to(heading, DOWN, buff=0.24)")
    lines.append(f"        bullet_lines = {repr(bullet_points)}")
    lines.append("        bullets = VGroup(*[")
    lines.append("            Text(f'- {b}', font_size=16, color='#586782') for b in bullet_lines")
    lines.append("        ])")
    lines.append("        for b in bullets:")
    lines.append("            if b.width > 3.9:")
    lines.append("                b.scale_to_fit_width(3.9)")
    lines.append("        bullets.arrange(DOWN, aligned_edge=LEFT, buff=0.14)")
    lines.append("        bullets.next_to(sidebar.get_top(), DOWN, buff=0.72).align_to(sidebar, LEFT).shift(RIGHT * 0.28)")
    lines.append(f"        self.play(FadeIn(sidebar, shift=RIGHT * 0.08), FadeIn(heading, shift=DOWN * 0.1), Write(seg), run_time={header_rt:.3f})")
    lines.append(f"        self.play(LaggedStart(*[Write(b) for b in bullets], lag_ratio=0.16), run_time={bullets_rt:.3f})")

    if template == "comparison":
        lines.append("        values = [2, 4, 7, 10, 13, 16, 19, 22]")
        lines.append("        left_title = Text('Linear search', font_size=22, color=RED_B).move_to(LEFT * 3.15 + DOWN * 0.15)")
        lines.append("        right_title = Text('Binary search', font_size=22, color=GREEN_B).move_to(RIGHT * 3.05 + DOWN * 0.15)")
        lines.append("        left_boxes = VGroup(*[Square(side_length=0.56, color=RED_D).set_fill('#ffe6e4', opacity=0.85) for _ in values]).arrange(RIGHT, buff=0.05).move_to(LEFT * 3.0 + DOWN * 1.0)")
        lines.append("        right_boxes = VGroup(*[Square(side_length=0.56, color=BLUE_D).set_fill('#e3efff', opacity=0.9) for _ in values]).arrange(RIGHT, buff=0.05).move_to(RIGHT * 3.0 + DOWN * 1.0)")
        lines.append("        left_labels = VGroup(*[Text(str(v), font_size=16).move_to(box) for box, v in zip(left_boxes, values)])")
        lines.append("        right_labels = VGroup(*[Text(str(v), font_size=16).move_to(box) for box, v in zip(right_boxes, values)])")
        lines.append("        scan_arrow = Arrow(left_boxes[0].get_top() + UP * 0.42, left_boxes[0].get_top() + UP * 0.02, buff=0.03, color=RED_B)")
        lines.append("        range_rect = SurroundingRectangle(VGroup(*[right_boxes[i] for i in range(8)]), buff=0.05, color=YELLOW_B)")
        lines.append("        mid_arrow = Arrow(right_boxes[3].get_top() + UP * 0.42, right_boxes[3].get_top() + UP * 0.02, buff=0.03, color=YELLOW_B)")
        lines.append("        note = Text('Binary search eliminates half each comparison', font_size=18, color='#586782').next_to(right_boxes, DOWN, buff=0.28)")
        lines.append(f"        self.play(Write(left_title), Write(right_title), Create(left_boxes), Create(right_boxes), FadeIn(left_labels), FadeIn(right_labels), run_time={visual_rt*0.35:.3f})")
        lines.append(f"        self.play(GrowArrow(scan_arrow), Create(range_rect), GrowArrow(mid_arrow), run_time={visual_rt*0.22:.3f})")
        lines.append(f"        self.play(scan_arrow.animate.put_start_and_end_on(left_boxes[4].get_top() + UP * 0.42, left_boxes[4].get_top() + UP * 0.02), Transform(range_rect, SurroundingRectangle(VGroup(*[right_boxes[i] for i in range(4, 8)]), buff=0.05, color=YELLOW_B)), Transform(mid_arrow, Arrow(right_boxes[5].get_top() + UP * 0.42, right_boxes[5].get_top() + UP * 0.02, buff=0.03, color=YELLOW_B)), FadeIn(note), run_time={visual_rt*0.28:.3f})")
        lines.append("        binary_hit = right_boxes[4].copy().set_fill(GREEN_E, opacity=0.32)")
        lines.append(f"        self.play(FadeIn(binary_hit), run_time={visual_rt*0.15:.3f})")
        lines.append("        visual_group = VGroup(left_title, right_title, left_boxes, right_boxes, left_labels, right_labels, scan_arrow, range_rect, mid_arrow, note, binary_hit)")
    elif template == "array":
        values = _parse_array_values(seg_title, seg_narration, seg_on_screen, visual_plan.get("example", ""))
        target = _parse_target_value(seg_title, seg_narration, seg_on_screen, visual_plan.get("example", ""), values=values)
        steps = _binary_search_steps(values, target)
        lines.append(f"        values = {values!r}")
        lines.append(f"        steps = {steps!r}")
        lines.append("        boxes = VGroup(*[Square(side_length=0.72, color=BLUE_D).set_fill('#e7f0ff', opacity=0.92) for _ in values]).arrange(RIGHT, buff=0.06)")
        lines.append("        labels = VGroup(*[Text(str(v), font_size=18) for v in values])")
        lines.append("        for box, label in zip(boxes, labels):")
        lines.append("            label.move_to(box.get_center())")
        lines.append("        arr = VGroup(boxes, labels).next_to(seg, DOWN, buff=0.7).shift(RIGHT * 1.4)")
        lines.append("        def span_rect(lo, hi):")
        lines.append("            return SurroundingRectangle(VGroup(*[boxes[i] for i in range(lo, hi + 1)]), buff=0.06, color=YELLOW_B)")
        lines.append("        def pointer(idx, drift, color):")
        lines.append("            return Arrow(start=arr.get_top() + UP * 0.62 + drift, end=boxes[idx].get_top() + UP * 0.05, buff=0.06, color=color)")
        lines.append(f"        target_badge = RoundedRectangle(width=1.55, height=0.56, corner_radius=0.12, color=GREEN_B).next_to(arr, DOWN, buff=0.32).shift(LEFT * 2.75)")
        lines.append(f"        target_text = Text('target {target}', font_size=18, color=GREEN_B).move_to(target_badge)")
        lines.append("        target_card = VGroup(target_badge, target_text)")
        lines.append("        first = steps[0]")
        lines.append("        range_rect = span_rect(first['low'], first['high'])")
        lines.append("        low_arrow = pointer(first['low'], LEFT * 2.15, BLUE_B)")
        lines.append("        mid_arrow = pointer(first['mid'], ORIGIN, YELLOW)")
        lines.append("        high_arrow = pointer(first['high'], RIGHT * 2.15, GREEN_B)")
        lines.append("        tags = VGroup(")
        lines.append("            Text('low', font_size=16, color=BLUE_B).next_to(low_arrow, UP, buff=0.03),")
        lines.append("            Text('mid', font_size=16, color=YELLOW).next_to(mid_arrow, UP, buff=0.03),")
        lines.append("            Text('high', font_size=16, color=GREEN_B).next_to(high_arrow, UP, buff=0.03),")
        lines.append("        )")
        lines.append("        comparison = Text(first['label'], font_size=22, color=YELLOW_B).next_to(arr, DOWN, buff=0.32).shift(RIGHT * 1.6)")
        lines.append(f"        self.play(Create(boxes), FadeIn(labels), DrawBorderThenFill(target_badge), FadeIn(target_text), run_time={visual_rt*0.28:.3f})")
        lines.append(f"        self.play(Create(range_rect), GrowArrow(low_arrow), GrowArrow(mid_arrow), GrowArrow(high_arrow), FadeIn(tags), FadeIn(comparison), run_time={visual_rt*0.22:.3f})")
        for step in steps[1:]:
            lines.append(
                "        self.play("
                f"Transform(range_rect, span_rect({step['low']}, {step['high']})), "
                f"Transform(low_arrow, pointer({step['low']}, LEFT * 2.15, BLUE_B)), "
                f"Transform(mid_arrow, pointer({step['mid']}, ORIGIN, YELLOW)), "
                f"Transform(high_arrow, pointer({step['high']}, RIGHT * 2.15, GREEN_B)), "
                f"Transform(comparison, Text({_py_str(step['label'])}, font_size=22, color=YELLOW_B).move_to(comparison)), "
                f"run_time={max(0.28, visual_rt*0.16):.3f})"
            )
        lines.append(f"        found_box = boxes[{steps[-1]['mid']}].copy().set_fill(GREEN_E, opacity=0.32)")
        lines.append(f"        self.play(FadeIn(found_box), run_time={max(0.22, visual_rt*0.10):.3f})")
        lines.append("        visual_group = VGroup(arr, range_rect, low_arrow, mid_arrow, high_arrow, tags, target_card, comparison, found_box)")
    elif template == "graph":
        lines.append("        axes = Axes(x_range=[1, 8, 1], y_range=[0, 8, 1], x_length=5.0, y_length=3.2, axis_config={'color': GREY_B})")
        lines.append("        axes.next_to(seg, DOWN, buff=0.7).shift(RIGHT * 1.35)")
        lines.append("        linear = Line(axes.c2p(1, 1), axes.c2p(7.3, 7.0), color=RED_B)")
        lines.append("        log_curve = VMobject(color=GREEN_B)")
        lines.append("        log_curve.set_points_smoothly([axes.c2p(1, 1), axes.c2p(2, 2.3), axes.c2p(3, 3.1), axes.c2p(4, 3.8), axes.c2p(6, 4.7), axes.c2p(8, 5.2)])")
        lines.append("        labels = VGroup(")
        lines.append("            Text('O(n)', font_size=18, color=RED_B).next_to(linear, RIGHT, buff=0.1),")
        lines.append("            Text('O(log n)', font_size=18, color=GREEN_B).next_to(log_curve, DOWN, buff=0.08),")
        lines.append("        )")
        lines.append(f"        self.play(Create(axes), run_time={visual_rt*0.28:.3f})")
        lines.append(f"        self.play(Create(linear), Create(log_curve), run_time={visual_rt*0.42:.3f})")
        lines.append("        table = VGroup(")
        lines.append("            Text('8 items -> at most 3 checks', font_size=18, color=GREEN_B),")
        lines.append("            Text('1024 items -> at most 10 checks', font_size=18, color=GREEN_B),")
        lines.append("            Text('Linear search can touch every item', font_size=18, color=RED_B),")
        lines.append("        ).arrange(DOWN, aligned_edge=LEFT, buff=0.12).next_to(axes, DOWN, buff=0.28).align_to(axes, LEFT)")
        lines.append(f"        self.play(FadeIn(labels), Write(table), run_time={visual_rt*0.22:.3f})")
        lines.append("        visual_group = VGroup(axes, linear, log_curve, labels, table)")
    elif template == "pseudocode":
        lines.append("        code_frame = RoundedRectangle(width=6.4, height=3.7, corner_radius=0.18, color=BLUE_D).set_fill('#ffffff', opacity=0.96)")
        lines.append("        code_frame.next_to(seg, DOWN, buff=0.72).shift(RIGHT * 1.2)")
        lines.append("        code_lines = VGroup(")
        lines.append("            Text('while low <= high:', font_size=22),")
        lines.append("            Text('    mid = (low + high) // 2', font_size=22),")
        lines.append("            Text('    if arr[mid] == target: return mid', font_size=20),")
        lines.append("            Text('    elif arr[mid] < target: low = mid + 1', font_size=20),")
        lines.append("            Text('    else: high = mid - 1', font_size=20),")
        lines.append("        ).arrange(DOWN, aligned_edge=LEFT, buff=0.14).move_to(code_frame.get_center())")
        lines.append("        tracker = VGroup(")
        lines.append("            RoundedRectangle(width=1.45, height=0.62, corner_radius=0.12, color=BLUE_B),")
        lines.append("            RoundedRectangle(width=1.45, height=0.62, corner_radius=0.12, color=YELLOW_B),")
        lines.append("            RoundedRectangle(width=1.45, height=0.62, corner_radius=0.12, color=GREEN_B),")
        lines.append("        ).arrange(RIGHT, buff=0.18).next_to(code_frame, DOWN, buff=0.28)")
        lines.append("        tracker_labels = VGroup(")
        lines.append("            Text('low=0', font_size=18, color=BLUE_B).move_to(tracker[0]),")
        lines.append("            Text('mid=3', font_size=18, color=YELLOW_B).move_to(tracker[1]),")
        lines.append("            Text('high=7', font_size=18, color=GREEN_B).move_to(tracker[2]),")
        lines.append("        )")
        lines.append("        focus = SurroundingRectangle(code_lines[1], buff=0.08, color=YELLOW_B)")
        lines.append(f"        self.play(Create(code_frame), Write(code_lines), run_time={visual_rt*0.42:.3f})")
        lines.append(f"        self.play(Create(focus), FadeIn(tracker), FadeIn(tracker_labels), run_time={visual_rt*0.32:.3f})")
        lines.append(f"        self.play(Transform(focus, SurroundingRectangle(code_lines[3], buff=0.08, color=BLUE_B)), Transform(tracker_labels[0], Text('low=4', font_size=18, color=BLUE_B).move_to(tracker[0])), run_time={visual_rt*0.26:.3f})")
        lines.append("        visual_group = VGroup(code_frame, code_lines, tracker, tracker_labels, focus)")
    elif template == "formula":
        formula_expr = r"O(\log n)" if "log" in (seg_title + ' ' + seg_on_screen).lower() else r"ax^2 + bx + c = 0"
        lines.append(f"        expr = {_py_str(formula_expr)}")
        lines.append("        frame = RoundedRectangle(width=6.8, height=2.35, corner_radius=0.22, color=BLUE_D)")
        lines.append("        frame.next_to(seg, DOWN, buff=0.8).shift(RIGHT * 1.0)")
        lines.append("        try:")
        lines.append("            formula = MathTex(expr, color=YELLOW_B)")
        lines.append("            formula.scale_to_fit_width(6.0)")
        lines.append("        except Exception:")
        lines.append("            formula = Text(expr, font_size=36, color=YELLOW_B)")
        lines.append("            formula.scale_to_fit_width(6.0)")
        lines.append("        formula.move_to(frame.get_center())")
        lines.append("        explain = Text('Interpret each symbol before solving', font_size=18, color='#586782')")
        lines.append("        explain.scale_to_fit_width(5.8)")
        lines.append("        explain.next_to(frame, DOWN, buff=0.24)")
        lines.append(f"        self.play(DrawBorderThenFill(frame), Write(formula), run_time={visual_rt*0.65:.3f})")
        lines.append(f"        self.play(Write(explain), run_time={visual_rt*0.35:.3f})")
        lines.append("        visual_group = VGroup(frame, formula, explain)")
    elif template == "flow":
        lines.append("        left = RoundedRectangle(width=2.2, height=0.95, corner_radius=0.16, color=BLUE_D).set_fill('#e3efff', opacity=0.95).shift(LEFT * 2.6 + DOWN * 0.6)")
        lines.append("        mid = RoundedRectangle(width=2.3, height=0.95, corner_radius=0.16, color=TEAL_D).set_fill('#def7f0', opacity=0.95).shift(DOWN * 0.6)")
        lines.append("        right = RoundedRectangle(width=2.2, height=0.95, corner_radius=0.16, color=GREEN_D).set_fill('#e5f6e7', opacity=0.95).shift(RIGHT * 2.6 + DOWN * 0.6)")
        lines.append("        labels = VGroup(")
        lines.append("            Text('Input', font_size=20).move_to(left),")
        lines.append("            Text('Check', font_size=20).move_to(mid),")
        lines.append("            Text('Output', font_size=20).move_to(right),")
        lines.append("        )")
        lines.append("        a1 = Arrow(left.get_right(), mid.get_left(), buff=0.08, color=GREY_B)")
        lines.append("        a2 = Arrow(mid.get_right(), right.get_left(), buff=0.08, color=GREY_B)")
        lines.append(f"        self.play(Create(VGroup(left, mid, right)), run_time={visual_rt*0.42:.3f})")
        lines.append(f"        self.play(FadeIn(labels), GrowArrow(a1), GrowArrow(a2), run_time={visual_rt*0.58:.3f})")
        lines.append("        visual_group = VGroup(left, mid, right, labels, a1, a2)")
    elif template == "timeline":
        lines.append("        cards = VGroup(*[RoundedRectangle(width=2.0, height=1.0, corner_radius=0.16, color=BLUE_D).set_fill('#edf4ff', opacity=0.96) for _ in range(3)]).arrange(RIGHT, buff=0.34)")
        lines.append("        cards.next_to(seg, DOWN, buff=0.9).shift(RIGHT * 1.1)")
        lines.append("        labels = VGroup(")
        lines.append("            Text('First', font_size=18).move_to(cards[0]),")
        lines.append("            Text('Next', font_size=18).move_to(cards[1]),")
        lines.append("            Text('Then', font_size=18).move_to(cards[2]),")
        lines.append("        )")
        lines.append("        arrows = VGroup(")
        lines.append("            Arrow(cards[0].get_right(), cards[1].get_left(), buff=0.08, color=GREY_B),")
        lines.append("            Arrow(cards[1].get_right(), cards[2].get_left(), buff=0.08, color=GREY_B),")
        lines.append("        )")
        lines.append("        recap = Text('Follow the steps in order and keep each state change visible.', font_size=18, color='#586782')")
        lines.append("        recap.scale_to_fit_width(5.9)")
        lines.append("        recap.next_to(cards, DOWN, buff=0.3)")
        lines.append(f"        self.play(Create(cards), Write(labels), run_time={visual_rt*0.42:.3f})")
        lines.append(f"        self.play(GrowArrow(arrows[0]), GrowArrow(arrows[1]), Write(recap), run_time={visual_rt*0.38:.3f})")
        lines.append("        emphasis_box = SurroundingRectangle(cards[1], buff=0.08, color=YELLOW_B)")
        lines.append(f"        self.play(Create(emphasis_box), run_time={visual_rt*0.20:.3f})")
        lines.append("        visual_group = VGroup(cards, labels, arrows, recap, emphasis_box)")
    elif template == "checklist":
        lines.append("        cards = VGroup(")
        lines.append("            RoundedRectangle(width=2.5, height=0.95, corner_radius=0.16, color=RED_D).set_fill('#ffe7e5', opacity=0.95),")
        lines.append("            RoundedRectangle(width=2.5, height=0.95, corner_radius=0.16, color=YELLOW_D).set_fill('#fff4d8', opacity=0.95),")
        lines.append("            RoundedRectangle(width=2.5, height=0.95, corner_radius=0.16, color=GREEN_D).set_fill('#e4f7e8', opacity=0.95),")
        lines.append("        ).arrange(DOWN, buff=0.2).move_to(RIGHT * 1.2 + DOWN * 0.8)")
        lines.append("        labels = VGroup(")
        lines.append("            Text('Empty array', font_size=20).move_to(cards[0]),")
        lines.append("            Text('Duplicates', font_size=20).move_to(cards[1]),")
        lines.append("            Text('Safe midpoint', font_size=20).move_to(cards[2]),")
        lines.append("        )")
        lines.append("        summary = Text('Handle the bounds carefully and binary search stays fast and reliable.', font_size=20, color='#586782')")
        lines.append("        summary.scale_to_fit_width(6.2)")
        lines.append("        summary.next_to(cards, DOWN, buff=0.3)")
        lines.append(f"        self.play(Create(cards), Write(labels), run_time={visual_rt*0.48:.3f})")
        lines.append(f"        self.play(Write(summary), run_time={visual_rt*0.30:.3f})")
        lines.append("        visual_group = VGroup(cards, labels, summary)")
    else:
        lines.append("        center = Circle(radius=0.96, color=BLUE_D).set_fill('#e3efff', opacity=0.95).shift(DOWN * 0.5 + RIGHT * 0.9)")
        lines.append("        center_label = Text('Core idea', font_size=20).move_to(center)")
        lines.append("        n1 = RoundedRectangle(width=2.0, height=0.82, corner_radius=0.14, color=TEAL_D).set_fill('#e3faf4', opacity=0.96).move_to(LEFT * 2.5 + DOWN * 0.3)")
        lines.append("        n2 = RoundedRectangle(width=2.0, height=0.82, corner_radius=0.14, color=TEAL_D).set_fill('#e3faf4', opacity=0.96).move_to(RIGHT * 3.2 + DOWN * 0.3)")
        lines.append("        n3 = RoundedRectangle(width=2.0, height=0.82, corner_radius=0.14, color=TEAL_D).set_fill('#e3faf4', opacity=0.96).move_to(RIGHT * 0.9 + DOWN * 2.0)")
        lines.append("        labels = VGroup(")
        lines.append("            Text('Why', font_size=18).move_to(n1),")
        lines.append("            Text('How', font_size=18).move_to(n2),")
        lines.append("            Text('When', font_size=18).move_to(n3),")
        lines.append("        )")
        lines.append("        links = VGroup(")
        lines.append("            Line(center.get_left(), n1.get_right(), color=GREY_B),")
        lines.append("            Line(center.get_right(), n2.get_left(), color=GREY_B),")
        lines.append("            Line(center.get_bottom(), n3.get_top(), color=GREY_B),")
        lines.append("        )")
        lines.append(f"        self.play(Create(center), Write(center_label), run_time={visual_rt*0.3:.3f})")
        lines.append(f"        self.play(Create(VGroup(n1, n2, n3)), Write(labels), Create(links), run_time={visual_rt*0.7:.3f})")
        lines.append("        visual_group = VGroup(center, center_label, n1, n2, n3, labels, links)")

    lines.append(f"        self.wait({hold:.3f})")
    lines.append(f"        self.play(FadeOut(visual_group), FadeOut(bullets), FadeOut(seg), FadeOut(sidebar), run_time={outro_rt:.3f})")
    lines.append("        self.wait(0.02)")

    scene_code = "\n".join(lines)
    _validate_scene_code(scene_code=scene_code, template=template, visual_plan=visual_plan)
    with open(scene_file, "w", encoding="utf-8") as f:
        f.write(scene_code)
    return scene_file, template


def _find_latest_mp4(media_dir: str) -> str:
    mp4s = glob.glob(os.path.join(media_dir, "**", "*.mp4"), recursive=True)
    if not mp4s:
        raise RuntimeError(f"Manim succeeded but no mp4 found under {media_dir}")
    mp4s.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return mp4s[0]


def _render_segment(*, media_dir: str, scene_file: str, scene_class: str, quality: str) -> str:
    qflag = "-ql" if quality == "low" else "-qm"
    cmd = ["manim", qflag, "--media_dir", media_dir, scene_file, scene_class]
    completed = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=600,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Manim failed:\n{completed.stdout}")
    return _find_latest_mp4(media_dir)


def _generate_segments(
    *,
    topic: str,
    goal: str,
    lang: str,
    student_state: Dict[str, Any],
    rag_context: str,
) -> list[dict]:
    system = f"You are an expert tutor. Output VALID JSON only. Language: {lang}."
    user = f"""
Topic: {topic}
User goal: {goal}
StudentState: {json.dumps(student_state, ensure_ascii=False)}
""".strip()

    if rag_context:
        user += "\n\nReference material (ground to this when relevant):\n" + rag_context

    user += """

Create 6 to 10 segments.
For each segment output:
- id: 1..N
- title
- narration
- on_screen
- duration_sec: rough estimate only (2..16)
- visual_plan:
  - template: one of array, comparison, graph, formula, flow, timeline, concept, pseudocode, checklist
  - layout: short phrase describing the composition
  - elements: 2..6 concrete visual objects that should appear
  - sequence: 1..5 concrete animation beats
  - emphasis: 1..4 labels or values to highlight
  - example: specific data, formula, or case to animate
""".strip()

    prompt = user
    last_err = None
    data = None
    for _ in range(1, 4):
        try:
            data = chat_json(
                system=system,
                user=prompt
                + (
                    "\n\nSTRICT REQUIREMENTS:\n"
                    "- Output JSON only. No markdown.\n"
                    "- segments array length 6..10.\n"
                    "- include id,title,narration,on_screen,duration_sec,visual_plan for each segment.\n"
                    "- Prefer concrete visual plans over generic slides.\n"
                    "- Use Manim Community Edition style only: real mobjects, grouped layout, and no planner-note text as on-screen copy.\n"
                    "- For binary search, include sorted array examples, low/mid/high pointers, shrinking interval, pseudocode, and O(log n) comparison visuals.\n"
                ),
                schema_name="lesson_segments",
                schema=SEGMENTS_SCHEMA,
                strict=True,
                temperature=0.2,
                max_tokens=4000,
            )
            break
        except Exception as e:
            last_err = e
            prompt = user + f"\n\nPrevious output failed validation: {str(e)[:220]}...\nRegenerate from scratch."
    if data is None:
        if "binary search" in topic.lower():
            return [
                {
                    "id": 1,
                    "title": "Why Search Strategy Matters",
                    "narration": "Searching one item at a time works, but it scales poorly. Binary search wins because it uses structure to skip most of the array instead of scanning every element.",
                    "on_screen": "Two rows: a left-to-right scan versus a midpoint-based search.",
                    "duration_sec": 8,
                    "visual_plan": {
                        "template": "comparison",
                        "layout": "Side-by-side comparison",
                        "elements": ["linear scan row", "binary search row", "target marker"],
                        "sequence": ["show scan", "show midpoint jump", "show range shrink"],
                        "emphasis": ["linear", "binary", "fewer checks"],
                        "example": "Search for 13 in [2,4,7,10,13,16,19,22]",
                    },
                },
                {
                    "id": 2,
                    "title": "Sorted Data Is The Key",
                    "narration": "Binary search only works when the data is sorted. Once the middle value tells us the target is larger or smaller, the ordering lets us safely discard half the possibilities.",
                    "on_screen": "Sorted array with low, mid, and high pointers.",
                    "duration_sec": 8,
                    "visual_plan": {
                        "template": "array",
                        "layout": "Sorted array with pointers",
                        "elements": ["sorted array", "low pointer", "mid pointer", "high pointer"],
                        "sequence": ["show pointers", "compare midpoint", "discard a half"],
                        "emphasis": ["sorted", "low", "mid", "high"],
                        "example": "Sorted array [2,4,7,10,13,16,19,22], target 13",
                    },
                },
                {
                    "id": 3,
                    "title": "Walk Through The Example",
                    "narration": "Start with [2,4,7,10,13,16,19,22] and target 13. Compare with 10, move right, then compare with 16, move left, and land exactly on 13.",
                    "on_screen": "Animated array with shrinking range and final hit on 13.",
                    "duration_sec": 12,
                    "visual_plan": {
                        "template": "array",
                        "layout": "Step-by-step array walkthrough",
                        "elements": ["array cells", "range rectangle", "target badge", "pointer labels"],
                        "sequence": ["mid at 10", "shift to right half", "mid at 16", "highlight 13"],
                        "emphasis": ["10", "16", "13"],
                        "example": "Find 13 in [2,4,7,10,13,16,19,22]",
                    },
                },
                {
                    "id": 4,
                    "title": "The Core Loop",
                    "narration": "The loop is short. Compute the midpoint, compare with the target, then move low or high until the element is found or the search interval becomes empty.",
                    "on_screen": "Pseudocode with low, mid, and high updates.",
                    "duration_sec": 9,
                    "visual_plan": {
                        "template": "pseudocode",
                        "layout": "Code panel with state tracker",
                        "elements": ["while loop", "mid formula", "low tracker", "high tracker"],
                        "sequence": ["compute mid", "compare", "update low", "repeat"],
                        "emphasis": ["low", "mid", "high"],
                        "example": "mid = (low + high) // 2",
                    },
                },
                {
                    "id": 5,
                    "title": "Why The Runtime Is Logarithmic",
                    "narration": "Every comparison cuts the remaining search space roughly in half. That means the number of checks grows with log base two of n, which stays small even when the array gets very large.",
                    "on_screen": "Graph comparing O(n) and O(log n), plus a small check-count table.",
                    "duration_sec": 10,
                    "visual_plan": {
                        "template": "graph",
                        "layout": "Graph with comparison table",
                        "elements": ["O(n) line", "O(log n) curve", "check-count table"],
                        "sequence": ["draw axes", "plot both curves", "show table"],
                        "emphasis": ["O(n)", "O(log n)", "10 checks at 1024 items"],
                        "example": "8 items -> 3 checks, 1024 items -> 10 checks",
                    },
                },
                {
                    "id": 6,
                    "title": "Where Binary Search Helps Most",
                    "narration": "Binary search shines when you have a large sorted collection and need fast lookups. It is a classic example of using data organization to reduce work dramatically.",
                    "on_screen": "Concept map linking sorted data, fast lookup, and predictable performance.",
                    "duration_sec": 8,
                    "visual_plan": {
                        "template": "concept",
                        "layout": "Concept map",
                        "elements": ["sorted data", "fast lookup", "predictable performance"],
                        "sequence": ["show core idea", "connect benefits"],
                        "emphasis": ["sorted data", "fast search"],
                        "example": "Phonebook-style lookup in sorted records",
                    },
                },
                {
                    "id": 7,
                    "title": "Edge Cases And Recap",
                    "narration": "You still need to handle empty arrays, duplicate values, and safe midpoint calculation. With those details in place, binary search stays both fast and reliable.",
                    "on_screen": "Checklist of empty array, duplicates, and safe midpoint calculation.",
                    "duration_sec": 8,
                    "visual_plan": {
                        "template": "checklist",
                        "layout": "Checklist cards",
                        "elements": ["empty array", "duplicates", "safe midpoint"],
                        "sequence": ["show edge cases", "show safeguards", "recap"],
                        "emphasis": ["edge cases", "recap"],
                        "example": "mid = low + (high - low) // 2",
                    },
                },
            ]
        raise RuntimeError(f"Failed to generate segments after retries: {last_err}")
    return data["segments"]


@celery.task(name="tasks.learn_pipeline.generate_lesson_video", bind=True)
def generate_lesson_video(
    self,
    *,
    job_id: str,
    topic: str,
    user_goal: Optional[str] = None,
    preferred_language: Optional[str] = "English",
    student_state: Optional[Dict[str, Any]] = None,
    quality: str = "low",
    rag_collection_id: Optional[str] = None,
) -> dict:
    goal = user_goal or "Learn clearly with intuition and examples."
    lang = preferred_language or "English"
    state = student_state or {}

    rag_context = ""
    rag_sources = []
    rag_error = None
    if rag_collection_id:
        try:
            rag_data = query_collection_with_sources(
                collection_id=rag_collection_id,
                question=f"{topic}\nGoal: {goal}",
                top_k=5,
                max_chars=1800,
            )
            rag_context = rag_data.get("context", "")
            rag_sources = rag_data.get("sources", [])
        except Exception as e:
            rag_error = f"{type(e).__name__}: {e}"
            rag_context = ""

    segments = _generate_segments(
        topic=topic,
        goal=goal,
        lang=lang,
        student_state=state,
        rag_context=rag_context,
    )

    job_dir = os.path.join(OUTPUT_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    # 1) Audio-first per-segment timing
    audio_dir = os.path.join(job_dir, "audio")
    segment_assets = build_segment_audio_assets(
        segments=segments,
        preferred_language=preferred_language,
        audio_dir=audio_dir,
    )

    # Persist the effective pacing used.
    for seg, a in zip(segments, segment_assets):
        seg["target_duration_sec"] = a["audio_duration_sec"]
        seg["visual_plan"] = _normalize_visual_plan(seg)

    segments_path = os.path.join(job_dir, "segments.json")
    with open(segments_path, "w", encoding="utf-8") as f:
        json.dump({"segments": segments}, f, ensure_ascii=False, indent=2)

    script_text = "\n\n".join([f"{s['id']}. {s['title']}\n{s['narration']}" for s in segments])
    script_path = os.path.join(job_dir, "script.txt")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script_text)

    if rag_context:
        with open(os.path.join(job_dir, "context.txt"), "w", encoding="utf-8") as f:
            f.write(rag_context)

    scene_files: list[str] = []
    raw_segment_videos: list[str] = []
    synced_segment_videos: list[str] = []
    render_plan: list[dict[str, Any]] = []

    # 2) Per-segment render paced to audio duration
    for seg, asset in zip(segments, segment_assets):
        idx = int(asset["segment_index"])
        scene_class = f"LessonSeg_{job_id.replace('-', '_')}_{idx:02d}"
        scene_file, template = _write_segment_scene_file(
            job_id=job_id,
            segment_idx=idx,
            scene_class=scene_class,
            topic=topic,
            seg_title=asset["title"],
            seg_narration=seg["narration"],
            seg_on_screen=asset["on_screen"],
            visual_plan=seg["visual_plan"],
            target_duration=float(asset["audio_duration_sec"]),
        )
        scene_files.append(scene_file)

        seg_media_dir = os.path.join(job_dir, f"seg_{idx:02d}_media")
        raw_video = _render_segment(
            media_dir=seg_media_dir,
            scene_file=scene_file,
            scene_class=scene_class,
            quality=quality,
        )
        raw_segment_videos.append(raw_video)

        # 3) Exact duration alignment before mux
        fixed_video = os.path.join(job_dir, "segments", f"segment_{idx:02d}_video_fixed.mp4")
        ensure_video_duration(raw_video, float(asset["audio_duration_sec"]), fixed_video)

        synced_video = os.path.join(job_dir, "segments", f"segment_{idx:02d}_synced.mp4")
        merge_audio_with_video(fixed_video, asset["audio_path"], synced_video)
        synced_segment_videos.append(synced_video)
        render_plan.append(
            {
                "segment_index": idx,
                "title": asset["title"],
                "template": template,
                "layout": seg["visual_plan"]["layout"],
                "elements": seg["visual_plan"]["elements"],
                "sequence": seg["visual_plan"]["sequence"],
                "example": seg["visual_plan"]["example"],
                "scene_file": scene_file,
                "raw_video_path": raw_video,
                "synced_video_path": synced_video,
            }
        )

    # 4) Final lesson from already-synced segments
    final_video_path = os.path.join(job_dir, "final_with_audio.mp4")
    concat_media_files(synced_segment_videos, final_video_path)

    # Optional full narration artifact by concatenating segment audios
    audio_path = os.path.join(job_dir, "audio", "narration_full.mp3")
    tts_error = None
    try:
        concat_media_files([a["audio_path"] for a in segment_assets], audio_path)
    except Exception as e:
        tts_error = f"{type(e).__name__}: {e}"
        audio_path = None

    render_plan_path = os.path.join(job_dir, "render_plan.json")
    with open(render_plan_path, "w", encoding="utf-8") as f:
        json.dump({"segments": render_plan}, f, ensure_ascii=False, indent=2)

    payload = {
        "status": "COMPLETED",
        "job_id": job_id,
        "topic": topic,
        "rag_collection_id": rag_collection_id,
        "rag_sources": rag_sources,
        "rag_error": rag_error,
        "segments_path": segments_path,
        "script_path": script_path,
        "scene_file": scene_files[-1] if scene_files else None,
        "scene_class": None,
        "video_path": raw_segment_videos[-1] if raw_segment_videos else None,
        "audio_path": audio_path,
        "final_video_path": final_video_path,
        "canonical_video_path": final_video_path,
        "render_plan_path": render_plan_path,
        "tts_error": tts_error,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "sync_strategy": "segment_audio_first_duration_lock",
    }

    with session_scope() as db:
        status = JobStatus.SUCCESS if not tts_error else JobStatus.PARTIAL
        warning = tts_error or rag_error
        upsert_lesson_job_result(db, task_id=self.request.id, status=status, result_payload=payload, warning=warning)

    return payload
