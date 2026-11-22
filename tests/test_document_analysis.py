"""Geometry, table structure, paid-call recovery and isolated export regressions."""

import io
import json

import pytest
from PIL import Image, ImageDraw
from test_multimodal_recognition import ScriptedModel, ocr, submit
from test_multimodal_recognition import studio as studio

from workbench.adapters import BACKENDS, BackendError
from workbench.engine import Worker
from workbench.geometry import estimate_deskew, map_box, prepare_image, rotate


def table_result():
    return {
        "text": "Order\nSeller: Example\nItem Amount\nA 12",
        "fields": [
            {
                "label": "Seller",
                "value": "Example",
                "location": {"bbox": [100, 100, 400, 200], "matched_text": "Example"},
            }
        ],
        "tables": [
            {
                "title": "Order",
                "cells": [
                    {"row": 0, "column": 0, "column_span": 2, "is_header": True, "text": "Order"},
                    {"row": 1, "column": 0, "is_header": True, "text": "Item"},
                    {"row": 1, "column": 1, "is_header": True, "text": "Amount"},
                    {"row": 2, "column": 0, "is_header": False, "text": "A"},
                    {"row": 2, "column": 1, "is_header": False, "text": "12"},
                ],
            }
        ],
    }


def process(studio, monkeypatch, model, **options):
    c, app, base = studio
    monkeypatch.setitem(BACKENDS, "openai", model)
    job = submit(studio, **options)
    Worker(app.state.store).process(job["id"])
    return c.get("/api/jobs/" + job["id"]).json()


def test_table_structure_key_values_spans_and_safe_exports(studio, monkeypatch):
    raw = table_result()
    raw["tables"][0]["cells"][3]["text"] = '=HYPERLINK("evil")<script>alert(1)</script>'
    result = process(studio, monkeypatch, ScriptedModel(raw), task="tables", deskew=True)
    c, app, _ = studio
    assert result["status"] == "succeeded"
    data = result["results"][0]["data"]
    assert data["fields"][0]["source_box"] == [20, 10, 80, 20]
    assert data["tables"][0]["cells"][0]["column_span"] == 2
    assert data["tables"][0]["cells"][3]["box"] is None
    prefix = "/api/jobs/" + result["id"] + "/tables/0/0/export"
    csv = c.get(prefix + "?format=csv")
    html = c.get(prefix + "?format=html")
    assert csv.status_code == html.status_code == 200
    assert "'=HYPERLINK" in csv.text and "<script>" not in html.text and "&lt;script&gt;" in html.text
    assert 'colspan="2"' in html.text and "<th" in html.text
    assert c.get(prefix.replace("/0/0/", "/-1/0/")).status_code == 404
    assert c.get("/api/jobs/" + result["id"] + "/export").status_code == 200
    c.headers.pop("Authorization")
    assert c.get(prefix).status_code == 401


@pytest.mark.parametrize(
    "damage", ["overlap", "negative", "huge_span", "boolean_index", "nontext", "bad_box"]
)
def test_invalid_table_geometry_preserves_paid_response(studio, monkeypatch, damage):
    raw = table_result()
    cell = raw["tables"][0]["cells"][1]
    if damage == "overlap":
        cell["row"] = 0
    if damage == "negative":
        cell["column"] = -1
    if damage == "huge_span":
        cell["column_span"] = 100000
    if damage == "boolean_index":
        cell["row"] = True
    if damage == "nontext":
        cell["text"] = 42
    if damage == "bad_box":
        cell["location"] = {"bbox": [0, 0, 2000, 10]}
    result = process(studio, monkeypatch, ScriptedModel(raw), task="tables")
    unit = result["results"][0]
    assert result["status"] == "failed" and unit["status"] == "invalid"
    assert json.loads(unit["raw_text"]) == raw and unit["usage"]["prompt_tokens"] == 10


def test_contextual_ocr_uses_text_matches_and_retains_original_boxes(studio, monkeypatch):
    model = ScriptedModel(
        ocr("Repeated 12", [0, 0, 100, 100]),
        {"locations": [{"id": "line-1", "bbox": [100, 200, 500, 400], "matched_text": "Repeated 12"}]},
    )
    result = process(studio, monkeypatch, model, position_mode="contextual")
    block = result["results"][0]["data"]["blocks"][0]
    assert block["box"] == pytest.approx([20, 20, 100, 40])
    assert block["original_model_box"] == [0, 0, 100, 100]
    assert block["position_source"] == "model_contextual"
    assert result["results"][0]["usage"]["prompt_tokens"] == 20
    assert json.loads(model.calls[1]["user"])[0]["value"] == "Repeated 12"


