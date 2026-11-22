"""Durable single-worker queue. SQLite claims survive process termination."""

import hashlib
import json
import sqlite3
import threading
import time

from .adapters import BACKENDS, BackendError
from .documents import preview_path
from .domain import decode_json, render_prompt, schema_errors
from .store import uid


def prepare_job(store, request):
    """Validate limits and resolve immutable configuration before enqueuing any work."""
    template = store.template(request.template_id, request.version)
    provider = store.get("providers", request.provider_id)
    if not template or not provider:
        raise ValueError("模板版本或模型配置不存在")
    if provider["kind"] not in BACKENDS:
        raise ValueError("模型连接已停用，请选择真实模型服务")
    if len(set(request.file_ids)) != len(request.file_ids):
        raise ValueError("不能重复提交同一个文件")
    files = []
    units = []
    for key in request.file_ids:
        file = store.get("files", key)
        if not file or key in store.archived("files"):
            raise ValueError("上传文件不存在")
        selected = sorted(set(request.pages or [p["page"] for p in file["pages"]]))
        if not selected or any(p < 1 or p > len(file["pages"]) for p in selected):
            raise ValueError("页码超出文件范围（从 1 开始）")
        if request.mode == "document" and len(selected) > 8:
            raise ValueError("整份文档模式最多联合处理 8 页；更大文件请使用逐页模式")
        files.append({"id": key, "name": file["name"]})
        groups = [selected] if request.mode == "document" else [[p] for p in selected]
        units.extend({"file_id": key, "filename": file["name"], "pages": pages} for pages in groups)
    if len(units) > 20:
        raise ValueError("每个任务最多 20 个处理单元")
    payload = {
        "template": template,
        "provider_id": request.provider_id,
        "provider_name": provider["name"],
        "model": provider["model"],
        "mode": request.mode,
        "files": files,
        "units": units,
    }
    if request.deskew or request.rotation or request.position_mode == "contextual":
        from .table_recognition import LOCATE_SYSTEM

        payload["processing"] = {
            "rotation": request.rotation,
            "deskew": request.deskew,
            "position_mode": request.position_mode,
            "max_localization_batches": request.max_localization_batches,
            "prompts": {"locate_system": LOCATE_SYSTEM},
        }
    return json.dumps(payload, sort_keys=True)


def create_job(store, request, idem=None):
    """Create one durable job with optional idempotency.

    The fingerprint covers resolved template/provider/file snapshots. Reusing a key with different input fails rather than silently returning another result."""
    return enqueue_job(store, prepare_job(store, request), idem)


def enqueue_job(store, encoded, idem=None):
    """Admit an already validated/frozen extraction or recognition payload atomically."""
    fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
    if idem:
        old = store.one("SELECT id,fingerprint FROM jobs WHERE idem=?", (idem,))
        if old:
            if old["fingerprint"] != fingerprint:
                raise FileExistsError("相同幂等键已用于不同请求；重试时请固定模板版本")
            return old["id"]
    key, now = uid(), time.time()
    try:
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if (
                db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
                >= 100
            ):
                raise ValueError("任务队列已满（100 个），请等待完成")
            db.execute(
                "INSERT INTO jobs(id,status,payload,created,updated,idem,fingerprint) VALUES(?,?,?,?,?,?,?)",
                (key, "queued", encoded, now, now, idem, fingerprint),
            )
            store.reserve_job_usage(db, key, encoded, now)
    except sqlite3.IntegrityError:
        old = store.one("SELECT id,fingerprint FROM jobs WHERE idem=?", (idem,))
        if not old or old["fingerprint"] != fingerprint:
            raise FileExistsError("幂等键冲突") from None
        return old["id"]
    return key


def create_evaluation(store, requests, run, samples):
    """Preflight all samples; commit jobs and the run in one transaction."""
    encoded = [prepare_job(store, request) for request in requests]
    keys = [uid() for _ in encoded]
    run = dict(run, samples=[dict(sample, job_id=key) for sample, key in zip(samples, keys)])
    run_id, now = uid(), time.time()
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        count = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
        if count + len(keys) > 100:
            raise ValueError("任务队列容量不足，请等待当前任务结束")
        db.executemany(
            "INSERT INTO jobs(id,status,payload,created,updated) VALUES(?,?,?,?,?)",
            [(key, "queued", payload, now, now) for key, payload in zip(keys, encoded)],
        )
        for key, payload in zip(keys, encoded):
            store.reserve_job_usage(db, key, payload, now)
        db.execute("INSERT INTO runs VALUES(?,?,?)", (run_id, json.dumps(run), now))
    return {"id": run_id, **run}


