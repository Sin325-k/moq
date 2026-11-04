"""Aggregate five minimal adaptive C runs without changing their raw artifacts."""

import argparse
import re
import statistics
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from analyze_adaptive_c import read_csv, tbf_boundaries
from analyze_stats_calibration import percentile


LABELS = ("pilot1", "run2", "run3", "run4", "run5")
REFERENCE = {
    "R Audio-only": (1.995, 2.572),
    "A no control": (81.101, 100.443),
    "Oracle B": (2.113, 2.631),
}


@dataclass(frozen=True)
class Run:
    label: str
    baseline_ms: float
    threshold_ms: float
    low_wall_ns: int
    transition_wall_ns: int
    stop_wall_ns: int
    detection_ms: float
    stop_pending: int
    stop_in_flight: int
    max_pending: int
    audio_mean_ms: float
    audio_p95_ms: float
    audio_max_ms: float
    audio_count: int
    background_count: int


def paths_for(root, label):
    prefix = f"adaptive_c_C_{label}"
    return {
        "quic": root / f"quic_{prefix}.csv",
        "tbf": root / f"tbf_{prefix}.jsonl",
        "publisher": root / f"publisher_{prefix}.txt",
        "subscriber": root / f"subscriber_{prefix}.txt",
        "subscriber_csv": root / f"subscriber_{prefix}.csv",
        "relay": root / f"relay_{prefix}.txt",
        "analysis": root / f"analysis_{prefix}.txt",
    }


def load_run(root, label):
    paths = paths_for(root, label)
    for path in paths.values():
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"missing or empty artifact: {path}")

    analyzer = root / "analyze_adaptive_c.py"
    result = subprocess.run(
        [sys.executable, str(analyzer), str(paths["quic"]), str(paths["tbf"]), str(paths["subscriber_csv"])],
        check=True,
        text=True,
        capture_output=True,
    )
    saved_analysis = paths["analysis"].read_text(encoding="utf-8").strip()
    if result.stdout.strip() != saved_analysis or not saved_analysis.endswith("validation: C transition and Audio delivery OK"):
        raise ValueError(f"analyzer validation or saved analysis differs: {label}")

    relay = paths["relay"].read_text(errors="replace")
    if "adaptive C NORMAL to CONGESTED" not in relay:
        raise ValueError(f"Relay has no C transition log: {label}")
    if "background one-mbps phase started" in relay or "background three-mbps recovery started" in relay:
        raise ValueError(f"Relay observed an Oracle TBF marker: {label}")

    samples = read_csv(paths["quic"])
    transitions = [row for row in samples if row["transition_reason"]]
    if len(transitions) != 1:
        raise ValueError(f"expected one C transition: {label}")
    transition = transitions[0]
    low_wall, low_mono, recovery_mono = tbf_boundaries(paths["tbf"])
    transition_wall = int(transition["transition_wall_time_ns"])
    match = re.search(r"^Background new-Group stop wall_time_ns: (\d+)$", saved_analysis, re.MULTILINE)
    if match is None:
        raise ValueError(f"analysis has no Background stop time: {label}")
    stop_wall = int(match.group(1))
    if stop_wall != transition_wall:
        raise ValueError(f"Background stop did not coincide with detection: {label}")
    baseline_ms = int(transition["baseline_rtt_ns"]) / 1_000_000
    threshold_ms = int(transition["threshold_rtt_ns"]) / 1_000_000
    if abs(threshold_ms - baseline_ms - 1) > 0.000001:
        raise ValueError(f"C threshold is not baseline +1 ms: {label}")
    pending = int(transition["background_pending"])
    in_flight = int(transition["background_in_flight"])
    if in_flight > 1:
        raise ValueError(f"Background in-flight exceeded one at stop: {label}")

    rows = read_csv(paths["subscriber_csv"])
    audio = [row for row in rows if row["track"] == "audio"]
    background = [row for row in rows if row["track"] == "background"]
    if [int(row["seq"]) for row in audio] != list(range(400)):
        raise ValueError(f"Audio is not 400/400 in sequence: {label}")
    if len(background) != len({int(row["seq"]) for row in background}):
        raise ValueError(f"Background has duplicate frames: {label}")
    low_audio = [float(row["delay_ms"]) for row in audio if low_mono <= int(row["send_time_ns"]) < recovery_mono]
    if not low_audio:
        raise ValueError(f"no 1 Mbps Audio frames: {label}")

    return Run(
        label=label,
        baseline_ms=baseline_ms,
        threshold_ms=threshold_ms,
        low_wall_ns=low_wall,
        transition_wall_ns=transition_wall,
        stop_wall_ns=stop_wall,
        detection_ms=(transition_wall - low_wall) / 1_000_000,
        stop_pending=pending,
        stop_in_flight=in_flight,
        max_pending=max(int(row["background_pending"]) for row in samples),
        audio_mean_ms=statistics.fmean(low_audio),
        audio_p95_ms=percentile(low_audio, 95),
        audio_max_ms=max(low_audio),
        audio_count=len(audio),
        background_count=len(background),
    )


def summary(values):
    return (
        f"{statistics.fmean(values):.3f} ± {statistics.stdev(values):.3f} "
        f"(min={min(values):.3f}, max={max(values):.3f})"
    )


def table_row(values):
    return "| " + " | ".join(str(value) for value in values) + " |"


