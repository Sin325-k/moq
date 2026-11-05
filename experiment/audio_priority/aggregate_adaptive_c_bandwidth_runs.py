"""Revalidate and aggregate the D1/D2 Adaptive C bandwidth runs."""

import argparse
import statistics
from dataclasses import dataclass
from pathlib import Path

from aggregate_adaptive_c_recovery_runs import LABELS, load_run as load_1mbps_run
from analyze_adaptive_c import read_csv
from analyze_adaptive_c_bandwidth import LOW_KBIT, analyze, tbf_events
from analyze_stats_calibration import percentile


NAMES = {"D1": "D1_0p8mbps", "D2": "D2_1p2mbps"}


def paths_for(root, condition, label):
    stem = f"adaptive_c_{NAMES[condition]}_{label}"
    return {
        "quic": root / f"quic_{stem}.csv",
        "tbf": root / f"tbf_{stem}.jsonl",
        "publisher": root / f"publisher_{stem}.txt",
        "subscriber_log": root / f"subscriber_{stem}.txt",
        "subscriber_csv": root / f"subscriber_{stem}.csv",
        "relay": root / f"relay_{stem}.txt",
        "analysis": root / f"analysis_{stem}.txt",
    }


def delays(rows):
    numbers = [float(row["delay_ms"]) for row in rows]
    return (statistics.fmean(numbers), percentile(numbers, 95), max(numbers)) if numbers else (None, None, None)


@dataclass(frozen=True)
class Run:
    label: str
    baseline_ms: float | None
    threshold_ms: float | None
    detection_ms: float | None
    stop_pending: int | None
    stop_in_flight: int | None
    audio_low: tuple[float | None, float | None, float | None]
    audio_restored: tuple[float | None, float | None, float | None]
    audio_count: int
    low_entries: int
    low_probe_starts: int
    low_probe_completes: int
    low_stage_completes: tuple[int, int, int]
    low_rebounds: int
    false_normal: int
    low_rtt_max_ms: float | None
    recovery_latency_ms: float | None
    stages: tuple[str, ...]
    stage2_latency_ms: float | None
    stage3_latency_ms: float | None
    normal_latency_ms: float | None
    background_count: int
    pending_at_restoration: int | None
    completion_ms: float | None
    backlog_drain_ms: float | None


