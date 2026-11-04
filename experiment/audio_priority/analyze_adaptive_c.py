"""Evaluate the first RTT-triggered Background stop against offline TBF timing."""

import argparse
import csv
import json
import statistics
from pathlib import Path

from analyze_stats_calibration import percentile


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def tbf_boundaries(path):
    with path.open(encoding="utf-8") as source:
        events = {event["event"]: event for line in source if line.strip() for event in [json.loads(line)]}
    low = events["low_rate"]
    recovery = events["recovery_rate"]
    if low["rate_kbit"] != 1000 or recovery["rate_kbit"] != 3000:
        raise ValueError("TBF schedule is not 3 -> 1 -> 3 Mbps")
    return low["applied_wall_time_ns"], low["applied_monotonic_ns"], recovery["applied_monotonic_ns"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("c_log_csv", type=Path)
    parser.add_argument("tbf_jsonl", type=Path)
    parser.add_argument("subscriber_csv", type=Path)
    args = parser.parse_args()

    samples = read_csv(args.c_log_csv)
    if not samples:
        raise ValueError("C log has no samples")
    transitions = [row for row in samples if row["transition_reason"]]
    if len(transitions) != 1:
        raise ValueError(f"expected one NORMAL-to-CONGESTED transition, got {len(transitions)}")
    transition = transitions[0]
    if transition["transition_reason"] != "RTT above baseline +1 ms for 3 samples":
        raise ValueError("unexpected C transition reason")
    if transition["state"] != "CONGESTED" or int(transition["consecutive_high"]) != 3:
        raise ValueError("transition row does not satisfy the three-sample rule")
    if any(int(row["background_in_flight"]) > 1 for row in samples):
        raise ValueError("Background in-flight exceeded one")
    if any(row["state"] != "CONGESTED" for row in samples[samples.index(transition) :]):
        raise ValueError("C returned to NORMAL without a recovery policy")
    baseline = int(transition["baseline_rtt_ns"])
    threshold = int(transition["threshold_rtt_ns"])
    if threshold != baseline + 1_000_000 or int(transition["rtt_ns"]) <= threshold:
        raise ValueError("transition threshold does not match baseline +1 ms")
    transition_wall = int(transition["transition_wall_time_ns"])
    if transition_wall != int(transition["wall_time_ns"]):
        raise ValueError("transition timestamp differs from sample timestamp")
    low_wall, low_mono, recovery_mono = tbf_boundaries(args.tbf_jsonl)
    baseline_start = int(transition["baseline_started_wall_time_ns"])
    baseline_ready = next(int(row["wall_time_ns"]) for row in samples if row["baseline_rtt_ns"])
    if baseline_start >= low_wall or baseline_ready >= low_wall:
        raise ValueError("baseline was not frozen in the normal 3 Mbps phase")

    rows = read_csv(args.subscriber_csv)
    audio = [row for row in rows if row["track"] == "audio"]
    background = [row for row in rows if row["track"] == "background"]
    if [int(row["seq"]) for row in audio] != list(range(400)):
        raise ValueError("Audio delivery is incomplete or out of order")
    background_sequences = [int(row["seq"]) for row in background]
    if len(background_sequences) != len(set(background_sequences)) or len(background) > 400:
        raise ValueError("Background delivery has duplicates or too many frames")
    low_audio = [
        float(row["delay_ms"])
        for row in audio
        if low_mono <= int(row["send_time_ns"]) < recovery_mono
    ]
    if not low_audio:
        raise ValueError("no Audio samples in the 1 Mbps phase")

    detection_ms = (transition_wall - low_wall) / 1_000_000
    if detection_ms < 0:
        raise ValueError("C transitioned before the 1 Mbps phase")
    print(f"baseline RTT median: {baseline / 1_000_000:.3f} ms")
    print(f"threshold RTT: {threshold / 1_000_000:.3f} ms")
    print(f"NORMAL-to-CONGESTED transition wall_time_ns: {transition_wall}")
    print(f"transition reason: {transition['transition_reason']}")
    print(f"1 Mbps switch-to-detection: {detection_ms:.3f} ms")
    print(f"Background new-Group stop wall_time_ns: {transition_wall}")
    print(f"Background pending/in-flight at stop: {transition['background_pending']}/{transition['background_in_flight']}")
    print(f"Background maximum pending: {max(int(row['background_pending']) for row in samples)}")
    print(
        f"Audio 1 Mbps mean/p95/max: {statistics.fmean(low_audio):.3f}/"
        f"{percentile(low_audio, 95):.3f}/{max(low_audio):.3f} ms"
    )
    print(f"Audio receive rate: {len(audio) / 400:.1%} ({len(audio)}/400)")
    print(f"Background receive: {len(background)}/400 ({len(background) / 400:.1%})")
    print("Reference 1 Mbps Audio mean/p95: R 1.995/2.572, A 81.101/100.443, Oracle B 2.113/2.631 ms")
    print("Background completion is not comparable with Oracle B because minimal C has no recovery or resume policy.")
    print("validation: C transition and Audio delivery OK")


if __name__ == "__main__":
    main()
