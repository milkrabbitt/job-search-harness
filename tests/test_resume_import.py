"""Private PDF import preserves sources and fails closed on invalid inputs."""

import hashlib
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_harness.resumes import MAX_PDF_BYTES, import_resume


class ResumeImportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.root = self.folder / "private"
        self.source = self.folder / "candidate resume.PDF"
        self.content = b"%PDF-1.4\nfictional test document\n%%EOF\n"
        self.source.write_bytes(self.content)

    def import_pdf(self, **changes):
        fields = dict(root=self.root, path=str(self.source), label="My resume", scopes=["general"], approved=True)
        fields.update(changes)
        return import_resume(**fields)

    def test_private_copy_and_hash_preserve_original(self):
        before = self.source.stat()
        result = self.import_pdf(scopes=["ai", "general", "ai"])
        digest = hashlib.sha256(self.content).hexdigest()
        self.assertEqual(result, {
            "label": "My resume", "path": str(self.root / "resumes" / digest / self.source.name),
            "sha256": digest, "scope": ["ai", "general"], "approved": True,
        })
        self.assertEqual(Path(result["path"]).read_bytes(), self.content)
        self.assertEqual(self.source.read_bytes(), self.content)
        self.assertEqual(self.source.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(stat.S_IMODE(self.source.stat().st_mode), stat.S_IMODE(before.st_mode))
        self.assertEqual(list(self.root.rglob(".resume-*")), [])

    def test_approval_requires_boolean_true(self):
        for approval in (False, None, "true", "false", 1, [], {}):
            with self.subTest(approval=approval):
                self.assertIs(self.import_pdf(approved=approval)["approved"], False)

    def test_empty_label_defaults_to_filename(self):
        self.assertEqual(self.import_pdf(label="  ")["label"], self.source.name)

    def test_label_validation(self):
        for label in (None, 5, "x" * 161, "resume\nlabel", "bad\x7flabel"):
            with self.subTest(label=label), self.assertRaises(ValueError):
                self.import_pdf(label=label)
        self.assertFalse(self.root.exists())

    def test_scope_validation(self):
        for scopes in (None, "general", [], ["unknown"], [None], ["general"] * 5):
            with self.subTest(scopes=scopes), self.assertRaises(ValueError):
                self.import_pdf(scopes=scopes)
        self.assertFalse(self.root.exists())

    def test_accepts_all_supported_scopes(self):
        scopes = ["general", "ai", "robotics", "finance"]
        self.assertEqual(self.import_pdf(scopes=scopes)["scope"], scopes)

    def test_invalid_paths_fail_without_staging(self):
        for path in ("", "   ", None, 5, "relative.pdf", "bad\0.pdf", str(self.folder / "missing.pdf")):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.import_pdf(path=path)
        self.assertFalse(self.root.exists())

    def test_other_file_types_and_pdf_spoofs_rejected(self):
        other = self.folder / "resume.docx"
        other.write_bytes(self.content)
        with self.assertRaisesRegex(ValueError, "PDF"):
            self.import_pdf(path=other)
        self.source.write_bytes(b"not a PDF document")
        with self.assertRaisesRegex(ValueError, "PDF"):
            self.import_pdf()
        self.assertFalse(self.root.exists())

    def test_unresolvable_home_path_has_actionable_error(self):
        with patch("job_harness.resumes.Path.expanduser", side_effect=RuntimeError("unknown home")):
            with self.assertRaisesRegex(ValueError, "路径"):
                self.import_pdf(path="~/resume.pdf")

    def test_empty_and_oversized_files_rejected(self):
        self.source.write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "PDF"):
            self.import_pdf()
        with self.source.open("wb") as source:
            source.write(b"%PDF-")
            source.truncate(MAX_PDF_BYTES + 1)
        with self.assertRaisesRegex(ValueError, "20 MB"):
            self.import_pdf()
        self.assertFalse(self.root.exists())

    def test_directory_source_rejected(self):
        directory = self.folder / "folder.pdf"
        directory.mkdir()
        with self.assertRaises(ValueError):
            self.import_pdf(path=directory)

    def make_symlink(self, destination, target, directory=False):
        try:
            destination.symlink_to(target, target_is_directory=directory)
        except (NotImplementedError, OSError):
            self.skipTest("This environment does not permit creating symbolic links")

    def test_source_symlink_rejected(self):
        link = self.folder / "linked.pdf"
        self.make_symlink(link, self.source)
        with self.assertRaisesRegex(ValueError, "符号链接"):
            self.import_pdf(path=link)
        self.assertFalse(self.root.exists())

    def test_destination_symlink_rejected_without_touching_target(self):
        result = self.import_pdf()
        target = Path(result["path"])
        target.unlink()
        other = self.folder / "unrelated.pdf"
        other.write_bytes(b"unrelated")
        self.make_symlink(target, other)
        with self.assertRaises(ValueError):
            self.import_pdf()
        self.assertEqual(other.read_bytes(), b"unrelated")

    def test_storage_directory_symlink_rejected(self):
        self.root.mkdir()
        outside = self.folder / "outside"
        outside.mkdir()
        self.make_symlink(self.root / "resumes", outside, directory=True)
        with self.assertRaisesRegex(ValueError, "符号链接"):
            self.import_pdf()
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits")
    def test_copy_and_directories_have_private_permissions(self):
        result = self.import_pdf()
        target = Path(result["path"])
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(target.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(target.parent.parent.stat().st_mode), 0o700)

    def test_replace_failure_keeps_source_and_existing_copy(self):
        result = self.import_pdf()
        target = Path(result["path"])
        with patch("job_harness.resumes.os.replace", side_effect=PermissionError("locked")):
            with self.assertRaisesRegex(ValueError, "无法保存"):
                self.import_pdf()
        self.assertEqual(self.source.read_bytes(), self.content)
        self.assertEqual(target.read_bytes(), self.content)
        self.assertEqual(list(self.root.rglob(".resume-*")), [])

    def test_reimporting_staged_copy_does_not_modify_it(self):
        result = self.import_pdf()
        target = Path(result["path"])
        before = target.stat()
        repeated = self.import_pdf(path=target)
        self.assertEqual(repeated["sha256"], result["sha256"])
        self.assertEqual(target.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), stat.S_IMODE(before.st_mode))


if __name__ == "__main__":
    unittest.main()
