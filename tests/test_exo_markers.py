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
            mistranscription_markers=[ExoMarker(0.2, 0.4, "reason")],
        )
        self.assertIn("layer=2", content)
        self.assertIn("layer=3", content)
        self.assertNotIn("layer=4", content)
        self.assertIn(encode_text_for_exo("Intro"), content)
        self.assertIn(encode_text_for_exo("reason"), content)


if __name__ == "__main__":
    unittest.main()