@pytest.mark.parametrize(
    "refinement",
    [
        BackendError("private upstream detail"),
        {"locations": []},
        {"locations": [{"id": "line-1", "bbox": [10, 10, 50, 50], "matched_text": "wrong 13"}]},
        {"locations": [{"id": "unknown", "bbox": [10, 10, 50, 50], "matched_text": "visible text"}]},
    ],
)
def test_refinement_failure_keeps_extraction(studio, monkeypatch, refinement):
    result = process(
        studio,
        monkeypatch,
        ScriptedModel(ocr("visible text", [100, 100, 800, 800]), refinement),
        position_mode="contextual",
    )
    unit = result["results"][0]
    assert result["status"] == unit["status"] == "partial"
    assert unit["data"]["text"] == "visible text" and unit["data"]["blocks"][0]["model_box"] == [
        100,
        100,
        800,
        800,
    ]
    assert "private upstream detail" not in json.dumps(result)


def test_localization_checkpoint_does_not_repeat_paid_extraction(studio, monkeypatch):
    c, app, _ = studio
    worker = Worker(app.state.store)

    def interrupt():
        worker.stop.set()
        raise InterruptedError

    model = ScriptedModel(
        ocr(),
        interrupt,
        {"locations": [{"id": "line-1", "bbox": [100, 100, 800, 800], "matched_text": "visible text"}]},
    )
    monkeypatch.setitem(BACKENDS, "openai", model)
    job = submit(studio, position_mode="contextual")
    worker.process(job["id"])
    saved = app.state.store.job(job["id"])
    assert saved["status"] == "queued" and saved["results"][0]["usage"]["prompt_tokens"] == 10
    Worker(app.state.store).process(job["id"])
    result = app.state.store.job(job["id"])
    assert result["status"] == "succeeded" and len(model.calls) == 3
    assert result["results"][0]["usage"]["prompt_tokens"] == 20


def test_model_bands_derive_cells_with_explicit_provenance(studio, monkeypatch):
    raw = table_result()
    band_locations = [
        ("table-1:row:0", [100, 300, 900, 400], "Order"),
        ("table-1:row:1", [100, 400, 900, 500], "Item Amount"),
        ("table-1:row:2", [100, 500, 900, 600], "A 12"),
        ("table-1:column:0", [100, 300, 500, 600], "Order Item A"),
        ("table-1:column:1", [500, 300, 900, 600], "Order Amount 12"),
        ("field-1", [100, 100, 400, 200], "Example"),
    ]
    model = ScriptedModel(
        raw,
        {"locations": [{"id": key, "bbox": box, "matched_text": text} for key, box, text in band_locations]},
    )
    result = process(studio, monkeypatch, model, task="tables", position_mode="contextual")
    table = result["results"][0]["data"]["tables"][0]
    cell = table["cells"][-1]
    assert cell["position_source"] == "model_band_intersection" and cell["band_derived"]
    assert cell["model_box"] == [500, 500, 900, 600] and cell["box"] == [100, 50, 180, 60]
    assert table["cells"][0]["model_box"] == [100, 300, 900, 400]


def test_call_budget_is_explicit_and_frozen(studio, monkeypatch):
    raw = {"text": "synthetic", "blocks": [{"text": str(i), "box": None} for i in range(50)]}
    model = ScriptedModel(raw, {"locations": []})
    result = process(studio, monkeypatch, model, position_mode="contextual", max_localization_batches=1)
    assert len(model.calls) == 2 and len(json.loads(model.calls[1]["user"])) == 24
    assert result["results"][0]["data"]["localization"]["truncated"]
    assert (
        result["payload"]["max_localization_batches"] == 1 and result["payload"]["prompts"]["locate_system"]
    )


