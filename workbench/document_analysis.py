"""Checkpointed multimodal document analysis shared by OCR and generic tables.

Every paid response is persisted before validation or the next network call.
A failed refinement preserves the extraction and its original positions. Call
limits are explicit; resuming does not repeat completed localization batches.
"""

import copy
import json

from .adapters import BACKENDS, BackendError
from .domain import decode_json
from .geometry import map_box, prepare_image
from .table_grid import assist_grid
from .table_recognition import (
    LOCATE_SYSTEM,
    TABLE_SYSTEM,
    TABLE_USER,
    apply_locations,
    normalize_tables,
    targets,
    text_matches,
)
from .vision import OCR_SYSTEM, OCR_USERS, image_png, valid_box


def usage(responses):
    reports = [r.get("usage") for r in responses if isinstance(r.get("usage"), dict)]
    if not reports:
        return None
    return {
        k: sum(r[k] for r in reports if type(r.get(k)) is int and r[k] >= 0)
        for k in ("prompt_tokens", "completion_tokens", "total_tokens")
        if any(type(r.get(k)) is int and r[k] >= 0 for r in reports)
    }


def _ocr_data(raw):
    if (
        not isinstance(raw.get("text"), str)
        or not isinstance(raw.get("blocks"), list)
        or len(raw["blocks"]) > 1000
    ):
        raise ValueError("多模态 OCR 未返回有效的 text 和 blocks")
    blocks = []
    for i, item in enumerate(raw["blocks"]):
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise ValueError("模型返回的文字块格式无效")
        box = valid_box(item.get("box"))
        blocks.append(
            {
                "id": f"line-{i + 1}",
                "text": item["text"],
                "model_box": box,
                "confidence": None,
                "position_source": "model_reported" if box else "unavailable",
            }
        )
    return {"text": raw["text"], "blocks": blocks}


def _locations(response, requests):
    """Validate items independently; one missing/malformed item cannot hide its peers."""
    raw = decode_json(response["raw_text"])
    entries = raw.get("locations")
    if isinstance(raw.get("field_locations"), dict):
        entries = [
            dict(value, id=key) for key, value in raw["field_locations"].items() if isinstance(value, dict)
        ]
    if not isinstance(entries, list) or len(entries) > max(32, len(requests) * 2):
        raise ValueError("精细定位输出格式无效")
    expected = {r["id"]: r for r in requests}
    result, seen = {}, set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            continue
        key = entry["id"]
        if key not in expected or key in seen:
            result.pop(key, None)
            continue
        seen.add(key)
        try:
            box = valid_box(entry.get("bbox"))
        except ValueError:
            continue
        matched = entry.get("matched_text")
        if (
            isinstance(expected[key]["value"], list)
            and isinstance(matched, list)
            and all(isinstance(v, str) for v in matched)
        ):
            matched = " ".join(matched)
        if box is None or not isinstance(matched, str):
            continue
        value = expected[key]["value"]
        compact = "".join(matched.split())
        verified = (
            all("".join(v.split()) in compact for v in value)
            if isinstance(value, list)
            else text_matches(value, matched, expected[key].get("label", ""))
        )
        if verified and compact:
            result[key] = {"bbox": box, "matched_text": matched}
    return result


def _map_data(data, transform, output_format):
    items = data["blocks"] + data.get("fields", [])
    for table in data.get("tables", []):
        items += table["cells"]
    for item in items:
        item["box"], item["polygon"] = map_box(item.get("geometry_box") or item.get("model_box"), transform)
        if item["box"] is None:
            item["position_source"] = "unavailable"
    data.update(
        format=output_format,
        rotation=transform["rotation"],
        coordinate_space="rendered_page_pixels",
        model_coordinate_space="normalized_0_1000",
        width=transform["source_width"],
        height=transform["source_height"],
        inference_width=transform["inference_width"],
        inference_height=transform["inference_height"],
        preprocessing=transform,
        review_required=True,
    )
    return data


