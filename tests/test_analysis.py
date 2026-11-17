import unittest

from practice_ledger.analysis.intervals import daily_union
from practice_ledger.analysis.attribution import allocate_minutes, assert_allocations_do_not_exceed_union
from practice_ledger.analysis.compare import compare_scorecards, source_concentration
from practice_ledger.analysis.coverage import company_coverage, merge_company_aliases, role_coverage
from practice_ledger.analysis.diagnostics import (coverage_gaps, inspect_activity_rows,
                                                    reconcile_department_totals, rejection_profile,
                                                    validate_scorecard)
from practice_ledger.analysis.cohorts import monthly_teacher_activity, compare_months, month_range
from practice_ledger.analysis.distribution import (department_distribution, compare_distribution,
                                                    outlier_candidates)
from support import LedgerCase


class IntervalTests(unittest.TestCase):
    def test_overlap_is_counted_once(self):
        values = daily_union([
            {"starts_at": "2026-01-01T09:00:00+00:00", "ends_at": "2026-01-01T13:00:00+00:00"},
            {"starts_at": "2026-01-01T11:00:00+00:00", "ends_at": "2026-01-01T15:00:00+00:00"},
        ], "2026-01-01", "2026-01-02")
        self.assertEqual(values, {"2026-01-01": 360})

    def test_touching_intervals_merge_without_gap(self):
        values = daily_union([
            {"starts_at": "2026-01-01T09:00:00+00:00", "ends_at": "2026-01-01T10:00:00+00:00"},
            {"starts_at": "2026-01-01T10:00:00+00:00", "ends_at": "2026-01-01T11:00:00+00:00"},
        ], "2026-01-01", "2026-01-02")
        self.assertEqual(values["2026-01-01"], 120)

    def test_daily_limit_applies_after_union(self):
        values = daily_union([
            {"starts_at": "2026-01-01T00:00:00+00:00", "ends_at": "2026-01-01T20:00:00+00:00"},
        ], "2026-01-01", "2026-01-02")
        self.assertEqual(values["2026-01-01"], 480)

    def test_interval_is_split_at_utc_midnight(self):
        values = daily_union([
            {"starts_at": "2026-01-01T23:00:00+00:00", "ends_at": "2026-01-02T02:00:00+00:00"},
        ], "2026-01-01", "2026-01-03")
        self.assertEqual(values, {"2026-01-01": 60, "2026-01-02": 120})

    def test_reporting_period_clips_both_sides(self):
        values = daily_union([
            {"starts_at": "2025-12-31T23:00:00+00:00", "ends_at": "2026-01-02T02:00:00+00:00"},
        ], "2026-01-01", "2026-01-02")
        self.assertEqual(values, {"2026-01-01": 480})

    def test_empty_days_are_explicit(self):
        values = daily_union([], "2026-01-01", "2026-01-04")
        self.assertEqual(values, {"2026-01-01": 0, "2026-01-02": 0, "2026-01-03": 0})

    def test_invalid_period_and_cap_are_rejected(self):
        with self.assertRaises(ValueError):
            daily_union([], "2026-01-02", "2026-01-02")
        with self.assertRaises(ValueError):
            daily_union([], "2026-01-01", "2026-01-02", limit=0)
        with self.assertRaises(ValueError):
            daily_union([], "2026-01-01", "2026-01-02", limit=1441)

    def test_attribution_assigns_overlapping_minutes_to_earliest_activity(self):
        rows = [
            {"activity_id": "later", "starts_at": "2026-01-01T10:00:00+00:00",
             "ends_at": "2026-01-01T12:00:00+00:00"},
            {"activity_id": "early", "starts_at": "2026-01-01T09:00:00+00:00",
             "ends_at": "2026-01-01T11:00:00+00:00"},
        ]
        self.assertEqual(allocate_minutes(rows, "2026-01-01", "2026-01-02"),
                         {"2026-01-01": {"early": 120, "later": 60}})

    def test_attribution_cap_uses_stable_activity_identifier(self):
        rows = [
            {"activity_id": "b", "starts_at": "2026-01-01T00:00:00+00:00",
             "ends_at": "2026-01-01T10:00:00+00:00"},
            {"activity_id": "a", "starts_at": "2026-01-01T00:00:00+00:00",
             "ends_at": "2026-01-01T10:00:00+00:00"},
        ]
        self.assertEqual(allocate_minutes(rows, "2026-01-01", "2026-01-02"),
                         {"2026-01-01": {"a": 480}})

    def test_allocation_matches_union_across_days_and_gaps(self):
        rows = [
            {"activity_id": "a", "starts_at": "2026-01-01T22:00:00+00:00",
             "ends_at": "2026-01-02T02:00:00+00:00"},
            {"activity_id": "b", "starts_at": "2026-01-02T01:00:00+00:00",
             "ends_at": "2026-01-02T04:00:00+00:00"},
        ]
        self.assertTrue(assert_allocations_do_not_exceed_union(rows, "2026-01-01", "2026-01-03"))

    def test_comparison_accounts_for_joiners_leavers_and_moves(self):
        old = {"revision": 1, "individual": [
            {"teacher_id": "A", "name": "甲", "department": "测绘", "total_minutes": 100},
            {"teacher_id": "B", "name": "乙", "department": "测绘", "total_minutes": 50},
        ], "department": {"测绘": {"teacher_count": 2, "participants": 2,
                                    "met_target": 1, "total_minutes": 150}}}
        new = {"revision": 2, "individual": [
            {"teacher_id": "A", "name": "甲", "department": "智能", "total_minutes": 120},
            {"teacher_id": "C", "name": "丙", "department": "智能", "total_minutes": 30},
        ], "department": {"智能": {"teacher_count": 2, "participants": 2,
                                    "met_target": 1, "total_minutes": 150}}}
        result = compare_scorecards(old, new)
        self.assertEqual(result["changed_teacher_count"], 3)
        self.assertEqual(result["net_minutes"], 0)
        moved = next(item for item in result["teacher_changes"] if item["teacher_id"] == "A")
        self.assertEqual(moved["current_department"], "智能")
        self.assertEqual(result["department_changes"]["测绘"]["teacher_count"], -2)

    def test_source_concentration_is_zero_safe_and_sums_shares(self):
        empty = source_concentration([])
        self.assertEqual(empty["total_minutes"], 0)
        values = source_concentration([
            {"source": "基地A", "credited_minutes": 60},
            {"source": "基地B", "credited_minutes": 40},
        ])
        self.assertEqual(values["total_minutes"], 100)
        self.assertAlmostEqual(sum(item["share"] for item in values["sources"]), 1.0)

    def test_company_coverage_attributes_minutes_once_for_overlapping_claims(self):
        rows = [
            {"activity_id": "A", "teacher_id": "T", "company": "基地甲", "role": "测量",
             "starts_at": "2026-01-01T09:00:00+00:00", "ends_at": "2026-01-01T12:00:00+00:00"},
            {"activity_id": "B", "teacher_id": "T", "company": "基地乙", "role": "导航",
             "starts_at": "2026-01-01T11:00:00+00:00", "ends_at": "2026-01-01T14:00:00+00:00"},
        ]
        result = company_coverage(rows, "2026-01-01", "2026-01-02")
        self.assertEqual(result["total_minutes"], 300)
        self.assertEqual(sum(row["minutes"] for row in result["companies"]), 300)

    def test_company_coverage_can_scope_the_active_roster(self):
        rows = [{"activity_id": "A", "teacher_id": "T", "company": "基地", "role": "测量",
                 "starts_at": "2026-01-01T09:00:00+00:00", "ends_at": "2026-01-01T10:00:00+00:00"}]
        self.assertEqual(company_coverage(rows, "2026-01-01", "2026-01-02", roster_ids=[])["total_minutes"], 0)

    def test_role_coverage_counts_teacher_and_company_distinctly(self):
        rows = [{"activity_id": "A", "teacher_id": "T", "company": "基地", "role": "测量",
                 "starts_at": "2026-01-01T09:00:00+00:00", "ends_at": "2026-01-01T10:00:00+00:00"},
                {"activity_id": "B", "teacher_id": "T", "company": "基地", "role": "测量",
                 "starts_at": "2026-01-01T11:00:00+00:00", "ends_at": "2026-01-01T12:00:00+00:00"}]
        result = role_coverage(rows, "2026-01-01", "2026-01-02")
        self.assertEqual(result["测量"], {"minutes": 120, "teacher_count": 1, "company_count": 1})

    def test_company_aliases_merge_reporting_names_without_losing_sources(self):
        coverage = {"companies": [{"company": "华测北京", "minutes": 60, "teacher_count": 2,
                                   "activity_count": 3},
                                  {"company": "华测上海", "minutes": 40, "teacher_count": 1,
                                   "activity_count": 2}]}
        result = merge_company_aliases(coverage, {"华测北京": "华测", "华测上海": "华测"})
        self.assertEqual(result[0]["minutes"], 100)
        self.assertEqual(result[0]["source_names"], ["华测上海", "华测北京"])
        with self.assertRaises(ValueError):
            merge_company_aliases(coverage, {"华测北京": " "})

    def test_data_quality_finds_duplicates_overlaps_and_invalid_intervals(self):
        rows = [{"activity_id": "A", "teacher_id": "T", "starts_at": "2026-01-01T09:00:00+00:00",
                 "ends_at": "2026-01-01T12:00:00+00:00"},
                {"activity_id": "A", "teacher_id": "T", "starts_at": "2026-01-01T11:00:00+00:00",
                 "ends_at": "2026-01-01T13:00:00+00:00"},
                {"activity_id": "bad", "teacher_id": "T", "starts_at": "later", "ends_at": "soon"}]
        findings = inspect_activity_rows(rows)
        self.assertIn("duplicate_activity_id", {item["kind"] for item in findings})
        self.assertIn("overlap", {item["kind"] for item in findings})
        self.assertIn("invalid_timestamp", {item["kind"] for item in findings})

    def test_rejection_profile_has_stable_rates_and_empty_result(self):
        profile = rejection_profile([{"error": "bad"}, {"error": "bad"}, {"error": "missing"}])
        self.assertEqual(profile["total"], 3)
        self.assertAlmostEqual(sum(item["share"] for item in profile["reasons"]), 1.0)
        self.assertEqual(rejection_profile([]), {"total": 0, "reasons": []})

    def test_coverage_gaps_distinguish_unrepresented_and_inactive(self):
        roster = [{"teacher_id": "A", "active": 1}, {"teacher_id": "B", "active": 1},
                  {"teacher_id": "C", "active": 0}]
        result = coverage_gaps(roster, [{"teacher_id": "A"}, {"teacher_id": "C"}])
        self.assertEqual(result["without_activity"], ["B"])
        self.assertEqual(result["outside_active_roster"], ["C"])
        self.assertEqual(result["coverage_rate"], 0.5)

    def test_department_reconciliation_returns_exact_discrepancy(self):
        result = reconcile_department_totals({"D": {"teacher_count": 1}}, [
            {"department": "D", "total_minutes": 10, "met_target": False},
        ])
        self.assertFalse(result["ok"])
        self.assertEqual(result["discrepancies"][0]["metric"], "participants")

    def test_monthly_series_includes_empty_months_and_compares_activity(self):
        rows = [{"activity_id": "A", "teacher_id": "T1", "starts_at": "2026-01-31T23:00:00+00:00",
                 "ends_at": "2026-02-01T01:00:00+00:00"}]
        result = monthly_teacher_activity(rows, "2026-01-01", "2026-04-01")
        self.assertEqual(result["months"], ["2026-01", "2026-02", "2026-03"])
        self.assertEqual(result["trend"][0]["total_minutes"], 60)
        self.assertEqual(result["trend"][1]["total_minutes"], 60)
        change = compare_months(result, "2026-01", "2026-03")
        self.assertEqual(change["net_minutes"], -60)
        self.assertEqual(len(change["changed_teachers"]), 1)

    def test_month_range_rejects_empty_or_reverse_windows(self):
        with self.assertRaises(ValueError):
            list(month_range("2026-02-01", "2026-02-01"))
        with self.assertRaises(ValueError):
            compare_months({"months": ["2026-01"], "teachers": []}, "2026-01", "2026-02")

    def test_distribution_bands_and_quantiles_are_deterministic(self):
        people = [{"department": "D", "total_minutes": value} for value in (0, 100, 200, 300)]
        result = department_distribution(people, 200)["D"]
        self.assertEqual(result["minimum"], 0)
        self.assertEqual(result["maximum"], 300)
        self.assertEqual(result["median"], 150)
        self.assertEqual(result["bands"], {"met": 2, "near": 0, "developing": 1, "none": 1})

    def test_distribution_handles_empty_groups_and_rejects_bad_threshold(self):
        self.assertEqual(department_distribution([], 100), {})
        with self.assertRaises(ValueError):
            department_distribution([], 0)

    def test_distribution_compare_tracks_departments_added_and_removed(self):
        change = compare_distribution({"A": {"teacher_count": 2, "median": 20}},
                                      {"B": {"teacher_count": 3, "median": 40}})
        self.assertEqual(change["A"]["teacher_count"], -2)
        self.assertEqual(change["B"]["teacher_count"], 3)

    def test_outlier_candidates_flag_extreme_records_only_for_large_groups(self):
        rows = [{"department": "D", "teacher_id": str(index), "total_minutes": value}
                for index, value in enumerate((100, 110, 115, 120, 125, 130, 1000))]
        result = outlier_candidates(rows)
        self.assertEqual([row["teacher_id"] for row in result], ["6"])
        self.assertTrue(result[0]["review_required"])
        self.assertEqual(outlier_candidates(rows[:3]), [])


