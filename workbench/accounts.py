"""SaaS control plane: identities, memberships, sessions and audit records.

Business documents live in separate tenant Stores. This database holds only account
and access metadata; no document JSON, provider keys or model prompts belong here.
"""

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException

from .store import uid

ROLES = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}
DEFAULT_LIMITS = {"monthly_pages": 1000, "storage_bytes": 256 * 1024 * 1024, "members": 10}
HASH_SLOTS = threading.BoundedSemaphore(2)
SESSION_SECONDS = 12 * 60 * 60


def token_hash(value):
    """Opaque random credentials are stored as SHA-256 digests, never plaintext."""
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt=None):
    """Use the OWASP scrypt N=2^15,r=8,p=3 profile with bounded concurrent hashing."""
    if not HASH_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "登录服务繁忙，请稍后重试")
    try:
        salt = salt or secrets.token_bytes(16)
        derived = hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=3, maxmem=64 * 1024 * 1024)
        return "scrypt$32768$8$3$" + salt.hex() + "$" + derived.hex()
    finally:
        HASH_SLOTS.release()


def password_matches(password, encoded):
    """Apply equal work for unknown users; compare derived hashes in constant time."""
    try:
        parts = encoded.split("$")
        salt = bytes.fromhex(parts[4])
        return hmac.compare_digest(password_hash(password, salt), encoded)
    except (ValueError, IndexError):
        return False


