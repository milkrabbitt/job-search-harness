#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
python_bin=${PYTHON_BIN:-}
if [ -z "$python_bin" ]; then
    if [ -x "$project_dir/.venv/bin/python" ]; then
        python_bin="$project_dir/.venv/bin/python"
    else
        python_bin=python3
    fi
fi
data_dir=${JOB_HARNESS_DATA_DIR:-"$project_dir/.job-harness-dev"}

exec "$python_bin" -B -m job_harness --data-dir "$data_dir" serve
