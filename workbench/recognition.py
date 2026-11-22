"""多模态全文与多文档任务：沿用持久化队列、租户权限和提交页额度。

定位结果和每个实例分别检查点落库。恢复时复用已定位区域及已完成实例，避免
同类证件互相覆盖；未落库的在途调用仍可能重复，不承诺模型侧恰好一次。
"""

import json
import math
import time
from typing import Literal

from PIL import Image
from pydantic import BaseModel, Field, model_validator

from .adapters import BACKENDS, BackendError
from .detection import locate
from .document_analysis import analyze, analyze_fields
from .documents import preview_path
from .domain import decode_json, render_prompt, schema_errors
from .geometry import map_box, prepare_image
from .store import uid
from .table_recognition import LOCATE_SYSTEM, TABLE_SYSTEM, TABLE_USER
from .vision import (
    DETECT_SYSTEM,
    DETECT_USER,
    LABELS,
    OCR_SYSTEM,
    OCR_USERS,
    VisionOutputError,
    image_png,
    recognize,
    rotate_image,
    source_box,
)


class TemplateRef(BaseModel):
    template_id: str = Field(min_length=1, max_length=100)
    version: int | None = Field(default=None, ge=1)


class RecognitionInput(BaseModel):
    task: Literal["ocr", "documents", "tables"] = "ocr"
    provider_id: str = Field(min_length=1, max_length=100)
    file_ids: list[str] = Field(min_length=1, max_length=5)
    pages: list[int] = Field(default_factory=list, max_length=20)
    output_format: Literal["text", "markdown"] = "text"
    rotation: Literal[0, 90, 180, 270] = 0
    deskew: bool = False
    grid_assist: bool = True
    position_mode: Literal["direct", "contextual"] = "direct"
    max_localization_batches: int = Field(default=4, ge=1, le=8)
    routes: dict[str, TemplateRef] = Field(default_factory=dict, max_length=40)

    @model_validator(mode="after")
    def known_routes(self):
        if set(self.routes) - set(LABELS):
            raise ValueError("文档路由包含不支持的类别")
        if self.task != "documents" and self.routes:
            raise ValueError("全文 OCR 不使用字段路由")
        return self


def prepare_recognition(store, request):
    """Freeze route templates and provider reference before atomically admitting pages."""
    provider = store.get("providers", request.provider_id)
    if not provider or provider.get("kind") not in BACKENDS:
        raise ValueError("模型配置不存在")
    if store.provider_policy and not store.provider_policy(provider):
        raise ValueError("模型服务地址不在平台允许列表，请联系管理员")
    if len(set(request.file_ids)) != len(request.file_ids):
        raise ValueError("不能重复提交同一个文件")
    routes = {}
    for label, ref in request.routes.items():
        template = store.template(ref.template_id, ref.version)
        if not template:
            raise ValueError("模板版本不存在")
        routes[label] = template
    files, units = [], []
    for key in request.file_ids:
        file = store.get("files", key)
        if not file or key in store.archived("files"):
            raise ValueError("上传文件不存在")
        pages = sorted(set(request.pages or [p["page"] for p in file["pages"]]))
        if not pages or any(p < 1 or p > len(file["pages"]) for p in pages):
            raise ValueError("页码超出文件范围（从 1 开始）")
        files.append({"id": key, "name": file["name"]})
        units.extend({"file_id": key, "filename": file["name"], "pages": [p]} for p in pages)
    if len(units) > 20:
        raise ValueError("每个任务最多 20 个处理单元")
    return json.dumps(
        {
            "task": request.task,
            "routes": routes,
            "provider_id": request.provider_id,
            "provider_name": provider["name"],
            "model": provider["model"],
            "files": files,
            "units": units,
            "output_format": request.output_format,
            "rotation": request.rotation,
            "deskew": request.deskew,
            "grid_assist": request.grid_assist,
            "position_mode": request.position_mode,
            "max_localization_batches": request.max_localization_batches,
            "contract_version": 2,
            "prompts": {
                "ocr_system": OCR_SYSTEM,
                "ocr_user": OCR_USERS[request.output_format],
                "detection_system": DETECT_SYSTEM,
                "detection_user": DETECT_USER,
                "table_system": TABLE_SYSTEM,
                "table_user": TABLE_USER,
                "locate_system": LOCATE_SYSTEM,
            },
        },
        sort_keys=True,
    )


