"""Authenticated application API and zero-build web interface."""

import collections
import fcntl
import hashlib
import hmac
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import PRODUCT_NAME, __version__
from .adapters import BackendError, discover_models
from .applications import application_router
from .bootstrap import initialize_workspace
from .documents import MAX_BYTES, ingest, preview_path
from .domain import DictionaryInput, JobInput, ProviderInput, TemplateInput, compare, render_prompt
from .engine import Worker, create_evaluation, create_job, enqueue_job
from .localization import translate
from .presets import starting_templates
from .recognition import RecognitionInput, prepare_recognition
from .store import Store, uid
from .vision import LABELS, image_png, rotate_image

STATIC = Path(__file__).parent / "static"


class Login(BaseModel):
    token: str = Field(max_length=2000)


class DatasetInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class SampleInput(BaseModel):
    file_id: str
    gold: dict  # document mode: one object for one uploaded file

    @field_validator("gold")
    @classmethod
    def finite_json(cls, value):
        """Reject NaN and Infinity in ground truth so stored answers remain portable, strict JSON."""
        json.dumps(value, allow_nan=False)
        return value


class RunInput(BaseModel):
    template_id: str
    version: Optional[int] = None
    provider_id: str


def create_app(
    data_dir=None,
    start_worker=True,
    access_control=None,
    configure_store=None,
    model_policy=None,
    model_environment=True,
):
    """Build an isolated application with lifespan-owned storage, authentication and worker.

    Tests may disable the worker to inspect/recover queue states. Persistence and
    model adapters remain identical to the production execution path.
    """

    @asynccontextmanager
    async def lifespan(application):
        """Own the workspace lock across initialization, recovery and graceful shutdown."""
        store = Store(data_dir or os.environ.get("WORKBENCH_DATA_DIR", "data"))
        lock = (store.directory / "workspace.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            raise RuntimeError("工作空间已被其他进程占用；请使用单个 uvicorn worker") from None
        worker = None
        try:
            application.state.store = store
            if configure_store:
                configure_store(store)
            initialize_workspace(store, model_environment=model_environment)
            worker = Worker(store)
            application.state.worker = worker
            if start_worker:
                worker.start()
            yield
        finally:
            # Configuration errors happen before yield too. Always release the lock
            # so correcting a bad deployment setting permits another startup.
            if worker and start_worker and worker.thread.is_alive():
                worker.close()
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()

    app = FastAPI(
        title=PRODUCT_NAME,
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    attempts = collections.deque(maxlen=100)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        """Translate interface errors without leaking upstream bodies or configuration."""
        return JSONResponse(
            {"detail": translate(str(exc.detail), request.headers.get("accept-language", "zh"))},
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def input_error(request: Request, exc: RequestValidationError):
        """Keep field locations/messages, but omit raw inputs (which can contain API keys)."""
        language = request.headers.get("accept-language", "zh")
        errors = [
            {"loc": e["loc"], "type": e["type"], "msg": translate(e["msg"], language)} for e in exc.errors()
        ]
        return JSONResponse({"detail": errors}, status_code=422)

    def store(request: Request):
        """Retrieve this application instance's store; no global mutable workspace state is shared across instances."""
        return request.app.state.store

    def session_value(database):
        """Derive a browser-session credential without placing the reusable API access token in a cookie."""
        return hmac.new(database.token.encode(), b"workbench-browser-session-v1", hashlib.sha256).hexdigest()

    def authorize(request: Request, database=Depends(store)):
        """Accept a constant-time-checked bearer token or HttpOnly session cookie.

        Cookie-authenticated writes additionally require a same-origin marker; all business routes use this dependency."""
        if access_control:
            return access_control(request, database)
        bearer = request.headers.get("authorization", "")
        api_ok = bearer.startswith("Bearer ") and secrets_equal(bearer[7:], database.token)
        cookie_ok = secrets_equal(request.cookies.get("workbench_session", ""), session_value(database))
        if not api_ok and not cookie_ok:
            raise HTTPException(401, "请先输入工作空间访问令牌")
        if not api_ok and request.method not in {"GET", "HEAD"} and request.headers.get("x-workbench") != "1":
            raise HTTPException(403, "缺少同源请求标记")
        return database

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        """Enforce request-size/origin boundaries and attach browser security headers.

        Files stay private; static hosting is limited to application assets. Proxy/TLS controls still belong to deployment configuration."""
        if request.method in {"POST", "PUT", "PATCH"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "不允许跨源写入"}, status_code=403)
            length = request.headers.get("content-length")
            limit = MAX_BYTES + 1024 * 1024 if request.url.path in {"/api/files", "/v1/files"} else 256 * 1024
            if length is None:
                return JSONResponse({"detail": "需要 Content-Length"}, status_code=411)
            if not length.isdecimal() or int(length) > limit:
                return JSONResponse({"detail": "请求大小超过上限"}, status_code=413)
        response = await call_next(request)
        response.headers.update(
            {
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY",
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        return response

    app.include_router(application_router(authorize))

    @app.get("/healthz")
    def health():
        """Expose a minimal liveness result without revealing documents, model endpoints or credentials."""
        return {"status": "ok", "version": __version__}

    @app.get("/api/deployment")
    def deployment():
        """Expose only UI mode selection; never expose workspace tokens or model settings."""
        return {"mode": "community", "registration": False}

    @app.get("/")
    def index():
        """Serve the application shell only; authenticated data is fetched separately after login."""
        return FileResponse(STATIC / "index.html")

    @app.get("/startup.js", include_in_schema=False)
    def startup_script():
        """Serve the relative bootstrap entry, which also works beside a local index.html."""
        return FileResponse(STATIC / "startup.js", media_type="text/javascript")

    @app.get("/style.css", include_in_schema=False)
    def stylesheet():
        """Keep the bootstrap guidance styled for both HTTP and direct-file entry."""
        return FileResponse(STATIC / "style.css", media_type="text/css")

    app.mount("/assets", StaticFiles(directory=STATIC), name="assets")

    @app.post("/api/session")
    def login(body: Login, request: Request, response: Response, database=Depends(store)):
        """Check the workspace token with bounded attempts and issue a 12-hour HttpOnly session cookie."""
        now = time.monotonic()
        while attempts and attempts[0] < now - 60:
            attempts.popleft()
        if len(attempts) >= 20:
            raise HTTPException(429, "尝试过于频繁，请一分钟后再试")
        if not secrets_equal(body.token, database.token):
            attempts.append(now)
            raise HTTPException(401, "访问令牌不正确")
        response.set_cookie(
            "workbench_session",
            session_value(database),
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
            max_age=43200,
        )
        return {"ok": True}

    @app.delete("/api/session")
    def logout(response: Response, database=Depends(authorize)):
        """Expire the browser session cookie without modifying immutable extraction records or model credentials."""
        response.delete_cookie("workbench_session")
        return {"ok": True}

    @app.get("/api/bootstrap")
    def bootstrap(request: Request, database=Depends(authorize)):
        # Legacy fixed-output entries are retained in storage, but never presented as
        # live providers, uploads or evaluation evidence in the real-model workspace.
        """Return workspace metadata needed for rendering, excluding secrets and archived fixed-output records."""
        archived = {name: database.archived(name) for name in ("providers", "files", "datasets")}
        templates = database.all(
            "SELECT id,MAX(version) AS version FROM templates GROUP BY id ORDER BY MIN(created)"
        )
        providers = [
            {"id": r["id"], **public_provider(json.loads(r["body"]))}
            for r in database.all("SELECT id,body FROM providers ORDER BY created")
            if r["id"] not in archived["providers"] and json.loads(r["body"])["kind"] == "openai"
        ]
        files = [
            {"id": r["id"], **public_file(json.loads(r["body"]))}
            for r in database.all("SELECT id,body FROM files ORDER BY created DESC LIMIT 100")
            if r["id"] not in archived["files"]
        ]
        datasets = [
            {"id": r["id"], "name": r["name"], "count": len(json.loads(r["samples"]))}
            for r in database.all("SELECT * FROM datasets ORDER BY created DESC")
            if r["id"] not in archived["datasets"]
        ]
        return {
            "templates": [database.template(r["id"]) for r in templates],
            "dictionaries": [
                database.dictionary(r["id"])
                for r in database.all("SELECT DISTINCT id FROM dictionaries ORDER BY id")
            ],
            "providers": providers,
            "files": files,
            "datasets": datasets,
            "limits": {"file_mb": 20, "pdf_pages": 20, "joint_pages": 8},
            "version": __version__,
            "default_provider_id": database.metadata("default_provider_id"),
            "saas": request.scope.get("state", {}).get("saas_public"),
        }

    @app.get("/api/openapi.json")
    def openapi(database=Depends(authorize)):
        """Expose the API schema only to authenticated workspace users."""
        return app.openapi()

    @app.post("/api/templates")
    def new_template(body: TemplateInput, database=Depends(authorize)):
        """Create an independent extraction application with validated structure and prompts."""
        return database.save_template(uid(), body.model_dump(by_alias=True))

    @app.get("/api/template-presets")
    def template_presets(request: Request, database=Depends(authorize)):
        """Offer starting schemas without creating records or calling an external model."""
        return starting_templates(request.headers.get("accept-language", "zh"))

    @app.post("/api/templates/{key}/versions")
    def save_template(key: str, body: TemplateInput, database=Depends(authorize)):
        """Append a version under a write transaction.

        BEGIN IMMEDIATE serializes version allocation so concurrent saves cannot overwrite each other."""
        if not database.template(key):
            raise HTTPException(404, "模板不存在")
        return database.save_template(key, body.model_dump(by_alias=True))

    @app.get("/api/templates/{key}/versions")
    def versions(key: str, database=Depends(authorize)):
        """Return immutable historical configurations so results can be reproduced and older drafts inspected."""
        rows = database.all("SELECT version FROM templates WHERE id=? ORDER BY version DESC", (key,))
        return [database.template(key, r["version"]) for r in rows]

    @app.post("/api/prompt-preview")
    def prompt_preview(body: TemplateInput, database=Depends(authorize)):
        """Render the draft using an explicit preview context; this endpoint never calls the model."""
        values = {
            "schema": json.dumps(body.schema_, ensure_ascii=False, indent=2),
            "filename": "sample.pdf",
            "pages": "1, 2",
        }
        return {
            "system": render_prompt(body.system_prompt, values),
            "user": render_prompt(body.user_prompt, values),
        }

    @app.post("/api/dictionaries")
    def new_dictionary(body: DictionaryInput, database=Depends(authorize)):
        """Create a reusable literal-value list; it does not alter any application."""
        return database.save_dictionary(uid(), body.model_dump())

    @app.get("/api/dictionaries/{key}/versions")
    def dictionary_versions(key: str, database=Depends(authorize)):
        """Return immutable versions for deliberate copy-to-field operations."""
        if not database.dictionary(key):
            raise HTTPException(404, "字典不存在")
        return [
            database.dictionary(key, row["version"])
            for row in database.all(
                "SELECT version FROM dictionaries WHERE id=? ORDER BY version DESC", (key,)
            )
        ]

    @app.post("/api/dictionaries/{key}/versions")
    def save_dictionary(key: str, body: DictionaryInput, database=Depends(authorize)):
        """Save a new library version; templates retain the enum snapshot previously copied."""
        if not database.dictionary(key):
            raise HTTPException(404, "字典不存在")
        return database.save_dictionary(key, body.model_dump())

    @app.post("/api/providers")
    def new_provider(body: ProviderInput, database=Depends(authorize)):
        """Save an immutable real-service profile; do not return its API key to the browser."""
        if body.kind == "openai" and not body.model.strip():
            raise HTTPException(422, "请输入模型名称")
        if model_policy:
            model_policy(body)
        key = database.put("providers", body.model_dump())
        if not database.metadata("default_provider_id"):
            database.metadata("default_provider_id", key)
        return {"id": key, **public_provider(body.model_dump())}

    @app.post("/api/providers/discover")
    def probe_provider(body: ProviderInput, database=Depends(authorize)):
        """Explicitly inspect the administrator's endpoint without sending a document."""
        if model_policy:
            model_policy(body)
        try:
            return discover_models(body.base_url, body.api_key)
        except BackendError as exc:
            raise HTTPException(502, str(exc)) from None

    @app.post("/api/providers/{key}/default")
    def select_default_provider(key: str, database=Depends(authorize)):
        """Persist a default connection; existing task snapshots remain unchanged."""
        provider = database.get("providers", key)
        if not provider or provider["kind"] != "openai":
            raise HTTPException(404, "模型配置不存在")
        database.metadata("default_provider_id", key)
        return {"default_provider_id": key}

    @app.post("/api/files")
    def upload(file: UploadFile = File(...), database=Depends(authorize)):
        """Parse one uploaded file and return private file metadata; normalize parser failures without leaking host paths."""
        try:
            return {"id": (result := ingest(database, file.filename, file.file))["id"], **public_file(result)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except Exception:
            raise HTTPException(422, "无法解析文件；请检查格式、尺寸或 PDF 密码") from None

    @app.get("/api/files/{key}/pages/{page}")
    def page_image(key: str, page: int, database=Depends(authorize)):
        """Authorize and serve a generated page PNG by database ID and one-based page number."""
        file = database.get("files", key)
        if not file or page < 1 or page > len(file["pages"]):
            raise HTTPException(404, "页面不存在")
        return FileResponse(preview_path(database, file, page), media_type="image/png")

    @app.get("/api/files/{key}/download")
    def download(key: str, database=Depends(authorize)):
        """Authorize an original-file download and force attachment disposition rather than executable inline content."""
        file = database.get("files", key)
        if not file:
            raise HTTPException(404, "文件不存在")
        return FileResponse(
            database.directory / "files" / file["storage_key"] / "original",
            filename=file["name"],
            media_type="application/octet-stream",
        )

    @app.post("/api/jobs")
    def submit(
        body: JobInput, idempotency_key: Optional[str] = Header(default=None), database=Depends(authorize)
    ):
        """Resolve/validate a job snapshot and enqueue it; an optional idempotency key protects client retries."""
        if idempotency_key and len(idempotency_key) > 128:
            raise HTTPException(422, "幂等键最长 128 字符")
        try:
            key = create_job(database, body, idempotency_key)
            return database.job(key)
        except FileExistsError as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/recognition/capabilities")
    def recognition_capabilities(database=Depends(authorize)):
        """Report executable modes and honest coordinate/score provenance."""
        return {
            "engine": "multimodal",
            "tasks": ["ocr", "documents", "tables"],
            "deskew": "projection-v1",
            "position_modes": ["direct", "contextual"],
            "table_exports": ["csv", "html"],
            "formats": ["text", "markdown"],
            "labels": LABELS,
            "coordinate_source": "model_reported",
            "calibrated_confidence": False,
            "max_instances_per_page": 12,
        }

    @app.post("/api/recognition/jobs")
    def recognition_job(
        body: RecognitionInput,
        idempotency_key: Optional[str] = Header(default=None),
        database=Depends(authorize),
    ):
        """No user field schema is required for full-text or per-document OCR."""
        if idempotency_key and len(idempotency_key) > 128:
            raise HTTPException(422, "幂等键最长 128 字符")
        try:
            key = enqueue_job(database, prepare_recognition(database, body), idempotency_key)
            return database.job(key)
        except FileExistsError as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/jobs/{key}/export")
    def export_recognition(key: str, format: str = "txt", database=Depends(authorize)):
        """Export only persisted visible text; partial failures are explicitly marked."""
        record = job(key, database)
        if record["payload"].get("task") not in {"ocr", "documents", "tables"} or format not in {"txt", "md"}:
            raise HTTPException(422, "当前任务不支持此导出格式")
        if record["status"] in {"queued", "running"}:
            raise HTTPException(409, "请等待识别任务结束后导出")
        chunks = []
        for unit in record["results"]:
            chunks.append(f"--- {unit['filename']} / page {unit['pages'][0]} / {unit['status']} ---")
            if record["payload"]["task"] == "documents":
                # Keep instance attribution and failures in human-readable
                # exports, including same-class documents and routed JSON.
                for instance in unit.get("instances", []):
                    chunks.append(f"--- {instance['label']} / {instance['id']} / {instance['status']} ---")
                    data = instance.get("data")
                    if isinstance(data, dict):
                        chunks.append(
                            json.dumps(data, ensure_ascii=False, indent=2)
                            if instance.get("template")
                            else data.get("text", "")
                        )
                    if instance.get("error"):
                        chunks.append("[ERROR] " + instance["error"])
            elif isinstance(unit.get("data"), dict):
                chunks.append(unit["data"].get("text", ""))
            if unit.get("error"):
                chunks.append("[ERROR] " + unit["error"])
        return Response(
            "\n\n".join(chunks),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{key}.{format}"'},
        )

    @app.get("/api/jobs/{key}/tables/{unit_index}/{table_index}/export")
    def export_table(
        key: str, unit_index: int, table_index: int, format: str = "csv", database=Depends(authorize)
    ):
        """Export one saved table under the same job/tenant authorization boundary."""
        from .table_export import table_csv, table_html

        record = job(key, database)
        if record["payload"].get("task") != "tables" or format not in {"csv", "html"}:
            raise HTTPException(422, "当前任务不支持此导出格式")
        if record["status"] in {"queued", "running"}:
            raise HTTPException(409, "请等待识别任务结束后导出")
        if unit_index < 0 or table_index < 0:
            raise HTTPException(404, "表格不存在")
        try:
            table = record["results"][unit_index]["data"]["tables"][table_index]
        except (IndexError, KeyError, TypeError):
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

    @app.get("/api/jobs/{key}/regions/{instance_id}/crop")
    def document_crop(key: str, instance_id: str, database=Depends(authorize)):
        """Resolve crops from authorized saved metadata, never a client path or rectangle."""
        from PIL import Image

        record = job(key, database)
        for unit in record["results"]:
            for instance in unit.get("instances", []):
                if instance["id"] == instance_id:
                    file = database.get("files", unit["file_id"])
                    with Image.open(preview_path(database, file, unit["pages"][0])) as image:
                        crop = rotate_image(image.crop(instance["box"]), record["payload"].get("rotation", 0))
                        return Response(image_png(crop), media_type="image/png")
        raise HTTPException(404, "文档实例不存在")

    @app.get("/api/jobs")
    def jobs(database=Depends(authorize)):
        """List lightweight recent job metadata; large raw results are retrieved through the single-job endpoint."""
        rows = database.all(
            "SELECT id,status,payload,error,created,updated,cancel FROM jobs ORDER BY created DESC LIMIT 100"
        )
        archived = database.archived("jobs")
        return [dict(r, payload=json.loads(r["payload"])) for r in rows if r["id"] not in archived]

    @app.get("/api/jobs/{key}")
    def job(key: str, database=Depends(authorize)):
        """Decode persisted job snapshots/results; this is an internal record, not an authorization check."""
        if key in database.archived("jobs"):
            raise HTTPException(410, "此历史记录已归档")
        result = database.job(key)
        if not result:
            raise HTTPException(404, "任务不存在")
        return result

    @app.post("/api/jobs/{key}/cancel")
    def cancel(key: str, database=Depends(authorize)):
        """Record cooperative cancellation; queued jobs stop immediately and in-flight calls finish or time out."""
        job(key, database)  # Apply the same not-found/archive boundary as result retrieval.
        database.execute(
            "UPDATE jobs SET cancel=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END,updated=? WHERE id=? AND status IN ('queued','running')",
            (time.time(), key),
        )
        return database.job(key)

    @app.post("/api/jobs/{key}/retry")
    def retry(key: str, database=Depends(authorize)):
        """Create a new execution using the original snapshot, while refusing archived or still-active jobs."""
        old = database.job(key)
        if key in database.archived("jobs"):
            raise HTTPException(410, "此历史记录已归档")
        if not old or old["status"] in {"queued", "running"}:
            raise HTTPException(409, "仅已结束的任务可以重新运行")
        new_key, now = uid(), time.time()
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if (
                db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
                >= 100
            ):
                raise HTTPException(422, "任务队列已满")
            db.execute(
                "INSERT INTO jobs(id,status,payload,created,updated) VALUES(?,?,?,?,?)",
                (new_key, "queued", json.dumps(old["payload"]), now, now),
            )
            try:
                database.reserve_job_usage(db, new_key, json.dumps(old["payload"]), now)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
        return database.job(new_key)

    @app.post("/api/datasets")
    def new_dataset(body: DatasetInput, database=Depends(authorize)):
        """Create an empty dataset; neither answers nor accuracy are invented for new workspaces."""
        key = uid()
        database.execute("INSERT INTO datasets VALUES(?,?,?,?)", (key, body.name, "[]", time.time()))
        return {"id": key, "name": body.name, "samples": []}

    @app.get("/api/datasets/{key}")
    def dataset(key: str, database=Depends(authorize)):
        """Read samples with ground truth; retired starter fixtures are not presented as live evaluation evidence."""
        if key in database.archived("datasets"):
            raise HTTPException(410, "此历史记录已归档")
        row = database.one("SELECT * FROM datasets WHERE id=?", (key,))
        if not row:
            raise HTTPException(404, "数据集不存在")
        row["samples"] = json.loads(row["samples"])
        return row

    @app.post("/api/datasets/{key}/samples")
    def add_sample(key: str, body: SampleInput, database=Depends(authorize)):
        """Append one file/ground-truth pair under a write transaction so concurrent additions cannot be lost."""
        if key in database.archived("datasets"):
            raise HTTPException(410, "此历史记录已归档")
        if not database.get("files", body.file_id):
            raise HTTPException(404, "文件不存在")
        # Hold a write transaction to avoid losing concurrent additions.
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT samples FROM datasets WHERE id=?", (key,)).fetchone()
            if not row:
                raise HTTPException(404, "数据集不存在")
            samples = json.loads(row["samples"])
            if len(samples) >= 50:
                raise HTTPException(422, "首版每个数据集最多 50 个样本")
            samples.append({"id": uid(), **body.model_dump()})
            db.execute("UPDATE datasets SET samples=? WHERE id=?", (json.dumps(samples), key))
        return {"id": key, "samples": samples}

    @app.post("/api/datasets/{key}/runs")
    def run_dataset(key: str, body: RunInput, database=Depends(authorize)):
        """Resolve the fixed template version and atomically enqueue all sample jobs plus the evaluation snapshot."""
        row = dataset(key, database)
        if not row["samples"]:
            raise HTTPException(422, "先添加包含标准答案的样本")
        template = database.template(body.template_id, body.version)
        if not template or not database.get("providers", body.provider_id):
            raise HTTPException(422, "模板或模型配置不存在")
        requests = [
            JobInput(
                template_id=body.template_id,
                version=template["version"],
                provider_id=body.provider_id,
                file_ids=[sample["file_id"]],
            )
            for sample in row["samples"]
        ]
        run = {
            "dataset_id": key,
            "dataset_name": row["name"],
            "template_id": template["id"],
            "version": template["version"],
            "provider_id": body.provider_id,
        }
        try:
            return create_evaluation(database, requests, run, row["samples"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/runs")
    def runs(database=Depends(authorize)):
        """List real-model evaluation snapshots without exposing archived obsolete runs."""
        return [
            {"id": r["id"], **database.get("runs", r["id"])}
            for r in database.all("SELECT id FROM runs ORDER BY created DESC LIMIT 100")
            if r["id"] not in database.archived("runs")
        ]

    @app.get("/api/runs/{key}")
    def run_report(key: str, database=Depends(authorize)):
        """Score saved predictions against the immutable ground-truth snapshot.

        Pending runs have no final accuracy. Failed/missing fields remain in the denominator, and dataset fingerprints allow fair comparisons."""
        if key in database.archived("runs"):
            raise HTTPException(410, "此历史记录已归档")
        run = database.get("runs", key)
        if not run:
            raise HTTPException(404, "评测不存在")
        samples, pending, matched, total = [], 0, 0, 0
        for sample in run["samples"]:
            task = database.job(sample["job_id"])
            waiting = task["status"] in {"queued", "running"}
            pending += waiting
            prediction = task["results"][0].get("data") if task["results"] else None
            comparison = compare(sample["gold"], prediction)
            matched += comparison["matched"]
            total += comparison["total"]
            samples.append({"id": sample["id"], "job_id": task["id"], "status": task["status"], **comparison})
        return {
            "id": key,
            "dataset_name": run["dataset_name"],
            "dataset_fingerprint": hashlib.sha256(
                json.dumps([(s["file_id"], s["gold"]) for s in run["samples"]], sort_keys=True).encode()
            ).hexdigest(),
            "template_id": run["template_id"],
            "provider_id": run["provider_id"],
            "version": run["version"],
            "status": "running" if pending else "completed",
            "pending": pending,
            "matched": matched,
            "total": total,
            "accuracy": matched / total if not pending and total else None,
            "samples": samples,
        }

    return app


def secrets_equal(left, right):
    """Compare UTF-8 credentials in constant time rather than using ordinary string equality."""
    return hmac.compare_digest(left.encode(), right.encode())


def public_provider(body):
    """Return displayable connection settings with only a key-presence flag, never the API key itself."""
    return {k: v for k, v in body.items() if k not in {"api_key", "api_key_encrypted"}} | {
        "has_api_key": bool(body.get("api_key") or body.get("api_key_encrypted"))
    }


def public_file(body):
    """Expose display metadata while keeping server storage paths/keys private."""
    return {k: v for k, v in body.items() if k != "storage_key"}


app = create_app()
