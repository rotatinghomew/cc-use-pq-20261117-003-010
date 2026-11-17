import json
import uuid
from datetime import datetime, timezone


class Releases:
    def __init__(self, store, scorecards):
        self.store = store
        self.scorecards = scorecards

    def preview(self, request):
        with self.store.transaction() as db:
            revision = self.store.revision(db)
            return self.scorecards.compute(db, request, revision)

    def publish(self, preview_id):
        if not isinstance(preview_id, str) or not preview_id:
            raise ValueError("preview_id不能为空")
        now = datetime.now(timezone.utc).isoformat()
        with self.store.transaction() as db:
            preview = db.execute("SELECT * FROM previews WHERE preview_id=?", (preview_id,)).fetchone()
            if preview is None:
                raise ValueError("预览不存在")
            current = self.store.revision(db)
            if preview["revision"] != current:
                raise ValueError("预览已过期，请重新计算")
            release_id = uuid.uuid4().hex
            db.execute("INSERT INTO releases VALUES(?,?,?,?,?)",
                       (release_id, preview_id, current, preview["body"], now))
            self.store.audit(db, "release", release_id,
                             {"preview_id": preview_id, "revision": current}, now)
        return self.get(release_id)

    def get(self, release_id):
        row = self.store.connection.execute("SELECT * FROM releases WHERE release_id=?", (release_id,)).fetchone()
        if row is None:
            raise ValueError("发布不存在")
        result = dict(row)
        result["body"] = json.loads(result["body"])
        result["current"] = result["revision"] == self.store.revision()
        return result

    def latest(self):
        row = self.store.latest_release()
        if row is None:
            return None
        row["body"] = json.loads(row["body"])
        row["current"] = row["revision"] == self.store.revision()
        return row
