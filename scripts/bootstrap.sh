#!/bin/sh
set -eu
umask 077

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
python_bin=${PYTHON_BIN:-python3}
data_dir=${JOB_HARNESS_DATA_DIR:-"$project_dir/.job-harness-dev"}

case "${1:-}" in
    "") set -- --empty ;;
    --demo) set -- --demo ;;
    --example) set -- --inheritance "$project_dir/examples/profile.example.json" ;;
    *) printf 'Usage: %s [--example|--demo]\n' "$0" >&2; exit 2 ;;
esac

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