class Worker:
    def __init__(self, store):
        self.store = store
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True, name="extraction-worker")

    def start(self):
        # The application holds an OS-level workspace lock. No second process can reset live jobs.
        """Recover interrupted jobs after acquiring the workspace lock, then start the single worker thread."""
        self.store.execute("UPDATE jobs SET status='queued',updated=? WHERE status='running'", (time.time(),))
        self.thread.start()

    def close(self):
        """Request cooperative shutdown and wait for the bounded current model call to complete."""
        self.stop.set()
        self.thread.join(timeout=190)

    def run(self):
        """Claim queued work with a conditional update; sleep interruptibly when the durable queue is empty."""
        while not self.stop.is_set():
            job = self.store.one("SELECT id FROM jobs WHERE status='queued' ORDER BY created LIMIT 1")
            if not job:
                self.stop.wait(0.25)
                continue
            if self.store.execute(
                "UPDATE jobs SET status='running',updated=? WHERE id=? AND status='queued'",
                (time.time(), job["id"]),
            ):
                self.process(job["id"])

    def process(self, key):
        """Execute source-page units in order and checkpoint after each unit.

        Cancellation stops later units, not a request already sent. Restart skips persisted units; an uncheckpointed in-flight request may be repeated."""
        job = self.store.job(key)
        payload, results = job["payload"], job["results"]
        if payload.get("task") in {"ocr", "documents", "tables"}:
            from .recognition import process_recognition

            return process_recognition(self, key)
        if payload.get("processing", {}).get("prompts"):
            from .structured_analysis import process_structured

            return process_structured(self, key)
        provider = self.store.get("providers", payload["provider_id"])
        try:
            for index, unit in enumerate(payload["units"]):
                if self.store.job(key)["cancel"]:
                    self.finish(key, "cancelled", results)
                    return
                if self.stop.is_set():
                    self.finish(key, "queued", results)
                    return
                if index < len(results):
                    continue  # completed units are checkpointed and not resent after restart
                outcome = dict(unit, status="failed", data=None, validation_errors=[])
                try:
                    file = self.store.get("files", unit["file_id"])
                    images = [preview_path(self.store, file, page).read_bytes() for page in unit["pages"]]
                    template = payload["template"]
                    variables = {
                        "schema": json.dumps(template["schema"], ensure_ascii=False, indent=2),
                        "filename": unit["filename"],
                        "pages": ", ".join(map(str, unit["pages"])),
                    }
                    if self.store.provider_policy and not self.store.provider_policy(provider):
                        raise BackendError("模型服务地址不在平台允许列表，请联系管理员")
                    slots = self.store.model_slots
                    if slots:
                        while not slots.acquire(timeout=0.25):
                            if self.stop.is_set() or self.store.job(key)["cancel"]:
                                self.finish(key, "queued" if self.stop.is_set() else "cancelled", results)
                                return
                    try:
                        # A queued waiter may acquire just as shutdown/cancel is
                        # requested. Recheck after acquisition and still release.
                        if self.stop.is_set() or self.store.job(key)["cancel"]:
                            self.finish(key, "queued" if self.stop.is_set() else "cancelled", results)
                            return
                        outcome.update(
                            BACKENDS[provider["kind"]].extract(
                                provider,
                                render_prompt(template["system_prompt"], variables),
                                render_prompt(template["user_prompt"], variables),
                                images,
                            )
                        )
                    finally:
                        if slots:
                            slots.release()
                    outcome["data"] = decode_json(outcome["raw_text"])
                    outcome["validation_errors"] = schema_errors(template["schema"], outcome["data"])
                    outcome["status"] = "invalid" if outcome["validation_errors"] else "succeeded"
                except BackendError as exc:
                    outcome["error"] = str(exc)
                except (ValueError, TypeError):
                    outcome["error"] = "模型未返回有效 JSON object；请查看原始输出并调整提示词"
                results.append(outcome)
                self.store.execute(
                    "UPDATE jobs SET results=?,updated=? WHERE id=?", (json.dumps(results), time.time(), key)
                )
            cancelled = self.store.job(key)["cancel"]
            statuses = [r["status"] for r in results]
            state = "succeeded" if all(s == "succeeded" for s in statuses) else "partial"
            if not any(s == "succeeded" for s in statuses):
                state = "failed"
            self.finish(key, "cancelled" if cancelled else state, results)
        except Exception:
            # Keep exception text/credentials/private paths out of public responses.
            self.store.execute(
                "UPDATE jobs SET status='failed',error=?,updated=? WHERE id=?",
                ("任务内部处理失败，请检查文件和模型配置", time.time(), key),
            )

    def finish(self, key, status, results):
        """Commit the terminal state together with the latest checkpointed unit results."""
        self.store.execute(
            "UPDATE jobs SET status=?,results=?,updated=? WHERE id=?",
            (status, json.dumps(results), time.time(), key),
        )