def render(runs):
    lines = [
        "# Minimal adaptive C, five-run comparison",
        "",
        "Runs: pilot1, run2, run3, run4, run5. SD is the between-run sample standard deviation (n-1).",
        "Wall timestamps are Unix nanoseconds. Detection latency is measured from the applied 1 Mbps TBF change using offline ground truth.",
        "",
        "## Detection",
        "",
        table_row(["Run", "baseline RTT median (ms)", "threshold RTT (ms)", "1 Mbps applied wall ns", "CONGESTED wall ns", "detection latency (ms)"]),
        table_row(["---"] * 6),
    ]
    for run in runs:
        lines.append(table_row([run.label, f"{run.baseline_ms:.3f}", f"{run.threshold_ms:.3f}", run.low_wall_ns, run.transition_wall_ns, f"{run.detection_ms:.3f}"]))
    for name, values in (
        ("baseline RTT median (ms)", [run.baseline_ms for run in runs]),
        ("threshold RTT (ms)", [run.threshold_ms for run in runs]),
        ("switch-to-detection (ms)", [run.detection_ms for run in runs]),
    ):
        lines.append(f"{name}: {summary(values)}")
    lines.append(f"Detected after 1 Mbps switch: {sum(run.detection_ms >= 0 for run in runs)}/5 runs.")

    lines.extend(["", "## Background stop", "", table_row(["Run", "new-Group stop wall ns", "same detection tick", "pending at stop", "in-flight at stop", "maximum pending"]), table_row(["---"] * 6)])
    for run in runs:
        lines.append(table_row([run.label, run.stop_wall_ns, "yes" if run.stop_wall_ns == run.transition_wall_ns else "no", run.stop_pending, run.stop_in_flight, run.max_pending]))
    lines.append(f"pending at stop: {summary([run.stop_pending for run in runs])}")
    lines.append(f"in-flight at stop: {summary([run.stop_in_flight for run in runs])}")
    lines.append(f"maximum pending: {summary([run.max_pending for run in runs])}")

    lines.extend(["", "## Audio, 1 Mbps phase", "", table_row(["Run", "mean (ms)", "p95 (ms)", "max (ms)", "received", "A-to-C mean reduction", "C minus B mean (ms)"]), table_row(["---"] * 7)])
    for run in runs:
        reduction = (REFERENCE["A no control"][0] - run.audio_mean_ms) / REFERENCE["A no control"][0] * 100
        lines.append(table_row([run.label, f"{run.audio_mean_ms:.3f}", f"{run.audio_p95_ms:.3f}", f"{run.audio_max_ms:.3f}", f"{run.audio_count}/400 ({run.audio_count / 400:.1%})", f"{reduction:.2f}%", f"{run.audio_mean_ms - REFERENCE['Oracle B'][0]:+.3f}"]))
    for name, values in (
        ("Audio mean (ms)", [run.audio_mean_ms for run in runs]),
        ("Audio p95 (ms)", [run.audio_p95_ms for run in runs]),
        ("Audio max (ms)", [run.audio_max_ms for run in runs]),
        ("Audio receive rate (%)", [run.audio_count / 400 * 100 for run in runs]),
    ):
        lines.append(f"{name}: {summary(values)}")
    mean_c = statistics.fmean(run.audio_mean_ms for run in runs)
    p95_c = statistics.fmean(run.audio_p95_ms for run in runs)
    reduction_c = (REFERENCE["A no control"][0] - mean_c) / REFERENCE["A no control"][0] * 100
    reduction_runs = [(REFERENCE["A no control"][0] - run.audio_mean_ms) / REFERENCE["A no control"][0] * 100 for run in runs]
    lines.append(f"A-to-C mean latency reduction: {reduction_c:.2f}% (per-run {summary(reduction_runs)}%).")
    lines.append(f"C minus Oracle B: mean {mean_c - REFERENCE['Oracle B'][0]:+.3f} ms; p95 {p95_c - REFERENCE['Oracle B'][1]:+.3f} ms.")

    lines.extend(["", "## Background", "", table_row(["Run", "received", "receive rate", "maximum pending"]), table_row(["---"] * 4)])
    for run in runs:
        lines.append(table_row([run.label, f"{run.background_count}/400", f"{run.background_count / 400:.1%}", run.max_pending]))
    lines.append(f"Background receive rate (%): {summary([run.background_count / 400 * 100 for run in runs])}")
    lines.append("Background completion time is not comparable with Oracle B because minimal C has no RECOVERY or Background resume policy.")

    lines.extend(["", "## R/A/B/C comparison, 1 Mbps Audio", "", table_row(["Condition", "mean (ms)", "p95 (ms)"]), table_row(["---"] * 3)])
    for name, (mean, p95) in REFERENCE.items():
        lines.append(table_row([name, f"{mean:.3f}", f"{p95:.3f}"]))
    lines.append(table_row(["C five-run mean", f"{mean_c:.3f}", f"{p95_c:.3f}"]))
    lines.append("R/A/B values are the prior five-run summaries; their raw data is not reprocessed here.")

    lines.extend(["", "## Validation", "", table_row(["Run", "C transition", "Audio 400/400", "TBF marker-free Relay log", "analyzer revalidation", "stop at detection tick"]), table_row(["---"] * 6)])
    for run in runs:
        lines.append(table_row([run.label, "OK", "OK", "OK", "OK", "OK"]))
    lines.append("The C runner removes Oracle marker environment variables; the Relay log contains a C transition and no Oracle phase events. TBF JSONL is read only after the run.")
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    runs = [load_run(args.root, label) for label in LABELS]
    report = render(runs)
    if args.output is None:
        print(report, end="")
    else:
        with args.output.open("x", encoding="utf-8") as output:
            output.write(report)
        print(f"five-run comparison saved: {args.output}")


if __name__ == "__main__":
    main()
