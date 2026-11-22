# API 使用

v0.5 提供独立 /v1 公开识别契约与 /api 应用管理。完整应用/证件清单、发布、权限及调用示例见 [应用指南](applications.md)。

所有 `/api` 业务接口需要 `Authorization: Bearer <工作空间令牌>`，浏览器使用 HttpOnly 会话 Cookie。`/healthz` 和前端静态文件公开；客户文件没有公开静态路径。API 可用 `GET /api/openapi.json` 获取接口定义（同样鉴权）。

```bash
export WORKBENCH_URL=http://127.0.0.1:8765
export WORKBENCH_ACCESS_TOKEN="$(cat data/access-token)"
curl -H "Authorization: Bearer $WORKBENCH_ACCESS_TOKEN" "$WORKBENCH_URL/api/bootstrap"
```

`bootstrap` 返回应用、模型连接（不含密钥）、最近上传文件和数据集的 ID。创建模型连接：

```json
{
  "name": "local vision",
  "kind": "openai",
  "base_url": "http://localhost:8001/v1",
  "model": "your-vision-model",
  "api_key": "",
  "max_tokens": 4096,
  "timeout_seconds": 90
}
```

提交到 `POST /api/providers`。密钥只在此输入，后续响应仅返回 `has_api_key`。

上传图片/PDF，读取响应的 `id`：

```bash
curl -H "Authorization: Bearer $WORKBENCH_ACCESS_TOKEN" \
  -F file=@examples/synthetic-invoice.png "$WORKBENCH_URL/api/files"
```

提交提取任务：

```json
{
  "template_id": "从 bootstrap 读取",
  "version": 1,
  "provider_id": "从 bootstrap 读取",
  "file_ids": ["上传返回的文件 id"],
  "mode": "document",
  "pages": [1, 2]
}
```

发送到 `POST /api/jobs`，可选 `Idempotency-Key` 请求头（最多 128 字符）。`pages: []` 表示所有页；对所选每个文件应用同一页范围。轮询 `GET /api/jobs/{id}`；状态为 queued/running/succeeded/partial/failed/cancelled。单元结果有 succeeded/invalid/failed。`GET /api/jobs` 仅返回最近 100 条任务元信息；完整结果使用单任务接口。

`POST /api/jobs/{id}/cancel` 协作式取消；`POST /api/jobs/{id}/retry` 按原快照创建新任务。调用时发送 JSON `{}`。请求必须有 Content-Length；上传体最多 21MB（其中原件最多 20MB），其他写请求最多 256KB。

模板：`POST /api/templates` 创建；`POST /api/templates/{id}/versions` 保存新版本；`GET /api/templates/{id}/versions` 取全部历史。请求体为 name、schema、system_prompt、user_prompt。`POST /api/prompt-preview` 以同样结构预览受控变量替换，不调用模型。

数据集：`POST /api/datasets` 创建 `{"name":"验证集"}`；`POST /api/datasets/{id}/samples` 添加 `{"file_id":"...","gold":{"field":"answer"}}`；`GET /api/datasets/{id}` 查看。Gold 必须为单个文档 object。

评测：`POST /api/datasets/{id}/runs` 输入 template_id、version、provider_id；整批入队后返回 run id。`GET /api/runs/{id}` 返回样本级字段差异和指标，完成前 accuracy 为 null。失败/取消的样本仍在分母，可能保留失败前的实际字段结果。模型输出 schema 无效但有可比较字段时，字段得分与整体状态需一并评估。

文件预览 `GET /api/files/{id}/pages/{page}` 返回 PNG，页码从 1 开始；原件 `GET /api/files/{id}/download` 以附件下载。不存在的文件/页面返回 404。具体可运行客户端见 `examples/extract.py`。


真实连接辅助接口：`POST /api/providers/discover` 接收 name、base_url、api_key（model 可留空），只调用 `/models` 并返回模型 ID 与耗时，不提交图片；`POST /api/providers/{id}/default` 保存默认连接。根地址自动补 `/v1`。`bootstrap` 返回 `default_provider_id`。

