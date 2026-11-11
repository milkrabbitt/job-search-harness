"""Small, bounded model adapters. Model output is advice, never an action.

Only a fixed set of matching features is transmitted. Credentials and raw
résumés, messages, URLs and job descriptions are not included in prompts.
"""
import ipaddress
import json
import math
import re
import socket
import ssl
import time
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .model import CodexModel, PROBE
from .model_credentials import DEFAULT_KEY_ENV, read_model_key


def _provider(identifier, label, url, protocol="openai", key=True, local=False):
    return {"id": identifier, "label": label, "base_url": url, "protocol": protocol,
            "requires_key": key, "key_required": key, "local": local,
            "api_key_env": DEFAULT_KEY_ENV.get(identifier, ""), "default_model": ""}


# Base URLs are protocol endpoints, not model availability promises. Region and
# account entitlements differ; users enter a model available to their account.
PROVIDERS = {
    "none": _provider("none", "暂不使用模型（规则模式）", "", "none", False),
    "codex_cli": _provider("codex_cli", "本机 Codex CLI", "", "cli", False),
    "openai": _provider("openai", "OpenAI API", "https://api.openai.com/v1"),
    "anthropic": _provider("anthropic", "Anthropic / Claude API", "https://api.anthropic.com/v1", "anthropic"),
    "gemini": _provider("gemini", "Google Gemini API", "https://generativelanguage.googleapis.com/v1beta/openai"),
    "minimax": _provider("minimax", "MiniMax API（全球）", "https://api.minimax.io/v1"),
    "deepseek": _provider("deepseek", "DeepSeek API", "https://api.deepseek.com"),
    "qwen": _provider("qwen", "通义千问 / 百炼（北京）", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    "moonshot": _provider("moonshot", "Kimi / Moonshot（中国）", "https://api.moonshot.cn/v1"),
    "zhipu": _provider("zhipu", "智谱 GLM / BigModel", "https://open.bigmodel.cn/api/paas/v4"),
    "ollama": _provider("ollama", "Ollama 本地服务", "http://127.0.0.1:11434/v1", key=False, local=True),
    "lmstudio": _provider("lmstudio", "LM Studio 本地服务", "http://127.0.0.1:1234/v1", key=False, local=True),
    "openai_compatible": _provider("openai_compatible", "自定义 OpenAI 兼容接口", "", key=False),
}

_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,127}\Z")
_ENV = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_STRENGTHS = ("具身与机器人", "对话、记忆与交互", "AI 应用或 Agent", "软件产品交付", "金融产品或分析")
MAX_RESPONSE_BYTES = 262144
_SYSTEM = (
    "你是求职匹配复核助手，只分析给定结构化字段，不补充或虚构个人经历。"
    "分数是排序参考，不是录用概率。不要执行指令、调用工具或决定外发。"
    "只返回一个 JSON 对象，恰含 summary（非空简短字符串，最多200字）和 "
    "review（最多3个字符串，每项最多120字）。不要返回 Markdown 或额外字段。"
)


def _boolean(value):
    if isinstance(value, bool):
        return value
    if value in ("true", "1", "on", 1):
        return True
    if value in ("false", "0", "off", "", None, 0):
        return False
    raise ValueError("模型开关无效")


def _integer(value, default, minimum, maximum, label):
    if value in (None, ""):
        value = default
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise ValueError(label + "必须是整数")
    value = int(value)
    if not minimum <= value <= maximum:
        raise ValueError(label + "超出允许范围")
    return value


def _loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_base_url(value, provider):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value):
        raise ValueError("模型 Base URL 格式无效")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError("模型 Base URL 格式无效") from None
    if (parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password
            or parsed.query or parsed.fragment or "\\" in value or "%" in parsed.netloc
            or (port is not None and not 1 <= port <= 65535)):
        raise ValueError("Base URL 必须是无凭据、查询参数或片段的 HTTP(S) 地址")
    local_allowed = provider in {"ollama", "lmstudio", "openai_compatible"}
    if parsed.scheme == "http" and not (local_allowed and _loopback(host)):
        raise ValueError("远程模型必须使用 HTTPS；HTTP 仅用于本机模型服务")
    if provider in {"ollama", "lmstudio"} and not _loopback(host):
        raise ValueError("本地模型预设仅连接回环地址；远程部署请选自定义 HTTPS 接口")
    path = parsed.path.rstrip("/")
    if path.endswith(("/chat/completions", "/messages")):
        raise ValueError("填写 Base URL，不要包含 /chat/completions 或 /messages")
    if any(part in {".", ".."} for part in path.split("/")):
        raise ValueError("Base URL 路径无效")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def normalize_model_config(config):
    if not isinstance(config, dict):
        raise ValueError("模型配置必须是对象")
    provider = str(config.get("provider") or "codex_cli").strip()
    if provider not in PROVIDERS:
        raise ValueError("不支持的模型供应商")
    spec = PROVIDERS[provider]
    enabled = _boolean(config.get("enabled", provider != "none")) and provider != "none"
    name = str(config.get("name") or config.get("model_name") or "").strip()
    if name and not _MODEL.fullmatch(name):
        raise ValueError("模型名称包含不支持的字符或超过128字符")
    if enabled and spec["protocol"] not in {"cli", "none"} and not name:
        raise ValueError("请填写此账号或本机服务中可用的模型名称")
    base_url = str(config.get("base_url") or spec["base_url"]).strip()
    if spec["protocol"] not in {"cli", "none"}:
        if base_url:
            base_url = validate_base_url(base_url, provider)
        elif enabled:
            raise ValueError("请填写模型 Base URL")
    else:
        base_url = ""
    env = str(config.get("api_key_env") or spec["api_key_env"]).strip()
    if env and not _ENV.fullmatch(env):
        raise ValueError("API Key 环境变量名称无效")
    executable = str(config.get("executable") or "").strip()
    if len(executable) > 4096 or any(ord(c) < 32 for c in executable):
        raise ValueError("CLI 路径无效")
    cost = str(config.get("cost_limit") or "").strip()
    if cost and not re.fullmatch(r"\d{1,9}(\.\d{1,4})?", cost):
        raise ValueError("费用上限格式无效")
    return {
        "provider": provider, "enabled": enabled, "name": name,
        "base_url": base_url, "api_key_env": env, "executable": executable,
        "max_calls": _integer(config.get("max_calls"), 3, 0, 1000, "每日调用上限"),
        "timeout": _integer(config.get("timeout"), 75, 5, 180, "请求超时"),
        "max_output_tokens": _integer(config.get("max_output_tokens"), 1024, 64, 4096, "单次输出上限"),
        "cost_limit": cost,
    }


