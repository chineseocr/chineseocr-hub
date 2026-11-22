# ChineseOCR Hub user guide

[中文](user-guide.md) · [Docker operations](docker.en.md) · [Configuration](configuration.md) · [API](api.md)

For **v0.6.0a1 Alpha**. Deploy or purchase a separate multimodal model service. All visual recognition uses your configured model.

## 1. Sign in

Start the backend using the [README](../README.en.md) or [Docker guide](docker.en.md), then open `http://127.0.0.1:8765/?lang=en`. Opening the static HTML file directly is not an application deployment.

- Community mode uses the private access token, which grants full workspace administration.
- SaaS uses accounts. Confirm your current team before uploading or editing. Teams have independent applications, files and connections. Viewers are read-only; members run tasks and edit drafts; admins/owners configure models and publish.
- Language changes affect system labels, not saved prompts, business fields, filenames or model output.

## 2. Run your first OCR

1. Under **Model connections**, provide the Base URL and required API key, discover/select the actual model ID and save. In SaaS, this requires admin/owner access and an operator-approved destination.
2. Open **Extraction**, select the connection and the full-text OCR task.
3. Upload and select `examples/synthetic-invoice.png` from the source tree, then start extraction. Samples and answers are not preloaded in the container.
4. Review text blocks, full text and JSON. Click a block to inspect its source box, then download text or JSON.

Start with synthetic input. Discovering a model ID does not prove visual support. Running recognition sends page PNGs and prompts to the selected service.

## 3. Choose the task

| Goal | Task | Fields required? |
|---|---|---|
| Read page text/Markdown | Full-text OCR | No |
| Extract arbitrary tables with page KV | General table recognition | No |
| Recognize several documents in one image | Multiple documents | Optional class-to-application routing |
| Produce a fixed business JSON structure | Custom field extraction | Yes, save an application version first |

An invoice application does not automatically turn into an identity-card application. Missing fields return `null`; a successful task does not establish that the selected schema matches the document.

## 4. Tables and page KV

1. Upload `examples/synthetic-table.png` and choose general table recognition.
2. Expand run settings. Start with direct localization; enable small-angle deskew or contextual localization as needed. Contextual localization can add up to four request batches per page by default, increasing latency and model cost.
3. Inspect page KV and the table with multi-row headers, body cells and retained merged spans.
4. Click a KV value or cell to highlight its source. Click an image box to select the result. Unavailable positions remain unavailable.
5. Export CSV or HTML. HTML retains merged spans; CSV is for data interchange and does not preserve visual merging. XLSX export is not implemented.

Deskew handles a bounded ±8° angle, not automatic quarter-turn orientation or perspective correction. Pixel-grid assistance is conservative and applies only to a suitable single ruled table. See [tables and localization](tables-and-localization.md).

## 5. Multi-page PDFs and linked review

Upload `examples/synthetic-table-multipage.pdf` and run table recognition. Result page tabs synchronize with the source preview; multi-file tabs identify their input file.

- Leave the page range blank for all pages, or enter `1-3,5`. PDFs support at most 20 pages; joint field extraction supports at most eight pages per file.
- OCR, tables and multiple-document recognition process pages separately. Custom fields offer per-page or joint-document extraction. Separate files are not merged.
- Use fit width, zoom and box visibility. Source and results scroll independently. Fields, blocks and cells link to image boxes in both directions.
- Reopen completed tasks from **Task history** to inspect their frozen inputs/configuration rather than a newer draft.

Coordinates map to original oriented-image pixels or PDF points; see [coordinate processing](coordinate-processing.md). Continuation-table merging and human correction writeback are not implemented. Linked review does not certify positional accuracy.

## 6. Identity applications and prompt management

1. In **Applications**, explicitly install common credential configurations, or create an application from the identity-card front starter. Installation creates editable drafts without publishing.
2. Open its fields and prompt settings. Verify that the structure matches the document side; front and back use separate configurations.
3. Edit system/user prompts and save a version. Keep `{{schema}}` in the user prompt; `{{filename}}` and `{{pages}}` are also supported.
4. In the workbench, select that application, version and model. Test with authorized samples. Preserve `null` for unreadable values rather than inferring missing information.

