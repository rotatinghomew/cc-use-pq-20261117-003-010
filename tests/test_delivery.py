import json
import csv
import io
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from practice_ledger.transport import handler_for
from practice_ledger.exports import ReportExport
from practice_ledger.audit import AuditTrail
from practice_ledger.search import ActivitySearch
from practice_ledger.maintenance import Maintenance
from support import LedgerCase


class ReleaseTests(LedgerCase):
    def test_preview_and_release_contain_same_immutable_body(self):
        self.batch("source", [self.row("A1")])
        self.approve("A1")
        preview = self.preview()
        release = self.app.dispatch("POST", "/reports/publish", {"preview_id": preview["preview_id"]})
        self.assertEqual(release["body"]["lineage_digest"], preview["lineage_digest"])
        self.assertTrue(release["current"])
        original = json.dumps(release["body"], sort_keys=True)
        self.app.dispatch("POST", "/teachers", {"teachers": [
            {"teacher_id": "T1", "name": "甲改名", "department": "测绘"},
        ]})
        historical = self.app.releases.get(release["release_id"])
        self.assertFalse(historical["current"])
        self.assertEqual(json.dumps(historical["body"], sort_keys=True), original)

    def test_mutation_between_preview_and_publish_is_rejected(self):
        preview = self.preview()
        self.app.dispatch("POST", "/teachers", {"teachers": [
            {"teacher_id": "T1", "name": "甲更新", "department": "测绘"},
        ]})
        with self.assertRaisesRegex(ValueError, "已过期"):
            self.app.dispatch("POST", "/reports/publish", {"preview_id": preview["preview_id"]})
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM releases").fetchone()[0], 0)

    def test_latest_reports_stale_status_after_source_correction(self):
        self.batch("v1", [self.row("A1")])
        self.approve("A1")
        release = self.app.releases.publish(self.preview()["preview_id"])
        self.assertTrue(self.app.dispatch("GET", "/reports/latest")["current"])
        self.batch("v2", [self.row("A1", end="2026-01-05T10:00:00+00:00")])
        latest = self.app.dispatch("GET", "/reports/latest")
        self.assertEqual(latest["release_id"], release["release_id"])
        self.assertFalse(latest["current"])

    def test_repeated_publish_creates_distinct_immutable_releases(self):
        preview = self.preview()
        first = self.app.releases.publish(preview["preview_id"])
        second = self.app.releases.publish(preview["preview_id"])
        self.assertNotEqual(first["release_id"], second["release_id"])
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM releases").fetchone()[0], 2)

    def test_nonexistent_preview_and_release_fail_cleanly(self):
        with self.assertRaisesRegex(ValueError, "预览不存在"):
            self.app.releases.publish("missing")
        with self.assertRaisesRegex(ValueError, "发布不存在"):
            self.app.releases.get("missing")
        self.assertIsNone(self.app.releases.latest())

    def test_public_summary_suppresses_individual_detail(self):
        self.batch("sample", [self.row("A1")])
        self.approve("A1")
        report = self.preview(privacy_k=3)
        self.assertEqual(report["public_department"]["测绘"], {"suppressed": True})
        self.assertIn("individual", report)
        self.assertEqual(report["individual"][0]["teacher_id"], "T1")

    def test_preview_creation_is_a_read_of_the_same_revision(self):
        before = self.store.revision()
        preview = self.preview()
        self.assertEqual(preview["revision"], before)
        self.assertEqual(self.store.revision(), before)

    def test_repeated_changes_keep_release_order_and_old_body(self):
        first_preview = self.preview()
        first = self.app.releases.publish(first_preview["preview_id"])
        self.app.dispatch("POST", "/teachers", {"teachers": [
            {"teacher_id": "T2", "name": "乙改名", "department": "测绘"},
        ]})
        second_preview = self.preview()
        second = self.app.releases.publish(second_preview["preview_id"])
        self.assertEqual(self.app.releases.latest()["release_id"], second["release_id"])
        self.assertFalse(self.app.releases.get(first["release_id"])["current"])
        self.assertNotEqual(first["body"], second["body"])

    def test_export_personal_csv_includes_traceable_evidence(self):
        self.batch("export", [self.row("A1")])
        self.approve("A1")
        report = self.preview()
        lines = list(csv.reader(io.StringIO(ReportExport.csv(report))))
        self.assertEqual(lines[0][0], "teacher_id")
        self.assertEqual(lines[1][0], "T1")
        self.assertIn("A1", lines[1][-1])

    def test_public_export_preserves_suppression_markers(self):
        self.batch("export", [self.row("A1")])
        self.approve("A1")
        report = self.preview(privacy_k=3)
        lines = list(csv.reader(io.StringIO(ReportExport.csv(report, include_individual=False))))
        row = next(value for value in lines[1:] if value[0] == "测绘")
        self.assertEqual(row[1:], ["suppressed"] * 4)

    def test_quality_summary_tracks_batch_rejections_and_staleness(self):
        self.batch("quality", [self.row("A1"), self.row("A2", teacher="missing")])
        self.approve("A1")
        before = self.app.quality.summary()
        self.assertEqual(before["batch_count"], 1)
        self.assertEqual(before["accepted_rows"], 1)
        self.assertEqual(before["rejected_rows"], 1)
        self.assertFalse(before["latest_release"])
        self.assertEqual(self.app.quality.rejected_rows("企业甲", "quality")[0]["row_no"], 2)

    def test_quality_source_health_counts_only_submitted_rows(self):
        self.batch("health-a", [self.row("A1"), self.row("bad", teacher="missing")], source="基地A")
        self.batch("health-b", [self.row("B1", teacher="T2")], source="基地B")
        health = self.app.quality.source_health()
        self.assertEqual(health[0]["source"], "基地A")
        self.assertAlmostEqual(health[0]["acceptance_rate"], 0.5)
        self.assertEqual(health[1]["acceptance_rate"], 1.0)

    def test_batch_readiness_exposes_quality_reasons_and_threshold(self):
        self.batch("ready", [self.row("A1"), self.row("A2", teacher="missing")])
        low_bar = self.app.quality.batch_readiness("企业甲", "ready", minimum_acceptance=0.5)
        high_bar = self.app.quality.batch_readiness("企业甲", "ready", minimum_acceptance=0.75)
        self.assertTrue(low_bar["ready_for_analysis"])
        self.assertFalse(high_bar["ready_for_analysis"])
        self.assertEqual(low_bar["rejection_reasons"][0]["count"], 1)

    def test_batch_readiness_rejects_unknown_batch_and_bad_threshold(self):
        with self.assertRaisesRegex(ValueError, "不存在"):
            self.app.quality.batch_readiness("企业甲", "missing")
        self.batch("ready", [self.row("A1")])
        for threshold in (-0.1, 1.1, True, "0.5"):
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                self.app.quality.batch_readiness("企业甲", "ready", minimum_acceptance=threshold)

    def test_export_document_hash_is_stable_and_public_document_excludes_people(self):
        self.batch("document", [self.row("A1")])
        self.approve("A1")
        report = self.preview()
        first = ReportExport.document(report)
        second = ReportExport.document(report)
        self.assertEqual(first["sha256"], second["sha256"])
        public = ReportExport.document(report, audience="public")
        self.assertNotIn("individual", public["content"])
        with self.assertRaises(ValueError):
            ReportExport.document(report, audience="unknown")

    def test_export_document_verification_detects_tampering(self):
        report = self.preview()
        document = ReportExport.document(report, audience="public")
        self.assertTrue(ReportExport.verify_document(document)["ok"])
        document["content"]["period_start"] = "1999-01-01"
        self.assertFalse(ReportExport.verify_document(document)["ok"])
        self.assertFalse(ReportExport.verify_document({})["ok"])

    def test_csv_text_cells_are_safe_for_spreadsheet_opening(self):
        report = {"individual": [{"teacher_id": "=1+1", "name": "@SUM(A1)", "department": "-cmd",
                    "daily_minutes": {}, "total_minutes": 0, "met_target": False, "evidence": []}]}
        rows = list(csv.reader(io.StringIO(ReportExport.csv(report))))
        self.assertEqual(rows[1][:3], ["'=1+1", "'@SUM(A1)", "'-cmd"])

    def test_activity_facets_match_searchable_business_dimensions(self):
        self.batch("facet", [self.row("A1", company="导航企业"), self.row("A2", "T2", company="空间企业")])
        facets = self.app.search.facets(department="测绘")
        self.assertEqual(len(facets), 2)
        self.assertEqual({row["company"] for row in facets}, {"导航企业", "空间企业"})
        self.assertTrue(all(row["decision"] == "pending" for row in facets))

    def test_journal_status_reports_expected_local_database_pragmas(self):
        status = self.app.maintenance.journal_status()
        self.assertEqual(status["journal_mode"].lower(), "wal")
        self.assertTrue(status["foreign_keys_enabled"])
        self.assertGreater(status["database_bytes"], 0)

    def test_quality_reports_old_decisions_after_activity_revision(self):
        self.batch("old", [self.row("A1")])
        self.approve("A1")
        self.batch("new", [self.row("A1", end="2026-01-05T14:00:00+00:00")])
        summary = self.app.quality.summary()
        self.assertEqual(summary["stale_decisions"], 1)

    def test_register_queries_filter_department_and_active_status(self):
        self.app.dispatch("POST", "/teachers", {"teachers": [
            {"teacher_id": "T2", "name": "乙", "department": "测绘", "active": False},
        ]})
        self.assertEqual([row["teacher_id"] for row in self.app.register.roster(department="测绘")], ["T1"])
        self.assertEqual(len(self.app.register.roster(department="测绘", include_inactive=True)), 2)
        self.assertIsNone(self.app.register.get("unknown"))
        self.assertIsNone(self.app.register.revision_for("unknown"))

    def test_intake_history_lists_batches_and_quarantined_payloads(self):
        self.batch("one", [self.row("A1"), self.row("bad", teacher="missing")])
        batches = self.app.intake.batches(source="企业甲")
        self.assertEqual(batches[0]["batch_id"], "one")
        rejected = self.app.intake.retryable_rejections("企业甲", "one")
        self.assertEqual(rejected[0]["payload"]["activity_id"], "bad")
        self.assertIn("教师不存在", rejected[0]["error"])

    def test_intake_history_limit_is_bounded(self):
        for value in (0, 1001, True):
            with self.subTest(limit=value), self.assertRaises(ValueError):
                self.app.intake.batches(limit=value)

    def test_audit_trace_carries_source_activity_and_decision_events(self):
        self.batch("trace", [self.row("A1")])
        self.approve("A1")
        trace = self.app.quality.activity_sources("A1")
        self.assertEqual(trace["activity"]["batch_id"], "trace")
        kinds = [event["kind"] for event in trace["history"]]
        self.assertIn("intake", kinds)
        self.assertIn("decision", kinds)

    def test_unknown_activity_trace_is_absent(self):
        self.assertIsNone(self.app.quality.activity_sources("missing"))

    def test_activity_search_filters_department_and_approval(self):
        self.batch("search", [self.row("A1"), self.row("B1", "T2", company="企业乙")])
        self.approve("A1")
        result = self.app.search.search(department="测绘", decision="approved")
        self.assertEqual([item["activity_id"] for item in result["items"]], ["A1"])
        pending = self.app.search.search(decision="pending")
        self.assertEqual([item["activity_id"] for item in pending["items"]], ["B1"])

    def test_activity_search_overlap_filter_and_cursor(self):
        self.batch("search", [self.row("A1"), self.row("B1", "T2")])
        first = self.app.search.search(limit=1)
        second = self.app.search.search(after_id=first["next_after"])
        self.assertEqual(len(first["items"]), 1)
        self.assertTrue(first["has_more"])
        self.assertFalse(second["has_more"])
        self.assertNotEqual(first["items"][0]["activity_id"], second["items"][0]["activity_id"])

    def test_activity_search_rejects_bad_filter_values(self):
        for values in ({"limit": 501}, {"decision": "unknown"}, {"teacher_id": "bad id"},
                       {"starts_after": "no-offset"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.app.search.search(**values)

    def test_activity_search_url_parser_rejects_unknown_and_repeated_keys(self):
        with self.assertRaisesRegex(ValueError, "未知筛选"):
            ActivitySearch.from_url(self.app.search, "/activities?surprise=x")
        with self.assertRaisesRegex(ValueError, "只能指定一次"):
            ActivitySearch.from_url(self.app.search, "/activities?limit=1&limit=2")

    def test_audit_pages_are_stable_and_filterable(self):
        self.batch("audit", [self.row("A1")])
        self.approve("A1")
        page = self.app.audit.page(limit=1)
        self.assertTrue(page["has_more"])
        next_page = self.app.audit.page(after=page["next_after"], limit=10)
        self.assertGreater(next_page["events"][0]["audit_id"], page["next_after"])
        decisions = self.app.audit.page(kind="decision")
        self.assertEqual(decisions["events"][0]["entity"], "A1")
        self.assertEqual(self.app.audit.counts()["decision"], 1)

    def test_audit_page_rejects_invalid_limits_and_cursor(self):
        for kwargs in ({"after": -1}, {"after": True}, {"limit": 0}, {"limit": 501}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.app.audit.page(**kwargs)

    def test_audit_verification_checks_append_only_event_shapes(self):
        self.batch("verify", [self.row("A1")])
        self.approve("A1")
        checked = self.app.audit.verify()
        self.assertTrue(checked["ok"])
        self.assertEqual(checked["event_count"], 3)
        self.assertEqual(checked["last_id"], 3)

    def test_audit_verification_reports_corrupt_detail_without_crashing(self):
        self.store.connection.execute("INSERT INTO audit(kind,entity,detail,created_at) VALUES(?,?,?,?)",
                                      ("broken", "test", "not-json", "2026-01-01T00:00:00+00:00"))
        checked = self.app.audit.verify()
        self.assertFalse(checked["ok"])
        self.assertEqual(checked["malformed"][0]["audit_id"], 2)

    def test_wal_checkpoint_reports_pages_and_rejects_unknown_mode(self):
        self.batch("wal", [self.row("A1")])
        result = self.app.maintenance.checkpoint()
        self.assertEqual(result["mode"], "PASSIVE")
        self.assertEqual(result["revision"], self.store.revision())
        with self.assertRaises(ValueError):
            self.app.maintenance.checkpoint("DELETE")

    def test_department_counts_distinguish_active_from_inactive(self):
        self.app.dispatch("POST", "/teachers", {"teachers": [
            {"teacher_id": "T2", "name": "乙", "department": "测绘", "active": False},
        ]})
        self.assertEqual(self.app.register.department_counts()["测绘"], {"active": 1, "inactive": 0})
        self.assertEqual(self.app.register.department_counts(include_inactive=True)["测绘"],
                         {"active": 1, "inactive": 1})

    def test_batch_preview_is_non_mutating_and_detects_identity_conflict(self):
        payload = {"source": "预检基地", "batch_id": "preview", "rows": [self.row("A1")]}
        before = self.store.revision()
        preview = self.app.intake.preview(payload)
        self.assertEqual(preview["valid_count"], 1)
        self.assertEqual(self.store.revision(), before)
        self.app.intake.apply(payload)
        changed = {**payload, "rows": [self.row("A1", end="2026-01-05T14:00:00+00:00")]}
        self.assertTrue(self.app.intake.preview(changed)["identity_conflict"])

    def test_audit_entity_history_and_integrity_check(self):
        self.batch("integrity", [self.row("A1")])
        self.approve("A1")
        history = self.app.audit.entity_history("A1")
        self.assertEqual(len(history), 1)
        self.assertEqual(self.app.maintenance.integrity()["integrity"], ["ok"])
        self.assertTrue(self.app.quality.reconcile()["ok"])

    def test_database_reconciliation_detects_missing_batch_link(self):
        self.batch("consistent", [self.row("A1")])
        self.store.connection.execute("DELETE FROM batches WHERE batch_id='consistent'")
        result = self.app.quality.reconcile()
        self.assertFalse(result["ok"])
        self.assertEqual(result["checks"]["current_activity_without_batch"], 1)

    def test_backup_and_restore_copy_preserve_revision_without_overwrite(self):
        self.batch("backup", [self.row("A1")])
        self.approve("A1")
        backup = self.database.parent / "backup.sqlite3"
        result = self.app.maintenance.backup(backup)
        self.assertEqual(result["revision"], self.store.revision())
        restored = self.database.parent / "restored.sqlite3"
        copy = Maintenance.restore_copy(backup, restored)
        self.assertTrue(copy["ok"])
        self.assertEqual(Maintenance.inspect_file(restored)["revision"], self.store.revision())
        with self.assertRaises(FileExistsError):
            self.app.maintenance.backup(backup)

    def test_invalid_backup_is_refused_and_does_not_create_restore_target(self):
        broken = self.database.parent / "broken.sqlite3"
        broken.write_text("not sqlite", encoding="utf-8")
        target = self.database.parent / "never-created.sqlite3"
        with self.assertRaises(Exception):
            Maintenance.restore_copy(broken, target)
        self.assertFalse(target.exists())

    def test_monthly_report_uses_current_approved_activity_only(self):
        self.batch("monthly", [self.row("A1", start="2026-01-05T09:00:00+00:00",
                                        end="2026-01-05T10:00:00+00:00"),
                               self.row("A2", "T2", start="2026-02-05T09:00:00+00:00",
                                        end="2026-02-05T10:00:00+00:00")])
        self.approve("A1")
        result = self.app.dispatch("POST", "/reports/monthly", {
            "period_start": "2026-01-01", "period_end": "2026-03-01",
        })
        self.assertEqual(result["trend"][0]["total_minutes"], 60)
        self.assertEqual(result["trend"][1]["total_minutes"], 0)


class HttpTests(LedgerCase):
    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.app))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        super().tearDown()

    def request_json(self, method, path, body=None):
        payload = None if body is None else json.dumps(body).encode()
        request = Request(self.base + path, data=payload, method=method,
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())

    def test_http_json_can_import_review_preview_and_publish(self):
        self.request_json("POST", "/batches", {
            "source": "http", "batch_id": "b1", "rows": [self.row("A1")],
        })
        status, approval = self.request_json("POST", "/decisions", {
            "activity_id": "A1", "expected_revision": 1, "decision": "approved",
            "reviewer": "HTTP复核", "note": "已核验",
        })
        self.assertEqual(status, 200)
        self.assertEqual(approval["result"]["decision"], "approved")
        _, preview = self.request_json("POST", "/reports/preview", {
            "period_start": "2026-01-01", "period_end": "2026-02-01",
            "target_minutes": 100, "privacy_k": 2,
        })
        _, released = self.request_json("POST", "/reports/publish", {
            "preview_id": preview["result"]["preview_id"],
        })
        self.assertEqual(released["result"]["body"]["individual"][0]["total_minutes"], 240)
        _, latest = self.request_json("GET", "/reports/latest")
        self.assertTrue(latest["result"]["current"])

    def test_http_bad_input_returns_structured_client_error(self):
        request = Request(self.base + "/batches", data=b'{"bad": true}', method="POST",
                          headers={"Content-Type": "application/json"})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 400)
        payload = json.loads(caught.exception.read())
        self.assertFalse(payload["ok"])
        self.assertIn("rows", payload["error"])

    def test_http_unknown_route_is_not_reported_as_success(self):
        request = Request(self.base + "/unknown", data=b'{}', method="POST",
                          headers={"Content-Type": "application/json"})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 404)
        self.assertFalse(json.loads(caught.exception.read())["ok"])

    def test_http_oversized_body_is_rejected(self):
        request = Request(self.base + "/teachers", data=b"{}", method="POST",
                          headers={"Content-Type": "application/json", "Content-Length": "1000001"})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 400)

    def test_http_quality_and_roster_endpoints_reflect_real_state(self):
        self.batch("http-quality", [self.row("A1")])
        status, roster = self.request_json("GET", "/teachers")
        self.assertEqual(status, 200)
        self.assertEqual(len(roster["result"]["teachers"]), 4)
        _, quality = self.request_json("GET", "/quality")
        self.assertEqual(quality["result"]["summary"]["batch_count"], 1)

    def test_http_csv_export_is_public_and_keeps_masking(self):
        self.batch("csv", [self.row("A1")])
        self.approve("A1")
        preview = self.preview(privacy_k=3)
        release = self.app.releases.publish(preview["preview_id"])
        _, response = self.request_json("GET", f"/reports/{release['release_id']}/csv")
        self.assertIn("suppressed", response["result"]["csv"])


if __name__ == "__main__":
    unittest.main()
