#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
python_bin=${PYTHON_BIN:-python3}
data_dir=${JOB_HARNESS_DATA_DIR:-"$project_dir/.job-harness-dev"}

exec "$python_bin" -B -m job_harness --data-dir "$data_dir" serve
