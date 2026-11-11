"""First-run readiness, credential-safe model settings and deliberate startup.

Only the explicit connectivity test makes a synthetic model call. Saving
configuration never invokes a provider or grants permission to send messages.
"""
import hashlib
import os

from .service import fingerprint, setting, set_setting
from .storage import fetch, stamp


MODEL_FIELDS = ("provider", "enabled", "name", "base_url", "api_key_env", "executable",
                "max_calls", "timeout", "max_output_tokens", "cost_limit")


def truth(value):
    return str(value).lower() in {"true", "1", "on", "yes"}


def is_demo(service, c):
    return service.config.get("demo") is True or setting(c, "demo_fixture", False)


def required(c):
    return bool(setting(c, "onboarding_required", False) and not setting(c, "onboarding_complete", False)
                and not setting(c, "demo_fixture", False))


def effective_model_config(service, c):
    """SQLite settings override startup config, but secrets never join this object."""
    base = service.config.get("model", {})
    saved = setting(c, "model_config", {})
    if not isinstance(base, dict) or not isinstance(saved, dict):
        raise ValueError("模型配置必须是 JSON 对象，请重新保存模型设置")
    merged = {"provider": "codex_cli", "enabled": service.model is not None,
              "name": "", "max_calls": 3, "timeout": 75, "max_output_tokens": 1024,
              "cost_limit": "", **base, **saved}
    from .model_backends import normalize_model_config
    return normalize_model_config({key: merged[key] for key in MODEL_FIELDS if key in merged})


def config_hash(service, config):
    from .model_credentials import read_model_key
    try:
        key = read_model_key(service.store.root, config)
    except (ValueError, OSError):
        key = "credential-unavailable"
    # A key or environment change invalidates an old test without persisting the secret.
    return fingerprint(config, hashlib.sha256(key.encode()).hexdigest())


def safe_model_text(service, config, value, limit=3000):
    from .model_credentials import read_model_key
    text = str(value or "")
    try:
        key = read_model_key(service.store.root, config)
        if key:
            text = text.replace(key, "[凭据已隐藏]")
    except (OSError, ValueError):
        pass
    return text[:limit]


def snapshot(service, c):
    from .model_backends import PROVIDERS
    from .model_credentials import model_key_present
    prefs = service.preferences(c)
    profile_ready = bool((prefs.get("cities", "").strip() or prefs.get("allow_remote"))
                         and (prefs.get("directions", "").strip() or prefs.get("target_scope", "").strip()))
    config_error = ""
    try:
        cfg = effective_model_config(service, c)
        digest = config_hash(service, cfg)
    except ValueError as exc:
        cfg, digest, config_error = {"provider": "codex_cli", "enabled": False, "name": ""}, "", str(exc)
    tested = setting(c, "model_test", {})
    if not isinstance(tested, dict):
        tested = {}
    test = {"state": "unverified", "detail": "保存设置后，请用虚构样例测试连接", "updated_at": None, **tested}
    if tested.get("config_hash") != digest:
        test = {"state": "unverified", "detail": "当前设置或凭据尚未通过连接测试", "updated_at": None}
    no_model = cfg.get("provider") == "none" and setting(c, "onboarding_no_model", False)
    if no_model:
        test = {"state": "done", "detail": "已明确选择规则筛选模式；不会调用模型", "updated_at": tested.get("updated_at")}
    model_ready = bool(no_model or (cfg.get("enabled") and test["state"] == "done" and not config_error))
    sources = fetch(c, "SELECT id,url FROM sources WHERE enabled=1 AND channel='official' ORDER BY id")
    connected = [row["name"] for row in fetch(c, "SELECT name,status,paused FROM channels")
                 if row["name"] == "liepin" and row["status"] in {"ready", "read_only"}
                 and not row["paused"] and service.adapters.get(row["name"])
                 and getattr(service.adapters[row["name"]], "enabled", True)
                 and service.config.get("channels", {}).get(row["name"], {}).get("enabled", True)]
    source_ready = bool(sources or connected)
    missing = []
    if not profile_ready:
        missing.append("填写求职方向，以及目标城市或允许远程")
    if not model_ready:
        missing.append(config_error or "保存并测试自己的模型，或明确选择暂不使用模型")
    if not source_ready:
        missing.append("添加至少一个官方招聘来源，或连接支持搜索的渠道")
    demo = is_demo(service, c)
    state = {"required": bool(setting(c, "onboarding_required", False) and not demo),
             "complete": bool(setting(c, "onboarding_complete", False) or demo),
             "profile_ready": profile_ready, "model_ready": model_ready, "source_ready": source_ready,
             "ready": not missing, "missing": missing, "requirements_missing": missing,
             "model_test": test, "model_config_hash": digest,
             "model_test_config_hash": tested.get("config_hash", ""),
             "no_model": no_model, "source_count": len(sources), "connected_channels": connected}
    model = {key: cfg.get(key, "") for key in MODEL_FIELDS}
    model.update({"key_configured": model_key_present(service.store.root, cfg),
                  "key_source": "environment" if cfg.get("api_key_env") and os.environ.get(cfg["api_key_env"])
                  else "private_file" if model_key_present(service.store.root, cfg) else "none",
                  "providers": [dict(item, id=key) for key, item in PROVIDERS.items()],
                  "configured_name": cfg.get("name", ""), "status": test["state"],
                  "detail": test["detail"], "adapter": PROVIDERS.get(cfg.get("provider"), {}).get("label", "模型"),
                  "cost": "未知；调用次数与输出长度为硬限制，金额预算会暂停调用"})
    return state, model


