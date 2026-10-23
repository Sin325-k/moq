#!/usr/bin/env python3

import argparse
import csv
import re
import numpy as np


# ==============================
# 正規表現
# ==============================

# 例:
# === 1792666320092148256 ===
TIMESTAMP_RE = re.compile(
    r"^===\s*(\d+)\s*===$"
)

# 例:
# Sent 5508771 bytes 7007 pkt (dropped 0, overlimits 11555 requeues 0)
SENT_RE = re.compile(
    r"Sent\s+(\d+)\s+bytes\s+(\d+)\s+pkt\s+"
    r"\(dropped\s+(\d+),\s+overlimits\s+(\d+)\s+requeues\s+(\d+)\)"
)

# 例:
# backlog 38039b 37p requeues 0
BACKLOG_RE = re.compile(
    r"backlog\s+([0-9.]+)([KMG]?b)\s+(\d+)p"
)


# ==============================
# backlogの単位をbyteへ変換
# ==============================

def backlog_to_bytes(value, unit):

    multipliers = {
        "b": 1,
        "Kb": 1024,
        "Mb": 1024 ** 2,
        "Gb": 1024 ** 3,
    }

    return value * multipliers[unit]


# ==============================
# TBFログを読み込む
# ==============================

def parse_tbf_log(path):

    samples = []

    current = None

    with open(path, "r", encoding="utf-8") as f:

        for raw_line in f:

            line = raw_line.strip()

            # ------------------
            # timestamp
            # ------------------

            m = TIMESTAMP_RE.match(line)

            if m:

                if current is not None and "backlog_bytes" in current:
                    samples.append(current)

                current = {
                    "timestamp_ns": int(m.group(1))
                }

                continue

            if current is None:
                continue

            # ------------------
            # Sent / dropped / overlimits
            # ------------------

            m = SENT_RE.search(line)

            if m:

                current["sent_bytes"] = int(m.group(1))

                current["sent_packets"] = int(m.group(2))

                current["dropped"] = int(m.group(3))

                current["overlimits"] = int(m.group(4))

                current["requeues"] = int(m.group(5))

                continue

            # ------------------
            # backlog
            # ------------------

            m = BACKLOG_RE.search(line)

            if m:

                current["backlog_bytes"] = backlog_to_bytes(
                    float(m.group(1)),
                    m.group(2)
                )

                current["backlog_packets"] = int(m.group(3))

    # 最後のデータを追加
    if current is not None and "backlog_bytes" in current:
        samples.append(current)

    return samples


# ==============================
# Subscriber CSVの受信時間を取得
# ==============================

def csv_time_range(path):

    times = []

    with open(path, "r", encoding="utf-8", newline="") as f:

        reader = csv.DictReader(f)

        for row in reader:

            value = row.get("received_wall_time_ns")

            if value:

                times.append(
                    int(value)
                )

    if not times:

        raise ValueError(
            "CSVから received_wall_time_ns を読み取れませんでした"
        )

    return min(times), max(times)


# ==============================
# CSVの時間に合わせてTBFログを切り出す
# ==============================

def filter_by_time(
    samples,
    start_ns,
    end_ns,
    margin_ms=200
):

    margin_ns = int(
        margin_ms * 1_000_000
    )

    start_ns -= margin_ns

    end_ns += margin_ns

    return [
        sample
        for sample in samples
        if start_ns
        <= sample["timestamp_ns"]
        <= end_ns
    ]


# ==============================
# TBF分析
# ==============================

