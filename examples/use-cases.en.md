# Examples

[中文](use-cases.md) · [Sample files](README.md) · [User guide](../docs/user-guide.en.md)

For UI examples, save a connection under **Model connections** first. Follow the [Qwen-VL / OpenAI tutorial](../docs/model-connections.en.md) if you need a model service. Paths below are relative to the project root.

## CLI setup

The CLI does not require the web server. Install Python 3.12 dependencies:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements.lock
```

Set a model address reachable from your terminal, its actual model ID and key. The CLI does not automatically read `.env` or saved UI connections:

```bash
export WORKBENCH_MODEL_BASE_URL=http://127.0.0.1:8000/v1
export WORKBENCH_MODEL_NAME=qwen-vl
export WORKBENCH_MODEL_API_KEY=your-model-api-key
export WORKBENCH_MODEL_REQUEST_PROFILE=compatible
```

The Qwen Compose example exposes the model only inside its container network. Use that deployment from the UI, or configure a separate terminal-reachable endpoint for CLI usage. For OpenAI use the tutorial's address/model and `WORKBENCH_MODEL_REQUEST_PROFILE=openai`. Commands send documents to the selected service and incur model usage.

## 1. Image to text

Choose **Full-text OCR**, upload the [sample invoice](synthetic-invoice.png), select text output and run. Review full text, text blocks and source boxes; download TXT or JSON.

```bash
python -m workbench ocr examples/synthetic-invoice.png --format text --output invoice.txt
```

The document contains an invoice number, an item and a total. Use `--format json` to retain returned blocks and coordinates.

## 2. Multi-page PDF to Markdown

Upload the [two-page PDF](synthetic-multipage.pdf), select full-text OCR and Markdown. Switch page tabs to review each page with its source image.

```bash
python -m workbench ocr examples/synthetic-multipage.pdf --format markdown --output invoice.md
```

CLI text output concatenates pages in order; JSON preserves per-page status and coordinates. The UI supports page ranges such as `1-2`; CLI processes the whole file.

## 3. Tables and page KV

Upload the [order table](synthetic-table.png) and select **General table recognition**. No custom schema is needed.

- Page KV: order `TEST-042`, supplier `Example Lab`, currency `USD`.
- Table: description, quantity and amount, with a merged title and total row.
- Select a field/cell to locate its source; export CSV or HTML with merged spans.

```bash
python -m workbench tables examples/synthetic-table.png --output table.json
python -m workbench tables examples/synthetic-table-skewed.png --deskew --output table-deskewed.json
python -m workbench tables examples/synthetic-table-multipage.pdf --output table-pages.json
```

Add `--position-mode contextual` for contextual localization. It allows at most four extra batches per page by default; adjust with `--max-localization-batches`. See [table recognition](../docs/tables-and-localization.md) for structures and exports.

## 4. Credential fields

1. Under **Applications**, install common credential configurations and search for the appropriate type, such as identity-card front, passport or driving license.
2. Review fields under **Fields & tables**, edit **Prompt versions**, then save.
3. Select custom field extraction, that application version and a model; upload the matching document type and run.
4. Review the field JSON; enable contextual localization to inspect source positions.

Alternatively import the [identity-card schema](identity-card.schema.json) into a new application. Front/back configurations are separate. See [applications](../docs/applications.md) for the 17 configurations and publication workflow.

## 5. Invoices and line items

Create an invoice application, import the [invoice schema](invoice.schema.json), then upload `synthetic-invoice.png`. The target structure combines page KV with an items array:

```json
{
  "invoice_number": "INV-2026-001",
  "vendor": "Example Supplies",
  "total": 120,
  "items": [{"description": "Notebook", "quantity": 3, "amount": 120}]
}
```

The [template](invoice.template.json) contains fields and prompts; [ground truth](synthetic-invoice.gold.json) supplies reference values. Save prompt changes as new versions and compare them with example 9.

## 6. Grouped expenses

Import the [grouped expense schema](grouped-expenses.schema.json). It defines a claim number and multiple groups, each with department, subtotal and nested items:

```json
{
  "claim_number": "CLAIM-001",
  "groups": [{
    "department": "Operations",
    "subtotal": 120,
    "items": [{"description": "Notebook", "quantity": 3, "amount": 120}]
  }]
}
```

This illustrates the output structure. Upload your grouped expense document, define grouping rules in field descriptions and explain how each group's KV relates to its items. Use object arrays in the visual editor or import more deeply nested JSON Schema.

## 7. Multiple documents in one image

Upload the [two-invoice image](synthetic-multiple-invoices.png) and select **Multiple documents**. The system locates and crops each invoice, then recognizes instances separately. Review text, crops and source boxes. The invoice numbers in the image are `INV-2026-001` and `INV-2026-002`.

```bash
python -m workbench documents examples/synthetic-multiple-invoices.png --output documents.json
```

For field JSON, bind the `invoice` class to an invoice application version in the UI route settings. Unbound classes use full-text OCR. CLI uses full-text instance recognition; configure field routes through the UI or HTTP API.

## 8. Publish an API

1. Create an invoice application with code `invoice-extraction` and configure its schema/prompts.
2. Test a sample, then publish a version with a model connection and custom field extraction.
3. Use the workspace token in community mode; in SaaS create an application-scoped API key.
4. Set `WORKBENCH_ACCESS_TOKEN` to that credential and run:

```bash
export WORKBENCH_ACCESS_TOKEN=your-workbench-or-application-api-key
python examples/recognize_application.py examples/synthetic-invoice.png \
  --app invoice-extraction --url http://127.0.0.1:8765 --output result.json
```

The client uploads, submits, polls and saves the result. Callers need the application code, without internal prompts or model-connection IDs. See the [application API](../docs/applications.md).

## 9. Compare prompt versions

1. Create a dataset, add `synthetic-invoice.png`, and use `synthetic-invoice.gold.json` as ground truth.
2. Run evaluation with an invoice application version and model.
3. Edit field descriptions or prompts, save a new version, then evaluate on the same dataset.
4. Compare field differences, exact-match scores, failures and model usage before publishing.

Evaluation compares ground-truth leaf fields and aligns arrays by position. See [evaluation rules](../docs/product.md#评测).