def test_projection_deskew_is_conservative_and_inverse_matches_rendered_ink():
    upright = Image.new("RGB", (600, 400), "white")
    draw = ImageDraw.Draw(upright)
    for y in range(60, 330, 25):
        for x in range(50, 500, 35):
            draw.rectangle((x, y, x + 20, y + 8), fill="black")
    tilted = upright.rotate(4, expand=True, fillcolor="white")
    assert estimate_deskew(tilted) == pytest.approx(-4, abs=0.5)
    assert estimate_deskew(upright) == estimate_deskew(Image.new("RGB", (600, 400), "white")) == 0
    # Compare inverse geometry to the original pixel mark, independently of
    # language-model outputs and the rectangle mapper implementation.
    mark = Image.new("RGB", (400, 200), "white")
    ImageDraw.Draw(mark).rectangle((140, 60, 180, 90), fill="black")
    prepared, inverse = rotate(mark, 5)
    bbox = (
        Image.eval(prepared.convert("L"), lambda v: 255 - v).point(lambda v: 255 if v > 128 else 0).getbbox()
    )
    transform = {
        "inference_width": prepared.width,
        "inference_height": prepared.height,
        "source_width": 400,
        "source_height": 200,
        "inverse_matrix": inverse,
    }
    box, polygon = map_box(
        [bbox[i] * 1000 / (prepared.width if i % 2 == 0 else prepared.height) for i in range(4)], transform
    )
    assert box[0] <= 142 and box[1] <= 62 and box[2] >= 178 and box[3] >= 88
    assert len(polygon) == 4


def test_routed_schema_fields_get_separate_locations(studio, monkeypatch):
    c, app, _ = studio
    template = c.post(
        "/api/templates",
        json={
            "name": "Synthetic fields",
            "schema": {"type": "object", "properties": {"name": {"type": "string"}}},
        },
    ).json()
    model = ScriptedModel(
        {"regions": [{"label": "id_card_front", "box": [100, 100, 900, 900]}]},
        {"name": "SAMPLE"},
        {"locations": [{"id": "field/name", "bbox": [100, 100, 500, 300], "matched_text": "SAMPLE"}]},
    )
    result = process(
        studio,
        monkeypatch,
        model,
        task="documents",
        position_mode="contextual",
        routes={"id_card_front": {"template_id": template["id"]}},
    )
    instance = result["results"][0]["instances"][0]
    assert instance["data"] == {"name": "SAMPLE"} and instance["status"] == "succeeded"
    assert instance["field_locations"][0]["source_box"] == pytest.approx([36, 18, 100, 34])


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_affine_quarter_rotations_match_existing_page_mapping(rotation):
    from workbench.vision import page_box

    image, transform = prepare_image(Image.new("RGB", (400, 200)), rotation=rotation)
    bounds, _ = map_box([100, 200, 600, 800], transform)
    assert bounds == pytest.approx(page_box([100, 200, 600, 800], (400, 200), rotation, rasterize=False))


def test_business_pdf_extracts_once_and_records_page_locations(studio, monkeypatch):
    c, app, base = studio
    first, second = Image.new("RGB", (200, 100), "white"), Image.new("RGB", (200, 100), "white")
    out = io.BytesIO()
    first.save(out, "PDF", save_all=True, append_images=[second])
    file = c.post("/api/files", files={"file": ("synthetic.pdf", out.getvalue())}).json()
    template = c.post(
        "/api/templates",
        json={"name": "PDF fields", "schema": {"type": "object", "properties": {"name": {"type": "string"}}}},
    ).json()
    model = ScriptedModel(
        {"name": "SAMPLE"},
        {"locations": [{"id": "field/name", "bbox": [100, 100, 500, 300], "matched_text": "SAMPLE"}]},
        {"locations": []},
    )
    monkeypatch.setitem(BACKENDS, "openai", model)
    response = c.post(
        "/api/jobs",
        json={
            **base,
            "template_id": template["id"],
            "file_ids": [file["id"]],
            "position_mode": "contextual",
            "deskew": True,
        },
    )
    assert response.status_code == 200
    Worker(app.state.store).process(response.json()["id"])
    result = app.state.store.job(response.json()["id"])
    unit = result["results"][0]
    assert len(model.calls) == 3 and len(model.calls[0]["sizes"]) == 2
    assert unit["data"] == {"name": "SAMPLE"} and result["status"] == "partial"
    assert [f["page"] for f in unit["field_locations"]] == [1, 2]
    assert unit["field_locations"][0]["box"] and unit["field_locations"][1]["box"] is None
    assert unit["usage"]["prompt_tokens"] == 30
    assert unit["field_locations"][0]["source_box"] == pytest.approx([20, 10, 100, 30])


