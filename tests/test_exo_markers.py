import unittest

from subtitler.exo import encode_text_for_exo, generate_exo_file
from subtitler.models import ExoMarker, ExoSettings, Subtitle


class ExoMarkerTests(unittest.TestCase):
    def test_vad_chain_and_qa_markers_use_separate_layers(self) -> None:
        content = generate_exo_file(
            [Subtitle(0.0, 1.0, "line")],
            ExoSettings(),
            total_duration=2.0,
            vad_markers=[ExoMarker(0.0, 0.5, "VAD 1")],
            chain_markers=[ExoMarker(0.0, 1.0, "")],
            mistranscription_markers=[ExoMarker(0.2, 0.4, "reason")],
        )
        self.assertIn("layer=2", content)
        self.assertIn("layer=3", content)
        self.assertIn("layer=4", content)
        self.assertIn(encode_text_for_exo("VAD 1"), content)
        self.assertIn(encode_text_for_exo("reason"), content)


if __name__ == "__main__":
    unittest.main()
