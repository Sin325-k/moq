#!/bin/bash

set -Eeuo pipefail

ROOT=/home/shinya/moq/experiment/audio_priority
CONDITION=C
[[ $# -eq 1 ]] || {
	echo "usage: $0 D1|D2 (RUN_LABEL may be set in the environment)" >&2
	exit 2
}

case "$1" in
	D1) BOTTLENECK_KBIT=800; BANDWIDTH_LABEL=D1_0p8mbps ;;
	D2) BOTTLENECK_KBIT=1200; BANDWIDTH_LABEL=D2_1p2mbps ;;
	*) echo "usage: $0 D1|D2 (RUN_LABEL may be set in the environment)" >&2; exit 2 ;;
esac
RUN_LABEL=${RUN_LABEL:-pilot1}
[[ "$RUN_LABEL" =~ ^[a-zA-Z0-9_-]+$ ]] || {
	echo "不正なrun labelです: $RUN_LABEL" >&2
	exit 2
}
PREFIX=adaptive_c_${BANDWIDTH_LABEL}_${RUN_LABEL}
RELAY_LOG=$ROOT/relay_${PREFIX}.txt
SUBSCRIBER_LOG=$ROOT/subscriber_${PREFIX}.txt
PUBLISHER_LOG=$ROOT/publisher_${PREFIX}.txt
TBF_LOG=$ROOT/tbf_${PREFIX}.jsonl
CSV_FILE=$ROOT/subscriber_${PREFIX}.csv
C_LOG_CSV=$ROOT/quic_${PREFIX}.csv
ANALYSIS_LOG=$ROOT/analysis_${PREFIX}.txt

for output in "$RELAY_LOG" "$SUBSCRIBER_LOG" "$PUBLISHER_LOG" "$TBF_LOG" "$CSV_FILE" "$C_LOG_CSV" "$ANALYSIS_LOG"; do
	[[ ! -e "$output" ]] || {
		echo "既存ファイルを上書きしません: $output" >&2
		exit 1
	}
done

for file in publisher_concurrent.py subscriber_adaptive_c_recovery.py tbf_schedule.py analyze_adaptive_c_bandwidth.py relay.sh; do
	[[ -f "$ROOT/$file" ]] || {
		echo "$file がありません" >&2
		exit 1
	}
done

sudo -n true 2>/dev/null || {
	echo "sudo認証がありません。同じ端末で sudo -v を実行してください" >&2
	exit 1
}

