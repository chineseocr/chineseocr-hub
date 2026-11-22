"""Account-based multi-tenant hosting around the same extraction application.

The gateway authenticates before choosing a workspace. Tenant IDs come from verified
memberships, never filenames. Each tenant has its own database, files and worker;
sessions, memberships, API keys and audit records live in the control plane.
"""

import asyncio
import fcntl
import json
import os
import re
import secrets
import threading
import time
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import PRODUCT_NAME, __version__
from .accounts import ROLES, Accounts, password_hash, password_matches, token_hash
from .app import STATIC, create_app
from .domain import ProviderInput
from .localization import translate
from .store import uid

CENTRAL = (
    "/api/account",
    "/api/tenants",
    "/api/invitations",
    "/api/api-keys",
    "/api/usage",
    "/api/audit",
    "/api/admin",
)


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("username")
    @classmethod
    def valid_name(cls, value):
        value = value.strip().casefold()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.@+-]{2,119}", value):
            raise ValueError("账户名称请使用至少 3 个字母、数字或常用邮箱字符")
        return value


class Registration(Credentials):
    password: str = Field(min_length=12, max_length=256)
    invitation: str = Field(default="", max_length=200)


class Named(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("名称不能为空")
        return value.strip()


class InviteInput(BaseModel):
    role: Literal["admin", "member", "viewer"] = "member"


class TokenInput(BaseModel):
    token: str = Field(min_length=1, max_length=200)


class ActiveTenant(BaseModel):
    tenant_id: str = Field(max_length=100)


class KeyInput(Named):
    role: Literal["member", "viewer"] = "member"
    days: int = Field(default=30, ge=1, le=90)
    application_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)


class LimitsInput(BaseModel):
    plan: Literal["starter", "pro", "custom"] = "custom"
    monthly_pages: int = Field(ge=1, le=10_000_000)
    storage_bytes: int = Field(ge=1024, le=1024**4)
    members: int = Field(ge=1, le=1000)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class AccountStatus(BaseModel):
    disabled: bool


def require_role(identity, minimum):
    """A platform administrator does not implicitly gain membership in every tenant."""
    if not identity["role"] or ROLES[identity["role"]] < ROLES[minimum]:
        raise HTTPException(403, "当前角色没有此操作权限")


def usage_report(store):
    """Separate admitted pages from actual model usage, without estimating absent tokens."""
    now = datetime.now(timezone.utc)
    period = now.strftime("%Y-%m")
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc).timestamp()
    pages = store.one(
        "SELECT COALESCE(SUM(pages),0) AS value FROM usage_admissions WHERE period=?", (period,)
    )["value"]
    storage = sum(
        json.loads(row["body"]).get("stored_bytes", json.loads(row["body"])["size"])
        for row in store.all("SELECT body FROM files")
    )
    result = {
        "period": period,
        "admitted_pages": pages,
        "storage_bytes": storage,
        "completed_units": 0,
        "input_pages": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "usage_reported_units": 0,
        "jobs": 0,
    }
    for job in store.all("SELECT results FROM jobs WHERE created>=?", (start,)):
        result["jobs"] += 1
        for unit in json.loads(job["results"]):
            if unit.get("status") in {"succeeded", "partial", "invalid", "failed"}:
                result["completed_units"] += 1
            result["input_pages"] += len(unit["pages"])
            usage = unit.get("usage")
            if isinstance(usage, dict):
                result["usage_reported_units"] += 1
                for key in ("prompt_tokens", "completion_tokens"):
                    value = usage.get(key)
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        result[key] += value
    return result