设置 `Accept-Language: en` 获得英文操作错误。422 返回字段位置、类型和提示，不回显原始输入或密钥。归档的旧固定输出任务/评测返回 410；不允许按旧模拟后端重跑。新任务和报告不再包含 simulated 标记。

## 初始结构与业务字典

`GET /api/template-presets` 返回五种初始结构，不创建记录、不调用模型；`Accept-Language` 决定新建结构的说明和提示词语言，业务枚举值保持原值。将所选结构的 name、schema、system_prompt、user_prompt 提交到 `POST /api/templates` 创建应用。

`POST /api/dictionaries` 创建 `{"name":"性别","description":"按原文保留","values":["男","女",null]}`；`POST /api/dictionaries/{id}/versions` 追加同样形状的新版本；`GET /api/dictionaries/{id}/versions` 返回历史。`bootstrap.dictionaries` 返回最新版本。上述接口均要求鉴权。

values 是 1–200 个不重复的 JSON 基础值，总大小最多 20KB；拒绝数组、对象和非有限数。字典库不被引擎动态解析；页面复制指定版本的 values 到字段 enum，再保存应用版本，因此字典更新不影响已提交任务。


## 多模态 OCR 与文档实例

这些接口复用当前团队、角色、任务幂等和入队页额度。viewer 可读本团队结果、来源和导出，不能提交任务。

| 路由 | 用途 |
|---|---|
| `GET /api/recognition/capabilities` | 引擎 multimodal、task/format、类别字典、最多 12 实例/页；calibrated_confidence=false |
| `POST /api/recognition/jobs` | 全文 OCR 或多文档任务，无需先创建业务字段 |
| `GET /api/jobs/{id}` | 轮询页与实例结果，含原始响应、实际用量和提示词快照 |
| `GET /api/jobs/{id}/regions/{instance_id}/crop` | 从本团队任务已保存区域生成真实 PNG 裁剪 |
| `GET /api/jobs/{id}/export?format=txt` | 终态识别任务文本下载，也接受 md；待完成 409，非识别任务或错误格式 422 |

```json
{
  "task": "documents",
  "provider_id": "provider-id",
  "file_ids": ["file-id"],
  "pages": [1, 2],
  "output_format": "text",
  "rotation": 0,
  "routes": {"invoice": {"template_id": "invoice-app-id", "version": 2}}
}
```

全文将 task 改为 ocr 并省略 routes。output_format 支持 text/markdown，rotation 为手动顺时针 0/90/180/270；pages 空数组为全部页，最多 5 文件/20 页处理单元。routes 必须使用 capabilities 中的类别，版本省略时固定提交时最新版；未配置类别执行全文 OCR。ocr 不接受 routes，全部视觉阶段使用同一 provider。

payload 包含 task、contract_version、OCR/定位 prompts 及 routes 完整快照。results 每项对应一页：全文 data 包含 text、format、blocks；多文档 instances 每项包含 id、label、box、source_box、source_unit、status、data、raw_text 和 usage，根 data.documents 是结果副本，不可与 instances 重复计数。

box 为来源预览像素；source_box 为新图片 EXIF 归一化后原尺寸像素或 PDF points，旧元数据不足时标记 preview_pixels。位置为 model_reported，缺失为 null，置信度为 null。定位非法标记 invalid 并保留原文，不修复或制造区域。多文档可以 partial，成功实例不会被丢弃；空定位显式 empty_detection=true。取消/重跑沿用原有 API，显式重跑重做全部流程。

完整 API、CLI/库调用、成本与恢复约束见 [多模态指南](multimodal-ocr.md)。标准答案评测仍是原有业务 JSON 字段比较，没有将全文/定位宣称为已具备 CER/IoU 评测。

## 在线模式控制面 / SaaS control plane

启动 `--mode saas` 后，`/api/session` 改用 `{username,password}` 登录，返回 HttpOnly 会话 Cookie；写入需要 `X-Workbench: 1` 与同源 Origin。API key 用 `Authorization: Bearer hub_...` 调用业务 API，只能访问创建时团队。`X-Tenant-ID` 可以为会话选择当前有权团队，API key 不可用它切到其他团队。控制面管理不接受 API key。

