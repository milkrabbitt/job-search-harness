"""Model credentials kept outside SQLite/config snapshots.

Environment variables take precedence. The optional local store uses private
files, NOT encryption; the OS account and disk encryption remain the boundary.
"""
import os
import re
import stat
import tempfile
from pathlib import Path


_PROVIDER = re.compile(r"[a-z][a-z0-9_]{0,39}\Z")
_ENV = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
DEFAULT_KEY_ENV = {
    "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY", "minimax": "MINIMAX_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY", "qwen": "DASHSCOPE_API_KEY",
    "moonshot": "MOONSHOT_API_KEY", "zhipu": "ZHIPU_API_KEY",
    "ollama": "OLLAMA_API_KEY", "lmstudio": "LM_STUDIO_API_KEY",
    "openai_compatible": "JOB_HARNESS_MODEL_API_KEY", "codex_cli": "",
}


def _key_path(root, provider, create=False):
    if not isinstance(provider, str) or not _PROVIDER.fullmatch(provider):
        raise ValueError("模型供应商标识无效")
    root = Path(root)
    directory = root / "private" / "model-keys"
    for path in (root / "private", directory):
        if path.is_symlink():
            raise ValueError("凭据目录不能是符号链接")
        if create:
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name == "posix":
                path.chmod(0o700)
    path = directory / (provider + ".key")
    if path.is_symlink():
        raise ValueError("凭据文件不能是符号链接")
    return path


def _valid_key(key):
    if not isinstance(key, str):
        raise ValueError("API Key 格式无效")
    key = key.strip()
    if not 1 <= len(key) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise ValueError("API Key 应为不含空白的 ASCII 文本，长度不超过 4096")
    return key


def save_model_key(root, provider, key):
    key = _valid_key(key)
    path = _key_path(root, provider, create=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".key-", delete=False) as stream:
            tmp = Path(stream.name)
            if os.name == "posix":
                os.fchmod(stream.fileno(), 0o600)
            stream.write(key.encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def read_model_key(root, config):
    provider = config.get("provider", "codex_cli")
    name = config.get("api_key_env") or DEFAULT_KEY_ENV.get(provider, "")
    if name:
        if not isinstance(name, str) or not _ENV.fullmatch(name):
            raise ValueError("API Key 环境变量名称无效")
        value = os.environ.get(name, "")
        if value:
            return _valid_key(value)
    path = _key_path(root, provider)
    try:
        mode = path.stat().st_mode
        if not stat.S_ISREG(mode):
            raise ValueError("凭据必须是普通文件")
        if os.name == "posix" and mode & 0o077:
            raise ValueError("凭据文件权限过宽，请在本机设置为仅当前用户可读写")
        with path.open("rb") as stream:
            data = stream.read(4097)
        if len(data) > 4096:
            raise ValueError("API Key 文件过大")
        return _valid_key(data.decode("ascii"))
    except FileNotFoundError:
        return ""
    except UnicodeError:
        raise ValueError("API Key 文件编码无效") from None


def model_key_present(root, config):
    try:
        return bool(read_model_key(root, config))
    except (OSError, ValueError):
        return False


def delete_model_key(root, provider):
    _key_path(root, provider).unlink(missing_ok=True)