class Accounts:
    """Single-process SQLite control plane with transactional membership changes."""

    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self.path = self.directory / "platform.sqlite3"
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users(
                id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,password TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,disabled INTEGER NOT NULL DEFAULT 0,created REAL);
            CREATE TABLE IF NOT EXISTS tenants(
                id TEXT PRIMARY KEY,name TEXT NOT NULL,plan TEXT NOT NULL,limits TEXT NOT NULL,created REAL);
            CREATE TABLE IF NOT EXISTS memberships(
                tenant_id TEXT,user_id TEXT,role TEXT NOT NULL,created REAL,
                PRIMARY KEY(tenant_id,user_id));
            CREATE TABLE IF NOT EXISTS sessions(
                hash TEXT PRIMARY KEY,user_id TEXT,tenant_id TEXT,expires REAL,created REAL);
            CREATE TABLE IF NOT EXISTS invitations(
                id TEXT PRIMARY KEY,hash TEXT UNIQUE,tenant_id TEXT,role TEXT,expires REAL,
                created REAL,accepted_by TEXT,revoked INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS api_keys(
                id TEXT PRIMARY KEY,hash TEXT UNIQUE,prefix TEXT,name TEXT,user_id TEXT,
                tenant_id TEXT,role TEXT,expires REAL,created REAL,revoked INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS api_key_scopes(key_id TEXT PRIMARY KEY,applications TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(
                id INTEGER PRIMARY KEY AUTOINCREMENT,created REAL,tenant_id TEXT,
                user_id TEXT,event TEXT,resource TEXT,metadata TEXT);
            CREATE TABLE IF NOT EXISTS login_attempts(key TEXT,created REAL);
            CREATE INDEX IF NOT EXISTS login_attempt_time ON login_attempts(created);
            """)
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        """Commit successful operations and always release the SQLite connection."""
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def one(self, query, args=()):
        with self.connect() as db:
            row = db.execute(query, args).fetchone()
        return dict(row) if row else None

    def all(self, query, args=()):
        with self.connect() as db:
            return [dict(row) for row in db.execute(query, args)]

    def audit(self, user_id, tenant_id, event, resource="", metadata=None):
        """Record identifiers and bounded operational metadata, never request bodies."""
        with self.connect() as db:
            db.execute(
                "INSERT INTO audit(created,tenant_id,user_id,event,resource,metadata) VALUES(?,?,?,?,?,?)",
                (time.time(), tenant_id, user_id, event, resource[:200], json.dumps(metadata or {})),
            )

    def bootstrap(self):
        """Create one administrator with an unguessable password in a private local file."""
        if self.one("SELECT id FROM users LIMIT 1"):
            return
        password = secrets.token_urlsafe(24)
        encoded = password_hash(password)
        user_id = uid()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                return
            # The platform lock serializes bootstrap. Credentials are never printed.
            credential_file = self.directory / "initial-admin.json"
            fd = os.open(credential_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as out:
                json.dump({"username": "admin", "password": password}, out)
            db.execute(
                "INSERT INTO users VALUES(?,?,?,?,?,?)", (user_id, "admin", encoded, 1, 0, time.time())
            )
        self.new_tenant(user_id, "我的团队")

    def user(self, user_id):
        """Return public account fields only."""
        return self.one("SELECT id,username,is_admin,disabled FROM users WHERE id=?", (user_id,))

    def new_user(self, username, password):
        """Register an account; callers enforce invitation/registration policy first."""
        encoded, key = password_hash(password), uid()
        try:
            with self.connect() as db:
                db.execute(
                    "INSERT INTO users VALUES(?,?,?,?,?,?)",
                    (key, username.casefold(), encoded, 0, 0, time.time()),
                )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "账户名称已被使用") from None
        return self.user(key)

    def throttle(self, remote, username):
        """Persist login throttling across restarts without recording raw IP addresses."""
        now = time.time()
        keys = [token_hash("ip:" + remote), token_hash("account:" + username.casefold())]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM login_attempts WHERE created<?", (now - 300,))
            for key in keys:
                if (
                    db.execute(
                        "SELECT COUNT(*) FROM login_attempts WHERE key=? AND created>?", (key, now - 60)
                    ).fetchone()[0]
                    >= 15
                ):
                    raise HTTPException(429, "登录尝试过于频繁，请稍后重试")
            db.executemany("INSERT INTO login_attempts VALUES(?,?)", [(key, now) for key in keys])

    def login(self, username, password, remote):
        self.throttle(remote, username)
        user = self.one("SELECT * FROM users WHERE username=?", (username.casefold(),))
        encoded = user["password"] if user else "scrypt$32768$8$3$" + "00" * 16 + "$" + "00" * 64
        matches = password_matches(password, encoded)
        if not user or user["disabled"] or not matches:
            raise HTTPException(401, "账户或密码不正确")
        token = secrets.token_urlsafe(32)
        membership = self.one(
            "SELECT tenant_id FROM memberships WHERE user_id=? ORDER BY created LIMIT 1", (user["id"],)
        )
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?,?,?)",
                (
                    token_hash(token),
                    user["id"],
                    membership["tenant_id"] if membership else None,
                    time.time() + SESSION_SECONDS,
                    time.time(),
                ),
            )
        self.audit(user["id"], None, "account.login")
        return token

    def identify(self, request, require_tenant=True, session_only=False):
        """Bind identity and tenant from server records; API keys cannot change tenants."""
        bearer = request.headers.get("authorization", "")
        api_key = bearer.startswith("Bearer ")
        token = bearer[7:] if api_key else request.cookies.get("studio_session", "")
        record = (
            self.one(
                "SELECT * FROM api_keys WHERE hash=? AND revoked=0"
                if api_key
                else "SELECT * FROM sessions WHERE hash=?",
                (token_hash(token),),
            )
            if token
            else None
        )
        if not record or record["expires"] <= time.time():
            raise HTTPException(401, "请先登录账户")
        user = self.user(record["user_id"])
        if not user or user["disabled"]:
            raise HTTPException(401, "账户已停用")
        if api_key and session_only:
            raise HTTPException(403, "此操作需要账户会话")
        tenant_id = request.headers.get("x-tenant-id") or record["tenant_id"]
        if api_key and tenant_id != record["tenant_id"]:
            raise HTTPException(403, "接口密钥不能切换团队")
        membership = self.one(
            "SELECT role FROM memberships WHERE tenant_id=? AND user_id=?", (tenant_id, user["id"])
        )
        if require_tenant and not membership:
            raise HTTPException(403, "你不是该团队的成员")
        role = membership["role"] if membership else None
        if api_key:
            if not membership:
                raise HTTPException(403, "接口密钥所属成员已移除")
            role = min((role, record["role"]), key=lambda item: ROLES[item])
        return {
            "user": user,
            "tenant_id": tenant_id,
            "role": role,
            "api_key_id": record["id"] if api_key else None,
            "session_hash": None if api_key else record["hash"],
            "application_ids": (
                json.loads(scope["applications"])
                if api_key
                and (
                    scope := self.one(
                        "SELECT applications FROM api_key_scopes WHERE key_id=?", (record["id"],)
                    )
                )
                else None
            ),
        }

    def memberships(self, user_id):
        return self.all(
            "SELECT t.id,t.name,t.plan,m.role FROM tenants t JOIN memberships m ON m.tenant_id=t.id WHERE m.user_id=? ORDER BY t.created",
            (user_id,),
        )

    def new_tenant(self, user_id, name):
        key, now = uid(), time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if (
                db.execute(
                    "SELECT COUNT(*) FROM memberships WHERE user_id=? AND role='owner'", (user_id,)
                ).fetchone()[0]
                >= 10
            ):
                raise HTTPException(422, "每个账户最多创建 10 个团队")
            if db.execute("SELECT COUNT(*) FROM tenants").fetchone()[0] >= 100:
                raise HTTPException(422, "平台团队容量已满，请联系管理员")
            db.execute(
                "INSERT INTO tenants VALUES(?,?,?,?,?)",
                (key, name, "starter", json.dumps(DEFAULT_LIMITS), now),
            )
            db.execute("INSERT INTO memberships VALUES(?,?,?,?)", (key, user_id, "owner", now))
        self.audit(user_id, key, "tenant.created", key)
        return self.tenant(key)

    def tenant(self, tenant_id):
        row = self.one("SELECT * FROM tenants WHERE id=?", (tenant_id,))
        if not row:
            raise HTTPException(404, "团队不存在")
        row["limits"] = json.loads(row["limits"])
        return row

    def invite(self, identity, role):
        """Reserve capacity for one pending invite; tokens are displayed only once."""
        key, token, now = uid(), secrets.token_urlsafe(32), time.time()
        tenant_id = identity["tenant_id"]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            limit = json.loads(
                db.execute("SELECT limits FROM tenants WHERE id=?", (tenant_id,)).fetchone()[0]
            )["members"]
            members = db.execute(
                "SELECT COUNT(*) FROM memberships WHERE tenant_id=?", (tenant_id,)
            ).fetchone()[0]
            pending = db.execute(
                "SELECT COUNT(*) FROM invitations WHERE tenant_id=? AND revoked=0 AND accepted_by IS NULL AND expires>?",
                (tenant_id, now),
            ).fetchone()[0]
            if members + pending >= limit:
                raise HTTPException(422, "团队成员额度不足")
            db.execute(
                "INSERT INTO invitations VALUES(?,?,?,?,?,?,?,?)",
                (key, token_hash(token), tenant_id, role, now + 7 * 86400, now, None, 0),
            )
        self.audit(identity["user"]["id"], tenant_id, "invitation.created", key, {"role": role})
        return {"id": key, "token": token, "expires": now + 7 * 86400}

    def invitation(self, token):
        row = self.one(
            "SELECT * FROM invitations WHERE hash=? AND revoked=0 AND accepted_by IS NULL AND expires>?",
            (token_hash(token), time.time()),
        )
        if not row:
            raise HTTPException(404, "邀请已失效或已使用")
        return row

    def accept(self, token, user_id):
        """Consume once under a transaction; a used invite cannot add another account."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            invite = db.execute(
                "SELECT * FROM invitations WHERE hash=? AND revoked=0 AND accepted_by IS NULL AND expires>?",
                (token_hash(token), time.time()),
            ).fetchone()
            if not invite:
                raise HTTPException(404, "邀请已失效或已使用")
            tenant_id = invite["tenant_id"]
            limit = json.loads(
                db.execute("SELECT limits FROM tenants WHERE id=?", (tenant_id,)).fetchone()[0]
            )["members"]
            if (
                db.execute("SELECT COUNT(*) FROM memberships WHERE tenant_id=?", (tenant_id,)).fetchone()[0]
                >= limit
            ):
                raise HTTPException(422, "团队成员额度不足")
            db.execute(
                "INSERT OR IGNORE INTO memberships VALUES(?,?,?,?)",
                (tenant_id, user_id, invite["role"], time.time()),
            )
            db.execute("UPDATE invitations SET accepted_by=? WHERE id=?", (user_id, invite["id"]))
        self.audit(user_id, tenant_id, "invitation.accepted", invite["id"])
        return self.tenant(tenant_id)
