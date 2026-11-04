import asyncio
import csv
import os
import time

import moq


RELAY_URL = "https://10.200.1.1:4443"
BROADCAST_NAME = "experiment/audio-priority/concurrent"
AUDIO_TRACK_NAME = "audio"
BACKGROUND_TRACK_NAME = "background"
MESSAGE_COUNT = 400


def read_frame(name, frame):
    receive_time = time.monotonic_ns()
    receive_wall_time = time.time_ns()
    sequence = int.from_bytes(frame.payload[0:4], byteorder="big")
    send_time = int.from_bytes(frame.payload[4:12], byteorder="big")
    delay_ms = (receive_time - send_time) / 1_000_000
    return sequence, [
        name,
        sequence,
        len(frame.payload),
        send_time,
        receive_time,
        receive_wall_time,
        delay_ms,
    ]


async def receive_audio(track, writer):
    sequences = []
    async for group in track:
        async for frame in group:
            sequence, row = read_frame("audio", frame)
            sequences.append(sequence)
            writer.writerow(row)
            print(f"audio:frame{sequence}:size{row[2]}bytes:delay={row[6]:.3f}ms")
            if len(sequences) == MESSAGE_COUNT:
                if sequences != list(range(MESSAGE_COUNT)):
                    raise RuntimeError(f"audio sequence mismatch: {sequences}")
                print("audio:受信完了")
                track.cancel()
                return


async def drain_background_group(group, completed):
    frames = []
    async for frame in group:
        sequence, row = read_frame("background", frame)
        if sequence != group.sequence:
            raise RuntimeError(
                "background group/payload sequence mismatch: "
                f"group={group.sequence}, payload={sequence}"
            )
        frames.append(row)
    if not frames:
        raise RuntimeError(f"background group has no frames: sequence={group.sequence}")
    print(f"background buffered sequence={group.sequence}")
    await completed.put((group.sequence, frames))


async def emit_background(completed, writer):
    expected_sequence = 0
    buffered = {}
    frame_count = 0
    while expected_sequence < MESSAGE_COUNT:
        sequence, frames = await completed.get()
        if sequence < expected_sequence or sequence in buffered:
            raise RuntimeError(f"duplicate background sequence={sequence}")
        buffered[sequence] = frames
        while expected_sequence in buffered:
            for row in buffered.pop(expected_sequence):
                writer.writerow(row)
                frame_count += 1
                print(
                    f"background:frame{row[1]}:size{row[2]}bytes:"
                    f"delay={row[6]:.3f}ms"
                )
            print(f"background emit sequence={expected_sequence}")
            expected_sequence += 1
    if frame_count != MESSAGE_COUNT:
        raise RuntimeError(
            f"background frame count mismatch: expected={MESSAGE_COUNT}, "
            f"actual={frame_count}"
        )


async def receive_background(track, writer):
    completed = asyncio.Queue()
    emitter = asyncio.create_task(emit_background(completed, writer))
    drains = []
    arrived = set()
    try:
        async for group in track.groups_as_arrived():
            sequence = group.sequence
            print(f"background arrived sequence={sequence}")
            if sequence in arrived:
                raise RuntimeError(f"duplicate background arrival sequence={sequence}")
            arrived.add(sequence)
            drains.append(asyncio.create_task(drain_background_group(group, completed)))
            if len(arrived) == MESSAGE_COUNT:
                break
        if arrived != set(range(MESSAGE_COUNT)):
            raise RuntimeError(
                f"background arrival mismatch: count={len(arrived)}, "
                f"missing={sorted(set(range(MESSAGE_COUNT)) - arrived)}"
            )
        await asyncio.gather(*drains)
        await emitter
        print("background:受信完了")
        track.cancel()
    finally:
        if not emitter.done():
            emitter.cancel()
            await asyncio.gather(emitter, return_exceptions=True)


async def main() -> None:
    condition = os.environ["EXPERIMENT_CONDITION"]
    output_file = os.environ["EXPERIMENT_CSV"]
    if condition not in {"R", "A", "B"}:
        raise ValueError(f"unknown experiment condition: {condition}")

    moq.log_level("debug")
    with open(output_file, "x", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(
            [
                "track",
                "seq",
                "size",
                "send_time_ns",
                "received_time_ns",
                "received_wall_time_ns",
                "delay_ms",
            ]
        )
        async with moq.Client(RELAY_URL, tls_verify=False) as client:
            broadcast = await client.announced_broadcast(BROADCAST_NAME)
            audio_track = await broadcast.subscribe_track(
                AUDIO_TRACK_NAME, moq.Subscription(priority=255)
            )
            receivers = [receive_audio(audio_track, writer)]
            if condition != "R":
                background_track = await broadcast.subscribe_track(
                    BACKGROUND_TRACK_NAME,
                    moq.Subscription(priority=0, max_age_us=5_000_000),
                )
                receivers.append(receive_background(background_track, writer))
            await asyncio.gather(*receivers)


asyncio.run(main())
