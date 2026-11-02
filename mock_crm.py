"""Loopback-only test CRM; independently commits operations before responding."""
import hashlib
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class MockCRM:
    def __init__(self, path, fail_after_apply_once=False, always_status=None):
        self.path = path
        self.fail_after_apply_once = fail_after_apply_once
        self.always_status = always_status
        with sqlite3.connect(path) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY, version INTEGER, owner TEXT);
                CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            """)

    def apply(self, operation, payload):
        if self.always_status:
            return self.always_status, {"error": "simulated failure"}
        encoded = json.dumps(payload, sort_keys=True)
        if operation != hashlib.sha256(encoded.encode()).hexdigest():
            return 400, {"error": "Invalid operation key"}
        if (not isinstance(payload.get("account_id"), str) or not isinstance(payload.get("owner"), str)
                or type(payload.get("version")) is not int or payload["version"] < 1):
            return 400, {"error": "Invalid payload"}
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT payload FROM operations WHERE id=?", (operation,)).fetchone()
            if old:
                return 200, {"operation_id": operation, "applied": True, "replayed": True}
            current = db.execute("SELECT version FROM accounts WHERE id=?", (payload["account_id"],)).fetchone()
            if (current[0] if current else 0) != payload["version"]-1:
                return 409, {"error": "Remote version conflict"}
            db.execute("INSERT OR REPLACE INTO accounts VALUES (?,?,?)",
                       (payload["account_id"], payload["version"], payload["owner"]))
            db.execute("INSERT INTO operations VALUES (?,?)", (operation, encoded))
        if self.fail_after_apply_once:
            self.fail_after_apply_once = False
            return 503, {"error": "Simulated response failure after durable application"}
        return 200, {"operation_id": operation, "applied": True, "replayed": False}

    def __enter__(self):
        remote = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                if self.path != "/updates":
                    self.send_error(404)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 16384:
                        self.send_error(413)
                        return
                    payload = json.loads(self.rfile.read(size))
                    if not isinstance(payload, dict):
                        raise ValueError("object required")
                    status, body = remote.apply(self.headers.get("Idempotency-Key"), payload)
                except (ValueError, KeyError, TypeError):
                    status, body = 400, {"error": "Malformed request"}
                encoded = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def applied_count(self):
        with sqlite3.connect(self.path) as db:
            return db.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
