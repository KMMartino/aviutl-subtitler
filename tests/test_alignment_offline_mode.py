import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aviutl_subtitle import _configure_alignment_offline_mode


class AlignmentOfflineModeTests(unittest.TestCase):
    def test_remote_model_identifier_never_enables_offline_mode(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            enabled = _configure_alignment_offline_mode(
                {"model": "MahmoudAshraf/mms-300m-1130-forced-aligner", "offline_model_cache": True}
            )
            self.assertFalse(enabled)
            self.assertNotIn("HF_HUB_OFFLINE", os.environ)

    def test_confirmed_local_model_directory_enables_offline_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            root = Path(directory)
            (root / "config.json").write_text("{}", encoding="utf-8")
            (root / "model.safetensors").write_bytes(b"model")
            (root / "tokenizer_config.json").write_text("{}", encoding="utf-8")
            enabled = _configure_alignment_offline_mode(
                {"model": str(root), "offline_model_cache": True}
            )
            self.assertTrue(enabled)
            self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")
            self.assertEqual(os.environ["TRANSFORMERS_OFFLINE"], "1")

    def test_confirmed_local_model_overrides_disabled_offline_environment(self) -> None:
        inherited = {"HF_HUB_OFFLINE": "0", "TRANSFORMERS_OFFLINE": "0"}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, inherited, clear=True):
            root = Path(directory)
            (root / "config.json").write_text("{}", encoding="utf-8")
            (root / "model.safetensors").write_bytes(b"model")
            (root / "tokenizer_config.json").write_text("{}", encoding="utf-8")

            enabled = _configure_alignment_offline_mode(
                {"model": str(root), "offline_model_cache": True}
            )

            self.assertTrue(enabled)
            self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")
            self.assertEqual(os.environ["TRANSFORMERS_OFFLINE"], "1")


if __name__ == "__main__":
    unittest.main()
