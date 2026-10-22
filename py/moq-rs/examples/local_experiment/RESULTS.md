# Controlled congestion results

This report measures how subscriber priority, subscription order, and group size
affect delivery through a constrained relay-to-subscriber path.

## Overall findings

The experiments separate three places where traffic can wait: the MoQ publisher,
Quinn's priority-aware stream scheduler, and a FIFO after Quinn has emitted a
datagram. The results support the following conclusions:

1. Subscriber priority reaches Quinn with the intended track ordering. Reversing
   subscription order does not swap or discard an explicit priority.
2. Priority changes latency only while competing stream data remains eligible
   inside Quinn. A fixed congestion window produced up to a 70% selected-track
   median improvement with no loss or retransmission, while a deep external FIFO
   produced no material benefit.
3. Packet loss is not required. Heavy loss made priority visible because recovery
   repeatedly returned stream ranges to Quinn's scheduler, but loss was one way to
   create internal contention rather than the mechanism itself.
4. The benefit is capacity redistribution, not a general speedup. The selected
   track finishes sooner and receives the larger share of constrained capacity.
5. The boundary is sensitive to packet admission and timing. In the synchronized
   1,944-byte workload, the median benefit ended between 12,750 and 13,000 bytes of
   fixed congestion window. At 13,000 bytes, delaying either track by 1 ms exposed
   a priority opportunity for that delayed track which was absent for the earlier
   track.
6. Priority orders streams that are simultaneously eligible. It does not reserve
   bandwidth, preempt bytes already emitted, or preserve its meaning inside a
   downstream FIFO.
7. Path delay changes whether a fixed send window sustains that eligibility. With
   a 13,000-byte window, the selected-track median benefit was at most 0.5% at
   25 ms, peaked at about 70% at 75 ms, fell to about 19% at 100 ms, and was
   about 12% at 150 ms with concurrent group reads.
   The benefit was largest when the window could sustain one selected track but
   not both tracks together.
8. The same three regimes appear when delay stays fixed and the window changes.
   At 75 ms, the selected-track median benefit was about 7% with a 6,000-byte
   window, about 70% with 13,000 bytes, and zero with 20,000 bytes.
9. An apparent improvement of the non-selected track was caused by the original
   subscriber reading one group to completion before requesting the next group.
   Reading group streams concurrently restores the expected capacity tradeoff:
   the selected median improves by about 19%, while the non-selected median gets
   about 14% worse.
10. Reversing subscription order under the corrected reader changed no p50 cell
    significantly. Every order-effect confidence interval included zero.

These are controlled local results, not universal byte or timing thresholds. The
exact boundary depends on RTT, payload size, MTU, pacing, control traffic, and the
phase between track production deadlines.

## Method

The publisher connected directly to a local `moq-relay`. The subscriber connected
through a user-space UDP proxy, preserving QUIC while shaping only packets from the
relay to the subscriber. The proxy applied:

- 2 Mbps serialization rate
- 50 ms one-way delay
- a 100-packet drop-tail queue

Each run published for two seconds at an offered load above the bottleneck:

- audio: 48 kHz mono signed 16-bit PCM, one 1,944-byte payload every 20 ms
- background: deterministic payloads at 4 Mbps

The two tracks used equal publisher-side priority. Subscriber priority was one of
`off`, `audio`, or `background`. Subscription order was either `audio-first` or
`background-first`; the first subscription completed before the second began. Each
priority and order combination ran five times in a deterministic randomized order.
Every run used a unique broadcast name to prevent relay linger from carrying state
between runs.

Two group-size conditions were tested:

1. Original size: 1,944-byte audio payloads and 32,768-byte background payloads.
2. Equal size: 1,944-byte payloads on both tracks. The background frame frequency
   increased to preserve its 4 Mbps offered rate.

The tables pool every received frame from the five repetitions. Latency is the
publisher's monotonic send timestamp to the subscriber's monotonic receive
timestamp. Both processes ran on the same host.

The experiments through the GSO control used the original sequential group
reader. The final receiver-boundary control identified its per-track
head-of-line effect and changed the example subscriber to keep receiving groups
while their frame streams are read concurrently. Results from both readers are
reported separately below.

## Original group sizes

Each condition received 500 audio frames and 155 background frames.

| Order | Priority | Track | p50 | p95 | p99 |
| --- | --- | --- | ---: | ---: | ---: |
| audio first | off | audio | 247 ms | 705 ms | 939 ms |
| audio first | off | background | 3,768 ms | 4,479 ms | 4,536 ms |
| audio first | audio | audio | 222 ms | 687 ms | 879 ms |
| audio first | audio | background | 3,810 ms | 4,582 ms | 4,607 ms |
| audio first | background | audio | 4,030 ms | 4,749 ms | 4,894 ms |
| audio first | background | background | 3,512 ms | 4,341 ms | 4,532 ms |
| background first | off | audio | 160 ms | 594 ms | 876 ms |
| background first | off | background | 3,767 ms | 4,526 ms | 4,590 ms |
| background first | audio | audio | 159 ms | 551 ms | 783 ms |
| background first | audio | background | 3,879 ms | 4,531 ms | 4,656 ms |
| background first | background | audio | 3,980 ms | 4,697 ms | 4,775 ms |
| background first | background | background | 3,391 ms | 3,607 ms | 3,876 ms |

Reversing subscription order did not reverse which track was favored. Audio was
slightly faster when subscribed second. Explicit audio priority added little because
small audio groups already completed quickly at equal priority. Background priority
made audio median latency approximately four seconds while moderately improving
background latency.

## Equal group sizes

Each condition received 500 audio frames and 2,575 background frames.

| Order | Priority | Track | p50 | p95 | p99 |
| --- | --- | --- | ---: | ---: | ---: |
| audio first | off | audio | 3,938 ms | 4,850 ms | 5,022 ms |
| audio first | off | background | 3,689 ms | 4,533 ms | 4,663 ms |
| audio first | audio | audio | 2,189 ms | 3,122 ms | 3,306 ms |
| audio first | audio | background | 3,324 ms | 4,698 ms | 4,921 ms |
| audio first | background | audio | 3,994 ms | 4,894 ms | 5,094 ms |
| audio first | background | background | 3,728 ms | 4,498 ms | 4,647 ms |
| background first | off | audio | 3,994 ms | 4,899 ms | 5,095 ms |
| background first | off | background | 3,690 ms | 4,496 ms | 4,649 ms |
| background first | audio | audio | 1,953 ms | 2,771 ms | 2,878 ms |
| background first | audio | background | 3,312 ms | 4,663 ms | 4,861 ms |
| background first | background | audio | 4,071 ms | 4,859 ms | 5,057 ms |
| background first | background | background | 3,784 ms | 4,543 ms | 4,660 ms |

With equal group sizes, subscription order had little effect. Audio priority reduced
audio median latency by 44% with audio first and 51% with background first. It also
reduced audio p95 by 36% and 43%, respectively. Background priority changed little
because background already supplied most groups and most offered bytes.

## Conclusions

Subscription order was not the source of the original audio advantage. Group size
was the dominant factor: small audio groups could finish between fragments of the
much larger background groups even at equal priority. Once both tracks used equal
group sizes, explicit audio priority produced a large and repeatable latency
improvement regardless of subscription order.

Priority therefore works, but observed latency depends on both the configured
priority and the traffic shape. A comparison that leaves group sizes unequal can
mistake natural completion behavior for a priority effect.

All 60 measured runs completed with exact contiguous sequences and no duplicate or
missing application frames. QUIC recovered packets dropped by the proxy queue. The
equal-size condition generated more concurrent groups and substantially more proxy
drops, so comparisons between the two size conditions include that packetization
effect. Comparisons among priorities within one size condition use the same shaper
and traffic shape.

Longer preliminary runs were excluded. Under sustained overload, low-priority groups
exceeded the producer's retention window and ended with `old` or `dropped` protocol
errors. The two-second duration keeps both tracks complete, making latency rather
than retention failure the measured outcome.

## Symmetric traffic with a deep queue

A follow-up removed the remaining traffic-rate and packet-loss asymmetries. Both
tracks used identical 1,944-byte payloads at identical 20 ms intervals, for an
offered rate of approximately 0.778 Mbps each. The combined 1.555 Mbps load passed
through a 1 Mbps, 50 ms shaper with a 1,000-packet queue. The deep queue was chosen
to avoid packet loss. All six priority and subscription-order combinations ran ten
times in deterministic randomized order.

Each condition received 1,000 audio frames and 1,000 background frames.

| Order | Priority | Track | p50 | p95 | p99 |
| --- | --- | --- | ---: | ---: | ---: |
| audio first | off | audio | 719 ms | 1,291 ms | 1,341 ms |
| audio first | off | background | 708 ms | 1,285 ms | 1,336 ms |
| audio first | audio | audio | 720 ms | 1,293 ms | 1,345 ms |
| audio first | audio | background | 712 ms | 1,286 ms | 1,336 ms |
| audio first | background | audio | 719 ms | 1,290 ms | 1,343 ms |
| audio first | background | background | 710 ms | 1,285 ms | 1,337 ms |
| background first | off | audio | 719 ms | 1,294 ms | 1,345 ms |
| background first | off | background | 711 ms | 1,288 ms | 1,338 ms |
| background first | audio | audio | 719 ms | 1,296 ms | 1,344 ms |
| background first | audio | background | 710 ms | 1,284 ms | 1,339 ms |
| background first | background | audio | 720 ms | 1,291 ms | 1,341 ms |
| background first | background | background | 706 ms | 1,283 ms | 1,336 ms |

The proxy dropped no UDP packets in any of the 60 runs. Every application sequence
was complete and contiguous. Mean completion time was 3.881 to 3.887 seconds across
the six conditions.

