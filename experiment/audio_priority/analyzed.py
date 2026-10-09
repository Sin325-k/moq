import csv
import numpy as np

CSV_FILE = "優先制御なし5回目.csv"
audio_delays = []
background_delays = []

with open(CSV_FILE,"r",newline = "") as csv_file:
    reader = csv.DictReader(csv_file)
    
    for row in reader:
        delay = float(row["delay_ms"])

        if row["track"] == "audio":
            audio_delays.append(delay)

        elif row["track"] == "background":
            background_delays.append(delay)

print(len(audio_delays))
print(len(background_delays))

###################################################################################################
audio_average = sum(audio_delays) / len(audio_delays)
background_average = sum(background_delays) / len(background_delays)

print(f"audio遅延の平均値は{audio_average:.3f}ms")
print(f"background遅延の平均値は{background_average:.3f}ms")

audio_min = min(audio_delays)
audio_max = max(audio_delays)

print(f"audio遅延の最大値は{audio_max:.3f}ms")
print(f"audio遅延の最小値は{audio_min:.3f}ms")

background_max = max(background_delays)
background_min = min(background_delays)

print(f"background遅延の最大値は{background_max:.3f}ms")
print(f"background遅延の最小値は{background_min:.3f}ms")

#95%遅延
audio_p95 = np.percentile(audio_delays,95)
background_p95 = np.percentile(background_delays,95)

print(f"audio遅延の95%タイルは{audio_p95:.3f}ms")
print(f"background遅延の95%タイルは{background_p95:.3f}ms")

#標準偏差
audio_std = np.std(audio_delays)
background_std = np.std(background_delays)

print(f"audio遅延の標準偏差は{audio_std:.3f}ms")
print(f"background遅延の標準偏差は{background_std:.3f}ms")

#中央値
audio_median = np.median(audio_delays)
background_median = np.median(background_delays)

print(f"audio遅延の中央値は{audio_median:.3f}ms")
print(f"background遅延の中央値は{background_median:.3f}ms")
######################################################################################################