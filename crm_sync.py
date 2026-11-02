"""Transactional outbox with leased delivery and a replay-safe mock CRM."""
import argparse
import hashlib
import json
import sqlite3
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


def connect(path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY, version INTEGER, owner TEXT);
        CREATE TABLE IF NOT EXISTS outbox (
            id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL,
            account_id TEXT NOT NULL, version INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0,
            lease_until REAL, token TEXT, error_code TEXT);
        CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY, operation_id TEXT, outcome TEXT,
            http_status INTEGER, at REAL);
    """)
    return db


def stage(db, account_id, owner, expected_version=0):
    """Commit local intent and an outbound operation together, with optimistic versioning."""
    if not account_id or not owner:
        raise ValueError("Account and owner required")
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT version FROM accounts WHERE id=?", (account_id,)).fetchone()
        current = row[0] if row else 0
        if current != expected_version:
            raise ValueError("Local version conflict")
        payload = json.dumps(dict(account_id=account_id, owner=owner, version=current+1), sort_keys=True)
        operation_id = hashlib.sha256(payload.encode()).hexdigest()
        db.execute("INSERT OR REPLACE INTO accounts VALUES (?,?,?)", (account_id, current+1, owner))
        db.execute("INSERT INTO outbox(id,payload,status,account_id,version) VALUES (?,?,'pending',?,?)",
                   (operation_id, payload, account_id, current+1))
        db.commit()
        return operation_id
    except BaseException:
        db.rollback()
        raise


def deliver_one(db, base_url, now=None, timeout=2, max_attempts=5):
    timestamp = time.time() if now is None else now
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute("""
            SELECT * FROM outbox AS candidate
            WHERE ((status IN ('pending','retry') AND next_at<=?)
              OR (status='sending' AND lease_until<=?))
            AND NOT EXISTS (
                SELECT 1 FROM outbox AS predecessor
                WHERE predecessor.account_id=candidate.account_id
                  AND predecessor.version<candidate.version
                  AND predecessor.status!='delivered')
            ORDER BY rowid LIMIT 1
        """, (timestamp, timestamp)).fetchone()
        if not row:
            db.commit()
            return None
        token = str(uuid.uuid4())
        db.execute("UPDATE outbox SET status='sending',token=?,lease_until=?,attempts=attempts+1 WHERE id=?",
                   (token, timestamp+max(30, timeout*2), row["id"]))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    request = urllib.request.Request(base_url.rstrip("/")+"/updates", data=row["payload"].encode(),
                                     headers={"Content-Type": "application/json", "Idempotency-Key": row["id"]},
                                     method="POST")
    status = None
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            receipt = json.loads(response.read())
            # A generic HTTP 200 is insufficient to establish this operation succeeded.
            success = receipt.get("operation_id") == row["id"] and receipt.get("applied") is True
            outcome = "delivered" if success else "retry"
    except urllib.error.HTTPError as exc:
        status = exc.code
        outcome = "retry" if status in (408, 429) or status >= 500 else "failed"
        exc.close()
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        outcome = "retry"
    if outcome == "retry" and row["attempts"]+1 >= max_attempts:
        outcome = "needs_reconciliation"
    with db:
        updated = db.execute("""UPDATE outbox SET status=?,next_at=?,error_code=?,lease_until=NULL
                                WHERE id=? AND token=? AND status='sending'""",
                             (outcome, timestamp+min(300, 2**min(row["attempts"]+1, 8)),
                              None if outcome == "delivered" else str(status or "transport"), row["id"], token))
        if updated.rowcount != 1:
            return {"operation_id": row["id"], "status": "claim_replaced"}
        db.execute("INSERT INTO audit(operation_id,outcome,http_status,at) VALUES (?,?,?,?)",
                   (row["id"], outcome, status, timestamp))
    return {"operation_id": row["id"], "status": outcome}


def main():
    from mock_crm import MockCRM
    parser = argparse.ArgumentParser(description="Run the lost-response recovery demonstration")
    parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="crm-demo-") as temp:
        db = connect(str(Path(temp)/"local.db"))
        try:
            with MockCRM(str(Path(temp)/"remote.db"), fail_after_apply_once=True) as remote:
                operation = stage(db, "DEMO-ACCOUNT-1", "demo-team-north")
                first = deliver_one(db, remote.url, now=0)
                second = deliver_one(db, remote.url, now=3)
                print(json.dumps({"first_attempt": first, "recovery": second,
                                  "remote_operations_applied": remote.applied_count(),
                                  "operation_id": operation}, indent=2))
        finally:
            db.close()


if __name__ == "__main__":
    main()
