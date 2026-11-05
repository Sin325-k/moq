"""Synthetic D1/D2 offline analyzer tests."""

import csv
import json
import tempfile
import unittest
from pathlib import Path

from analyze_adaptive_c_bandwidth import analyze


QUIC_FIELDS = (
    "event", "state", "transition_reason", "background_in_flight", "wall_time_ns",
    "baseline_rtt_ns", "threshold_rtt_ns", "background_pending", "recovery_stage", "rtt_ns",
)
FRAME_FIELDS = (
    "track", "seq", "size", "send_time_ns", "received_time_ns",
    "received_wall_time_ns", "delay_ms",
)


def write_csv(path, fields, data):
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(data)


def sample(wall, state="NORMAL", reason="", stage="", event="SAMPLE", rtt=200_000, pending=10):
    return {
        "event": event, "state": state, "transition_reason": reason,
        "background_in_flight": "0", "wall_time_ns": str(wall),
        "baseline_rtt_ns": "200000", "threshold_rtt_ns": "1200000",
        "background_pending": str(pending), "recovery_stage": stage, "rtt_ns": str(rtt),
    }


class BandwidthAnalyzerTest(unittest.TestCase):
    def make_files(self, root, rate, samples, complete):
        quic = root / "quic.csv"
        tbf = root / "tbf.jsonl"
        subscriber = root / "subscriber.csv"
        relay = root / "relay.txt"
        write_csv(quic, QUIC_FIELDS, samples)
        with tbf.open("w") as output:
            for name, value, when in (
                ("initial_rate", 3000, 0), ("schedule_started", None, 0),
                ("low_rate", rate, 2_000_000_000), ("recovery_rate", 3000, 4_000_000_000),
            ):
                event = {"event": name, "applied_wall_time_ns": when, "applied_monotonic_ns": when}
                if value is not None:
                    event["rate_kbit"] = value
                if name == "schedule_started":
                    event["experiment_start_monotonic_ns"] = 0
                output.write(json.dumps(event) + "\n")
        count = 400 if complete else 1
        frames = []
        for track in ("audio", "background"):
            for seq in range(count):
                send = 3_000_000_000 if seq < count // 2 else 5_000_000_000
                frames.append({"track": track, "seq": seq, "size": 1945,
                               "send_time_ns": send, "received_time_ns": 8_000_000_000 + seq,
                               "received_wall_time_ns": send, "delay_ms": "4.0"})
        write_csv(subscriber, FRAME_FIELDS, frames)
        relay.write_text("adaptive C state transition\n")
        return quic, tbf, subscriber, relay

    def test_d2_reports_probe_and_restoration(self):
        samples = [
            sample(1_000_000_000),
            sample(2_080_000_000, "CONGESTED", "RTT above baseline +1 ms for 3 samples", rtt=1_400_000),
            sample(3_000_000_000, "RECOVERY", "CONGESTED to RECOVERY: RTT stable for 150 ms and no Background in flight", "1"),
            sample(3_020_000_000, "RECOVERY", stage="1", event="GROUP_START"),
            sample(3_040_000_000, "RECOVERY", stage="1", event="GROUP_COMPLETE"),
            sample(3_100_000_000, "RECOVERY", stage="1", rtt=1_500_000),
            sample(3_130_000_000, "CONGESTED", "RECOVERY to CONGESTED: RTT above baseline +1 ms for 3 samples", rtt=1_500_000),
            sample(4_100_000_000, "RECOVERY", "CONGESTED to RECOVERY: RTT stable for 150 ms and no Background in flight", "1"),
            sample(4_420_000_000, "RECOVERY", "RECOVERY stage advanced: RTT stable during probe", "2"),
            sample(4_740_000_000, "RECOVERY", "RECOVERY stage advanced: RTT stable during probe", "3"),
            sample(5_060_000_000, "NORMAL", "RECOVERY to NORMAL: all probe stages stable"),
            sample(8_000_000_000, pending=0),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            report = analyze("D2", *self.make_files(Path(tmp), 1200, samples, True))
        self.assertIn("3 -> 1.2 -> 3 Mbps", report)
        self.assertIn("bottleneck switch -> CONGESTED: 80.000 ms", report)
        self.assertIn("RECOVERY -> CONGESTED / false NORMAL: 1 / 0", report)
        self.assertIn("switch -> NORMAL: 1060.000 ms", report)
        self.assertIn("Audio / Background received: 400/400 / 400/400", report)

    def test_d1_keeps_missing_detection_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = analyze("D1", *self.make_files(Path(tmp), 800, [sample(1_000_000_000)], False))
        self.assertIn("bottleneck switch -> CONGESTED: not observed", report)
        self.assertIn("switch -> NORMAL: not observed", report)
        self.assertIn("Audio / Background received: 1/400 / 1/400", report)
        self.assertIn("detection after bottleneck=no", report)

    def test_rejects_wrong_tbf_rate_for_condition(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_files(Path(tmp), 1000, [sample(1_000_000_000)], False)
            with self.assertRaisesRegex(ValueError, "TBF rates differ"):
                analyze("D2", *paths)


if __name__ == "__main__":
    unittest.main()
