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
        async for frame in group:

            #受信した時刻を取得
            receive_time = time.monotonic_ns()

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


async def main() ->None:
    #moq.log_level("debug")
    with open(CSV_FILE,"w",newline="") as csv_file:
        writer = csv.writer(csv_file)

        writer.writerow([
            "track",
            "seq",
            "size",
            "send_time_ns",
            "received_time_ns",
            "delay_ms"
        ])

        async with moq.Client(RELAY_URL, tls_verify=False) as client:
            broadcast = await client.announced_broadcast(BROADCAST_NAME)
            #track = await broadcast.subscribe_track(TRACK_NAME)
            audio_track = await broadcast.subscribe_track(AUDIO_TRACK_NAME)
            background_track = await broadcast.subscribe_track(BACKGROUND_TRACK_NAME)
        
            await asyncio.gather(
                receive_track("audio",audio_track,writer),
                receive_track("background",background_track,writer)
            )        

asyncio.run(main())
