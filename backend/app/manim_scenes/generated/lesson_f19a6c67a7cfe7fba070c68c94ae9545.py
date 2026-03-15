from manim import *

class LessonScene_f19a6c67a7cfe7fba070c68c94ae9545(Scene):
    def construct(self):
        self.camera.background_color = BLACK
        title = Text("Binary Search", font_size=52).to_edge(UP)
        self.play(FadeIn(title), run_time=0.6)
        self.wait(0.2)
        self.play(FadeOut(title), run_time=0.4)

        seg_title = Text("What is Binary Search?", font_size=44).to_edge(UP)
        body = Text("Binary Search = fast, divide‑and‑conquer search", font_size=30, line_spacing=1.1).scale(0.9)
        body.move_to(ORIGIN)
        self.play(FadeIn(seg_title), Write(body), run_time=0.8)
        self.wait(5.0)
        self.play(FadeOut(seg_title), FadeOut(body), run_time=0.6)

        seg_title = Text("Why Sorting Matters", font_size=44).to_edge(UP)
        body = Text("Prerequisite: sorted array (ascending/descending)", font_size=30, line_spacing=1.1).scale(0.9)
        body.move_to(ORIGIN)
        self.play(FadeIn(seg_title), Write(body), run_time=0.8)
        self.wait(5.0)
        self.play(FadeOut(seg_title), FadeOut(body), run_time=0.6)

        seg_title = Text("Core Idea", font_size=44).to_edge(UP)
        body = Text("Compare target ↔ middle → left / right half", font_size=30, line_spacing=1.1).scale(0.9)
        body.move_to(ORIGIN)
        self.play(FadeIn(seg_title), Write(body), run_time=0.8)
        self.wait(6.0)
        self.play(FadeOut(seg_title), FadeOut(body), run_time=0.6)

        seg_title = Text("Iterative Steps", font_size=44).to_edge(UP)
        body = Text("low=0, high=n‑1; while(low≤high){mid=(low+high)/2 …}", font_size=30, line_spacing=1.1).scale(0.9)
        body.move_to(ORIGIN)
        self.play(FadeIn(seg_title), Write(body), run_time=0.8)
        self.wait(7.0)
        self.play(FadeOut(seg_title), FadeOut(body), run_time=0.6)

        seg_title = Text("Complexity Analysis", font_size=44).to_edge(UP)
        body = Text("Time: O(log n) • Space: Iterative O(1) • Recursive O(log n)", font_size=30, line_spacing=1.1).scale(0.9)
        body.move_to(ORIGIN)
        self.play(FadeIn(seg_title), Write(body), run_time=0.8)
        self.wait(6.0)
        self.play(FadeOut(seg_title), FadeOut(body), run_time=0.6)

        seg_title = Text("Example Walkthrough", font_size=44).to_edge(UP)
        body = Text("Array: [2,4,6,8,10,12] • Target=8 • Steps: 6 → 8 (found)", font_size=30, line_spacing=1.1).scale(0.9)
        body.move_to(ORIGIN)
        self.play(FadeIn(seg_title), Write(body), run_time=0.8)
        self.wait(8.0)
        self.play(FadeOut(seg_title), FadeOut(body), run_time=0.6)

        seg_title = Text("Where It’s Used", font_size=44).to_edge(UP)
        body = Text("Applications: databases, dictionaries, algorithmic helpers", font_size=30, line_spacing=1.1).scale(0.9)
        body.move_to(ORIGIN)
        self.play(FadeIn(seg_title), Write(body), run_time=0.8)
        self.wait(5.0)
        self.play(FadeOut(seg_title), FadeOut(body), run_time=0.6)

        end = Text("End", font_size=46).move_to(ORIGIN)
        self.play(FadeIn(end), run_time=0.6)
        self.wait(0.6)