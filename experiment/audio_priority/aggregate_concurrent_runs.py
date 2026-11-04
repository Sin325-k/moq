import csv
import json
import math
import re
import statistics
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CONDITIONS = ("R", "A", "B")
RUNS = range(1, 6)
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def percentile(values, percentile_value):
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile_value / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def delay_stats(rows):
    values = [float(row["delay_ms"]) for row in rows]
    return {
        "mean": statistics.fmean(values),
        "p95": percentile(values, 95),
        "max": max(values),
    }


def parse_field(line, name):
    match = re.search(rf"{name}=(\d+)", line)
    if match is None:
        raise RuntimeError(f"missing {name}: {line}")
    return int(match.group(1))


def load_run(condition, run):
    prefix = f"concurrent_{condition}_3to1to3_run{run}"
    csv_path = ROOT / f"{prefix}.csv"
    tbf_path = ROOT / f"tbf_{prefix}.jsonl"
    relay_path = ROOT / f"relay_{prefix}.txt"
    analysis_path = ROOT / f"analysis_{prefix}.txt"
    revalidated_path = ROOT / f"analysis_{prefix}_revalidated.txt"
    for path in (csv_path, tbf_path, relay_path, analysis_path):
        if not path.is_file():
            raise RuntimeError(f"missing run artifact: {path}")
    validation_path = (
        revalidated_path if revalidated_path.is_file() else analysis_path
    )
    if "validation:" not in validation_path.read_text(errors="replace"):
        raise RuntimeError(f"run was not validated: {validation_path}")

    with csv_path.open(newline="") as source:
        rows = list(csv.DictReader(source))
    with tbf_path.open(encoding="utf-8") as source:
        events = [json.loads(line) for line in source if line.strip()]
    changes = {event["event"]: event for event in events if event["event"] != "sample"}
    low_ns = int(changes["low_rate"]["applied_monotonic_ns"])
    recovery_ns = int(changes["recovery_rate"]["applied_monotonic_ns"])

    audio = [row for row in rows if row["track"] == "audio"]
    background = [row for row in rows if row["track"] == "background"]
    phases = {
        "overall": audio,
        "pre": [row for row in audio if int(row["send_time_ns"]) < low_ns],
        "low": [
            row
            for row in audio
            if low_ns <= int(row["send_time_ns"]) < recovery_ns
        ],
        "recovery": [
            row for row in audio if int(row["send_time_ns"]) >= recovery_ns
        ],
    }
    result = {
        **{
            f"audio_{phase}_{metric}": value
            for phase, phase_rows in phases.items()
            for metric, value in delay_stats(phase_rows).items()
        },
        "audio_rate": len(audio) / 400 * 100,
    }
    if condition == "R":
        return result

    relay = ANSI.sub("", relay_path.read_text(errors="replace"))
    recovery_line = next(
        line
        for line in relay.splitlines()
        if "background three-mbps recovery started" in line
    )
    drained_line = next(
        line
        for line in relay.splitlines()
        if "background recovery backlog drained" in line
    )
    low_line = next(
        line
        for line in relay.splitlines()
        if "background one-mbps phase started" in line
    )
    pending = parse_field(recovery_line, "pending")
    drain_ms = parse_field(drained_line, "recovery_ms")
    low_marker_ms = parse_field(low_line, "observation_delay_ms")
    recovery_marker_ms = parse_field(recovery_line, "observation_delay_ms")
    completion_ms = (
        max(int(row["received_time_ns"]) for row in background)
        - min(int(row["send_time_ns"]) for row in background)
    ) / 1_000_000
    backlog_end_ns = recovery_ns + (recovery_marker_ms + drain_ms) * 1_000_000
    backlog_audio = [
        row
        for row in audio
        if recovery_ns <= int(row["send_time_ns"]) <= backlog_end_ns
    ]
    if not backlog_audio:
        raise RuntimeError(f"empty backlog-drain Audio window: {prefix}")

    result.update(
        {
            "background_rate": len(background) / 400 * 100,
            "background_completion": completion_ms,
            "background_pending_4s": pending,
            "background_backlog_drain": drain_ms,
            "relay_low_marker_delay": low_marker_ms,
            "relay_recovery_marker_delay": recovery_marker_ms,
            **{
                f"audio_backlog_{metric}": value
                for metric, value in delay_stats(backlog_audio).items()
            },
        }
    )
    return result


def format_summary(values, unit):
    mean = statistics.fmean(values)
    deviation = statistics.pstdev(values)
    return f"{mean:.3f} ± {deviation:.3f}{unit}"


