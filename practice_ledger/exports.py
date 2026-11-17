import csv
import io
import json
import hashlib


class ReportExport:
    columns = ("teacher_id", "name", "department", "daily_minutes", "total_minutes",
               "met_target", "evidence")

    @staticmethod
    def spreadsheet_text(value):
        rendered = str(value)
        if rendered.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
            return "'" + rendered
        return rendered

    @classmethod
    def csv(cls, report, *, include_individual=True):
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\n")
        if include_individual:
            writer.writerow(cls.columns)
            for item in report.get("individual", []):
                writer.writerow(tuple(cls.spreadsheet_text(item[key]) for key in ("teacher_id", "name", "department")) + (
                                 json.dumps(item["daily_minutes"], ensure_ascii=False, sort_keys=True),
                                 item["total_minutes"], str(item["met_target"]).lower(),
                                 json.dumps(item["evidence"], ensure_ascii=False, sort_keys=True)))
        else:
            writer.writerow(("department", "teacher_count", "participants", "met_target", "total_minutes"))
            for name, values in sorted(report.get("public_department", {}).items()):
                if values.get("suppressed"):
                    writer.writerow((cls.spreadsheet_text(name), "suppressed", "suppressed", "suppressed", "suppressed"))
                else:
                    writer.writerow((cls.spreadsheet_text(name), values["teacher_count"], values["participants"],
                                     values["met_target"], values["total_minutes"]))
        return stream.getvalue()

    @staticmethod
    def load_release(store, release_id):
        row = store.connection.execute("SELECT body FROM releases WHERE release_id=?", (release_id,)).fetchone()
        if row is None:
            raise ValueError("发布不存在")
        return json.loads(row["body"])

    @staticmethod
    def document(report, *, audience="internal"):
        if audience not in {"internal", "public"}:
            raise ValueError("audience必须为internal或public")
        if audience == "public":
            content = {"revision": report["revision"], "period_start": report["period_start"],
                       "period_end": report["period_end"], "department": report["public_department"]}
        else:
            content = report
        serialized = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        return {"schema": "practice-ledger-report/1", "audience": audience,
                "revision": report["revision"], "sha256": digest, "content": content}

    @staticmethod
    def verify_document(document):
        if not isinstance(document, dict) or document.get("schema") != "practice-ledger-report/1":
            return {"ok": False, "reason": "unsupported_schema"}
        if document.get("audience") not in {"internal", "public"}:
            return {"ok": False, "reason": "unsupported_audience"}
        if type(document.get("revision")) is not int or document["revision"] < 0:
            return {"ok": False, "reason": "invalid_revision"}
        content = document.get("content")
        if not isinstance(content, dict):
            return {"ok": False, "reason": "content_missing"}
        if document["audience"] == "public" and "individual" in content:
            return {"ok": False, "reason": "public_document_contains_individuals"}
        serialized = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        actual = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        return {"ok": actual == document.get("sha256"),
                "expected": document.get("sha256"), "actual": actual}
