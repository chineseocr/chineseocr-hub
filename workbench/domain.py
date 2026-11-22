"""Small, explicit configuration and evaluation contracts."""

import json
import re
from typing import Any, Dict, List, Literal, Optional

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, Field, field_validator

# 以下是新建应用时的默认值，不是每次运行都会覆盖的固定规则。
# 页面编辑后的提示词和 Schema 保存在 SQLite 的 templates 表中；任务执行读取
# 提交时固定的版本快照。修改这里不会改写已有应用或历史任务。
SYSTEM_PROMPT = "Extract the requested data from the supplied document images. Return only a JSON object. Do not invent missing values; use null."
USER_PROMPT = "Extract this document according to the JSON Schema below.\n{{schema}}\nInput: {{filename}}\nSource pages: {{pages}}"
# properties 是输出字段，description 是交给模型的业务说明；items 定义表体每行。
# 字典可选值使用 enum 快照。自定义提取始终遵守所选结构；自动多文档定位与
# 类别路由在 recognition.py 中单独执行，不会隐式改写自定义提取的模板。
INVOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "invoice_number": {"type": ["string", "null"], "description": "发票号码"},
        "vendor": {"type": ["string", "null"], "description": "销售方"},
        "total": {"type": ["number", "null"], "description": "总金额"},
        "items": {
            "type": "array",
            "description": "商品明细表体",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": ["string", "null"], "description": "品名"},
                    "quantity": {"type": ["number", "null"], "description": "数量"},
                    "amount": {"type": ["number", "null"], "description": "金额"},
                },
                "required": ["description", "quantity", "amount"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["invoice_number", "vendor", "total", "items"],
    "additionalProperties": False,
}


def validate_schema(schema: dict) -> dict:
    """Deliberately support a safe, finite subset; reject references and remote resolution."""
    if len(json.dumps(schema)) > 40000:
        raise ValueError("Schema 超过 40KB")
    allowed = {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "description",
        "enum",
        "title",
    }

    def visit(node, depth=0):
        """Walk the supported subset without external references or arbitrary resolvers."""
        if depth > 6 or not isinstance(node, dict) or set(node) - allowed:
            raise ValueError("仅支持 6 层以内的字段、对象、数组、enum；不支持 $ref 等外部引用")
        kind = node.get("type")
        kinds = kind if isinstance(kind, list) else [kind]
        if not kinds or any(
            k not in {"object", "array", "string", "number", "integer", "boolean", "null"} for k in kinds
        ):
            raise ValueError("每个字段必须声明有效 type")
        if "object" in kinds:
            props = node.get("properties", {})
            if not isinstance(props, dict) or len(props) > 100:
                raise ValueError("对象最多包含 100 个字段")
            if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key) for key in props):
                raise ValueError("字段 key 使用字母、数字和下划线，且不能以数字开头")
            for child in props.values():
                visit(child, depth + 1)
            if not set(node.get("required", [])) <= set(props):
                raise ValueError("required 引用了不存在的字段")
            if node.get("additionalProperties", False) not in (True, False):
                raise ValueError("additionalProperties 仅支持布尔值")
        if "array" in kinds:
            visit(node.get("items"), depth + 1)

    if schema.get("type") != "object":
        raise ValueError("根 Schema 必须为 object")
    try:
        visit(schema)
        Draft202012Validator.check_schema(schema)
    except (SchemaError, TypeError):
        raise ValueError("Schema 定义无效，请检查 required、enum、description 和字段类型") from None
    return schema


def render_prompt(template: str, values: dict) -> str:
    """Replace only the three supported variables.

    This is literal substitution rather than a template engine: document text cannot execute Python or resolve arbitrary expressions."""
    variables = re.findall(r"\{\{(.*?)\}\}", template)
    if any(v not in {"schema", "filename", "pages"} for v in variables):
        raise ValueError("仅支持 {{schema}}、{{filename}}、{{pages}} 变量")
    return re.sub(r"\{\{(schema|filename|pages)\}\}", lambda m: str(values[m[1]]), template)


class TemplateInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    schema_: Dict[str, Any] = Field(alias="schema")
    system_prompt: str = Field(default=SYSTEM_PROMPT, max_length=20000)
    user_prompt: str = Field(default=USER_PROMPT, max_length=20000)

    @field_validator("schema_")
    @classmethod
    def schema_valid(cls, value):
        """Validate the bounded supported Schema subset before any template is saved or any model is called."""
        return validate_schema(value)

    @field_validator("system_prompt", "user_prompt")
    @classmethod
    def prompt_valid(cls, value):
        """Reject unsupported placeholders at configuration time using a harmless preview context."""
        render_prompt(value, {"schema": "{}", "filename": "sample.png", "pages": "1"})
        return value


