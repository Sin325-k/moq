# Adaptive C RECOVERY pilot

This pilot extends the RTT-driven Minimal C condition with a controlled Background probe. It uses the same Audio and Background publishers (400 Groups each, 1945 bytes every 20 ms, priorities 255 and 0), Background in-flight limit 1, and 3 -> 1 -> 3 Mbps TBF schedule. The Relay receives no TBF marker, rate, or scheduled switch time. The analyzer alone reads the TBF JSONL.

The first 1 s of RTT samples after the first Background Group sets the median baseline. NORMAL -> CONGESTED still requires three consecutive 10 ms samples above baseline +1 ms. CONGESTED holds new Background Groups in the pending queue. After RTT stays at or below that threshold for 150 ms, any started Background Group has finished, and a 300 ms cooldown after a failed probe has elapsed, the Relay enters RECOVERY. Low RTT only permits a probe. It does not establish that the path has regained 3 Mbps.

RECOVERY starts at Stage 1 (60 ms between Background starts), then Stage 2 (40 ms), then Stage 3 (20 ms). Each stage must last at least 300 ms and complete at least 5, 7, and 14 Groups respectively, with none still in flight when it advances. In-flight remains at most one. Three consecutive RTT samples above baseline +1 ms return to CONGESTED immediately, with new starts stopped and a 300 ms cooldown. Successful Stage 3 returns to NORMAL. After that, a 20 ms start interval prevents the pending backlog from bursting. These values are initial pilot settings, not calibrated policy thresholds.

`run_adaptive_c_recovery_pilot.sh` creates only files containing `adaptive_c_recovery_C_pilot1`, refuses every existing output, and uses a dedicated subscriber that waits up to 20 s after Audio completion for Background to finish. The CSV records 10 ms samples and `GROUP_START`/`GROUP_COMPLETE`/`GROUP_FAILED` events with wall and monotonic timestamps, state, RTT, baseline, threshold, consecutive-high count, stable duration, stage, start interval, Group sequence, pending and in-flight counts, and transition time and reason. `analyze_adaptive_c_recovery.py` compares transitions with offline TBF ground truth and reports the 1 Mbps probe behavior, 3 Mbps stage progression, Audio delay, and Background completion or remaining backlog. An incomplete Background run remains analyzable.

From the normal experiment terminal, after the existing namespace and `.venv` are ready:

```bash
cd /home/shinya/moq/experiment/audio_priority
sudo -v
RUN_LABEL=pilot1 ./run_adaptive_c_recovery_pilot.sh
```

Existing Minimal C, calibration, and R/A/B artifacts use different names and are left in place.

## Five-run reproducibility check

`run_adaptive_c_recovery_measurements.sh` keeps `pilot1` and uses separate `run2` through `run5` names. It verifies all seven artifacts and reruns the analyzer before skipping a complete run. Missing, empty, inconsistent, or invalid artifacts stop the runner without overwriting anything. After five valid runs, `aggregate_adaptive_c_recovery_runs.py` writes `comparison_adaptive_c_recovery_runs1-5.txt` with exclusive creation. The summary is regenerated and compared if it already exists.

```bash
cd /home/shinya/moq/experiment/audio_priority
./run_adaptive_c_recovery_measurements.sh --check-resume
sudo -v
./run_adaptive_c_recovery_measurements.sh
```

The report covers detection, 1 Mbps probe and false-NORMAL behavior, stage progression after 3 Mbps restoration, Audio, Background, and validation. The control settings above remain unchanged during this five-run check.

## Five-run result at 3 -> 1 -> 3 Mbps

`pilot1` and `run2` through `run5` completed. The saved per-run analyzer reports and `comparison_adaptive_c_recovery_runs1-5.txt` were reproduced exactly from the raw QUIC CSV, TBF JSONL, subscriber CSV, and Relay logs. Values below are the five-run mean ± between-run sample SD.

| Metric | Result |
| --- | ---: |
| 1 Mbps switch to CONGESTED | 71.369 ± 11.332 ms |
| 1 Mbps Audio mean / p95 | 4.157 ± 0.244 / 14.361 ± 2.049 ms |
| 3 Mbps switch to NORMAL | 1050.569 ± 28.065 ms |
| Background backlog drain after restoration | 7624.326 ± 85.455 ms |

All five runs had three low-rate RECOVERY probes return to CONGESTED, zero false NORMAL transitions before restoration, and Stage 1 -> Stage 2 -> Stage 3 -> NORMAL after restoration. Audio and Background both reached 400/400 in every run. The state transition sequence matched in all five runs.

## Bandwidth robustness at 0.8 / 1.0 / 1.2 Mbps

The same Adaptive C control was evaluated in five runs at each bottleneck rate under fixed local 3 -> bottleneck -> 3 Mbps conditions. D1 (0.8 Mbps) and D2 (1.2 Mbps) use the procedure in `ADAPTIVE_C_BANDWIDTH.md`; 1.0 Mbps uses the Recovery C runs above. The saved reports are `comparison_adaptive_c_D1_0p8mbps_runs1-5.txt`, `comparison_adaptive_c_recovery_runs1-5.txt`, `comparison_adaptive_c_D2_1p2mbps_runs1-5.txt`, and `comparison_adaptive_c_bandwidth_0p8_1p0_1p2.txt`. Values are five-run means ± between-run sample SD.

| Bottleneck | Detection ms | Audio mean / p95 ms | Low-rate RECOVERY behavior | False NORMAL | Restoration to NORMAL ms | Backlog drain ms | Audio / Background |
| --- | ---: | ---: | --- | --- | ---: | ---: | --- |
| 0.8 Mbps D1 | 79.310 ± 8.556 | 68.879 ± 1.900 / 92.012 ± 2.072 | 0 entries in all 5 runs | 0/5 | 1735.138 ± 46.466 | 8712.365 ± 156.174 | 400/400 each in all 5 runs |
| 1.0 Mbps Recovery C | 71.369 ± 11.332 | 4.157 ± 0.244 / 14.361 ± 2.049 | 3 entries and 3 returns to CONGESTED per run | 0/5 | 1050.569 ± 28.065 | 7624.326 ± 85.455 | 400/400 each in all 5 runs |
| 1.2 Mbps D2 | 77.553 ± 14.517 | 2.918 ± 0.149 / 6.881 ± 0.382 | Stage 2 reached in all 5 runs | 0/5 | 717.784 ± 444.675 | 7005.127 ± 344.944 | 400/400 each in all 5 runs |

The three rates show repeatable differences under these fixed local conditions. Audio latency rises sharply at 0.8 Mbps despite complete delivery, while 1.2 Mbps permits Stage 2 probes during the bottleneck. These measurements do not establish behavior on arbitrary network paths. The next research task is to shorten Background backlog drain while preserving Audio protection. Recovery Boost is the candidate method to design and pilot.