Neither subscriber priority nor subscription order produced a material difference.
All medians were between 706 and 720 ms, and all p95 values were between 1,283 and
1,296 ms. Selecting audio priority changed audio median latency by less than 0.2%
relative to `off`. Selecting background priority changed background median latency
by less than 0.7% relative to `off`.

This result does not show that priority is generally ineffective. The shaper sits
outside the QUIC endpoint. Its deep queue accepts the relay's emitted datagrams
without loss and can absorb the entire short-run excess load. That moves contention
into the proxy queue instead of forcing the relay's stream scheduler to choose what
to emit next. Once datagrams enter the external FIFO, their MoQ priority is no longer
visible to the shaper.

The shallow-queue experiments created loss and delayed acknowledgements, which fed
congestion back to QUIC and exposed priority differences. The deep-queue experiment
shows that an external FIFO can turn priority into bufferbloat shared nearly equally
by both tracks. A clean no-loss scheduler test requires backpressure at or before the
QUIC sender, such as kernel traffic control on the relay's egress interface or a
transport test harness whose send operation blocks at the configured rate.

## Symmetric queue-depth sweep

A second follow-up kept the symmetric traffic and 1 Mbps link unchanged while
reducing the proxy's drop-tail queue. Subscription order was fixed to audio first,
since the deep-queue experiment showed no ordering effect. Each priority mode ran
five times at queue depths 200, 100, 50, and 20. The existing ten-run, 1,000-packet
result is included as the no-loss baseline.

The traffic remained identical on both tracks:

- 1,944-byte payloads
- one group every 20 ms
- approximately 0.778 Mbps per track
- two seconds and 100 groups per track per run

### Packet loss and delivery

The proxy drop percentage counts every attempted relay-to-subscriber datagram,
including retransmissions. Missing frames are application groups absent from the
CSV after otherwise clean subscription completion.

| Queue | Mode | Proxy drop | Received / expected | Missing frames |
| ---: | --- | ---: | ---: | ---: |
| 1,000 | off | 0.00% | 2,000 / 2,000 | 0 |
| 1,000 | audio | 0.00% | 2,000 / 2,000 | 0 |
| 1,000 | background | 0.00% | 2,000 / 2,000 | 0 |
| 200 | off | 3.81% | 999 / 1,000 | 1 |
| 200 | audio | 4.21% | 998 / 1,000 | 2 |
| 200 | background | 3.79% | 999 / 1,000 | 1 |
| 100 | off | 24.76% | 999 / 1,000 | 1 |
| 100 | audio | 24.69% | 1,000 / 1,000 | 0 |
| 100 | background | 24.26% | 1,000 / 1,000 | 0 |
| 50 | off | 50.71% | 997 / 1,000 | 3 |
| 50 | audio | 50.51% | 1,000 / 1,000 | 0 |
| 50 | background | 51.26% | 1,000 / 1,000 | 0 |
| 20 | off | 74.83% | 1,000 / 1,000 | 0 |
| 20 | audio | 74.73% | 1,000 / 1,000 | 0 |
| 20 | background | 75.06% | 1,000 / 1,000 | 0 |

The non-monotonic missing-frame count is expected for a small sample of a
partial-reliability protocol. A smaller queue caused more retransmission attempts,
but whether a particular group missed its useful delivery window depended on loss
timing rather than only the aggregate drop percentage.

### Median latency

Each latency cell is `audio / background` in milliseconds.

| Queue | off | audio priority | background priority |
| ---: | ---: | ---: | ---: |
| 1,000 | 719 / 708 | 720 / 712 | 719 / 710 |
| 200 | 716 / 708 | 710 / 710 | 721 / 706 |
| 100 | 1,299 / 1,383 | 1,257 / 1,383 | 1,358 / 1,243 |
| 50 | 1,473 / 1,668 | 1,256 / 1,721 | 1,784 / 1,261 |
| 20 | 1,813 / 1,856 | 1,175 / 1,990 | 2,034 / 967 |

Priority had no material effect at zero loss or approximately 4% proxy loss. A
small effect appeared around 24% loss, became clear around 51%, and was strongest
around 75%.

| Queue | Audio improvement with audio priority | Background improvement with background priority |
| ---: | ---: | ---: |
| 1,000 | less than 0.2% | less than 0.7% |
| 200 | 0.8% | 0.3% |
| 100 | 3.2% | 10.1% |
| 50 | 14.8% | 24.4% |
| 20 | 35.2% | 47.9% |

The low-priority track paid for the gain. At queue depth 50, background priority
raised audio median latency from 1,473 to 1,784 ms, while audio priority raised
background median latency from 1,668 to 1,721 ms. At depth 20, audio priority raised
background median latency from 1,856 to 1,990 ms, while background priority raised
audio median latency from 1,813 to 2,034 ms.

The p95 results showed the same general transition. At depth 50, audio priority
reduced audio p95 from 2,282 to 2,058 ms, while background priority reduced
background p95 from 2,495 to 1,902 ms. At depth 20, the corresponding changes were
2,742 to 2,180 ms and 2,563 to 1,667 ms.

### Queue-sweep conclusion

In this proxy setup, priority became visible only after the queue was shallow enough
to create substantial loss and retransmission pressure. The effect grew
monotonically as the proxy drop percentage rose. This supports the hypothesis that
the earlier priority results were coupled to congestion feedback and repeated
scheduler choices, rather than to serialization in an external FIFO alone.

It does not prove that packet loss itself is required. Queue depth simultaneously
changes loss, acknowledgement timing, congestion-window behavior, and the number of
retransmission decisions. Separating those mechanisms still requires sender-side
backpressure or controlled ECN without an external FIFO absorbing the excess load.

The two favored directions were not perfectly symmetric. Background was subscribed
second, and its priority benefit was larger than audio's at the shallowest queues.
The deep-queue experiment showed no order effect, but order may interact with
priority once loss is high. Repeating depths 50 and 20 with background-first order
would test that remaining interaction.

## Subscription order under heavy loss

The final follow-up repeated the 50- and 20-packet conditions with background
subscribed first. All traffic parameters remained symmetric and unchanged. Each
priority mode ran five times at each queue depth, adding 30 runs. The existing
audio-first measurements provide the comparison.

The publisher waits until both subscriptions are active before starting either
track. Reversing order therefore changes the API request order and may change the
resulting stream lifecycle, without giving the first track an earlier data start.

### Queue depth 50

| Order | Mode | Audio p50 / p95 | Background p50 / p95 |
| --- | --- | ---: | ---: |
| audio first | off | 1,473 / 2,282 ms | 1,668 / 2,495 ms |
| audio first | audio | 1,256 / 2,058 ms | 1,721 / 2,399 ms |
| audio first | background | 1,784 / 2,574 ms | 1,261 / 1,902 ms |
| background first | off | 1,479 / 2,238 ms | 1,599 / 2,310 ms |
| background first | audio | 1,138 / 1,901 ms | 1,599 / 2,439 ms |
| background first | background | 1,865 / 2,604 ms | 1,253 / 2,037 ms |

With audio first, audio priority improved audio median latency by 14.8%, while
background priority improved background by 24.4%. Reversing order changed those
improvements to 23.0% and 21.6%, respectively. The directions became nearly
symmetric.

The proxy drop percentages were 50.5% to 51.3% across all six conditions. Off mode
missed three audio groups with audio first and one audio group with background
first. Both explicit-priority modes delivered every application group.

### Queue depth 20

| Order | Mode | Audio p50 / p95 | Background p50 / p95 |
| --- | --- | ---: | ---: |
| audio first | off | 1,813 / 2,742 ms | 1,856 / 2,563 ms |
| audio first | audio | 1,175 / 2,180 ms | 1,990 / 2,686 ms |
| audio first | background | 2,034 / 2,721 ms | 967 / 1,667 ms |
| background first | off | 1,860 / 2,755 ms | 1,944 / 2,696 ms |
| background first | audio | 1,134 / 1,885 ms | 2,045 / 2,644 ms |
| background first | background | 1,992 / 2,721 ms | 1,149 / 1,768 ms |

With audio first, audio and background priority improved their selected medians by
35.2% and 47.9%. With background first, the improvements were 39.0% and 40.9%.
Again, reversing order made the two directions substantially more symmetric.

The proxy drop percentages were 74.7% to 75.1%. Audio-first runs delivered every
application group. Background-first off mode missed four background groups, while
both explicit-priority modes delivered every group.

### Order conclusion

Subscription order affects the magnitude of the priority benefit under heavy loss,
but it does not determine whether priority works. The selected track improved
substantially in both orders. At both queue depths, a selected track received the
larger median benefit when it was subscribed second:

- At depth 50, audio's benefit rose from 14.8% to 23.0% when audio moved second;
  background's benefit fell from 24.4% to 21.6% when background moved first.
- At depth 20, audio's benefit rose from 35.2% to 39.0% when audio moved second;
  background's benefit fell from 47.9% to 40.9% when background moved first.

Because both tracks start publishing together and measured drop rates were matched,
the result points to request order, stream identifiers, or an internal equal-class
queue tie-break rather than a data-start advantage. The evidence does not identify
which layer owns the bias. A transport-level test that records stream creation,
priority changes, and scheduler selections would be needed to locate it.

## Publisher-to-transport priority boundary

A transport-level follow-up isolated the publisher's scheduler from the network.
It used the Rust `moq-net` test transport and the production lite publisher path,
without a relay process, UDP proxy, socket, congestion controller, or packet loss.
The purpose was to determine whether subscription order changes the priority value
that MoQ hands to the transport.