def invalidate(c, detail="模型配置已变更，请重新测试连接"):
    set_setting(c, "model_test", {"state": "unverified", "detail": detail, "updated_at": stamp()})


def _pause_for_setup(c):
    set_setting(c, "onboarding_required", True)
    set_setting(c, "onboarding_complete", False)
    set_setting(c, "stopped", True)
    set_setting(c, "outbound_paused", True)
    prefs = setting(c, "preferences", {})
    prefs["auto_send"] = False
    set_setting(c, "preferences", prefs)


def _save_model(service, data):
    from .model_backends import normalize_model_config
    from .model_credentials import save_model_key, delete_model_key
    with service.store.tx() as c:
        try:
            previous = effective_model_config(service, c)
        except ValueError:
            # Let the setup page repair a malformed manually edited stored model configuration.
            previous = normalize_model_config({"provider": "codex_cli", "enabled": False})
    provider = str(data.get("provider", previous.get("provider", "codex_cli"))).strip()
    # A provider switch uses its own default endpoint/environment, never those of the old provider.
    proposed = dict(previous) if provider == previous.get("provider") else {"provider": provider}
    proposed.update({key: data[key] for key in MODEL_FIELDS if key in data})
    proposed["provider"] = provider
    proposed["enabled"] = truth(data.get("enabled", True)) and provider != "none"
    if "model_name" in data:
        proposed["name"] = data["model_name"]
    for key in ("max_calls", "timeout", "max_output_tokens"):
        if key in proposed and str(proposed[key]).strip() == "":
            proposed.pop(key)
    config = normalize_model_config(proposed)
    key = str(data.get("api_key") or "").strip()
    if provider == "none" and key:
        raise ValueError("无模型模式不保存 API Key")
    # Validate all non-secret settings before touching the private credential file.
    if key:
        save_model_key(service.store.root, provider, key)
    elif truth(data.get("delete_api_key", False)):
        delete_model_key(service.store.root, provider)
    with service.store.tx(True) as c:
        set_setting(c, "model_config", config)
        set_setting(c, "onboarding_no_model", provider == "none")
        invalidate(c)
        _pause_for_setup(c)
        service.event(c, "首次配置 · 已保存模型设置；尚未调用供应商")
    return "模型设置已保存。请测试连接；保存本身不会调用模型。"