def _call(worker, job_id, provider, action):
    """Apply the operator allowlist and shared model concurrency to every stage."""
    if worker.store.provider_policy and not worker.store.provider_policy(provider):
        raise BackendError("模型服务地址不在平台允许列表，请联系管理员")
    slots = worker.store.model_slots
    if slots:
        while not slots.acquire(timeout=0.25):
            if _interrupted(worker, job_id):
                raise InterruptedError
    try:
        if _interrupted(worker, job_id):
            raise InterruptedError
        return action()
    finally:
        if slots:
            slots.release()


def _interrupted(worker, job_id):
    return worker.stop.is_set() or worker.store.job(job_id)["cancel"]


def _checkpoint(worker, key, results):
    """Persist actual paid usage together with each localization/instance checkpoint.

    An interrupted page is still visible to usage reporting. Missing token fields
    stay missing rather than becoming zero or an estimate of an upstream bill.
    """
    for outcome in results:
        for record in [outcome, *outcome.get("instances", [])]:
            stages = record.get("analysis", {})
            if "extraction" in stages:
                record["usage"] = _usage([stages["extraction"], *stages.get("localization", {}).values()])
        if "detection" in outcome:
            outcome["usage"] = _usage([outcome["detection"], *outcome.get("instances", [])])
    worker.store.execute(
        "UPDATE jobs SET results=?,updated=? WHERE id=?", (json.dumps(results), time.time(), key)
    )


def _usage(responses):
    """Sum only supplied nonnegative token counters; no estimates for absent usage."""
    reported = [r.get("usage") for r in responses if isinstance(r.get("usage"), dict)]
    if not reported:
        return None
    totals = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        values = [
            u[key]
            for u in reported
            if isinstance(u.get(key), int) and not isinstance(u.get(key), bool) and u[key] >= 0
        ]
        if values:
            totals[key] = sum(values)
    return totals or None


def _decorate(data, page, offset=(0, 0)):
    """Attach source coordinates while retaining model-reported preview provenance."""
    data["source_unit"] = page.get("source_unit", "preview_pixels")
    # Crop OCR starts in crop coordinates; after adding its offset, every box is
    # in the full source-page canvas, so its declared dimensions must match too.
    data["width"], data["height"] = page["width"], page["height"]
    items = data["blocks"] + data.get("fields", [])
    for table in data.get("tables", []):
        items += table["cells"]
    for block in items:
        if block["box"] is not None:
            block["box"] = [v + offset[i % 2] for i, v in enumerate(block["box"])]
            block["source_box"] = source_box(block["box"], page)
        else:
            block["source_box"] = None
        if block.get("polygon"):
            block["polygon"] = [[x + offset[0], y + offset[1]] for x, y in block["polygon"]]
            block["source_polygon"] = [source_box([x, y], page) for x, y in block["polygon"]]
    return data