The temporary test created two complete, otherwise identical sequence-zero groups,
one named audio and one named background. Both subscriptions shared the same
`PriorityQueue`, just as subscriptions in one publisher session do. The test varied
which group entered the queue first and used the same three relative priority modes
as the network experiment. A closed write gate stopped both unidirectional streams
on their first byte, leaving both queue entries live simultaneously. Each track had
its own log, so the first `SendStream::set_priority` value could be attributed to
the correct stream.

The exact input pairs were `(128, 128)` for the equal-priority control, `(255, 0)`
for audio priority, and `(0, 255)` for background priority. The network script uses
`(0, 0)` for its control. With only these two live entries, changing both equal
values together cannot change their relative queue ranks, so the control exercises
the same tie condition.

The transport contract sends larger `send_order` values first. The queue can assign
at most one stream to each rank, so two live streams receive adjacent values even
when their MoQ priorities are equal.

| Registration order | Mode | Audio `send_order` | Background `send_order` |
| --- | --- | ---: | ---: |
| audio first | off | 254 | 255 |
| audio first | audio | 255 | 254 |
| audio first | background | 254 | 255 |
| background first | off | 255 | 254 |
| background first | audio | 255 | 254 |
| background first | background | 254 | 255 |

The focused test passed with one test run and 931 unrelated unit tests filtered out.
It was run with:

```text
cargo test -p moq-net lite::publisher::serve_group_test::transport_priority_experiment -- --nocapture
```

The temporary instrumentation was then removed from `publisher.rs`; only these
results were retained.

### Boundary conclusion

The publisher-to-transport conversion is symmetric for explicit priorities. Audio
priority always produced `audio=255, background=254`, and background priority
always produced the inverse, regardless of registration order. The order-dependent
latency magnitude in the lossy network experiment is therefore not caused by the
publisher accidentally swapping or discarding an explicit track priority before
calling the transport.

Equal priorities do have a deterministic insertion-order tie-break. The second
equal-priority group occupied rank zero and received `send_order=255`; the first
received `254`. This comes from inserting equal `Priority` values into the sorted
queue at the binary-search match position. Reversing insertion order reversed the
transport orders. It gives the previous order effect a concrete MoQ-layer mechanism,
although the experiment does not prove that it is the only mechanism active in the
end-to-end runs.

The remaining uncertainty is below this boundary. This test records what the MoQ
publisher requests, not which QUIC stream is selected for each packet or
retransmission. The explicit-priority request is correct and order-independent at
the transport API. A further experiment would need scheduler or qlog observations
from the real QUIC connection under the 50- and 20-packet loss conditions to explain
the residual magnitude difference.

## Real QUIC qlog under heavy loss

The next experiment enabled Quinn qlog on the relay and repeated the symmetric
50-packet condition. Its first goal was to determine whether the remaining
subscription-order difference came from different connection-wide loss or
congestion states after MoQ handed priorities to QUIC.

The relay was built with its existing `qlog` feature and run with the Quinn backend.
No product source was changed. The traffic and shaper settings were:

- 1,944-byte payloads on both tracks
- one group every 20 ms on both tracks
- approximately 0.778 Mbps per track, or 1.555 Mbps combined
- 1 Mbps relay-to-subscriber shaper
- 50 ms one-way proxy delay
- 50-packet drop-tail queue
- two seconds and 100 groups per track per run
- audio-first and background-first subscription orders
- off, audio-priority, and background-priority modes
- five repetitions per condition, randomized with the existing fixed seed

This produced 30 runs and 6,000 received application groups. The server qlog was
15 MB and contained 90,357 events across 60 QUIC connections: one publisher and one
subscriber connection per run. Connections were mapped to runs by first-event time.
Within every pair, the publisher connected first and the subscriber connected about
0.5 seconds later. As an independent check, the selected subscriber connection was
the only member of every pair with heavy packet loss.

### Application latency

Each condition received all 500 expected groups on each track. No application group
was missing.

| Order | Mode | Audio p50 / p95 | Background p50 / p95 |
| --- | --- | ---: | ---: |
| audio first | off | 1,401 / 2,239 ms | 1,577 / 2,335 ms |
| audio first | audio | 1,175 / 1,924 ms | 1,640 / 2,375 ms |
| audio first | background | 1,814 / 2,586 ms | 1,203 / 1,703 ms |
| background first | off | 1,499 / 2,313 ms | 1,580 / 2,265 ms |
| background first | audio | 1,277 / 2,108 ms | 1,685 / 2,438 ms |
| background first | background | 1,893 / 2,585 ms | 1,249 / 1,950 ms |

Explicit priority remained effective in both subscription orders:

| Selected track | Audio-first improvement | Background-first improvement |
| --- | ---: | ---: |
| audio with audio priority | 16.1% | 14.8% |
| background with background priority | 23.7% | 21.0% |

The non-selected track paid for the selected track's improvement. Audio priority
increased background median latency by 4.0% with audio first and 6.7% with
background first. Background priority increased audio median latency by 29.5% and
26.2%, respectively.

### QUIC connection state

The table aggregates the relay-to-subscriber QUIC connection for five runs per
condition. `qlog loss` is the number of packets Quinn declared lost divided by sent
1-RTT packets. `Proxy drop` is measured independently at the UDP shaper.

| Order | Mode | Sent 1-RTT | Lost | qlog loss | Proxy drop | Median RTT | Median cwnd |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| audio first | off | 4,660 | 2,312 | 49.61% | 49.84% | 358 ms | 453 KB |
| audio first | audio | 4,666 | 2,326 | 49.85% | 50.02% | 355 ms | 450 KB |
| audio first | background | 4,672 | 2,328 | 49.83% | 49.97% | 367 ms | 455 KB |
| background first | off | 4,644 | 2,314 | 49.83% | 50.02% | 360 ms | 451 KB |
| background first | audio | 4,572 | 2,248 | 49.17% | 49.32% | 366 ms | 454 KB |
| background first | background | 4,693 | 2,339 | 49.84% | 49.96% | 365 ms | 452 KB |

The qlog and proxy measurements agree closely. Across conditions, qlog loss varied
by 0.68 percentage points, proxy loss by 0.70 points, median RTT by 12 ms, and median
congestion window by about 5 KB. These variations are much smaller than the
hundreds-of-milliseconds track latency shifts caused by explicit priority.

All 13,867 qlog packet losses were declared by the reordering threshold. There were
no time-threshold losses, and the maximum PTO count was zero in every run. The
result is therefore not explained by one subscription order suffering extra probe
timeouts or entering a materially different connection-wide congestion state.

### Reproducibility of the earlier order effect

The earlier five-run queue-depth experiment suggested that the selected track got
a larger benefit when subscribed second. That did not reproduce consistently:

| Selected track | Earlier audio first / background first | qlog run audio first / background first |
| --- | ---: | ---: |
| audio improvement | 14.8% / 23.0% | 16.1% / 14.8% |
| background improvement | 24.4% / 21.6% | 23.7% / 21.0% |

The audio direction reversed. In the qlog run, moving audio second reduced its
measured improvement instead of increasing it. Background retained a small
2.7-percentage-point difference, but the run-level medians were variable. For
example, background-first audio-priority audio medians ranged from 1,086 to
1,781 ms across only five repetitions.

The supported conclusion is narrower than the earlier one: explicit priority works
under heavy loss in either subscription order, but the size of the order interaction
is not stable with five repetitions. The earlier claim that the second subscription
systematically benefits more is not reproducible and should not be treated as a
protocol effect.

### qlog limitation and conclusion

Quinn's current qlog output records packet headers, loss events, and recovery
metrics, but its `transport:packet_sent` events do not include STREAM frames or
stream IDs. It can rule out a connection-wide loss, RTT, congestion-window, or PTO
imbalance, but it cannot attribute an individual new transmission or retransmission
to audio or background.

Combined with the publisher-boundary experiment, the evidence now shows:

1. MoQ hands the requested explicit priority to the transport correctly in either
   registration order.
2. QUIC connection-wide loss and congestion measurements are closely matched across
   the six conditions.
3. Explicit priority still creates a large and repeatable selected-track latency
   benefit.
4. The smaller subscription-order interaction is not repeatable with the current
   five-run samples.

Directly measuring per-track QUIC scheduling would require temporary Quinn scheduler
instrumentation or decrypted packet capture, because the available qlog schema does
not expose the required stream selection events.

## Direct Quinn stream scheduling under heavy loss

A follow-up instrumented the real Quinn send path to attribute every scheduled
STREAM range to its application track. The experiment did not change scheduling
behavior. It only added two temporary observations:

1. The lite publisher attached the track name and group sequence to each group task.
2. A local `web-transport-quinn` patch recognized the existing `MLAB` payload header
   and recorded the QUIC stream ID, track ID, and sequence. Quinn's existing
   `quinn_proto::connection::streams::state` trace then supplied every scheduled
   byte range for that stream, including retransmissions.

The temporary Rust instrumentation and dependency override were removed after the
run. The experiment used the same symmetric 50-packet condition as the qlog run:

- 1,944-byte payloads on both tracks
- one group every 20 ms on both tracks
- 1.555 Mbps combined offered load
- 1 Mbps relay-to-subscriber serialization rate
- 50 ms one-way proxy delay
- 50-packet drop-tail queue
- two seconds and 100 groups per track
- both subscription orders and all three priority modes
- 20 repetitions per condition, or 120 runs total

The condition order was randomized with the fixed seed `20260923`. Each condition
summary below treats a run as the independent sample. The reported center is the
median of 20 run medians, and brackets are a 95% percentile bootstrap interval from
20,000 resamples. This avoids treating 2,000 correlated groups as 2,000 independent
replicates.

