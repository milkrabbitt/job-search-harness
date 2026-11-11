"""Stage explicitly uploaded PDFs and extract unconfirmed text in a bounded worker.

The original bytes remain unchanged. Only page text is returned; PDF actions,
attachments, annotations, metadata and external references are never followed.
Extraction is evidence for a user to review, not verified profile information.
"""

import io
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .resumes import MAX_PDF_BYTES, import_resume


MAX_PAGES = 20
MAX_TEXT_CHARS = 120_000
MAX_STREAM_BYTES = 4_000_000
EXTRACTION_TIMEOUT = 8
_RESERVED_NAMES = {"con", "prn", "aux", "nul", *("com" + str(i) for i in range(1, 10)),
                   *("lpt" + str(i) for i in range(1, 10))}


def _filename(value):
    if (not isinstance(value, str) or not value or len(value.encode("utf-8", errors="replace")) > 240
            or value != value.strip()
            or any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF for char in value)
            or any(char in '<>:"/\\|?*' for char in value)
            or value.split(".", 1)[0].casefold() in _RESERVED_NAMES):
        raise ValueError("请上传文件名不含路径或保留字符的 PDF，文件名不能超过 240 字节。")
    if Path(value).suffix.casefold() != ".pdf":
        raise ValueError("目前仅支持 .pdf 简历，请先导出为 PDF。")
    return value


def _manual(detail, *, text="", pages=0, truncated=False):
    return {"status": "needs_manual", "text": text, "detail": detail,
            "pages": pages, "truncated": truncated}


def extract_text(content):
    """Run a text-only parser with a deadline; never expose parser diagnostics."""
    package_root = Path(__file__).resolve().parent.parent
    environment = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "LANG", "LC_ALL")
                   if key in os.environ}
    environment["PYTHONPATH"] = str(package_root)
    environment["PYTHONNOUSERSITE"] = "1"
    try:
        result = subprocess.run(
            [sys.executable, "-B", "-m", "job_harness.resume_upload", "--extract"],
            input=content, capture_output=True, timeout=EXTRACTION_TIMEOUT,
            cwd=package_root, env=environment, check=False,
        )
    except subprocess.TimeoutExpired:
        return _manual("文字提取超过时间限制，请手动核对或上传更精简的 PDF。")
    except OSError:
        return _manual("无法启动文字提取程序，请检查 Python 环境。")
    if result.returncode or len(result.stdout) > MAX_TEXT_CHARS * 6 + 10_000:
        return _manual("文字提取未完成，请手动核对 PDF 内容。")
    try:
        parsed = json.loads(result.stdout)
        if (not isinstance(parsed, dict) or parsed.get("status") not in {"extracted", "needs_manual"}
                or not isinstance(parsed.get("text"), str) or len(parsed["text"]) > MAX_TEXT_CHARS
                or not isinstance(parsed.get("detail"), str) or len(parsed["detail"]) > 300
                or type(parsed.get("pages")) is not int or not 0 <= parsed["pages"] <= 128
                or type(parsed.get("truncated")) is not bool):
            raise ValueError
        return {key: parsed[key] for key in ("status", "text", "detail", "pages", "truncated")}
    except (UnicodeError, ValueError, TypeError, KeyError):
        return _manual("文字提取结果无法校验，请手动核对 PDF 内容。")


def stage_upload(root, filename, content_bytes, label, scopes, approved):
    """Return an import_resume-compatible record plus unconfirmed extraction.

    Invalid upload metadata, size and type fail before creating a private copy.
    A PDF that cannot be read still gets an explicit manual-review extraction
    result. Approval authorizes this attachment only, not any extracted claims.
    """
    filename = _filename(filename)
    if not isinstance(content_bytes, bytes):
        raise ValueError("上传内容必须是 PDF 文件数据。")
    if len(content_bytes) > MAX_PDF_BYTES:
        raise ValueError("PDF 不能超过 20 MB。")
    if not content_bytes.startswith(b"%PDF-"):
        raise ValueError("文件不是有效的 PDF 格式，请重新导出后上传。")
    with tempfile.TemporaryDirectory(prefix="job-harness-upload-") as temporary:
        source = Path(temporary) / filename
        with source.open("xb") as stream:
            if os.name == "posix":
                os.fchmod(stream.fileno(), 0o600)
            stream.write(content_bytes)
        record = import_resume(root, source, label, scopes, approved)
    record["extraction"] = extract_text(content_bytes)
    return record


