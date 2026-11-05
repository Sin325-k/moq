"""Evaluate D1/D2 adaptive C pilots using TBF JSONL only as offline truth."""

import argparse
import json
import statistics
from pathlib import Path

from analyze_adaptive_c import read_csv
from analyze_stats_calibration import percentile


LOW_KBIT = {"D1": 800, "D2": 1200}


def tbf_events(path, expected_low_kbit):
    with path.open(encoding="utf-8") as source:
        events = {event["event"]: event for line in source if line.strip() for event in [json.loads(line)]}
    rates = [int(events[name]["rate_kbit"]) for name in ("initial_rate", "low_rate", "recovery_rate")]
    if rates != [3000, expected_low_kbit, 3000]:
        raise ValueError(f"TBF rates differ from the selected condition: {rates}")
    return events


def latency_stats(values):
    if not values:
        return "none"
    return f"n={len(values)}, mean/p95/max={statistics.fmean(values):.3f}/{percentile(values, 95):.3f}/{max(values):.3f} ms"


def elapsed_ms(event_wall, switch_wall):
    return f"{(event_wall - switch_wall) / 1_000_000:.3f} ms"


def difference(actual, reference):
    return f"{actual - reference:+.3f} ms" if actual is not None else "not observed"


def analyze(condition, quic_csv, tbf_jsonl, subscriber_csv, relay_log):
    if condition not in LOW_KBIT:
        raise ValueError(f"unknown condition: {condition}")
    samples = read_csv(quic_csv)
    if not samples or not {"event", "state", "transition_reason", "background_in_flight"}.issubset(samples[0]):
        raise ValueError("missing adaptive C CSV samples or columns")
    events = tbf_events(tbf_jsonl, LOW_KBIT[condition])
    low = events["low_rate"]
    restored = events["recovery_rate"]
    low_wall = int(low["applied_wall_time_ns"])
    restore_wall = int(restored["applied_wall_time_ns"])
    low_mono = int(low["applied_monotonic_ns"])
    restore_mono = int(restored["applied_monotonic_ns"])
    if not low_wall < restore_wall or not low_mono < restore_mono:
        raise ValueError("TBF switch order is invalid")
    frames = read_csv(subscriber_csv)
    audio = [row for row in frames if row["track"] == "audio"]
    background = [row for row in frames if row["track"] == "background"]
    if any(int(row["size"]) != 1945 for row in frames):
        raise ValueError("received payload size differs from 1945 bytes")
    if len(audio) != len({int(row["seq"]) for row in audio}):
        raise ValueError("duplicate Audio sequence")
    if len(background) != len({int(row["seq"]) for row in background}):
        raise ValueError("duplicate Background sequence")
    audio_complete = sorted(int(row["seq"]) for row in audio) == list(range(400))
    background_complete = sorted(int(row["seq"]) for row in background) == list(range(400))

    transitions = [row for row in samples if row["transition_reason"]]
    initial = next((row for row in transitions if row["transition_reason"] == "RTT above baseline +1 ms for 3 samples"), None)
    baseline = next((int(row["baseline_rtt_ns"]) for row in samples if row["baseline_rtt_ns"]), None)
    threshold = baseline + 1_000_000 if baseline is not None else None
    low_transitions = [row for row in transitions if low_wall <= int(row["wall_time_ns"]) < restore_wall]
    after_transitions = [row for row in transitions if int(row["wall_time_ns"]) >= restore_wall]
    low_entries = [row for row in low_transitions if row["transition_reason"].startswith("CONGESTED to RECOVERY")]
    low_rebounds = [row for row in low_transitions if row["transition_reason"].startswith("RECOVERY to CONGESTED")]
    false_normals = [row for row in transitions if int(row["wall_time_ns"]) < restore_wall and row["transition_reason"].startswith("RECOVERY to NORMAL")]
    starts = [row for row in samples if row["event"] == "GROUP_START" and row["state"] == "RECOVERY" and low_wall <= int(row["wall_time_ns"]) < restore_wall]
    completes = [row for row in samples if row["event"] == "GROUP_COMPLETE" and row["state"] == "RECOVERY" and low_wall <= int(row["wall_time_ns"]) < restore_wall]
    low_recovery_samples = [row for row in samples if row["event"] == "SAMPLE" and row["state"] == "RECOVERY" and row["rtt_ns"] and low_wall <= int(row["wall_time_ns"]) < restore_wall]
    low_rtt_max = max((int(row["rtt_ns"]) for row in low_recovery_samples), default=None)
    low_probe_audio = [float(row["delay_ms"]) for row in audio if any(
        abs(int(row["received_wall_time_ns"]) - int(sample["wall_time_ns"])) <= 5_000_000
        for sample in low_recovery_samples
    )]
    low_audio = [float(row["delay_ms"]) for row in audio if low_mono <= int(row["send_time_ns"]) < restore_mono]
    restored_audio = [float(row["delay_ms"]) for row in audio if int(row["send_time_ns"]) >= restore_mono]
    after_entry = next((row for row in after_transitions if row["transition_reason"].startswith("CONGESTED to RECOVERY")), None)
    after_normal = next((row for row in after_transitions if row["transition_reason"].startswith("RECOVERY to NORMAL")), None)
    active_at_restore = any(row["state"] == "RECOVERY" for row in samples if restore_wall <= int(row["wall_time_ns"]) < restore_wall + 20_000_000)
    stage_events = [row for row in after_transitions if row["transition_reason"].startswith(("CONGESTED to RECOVERY", "RECOVERY stage advanced"))]
    after_samples = [row for row in samples if int(row["wall_time_ns"]) >= restore_wall]
    drained = next((row for row in after_samples if int(row["background_pending"]) == 0 and int(row["background_in_flight"]) == 0), None)
    completion_ms = None
    if background_complete and "schedule_started" in events:
        started_mono = int(events["schedule_started"]["experiment_start_monotonic_ns"])
        completion_ms = (max(int(row["received_time_ns"]) for row in background) - started_mono) / 1_000_000
    relay = relay_log.read_text(errors="replace")
    marker_free = not any(message in relay for message in (
        "background one-mbps phase started", "background three-mbps recovery started", "tbf_applied_wall_time_ns"
    ))
    max_in_flight = max(int(row["background_in_flight"]) for row in samples)
    detection_ms = (int(initial["wall_time_ns"]) - low_wall) / 1_000_000 if initial else None
    normal_ms = (int(after_normal["wall_time_ns"]) - restore_wall) / 1_000_000 if after_normal else None
    drain_ms = (int(drained["wall_time_ns"]) - restore_wall) / 1_000_000 if drained else None

    sequence = ["NORMAL"]
    inserted_restore = False
    for row in transitions:
        if not inserted_restore and int(row["wall_time_ns"]) >= restore_wall:
            sequence.append("3 Mbps restored (offline)")
            inserted_restore = True
        state = row["state"]
        sequence.append(f"RECOVERY stage {row['recovery_stage']}" if state == "RECOVERY" else state)
    if not inserted_restore:
        sequence.append("3 Mbps restored (offline)")
    sequence.append(f"end in {samples[-1]['state']}")

    lines = [
        f"# Adaptive C bandwidth pilot: {condition} (3 -> {LOW_KBIT[condition] / 1000:g} -> 3 Mbps)",
        f"TBF applied low / restoration wall ns: {low_wall} / {restore_wall}",
        f"baseline / threshold RTT: {baseline / 1_000_000:.3f} / {threshold / 1_000_000:.3f} ms" if baseline is not None else "baseline / threshold RTT: unavailable",
        f"bottleneck switch -> CONGESTED: {elapsed_ms(int(initial['wall_time_ns']), low_wall)}" if initial else "bottleneck switch -> CONGESTED: not observed",
        f"Background stop wall ns: {initial['wall_time_ns']}" if initial else "Background stop wall ns: not observed",
        f"pending / in-flight at stop: {initial['background_pending']} / {initial['background_in_flight']}" if initial else "pending / in-flight at stop: not observed",
        "",
        "## Bottleneck phase",
        f"Audio: {latency_stats(low_audio)}; received total {len(audio)}/400",
        f"RECOVERY entries / probe starts / probe completes: {len(low_entries)} / {len(starts)} / {len(completes)}",
        "probe completes by stage 1/2/3: " + "/".join(str(sum(row["recovery_stage"] == str(stage) for row in completes)) for stage in (1, 2, 3)),
        f"RECOVERY -> CONGESTED / false NORMAL: {len(low_rebounds)} / {len(false_normals)}",
        f"RECOVERY RTT max: {low_rtt_max / 1_000_000:.3f} ms" if low_rtt_max is not None else "RECOVERY RTT max: not observed",
        f"Audio during probe by receive time: {latency_stats(low_probe_audio)}",
        "",
        "## 3 Mbps restoration",
        f"switch -> RECOVERY: {elapsed_ms(int(after_entry['wall_time_ns']), restore_wall)}" if after_entry else ("RECOVERY active at switch" if active_at_restore else "switch -> RECOVERY: not observed"),
        "stage events: " + (", ".join(f"{row['recovery_stage']}@{elapsed_ms(int(row['wall_time_ns']), restore_wall)}" for row in stage_events) or "none"),
        f"switch -> NORMAL: {elapsed_ms(int(after_normal['wall_time_ns']), restore_wall)}" if after_normal else "switch -> NORMAL: not observed",
        f"Audio: {latency_stats(restored_audio)}",
        f"Background pending at restoration: {after_samples[0]['background_pending']}" if after_samples else "Background pending at restoration: not observed",
        f"backlog drain after restoration: {elapsed_ms(int(drained['wall_time_ns']), restore_wall)}" if drained else "backlog drain after restoration: not observed",
        f"Background completion from experiment start: {completion_ms:.3f} ms" if completion_ms is not None else "Background completion from experiment start: incomplete",
        "",
        "## Overall and comparison",
        f"Audio / Background received: {len(audio)}/400 / {len(background)}/400",
        f"Background max in-flight: {max_in_flight}",
        f"marker-free Relay log: {'yes' if marker_free else 'NO'}",
        "state sequence: " + " -> ".join(sequence),
        "Reference 3 -> 1 -> 3 Mbps Recovery C (five-run mean ± SD): detection 71.369 ± 11.332 ms; bottleneck Audio mean/p95 4.157 ± 0.244 / 14.361 ± 2.049 ms; false NORMAL 0/5; restoration -> NORMAL 1050.569 ± 28.065 ms; backlog drain 7624.326 ± 85.455 ms; Audio and Background 400/400 in all five runs.",
        "Difference from 1 Mbps reference: detection " + difference(detection_ms, 71.369)
        + "; Audio mean " + difference(statistics.fmean(low_audio) if low_audio else None, 4.157)
        + "; Audio p95 " + difference(percentile(low_audio, 95), 14.361)
        + "; restoration -> NORMAL " + difference(normal_ms, 1050.569)
        + "; backlog drain " + difference(drain_ms, 7624.326),
        f"validation: detection after bottleneck={'yes' if initial and low_wall <= int(initial['wall_time_ns']) < restore_wall else 'no'}, Audio 400/400={'yes' if audio_complete else 'no'}, Background 400/400={'yes' if background_complete else 'no'}, in-flight <=1={'yes' if max_in_flight<=1 else 'no'}, marker-free={'yes' if marker_free else 'no'}, false NORMAL=0={'yes' if not false_normals else 'no'}, NORMAL after restoration={'yes' if after_normal else 'no'}",
    ]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--condition", required=True, choices=LOW_KBIT)
    parser.add_argument("quic_csv", type=Path)
    parser.add_argument("tbf_jsonl", type=Path)
    parser.add_argument("subscriber_csv", type=Path)
    parser.add_argument("relay_log", type=Path)
    args = parser.parse_args()
    print(analyze(args.condition, args.quic_csv, args.tbf_jsonl, args.subscriber_csv, args.relay_log), end="")


if __name__ == "__main__":
    main()
