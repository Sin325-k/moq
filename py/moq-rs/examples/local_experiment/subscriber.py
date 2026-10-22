"""Subscribe to local experiment tracks and record per-frame delay as CSV."""

from __future__ import annotations

import argparse
import asyncio
import csv
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import moq

MAGIC = b"MLAB"
VERSION = 1
TRACK_NAMES = {1: "audio", 2: "background"}
HEADER = struct.Struct("!4sBBHQQ")
ACK = b"MLAB-DONE\n"
CSV_FIELDS = (
    "track",
    "sequence",
    "send_time_ns",
    "receive_time_ns",
    "group_receive_wall_time_ns",
    "receive_wall_time_ns",
    "latency_us",
    "frame_timestamp_us",
    "payload_bytes",
)
PRIORITIES = {
    "off": {"audio": 0, "background": 0},
    "audio": {"audio": 255, "background": 0},
    "background": {"audio": 0, "background": 255},
}
SUBSCRIBE_ORDERS = ("audio-first", "background-first")


@dataclass(frozen=True)
class Config:
    """Command-line settings for the subscriber."""

    url: str
    broadcast: str
    mode: str
    subscribe_order: str
    csv_path: Path
    ack_host: str
    ack_port: int
    tls_verify: bool


@dataclass(frozen=True)
class Row:
    """One received frame ready for CSV serialization."""

    track: str
    sequence: int
    send_time_ns: int
    receive_time_ns: int
    group_receive_wall_time_ns: int
    receive_wall_time_ns: int
    latency_us: float
    frame_timestamp_us: int
    payload_bytes: int


def decode_payload(expected_track: str, data: bytes) -> tuple[int, int]:
    """Validate an experiment header and return its sequence and send time."""
    if len(data) < HEADER.size:
        raise ValueError(f"{expected_track} payload is shorter than the {HEADER.size}-byte header")
    magic, version, track_id, reserved, sequence, send_time_ns = HEADER.unpack_from(data)
    if magic != MAGIC or version != VERSION or reserved != 0:
        raise ValueError(f"{expected_track} payload has an unsupported experiment header")
    actual_track = TRACK_NAMES.get(track_id)
    if actual_track != expected_track:
        raise ValueError(f"expected {expected_track} payload, got track id {track_id}")
    return sequence, send_time_ns


async def receive_track(name: str, track: moq.TrackConsumer, queue: asyncio.Queue[Row | None]) -> int:
    """Receive groups in arrival order while reading their frames concurrently."""
    count = 0
    readers: set[asyncio.Task[int]] = set()
    receive = asyncio.create_task(track.recv_group())
    try:
        while True:
            done, _ = await asyncio.wait((receive, *readers), return_when=asyncio.FIRST_COMPLETED)
            completed = {reader for reader in readers if reader in done}
            readers.difference_update(completed)
            count += sum(task.result() for task in completed)

            if receive not in done:
                continue
            group = receive.result()
            if group is None:
                if readers:
                    count += sum(await asyncio.gather(*readers))
                return count

            group_receive_wall_time_ns = time.time_ns()
            readers.add(asyncio.create_task(receive_group(name, group, group_receive_wall_time_ns, queue)))
            receive = asyncio.create_task(track.recv_group())
    finally:
        receive.cancel()
        for reader in readers:
            reader.cancel()
        await asyncio.gather(receive, *readers, return_exceptions=True)


async def receive_group(
    name: str,
    group: moq.GroupConsumer,
    group_receive_wall_time_ns: int,
    queue: asyncio.Queue[Row | None],
) -> int:
    """Read every frame from one group without blocking later group arrivals."""
    count = 0
    async for frame in group:
        receive_time_ns = time.monotonic_ns()
        receive_wall_time_ns = time.time_ns()
        sequence, send_time_ns = decode_payload(name, frame.payload)
        latency_us = (receive_time_ns - send_time_ns) / 1_000
        if latency_us < 0:
            raise RuntimeError("negative monotonic delay; publisher and subscriber must run on the same host")
        await queue.put(
            Row(
                track=name,
                sequence=sequence,
                send_time_ns=send_time_ns,
                receive_time_ns=receive_time_ns,
                group_receive_wall_time_ns=group_receive_wall_time_ns,
                receive_wall_time_ns=receive_wall_time_ns,
                latency_us=latency_us,
                frame_timestamp_us=frame.timestamp_us,
                payload_bytes=len(frame.payload),
            )
        )
        count += 1
    return count


