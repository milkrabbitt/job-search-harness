"""Import an explicitly selected PDF into the private, immutable-by-content store.

This module has no network access and never modifies the user's source file.
PDF checks establish type/size only; they do not certify its contents or safety.
"""

import hashlib
import os
import stat
import tempfile
from pathlib import Path


MAX_PDF_BYTES = 20_000_000
SUPPORTED_SCOPES = frozenset({"general", "ai", "robotics", "finance"})


def _read_pdf(source):
    try:
        original = source.lstat()
        if not stat.S_ISREG(original.st_mode):
            raise ValueError("请选择普通 PDF 文件；不接受目录或符号链接。")
        if original.st_size > MAX_PDF_BYTES:
            raise ValueError("PDF 不能超过 20 MB。")
        if original.st_size < 5:
            raise ValueError("PDF 文件为空或不完整。")
        # Nonblocking open also prevents a raced-in FIFO from hanging setup.
        flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_NONBLOCK", 0))
        descriptor = os.open(source, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (original.st_dev, original.st_ino):
                raise ValueError("所选文件在导入前发生变化，请重新选择。")
            content = stream.read(MAX_PDF_BYTES + 1)
            after = os.fstat(stream.fileno())
        if len(content) > MAX_PDF_BYTES:
            raise ValueError("PDF 不能超过 20 MB。")
        if len(content) != opened.st_size or (after.st_size, after.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns):
            raise ValueError("所选文件在读取时发生变化，请保存后重新导入。")
        if not content.startswith(b"%PDF-"):
            raise ValueError("文件不是有效的 PDF 格式；请从原应用导出 PDF 后重试。")
        return content
    except OSError:
        raise ValueError("无法读取所选 PDF，请确认文件存在、可读取且未被其他程序独占。") from None


def _private_directory(path):
    if path.is_symlink():
        raise ValueError("简历存储目录不能是符号链接。")
    path.mkdir(mode=0o700, exist_ok=True)
    if not path.is_dir():
        raise ValueError("简历存储位置不是目录。")
    if os.name == "posix":
        path.chmod(0o700)


def import_resume(root, path, label, scopes, approved):
    """Return a registration record after staging a verified private PDF copy.

    ``approved`` is never coerced: only the boolean True records approval.
    On Windows permissions inherit from the selected private data directory;
    POSIX copies are explicitly restricted to the current account.
    """
    if not isinstance(path, (str, Path)) or not str(path).strip() or "\0" in str(path):
        raise ValueError("请填写本机 PDF 文件的完整路径。")
    try:
        source = Path(path).expanduser()
    except (OSError, RuntimeError, ValueError):
        raise ValueError("无法解析文件路径，请使用本机 PDF 的完整路径。") from None
    if not source.is_absolute():
        raise ValueError("请使用 PDF 文件的绝对路径，例如用户目录下的完整文件路径。")
    if source.suffix.lower() != ".pdf":
        raise ValueError("目前仅支持 PDF 简历；请先导出为 .pdf 文件。")
    if not isinstance(label, str):
        raise ValueError("简历名称必须是文本。")
    label = label.strip() or source.name
    if len(label) > 160 or any(ord(character) < 32 or ord(character) == 127 for character in label):
        raise ValueError("简历名称不能超过 160 个字符或包含控制字符。")
    if not isinstance(scopes, list) or not 1 <= len(scopes) <= 4 or any(
        not isinstance(scope, str) or scope not in SUPPORTED_SCOPES for scope in scopes
    ):
        raise ValueError("简历用途请选择 general、ai、robotics、finance，可多选。")
    scopes = list(dict.fromkeys(scopes))
    content = _read_pdf(source)
    digest = hashlib.sha256(content).hexdigest()
    root = Path(root).expanduser()
    temporary = None
    try:
        if root.is_symlink():
            raise ValueError("私有数据目录不能是符号链接。")
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        directory = root / "resumes"
        _private_directory(directory)
        directory = directory / digest
        _private_directory(directory)
        target = directory / source.name
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError("简历副本位置不是普通文件，请检查私有存储目录。")
        # A later import may point to the already-staged copy. Keep it intact.
        if source.resolve() != target.resolve():
            with tempfile.NamedTemporaryFile(dir=directory, prefix=".resume-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                if os.name == "posix":
                    os.fchmod(stream.fileno(), 0o600)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            # Windows requires the temporary file handle to be closed first.
            os.replace(temporary, target)
    except OSError:
        raise ValueError("无法保存简历副本，请确认私有数据目录可写且磁盘空间充足。") from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # Preserve the actionable write error; the temporary file is
                # restricted to the private directory if cleanup is blocked.
                pass
    return {"label": label, "path": str(target), "sha256": digest, "scope": scopes, "approved": approved is True}
