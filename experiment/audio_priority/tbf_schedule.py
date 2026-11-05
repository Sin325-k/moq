import argparse
import json
import signal
import subprocess
import time
from pathlib import Path


DEVICE = "veth-host"
BURST = "4Kb"
LATENCY = "400ms"
SAMPLE_INTERVAL = 0.1


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-file", required=True, type=Path)
    parser.add_argument("--ready-file", required=True, type=Path)
    parser.add_argument("--stop-file", required=True, type=Path)
    parser.add_argument("--low-marker-file", type=Path)
    parser.add_argument("--recovery-marker-file", type=Path)
    parser.add_argument("--bottleneck-kbit", type=int, default=1000)
    args = parser.parse_args()
    if not 0 < args.bottleneck_kbit < 3000:
        parser.error("--bottleneck-kbit must be between 1 and 2999")
    return args


def tc(*args):
    return subprocess.run(
        ["tc", *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    ).stdout


def stats():
    return tc("-s", "qdisc", "show", "dev", DEVICE).strip()


def emit(event, **fields):
    print(
        json.dumps(
            {
                "event": event,
                "monotonic_ns": time.monotonic_ns(),
                "wall_time_ns": time.time_ns(),
                **fields,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def change_rate(rate_kbit, event, experiment_start_ns=None, marker_file=None):
    requested_ns = time.monotonic_ns()
    tc(
        "qdisc",
        "change",
        "dev",
        DEVICE,
        "root",
        "tbf",
        "rate",
        f"{rate_kbit}Kbit",
        "burst",
        BURST,
        "latency",
        LATENCY,
    )
    applied_ns = time.monotonic_ns()
    applied_wall_time_ns = time.time_ns()
    if marker_file is not None:
        marker_file.write_text(
            f"{applied_ns} {applied_wall_time_ns}\n", encoding="utf-8"
        )
    emit(
        event,
        rate_kbit=rate_kbit,
        requested_monotonic_ns=requested_ns,
        applied_monotonic_ns=applied_ns,
        applied_wall_time_ns=applied_wall_time_ns,
        elapsed_ms=(
            None
            if experiment_start_ns is None
            else (applied_ns - experiment_start_ns) / 1_000_000
        ),
        qdisc=stats(),
    )


def main():
    args = parse_args()
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    current_rate = None
    try:
        change_rate(3000, "initial_rate")
        current_rate = 3000
        args.ready_file.write_text("ready\n", encoding="utf-8")

        while not stopping and not args.stop_file.exists() and not args.start_file.exists():
            time.sleep(0.01)
        if stopping or args.stop_file.exists():
            return

        experiment_start_ns = int(args.start_file.read_text(encoding="utf-8").strip())
        emit("schedule_started", experiment_start_monotonic_ns=experiment_start_ns)
        low_applied = False
        recovery_applied = False
        next_sample_ns = time.monotonic_ns()

        while not stopping and not args.stop_file.exists():
            now_ns = time.monotonic_ns()
            elapsed = (now_ns - experiment_start_ns) / 1_000_000_000
            if not low_applied and elapsed >= 2:
                change_rate(
                    args.bottleneck_kbit,
                    "low_rate",
                    experiment_start_ns,
                    args.low_marker_file,
                )
                current_rate = args.bottleneck_kbit
                low_applied = True
            if not recovery_applied and elapsed >= 4:
                change_rate(
                    3000,
                    "recovery_rate",
                    experiment_start_ns,
                    args.recovery_marker_file,
                )
                current_rate = 3000
                recovery_applied = True
            if now_ns >= next_sample_ns:
                emit(
                    "sample",
                    elapsed_ms=(now_ns - experiment_start_ns) / 1_000_000,
                    qdisc=stats(),
                )
                next_sample_ns += int(SAMPLE_INTERVAL * 1_000_000_000)
            time.sleep(0.01)
    finally:
        if current_rate is not None and current_rate != 3000:
            change_rate(3000, "cleanup_rate")


if __name__ == "__main__":
    main()
