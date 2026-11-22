"""Recognition contracts, geometry, tenant boundaries and instance recovery.

Responses below are protocol fixtures only. Live-model observations are recorded
separately; none of these fixtures are registered in production code.
"""

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from workbench.adapters import BACKENDS, BackendError
from workbench.app import create_app
from workbench.engine import Worker
from workbench.recognition import _decorate
from workbench.vision import image_png, page_box, recognize, source_box


class ScriptedModel:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def extract(self, provider, system, user, images):
        self.calls.append(
            {"system": system, "user": user, "sizes": [Image.open(io.BytesIO(b)).size for b in images]}
        )
        result = self.responses.pop(0)
        if callable(result):
            result = result()
        if isinstance(result, BaseException):
            raise result
        return {
            "raw_text": json.dumps(result, ensure_ascii=False),
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "duration_ms": 12,
        }


def ocr(text="visible text", box=None):
    return {"text": text, "blocks": [{"text": text, "box": box}]}


def detection():
    return {
        "regions": [
            {"label": "id_card_front", "box": [0, 0, 450, 1000]},
            {"label": "id_card_front", "box": [550, 0, 1000, 1000]},
        ]
    }


@pytest.fixture
def studio(tmp_path):
    app = create_app(tmp_path, start_worker=False)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + app.state.store.token
        provider = client.post("/api/providers", json={"name": "Protocol test", "model": "vision"}).json()
        image = Image.new("RGB", (200, 100), "red")
        image.paste("blue", (100, 0, 200, 100))
        out = io.BytesIO()
        image.save(out, "PNG")
        file = client.post("/api/files", files={"file": ("synthetic.png", out.getvalue())}).json()
        yield client, app, {"provider_id": provider["id"], "file_ids": [file["id"]]}


def submit(studio, task="ocr", **options):
    c, app, base = studio
    response = c.post("/api/recognition/jobs", json={**base, "task": task, **options})
    assert response.status_code == 200, response.text
    return response.json()


def test_fulltext_needs_no_schema_preserves_text_and_exports(studio, monkeypatch):
    c, app, base = studio
    model = ScriptedModel(ocr("发票 A\n<script>literal</script>", [100, 100, 800, 800]))
    monkeypatch.setitem(BACKENDS, "openai", model)
    job = submit(studio)
    assert c.get("/api/jobs/" + job["id"] + "/export").status_code == 409
    Worker(app.state.store).process(job["id"])
    result = c.get("/api/jobs/" + job["id"]).json()
    assert result["status"] == "succeeded"
    data = result["results"][0]["data"]
    assert data["text"] == "发票 A\n<script>literal</script>"
    assert data["blocks"][0]["box"] == [20, 10, 160, 80]
    assert data["blocks"][0]["confidence"] is None
    assert data["blocks"][0]["source_box"] == [20.0, 10.0, 160.0, 80.0]
    assert data["source_unit"] == "pixels"
    exported = c.get("/api/jobs/" + job["id"] + "/export?format=txt")
    assert exported.status_code == 200 and "发票 A" in exported.text
    c.headers.pop("Authorization")
    assert c.get("/api/jobs/" + job["id"] + "/export").status_code == 401


@pytest.mark.parametrize(
    "rotation,expected",
    [
        (0, [40, 40, 240, 160]),
        (90, [80, 80, 320, 180]),
        (180, [160, 40, 360, 160]),
        (270, [80, 20, 320, 120]),
    ],
)
def test_rotation_inverse_uses_all_corners(rotation, expected):
    assert page_box([100, 200, 600, 800], (400, 200), rotation) == expected
    assert source_box(
        [20, 10, 160, 80], {"width": 200, "height": 100, "source_width": 400, "source_height": 200}
    ) == [40, 20, 320, 160]


@pytest.mark.parametrize(
    "rotation,expected",
    [
        (0, [40.1, 40.1, 240.3, 160.05]),
        (90, [80.2, 79.85, 320.1, 179.95]),
        (180, [159.7, 39.95, 359.9, 159.9]),
        (270, [79.9, 20.05, 319.8, 120.15]),
    ],
)
def test_text_geometry_keeps_fractions_through_every_rotation(rotation, expected):
    # Text boxes are continuous geometry; only document raster crops round out.
    assert page_box([100.25, 200.5, 600.75, 800.25], (400, 200), rotation, rasterize=False) == pytest.approx(
        expected
    )


def test_original_coordinates_do_not_amplify_preview_rounding(monkeypatch):
    model_box = [100.13, 200.27, 600.49, 800.31]
    monkeypatch.setitem(BACKENDS, "openai", ScriptedModel(ocr("合成文字", model_box)))
    answer = recognize({"kind": "openai"}, image_png(Image.new("RGB", (425, 600))))
    data = _decorate(
        answer["data"],
        {"width": 425, "height": 600, "source_width": 4250, "source_height": 6000, "source_unit": "pixels"},
    )
    block = data["blocks"][0]
    assert block["source_box"] == pytest.approx([425.552, 1201.62, 2552.082, 4801.86], abs=0.001)
    assert block["model_box"] == model_box
    assert (data["inference_width"], data["inference_height"]) == (425, 600)
    assert data["model_coordinate_space"] == "normalized_0_1000"


