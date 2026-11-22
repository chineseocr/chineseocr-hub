# 多模态全文 OCR 与多文档识别

v0.6.0a1 的全文转写、文档定位、分类和业务字段提取统一使用所选多模态模型。不依赖传统 OCR 引擎，不下载模型权重；可连接自行部署或云端的图像 Chat Completions 服务。服务必须接受 PNG `image_url` data URL，且能遵守输出契约。模型发现只检查协议，不证明识别质量。

## 页面使用

「提取工作台 → 识别能力」有三个入口：

1. **全文 OCR · 多模态识别**：无需创建字段，选择模型、文件、页范围、纯文本或 Markdown 即可运行。结果保留全文、文字块、原始响应、实际 usage；位置框叠加在来源页上。
2. **多证件 / 混合文档**：每页先用模型定位独立文档，实际裁剪后逐实例调用模型。同类型多个文档使用不同实例 ID，不会以类别 key 相互覆盖。结果可查看裁剪图并定位回原页。
3. **自定义字段提取**：沿用应用 Schema 与提示词，支持联合输入或逐页业务 JSON、字段校验与标准答案评测。

多文档的「按文档类别路由到字段结构」可为身份证正反面、发票、收据等类别分别选择应用。提交时固定所选版本；未配置路由的类别和未知类别执行全文 OCR。不会把当前票据应用悄悄套到所有证件上。类别由模型判断，不代表经过专项数据集验收；不自动配对正反面或跨页归并。

用户可显式选择顺时针 0/90/180/270°。可另选小角度投影纠偏并保留逆矩阵；不做自动 90° 内容方向识别或透视矫正。模型与接口错误保留为失败，不切换到固定答案或另一种 OCR 引擎。

## 提示词在哪里

- 全文输出契约：`workbench/vision.py` 的 `OCR_SYSTEM`、`OCR_USERS`。
- 文档定位契约与类别列表：同文件 `DETECT_SYSTEM`、`DETECT_USER`、`LABELS`。
- 字段路由的业务提示词与字典：页面「提示词版本」「字段与表格」「字典管理」，见 [配置说明](configuration.md)。

固定的 OCR/定位提示词保证接口输出格式，不在页面作为任意业务提示词编辑。需调整业务规则时使用自定义字段应用或类别路由。任务 `payload.prompts` 保存提交时的系统和用户提示词，`contract_version` 为 1，路由模板也保存完整快照；升级不会让排队任务随默认提示词漂移。旧任务缺失快照时兼容使用运行时默认值。

## HTTP API

先鉴权、配置模型并上传文件，见 [API](api.md)。全文任务无需 template_id：

```json
{
  "task": "ocr",
  "provider_id": "saved-provider-id",
  "file_ids": ["uploaded-file-id"],
  "pages": [],
  "output_format": "markdown",
  "rotation": 0
}
```

提交 `POST /api/recognition/jobs`，可加 `Idempotency-Key`；响应和轮询复用 `GET /api/jobs/{id}`。多文档请求将 task 改为 documents，可选：

```json
{
  "task": "documents",
  "provider_id": "saved-provider-id",
  "file_ids": ["uploaded-file-id"],
  "routes": {
    "id_card_front": {"template_id": "identity-application-id", "version": 1},
    "invoice": {"template_id": "invoice-application-id", "version": 3}
  }
}
```

`GET /api/recognition/capabilities` 查询固定类别、格式和限制。`GET /api/jobs/{id}/regions/{instance_id}/crop` 返回经过鉴权的裁剪 PNG。`GET /api/jobs/{id}/export?format=txt` 或 md 下载文本；JSON 使用任务接口。取消/重跑沿用现有任务 API。

`results` 每项对应一页，`instances` 为持久化实例检查点，含 id、label、box、source_box、status、data、raw_text、usage。`data.documents` 提供实例结果副本，避免读取方依赖内部原始响应；它和 instances 不是两批独立识别结果，不能重复计数。`data.empty_detection=true` 表示模型报告未找到文档；`review_required=true` 提醒模型定位尚待核验。位置不合法会标为 invalid，保留付费调用的原始响应，不猜测或修复坐标。

## CLI：无需启动工作台

安装项目后配置私有模型环境变量。Python 不自动读取 .env：

```bash
export WORKBENCH_MODEL_BASE_URL=https://vision.example.test/v1
export WORKBENCH_MODEL_NAME=your-vision-model
export WORKBENCH_MODEL_API_KEY=your-private-key
python -m workbench ocr examples/synthetic-invoice.png --format text
python -m workbench ocr examples/synthetic-multipage.pdf --format markdown --output invoice.md
python -m workbench documents combined-documents.png --format json --output instances.json
```

也可用安装后的 `chineseocr-hub ocr ...`。CLI 使用临时私有工作空间，处理后清理；指定的输出文件保留。凭据不放命令行参数；输入或配置无效退出 2，部分/全部识别失败退出 1，成功退出 0。文本输出不含原始错误细节，失败时使用 JSON 检查逐页/实例状态。CLI 当前不提供类别模板管理，业务路由使用 HTTP API 或页面。