def load_run(root, condition, label):
    paths = paths_for(root, condition, label)
    for path in paths.values():
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"missing or empty artifact: {path}")
    regenerated = analyze(condition, paths["quic"], paths["tbf"], paths["subscriber_csv"], paths["relay"])
    if regenerated != paths["analysis"].read_text(encoding="utf-8"):
        raise ValueError(f"saved analyzer output differs from raw reanalysis: {condition} {label}")

    events = tbf_events(paths["tbf"], LOW_KBIT[condition])
    low_wall = int(events["low_rate"]["applied_wall_time_ns"])
    restore_wall = int(events["recovery_rate"]["applied_wall_time_ns"])
    low_mono = int(events["low_rate"]["applied_monotonic_ns"])
    restore_mono = int(events["recovery_rate"]["applied_monotonic_ns"])
    samples = read_csv(paths["quic"])
    transitions = [row for row in samples if row["transition_reason"]]
    first = next((row for row in transitions if row["transition_reason"] == "RTT above baseline +1 ms for 3 samples"), None)
    if first is None or not low_wall <= int(first["wall_time_ns"]) < restore_wall:
        raise ValueError(f"NORMAL -> CONGESTED missing in bottleneck phase: {condition} {label}")
    baseline_ns = int(first["baseline_rtt_ns"])
    if int(first["threshold_rtt_ns"]) != baseline_ns + 1_000_000 or int(first["consecutive_high"]) != 3:
        raise ValueError(f"wrong congestion threshold or sample count: {condition} {label}")
    false_normal = [row for row in transitions if int(row["wall_time_ns"]) < restore_wall and row["transition_reason"].startswith("RECOVERY to NORMAL")]
    if false_normal:
        raise ValueError(f"false NORMAL before restoration: {condition} {label}")
    if max(int(row["background_in_flight"]) for row in samples) > 1:
        raise ValueError(f"Background in-flight exceeded 1: {condition} {label}")
    relay = paths["relay"].read_text(errors="replace")
    if "adaptive C NORMAL to CONGESTED" not in relay or any(event in relay for event in (
        "background one-mbps phase started", "background three-mbps recovery started", "tbf_applied_wall_time_ns"
    )):
        raise ValueError(f"Relay transition missing or Oracle marker seen: {condition} {label}")

    frames = read_csv(paths["subscriber_csv"])
    audio = [row for row in frames if row["track"] == "audio"]
    background = [row for row in frames if row["track"] == "background"]
    if sorted(int(row["seq"]) for row in audio) != list(range(400)):
        raise ValueError(f"Audio is not 400/400: {condition} {label}")
    if sorted(int(row["seq"]) for row in background) != list(range(400)):
        raise ValueError(f"Background is not 400/400: {condition} {label}")
    if any(int(row["size"]) != 1945 for row in frames):
        raise ValueError(f"payload size differs from 1945: {condition} {label}")
    normal = next((row for row in transitions if int(row["wall_time_ns"]) >= restore_wall and row["transition_reason"].startswith("RECOVERY to NORMAL")), None)
    if normal is None:
        raise ValueError(f"NORMAL did not return after restoration: {condition} {label}")

    normal_index = transitions.index(normal)
    final_entry_index = next((index for index in range(normal_index - 1, -1, -1) if transitions[index]["transition_reason"].startswith("CONGESTED to RECOVERY")), None)
    if final_entry_index is None:
        raise ValueError(f"NORMAL has no RECOVERY entry: {condition} {label}")
    stage_rows = [row for row in transitions[final_entry_index:normal_index] if row["transition_reason"].startswith(("CONGESTED to RECOVERY", "RECOVERY stage advanced"))]
    if [row["recovery_stage"] for row in stage_rows] != ["1", "2", "3"]:
        raise ValueError(f"successful RECOVERY did not pass stages 1/2/3: {condition} {label}")
    post_entry = next((row for row in transitions if int(row["wall_time_ns"]) >= restore_wall and row["transition_reason"].startswith("CONGESTED to RECOVERY")), None)
    active_at_restoration = any(row["state"] == "RECOVERY" for row in samples if restore_wall <= int(row["wall_time_ns"]) < restore_wall + 20_000_000)
    if post_entry is None and not active_at_restoration:
        raise ValueError(f"RECOVERY absent after restoration: {condition} {label}")

    low_audio = [row for row in audio if low_mono <= int(row["send_time_ns"]) < restore_mono]
    restored_audio = [row for row in audio if int(row["send_time_ns"]) >= restore_mono]
    if not low_audio or not restored_audio:
        raise ValueError(f"Audio missing in a TBF phase: {condition} {label}")
    low_entries = [row for row in transitions if low_wall <= int(row["wall_time_ns"]) < restore_wall and row["transition_reason"].startswith("CONGESTED to RECOVERY")]
    low_rebounds = [row for row in transitions if low_wall <= int(row["wall_time_ns"]) < restore_wall and row["transition_reason"].startswith("RECOVERY to CONGESTED")]
    low_starts = [row for row in samples if row["event"] == "GROUP_START" and row["state"] == "RECOVERY" and low_wall <= int(row["wall_time_ns"]) < restore_wall]
    low_completes = [row for row in samples if row["event"] == "GROUP_COMPLETE" and row["state"] == "RECOVERY" and low_wall <= int(row["wall_time_ns"]) < restore_wall]
    low_rtt = [int(row["rtt_ns"]) / 1_000_000 for row in samples if row["event"] == "SAMPLE" and row["state"] == "RECOVERY" and row["rtt_ns"] and low_wall <= int(row["wall_time_ns"]) < restore_wall]
    after_samples = [row for row in samples if int(row["wall_time_ns"]) >= restore_wall]
    if not after_samples:
        raise ValueError(f"no QUIC samples after restoration: {condition} {label}")
    drained = next((row for row in after_samples if int(row["background_pending"]) == 0 and int(row["background_in_flight"]) == 0), None)
    if drained is None:
        raise ValueError(f"backlog did not drain: {condition} {label}")
    start_mono = int(events["schedule_started"]["experiment_start_monotonic_ns"])
    completed_mono = max(int(row["received_time_ns"]) for row in background)
    stage_latency = lambda row: (int(row["wall_time_ns"]) - restore_wall) / 1_000_000
    return Run(
        label=label,
        baseline_ms=baseline_ns / 1_000_000,
        threshold_ms=(baseline_ns + 1_000_000) / 1_000_000,
        detection_ms=(int(first["wall_time_ns"]) - low_wall) / 1_000_000,
        stop_pending=int(first["background_pending"]),
        stop_in_flight=int(first["background_in_flight"]),
        audio_low=delays(low_audio),
        audio_restored=delays(restored_audio),
        audio_count=len(audio),
        low_entries=len(low_entries),
        low_probe_starts=len(low_starts),
        low_probe_completes=len(low_completes),
        low_stage_completes=tuple(sum(row["recovery_stage"] == str(stage) for row in low_completes) for stage in (1, 2, 3)),
        low_rebounds=len(low_rebounds),
        false_normal=len(false_normal),
        low_rtt_max_ms=max(low_rtt, default=None),
        recovery_latency_ms=stage_latency(post_entry) if post_entry else 0.0,
        stages=tuple(f"{row['recovery_stage']}@{stage_latency(row):.3f}ms" for row in stage_rows),
        stage2_latency_ms=stage_latency(stage_rows[1]),
        stage3_latency_ms=stage_latency(stage_rows[2]),
        normal_latency_ms=stage_latency(normal),
        background_count=len(background),
        pending_at_restoration=int(after_samples[0]["background_pending"]),
        completion_ms=(completed_mono - start_mono) / 1_000_000,
        backlog_drain_ms=(int(drained["wall_time_ns"]) - restore_wall) / 1_000_000,
    )


