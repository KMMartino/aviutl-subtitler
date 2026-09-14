import unittest

from subtitler.focused_evidence import plan_focused_evidence


def fact(identifier, start, end, **extra):
    return {"id": identifier, "start_ms": start, "end_ms": end,
            "evidence_kind": "dense_visual", **extra}


class FocusedEvidenceTests(unittest.TestCase):
    def plan(self, facts=(), cuts=(), **overrides):
        args = {"source_id": "source", "duration_ms": 10000, "core": (0, 10000),
                "decision": {"question": "What changed?", "cuts": list(cuts)},
                "facts": list(facts), "source_identity": {"fingerprint": "original"}}
        return plan_focused_evidence(**(args | overrides))

    def test_short_cited_event_and_actual_observation_survive_endpoint_pressure(self):
        facts = [fact(f"long-{i}", 0, 10000) for i in range(20)]
        facts.append(fact("brief", 4200, 4400, frame_times_ms=[4210]))
        cuts = [{"start_ms": i * 400, "end_ms": i * 400 + 200,
                 "evidence_ids": ["brief"]} for i in range(20)]
        packet = self.plan(facts, cuts)
        frames = packet["frames"]
        self.assertLessEqual(len(frames), 16)
        self.assertEqual(len({r["timestamp_ms"] for r in frames}), len(frames))
        anchor = next(r for r in frames if r["timestamp_ms"] == 4210)
        self.assertTrue(any(r["basis"] == "supporting_observation" for r in anchor["requests"]))
        self.assertNotIn("brief", packet["unserved_anchor_ids"])
        self.assertTrue(packet["unserved_endpoint_times_ms"])

    def test_source_and_core_boundaries_prevent_external_reference_leaks(self):
        packet = self.plan([fact("outside", 9000, 9500), fact("edge", 1900, 2100),
                            fact("inside", 2800, 2900, frame_times_ms=[9000, 2850])],
                           [{"start_ms": -500, "end_ms": 11000, "evidence_ids": ["outside"]}],
                           core=(2000, 3000))
        times = [r["timestamp_ms"] for r in packet["frames"]]
        self.assertTrue(all(2000 <= t < 3000 for t in times))
        self.assertIn(2850, times)
        self.assertIn("outside", packet["unavailable_visual_reference_ids"])
        clamped = self.plan(core=(-2000, 20000))
        self.assertEqual([r["timestamp_ms"] for r in clamped["frames"]], [0, 4999, 9999])

    def test_empty_and_single_millisecond_ranges_are_safe(self):
        for core, duration in [((0, 0), 0), ((300, 200), 1000), ((2000, 3000), 1000)]:
            self.assertEqual(self.plan(core=core, duration_ms=duration)["frames"], [])
        self.assertEqual(self.plan(max_frames=0)["frames"], [])
        self.assertEqual(len(self.plan(core=(0, 1))["frames"]), 1)
        with self.assertRaises(ValueError):
            self.plan(max_frames=17)

    def test_references_and_revisions_control_reuse_without_mutating_inputs(self):
        facts = [fact("anchor", 1000, 2000)]
        first = self.plan(facts, treatment_refs=("anchor",))
        self.assertEqual(first, self.plan(facts, treatment_refs=("anchor",)))
        self.assertEqual(facts, [fact("anchor", 1000, 2000)])
        changed = self.plan([fact("anchor", 1000, 2000, observation="Changed evidence")],
                            treatment_refs=("anchor",))
        self.assertNotEqual(first["packet_id"], changed["packet_id"])
        self.assertEqual(first["frames"][0]["frame_id"], changed["frames"][0]["frame_id"])
        other_source = self.plan(facts, source_identity={"fingerprint": "replacement"})
        self.assertNotEqual(first["frames"][0]["frame_id"], other_source["frames"][0]["frame_id"])


if __name__ == "__main__":
    unittest.main()
