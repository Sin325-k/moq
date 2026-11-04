"""Aggregate five A-condition QUIC stats calibration runs."""

import argparse
import statistics
from pathlib import Path

from analyze_stats_calibration import (
    first_detection,
    load_samples,
    load_tbf,
    percentile,
    validate_subscriber,
)


LABELS = ("pilot1", "run2", "run3", "run4", "run5")
RULES = (
    ("1 sample", 1, None),
    ("2 samples", 2, None),
    ("3 samples", 3, None),
    ("50 ms", 1, 50_000_000),
    ("100 ms", 1, 100_000_000),
)
THRESHOLDS = (
    ("+1 ms", lambda baseline: baseline + 1),
    ("+2 ms", lambda baseline: baseline + 2),
    ("+5 ms", lambda baseline: baseline + 5),
    ("+10 ms", lambda baseline: baseline + 10),
    ("×1.25", lambda baseline: baseline * 1.25),
    ("×1.5", lambda baseline: baseline * 1.5),
    ("×2.0", lambda baseline: baseline * 2),
)


def rtt_ms(row):
    value = row["rtt_ns"]
    return None if value is None else value / 1_000_000


def rtt_summary(rows):
    values = [value for row in rows if (value := rtt_ms(row)) is not None]
    if not values:
        raise ValueError("phase has no RTT samples")
    return statistics.median(values), percentile(values, 95)


def false_episodes(rows, predicate, consecutive, duration_ns):
    segments = []
    current = []
    for row in rows:
        if predicate(row):
            current.append(row["wall_time_ns"])
        elif current:
            segments.append(current)
            current = []
    if current:
        segments.append(current)
    return sum(
        len(segment) >= consecutive
        and (duration_ns is None or segment[-1] - segment[0] >= duration_ns)
        for segment in segments
    )