The trace contained 24,000 group-to-stream mappings and 166,475 scheduled STREAM
events for group streams. Every group stream was mapped. Another 480 events belonged
to WebTransport control streams and were excluded. Reconstructing the scheduled byte
ranges produced 1,961 or 1,962 unique bytes per group, matching the encoded group
sizes.

### Application latency

| Order | Mode | Track | Received | Median of run p50 | 95% interval |
| --- | --- | --- | ---: | ---: | ---: |
| audio first | off | audio | 1,999 / 2,000 | 1,533 ms | 1,477 to 1,694 ms |
| audio first | off | background | 2,000 / 2,000 | 1,634 ms | 1,589 to 1,744 ms |
| audio first | audio | audio | 2,000 / 2,000 | 1,241 ms | 1,211 to 1,388 ms |
| audio first | audio | background | 2,000 / 2,000 | 1,689 ms | 1,646 to 1,723 ms |
| audio first | background | audio | 2,000 / 2,000 | 1,883 ms | 1,811 to 1,931 ms |
| audio first | background | background | 2,000 / 2,000 | 1,353 ms | 1,320 to 1,547 ms |
| background first | off | audio | 2,000 / 2,000 | 1,581 ms | 1,505 to 1,660 ms |
| background first | off | background | 1,999 / 2,000 | 1,695 ms | 1,578 to 1,746 ms |
| background first | audio | audio | 2,000 / 2,000 | 1,393 ms | 1,260 to 1,445 ms |
| background first | audio | background | 2,000 / 2,000 | 1,668 ms | 1,639 to 1,739 ms |
| background first | background | audio | 2,000 / 2,000 | 1,836 ms | 1,770 to 1,940 ms |
| background first | background | background | 2,000 / 2,000 | 1,418 ms | 1,259 to 1,462 ms |

Explicit priority improved the selected track in all four order and track
comparisons:

| Order | Selected track | Median improvement | 95% interval |
| --- | --- | ---: | ---: |
| audio first | audio | 19.1% | 8.8% to 26.8% |
| background first | audio | 11.9% | 5.3% to 20.6% |
| audio first | background | 17.2% | 5.4% to 23.3% |
| background first | background | 16.4% | 9.9% to 26.4% |

All four intervals exclude zero. Subscription order changed the point estimate, but
did not change the direction of the effect.

### Scheduler observations

`Last scheduled` is the median time from the relay attempting to write the
application payload until Quinn scheduled that stream's final transmission or
retransmission. `Retransmitted` is the share of all scheduled bytes that overlapped
a previously scheduled range. `Finished first` compares the final scheduled time of
the two same-sequence groups.

| Order | Selected track | Last scheduled, off | Last scheduled, selected | Retransmitted, off | Retransmitted, selected | Finished first, off | Finished first, selected |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| audio first | audio | 570 ms | 383 ms | 49.0% | 45.0% | 59.0% | 72.5% |
| background first | audio | 578 ms | 376 ms | 49.2% | 45.1% | 58.0% | 73.0% |
| audio first | background | 700 ms | 360 ms | 52.2% | 44.0% | 41.0% | 97.0% |
| background first | background | 689 ms | 357 ms | 52.0% | 43.7% | 42.0% | 97.0% |

Priority reduced the selected track's final scheduling delay by 33% to 49%. The
selected track also used fewer repeated byte transmissions, while the non-selected
track moved in the opposite direction: its retransmitted share was 55.2% to 55.7%.
This is direct evidence that the latency gain is created inside Quinn's scheduling
of loss recovery, after MoQ has assigned the correct stream priority.

Initial transmission was not the main source of the result. Quinn normally scheduled
a group's first FIN within 0.2 to 0.3 ms of the payload write attempt. The large
separation appeared in later transmissions after congestion feedback and loss had
created repeated scheduling decisions. This explains why the external shallow queue
made priority visible and why the no-loss deep queue did not.

The background direction remained stronger in the pairwise completion metric. Its
selected group finished scheduling before the same-sequence audio group in 97% of
pairs, while selected audio finished first in about 73%. The transport trace proves
that both directions receive the intended priority, but does not remove every
asymmetry from task creation timing, ACK timing, and stream lifetime.

### Heavy-loss conclusion

The combined evidence now locates the mechanism more narrowly:

1. MoQ converts the requested track priority to the correct Quinn priority.
2. Connection-wide loss, RTT, congestion window, and PTO state are matched across
   conditions.
3. Under heavy loss, Quinn reschedules the selected track sooner and with fewer
   repeated scheduled bytes.
4. The earlier five-run subscription-order interaction was sampling variation. With
   20 run-level replicates, both orders retain a positive selected-track effect.

Priority is therefore effective here because loss and delayed acknowledgements keep
returning stream ranges to a priority-aware scheduling decision. It is not merely an
artifact of pooled application latency.

## No-loss send-window controls

Two follow-ups tested whether a smaller Quinn `send_window` could create clean
sender-side contention without packet loss. Both used the symmetric traffic, a
1,000-packet proxy queue, and zero observed proxy drops. The setting was a temporary
relay-only change and was removed after the runs.

### 12.5 KB window

The first setting was 12,500 bytes, chosen from an initial 100 ms RTT assumption.
The proxy delays only relay-to-subscriber packets, however, so the base RTT is near
50 ms. The resulting window rate is approximately 2 Mbps, above the 1.555 Mbps
offered load. It was therefore a negative control rather than a constrained-sender
test.

All six conditions ran ten times, for 60 runs and 12,000 received groups. No proxy
packet or application group was lost. The selected-track application improvements
were small and every 95% interval included zero:

| Order | Selected track | Median improvement | 95% interval |
| --- | --- | ---: | ---: |
| audio first | audio | 1.9% | -2.5% to 13.0% |
| background first | audio | 1.3% | -2.9% to 7.0% |
| audio first | background | 0.8% | -8.3% to 4.2% |
| background first | background | 0.5% | -3.5% to 4.7% |

The transport trace recorded zero retransmitted bytes. Median final scheduling delay
was only 33 to 53 ms, while application latency was about 1.9 to 2.0 seconds. The
remaining delay was therefore in the external serialization queue after Quinn had
scheduled the bytes, where stream priority is unavailable.

### 6 KB pilot

A 6,000-byte window corresponds to approximately 0.96 Mbps at a 50 ms RTT. One
exploratory run of each of the six conditions completed in 5.16 to 5.37 seconds with
no proxy drops or missing groups. Application medians were 2.64 to 3.32 seconds.

This smaller window still produced zero retransmissions, and Quinn scheduled each
group's final bytes within a median 48 to 59 ms of the payload write attempt. The
proxy FIFO still owned most of the end-to-end delay. A single repetition showed
selected-track changes in both directions, so no effect estimate was retained.

### No-loss conclusion

`send_window` limits accepted unacknowledged stream data, but it is not an egress
rate limiter. In this setup it did not move the dominant serialization queue inside
the priority-aware Quinn scheduler. Lowering it further would mostly block
application writes before enough competing stream data reaches the scheduler, which
tests write-wakeup order rather than packet scheduling.

A clean no-loss scheduler experiment still requires one of the following:

- a Quinn congestion-controller or pacer test implementation capped near 1 Mbps;
- a transport harness whose packet transmit operation applies backpressure instead
  of accepting packets into an external FIFO; or
- kernel egress shaping directly on the relay socket or interface.

The current environment has no `CAP_NET_ADMIN`, so the kernel-shaping option was not
available. The send-window controls are useful negative evidence, but they do not
establish whether packet loss is intrinsically required for priority to matter.

## No-loss contention inside Quinn

The next experiment replaced Quinn's normal congestion controller with a temporary
fixed-window controller. Its purpose was to put the bottleneck inside Quinn's
priority-aware packet scheduler without introducing packet loss. This directly
tested the question left open by the send-window controls: does priority require
loss, or only an internal scheduling backlog?

The controller held the congestion window at 5,000 bytes. Quinn's pacer derives its
rate from 1.25 times the congestion window divided by the smoothed RTT. With the
proxy delaying only relay-to-subscriber traffic by 50 ms, the expected pacing rate
was approximately 125,000 bytes per second, or 1 Mbps. The external proxy link was
raised to 100 Mbps and its queue to 1,000 packets so that it could accept paced
datagrams immediately instead of becoming a second bottleneck.

All other workload choices matched the symmetric heavy-loss experiment:

- 1,944-byte payloads on both tracks
- one group every 20 ms on both tracks
- 1.555 Mbps combined offered load
- two seconds and 100 groups per track
- both subscription orders and all three priority modes
- 20 repetitions per condition, or 120 runs total
- randomized condition order with fixed seed `20260923`

The same temporary stream instrumentation mapped the application track and sequence
to each QUIC stream, then reconstructed the ranges Quinn scheduled. The fixed
controller, stream instrumentation, and dependency override were removed after the
experiment.

### Integrity checks

All 120 runs completed. The proxy forwarded 356 to 367 datagrams per run and dropped
none. All 24,000 application groups arrived and mapped to a QUIC stream. The trace
also contained 480 events for WebTransport control streams, which were excluded.
Each group contained 1,961 or 1,962 unique encoded bytes. No scheduled group byte
overlapped an earlier range, so the measured retransmitted share was exactly 0% in
every condition.

These checks distinguish this experiment from the heavy-loss result. Quinn made
repeated priority-aware choices because its pacer had more eligible stream data than
it could send, not because loss returned byte ranges for retransmission.

### Application latency

As before, each run was the independent sample. The center below is the median of 20
run medians, with a 95% percentile bootstrap interval from 20,000 resamples.

