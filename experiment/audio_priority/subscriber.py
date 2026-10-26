import asyncio
import moq
import time
import csv #CSVファイルの保存に利用
from datetime import datetime

#RELAY_URL="https://localhost:4443"
RELAY_URL="https://10.200.1.1:4443"
BROADCAST_NAME="experiment/audio-priority/minimal"


#TRACK_NAME = "test"
AUDIO_TRACK_NAME = "audio"
BACKGROUND_TRACK_NAME = "background"
MESSAGE_COUNT = 100
#CSV_FILE = "results.csv"#結果保存用
CSV_FILE = f"results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"


async def receive_track(name,track,writer):
    count = 0

    async for group in track:
        print(f"{name}: group received: sequence={group.sequence}")
        async for frame in group:

            #受信した時刻を取得
            receive_time = time.monotonic_ns()

            #tbfログと時刻を合わせるためのやつ
            receive_wall_time = time.time_ns()
            

            count += 1
            #data = frame.payload.decode()

            seq = int.from_bytes(
                frame.payload[0:4],
                byteorder = "big"
            )

            send_time = int.from_bytes(
                frame.payload[4:12],
                byteorder = "big"
            )


            #遅延を測定
            delay_ns = receive_time - send_time

            delay_ms = delay_ns / 1000000

            writer.writerow([
                name,
                seq,
                len(frame.payload),
                send_time,
                receive_time,
                receive_wall_time,
                delay_ms
            ])

            #print(data)
            print(
                f"{name}:"
                f"frame{seq}:"
                f"size{len(frame.payload)}bytes:"
                f"delay={delay_ms:.3f}ms"
            )

            if count == MESSAGE_COUNT:
                print(f"{name}:受信完了")
                track.cancel()
                return


def read_frame(name, frame):
    receive_time = time.monotonic_ns()
    receive_wall_time = time.time_ns()
    seq = int.from_bytes(frame.payload[0:4], byteorder="big")
    send_time = int.from_bytes(frame.payload[4:12], byteorder="big")
    delay_ms = (receive_time - send_time) / 1000000

    return seq, [
        name,
        seq,
        len(frame.payload),
        send_time,
        receive_time,
        receive_wall_time,
        delay_ms,
    ]


async def drain_background_group(group, completed):
    frames = []
    async for frame in group:
        seq, row = read_frame("background", frame)
        if seq != group.sequence:
            raise RuntimeError(
                f"background group/payload sequence mismatch: "
                f"group={group.sequence}, payload={seq}"
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
            frames = buffered.pop(expected_sequence)
            for row in frames:
                writer.writerow(row)
                frame_count += 1
                print(
                    f"background:frame{row[1]}:"
                    f"size{row[2]}bytes:"
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
            drains.append(
                asyncio.create_task(drain_background_group(group, completed))
            )
            if len(arrived) == MESSAGE_COUNT:
                break

        if len(arrived) != MESSAGE_COUNT:
            raise RuntimeError(
                f"background arrival count mismatch: expected={MESSAGE_COUNT}, "
                f"actual={len(arrived)}"
            )

        await asyncio.gather(*drains)
        await emitter
        print("background:受信完了")
        track.cancel()
    finally:
        if not emitter.done():
            emitter.cancel()
            await asyncio.gather(emitter, return_exceptions=True)


async def main() ->None:
    moq.log_level("debug")
    with open(CSV_FILE,"w",newline="") as csv_file:
        writer = csv.writer(csv_file)

        writer.writerow([
            "track",
            "seq",
            "size",
            "send_time_ns",
            "received_time_ns",
            "received_wall_time_ns",
            "delay_ms"
        ])

        async with moq.Client(RELAY_URL, tls_verify=False) as client:
            broadcast = await client.announced_broadcast(BROADCAST_NAME)
            #track = await broadcast.subscribe_track(TRACK_NAME)
            audio_track = await broadcast.subscribe_track(
                AUDIO_TRACK_NAME,
                moq.Subscription(priority=255)
            )

            background_track = await broadcast.subscribe_track(
                BACKGROUND_TRACK_NAME,
                moq.Subscription(priority=0, max_age_us=5_000_000)
                )

            await asyncio.gather(
                receive_track("audio",audio_track,writer),
                receive_background(background_track,writer)
            )        

asyncio.run(main())
