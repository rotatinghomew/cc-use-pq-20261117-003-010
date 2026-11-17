import unittest

from support import LedgerCase


class AssuranceTests(LedgerCase):
    def setUp(self):
        super().setUp()
        self.batch("evidence", [self.row("A1")])

    def test_approval_is_scoped_to_activity_revision(self):
        result = self.approve("A1")
        self.assertEqual(result["decision"], "approved")
        self.assertEqual(result["activity_revision"], 1)
        self.assertEqual(self.store.revision(), 3)
        self.assertEqual(len(self.app.dispatch("GET", "/activities/A1")["decisions"]), 1)

    def test_review_outcomes_are_retained_in_audit_history(self):
        self.app.dispatch("POST", "/decisions", {
            "activity_id": "A1", "expected_revision": 1, "decision": "needs_info",
            "reviewer": "复核甲", "note": "缺少岗位证明",
        })
        self.approve("A1")
        history = self.app.dispatch("GET", "/activities/A1")["decisions"]
        self.assertEqual([row["decision"] for row in history], ["needs_info", "approved"])
        self.assertEqual(history[0]["note"], "缺少岗位证明")

    def test_reject_decision_is_not_in_statistical_qualification(self):
        self.app.dispatch("POST", "/decisions", {
            "activity_id": "A1", "expected_revision": 1, "decision": "rejected",
            "reviewer": "复核甲", "note": "地点无法核验",
        })
        self.assertEqual(self.preview()["individual"][0]["total_minutes"], 0)

    def test_cas_rejects_decision_for_noncurrent_revision(self):
        self.batch("fix", [self.row("A1", end="2026-01-05T14:00:00+00:00")])
        with self.assertRaisesRegex(ValueError, "版本已变化"):
            self.approve("A1", 1)
        self.assertEqual(self.app.dispatch("GET", "/activities/A1")["decisions"], [])

    def test_unknown_decision_and_invalid_reviewer_fail(self):
        for decision in ("approved-ish", None):
            with self.subTest(decision=decision), self.assertRaises(ValueError):
                self.app.dispatch("POST", "/decisions", {
                    "activity_id": "A1", "expected_revision": 1,
                    "decision": decision, "reviewer": "甲", "note": "ok",
                })
        with self.assertRaises(ValueError):
            self.app.dispatch("POST", "/decisions", {
                "activity_id": "A1", "expected_revision": 1, "decision": "approved",
                "reviewer": " ", "note": "ok",
            })

    def test_invalid_revision_types_are_not_coerced(self):
        for revision in (True, 0, "1", 1.0):
            with self.subTest(revision=revision), self.assertRaisesRegex(ValueError, "正整数"):
                self.approve("A1", revision)

    def test_activity_missing_or_inactive_has_no_decision(self):
        with self.assertRaisesRegex(ValueError, "不存在"):
            self.approve("absent")
        self.store.connection.execute("UPDATE activities SET active=0 WHERE activity_id='A1'")
        with self.assertRaisesRegex(ValueError, "不存在"):
            self.approve("A1")

    def test_audit_log_records_the_decision_and_system_revision(self):
        approved = self.approve("A1")
        audit = self.store.connection.execute("SELECT * FROM audit WHERE kind='decision'").fetchone()
        self.assertEqual(audit["entity"], "A1")
        self.assertIn('"decision": "approved"', audit["detail"])
        self.assertEqual(approved["system_revision"], self.store.revision())

    def test_only_active_roster_members_can_approve(self):
        self.app.dispatch("POST", "/teachers", {"teachers": [
            {"teacher_id": "T1", "name": "甲", "department": "测绘", "active": False},
        ]})
        with self.assertRaisesRegex(ValueError, "非在册教师"):
            self.approve("A1")

    def test_failed_review_does_not_change_global_revision(self):
        before = self.store.revision()
        with self.assertRaises(ValueError):
            self.approve("absent")
        self.assertEqual(self.store.revision(), before)


if __name__ == "__main__":
    unittest.main()
