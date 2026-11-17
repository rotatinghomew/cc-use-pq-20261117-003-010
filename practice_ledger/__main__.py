import argparse
import json
from pathlib import Path

from .storage import Store
from .transport import Application, serve


def fixture_run(store, path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    app = Application(store)
    app.dispatch("POST", "/teachers", {"teachers": data["teachers"]})
    imported = app.dispatch("POST", "/batches", data["batch"])
    for item in imported["accepted"]:
        app.dispatch("POST", "/decisions", {
            "activity_id": item["activity_id"], "expected_revision": item["revision"],
            "decision": "approved", "reviewer": "示例复核员", "note": "本地合成样本",
        })
    report = app.dispatch("POST", "/reports/preview", data["report"])
    published = app.dispatch("POST", "/reports/publish", {"preview_id": report["preview_id"]})
    return {"batch": imported, "report": published}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="practice-ledger")
    parser.add_argument("--db", default="practice.sqlite3")
    sub = parser.add_subparsers(dest="command")
    demo = sub.add_parser("demo")
    demo.add_argument("--fixture", default="fixtures/practice.json")
    server = sub.add_parser("serve")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    with Store(args.db) as store:
        if args.command == "serve":
            serve(Application(store), args.host, args.port)
            return 0
        result = fixture_run(store, getattr(args, "fixture", "fixtures/practice.json"))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
