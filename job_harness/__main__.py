"""Local operator commands and the macOS user service entry point."""

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import plistlib
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

from .channels import build_channels
from .browser_actions import KINDS, RESULTS
from .console import make_handler
from .legacy import load_legacy
from . import rules
from .service import Harness, set_setting, setting
from .storage import Store, stamp


WORKSPACE = Path(__file__).resolve().parent.parent
PRIVATE_ROOT = (Path.home() / "Library" / "Application Support" / "JobHarness-source"
                if sys.platform == "darwin" else Path.home() / ".local" / "share" / "job-harness")
CONFIG_FILE = "config.json"
LAUNCH_AGENT = "local.job-harness.source"
LAUNCHCTL = "/bin/launchctl"
DEFAULT_PORT = 8876


@contextlib.contextmanager
def lock_file(path, description):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(description) from None
        yield


def runtime_locks(root, account):
    locks = contextlib.ExitStack()
    try:
        locks.enter_context(lock_file(root / "runtime.lock", "该数据库已有运行中的 Harness。"))
        if account:
            identifier = hashlib.sha256(account.encode()).hexdigest()
            # Share only the mailbox lock with pre-existing macOS installs. Data roots stay separate.
            lock_root = PRIVATE_ROOT.with_name("JobHarness") if PRIVATE_ROOT.name == "JobHarness-source" else PRIVATE_ROOT
            account_lock = lock_root / "account-locks" / (identifier + ".lock")
            locks.enter_context(lock_file(account_lock, "该邮箱已有运行中的 Harness，不能启动第二个执行器。"))
    except BaseException:
        locks.close()
        raise
    return locks


