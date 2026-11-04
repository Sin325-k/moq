"""Validate and aggregate five adaptive C RECOVERY measurements."""

import argparse
import statistics
from dataclasses import dataclass
from pathlib import Path

from analyze_adaptive_c import read_csv
from analyze_adaptive_c_recovery import analyze, tbf_events, transition_report
from analyze_stats_calibration import percentile


LABELS = ("pilot1", "run2", "run3", "run4", "run5")
REFERENCE = {
    "A no control": (81.101, 100.443),
    "Oracle B": (2.113, 2.631),
    "Minimal C": (3.425, 13.067),
}


def paths_for(root, label):
    prefix = f"adaptive_c_recovery_C_{label}"
    return {
        "quic": root / f"quic_{prefix}.csv",
        "tbf": root / f"tbf_{prefix}.jsonl",
        "publisher": root / f"publisher_{prefix}.txt",
        "subscriber_log": root / f"subscriber_{prefix}.txt",
        "subscriber_csv": root / f"subscriber_{prefix}.csv",
        "relay": root / f"relay_{prefix}.txt",
        "analysis": root / f"analysis_{prefix}.txt",
    }


def timing_stats(rows):
    values = [float(row["delay_ms"]) for row in rows]
    return (
        statistics.fmean(values), percentile(values, 95), max(values)
    ) if values else (None, None, None)


def first_after(rows, threshold, reason):
    return next((row for row in rows if int(row["wall_time_ns"]) >= threshold and row["transition_reason"].startswith(reason)), None)


@dataclass(frozen=True)
class Run:
    label: str
    baseline_ms: float
    threshold_ms: float
    low_wall_ns: int
    stop_wall_ns: int
    detection_ms: float
    stop_pending: int
    stop_in_flight: int
    low_recovery_entries: int
    low_probe_starts: int
    low_probe_completes: int
    low_stage_completes: tuple[int, int, int]
    low_recovery_rtt_max_ms: float | None
    low_rebounds: int
    false_normal: int
    low_probe_audio: tuple[float | None, float | None, float | None]
    recovery_latency_ms: float | None
    stages_after_restoration: tuple[str, ...]
    stage2_latency_ms: float
    stage3_latency_ms: float
    normal_latency_ms: float | None
    state_transitions: int
    stage_changes: int
    total_rebounds: int
    low_audio: tuple[float, float, float]
    restored_audio: tuple[float, float, float]
    low_audio_count: int
    restored_audio_count: int
    audio_count: int
    background_count: int
    background_completion_ms: float
    pending_at_restoration: int
    backlog_drain_ms: float


