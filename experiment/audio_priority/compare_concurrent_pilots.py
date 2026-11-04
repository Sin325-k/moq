import csv
import json
import math
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CONDITIONS = ("R", "A", "B")
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
    return sum(values) / len(values), percentile(values, 95), max(values)


def load_condition(condition):
    prefix = f"concurrent_{condition}_3to1to3_pilot1"
    with (ROOT / f"{prefix}.csv").open(newline="") as source:
        rows = list(csv.DictReader(source))
    with (ROOT / f"tbf_{prefix}.jsonl").open(encoding="utf-8") as source:
        events = [json.loads(line) for line in source if line.strip()]
    changes = {event["event"]: event for event in events if event["event"] != "sample"}
    low_ns = changes["low_rate"]["applied_monotonic_ns"]
    recovery_ns = changes["recovery_rate"]["applied_monotonic_ns"]
    audio = [row for row in rows if row["track"] == "audio"]
    background = [row for row in rows if row["track"] == "background"]
    phases = {
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
        "overall": delay_stats(audio),
        **{name: delay_stats(phase) for name, phase in phases.items()},
        "audio_rate": len(audio) / 400 * 100,
        "background_rate": len(background) / 400 * 100 if condition != "R" else None,
        "background_completion": None,
        "pending_at_4s": None,
        "backlog_drain_ms": None,
    }
    if background:
        result["background_completion"] = (
            max(int(row["received_time_ns"]) for row in background)
            - min(int(row["send_time_ns"]) for row in background)
        ) / 1_000_000
        relay = ANSI.sub(
            "",
            (ROOT / f"relay_{prefix}.txt").read_text(errors="replace"),
        )
        recovery = next(
            line
            for line in relay.splitlines()
            if "background three-mbps recovery started" in line
        )
        drained = next(
            line
            for line in relay.splitlines()
            if "background recovery backlog drained" in line
        )
        result["pending_at_4s"] = int(re.search(r"pending=(\d+)", recovery).group(1))
        result["backlog_drain_ms"] = int(
            re.search(r"recovery_ms=(\d+)", drained).group(1)
        )
    return result


def change(after, before):
    absolute = after - before
    percent = absolute / before * 100
    return f"{absolute:+.3f} ms ({percent:+.2f}%)"


def main():
    results = {condition: load_condition(condition) for condition in CONDITIONS}
    print("| Condition | Phase | Audio mean | Audio p95 | Audio max | Audio receive |")
    print("|---|---|---:|---:|---:|---:|")
    phase_labels = (
        ("overall", "overall"),
        ("pre", "0-2s 3 Mbps"),
        ("low", "2-4s 1 Mbps"),
        ("recovery", "4s+ 3 Mbps"),
    )
    for condition in CONDITIONS:
        for key, label in phase_labels:
            mean, p95, maximum = results[condition][key]
            print(
                f"| {condition} | {label} | {mean:.3f} ms | {p95:.3f} ms | "
                f"{maximum:.3f} ms | {results[condition]['audio_rate']:.3f}% |"
            )

    print()
    print("| Condition | Background receive | Completion | Pending at 4s | Drain after recovery |")
    print("|---|---:|---:|---:|---:|")
    for condition in ("A", "B"):
        result = results[condition]
        print(
            f"| {condition} | {result['background_rate']:.3f}% | "
            f"{result['background_completion']:.3f} ms | "
            f"{result['pending_at_4s']} | {result['backlog_drain_ms']} ms |"
        )

    print()
    print(
        "A congestion effect, Audio mean: "
        + change(results["A"]["low"][0], results["A"]["pre"][0])
    )
    print(
        "A congestion effect, Audio p95: "
        + change(results["A"]["low"][1], results["A"]["pre"][1])
    )
    print(
        "B versus A during 1 Mbps, Audio mean: "
        + change(results["B"]["low"][0], results["A"]["low"][0])
    )
    print(
        "B versus A during 1 Mbps, Audio p95: "
        + change(results["B"]["low"][1], results["A"]["low"][1])
    )


if __name__ == "__main__":
    main()
