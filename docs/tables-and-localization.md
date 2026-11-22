# 通用表格与来源核对 / Tables and source review

v0.6.0a1，2026-11-22。全文、表格、字段及文档定位均使用所选多模态模型。新增图像投影纠偏和线框几何辅助不读取文字、不加载其他 OCR 模型，也不需要新增运行依赖。

## 页面操作

1. 在提取工作台选择“通用表格识别”、模型和文件，不需要预先定义业务字段。
2. 展开“运行设置”选择页范围、小角度纠偏及直接/精细定位。设置默认不自动纠偏、不追加定位调用；清晰线框辅助默认开启，可选择“仅模型结构”关闭。
3. 左侧显示来源图像，右侧使用 PDF 页签，只渲染当前页。切换页签或预览页码同步图像和结果；多文件页签包含文件名。
4. 点击字段、文字块或表格单元格，图像框高亮并滚动到来源；点击图像框反向定位结果。悬停可预览高亮，键盘 Enter 可选择位置，页签支持左右方向键。缺失坐标的值仍显示，可复制，但不生成一个推测框。
5. 图像可缩放、适应宽度或开关全部位置框。来源图像与结果面板各自滚动；窄窗口自动纵向排列。
6. 结果可切换结构化/文字块、全文和 JSON；处理记录及原始响应折叠显示。CSV 每格保留一次值；HTML 保留行列合并。没有实现 XLSX 或可搜索 PDF。

自定义字段提取也能开启纠偏和精细定位。业务 JSON 保持原 Schema；另在 `field_locations` 返回 JSON-pointer 字段 ID、实际来源页和位置。联合 PDF 每组页面只做一次业务提取，再逐页定位；同一值出现在多个页时保留各页证据，不自动判定唯一归属。默认直接提取的历史任务不会自动增加字段坐标。

## HTTP 与 CLI

使用已有社区工作空间令牌或在线团队接口密钥，先通过 `/api/files` 上传，再提交：

```http
POST /api/recognition/jobs
Authorization: Bearer <TOKEN>
Content-Type: application/json

{
  "task": "tables",
  "provider_id": "<SAVED_PROVIDER_ID>",
  "file_ids": ["<UPLOADED_FILE_ID>"],
  "pages": [1, 2],
  "deskew": true,
  "grid_assist": true,
  "position_mode": "contextual",
  "max_localization_batches": 4
}
```

轮询 `GET /api/jobs/{id}`。每个结果单元对应一页，PDF 最多 20 页，仍使用原任务队列、权限、存储/页额度及取消机制。

单表导出：`GET /api/jobs/{id}/tables/{unit_index}/{table_index}/export?format=csv`，索引从 0 开始，format 也可为 html。导出读取已保存结果，不再调用模型；CSV 防止公式执行，HTML 转义全部内容并作为带沙箱策略的附件下载。

```bash
python -m workbench tables examples/synthetic-table.png --deskew --position-mode contextual --output result.json
python -m workbench tables examples/synthetic-table-multipage.pdf --format json
python -m workbench ocr examples/synthetic-invoice.png --position-mode contextual
```

凭据和模型地址沿用 README 中的环境变量；不将它们写入命令行参数。`--no-grid-assist` 可关闭线框辅助。

独立 Python 字节接口：

```python
from pathlib import Path
from workbench.document_analysis import recognize_document

# provider is your validated private model profile; do not embed credentials in source.
answer = recognize_document(provider, Path("examples/synthetic-table.png").read_bytes(),
                            task="tables", deskew=True, position_mode="contextual")
print(answer["status"])
```

独立库调用由调用方负责网络和输入安全；需要有界 PDF 解码、租户隔离或持久化恢复时使用 CLI/HTTP。

## 发布为对外服务

应用管理的“发布识别能力”可选自定义字段、全文 OCR 或通用表格。发布时固定 task、字段/提示词、模型连接 ID、旋转、纠偏、线框辅助、定位策略和追加调用上限。通用能力使用内置识别契约，发布快照保存实际提示词；自定义字段使用应用提示词。编辑草稿或以后更新内置提示词不改变历史发布。

继续使用原 `/v1/files` → `/v1/apps/{code}/recognitions` → `/v1/jobs/{id}`。客户端不能传入提示词、模型、task 或处理策略覆盖发布版本。限定应用 Key 只能访问本 Key 的上传、任务与导出。

公开单表导出为 `/v1/jobs/{id}/tables/{unit_index}/{table_index}/export?format=csv|html`，检查同一套租户、应用和 Key 所有权；公开结果不包含内部提示词、模型配置、分阶段原始响应或存储路径。