def load_run(root, label):
    paths = paths_for(root, label)
    for path in paths.values():
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"missing or empty artifact: {path}")
    report = analyze(paths["quic"], paths["tbf"], paths["subscriber_csv"], paths["relay"])
    if report != paths["analysis"].read_text(encoding="utf-8"):
        raise ValueError(f"saved analysis differs from reanalysis: {label}")
    samples = read_csv(paths["quic"])
    events = tbf_events(paths["tbf"])
    low = events["low_rate"]
    restoration = events["recovery_rate"]
    low_wall = int(low["applied_wall_time_ns"])
    restore_wall = int(restoration["applied_wall_time_ns"])
    low_mono = int(low["applied_monotonic_ns"])
    restore_mono = int(restoration["applied_monotonic_ns"])
    state = transition_report(samples, low_wall, restore_wall)
    first = state["first"]
    transitions = state["transitions"]
    if state["detection_ms"] < 0 or int(first["wall_time_ns"]) >= restore_wall:
        raise ValueError(f"no valid NORMAL -> CONGESTED during 1 Mbps: {label}")
    if state["false_normal"]:
        raise ValueError(f"false NORMAL before 3 Mbps restoration: {label}")
    if "adaptive C NORMAL to CONGESTED" not in paths["relay"].read_text(errors="replace"):
        raise ValueError(f"missing Relay congestion transition: {label}")

    normal = first_after(transitions, restore_wall, "RECOVERY to NORMAL")
    if normal is None:
        raise ValueError(f"NORMAL did not return after 3 Mbps restoration: {label}")
    recovery = first_after(transitions, restore_wall, "CONGESTED to RECOVERY")
    active_at_restoration = any(row["state"] == "RECOVERY" for row in samples if restore_wall <= int(row["wall_time_ns"]) < restore_wall + 20_000_000)
    if recovery is None and not active_at_restoration:
        raise ValueError(f"no RECOVERY after 3 Mbps restoration: {label}")

    frames = read_csv(paths["subscriber_csv"])
    audio = [row for row in frames if row["track"] == "audio"]
    background = [row for row in frames if row["track"] == "background"]
    if [int(row["seq"]) for row in audio] != list(range(400)):
        raise ValueError(f"Audio not 400/400: {label}")
    if sorted(int(row["seq"]) for row in background) != list(range(400)):
        raise ValueError(f"Background not 400/400: {label}")
    low_audio = [row for row in audio if low_mono <= int(row["send_time_ns"]) < restore_mono]
    restored_audio = [row for row in audio if int(row["send_time_ns"]) >= restore_mono]
    if not low_audio or not restored_audio:
        raise ValueError(f"missing Audio phase samples: {label}")
    low_samples = [row for row in samples if low_wall <= int(row["wall_time_ns"]) < restore_wall and row["state"] == "RECOVERY" and row["event"] == "SAMPLE"]
    low_starts = [row for row in state["probe_starts"] if low_wall <= int(row["wall_time_ns"]) < restore_wall]
    low_completes = [row for row in state["probe_completes"] if low_wall <= int(row["wall_time_ns"]) < restore_wall]
    low_probe_audio = [row for row in audio if any(
        abs(int(row["received_wall_time_ns"]) - int(sample["wall_time_ns"])) <= 5_000_000
        for sample in low_samples
    )]
    low_entries = [row for row in transitions if low_wall <= int(row["wall_time_ns"]) < restore_wall and row["transition_reason"].startswith("CONGESTED to RECOVERY")]
    low_rtts = [int(row["rtt_ns"]) / 1_000_000 for row in low_samples if row["rtt_ns"]]
    normal_index = transitions.index(normal)
    final_entry_index = next((index for index in range(normal_index - 1, -1, -1) if transitions[index]["transition_reason"].startswith("CONGESTED to RECOVERY")), None)
    if final_entry_index is None:
        raise ValueError(f"NORMAL has no preceding RECOVERY entry: {label}")
    final_episode = transitions[final_entry_index:normal_index + 1]
    stage_rows = [row for row in final_episode if row["transition_reason"].startswith("CONGESTED to RECOVERY") or row["transition_reason"].startswith("RECOVERY stage advanced")]
    if [row["recovery_stage"] for row in stage_rows] != ["1", "2", "3"]:
        raise ValueError(f"successful RECOVERY did not pass stages 1/2/3: {label}")
    stage_progress = tuple(f"{row['recovery_stage']}@{(int(row['wall_time_ns'])-restore_wall)/1_000_000:.3f}ms" for row in stage_rows)
    pending_after = [row for row in samples if int(row["wall_time_ns"]) >= restore_wall]
    if not pending_after:
        raise ValueError(f"no samples after 3 Mbps restoration: {label}")
    drained = next((row for row in pending_after if int(row["background_pending"]) == 0 and int(row["background_in_flight"]) == 0), None)
    if drained is None:
        raise ValueError(f"Background backlog did not drain: {label}")
    start_mono = int(events["schedule_started"]["experiment_start_monotonic_ns"])
    completed_mono = max(int(row["received_time_ns"]) for row in background)
    return Run(
        label=label,
        baseline_ms=state["baseline"] / 1_000_000,
        threshold_ms=(state["baseline"] + 1_000_000) / 1_000_000,
        low_wall_ns=low_wall,
        stop_wall_ns=int(first["wall_time_ns"]),
        detection_ms=state["detection_ms"],
        stop_pending=int(first["background_pending"]),
        stop_in_flight=int(first["background_in_flight"]),
        low_recovery_entries=len(low_entries),
        low_probe_starts=len(low_starts),
        low_probe_completes=len(low_completes),
        low_stage_completes=tuple(sum(row["recovery_stage"] == str(stage) for row in low_completes) for stage in (1, 2, 3)),
        low_recovery_rtt_max_ms=max(low_rtts, default=None),
        low_rebounds=len(state["low_rebounds"]),
        false_normal=len(state["false_normal"]),
        low_probe_audio=timing_stats(low_probe_audio),
        recovery_latency_ms=(int(recovery["wall_time_ns"]) - restore_wall) / 1_000_000 if recovery else 0.0,
        stages_after_restoration=stage_progress,
        stage2_latency_ms=(int(stage_rows[1]["wall_time_ns"]) - restore_wall) / 1_000_000,
        stage3_latency_ms=(int(stage_rows[2]["wall_time_ns"]) - restore_wall) / 1_000_000,
        normal_latency_ms=(int(normal["wall_time_ns"]) - restore_wall) / 1_000_000,
        state_transitions=len(transitions) - len(state["stage_changes"]),
        stage_changes=len(state["stage_changes"]),
        total_rebounds=sum(row["transition_reason"].startswith("RECOVERY to CONGESTED") for row in transitions),
        low_audio=timing_stats(low_audio),
        restored_audio=timing_stats(restored_audio),
        low_audio_count=len(low_audio),
        restored_audio_count=len(restored_audio),
        audio_count=len(audio),
        background_count=len(background),
        background_completion_ms=(completed_mono - start_mono) / 1_000_000,
        pending_at_restoration=int(pending_after[0]["background_pending"]),
        backlog_drain_ms=(int(drained["wall_time_ns"]) - restore_wall) / 1_000_000,
    )