def test_standalone_ocr_normalizes_exif_before_rotation(monkeypatch):
    model = ScriptedModel(ocr("synthetic", [100, 200, 600, 800]))
    monkeypatch.setitem(BACKENDS, "openai", model)
    image = Image.new("RGB", (400, 200))
    exif = Image.Exif()
    exif[274] = 6
    out = io.BytesIO()
    image.save(out, "JPEG", exif=exif)
    result = recognize({"kind": "openai"}, out.getvalue(), rotation=90)["data"]
    assert model.calls[0]["sizes"] == [(400, 200)]
    assert (result["width"], result["height"]) == (200, 400)
    assert (result["inference_width"], result["inference_height"]) == (400, 200)
    assert result["blocks"][0]["box"] == [40, 160, 160, 360]


def test_same_class_instances_are_distinct_and_crops_are_authorized(studio, monkeypatch):
    c, app, base = studio
    model = ScriptedModel(detection(), ocr("left card"), ocr("right card"))
    monkeypatch.setitem(BACKENDS, "openai", model)
    job = submit(studio, "documents")
    Worker(app.state.store).process(job["id"])
    record = c.get("/api/jobs/" + job["id"]).json()
    assert record["status"] == "succeeded"
    result = record["results"][0]
    left, right = result["instances"]
    assert left["id"] != right["id"] and left["label"] == right["label"] == "id_card_front"
    assert [i["data"]["text"] for i in result["instances"]] == ["left card", "right card"]
    assert result["usage"]["prompt_tokens"] == 30
    assert right["model_box"] == [550, 0, 1000, 1000]
    assert result["detection"]["inference_width"] == 200
    assert result["detection"]["inference_height"] == 100
    assert model.calls[1]["sizes"] == model.calls[2]["sizes"] == [(90, 100)]
    path = "/api/jobs/" + job["id"] + "/regions/" + right["id"] + "/crop"
    crop = Image.open(io.BytesIO(c.get(path).content))
    assert crop.size == (90, 100) and crop.getpixel((10, 10)) == (0, 0, 255)
    assert right["detection_confidence"] is None
    assert right["data"]["width"] == 200 and right["data"]["height"] == 100
    exported = c.get("/api/jobs/" + job["id"] + "/export").text
    assert left["id"] in exported and right["id"] in exported
    assert "left card" in exported and "right card" in exported
    assert c.get("/api/jobs/" + job["id"] + "/regions/missing/crop").status_code == 404
    c.headers.pop("Authorization")
    assert c.get(path).status_code == 401


def test_class_routes_freeze_versions_and_do_not_run_wrong_template(studio, monkeypatch):
    c, app, base = studio
    template = c.post(
        "/api/templates",
        json={
            "name": "ID fields",
            "schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
            "system_prompt": "ORIGINAL {{schema}}",
        },
    ).json()
    route = {"id_card_front": {"template_id": template["id"], "version": 1}}
    job = submit(studio, "documents", routes=route)
    newer = template | {
        "schema": {"type": "object", "properties": {"changed": {"type": "number"}}, "required": ["changed"]},
        "system_prompt": "CHANGED {{schema}}",
    }
    assert c.post("/api/templates/" + template["id"] + "/versions", json=newer).status_code == 200
    model = ScriptedModel(detection(), {"code": "A"}, {"code": "B"})
    monkeypatch.setitem(BACKENDS, "openai", model)
    Worker(app.state.store).process(job["id"])
    result = c.get("/api/jobs/" + job["id"]).json()
    assert result["status"] == "succeeded"
    assert [x["data"]["code"] for x in result["results"][0]["instances"]] == ["A", "B"]
    assert model.calls[1]["system"].startswith("ORIGINAL")
    assert all(x["template"]["version"] == 1 for x in result["results"][0]["instances"])


def test_instance_checkpoint_resumes_without_repeating_detection_or_completed_peer(studio, monkeypatch):
    c, app, base = studio
    first = Worker(app.state.store)

    def interrupted():
        first.stop.set()
        raise InterruptedError

    model = ScriptedModel(detection(), ocr("done left"), interrupted, ocr("done right"))
    monkeypatch.setitem(BACKENDS, "openai", model)
    job = submit(studio, "documents")
    first.process(job["id"])
    saved = app.state.store.job(job["id"])
    assert saved["status"] == "queued"
    assert saved["results"][0]["usage"] == {"prompt_tokens": 20, "completion_tokens": 10}
    instance_id = saved["results"][0]["instances"][0]["id"]
    Worker(app.state.store).process(job["id"])
    result = c.get("/api/jobs/" + job["id"]).json()
    assert result["status"] == "succeeded" and len(model.calls) == 4
    assert result["results"][0]["instances"][0]["id"] == instance_id
    assert result["results"][0]["instances"][1]["data"]["text"] == "done right"
    assert sum("Locate separate" in call["system"] for call in model.calls) == 1


