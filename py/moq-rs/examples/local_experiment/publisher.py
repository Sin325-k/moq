"""Publish raw audio and background tracks for a local MoQ experiment."""

from __future__ import annotations

import argparse
import asyncio
import math
import struct
import time
from dataclasses import dataclass
from pathlib import Path

import moq

MAGIC = b"MLAB"
VERSION = 1
TRACK_AUDIO = 1
TRACK_BACKGROUND = 2
HEADER = struct.Struct("!4sBBHQQ")
ACK = b"MLAB-DONE\n"

AUDIO_RATE = 48_000
AUDIO_FRAME_MS = 20
AUDIO_SAMPLES = AUDIO_RATE * AUDIO_FRAME_MS // 1_000
AUDIO_FRAME_BYTES = AUDIO_SAMPLES * 2
TRACK_PRIORITY = 0


@dataclass(frozen=True)
class Config:
    """Command-line settings for the publisher."""

    url: str
    broadcast: str
    audio: Path | None
    duration: float
    background_mbps: float
    background_frame_bytes: int
    audio_phase_ms: float
    background_phase_ms: float
    publish_order: str
    ack_host: str
    ack_port: int
    tls_verify: bool


@dataclass(frozen=True)
class Schedule:
    """A fixed-rate frame schedule on the shared experiment clock."""

    start_ns: int
    interval_ns: int
    count: int


def payload(track: int, sequence: int, send_time_ns: int, body: bytes) -> bytes:
    """Encode one experiment payload."""
    return HEADER.pack(MAGIC, VERSION, track, 0, sequence, send_time_ns) + body


async def wait_until(deadline_ns: int) -> None:
    """Sleep until a monotonic deadline without accumulating interval drift."""
    remaining = deadline_ns - time.monotonic_ns()
    if remaining > 0:
        await asyncio.sleep(remaining / 1_000_000_000)