async def write_csv(output: TextIO, queue: asyncio.Queue[Row | None]) -> None:
    """Serialize rows from both receive tasks through one ordered writer."""
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
    writer.writeheader()
    output.flush()
    while row := await queue.get():
        writer.writerow(
            {
                "track": row.track,
                "sequence": row.sequence,
                "send_time_ns": row.send_time_ns,
                "receive_time_ns": row.receive_time_ns,
                "group_receive_wall_time_ns": row.group_receive_wall_time_ns,
                "receive_wall_time_ns": row.receive_wall_time_ns,
                "latency_us": f"{row.latency_us:.3f}",
                "frame_timestamp_us": row.frame_timestamp_us,
                "payload_bytes": row.payload_bytes,
            }
        )
        output.flush()


async def acknowledge(config: Config) -> None:
    """Tell the local publisher that every CSV row is durable."""
    reader, writer = await asyncio.open_connection(config.ack_host, config.ack_port)
    try:
        writer.write(ACK)
        await writer.drain()
        await reader.read()
    finally:
        writer.close()
        await writer.wait_closed()


async def run(config: Config) -> None:
    """Connect, subscribe to both tracks, and record them until clean end."""
    priorities = PRIORITIES[config.mode]
    config.csv_path.parent.mkdir(parents=True, exist_ok=True)

    async with moq.Client(config.url, tls_verify=config.tls_verify) as client:
        print(f"waiting for broadcast {config.broadcast!r}")
        broadcast = await client.announced_broadcast(config.broadcast)
        subscriptions = {
            "audio": moq.Subscription(priority=priorities["audio"]),
            "background": moq.Subscription(priority=priorities["background"]),
        }
        first = config.subscribe_order.removesuffix("-first")
        second = "background" if first == "audio" else "audio"
        tracks = {
            first: await broadcast.subscribe_track(first, subscriptions[first]),
            second: await broadcast.subscribe_track(second, subscriptions[second]),
        }
        audio = tracks["audio"]
        background = tracks["background"]
        print(
            f"subscribed mode={config.mode} order={config.subscribe_order} "
            f"audio={priorities['audio']} background={priorities['background']}"
        )

        queue: asyncio.Queue[Row | None] = asyncio.Queue()
        with config.csv_path.open("w", newline="", encoding="utf-8") as output:
            writer = asyncio.create_task(write_csv(output, queue))
            try:
                try:
                    audio_count, background_count = await asyncio.gather(
                        receive_track("audio", audio, queue),
                        receive_track("background", background, queue),
                    )
                finally:
                    audio.cancel()
                    background.cancel()
            finally:
                await queue.put(None)
                await writer

        await acknowledge(config)
        print(f"received audio={audio_count} background={background_count} frames; CSV={config.csv_path}")


def parse_args() -> Config:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://localhost:4443", help="relay URL")
    parser.add_argument("--broadcast", default="research/local", help="broadcast path")
    parser.add_argument("--mode", choices=PRIORITIES, default="off", help="subscriber priority mode")
    parser.add_argument(
        "--subscribe-order",
        choices=SUBSCRIBE_ORDERS,
        default="audio-first",
        help="order in which to subscribe to the two tracks",
    )
    parser.add_argument("--csv", type=Path, default=Path("local-experiment.csv"), help="CSV output path")
    parser.add_argument("--ack-host", default="127.0.0.1", help="publisher acknowledgement host")
    parser.add_argument("--ack-port", type=int, default=4444, help="publisher acknowledgement port")
    parser.add_argument(
        "--no-tls-verify",
        action="store_true",
        help="disable certificate verification for a local self-signed relay",
    )
    args = parser.parse_args()
    if not 1 <= args.ack_port <= 65_535:
        parser.error("--ack-port must be between 1 and 65535")
    return Config(
        url=args.url,
        broadcast=args.broadcast,
        mode=args.mode,
        subscribe_order=args.subscribe_order,
        csv_path=args.csv,
        ack_host=args.ack_host,
        ack_port=args.ack_port,
        tls_verify=not args.no_tls_verify,
    )


def main() -> None:
    """Run the subscriber until both tracks end or interruption."""
    try:
        asyncio.run(run(parse_args()))
    except KeyboardInterrupt:
        print("subscriber interrupted")


if __name__ == "__main__":
    main()
