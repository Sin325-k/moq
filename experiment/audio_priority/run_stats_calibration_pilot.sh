#!/bin/bash

set -Eeuo pipefail

ROOT=/home/shinya/moq/experiment/audio_priority
CONDITION=A
[[ $# -eq 0 ]] || {
	echo "usage: $0 (RUN_LABEL may be set in the environment)" >&2
	exit 2
}

RUN_LABEL=${RUN_LABEL:-pilot1}
[[ "$RUN_LABEL" =~ ^[a-zA-Z0-9_-]+$ ]] || {
	echo "不正なrun labelです: $RUN_LABEL" >&2
	exit 2
}
PREFIX=stats_calibration_${CONDITION}_${RUN_LABEL}
RELAY_LOG=$ROOT/relay_${PREFIX}.txt
SUBSCRIBER_LOG=$ROOT/subscriber_${PREFIX}.txt
PUBLISHER_LOG=$ROOT/publisher_${PREFIX}.txt
TBF_LOG=$ROOT/tbf_${PREFIX}.jsonl
CSV_FILE=$ROOT/subscriber_${PREFIX}.csv
QUIC_CSV=$ROOT/quic_${PREFIX}.csv
ANALYSIS_LOG=$ROOT/analysis_${PREFIX}.txt

for output in "$RELAY_LOG" "$SUBSCRIBER_LOG" "$PUBLISHER_LOG" "$TBF_LOG" "$CSV_FILE" "$QUIC_CSV" "$ANALYSIS_LOG"; do
	[[ ! -e "$output" ]] || {
		echo "既存ファイルを上書きしません: $output" >&2
		exit 1
	}
done

for file in publisher_concurrent.py subscriber_concurrent.py tbf_schedule.py analyze_stats_calibration.py relay.sh; do
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
	MOQ_QUIC_STATS_CALIBRATION_CSV="$QUIC_CSV" \
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
	"$ROOT/.venv/bin/python" "$ROOT/subscriber_concurrent.py" \
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

.venv/bin/python analyze_stats_calibration.py \
	"$QUIC_CSV" "$TBF_LOG" "$CSV_FILE" 2>&1 | tee "$ANALYSIS_LOG"

echo "condition $CONDITION $RUN_LABEL 成功"
