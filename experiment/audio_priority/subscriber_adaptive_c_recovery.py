"""Receive Audio and wait for Background during the adaptive C RECOVERY pilot."""

import asyncio
import csv
import os
import time

import moq


RELAY_URL = "https://10.200.1.1:4443"
BROADCAST_NAME = "experiment/audio-priority/concurrent"
MESSAGE_COUNT = 400


def record_frame(track, frame, writer):
    received_ns = time.monotonic_ns()
    received_wall_ns = time.time_ns()
    sequence = int.from_bytes(frame.payload[:4], "big")
    sent_ns = int.from_bytes(frame.payload[4:12], "big")
    writer.writerow(
        [
            track,
            sequence,
            len(frame.payload),
            sent_ns,
            received_ns,
            received_wall_ns,
            (received_ns - sent_ns) / 1_000_000,
        ]
    )
    return sequence


async def receive_audio(track, writer):
    sequences = []
    async for group in track:
        async for frame in group:
            sequence = record_frame("audio", frame, writer)
            sequences.append(sequence)
            if len(sequences) == MESSAGE_COUNT:
                if sequences != list(range(MESSAGE_COUNT)):
                    raise RuntimeError("Audio sequence is incomplete or out of order")
                track.cancel()
                print("Audio 400/400 received", flush=True)
                return
    raise RuntimeError(f"Audio ended after {len(sequences)}/{MESSAGE_COUNT} frames")


async def receive_background(track, writer):
    sequences = set()
    async for group in track.groups_as_arrived():
        async for frame in group:
            sequence = record_frame("background", frame, writer)
            if sequence != group.sequence or sequence in sequences:
                raise RuntimeError(f"Background duplicate or sequence mismatch: {sequence}")
            sequences.add(sequence)
    print(f"Background received {len(sequences)}/{MESSAGE_COUNT}", flush=True)


async def main():
    if os.environ.get("EXPERIMENT_CONDITION") != "C":
        raise ValueError("EXPERIMENT_CONDITION must be C")
    moq.log_level("debug")
    with open(os.environ["EXPERIMENT_CSV"], "x", newline="") as output:
        writer = csv.writer(output)
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
            audio = await broadcast.subscribe_track("audio", moq.Subscription(priority=255))
            background = await broadcast.subscribe_track(
                "background", moq.Subscription(priority=0, max_age_us=5_000_000)
            )
            background_task = asyncio.create_task(receive_background(background, writer))
            try:
                await receive_audio(audio, writer)
                try:
                    await asyncio.wait_for(background_task, timeout=20)
                except asyncio.TimeoutError:
                    print("Background incomplete 20 s after Audio completion", flush=True)
            finally:
                background.cancel()
                if not background_task.done():
                    background_task.cancel()
                    await asyncio.gather(background_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
