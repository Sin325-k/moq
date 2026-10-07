import asyncio
import moq

RELAY_URL="https://localhost:4443"
BROADCAST_NAME="experiment/audio-priority/minimal"
TRACK_NAME = "test"

async def main() ->None:
    moq.log_level("debug")

    async with moq.Client(RELAY_URL, tls_verify=False) as client:
        broadcast = await client.announced_broadcast(BROADCAST_NAME)
        track = await broadcast.subscribe_track(TRACK_NAME)
        async for group in track:
            async for frame in group:
                data = frame.payload.decode()

                print(data)


asyncio.run(main())
