# Adaptive C bandwidth pilots D1 and D2

These pilots keep the checkpointed Adaptive C RECOVERY control unchanged. D1 uses 3 -> 0.8 -> 3 Mbps and D2 uses 3 -> 1.2 -> 3 Mbps. The low-rate period begins at 2 s and ends at 4 s. Both use 400 Audio and 400 Background Groups, 1945-byte payloads every 20 ms, Audio priority 255, Background priority 0, and Background in-flight limit 1.

The TBF controller alone receives `--bottleneck-kbit` (800 or 1200). Its default remains 1000 for the existing 1 Mbps experiments. The runner does not create marker files. It removes Oracle marker environment variables before starting the Relay. The Relay writes its QUIC CSV to an opaque temporary `quic.csv` path. After the Relay stops, the runner moves that file to a condition-named artifact. This keeps the condition label and bandwidth value out of the Relay's configuration and control inputs. The TBF JSONL is read only by the offline analyzer.

Each pilot creates seven separate files with `adaptive_c_D1_0p8mbps_pilot1` or `adaptive_c_D2_1p2mbps_pilot1` in the name: QUIC CSV, TBF JSONL, publisher log, subscriber log and CSV, Relay log, and analysis report. The runner refuses any existing output. If a process fails, the logs and any collected raw artifacts remain for diagnosis; the runner does not claim pilot success.

Run each pilot from the normal experiment terminal after preparing the existing `.venv`, network namespace, Relay configuration, and sudo credentials:

```bash
cd /home/shinya/moq/experiment/audio_priority
sudo -v
./run_adaptive_c_bandwidth_pilot.sh D1
./run_adaptive_c_bandwidth_pilot.sh D2
```

Run D2 separately after reviewing D1. The offline report records detection, Background stop, low-rate Audio latency and probe behavior, false NORMAL, restored-rate stage progression, Audio and Background delivery, backlog drain, and the full state sequence. Missing detection or incomplete delivery remains visible as an observation. Compare the pilot values with the existing five-run 3 -> 1 -> 3 Mbps reference in the report.

## Five-run measurements

The measurement runner retains each completed pilot1, then runs run2 through run5 in order for the chosen condition. Every run has its own seven artifacts and the pilot runner refuses existing output paths. Before any communication, the measurement runner checks all five run slots. It reanalyzes each completed run from QUIC CSV, TBF JSONL, Subscriber CSV, and Relay log; compares the result byte-for-byte with its saved analysis; and validates delivery, marker independence, state progression, and the in-flight limit. Partial or empty artifacts, analysis differences, validation failures, and gaps in the run sequence stop the runner without changing the files. D1's low-rate RECOVERY count and D2's Stage 2 progression remain observations, not validation requirements.

Inspect the resume decision without starting communication:

```bash
cd /home/shinya/moq/experiment/audio_priority
./run_adaptive_c_bandwidth_measurements.sh D1 --check-resume
./run_adaptive_c_bandwidth_measurements.sh D2 --check-resume
```

Start one condition at a time from the normal experiment terminal, with the existing namespace, Relay setup, `.venv`, and sudo credentials available:

```bash
cd /home/shinya/moq/experiment/audio_priority
sudo -v
./run_adaptive_c_bandwidth_measurements.sh D1
```

```bash
cd /home/shinya/moq/experiment/audio_priority
sudo -v
./run_adaptive_c_bandwidth_measurements.sh D2
```

After all five runs of a condition validate, its report is created exclusively as `comparison_adaptive_c_D1_0p8mbps_runs1-5.txt` or `comparison_adaptive_c_D2_1p2mbps_runs1-5.txt`. An existing report must match the regenerated report exactly. Once both condition reports exist, the runner also creates `comparison_adaptive_c_bandwidth_0p8_1p0_1p2.txt` from validated D1, existing 1 Mbps Recovery C, and D2 raw artifacts. The reports show per-run values and mean, sample SD, min, and max; they do not assume the pilot1 behavior will repeat.
