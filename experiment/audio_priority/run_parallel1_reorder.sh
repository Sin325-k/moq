#!/bin/bash

set -Eeuo pipefail

ROOT=/home/shinya/moq/experiment/audio_priority

OUTPUT_BASE=bg_parallel1_reorder_tbf1500k \
EXPECTED_MAX_IN_FLIGHT=1 \
exec "$ROOT/run_parallel2.sh"
