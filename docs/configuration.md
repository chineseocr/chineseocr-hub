# 提示词、字段与字典在哪里配置

## 应用目录入口

v0.5 起先进入「应用管理」，搜索或新建业务场景。点击「安装常用证件配置」将 17 套证件字段与提示词保存为可编辑草稿；详情提供配置字段、编辑提示词、样本调试、评测、版本对比和发布。每种提示词属于对应应用版本，发布固定版本后通过稳定应用编码调用，详见 [应用管理](applications.md)。

## 页面入口

业务提取先选择「自定义字段提取」，再选顶部的「提取应用」和「配置版本」，字段与业务提示词属于这个应用版本。全文 OCR 无需字段配置；多证件可按类别选择应用路由，详见 [多模态 OCR](multimodal-ocr.md)。

| 配置 | 页面入口 | 实际用途 |
|---|---|---|
| 普通字段、分组、表头、表体 | 字段与表格 | 字段标识是输出 JSON 的 key；说明告诉模型提取什么；数组的子字段定义每行的列 |
| 业务字典库 | 字典管理 | 新建、编辑并保存不可变版本；每版最多 200 个不重复的基础 JSON 值 |
| 字段的可选值 | 字段与表格 → 字段右侧「⋯」 | 手动编辑 `enum` 或复制字典的指定版本；导入 JSON Schema 也可配置 |
| 系统提示词、用户提示词 | 提示词版本 | 系统提示词定义提取原则，用户提示词包含字段结构、文件名、页码等上下文 |
| 最终文本预览 | 提示词版本 → 查看渲染后的请求 | 用 sample.pdf、第 1、2 页替换变量；仅预览，不调用图像模型 |
| 服务地址、模型、接口密钥、参数模式 | 模型接入 | 配置真实推理服务，与应用的业务规则分开保存；[Qwen/OpenAI 接入案例](model-connections.md) |

点击「保存新版本」同时保存字段和提示词。开始提取或评测时，未保存的草稿也会先保存。历史版本和任务快照不会被覆盖。

「新建应用」可选择票据、身份证、分组报销、通用字段或空白结构。界面语言决定新建配置的初始提示词和说明语言；切换界面语言不会改写保存的业务配置。旧工作空间、旧应用版本保持不变。

字典管理保存可复用的版本库。字段复制指定版本后，可选值直接存入草稿的 `enum`，不是动态引用。更新字典不会扩散到已有应用；需重新复制并保存应用新版本。输出不在字典中时显示校验问题，不会自动替换识别结果。

提示词可使用 `{{schema}}`、`{{filename}}`、`{{pages}}` 三个变量。保留用户提示词中的 `{{schema}}` 才会把字段定义传给模型；字段结构始终用于返回结果的校验。

## 为不同文档配置应用

「自定义字段提取」使用当前所选应用的字段与提示词。为身份证、票据等文档分别创建应用，运行时选择对应版本；多文档图片可按类别配置应用路由。

配置身份证提取时：

1. 点击「新建应用」，命名为「身份证提取」。
2. 选择「身份证正面」初始结构；或在空白应用的「字段与表格 → 导入提取结构」粘贴 [identity-card.schema.json](../examples/identity-card.schema.json)。也可以逐个添加姓名、号码、住址等字段。
3. 在「提示词版本」设置业务原则，例如「仅提取图片中印刷的证件字段；看不清或未出现时返回 null，不推断补全；仅返回 JSON」。用户提示词保留 `{{schema}}`。
4. 保存版本，确认选择了对应应用后再运行。多证件图片切换「多证件 / 混合文档」，在类别路由中将身份证正面绑定此应用；模型定位后真实裁剪并逐实例提取。该结构本身只描述一个实例，不负责正反面配对。

该文件可直接作为身份证应用的字段初始结构。

## 字典与界面翻译的区别

业务字典属于提取规则，例如：

```json
{
  "type": ["string", "null"],
  "description": "证件上的性别；没有显示或看不清时返回 null",
  "enum": ["男", "女", null]
}
```

`enum` 会随 `{{schema}}` 进入提示词，也用于返回值校验。它不是数据库查询或同义词归一化；模型未按字典输出时会显示校验问题，不会静默改写结果。允许缺失值时，要同时允许 `null` 类型和 `null` 枚举值。

界面语言词典是 `workbench/static/locales/en.json`，以中文文案为 key。它只控制导航、按钮、标签和状态，不参与 OCR。切换语言不会改写应用名称、字段说明、提示词、文件名或模型输出。内置票据应用的选择标签支持中英文显示，数据库中的原始名称与版本保持不变。

## 后端与外部模型的分工

