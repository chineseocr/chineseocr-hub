# Changelog

## 0.6.0a1 — 2026-11-22 — Tables and reversible localization

- Add bilingual Qwen3-VL/vLLM GPU Compose and OpenAI API connection tutorials, linked from README, UI and operations guides. Add explicit compatible/OpenAI request profiles with legacy-safe defaults, environment/CLI support and local HTTP regressions; no automatic parameter retries. Qwen GPU execution and live OpenAI inference remain unvalidated.

- Add schema-free mixed KV/table recognition with header/body cells, validated merged spans, source selection and authenticated CSV/HTML exports.
- Add conservative projection deskew, recorded inverse affine matrices and source polygons; no additional OCR engine or weights.
- Add bounded contextual text localization and model row/column-band intersections, plus conservative pixel-grid assistance for one large ruled table. Preserve missing positions and original extraction on partial failures.
- Persist extraction and localization batches for recovery and actual usage; extend geometry/localization to custom schemas, joint PDFs and routed instances without changing business JSON.
- Freeze recognition task, processing settings and prompts in application releases; expose generic tables through scoped public API and exports.
- Add bilingual controls, synthetic table/skewed/PDF fixtures, and standalone library/CLI entry points.
- Redesign the review workspace with PDF page tabs, linked fields/text/cells and clickable source polygons, zoom, separate scroll panes, empty-field grouping and JSON/text views. Preserve page selection across refresh and language changes.
- Document Docker build/start, SaaS bootstrap, host-model networking, stopped-volume backup/restore, upgrades and offline images in Chinese and English. Add bilingual end-to-end UI guides and README navigation. Annotate the non-root Dockerfile, support local image/port/base-image configuration, host-gateway mapping and rotated logs; container execution remains an external validation item.


## 0.5.0a1 — 2026-11-22 — Applications and public APIs

- Retain fractional OCR coordinates until source mapping, normalize EXIF in standalone OCR and expose raw model boxes/inference dimensions for geometry review. Keep raster crop rounding separate; document medical-pipeline differences without claiming improved model localization.
- Add server-side searchable/paginated application catalogs, categories/tags/owners, copy, portable draft bundles and schema/prompt version diffs.
- Add 17 independently editable bilingual credential configurations; explicit idempotent installation preserves user versions and does not auto-publish.
- Add immutable template/provider/mode releases, historical activation and disabling, without draft changes altering live calls.
- Add authenticated /v1 upload, application-code submission, polling, cancellation and public OpenAPI; preserve idempotency across release switches.
- Add application-scoped SaaS API keys with own-upload/own-job boundaries, live tenant roles and quotas; management writes require account sessions and publishing requires administrators.
- Validate 150-application pagination/search and a real multimodal synthetic invoice through the published public API.


## 0.4.0a1 — 2026-11-22 — Multimodal OCR

- Add full-text OCR and Markdown through the selected multimodal model, without a separate OCR engine or weights.
- Add real multi-document localization, crop recognition, unique same-class instance IDs and optional versioned schema routing.
- Persist OCR/localization prompt snapshots and per-page/per-instance checkpoints; preserve raw failures, partial results and reported usage during recovery.
- Add rotation/crop inverse geometry, source-image/PDF coordinate metadata and explicitly unverified model positions with null confidence.
- Add authenticated crop/text exports, independent library and CLI APIs, bilingual task selection, routing and source overlays.
- Extend tenant-role/quota/isolation coverage to new recognition endpoints; validate synthetic OCR and two-document recognition against a live multimodal service.
- Document current implementation and limits; automatic deskew, original table-grid reconstruction and human review remain future work.

## 0.3.0a1 — 2026-11-21 — ChineseOCR Hub Alpha

Documentation clarification on 2026-11-22: add a bilingual capability matrix and source-based audit. Distinguish the implemented vision-model extraction workflow from unimplemented general OCR, detection, layout/table reconstruction and human review. No new recognition capability was added by this documentation update.

- Adopt ChineseOCR Hub as the working product name and chineseocr-hub as the source/package name; preserve the workbench module and deployment variables.
- Add optional online multi-tenant mode: accounts, isolated team stores, roles, invites, scoped API keys, server-side session revocation and audit logs.
- Add encrypted SaaS model credentials, exact operator URL allowlists, atomic page/storage quotas and a metadata-only platform console.
- Add tenant-boundary, expiry, member-removal, concurrent admission and restart regressions.
- Add five bilingual application starters and an application creation dialog.
- Add immutable dictionary versions and visual field enums with explicit copy-to-template snapshots.
- Fix concurrent template saves returning another caller's newer version.
- Add a standalone launch guide for direct HTML entry and HTTP asset load failures.
- Add archive integrity/privacy/source-parity checks and extracted-source smoke tests.
- Add Linux/macOS CI, dependency audit and isolated container startup/restart persistence checks.
- Align the synthetic continuation page with its invoice number.

CI and Docker execution remain external validation items until actually run; no repository or image has been published.

## 0.2.0 — 2026-11-18

- Complete localization of branding, section headings, prompt labels and connection fields; add field/prompt shortcuts and configuration guidance.
- Document prompt/schema storage and model request boundaries; include an identity-card field schema with enum values and no personal data.
- Real vision-model workflow only; no fixed-output runtime backend or seeded accuracy.
- Chinese/English interface, persistent language selection and localized errors/date formatting.
- Endpoint normalization, model discovery and persistent default connections.
- Deployment environment bootstrap and non-destructive archival of obsolete candidate records.
- Formatted frontend and detailed function contracts, maintenance guide and deployment guide.
- Live image/PDF/evaluation validation; observed field mismatches are retained.


## 0.1.0 — 2026-11-18 — local candidate

- Independent FastAPI/SQLite workbench with a zero-build Chinese web UI.
- Visual field/group/table schema editing, Schema import/export, prompt preview and immutable versions.
- Authenticated image/PDF uploads, bounded page rendering and page selection.
- Vision Chat Completions adapter plus explicitly simulated demo provider.
- Durable jobs, checkpoints/restart recovery, idempotent submission, cancellation and explicit rerun.
- Ground truth datasets, atomic evaluation runs, leaf-field comparison and version summaries.
- Synthetic examples, Docker/Compose, dependency hashes, CI, security/contribution docs and source-only packaging.
- No legacy customer code, activation checks, model weights or automatic credential detector.

Historical note: these were the 0.1.0 candidate limitations. Later real-model validation is recorded in docs/validation.md; public CI and local Docker execution remain unverified.
