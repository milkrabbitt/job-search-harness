#!/bin/sh
set -eu

case "${1:-}" in
    -h|--help)
        printf '%s\n' '用法：./scripts/doctor.sh' '可选环境变量：PYTHON_BIN、JOB_HARNESS_DATA_DIR'
        exit 0
        ;;
    "") ;;
    *) printf '%s\n' '用法：./scripts/doctor.sh' >&2; exit 2 ;;
esac

project_dir=$(CDPATH= cd -P "$(dirname "$0")/.." && pwd)
cd "$project_dir"
python_bin=${PYTHON_BIN:-python3}
data_dir=${JOB_HARNESS_DATA_DIR:-"$project_dir/.job-harness-dev"}

if ! command -v "$python_bin" >/dev/null 2>&1; then
    printf '%s\n' '[失败] 找不到 Python。请安装 Python 3.9+，或通过 PYTHON_BIN 指定解释器。' >&2
    exit 1
fi

exec "$python_bin" -B - "$data_dir" <<'PYTHON'
import importlib
import json
import os
import shutil
import socket
import sys
from pathlib import Path

if sys.version_info < (3, 9):
    raise SystemExit("[失败] 需要 Python 3.9 或更新版本。")
print("[通过] Python " + sys.version.split()[0])

required_modules = ("sqlite3", "zoneinfo", "msvcrt" if os.name == "nt" else "fcntl", "ssl")
for name in required_modules:
    try:
        importlib.import_module(name)
    except (ImportError, OSError):
        raise SystemExit(f"[失败] Python 缺少 {name}。请使用完整的 Python 安装。")

import sqlite3
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if sqlite3.sqlite_version_info < (3, 24):
    raise SystemExit("[失败] 当前解释器需要链接 SQLite 3.24 或更新版本。")
try:
    with sqlite3.connect(":memory:") as connection:
        connection.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO probe VALUES (1) ON CONFLICT(id) DO NOTHING")
except sqlite3.Error:
    raise SystemExit("[失败] SQLite 无法运行本地内存数据库。")
print(f"[通过] SQLite {sqlite3.sqlite_version}，并支持文件锁和 TLS 所需模块")

try:
    ZoneInfo("Asia/Shanghai")
except ZoneInfoNotFoundError:
    raise SystemExit("[失败] 缺少 Asia/Shanghai 时区。请安装系统 tzdata，或在当前环境安装 Python tzdata。")
print("[通过] Asia/Shanghai 时区")

for required_file in ("job_harness/__main__.py", "job_harness/storage.py", "job_harness/console.py",
                      "examples/config.example.json"):
    if not Path(required_file).is_file():
        raise SystemExit(f"[失败] 缺少项目文件 {required_file}。请重新克隆完整仓库。")
print("[通过] 找到服务、数据库结构和控制台源码")

profile = Path(sys.argv[1]).expanduser()
try:
    writable_root = next(item for item in (profile, *profile.parents) if item.exists())
except (OSError, StopIteration):
    raise SystemExit("[失败] 无法定位资料目录。")
if not writable_root.is_dir() or not os.access(writable_root, os.W_OK | os.X_OK):
    raise SystemExit("[失败] 资料目录不可写。请设置 JOB_HARNESS_DATA_DIR 为自己的可写目录。")

config = profile / "config.json"
if config.exists():
    try:
        settings = json.loads(config.read_text(encoding="utf-8"))
        if not isinstance(settings, dict):
            raise ValueError
        print("[通过] 本机配置可读")
    except (OSError, ValueError):
        raise SystemExit("[失败] config.json 无法读取或不是有效的 JSON 对象。")
else:
    print("[提示] 尚未初始化此资料目录，可以先运行 ./scripts/bootstrap.sh --demo。")
    settings = {}

port = settings.get("port", 8876)
if type(port) is not int or not 1024 <= port <= 65535:
    raise SystemExit("[失败] config.json 的 port 必须是 1024 到 65535 的整数。")

try:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
except OSError:
    raise SystemExit("[失败] 当前环境无法绑定 IPv4 回环地址 127.0.0.1。")
try:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", port))
except OSError:
    print(f"[提示] 127.0.0.1:{port} 无法绑定。若控制台已运行，直接打开即可；否则选择其他端口。")
else:
    print(f"[通过] 本机控制台端口 {port} 可绑定")

print("[可选] 在控制台开始配置页选择并测试模型；此诊断不会读取密钥或调用模型。")

if sys.platform == "darwin":
    print("[可选] macOS 支持后台服务和钥匙串接入。")
else:
    print("[可选] 当前平台使用前台服务。钥匙串和 LaunchAgent 功能需 macOS。")
print("诊断完成。未检查账号、凭据、网络或招聘渠道。")
PYTHON