def print_summary(results):
    metrics = [
        ("Audio overall mean", "audio_overall_mean", " ms"),
        ("Audio overall p95", "audio_overall_p95", " ms"),
        ("Audio overall max", "audio_overall_max", " ms"),
        ("Audio 0-2s mean", "audio_pre_mean", " ms"),
        ("Audio 0-2s p95", "audio_pre_p95", " ms"),
        ("Audio 0-2s max", "audio_pre_max", " ms"),
        ("Audio 2-4s mean", "audio_low_mean", " ms"),
        ("Audio 2-4s p95", "audio_low_p95", " ms"),
        ("Audio 2-4s max", "audio_low_max", " ms"),
        ("Audio 4s+ mean", "audio_recovery_mean", " ms"),
        ("Audio 4s+ p95", "audio_recovery_p95", " ms"),
        ("Audio 4s+ max", "audio_recovery_max", " ms"),
        ("Audio receive rate", "audio_rate", "%"),
    ]
    print("## Five-run summary")
    print()
    print("| Metric | R | A | B |")
    print("|---|---:|---:|---:|")
    for label, key, unit in metrics:
        cells = [
            format_summary([results[condition][run][key] for run in RUNS], unit)
            for condition in CONDITIONS
        ]
        print(f"| {label} | {cells[0]} | {cells[1]} | {cells[2]} |")

    background_metrics = [
        ("Background receive rate", "background_rate", "%"),
        ("Background completion", "background_completion", " ms"),
        ("Pending Groups at 4s", "background_pending_4s", ""),
        ("Backlog drain after recovery", "background_backlog_drain", " ms"),
        ("Relay 1 Mbps marker delay", "relay_low_marker_delay", " ms"),
        (
            "Relay 3 Mbps recovery marker delay",
            "relay_recovery_marker_delay",
            " ms",
        ),
        ("Audio backlog-window mean", "audio_backlog_mean", " ms"),
        ("Audio backlog-window p95", "audio_backlog_p95", " ms"),
        ("Audio backlog-window max", "audio_backlog_max", " ms"),
    ]
    print()
    print("| Metric | A | B |")
    print("|---|---:|---:|")
    for label, key, unit in background_metrics:
        a = format_summary([results["A"][run][key] for run in RUNS], unit)
        b = format_summary([results["B"][run][key] for run in RUNS], unit)
        print(f"| {label} | {a} | {b} |")


def print_paired(results):
    print()
    print("## Paired comparisons")
    print()
    print(
        "| Run | A low mean | B low mean | B-A mean | "
        "A low p95 | B low p95 | B-A p95 | B-R mean | B-R p95 | "
        "B-A Background completion |"
    )
    print("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    mean_differences = []
    p95_differences = []
    completion_differences = []
    for run in RUNS:
        r = results["R"][run]
        a = results["A"][run]
        b = results["B"][run]
        mean_difference = b["audio_low_mean"] - a["audio_low_mean"]
        p95_difference = b["audio_low_p95"] - a["audio_low_p95"]
        completion_difference = (
            b["background_completion"] - a["background_completion"]
        )
        mean_differences.append(mean_difference)
        p95_differences.append(p95_difference)
        completion_differences.append(completion_difference)
        print(
            f"| {run} | {a['audio_low_mean']:.3f} | {b['audio_low_mean']:.3f} | "
            f"{mean_difference:+.3f} | {a['audio_low_p95']:.3f} | "
            f"{b['audio_low_p95']:.3f} | {p95_difference:+.3f} | "
            f"{b['audio_low_mean'] - r['audio_low_mean']:+.3f} | "
            f"{b['audio_low_p95'] - r['audio_low_p95']:+.3f} | "
            f"{completion_difference:+.3f} |"
        )

    print()
    print(
        "Paired B-A low Audio mean difference: "
        + format_summary(mean_differences, " ms")
    )
    print(
        "Paired B-A low Audio p95 difference: "
        + format_summary(p95_differences, " ms")
    )
    print(
        "Paired B-A Background completion difference: "
        + format_summary(completion_differences, " ms")
    )
    a_reproduced = sum(
        results["A"][run]["audio_low_mean"]
        > results["R"][run]["audio_low_mean"]
        for run in RUNS
    )
    b_mean_improved = sum(
        results["B"][run]["audio_low_mean"]
        < results["A"][run]["audio_low_mean"]
        for run in RUNS
    )
    b_p95_improved = sum(
        results["B"][run]["audio_low_p95"]
        < results["A"][run]["audio_low_p95"]
        for run in RUNS
    )
    print(f"A low-phase mean exceeded R in {a_reproduced}/5 paired runs.")
    print(f"B improved low-phase mean over A in {b_mean_improved}/5 paired runs.")
    print(f"B improved low-phase p95 over A in {b_p95_improved}/5 paired runs.")


def main():
    results = {
        condition: {
            run: load_run(condition, run)
            for run in RUNS
        }
        for condition in CONDITIONS
    }
    print_summary(results)
    print_paired(results)


if __name__ == "__main__":
    main()
