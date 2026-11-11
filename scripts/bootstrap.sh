#!/bin/sh
set -eu
umask 077

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

bootstrap_mode=--empty
bootstrap_deps=0
for bootstrap_arg in "$@"; do
    case "$bootstrap_arg" in
        --demo) bootstrap_mode=--demo ;;
        --example) bootstrap_mode=--example ;;
        --install-deps) bootstrap_deps=1 ;;
        *) printf 'Usage: %s [--example|--demo] [--install-deps]\n' "$0" >&2; exit 2 ;;
    esac
done
case "$bootstrap_mode" in
    --example) set -- --inheritance "$project_dir/examples/profile.example.json" ;;
    *) set -- "$bootstrap_mode" ;;
esac
if [ "$bootstrap_deps" = 1 ]; then
    if [ -z "${PYTHON_BIN:-}" ]; then
        if [ ! -e "$project_dir/.venv" ]; then
            "$python_bin" -m venv "$project_dir/.venv"
        fi
        if [ ! -x "$project_dir/.venv/bin/python" ]; then
            printf '%s\n' 'Existing .venv is not a usable POSIX environment; set PYTHON_BIN explicitly.' >&2
            exit 1
        fi
        python_bin="$project_dir/.venv/bin/python"
    fi
    "$python_bin" -m pip install -r "$project_dir/requirements.txt"
fi

"$python_bin" -B - <<'PYTHON'
import sqlite3
import sys

if sys.version_info < (3, 9) or sqlite3.sqlite_version_info < (3, 24):
    raise SystemExit("需要 Python 3.9+ 和 SQLite 3.24+")
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
try:
    ZoneInfo("Asia/Shanghai")
except ZoneInfoNotFoundError:
    raise SystemExit("缺少 IANA 时区数据。请安装系统 tzdata，或在当前 Python 环境中安装 tzdata。")
PYTHON

if [ "${1:-}" = --demo ]; then
    exec "$python_bin" -B -m job_harness --data-dir "$data_dir" demo
fi

mkdir -p -- "$data_dir"
if [ ! -e "$data_dir/config.json" ]; then
    cp -- "$project_dir/examples/config.example.json" "$data_dir/config.json"
fi
exec "$python_bin" -B -m job_harness --data-dir "$data_dir" init "$@"
