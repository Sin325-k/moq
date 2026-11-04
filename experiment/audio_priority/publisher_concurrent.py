import asyncio
import os
import time
from pathlib import Path

import moq


RELAY_URL = "https://localhost:4443"
BROADCAST_NAME = "experiment/audio-priority/concurrent"
AUDIO_TRACK_NAME = "audio"
BACKGROUND_TRACK_NAME = "background"
MESSAGE_COUNT = 400
PAYLOAD_SIZE = 1945
INTERVAL = 0.020


async def main() -> None:
    condition = os.environ["EXPERIMENT_CONDITION"]
    if condition not in {"R", "A", "B", "C"}:
        raise ValueError(f"unknown experiment condition: {condition}")

    start_file = Path(os.environ["EXPERIMENT_START_FILE"])
    include_background = condition != "R"

    moq.log_level("debug")
    print(f"relayへ接続します: {RELAY_URL}")
    async with moq.Client(RELAY_URL, tls_verify=False) as client:
        broadcast = client.create_broadcast(BROADCAST_NAME)
        audio_track = broadcast.publish_track(AUDIO_TRACK_NAME)
        background_track = (
            broadcast.publish_track(BACKGROUND_TRACK_NAME)
            if include_background
            else None
        )
        broadcast.announce()

        start_ns = time.monotonic_ns()
        start_file.write_text(f"{start_ns}\n", encoding="utf-8")
        print(f"experiment start monotonic_ns={start_ns}", flush=True)

        for sequence in range(MESSAGE_COUNT):
            seq = sequence.to_bytes(4, byteorder="big")

            audio_send_time = time.monotonic_ns()
            audio_group = audio_track.append_group()
            audio_group.write_frame(
                seq
                + audio_send_time.to_bytes(8, byteorder="big")
                + bytes(PAYLOAD_SIZE - 12),
                timestamp_us=sequence * 20_000,
            )
            audio_group.finish()

            if background_track is not None:
                background_send_time = time.monotonic_ns()
                background_group = background_track.append_group()
                background_group.write_frame(
                    seq
                    + background_send_time.to_bytes(8, byteorder="big")
                    + bytes(PAYLOAD_SIZE - 12),
                    timestamp_us=sequence * 20_000,
                )
                background_group.finish()

            await asyncio.sleep(INTERVAL)

        audio_track.finish()
        if background_track is not None:
            background_track.finish()
        await asyncio.sleep(1)
        broadcast.close()


asyncio.run(main())
