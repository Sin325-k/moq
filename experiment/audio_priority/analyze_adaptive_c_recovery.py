"""Evaluate adaptive C RECOVERY against offline TBF ground truth."""

import argparse
import json
import statistics
from pathlib import Path

from analyze_adaptive_c import read_csv
from analyze_stats_calibration import percentile


ALLOWED_TRANSITIONS = {
    ("NORMAL", "CONGESTED"),
    ("CONGESTED", "RECOVERY"),
    ("RECOVERY", "CONGESTED"),
    ("RECOVERY", "NORMAL"),
}


def tbf_events(path):
    with path.open(encoding="utf-8") as source:
        events = {event["event"]: event for line in source if line.strip() for event in [json.loads(line)]}
    if events["low_rate"]["rate_kbit"] != 1000 or events["recovery_rate"]["rate_kbit"] != 3000:
        raise ValueError("unexpected TBF rates")
    return events


def summarize(values):
    if not values:
        return "none"
    return f"count={len(values)}, mean/p95/max={statistics.fmean(values):.3f}/{percentile(values, 95):.3f}/{max(values):.3f} ms"


def transition_report(samples, low_wall, recovery_wall):
    transitions = [row for row in samples if row["transition_reason"]]
    previous = "NORMAL"
    stage_changes = []
    for row in transitions:
        current = row["state"]
        if current == previous:
            if current != "RECOVERY" or not row["transition_reason"].startswith("RECOVERY stage advanced"):
                raise ValueError(f"unexpected same-state event: {row['transition_reason']}")
            stage_changes.append(row)
        elif (previous, current) not in ALLOWED_TRANSITIONS:
            raise ValueError(f"invalid transition: {previous} -> {current}")
        previous = current
    congested = [row for row in transitions if row["transition_reason"] == "RTT above baseline +1 ms for 3 samples"]
    if not congested:
        raise ValueError("missing initial congestion detection")
    first = congested[0]
    if int(first["consecutive_high"]) != 3:
        raise ValueError("initial detection did not use three high samples")
    baseline = int(first["baseline_rtt_ns"])
    if int(first["threshold_rtt_ns"]) != baseline + 1_000_000:
        raise ValueError("wrong RTT threshold")
    if any(int(row["background_in_flight"]) > 1 for row in samples):
        raise ValueError("Background in-flight exceeded one")
    false_normal = [row for row in transitions if row["transition_reason"].startswith("RECOVERY to NORMAL") and int(row["wall_time_ns"]) < recovery_wall]
    probe_starts = [row for row in samples if row["event"] == "GROUP_START" and row["state"] == "RECOVERY"]
    probe_completes = [row for row in samples if row["event"] == "GROUP_COMPLETE" and row["state"] == "RECOVERY"]
    for row in probe_starts:
        if row["recovery_stage"] not in {"1", "2", "3"} or row["background_start_interval_ms"] not in {"60", "40", "20"}:
            raise ValueError("invalid recovery probe stage or interval")
    return {
        "transitions": transitions,
        "first": first,
        "stage_changes": stage_changes,
        "false_normal": false_normal,
        "probe_starts": probe_starts,
        "probe_completes": probe_completes,
        "baseline": baseline,
        "low_probes": [row for row in probe_starts if int(row["wall_time_ns"]) < recovery_wall],
        "low_rebounds": [row for row in transitions if row["transition_reason"].startswith("RECOVERY to CONGESTED") and int(row["wall_time_ns"]) < recovery_wall],
        "post_recovery": [row for row in transitions if int(row["wall_time_ns"]) >= recovery_wall],
        "detection_ms": (int(first["wall_time_ns"]) - low_wall) / 1_000_000,
    }


