from manim import *

class LessonScene_758812e8d3b22177609b9df4368f7b9c(Scene):
    def construct(self):
        self.camera.background_color = BLACK
        title = Text("Binary Search", font_size=52).to_edge(UP)
        self.play(FadeIn(title), run_time=0.6)
        self.wait(0.2)
        self.play(FadeOut(title), run_time=0.4)

        seg_title = Text("What is Binary Search?", font_size=44).to_edge(UP)
        body = Text("Binary Search = repeatedly halve a sorted list to locate a target", font_size=30, line_spacing=1.1).scale(0.9)
        body.move_to(ORIGIN)
        self.play(FadeIn(seg_title), Write(body), run_time=0.8)
        self.wait(5.0)
        self.play(FadeOut(seg_title), FadeOut(body), run_time=0.6)

        seg_title = Text("Prerequisites", font_size=44).to_edge(UP)
        body = Text("Requirements: Sorted array + random access (e.g., array)", font_size=30, line_spacing=1.1).scale(0.9)
        body.move_to(ORIGIN)
        self.play(FadeIn(seg_title), Write(body), run_time=0.8)
        self.wait(5.0)
        self.play(FadeOut(seg_title), FadeOut(body), run_time=0.6)

        seg_title = Text("Algorithm Steps", font_size=44).to_edge(UP)
        body = Text("low, high → mid = (low+high)/2\nif A[mid]==target → found\nelse if target<A[mid] → high=mid-1\nelse low=mid+1", font_size=30, line_spacing=1.1).scale(0.9)
        body.move_to(ORIGIN)
        self.play(FadeIn(seg_title), Write(body), run_time=0.8)
        self.wait(8.0)
        self.play(FadeOut(seg_title), FadeOut(body), run_time=0.6)

        seg_title = Text("Worked Example", font_size=44).to_edge(UP)
        body = Text("Array: 2 4 7 10 14 18 21\nTarget=14\nSteps → mid indices: 3 → 5 → 4 (found)", font_size=30, line_spacing=1.1).scale(0.9)
        body.move_to(ORIGIN)
        self.play(FadeIn(seg_title), Write(body), run_time=0.8)
        self.wait(7.0)
        self.play(FadeOut(seg_title), FadeOut(body), run_time=0.6)

        seg_title = Text("Time & Space Complexity", font_size=44).to_edge(UP)
        body = Text("Time: O(log n)\nSpace: O(1)", font_size=30, line_spacing=1.1).scale(0.9)
        body.move_to(ORIGIN)
        self.play(FadeIn(seg_title), Write(body), run_time=0.8)
        self.wait(5.0)
        self.play(FadeOut(seg_title), FadeOut(body), run_time=0.6)

        seg_title = Text("Pitfalls & Variants", font_size=44).to_edge(UP)
        body = Text("Watch out: unsorted data, overflow, off‑by‑one\nVariants: lower‑bound, upper‑bound", font_size=30, line_spacing=1.1).scale(0.9)
        body.move_to(ORIGIN)
        self.play(FadeIn(seg_title), Write(body), run_time=0.8)
        self.wait(6.0)
        self.play(FadeOut(seg_title), FadeOut(body), run_time=0.6)

        end = Text("End", font_size=46).move_to(ORIGIN)
        self.play(FadeIn(end), run_time=0.6)
        self.wait(0.6)