Use one application per business scenario. Search 100+ scenarios by stable code, name, tags or owner; filter and page the catalog. Copy to create an independent scenario, compare versions and migrate configuration bundles without model secrets or documents. See [application management](applications.md).

The 17 credential starters are schemas/prompts, not separately validated specialist models. They do not establish authenticity or verify identity.

## 7. Fixed header KV, line items and dictionaries

For a fixed business format, choose custom field extraction and configure **Fields & tables**:

- Scalar fields for document number, date, supplier and total.
- Object groups for buyer/seller information.
- Object arrays for line items, with child fields defining columns such as description, quantity and amount. Put actual header names and extraction rules in column descriptions.

You can import the [grouped expense schema](../examples/grouped-expenses.schema.json). `required` requires a key, not a non-null recognized value when its type permits null.

Save reusable choices under **Dictionaries**, then copy a version into a field's `enum` using the choices menu. Copies are snapshots: later dictionary updates do not change saved applications. To permit missing values, both the field type and enum must permit null. Validation reports invalid outputs rather than silently replacing them.

## 8. Multiple documents in one image

Select the multiple-document task and optionally bind detected classes to field-application versions. The model localizes documents, the server makes real crops, and each instance is recognized separately. Same-class instances retain distinct IDs, boxes and results.

Unconfigured classes use full-text OCR. The limit is 12 instances per page; automatic front/back pairing is not implemented. Expect one localization request plus a recognition request per instance, with potential additional contextual-localization requests.

## 9. Datasets and evaluation

1. Save the field-application version and upload sample files.
2. Under **Datasets & evaluation**, create a dataset and expand the sample-with-ground-truth section. Select an uploaded file.
3. Enter or import one business JSON object, then add the sample. For practice, pair `synthetic-invoice.png` with `synthetic-invoice.gold.json` using the matching invoice structure.
4. Choose the connection, application version and mode, run evaluation, inspect field differences and download the report.
5. Save a new prompt version and rerun against the same ground-truth snapshot. Select two runs to compare.

Each dataset supports up to 50 samples. Scores compare business-JSON leaves exactly; failed/missing fields count in the denominator and arrays align by position. This is not character accuracy, box IoU or a general-table benchmark. Invoice ground truth cannot measure an unrelated table-recognition task.

## 10. Publish for external clients

1. In **Applications**, select a verified version, connection and published task: custom extraction, OCR or tables. Configure positional processing and publish. SaaS requires an admin/owner account session.
2. Create an application-scoped API key and privately provide it to the client. Plaintext is displayed only once.
3. Upload through `POST /v1/files`, submit `POST /v1/apps/{code}/recognitions`, then poll `GET /v1/jobs/{id}`. Copyable requests are in the [application API guide](applications.md).

A release freezes fields, prompts, the connection selection and processing strategy. Saving drafts does not change live calls. Publish a new release to activate changes, activate a historical release to roll back, or disable new submissions while retaining authorized history. Clients cannot override prompts or model addresses.

## 11. Failures, cancellation and cost

Inspect queued/running/succeeded/partial/failed/cancelled tasks under **Task history**. Partial results require checking individual units and unconfirmed content. Raw output is useful diagnostic evidence, not reviewed business data.

Cancellation is cooperative and cannot retract an upstream request. Restart reuses persisted checkpoints; an interrupted call not yet saved can run and incur cost again. Model errors are not retried automatically; explicit reruns add calls. Admitted-page quotas are not model bills, and token usage includes only fields actually reported by the provider.

## 12. Further reading

- [Docker, backups and upgrades](docker.en.md), [deployment](deployment.md).
- [Configuration storage and request flow](configuration.md).
- [HTTP API](api.md), [CLI/library](multimodal-ocr.md), [application client](../examples/recognize_application.py).
- [Validation evidence](validation.md), [capabilities and limits](ocr-capabilities.md).