def write_private(path, payload):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".save-", delete=False) as temp:
        temporary = Path(temp.name)
        try:
            temp.write(payload)
            temp.flush()
            os.fsync(temp.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def load_config(root):
    path = root / CONFIG_FILE
    if not path.is_file():
        raise ValueError("请先执行 init，或使用 install 创建私有配置。")
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("私有配置须是 JSON 对象。")
    port = config.get("port", DEFAULT_PORT)
    if not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ValueError("控制台端口必须在 1024 至 65535 之间。")
    return config


def account_from(manifest):
    for field in ("account", "address"):
        email = manifest.get("email") or {}
        if isinstance(email, dict) and email.get(field):
            return str(email[field])
    for row in manifest.get("facts", []):
        if row.get("key") in {"email", "email.address", "contact.email", "identity.email"} and row.get("state") in {
            "verified", "confirmed", "已核实"
        }:
            return str(row["value"])
    return ""


def stage_registered_resumes(store):
    with store.tx() as connection:
        resumes = connection.execute("SELECT id,path,sha256 FROM resumes WHERE approved=1").fetchall()
    staged, unavailable = [], 0
    for resume in resumes:
        source = Path(resume["path"])
        digest = resume["sha256"]
        if not re.fullmatch(r"[a-f0-9]{64}", digest) or source.suffix.lower() != ".pdf":
            unavailable += 1
            continue
        target = store.root / "resumes" / digest / source.name
        try:
            if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == digest:
                staged.append((str(target), resume["id"]))
                continue
            if source.stat().st_size > 20_000_000:
                unavailable += 1
                continue
            content = source.read_bytes()
            if not content.startswith(b"%PDF-") or hashlib.sha256(content).hexdigest() != digest:
                unavailable += 1
                continue
            write_private(target, content)
            staged.append((str(target), resume["id"]))
        except OSError:
            unavailable += 1
    if staged:
        with store.tx(True) as connection:
            connection.executemany("UPDATE resumes SET path=? WHERE id=?", staged)
    return len(staged), unavailable


def initialize(args, root):
    manifest = load_legacy(args.inheritance.expanduser().resolve()) if args.inheritance else {}
    if not isinstance(manifest, dict):
        raise ValueError("继承文件须是 JSON 对象。")
    prior = load_config(root) if (root / CONFIG_FILE).is_file() else {}
    if prior.get("demo"):
        raise ValueError("演示不能转换为真实资料库。请使用另一个私有数据目录。")
    default_model = {"enabled": False, "executable": ""}
    config = {
        "port": DEFAULT_PORT,
        "email": {"enabled": bool(args.account)},
        "channels": {"liepin": {"enabled": False}},
        "model": default_model,
        "notifications": {"enabled": False},
        **prior,
    }
    account = (args.account or config["email"].get("account") or account_from(manifest)).strip().lower()
    if account and not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@126\.com", account):
        raise ValueError("仅配置当前使用的 @126.com 邮箱地址。")
    config["email"]["account"] = account
    if args.account:
        config["email"]["enabled"] = True
    if args.port is not None:
        config["port"] = args.port
    if not 1024 <= config["port"] <= 65535:
        raise ValueError("控制台端口必须在 1024 至 65535 之间。")
    with runtime_locks(root, account):
        store = Store(root)
        counts = Harness(store, config, build_channels(config)).ingest_legacy(manifest) if manifest else {}
        staged, unavailable = stage_registered_resumes(store)
        write_private(root / CONFIG_FILE, (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    counts_text = "、".join(
        f"{name} {counts[key]}" for key, name in (
            ("facts", "资料"), ("resumes", "简历"), ("sources", "招聘来源"), ("history", "历史记录")
        ) if key in counts
    )
    print("私有配置和数据库已就绪。" + ("已继承 " + counts_text + "。" if counts_text else ""))
    if staged or unavailable:
        print(f"已校验并登记 {staged} 份私有 PDF 副本，{unavailable} 份待核对。")


def notify_local(*_args, **_kwargs):
    """Notification Center uses a fixed local message, with details in the console."""
    script = (
        'display notification "有招聘事项需要处理，请打开本机控制台查看。" '
        'with title "个人求职 Harness"'
    )
    if sys.platform != "darwin":
        return False
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=5, check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def build_service(root, config):
    from .model import CodexModel

    store = Store(root)
    with store.tx() as c:
        demo_mode = config.get("demo") is True or setting(c, "demo_fixture", False)
    if demo_mode:
        from .demo import OfflineRuntime
        offline = OfflineRuntime()
        return Harness(store, {"demo": True}, {"official": offline, "email": offline})
    notifier = notify_local if config.get("notifications", {}).get("enabled", False) else None
    model = CodexModel(config) if config.get("model", {}).get("enabled", False) else None
    return Harness(store, config, build_channels(config), model=model, notifier=notifier)


def serve(root):
    config = load_config(root)
    account = "" if config.get("demo") else config.get("email", {}).get("account", "")
    port = config.get("port", DEFAULT_PORT)
    with runtime_locks(root, account):
        service = build_service(root, config)
        handler = make_handler(service, secrets.token_urlsafe(32), port)
        with ThreadingHTTPServer(("127.0.0.1", port), handler) as server:
            server.timeout = 0.5
            crashed = []

            def stop(_signum=None, _frame=None):
                service.shutdown.set()
                service.wake.set()

            def run():
                try:
                    service.run()
                except Exception as error:
                    crashed.append(type(error).__name__)
                finally:
                    stop()

            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, stop)
            worker = threading.Thread(target=run, name="scheduler")
            worker.start()
            print(f"个人求职 Harness 运行于 http://127.0.0.1:{port}/", flush=True)
            try:
                while not service.shutdown.is_set():
                    server.handle_request()
            finally:
                stop()
                worker.join()
            if crashed:
                raise RuntimeError("调度器异常退出：" + crashed[0])


def service_name(root):
    if root == PRIVATE_ROOT.resolve():
        return LAUNCH_AGENT
    suffix = hashlib.sha256(os.fsencode(root)).hexdigest()[:12]
    return LAUNCH_AGENT + "." + suffix


def launch_paths(root):
    label = service_name(root)
    domain = "gui/" + str(os.getuid())
    plist = Path.home() / "Library" / "LaunchAgents" / (label + ".plist")
    return label, domain, plist


def launch(*args, verify=True):
    if sys.platform != "darwin":
        raise ValueError("本机不是 macOS。请在终端用 serve 启动。")
    result = subprocess.run(
        [LAUNCHCTL, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, check=False,
    )
    if verify and result.returncode:
        raise ValueError("macOS 用户服务操作失败。请确认当前用户已登录桌面后重试。")
    return result


def stage_runtime(root):
    runtime = root / "runtime"
    previous = runtime.resolve() if runtime.is_symlink() else None
    staging = Path(tempfile.mkdtemp(prefix="runtime-", dir=root))
    try:
        package = staging / "job_harness"
        package.mkdir(mode=0o700)
        for source in (WORKSPACE / "job_harness").glob("*.py"):
            write_private(package / source.name, source.read_bytes())
        pointer = staging / ".pointer"
        pointer.symlink_to(staging, target_is_directory=True)
        os.replace(pointer, runtime)
    except BaseException:
        shutil.rmtree(staging)
        raise
    if previous and previous != staging and previous.parent == root and previous.name.startswith("runtime-"):
        shutil.rmtree(previous, ignore_errors=True)
    return runtime


def install(args, root):
    if sys.platform != "darwin":
        raise ValueError("后台安装使用 macOS LaunchAgent。")
    if not (root / CONFIG_FILE).exists() or args.account or args.port is not None or args.inheritance:
        initialize(args, root)
    config = load_config(root)
    label, domain, plist = launch_paths(root)
    if launch("print", domain + "/" + label, verify=False).returncode == 0:
        raise ValueError("后台服务仍在运行。变更安装前先执行 stop。")
    with runtime_locks(root, config.get("email", {}).get("account", "")):
        stage_registered_resumes(Store(root))
        runtime = stage_runtime(root)
    log_dir = root / "logs"
    log_dir.mkdir(mode=0o700, exist_ok=True)
    log_files = [log_dir / name for name in ("service.log", "errors.log")]
    for path in log_files:
        path.touch(mode=0o600, exist_ok=True)
    python = str(Path(sys.executable).absolute())
    codex = config.get("model", {}).get("executable")
    search_path = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    if codex and Path(codex).is_absolute():
        search_path.insert(0, str(Path(codex).parent))
    write_private(plist, plistlib.dumps({
        "Label": label,
        "ProgramArguments": [python, "-m", "job_harness", "--data-dir", str(root), "serve"],
        "WorkingDirectory": str(runtime),
        "EnvironmentVariables": {
            "PATH": os.pathsep.join(search_path), "PYTHONPATH": str(runtime), "PYTHONDONTWRITEBYTECODE": "1",
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "ExitTimeOut": 90,
        "Umask": 0o077,
        "ProcessType": "Background",
        "StandardOutPath": str(log_files[0]),
        "StandardErrorPath": str(log_files[1]),
    }))
    launch("enable", domain + "/" + label)
    start(root)


def healthy(port):
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/healthz", timeout=1) as response:
            return response.status == 200 and json.loads(response.read(200)).get("status") == "ok"
    except (URLError, OSError, ValueError):
        return False


def start(root, open_console=False):
    config = load_config(root)
    label, domain, plist = launch_paths(root)
    if not plist.is_file():
        raise ValueError("尚未安装 LaunchAgent。请先执行 install。")
    url = f"http://127.0.0.1:{config['port']}/"
    target = domain + "/" + label
    with lock_file(root / "service-control.lock", "已有启动或停止操作正在进行。"):
        if launch("print", target, verify=False).returncode:
            launch("enable", target)
            launch("bootstrap", domain, str(plist))
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            state = launch("print", target, verify=False)
            if "state = running" in state.stdout and healthy(config["port"]):
                if open_console:
                    subprocess.run(["/usr/bin/open", url], check=True, timeout=10)
                print("后台服务运行中。控制台 " + url)
                return
            time.sleep(0.25)
    raise ValueError("LaunchAgent 已加载，控制台未通过健康检查。请执行 status 并查看私有服务日志。")


def stop(root, persistent=False):
    label, domain, _plist = launch_paths(root)
    target = domain + "/" + label
    with lock_file(root / "service-control.lock", "已有启动或停止操作正在进行。"):
        if persistent:
            launch("disable", target)
        if launch("print", target, verify=False).returncode == 0:
            launch("bootout", target)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                with lock_file(root / "runtime.lock", "当前执行器仍在退出。"):
                    print("后台服务已停止。")
                    return
            except ValueError:
                time.sleep(0.25)
    raise ValueError("当前执行器仍在退出。请稍后检查 status；前台 serve 须在其终端按 Ctrl+C。")


def status(root):
    config = load_config(root)
    label, domain, plist = launch_paths(root)
    loaded = launch("print", domain + "/" + label, verify=False) if sys.platform == "darwin" else None
    running = loaded and loaded.returncode == 0 and "state = running" in loaded.stdout
    if running:
        print("LaunchAgent：运行中")
    elif loaded and loaded.returncode == 0:
        print("LaunchAgent：已加载，进程未报告运行")
    else:
        print("LaunchAgent：" + ("已安装，未加载" if plist.is_file() else "尚未安装"))
    port = config["port"]
    print(f"控制台：{'健康' if healthy(port) else '离线'}，http://127.0.0.1:{port}/")
    print("私有数据：" + str(root))


def backup(root):
    load_config(root)
    with lock_file(root / "maintenance.lock", "另一个备份或恢复操作正在执行。"):
        print("数据库备份：" + str(Store(root).backup()))


def protect_later_sends(current, incoming):
    sql = "SELECT message_id,state FROM outbox WHERE state IN ('sending','unknown','confirmed')"
    evidence = dict(incoming.execute(sql).fetchall())
    for message_id, state in current.execute(sql):
        prior = evidence.get(message_id)
        if prior is None or state == "confirmed" and prior != "confirmed":
            raise ValueError("指定备份早于当前的外发证据。请使用较新的备份，避免恢复后重复联系。")
    blocking = (*rules.BLOCKING, "stop")
    history = "SELECT fingerprint FROM history WHERE status IN (" + ",".join("?" for _ in blocking) + ")"
    preserved = {row[0] for row in incoming.execute(history, blocking)}
    if any(row[0] not in preserved for row in current.execute(history, blocking)):
        raise ValueError("指定备份缺少当前台账的外联记录。请使用较新的备份。")
    def browser_evidence(connection):
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='browser_actions'").fetchone():
            return {}
        return {
            row[:-1]: row[-1]
            for row in connection.execute("""SELECT id,job_id,channel,url,kind,anchor_id,claim_token,state
                 FROM browser_actions WHERE state IN ('sending','unknown','attachment_requested','confirmed')""")
        }

    previous = browser_evidence(incoming)
    for identity, state in browser_evidence(current).items():
        if identity not in previous or state in {"confirmed", "attachment_requested"} and previous[identity] != state:
            raise ValueError("指定备份早于当前浏览器外发证据。请使用较新的备份，避免重复联系。")

    def active_followups(connection):
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='employer_followups'").fetchone()
        if not exists:
            return set()
        return {row[0] for row in connection.execute("""SELECT e.identity FROM employer_followups f
                  JOIN employers e ON e.id=f.employer_id WHERE f.released_at IS NULL""")}

    if active_followups(current) - active_followups(incoming):
        raise ValueError("指定备份缺少当前公司人工跟进的暂停记录。请使用较新的备份。")


def preserve_current_database(root, path, incoming):
    if not path.exists():
        return None
    try:
        with contextlib.closing(sqlite3.connect(str(path), timeout=8)) as connection:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("integrity check failed")
            protect_later_sends(connection, incoming)
        fallback = Store(root).backup()
        with contextlib.closing(sqlite3.connect(str(path), timeout=8)) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return fallback
    except sqlite3.DatabaseError:
        folder = root / "backups"
        folder.mkdir(exist_ok=True, mode=0o700)
        archive = Path(tempfile.mkdtemp(prefix="damaged-database-", dir=folder))
        for suffix in ("", "-wal", "-shm"):
            source = Path(str(path) + suffix)
            if source.is_file():
                shutil.copyfile(source, archive / source.name)
        return archive


def restore(root, source):
    config = load_config(root)
    account = config.get("email", {}).get("account", "")
    source = source.expanduser().resolve()
    if not source.is_file():
        raise ValueError("找不到指定备份。")
    with runtime_locks(root, account), lock_file(root / "maintenance.lock", "另一个备份或恢复操作正在执行。"):
        path = root / "harness.sqlite3"
        if source == path:
            raise ValueError("请选择备份文件。")
        descriptor, temp_name = tempfile.mkstemp(dir=root, prefix=".restore-")
        os.close(descriptor)
        temporary = Path(temp_name)
        try:
            with contextlib.closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as saved:
                if saved.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("备份未通过 SQLite 完整性检查。")
                names = {row[0] for row in saved.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {"settings", "channels", "history", "outbox", "tasks"} <= names:
                    raise ValueError("文件不是此 Harness 的数据库备份。")
                fallback = preserve_current_database(root, path, saved)
                with contextlib.closing(sqlite3.connect(str(temporary))) as dest:
                    saved.backup(dest)
                    with dest:
                        set_setting(dest, "outbound_paused", True)
                        set_setting(dest, "stopped", True)
                        set_setting(dest, "stop_generation", int(setting(dest, "stop_generation", 0)) + 1)
                        set_setting(dest, "live_armed", False)
                        dest.execute(
                            "UPDATE outbox SET state='unknown',updated_at=?,evidence=? WHERE state='sending'",
                            (stamp(), "数据库恢复时发送结果未核实；检查原渠道后处理。"),
                        )
                        dest.execute(
                            """UPDATE outbox SET state='draft',approved=0,approval_hash=NULL,policy_generated=0,
                               updated_at=?,evidence=? WHERE state IN ('queued','draft')""",
                            (stamp(), "由备份恢复。请先核对已发送记录，然后重新批准。"),
                        )
                        dest.execute(
                            "UPDATE outbox SET retryable=0 WHERE state IN ('unknown','failed')"
                        )
                        dest.execute(
                            """UPDATE tasks SET state='stopped',cancelled=1,updated_at=?,detail=?
                               WHERE state IN ('queued','running')""",
                            (stamp(), "备份恢复后取消旧队列。"),
                        )
                        if "browser_actions" in names:
                            dest.execute("""UPDATE browser_actions SET state='unknown',updated_at=?,evidence=?
                                          WHERE state='sending'""",
                                         (stamp(), "数据库恢复时结果未核实，请查原平台。"))
                            dest.execute("""UPDATE browser_actions SET state='blocked',updated_at=?,evidence=?
                                          WHERE state='queued'""",
                                         (stamp(), "数据库恢复后暂停旧动作，请核对原平台后再认领。"))
            for suffix in ("-wal", "-shm"):
                Path(str(path) + suffix).unlink(missing_ok=True)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    print("数据库已恢复，外发暂停。请先核对实际发送记录。")
    if fallback:
        print("恢复前备份：" + str(fallback))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="个人求职 Harness")
    parser.add_argument("--data-dir", type=Path, default=PRIVATE_ROOT, help="本机私有数据目录")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "install"):
        command = commands.add_parser(name)
        inputs = command.add_mutually_exclusive_group()
        inputs.add_argument("--inheritance", type=Path, help="显式导入本地 JSON，不读取原项目或运行目录")
        inputs.add_argument("--empty", action="store_true", help="从空资料开始（默认）")
        command.add_argument("--account", help="已确认的网易 126 邮箱地址")
        command.add_argument("--port", type=int, help="本地控制台端口")
    for name in ("serve", "start", "stop", "status", "backup", "model-check", "desktop-open", "desktop-stop", "install-desktop"):
        commands.add_parser(name)
    commands.add_parser("demo", help="初始化虚构数据，不连接招聘渠道")
    commands.add_parser("restore").add_argument("source", type=Path)
    commands.add_parser("audit-mail").add_argument("source", type=Path)
    commands.add_parser("review-liepin").add_argument("task_id", type=int)
    commands.add_parser("send-now", help="仅授权指定的已批准邮件申请越过本次发送时段").add_argument("outbox_id", type=int)
    browser_queue = commands.add_parser("browser-queue", help="为已登录浏览器登记求职动作")
    browser_queue.add_argument("job_id", type=int)
    browser_queue.add_argument("kind", choices=KINDS)
    browser_queue.add_argument("--url")
    browser_queue.add_argument("--body")
    browser_queue.add_argument("--anchor", type=int)
    browser_list = commands.add_parser("browser-actions", help="查看浏览器动作及平台回执")
    browser_list.add_argument("--job", type=int)
    browser_claim = commands.add_parser("browser-claim", help="原平台复核后认领一个前台动作")
    browser_claim.add_argument("action_id", type=int)
    browser_claim.add_argument("--retry-evidence")
    browser_check = commands.add_parser("browser-check", help="在原平台提交前复查认领和外发状态")
    browser_check.add_argument("action_id", type=int)
    browser_check.add_argument("--token", required=True)
    browser_finish = commands.add_parser("browser-finish", help="登记浏览器上核实到的实际结果")
    browser_finish.add_argument("action_id", type=int)
    browser_finish.add_argument("state", choices=RESULTS)
    browser_finish.add_argument("--evidence", required=True)
    browser_finish.add_argument("--receipt")
    browser_finish.add_argument("--token")
    browser_reconcile = commands.add_parser("browser-reconcile", help="依据原平台证据核对已过期或待确认的动作")
    browser_reconcile.add_argument("action_id", type=int)
    browser_reconcile.add_argument("state", choices=("confirmed", "blocked"))
    browser_reconcile.add_argument("--evidence", required=True)
    browser_reconcile.add_argument("--receipt")
    args = parser.parse_args()
    root = args.data_dir.expanduser().resolve()
    try:
        if args.command in {"init", "install"}:
            {"init": initialize, "install": install}[args.command](args, root)
        elif args.command == "demo":
            from .demo import initialize as init_demo
            sample = json.loads((WORKSPACE / "examples" / "config.example.json").read_text(encoding="utf-8"))
            init_demo(root, sample, load_config, runtime_locks, write_private)
        elif args.command == "restore":
            restore(root, args.source)
        elif args.command == "model-check":
            config = load_config(root)
            print(build_service(root, config).action("model_probe", {}))
        elif args.command == "audit-mail":
            service = build_service(root, load_config(root))
            source = args.source.expanduser()
            if source.stat().st_size > 250_000:
                raise ValueError("基线证据文件过大。")
            manifest = json.loads(source.read_text(encoding="utf-8"))
            print("已安排后台复核基线，登记 " + str(service.stage_mail_evidence(manifest)) + " 项锚点。")
        elif args.command == "desktop-open":
            start(root, open_console=True)
        elif args.command == "desktop-stop":
            stop(root, persistent=True)
        elif args.command == "install-desktop":
            from .desktop import install_shortcuts
            for path in install_shortcuts(root, Path(sys.executable).absolute()):
                print("桌面入口：" + str(path))
        elif args.command == "review-liepin":
            service = build_service(root, load_config(root))
            print("已登记 " + str(service.stage_liepin_details(args.task_id)) + " 个公开猎聘详情，按后台节奏处理。")
        elif args.command == "send-now":
            config = load_config(root)
            if not healthy(config["port"]):
                raise ValueError("后台服务未运行，先启动求职助手")
            print(build_service(root, config).authorize_send_once(args.outbox_id))
        elif args.command.startswith("browser-"):
            service = build_service(root, load_config(root))
            browser = service.browser_actions
            if args.command == "browser-queue":
                print(service.action("browser_queue", {"job_id": args.job_id, "kind": args.kind,
                    "url": args.url, "body": args.body, "anchor_id": args.anchor}))
            elif args.command == "browser-actions":
                with service.store.tx() as c:
                    print(json.dumps(browser.list(c, args.job), ensure_ascii=False, indent=2))
            elif args.command == "browser-claim":
                print(json.dumps(browser.claim(args.action_id, args.retry_evidence), ensure_ascii=False))
            elif args.command == "browser-check":
                browser.check(args.action_id, args.token)
                print("认领和外发状态有效")
            else:
                browser.finish(args.action_id, args.state, args.evidence, args.receipt,
                               getattr(args, "token", None), reconcile=args.command == "browser-reconcile")
                print(f"浏览器动作 #{args.action_id} 已记录 {args.state}")
        else:
            {"serve": serve, "start": start, "stop": stop, "status": status, "backup": backup}[args.command](root)
    except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as error:
        message = str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
        print("操作未完成：" + message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
