import argparse
import csv
import json
import math
import re
from pathlib import Path


MESSAGE_COUNT = 400
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("condition", choices=["R", "A", "B"])
    parser.add_argument("csv_file", type=Path)
    parser.add_argument("tbf_log", type=Path)
    parser.add_argument("relay_log", type=Path)
    parser.add_argument("subscriber_log", type=Path)
    parser.add_argument("publisher_log", type=Path)
    return parser.parse_args()


def percentile(values, percentile_value):
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile_value / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def describe(name, rows):
    delays = [float(row["delay_ms"]) for row in rows]
    print(
        f"{name}: count={len(delays)}, mean={sum(delays) / len(delays):.3f} ms, "
        f"p95={percentile(delays, 95):.3f} ms, max={max(delays):.3f} ms"
    )


def load_tbf(path):
    events = []
    with path.open(encoding="utf-8") as source:
        for line in source:
            if line.strip():
                events.append(json.loads(line))
    by_name = {event["event"]: event for event in events if event["event"] != "sample"}
    for required in ("initial_rate", "schedule_started", "low_rate", "recovery_rate"):
        if required not in by_name:
            raise RuntimeError(f"TBF log is missing {required}")
    if by_name["initial_rate"]["rate_kbit"] != 3000:
        raise RuntimeError("initial TBF rate is not 3000 Kbit")
    if by_name["low_rate"]["rate_kbit"] != 1000:
        raise RuntimeError("low TBF rate is not 1000 Kbit")
    if by_name["recovery_rate"]["rate_kbit"] != 3000:
        raise RuntimeError("recovery TBF rate is not 3000 Kbit")
    for event_name, rate_pattern in (
        ("initial_rate", r"rate (?:3000Kbit|3Mbit)"),
        ("low_rate", r"rate (?:1000Kbit|1Mbit)"),
        ("recovery_rate", r"rate (?:3000Kbit|3Mbit)"),
    ):
        qdisc = by_name[event_name]["qdisc"]
        if "qdisc tbf " not in qdisc:
            raise RuntimeError(f"{event_name} is not using TBF: {qdisc}")
        if re.search(rate_pattern, qdisc) is None:
            raise RuntimeError(f"{event_name} has the wrong applied rate: {qdisc}")
        if "burst 4Kb" not in qdisc or "lat 400ms" not in qdisc:
            raise RuntimeError(f"{event_name} changed burst or latency: {qdisc}")
    low_elapsed = by_name["low_rate"]["elapsed_ms"]
    recovery_elapsed = by_name["recovery_rate"]["elapsed_ms"]
    if not 1_900 <= low_elapsed <= 2_200:
        raise RuntimeError(f"1 Mbps transition was late or early: {low_elapsed:.3f} ms")
    if not 3_900 <= recovery_elapsed <= 4_200:
        raise RuntimeError(f"3 Mbps recovery was late or early: {recovery_elapsed:.3f} ms")
    return by_name


def assert_sequences(rows, track, expected_count):
    sequences = [int(row["seq"]) for row in rows if row["track"] == track]
    expected = list(range(expected_count))
    if sequences != expected:
        missing = sorted(set(expected) - set(sequences))
        duplicates = sorted(
            sequence for sequence in set(sequences) if sequences.count(sequence) > 1
        )
        raise RuntimeError(
            f"{track} sequence mismatch: count={len(sequences)}, "
            f"missing={missing}, duplicates={duplicates}"
        )
    return set(sequences)


def validate_logs(paths):
    markers = ("MoqError.Protocol", "MoqProtocolKind.OLD", "Error::Old", "Traceback")
    for path in paths:
        contents = ANSI.sub("", path.read_text(errors="replace"))
        for marker in markers:
            if marker in contents:
                raise RuntimeError(f"{path.name} contains {marker}")