class TenantRuntime:
    """Own child lifespans under the platform lock; all workers close before shutdown."""

    def __init__(self, accounts, codec, stack, allowed_bases, start_worker):
        self.accounts, self.codec, self.stack = accounts, codec, stack
        self.allowed_bases, self.start_worker = allowed_bases, start_worker
        self.apps, self.lock = {}, asyncio.Lock()
        self.model_slots = threading.BoundedSemaphore(4)

    def model_policy(self, provider):
        # Hosted tenants cannot choose arbitrary network destinations. Only exact
        # administrator-controlled API bases are accepted, including scheme/port/path.
        if provider.base_url not in self.allowed_bases:
            raise HTTPException(403, "模型服务地址不在平台允许列表，请联系管理员")

    async def get(self, tenant_id):
        async with self.lock:
            if tenant_id in self.apps:
                return self.apps[tenant_id]
            self.accounts.tenant(tenant_id)  # Reject unknown IDs before deriving a path.

            def configure(store):
                store.secret_codec = self.codec
                store.quota_policy = lambda: self.accounts.tenant(tenant_id)["limits"]
                store.provider_policy = lambda provider: provider["base_url"] in self.allowed_bases
                store.model_slots = self.model_slots

            def authorize(request, database):
                identity = request.scope.get("state", {}).get("saas_identity")
                if not identity or identity["tenant_id"] != tenant_id:
                    raise HTTPException(401, "请先登录账户")
                path, method = request.url.path, request.method
                if identity.get("application_ids") is not None and not path.startswith("/v1/"):
                    raise HTTPException(403, "应用接口密钥仅允许调用公开识别 API")
                if method not in {"GET", "HEAD"}:
                    if path.startswith("/api/providers"):
                        require_role(identity, "admin")
                        if identity["api_key_id"]:
                            raise HTTPException(403, "模型配置需要管理员账户会话")
                    elif path != "/api/prompt-preview":
                        require_role(identity, "member")
                return database

            child = create_app(
                self.accounts.directory / "tenants" / tenant_id,
                start_worker=self.start_worker,
                access_control=authorize,
                configure_store=configure,
                model_policy=self.model_policy,
                model_environment=False,
            )
            await self.stack.enter_async_context(child.router.lifespan_context(child))
            self.apps[tenant_id] = child
            return child


class TenantGateway:
    """ASGI dispatch with membership checks before any business route or file is read."""

    def __init__(self, platform):
        self.platform, self.state = platform, platform.state

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or not (path.startswith("/api/") or path.startswith("/v1/"))
            or path in {"/api/session", "/api/deployment"}
            or path.startswith(CENTRAL)
        ):
            return await self.platform(scope, receive, send)
        request = Request(scope, receive)
        try:
            check_write_origin(request)
            identity = self.state.accounts.identify(request)
            child = await self.state.runtime.get(identity["tenant_id"])
            tenant = self.state.accounts.tenant(identity["tenant_id"])
            forwarded = dict(scope)
            forwarded["state"] = dict(scope.get("state", {}))
            forwarded["state"]["saas_identity"] = identity
            forwarded["state"]["saas_public"] = {
                "user": identity["user"],
                "tenant": tenant,
                "role": identity["role"],
                "api_key": bool(identity["api_key_id"]),
            }
            status = 500

            async def capture(message):
                nonlocal status
                if message["type"] == "http.response.start":
                    status = message["status"]
                await send(message)

            await child(forwarded, receive, capture)
            if request.method not in {"GET", "HEAD"}:
                self.state.accounts.audit(
                    identity["user"]["id"],
                    tenant["id"],
                    "workspace." + request.method.lower(),
                    path,
                    {"status": status, "api_key": identity["api_key_id"]},
                )
        except HTTPException as exc:
            response = JSONResponse(
                {"detail": translate(str(exc.detail), request.headers.get("accept-language", "zh"))},
                status_code=exc.status_code,
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)


def check_write_origin(request):
    """A session-authenticated write needs the same-origin marker and matching Origin."""
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "不允许跨源写入")
    if (
        not request.headers.get("authorization", "").startswith("Bearer ")
        and request.headers.get("x-workbench") != "1"
    ):
        raise HTTPException(403, "缺少同源请求标记")