def process_recognition(worker, key):
    """Resume at page/instance checkpoints; one instance failure leaves its peers intact."""
    job = worker.store.job(key)
    payload, results = job["payload"], job["results"]
    provider = worker.store.get("providers", payload["provider_id"])
    try:
        for index, unit in enumerate(payload["units"]):
            if _interrupted(worker, key):
                raise InterruptedError
            if index < len(results) and results[index]["status"] in {
                "succeeded",
                "failed",
                "invalid",
                "partial",
            }:
                continue
            if index == len(results):
                results.append(dict(unit, status="running", data=None, validation_errors=[]))
            outcome = results[index]
            file = worker.store.get("files", unit["file_id"])
            page = file["pages"][unit["pages"][0] - 1]
            image = Image.open(preview_path(worker.store, file, unit["pages"][0])).convert("RGB")
            started = time.monotonic()
            try:
                if payload["task"] in {"ocr", "tables"}:
                    if (
                        payload["task"] == "tables"
                        or payload.get("deskew")
                        or payload.get("position_mode") == "contextual"
                    ):
                        answer = analyze(
                            provider,
                            image,
                            payload,
                            outcome.setdefault("analysis", {}),
                            call=lambda action: _call(worker, key, provider, action),
                            checkpoint=lambda: _checkpoint(worker, key, results),
                        )
                    else:
                        answer = _call(
                            worker,
                            key,
                            provider,
                            lambda: recognize(
                                provider,
                                image_png(image),
                                output_format=payload["output_format"],
                                rotation=payload["rotation"],
                                system_prompt=payload.get("prompts", {}).get("ocr_system"),
                                user_prompt=payload.get("prompts", {}).get("ocr_user"),
                            ),
                        )
                    outcome.update(answer)
                    if answer.get("data"):
                        _decorate(answer["data"], page)
                else:
                    # The first successful localization is persisted before any
                    # crop call. Instance IDs and boxes survive service restarts.
                    if "instances" not in outcome:
                        prepared, geometry = prepare_image(
                            image, rotation=payload["rotation"], deskew=payload.get("deskew", False)
                        )
                        regions, detection = _call(
                            worker,
                            key,
                            provider,
                            lambda: locate(
                                provider,
                                image_png(prepared),
                                system_prompt=payload.get("prompts", {}).get("detection_system"),
                                user_prompt=payload.get("prompts", {}).get("detection_user"),
                            ),
                        )
                        outcome["detection"] = {
                            k: detection[k] for k in ("usage", "raw_text", "duration_ms") if k in detection
                        }
                        outcome["detection"].update(
                            model_coordinate_space="normalized_0_1000",
                            inference_width=prepared.width,
                            inference_height=prepared.height,
                            rotation=payload["rotation"],
                            preprocessing=geometry,
                        )
                        outcome["instances"] = []
                        for region in regions:
                            bounds, polygon = map_box(region["box"], geometry)
                            if bounds is None:
                                raise ValueError("文档区域映射无效")
                            # Raster crops round outward; the precise model
                            # polygon remains available for source overlays.
                            box = [math.floor(v) if i < 2 else math.ceil(v) for i, v in enumerate(bounds)]
                            if box[2] - box[0] < 8 or box[3] - box[1] < 8:
                                raise ValueError("文档区域过小，无法裁剪识别")
                            outcome["instances"].append(
                                {
                                    "id": uid(),
                                    "label": region["label"],
                                    "box": box,
                                    "model_box": region["box"],
                                    "polygon": polygon,
                                    "source_box": source_box(box, page),
                                    "source_unit": page.get("source_unit", "preview_pixels"),
                                    "detection_confidence": None,
                                    "position_source": "model_reported",
                                    "status": "queued",
                                    "data": None,
                                    "validation_errors": [],
                                }
                            )
                        _checkpoint(worker, key, results)
                    for instance in outcome["instances"]:
                        if instance["status"] != "queued":
                            continue
                        if _interrupted(worker, key):
                            raise InterruptedError
                        crop = image.crop(instance["box"])
                        template = payload["routes"].get(instance["label"])
                        try:
                            if template:
                                variables = {
                                    "schema": json.dumps(template["schema"], ensure_ascii=False),
                                    "filename": unit["filename"] + " / " + instance["id"],
                                    "pages": str(unit["pages"][0]),
                                }
                                if payload.get("deskew") or payload.get("position_mode") == "contextual":
                                    answer = analyze_fields(
                                        provider,
                                        crop,
                                        payload,
                                        instance.setdefault("analysis", {}),
                                        render_prompt(template["system_prompt"], variables),
                                        render_prompt(template["user_prompt"], variables),
                                        call=lambda action: _call(worker, key, provider, action),
                                        checkpoint=lambda: _checkpoint(worker, key, results),
                                    )
                                else:
                                    answer = _call(
                                        worker,
                                        key,
                                        provider,
                                        lambda: BACKENDS[provider["kind"]].extract(
                                            provider,
                                            render_prompt(template["system_prompt"], variables),
                                            render_prompt(template["user_prompt"], variables),
                                            [image_png(rotate_image(crop, payload["rotation"]))],
                                        ),
                                    )
                                instance.update(answer)
                                instance["data"] = (
                                    answer["data"] if "data" in answer else decode_json(answer["raw_text"])
                                )
                                instance["validation_errors"] = schema_errors(
                                    template["schema"], instance["data"]
                                )
                                instance["status"] = (
                                    "invalid"
                                    if instance["validation_errors"]
                                    else answer.get("status", "succeeded")
                                )
                                if instance.get("field_locations"):
                                    _decorate(
                                        {"blocks": instance["field_locations"]}, page, instance["box"][:2]
                                    )
                                    for field in instance["field_locations"]:
                                        field["overlay_id"] = instance["id"] + ":" + field["id"]
                                instance["template"] = {
                                    "id": template["id"],
                                    "version": template["version"],
                                    "name": template["name"],
                                }
                            else:
                                if payload.get("deskew") or payload.get("position_mode") == "contextual":
                                    answer = analyze(
                                        provider,
                                        crop,
                                        payload | {"task": "ocr"},
                                        instance.setdefault("analysis", {}),
                                        call=lambda action: _call(worker, key, provider, action),
                                        checkpoint=lambda: _checkpoint(worker, key, results),
                                    )
                                else:
                                    answer = _call(
                                        worker,
                                        key,
                                        provider,
                                        lambda: recognize(
                                            provider,
                                            image_png(crop),
                                            output_format=payload["output_format"],
                                            rotation=payload["rotation"],
                                            system_prompt=payload.get("prompts", {}).get("ocr_system"),
                                            user_prompt=payload.get("prompts", {}).get("ocr_user"),
                                        ),
                                    )
                                instance.update(answer)
                                if answer.get("data"):
                                    _decorate(answer["data"], page, instance["box"][:2])
                        except (BackendError, ValueError, TypeError):
                            instance.update(
                                status="failed", error="文档实例识别失败，请检查原始输出或重跑任务"
                            )
                        _checkpoint(worker, key, results)
                    instances = outcome["instances"]
                    statuses = [i["status"] for i in instances]
                    outcome["status"] = (
                        (
                            "succeeded"
                            if all(s == "succeeded" for s in statuses)
                            else "partial"
                            if any(s in {"succeeded", "partial"} for s in statuses)
                            else "failed"
                        )
                        if instances
                        else "succeeded"
                    )
                    outcome["data"] = {
                        "documents": [
                            {k: v for k, v in i.items() if k not in {"raw_text", "usage"}} for i in instances
                        ],
                        "text": "\n\n".join(
                            (
                                json.dumps(i["data"], ensure_ascii=False, indent=2)
                                if i.get("template")
                                else i.get("data", {}).get("text", "")
                            )
                            for i in instances
                            if isinstance(i.get("data"), dict)
                        ),
                        "empty_detection": not bool(instances),
                        "review_required": True,
                    }
                    outcome["usage"] = _usage([outcome.get("detection", {}), *instances])
                outcome["duration_ms"] = round((time.monotonic() - started) * 1000)
            except VisionOutputError as exc:
                outcome.update(
                    status="invalid",
                    error=str(exc),
                    detection={
                        k: exc.response[k] for k in ("usage", "raw_text", "duration_ms") if k in exc.response
                    },
                )
                outcome["usage"] = exc.response.get("usage")
            except (BackendError, ValueError, TypeError):
                outcome.update(status="failed", error="识别阶段失败，请检查原始响应和模型配置")
            _checkpoint(worker, key, results)
        statuses = [r["status"] for r in results]
        status = (
            "succeeded"
            if all(s == "succeeded" for s in statuses)
            else "partial"
            if any(s in {"succeeded", "partial"} for s in statuses)
            else "failed"
        )
        worker.finish(key, status, results)
    except InterruptedError:
        worker.finish(key, "queued" if worker.stop.is_set() else "cancelled", results)
    except Exception:
        worker.store.execute(
            "UPDATE jobs SET status='failed',error=?,updated=? WHERE id=?",
            ("任务内部处理失败，请检查文件和模型配置", time.time(), key),
        )