class AudioSource:
    """Produce 48 kHz mono signed 16-bit PCM frames."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.process: asyncio.subprocess.Process | None = None

    async def __aenter__(self) -> AudioSource:
        if self.path is not None:
            self.process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-v",
                "error",
                "-stream_loop",
                "-1",
                "-i",
                str(self.path),
                "-map",
                "0:a:0",
                "-f",
                "s16le",
                "-acodec",
                "pcm_s16le",
                "-ar",
                str(AUDIO_RATE),
                "-ac",
                "1",
                "pipe:1",
                stdout=asyncio.subprocess.PIPE,
            )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self.process is None or self.process.returncode is not None:
            return
        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=2)
        except TimeoutError:
            self.process.kill()
            await self.process.wait()

    async def read(self) -> bytes:
        """Return exactly one 20 ms PCM frame."""
        if self.process is None:
            return self._silence()
        if self.process.stdout is None:
            raise RuntimeError("FFmpeg stdout is unavailable")

        try:
            return await self.process.stdout.readexactly(AUDIO_FRAME_BYTES)
        except asyncio.IncompleteReadError as err:
            stderr = await self.process.wait()
            raise RuntimeError(f"FFmpeg ended before producing a full audio frame (exit {stderr})") from err

    def _silence(self) -> bytes:
        # Zero-valued PCM avoids synchronous signal-generation work in scheduler experiments.
        return bytes(AUDIO_FRAME_BYTES)


async def publish_audio(track: moq.TrackProducer, source: AudioSource, schedule: Schedule) -> int:
    """Publish 20 ms PCM frames against the shared experiment clock."""
    for sequence in range(schedule.count):
        deadline_ns = schedule.start_ns + sequence * schedule.interval_ns
        await wait_until(deadline_ns)
        pcm = await source.read()
        send_time_ns = time.monotonic_ns()
        track.write_frame(
            payload(TRACK_AUDIO, sequence, send_time_ns, pcm),
            timestamp_us=send_time_ns // 1_000,
        )
    return schedule.count


async def publish_background(track: moq.TrackProducer, config: Config, schedule: Schedule) -> int:
    """Publish deterministic bytes at the requested nominal bit rate."""
    body_bytes = config.background_frame_bytes - HEADER.size
    pattern = bytes(range(256))
    body = (pattern * math.ceil(body_bytes / len(pattern)))[:body_bytes]

    for sequence in range(schedule.count):
        deadline_ns = schedule.start_ns + sequence * schedule.interval_ns
        await wait_until(deadline_ns)
        send_time_ns = time.monotonic_ns()
        track.write_frame(
            payload(TRACK_BACKGROUND, sequence, send_time_ns, body),
            timestamp_us=send_time_ns // 1_000,
        )
    return schedule.count


async def run(config: Config) -> None:
    """Connect, wait for both subscriptions, and run both publishers."""
    acknowledged = asyncio.get_running_loop().create_future()

    async def receive_ack(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            if await reader.readexactly(len(ACK)) == ACK and not acknowledged.done():
                acknowledged.set_result(None)
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    ack_server = await asyncio.start_server(receive_ack, config.ack_host, config.ack_port)
    async with ack_server:
        async with moq.Client(config.url, tls_verify=config.tls_verify) as client:
            broadcast = client.create_broadcast(config.broadcast)
            info = moq.TrackInfo(priority=TRACK_PRIORITY)
            audio = broadcast.publish_track("audio", info)
            background = broadcast.publish_track("background", info)

            try:
                print(f"waiting for audio and background subscribers on {config.broadcast!r}")
                await asyncio.gather(audio.used(), background.used())

                start_ns = time.monotonic_ns() + 100_000_000
                duration_ns = round(config.duration * 1_000_000_000)
                audio_interval_ns = AUDIO_FRAME_MS * 1_000_000
                background_interval_ns = round(config.background_frame_bytes * 8 * 1_000 / config.background_mbps)
                audio_schedule = Schedule(
                    start_ns=start_ns + round(config.audio_phase_ms * 1_000_000),
                    interval_ns=audio_interval_ns,
                    count=math.ceil(duration_ns / audio_interval_ns),
                )
                background_schedule = Schedule(
                    start_ns=start_ns + round(config.background_phase_ms * 1_000_000),
                    interval_ns=background_interval_ns,
                    count=math.ceil(duration_ns / background_interval_ns),
                )
                audio.finish_at(audio_schedule.count)
                background.finish_at(background_schedule.count)

                source_name = str(config.audio) if config.audio is not None else "zero-valued PCM"
                print(
                    f"publishing for {config.duration:g}s from {source_name}; "
                    f"background={config.background_mbps:g} Mbps"
                )

                async with AudioSource(config.audio) as source:
                    if config.publish_order == "audio-first":
                        audio_count, background_count = await asyncio.gather(
                            publish_audio(audio, source, audio_schedule),
                            publish_background(background, config, background_schedule),
                        )
                    else:
                        background_count, audio_count = await asyncio.gather(
                            publish_background(background, config, background_schedule),
                            publish_audio(audio, source, audio_schedule),
                        )
                print(f"published audio={audio_count} background={background_count} frames")
                await acknowledged
                print("subscriber acknowledged complete CSV output")
            finally:
                for producer in (audio, background):
                    try:
                        producer.finish()
                    except Exception as err:
                        print(f"warning: could not finish {producer.name}: {err}")
                try:
                    broadcast.finish()
                except Exception as err:
                    print(f"warning: could not finish broadcast: {err}")


def parse_args() -> Config:
    """Parse and validate command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://localhost:4443", help="relay URL")
    parser.add_argument("--broadcast", default="research/local", help="broadcast path")
    parser.add_argument("--audio", type=Path, help="audio or video file containing an audio stream")
    parser.add_argument("--duration", type=float, default=10.0, help="send duration in seconds")
    parser.add_argument("--background-mbps", type=float, default=10.0, help="nominal background rate")
    parser.add_argument(
        "--background-frame-bytes",
        type=int,
        default=32_768,
        help="total bytes per background payload, including its header",
    )
    parser.add_argument(
        "--audio-phase-ms",
        type=float,
        default=0.0,
        help="delay the audio schedule relative to the shared start, in milliseconds",
    )
    parser.add_argument(
        "--background-phase-ms",
        type=float,
        default=0.0,
        help="delay the background schedule relative to the shared start, in milliseconds",
    )
    parser.add_argument(
        "--publish-order",
        choices=("audio-first", "background-first"),
        default="audio-first",
        help="order in which equal-deadline publisher tasks run",
    )
    parser.add_argument("--ack-host", default="127.0.0.1", help="subscriber acknowledgement host")
    parser.add_argument("--ack-port", type=int, default=4444, help="subscriber acknowledgement port")
    parser.add_argument(
        "--no-tls-verify",
        action="store_true",
        help="disable certificate verification for a local self-signed relay",
    )
    args = parser.parse_args()

    if args.duration <= 0:
        parser.error("--duration must be greater than zero")
    if args.background_mbps <= 0:
        parser.error("--background-mbps must be greater than zero")
    if args.background_frame_bytes <= HEADER.size:
        parser.error(f"--background-frame-bytes must be greater than {HEADER.size}")
    if args.audio_phase_ms < 0 or args.background_phase_ms < 0:
        parser.error("track phase delays must not be negative")
    if not 1 <= args.ack_port <= 65_535:
        parser.error("--ack-port must be between 1 and 65535")
    if args.audio is not None and not args.audio.is_file():
        parser.error(f"audio input does not exist: {args.audio}")

    return Config(
        url=args.url,
        broadcast=args.broadcast,
        audio=args.audio,
        duration=args.duration,
        background_mbps=args.background_mbps,
        background_frame_bytes=args.background_frame_bytes,
        audio_phase_ms=args.audio_phase_ms,
        background_phase_ms=args.background_phase_ms,
        publish_order=args.publish_order,
        ack_host=args.ack_host,
        ack_port=args.ack_port,
        tls_verify=not args.no_tls_verify,
    )


def main() -> None:
    """Run the publisher until completion or interruption."""
    try:
        asyncio.run(run(parse_args()))
    except KeyboardInterrupt:
        print("publisher interrupted")


if __name__ == "__main__":
    main()
