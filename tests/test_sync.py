import tempfile
import sqlite3
import unittest
from pathlib import Path
from crm_sync import connect, stage, deliver_one
from mock_crm import MockCRM


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = connect(str(Path(self.temp.name)/"local.db"))
        self.addCleanup(self.db.close)
        self.remote_path = str(Path(self.temp.name)/"remote.db")

    def test_response_failure_replays_without_duplicate_side_effect(self):
        operation = stage(self.db, "DEMO-1", "demo-team")
        with MockCRM(self.remote_path, fail_after_apply_once=True) as remote:
            self.assertEqual(deliver_one(self.db, remote.url, now=0)["status"], "retry")
            self.assertIsNone(deliver_one(self.db, remote.url, now=1))
            self.assertEqual(deliver_one(self.db, remote.url, now=3)["status"], "delivered")
            self.assertEqual(remote.applied_count(), 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM audit WHERE operation_id=?", (operation,)).fetchone()[0], 2)

    def test_local_intent_and_outbox_are_atomic(self):
        self.db.executescript("""CREATE TRIGGER fail_outbox BEFORE INSERT ON outbox
            BEGIN SELECT RAISE(ABORT,'simulated storage failure'); END;""")
        with self.assertRaises(sqlite3.IntegrityError):
            stage(self.db, "DEMO-1", "demo-team")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 0)

    def test_local_version_conflict(self):
        stage(self.db, "DEMO-1", "demo-team")
        with self.assertRaises(ValueError):
            stage(self.db, "DEMO-1", "other-team", expected_version=0)

    def test_later_version_waits_for_predecessor_retry(self):
        stage(self.db, "DEMO-1", "first-team")
        stage(self.db, "DEMO-1", "second-team", expected_version=1)
        with MockCRM(self.remote_path, fail_after_apply_once=True) as remote:
            self.assertEqual(deliver_one(self.db, remote.url, now=0)["status"], "retry")
            self.assertIsNone(deliver_one(self.db, remote.url, now=1))
            self.assertEqual(deliver_one(self.db, remote.url, now=3)["status"], "delivered")
            self.assertEqual(deliver_one(self.db, remote.url, now=3)["status"], "delivered")
            with sqlite3.connect(self.remote_path) as db:
                self.assertEqual(db.execute("SELECT version,owner FROM accounts").fetchone(), (2, "second-team"))

    def test_permanent_failure_does_not_claim_success(self):
        stage(self.db, "DEMO-1", "demo-team")
        with MockCRM(self.remote_path, always_status=400) as remote:
            self.assertEqual(deliver_one(self.db, remote.url, now=0)["status"], "failed")
            self.assertIsNone(deliver_one(self.db, remote.url, now=100))

    def test_exhausted_retries_require_reconciliation(self):
        stage(self.db, "DEMO-1", "demo-team")
        with MockCRM(self.remote_path, always_status=503) as remote:
            self.assertEqual(deliver_one(self.db, remote.url, now=0, max_attempts=1)["status"], "needs_reconciliation")

    def test_crashed_worker_lease_is_recoverable(self):
        operation = stage(self.db, "DEMO-1", "demo-team")
        with self.db:
            self.db.execute("UPDATE outbox SET status='sending',lease_until=10,token='old' WHERE id=?", (operation,))
        with MockCRM(self.remote_path) as remote:
            self.assertIsNone(deliver_one(self.db, remote.url, now=9))
            self.assertEqual(deliver_one(self.db, remote.url, now=10)["status"], "delivered")

    def test_remote_version_conflict_preserves_remote_data(self):
        stage(self.db, "DEMO-1", "demo-team")
        with MockCRM(self.remote_path) as remote:
            import sqlite3
            with sqlite3.connect(self.remote_path) as rdb:
                rdb.execute("INSERT INTO accounts VALUES ('DEMO-1',4,'remote-team')")
            self.assertEqual(deliver_one(self.db, remote.url, now=0)["status"], "failed")
            self.assertEqual(remote.applied_count(), 0)