## 结构与几何来源

- `data.fields[]` 保存 label/text；`data.tables[].cells[]` 保存零起始 row/column、正整数 row_span/column_span、is_header 和 text。支持多行表头、表体、多个表以及页面 KV。重复值、空单元格和金额不根据邻行补写。
- 校验索引、跨度、重叠、有限数和图像边界。最多 10 表、200 字段、3000 返回单元格、20000 个跨度覆盖槽位；每表最多 300 行、60 列。页面预览前 100 行，导出保留全部结果。
- 模型框使用 0..1000 的相对坐标，先除 1000 乘实际推理图宽高。依次逆纠偏、逆旋转、加裁剪偏移，再映射到原图像素或 PDF points。保留四角 polygon、逆矩阵、实际推理尺寸和原始模型框；文字几何不提前取整。
- 小角度纠偏使用最长边 512 的图像做 ±8° 投影搜索；空白/过暗、弱改善和边界最优时不旋转。它不是自动 90° 内容方向检测、透视校正或照片展开，也不是可靠概率评分。
- 精细定位结合标签、前后文字和行上下文；表格请求实际行带/列带。字段支持标签和值的完整匹配，数值不截短、不接受任意子串。缺失单元格只在完整行列带确实相交时推导，标记 `model_band_intersection`，不伪装成直接文本检测。
- 线框辅助只处理**单张大型、清晰、接近水平的线框表**：行列数须与模型一致，外边界须完整，不能把两个非空模型单元格强行合并。识别文字始终来自模型；线框只修正已有单元格的几何与合并范围，标记 `image_grid_cell`，另保留 `model_box` 和 `geometry_box`。缺少模型内容的网格记录在 `unrecognized_grid_cells`，任务标为 partial；不抄入标准答案或编造缺失内容。无框/多表/弱线/不匹配时保留模型结构；可关闭辅助。
- 新字段的来源有 `model_reported`、`model_contextual`、`model_band_intersection`、`image_grid_cell` 或 `unavailable`。框是整格范围还是文字位置由该来源区分。置信度均为 null，模型位置仍需人工或标注集核验。

## 调用成本、恢复和失败

直接 OCR/表格每页一次提取；多文档另有每页定位和各实例提取。精细定位每批最多 24 个目标，每页/实例默认最多追加 4 次、可配置 1–8 次；超出上限明确标记 truncated/partial。实际线框已确定的格子无需再请求模型行列带。自定义字段最多收集 300 个叶子位置，达到上限明确提示。

提取响应、定位目标快照和每批响应在下一次调用前保存。单项坏位置不抹掉其他有效项；定位失败、遗漏或截断保留原提取，标记 partial。重启复用已完成阶段，不自动重试已失败定位；中断时未落库的在途调用仍可能重复计费。页额度计入队输入页，模型真实词元另累计，不把多次调用当作免费。

没有自动跨页续表合并、人工修改/审核回写或代表性商用精度承诺。当前合成样本与权限、恢复检查见 [验证记录](validation.md)。

## English

Choose **General table recognition** to extract page KV and tables without a custom schema. The review workspace uses PDF page tabs, separately scrolling source/results, bidirectional field/block/cell selection, zoom, source overlays, copy actions and CSV/HTML exports. Narrow windows stack the panels vertically.

Optional small-angle projection deskew records its inverse matrix. Contextual localization validates text/labels and real row/column anchors, preserves extraction after failures and has an explicit call budget. Conservative pixel-grid assistance can fix merged spans for a single clear ruled table when its dimensions agree with model structure; it never recognizes or fills missing text and can be disabled. Source polygons map through all transforms to the original page. Model positions remain unverified and confidence remains null.

Use `POST /api/recognition/jobs` with `task=tables`, or `python -m workbench tables FILE`. The independent bytes API is `workbench.document_analysis.recognize_document`. Existing custom-schema and joint-PDF tasks can return separate per-page `field_locations` without changing business JSON. Published applications freeze the task, prompts and processing settings and remain callable through `/v1` with the existing tenant/application/key boundaries, including table exports.

Limits: 20-page PDFs, 10 tables/page, 3000 returned cells, 300 rows/60 columns per table; UI previews 100 rows. Refinement defaults to at most 4 additional calls/page or instance, 24 targets/call. No perspective correction, content-based quarter-turn detection, XLSX/searchable-PDF output, automatic continuation merging or human correction workflow is claimed.