def handle_action(service, name, data):
    """Called before the service action transaction: model/network work must not hold it."""
    with service.store.tx() as c:
        if is_demo(service, c):
            raise ValueError("演示数据与真实配置隔离。请在新的私有目录运行 setup。")
    if name == "setup_profile":
        profile = {key: data[key] for key in ("cities", "directions", "target_scope", "salary_floor", "scan_times", "reply_minutes") if key in data}
        profile["allow_remote"] = truth(data.get("allow_remote", False))
        profile["auto_send"] = False
        if not (str(profile.get("cities", "")).strip() or profile["allow_remote"]):
            raise ValueError("请填写目标城市，或明确允许远程岗位")
        if not (str(profile.get("directions", "")).strip() or str(profile.get("target_scope", "")).strip()):
            raise ValueError("请填写岗位方向或目标范围")
        summary = str(data.get("summary") or "").strip()
        if len(summary) > 3000:
            raise ValueError("本人确认的经历摘要请控制在 3000 字以内")
        preset = str(data.get("matching_preset") or "general")
        if preset not in {"general", "legacy_ai_application"}:
            raise ValueError("请选择通用筛选或既有 AI 应用筛选规则")
        service.action("preferences", profile)
        with service.store.tx(True) as c:
            preferences = service.preferences(c)
            preferences["matching_preset"] = preset
            set_setting(c, "preferences", preferences)
            service.refresh_matches(c)
            if summary:
                c.execute("""INSERT INTO facts(key,value,source,updated_at,state) VALUES(?,?,?,?,?)
                    ON CONFLICT(key,source) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at,state=excluded.state""",
                    ("summary_general" if preset == "general" else "summary_app", summary,
                     "首次配置：本人确认", stamp(), "confirmed"))
            _pause_for_setup(c)
            service.event(c, "首次配置 · 已保存本人填写的求职方向")
        notice = "求职方向已保存。经历摘要只采用你本人填写的内容。"
    elif name == "setup_model":
        notice = _save_model(service, data)
    elif name == "setup_no_model":
        _save_model(service, {"provider": "none", "enabled": False})
        notice = "已明确选择规则筛选模式；不会调用任何大模型。"
    elif name == "setup_test_model":
        state, detail = service.invoke_model_probe(setup=True)
        notice = ("连接测试通过 · " if state == "done" else "连接测试未通过 · ") + detail
    elif name == "setup_source":
        source = {key: str(data[key]).strip() for key in ("url", "company", "location", "recipient", "recipient_source") if key in data}
        if not source.get("url"):
            raise ValueError("请填写官方招聘页的 HTTPS 链接")
        source["channel"] = "official"
        with service.store.tx(True) as c:
            sid = service.add_source(c, source)
            c.execute("UPDATE sources SET enabled=1 WHERE id=?", (sid,))
            active = (setting(c, "onboarding_complete", False) and not setting(c, "stopped", False)
                      and not required(c))
            if active:
                service.enqueue(c, "scan", "official", "onboarding:source:" + str(sid) + ":" + fingerprint(source),
                                payload={"source_id": sid})
            service.event(c, "首次配置 · 已登记官方招聘来源，" + ("已安排读取核验" if active else "启动时将读取核验"))
        notice = ("来源已添加，已安排读取并核验可解析的招聘内容。" if active
                  else "来源已添加；启动时将读取页面，确认可解析的招聘内容。")
    elif name == "setup_resume":
        from .resumes import import_resume
        if not truth(data.get("approved", False)):
            raise ValueError("请先确认这是你本人允许用于求职的简历；导入不会授权发送")
        scopes = data.get("scopes", data.get("scope", "general"))
        if isinstance(scopes, str):
            scopes = [part.strip() for part in scopes.replace("，", ",").split(",") if part.strip()]
        record = import_resume(service.store.root, str(data.get("path") or ""),
                               str(data.get("label") or ""), scopes, approved=True)
        with service.store.tx(True) as c:
            c.execute("""INSERT INTO resumes(label,path,sha256,modified_at,scope,approved) VALUES(?,?,?,?,?,?)
                ON CONFLICT(sha256) DO UPDATE SET label=excluded.label,path=excluded.path,
                modified_at=excluded.modified_at,scope=excluded.scope,approved=excluded.approved""",
                (record["label"], record["path"], record["sha256"], record.get("modified_at") or stamp(),
                 ",".join(record["scope"]), bool(record["approved"])))
            service.event(c, "首次配置 · 已校验并登记本人确认的 PDF 私有副本；未授权发送")
        notice = "简历已校验并复制到私有资料目录；发送仍需单独授权。"
    elif name == "setup_start":
        with service.store.tx(True) as c:
            state, model = snapshot(service, c)
            if state["missing"]:
                raise ValueError("尚不能启动：" + "；".join(state["missing"]))
            if state["complete"] and not setting(c, "stopped", False):
                return "后台已启动；现有任务继续运行。"
            prefs = service.preferences(c)
            prefs["auto_send"] = False
            set_setting(c, "preferences", prefs)
            set_setting(c, "outbound_paused", True)
            set_setting(c, "onboarding_required", True)
            set_setting(c, "onboarding_complete", True)
            set_setting(c, "onboarding_completed_at", stamp())
            set_setting(c, "stopped", False)
            sources = fetch(c, "SELECT id,url FROM sources WHERE enabled=1 AND channel='official' ORDER BY id")
            generation = fingerprint(prefs, state["model_config_hash"], sources)
            c.execute("UPDATE channels SET paused=0 WHERE name='official'")
            for source in sources:
                service.enqueue(c, "scan", "official", "onboarding:start:" + generation + ":" + str(source["id"]),
                                payload={"source_id": source["id"]})
            for channel in state["connected_channels"]:
                service.enqueue(c, "platform_search", channel, "onboarding:start:" + generation + ":" + channel)
            if service.email_enabled():
                service.enqueue(c, "probe", "email", "onboarding:email:" + generation)
            service.event(c, "首次配置完成 · 已启动来源扫描和筛选；申请与邮件外发保持暂停")
        notice = "已启动来源扫描与筛选。模型按每日限额辅助复核；申请和邮件外发仍需单独授权。"
    else:
        raise ValueError("此配置操作不存在")
    service.wake.set()
    return notice