| 路由 | 方法与含义 |
|---|---|
| `/api/deployment` | GET：mode、registration、产品名，无凭据 |
| `/api/account` | GET：当前用户及可访问团队，不返回密码散列 |
| `/api/account/register` | POST：username/password/invitation；默认仅邀请注册 |
| `/api/account/active` | POST：tenant_id，更新该会话默认团队 |
| `/api/account/password` | POST：current_password/new_password，改密并撤销全部会话/API key |
| `/api/account/openapi.json` | GET：账户登录后读取控制面 OpenAPI |
| `/api/tenants` | POST：name，新建本人所有的团队 |
| `/api/tenants/members` | GET：本团队成员与角色 |
| `/api/tenants/members/{user_id}/role` | POST：role，遵守 owner/admin 权限规则 |
| `/api/tenants/members/{user_id}/remove` | POST：移除允许管理的成员并撤销团队密钥 |
| `/api/invitations` | GET：邀请元数据；POST：role，返回一次性 token |
| `/api/invitations/accept` | POST：token，加入团队 |
| `/api/invitations/{id}/revoke` | POST：撤销邀请 |
| `/api/api-keys` | GET：本人本团队密钥元数据；POST：name/role/days，仅一次返回明文 |
| `/api/api-keys/{id}/revoke` | POST：撤销本人密钥 |
| `/api/usage` | GET：UTC 月提交页、实际文件存储和模型已报告词元、团队限额 |
| `/api/audit` | GET：管理员读取最近 100 条团队操作 |
| `/api/admin/tenants` | GET：平台管理员读取团队套餐/额度元数据 |
| `/api/admin/tenants/{id}/limits` | POST：plan/monthly_pages/storage_bytes/members |
| `/api/admin/users` | GET：平台账户公开状态 |
| `/api/admin/users/{id}/status` | POST：disabled，停用时撤销会话/密钥 |

业务 `/api/openapi.json` 仍属于当前团队。401 未登录/凭据撤销，403 无成员或角色权限，404 本团队无该资源，422 输入无效或额度不足。跨团队资源按当前团队查询，不透露其他团队内容。正式类型和上下限以两个 OpenAPI 为准。


## 应用管理 API

- GET /api/application-presets：17 套证件配置；POST /api/applications/install-presets，输入 {} 安装全部，或 preset_ids 指定子集。安装不发布、不调用模型。
- GET /api/applications：q/category/state/offset/limit，最多 100/页；POST：code/category/description/tags/owner/template，template 与原模板请求体一致。
- GET /api/applications/{id}：当前目录、最新模板和历史发布；POST /metadata 编辑目录，编码固定；POST /copy 输入 code/name 创建独立草稿。
- GET /api/applications/{id}/diff?before=1&after=2：字段及系统/用户提示词差异。
- POST /api/applications/{id}/publish：version/provider_id/mode/notes；POST /activate：release_id；POST /state：disabled。在线发布/切换/停用要求 admin/owner 会话，其他目录写操作要求 member 会话。
- GET /api/applications/export?ids=id1,id2 或 /{id}/export：可移植最新配置，最多 100 个；POST /import 输入 format_version=1 和 applications 数组，冲突整批回滚。无模型连接或发布配置迁移。
- POST /v1/files 上传；POST /v1/apps/{code}/recognitions 输入 file_ids/pages/可选 release_id；GET /v1/jobs/{id}，POST /cancel；GET /v1/openapi.json 返回公开 API 契约。全部鉴权，并复用团队额度与持久化任务。

POST /api/api-keys 可带 application_ids；省略保持旧版团队范围，指定后仅允许 /v1 及相应应用，文件与任务同时限定为该 Key 所创建。查询密钥列表包含 application_ids 元数据，无明文密钥。公开结果不返回模型配置、提示词和原始模型输出。异步轮询，不含回调或同步推理。


## v0.6 表格与精细定位

通用表格任务、可选纠偏、上下文定位、自定义字段来源、发布快照及带权限的 CSV/HTML 导出见 [完整指南](tables-and-localization.md)。接口没有取消原有租户和应用 Key 限制。
