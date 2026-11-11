"""Uploads and extraction use fictional PDFs and never contact any provider."""

import hashlib
import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from pypdf.actions import JavaScript
from pypdf.annotations import Link

from job_harness.demo import synthetic_pdf
from job_harness.resume_upload import MAX_PAGES, MAX_TEXT_CHARS, _extract_document, stage_upload
from job_harness.resumes import MAX_PDF_BYTES


class ResumeUploadTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "private"
        self.content = synthetic_pdf()

    def upload(self, **changes):
        values = {"root": self.root, "filename": "fictional resume.pdf", "content_bytes": self.content,
                  "label": "虚构 AI 简历", "scopes": ["ai"], "approved": True}
        values.update(changes)
        return stage_upload(**values)

    def pdf_bytes(self, writer):
        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()

    def writer(self):
        writer = PdfWriter()
        writer.clone_document_from_reader(PdfReader(io.BytesIO(self.content)))
        return writer

    def test_actual_upload_stores_original_and_extracts_unconfirmed_text(self):
        record = self.upload()
        digest = hashlib.sha256(self.content).hexdigest()
        target = self.root / "resumes" / digest / "fictional resume.pdf"
        self.assertEqual(record["path"], str(target))
        self.assertEqual(record["sha256"], digest)
        self.assertEqual(target.read_bytes(), self.content)
        self.assertEqual(record["scope"], ["ai"])
        self.assertIs(record["approved"], True)
        self.assertEqual(record["extraction"]["status"], "extracted")
        self.assertIn("Fictional resume", record["extraction"]["text"])
        self.assertEqual(record["extraction"]["pages"], 1)
        self.assertFalse(record["extraction"]["truncated"])
        self.assertNotIn("facts", record)
        self.assertIn("确认", record["extraction"]["detail"])
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertEqual(list(self.root.rglob("*.tmp")), [])

    def test_repeat_upload_uses_the_same_content_identity(self):
        first = self.upload()
        repeated = self.upload(label="虚构应用简历")
        self.assertEqual(first["sha256"], repeated["sha256"])
        self.assertEqual(first["path"], repeated["path"])
        self.assertEqual(len(list((self.root / "resumes").iterdir())), 1)
        self.assertEqual(Path(first["path"]).read_bytes(), self.content)

    def test_rejects_paths_controls_and_reserved_names_before_staging(self):
        for name in ("../outside.pdf", "/outside.pdf", "C:\\fakepath\\resume.pdf", "nested/resume.pdf",
                     "C:resume.pdf", "bad\0.pdf", "bad\n.pdf", "CON.pdf", "aux.extra.pdf",
                     "resume.pdf ", "x" * 241 + ".pdf", "bad\ud800.pdf", "", None):
            with self.subTest(filename=name), self.assertRaises(ValueError):
                self.upload(filename=name)
        self.assertFalse(self.root.exists())

    def test_rejects_wrong_file_type_and_non_pdf_content(self):
        for changes in ({"filename": "resume.docx"}, {"content_bytes": b"not a pdf"},
                        {"content_bytes": b""}, {"content_bytes": "PDF"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.upload(**changes)
        self.assertFalse(self.root.exists())

    def test_rejects_oversized_payload_before_staging(self):
        with self.assertRaisesRegex(ValueError, "20 MB"):
            self.upload(content_bytes=b"%PDF-" + b" " * MAX_PDF_BYTES)
        self.assertFalse(self.root.exists())

    def test_reuses_scope_label_and_strict_approval_validation(self):
        for changes in ({"label": "unsafe\nlabel"}, {"scopes": ["unknown"]}, {"scopes": []}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.upload(**changes)
        self.assertFalse(self.root.exists())
        result = self.upload(approved="true", label="", scopes=["ai", "ai", "general"])
        self.assertFalse(result["approved"])
        self.assertEqual(result["label"], "fictional resume.pdf")
        self.assertEqual(result["scope"], ["ai", "general"])

    def test_real_encrypted_pdf_requires_manual_review(self):
        writer = self.writer()
        writer.encrypt("fictional-password", algorithm="RC4-128")
        result = self.upload(content_bytes=self.pdf_bytes(writer))["extraction"]
        self.assertEqual(result["status"], "needs_manual")
        self.assertEqual(result["text"], "")
        self.assertIn("加密", result["detail"])

    def test_blank_or_image_only_pdf_requires_manual_review(self):
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        result = self.upload(content_bytes=self.pdf_bytes(writer))["extraction"]
        self.assertEqual(result["status"], "needs_manual")
        self.assertEqual(result["text"], "")
        self.assertIn("图片", result["detail"])

    def test_malformed_pdf_cannot_crash_or_leak_parser_fragments(self):
        result = self.upload(content_bytes=b"%PDF-1.4\nfictional-private-fragment\n%%EOF")["extraction"]
        self.assertEqual(result["status"], "needs_manual")
        self.assertEqual(result["text"], "")
        self.assertNotIn("fictional-private-fragment", result["detail"])

    def test_actions_metadata_and_links_are_not_extracted_or_followed(self):
        writer = self.writer()
        writer.add_open_action(JavaScript('app.launchURL("https://fictional-script.example/")'))
        writer.add_metadata({"/Title": "fictional-private-metadata"})
        writer.add_annotation(0, Link(rect=(0, 0, 40, 40), url="https://fictional-link.example/"))
        content = self.pdf_bytes(writer)
        with patch("builtins.open") as file_open, patch("socket.socket") as network:
            result = _extract_document(content)
        file_open.assert_not_called()
        network.assert_not_called()
        self.assertEqual(result["status"], "extracted")
        self.assertNotIn("fictional-script", result["text"])
        self.assertNotIn("fictional-link", result["text"])
        self.assertNotIn("fictional-private-metadata", result["text"])

    def test_parser_deadline_leaves_explicit_manual_result(self):
        with patch("job_harness.resume_upload.subprocess.run", side_effect=subprocess.TimeoutExpired("worker", 8)):
            result = self.upload()
        self.assertEqual(result["extraction"]["status"], "needs_manual")
        self.assertIn("时间限制", result["extraction"]["detail"])
        self.assertEqual(Path(result["path"]).read_bytes(), self.content)

    def test_crashed_or_invalid_worker_output_cannot_be_used_as_text(self):
        outputs = [subprocess.CompletedProcess([], 1, b"private data", b"private error"),
                   subprocess.CompletedProcess([], 0, b"not-json", b""),
                   subprocess.CompletedProcess([], 0, json.dumps({"status": "extracted", "text": "x"}).encode(), b"")]
        for output in outputs:
            with self.subTest(code=output.returncode, stdout=output.stdout):
                with patch("job_harness.resume_upload.subprocess.run", return_value=output):
                    result = self.upload()["extraction"]
                self.assertEqual(result["status"], "needs_manual")
                self.assertEqual(result["text"], "")
                self.assertNotIn("private", result["detail"])

    def test_page_limit_keeps_partial_text_explicitly_unconfirmed(self):
        writer = self.writer()
        template = PdfReader(io.BytesIO(self.content)).pages[0]
        for _ in range(MAX_PAGES):
            writer.add_page(template)
        result = self.upload(content_bytes=self.pdf_bytes(writer))["extraction"]
        self.assertEqual(result["status"], "needs_manual")
        self.assertTrue(result["truncated"])
        self.assertEqual(result["pages"], MAX_PAGES + 1)
        self.assertIn("Fictional resume", result["text"])

    def test_text_limit_is_bounded_and_requires_confirmation(self):
        from pypdf.generic import DecodedStreamObject, NameObject
        writer = self.writer()
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf (" + b"x" * (MAX_TEXT_CHARS + 1) + b") Tj ET")
        writer.pages[0][NameObject("/Contents")] = stream
        result = self.upload(content_bytes=self.pdf_bytes(writer))["extraction"]
        self.assertEqual(result["status"], "needs_manual")
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["text"]), MAX_TEXT_CHARS)


if __name__ == "__main__":
    unittest.main()