```text
页面编辑字段与提示词
    → SQLite 保存应用版本
    → 创建任务，固定版本与来源页
    → 将 PDF/图片转换为页面 PNG
    → 替换提示词变量
    → POST /v1/chat/completions（system + user + image_url）
    → 保存原文，解析 JSON，校验字段结构
    → 页面展示、导出或与标准答案比较
```

工作台后端使用 FastAPI、SQLite WAL 和单个持久化任务 worker。图像模型在外部独立部署。适配器发送系统提示词、含字段结构的用户提示词，以及页面 PNG 的 data URL；使用非流式输出；`compatible` 参数模式发送 `temperature=0` 和 `max_tokens`，`openai` 模式不传 temperature、改用 `max_completion_tokens`。两者均使用连接中配置的输出预算，不根据模型名称猜测或自动重试。当前未发送服务端 `response_format/json_schema` 参数，格式约束由提示词和工作台返回后校验共同完成。

兼容接口及 `/models` 元信息只能说明模型服务暴露了什么协议和模型标识，不能确定服务内部的训练规则、聊天模板、隐藏提示词或预处理实现。确认那些细节需要对方的启动配置、模型说明和服务源码。工作台源码中没有对方的模型权重或私有服务代码。

## 源码与保存位置

| 位置 | 内容 |
|---|---|
| `workbench/domain.py` | 新建应用的默认 `SYSTEM_PROMPT`、`USER_PROMPT`、`INVOICE_SCHEMA`；结构校验与受控变量替换 |
| `workbench/bootstrap.py` | 空工作空间的票据应用初始化；环境变量模型连接初始化 |
| `data/workbench.sqlite3` 的 `templates.body` | 页面保存的 `name`、`schema`、`system_prompt`、`user_prompt`；用应用 ID 和版本定位 |
| `data/workbench.sqlite3` 的 `providers.body` | 模型地址、模型 ID、参数模式和连接设置；API 不提供原地修改，变更时新建连接；旧连接缺少模式时视为 compatible |
| `data/workbench.sqlite3` 的 `jobs.payload` | 任务使用的字段与提示词版本快照 |
| `data/workbench.sqlite3` 的 `dictionaries.body` | 字典名称、说明和可选值；以字典 ID 和版本定位 |
| `workbench/presets.py`、`starter_schemas/` | 公开初始字段结构与双语提示词，不含客户图像 |
| `workbench/vision.py`、`recognition.py` | 全文/定位固定契约、系统与用户提示词；提交时快照到 jobs.payload.prompts；实例检查点与类别路由 |
| `workbench/engine.py` | 读取来源页和配置快照，替换变量，执行模型请求及结构校验 |
| `workbench/adapters.py` | 构造真实 Chat Completions HTTP 请求并读取响应 |
| `workbench/static/i18n.js`、`locales/en.json` | 界面语言与英文文案，和业务字典无关 |

修改默认 Python 常量只影响之后创建的应用，不会替换数据库已有版本。日常调参应通过页面保存新版本；私有数据库和真实业务文件不进入源码发布包。

## English notes

Choose an application and version first. Configure fields under **Fields & tables**, reusable dictionaries under **Dictionaries**, field enums through the choices button beside each field, and system/user prompts under **Prompt versions**. Keep `{{schema}}` in the user prompt to send field definitions to the model. Saved configurations are immutable SQLite versions; jobs freeze their own snapshots.

The starter invoice application does not classify documents. Create an identity-document application using `examples/identity-card.schema.json` before attempting that task. For mixed images, select Multiple documents and route the detected class to that application. Localization and real per-instance cropping are implemented; automatic front/back pairing is not. The example contains schema only, with no identity data or accuracy claim.

The FastAPI worker sends rendered prompts and page PNGs to an independent Chat Completions service, then parses and validates the returned JSON. Public API metadata does not reveal the provider's training, hidden prompts or internal chat template. UI translations in `locales/en.json` do not alter extraction configuration or model output.

Dictionary copies are snapshots, not live references. Library updates never mutate saved application or task versions. New applications offer invoice, identity-card, grouped-expense, key-value and blank starters in the current interface language.


## 在线模式的保存位置

以上配置界面在 community/saas 共用。saas 的每个团队保存在 `data/saas/tenants/<服务器生成的团队ID>/workbench.sqlite3`，原件/页面位于同一团队目录。提示词和 Schema 在 templates，字典在 dictionaries；团队之间不共享库或文档。模型密钥在团队 providers 中加密，主密钥独立保存；账户、权限和审计位于 `data/saas/platform.sqlite3`。详细权限见 saas.md。源码中的 starter_schemas 只是新建时复制的初始结构，不是用户保存后的配置真源。