def fmt(value):
    return "none" if value is None else f"{value:.3f}"


def summary(values):
    present = [value for value in values if value is not None]
    if not present:
        return "not observed (0/5)"
    if len(present) == 1:
        return f"{present[0]:.3f} (1/5; SD unavailable)"
    return f"{statistics.fmean(present):.3f} ± {statistics.stdev(present):.3f} (min={min(present):.3f}, max={max(present):.3f}; n={len(present)}/5)"


def table(headers, data):
    return ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |", *["| " + " | ".join(str(item) for item in row) + " |" for row in data]]


def render_condition(condition, runs):
    if [run.label for run in runs] != list(LABELS):
        raise ValueError("expected pilot1, run2, run3, run4, run5")
    lines = [f"# Adaptive C {condition}, five-run bandwidth comparison", "", f"TBF: 3 -> {LOW_KBIT[condition] / 1000:g} -> 3 Mbps. Values come from validated raw artifacts. SD uses n-1.", ""]
    lines += ["## Detection", ""]
    lines += table(["run", "baseline ms", "threshold ms", "switch to CONGESTED ms", "pending/in-flight at stop"], [
        [r.label, fmt(r.baseline_ms), fmt(r.threshold_ms), fmt(r.detection_ms), f"{r.stop_pending}/{r.stop_in_flight}"] for r in runs
    ])
    for name, values in [("baseline RTT (ms)", [r.baseline_ms for r in runs]), ("threshold RTT (ms)", [r.threshold_ms for r in runs]), ("detection (ms)", [r.detection_ms for r in runs]), ("pending at stop", [r.stop_pending for r in runs]), ("in-flight at stop", [r.stop_in_flight for r in runs])]:
        lines.append(f"{name}: {summary(values)}")

    lines += ["", "## Bottleneck Audio", ""]
    lines += table(["run", "mean/p95/max ms", "received"], [
        [r.label, "/".join(map(fmt, r.audio_low)), f"{r.audio_count}/400 ({r.audio_count/400:.1%})"] for r in runs
    ])
    for index, metric in enumerate(("mean", "p95", "max")):
        lines.append(f"Audio {metric} (ms): {summary([r.audio_low[index] for r in runs])}")
    lines.append(f"Audio receive rate (%): {summary([r.audio_count / 400 * 100 for r in runs])}")

    lines += ["", "## RECOVERY during bottleneck", ""]
    lines += table(["run", "entries", "probe start/complete", "stage 1/2/3 complete", "RECOVERY to CONGESTED", "false NORMAL", "RTT max ms"], [
        [r.label, r.low_entries, f"{r.low_probe_starts}/{r.low_probe_completes}", "/".join(map(str, r.low_stage_completes)), r.low_rebounds, r.false_normal, fmt(r.low_rtt_max_ms)] for r in runs
    ])
    for name, values in [("RECOVERY entries", [r.low_entries for r in runs]), ("probe starts", [r.low_probe_starts for r in runs]), ("probe completes", [r.low_probe_completes for r in runs]), ("stage 1 completes", [r.low_stage_completes[0] for r in runs]), ("stage 2 completes", [r.low_stage_completes[1] for r in runs]), ("stage 3 completes", [r.low_stage_completes[2] for r in runs]), ("RECOVERY to CONGESTED", [r.low_rebounds for r in runs]), ("RECOVERY RTT max (ms)", [r.low_rtt_max_ms for r in runs])]:
        lines.append(f"{name}: {summary(values)}")
    lines.append(f"False NORMAL before restoration: {sum(r.false_normal == 0 for r in runs)}/5 runs had zero; total={sum(r.false_normal for r in runs)}.")
    if condition == "D1":
        lines.append(f"Runs with zero RECOVERY entries during bottleneck: {sum(r.low_entries == 0 for r in runs)}/5.")
    else:
        lines.append(f"Runs with Stage 2 probe completion during bottleneck: {sum(r.low_stage_completes[1] > 0 for r in runs)}/5.")

    lines += ["", "## 3 Mbps restoration", ""]
    lines += table(["run", "switch to RECOVERY ms", "successful stage 1 -> 2 -> 3", "switch to NORMAL ms", "NORMAL returned", "Audio mean/p95/max ms"], [
        [r.label, fmt(r.recovery_latency_ms), "→".join(r.stages), fmt(r.normal_latency_ms), "yes", "/".join(map(fmt, r.audio_restored))] for r in runs
    ])
    for name, values in [("switch to RECOVERY (ms)", [r.recovery_latency_ms for r in runs]), ("switch to Stage 2 (ms)", [r.stage2_latency_ms for r in runs]), ("switch to Stage 3 (ms)", [r.stage3_latency_ms for r in runs]), ("switch to NORMAL (ms)", [r.normal_latency_ms for r in runs])]:
        lines.append(f"{name}: {summary(values)}")
    for index, metric in enumerate(("mean", "p95", "max")):
        lines.append(f"restored Audio {metric} (ms): {summary([r.audio_restored[index] for r in runs])}")
    lines.append("Switch-to-RECOVERY is 0 ms when RECOVERY is already active at the 3 Mbps switch. Negative stage times show a stage reached before that switch.")

    lines += ["", "## Background", ""]
    lines += table(["run", "received", "pending at restoration", "completion from start ms", "backlog drain ms"], [
        [r.label, f"{r.background_count}/400", r.pending_at_restoration, fmt(r.completion_ms), fmt(r.backlog_drain_ms)] for r in runs
    ])
    for name, values in [("pending at restoration", [r.pending_at_restoration for r in runs]), ("completion from start (ms)", [r.completion_ms for r in runs]), ("backlog drain (ms)", [r.backlog_drain_ms for r in runs])]:
        lines.append(f"{name}: {summary(values)}")
    lines.append(f"Background 400/400: {sum(r.background_count == 400 for r in runs)}/5 runs.")

    lines += ["", "## Validation", ""]
    lines += table(["run", "Audio 400/400", "Background 400/400", "in-flight <=1", "marker-free", "false NORMAL=0", "NORMAL after restoration", "analyzer revalidation"], [
        [r.label, "OK", "OK", "OK", "OK", "OK", "OK", "OK"] for r in runs
    ])
    lines.append("The table records validated observations. D1/D2 bottleneck RECOVERY counts are not fixed in advance.")
    lines.append("")
    return "\n".join(lines)


