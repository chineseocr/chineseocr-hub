"""Public starting configurations, separate from saved customer applications.

每次返回独立副本，只有用户明确创建应用时才入库。初始结构不包含证件图片、
识别答案、模型连接或准确率。语言只在创建时决定；不会翻译已有业务版本。
"""

import json
from pathlib import Path

from .domain import SYSTEM_PROMPT, USER_PROMPT, TemplateInput

CATALOG = [
    ("invoice", "票据提取", "Invoice extraction", "普通字段与商品明细表", "Invoice fields and line items"),
    (
        "identity-card",
        "身份证提取",
        "Identity card extraction",
        "单张证件字段与性别字典",
        "Single-card fields and a gender enum",
    ),
    (
        "grouped-expenses",
        "分组报销提取",
        "Grouped expense extraction",
        "文档字段、分组字段及嵌套明细表",
        "Document fields, groups and nested expense tables",
    ),
    ("key-value", "通用字段提取", "Key-value extraction", "文档编号与签发单位", "Document number and issuer"),
    ("blank", "空白应用", "Blank application", "从空结构开始配置", "Start with an empty schema"),
]
ENGLISH_DESCRIPTIONS = {
    "发票号码": "Invoice number",
    "销售方": "Vendor",
    "总金额": "Total amount",
    "商品明细表体": "Line items",
    "品名": "Item description",
    "数量": "Quantity",
    "金额": "Amount",
    "证件上的姓名，按原文保留": "Printed name; preserve the original text",
    "证件上的性别；没有显示或看不清时返回 null": "Printed gender; return null when absent or unreadable. Preserve the printed Chinese value.",
    "证件上的出生日期，按原文保留；不可从证件号码推断": "Printed birth date; preserve the original text and do not infer it from the ID number",
    "证件上印刷的住址，保留完整内容": "Full printed address",
    "证件上印刷的公民身份号码，使用文本保留前导零和末尾字母；不可推断缺失字符": "Printed citizen ID number as text; preserve leading zeros and trailing letters, without inferring missing characters",
    "报销申请编号": "Expense claim number",
    "费用分组；每组同时有 KV 和明细表": "Expense groups, each containing fields and a detail table",
    "科室/部门": "Department",
    "本组小计": "Group subtotal",
    "文档编号": "Document number",
    "签发单位": "Issuer",
}
CHINESE_SYSTEM = "从所提供的文档图片中提取指定字段，仅返回一个 JSON 对象。以图片中的实际内容为准，不推断或编造缺失信息；缺失或无法辨认的值返回 null。"
CHINESE_USER = (
    "请按照以下 JSON Schema 提取文档字段：\n{{schema}}\n输入文件：{{filename}}\n来源页码：{{pages}}"
)


def starting_templates(language="zh"):
    """Return validated starter bodies; enum values remain business values in either language."""
    english = language.lower().startswith("en")

    def localize(node):
        # Traverse Schema nodes, not arbitrary dictionaries: a property may itself
        # be named "description" and its name must never be mistaken for a label.
        if "description" in node:
            node["description"] = ENGLISH_DESCRIPTIONS[node["description"]]
        for child in node.get("properties", {}).values():
            localize(child)
        if "items" in node:
            localize(node["items"])

    result = []
    for key, chinese_name, english_name, chinese_hint, english_hint in CATALOG:
        schema = (
            {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
            if key == "blank"
            else json.loads((Path(__file__).parent / "starter_schemas" / (key + ".json")).read_text())
        )
        if english:
            localize(schema)
        body = TemplateInput(
            name=english_name if english else chinese_name,
            schema=schema,
            system_prompt=SYSTEM_PROMPT if english else CHINESE_SYSTEM,
            user_prompt=USER_PROMPT if english else CHINESE_USER,
        )
        result.append(
            {"id": key, "hint": english_hint if english else chinese_hint, **body.model_dump(by_alias=True)}
        )
    return result