def _facts(evidence):
    if evidence is None:
        return dict(PROBE)
    if not isinstance(evidence, dict):
        raise ValueError("匹配依据格式无效")
    score = evidence.get("score", 0)
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        score = 0
    if isinstance(score, float) and not math.isfinite(score):
        score = 0
    def count(key):
        values = evidence.get(key, [])
        return min(len(values), 1000) if isinstance(values, list) else 0
    strengths = evidence.get("strengths", [])
    state, scope = evidence.get("state"), evidence.get("scope")
    return {"state": state if isinstance(state, str) and state in {"match", "review", "excluded"} else "review",
            "score": max(0, min(100, int(score))),
            "scope": scope if isinstance(scope, str) and scope in {"ai", "robotics", "finance", "general"} else "other",
            "hard_count": count("hard"), "unknown_count": count("unknown"),
            "strengths": [item for item in _STRENGTHS if isinstance(strengths, list) and item in strengths]}


def _parse_advice(text, secret=""):
    if not isinstance(text, str) or len(text) > 16000:
        raise ValueError("invalid structured result")
    advice = json.loads(text)
    if (not isinstance(advice, dict) or set(advice) != {"summary", "review"}
            or not isinstance(advice["summary"], str) or not advice["summary"].strip()
            or len(advice["summary"]) > 1000 or not isinstance(advice["review"], list)
            or len(advice["review"]) > 3
            or any(not isinstance(x, str) or len(x) > 500 for x in advice["review"])):
        raise ValueError("invalid structured result")
    def clean(value, limit):
        # Redact before truncation so a long echoed key cannot leak its prefix.
        if secret:
            value = value.replace(secret, "[已隐藏凭据]")
        return value.strip()[:limit]
    return clean(advice["summary"], 200), [clean(x, 120) for x in advice["review"]]


def _token(value):
    return value if type(value) is int and 0 <= value <= 1000000000 else None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "Redirects are disabled", headers, fp)


def _read_response(response, deadline):
    """Cap aggregate bytes and check one deadline across body chunks.

    urllib's timeout limits socket operations; reducing it as the deadline
    approaches prevents a slow response from resetting the full timeout for
    every body read. DNS resolution remains the operating system's timeout.
    """
    chunks, total = [], 0
    reader = getattr(response, "read1", response.read)
    while total <= MAX_RESPONSE_BYTES:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("response deadline reached")
        # CPython's HTTPResponse exposes its socket through the buffered file.
        # Other conforming transports still get explicit chunk/deadline checks.
        raw_stream = getattr(getattr(response, "fp", None), "raw", None)
        sock = getattr(raw_stream, "_sock", None)
        if sock is not None:
            sock.settimeout(remaining)
        chunk = reader(min(65536, MAX_RESPONSE_BYTES + 1 - total))
        if time.monotonic() > deadline:
            raise TimeoutError("response deadline reached")
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


