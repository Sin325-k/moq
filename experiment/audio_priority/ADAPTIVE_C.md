# Minimal adaptive C pilot

Condition C sends the same 400 Audio and 400 Background Groups as condition A, with 1945-byte payloads every 20 ms, Audio priority 255, Background priority 0, and Background max in-flight 1. The TBF schedule is 3 Mbps for 0-2 s, 1 Mbps for 2-4 s, and 3 Mbps afterward.

The Relay starts its baseline window when the first Background Group arrives. It samples the Relay-to-Subscriber connection RTT every 10 ms, collects the first 1 s of available RTT samples, and freezes their median as the baseline. At least 20 samples are required. Later samples cannot change the baseline. In NORMAL, three consecutive samples strictly above `baseline + 1 ms` move C to CONGESTED. A missing or lower sample resets the count. C then holds new Background Groups in the pending queue while its one already-started Group can finish. Audio continues. This minimal pilot has no RECOVERY transition or Background resume rule.

The Relay receives no TBF marker, current TBF rate, or switch time. Only the offline analyzer reads the TBF JSONL to measure detection delay. The Oracle B marker path is disabled when C is active.

From the normal experiment terminal, with the existing `.venv`, network namespace, relay configuration, and sudo credentials ready:

```bash
cd /home/shinya/moq/experiment/audio_priority
sudo -v
RUN_LABEL=pilot1 ./run_adaptive_c_pilot.sh
```

All output names contain `adaptive_c_C_pilot1`; the runner refuses to overwrite any existing output. The Relay's `quic_adaptive_c_C_pilot1.csv` records relative monotonic and Unix timestamps, RTT, frozen baseline, threshold, consecutive-high count, state, pending and in-flight Background counts, and transition time and reason. Separate files hold the TBF JSONL, subscriber CSV, analysis, and process logs.

The C subscriber requires all 400 Audio Groups and records however many Background Groups complete. The analyzer reports the NORMAL-to-CONGESTED transition, elapsed time from the 1 Mbps switch, Background stop time and pending count, 1 Mbps Audio mean/p95/max, and receive rates. Background completion time cannot be compared with Oracle B because C has no resume policy in this pilot.

## Five-run measurement

Keep `pilot1` as the first run. To check the existing artifacts and then measure `run2` through `run5` in order:

```bash
cd /home/shinya/moq/experiment/audio_priority
./run_adaptive_c_measurements.sh --check-resume
sudo -v
./run_adaptive_c_measurements.sh
```

The measurement runner checks all seven artifacts for each run before starting traffic. A complete run is reanalyzed and skipped. A missing, partial, empty, or invalid run stops the runner without overwriting it. Each new run uses `run_adaptive_c_pilot.sh` and its own `adaptive_c_C_runN` filenames. After all five runs validate, `aggregate_adaptive_c_runs.py` creates `comparison_adaptive_c_runs1-5.txt` exclusively. The report separates Detection, Background stop, Audio, Background, R/A/B/C comparison, and Validation, including between-run sample SD and min/max.

## Five-run result

`pilot1` and `run2` through `run5` completed and passed analyzer revalidation. Values below are the mean ± between-run sample SD. See `comparison_adaptive_c_runs1-5.txt` for each run and the validation table.

| Metric | Five-run result |
| --- | ---: |
| Baseline RTT median | 0.174 ± 0.017 ms |
| Detection threshold | 1.174 ± 0.017 ms |
| 1 Mbps switch to detection | 71.987 ± 12.980 ms (min 57.366, max 81.900) |
| 1 Mbps Audio mean | 3.425 ± 0.047 ms |
| 1 Mbps Audio p95 | 13.067 ± 0.218 ms |
| 1 Mbps Audio max | 25.780 ± 0.440 ms |
| Background received | 25.150 ± 0.137% |

All five runs detected congestion, stopped new Background Groups on the detection tick, and received Audio 400/400. C reduced the 1 Mbps Audio mean by 95.78% relative to A's 81.101 ms. Oracle B's corresponding mean was 2.113 ms. Background completion is not comparable with B because this minimal C remains CONGESTED and never resumes Background.

The next phase is RECOVERY design. A quiet RTT after Background stops cannot establish that the path regained 3 Mbps: Audio-only load is about 0.78 Mbps, below the 1 Mbps constrained rate. Recovery therefore needs a bounded Background probe before normal Background sending can resume. No RECOVERY control is implemented in this checkpoint.
