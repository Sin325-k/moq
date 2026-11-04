"""Synthetic checks for the RECOVERY pilot analyzer."""

import csv
import json
import tempfile
import unittest
from pathlib import Path

from analyze_adaptive_c_recovery import analyze, transition_report


FIELDS = [
    "monotonic_ns", "wall_time_ns", "rtt_ns", "baseline_rtt_ns", "threshold_rtt_ns",
    "consecutive_high", "state", "background_pending", "background_in_flight",
    "baseline_started_wall_time_ns", "transition_wall_time_ns", "transition_reason",
    "stable_duration_ns", "recovery_stage", "background_start_interval_ms", "event", "group_sequence",
]


def sample(wall, state, reason="", stage="", event="SAMPLE", sequence="", pending=100):
    return dict.fromkeys(FIELDS, "") | {
        "wall_time_ns": wall, "rtt_ns": 1_300_000 if state == "CONGESTED" else 200_000,
        "baseline_rtt_ns": 200_000, "threshold_rtt_ns": 1_200_000,
        "consecutive_high": 3 if state == "CONGESTED" else 0,
        "state": state, "background_pending": pending, "background_in_flight": 0,
        "transition_reason": reason, "recovery_stage": stage,
        "background_start_interval_ms": {"1": "60", "2": "40", "3": "20"}.get(stage, ""),
        "event": event, "group_sequence": sequence,
    }


def write_csv(path, fields, rows):
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class RecoveryAnalyzerTest(unittest.TestCase):
    def test_reports_probe_rebound_and_later_normal(self):
        rows = [
            sample(2_070_000_000, "CONGESTED", "RTT above baseline +1 ms for 3 samples"),
            sample(3_000_000_000, "RECOVERY", "CONGESTED to RECOVERY: RTT stable for 150 ms and no Background in flight", "1"),
            sample(3_010_000_000, "RECOVERY", stage="1", event="GROUP_START", sequence="101"),
            sample(3_050_000_000, "RECOVERY", stage="1", event="GROUP_COMPLETE", sequence="101"),
            sample(3_400_000_000, "CONGESTED", "RECOVERY to CONGESTED: RTT above baseline +1 ms for 3 samples"),
            sample(5_000_000_000, "RECOVERY", "CONGESTED to RECOVERY: RTT stable for 150 ms and no Background in flight", "1"),
            sample(5_020_000_000, "RECOVERY", stage="1", event="GROUP_START", sequence="102"),
            sample(5_310_000_000, "RECOVERY", "RECOVERY stage advanced: RTT stable during probe", "2"),
            sample(5_620_000_000, "RECOVERY", "RECOVERY stage advanced: RTT stable during probe", "3"),
            sample(5_930_000_000, "NORMAL", "RECOVERY to NORMAL: all probe stages stable", pending=1),
            sample(7_000_000_000, "NORMAL", pending=0),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_csv(root / "quic.csv", FIELDS, rows)
            with (root / "tbf.jsonl").open("w") as output:
                for event, rate, when in [("low_rate", 1000, 2_000_000_000), ("recovery_rate", 3000, 4_000_000_000)]:
                    output.write(json.dumps({"event": event, "rate_kbit": rate, "applied_wall_time_ns": when, "applied_monotonic_ns": when}) + "\n")
            frames = [
                {"track": "audio", "seq": i, "send_time_ns": 2_100_000_000 + i * 1_000_000 if i < 200 else 4_100_000_000 + i * 1_000_000,
                 "received_wall_time_ns": 3_010_000_000 if i == 0 else 4_100_000_000 + i * 1_000_000, "delay_ms": "3.0"}
                for i in range(400)
            ]
            frames += [{"track": "background", "seq": i, "send_time_ns": 0, "received_wall_time_ns": 6_000_000_000, "delay_ms": "5.0"} for i in range(100)]
            write_csv(root / "subscriber.csv", list(frames[0]), frames)
            (root / "relay.txt").write_text("adaptive C state transition\n")
            report = analyze(root / "quic.csv", root / "tbf.jsonl", root / "subscriber.csv", root / "relay.txt")
            self.assertIn("1 Mbps RECOVERY -> CONGESTED count: 1", report)
            self.assertIn("False NORMAL before 3 Mbps restoration: 0", report)
            self.assertIn("3 Mbps switch -> NORMAL: 1930.000 ms", report)
            self.assertIn("Background received: 100/400", report)

    def test_rejects_invalid_state_edge(self):
        rows = [sample(2_000_000_000, "RECOVERY", "CONGESTED to RECOVERY", "1")]
        with self.assertRaisesRegex(ValueError, "invalid transition"):
            transition_report(rows, 1_000_000_000, 4_000_000_000)

    def test_flags_false_normal_before_bandwidth_restoration(self):
        rows = [
            sample(2_070_000_000, "CONGESTED", "RTT above baseline +1 ms for 3 samples"),
            sample(3_000_000_000, "RECOVERY", "CONGESTED to RECOVERY: RTT stable for 150 ms and no Background in flight", "1"),
            sample(3_900_000_000, "NORMAL", "RECOVERY to NORMAL: all probe stages stable"),
        ]
        result = transition_report(rows, 2_000_000_000, 4_000_000_000)
        self.assertEqual(len(result["false_normal"]), 1)


if __name__ == "__main__":
    unittest.main()
