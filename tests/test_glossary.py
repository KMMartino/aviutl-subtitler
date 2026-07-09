from pathlib import Path

from subtitler.glossary import format_glossary, load_glossary


def test_load_glossary_ignores_tags_and_disabled_rows(tmp_path: Path) -> None:
    glossary_path = tmp_path / "glossary.txt"
    glossary_path.write_text(
        "\n".join(
            [
                "# preferred term | optional guidance",
                "# tag: gaming",
                "PSSR | PlayStation image upscaling | gaming",
                "# DLSS | Nvidia upscaling | gaming",
                "FSR 4.1 | AMD upscaling",
            ]
        ),
        encoding="utf-8",
    )

    entries = load_glossary(glossary_path)

    assert [entry.term for entry in entries] == ["PSSR", "FSR 4.1"]
    assert [entry.guidance for entry in entries] == ["PlayStation image upscaling", "AMD upscaling"]
    assert format_glossary(entries) == "PSSR | PlayStation image upscaling\nFSR 4.1 | AMD upscaling"
