# Local raw-track experiment

See [RESULTS.md](RESULTS.md) for the controlled congestion experiment and its
subscription-order, payload-size, congestion-window, timing, and path-delay
findings.

This experiment sends two raw tracks through a local `moq-relay`:

- `audio`: 48 kHz, mono, signed 16-bit PCM in 20 ms (1920-byte) chunks
- `background`: deterministic bytes at a configurable nominal bit rate

Both tracks use the same publisher-side `TrackInfo.priority`. Only the subscriber's
`Subscription.priority` changes between modes:

| Mode | audio | background |
| --- | ---: | ---: |
| `off` | 0 | 0 |
| `audio` | 255 | 0 |
| `background` | 0 | 255 |

Higher values have higher priority. The scripts do not configure `tc`, `netem`, or
any other bandwidth limiter. Loopback may be fast enough that priority modes show
no statistically significant latency difference.

## Prerequisites

Use the repository's Nix development shell, then build the local Python bindings:

```bash
nix develop --command just py build
```

The publisher invokes FFmpeg when `--audio` is supplied. FFmpeg decodes the first
audio stream from any supported audio or video file to the PCM format above. With
no `--audio`, it generates zero-valued PCM instead. This keeps the payload
deterministic without adding synchronous signal-generation work to the publisher's
event loop.

## Run

Start the local relay in one terminal:

```bash
nix develop --command just relay
```

Start the publisher in a second terminal. It creates the broadcast, then waits for
subscribers on both tracks before starting either sender:

```bash
nix develop --command uv run --no-sync python \
  py/moq-rs/examples/local_experiment/publisher.py \
  --duration 10 \
  --background-mbps 10 \
  --no-tls-verify
```

To use a file instead of zero-valued PCM, add for example:

```bash
--audio demo/pub/media/bbb.mp4
```

Start the subscriber in a third terminal:

```bash
nix develop --command uv run --no-sync python \
  py/moq-rs/examples/local_experiment/subscriber.py \
  --mode off \
  --csv /tmp/moq-off.csv \
  --no-tls-verify
```

Repeat with `--mode audio` or `--mode background`. The default relay URL is
`https://localhost:4443` and the default broadcast is `research/local`.
`--no-tls-verify` is necessary for the demo relay's generated self-signed
certificate unless its fingerprint or root is configured separately. Verification
remains enabled when the flag is omitted.

The subscriber requests `audio` first by default. Pass
`--subscribe-order background-first` to reverse the request order when measuring
whether equal-priority delivery depends on subscription order.

The publisher defaults to 10 Mbps of background traffic. Change the rate with
`--background-mbps` and the total raw background payload size with
`--background-frame-bytes`.

Both track schedules start together by default. Use `--audio-phase-ms` or
`--background-phase-ms` to delay every deadline for one track by a fixed amount
when measuring sensitivity to the phase between otherwise equal periodic tracks.

The scripts use `127.0.0.1:4444` for a completion acknowledgement after the
subscriber has flushed every CSV row. This keeps the publisher's MoQ connection
open until the subscriber has received the final frame. If that port is already
in use, pass the same alternative `--ack-host` and `--ack-port` to both scripts.
This acknowledgement is not part of the measured traffic or latency interval.

## Payload and CSV

Every payload starts with a network-byte-order header containing:

1. magic (`MLAB`)
2. version (`1`)
3. track kind (`1` for audio, `2` for background)
4. a reserved zero field
5. sequence number
6. `time.monotonic_ns()` at send time

The subscriber receives groups in arrival order and reads each group's frames in
a separate async task. A delayed group therefore does not prevent the application
from observing later group arrivals. It writes:

```text
track,sequence,send_time_ns,receive_time_ns,group_receive_wall_time_ns,receive_wall_time_ns,latency_us,frame_timestamp_us,payload_bytes
```

The one-way delay is an application-level measurement from the Python publisher,
through the relay, to the Python subscriber. Monotonic timestamps can only be
compared when publisher and subscriber run on the same host. They are not suitable
for measurements between machines because their monotonic clock epochs are unrelated.
The wall-clock group and frame receive timestamps support correlation with relay
traces from the same host and are not used to calculate latency. Their difference
measures how long the concurrently consumed group waited for its frame payload
after `recv_group()` returned.

The publisher declares each track's final sequence before sending, then cleanly
finishes both tracks and the broadcast after the subscriber acknowledges its CSV.
Ctrl+C triggers the same cleanup on a best-effort basis.
