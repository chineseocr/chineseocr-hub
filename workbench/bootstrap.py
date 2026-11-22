"""Workspace initialization and non-destructive upgrades.

生产运行只建立字段模板，不植入文件、答案或虚假识别连接。环境变量可以在
部署时建立默认模型配置；未设置模型时，界面会要求管理员连接真实服务。
已有配置和历史结果不能被升级覆盖，因此旧版模拟记录采用归档标记保留。
"""

import json
import os
import time

from pydantic import ValidationError

from .domain import INVOICE_SCHEMA, ProviderInput, TemplateInput
from .store import uid


def initialize_workspace(store, model_environment=True):
    """Initialize a clean workspace or upgrade an earlier local candidate in place."""
    retire_legacy_records(store)
    if not store.one("SELECT id FROM templates LIMIT 1"):
        template = TemplateInput(name="Invoice / 票据提取", schema=INVOICE_SCHEMA)
        store.save_template(uid(), template.model_dump(by_alias=True))

    # SaaS tenants never inherit the operator's standalone provider or API key.
    if not model_environment:
        return

    # Bootstrap from explicit deployment configuration only. The caller's real model
    # address must stay in local state/environment, never in committed source defaults.
    base_url = os.environ.get("WORKBENCH_MODEL_BASE_URL", "").strip()
    model = os.environ.get("WORKBENCH_MODEL_NAME", "").strip()
    if bool(base_url) != bool(model):
        raise ValueError("WORKBENCH_MODEL_BASE_URL and WORKBENCH_MODEL_NAME must be set together")
    if base_url:
        try:
            configured = ProviderInput(
                name=os.environ.get("WORKBENCH_MODEL_LABEL") or model,
                base_url=base_url,
                model=model,
                api_key=os.environ.get("WORKBENCH_MODEL_API_KEY", ""),
                request_profile=os.environ.get("WORKBENCH_MODEL_REQUEST_PROFILE", "compatible"),
            )
        except ValidationError:
            # Pydantic's default exception includes input values; startup logs must
            # never print an invalid API key or a URL containing embedded secrets.
            raise ValueError(
                "Invalid model environment configuration; check URL, model ID and key length"
            ) from None
        existing = next(
            (
                r["id"]
                for r in store.all("SELECT id,body FROM providers")
                # Profiles added after the first Alpha must not duplicate an
                # otherwise identical environment-created legacy connection.
                if {"request_profile": "compatible", **json.loads(r["body"])} == configured.model_dump()
            ),
            None,
        )
        key = existing or store.put("providers", configured.model_dump())
        store.metadata("default_provider_id", key)


def retire_legacy_records(store):
    """Hide obsolete fixed-output records without deleting or relabeling their contents.

    The migration is idempotent. Historical data remains available in database backups;
    it cannot be rerun as if it came from a real model. User-created real-model data is
    left untouched. Only the precisely identified original synthetic starter is retired.
    """
    if store.metadata("real_model_upgrade") == "1":
        return
    retired = []
    legacy_providers = {
        r["id"]
        for r in store.all("SELECT id,body FROM providers")
        if json.loads(r["body"]).get("kind") != "openai"
    }
    retired.extend(("providers", key) for key in legacy_providers)
    legacy_jobs = set()
    for row in store.all("SELECT id,payload FROM jobs"):
        payload = json.loads(row["payload"])
        if payload.get("simulated") or payload.get("provider_id") in legacy_providers:
            legacy_jobs.add(row["id"])
            retired.append(("jobs", row["id"]))
    for row in store.all("SELECT id,body FROM runs"):
        run = json.loads(row["body"])
        if any(s["job_id"] in legacy_jobs for s in run["samples"]):
            retired.append(("runs", row["id"]))
    for row in store.all("SELECT * FROM datasets"):
        samples = json.loads(row["samples"])
        if (
            row["name"] == "合成票据验证集"
            and samples
            and all(s.get("gold", {}).get("invoice_number") == "DEMO-2026-001" for s in samples)
        ):
            retired.append(("datasets", row["id"]))
            for sample in samples:
                file = store.get("files", sample["file_id"])
                if file and file["name"] == "synthetic-invoice.png":
                    retired.append(("files", sample["file_id"]))
    with store.connect() as db:
        db.executemany(
            "INSERT OR IGNORE INTO archived_records VALUES(?,?,?)",
            [(collection, key, "Retired fixed-output starter") for collection, key in retired],
        )
        for key in legacy_jobs:
            db.execute(
                "UPDATE jobs SET status='cancelled',cancel=1,updated=? WHERE id=? AND status IN ('queued','running')",
                (time.time(), key),
            )
        db.execute("INSERT OR REPLACE INTO metadata VALUES('real_model_upgrade','1')")

    # A new version updates only the known starter name, preserving user edits/history.
    for row in store.all("SELECT DISTINCT id FROM templates"):
        template = store.template(row["id"])
        if template["name"] == "通用票据 · 合成示例":
            body = {k: template[k] for k in ("name", "schema", "system_prompt", "user_prompt")}
            body["name"] = "Invoice / 票据提取"
            store.save_template(row["id"], body)