| Order | Mode | Track | Received | Median of run p50 | 95% interval |
| --- | --- | --- | ---: | ---: | ---: |
| audio first | off | audio | 2,000 / 2,000 | 3,472 ms | 3,470 to 3,477 ms |
| audio first | off | background | 2,000 / 2,000 | 3,494 ms | 3,491 to 3,496 ms |
| audio first | audio | audio | 2,000 / 2,000 | 2,625 ms | 2,624 to 2,630 ms |
| audio first | audio | background | 2,000 / 2,000 | 3,030 ms | 3,028 to 3,033 ms |
| audio first | background | audio | 2,000 / 2,000 | 3,027 ms | 3,023 to 3,031 ms |
| audio first | background | background | 2,000 / 2,000 | 2,629 ms | 2,627 to 2,631 ms |
| background first | off | audio | 2,000 / 2,000 | 3,475 ms | 3,469 to 3,477 ms |
| background first | off | background | 2,000 / 2,000 | 3,494 ms | 3,489 to 3,499 ms |
| background first | audio | audio | 2,000 / 2,000 | 2,626 ms | 2,624 to 2,629 ms |
| background first | audio | background | 2,000 / 2,000 | 3,030 ms | 3,026 to 3,035 ms |
| background first | background | audio | 2,000 / 2,000 | 3,030 ms | 3,025 to 3,037 ms |
| background first | background | background | 2,000 / 2,000 | 2,630 ms | 2,626 to 2,634 ms |

Explicit priority improved the selected track in all four comparisons:

| Order | Selected track | Median improvement | 95% interval |
| --- | --- | ---: | ---: |
| audio first | audio | 24.4% | 24.2% to 24.5% |
| background first | audio | 24.4% | 24.3% to 24.5% |
| audio first | background | 24.7% | 24.7% to 24.8% |
| background first | background | 24.7% | 24.6% to 24.9% |

All intervals exclude zero and the two subscription orders produced nearly identical
results. Giving one track priority delayed the other track, as expected under a
fixed shared capacity: its median was about 3,027 to 3,030 ms, between the selected
track and the priority-off baseline.

### Scheduler observations

| Order | Selected track | Last scheduled, off | Last scheduled, selected | Improvement | Finished first, off | Finished first, selected |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| audio first | audio | 2,567 ms | 2,156 ms | 16.0% | 48.0% | 99.0% |
| background first | audio | 2,573 ms | 2,154 ms | 16.3% | 48.0% | 99.0% |
| audio first | background | 2,542 ms | 2,154 ms | 15.3% | 52.0% | 100.0% |
| background first | background | 2,540 ms | 2,153 ms | 15.2% | 52.0% | 100.0% |

The selected stream's final bytes entered packets 15% to 16% sooner than under the
priority-off control. More decisively, same-sequence pairs were nearly balanced in
the control but the selected track finished scheduling first in 99% to 100% of
pairs. This ordering was independent of which subscription was created first.

### No-loss conclusion

Packet loss is not intrinsically required for priority to matter. Priority becomes
effective when competing streams remain queued inside Quinn at its packet selection
boundary. Under that condition, the selected track had about 24% lower application
median latency, finished scheduling first in virtually every matched pair, and did
so with zero proxy loss and zero retransmitted stream bytes.

The earlier deep-queue no-loss control did not contradict this result. In that
control Quinn handed packets rapidly to an external FIFO, which erased its ability
to choose between tracks after enqueue. The decisive variable is therefore the
location of the queue:

1. A backlog inside Quinn exposes competing streams to its priority scheduler.
2. A backlog in an external FIFO preserves packet arrival order and hides priority.
3. Loss can create repeated internal scheduling opportunities, but it is only one
   way to do so.

This also resolves the earlier subscription-order uncertainty. With clean internal
contention and 20 run-level repetitions, both orders produced the same effect within
about 0.2 percentage points. Track priority, rather than registration order, controls
the scheduling outcome.

## Fixed-window boundary sweep

The 5,000-byte fixed-window result established that loss-free contention inside
Quinn is sufficient. A follow-up swept larger windows to locate where that
contention, and therefore the application benefit from priority, disappears.

The proxy used a 100 Mbps serialization rate, 50 ms one-way delay, and a
1,000-packet queue. Only the fixed congestion window constrained the relay. Audio
and background both used zero-valued 1,944-byte payloads every 20 ms, avoiding
synchronous signal-generation work in the publisher event loop. The combined
offered load remained approximately 1.555 Mbps.

Windows of 10,000, 11,000, 12,500, and 20,000 bytes were tested. Subscription and
publisher task order were audio first. Each priority mode ran ten times in the same
deterministic randomized order, for 120 runs and 24,000 application groups. The
table reports the median of ten run medians. Intervals are 95% percentile bootstrap
intervals over runs.

| Fixed cwnd | Selected track | Off p50 | Selected p50 | Improvement | 95% interval |
| ---: | --- | ---: | ---: | ---: | ---: |
| 10,000 B | audio | 540 ms | 89 ms | 83.5% | 82.2% to 84.3% |
| 10,000 B | background | 526 ms | 91 ms | 82.7% | 80.8% to 84.7% |
| 11,000 B | audio | 80 ms | 70 ms | 12.2% | 7.9% to 15.1% |
| 11,000 B | background | 79 ms | 71 ms | 9.9% | 4.9% to 14.4% |
| 12,500 B | audio | 63 ms | 55 ms | 13.3% | 12.5% to 14.0% |
| 12,500 B | background | 62 ms | 55 ms | 12.4% | 4.8% to 13.8% |
| 20,000 B | audio | 54 ms | 54 ms | -0.1% | -0.5% to 0.5% |
| 20,000 B | background | 54 ms | 55 ms | -0.2% | -0.7% to 0.1% |

Every proxy reported zero drops. Every application sequence was complete and
contiguous. The scheduler trace recorded no repeated byte range, so transport
retransmission was also zero.

At 10,000 bytes, priority reduced the selected track's median final scheduling
delay from 274 to 20 ms for audio and from 279 to 19 ms for background. The selected
track finished scheduling first for 99% to 100% of same-sequence pairs. At 20,000
bytes, both tracks finished their initial scheduling in about 0.3 ms. Priority still
decided their sub-millisecond ordering, but there was no application-latency benefit
because neither stream waited materially.

### Publisher task-order control

The 12,500-byte condition was repeated with the background publisher task created
first. Subscription order remained audio first. This tests whether the roughly
10 ms selected-track benefit at the boundary was an artifact of which coroutine
entered the event loop first.

| Publisher task order | Selected track | Improvement | 95% interval | Final scheduling improvement |
| --- | --- | ---: | ---: | ---: |
| audio first | audio | 13.3% | 12.5% to 14.0% | 97.0% |
| audio first | background | 12.4% | 4.8% to 13.8% | 97.0% |
| background first | audio | 11.9% | 5.4% to 13.7% | 96.9% |
| background first | background | 9.5% | 4.1% to 13.2% | 96.0% |

Both directions retained a positive effect with either publisher task order. The
point estimates moved by a few percentage points, but all four run-level intervals
excluded zero.

### Boundary conclusion

The priority benefit disappears once the congestion window is large enough to
schedule both tracks immediately. It does not require loss, and it does not remain
as a fixed per-group advantage without contention. The 20,000-byte negative control
separates a real scheduler benefit from payload, coroutine, or measurement bias.

Together with the 5,000-byte result, this sweep shows that the size of the benefit
depends sharply on how much competing data remains eligible inside Quinn. The
controlling distinction is contention inside the QUIC sender versus buffering after
the sender, not loss versus no loss.

## Fine-grained fixed-window boundary

The coarse sweep left the disappearance point somewhere between 12,500 and 20,000
bytes. A 15,000-byte pilot already showed no application effect, so a finer search
tested 12,750, 13,000, 13,250, and 13,500 bytes. Each pilot ran the three priority
modes once. The transition appeared inside this 750-byte range, so all four points
were then repeated ten times per mode.

The full measurement added 120 runs and 24,000 application groups. Traffic, proxy,
subscription order, publisher task order, random seed, and instrumentation were
unchanged from the preceding sweep. Every application sequence was complete and
contiguous. Every proxy reported zero drops, and the scheduler trace found zero
retransmitted stream bytes.

Each run remained the independent sample. The table reports the median of ten run
percentiles and percentile bootstrap intervals over those ten runs. `Delayed` is
the median share of a run's groups whose final STREAM range was scheduled more than
2 ms after the relay attempted its payload write. Immediate groups formed a tight
cluster near 0.3 ms, while constrained groups formed a separate cluster near 10 to
12 ms, so the threshold separates the two observed populations.

| Fixed cwnd | Track | Off p50 | Selected p50 | p50 improvement | Off p95 | Selected p95 | p95 improvement | Delayed, off / selected |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 12,750 B | audio | 58.4 ms | 54.5 ms | 6.8% (3.2% to 13.9%) | 66.3 ms | 56.6 ms | 14.7% (13.2% to 15.9%) | 50.5% / 6.0% |
| 12,750 B | background | 58.3 ms | 54.3 ms | 6.9% (4.3% to 14.1%) | 66.3 ms | 56.4 ms | 15.0% (13.5% to 15.7%) | 50.5% / 5.0% |
| 13,000 B | audio | 54.9 ms | 54.5 ms | 0.6% (0.1% to 1.0%) | 65.0 ms | 55.9 ms | 13.9% (-0.1% to 15.1%) | 18.0% / 0.5% |
| 13,000 B | background | 54.8 ms | 54.5 ms | 0.5% (0.2% to 0.9%) | 65.4 ms | 55.7 ms | 14.7% (0.1% to 15.3%) | 16.5% / 1.0% |
| 13,250 B | audio | 54.7 ms | 54.6 ms | 0.0% (-0.3% to 0.3%) | 55.8 ms | 55.8 ms | 0.1% (-0.5% to 14.2%) | 2.5% / 0.0% |
| 13,250 B | background | 54.7 ms | 54.6 ms | 0.2% (-0.1% to 0.5%) | 56.0 ms | 55.5 ms | 0.9% (0.1% to 14.4%) | 2.5% / 0.0% |
| 13,500 B | audio | 54.6 ms | 54.6 ms | 0.0% (-0.2% to 0.2%) | 55.5 ms | 55.6 ms | -0.2% (-0.7% to 0.3%) | 0.0% / 0.0% |
| 13,500 B | background | 54.5 ms | 54.5 ms | 0.1% (0.0% to 0.3%) | 55.6 ms | 55.5 ms | 0.2% (-0.2% to 0.7%) | 0.0% / 0.0% |