def create_saas_app(data_dir=None, start_worker=True, registration=None, allowed_model_bases=None):
    """Create an invite-first hosted application; never import standalone workspace data."""
    directory = Path(data_dir or os.environ.get("WORKBENCH_DATA_DIR", "data")) / "saas"
    registration = (
        registration if registration is not None else os.environ.get("WORKBENCH_ALLOW_REGISTRATION") == "1"
    )
    allowed = allowed_model_bases
    if allowed is None:
        allowed = [
            value.strip()
            for value in os.environ.get("WORKBENCH_ALLOWED_MODEL_BASES", "").split(",")
            if value.strip()
        ]
    allowed = {ProviderInput(name="Allowed service", base_url=value).base_url for value in allowed}

    @asynccontextmanager
    async def lifespan(app):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        lock = (directory / "platform.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            raise RuntimeError("在线平台目录已被其他进程占用") from None
        try:
            key_file = directory / "provider-encryption.key"
            if not key_file.exists():
                if (directory / "platform.sqlite3").exists():
                    raise RuntimeError("缺少原始平台加密密钥，请从备份恢复")
                fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as out:
                    out.write(Fernet.generate_key())
            codec = Fernet(key_file.read_bytes().strip())
            app.state.accounts = Accounts(directory)
            app.state.accounts.bootstrap()
            async with AsyncExitStack() as stack:
                app.state.runtime = TenantRuntime(app.state.accounts, codec, stack, allowed, start_worker)
                # Resume every persisted queue even before its members open a browser.
                for tenant in app.state.accounts.all("SELECT id FROM tenants"):
                    await app.state.runtime.get(tenant["id"])
                yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()

    platform = FastAPI(
        title=PRODUCT_NAME,
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @platform.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return JSONResponse(
            {"detail": translate(str(exc.detail), request.headers.get("accept-language", "zh"))},
            status_code=exc.status_code,
        )

    @platform.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(
            {
                "detail": [
                    {
                        "loc": e["loc"],
                        "type": e["type"],
                        "msg": translate(e["msg"], request.headers.get("accept-language", "zh")),
                    }
                    for e in exc.errors()
                ]
            },
            status_code=422,
        )

    @platform.middleware("http")
    async def boundaries(request, call_next):
        try:
            check_write_origin(request)
            if request.method in {"POST", "PUT", "PATCH"}:
                size = request.headers.get("content-length", "")
                if not size.isdecimal() or int(size) > 256 * 1024:
                    raise HTTPException(413, "请求大小超过上限")
            response = await call_next(request)
        except HTTPException as exc:
            response = await http_error(request, exc)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        return response

    def account(request: Request):
        return platform.state.accounts.identify(request, require_tenant=False, session_only=True)

    def member(request: Request):
        return platform.state.accounts.identify(request, session_only=True)

    def administrator(identity=Depends(member)):
        require_role(identity, "admin")
        return identity

    def platform_admin(identity=Depends(account)):
        if not identity["user"]["is_admin"]:
            raise HTTPException(403, "仅平台管理员可执行此操作")
        return identity

    @platform.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @platform.get("/startup.js")
    def startup():
        return FileResponse(STATIC / "startup.js", media_type="text/javascript")

    @platform.get("/style.css")
    def styles():
        return FileResponse(STATIC / "style.css", media_type="text/css")

    platform.mount("/assets", StaticFiles(directory=STATIC), name="assets")

    @platform.get("/healthz")
    def health():
        return {"status": "ok", "version": __version__, "mode": "saas"}

    @platform.get("/api/deployment")
    def deployment():
        return {"mode": "saas", "registration": registration, "product": PRODUCT_NAME}

    @platform.post("/api/session")
    def login(body: Credentials, request: Request, response: Response):
        token = platform.state.accounts.login(
            body.username, body.password, request.client.host if request.client else ""
        )
        response.set_cookie(
            "studio_session",
            token,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
            max_age=43200,
        )
        return {"ok": True}

    @platform.delete("/api/session")
    def logout(response: Response, identity=Depends(account)):
        with platform.state.accounts.connect() as db:
            db.execute("DELETE FROM sessions WHERE hash=?", (identity["session_hash"],))
        response.delete_cookie("studio_session")
        return {"ok": True}

    @platform.get("/api/account/openapi.json")
    def control_schema(identity=Depends(account)):
        """Expose the control-plane contract only to a signed-in account."""
        return platform.openapi()

    @platform.get("/api/account")
    def me(identity=Depends(account)):
        # A removed membership must not strand an otherwise valid account. Only
        # this account metadata route repairs the session's default team; business
        # requests with an explicit foreign/stale tenant still fail authorization.
        accounts = platform.state.accounts
        memberships = accounts.memberships(identity["user"]["id"])
        if identity["tenant_id"] not in {row["id"] for row in memberships}:
            identity["tenant_id"] = memberships[0]["id"] if memberships else None
            with accounts.connect() as db:
                db.execute(
                    "UPDATE sessions SET tenant_id=? WHERE hash=?",
                    (identity["tenant_id"], identity["session_hash"]),
                )
        return {
            "user": identity["user"],
            "tenants": memberships,
            "active_tenant_id": identity["tenant_id"],
        }

    @platform.post("/api/account/register")
    def register(body: Registration, request: Request):
        accounts = platform.state.accounts
        accounts.throttle(request.client.host if request.client else "", body.username)
        if body.invitation:
            accounts.invitation(body.invitation)
        elif not registration:
            raise HTTPException(403, "当前平台仅允许通过邀请注册")
        user = accounts.new_user(body.username, body.password)
        try:
            if body.invitation:
                accounts.accept(body.invitation, user["id"])
            else:
                accounts.new_tenant(user["id"], body.username + " 的团队")
        except HTTPException:
            # An invite may be consumed between validation and account creation.
            # Roll back this fresh, unreturned account so its username can retry.
            with accounts.connect() as db:
                db.execute("DELETE FROM users WHERE id=?", (user["id"],))
            raise
        return {"ok": True}

    @platform.post("/api/account/active")
    def active(body: ActiveTenant, identity=Depends(account)):
        accounts = platform.state.accounts
        if not accounts.one(
            "SELECT role FROM memberships WHERE tenant_id=? AND user_id=?",
            (body.tenant_id, identity["user"]["id"]),
        ):
            raise HTTPException(403, "你不是该团队的成员")
        with accounts.connect() as db:
            db.execute(
                "UPDATE sessions SET tenant_id=? WHERE hash=?", (body.tenant_id, identity["session_hash"])
            )
        return {"ok": True}

    @platform.post("/api/account/password")
    def change_password(body: PasswordChange, response: Response, identity=Depends(account)):
        accounts = platform.state.accounts
        saved = accounts.one("SELECT password FROM users WHERE id=?", (identity["user"]["id"],))
        if not password_matches(body.current_password, saved["password"]):
            raise HTTPException(403, "当前密码不正确")
        encoded = password_hash(body.new_password)
        with accounts.connect() as db:
            db.execute("UPDATE users SET password=? WHERE id=?", (encoded, identity["user"]["id"]))
            db.execute("DELETE FROM sessions WHERE user_id=?", (identity["user"]["id"],))
            db.execute("UPDATE api_keys SET revoked=1 WHERE user_id=?", (identity["user"]["id"],))
        accounts.audit(identity["user"]["id"], None, "account.password_changed")
        response.delete_cookie("studio_session")
        return {"ok": True}

    @platform.post("/api/tenants")
    def new_tenant(body: Named, identity=Depends(account)):
        return platform.state.accounts.new_tenant(identity["user"]["id"], body.name)

    @platform.get("/api/tenants/members")
    def members(identity=Depends(member)):
        return platform.state.accounts.all(
            "SELECT u.id,u.username,m.role FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.tenant_id=? ORDER BY m.created",
            (identity["tenant_id"],),
        )

    @platform.post("/api/tenants/members/{user_id}/role")
    def set_role(user_id: str, body: InviteInput, identity=Depends(administrator)):
        accounts = platform.state.accounts
        target = accounts.one(
            "SELECT role FROM memberships WHERE tenant_id=? AND user_id=?", (identity["tenant_id"], user_id)
        )
        if not target:
            raise HTTPException(404, "成员不存在")
        if target["role"] == "owner" or (
            identity["role"] != "owner" and (body.role == "admin" or target["role"] == "admin")
        ):
            raise HTTPException(403, "仅团队所有者可管理管理员，所有者角色不能修改")
        with accounts.connect() as db:
            db.execute(
                "UPDATE memberships SET role=? WHERE tenant_id=? AND user_id=?",
                (body.role, identity["tenant_id"], user_id),
            )
        accounts.audit(
            identity["user"]["id"], identity["tenant_id"], "member.role_changed", user_id, {"role": body.role}
        )
        return {"ok": True}

    @platform.post("/api/tenants/members/{user_id}/remove")
    def remove_member(user_id: str, identity=Depends(administrator)):
        accounts = platform.state.accounts
        target = accounts.one(
            "SELECT role FROM memberships WHERE tenant_id=? AND user_id=?", (identity["tenant_id"], user_id)
        )
        if (
            not target
            or target["role"] == "owner"
            or (target["role"] == "admin" and identity["role"] != "owner")
        ):
            raise HTTPException(403, "不能移除此成员")
        with accounts.connect() as db:
            db.execute(
                "DELETE FROM memberships WHERE tenant_id=? AND user_id=?", (identity["tenant_id"], user_id)
            )
            db.execute(
                "UPDATE api_keys SET revoked=1 WHERE tenant_id=? AND user_id=?",
                (identity["tenant_id"], user_id),
            )
        accounts.audit(identity["user"]["id"], identity["tenant_id"], "member.removed", user_id)
        return {"ok": True}

    @platform.post("/api/invitations")
    def invite(body: InviteInput, identity=Depends(administrator)):
        if body.role == "admin" and identity["role"] != "owner":
            raise HTTPException(403, "仅团队所有者可邀请管理员")
        return platform.state.accounts.invite(identity, body.role)

    @platform.post("/api/invitations/accept")
    def accept(body: TokenInput, identity=Depends(account)):
        return platform.state.accounts.accept(body.token, identity["user"]["id"])

    @platform.get("/api/invitations")
    def invites(identity=Depends(administrator)):
        return platform.state.accounts.all(
            "SELECT id,role,expires,accepted_by,revoked FROM invitations WHERE tenant_id=? ORDER BY created DESC LIMIT 100",
            (identity["tenant_id"],),
        )

    @platform.post("/api/invitations/{key}/revoke")
    def revoke_invite(key: str, identity=Depends(administrator)):
        with platform.state.accounts.connect() as db:
            db.execute(
                "UPDATE invitations SET revoked=1 WHERE id=? AND tenant_id=?", (key, identity["tenant_id"])
            )
        platform.state.accounts.audit(
            identity["user"]["id"], identity["tenant_id"], "invitation.revoked", key
        )
        return {"ok": True}

    @platform.get("/api/api-keys")
    def keys(identity=Depends(member)):
        rows = platform.state.accounts.all(
            "SELECT id,prefix,name,role,expires,revoked FROM api_keys WHERE tenant_id=? AND user_id=? ORDER BY created DESC",
            (identity["tenant_id"], identity["user"]["id"]),
        )
        for row in rows:
            scope = platform.state.accounts.one(
                "SELECT applications FROM api_key_scopes WHERE key_id=?", (row["id"],)
            )
            row["application_ids"] = json.loads(scope["applications"]) if scope else None
        return rows

    @platform.post("/api/api-keys")
    async def create_key(body: KeyInput, identity=Depends(member)):
        if body.application_ids is not None:
            child = await platform.state.runtime.get(identity["tenant_id"])
            if any(
                not child.state.store.one("SELECT id FROM applications WHERE id=?", (key,))
                for key in body.application_ids
            ):
                raise HTTPException(422, "提取应用不存在")
        role = body.role
        if ROLES[role] > ROLES[identity["role"]]:
            raise HTTPException(403, "接口密钥不能高于当前成员权限")
        token, key, now = "hub_" + secrets.token_urlsafe(32), uid(), time.time()
        with platform.state.accounts.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute(
                "SELECT COUNT(*) FROM api_keys WHERE tenant_id=? AND user_id=? AND revoked=0 AND expires>?",
                (identity["tenant_id"], identity["user"]["id"], now),
            ).fetchone()[0]
            if count >= 20:
                raise HTTPException(422, "每个成员最多创建 20 个有效接口密钥")
            db.execute(
                "INSERT INTO api_keys VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    key,
                    token_hash(token),
                    token[:10],
                    body.name,
                    identity["user"]["id"],
                    identity["tenant_id"],
                    role,
                    now + body.days * 86400,
                    now,
                    0,
                ),
            )
            if body.application_ids is not None:
                db.execute(
                    "INSERT INTO api_key_scopes VALUES(?,?)",
                    (key, json.dumps(list(dict.fromkeys(body.application_ids)))),
                )
        platform.state.accounts.audit(identity["user"]["id"], identity["tenant_id"], "api_key.created", key)
        return {"id": key, "token": token, "expires": now + body.days * 86400}

    @platform.post("/api/api-keys/{key}/revoke")
    def revoke_key(key: str, identity=Depends(member)):
        with platform.state.accounts.connect() as db:
            db.execute(
                "UPDATE api_keys SET revoked=1 WHERE id=? AND user_id=? AND tenant_id=?",
                (key, identity["user"]["id"], identity["tenant_id"]),
            )
        platform.state.accounts.audit(identity["user"]["id"], identity["tenant_id"], "api_key.revoked", key)
        return {"ok": True}

    @platform.get("/api/usage")
    async def usage(identity=Depends(member)):
        child = await platform.state.runtime.get(identity["tenant_id"])
        return {
            "usage": usage_report(child.state.store),
            "tenant": platform.state.accounts.tenant(identity["tenant_id"]),
            "role": identity["role"],
        }

    @platform.get("/api/audit")
    def audit(identity=Depends(administrator)):
        return platform.state.accounts.all(
            "SELECT created,user_id,event,resource,metadata FROM audit WHERE tenant_id=? ORDER BY id DESC LIMIT 100",
            (identity["tenant_id"],),
        )

    @platform.get("/api/admin/tenants")
    def tenants(identity=Depends(platform_admin)):
        return [
            platform.state.accounts.tenant(row["id"])
            for row in platform.state.accounts.all("SELECT id FROM tenants ORDER BY created")
        ]

    @platform.post("/api/admin/tenants/{tenant_id}/limits")
    def limits(tenant_id: str, body: LimitsInput, identity=Depends(platform_admin)):
        accounts = platform.state.accounts
        accounts.tenant(tenant_id)
        values = body.model_dump()
        plan = values.pop("plan")
        with accounts.connect() as db:
            db.execute("UPDATE tenants SET plan=?,limits=? WHERE id=?", (plan, json.dumps(values), tenant_id))
        accounts.audit(identity["user"]["id"], tenant_id, "tenant.limits_changed", tenant_id, values)
        return accounts.tenant(tenant_id)

    @platform.get("/api/admin/users")
    def users(identity=Depends(platform_admin)):
        return platform.state.accounts.all("SELECT id,username,is_admin,disabled FROM users ORDER BY created")

    @platform.post("/api/admin/users/{user_id}/status")
    def user_status(user_id: str, body: AccountStatus, identity=Depends(platform_admin)):
        if user_id == identity["user"]["id"]:
            raise HTTPException(403, "不能停用当前管理员账户")
        accounts = platform.state.accounts
        if not accounts.user(user_id):
            raise HTTPException(404, "账户不存在")
        with accounts.connect() as db:
            db.execute("UPDATE users SET disabled=? WHERE id=?", (int(body.disabled), user_id))
            if body.disabled:
                db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
                db.execute("UPDATE api_keys SET revoked=1 WHERE user_id=?", (user_id,))
        accounts.audit(
            identity["user"]["id"], None, "account.status_changed", user_id, {"disabled": body.disabled}
        )
        return {"ok": True}

    return TenantGateway(platform)
