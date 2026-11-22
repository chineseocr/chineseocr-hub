"""Application catalog, immutable releases and the public recognition API.

应用 ID 复用模板 ID；字段/提示词仍只有 templates 一个真源。发布是一个不可变
快照和活动指针，不覆盖草稿。公开 API 不接收任意提示词、模型 URL 或磁盘路径。
"""

import difflib
import hashlib
import json
import sqlite3
import time
from typing import Literal

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .credential_presets import credential_presets
from .documents import MAX_BYTES, ingest
from .domain import JobInput, TemplateInput
from .engine import enqueue_job, prepare_job
from .recognition import RecognitionInput, prepare_recognition
from .store import uid
from .table_recognition import LOCATE_SYSTEM, TABLE_SYSTEM, TABLE_USER
from .vision import OCR_SYSTEM, OCR_USERS


class CatalogFields(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    category: str = Field(default="general", min_length=1, max_length=60)
    description: str = Field(default="", max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    owner: str = Field(default="", max_length=100)

    @field_validator("tags")
    @classmethod
    def tags_are_bounded(cls, values):
        if any(not v.strip() or len(v) > 40 for v in values):
            raise ValueError("标签不能为空且最多 40 个字符")
        return list(dict.fromkeys(v.strip() for v in values))


class ApplicationCreate(CatalogFields):
    template: TemplateInput


class CopyInput(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    name: str = Field(min_length=1, max_length=100)


class PublishInput(BaseModel):
    version: int = Field(ge=1)
    provider_id: str = Field(min_length=1, max_length=100)
    mode: Literal["document", "page"] = "document"
    notes: str = Field(default="", max_length=1000)
    task: Literal["extract", "ocr", "tables"] = "extract"
    rotation: Literal[0, 90, 180, 270] = 0
    deskew: bool = False
    grid_assist: bool = True
    position_mode: Literal["direct", "contextual"] = "direct"
    max_localization_batches: int = Field(default=4, ge=1, le=8)


class ReleaseInput(BaseModel):
    release_id: str = Field(min_length=1, max_length=100)


class DisabledInput(BaseModel):
    disabled: bool


class InstallInput(BaseModel):
    preset_ids: list[str] | None = Field(default=None, min_length=1, max_length=50)


class BundleInput(BaseModel):
    format_version: Literal[1] = 1
    applications: list[ApplicationCreate] = Field(min_length=1, max_length=100)


class PublicInput(BaseModel):
    # Unknown inputs are rejected: callers cannot override the published model,
    # prompt, schema, mode or tenant by smuggling in internal API parameters.
    model_config = ConfigDict(extra="forbid")
    file_ids: list[str] = Field(min_length=1, max_length=5)
    pages: list[int] = Field(default_factory=list, max_length=20)
    release_id: str | None = Field(default=None, max_length=100)


def catalog_entry(store, key):
    row = store.one("SELECT * FROM applications WHERE id=?", (key,))
    template = store.template(key)
    if not row or not template:
        raise HTTPException(404, "提取应用不存在")
    row["tags"] = json.loads(row["tags"])
    row["disabled"] = bool(row["disabled"])
    row["name"], row["latest_version"] = template["name"], template["version"]
    release = store.one("SELECT body FROM application_releases WHERE id=?", (row["active_release"],))
    row["published"] = json.loads(release["body"]) if release else None
    row["state"] = "disabled" if row["disabled"] else "published" if release else "draft"
    row["has_changes"] = bool(release and row["published"]["template"]["version"] != template["version"])
    return row


def insert_application(db, body, source_preset=None):
    """Write metadata and first prompt/schema version in the caller's transaction."""
    key, now = uid(), time.time()
    db.execute(
        "INSERT INTO templates VALUES(?,?,?,?)", (key, 1, body.template.model_dump_json(by_alias=True), now)
    )
    db.execute(
        """INSERT INTO applications(id,code,category,description,tags,owner,source_preset,created,updated)
                  VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            key,
            body.code,
            body.category,
            body.description,
            json.dumps(body.tags),
            body.owner,
            source_preset,
            now,
            now,
        ),
    )
    return key


def management(store, request, release=False):
    """Publishing changes live behavior and therefore needs a live admin session."""
    identity = request.scope.get("state", {}).get("saas_identity")
    if identity and (identity["api_key_id"] or (release and identity["role"] not in {"admin", "owner"})):
        raise HTTPException(403, "应用管理需要账户会话，发布需要管理员权限")


def scope_check(request, application_id):
    identity = request.scope.get("state", {}).get("saas_identity", {})
    allowed = identity.get("application_ids")
    if allowed is not None and application_id not in allowed:
        raise HTTPException(403, "接口密钥没有此应用的调用权限")


def principal(request):
    identity = request.scope.get("state", {}).get("saas_identity")
    return (
        ("key:" + identity["api_key_id"] if identity["api_key_id"] else "user:" + identity["user"]["id"])
        if identity
        else "community"
    )


def public_job(record):
    """Do not expose prompts, model connections, raw model traces or storage metadata."""
    return {
        "id": record["id"],
        "status": record["status"],
        "application": record["payload"]["application"],
        "created": record["created"],
        "updated": record["updated"],
        "error": record["error"],
        "results": [
            {
                k: v
                for k, v in unit.items()
                if k
                in {
                    "file_id",
                    "filename",
                    "pages",
                    "status",
                    "data",
                    "validation_errors",
                    "error",
                    "usage",
                    "duration_ms",
                    "field_locations",
                    "preprocessing",
                    "localization",
                }
            }
            for unit in record["results"]
        ],
    }


def application_router(authorize):
    router = APIRouter()

    @router.get("/api/application-presets")
    def presets(request: Request, store=Depends(authorize)):
        return credential_presets(request.headers.get("accept-language", "zh"))

    @router.get("/api/applications/export")
    def export(ids: str = Query(default="", max_length=4000), store=Depends(authorize)):
        # The UI exports the current bounded result page; API clients may supply
        # up to 100 IDs. Large catalogs therefore do not require one huge bundle.
        keys = (
            list(dict.fromkeys(ids.split(",")))
            if ids
            else [r["id"] for r in store.all("SELECT id FROM applications ORDER BY created")]
        )
        if len(keys) > 100:
            raise HTTPException(422, "单次最多导出 100 个应用，请使用单应用导出")
        return {"format_version": 1, "applications": [export_body(store, key) for key in keys]}

    @router.post("/api/applications/import")
    def import_bundle(body: BundleInput, request: Request, store=Depends(authorize)):
        management(store, request)
        try:
            with store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                keys = [insert_application(db, item) for item in body.applications]
        except sqlite3.IntegrityError:
            raise HTTPException(409, "应用编码已存在；整批导入未写入") from None
        return {"ids": keys}

    @router.post("/api/applications/install-presets")
    def install(body: InstallInput, request: Request, store=Depends(authorize)):
        management(store, request)
        presets = credential_presets(request.headers.get("accept-language", "zh"))
        selected = set(body.preset_ids or [p["id"] for p in presets])
        if selected - {p["id"] for p in presets}:
            raise HTTPException(422, "证件配置不存在")
        created, skipped = [], []
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for preset in presets:
                if preset["id"] not in selected:
                    continue
                if db.execute(
                    "SELECT id FROM applications WHERE source_preset=? OR code=?",
                    (preset["id"], preset["code"]),
                ).fetchone():
                    skipped.append(preset["id"])
                    continue
                config = ApplicationCreate(
                    code=preset["code"],
                    category="credentials",
                    tags=[preset["document_label"]],
                    template=preset,
                )
                created.append(insert_application(db, config, preset["id"]))
        return {"created": created, "skipped": skipped}

    @router.get("/api/applications")
    def listing(
        q: str = Query(default="", max_length=200),
        category: str = Query(default="", max_length=60),
        state: Literal["", "draft", "published", "disabled"] = "",
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=25, ge=1, le=100),
        store=Depends(authorize),
    ):
        # Search runs in SQLite before pagination. No browser-only filtering of a
        # single page, and no full prompt bodies in catalog-list responses.
        conditions = [
            "instr(lower(a.code || ' ' || a.description || ' ' || a.tags || ' ' || a.owner || ' ' || json_extract(t.body,'$.name')),lower(?))>0"
        ]
        values = [q]
        if category:
            conditions.append("a.category=?")
            values.append(category)
        if state:
            conditions.append(
                {
                    "disabled": "a.disabled=1",
                    "draft": "a.disabled=0 AND a.active_release IS NULL",
                    "published": "a.disabled=0 AND a.active_release IS NOT NULL",
                }[state]
            )
        base = (
            " FROM applications a JOIN templates t ON t.id=a.id AND t.version=(SELECT MAX(version) FROM templates WHERE id=a.id) WHERE "
            + " AND ".join(conditions)
        )
        total = store.one("SELECT COUNT(*) AS n" + base, values)["n"]
        rows = store.all(
            "SELECT a.id" + base + " ORDER BY a.updated DESC,a.id LIMIT ? OFFSET ?", [*values, limit, offset]
        )
        items = []
        for row in rows:
            item = catalog_entry(store, row["id"])
            if item["published"]:
                item["published"] = {
                    k: item["published"][k]
                    for k in ("id", "version", "provider_id", "mode", "created", "notes")
                }
            items.append(item)
        return {
            "items": items,
            "total": total,
            "offset": offset,
            "limit": limit,
            "categories": [
                r["category"]
                for r in store.all("SELECT DISTINCT category FROM applications ORDER BY category")
            ],
        }

    @router.post("/api/applications")
    def create(body: ApplicationCreate, request: Request, store=Depends(authorize)):
        management(store, request)
        try:
            with store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                key = insert_application(db, body)
        except sqlite3.IntegrityError:
            raise HTTPException(409, "应用编码已存在") from None
        return catalog_entry(store, key)

    @router.get("/api/applications/{key}")
    def detail(key: str, store=Depends(authorize)):
        return {
            **catalog_entry(store, key),
            "template": store.template(key),
            "releases": [
                json.loads(r["body"])
                for r in store.all(
                    "SELECT body FROM application_releases WHERE application_id=? ORDER BY created DESC",
                    (key,),
                )
            ],
        }

    @router.get("/api/applications/{key}/export")
    def export_one(key: str, store=Depends(authorize)):
        return {"format_version": 1, "applications": [export_body(store, key)]}

    @router.post("/api/applications/{key}/metadata")
    def metadata(key: str, body: CatalogFields, request: Request, store=Depends(authorize)):
        management(store, request)
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM applications WHERE id=?", (key,)).fetchone()
            if not row:
                raise HTTPException(404, "提取应用不存在")
            if row["code"] != body.code:
                raise HTTPException(422, "应用编码创建后不可修改，请复制为新应用")
            db.execute(
                "UPDATE applications SET category=?,description=?,tags=?,owner=?,updated=? WHERE id=?",
                (body.category, body.description, json.dumps(body.tags), body.owner, time.time(), key),
            )
        return catalog_entry(store, key)

    @router.post("/api/applications/{key}/copy")
    def copy(key: str, body: CopyInput, request: Request, store=Depends(authorize)):
        management(store, request)
        old = catalog_entry(store, key)
        template = dict(store.template(key), name=body.name)
        config = ApplicationCreate(
            code=body.code,
            category=old["category"],
            description=old["description"],
            tags=old["tags"],
            owner=old["owner"],
            template=template,
        )
        try:
            with store.connect() as db:
                new = insert_application(db, config)
        except sqlite3.IntegrityError:
            raise HTTPException(409, "应用编码已存在") from None
        return catalog_entry(store, new)

    @router.get("/api/applications/{key}/diff")
    def diff(key: str, before: int = Query(ge=1), after: int = Query(ge=1), store=Depends(authorize)):
        left, right = store.template(key, before), store.template(key, after)
        if not left or not right:
            raise HTTPException(404, "模板版本不存在")
        return {
            "before": before,
            "after": after,
            "diff": {
                field: "\n".join(
                    difflib.unified_diff(
                        json.dumps(left[field], ensure_ascii=False, indent=2).splitlines(),
                        json.dumps(right[field], ensure_ascii=False, indent=2).splitlines(),
                        fromfile=f"v{before}",
                        tofile=f"v{after}",
                        lineterm="",
                    )
                )
                for field in ("schema", "system_prompt", "user_prompt")
            },
        }

    @router.post("/api/applications/{key}/publish")
    def publish(key: str, body: PublishInput, request: Request, store=Depends(authorize)):
        management(store, request, True)
        catalog_entry(store, key)
        template = store.template(key, body.version)
        provider = store.get("providers", body.provider_id)
        if not template:
            raise HTTPException(404, "模板版本不存在")
        if not provider or body.provider_id in store.archived("providers"):
            raise HTTPException(422, "模型配置不存在")
        if store.provider_policy and not store.provider_policy(provider):
            raise HTTPException(403, "模型服务地址不在平台允许列表，请联系管理员")
        release = {
            "id": uid(),
            "application_id": key,
            "version": body.version,
            "template": template,
            "provider_id": body.provider_id,
            "mode": body.mode,
            "task": body.task,
            "processing": {
                "rotation": body.rotation,
                "deskew": body.deskew,
                "grid_assist": body.grid_assist,
                "position_mode": body.position_mode,
                "max_localization_batches": body.max_localization_batches,
                "prompts": {"locate_system": LOCATE_SYSTEM},
            },
            "recognition_prompts": {
                "table_system": TABLE_SYSTEM,
                "table_user": TABLE_USER,
                "ocr_system": OCR_SYSTEM,
                "ocr_user": OCR_USERS["text"],
                "locate_system": LOCATE_SYSTEM,
            },
            "notes": body.notes,
            "created": time.time(),
        }
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO application_releases VALUES(?,?,?,?)",
                (release["id"], key, json.dumps(release), release["created"]),
            )
            db.execute(
                "UPDATE applications SET active_release=?,disabled=0,updated=? WHERE id=?",
                (release["id"], time.time(), key),
            )
        return release

    @router.post("/api/applications/{key}/activate")
    def activate(key: str, body: ReleaseInput, request: Request, store=Depends(authorize)):
        management(store, request, True)
        release = store.one(
            "SELECT body FROM application_releases WHERE id=? AND application_id=?", (body.release_id, key)
        )
        if not release:
            raise HTTPException(404, "发布版本不存在")
        config = json.loads(release["body"])
        provider = store.get("providers", config["provider_id"])
        if not provider or (store.provider_policy and not store.provider_policy(provider)):
            raise HTTPException(422, "发布版本的模型连接不可用")
        store.execute(
            "UPDATE applications SET active_release=?,disabled=0,updated=? WHERE id=?",
            (body.release_id, time.time(), key),
        )
        return catalog_entry(store, key)

    @router.post("/api/applications/{key}/state")
    def enabled(key: str, body: DisabledInput, request: Request, store=Depends(authorize)):
        management(store, request, True)
        catalog_entry(store, key)
        store.execute(
            "UPDATE applications SET disabled=?,updated=? WHERE id=?", (int(body.disabled), time.time(), key)
        )
        return catalog_entry(store, key)

    @router.get("/v1/openapi.json")
    def public_schema(request: Request, store=Depends(authorize)):
        schema = request.app.openapi().copy()
        schema["paths"] = {k: v for k, v in schema["paths"].items() if k.startswith("/v1/")}
        # Components are contracts, never runtime credentials or customer prompts.
        return schema

    @router.post("/v1/files")
    def public_upload(request: Request, file: UploadFile = File(...), store=Depends(authorize)):
        file.file.seek(0, 2)
        if file.file.tell() > MAX_BYTES:
            raise HTTPException(413, "单文件上限为 20MB")
        file.file.seek(0)
        try:
            saved = ingest(store, file.filename, file.file)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        store.execute("INSERT INTO public_uploads VALUES(?,?)", (saved["id"], principal(request)))
        return {k: saved[k] for k in ("id", "name", "pages")}

    @router.post("/v1/apps/{code}/recognitions")
    def recognize(
        code: str,
        body: PublicInput,
        request: Request,
        idempotency_key: str | None = Header(default=None),
        store=Depends(authorize),
    ):
        row = store.one("SELECT id FROM applications WHERE code=?", (code,))
        if not row:
            raise HTTPException(404, "提取应用不存在")
        key = row["id"]
        scope_check(request, key)
        app = catalog_entry(store, key)
        if app["disabled"] or not app["active_release"]:
            raise HTTPException(409, "应用尚未发布或已停用")
        identity = request.scope.get("state", {}).get("saas_identity", {})
        if identity.get("application_ids") is not None:
            for file_id in body.file_ids:
                if not store.one(
                    "SELECT file_id FROM public_uploads WHERE file_id=? AND principal=?",
                    (file_id, principal(request)),
                ):
                    raise HTTPException(404, "上传文件不存在")
        fingerprint = hashlib.sha256(
            json.dumps(
                {"code": code, "principal": principal(request), **body.model_dump()}, sort_keys=True
            ).encode()
        ).hexdigest()
        idem = None
        if idempotency_key:
            if len(idempotency_key) > 128:
                raise HTTPException(422, "幂等键最长 128 字符")
            idem = (
                "public:"
                + hashlib.sha256(
                    (principal(request) + ":" + code + ":" + idempotency_key).encode()
                ).hexdigest()
            )
            old = store.one("SELECT id FROM jobs WHERE idem=?", (idem,))
            if old:
                record = store.job(old["id"])
                if record["payload"].get("public_request") != fingerprint:
                    raise HTTPException(409, "相同幂等键已用于不同请求")
                return public_job(record)
        release_id = body.release_id or app["active_release"]
        release_row = store.one(
            "SELECT body FROM application_releases WHERE id=? AND application_id=?", (release_id, key)
        )
        if not release_row:
            raise HTTPException(404, "发布版本不存在")
        release = json.loads(release_row["body"])
        try:
            payload = json.loads(
                prepare_job(
                    store,
                    JobInput(
                        template_id=key,
                        version=release["version"],
                        provider_id=release["provider_id"],
                        mode=release["mode"] if release.get("task", "extract") == "extract" else "page",
                        file_ids=body.file_ids,
                        pages=body.pages,
                    ),
                )
            )
            payload["template"] = release["template"]
            if release.get("task", "extract") in {"ocr", "tables"}:
                settings = {k: v for k, v in release.get("processing", {}).items() if k != "prompts"}
                payload = json.loads(
                    prepare_recognition(
                        store,
                        RecognitionInput(
                            task=release["task"],
                            provider_id=release["provider_id"],
                            file_ids=body.file_ids,
                            pages=body.pages,
                            **settings,
                        ),
                    )
                )
                payload["prompts"].update(release.get("recognition_prompts", {}))
            elif "processing" in release:
                settings = release["processing"]
                payload["processing"] = {k: v for k, v in settings.items() if k != "prompts"}
                if (
                    settings.get("deskew")
                    or settings.get("rotation")
                    or settings.get("position_mode") == "contextual"
                ):
                    payload["processing"]["prompts"] = settings["prompts"]
            payload["application"] = {
                "id": key,
                "code": code,
                "release_id": release_id,
                "version": release["version"],
            }
            payload["public_request"] = fingerprint
            payload["public_principal"] = principal(request)
            try:
                job_id = enqueue_job(store, json.dumps(payload, sort_keys=True), idem)
            except FileExistsError:
                old = store.one("SELECT id FROM jobs WHERE idem=?", (idem,))
                if not old or store.job(old["id"])["payload"].get("public_request") != fingerprint:
                    raise
                job_id = old["id"]
        except FileExistsError:
            raise HTTPException(409, "相同幂等键已用于不同请求") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return public_job(store.job(job_id))

    def authorized_public_job(key, request, store):
        record = store.job(key)
        if not record or "application" not in record["payload"]:
            raise HTTPException(404, "任务不存在")
        scope_check(request, record["payload"]["application"]["id"])
        identity = request.scope.get("state", {}).get("saas_identity", {})
        if identity.get("application_ids") is not None and record["payload"].get(
            "public_principal"
        ) != principal(request):
            raise HTTPException(404, "任务不存在")
        return record

    @router.get("/v1/jobs/{key}")
    def job(key: str, request: Request, store=Depends(authorize)):
        return public_job(authorized_public_job(key, request, store))

    @router.get("/v1/jobs/{key}/tables/{unit_index}/{table_index}/export")
    def public_table_export(
        key: str,
        unit_index: int,
        table_index: int,
        request: Request,
        format: Literal["csv", "html"] = "csv",
        store=Depends(authorize),
    ):
        from .table_export import table_csv, table_html

        record = authorized_public_job(key, request, store)
        if record["status"] in {"queued", "running"}:
            raise HTTPException(409, "请等待识别任务结束后导出")
        if record["payload"].get("task") != "tables" or unit_index < 0 or table_index < 0:
            raise HTTPException(404, "表格不存在")
        try:
            table = record["results"][unit_index]["data"]["tables"][table_index]
        except (IndexError, TypeError, KeyError):
            raise HTTPException(404, "表格不存在") from None
        content = (
            table_csv(table)
            if format == "csv"
            else '<!doctype html><meta charset="utf-8">' + table_html(table)
        )
        return Response(
            content,
            media_type="text/csv; charset=utf-8" if format == "csv" else "text/html; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{key}-{unit_index}-{table_index}.{format}"',
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )

    @router.post("/v1/jobs/{key}/cancel")
    def cancel(key: str, request: Request, store=Depends(authorize)):
        authorized_public_job(key, request, store)
        store.execute(
            "UPDATE jobs SET cancel=1,updated=? WHERE id=? AND status IN ('queued','running')",
            (time.time(), key),
        )
        return {"ok": True}

    return router


def export_body(store, key):
    """Portable drafts contain no provider IDs, secrets, releases, files or results."""
    app = catalog_entry(store, key)
    return {k: app[k] for k in ("code", "category", "description", "tags", "owner")} | {
        "template": {k: store.template(key)[k] for k in ("name", "schema", "system_prompt", "user_prompt")}
    }
