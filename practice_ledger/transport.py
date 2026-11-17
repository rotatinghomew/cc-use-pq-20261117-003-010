import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .assurance import Assurance
from .intake import Intake
from .register import Register
from .releases import Releases
from .analysis.scorecards import Scorecards
from .exports import ReportExport
from .quality import Quality
from .analysis.compare import compare_scorecards
from .audit import AuditTrail
from .search import ActivitySearch
from .analysis.cohorts import monthly_teacher_activity, compare_months
from .maintenance import Maintenance


class Application:
    def __init__(self, store):
        self.store = store
        self.register = Register(store)
        self.intake = Intake(store)
        self.assurance = Assurance(store)
        self.scorecards = Scorecards(store)
        self.releases = Releases(store, self.scorecards)
        self.quality = Quality(store)
        self.audit = AuditTrail(store)
        self.search = ActivitySearch(store)
        self.maintenance = Maintenance(store)

    def dispatch(self, method, path, body=None):
        body = body or {}
        if method == "POST" and path == "/teachers":
            return self.register.upsert(body.get("teachers"))
        if method == "POST" and path == "/batches":
            return self.intake.apply(body)
        if method == "POST" and path == "/batches/preview":
            return self.intake.preview(body)
        if method == "POST" and path == "/batches/readiness":
            return self.quality.batch_readiness(body.get("source"), body.get("batch_id"),
                                                minimum_acceptance=body.get("minimum_acceptance", 0.95))
        if method == "POST" and path == "/decisions":
            return self.assurance.decide(body)
        if method == "POST" and path == "/reports/preview":
            return self.releases.preview(body)
        if method == "POST" and path == "/reports/monthly":
            with self.store.transaction() as db:
                return self.scorecards.monthly(db, body)
        if method == "POST" and path == "/reports/publish":
            return self.releases.publish(body.get("preview_id"))
        if method == "GET" and path == "/reports/latest":
            return self.releases.latest()
        if method == "GET" and path == "/teachers":
            return {"teachers": self.register.roster()}
        if method == "GET" and path == "/batches":
            return {"batches": self.intake.batches()}
        if method == "GET" and path == "/quality":
            return {"summary": self.quality.summary(), "reconciliation": self.quality.reconcile()}
        if method == "GET" and path == "/integrity":
            return self.maintenance.integrity()
        if method == "GET" and path == "/audit":
            return self.audit.page()
        if method == "GET" and path == "/activities":
            return ActivitySearch.from_url(self.search, path)
        if method == "GET" and path.startswith("/reports/") and path.endswith("/csv"):
            release_id = path.split("/")[2]
            report = ReportExport.load_release(self.store, release_id)
            return {"csv": ReportExport.csv(report, include_individual=False)}
        if method == "GET" and path.startswith("/reports/") and "/compare/" in path:
            left, right = path.removeprefix("/reports/").split("/compare/", 1)
            return compare_scorecards(self.releases.get(left)["body"], self.releases.get(right)["body"])
        if method == "GET" and path.startswith("/reports/"):
            return self.releases.get(path.rsplit("/", 1)[-1])
        if method == "GET" and path.startswith("/activities/"):
            return {"decisions": self.assurance.history(path.rsplit("/", 1)[-1])}
        raise LookupError("路由不存在")


def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= 1_000_000:
                    raise ValueError("请求体长度必须为1至1000000字节")
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                result = app.dispatch("POST", self.path, body)
                self.respond(200, {"ok": True, "result": result})
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                self.respond(400, {"ok": False, "error": str(exc)})
            except LookupError as exc:
                self.respond(404, {"ok": False, "error": str(exc)})
            except Exception:
                self.respond(500, {"ok": False, "error": "内部错误"})

        def do_GET(self):
            try:
                result = app.dispatch("GET", self.path)
                self.respond(200, {"ok": True, "result": result})
            except LookupError as exc:
                self.respond(404, {"ok": False, "error": str(exc)})
            except ValueError as exc:
                self.respond(400, {"ok": False, "error": str(exc)})

        def respond(self, status, value):
            payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    return Handler


def serve(app, host="127.0.0.1", port=8765):
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("服务仅可绑定本机回环地址")
    server = ThreadingHTTPServer((host, port), handler_for(app))
    try:
        server.serve_forever()
    finally:
        server.server_close()
