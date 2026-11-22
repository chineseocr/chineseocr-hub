"""Advanced geometry/localization for existing schema and joint-PDF extraction.

Business JSON is extracted once for the selected page group. Optional location
requests then examine each supplied page with stable JSON-pointer field IDs.
Locations remain separate metadata and always identify the actual source page.
"""

import json
import time

from PIL import Image

from .adapters import BACKENDS, BackendError
from .document_analysis import analyze_fields, usage
from .documents import preview_path
from .domain import render_prompt, schema_errors
from .geometry import prepare_image
from .recognition import _call, _decorate, _interrupted
from .vision import image_png


def process_structured(worker, key):
    job = worker.store.job(key)
    payload, results = job["payload"], job["results"]
    provider = worker.store.get("providers", payload["provider_id"])
    options = payload["processing"] | {"output_format": "text"}
    template = payload["template"]

    def checkpoint():
        for result in results:
            stages = result.get("analysis", {})
            responses = [stages["extraction"]] if "extraction" in stages else []
            for page_stages in stages.get("pages", {}).values():
                responses.extend(page_stages.get("localization", {}).values())
            if responses:
                result["usage"] = usage(responses)
        worker.store.execute(
            "UPDATE jobs SET results=?,updated=? WHERE id=?", (json.dumps(results), time.time(), key)
        )

    try:
        for index, unit in enumerate(payload["units"]):
            if _interrupted(worker, key):
                raise InterruptedError
            if index < len(results) and results[index]["status"] in {
                "succeeded",
                "partial",
                "failed",
                "invalid",
            }:
                continue
            if index == len(results):
                results.append(dict(unit, status="running", data=None, validation_errors=[]))
            outcome = results[index]
            stages = outcome.setdefault("analysis", {})
            source = worker.store.get("files", unit["file_id"])
            images = []
            for page in unit["pages"]:
                with Image.open(preview_path(worker.store, source, page)) as loaded:
                    images.append(loaded.convert("RGB"))
            variables = {
                "schema": json.dumps(template["schema"], ensure_ascii=False),
                "filename": unit["filename"],
                "pages": ", ".join(map(str, unit["pages"])),
            }
            system = render_prompt(template["system_prompt"], variables)
            user = render_prompt(template["user_prompt"], variables)
            try:
                if "extraction" not in stages:
                    prepared = [
                        image_png(
                            prepare_image(image, rotation=options["rotation"], deskew=options["deskew"])[0]
                        )
                        for image in images
                    ]
                    stages["extraction"] = _call(
                        worker,
                        key,
                        provider,
                        lambda: BACKENDS[provider["kind"]].extract(provider, system, user, prepared),
                    )
                    checkpoint()
                fields, transforms, localizations, statuses = [], [], [], []
                for page, image in zip(unit["pages"], images):
                    if _interrupted(worker, key):
                        raise InterruptedError
                    answer = analyze_fields(
                        provider,
                        image,
                        options,
                        stages.setdefault("pages", {}).setdefault(str(page), {}),
                        system,
                        user,
                        extraction=stages["extraction"],
                        call=lambda action: _call(worker, key, provider, action),
                        checkpoint=checkpoint,
                    )
                    outcome.update({k: answer[k] for k in ("data", "raw_text", "error") if k in answer})
                    if answer["data"] is None:
                        outcome["status"] = "invalid"
                        break
                    metadata = {"blocks": answer["field_locations"]}
                    _decorate(metadata, source["pages"][page - 1])
                    fields.extend(dict(item, page=page) for item in metadata["blocks"])
                    transforms.append(dict(answer["preprocessing"], page=page))
                    localizations.append(dict(answer["localization"], page=page))
                    statuses.append(answer["status"])
                    outcome.update(
                        field_locations=fields, preprocessing=transforms, localization=localizations
                    )
                    checkpoint()
                if outcome["data"] is not None:
                    outcome["validation_errors"] = schema_errors(template["schema"], outcome["data"])
                    outcome["status"] = (
                        "invalid"
                        if outcome["validation_errors"]
                        else "partial"
                        if "partial" in statuses
                        else "succeeded"
                    )
            except (BackendError, ValueError, TypeError):
                outcome.update(status="failed", error="识别阶段失败，请检查原始响应和模型配置")
            checkpoint()
        statuses = [r["status"] for r in results]
        state = (
            "succeeded"
            if all(s == "succeeded" for s in statuses)
            else "partial"
            if any(s in {"succeeded", "partial"} for s in statuses)
            else "failed"
        )
        worker.finish(key, state, results)
    except InterruptedError:
        worker.finish(key, "queued" if worker.stop.is_set() else "cancelled", results)
    except Exception:
        worker.store.execute(
            "UPDATE jobs SET status='failed',error=?,updated=? WHERE id=?",
            ("任务内部处理失败，请检查文件和模型配置", time.time(), key),
        )