temp_dir=$(mktemp -d)
[[ "$temp_dir" == /tmp/* ]] || {
	echo "安全でない一時ディレクトリです: $temp_dir" >&2
	exit 1
}
start_file=$temp_dir/start
ready_file=$temp_dir/ready
stop_file=$temp_dir/stop
relay_csv_temp=$temp_dir/quic.csv

relay_pid=
subscriber_pid=
tbf_pid=

stop_group() {
	local pid=$1
	if [[ -n "$pid" ]] && kill -0 -- "-$pid" 2>/dev/null; then
		kill -- "-$pid" 2>/dev/null || true
		for _ in {1..50}; do
			kill -0 -- "-$pid" 2>/dev/null || break
			sleep 0.1
		done
		if kill -0 -- "-$pid" 2>/dev/null; then
			kill -KILL -- "-$pid" 2>/dev/null || true
		fi
		wait "$pid" 2>/dev/null || true
	fi
}

stop_tbf() {
	[[ -n "$tbf_pid" ]] || return
	touch "$stop_file" 2>/dev/null || true
	for _ in {1..100}; do
		if ! ps -p "$tbf_pid" >/dev/null 2>&1; then
			wait "$tbf_pid" 2>/dev/null || true
			tbf_pid=
			return
		fi
		sleep 0.1
	done
	sudo -n kill -TERM -- "-$tbf_pid" 2>/dev/null || true
	wait "$tbf_pid" 2>/dev/null || true
	tbf_pid=
}

cleanup() {
	touch "$stop_file" 2>/dev/null || true
	stop_group "$subscriber_pid"
	stop_group "$relay_pid"
	stop_tbf
	if [[ -e "$relay_csv_temp" ]]; then
		if [[ -e "$C_LOG_CSV" ]]; then
			echo "Relay CSVを保持します。衝突先: $C_LOG_CSV、保持場所: $relay_csv_temp" >&2
			return
		fi
		mv -- "$relay_csv_temp" "$C_LOG_CSV"
	fi
	rm -rf -- "$temp_dir"
}
trap cleanup EXIT INT TERM

wait_for_exit() {
	local pid=$1
	local timeout=$2
	local elapsed=0
	while kill -0 "$pid" 2>/dev/null; do
		if ((elapsed >= timeout * 10)); then
			return 124
		fi
		sleep 0.1
		((elapsed += 1)) || true
	done
	wait "$pid"
}

cd "$ROOT"

sudo -n setsid -- "$ROOT/.venv/bin/python" "$ROOT/tbf_schedule.py" \
	--start-file "$start_file" \
	--ready-file "$ready_file" \
	--stop-file "$stop_file" \
	--bottleneck-kbit "$BOTTLENECK_KBIT" \
	> >(tee "$TBF_LOG") 2>&1 &
tbf_pid=$!
for _ in {1..100}; do
	[[ -e "$ready_file" ]] && break
	kill -0 "$tbf_pid" 2>/dev/null || {
		echo "TBF controllerが異常終了しました" >&2
		exit 1
	}
	sleep 0.1
done
[[ -e "$ready_file" ]] || {
	echo "TBF controller ready timeout" >&2
	exit 1
}

setsid env \
	-u MOQ_BACKGROUND_ORACLE \
	-u MOQ_BACKGROUND_LOW_MARKER \
	-u MOQ_BACKGROUND_RECOVERY_MARKER \
	-u MOQ_QUIC_STATS_CALIBRATION_CSV \
	-u BOTTLENECK_KBIT \
	-u BANDWIDTH_LABEL \
	-u PREFIX \
	-u TBF_LOG \
	-u C_LOG_CSV \
	-u RELAY_LOG \
	-u SUBSCRIBER_LOG \
	-u PUBLISHER_LOG \
	-u ANALYSIS_LOG \
	-u CSV_FILE \
	-u RUN_LABEL \
	MOQ_BACKGROUND_ADAPTIVE_C=1 \
	MOQ_BACKGROUND_ADAPTIVE_C_RECOVERY=1 \
	MOQ_BACKGROUND_ADAPTIVE_C_LOG_CSV="$relay_csv_temp" \
	RUST_LOG=moq_net::lite::publisher=debug,moq_relay=info \
	./relay.sh > >(tee "$RELAY_LOG") 2>&1 &
relay_pid=$!
for _ in {1..120}; do
	grep -q "listening" "$RELAY_LOG" 2>/dev/null && break
	kill -0 "$relay_pid" 2>/dev/null || {
		echo "Relayが異常終了しました" >&2
		exit 1
	}
	sleep 1
done
grep -q "listening" "$RELAY_LOG" || {
	echo "Relay start timeout" >&2
	exit 1
}

sudo -n setsid --wait ip netns exec subscriber_ns env \
	EXPERIMENT_CONDITION="$CONDITION" \
	EXPERIMENT_CSV="$CSV_FILE" \
	"$ROOT/.venv/bin/python" "$ROOT/subscriber_adaptive_c_recovery.py" \
	> >(tee "$SUBSCRIBER_LOG") 2>&1 &
subscriber_pid=$!
sleep 5
kill -0 "$subscriber_pid" 2>/dev/null || {
	echo "Subscriberが早期終了しました" >&2
	exit 1
}

set +e
EXPERIMENT_CONDITION="$CONDITION" \
EXPERIMENT_START_FILE="$start_file" \
timeout --signal=TERM --kill-after=5s 180s \
	.venv/bin/python publisher_concurrent.py 2>&1 | tee "$PUBLISHER_LOG"
publisher_status=${PIPESTATUS[0]}
set -e
((publisher_status == 0)) || {
	echo "Publisher異常終了: $publisher_status" >&2
	exit 1
}

set +e
wait_for_exit "$subscriber_pid" 180
subscriber_status=$?
set -e
((subscriber_status == 0)) || {
	echo "Subscriber異常終了: $subscriber_status" >&2
	exit 1
}
subscriber_pid=

touch "$stop_file"
set +e
wait_for_exit "$tbf_pid" 10
tbf_status=$?
set -e
((tbf_status == 0)) || {
	echo "TBF controller異常終了: $tbf_status" >&2
	exit 1
}
tbf_pid=

sleep 1
stop_group "$relay_pid"
relay_pid=
[[ -e "$relay_csv_temp" && ! -e "$C_LOG_CSV" ]] || {
	echo "Relay CSVがないか、成果物が衝突しました。停止します" >&2
	exit 1
}
mv -- "$relay_csv_temp" "$C_LOG_CSV"

.venv/bin/python analyze_adaptive_c_bandwidth.py --condition "$1" \
	"$C_LOG_CSV" "$TBF_LOG" "$CSV_FILE" "$RELAY_LOG" 2>&1 | tee "$ANALYSIS_LOG"

echo "$BANDWIDTH_LABEL $RUN_LABEL の測定・解析が完了しました。成功判定はanalysisを確認してください"
