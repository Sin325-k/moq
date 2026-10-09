import asyncio
import moq
import time

RELAY_URL="https://localhost:4443"
BROADCAST_NAME="experiment/audio-priority/minimal"


#TRACK_NAME = "test"
AUDIO_TRACK_NAME = "audio"
BACKGROUND_TRACK_NAME = "background"
MESSAGE_COUNT = 100


async def receive_track(name,track):
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

    async with moq.Client(RELAY_URL, tls_verify=False) as client:
        broadcast = await client.announced_broadcast(BROADCAST_NAME)
        #track = await broadcast.subscribe_track(TRACK_NAME)
        audio_track = await broadcast.subscribe_track(AUDIO_TRACK_NAME)
        background_track = await broadcast.subscribe_track(BACKGROUND_TRACK_NAME)
        
        await asyncio.gather(
            receive_track("audio",audio_track),
            receive_track("background",background_track)
        )        

asyncio.run(main())
