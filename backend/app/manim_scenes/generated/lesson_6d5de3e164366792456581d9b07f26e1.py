from manim import *

class LessonScene_6d5de3e164366792456581d9b07f26e1(Scene):
    def construct(self):
        self.camera.background_color = BLACK
        title = Text("Binary Search", font_size=52).to_edge(UP)
        self.play(FadeIn(title), run_time=0.6)
        self.wait(0.2)
        self.play(FadeOut(title), run_time=0.4)

        seg_title = Text("What is Binary Search?", font_size=44).to_edge(UP)
        body = Text("Binary Search: Find item by repeatedly halving a sorted list", font_size=30, line_spacing=1.1).scale(0.9)
        body.move_to(ORIGIN)
        self.play(FadeIn(seg_title), Write(body), run_time=0.8)
        self.wait(5.0)
        self.play(FadeOut(seg_title), FadeOut(body), run_time=0.6)

        seg_title = Text("Why Sorting Matters", font_size=44).to_edge(UP)
        body = Text("Precondition: List must be sorted in ascending order", font_size=30, line_spacing=1.1).scale(0.9)
        body.move_to(ORIGIN)
        self.play(FadeIn(seg_title), Write(body), run_time=0.8)
        self.wait(5.0)
        self.play(FadeOut(seg_title), FadeOut(body), run_time=0.6)

        seg_title = Text("Divide and Conquer Idea", font_size=44).to_edge(UP)
        body = Text("Check middle → left half or right half → repeat", font_size=30, line_spacing=1.1).scale(0.9)
        body.move_to(ORIGIN)
        self.play(FadeIn(seg_title), Write(body), run_time=0.8)
        self.wait(5.0)
        self.play(FadeOut(seg_title), FadeOut(body), run_time=0.6)

        seg_title = Text("Step‑by‑Step Algorithm", font_size=44).to_edge(UP)
        body = Text("low=0, high=n‑1; while low≤high: mid=(low+high)//2; compare; adjust; return index or -1", font_size=30, line_spacing=1.1).scale(0.9)
        body.move_to(ORIGIN)
        self.play(FadeIn(seg_title), Write(body), run_time=0.8)
        self.wait(5.0)
        self.play(FadeOut(seg_title), FadeOut(body), run_time=0.6)

        seg_title = Text("Complexity Analysis", font_size=44).to_edge(UP)
        body = Text("Time: O(log n) Space: O(1)", font_size=30, line_spacing=1.1).scale(0.9)
        body.move_to(ORIGIN)
        self.play(FadeIn(seg_title), Write(body), run_time=0.8)
        self.wait(5.0)
        self.play(FadeOut(seg_title), FadeOut(body), run_time=0.6)

        seg_title = Text("Common Pitfalls & Example", font_size=44).to_edge(UP)
        body = Text("mid = low + (high‑low)//2 # safe mid calculation\ndef binary_search(arr, target):\n    low, high = 0, len(arr)-1\n    while low <= high:\n        mid = low + (high-low)//2\n        if arr[mid] == target:\n            return mid\n        elif arr[mid] < target:\n            low = mid + 1\n        else:\n            high = mid - 1\n    return -1", font_size=30, line_spacing=1.1).scale(0.9)
        body.move_to(ORIGIN)
        self.play(FadeIn(seg_title), Write(body), run_time=0.8)
        self.wait(5.0)
        self.play(FadeOut(seg_title), FadeOut(body), run_time=0.6)

        end = Text("End", font_size=46).move_to(ORIGIN)
        self.play(FadeIn(end), run_time=0.6)
        self.wait(0.6)