class ScorecardTests(LedgerCase):
    def test_department_denominator_comes_from_active_roster(self):
        self.batch("two", [self.row("A1"), self.row("A2", "T2")])
        self.approve("A1")
        self.approve("A2")
        report = self.preview()
        self.assertEqual(report["department"]["测绘"]["teacher_count"], 2)
        self.assertEqual(report["department"]["测绘"]["participants"], 2)
        self.assertEqual(report["department"]["测绘"]["met_target"], 2)
        self.assertEqual(report["department"]["智能"]["participants"], 0)

    def test_inactive_teacher_is_removed_from_denominator(self):
        self.app.dispatch("POST", "/teachers", {"teachers": [
            {"teacher_id": "T1", "name": "甲", "department": "测绘", "active": False},
        ]})
        report = self.preview()
        self.assertEqual(report["department"]["测绘"]["teacher_count"], 1)
        self.assertEqual(len(report["individual"]), 3)

    def test_only_current_approved_decision_contributes(self):
        self.batch("entries", [self.row("A1"), self.row("A2", "T2")])
        self.approve("A1")
        self.app.dispatch("POST", "/decisions", {
            "activity_id": "A2", "expected_revision": 1, "decision": "needs_info",
            "reviewer": "复核员", "note": "补材料",
        })
        report = self.preview()
        self.assertEqual(report["individual"][0]["total_minutes"], 240)
        self.assertEqual(report["individual"][1]["total_minutes"], 0)

    def test_personal_traceability_names_source_batch_and_revision(self):
        self.batch("source-batch", [self.row("A1")], source="导航基地")
        self.approve("A1")
        person = self.preview()["individual"][0]
        self.assertEqual(person["evidence"], [{"activity_id": "A1", "revision": 1,
                                                "source": "导航基地", "batch_id": "source-batch"}])

    def test_target_threshold_controls_met_count(self):
        self.batch("threshold", [self.row("A1")])
        self.approve("A1")
        low = self.preview(target_minutes=239)
        high = self.preview(target_minutes=241)
        self.assertEqual(low["department"]["测绘"]["met_target"], 1)
        self.assertEqual(high["department"]["测绘"]["met_target"], 0)

    def test_lineage_digest_is_repeatable_for_identical_facts(self):
        first = self.preview()
        second = self.preview()
        self.assertEqual(first["lineage"], second["lineage"])
        self.assertEqual(first["lineage_digest"], second["lineage_digest"])

    def test_single_small_department_hides_complementary_row(self):
        groups = {"small": {"teacher_count": 1, "total_minutes": 100},
                  "large": {"teacher_count": 5, "total_minutes": 900}}
        view = self.app.releases.scorecards.public_view(groups, 3)
        self.assertEqual(view["small"], {"suppressed": True})
        self.assertEqual(view["large"], {"suppressed": True})

    def test_multiple_small_departments_are_all_suppressed(self):
        groups = {"a": {"teacher_count": 1}, "b": {"teacher_count": 2},
                  "c": {"teacher_count": 5}}
        view = self.app.releases.scorecards.public_view(groups, 3)
        self.assertEqual(view["a"], {"suppressed": True})
        self.assertEqual(view["b"], {"suppressed": True})
        self.assertEqual(view["c"], groups["c"])

    def test_all_rows_hidden_when_only_one_department_is_small(self):
        groups = {"small": {"teacher_count": 2}}
        self.assertEqual(self.app.releases.scorecards.public_view(groups, 3),
                         {"small": {"suppressed": True}})

    def test_bad_dates_threshold_and_privacy_values_fail(self):
        invalid = [{"period_start": "bad"}, {"period_start": "2026-02-01", "period_end": "2026-01-01"},
                   {"target_minutes": True}, {"target_minutes": 0}, {"privacy_k": 1},
                   {"privacy_k": True}]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.preview(**changes)

    def test_personal_credits_are_deterministic_and_never_exceed_daily_union(self):
        self.batch("overlap", [self.row("A1", start="2026-01-05T09:00:00+00:00",
                                        end="2026-01-05T13:00:00+00:00"),
                               self.row("A2", start="2026-01-05T11:00:00+00:00",
                                        end="2026-01-05T15:00:00+00:00")])
        self.approve("A1")
        self.approve("A2")
        person = self.preview()["individual"][0]
        self.assertEqual(person["total_minutes"], 360)
        self.assertEqual(sum(sum(day.values()) for day in person["credited_activity_minutes"].values()), 360)
        self.assertEqual(person["credited_activity_minutes"]["2026-01-05"]["A1"], 240)

    def test_scorecard_validation_detects_cross_section_tampering(self):
        body = {"department": {}, "individual": [{"teacher_id": "T", "total_minutes": 90,
                 "daily_minutes": {"2026-01-01": 60}, "credited_activity_minutes": {}, "evidence": []}]}
        result = validate_scorecard(body)
        self.assertFalse(result["ok"])
        self.assertIn("daily_total", {item["check"] for item in result["problems"]})
        self.assertIn("activity_attribution", {item["check"] for item in result["problems"]})


if __name__ == "__main__":
    unittest.main()