def relay_background_stats(path, condition, tbf, received_sequences):
    relay = ANSI.sub("", path.read_text(errors="replace"))
    if condition == "R":
        return None, None, None, None

    in_flight = 0
    max_in_flight = 0
    started = set()
    completed = set()
    received_drops = set()
    terminated = set()
    for line in relay.splitlines():
        if "track=background" not in line:
            continue
        match = re.search(r"sequence=(\d+)", line)
        if "serve group task poll/start" in line and match:
            sequence = int(match.group(1))
            if sequence in started:
                raise RuntimeError(f"background sequence {sequence} started twice")
            started.add(sequence)
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        elif "serve group complete" in line and match:
            sequence = int(match.group(1))
            if sequence not in started or sequence in completed:
                raise RuntimeError(f"background sequence {sequence} completed out of state")
            completed.add(sequence)
            if sequence not in terminated:
                terminated.add(sequence)
                in_flight -= 1
        elif (
            "serve group task dropped" in line
            and "started=true" in line
            and match
        ):
            sequence = int(match.group(1))
            if sequence not in started:
                raise RuntimeError(f"background sequence {sequence} dropped before start")
            if sequence not in received_sequences:
                raise RuntimeError(
                    f"background sequence {sequence} dropped without CSV reception"
                )
            received_drops.add(sequence)
            if sequence not in terminated:
                terminated.add(sequence)
                in_flight -= 1
    expected = set(range(MESSAGE_COUNT))
    if started != expected or terminated != expected:
        raise RuntimeError(
            f"background relay incomplete: started={len(started)}, "
            f"completed={len(completed)}, received_drops={len(received_drops)}, "
            f"terminated={len(terminated)}"
        )
    if in_flight != 0 or max_in_flight != 1:
        raise RuntimeError(
            f"background in-flight mismatch: current={in_flight}, max={max_in_flight}"
        )

    low = next(
        (
            line
            for line in relay.splitlines()
            if "background one-mbps phase started" in line
        ),
        None,
    )
    recovery = next(
        (
            line
            for line in relay.splitlines()
            if "background three-mbps recovery started" in line
        ),
        None,
    )
    if low is None or recovery is None:
        raise RuntimeError("relay log is missing a TBF phase marker")
    pending_match = re.search(r"pending=(\d+)", recovery)
    oracle_match = re.search(r"oracle=(true|false)", recovery)
    if pending_match is None or oracle_match is None:
        raise RuntimeError(f"could not parse recovery snapshot: {recovery}")
    pending = int(pending_match.group(1))
    expected_oracle = condition == "B"
    if (oracle_match.group(1) == "true") != expected_oracle:
        raise RuntimeError(f"oracle mode mismatch in relay log: {recovery}")

    phase_delays = []
    for line, event_name in ((low, "low_rate"), (recovery, "recovery_rate")):
        applied_match = re.search(r"tbf_applied_monotonic_ns=(\d+)", line)
        delay_match = re.search(r"observation_delay_ms=(\d+)", line)
        if applied_match is None or delay_match is None:
            raise RuntimeError(f"could not parse Relay phase timing: {line}")
        applied_ns = int(applied_match.group(1))
        if applied_ns != int(tbf[event_name]["applied_monotonic_ns"]):
            raise RuntimeError(
                f"Relay and TBF disagree on {event_name}: "
                f"relay={applied_ns}, tbf={tbf[event_name]['applied_monotonic_ns']}"
            )
        delay_ms = int(delay_match.group(1))
        if delay_ms > 50:
            raise RuntimeError(
                f"Relay observed {event_name} too late: {delay_ms} ms"
            )
        phase_delays.append(delay_ms)

    lines = relay.splitlines()
    low_index = next(
        index
        for index, line in enumerate(lines)
        if "background one-mbps phase started" in line
    )
    recovery_index = next(
        index
        for index, line in enumerate(lines)
        if "background three-mbps recovery started" in line
    )
    starts_during_low = sum(
        "serve group task poll/start" in line and "track=background" in line
        for line in lines[low_index + 1 : recovery_index]
    )
    starts_after_recovery = sum(
        "serve group task poll/start" in line and "track=background" in line
        for line in lines[recovery_index + 1 :]
    )
    if condition == "A" and starts_during_low == 0:
        raise RuntimeError("condition A did not start Background during the 1 Mbps phase")
    if condition == "B" and starts_during_low != 0:
        raise RuntimeError(
            f"condition B started {starts_during_low} Background groups during suppression"
        )
    if condition == "B" and starts_after_recovery == 0:
        raise RuntimeError("condition B did not resume Background after recovery")

    drained = next(
        (
            line
            for line in relay.splitlines()
            if "background recovery backlog drained" in line
        ),
        None,
    )
    if drained is None:
        raise RuntimeError("relay log has no recovery backlog drain marker")
    drain_match = re.search(r"recovery_ms=(\d+)", drained)
    if drain_match is None:
        raise RuntimeError(f"could not parse backlog drain time: {drained}")
    return pending, int(drain_match.group(1)), phase_delays[0], phase_delays[1]


