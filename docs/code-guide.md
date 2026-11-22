# 代码阅读与维护 / Code reading guide

本版同时提供本地单工作空间与在线多租户真实模型应用。阅读时先看业务契约，再看数据和执行，最后看 HTTP/页面；不要从事件回调猜完整流程。Python 公共函数有契约说明，前端与样式已展开成可审查格式。

## 阅读顺序

| 文件 | 责任 | 维护时注意 |
|---|---|---|
| `workbench/applications.py` | 应用目录、配置包、版本差异、不可变发布、公开识别 API | 模板是唯一字段/提示词真源；public 请求不能覆盖模型/Schema；幂等按身份/应用隔离，跨发布保留原任务 |
| `workbench/credential_presets.py` | 17 套双语证件结构与专属提示词 | 安装复制为可编辑草稿，重复安装不覆盖；证件号码/日期不推导，无精度承诺 |
| `workbench/static/applications.js` | 目录、管理详情、发布/切换、配置迁移与应用 Key 入口 | 搜索在服务器分页前完成，发布选显式版本；只渲染文本，不在页面保存密钥 |
| `workbench/domain.py` | 输入模型、Schema 子集、提示词替换、JSON 解析与字段比较 | required 不代表禁止 null；数组按位置评分；禁止外部引用和代码执行 |
| `workbench/store.py` | SQLite 连接、不可变配置、任务检查点、归档标记、默认连接 | 每次连接提交后必须关闭；动态表名只来自内部允许列表，值使用绑定参数 |
| `workbench/presets.py`、`starter_schemas/` | 双语初始应用配置 | 只提供公开字段结构；创建后配置作为业务数据，不再随界面语言翻译 |
| `workbench/bootstrap.py` | 空库初始化、部署环境连接、旧数据归档 | 不预置文件/答案/分数；相同环境配置不能重复新增；不覆盖历史快照 |
| `workbench/documents.py` | 输入校验与页面 PNG | PDFium 必须串行；透明图先白底合成；预览就是实际模型输入 |
| `workbench/adapters.py` | 模型发现与图像 Chat Completions | /models 只检查协议；真正能力用图像验证。不要泄漏密钥或上游错误正文 |
| `workbench/vision.py`、`detection.py` | 独立多模态 OCR/定位契约和旋转/裁剪坐标映射 | 归一化框映射到来源坐标，缺失位置/置信度为 null；固定契约提示词提交时快照 |
| `workbench/recognition.py`、`recognition_cli.py` | 全文与多文档任务、类别路由、实例恢复、独立 CLI | 每阶段校验模型目标并共享并发限制；定位先落库，实例失败不丢弃已完成同页结果 |
| `workbench/engine.py` | 任务预检、幂等、原子批次、单 worker 与恢复 | 一单元一检查点；中断中的请求仍可能重复计费，不能声称恰好一次 |
| `workbench/accounts.py` | 账户、密码散列、成员、会话、邀请、接口密钥及审计 | 所有随机凭据仅存散列；公开账户字段不能包含密码散列 |
| `workbench/saas.py` | 团队网关、角色策略、密钥加密、额度和平台 API | 先校验成员关系，再打开独立团队 Store；平台管理员不绕过文档权限 |
| `workbench/app.py` | 鉴权、HTTP 边界、模板/文件/任务/评测路由 | 公开响应不得带 API key、文件存储键或请求原始敏感输入 |
| `workbench/localization.py` | HTTP 操作错误翻译 | 与页面共享词条，只处理系统提示，不翻译业务数据 |
| `workbench/static/i18n.js` | 语言选择、词条加载、显式 DOM 翻译 | 使用 data-i18n 和 t()；不要扫描所有页面文本替换，也不要改写表单值 |
| `workbench/static/tenants.js` | 账户、团队、用量与平台页面 | UI 禁用不是安全边界；服务器再次校验角色；不把密码或密钥存 localStorage |
| `workbench/static/recognition.js` | 识别模式、路由、裁剪、文本和来源区域展示 | Markdown 仅显示源文本；图像与 SVG 同一预览坐标，裁剪仍走鉴权路由 |
| `workbench/static/app.js` | 页面状态、编辑、任务与评测展示 | state.template 是副本；页面显示值使用 textContent，禁止拼接 innerHTML |
| `workbench/static/locales/en.json` | 全部英文界面词条 | 中文原文作为稳定 key；调整文案时同步更新引用与词条 |
| `scripts/`、`tests/` | 合成样本生成、允许列表打包、协议替身与行为验证 | 测试 HTTP 服务只在 tests 内；绝不作为运行时后端或默认模型 |

## 一次提取的调用链

1. 页面编辑 `state.template`，`markDirty()` 对比已保存基线。撤销修改会清除草稿状态。
2. `ensureSaved()` 在必要时创建新版本；`runExtraction()` 提交文件 ID、版本和模型连接 ID。
3. HTTP 路由鉴权后调用 `prepare_job()`，一次性固定模板和输入页集合。
4. `create_job()` 在数据库中入队。worker 条件更新状态获取执行权。
5. 每个单元读取服务器生成的 PNG，渲染受控变量，调用真实模型。
6. 原始文本完整解析为 JSON object，再做 Schema 校验，写单元检查点。
7. 页面轮询并显示真实输出/usage/输入页。不会在服务失败时切换成固定业务答案。

## 一次评测的调用链

`run_dataset()` → 固定模板版本 → `create_evaluation()` 全量预检 → 一个事务写全部任务及 run → worker 提取 → `run_report()` 逐字段比较。

标准答案、文件引用、模型连接和模板版本被固定。失败/缺失字段仍在分母；待完成时 accuracy 为 null；Schema 校验失败与字段部分得分分开呈现。fingerprint 用于辨别是否在比较同一份答案快照。

## 中英文的边界

切换语言只重绘展示层，保留草稿、文件选择、预览页和模型配置。新建应用可在创建时选择当前语言的初始结构；字典复制后以字段 enum 快照保存。应用名称、字段说明、提示词和提取内容都是用户数据；即使英文页面显示中文业务内容，也不会自动重写或另存模板版本。系统错误可通过 `Accept-Language` 选择，模型原始输出保留原文。

## English maintenance notes

Start with contracts in `domain.py`, storage in `store.py`, execution in `engine.py`, and only then inspect HTTP/UI handlers. Public functions document invariants and failure behavior. A draft is a clone, versions are append-only, jobs checkpoint units, and evaluations freeze ground truth.

The runtime has only the real Chat Completions backend. Protocol substitutes exist exclusively inside tests. Never add a silent fixed-output fallback, infer confidence/coordinates, expose keys in errors, or translate user/model content as interface text. UI event errors pass through `guard()`, generated nodes use `textContent`, and locale changes do not submit inference.

## 多模态识别的调用链

`RecognitionInput` → `prepare_recognition()` 固定 OCR/定位提示词与路由应用 → `enqueue_job()` 原子预占输入页额度 → `Worker.process()` → `process_recognition()` → `vision.recognize()` 或 `detection.locate()` 后逐实例调用 → 逐页/定位/实例检查点。所有阶段调用同一个 BACKENDS 图像适配器；默认 OCR 不保存无关字段草稿。独立调用见 [多模态指南](multimodal-ocr.md)。