def test_custom_field_analysis_resumes_after_interrupted_localization(studio, monkeypatch):
    c, app, base = studio
    template = c.post(
        "/api/templates",
        json={"name": "Fields", "schema": {"type": "object", "properties": {"name": {"type": "string"}}}},
    ).json()
    job = c.post(
        "/api/jobs", json={**base, "template_id": template["id"], "position_mode": "contextual"}
    ).json()
    first = Worker(app.state.store)

    def stop():
        first.stop.set()
        raise InterruptedError

    model = ScriptedModel(
        {"name": "SAMPLE"},
        stop,
        {"locations": [{"id": "field/name", "bbox": [100, 100, 500, 300], "matched_text": "SAMPLE"}]},
    )
    monkeypatch.setitem(BACKENDS, "openai", model)
    first.process(job["id"])
    assert app.state.store.job(job["id"])["status"] == "queued"
    Worker(app.state.store).process(job["id"])
    result = app.state.store.job(job["id"])
    assert result["status"] == "succeeded" and len(model.calls) == 3
    assert result["results"][0]["usage"]["prompt_tokens"] == 20


def test_public_table_release_freezes_contract_and_returns_safe_exports(studio, monkeypatch):
    from test_applications import config

    import workbench.table_recognition as contracts

    c, app, base = studio
    entry = c.post("/api/applications", json=config("generic-table")).json()
    release = c.post(
        "/api/applications/" + entry["id"] + "/publish",
        json={"version": 1, "provider_id": base["provider_id"], "task": "tables", "deskew": True},
    ).json()
    assert release["task"] == "tables" and release["processing"]["deskew"]
    # Change module defaults after publishing. The published contract must win.
    monkeypatch.setattr(contracts, "TABLE_SYSTEM", "NEW UNPUBLISHED CONTRACT")
    source = c.post("/v1/files", files={"file": ("synthetic.png", image_bytes())}).json()
    job = c.post("/v1/apps/generic-table/recognitions", json={"file_ids": [source["id"]]}).json()
    assert "id" in job
    model = ScriptedModel(table_result())
    monkeypatch.setitem(BACKENDS, "openai", model)
    Worker(app.state.store).process(job["id"])
    record = c.get("/v1/jobs/" + job["id"]).json()
    assert record["status"] == "succeeded" and record["results"][0]["data"]["tables"]
    assert model.calls[0]["system"] == release["recognition_prompts"]["table_system"]
    assert "analysis" not in record["results"][0] and "raw_text" not in record["results"][0]
    export = "/v1/jobs/" + job["id"] + "/tables/0/0/export"
    assert c.get(export).status_code == 200
    c.headers.pop("Authorization")
    assert c.get(export).status_code == 401


def image_bytes():
    out = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(out, "PNG")
    return out.getvalue()