## Python 库：无需数据库

```python
import os
from pathlib import Path
from workbench.domain import ProviderInput
from workbench.vision import recognize

provider = ProviderInput(
    name="Private vision model",
    base_url=os.environ["WORKBENCH_MODEL_BASE_URL"],
    model=os.environ["WORKBENCH_MODEL_NAME"],
    api_key=os.environ.get("WORKBENCH_MODEL_API_KEY", ""),
).model_dump()
result = recognize(provider, Path("examples/synthetic-invoice.png").read_bytes())
if result["status"] == "succeeded":
    print(result["data"]["text"])
else:
    print(result["status"])
```

`recognize` 接受单张图像字节；`workbench.vision.locate` 提供同模型定位及校验。直接库调用由调用方负责网络授权、文件大小与图像安全；需要 HTTP 同等输入限制、PDF 支持、任务持久化或租户隔离时用 CLI/HTTP 层。库调用不会自行创建模型连接或上传到别的端点。

## 坐标与可信度

- 模型原始 box 是归一化 0..1000 坐标；只校验有限数、顺序和边界，不保证定位准确。
- 新结果的 `model_box` 保留未经转换的模型框；`model_coordinate_space=normalized_0_1000`、`inference_width` 和 `inference_height` 记录实际推理画布。旧任务可能没有这些新增字段。
- 结果 `box` 为旋转逆变换、加裁剪偏移后的**未旋转来源预览像素**，与页面叠加层一致。
- 文字框保留小数，再映射原图；仅实际文档裁剪框向外取整。不能先将文字框取整再放大到原图。独立字节库调用也会先进行 EXIF 方向归一化。
- 新上传文件的 `source_box` 映射到 EXIF 归一化后原图像素（source_unit=pixels），PDF 则为 PDFium 页面的 points 坐标，左上角为原点。旧文件缺少原尺寸时显式回退为 preview_pixels。
- 文本块的位置缺失时 box/source_box 为 null，position_source=unavailable；有位置时为 model_reported。
- confidence、detection_confidence 均为 null。语言模型输出的位置或自评不是经过校准的检测/识别置信度。业务字段路由保留实例来源框，不声称每个字段都有准确坐标。

Markdown 是模型生成的排版文本，页面以纯文本源显示，不执行其中 HTML/脚本；Markdown 表格不代表恢复了原表合并单元格或网格。裁剪来自预览 PNG，不会凭坐标得到原图里已丢失的小字细节。

坐标变换顺序、与医疗票据链路的差异和本轮合成样本观测见 [坐标处理说明](coordinate-processing.md)。这些几何修正不代表模型框已通过质量验收。

## 多页、费用与恢复

单文件 20MB，PDF 最多 20 页，单任务最多 5 文件、20 页处理单元；页范围对每个文件独立应用。OCR 和多文档均逐页处理，不将 PDF 多页塞进一次请求，也不自动合并续表。自定义字段联合模式仍最多 8 页/文件。

全文每页一次调用；多文档每页一次定位，再对每个实例调用一次，最多 12 实例/页，理论单任务最多 260 次调用。额度预占仍按**输入页**计数，并非按模型请求数；模型用量汇总已返回的非负词元计数，缺失字段不估算。定位、实例 OCR 和路由都经过同一平台允许列表及全平台最多 4 个在途调用限制。超时和非法输出不会自动重试。

定位完成和每个实例完成时分别检查点落库；恢复复用已定位框/实例 ID，跳过已完成实例。单实例失败仍保留同页其他成功结果。未落库的在途请求仍可能恢复后再次计费；显式重跑会创建新任务并重跑全部流程。取消只能停止后续调用，不能撤回已经发送的请求。

## English reference

All stages use the selected multimodal endpoint: full-text transcription, document localization/classification and optional schema extraction per real crop. No separate OCR runtime or weights are installed. Choose **Full-text OCR**, **Multiple documents**, or **Custom field extraction** in the workbench. The first two do not require a schema.

Use `POST /api/recognition/jobs` with task ocr/documents, provider_id and uploaded file_ids. Poll the standard job endpoint; authenticated crop and TXT/Markdown download endpoints are shown above. Library APIs are `vision.recognize` and `vision.locate`; the CLI shares the bounded HTTP ingestion pipeline in a temporary workspace.

Model-reported positions remain unverified and confidence is null. Optional small-angle projection deskew and contextual refinement are available; see [tables and localization](tables-and-localization.md). PDF pages are rendered to at most 1600px. Source coordinates map back to oriented image pixels or PDF points when metadata is present. Localization and instance results are checkpointed independently; interrupted in-flight calls can still be billed twice. A page quota counts submitted pages, not multimodal request count. No continuation-table merging, original table-grid reconstruction, automatic quarter-turn orientation detection or human review workflow is claimed.
