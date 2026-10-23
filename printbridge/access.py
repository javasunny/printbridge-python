import hashlib
import secrets
import time
from uuid import uuid4


class AccessStore:
    def __init__(self, store):
        self.store = store

    def initialize(self):
        with self.store.connect() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS access_keys (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, prefix TEXT NOT NULL,
                digest TEXT UNIQUE NOT NULL, created_at REAL NOT NULL, revoked_at REAL)""")
            con.execute("""CREATE TABLE IF NOT EXISTS clients (
                id TEXT NOT NULL, key_id TEXT NOT NULL, ip TEXT NOT NULL,
                last_seen REAL NOT NULL, connected INTEGER NOT NULL,
                PRIMARY KEY (id, key_id))""")
            con.execute("UPDATE clients SET connected=0")

    def issue(self, name):
        secret = "pb_" + secrets.token_urlsafe(32)
        key_id = str(uuid4())
        with self.store.connect() as con:
            con.execute("INSERT INTO access_keys VALUES (?,?,?,?,?,NULL)",
                        (key_id, name, secret[:11], hashlib.sha256(secret.encode()).hexdigest(), time.time()))
        return {"id": key_id, "secret": secret}

    def authenticate(self, secret):
        digest = hashlib.sha256(secret.encode()).hexdigest()
        with self.store.connect() as con:
            row = con.execute("SELECT id FROM access_keys WHERE digest=? AND revoked_at IS NULL", (digest,)).fetchone()
        return row["id"] if row else None

    def keys(self):
        with self.store.connect() as con:
            return [dict(r) for r in con.execute(
                "SELECT id,name,prefix,created_at,revoked_at FROM access_keys ORDER BY created_at DESC")]

    def revoke(self, key_id):
        with self.store.connect() as con:
            result = con.execute("UPDATE access_keys SET revoked_at=COALESCE(revoked_at,?) WHERE id=?", (time.time(), key_id))
            con.execute("UPDATE clients SET connected=0 WHERE key_id=?", (key_id,))
            return result.rowcount > 0

    def heartbeat(self, key_id, client_id, ip):
        with self.store.connect() as con:
            # Recheck in the same transaction so revocation cannot race a heartbeat.
            con.execute("BEGIN IMMEDIATE")
            if not con.execute("SELECT 1 FROM access_keys WHERE id=? AND revoked_at IS NULL", (key_id,)).fetchone():
                return False
            con.execute("""INSERT INTO clients VALUES (?,?,?,?,1)
                ON CONFLICT(id,key_id) DO UPDATE SET ip=excluded.ip,last_seen=excluded.last_seen,connected=1""",
                        (client_id, key_id, ip, time.time()))
            return True

    def disconnect(self, key_id, client_id):
        with self.store.connect() as con:
            con.execute("UPDATE clients SET connected=0 WHERE id=? AND key_id=?", (client_id, key_id))

    def clients(self):
        with self.store.connect() as con:
            rows = con.execute("""SELECT c.*, k.name, k.prefix, k.revoked_at FROM clients c
                JOIN access_keys k ON k.id=c.key_id ORDER BY c.last_seen DESC""").fetchall()
        return [{"id": r["id"], "key_id": r["key_id"], "name": r["name"], "prefix": r["prefix"],
                 "ip": r["ip"], "last_seen": r["last_seen"],
                 "online": bool(r["connected"] and r["revoked_at"] is None and time.time() - r["last_seen"] < 60)} for r in rows]