def render_cross(d1, one, d2):
    def values(runs, field):
        return [field(run) for run in runs]

    rows = []
    for label, runs, get_audio, get_detection, get_normal, get_drain, get_stage2, get_false, get_audio_count, get_bg_count in (
        ("0.8 Mbps D1", d1, lambda r: r.audio_low, lambda r: r.detection_ms, lambda r: r.normal_latency_ms, lambda r: r.backlog_drain_ms, lambda r: r.low_stage_completes[1], lambda r: r.false_normal, lambda r: r.audio_count, lambda r: r.background_count),
        ("1.0 Mbps Recovery C", one, lambda r: r.low_audio, lambda r: r.detection_ms, lambda r: r.normal_latency_ms, lambda r: r.backlog_drain_ms, lambda r: r.low_stage_completes[1], lambda r: r.false_normal, lambda r: r.audio_count, lambda r: r.background_count),
        ("1.2 Mbps D2", d2, lambda r: r.audio_low, lambda r: r.detection_ms, lambda r: r.normal_latency_ms, lambda r: r.backlog_drain_ms, lambda r: r.low_stage_completes[1], lambda r: r.false_normal, lambda r: r.audio_count, lambda r: r.background_count),
    ):
        rows.append([label, summary(values(runs, get_detection)), summary(values(runs, lambda r: get_audio(r)[0])), summary(values(runs, lambda r: get_audio(r)[1])), f"{sum(get_false(r)==0 for r in runs)}/5 zero", f"{sum(get_stage2(r)>0 for r in runs)}/5", summary(values(runs, get_normal)), summary(values(runs, get_drain)), f"{sum(get_audio_count(r)==400 for r in runs)}/5", f"{sum(get_bg_count(r)==400 for r in runs)}/5"])
    lines = ["# Adaptive C bandwidth comparison: 0.8 / 1.0 / 1.2 Mbps", "", "Each condition uses five validated raw runs. Mean ± between-run sample SD includes min/max. The 1.0 Mbps runs are reloaded from the existing RECOVERY artifacts.", ""]
    lines += table(["bottleneck", "detection ms", "Audio mean ms", "Audio p95 ms", "false NORMAL", "Stage 2 reached in bottleneck", "restoration to NORMAL ms", "backlog drain ms", "Audio 400/400", "Background 400/400"], rows)
    lines.append("")
    lines.append(f"D1 runs with zero low-rate RECOVERY entries: {sum(run.low_entries == 0 for run in d1)}/5.")
    lines.append(f"D2 runs with low-rate Stage 2 completion: {sum(run.low_stage_completes[1] > 0 for run in d2)}/5.")
    lines.append("The three bandwidths are distinct fixed test conditions. Do not extrapolate to other links or network paths from this table.")
    lines.append("")
    return "\n".join(lines)


def write_or_verify(path, report):
    if path.exists():
        if path.read_text(encoding="utf-8") != report:
            raise ValueError(f"existing report differs; refusing overwrite: {path}")
        print(f"existing report matches: {path}")
    else:
        with path.open("x", encoding="utf-8") as output:
            output.write(report)
        print(f"saved: {path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("condition", nargs="?", choices=NAMES)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--validate-run", choices=LABELS)
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.compare:
        if args.condition or args.validate_run:
            parser.error("--compare takes no condition or --validate-run")
        d1 = [load_run(args.root, "D1", label) for label in LABELS]
        d2 = [load_run(args.root, "D2", label) for label in LABELS]
        one = [load_1mbps_run(args.root, label) for label in LABELS]
        report = render_cross(d1, one, d2)
    else:
        if not args.condition:
            parser.error("D1 or D2 is required")
        if args.validate_run:
            run = load_run(args.root, args.condition, args.validate_run)
            print(f"{args.condition} {run.label}: reanalysis and validation OK")
            return
        report = render_condition(args.condition, [load_run(args.root, args.condition, label) for label in LABELS])
    if args.output:
        write_or_verify(args.output, report)
    else:
        print(report, end="")


if __name__ == "__main__":
    main()