def load_run(root, label):
    prefix = f"stats_calibration_A_{label}"
    paths = {
        "quic": root / f"quic_{prefix}.csv",
        "tbf": root / f"tbf_{prefix}.jsonl",
        "publisher": root / f"publisher_{prefix}.txt",
        "subscriber": root / f"subscriber_{prefix}.txt",
        "subscriber_csv": root / f"subscriber_{prefix}.csv",
        "relay": root / f"relay_{prefix}.txt",
        "analysis": root / f"analysis_{prefix}.txt",
    }
    for path in paths.values():
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"missing or empty run artifact: {path}")
    if "validation: QUIC samples and A-condition delivery OK" not in paths["analysis"].read_text(errors="replace"):
        raise ValueError(f"run lacks successful analysis: {label}")

    samples = load_samples(paths["quic"])
    start_ns, low_ns, recovery_ns = load_tbf(paths["tbf"])
    validate_subscriber(paths["subscriber_csv"])
    phases = {
        "pre": [row for row in samples if start_ns <= row["wall_time_ns"] < low_ns],
        "low": [row for row in samples if low_ns <= row["wall_time_ns"] < recovery_ns],
        "recovery": [row for row in samples if row["wall_time_ns"] >= recovery_ns],
    }
    if any(not rows for rows in phases.values()):
        raise ValueError(f"one or more TBF phases lack samples: {label}")
    pre, low, recovery = (phases[key] for key in ("pre", "low", "recovery"))
    baseline = rtt_summary(pre)[0]
    rate_values = [row["estimated_send_rate_bps"] for row in pre if row["estimated_send_rate_bps"] is not None]
    if not rate_values:
        raise ValueError(f"normal phase has no estimated send rate: {label}")
    baseline_rate = statistics.median(rate_values)
    intervals = [
        (current["monotonic_ns"] - previous["monotonic_ns"]) / 1_000_000
        for previous, current in zip(samples, samples[1:])
    ]
    result = {
        "label": label,
        "phase": {key: rtt_summary(rows) for key, rows in phases.items()},
        "baseline_rtt": baseline,
        "baseline_rate": baseline_rate,
        "interval_median": statistics.median(intervals),
        "detection": {},
        "false_episodes": {},
        "false_samples": {},
        "normal_samples": len(pre),
        "rate_detection": {},
        "recovery_detection": {},
        "loss": {},
    }
    for threshold_name, threshold_fn in THRESHOLDS:
        threshold = threshold_fn(baseline)
        predicate = lambda row, limit=threshold: (value := rtt_ms(row)) is not None and value > limit
        result["false_samples"][threshold_name] = sum(predicate(row) for row in pre)
        for rule_name, consecutive, duration_ns in RULES:
            key = (threshold_name, rule_name)
            result["detection"][key] = first_detection(low, low_ns, predicate, consecutive, duration_ns)
            result["false_episodes"][key] = false_episodes(pre, predicate, consecutive, duration_ns)

    for fraction in (0.9, 0.8, 0.7):
        predicate = lambda row, limit=baseline_rate * fraction: row["estimated_send_rate_bps"] is not None and row["estimated_send_rate_bps"] < limit
        result["rate_detection"][fraction] = first_detection(low, low_ns, predicate)

    for name, limit in (("RTT <= +1 ms", baseline + 1), ("RTT <= +2 ms", baseline + 2), ("RTT <= ×1.25", baseline * 1.25)):
        predicate = lambda row, threshold=limit: (value := rtt_ms(row)) is not None and value <= threshold
        result["recovery_detection"][name] = first_detection(recovery, recovery_ns, predicate)
    predicate = lambda row: row["estimated_send_rate_bps"] is not None and row["estimated_send_rate_bps"] >= baseline_rate * 0.9
    result["recovery_detection"]["rate >= 90%"] = first_detection(recovery, recovery_ns, predicate)

    experiment_samples = pre + low + recovery
    after_low = low + recovery
    for name, column in (("bytes", "bytes_lost_delta"), ("packets", "packets_lost_delta")):
        total = sum(row[column] or 0 for row in experiment_samples)
        predicate = lambda row, key=column: row[key] is not None and row[key] > 0
        result["loss"][name] = (total, first_detection(after_low, low_ns, predicate))
    return result


def value(number):
    return "n/a" if number is None else f"{number:.3f}"


def summary(values):
    present = [number for number in values if number is not None]
    if not present:
        return "n/a (0/5)"
    mean = statistics.fmean(present)
    sd = statistics.stdev(present) if len(present) > 1 else 0.0
    return f"{mean:.3f} ± {sd:.3f} (n={len(present)}/5; min={min(present):.3f}, max={max(present):.3f})"


def row(cells):
    return "| " + " | ".join(str(cell) for cell in cells) + " |"