class DictionaryInput(BaseModel):
    """A versioned list of literal JSON values, copied into a field's enum on request."""

    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    values: List[Any] = Field(min_length=1, max_length=200)

    @field_validator("values")
    @classmethod
    def literal_values(cls, values):
        """Bound size and reject structures, non-finite numbers and equivalent duplicates."""
        seen = set()
        for value in values:
            if not isinstance(value, (str, int, float, bool, type(None))):
                raise ValueError("字典仅支持文本、数字、布尔值和空值")
            # JSON numbers compare numerically, while true and 1 are distinct values.
            kind = (
                "number"
                if isinstance(value, (int, float)) and not isinstance(value, bool)
                else type(value).__name__
            )
            key = (kind, value)
            if key in seen:
                raise ValueError("字典可选值不能重复")
            seen.add(key)
        try:
            encoded = json.dumps(values, ensure_ascii=False, allow_nan=False)
        except ValueError:
            raise ValueError("字典数字必须为有限值") from None
        if len(encoded.encode()) > 20000:
            raise ValueError("字典可选值总大小不能超过 20KB")
        return values


class ProviderInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["openai"] = "openai"
    base_url: str = Field(default="http://localhost:8001/v1", max_length=500)
    model: str = Field(default="", max_length=200)
    api_key: str = Field(default="", max_length=2000)
    # Explicit wire format, not an inference from a model name or destination host.
    # Old connections retain the compatible-service request unchanged.
    request_profile: Literal["compatible", "openai"] = "compatible"
    max_tokens: int = Field(default=4096, ge=128, le=16384)
    timeout_seconds: int = Field(default=90, ge=5, le=180)

    @field_validator("base_url")
    @classmethod
    def url_valid(cls, value):
        """Validate and normalize an administrator-selected endpoint; credentials belong in the separate secret field."""
        from urllib.parse import urlsplit

        # Pasted URLs often contain trailing spaces (including non-breaking spaces).
        # A bare service root is normalized to the standard /v1 API base.
        value = value.strip().rstrip("/")
        p = urlsplit(value)
        if (
            p.scheme not in {"http", "https"}
            or not p.hostname
            or p.username
            or p.password
            or p.query
            or p.fragment
        ):
            raise ValueError("请输入不含用户名、查询参数的 http(s) 模型服务地址")
        return value if p.path else value + "/v1"


class JobInput(BaseModel):
    template_id: str
    version: Optional[int] = None
    provider_id: str
    file_ids: List[str] = Field(min_length=1, max_length=5)
    mode: Literal["document", "page"] = "document"
    pages: List[int] = Field(default_factory=list, max_length=20)
    rotation: Literal[0, 90, 180, 270] = 0
    deskew: bool = False
    position_mode: Literal["direct", "contextual"] = "direct"
    max_localization_batches: int = Field(default=4, ge=1, le=8)


def decode_json(raw: str):
    """Parse a complete JSON object, optionally enclosed in one Markdown code fence.

    Do not repair quoted values or extract arbitrary fragments: invalid/truncated outputs must remain visible failures."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    def reject_constant(constant):
        """Reject non-standard numbers rather than persisting values browsers cannot parse."""
        raise ValueError("非法 JSON 数值：" + constant)

    try:
        value = json.loads(text, parse_constant=reject_constant)
    except RecursionError:
        raise ValueError("模型输出嵌套过深") from None
    if not isinstance(value, dict):
        raise ValueError("模型输出的根结构必须为 JSON object")
    return value


def schema_errors(schema, value):
    """Return every JSON Schema violation with its output path; errors never turn a request into a successful extraction."""
    return [
        {"path": "/" + "/".join(map(str, e.absolute_path)), "message": e.message}
        for e in Draft202012Validator(schema).iter_errors(value)
    ]


def flatten(value, path=""):
    """Convert JSON leaves into escaped JSON Pointer paths.

    Arrays use positional indices. Empty containers count as leaves; escaping prevents keys containing / or ~ from colliding."""
    if isinstance(value, dict) and value:
        return {
            p: v
            for k, child in value.items()
            for p, v in flatten(child, path + "/" + k.replace("~", "~0").replace("/", "~1")).items()
        }
    if isinstance(value, list) and value:
        return {p: v for i, child in enumerate(value) for p, v in flatten(child, path + "/" + str(i)).items()}
    return {path or "/": value}


def compare(gold, prediction):
    """Gold leaf paths are the denominator; failed jobs produce zero matched leaves."""
    expected = flatten(gold)
    actual = flatten(prediction) if prediction is not None else {}
    rows = []
    for path, value in expected.items():
        present = path in actual
        other = actual.get(path)
        # JSON numbers compare numerically; booleans must never compare equal to 0/1.
        equal = present and value == other and (isinstance(value, bool) == isinstance(other, bool))
        rows.append(
            {"path": path, "expected": value, "actual": other, "missing": not present, "match": equal}
        )
    return {
        "matched": sum(r["match"] for r in rows),
        "total": len(rows),
        "fields": rows,
        "extra_paths": sorted(set(actual) - set(expected)),
    }