def analyze(provider, image, options, stages, *, call, checkpoint):
    """Run extraction and optionally bounded contextual localization.

    call() enforces the shared provider policy/concurrency/cancellation gateway.
    checkpoint() writes the caller-owned stages dictionary into its job record.
    Neither this module nor the deskew step creates another OCR backend.
    """
    prepared, transform = prepare_image(
        image, rotation=options["rotation"], deskew=options.get("deskew", False)
    )
    # Frozen geometry keeps resumed stages auditable even if preprocessing code
    # changes. Do not apply cached model coordinates to a different canvas.
    if stages.get("preprocessing") not in (None, transform):
        raise ValueError("预处理版本已变化，请创建新任务")
    stages["preprocessing"] = transform
    prompts = options["prompts"]
    table_task = options["task"] == "tables"
    if "extraction" not in stages:
        system = (
            prompts.get("table_system", TABLE_SYSTEM) if table_task else prompts.get("ocr_system", OCR_SYSTEM)
        )
        user = (
            prompts.get("table_user", TABLE_USER)
            if table_task
            else prompts.get("ocr_user", OCR_USERS[options["output_format"]])
        )
        stages["extraction"] = call(
            lambda: BACKENDS[provider["kind"]].extract(provider, system, user, [image_png(prepared)])
        )
        checkpoint()
    response = stages["extraction"]
    paid = [response]
    try:
        raw = decode_json(response["raw_text"])
        data = normalize_tables(raw) if table_task else _ocr_data(raw)
    except (ValueError, TypeError, RecursionError):
        return dict(response, status="invalid", data=None, error="识别结构无效，请检查原始模型输出")
    if table_task and options.get("grid_assist", True):
        assist_grid(data, prepared)
    localization = refine(provider, prepared, options, stages, data, call=call, checkpoint=checkpoint)
    paid.extend(stages.get("localization", {}).values())
    result = _map_data(copy.deepcopy(data), transform, options["output_format"])
    result["localization"] = localization
    incomplete_grid = any(table.get("unrecognized_grid_cells") for table in data.get("tables", []))
    return dict(
        response,
        data=result,
        usage=usage(paid),
        status="partial" if localization["status"] == "partial" or incomplete_grid else "succeeded",
    )


def refine(provider, prepared, options, stages, data, *, call, checkpoint):
    """Refine a normalized document using frozen prompts and persisted batches."""
    prompts = options["prompts"]
    localization = {"mode": options.get("position_mode", "direct"), "status": "not_requested", "batches": 0}
    if options.get("position_mode") == "contextual":
        if "localization_requests" not in stages:
            if stages.get("localization"):
                raise ValueError("定位契约已变化，请创建新任务")
            stages["localization_requests"] = targets(data)
            checkpoint()
        requests = stages["localization_requests"]
        limit = options.get("max_localization_batches", 4)
        batches = [requests[i : i + 24] for i in range(0, min(len(requests), limit * 24), 24)]
        localization.update(
            status="succeeded",
            batches=len(batches),
            truncated=len(requests) > limit * 24,
            target_count=len(requests),
            matched_count=0,
            batch_results=[],
        )
        locations = {}
        cached = stages.setdefault("localization", {})
        for i, batch in enumerate(batches):
            key = str(i)
            if key not in cached:
                # Text itself may be long; bound request bytes as well as item
                # count. No implicit retry or hidden extra model call is made.
                user = json.dumps(batch, ensure_ascii=False)
                if len(user.encode()) > 48000:
                    cached[key] = {"error": "定位上下文超过大小限制"}
                else:
                    try:
                        cached[key] = call(
                            lambda: BACKENDS[provider["kind"]].extract(
                                provider,
                                prompts.get("locate_system", LOCATE_SYSTEM),
                                user,
                                [image_png(prepared)],
                            )
                        )
                    except BackendError:
                        cached[key] = {"error": "精细定位调用失败"}
                checkpoint()
            entry = cached[key]
            try:
                if "error" in entry:
                    raise ValueError(entry["error"])
                found = _locations(entry, batch)
                locations.update(found)
                localization["batch_results"].append(
                    {"batch": i, "status": "validated", "matched": len(found), "requested": len(batch)}
                )
            except (ValueError, TypeError, KeyError, RecursionError):
                localization["status"] = "partial"
                localization["batch_results"].append(
                    {"batch": i, "status": "invalid_or_failed", "matched": 0, "requested": len(batch)}
                )
        apply_locations(data, locations)
        localization["matched_count"] = len(locations)
        if localization["truncated"] or len(locations) < len(requests):
            localization["status"] = "partial"
    return localization


