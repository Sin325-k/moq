# QUIC stats calibration

This pilot uses the existing concurrent A traffic: 400 Audio Groups and 400 Background Groups, 1945-byte payloads every 20 ms, Background max in-flight 1, and priorities 255/0. The TBF schedule is 3 Mbps for 0-2 s, 1 Mbps for 2-4 s, and 3 Mbps afterward. The Relay receives no TBF markers or rate information. It does not suppress Background.

In the normal experiment terminal, with the existing `.venv`, relay configuration, network namespace, and sudo credentials ready:

```bash
cd /home/shinya/moq/experiment/audio_priority
sudo -v
RUN_LABEL=pilot1 ./run_stats_calibration_pilot.sh
```

The runner refuses to overwrite any output. Use a new `RUN_LABEL` for another pilot. Its outputs include `quic_stats_calibration_A_pilot1.csv`, `subscriber_stats_calibration_A_pilot1.csv`, `tbf_stats_calibration_A_pilot1.jsonl`, `analysis_stats_calibration_A_pilot1.txt`, and separate Relay, Publisher, and Subscriber logs.

The QUIC CSV is sampled at a target 10 ms interval on the Relay-to-Subscriber connection while its Background subscription is active. `monotonic_ns` is elapsed nanoseconds since that logger opened, while `wall_time_ns` is Unix nanoseconds. The offline analyzer aligns samples to the TBF JSONL using wall-clock timestamps. Blank transport fields mean unavailable; the first counter deltas are blank. `inferred_cwnd_bytes` is `estimated_send_rate_bps * rtt_ns / 8,000,000,000`. With the current web-transport-quinn implementation, this reverses its cwnd/RTT rate estimate and is not a direct cwnd measurement. `background_pending` and `background_in_flight` are the counts in the Background subscription at sample time.

The analyzer reports per-phase RTT, rate, inferred cwnd, loss, and actual bytes-sent rate; normal-phase RTT baselines; threshold detection delays with consecutive-sample and duration rules; normal-phase false positives; rate and loss detection delays; and recovery delays. A loss delta is assigned to the phase of the reporting sample, so a sample interval crossing a TBF boundary can contain earlier loss.

After pilot1, run the four remaining measurements from the same terminal:

```bash
cd /home/shinya/moq/experiment/audio_priority
./run_stats_calibration_measurements.sh --check-resume
sudo -v
./run_stats_calibration_measurements.sh
```

The runner uses labels `run2` through `run5`. Before starting any traffic, it checks all seven artifacts for pilot1 and each later run. A complete run is revalidated and skipped; a partial or empty run stops the runner. It never overwrites `comparison_stats_calibration_runs1-5.txt`. When all five runs are complete, the aggregator creates that report exclusively. The report compares between-run RTT, detection latency, normal-phase false positives, loss, and recovery without setting a control threshold.

## Five-run results

The `pilot1` and `run2` through `run5` A-condition measurements are complete. The full per-run and threshold tables are in `comparison_stats_calibration_runs1-5.txt`. Values below are five-run mean ± between-run sample SD.

| TBF phase | RTT median | RTT p95 |
| --- | ---: | ---: |
| 0-2 s, 3 Mbps | 0.171 ± 0.012 ms | 0.244 ± 0.020 ms |
| 2-4 s, 1 Mbps | 83.924 ± 2.863 ms | 92.643 ± 0.902 ms |
| 4 s onward, 3 Mbps | 0.181 ± 0.008 ms | 7.966 ± 0.303 ms |

Using each run's normal-phase median RTT as its baseline, `RTT > baseline +1 ms` for three consecutive samples detected the 1 Mbps transition in 69.483 ± 9.911 ms (59.241-82.295 ms). It had zero normal-phase false-positive episodes and zero raw false-positive samples out of 1002. The corresponding `+2 ms` rule took 89.357 ± 9.947 ms and also had zero false positives. Thus `+1 ms` with three consecutive samples is the current NORMAL-to-CONGESTED candidate: it detected earlier with the same observed false-positive count. This is a calibration candidate, not an implemented control threshold.

The multiplier rules had normal-phase false positives. At three consecutive samples, ×1.25, ×1.5, and ×2.0 produced 7, 5, and 4 false-positive episodes respectively. No byte or packet loss occurred in any of the five runs, so loss cannot serve as an early trigger in these measurements. After 3 Mbps restoration, RTT returned to at most baseline +1 ms after 1107.241 ± 29.597 ms, or to at most baseline +2 ms after 1045.119 ± 23.902 ms. Recovery therefore needs separate evaluation when C is designed.
