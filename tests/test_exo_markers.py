import unittest

from subtitler.exo import encode_text_for_exo, generate_exo_file
from subtitler.models import ExoMarker, ExoSettings, Subtitle


class ExoMarkerTests(unittest.TestCase):
    def test_chapter_and_qa_markers_use_separate_layers(self) -> None:
        content = generate_exo_file(
            [Subtitle(0.0, 1.0, "line")],
            ExoSettings(),
            total_duration=2.0,
            chapter_markers=[ExoMarker(0.0, 1.0, "Intro")],
            mistranscription_markers=[ExoMarker(0.2, 0.4, "1: high - reason")],
        )
        self.assertIn("layer=1", content)
        self.assertIn("layer=2", content)
        self.assertIn("layer=3", content)
        self.assertNotIn("layer=4", content)
        self.assertIn(encode_text_for_exo("Intro"), content)
        self.assertIn(encode_text_for_exo("1: high - reason"), content)
        self.assertIn("_name=アニメーション効果", content)
        self.assertIn("track0=0.20", content)
        self.assertIn("track0=-0.20", content)
        self.assertIn("サイズ=33", content)
        self.assertIn("color=ff0000", content)
        self.assertIn("Y=642.0", content)


if __name__ == "__main__":
    unittest.main()