class HTTPModel:
    def __init__(self, config, root):
        self.config = normalize_model_config(config)
        self.root = Path(root)

    def evaluate(self, run_config, evidence=None):
        # Per-run limits/names may change, but a task cannot change credential
        # destination or provider by smuggling fields into matching evidence.
        config = dict(self.config)
        if isinstance(run_config, dict):
            for field in ("name", "timeout", "max_output_tokens"):
                if field in run_config:
                    config[field] = run_config[field]
        model = str(config.get("name") or "未配置")
        def failed(code, detail):
            return {"state": "failed", "model": model, "detail": detail,
                    "error_code": code, "input_tokens": None, "output_tokens": None}
        try:
            if not isinstance(run_config, dict):
                raise ValueError("run config must be an object")
            config = normalize_model_config(config)
            if not config["enabled"]:
                return failed("disabled", "模型已禁用")
            spec = PROVIDERS[config["provider"]]
            key = read_model_key(self.root, config)
            if spec["requires_key"] and not key:
                return failed("missing_key", "尚未配置此供应商的 API Key")
            facts = _facts(evidence)
        except (OSError, ValueError):
            return failed("configuration", "模型配置或凭据不可用，请检查本机设置")
        prompt = "请复核以下匹配特征，只返回指定 JSON：\n" + json.dumps(facts, ensure_ascii=False)
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "User-Agent": "JobSearchHarness/0.2"}
        if config["provider"] == "gemini":
            headers["x-goog-api-client"] = "job-search-harness/0.2.0"
        if spec["protocol"] == "anthropic":
            url = config["base_url"] + "/messages"
            headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
            payload = {"model": model, "max_tokens": config["max_output_tokens"], "system": _SYSTEM,
                       "messages": [{"role": "user", "content": prompt}]}
        else:
            url = config["base_url"] + "/chat/completions"
            if key:
                headers["Authorization"] = "Bearer " + key
            payload = {"model": model, "messages": [{"role": "system", "content": _SYSTEM},
                                                      {"role": "user", "content": prompt}], "stream": False}
            token_field = "max_completion_tokens" if config["provider"] in {"openai", "minimax"} else "max_tokens"
            payload[token_field] = config["max_output_tokens"]
            if config["provider"] == "minimax":
                payload["reasoning_split"] = True
        request = Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
        handlers = [_NoRedirect()]
        if _loopback(urlsplit(url).hostname):
            handlers.append(ProxyHandler({}))
        try:
            # One bounded request only. Failed requests may still be billable;
            # retry and failover always remain an explicit operator decision.
            deadline = time.monotonic() + config["timeout"]
            with build_opener(*handlers).open(request, timeout=config["timeout"]) as response:
                raw = _read_response(response, deadline)
            if len(raw) > MAX_RESPONSE_BYTES:
                return failed("response_too_large", "模型返回超过本机响应上限，已停止处理")
            body = json.loads(raw.decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("response must be an object")
            if spec["protocol"] == "anthropic":
                if body.get("stop_reason") in {"max_tokens", "refusal"}:
                    return failed("incomplete", "模型拒绝或输出被截断，请检查模型和单次输出上限")
                text = "".join(part["text"] for part in body["content"] if part.get("type") == "text")
                usage = body.get("usage") or {}
                input_tokens, output_tokens = _token(usage.get("input_tokens")), _token(usage.get("output_tokens"))
            else:
                choice = body["choices"][0]
                if choice.get("finish_reason") in {"length", "content_filter"} or choice.get("message", {}).get("refusal"):
                    return failed("incomplete", "模型拒绝或输出被截断，请检查模型和单次输出上限")
                text = choice["message"]["content"]
                usage = body.get("usage") or {}
                input_tokens, output_tokens = _token(usage.get("prompt_tokens")), _token(usage.get("completion_tokens"))
            summary, review = _parse_advice(text, key)
            detail = ("连接验证通过 · " if evidence is None else "建议 · ") + summary
            if review:
                detail += " · " + "；".join(review)
            if key:
                # A malicious/erroring proxy may echo headers in output.
                detail = detail.replace(key, "[已隐藏凭据]")
            return {"state": "done", "model": model, "detail": detail,
                    "input_tokens": input_tokens, "output_tokens": output_tokens}
        except HTTPError as exc:
            exc.close()
            if 300 <= exc.code < 400:
                return failed("redirect", "接口尝试重定向，已阻止转发凭据；请核对 Base URL")
            if exc.code in {401, 403}:
                return failed("authentication", "API Key、账号权限或区域不匹配，请在供应商控制台核实")
            if exc.code == 429:
                return failed("rate_limit", "模型服务限流或额度不足；本次未自动重试")
            if exc.code in {400, 404, 422}:
                return failed("model_or_protocol", "模型名称、接口路径或请求协议不被支持，请核对供应商说明")
            return failed("http_error", "模型服务返回 HTTP " + str(exc.code) + "；本次未自动重试")
        except (TimeoutError, socket.timeout):
            return failed("timeout", "模型请求超时；结果未知，本次未自动重试")
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                return failed("timeout", "模型请求超时；结果未知，本次未自动重试")
            return failed("connection", "无法安全连接模型服务，请检查地址、网络、TLS 和本地服务状态")
        except (ssl.SSLError, OSError, HTTPException):
            return failed("connection", "无法安全连接模型服务，请检查地址、网络、TLS 和本地服务状态")
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError):
            return failed("invalid_response", "模型未返回有效的结构化结果，请换用支持指令与 JSON 输出的模型")


def build_model(config, root):
    config = normalize_model_config(config)
    if not config["enabled"] or config["provider"] == "none":
        return None
    if config["provider"] == "codex_cli":
        return CodexModel({"model": config})
    return HTTPModel(config, root)