def test_bad_instance_does_not_discard_successful_peer(studio, monkeypatch):
    c, app, base = studio
    monkeypatch.setitem(BACKENDS, "openai", ScriptedModel(detection(), BackendError("redacted"), ocr("peer")))
    job = submit(studio, "documents")
    Worker(app.state.store).process(job["id"])
    result = c.get("/api/jobs/" + job["id"]).json()
    assert result["status"] == "partial"
    assert [i["status"] for i in result["results"][0]["instances"]] == ["failed", "succeeded"]
    assert result["results"][0]["instances"][1]["data"]["text"] == "peer"


@pytest.mark.parametrize(
    "response",
    [
        {"regions": [{"label": "id_card_front", "box": [0, 0, 1100, 900]}]},
        {"regions": [{"label": "invented", "box": [0, 0, 1000, 1000]}]},
        {"regions": "bad"},
    ],
)
def test_invalid_localization_retains_raw_response_and_no_crops(studio, monkeypatch, response):
    c, app, base = studio
    model = ScriptedModel(response)
    monkeypatch.setitem(BACKENDS, "openai", model)
    job = submit(studio, "documents")
    Worker(app.state.store).process(job["id"])
    result = c.get("/api/jobs/" + job["id"]).json()["results"][0]
    assert result["status"] == "invalid"
    assert json.loads(result["detection"]["raw_text"]) == response
    assert "instances" not in result and len(model.calls) == 1


def test_empty_localization_is_explicit_and_never_fabricates_instance(studio, monkeypatch):
    c, app, base = studio
    monkeypatch.setitem(BACKENDS, "openai", ScriptedModel({"regions": []}))
    job = submit(studio, "documents")
    Worker(app.state.store).process(job["id"])
    data = c.get("/api/jobs/" + job["id"]).json()["results"][0]["data"]
    assert data["empty_detection"] is True and data["documents"] == [] and data["text"] == ""


def test_no_boxes_are_invented_and_invalid_text_contract_is_preserved(studio, monkeypatch):
    c, app, base = studio
    monkeypatch.setitem(
        BACKENDS, "openai", ScriptedModel({"text": "plain", "blocks": [{"text": "plain", "box": None}]})
    )
    job = submit(studio)
    Worker(app.state.store).process(job["id"])
    block = c.get("/api/jobs/" + job["id"]).json()["results"][0]["data"]["blocks"][0]
    assert block["box"] is None and block["source_box"] is None
    monkeypatch.setitem(
        BACKENDS,
        "openai",
        ScriptedModel({"text": "bad", "blocks": [{"text": "bad", "box": [0, 0, 9999, 9999]}]}),
    )
    job = submit(studio)
    Worker(app.state.store).process(job["id"])
    result = c.get("/api/jobs/" + job["id"]).json()["results"][0]
    assert result["status"] == "invalid" and result["data"] is None and "raw_text" in result


def test_recognition_idempotency_and_route_validation(studio):
    c, app, base = studio
    body = {**base, "task": "ocr"}
    one = c.post("/api/recognition/jobs", json=body, headers={"Idempotency-Key": "recognition"}).json()
    two = c.post("/api/recognition/jobs", json=body, headers={"Idempotency-Key": "recognition"}).json()
    assert one["id"] == two["id"]
    assert (
        c.post(
            "/api/recognition/jobs", json=body | {"rotation": 90}, headers={"Idempotency-Key": "recognition"}
        ).status_code
        == 409
    )
    assert (
        c.post(
            "/api/recognition/jobs", json=body | {"routes": {"unknown": {"template_id": "missing"}}}
        ).status_code
        == 422
    )
    assert c.get("/api/recognition/capabilities").json()["engine"] == "multimodal"


def test_queued_ocr_retains_its_prompt_snapshot_across_code_default_changes(studio, monkeypatch):
    import workbench.vision as vision

    c, app, base = studio
    job = submit(studio)
    original = job["payload"]["prompts"]["ocr_system"]
    monkeypatch.setattr(vision, "OCR_SYSTEM", "Changed default after submission")
    model = ScriptedModel(ocr("snapshot"))
    monkeypatch.setitem(BACKENDS, "openai", model)
    Worker(app.state.store).process(job["id"])
    assert model.calls[0]["system"] == original
    assert model.calls[0]["user"] == job["payload"]["prompts"]["ocr_user"]
