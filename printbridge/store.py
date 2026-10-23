import sqlite3
import time


class Store:
    def __init__(self, root):
        self.root = root
        self.db = root / "jobs.sqlite3"

    def connect(self):
        con = sqlite3.connect(self.db, timeout=10)
        con.row_factory = sqlite3.Row
        return con

    def initialize(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
                fingerprint TEXT NOT NULL, name TEXT NOT NULL,
                copies INTEGER NOT NULL, sides TEXT NOT NULL,
                status TEXT NOT NULL, message TEXT NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL)""")
            con.execute("""CREATE TABLE IF NOT EXISTS ipp_jobs (
                number INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT UNIQUE NOT NULL, owner TEXT NOT NULL)""")
            con.execute("UPDATE jobs SET status='uncertain', message=?, updated_at=? WHERE status='processing'",
                        ("服务中断，可能已提交；请检查 Windows 队列和出纸情况，勿直接重打。", time.time()))
        # The process lock guarantees that these are abandoned uploads.
        for path in self.root.glob("*.part"):
            path.unlink(missing_ok=True)
        with self.connect() as con:
            ids = {r[0] for r in con.execute("SELECT id FROM jobs")}
        for path in self.root.glob("*.pdf"):
            if path.stem not in ids:
                path.unlink(missing_ok=True)

    def add(self, job_id, key, fingerprint, name, copies, sides, owner=None):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute("SELECT * FROM jobs WHERE request_key=?", (key,)).fetchone()
            if existing and owner and time.time() - existing["created_at"] > 600:
                con.execute("UPDATE jobs SET request_key=? WHERE id=?", (key + ":expired:" + existing["id"], existing["id"]))
                existing = None
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise ValueError("同一请求编号对应的文件或打印参数不同")
                return dict(existing), False
            now = time.time()
            con.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?, 'queued', ?,?,?)",
                        (job_id, key, fingerprint, name, copies, sides, "等待处理", now, now))
            if owner:
                con.execute("INSERT INTO ipp_jobs(job_id,owner) VALUES (?,?)", (job_id, owner))
            return dict(con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()), True

    def claim(self):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row:
                con.execute("UPDATE jobs SET status='processing', message='正在处理', updated_at=? WHERE id=?",
                            (time.time(), row["id"]))
                return dict(row)

    def finish(self, job_id, status, message):
        with self.connect() as con:
            con.execute("UPDATE jobs SET status=?, message=?, updated_at=? WHERE id=?",
                        (status, message, time.time(), job_id))

    def list(self):
        with self.connect() as con:
            return [dict(row) for row in con.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 100")]

    def cleanup(self, hours):
        with self.connect() as con:
            rows = con.execute("SELECT id FROM jobs WHERE status IN ('submitted','simulated','failed','uncertain','canceled') AND updated_at<?",
                               (time.time() - hours * 3600,)).fetchall()
        for row in rows:
            (self.root / (row["id"] + ".pdf")).unlink(missing_ok=True)

    def ipp_job(self, owner, number=None, job_id=None):
        with self.connect() as con:
            row = con.execute("""SELECT j.*, i.number FROM jobs j JOIN ipp_jobs i ON j.id=i.job_id
                WHERE i.owner=? AND (i.number=? OR j.id=?)""", (owner, number, job_id)).fetchone()
            return dict(row) if row else None

    def ipp_list(self, owner):
        with self.connect() as con:
            return [dict(row) for row in con.execute("""SELECT j.*, i.number FROM jobs j
                JOIN ipp_jobs i ON j.id=i.job_id WHERE i.owner=? ORDER BY i.number DESC""", (owner,))]

    def cancel_ipp(self, owner, number):
        with self.connect() as con:
            result = con.execute("""UPDATE jobs SET status='canceled', message='排队任务已取消', updated_at=?
                WHERE status='queued' AND id IN (SELECT job_id FROM ipp_jobs WHERE owner=? AND number=?)""",
                (time.time(), owner, number))
            return result.rowcount > 0