def analyze(quic_csv, tbf_jsonl, subscriber_csv, relay_log):
    samples = read_csv(quic_csv)
    if not samples or not {"event", "recovery_stage", "transition_reason"}.issubset(samples[0]):
        raise ValueError("missing recovery CSV samples or columns")
    events = tbf_events(tbf_jsonl)
    low = events["low_rate"]
    restored = events["recovery_rate"]
    low_wall = int(low["applied_wall_time_ns"])
    recovery_wall = int(restored["applied_wall_time_ns"])
    low_mono = int(low["applied_monotonic_ns"])
    recovery_mono = int(restored["applied_monotonic_ns"])
    result = transition_report(samples, low_wall, recovery_wall)
    relay = relay_log.read_text(errors="replace")
    if "background one-mbps phase started" in relay or "background three-mbps recovery started" in relay:
        raise ValueError("Relay observed an Oracle marker")

    frames = read_csv(subscriber_csv)
    audio = [row for row in frames if row["track"] == "audio"]
    background = [row for row in frames if row["track"] == "background"]
    if [int(row["seq"]) for row in audio] != list(range(400)):
        raise ValueError("Audio delivery is not 400/400 in order")
    if len(background) != len({int(row["seq"]) for row in background}) or len(background) > 400:
        raise ValueError("Background duplicate or excess delivery")
    phase = lambda rows, start, end: [float(row["delay_ms"]) for row in rows if start <= int(row["send_time_ns"]) < end]
    low_audio = phase(audio, low_mono, recovery_mono)
    restored_audio = phase(audio, recovery_mono, 2**63)
    if not low_audio or not restored_audio:
        raise ValueError("Audio samples missing in a TBF phase")

    low_recovery_samples = [row for row in samples if low_wall <= int(row["wall_time_ns"]) < recovery_wall and row["state"] == "RECOVERY" and row["event"] == "SAMPLE"]
    low_recovery_audio = [float(row["delay_ms"]) for row in audio if any(
        abs(int(row["received_wall_time_ns"]) - int(sample["wall_time_ns"])) <= 5_000_000
        for sample in low_recovery_samples
    )]
    stage_rows = [row for row in result["transitions"] if row["transition_reason"].startswith("CONGESTED to RECOVERY") or row["transition_reason"].startswith("RECOVERY stage advanced")]
    normal_rows = [row for row in result["post_recovery"] if row["transition_reason"].startswith("RECOVERY to NORMAL")]
    post_probe = next((row for row in stage_rows if int(row["wall_time_ns"]) >= recovery_wall and row["transition_reason"].startswith("CONGESTED to RECOVERY")), None)
    active_recovery_at_switch = any(row["state"] == "RECOVERY" for row in samples if recovery_wall <= int(row["wall_time_ns"]) < recovery_wall + 20_000_000)
    pending_after_recovery = [int(row["background_pending"]) for row in samples if int(row["wall_time_ns"]) >= recovery_wall]
    drain = next((row for row in samples if int(row["wall_time_ns"]) >= recovery_wall and int(row["background_pending"]) == 0 and int(row["background_in_flight"]) == 0), None)
    completion_ms = None
    if len(background) == 400 and "schedule_started" in events:
        completion_ms = (max(int(row["received_time_ns"]) for row in background) - int(events["schedule_started"]["experiment_start_monotonic_ns"])) / 1_000_000
    lines = [
        "# Adaptive C RECOVERY pilot analysis",
        f"baseline/threshold RTT: {result['baseline']/1_000_000:.3f}/{(result['baseline']+1_000_000)/1_000_000:.3f} ms",
        f"1 Mbps switch -> CONGESTED: {result['detection_ms']:.3f} ms",
        f"Background stop wall ns: {result['first']['wall_time_ns']}",
        f"Background pending/in-flight at stop: {result['first']['background_pending']}/{result['first']['background_in_flight']}",
        f"1 Mbps Audio: {summarize(low_audio)}",
        f"1 Mbps RECOVERY sample count / probe starts / probe completes: {len(low_recovery_samples)}/{len(result['low_probes'])}/{len([row for row in result['probe_completes'] if int(row['wall_time_ns']) < recovery_wall])}",
        "Probe completes by stage: " + ", ".join(
            f"{stage}={sum(row['recovery_stage'] == str(stage) for row in result['probe_completes'])}"
            for stage in (1, 2, 3)
        ),
        f"1 Mbps RECOVERY RTT max: {max((int(row['rtt_ns']) for row in low_recovery_samples if row['rtt_ns']), default=0)/1_000_000:.3f} ms",
        f"1 Mbps probe Audio by receive time: {summarize(low_recovery_audio)}",
        f"1 Mbps RECOVERY -> CONGESTED count: {len(result['low_rebounds'])}",
        f"False NORMAL before 3 Mbps restoration: {len(result['false_normal'])}",
        f"3 Mbps switch -> next RECOVERY entry: {(int(post_probe['wall_time_ns'])-recovery_wall)/1_000_000:.3f} ms" if post_probe else ("RECOVERY active at 3 Mbps switch" if active_recovery_at_switch else "3 Mbps switch -> RECOVERY: not observed"),
        "Recovery stage events: " + (", ".join(f"{row['recovery_stage']}@{row['wall_time_ns']}" for row in stage_rows) or "none"),
        f"3 Mbps switch -> NORMAL: {(int(normal_rows[0]['wall_time_ns'])-recovery_wall)/1_000_000:.3f} ms" if normal_rows else "3 Mbps switch -> NORMAL: not observed",
        f"3 Mbps Audio: {summarize(restored_audio)}",
        f"Background pending after restoration: first={pending_after_recovery[0] if pending_after_recovery else 'none'}, min={min(pending_after_recovery) if pending_after_recovery else 'none'}, max={max(pending_after_recovery) if pending_after_recovery else 'none'}",
        f"Background received: {len(background)}/400",
        f"Background completion wall ns: {max(int(row['received_wall_time_ns']) for row in background) if len(background)==400 else 'incomplete'}",
        f"Background completion from experiment start: {completion_ms:.3f} ms" if completion_ms is not None else "Background completion from experiment start: incomplete",
        f"Backlog drain after 3 Mbps switch: {(int(drain['wall_time_ns'])-recovery_wall)/1_000_000:.3f} ms" if drain else "Backlog drain after 3 Mbps switch: not observed",
        f"State transitions / stage changes / RECOVERY -> CONGESTED: {len(result['transitions'])-len(result['stage_changes'])}/{len(result['stage_changes'])}/{sum(row['transition_reason'].startswith('RECOVERY to CONGESTED') for row in result['transitions'])}",
        f"validation: Audio 400/400, in-flight <=1, TBF marker-free; false NORMAL={len(result['false_normal'])}",
    ]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("quic_csv", type=Path)
    parser.add_argument("tbf_jsonl", type=Path)
    parser.add_argument("subscriber_csv", type=Path)
    parser.add_argument("relay_log", type=Path)
    args = parser.parse_args()
    print(analyze(args.quic_csv, args.tbf_jsonl, args.subscriber_csv, args.relay_log), end="")


if __name__ == "__main__":
    main()
