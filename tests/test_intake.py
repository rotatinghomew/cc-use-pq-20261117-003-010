import unittest

from support import LedgerCase


class IntakeTests(LedgerCase):
    def test_batch_accepts_valid_rows_and_quarantines_bad_rows(self):
        result = self.batch("mixed", [self.row("A1"), self.row("A2", teacher="UNKNOWN"),
                                       {"activity_id": "broken"}])
        self.assertEqual(result["accepted_count"], 1)
        self.assertEqual(result["rejected_count"], 2)
        self.assertEqual(result["accepted"][0]["activity_id"], "A1")
        self.assertEqual([item["row_no"] for item in result["rejected"]], [2, 3])
        self.assertIn("教师不存在", result["rejected"][0]["error"])

    def test_identical_batch_replay_is_idempotent(self):
        rows = [self.row("A1")]
        first = self.batch("retry", rows)
        revision = self.store.revision()
        second = self.batch("retry", rows)
        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        self.assertEqual(second["accepted_count"], 1)
        self.assertEqual(self.store.revision(), revision)
        count = self.store.connection.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
        self.assertEqual(count, 1)

    def test_changed_content_cannot_reuse_batch_identity(self):
        self.batch("same", [self.row("A1")])
        with self.assertRaisesRegex(ValueError, "batch_id"):
            self.batch("same", [self.row("A1", end="2026-01-05T14:00:00+00:00")])
        self.assertEqual(self.store.connection.execute("SELECT ends_at FROM activities").fetchone()[0],
                         "2026-01-05T13:00:00+00:00")

    def test_row_revision_invalidates_old_approval(self):
        self.batch("first", [self.row("A1")])
        self.approve("A1")
        corrected = self.batch("correction", [self.row("A1", end="2026-01-05T14:00:00+00:00")])
        self.assertEqual(corrected["accepted"][0]["revision"], 2)
        decisions = self.app.dispatch("GET", "/activities/A1")["decisions"]
        self.assertEqual(len(decisions), 1)
        report = self.preview()
        self.assertEqual(report["individual"][0]["total_minutes"], 0)
        with self.assertRaisesRegex(ValueError, "版本已变化"):
            self.approve("A1", revision=1)

    def test_malformed_row_does_not_abort_good_siblings(self):
        result = self.batch("partial", [self.row("good"), self.row("bad", start="later")])
        self.assertEqual(result["accepted_count"], 1)
        self.assertEqual(result["rejected_count"], 1)
        self.assertEqual(result["accepted"][0]["activity_id"], "good")

    def test_rejects_unknown_teacher_without_writing_activity(self):
        result = self.batch("unknown", [self.row("A1", teacher="missing")])
        self.assertEqual(result["accepted_count"], 0)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM activities").fetchone()[0], 0)

    def test_inactive_teacher_cannot_receive_new_activity(self):
        self.app.dispatch("POST", "/teachers", {"teachers": [
            {"teacher_id": "T1", "name": "甲", "department": "测绘", "active": False},
        ]})
        result = self.batch("inactive", [self.row("A1")])
        self.assertEqual(result["rejected_count"], 1)
        self.assertEqual(result["accepted_count"], 0)

    def test_duplicate_activity_row_is_quarantined_and_first_is_kept(self):
        self.batch("initial", [self.row("A1")])
        result = self.batch("duplicates", [self.row("A2"), self.row("A2", end="2026-01-05T14:00:00+00:00")])
        self.assertEqual(result["accepted_count"], 1)
        self.assertEqual(result["rejected_count"], 1)
        self.assertIn("重复", result["rejected"][0]["error"])
        activity = self.store.connection.execute("SELECT ends_at FROM activities WHERE activity_id='A2'").fetchone()
        self.assertEqual(activity["ends_at"], "2026-01-05T13:00:00+00:00")

    def test_teacher_batch_duplicate_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "不能重复"):
            self.app.dispatch("POST", "/teachers", {"teachers": [
                {"teacher_id": "D", "name": "甲", "department": "测绘"},
                {"teacher_id": "D", "name": "乙", "department": "测绘"},
            ]})

    def test_malformed_container_and_empty_batches_fail(self):
        for payload in ({"source": "S", "batch_id": "B"},
                        {"source": "S", "batch_id": "B", "rows": []}, []):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.app.dispatch("POST", "/batches", payload)

    def test_source_and_batch_identifiers_are_validated(self):
        with self.assertRaisesRegex(ValueError, "source"):
            self.batch("b", [self.row("A")], source="not valid")
        with self.assertRaisesRegex(ValueError, "batch_id"):
            self.batch("", [self.row("A")])

    def test_rejected_rows_are_durable_after_reopening_database(self):
        self.batch("durable", [self.row("A1", teacher="absent")])
        self.store.close()
        from practice_ledger.storage import Store
        self.store = Store(self.database)
        self.app = __import__("practice_ledger.transport", fromlist=["Application"]).Application(self.store)
        replay = self.batch("durable", [self.row("A1", teacher="absent")])
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["rejected_count"], 1)
        self.assertEqual(len(replay["rejected"]), 1)


if __name__ == "__main__":
    unittest.main()
