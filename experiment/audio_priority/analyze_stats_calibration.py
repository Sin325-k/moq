"""Analyze Relay-to-Subscriber QUIC samples against offline TBF ground truth."""

import argparse
import csv
import json
import math
import statistics
from pathlib import Path


def percentile(values, percentile_value):
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * percentile_value / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def number(row, key):
    value = row[key]
    return None if value == "" else int(value)


def fmt(value, unit=""):
    return "n/a" if value is None else f"{value:.3f}{unit}"


def load_samples(path):
    with path.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    if len(rows) < 2:
        raise ValueError("QUIC stats CSV has fewer than two samples")
    keys = (
        "monotonic_ns",
        "wall_time_ns",
        "rtt_ns",
        "estimated_send_rate_bps",
        "inferred_cwnd_bytes",
        "bytes_sent",
        "bytes_sent_delta",
        "packets_sent",
        "packets_sent_delta",
        "bytes_lost",
        "bytes_lost_delta",
        "packets_lost",
        "packets_lost_delta",
        "background_pending",
        "background_in_flight",
    )
    samples = [{key: number(row, key) for key in keys} for row in rows]
    for previous, current in zip(samples, samples[1:]):
        if current["monotonic_ns"] <= previous["monotonic_ns"]:
            raise ValueError("QUIC sample monotonic timestamps are not increasing")
        if current["wall_time_ns"] <= previous["wall_time_ns"]:
            raise ValueError("QUIC sample wall timestamps are not increasing")
        for key, delta_key in (
            ("bytes_sent", "bytes_sent_delta"),
            ("packets_sent", "packets_sent_delta"),
            ("bytes_lost", "bytes_lost_delta"),
            ("packets_lost", "packets_lost_delta"),
        ):
            before, after = previous[key], current[key]
            if before is not None and after is not None:
                if after < before or current[delta_key] != after - before:
                    raise ValueError(f"invalid {key} counter or delta")
    return samples


def load_tbf(path):
    with path.open(encoding="utf-8") as source:
        events = [json.loads(line) for line in source if line.strip()]
    by_name = {event["event"]: event for event in events}
    started = by_name["schedule_started"]
    low = by_name["low_rate"]
    recovery = by_name["recovery_rate"]
    start_ns = (
        started["wall_time_ns"]
        - started["monotonic_ns"]
        + started["experiment_start_monotonic_ns"]
    )
    low_ns = low["applied_wall_time_ns"]
    recovery_ns = recovery["applied_wall_time_ns"]
    if not start_ns < low_ns < recovery_ns:
        raise ValueError("TBF phase timestamps are not ordered")
    if low["rate_kbit"] != 1000 or recovery["rate_kbit"] != 3000:
        raise ValueError("TBF schedule is not 3 -> 1 -> 3 Mbps")
    return start_ns, low_ns, recovery_ns


def validate_subscriber(path):
    with path.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    for track in ("audio", "background"):
        sequences = [int(row["seq"]) for row in rows if row["track"] == track]
        if sorted(sequences) != list(range(400)):
            raise ValueError(f"{track} delivery is incomplete or duplicated")


def phase_summary(label, samples):
    rtts = [row["rtt_ns"] / 1_000_000 for row in samples if row["rtt_ns"] is not None]
    rates = [row["estimated_send_rate_bps"] for row in samples if row["estimated_send_rate_bps"] is not None]
    cwnds = [row["inferred_cwnd_bytes"] for row in samples if row["inferred_cwnd_bytes"] is not None]
    loss_bytes = sum(row["bytes_lost_delta"] or 0 for row in samples)
    loss_packets = sum(row["packets_lost_delta"] or 0 for row in samples)
    sent_rate = None
    sent = [row for row in samples if row["bytes_sent"] is not None]
    if len(sent) >= 2:
        duration_ns = sent[-1]["monotonic_ns"] - sent[0]["monotonic_ns"]
        if duration_ns > 0:
            sent_rate = (sent[-1]["bytes_sent"] - sent[0]["bytes_sent"]) * 8_000_000_000 / duration_ns
    print(
        f"{label}: samples={len(samples)}, RTT min/median/p95={fmt(min(rtts) if rtts else None, ' ms')}/"
        f"{fmt(statistics.median(rtts) if rtts else None, ' ms')}/{fmt(percentile(rtts, 95), ' ms')}, "
        f"estimated rate median={fmt(statistics.median(rates) if rates else None, ' bps')}, "
        f"inferred cwnd median={fmt(statistics.median(cwnds) if cwnds else None, ' bytes')}, "
        f"loss bytes/packets={loss_bytes}/{loss_packets}, "
        f"bytes-sent rate={fmt(sent_rate, ' bps')}"
    )


def first_detection(samples, boundary_ns, predicate, consecutive=1, duration_ns=None):
    first_true_ns = None
    streak = 0
    for row in samples:
        value = predicate(row)
        if value:
            streak += 1
            if first_true_ns is None:
                first_true_ns = row["wall_time_ns"]
            if streak >= consecutive and (duration_ns is None or row["wall_time_ns"] - first_true_ns >= duration_ns):
                return (row["wall_time_ns"] - boundary_ns) / 1_000_000
        else:
            first_true_ns = None
            streak = 0
    return None


