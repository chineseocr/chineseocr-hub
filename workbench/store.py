"""Single-host SQLite store. Each operation owns a connection; versions are immutable."""

import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def uid():
    """Create an opaque storage identifier; never derive file paths or database keys from user filenames."""
    return uuid.uuid4().hex


class Store:
    def __init__(self, directory):
        self.quota_policy = None
        self.secret_codec = None
        self.provider_policy = None
        self.model_slots = None
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self.path = self.directory / "workbench.sqlite3"
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS templates(id TEXT, version INTEGER, body TEXT NOT NULL,
                created REAL, PRIMARY KEY(id,version));
            CREATE TABLE IF NOT EXISTS applications(
                id TEXT PRIMARY KEY,code TEXT UNIQUE NOT NULL,category TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',tags TEXT NOT NULL DEFAULT '[]',
                owner TEXT NOT NULL DEFAULT '',active_release TEXT,disabled INTEGER NOT NULL DEFAULT 0,
                source_preset TEXT UNIQUE,created REAL,updated REAL);
            CREATE TABLE IF NOT EXISTS application_releases(
                id TEXT PRIMARY KEY,application_id TEXT NOT NULL,body TEXT NOT NULL,created REAL);
            CREATE INDEX IF NOT EXISTS app_release_parent ON application_releases(application_id,created);
            CREATE TABLE IF NOT EXISTS public_uploads(file_id TEXT PRIMARY KEY,principal TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS providers(id TEXT PRIMARY KEY, body TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS dictionaries(id TEXT, version INTEGER, body TEXT NOT NULL,
                created REAL, PRIMARY KEY(id,version));
            CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY, body TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, status TEXT, payload TEXT,
                results TEXT DEFAULT '[]', error TEXT, created REAL, updated REAL,
                idem TEXT UNIQUE, fingerprint TEXT, cancel INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS datasets(id TEXT PRIMARY KEY, name TEXT, samples TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, body TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_admissions(
                job_id TEXT PRIMARY KEY,period TEXT NOT NULL,pages INTEGER NOT NULL,created REAL);
            CREATE TABLE IF NOT EXISTS archived_records(collection TEXT, record_id TEXT,
                reason TEXT NOT NULL, PRIMARY KEY(collection, record_id));
            """)
        # Existing templates receive stable catalog IDs without rewriting versions.
        # A migration is additive; installed/user-edited prompts are never replaced.
        with self.connect() as db:
            db.execute("""INSERT OR IGNORE INTO applications(id,code,category,created,updated)
                SELECT id,'app-' || id,'general',MIN(created),MAX(created)
                FROM templates GROUP BY id""")
        os.chmod(self.path, 0o600)
        token_file = self.directory / "access-token"
        if not token_file.exists():
            fd = os.open(str(token_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as out:
                out.write(secrets.token_urlsafe(32))
        self.token = os.environ.get("WORKBENCH_TOKEN") or token_file.read_text().strip()
        if len(self.token) < 16:
            raise ValueError("WORKBENCH_TOKEN 至少需要 16 个字符")

    @contextmanager
    def connect(self):
        """Open a per-operation connection, commit successful work and always close it.

        A SQLite connection context alone commits but does not close; explicit cleanup prevents descriptor leaks."""
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def one(self, sql, params=()):
        """Execute a parameterized read and return a detached row, or None when no row exists."""
        with self.connect() as db:
            row = db.execute(sql, params).fetchone()
        return dict(row) if row else None

    def all(self, sql, params=()):
        """Fetch detached rows using a short-lived connection; callers cannot retain live database cursors."""
        with self.connect() as db:
            return [dict(r) for r in db.execute(sql, params).fetchall()]

    def execute(self, sql, params=()):
        """Run one parameterized mutation atomically and return its affected-row count."""
        with self.connect() as db:
            return db.execute(sql, params).rowcount

    def put(self, table, body):
        """Insert immutable JSON into an explicitly allowed collection.

        Only table names from the internal allowlist may enter SQL; user values remain bound parameters."""
        assert table in {"providers", "files", "runs"}
        key = uid()
        body = dict(body)
        if table == "providers" and self.secret_codec and body.get("api_key"):
            body["api_key_encrypted"] = self.secret_codec.encrypt(body.pop("api_key").encode()).decode()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if table == "files" and self.quota_policy:
                used = sum(
                    json.loads(row[0]).get("stored_bytes", json.loads(row[0])["size"])
                    for row in db.execute("SELECT body FROM files")
                )
                if used + body.get("stored_bytes", body["size"]) > self.quota_policy()["storage_bytes"]:
                    raise ValueError("团队存储额度不足")
            db.execute(
                f"INSERT INTO {table}(id,body,created) VALUES(?,?,?)", (key, json.dumps(body), time.time())
            )
        return key

    def get(self, table, key):
        """Retrieve immutable JSON by opaque ID without exposing SQL cursors to the caller."""
        assert table in {"providers", "files", "runs"}
        row = self.one(f"SELECT body FROM {table} WHERE id=?", (key,))
        if not row:
            return None
        body = json.loads(row["body"])
        if table == "providers" and "api_key_encrypted" in body:
            if not self.secret_codec:
                raise ValueError("模型密钥需要原始平台加密密钥")
            body["api_key"] = self.secret_codec.decrypt(body["api_key_encrypted"].encode()).decode()
        return body

    def reserve_job_usage(self, db, job_id, encoded, created):
        """Reserve submitted pages in the same transaction as a job, including retries.

        Admission is the quota unit, not a claim of successful OCR or provider billing.
        Failed/cancelled submissions retain their admission; actual results and usage
        are reported separately. Rollbacks cannot leave an orphan reservation.
        """
        if not self.quota_policy:
            return
        period = datetime.fromtimestamp(created, timezone.utc).strftime("%Y-%m")
        payload = json.loads(encoded)
        pages = sum(len(unit["pages"]) for unit in payload["units"])
        used = db.execute(
            "SELECT COALESCE(SUM(pages),0) FROM usage_admissions WHERE period=?", (period,)
        ).fetchone()[0]
        if used + pages > self.quota_policy()["monthly_pages"]:
            raise ValueError("团队本月处理页额度不足")
        db.execute("INSERT INTO usage_admissions VALUES(?,?,?,?)", (job_id, period, pages, created))

    def template(self, key, version=None):
        """Resolve a specific immutable version, or the latest version when none is requested."""
        sql = "SELECT * FROM templates WHERE id=?"
        params = [key]
        if version is not None:
            sql += " AND version=?"
            params.append(version)
        row = self.one(sql + " ORDER BY version DESC LIMIT 1", params)
        return dict(id=key, version=row["version"], **json.loads(row["body"])) if row else None

    def save_template(self, key, body):
        """Append a version under a write transaction.

        BEGIN IMMEDIATE serializes version allocation so concurrent saves cannot overwrite each other."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            latest = db.execute("SELECT MAX(version) FROM templates WHERE id=?", (key,)).fetchone()[0] or 0
            version = latest + 1
            db.execute("INSERT INTO templates VALUES(?,?,?,?)", (key, version, json.dumps(body), time.time()))
            db.execute(
                "INSERT OR IGNORE INTO applications(id,code,category,created,updated) VALUES(?,?,?,?,?)",
                (key, "app-" + key, "general", time.time(), time.time()),
            )
            db.execute("UPDATE applications SET updated=? WHERE id=?", (time.time(), key))
        # Another writer may commit after this transaction. Return this caller's
        # exact version, rather than accidentally returning that newer draft.
        return self.template(key, version)

    def job(self, key):
        """Decode persisted job snapshots/results; this is an internal record, not an authorization check."""
        row = self.one("SELECT * FROM jobs WHERE id=?", (key,))
        if row:
            row["payload"] = json.loads(row["payload"])
            row["results"] = json.loads(row["results"])
        return row

    def dictionary(self, key, version=None):
        """Return a saved dictionary version without following live references from templates."""
        sql = "SELECT version,body FROM dictionaries WHERE id=?"
        params = [key]
        if version is not None:
            sql += " AND version=?"
            params.append(version)
        row = self.one(sql + " ORDER BY version DESC LIMIT 1", params)
        return {"id": key, "version": row["version"], **json.loads(row["body"])} if row else None

    def save_dictionary(self, key, body):
        """Append atomically; editing a library dictionary never mutates a saved field enum."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            latest = db.execute("SELECT MAX(version) FROM dictionaries WHERE id=?", (key,)).fetchone()[0] or 0
            version = latest + 1
            db.execute(
                "INSERT INTO dictionaries VALUES(?,?,?,?)", (key, version, json.dumps(body), time.time())
            )
        return self.dictionary(key, version)

    def metadata(self, key, value=None):
        """Read/write workspace preferences, never credentials or extraction snapshots."""
        if value is not None:
            self.execute(
                "INSERT INTO metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        row = self.one("SELECT value FROM metadata WHERE key=?", (key,))
        return row["value"] if row else None

    def archived(self, collection):
        """Return IDs retired by a non-destructive migration; underlying rows remain intact."""
        return {
            r["record_id"]
            for r in self.all("SELECT record_id FROM archived_records WHERE collection=?", (collection,))
        }