def print_analysis(samples, title):

    if not samples:

        print(
            f"\n=== {title} ==="
        )

        print(
            "分析できるサンプルがありません"
        )

        return

    # ------------------
    # backlog
    # ------------------

    backlog_bytes = np.array(
        [
            sample["backlog_bytes"]
            for sample in samples
        ],
        dtype=float
    )

    backlog_packets = np.array(
        [
            sample["backlog_packets"]
            for sample in samples
        ],
        dtype=float
    )

    # ------------------
    # 最初と最後
    # ------------------

    first = samples[0]

    last = samples[-1]

    # ------------------
    # 観測時間
    # ------------------

    duration_sec = (
        last["timestamp_ns"]
        -
        first["timestamp_ns"]
    ) / 1_000_000_000

    # ------------------
    # 送信量
    # ------------------

    sent_bytes_delta = (
        last.get("sent_bytes", 0)
        -
        first.get("sent_bytes", 0)
    )

    sent_packets_delta = (
        last.get("sent_packets", 0)
        -
        first.get("sent_packets", 0)
    )

    # ------------------
    # dropped
    # ------------------

    dropped_delta = (
        last.get("dropped", 0)
        -
        first.get("dropped", 0)
    )

    # ------------------
    # overlimits
    # ------------------

    overlimits_delta = (
        last.get("overlimits", 0)
        -
        first.get("overlimits", 0)
    )

    # ------------------
    # backlog > 0 の回数
    # ------------------

    positive_count = int(
        np.sum(
            backlog_bytes > 0
        )
    )

    # ------------------
    # 平均スループット
    # ------------------

    if duration_sec > 0:

        throughput_mbps = (
            sent_bytes_delta
            * 8
            / duration_sec
            / 1_000_000
        )

    else:

        throughput_mbps = 0.0

    # ==============================
    # 結果表示
    # ==============================

    print(
        f"\n=== {title} ==="
    )

    print(
        f"観測サンプル数           : {len(samples)}"
    )

    print(
        f"観測時間                 : {duration_sec:.3f} 秒"
    )

    print(
        f"送信byte増加量           : {sent_bytes_delta} byte"
    )

    print(
        f"送信packet増加量         : {sent_packets_delta}"
    )

    print(
        f"平均スループット         : {throughput_mbps:.3f} Mbps"
    )

    print()

    print(
        f"最大backlog              : {np.max(backlog_bytes):.0f} byte"
    )

    print(
        f"最大backlog packet数     : {np.max(backlog_packets):.0f} packet"
    )

    print(
        f"平均backlog              : {np.mean(backlog_bytes):.1f} byte"
    )

    print(
        f"backlog 95%タイル        : {np.percentile(backlog_bytes, 95):.1f} byte"
    )

    print(
        f"backlog > 0 の観測回数   : {positive_count}/{len(samples)}"
    )

    print()

    print(
        f"dropped増加数            : {dropped_delta}"
    )

    print(
        f"overlimits増加数         : {overlimits_delta}"
    )


# ==============================
# main
# ==============================

def main():

    parser = argparse.ArgumentParser(
        description="tc TBFログを解析するスクリプト"
    )

    parser.add_argument(
        "tbf_log",
        help="TBFログのtxtファイル"
    )

    parser.add_argument(
        "--csv",
        help=(
            "SubscriberのCSVを指定すると、"
            "received_wall_time_nsに合わせて"
            "実験区間だけも解析する"
        )
    )

    parser.add_argument(
        "--margin-ms",
        type=int,
        default=200,
        help=(
            "CSV時刻の前後に含める余裕時間(ms)"
            "。初期値は200"
        )
    )

    args = parser.parse_args()

    # ------------------
    # TBFログ読み込み
    # ------------------

    samples = parse_tbf_log(
        args.tbf_log
    )

    if not samples:

        print(
            "TBFログを読み取れませんでした"
        )

        return

    # ------------------
    # ログ全体を分析
    # ------------------

    print_analysis(
        samples,
        "TBFログ全体"
    )

    # ------------------
    # CSVが指定された場合
    # ------------------

    if args.csv:

        start_ns, end_ns = csv_time_range(
            args.csv
        )

        experiment_samples = filter_by_time(
            samples,
            start_ns,
            end_ns,
            args.margin_ms
        )

        print_analysis(
            experiment_samples,
            "Subscriber受信時間に合わせた実験区間"
        )


if __name__ == "__main__":
    main()