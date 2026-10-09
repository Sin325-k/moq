import asyncio
import moq
import time #時刻の測定に必要


RELAY_URL = "https://localhost:4443"
BROADCAST_NAME = "experiment/audio-priority/minimal"
#TRACK_NAME = "test"
AUDIO_TRACK_NAME="audio"
BACKGROUND_TRACK_NAME="background"

#送信の条件
MESSAGE_COUNT = 100
PAYLOAD_SIZE  = 1945
INTERVAL      = 0.020

async def main() -> None:
	#moq.log_level("debug")
	print(f"relayへ接続します: {RELAY_URL}")
	async with moq.Client(RELAY_URL,tls_verify=False) as client:
		
		#broadcastの作成
		broadcast = client.create_broadcast(BROADCAST_NAME)

		#trackの作成
		#track = broadcast.publish_track(TRACK_NAME)
		audio_track = broadcast.publish_track(AUDIO_TRACK_NAME)
		background_track = broadcast.publish_track(BACKGROUND_TRACK_NAME)

		#Broadcastの公開
		broadcast.announce()

		#groupの作成
		for i in range(MESSAGE_COUNT):

			#seququence番号
			seq = i.to_bytes(4, byteorder = "big")

			#group = track.append_group()
			#data = f"message{i}".encode()
			#data = bytes(PAYLOAD_SIZE)
			#group.write_frame(data,timestamp_us=i*20000)
			
			#groupの終了
			#group.finish()

			###################音声データ###############################################################

			#時刻の取得
			audio_send_time = time.monotonic_ns()
			
			#送信時刻を8bytesデータに変換
			audio_send_time_bytes = audio_send_time.to_bytes(8 , byteorder = "big")
			
			audio_group = audio_track.append_group()
			audio_data = seq +  audio_send_time_bytes + bytes(PAYLOAD_SIZE - 12) 
			audio_group.write_frame(audio_data, timestamp_us=i*20000)
			audio_group.finish()

			###################背景データ############################################################
			background_send_time = time.monotonic_ns()
			background_send_time_bytes = background_send_time.to_bytes(8 ,byteorder = "big")

			background_group =background_track.append_group()
			background_data = seq + background_send_time_bytes + bytes(PAYLOAD_SIZE - 12)  
			background_group.write_frame(background_data, timestamp_us=i*20000)
			background_group.finish()

			#await asyncio.sleep(1)
			await asyncio.sleep(INTERVAL)

		#trackの終了
		audio_track.finish()
		background_track.finish()

		#broadcastの終了
		broadcast.close() 

asyncio.run(main())
