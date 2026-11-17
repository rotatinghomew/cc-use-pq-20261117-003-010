import tempfile
import unittest
from pathlib import Path

from practice_ledger.storage import Store
from practice_ledger.transport import Application


class LedgerCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "ledger.sqlite3"
        self.store = Store(self.database)
        self.app = Application(self.store)
        self.app.dispatch("POST", "/teachers", {"teachers": [
            {"teacher_id": "T1", "name": "甲", "department": "测绘"},
            {"teacher_id": "T2", "name": "乙", "department": "测绘"},
            {"teacher_id": "T3", "name": "丙", "department": "智能"},
            {"teacher_id": "T4", "name": "丁", "department": "智能"},
        ]})

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def row(self, activity, teacher="T1", start="2026-01-05T09:00:00+00:00",
            end="2026-01-05T13:00:00+00:00", company="基地甲"):
        return {"activity_id": activity, "teacher_id": teacher, "company": company,
                "role": "实践", "starts_at": start, "ends_at": end}

    def batch(self, batch_id, rows, source="企业甲"):
        return self.app.dispatch("POST", "/batches", {
            "source": source, "batch_id": batch_id, "rows": rows,
        })

    def approve(self, activity_id, revision=1):
        return self.app.dispatch("POST", "/decisions", {
            "activity_id": activity_id, "expected_revision": revision,
            "decision": "approved", "reviewer": "复核员", "note": "凭证相符",
        })

    def preview(self, **changes):
        request = {"period_start": "2026-01-01", "period_end": "2026-02-01",
                   "target_minutes": 240, "privacy_k": 2}
        request.update(changes)
        return self.app.dispatch("POST", "/reports/preview", request)