def test_table_export_tenant_key_and_quota_boundaries(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_applications import config
    from test_applications import model as make_model
    from test_saas import MODEL_BASE, login, register

    from workbench.saas import create_saas_app

    app = create_saas_app(tmp_path, start_worker=False, registration=True, allowed_model_bases=[MODEL_BASE])
    with TestClient(app) as c:
        c.headers["X-Workbench"] = "1"
        credentials = json.loads((tmp_path / "saas/initial-admin.json").read_text())
        login(c, credentials)
        tenant = c.get("/api/account").json()["active_tenant_id"]
        entry = c.post("/api/applications", json=config("table-safe")).json()
        provider = make_model(c)
        assert (
            c.post(
                "/api/applications/" + entry["id"] + "/publish",
                json={"version": 1, "provider_id": provider, "task": "tables"},
            ).status_code
            == 200
        )
        key1 = c.post("/api/api-keys", json={"name": "table 1", "application_ids": [entry["id"]]}).json()[
            "token"
        ]
        key2 = c.post("/api/api-keys", json={"name": "table 2", "application_ids": [entry["id"]]}).json()[
            "token"
        ]
        c.cookies.clear()
        c.headers["Authorization"] = "Bearer " + key1
        source = c.post("/v1/files", files={"file": ("synthetic.png", image_bytes())}).json()
        task = c.post("/v1/apps/table-safe/recognitions", json={"file_ids": [source["id"]]}).json()
        monkeypatch.setitem(BACKENDS, "openai", ScriptedModel(table_result()))
        Worker(app.state.runtime.apps[tenant].state.store).process(task["id"])
        export = "/v1/jobs/" + task["id"] + "/tables/0/0/export"
        assert c.get(export).status_code == 200
        c.headers["Authorization"] = "Bearer " + key2
        assert c.get(export).status_code == 404
        alice = register(c, "table-alice")
        login(c, alice)
        assert c.get(export).status_code == 404
        login(c, credentials)
        # The viewer cannot submit any of the new recognition modes.
        viewer = c.post("/api/api-keys", json={"name": "read only", "role": "viewer"}).json()["token"]
        c.cookies.clear()
        c.headers["Authorization"] = "Bearer " + viewer
        assert (
            c.post(
                "/api/recognition/jobs",
                json={"task": "tables", "provider_id": provider, "file_ids": [source["id"]]},
            ).status_code
            == 403
        )


def test_pixel_grid_corrects_only_evidenced_merges_and_keeps_model_boxes():
    from scripts.generate_examples import make_table_sample
    from workbench.table_grid import assist_grid
    from workbench.table_recognition import normalize_tables

    image = make_table_sample()
    rows = [
        ["Order items"],
        ["Description", "Qty", "Amount"],
        ["Notebook", "3", "120.00"],
        ["Pen", "2", "10.00"],
        ["Total", "", "130.00"],
    ]
    raw = {
        "text": "synthetic",
        "fields": [],
        "tables": [
            {
                "title": "Order items",
                "cells": [
                    {"row": r, "column": c, "is_header": r < 2, "text": text}
                    for r, row in enumerate(rows)
                    for c, text in enumerate(row)
                ],
            }
        ],
    }
    data = normalize_tables(raw)
    assist_grid(data, image)
    table = data["tables"][0]
    assert table["cells"][0]["column_span"] == 3 and table["cells"][-2]["column_span"] == 2
    assert table["cells"][-1]["geometry_box"] == pytest.approx([700, 570, 930, 640], abs=1)
    assert table["cells"][-1]["position_source"] == "image_grid_cell"
    assert table["cells"][-1]["model_box"] is None and len(table["cells"]) == 12
    # Two conflicting values in one physical merged cell are never combined.
    conflicting = normalize_tables(raw)
    conflicting["tables"][0]["cells"][0]["column_span"] = 1
    conflicting["tables"][0]["cells"].append(
        dict(conflicting["tables"][0]["cells"][0], id="conflict", column=1, text="OTHER")
    )
    assist_grid(conflicting, image)
    assert "source_grid" not in conflicting["tables"][0]
    unruled = normalize_tables(raw)
    assist_grid(unruled, Image.new("RGB", image.size, "white"))
    assert "source_grid" not in unruled["tables"][0]


def test_medical_style_location_variants_preserve_valid_peer_items():
    from workbench.document_analysis import _locations

    requests = [
        {"id": "field-1", "kind": "text", "label": "Order", "value": "TEST-042"},
        {"id": "row-1", "kind": "row_band", "value": ["Pen", "2", "10.00"]},
        {"id": "bad", "kind": "text", "value": "wrong"},
    ]
    response = {
        "raw_text": json.dumps(
            {
                "locations": [
                    {
                        "id": "field-1",
                        "bbox": {"x1": 100, "y1": 100, "x2": 400, "y2": 200},
                        "matched_text": "Order: TEST-042",
                    },
                    {"id": "row-1", "bbox": [100, 300, 900, 400], "matched_text": ["Pen", "2", "10.00"]},
                    {"id": "bad", "bbox": [900, 0, 100, 100], "matched_text": "wrong"},
                ]
            }
        )
    }
    result = _locations(response, requests)
    assert set(result) == {"field-1", "row-1"}
    assert result["row-1"]["matched_text"] == "Pen 2 10.00"
    dictionary = {
        "raw_text": json.dumps(
            {
                "field_locations": {
                    "field-1": {"bbox": [100, 100, 400, 200], "matched_text": "Order: TEST-042"}
                }
            }
        )
    }
    assert _locations(dictionary, requests)["field-1"]["bbox"] == [100, 100, 400, 200]


def test_duplicate_location_ids_and_numeric_substrings_are_not_accepted():
    from workbench.document_analysis import _locations

    requests = [{"id": "amount", "kind": "text", "label": "Total", "value": "12"}]
    duplicate = {
        "raw_text": json.dumps(
            {
                "locations": [
                    {"id": "amount", "bbox": [0, 0, 100, 100], "matched_text": "12"},
                    {"id": "amount", "bbox": [100, 0, 200, 100], "matched_text": "12"},
                ]
            }
        )
    }
    assert _locations(duplicate, requests) == {}
    substring = {
        "raw_text": json.dumps(
            {"locations": [{"id": "amount", "bbox": [0, 0, 100, 100], "matched_text": "Total: 312"}]}
        )
    }
    assert _locations(substring, requests) == {}
