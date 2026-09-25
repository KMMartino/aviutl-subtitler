"""Conservative, independent acceptance of subtitle cleanup edits."""
import re
import unicodedata

CLEANUP_GATE_VERSION = 1

def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text)

def formatting(text: str) -> str:
    # Preserve question/exclamation marks, long vowels, hyphens, and middle dots.
    return re.sub(r"[\s、。,，.．]", "", normalize(text))

FILLER = re.compile(r"(^|[\s、。,，.．])(?:えー*っと|えっと|えー*|あー+|うーん+|まあ|ま)(?=$|[\s、。,，.．])")

def remove_fillers(text: str) -> str:
    text = normalize(text)
    while True:
        new = FILLER.sub(r"\1", text)
        if new == text:
            return text
        text = new

def numbers(text: str) -> list[str]:
    # A decimal point is meaningful even though sentence punctuation is not.
    return re.findall(r"[0-9]+(?:[.,][0-9]+)*", normalize(text))

def cleanup_decision(before: str, after: str) -> str:
    if before == after:
        return "unchanged"
    if "\n" in after or "\r" in after or "<DELETE>" in after:
        return "invalid_structure"
    if numbers(before) != numbers(after):
        return "protected_number"
    if re.search(r"[ぁ-んァ-ヶ一-龯]", before) and after and not re.search(r"[ぁ-んァ-ヶ一-龯]", after):
        return "language_change"
    if formatting(before) == formatting(after):
        return "formatting"
    # Compare the proposed output to a deletion-only transformation of the input.
    # Stripping fillers from BOTH sides would allow insertion of invented speech.
    if formatting(remove_fillers(before)) == formatting(after):
        return "bounded_filler"
    # Allow a subset of the recognized deletions, without permitting insertions.
    spans = [(m.start() + len(m.group(1)), m.end()) for m in FILLER.finditer(normalize(before))]
    if len(spans) <= 12:
        original = normalize(before)
        for mask in range(1, 1 << len(spans)):
            candidate = original
            for i in reversed(range(len(spans))):
                if mask & (1 << i):
                    start, end = spans[i]
                    candidate = candidate[:start] + candidate[end:]
            if formatting(candidate) == formatting(after):
                return "bounded_filler"
    return "needs_evidence"

SAFE = {"unchanged", "formatting", "bounded_filler"}