def rtt_thresholds(baseline):
    return [
        ("+1 ms", baseline + 1),
        ("+2 ms", baseline + 2),
        ("+5 ms", baseline + 5),
        ("+10 ms", baseline + 10),
        ("x1.25", baseline * 1.25),
        ("x1.5", baseline * 1.5),
        ("x2.0", baseline * 2),
    ]


def analyze(samples, start_ns, low_ns, recovery_ns):
    pre = [row for row in samples if start_ns <= row["wall_time_ns"] < low_ns]
    low = [row for row in samples if low_ns <= row["wall_time_ns"] < recovery_ns]
    recovered = [row for row in samples if row["wall_time_ns"] >= recovery_ns]
    if not pre or not low or not recovered:
        raise ValueError("one or more TBF phases have no QUIC samples")
    intervals = [
        (current["monotonic_ns"] - previous["monotonic_ns"]) / 1_000_000
        for previous, current in zip(samples, samples[1:])
    ]
    print(f"sample interval median/p95/max: {fmt(statistics.median(intervals), ' ms')}/"
          f"{fmt(percentile(intervals, 95), ' ms')}/{fmt(max(intervals), ' ms')}")
    for label, phase in (("0-2s 3 Mbps", pre), ("2-4s 1 Mbps", low), ("4s+ 3 Mbps", recovered)):
        phase_summary(label, phase)

    baseline_rtts = [row["rtt_ns"] / 1_000_000 for row in pre if row["rtt_ns"] is not None]
    baseline_rates = [row["estimated_send_rate_bps"] for row in pre if row["estimated_send_rate_bps"] is not None]
    if not baseline_rtts or not baseline_rates:
        raise ValueError("normal 3 Mbps phase lacks RTT or estimated rate")
    baseline_rate = statistics.median(baseline_rates)
    baselines = (
        ("minimum", min(baseline_rtts)),
        ("p10", percentile(baseline_rtts, 10)),
        ("median", statistics.median(baseline_rtts)),
    )
    print(f"baseline estimated send rate median: {fmt(baseline_rate, ' bps')}")
    for baseline_name, baseline in baselines:
        print(f"baseline RTT {baseline_name}: {fmt(baseline, ' ms')}")
        for label, threshold in rtt_thresholds(baseline):
            predicate = lambda row, limit=threshold: row["rtt_ns"] is not None and row["rtt_ns"] / 1_000_000 > limit
            delays = [first_detection(low, low_ns, predicate, consecutive=count) for count in (1, 2, 3)]
            delays += [first_detection(low, low_ns, predicate, duration_ns=milliseconds * 1_000_000) for milliseconds in (50, 100)]
            false_count = sum(predicate(row) for row in pre)
            print(f"  RTT > {label} ({fmt(threshold, ' ms')}): "
                  f"1/2/3 samples, 50/100 ms = {', '.join(fmt(delay, ' ms') for delay in delays)}; "
                  f"normal false positives = {false_count}/{len(pre)} ({false_count / len(pre):.1%})")
        for label, threshold in (("+1 ms", baseline + 1), ("+2 ms", baseline + 2), ("x1.25", baseline * 1.25)):
            predicate = lambda row, limit=threshold: row["rtt_ns"] is not None and row["rtt_ns"] / 1_000_000 <= limit
            print(f"  recovery RTT <= {label}: {fmt(first_detection(recovered, recovery_ns, predicate), ' ms')}")

    for fraction in (0.9, 0.8, 0.7):
        predicate = lambda row, limit=baseline_rate * fraction: row["estimated_send_rate_bps"] is not None and row["estimated_send_rate_bps"] < limit
        print(f"estimated rate < {fraction:.0%} baseline: "
              f"{fmt(first_detection(low, low_ns, predicate), ' ms')}")
    predicate = lambda row: row["estimated_send_rate_bps"] is not None and row["estimated_send_rate_bps"] >= baseline_rate * 0.9
    print(f"recovery estimated rate >= 90% baseline: {fmt(first_detection(recovered, recovery_ns, predicate), ' ms')}")
    after_low = low + recovered
    for label in ("bytes_lost_delta", "packets_lost_delta"):
        predicate = lambda row, key=label: row[key] is not None and row[key] > 0
        print(f"first {label} after 1 Mbps switch: {fmt(first_detection(after_low, low_ns, predicate), ' ms')}")
    print("Note: loss deltas are assigned to the phase of the reporting sample; a boundary-straddling interval may include earlier events.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("quic_csv", type=Path)
    parser.add_argument("tbf_jsonl", type=Path)
    parser.add_argument("subscriber_csv", type=Path)
    args = parser.parse_args()
    samples = load_samples(args.quic_csv)
    start_ns, low_ns, recovery_ns = load_tbf(args.tbf_jsonl)
    validate_subscriber(args.subscriber_csv)
    analyze(samples, start_ns, low_ns, recovery_ns)
    print("validation: QUIC samples and A-condition delivery OK")


if __name__ == "__main__":
    main()
