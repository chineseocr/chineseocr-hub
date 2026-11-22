"""Generate public fixtures from scratch. Never reads legacy/customer assets."""

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from workbench.domain import INVOICE_SCHEMA, SYSTEM_PROMPT, USER_PROMPT  # noqa: E402

SAMPLE_RESULT = {
    "invoice_number": "INV-2026-001",
    "vendor": "Example Supplies",
    "total": 120.0,
    "items": [{"description": "Notebook", "quantity": 3, "amount": 120.0}],
}


def make_sample():
    from PIL import ImageDraw

    image = Image.new("RGB", (1000, 1250), "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((65, 65, 935, 1185), radius=16, outline="#d4dce8", width=3)
    lines = [
        "SYNTHETIC INVOICE / NOT A REAL RECEIPT",
        "",
        "Example Supplies",
        "Invoice: INV-2026-001",
        "Date: 2026-01-01",
        "",
        "Description          Quantity          Amount",
        "-" * 54,
        "Notebook                  3            120.00",
        "",
        "Total: USD 120.00",
        "",
        "Created for testing. No customer or personal data.",
    ]
    for index, line in enumerate(lines):
        draw.text((105, 125 + index * 62), line, fill="#223049", font_size=24)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    buffer.seek(0)
    return buffer


def make_table_sample():
    """A public, independently drawn mixed-KV/table sample with two header rows."""
    image = Image.new("RGB", (1000, 1000), "white")
    draw = ImageDraw.Draw(image)
    draw.text((70, 55), "SYNTHETIC ORDER - NOT A REAL DOCUMENT", fill="black", font_size=28)
    draw.text((70, 115), "Order: TEST-042", fill="black", font_size=26)
    draw.text((70, 165), "Supplier: Example Lab", fill="black", font_size=26)
    draw.text((70, 215), "Currency: USD", fill="black", font_size=26)
    # A merged title row, column-header row, two body rows and a merged total label.
    xs, ys = [70, 550, 700, 930], [300, 365, 430, 500, 570, 640]
    draw.rectangle((xs[0], ys[0], xs[-1], ys[-1]), outline="black", width=2)
    for y in ys[1:-1]:
        draw.line((xs[0], y, xs[-1], y), fill="black", width=2)
    draw.line((xs[1], ys[1], xs[1], ys[-2]), fill="black", width=2)
    draw.line((xs[2], ys[1], xs[2], ys[-1]), fill="black", width=2)
    rows = [
        ("Order items", "", ""),
        ("Description", "Qty", "Amount"),
        ("Notebook", "3", "120.00"),
        ("Pen", "2", "10.00"),
        ("Total", "", "130.00"),
    ]
    for row, values in enumerate(rows):
        for column, value in enumerate(values):
            if value:
                draw.text((xs[column] + 16, ys[row] + 20), value, fill="black", font_size=25)
    draw.text((70, 710), "All names and values are synthetic test data.", fill="black", font_size=23)
    return image


def main():
    from PIL import Image

    folder = ROOT / "examples"
    folder.mkdir(exist_ok=True)
    sample = make_sample()
    (folder / "synthetic-invoice.png").write_bytes(sample.getvalue())
    page1 = Image.open(sample).convert("RGB")
    page2 = Image.new("RGB", page1.size, "white")
    draw = ImageDraw.Draw(page2)
    draw.text((100, 130), "SYNTHETIC INVOICE / CONTINUATION", font_size=28, fill="black")
    draw.text((100, 220), "Invoice: INV-2026-001", font_size=24, fill="black")
    draw.text(
        (100, 300), "Notes: synthetic continuation for PDF processing tests.", font_size=24, fill="black"
    )
    draw.text((100, 380), "No additional line items. Not a real receipt.", font_size=24, fill="black")
    page1.save(folder / "synthetic-multipage.pdf", save_all=True, append_images=[page2])
    table = make_table_sample()
    table.save(folder / "synthetic-table.png")
    table.rotate(4, expand=True, fillcolor="white").save(folder / "synthetic-table-skewed.png")
    table.save(folder / "synthetic-table-multipage.pdf", save_all=True, append_images=[table])
    grouped = {
        "type": "object",
        "additionalProperties": False,
        "required": ["claim_number", "groups"],
        "properties": {
            "claim_number": {"type": ["string", "null"], "description": "报销申请编号"},
            "groups": {
                "type": "array",
                "description": "费用分组；每组同时有 KV 和明细表",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["department", "subtotal", "items"],
                    "properties": {
                        "department": {"type": ["string", "null"], "description": "科室/部门"},
                        "subtotal": {"type": ["number", "null"], "description": "本组小计"},
                        "items": INVOICE_SCHEMA["properties"]["items"],
                    },
                },
            },
        },
    }
    fixtures = {
        "synthetic-invoice.gold.json": SAMPLE_RESULT,
        "invoice.schema.json": INVOICE_SCHEMA,
        "grouped-expenses.schema.json": grouped,
        "key-value.schema.json": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "document_number": {"type": ["string", "null"], "description": "文档编号"},
                "issuer": {"type": ["string", "null"], "description": "签发单位"},
            },
            "required": ["document_number", "issuer"],
        },
        "invoice.template.json": {
            "name": "通用票据",
            "schema": INVOICE_SCHEMA,
            "system_prompt": SYSTEM_PROMPT,
            "user_prompt": USER_PROMPT,
        },
    }
    for name, body in fixtures.items():
        (folder / name).write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Generated synthetic fixtures in examples/ (no customer inputs).")


if __name__ == "__main__":
    main()
