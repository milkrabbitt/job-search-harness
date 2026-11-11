"""SQLite handles close deterministically, including initialization/backup errors."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_harness.storage import Store


SQLITE_CONNECT = sqlite3.connect


class TrackingConnection(sqlite3.Connection):
    closed = False
    fail_integrity = False

    def close(self):
        self.closed = True
        return super().close()

    def execute(self, statement, *args, **kwargs):
        if self.fail_integrity and statement == "PRAGMA integrity_check":
            raise sqlite3.DatabaseError("simulated backup verification failure")
        return super().execute(statement, *args, **kwargs)


class StorageLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.connections = []
        self.fail_integrity = False

        def tracked_connect(*args, **kwargs):
            connection = SQLITE_CONNECT(*args, factory=TrackingConnection, **kwargs)
            connection.fail_integrity = self.fail_integrity
            self.connections.append(connection)
            return connection

        self.patch = patch("job_harness.storage.sqlite3.connect", side_effect=tracked_connect)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        # Close even on a failing assertion so Windows can remove the test folder.
        self.addCleanup(lambda: [connection.close() for connection in self.connections])

    def assertConnectionsClosed(self):
        self.assertTrue(self.connections)
        for connection in self.connections:
            self.assertTrue(connection.closed)
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_initialization_closes_connection(self):
        Store(self.root)
        self.assertConnectionsClosed()

    def test_initialization_failure_closes_connection(self):
        with patch("job_harness.storage.SCHEMA", "not valid SQL"):
            with self.assertRaises(sqlite3.OperationalError):
                Store(self.root)
        self.assertConnectionsClosed()

    def test_backup_closes_both_handles_and_preserves_data(self):
        store = Store(self.root)
        with store.tx(True) as connection:
            connection.execute("INSERT INTO settings VALUES ('example', 'saved')")
        backup = store.backup()
        self.assertConnectionsClosed()
        # Opening independently verifies that the on-disk backup is complete.
        connection = SQLITE_CONNECT(str(backup))
        try:
            self.assertEqual(connection.execute("SELECT value FROM settings WHERE key='example'").fetchone()[0], "saved")
        finally:
            connection.close()

    def test_backup_failure_closes_both_handles(self):
        store = Store(self.root)
        self.fail_integrity = True
        with self.assertRaises(sqlite3.DatabaseError):
            store.backup()
        self.assertConnectionsClosed()


if __name__ == "__main__":
    unittest.main()
