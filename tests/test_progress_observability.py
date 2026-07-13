import csv
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from aviutl_subtitle import _count_progress_reporter
from subtitler.subtitle_planner import CleanupStats, _write_planning_profile


class ProgressObservabilityTests(unittest.TestCase):
    def test_count_reporter_is_coarse_and_reports_completion(self) -> None:
        output = StringIO()
        report = _count_progress_reporter(step=25)
        with redirect_stdout(output):
            for completed in range(11):
                report("Planning subtitle breaks", completed, 10)

        self.assertEqual(
            output.getvalue().splitlines(),
            [
                "Planning subtitle breaks: 0/10",
                "Planning subtitle breaks: 3/10",
                "Planning subtitle breaks: 6/10",
                "Planning subtitle breaks: 9/10",
                "Planning subtitle breaks: 10/10",
            ],
        )

    def test_cleanup_stats_count_retained_originals(self) -> None:
        stats = CleanupStats(group_count=3, input_count=20, changed_count=4, deleted_count=2)
        self.assertEqual(stats.retained_count, 14)

    def test_planning_profile_is_diagnostic_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "run.planning.csv"
            _write_planning_profile(path, {"split_planning_seconds": 1.25, "chain_count": 4})
            with path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.reader(handle))
        self.assertEqual(rows, [["metric", "value"], ["split_planning_seconds", "1.25"], ["chain_count", "4"]])


if __name__ == "__main__":
    unittest.main()