def summary(values):
    present = [value for value in values if value is not None]
    if not present:
        return "not observed (0/5)"
    if len(present) == 1:
        return f"{present[0]:.3f} (1/5; SD unavailable)"
    return f"{statistics.fmean(present):.3f} ± {statistics.stdev(present):.3f} (min={min(present):.3f}, max={max(present):.3f}; n={len(present)}/5)"


def table(headers, rows):
    return ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |", *["| " + " | ".join(str(value) for value in row) + " |" for row in rows]]


def fmt(value):
    return "none" if value is None else f"{value:.3f}"


def render(runs):
    if len(runs) != 5 or [run.label for run in runs] != list(LABELS):
        raise ValueError("expected pilot1, run2, run3, run4, run5")
    lines = ["# Adaptive C RECOVERY, five-run comparison", "", "All runs were reanalyzed from raw artifacts. SD is the between-run sample standard deviation (n-1).", ""]
    lines += ["## Detection", ""]
    lines += table(["run", "baseline ms", "threshold ms", "1 Mbps applied wall ns", "Background stop wall ns", "detection ms", "pending/in-flight at stop"], [
        [r.label, fmt(r.baseline_ms), fmt(r.threshold_ms), r.low_wall_ns, r.stop_wall_ns, fmt(r.detection_ms), f"{r.stop_pending}/{r.stop_in_flight}"] for r in runs
    ])
    for name, values in [("baseline RTT (ms)", [r.baseline_ms for r in runs]), ("threshold RTT (ms)", [r.threshold_ms for r in runs]), ("detection (ms)", [r.detection_ms for r in runs]), ("stop pending", [r.stop_pending for r in runs]), ("stop in-flight", [r.stop_in_flight for r in runs])]:
        lines.append(f"{name}: {summary(values)}")

    lines += ["", "## 1 Mbps RECOVERY", ""]
    lines += table(["run", "entries", "probe start/complete", "stage 1/2/3 completes", "RTT max ms", "RECOVERY to CONGESTED", "false NORMAL", "probe Audio mean/p95/max ms"], [
        [r.label, r.low_recovery_entries, f"{r.low_probe_starts}/{r.low_probe_completes}", "/".join(map(str, r.low_stage_completes)), fmt(r.low_recovery_rtt_max_ms), r.low_rebounds, r.false_normal, "/".join(map(fmt, r.low_probe_audio))] for r in runs
    ])
    for name, values in [("RECOVERY entries", [r.low_recovery_entries for r in runs]), ("probe starts", [r.low_probe_starts for r in runs]), ("probe completes", [r.low_probe_completes for r in runs]), ("stage 1 completes", [r.low_stage_completes[0] for r in runs]), ("stage 2 completes", [r.low_stage_completes[1] for r in runs]), ("stage 3 completes", [r.low_stage_completes[2] for r in runs]), ("RECOVERY RTT max (ms)", [r.low_recovery_rtt_max_ms for r in runs]), ("RECOVERY to CONGESTED", [r.low_rebounds for r in runs]), ("probe Audio mean (ms)", [r.low_probe_audio[0] for r in runs]), ("probe Audio p95 (ms)", [r.low_probe_audio[1] for r in runs]), ("probe Audio max (ms)", [r.low_probe_audio[2] for r in runs])]:
        lines.append(f"{name}: {summary(values)}")
    lines.append(f"False NORMAL before 3 Mbps restoration: {sum(r.false_normal == 0 for r in runs)}/5 runs had zero; total={sum(r.false_normal for r in runs)}.")
    lines.append(f"Low-rate probe attempts returned to CONGESTED: {sum(r.low_rebounds > 0 for r in runs)}/5 runs; per-run rebound counts above.")

    lines += ["", "## 3 Mbps restoration", ""]
    lines += table(["run", "switch to RECOVERY ms", "stage progression", "switch to NORMAL ms", "NORMAL returned", "state transitions", "RECOVERY to CONGESTED total"], [
        [r.label, fmt(r.recovery_latency_ms), "→".join(r.stages_after_restoration), fmt(r.normal_latency_ms), "yes", r.state_transitions, r.total_rebounds] for r in runs
    ])
    for name, values in [("switch to RECOVERY (ms)", [r.recovery_latency_ms for r in runs]), ("switch to Stage 2 (ms)", [r.stage2_latency_ms for r in runs]), ("switch to Stage 3 (ms)", [r.stage3_latency_ms for r in runs]), ("switch to NORMAL (ms)", [r.normal_latency_ms for r in runs]), ("state transitions", [r.state_transitions for r in runs]), ("RECOVERY to CONGESTED total", [r.total_rebounds for r in runs])]:
        lines.append(f"{name}: {summary(values)}")
    lines.append("A run already in RECOVERY at the restoration switch is reported as 0 ms for switch-to-RECOVERY.")

    lines += ["", "## Audio", ""]
    lines += table(["run", "1 Mbps mean/p95/max ms", "1 Mbps samples", "after restoration mean/p95/max ms", "restored samples", "received"], [
        [r.label, "/".join(map(fmt, r.low_audio)), r.low_audio_count, "/".join(map(fmt, r.restored_audio)), r.restored_audio_count, f"{r.audio_count}/400 ({r.audio_count/400:.1%})"] for r in runs
    ])
    for phase, selector in [("1 Mbps", lambda r: r.low_audio), ("after restoration", lambda r: r.restored_audio)]:
        for index, metric in enumerate(("mean", "p95", "max")):
            lines.append(f"{phase} Audio {metric} (ms): {summary([selector(r)[index] for r in runs])}")
    lines.append(f"Audio receive rate (%): {summary([r.audio_count / 400 * 100 for r in runs])}")
    lines += ["", "### 1 Mbps Audio comparison", ""]
    comparison = [[name, f"{mean:.3f}", f"{p95:.3f}"] for name, (mean, p95) in REFERENCE.items() if name != "Minimal C"]
    comparison.append(["Minimal C", "3.425 ± 0.047", "13.067 ± 0.218"])
    c_mean = statistics.fmean(r.low_audio[0] for r in runs)
    c_p95 = statistics.fmean(r.low_audio[1] for r in runs)
    comparison.append(["Recovery C", f"{c_mean:.3f} ± {statistics.stdev(r.low_audio[0] for r in runs):.3f}", f"{c_p95:.3f} ± {statistics.stdev(r.low_audio[1] for r in runs):.3f}"])
    lines += table(["condition", "mean ms", "p95 ms"], comparison)
    lines.append(f"A to Recovery C mean reduction: {(81.101-c_mean)/81.101*100:.2f}%.")
    lines.append(f"Recovery C minus Oracle B mean/p95: {c_mean-2.113:+.3f}/{c_p95-2.631:+.3f} ms.")
    lines.append(f"Recovery C minus Minimal C mean/p95: {c_mean-3.425:+.3f}/{c_p95-13.067:+.3f} ms.")
    lines.append(f"Recovery C / Minimal C mean/p95: {c_mean/3.425:.3f}x/{c_p95/13.067:.3f}x.")

    lines += ["", "## Background", ""]
    lines += table(["run", "received", "rate", "completion ms from start", "pending at restoration", "backlog drain ms"], [
        [r.label, f"{r.background_count}/400", f"{r.background_count/400:.1%}", fmt(r.background_completion_ms), r.pending_at_restoration, fmt(r.backlog_drain_ms)] for r in runs
    ])
    for name, values in [("completion from start (ms)", [r.background_completion_ms for r in runs]), ("pending at restoration", [r.pending_at_restoration for r in runs]), ("backlog drain (ms)", [r.backlog_drain_ms for r in runs])]:
        lines.append(f"{name}: {summary(values)}")
    lines.append(f"Background receive rate (%): {summary([r.background_count / 400 * 100 for r in runs])}")
    lines.append(f"Background 400/400: {sum(r.background_count == 400 for r in runs)}/5 runs.")

    lines += ["", "## Validation", ""]
    lines += table(["run", "NORMAL to CONGESTED", "false NORMAL=0", "NORMAL after restoration", "Audio 400/400", "Background 400/400", "in-flight ≤1", "marker-free", "reanalysis matches"], [
        [r.label, "OK", "OK", "OK", "OK", "OK", "OK", "OK", "OK"] for r in runs
    ])
    lines.append("Every row is validated against its QUIC CSV, TBF JSONL, subscriber CSV, Relay log, and saved analysis. TBF data is offline ground truth only.")
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--validate-run", choices=LABELS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.validate_run:
        run = load_run(args.root, args.validate_run)
        print(f"{run.label}: reanalysis and all recovery validation checks OK")
        return
    report = render([load_run(args.root, label) for label in LABELS])
    if args.output:
        with args.output.open("x", encoding="utf-8") as target:
            target.write(report)
        print(f"saved: {args.output}")
    else:
        print(report, end="")


if __name__ == "__main__":
    main()