### Transition shape

The application effect does not fade uniformly across the latency distribution.
At 12,750 bytes, contention affected roughly half of the equal-priority groups, so
priority improved both p50 and p95. At 13,000 bytes, only about one group in six was
delayed in the control. The median benefit fell below 1%, while an approximately
10 ms p95 benefit remained. At 13,250 bytes, delayed groups fell to 2.5% and neither
track retained a material and symmetric run-level p50 or p95 improvement. At 13,500
bytes, none of the groups crossed the 2 ms scheduling-delay threshold.

Priority continued to determine relative ordering after the latency benefit
vanished. The selected track finished scheduling first in 98% to 99% of matched
same-sequence pairs at every point, including 13,500 bytes. The equal-priority
control stayed near 50%. At the larger windows this ordering happened within a
sub-millisecond interval, so it did not produce a measurable delivery improvement.

At 12,750 bytes the non-selected track paid for the selected track's gain. Its
median was about 65.2 ms and 97% of its groups were delayed. This confirms that the
selected result is capacity redistribution inside the scheduler, not a general
reduction in run latency.

### Fine-boundary conclusion

For this exact 50 ms, 1,944-byte, synchronized two-track workload, the median
priority benefit ends between 12,750 and 13,000 bytes. A tail benefit remains at
13,000 bytes and is no longer material and symmetric at 13,250 bytes. All
observable application benefit is gone by 13,500 bytes.

These byte values are workload-specific rather than protocol constants. The sharp
transition and the separate immediate and delayed clusters point to packetization
and burst admission at the congestion-window boundary. Changing RTT, MTU, group
size, phase between tracks, or control traffic can move the threshold. The durable
result is the mechanism: priority changes latency only for groups that remain
eligible together inside Quinn long enough for its stream ordering to choose among
them.

## Track-phase sensitivity

The fine boundary used synchronized 20 ms schedules. A follow-up kept the fixed
window at 13,000 bytes and delayed every background deadline by 1, 2, 3, 4, 5, or
10 ms. The 1 ms point was added after the first sweep to measure the unobserved
interval between the synchronized control and the positive 2 ms result.

All other conditions matched the fine sweep: 1,944-byte payloads, 50 ms one-way
delay, a 100 Mbps external link, a 1,000-packet queue, audio-first subscription and
publisher task order, and ten repetitions per priority mode.

All 180 runs completed. Each phase received 1,000 frames per track and mode. The
proxy dropped no packets, and the scheduler trace found no retransmitted stream
bytes. The table reports the median of ten run medians and 95% percentile bootstrap
intervals over runs.

| Background phase | Selected track | Off p50 | Selected p50 | Improvement | 95% interval | Delayed, off / selected |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 1 ms | audio | 54.5 ms | 54.5 ms | 0.1% | -0.1% to 0.2% | 2.0% / 2.5% |
| 1 ms | background | 64.4 ms | 54.3 ms | 15.7% | 15.4% to 15.9% | 86.5% / 20.0% |
| 2 ms | audio | 54.4 ms | 54.4 ms | 0.0% | -0.4% to 0.2% | 4.0% / 3.0% |
| 2 ms | background | 63.2 ms | 54.7 ms | 13.5% | 12.8% to 15.2% | 77.0% / 33.5% |
| 3 ms | audio | 53.7 ms | 53.7 ms | 0.0% | -0.2% to 0.1% | 1.0% / 0.5% |
| 3 ms | background | 54.5 ms | 61.0 ms | -12.0% | -12.8% to -0.2% | 52.5% / 66.5% |
| 4 ms | audio | 53.6 ms | 53.7 ms | -0.1% | -0.2% to 0.1% | 0.5% / 0.0% |
| 4 ms | background | 60.1 ms | 60.2 ms | -0.2% | -0.7% to 0.3% | 70.0% / 70.0% |
| 5 ms | audio | 53.6 ms | 53.6 ms | 0.0% | -0.2% to 0.2% | 1.0% / 1.0% |
| 5 ms | background | 59.3 ms | 59.4 ms | -0.2% | -0.6% to 0.4% | 78.5% / 80.0% |
| 10 ms | audio | 53.6 ms | 53.6 ms | 0.0% | -0.2% to 0.2% | 11.5% / 13.5% |
| 10 ms | background | 53.6 ms | 53.6 ms | 0.0% | -0.2% to 0.2% | 12.0% / 16.0% |

The 1 and 2 ms phases exposed an asymmetric priority opportunity. At 1 ms, audio
finished scheduling first in every equal-priority same-sequence pair. Selecting
audio therefore added nothing. Selecting the later background track reduced its
median final scheduling delay from 11.6 to 0.2 ms and let it finish first in 80.5%
of pairs. This reduced background p50 by 15.7%, although p95 improved by only 2.2%
because 20% of selected-background groups still crossed the 2 ms delay threshold.

At 2 ms, the same direction remained but affected fewer pairs. Selecting background
reduced its median final scheduling delay from 10.8 to 0.2 ms and let it finish
first in 62% of same-sequence pairs. Its p95 improved only 1.4%, again showing that
the priority opportunity did not occur for every group.

At 3 ms, the immediate and delayed populations straddled p50. Selecting background
raised its median delayed-group share from 52.5% to 66.5%, which moved run p50 from
the immediate cluster to the delayed cluster and produced the apparent 12% penalty.
Its p95 was 62.5 ms both with and without priority, and it never finished before the
same-sequence audio group. This is a quantile-boundary result, not evidence of a
broad latency penalty across the distribution.

At 4 and 5 ms, background waited about 7.2 and 6.2 ms after its payload write,
respectively, but changing its priority did not change that wait. At 10 ms, both
tracks finished initial scheduling in about 0.2 ms and priority had no application
effect. A one-run 5 ms pilot appeared to show a 9% background improvement, but the
ten-run interval excluded that effect. The repeated result is therefore the one
reported here.

### Mirrored 1 ms phase

The 1 ms condition was mirrored by delaying audio instead of background. This
changed only which schedule received the delay. Subscription order and publisher
task creation order remained audio first, allowing the result to distinguish track
identity from relative production time without changing either ordering input.

The three priority modes again ran ten times in deterministic randomized order.
All 6,000 application groups arrived, the proxy dropped no packets, and the
scheduler trace contained no retransmitted stream bytes.

| Delayed track | Selected track | Off p50 | Selected p50 | Improvement | 95% interval | Final scheduling delay, off / selected |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| background | background | 64.4 ms | 54.3 ms | 15.7% | 15.4% to 15.9% | 11.6 / 0.2 ms |
| audio | audio | 64.4 ms | 54.2 ms | 15.8% | 15.5% to 16.0% | 11.5 / 0.2 ms |

The earlier track provided the corresponding negative control. With background
delayed, selecting the earlier audio improved its p50 by only 0.1%, with a -0.1%
to 0.2% interval. With audio delayed, selecting the earlier background changed its
p50 by 0.0%, with a -0.3% to 0.2% interval.

The pairwise scheduler result also mirrored. Selecting delayed background raised
its same-sequence final-scheduling win rate from 0% to 80.5%. Selecting delayed
audio raised its win rate from 0% to 83.0%. Capacity moved away from the other
track in both directions: its p50 rose from about 54.5 ms to 64.4 to 64.7 ms.

### Phase conclusion

The fixed-window threshold is sensitive even to a single millisecond of phase. A
1 ms offset in either direction was short enough for the later write to participate
in some packet selection rounds, where its priority could reverse the natural
earlier-track ordering. The nearly identical mirrored effect rules out audio or
background identity as the cause. For the background-delay sweep, the effect was
strongest at 1 ms, smaller at 2 ms, and gone at 3 ms. At 4 and 5 ms the later track
still encountered congestion-window delay, but no priority-aware choice improved
it. At 10 ms the two bursts were admitted separately and neither waited materially.

This sharp transition reinforces that priority orders eligible streams; it does
not reserve bandwidth or preempt bytes already handed to QUIC. Periodic media
tracks can therefore see different priority behavior at the same rate, RTT, group
size, and congestion window solely because their production deadlines have a
different phase relationship.

## Path-delay sensitivity

The final follow-up returned to synchronized track schedules and kept the fixed
congestion window at 13,000 bytes. It changed only the proxy's
relay-to-subscriber delay from the preceding 50 ms condition to 25, 75, 100, and
150 ms. The return path was not shaped, so these values describe the added
downstream delay rather than a symmetric network RTT.

All other workload choices remained unchanged:

- 1,944-byte payloads on both tracks
- one group every 20 ms on both tracks
- approximately 1.555 Mbps combined offered load
- a 100 Mbps external link and 1,000-packet queue
- audio-first subscription and publisher task order
- ten repetitions per priority mode at each delay

The 150 ms pilot exposed a setup artifact: the publisher's fixed 100 ms start
lead was shorter than the downstream delay, so the subscriber missed sequences
zero and one on both tracks in every mode. The measured 150 ms runs delayed both
track schedules by an additional 100 ms. This left their relative phase and the
latency interval unchanged while allowing subscription setup to finish. The pilot
was excluded.

