"""Bounded multipart reader. Form data is inert and cannot name server files."""
from email import policy
from email.parser import BytesParser

MAX_REQUEST_BYTES = 20_040_000
TEXT_FIELDS = {"token", "action", "next", "label", "scope", "approved"}


def parse_multipart(content_type, raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_REQUEST_BYTES:
        raise ValueError("上传超过 20 MB 限制。")
    if "\r" in content_type or "\n" in content_type:
        raise ValueError("上传格式无效。")
    from email.message import Message
    header = Message()
    header["Content-Type"] = content_type
    boundary = header.get_boundary()
    if not boundary or len(boundary)>120 or raw.count(("--"+boundary).encode("ascii",errors="replace"))>12:
        raise ValueError("上传字段过多或边界无效。")
    try:
        document = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: " + content_type.encode("ascii") + b"\r\nMIME-Version: 1.0\r\n\r\n" + raw)
    except (ValueError, UnicodeError):
        raise ValueError("上传格式无效。") from None
    if document.defects or not document.is_multipart() or document.get_content_subtype() != "form-data":
        raise ValueError("上传边界不完整，请重新选择文件。")
    fields, uploaded = {}, None
    for index, part in enumerate(document.iter_parts()):
        if index >= 8 or part.defects or part.is_multipart():
            raise ValueError("上传字段过多或格式无效。")
        name = part.get_param("name", header="Content-Disposition")
        if part.get_content_disposition() != "form-data" or name not in TEXT_FIELDS | {"resume"}:
            raise ValueError("上传包含无法识别的字段。")
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            raise ValueError("上传字段无法读取。")
        if name == "resume":
            if uploaded is not None or part.get_content_type() not in {"application/pdf", "application/octet-stream"}:
                raise ValueError("每次只能上传一份 PDF。")
            uploaded = (part.get_filename() or "", payload)
        else:
            if name in fields or part.get_filename() is not None or len(payload) > 4000:
                raise ValueError("上传字段重复或超过长度限制。")
            try:
                fields[name] = payload.decode("utf-8")
            except UnicodeError:
                raise ValueError("文字字段必须使用 UTF-8。") from None
    if uploaded is None or fields.get("action") != "resume_upload":
        raise ValueError("请选择一份 PDF 简历。")
    return fields, uploaded
