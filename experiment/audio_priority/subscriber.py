import asyncio
import moq

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
            count += 1
            #data = frame.payload.decode()

            #print(data)
            print(
                f"{name}:"
                f"frame{count}:"
                f"size{len(frame.payload)}bytes"
            )

            if count == MESSAGE_COUNT:
                print("受信完了")
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