def render(runs):
    lines = [
        "# QUIC stats calibration, A condition, five runs",
        "",
        "Runs: pilot1, run2, run3, run4, run5. Times are milliseconds after the applied TBF rate change.",
        "Between-run SD uses sample standard deviation (n-1). Missing detections stay n/a and are excluded from the numeric mean.",
        "False-positive episodes count contiguous normal-phase threshold crossings that satisfy each confirmation rule; raw false-positive samples are also shown.",
        "",
        "## Main results by run",
        "",
        row(["Run", "sample interval median", "baseline RTT median", "pre RTT median/p95", "low RTT median/p95", "recovery RTT median/p95", "+1 ms, 3 samples", "+2 ms, 3 samples", "loss bytes/packets"]),
        row(["---"] * 9),
    ]
    for run in runs:
        phase = lambda name: "/".join(value(item) for item in run["phase"][name])
        lines.append(row([
            run["label"], value(run["interval_median"]), value(run["baseline_rtt"]),
            phase("pre"), phase("low"), phase("recovery"),
            value(run["detection"][("+1 ms", "3 samples")]),
            value(run["detection"][("+2 ms", "3 samples")]),
            f"{run['loss']['bytes'][0]}/{run['loss']['packets'][0]}",
        ]))

    lines.extend(["", "## Phase RTT across runs", "", row(["Phase", "RTT median mean ± SD", "RTT p95 mean ± SD"]), row(["---"] * 3)])
    for label, key in (("0-2s 3 Mbps", "pre"), ("2-4s 1 Mbps", "low"), ("4s+ 3 Mbps", "recovery")):
        lines.append(row([label, summary([run["phase"][key][0] for run in runs]), summary([run["phase"][key][1] for run in runs])]))

    lines.extend(["", "## Primary comparison", "", row(["Candidate", *LABELS, "Detection mean ± SD; min/max", "False-positive episodes (total)", "Raw false-positive samples"]), row(["---"] * 9)])
    for threshold_name in ("+1 ms", "+2 ms"):
        key = (threshold_name, "3 samples")
        lines.append(detection_row(runs, key))

    lines.extend(["", "## All RTT detection candidates", "", row(["Threshold", "Rule", *LABELS, "Detection mean ± SD; min/max", "False-positive episodes (total)", "Raw false-positive samples"]), row(["---"] * 10)])
    for threshold_name, _ in THRESHOLDS:
        for rule_name, _, _ in RULES:
            key = (threshold_name, rule_name)
            lines.append(detection_row(runs, key, split_key=True))

    lines.extend(["", "## Estimated send rate, supplementary signal", "", row(["Threshold", *LABELS, "Detection mean ± SD; min/max"]), row(["---"] * 7)])
    for fraction in (0.9, 0.8, 0.7):
        values = [run["rate_detection"][fraction] for run in runs]
        lines.append(row([f"< {fraction:.0%} normal median", *(value(item) for item in values), summary(values)]))

    lines.extend(["", "## Loss", "", row(["Run", "Total bytes lost", "Total packets lost", "First byte-loss increase after low switch", "First packet-loss increase after low switch"]), row(["---"] * 5)])
    for run in runs:
        lines.append(row([run["label"], run["loss"]["bytes"][0], run["loss"]["packets"][0], value(run["loss"]["bytes"][1]), value(run["loss"]["packets"][1])]))
    lines.append(f"Runs with any byte/packet loss: {sum(run['loss']['bytes'][0] > 0 for run in runs)}/5 / {sum(run['loss']['packets'][0] > 0 for run in runs)}/5")

    lines.extend(["", "## Recovery after 3 Mbps restoration", "", row(["Criterion", *LABELS, "Recovery mean ± SD; min/max"]), row(["---"] * 7)])
    for criterion in ("RTT <= +1 ms", "RTT <= +2 ms", "RTT <= ×1.25", "rate >= 90%"):
        values = [run["recovery_detection"][criterion] for run in runs]
        lines.append(row([criterion, *(value(item) for item in values), summary(values)]))
    lines.extend(["", "Loss deltas are assigned to the phase of the reporting sample; a boundary-straddling interval can include earlier events.", "Thresholds are analysis candidates only. No adaptive control or fixed threshold is implemented.", ""])
    return "\n".join(lines)


def detection_row(runs, key, split_key=False):
    values = [run["detection"][key] for run in runs]
    false_total = sum(run["false_episodes"][key] for run in runs)
    false_samples = sum(run["false_samples"][key[0]] for run in runs)
    normal_samples = sum(run["normal_samples"] for run in runs)
    label = [key[0], key[1]] if split_key else [f"{key[0]} × {key[1]}"]
    return row([*label, *(value(item) for item in values), summary(values), false_total, f"{false_samples}/{normal_samples} ({false_samples / normal_samples:.1%})"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = render([load_run(args.root, label) for label in LABELS])
    if args.output is None:
        print(report, end="")
    else:
        with args.output.open("x", encoding="utf-8") as output:
            output.write(report)
        print(f"five-run comparison saved: {args.output}")


if __name__ == "__main__":
    main()
