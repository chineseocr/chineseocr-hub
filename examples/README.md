# 合成示例

本目录全部业务数据由 `scripts/generate_examples.py` 人工构造，按本项目 Apache-2.0 许可提供，不含客户信息。Pillow 默认内置字体用于生成图像；没有捆绑系统字体文件。

- `synthetic-invoice.png`：虚构票据，与 `synthetic-invoice.gold.json` 对应。
- `synthetic-multipage.pdf`：同一合成票据与一页说明，用于验证联合/逐页处理；不代表真实扫描件难度。
- `invoice.schema.json`：KV 与明细表组合。
- `key-value.schema.json`：普通 KV 字段。
- `grouped-expenses.schema.json`：分组 KV + 每组明细数组。仅提供配置，无真实医疗/报销样本。
- `invoice.template.json`：可直接提交到模板创建接口的完整配置。
- `extract.py`：真实调用工作台 API 的客户端；默认使用工作空间选定的真实模型，也可通过 provider-id 指定连接。

每次提取都会调用已配置的真实模型，可能产生模型服务费用。合成样本不代表真实扫描件难度；应按标准答案照实比较实际输出。

全文与 PDF 的直接调用（先设置 README 所列模型环境变量）：

```bash
python -m workbench ocr examples/synthetic-invoice.png --format text
python -m workbench ocr examples/synthetic-multipage.pdf --format markdown --output invoice.md
```

全部视觉步骤使用多模态模型；全文无需先创建业务模板。多文档、API 和独立库调用见 [使用指南](../docs/multimodal-ocr.md)。

`recognize_application.py` 调用已发布应用的公开 API，无需知道模板/模型连接 ID；密钥从 WORKBENCH_ACCESS_TOKEN 读取。例：`python examples/recognize_application.py authorized.png --app identity-card-front --url http://127.0.0.1:8767 --output result.json`。先发布应用并取得有权 Key；不要用未授权证件做测试。
