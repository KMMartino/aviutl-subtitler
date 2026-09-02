import csv
import tempfile
import threading
import unittest
from pathlib import Path

from tools.alignment_memory import (
    AlignmentMemoryProfiler,
    ProcessMemorySnapshot,
)


class AlignmentMemoryProfilerTests(unittest.TestCase):
    def test_records_phase_concurrency_and_process_memory(self) -> None:
        snapshot = ProcessMemorySnapshot(
            working_set_bytes=100,
            private_working_set_bytes=90,
            peak_working_set_bytes=120,
            commit_bytes=130,
            peak_commit_bytes=140,
        )
        with tempfile.TemporaryDirectory() as temp_name:
            output_path = Path(temp_name) / "sample.alignment_memory.csv"
            profiler = AlignmentMemoryProfiler(
                output_path,
                interval_sec=10,
                memory_reader=lambda: snapshot,
            )
            profiler.start()
            worker = threading.Thread(
                target=lambda: (
                    profiler.phase("generate_emissions", 7),
                    profiler.phase("idle", 7),
                ),
                name="aligner-1",
            )
            worker.start()
            worker.join()
            profiler.close()

            with output_path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))

        emission_row = next(row for row in rows if row["phase"] == "generate_emissions")
        self.assertEqual(emission_row["chunk_index"], "7")
        self.assertEqual(emission_row["active_alignment_workers"], "1")
        self.assertEqual(emission_row["emission_workers"], "1")
        self.assertEqual(emission_row["private_working_set_bytes"], "90")
        self.assertEqual(rows[-1]["event"], "pool_closed")

if __name__ == "__main__":
    unittest.main()
