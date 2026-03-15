from manim import *

class SampleScene(Scene):
    def construct(self):
        title = Text("Adaptive Tutor MVP", font_size=48).to_edge(UP)
        eq = MathTex(r"E = mc^2").scale(1.5)
        note = Text("Rendered with Manim + LaTeX", font_size=28).to_edge(DOWN)

        group = VGroup(title, eq, note).arrange(DOWN, buff=0.6).move_to(ORIGIN)

        self.play(Write(group[0]))
        self.play(Write(group[1]))
        self.play(FadeIn(group[2]))
        self.wait(1)
        self.play(FadeOut(group))