def _resource_limits():
    try:
        import resource
    except ImportError:
        return
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (6, 6))
        if sys.platform.startswith("linux"):
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    except (OSError, ValueError):
        # The parent deadline and parser limits also apply on restricted hosts.
        pass


def _extract_document(content):
    try:
        from pypdf import PdfReader, overwrite_configuration
    except ImportError:
        return _manual("缺少 PDF 提取依赖，请在运行环境安装 requirements.txt 后重试。")
    overwrite_configuration(
        maximum_declared_stream_length=MAX_STREAM_BYTES,
        array_based_stream_maximum_output_length=MAX_STREAM_BYTES,
        zlib_maximum_output_length=MAX_STREAM_BYTES,
        lzw_maximum_output_length=MAX_STREAM_BYTES,
        run_length_maximum_output_length=MAX_STREAM_BYTES,
        brotli_maximum_output_length=MAX_STREAM_BYTES,
        stream_decoding_work_maximum_length=MAX_STREAM_BYTES * 4,
        stream_parsing_work_maximum_count=250_000,
        page_tree_maximum_entries=512,
        page_tree_maximum_page_entries=128,
        page_tree_maximum_depth=25,
        xform_maximum_invocations_per_extraction=200,
    )
    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted:
            return _manual("PDF 已加密，请上传未加密版本或手动填写资料。")
        pages = len(reader.pages)
        if not pages:
            return _manual("PDF 没有可读取的页面，请手动核对。")
        parts = []
        remaining = MAX_TEXT_CHARS
        truncated = pages > MAX_PAGES
        for page in reader.pages[:MAX_PAGES]:
            contents = page.get_contents()
            if contents is not None and len(contents.get_data()) > MAX_STREAM_BYTES:
                return _manual("PDF 页面内容超过提取限制，请手动核对。", pages=pages)
            text = page.extract_text() or ""
            text = "".join(char for char in text if char in "\n\r\t" or (ord(char) >= 32 and ord(char) != 127))
            separator = "\n\n" if parts else ""
            fragment = separator + text
            parts.append(fragment[:remaining])
            if len(fragment) > remaining:
                truncated = True
            remaining -= min(len(fragment), remaining)
            if remaining == 0:
                truncated = True
                break
        text = "".join(parts).strip()
        if not text:
            return _manual("未提取到文字，可能是扫描件或图片 PDF，请手动填写或上传带文字层的版本。", pages=pages)
        if truncated:
            return _manual("PDF 超过页数或文字量限制，仅提取部分文字，请逐项核对。",
                           text=text, pages=pages, truncated=True)
        return {"status": "extracted", "text": text, "detail": "已提取 PDF 文字，保存为个人资料前请逐项确认。",
                "pages": pages, "truncated": False}
    except Exception:
        # Untrusted PDF structures must not crash the HTTP handler or leak text
        # through exception strings, which may contain document fragments.
        return _manual("无法可靠解析 PDF 文字，请重新导出 PDF 或手动填写资料。")


def _worker():
    logging.disable(logging.CRITICAL)
    _resource_limits()
    content = sys.stdin.buffer.read(MAX_PDF_BYTES + 1)
    if len(content) > MAX_PDF_BYTES or not content.startswith(b"%PDF-"):
        result = _manual("PDF 文件类型或大小不符合要求。")
    else:
        result = _extract_document(content)
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    if sys.argv[1:] != ["--extract"]:
        raise SystemExit("This worker is used by the local upload handler.")
    _worker()
