"""Install two local macOS app shortcuts for the existing user service."""

import plistlib
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path


SHORTCUTS = (
    ("Job Harness（源码版）", "local.job-harness.source.open", "desktop-open"),
    ("停止 Job Harness（源码版）", "local.job-harness.source.stop", "desktop-stop"),
)


def script(command, title, stopped):
    quoted = command.replace("\\", "\\\\").replace('"', '\\"')
    success = 'display alert "求职助手已停止" message "再次打开桌面上的「Job Harness（源码版）」即可恢复。"' if stopped else ""
    return f'''on run
        try
            do shell script "{quoted}"
            {success}
        on error reason
            display alert "{title}" message reason as warning
        end try
    end run'''


def install_shortcuts(root, python):
    runtime = root / "runtime"
    if not (runtime / "job_harness" / "__main__.py").is_file():
        raise ValueError("请先安装求职助手后台，再建立桌面入口。")
    desktop = Path.home() / "Desktop"
    if not desktop.is_dir():
        raise ValueError("找不到当前用户的桌面。")
    installed = []
    with tempfile.TemporaryDirectory(prefix=".job-harness-", dir=desktop) as directory:
        staging = Path(directory)
        for title, identifier, action in SHORTCUTS:
            path = desktop / (title + ".app")
            candidate = staging / path.name
            if path.exists() or path.is_symlink():
                if path.is_symlink() or not path.is_dir():
                    raise ValueError("桌面已有同名入口：" + title)
                try:
                    existing = plistlib.loads((path / "Contents" / "Info.plist").read_bytes())
                except (OSError, plistlib.InvalidFileException):
                    existing = {}
                if existing.get("CFBundleIdentifier") != identifier:
                    raise ValueError("桌面已有同名应用：" + title)
            command = "cd " + shlex.quote(str(runtime)) + " && exec " + shlex.join([
                str(python), "-B", "-m", "job_harness", "--data-dir", str(root), action,
            ])
            subprocess.run(["/usr/bin/osacompile", "-o", str(candidate), "-e", script(command, title, action == "desktop-stop")],
                           check=True, timeout=20, capture_output=True)
            info = candidate / "Contents" / "Info.plist"
            metadata = plistlib.loads(info.read_bytes())
            metadata.update(CFBundleIdentifier=identifier, CFBundleName=title, CFBundleDisplayName=title)
            info.write_bytes(plistlib.dumps(metadata))
            subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(candidate)],
                           check=True, timeout=20, capture_output=True)
            installed.append((candidate, path))
        for candidate, path in installed:
            if path.exists():
                backup = staging / (path.name + ".previous")
                path.rename(backup)
                try:
                    candidate.rename(path)
                except OSError:
                    backup.rename(path)
                    raise
                shutil.rmtree(backup)
            else:
                candidate.rename(path)
    return [path for _, path in installed]
