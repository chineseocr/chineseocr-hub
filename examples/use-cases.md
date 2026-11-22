# 使用案例

[English](use-cases.en.md) · [示例文件](README.md) · [操作手册](../docs/user-guide.md)

页面案例先在「模型接入」保存连接。没有模型服务时，按 [Qwen-VL / OpenAI 教程](../docs/model-connections.md) 配置。以下路径均相对于项目根目录。

## 命令行准备

CLI 可直接运行，不需要启动 Web 服务。先安装 Python 3.12 依赖：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements.lock
```

配置模型地址、实际模型 ID 和密钥。下面以 Qwen 教程的服务别名为例，地址需要从当前终端可达；CLI 不自动读取 `.env`，也不读取页面保存的连接：

```bash
export WORKBENCH_MODEL_BASE_URL=http://127.0.0.1:8000/v1
export WORKBENCH_MODEL_NAME=qwen-vl
export WORKBENCH_MODEL_API_KEY=your-model-api-key
export WORKBENCH_MODEL_REQUEST_PROFILE=compatible
```

Qwen Compose 服务默认仅在容器网络内开放，应在页面通过内部地址使用，或为 CLI 配置一个从终端可达的模型地址。OpenAI 使用教程中的地址/模型，并把参数模式改成 `openai`。命令会向所选模型发送文档并产生模型用量。

## 1. 图片转文字

页面选择「全文 OCR」，上传 [示例票据](synthetic-invoice.png)，输出格式选纯文本，点击「开始提取」。结果中查看全文、文字块与来源框，下载 TXT 或 JSON。

```bash
python -m workbench ocr examples/synthetic-invoice.png --format text --output invoice.txt
```

输出应包含图片中的票据编号、商品行和总额。需要保留模型返回的文字块及坐标时，将 `--format` 改成 `json`。

## 2. 多页 PDF 转 Markdown

上传 [双页 PDF](synthetic-multipage.pdf)，选择全文 OCR、Markdown 输出。结果区切换页签，来源预览会同步；点击文字块查看相应位置。

```bash
python -m workbench ocr examples/synthetic-multipage.pdf --format markdown --output invoice.md
```

CLI 文本输出按页顺序连接。需要分别读取每页状态、页码与坐标时，使用 `--format json`。页面运行设置支持页范围，例如 `1-2`；CLI 处理整个文件。

## 3. 表格与页面 KV

上传 [订单表格](synthetic-table.png)，选择「通用表格识别」，无需新建字段。

- 页面 KV：订单号 `TEST-042`、供应商 `Example Lab`、币种 `USD`。
- 表头与表体：商品、数量、金额，以及合并标题和总计行。
- 点击 KV 或单元格联动来源；下载 CSV 或保留合并跨度的 HTML。

```bash
python -m workbench tables examples/synthetic-table.png --output table.json
python -m workbench tables examples/synthetic-table-skewed.png --deskew --output table-deskewed.json
python -m workbench tables examples/synthetic-table-multipage.pdf --output table-pages.json
```

需要上下文定位时添加 `--position-mode contextual`。每页默认最多追加 4 批定位请求，可通过 `--max-localization-batches` 调整。返回结构与导出接口见 [表格指南](../docs/tables-and-localization.md)。

## 4. 证件字段提取

1. 在「应用管理」点击「安装常用证件配置」，搜索身份证正面、护照或驾驶证等对应类型。
2. 进入「字段与表格」，检查需要的字段；进入「提示词版本」调整提取要求并保存。
3. 在工作台选择「自定义字段提取」、对应应用版本和模型，上传该类型的文档后运行。
4. 查看字段 JSON，开启精细定位可核对字段来源。

也可新建空白应用，导入 [身份证 Schema](identity-card.schema.json)。正面和背面分别使用对应配置。17 套配置的管理与发布步骤见 [应用指南](../docs/applications.md)。

## 5. 票据与明细表

新建票据应用，导入 [票据 Schema](invoice.schema.json)，上传 `synthetic-invoice.png`。目标业务 JSON 如下，包含页面 KV 和明细数组：

```json
{
  "invoice_number": "INV-2026-001",
  "vendor": "Example Supplies",
  "total": 120,
  "items": [{"description": "Notebook", "quantity": 3, "amount": 120}]
}
```

完整字段与提示词配置见 [invoice.template.json](invoice.template.json)，标准答案见 [synthetic-invoice.gold.json](synthetic-invoice.gold.json)。修改提示词后保存新版本，可使用案例 9 对比效果。

## 6. 报销分组与嵌套明细

新建应用并导入 [分组报销 Schema](grouped-expenses.schema.json)。结构为申请编号 → 多个费用分组 → 每组的部门、小计及明细表：

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

这是输出结构示例。上传你的分组费用文档，在字段说明中定义分组依据；在提示词中说明同一分组的 KV 与明细如何对应。页面编辑器使用对象数组配置明细，更多嵌套可通过 JSON Schema 导入。

## 7. 同图多份文档

上传 [双票据图片](synthetic-multiple-invoices.png)，选择「多证件 / 混合文档」。系统先定位并裁剪两张票据，再分别识别；结果按实例查看文字、裁剪图和来源框。图片中的两个票据编号分别为 `INV-2026-001` 和 `INV-2026-002`。

```bash
python -m workbench documents examples/synthetic-multiple-invoices.png --output documents.json
```

需要字段 JSON 时，在页面的类别路由中把 `invoice` 绑定到票据应用版本；未绑定的类别执行全文 OCR。CLI 使用全文实例识别，类别字段路由通过页面或 HTTP 配置。

## 8. 发布为 API 服务

1. 新建应用，编码填写 `invoice-extraction`，使用票据字段及提示词。
2. 样本调试后选择应用版本、模型连接及「自定义字段提取」并发布。
3. 社区版使用工作空间令牌；在线版由管理员创建限定该应用的 API Key。
4. 设置 `WORKBENCH_ACCESS_TOKEN` 为相应凭据，运行客户端：

```bash
export WORKBENCH_ACCESS_TOKEN=your-workbench-or-application-api-key
python examples/recognize_application.py examples/synthetic-invoice.png \
  --app invoice-extraction --url http://127.0.0.1:8765 --output result.json
```

客户端依次上传文件、提交任务、轮询并保存结果。业务调用只需要应用编码，不需要提示词或内部模型连接 ID。接口契约见 [应用 API](../docs/applications.md)。

## 9. 比较提示词版本

1. 在「数据集与评测」新建数据集，添加 `synthetic-invoice.png` 和 `synthetic-invoice.gold.json` 中的标准答案。
2. 选择票据应用、版本和模型，运行评测。
3. 修改字段说明或提示词，保存新版本，使用同一数据集再次运行。
4. 对比字段差异、精确匹配率、失败样本及模型用量，选择合适版本发布。

评测按标准答案叶子字段比较，数组按位置对齐。详细规则见 [产品功能](../docs/product.md#评测)。
