"""Lock contention and Windows byte-range behavior, without external services."""

import errno
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from job_harness import filelock


class FileLockTests(unittest.TestCase):
    def test_real_lock_contends_then_becomes_available(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instance.lock"
            with path.open("a", encoding="utf-8") as first, path.open("a", encoding="utf-8") as second:
                filelock.acquire_lock(first)
                try:
                    with self.assertRaises(BlockingIOError):
                        filelock.acquire_lock(second)
                finally:
                    filelock.release_lock(first)
                filelock.acquire_lock(second)
                filelock.release_lock(second)

    def test_closing_owner_releases_lock_after_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instance.lock"
            with path.open("a", encoding="utf-8") as first:
                filelock.acquire_lock(first)
            with path.open("a", encoding="utf-8") as recovered:
                filelock.acquire_lock(recovered)
                filelock.release_lock(recovered)

    def test_windows_locks_first_byte_and_releases_same_range(self):
        for mode in ("a", "ab"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "instance.lock"
                calls = []

                def locking(fd, operation, length):
                    calls.append((operation, length, os.lseek(fd, 0, os.SEEK_CUR)))

                fake = SimpleNamespace(locking=locking, LK_NBLCK=2, LK_UNLCK=0)
                with patch.object(filelock, "_IS_WINDOWS", True), patch.dict(sys.modules, {"msvcrt": fake}):
                    with path.open(mode) as handle:
                        filelock.acquire_lock(handle)
                        handle.seek(1)
                        filelock.release_lock(handle)
                    self.assertEqual(calls, [(2, 1, 0), (0, 1, 0)])
                    self.assertEqual(path.read_bytes(), b"\0")

    def test_windows_existing_lock_file_is_not_truncated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instance.lock"
            path.write_bytes(b"existing-lock")
            fake = SimpleNamespace(locking=lambda *args: None, LK_NBLCK=2, LK_UNLCK=0)
            with patch.object(filelock, "_IS_WINDOWS", True), patch.dict(sys.modules, {"msvcrt": fake}):
                with path.open("ab") as handle:
                    filelock.acquire_lock(handle)
                    filelock.release_lock(handle)
            self.assertEqual(path.read_bytes(), b"existing-lock")

    def test_windows_busy_lock_is_normalized(self):
        for code in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            with self.subTest(code=code), tempfile.TemporaryFile(mode="a+b") as handle:
                def busy(*args):
                    raise OSError(code, "locked")

                fake = SimpleNamespace(locking=busy, LK_NBLCK=2, LK_UNLCK=0)
                with patch.object(filelock, "_IS_WINDOWS", True), patch.dict(sys.modules, {"msvcrt": fake}):
                    with self.assertRaises(BlockingIOError):
                        filelock.acquire_lock(handle)

    def test_windows_unrelated_lock_errors_are_not_hidden(self):
        with tempfile.TemporaryFile(mode="a+b") as handle:
            def invalid(*args):
                raise OSError(errno.EBADF, "bad descriptor")

            fake = SimpleNamespace(locking=invalid, LK_NBLCK=2, LK_UNLCK=0)
            with patch.object(filelock, "_IS_WINDOWS", True), patch.dict(sys.modules, {"msvcrt": fake}):
                with self.assertRaises(OSError) as error:
                    filelock.acquire_lock(handle)
                self.assertNotIsInstance(error.exception, BlockingIOError)
                self.assertEqual(error.exception.errno, errno.EBADF)


if __name__ == "__main__":
    unittest.main()