def analyze_fields(provider, image, options, stages, system, user, *, call, checkpoint, extraction=None):
    """Keep business JSON unchanged; return positions in a separate metadata list.

    This lets existing schema applications use contextual localization without
    breaking their output contract or injecting location keys into business data.
    """
    prepared, transform = prepare_image(
        image, rotation=options["rotation"], deskew=options.get("deskew", False)
    )
    if stages.get("preprocessing") not in (None, transform):
        raise ValueError("预处理版本已变化，请创建新任务")
    stages["preprocessing"] = transform
    if extraction is None and "extraction" not in stages:
        stages["extraction"] = call(
            lambda: BACKENDS[provider["kind"]].extract(provider, system, user, [image_png(prepared)])
        )
        checkpoint()
    response = extraction if extraction is not None else stages["extraction"]
    try:
        business = decode_json(response["raw_text"])
    except (ValueError, TypeError, RecursionError):
        return dict(response, data=None, status="invalid", error="识别结构无效，请检查原始模型输出")
    fields = []

    def visit(value, pointer=""):
        if len(fields) >= 300:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, pointer + "/" + str(key).replace("~", "~0").replace("/", "~1"))
        elif isinstance(value, list):
            for i, child in enumerate(value):
                visit(child, pointer + "/" + str(i))
        elif value is not None:
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            fields.append(
                {
                    "id": "field" + pointer,
                    "label": pointer,
                    "text": text,
                    "model_box": None,
                    "confidence": None,
                    "position_source": "unavailable",
                }
            )

    visit(business)
    metadata = {"blocks": [], "fields": fields}
    localization = refine(provider, prepared, options, stages, metadata, call=call, checkpoint=checkpoint)
    if len(fields) >= 300:
        localization.update(field_limit_reached=True, status="partial")
    _map_data(metadata, transform, options["output_format"])
    return dict(
        response,
        data=business,
        field_locations=metadata["fields"],
        preprocessing=transform,
        localization=localization,
        usage=usage([response, *stages.get("localization", {}).values()]),
        status="partial" if localization["status"] == "partial" else "succeeded",
    )


def recognize_document(
    provider,
    image,
    *,
    task="tables",
    rotation=0,
    deskew=False,
    grid_assist=True,
    position_mode="direct",
    max_localization_batches=4,
    output_format="text",
):
    """Standalone bytes API for the same table/OCR analysis used by the worker.

    The caller owns network authorization and input limits, as with vision.recognize.
    Use HTTP/CLI ingestion for bounded PDFs, durable stages and tenant policies.
    """
    import io

    from PIL import Image

    if (
        task not in {"tables", "ocr"}
        or rotation not in {0, 90, 180, 270}
        or position_mode not in {"direct", "contextual"}
        or type(max_localization_batches) is not int
        or not 1 <= max_localization_batches <= 8
        or output_format not in {"text", "markdown"}
    ):
        raise ValueError("识别输出格式或旋转角度无效")
    options = {
        "task": task,
        "rotation": rotation,
        "deskew": deskew,
        "grid_assist": grid_assist,
        "position_mode": position_mode,
        "max_localization_batches": max_localization_batches,
        "output_format": output_format,
        "prompts": {
            "ocr_system": OCR_SYSTEM,
            "ocr_user": OCR_USERS[output_format],
            "table_system": TABLE_SYSTEM,
            "table_user": TABLE_USER,
            "locate_system": LOCATE_SYSTEM,
        },
    }
    stages = {}
    with Image.open(io.BytesIO(image)) as source:
        source.load()
        result = analyze(
            provider, source, options, stages, call=lambda action: action(), checkpoint=lambda: None
        )
    result["analysis"] = stages
    return result