All 120 measured runs completed. Every one of the 24,000 application groups
arrived. The proxy dropped no packets, and the scheduler trace found no
retransmitted stream bytes. Each run remained the independent sample. The table
reports the median of ten run medians and 95% percentile bootstrap intervals over
runs.

This table used the original sequential group reader. It is retained as the
historical observation that motivated the receiver investigation. The corrected
75, 100, and 150 ms application effects are reported under
[Corrected delay scaling](#corrected-delay-scaling) and supersede these application
columns. The scheduler columns remain direct relay measurements.

| Downstream delay | Selected track | Off p50 | Selected p50 | Improvement | 95% interval | Final scheduling improvement |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 25 ms | audio | 29.7 ms | 29.6 ms | 0.0% | -0.3% to 0.4% | 8.2% |
| 25 ms | background | 29.7 ms | 29.5 ms | 0.5% | -0.1% to 0.9% | 2.2% |
| 75 ms | audio | 1,372.9 ms | 368.7 ms | 73.1% | 69.7% to 74.2% | 77.7% |
| 75 ms | background | 1,320.6 ms | 367.5 ms | 72.2% | 69.9% to 73.5% | 78.1% |
| 100 ms | audio | 2,480.5 ms | 1,846.9 ms | 25.5% | 25.3% to 25.8% | 19.9% |
| 100 ms | background | 2,500.3 ms | 1,844.2 ms | 26.2% | 25.9% to 26.5% | 19.7% |
| 150 ms | audio | 4,182.3 ms | 3,233.4 ms | 22.7% | 22.6% to 22.9% | 13.3% |
| 150 ms | background | 4,198.6 ms | 3,233.9 ms | 23.0% | 22.8% to 23.1% | 13.3% |

At 25 ms, every group finished its initial scheduling within 2 ms of the relay's
payload write. Explicit priority still changed which same-sequence track finished
first from a balanced 50% control to 98% or 99%, but both streams completed soon
enough that this ordering did not affect application latency.

At 75 ms, the selected track remained close to the front of the backlog while the
non-selected track absorbed nearly all of the overload. Priority reduced the
selected track's final scheduling delay by about 78% and its application p50 by
about 72% to 73% with the original reader. The non-selected p50 was 0.7% to 5.0%
slower than its
equal-priority baseline, so this condition showed the expected capacity transfer
without the apparent shared improvement observed by the original reader at 100 ms.

At 100 ms, 97% to 97.5% of groups waited more than 2 ms for their final STREAM
range to be scheduled. Explicit priority raised the selected track's matched-pair
win rate from approximately 50% to 99% or 100%, scheduled its final bytes about
20% sooner, and reduced its application p50 by approximately 26%. The non-selected
track appeared to improve relative to the equal-priority baseline, but remained
about 300 ms slower than the selected track. The receiver-boundary controls below
show that the sequential group reader inflated the equal-priority reference. With
concurrent group reads, the selected improvement is about 19% and the non-selected
track is about 14% slower than equal priority.

At 150 ms, even the selected track could not stay current. Priority reduced its
final scheduling delay by 13.3% and its application p50 by about 23% with the
original reader. The
original reader again showed an apparent 12% non-selected improvement while that
track remained approximately 470 ms slower than the selected track. Because the
100 ms receiver control identifies the same effect as per-track application
head-of-line waiting, this 150 ms value is not evidence of a network-wide gain.

The fixed-window throughput bound explains the transition. Ignoring protocol
overhead, a 13,000-byte window can admit roughly 4.16 Mbps per 25 ms feedback
interval, 2.08 Mbps per 50 ms interval, 1.39 Mbps per 75 ms interval, 1.04 Mbps
per 100 ms interval, and 0.69 Mbps per 150 ms interval. The first two exceed the
1.555 Mbps combined offered load. The 75 ms bound lies below the combined load but
above one track's 0.778 Mbps load, so strict priority can keep the selected track
mostly current while deferring the other. This produced the largest benefit. At
100 ms the remaining headroom was smaller, and at 150 ms the bound fell below one
track's offered load, so the selected track also accumulated backlog.

The synchronized 50 ms experiment still had short burst contention in its tail:
priority improved p50 by less than 1% but p95 by about 14%. From 75 ms onward, the
sender could not drain the combined offered load between acknowledgements, so
contention persisted across nearly the entire run and priority affected the center
of the latency distribution.

### Delay conclusion

The congestion-window boundary is a bandwidth-delay relationship, not a fixed byte
threshold. A window that is ample on a short path can become the bottleneck on a
longer path without any packet loss. Priority then redistributes the constrained
send capacity toward the selected stream. The selected benefit is not monotonic in
delay: it peaks where the window can sustain the selected class but not the total
load, then shrinks once the selected class also exceeds the window's approximate
rate. This confirms the earlier warning that the 12,750-to-13,000-byte transition
is specific to the experiment's delay, payload, and production schedule.

## Fixed-delay window scaling

The delay sweep suggested that the largest benefit occurs when the fixed window
can sustain one track but not the combined load. A final control held the
downstream delay at 75 ms and changed the fixed congestion window instead. The
existing 13,000-byte result was bracketed with 6,000- and 20,000-byte windows.

All other conditions matched the path-delay experiment:

- 1,944-byte payloads on both tracks
- one group every 20 ms on both tracks
- approximately 0.778 Mbps per track and 1.555 Mbps combined
- a 100 Mbps external link and 1,000-packet queue
- audio-first subscription and publisher task order
- ten repetitions per priority mode and window

The two added windows contributed 60 runs and 12,000 application groups. Together
with the existing 13,000-byte condition, the comparison covers 90 runs and 18,000
groups. Every sequence arrived, the proxy dropped no packets, and the scheduler
trace found no retransmitted stream bytes. The table reports medians of ten run
medians and run-level 95% percentile bootstrap intervals.

This table also used the original sequential reader. The corrected 6,000- and
13,000-byte effects are reported under
[Corrected window scaling](#corrected-window-scaling). The 20,000-byte condition
does not develop a group backlog and remains a valid zero-effect control.

| Fixed cwnd | Approximate window rate | Selected track | Off p50 | Selected p50 | Improvement | 95% interval |
| ---: | ---: | --- | ---: | ---: | ---: | ---: |
| 6,000 B | 0.64 Mbps | audio | 5,577.7 ms | 4,298.9 ms | 22.9% | 22.6% to 23.4% |
| 6,000 B | 0.64 Mbps | background | 5,618.0 ms | 4,300.3 ms | 23.5% | 23.0% to 23.6% |
| 13,000 B | 1.39 Mbps | audio | 1,372.9 ms | 368.7 ms | 73.1% | 69.7% to 74.2% |
| 13,000 B | 1.39 Mbps | background | 1,320.6 ms | 367.5 ms | 72.2% | 69.9% to 73.5% |
| 20,000 B | 2.13 Mbps | audio | 79.4 ms | 79.4 ms | 0.0% | -0.1% to 0.1% |
| 20,000 B | 2.13 Mbps | background | 79.4 ms | 79.4 ms | 0.0% | -0.1% to 0.1% |

At 6,000 bytes, the approximate window rate was lower than one track's offered
load. Both tracks accumulated backlog even when one had priority. Selecting a
track reduced its original-reader median by about 23%, but could not keep it
current.

At 13,000 bytes, the approximate window rate was above one track's load and below
the combined load. The scheduler could keep the selected track near the front of
the backlog while moving most of the excess delay to the other track. This was the
largest effect.

At 20,000 bytes, every group finished initial scheduling in about 0.3 ms. Explicit
priority changed the selected track's same-sequence scheduling win rate from 50%
to 97% or 99%, but its application median did not change. Ordering streams that
already finish immediately has no useful latency effect.

### Window-scaling conclusion

Holding delay fixed reproduced the three regimes from the delay sweep:

1. Below one track's required window rate, priority helps but the selected track
   also accumulates backlog.
2. Between one track and the combined load, priority has its largest effect because
   the selected track can stay current at the expense of the other.
3. Above the combined load, both tracks schedule immediately and priority has no
   application-latency effect.

The agreement between changing delay and changing window strengthens the
bandwidth-delay explanation. Neither path delay nor a congestion-window byte value
is the controlling variable by itself. The relevant quantity is how the available
in-flight budget compares with the selected and combined offered loads over the
feedback interval.

## Receiver-boundary and group-reading controls

The 100 and 150 ms experiments initially contained a result that strict capacity
transfer could not explain: the non-selected track was slower than the selected
track, but faster than the same track at equal priority. The following controls
first narrowed the effect and then identified it as a subscriber measurement
artifact.

### Controls that preserved the original reader

The 100 ms, 13,000-byte condition was repeated at 0.5, 1, 2, and 3 seconds. Ten
repetitions per mode showed a selected-track improvement of 26% to 28% and an
apparent non-selected improvement that grew from 7% to 12% at 0.5 seconds to about
15% at 3 seconds. This ruled out finite-run draining, but every duration used the
same sequential group reader and therefore reproduced the same artifact.

The relay advertised 10,000 concurrent unidirectional streams while the largest
run created only 300. Each group used about 2.68 STREAM events on average and four
at p95 in every mode. The effect was therefore unrelated to the stream limit or a
change in STREAM fragmentation.

Publisher, relay, and subscriber wall clocks divided a fresh 6,000-group series at
the relay boundary. Publisher-to-relay time was 1.6 ms in every condition. All of
the apparent shared improvement occurred downstream. Disabling UDP GSO in another
6,000-group series left the selected improvement near 26% and the apparent
non-selected improvement near 14%. This excluded publisher ingress and GSO, but
did not exclude behavior in the subscriber.

An initial `recv_group()` timestamp was also misleading. The receiver loop awaited
all frames from one group before calling `recv_group()` again. Its timestamp meant
"the application asked for this group after finishing the previous one", not
"MoQ first made this group available". The sub-millisecond interval from that
timestamp to its frame reflected an already-buffered frame and could not locate
the actual receive boundary.

### Direct QUIC receive trace

A temporary instrumented `web-transport-quinn` build logged the first readable
bytes of every receiving stream and mapped the experiment track and sequence from
the `MLAB` header. These events were joined with relay STREAM traces and the two
subscriber wall clocks by mode, repetition, track, and sequence. The original
sequential reader was rerun ten times per mode, yielding 6,000 complete groups.

The first stream bytes arrived 139 to 145 ms after relay mapping in every cell.
Priority had no material effect on this boundary. The experiment payload header
was recognized another 0.016 to 0.020 ms later. Nevertheless, the application did
not request the median group until 1.67 to 2.32 seconds after that event. This gap
identified per-track application head-of-line waiting rather than network ingress
or MoQ group creation as the missing interval.

### Concurrent group control

The subscriber was changed to continue receiving groups in arrival order while a
separate async task reads each group's frames. The same 100 ms delay, 13,000-byte
window, 2-second workload was then run ten times per mode. All 30 runs delivered
all 6,000 expected groups with zero proxy drops and zero retransmitted stream
bytes. Preliminary runs were excluded.

The values below are medians of the ten per-run medians. Confidence intervals are
95% bootstrap intervals over runs.

| Mode | Track | p50 | 95% CI | Change from equal priority |
| --- | --- | ---: | ---: | ---: |
| off | audio | 1,889.7 ms | 1,880.2 to 1,900.0 ms | reference |
| off | background | 1,885.1 ms | 1,864.5 to 1,889.6 ms | reference |
| audio priority | audio | 1,525.9 ms | 1,522.2 to 1,535.2 ms | 19.3% faster (18.7% to 19.6%) |
| audio priority | background | 2,162.7 ms | 2,154.3 to 2,166.1 ms | 14.7% slower (14.2% to 15.8%) |
| background priority | audio | 2,151.6 ms | 2,144.7 to 2,164.6 ms | 13.9% slower (13.4% to 14.7%) |
| background priority | background | 1,523.0 ms | 1,517.9 to 1,535.2 ms | 19.2% faster (18.1% to 19.6%) |

The corrected receiver shows the expected tradeoff. Explicit priority advances
the selected track and delays the non-selected track. It does not improve both.

Comparing readers under identical network conditions shows why the old result was
convincing. Concurrent reads reduced the equal-priority median by 24.5% to 24.7%
and the selected median by 17.8% to 18.1%, but changed the non-selected median by
only -0.3% to +0.5%. The sequential loop inflated the equal-priority reference
most, turning the real non-selected penalty into an apparent benefit.

### Corrected latency boundary

With concurrent group reads, the joined clocks locate the remaining delay:

| Mode and role | Relay mapping to first bytes | Relay mapping to group return | Group return to frame | Final STREAM event to frame |
| --- | ---: | ---: | ---: | ---: |
| off, either track | 141 to 142 ms | 142 to 143 ms | 1,734 ms | 103.3 ms |
| explicit priority, selected | 141 ms | 142 ms | 1,379 ms | 103.3 ms |
| explicit priority, non-selected | 141 ms | 142 ms | 2,008 to 2,022 ms | 103.2 ms |

MoQ exposes group headers at essentially the same time in every condition because
their first bytes arrive early. The wait is for the single frame payload to become
complete. Relay scheduling takes about 1.42 seconds for the selected stream, 1.77
to 1.78 seconds at equal priority, and 2.04 to 2.05 seconds for the non-selected
stream. After the final STREAM event, the frame arrives about 103 ms later in all
conditions, matching the configured 100 ms one-way proxy delay plus processing.

This directly locates the priority effect in Quinn's relay-side STREAM scheduling.
The network and receiver add a nearly constant tail after scheduling. The earlier
shared improvement was produced solely by the sequential subscriber's group-level
head-of-line waiting.

### Corrected subscription-order control

The 100 ms, 13,000-byte concurrent-reader series was repeated with background
subscribed before audio. Ten repetitions per mode delivered all 6,000 groups with
zero proxy drops and zero retransmitted stream bytes.

| Subscription order | Selected audio change | Selected background change | Non-selected audio change | Non-selected background change |
| --- | ---: | ---: | ---: | ---: |
| audio first | 18.9% faster | 19.1% faster | 14.2% slower | 14.5% slower |
| background first | 18.8% faster | 19.2% faster | 14.4% slower | 14.1% slower |

Directly comparing audio-first and background-first produced differences between
0.0% and 0.6% across the six mode and track cells. Every 95% confidence interval
included zero. Subscription order therefore neither creates the priority effect
nor changes which track receives it.

### Corrected delay scaling

The concurrent reader was also run at 75 and 150 ms, with the existing 100 ms
series completing the comparison. Each delay used ten repetitions per mode and
delivered all 6,000 expected groups without proxy loss or retransmitted stream
bytes. The 150 ms schedule retained the 100 ms setup lead described above.

Because the tracks are symmetric, each equal-priority run pooled both tracks when
forming its reference median. This prevents a run's natural same-priority winner
from biasing a result toward the track name that happened to win more often.

| Delay | Selected audio change | Selected background change | Non-selected audio change | Non-selected background change |
| ---: | ---: | ---: | ---: | ---: |
| 25 ms | no material change | no material change | no material change | no material change |
| 75 ms | 69.7% faster (68.8% to 70.7%) | 70.3% faster (70.0% to 71.2%) | 52.5% slower (50.8% to 55.4%) | 54.6% slower (51.6% to 56.4%) |
| 100 ms | 18.9% faster (18.7% to 19.2%) | 19.1% faster (18.6% to 19.4%) | 14.2% slower (13.8% to 14.7%) | 14.5% slower (14.3% to 14.8%) |
| 150 ms | 12.4% faster (12.4% to 12.5%) | 12.3% faster (12.2% to 12.4%) | 17.2% slower (17.1% to 17.4%) | 17.3% slower (17.1% to 17.4%) |

The delay-dependent shape remains the same after removing subscriber
head-of-line waiting. The effect peaks where the window can sustain one track but
not both, then falls when even the selected track exceeds the approximate window
rate. The corrected result additionally shows the corresponding cost paid by the
non-selected track at every congested delay.

### Corrected window scaling

The 75 ms, 6,000-byte series was repeated with concurrent reads. Ten repetitions
per mode delivered all 6,000 groups with zero proxy drops and zero retransmitted
stream bytes. The 13,000-byte result comes from the corrected delay control. The
20,000-byte streams already completed scheduling in about 0.3 ms, so they could
not trigger the sequential-reader wait and were not rerun.

| Fixed cwnd | Approximate window rate | Selected audio change | Selected background change | Non-selected audio change | Non-selected background change |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 6,000 B | 0.64 Mbps | 7.3% faster (7.1% to 7.6%) | 7.1% faster (6.9% to 7.5%) | 24.2% slower (23.8% to 24.6%) | 24.0% slower (23.4% to 24.2%) |
| 13,000 B | 1.39 Mbps | 69.7% faster (68.8% to 70.7%) | 70.3% faster (70.0% to 71.2%) | 52.5% slower (50.8% to 55.4%) | 54.6% slower (51.6% to 56.4%) |
| 20,000 B | 2.13 Mbps | no material change | no material change | no material change | no material change |

The three-regime model remains supported, but the old low-window effect was
overstated. Below one track's required rate, priority can only make a modest
improvement because the selected track still accumulates backlog. Between one
track and the combined rate it can keep the selected stream near the front while
moving most delay to the non-selected stream. Above the combined rate, ordering
does not change application latency.

Across the duration, clock-boundary, group-clock, GSO, direct-receive, and
concurrent-reader controls, 360 measured runs delivered 67,500 groups. Pilot runs
were excluded. Every measured run completed without an application group loss.
The fixed-window series reported zero proxy drops and zero retransmitted stream
bytes.

## Implementation and validation log

The investigation made the following experiment-only changes while locating the
boundary:

1. Added wall-clock timestamps for `recv_group()` return and frame receipt to the
   subscriber CSV while retaining the monotonic clock for latency calculation.
2. Saved each subscriber process log beside its CSV so receiver events could be
   joined without mixing independent runs.
3. Used a temporary local `web-transport-quinn` patch to log the first readable
   bytes and identify the `MLAB` track and sequence on each QUIC stream.
4. Joined 6,000 group records across relay STREAM events, QUIC receive events,
   group returns, and frame reads. The join asserted the expected group count for
   every run before producing summaries.
5. Replaced the sequential per-track reader with concurrent group-frame readers,
   then repeated all three priority modes ten times under the same fixed-window
   condition.
6. Repeated the corrected measurement with reversed subscription order, at 75 and
   150 ms path delay, and with a 6,000-byte congestion window. Equal-priority
   references pooled the symmetric tracks before calculating run medians.
7. Removed the temporary Cargo patch, restored the registry lock metadata, and
   rebuilt both `moq-ffi` and `moq-relay` from the normal dependency graph. No
   receiver instrumentation remains in the repository.

The Nix daemon was unavailable in this environment, so the binding and relay were
built offline with the repository's existing Cargo and Maturin inputs. Validation
used Ruff lint and format checks, Pyright, Python bytecode compilation, normal
binding import and CLI startup, and a final three-mode smoke run. The smoke run
received all 150 expected groups with zero proxy drops. It checked program flow
only and is excluded from the fixed-window performance results.