def main():
    args = parse_args()
    validate_logs((args.relay_log, args.subscriber_log, args.publisher_log))
    tbf = load_tbf(args.tbf_log)
    with args.csv_file.open(newline="") as source:
        rows = list(csv.DictReader(source))

    assert_sequences(rows, "audio", MESSAGE_COUNT)
    background_expected = 0 if args.condition == "R" else MESSAGE_COUNT
    background_sequences = assert_sequences(rows, "background", background_expected)

    low_ns = int(tbf["low_rate"]["applied_monotonic_ns"])
    recovery_ns = int(tbf["recovery_rate"]["applied_monotonic_ns"])
    audio = [row for row in rows if row["track"] == "audio"]
    phases = {
        "Audio 0-2s (3 Mbps)": [
            row for row in audio if int(row["send_time_ns"]) < low_ns
        ],
        "Audio 2-4s (1 Mbps)": [
            row
            for row in audio
            if low_ns <= int(row["send_time_ns"]) < recovery_ns
        ],
        "Audio 4s+ (3 Mbps)": [
            row for row in audio if int(row["send_time_ns"]) >= recovery_ns
        ],
    }
    if any(not phase_rows for phase_rows in phases.values()):
        raise RuntimeError(f"one or more Audio phases are empty: {phases}")

    print(f"condition={args.condition}")
    print(
        f"TBF transitions: 3 Mbps initial, 1 Mbps at "
        f"{tbf['low_rate']['elapsed_ms']:.3f} ms, 3 Mbps at "
        f"{tbf['recovery_rate']['elapsed_ms']:.3f} ms"
    )
    describe("Audio overall", audio)
    for name, phase_rows in phases.items():
        describe(name, phase_rows)
    print(f"Audio receive rate: {len(audio) / MESSAGE_COUNT * 100:.3f}%")

    if args.condition != "R":
        background = [row for row in rows if row["track"] == "background"]
        completion_ms = (
            max(int(row["received_time_ns"]) for row in background)
            - min(int(row["send_time_ns"]) for row in background)
        ) / 1_000_000
        pending, drain_ms, low_delay_ms, recovery_delay_ms = relay_background_stats(
            args.relay_log, args.condition, tbf, background_sequences
        )
        print(f"Background receive rate: {len(background) / MESSAGE_COUNT * 100:.3f}%")
        print(f"Background completion: {completion_ms:.3f} ms")
        print(f"Background pending at 4s: {pending}")
        print(f"Background backlog drain after recovery: {drain_ms} ms")
        print(f"Relay observation delay at 1 Mbps: {low_delay_ms} ms")
        print(f"Relay observation delay at 3 Mbps recovery: {recovery_delay_ms} ms")
    else:
        relay_background_stats(
            args.relay_log, args.condition, tbf, background_sequences
        )

    checks = "sequence, errors, and TBF schedule"
    if args.condition != "R":
        checks += ", Background phase timing, and max in-flight"
    print(f"validation: {checks} OK")


if __name__ == "__main__":
    main()
