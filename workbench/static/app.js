/**
 * Workspace UI controller (no build step).
 *
 * 阅读顺序：状态与 DOM 工具 → API/鉴权 → 模板与字段编辑 → 文件/提取任务
 * → 模型连接 → 数据集评测 → 事件绑定与轮询。
 *
 * state.template is an editable clone; server versions and job snapshots never change.
 * All source/model content is inserted as text. Interface messages go through i18n;
 * schema keys, prompts, filenames and extracted values never do.
 */
"use strict";
const t = (...args) => WorkbenchI18n.t(...args);
const $ = id => document.getElementById(id);
const state = {
    templates: [],
    providers: [],
    files: [],
    datasets: [],
    dictionaries: [],
    presets: [],
    mode: "community",
    recognitionTask: "ocr",
    recognitionLabels: {},
    recognitionRoutes: {},
    account: null,
    saas: null,
    dictionaryId: null,
    enumTarget: null,
    enumVersions: [],
    template: null,
    providerId: "",
    selected: new Set(),
    preview: null,
    job: null,
    dataset: null,
    run: null,
    dirty: false,
    view: "extract",
    reports: new Map(),
    compare: new Set(),
    busy: false,
    baseline: "",
    promptPreview: null
};
const names = {
    applications: ["应用管理", "管理每一种文档提取能力", "按分类、标签与版本管理提示词，验证后发布为稳定接口。"],
    extract: ["提取工作台", "把文档变成可用的数据", "定义提取目标，验证每一次结果。"],
    schema: ["字段与表格", "用结构定义你要的数据", "字段、分组和表格，在同一份提取结构中配置。"],
    prompts: ["提示词版本", "让每次调整都有迹可循", "配置、预览和保存提示词，用固定版本验证效果。"],
    dictionaries: ["字典管理", "让可选值可复用、可追溯", "维护字典版本，复制到字段；已有应用保留自己的可选值快照。"],
    team: ["团队与用量", "管理团队协作与服务用量", "成员权限、接口密钥、用量和审计，在团队内独立管理。"],
    platform: ["平台管理", "管理在线平台", "配置团队额度与账户状态，保留操作记录。"],
    datasets: ["数据集与评测", "用样本验证，而不凭感觉", "基于标准答案比较字段结果，持续积累你的验证集。"],
    jobs: ["任务记录", "每次运行，都有记录", "查看任务状态，追溯配置与输入，按需重新运行。"],
    providers: ["模型接入", "连接适合你的图像模型", "模型服务独立部署，工作台负责配置、执行与验证。"]
};
const statusNames = {
    queued: "排队中",
    running: "提取中",
    succeeded: "已完成",
    partial: "部分成功",
    failed: "失败",
    invalid: "校验未通过",
    cancelled: "已取消",
    completed: "评测完成"
};
const json = value => JSON.stringify(value, null, 2);

/** Localize the shipped starter's display name only; custom names and persisted snapshots stay intact. */
function applicationLabel(name) {
    return name === "Invoice / 票据提取" ? t("票据提取") : name;
}

/** Render preview headings in the interface language while preserving the exact rendered model prompts. */
function renderPromptPreview() {
    const preview = $("prompt-preview");
    if (!state.promptPreview) {
        const message = "点击左侧按钮生成预览。\n\n预览使用 sample.pdf 和第 1、2 页。\n实际任务会替换为上传文件名称及输入页码。";
        preview.dataset.i18n = message;
        preview.textContent = t(message);
        return;
    }
    delete preview.dataset.i18n;
    preview.textContent = t("系统提示词") + "\n\n" + state.promptPreview.system +
        "\n\n" + t("用户提示词") + "\n\n" + state.promptPreview.user;
}

/** Create an element with textContent, never innerHTML. User/model values therefore remain inert text. */
function node(tag, text, cls) {
    const e = document.createElement(tag);
    if (text !== undefined) e.textContent = text;
    if (cls) e.className = cls;
    return e;
}

/** Replace children of a known UI container and return it for subsequent rendering. */
function clear(id) {
    const el = typeof id === "string" ? $(id) : id;
    el.replaceChildren();
    return el;
}

/** Append a native option while keeping its opaque API ID separate from its displayed label. */
function option(select, value, label) {
    const e = node("option", label);
    e.value = value;
    select.append(e);
}

/** Create a non-submit action and route asynchronous failures through the shared error boundary. */
function button(text, action, cls = "text-button") {
    const e = node("button", text, cls);
    e.type = "button";
    e.addEventListener("click", guard(action));
    return e;
}

/** Display a temporary operation message without interrupting the current draft or selection. */
function toast(message) {
    $("toast").textContent = message;
    $("toast").classList.remove("hidden");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => $("toast").classList.add("hidden"), 5000);
}

/** Normalize API field errors and translate operational text; business payloads are not translated. */
function errorText(detail) {
    return Array.isArray(detail) ? detail.map(x => (x.loc || []).slice(1).join(".") + ": " + t(x.msg))
        .join("\n") : t(String(detail || t("请求失败")));
}
/** Call the authenticated workspace API. The browser never talks directly to model endpoints or stores their API keys. */
async function api(path, body, method) {
    const options = {
        method: method || (body === undefined ? "GET" : "POST"),
        headers: {
            "X-Workbench": "1",
            "Accept-Language": WorkbenchI18n.language()
        }
    };
    if (state.saas?.tenant?.id) options.headers["X-Tenant-ID"] = state.saas.tenant.id;
    if (body !== undefined) {
        if (body instanceof FormData) options.body = body;
        else {
            options.body = JSON.stringify(body);
            options.headers["Content-Type"] = "application/json";
        }
    }
    let response;
    try {
        response = await fetch("/api" + path, options);
    } catch {
        throw Error(t("连接工作台失败，请检查服务是否运行"));
    }
    const isJSON = response.headers.get("content-type")?.includes("application/json");
    const data = isJSON ? await response.json() : null;
    if (!response.ok) {
        if (response.status === 401) showLogin();
        throw Error(errorText(data?.detail));
    }
    return data;
}

/** Catch rejected event-handler promises so network/validation failures reach the user instead of becoming silent errors. */
function guard(action) {
    return async event => {
        try {
            await action(event);
        } catch (err) {
            toast(err.message);
        }
    };
}

/** Show authentication without clearing a user's unsaved in-memory configuration. */
function showLogin() {
    $("login").classList.remove("hidden");
    $("shell").classList.add("hidden");
}

/** Mark the local draft as changed; persisted template versions remain immutable. */
function markDirty() {
    // Comparing editable values also clears the flag when an edit is undone.
    // Language changes are presentation-only and cannot dirty the configuration.
    state.dirty = JSON.stringify(templateBody()) !== state.baseline;
    $("dirty-label").textContent = state.dirty ? t("未保存的草稿") : "";
    // A rendered preview belongs to the previous draft and must not look current after an edit.
    state.promptPreview = null;
    renderPromptPreview();
}

/** Serialize only editable template fields, excluding IDs and version metadata. */
function templateBody() {
    const template = state.template;
    return {
        name: template.name,
        schema: template.schema,
        system_prompt: template.system_prompt,
        user_prompt: template.user_prompt
    };
}
/** Refresh server metadata without replacing the current editable draft. Initial load alone selects the latest template/source. */
async function loadBootstrap(initial = false) {
    const data = await api("/bootstrap");
    Object.assign(state, {
        templates: data.templates,
        providers: data.providers,
        files: data.files,
        datasets: data.datasets,
        dictionaries: data.dictionaries
    });
    state.saas = data.saas;
    const tsel = clear("template-select");
    state.templates.forEach(template => option(tsel, template.id, applicationLabel(template.name)));
    if (state.template) tsel.value = state.template.id;
    const psel = clear("provider-select");
    state.providers.forEach(p => option(psel, p.id, p.name));
    if (!state.providers.some(p => p.id === state.providerId)) {
        const preferred = state.providers.find(p => p.id === data.default_provider_id);
        state.providerId = preferred?.id || state.providers[0]?.id || "";
    }
    psel.value = state.providerId;
    updateConnectionState();
    renderFiles();
    renderProviders();
    renderDictionaries();
    if (!Object.keys(state.recognitionLabels).length) await loadRecognitionCapabilities();
    renderRecognitionRoutes();
    updateRecognitionMode();
    if (state.mode === "saas") await renderTenantControls();
    const ds = clear("dataset-select");
    state.datasets.forEach(d => option(ds, d.id, d.name + " · " + t("样本数：{count}", {
        count: d.count
    })));
    if (state.dataset && state.datasets.some(d => d.id === state.dataset.id)) ds.value = state
        .dataset.id;
    const sf = clear("sample-file");
    state.files.forEach(f => option(sf, f.id, f.name));
    if (initial) {
        await selectTemplate(state.templates[0]);
        if (state.files.length) {
            state.selected.add(state.files[0].id);
            previewFile(state.files[0].id);
            renderFiles();
        }
    }
}
/** Initialize the authenticated workspace after translations have loaded, or present the login screen. */
async function start() {
    const deployment = await api("/deployment");
    state.mode = deployment.mode;
    configureLogin(deployment);
    try {
        if (state.mode === "saas") {
            state.account = await api("/account");
            if (!state.account.tenants.length) {
                $("login").classList.remove("hidden");
                $("login-form").classList.add("hidden");
                $("no-team").classList.remove("hidden");
                return;
            }
        }
        $("no-team").classList.add("hidden");
        $("login-form").classList.remove("hidden");
        await loadBootstrap(true);
        $("login").classList.add("hidden");
        $("shell").classList.remove("hidden");
        const linkedJob = new URLSearchParams(location.search).get("job");
        if (linkedJob && /^[a-f0-9]{32}$/.test(linkedJob)) {
            try {
                renderResult(await api("/jobs/" + linkedJob));
            } catch (error) {
                toast(error.message);
            }
        }
    } catch (err) {
        if (!err.message.includes(t("令牌")) && !err.message.includes(t("请先登录账户"))) toast(err.message);
        showLogin();
    }
}

/** Disable model-dependent actions until a real saved connection has been selected. */
function updateConnectionState() {
    const connected = Boolean(state.providerId);
    $("connection-notice").classList.toggle("hidden", connected);
    $("run-extraction").disabled = !connected;
    $("run-evaluation").disabled = !connected;
    if (!connected) option($("provider-select"), "", t("尚未配置模型"));
}
/** Clone a saved snapshot before editing, preventing accidental mutation of history entries in shared state. */
async function selectTemplate(t) {
    if (!t) return;
    state.template = structuredClone(t);
    state.baseline = JSON.stringify(templateBody());
    state.dirty = false;
    state.promptPreview = null;
    renderPromptPreview();
    $("dirty-label").textContent = "";
    $("template-select").value = t.id;
    const versions = await api("/templates/" + t.id + "/versions");
    const vs = clear("version-select");
    versions.forEach(v => option(vs, String(v.version), "v" + v.version));
    vs.value = String(t.version);
    renderTemplate();
}

/** Render current draft values; this does not save, translate or rewrite business configuration. */
function renderTemplate() {
    const template = state.template;
    $("template-name").value = template.name;
    $("system-prompt").value = template.system_prompt;
    $("user-prompt").value = template.user_prompt;
    $("prompt-version").textContent = "v" + template.version;
    renderFields();
    if (state.mode === "saas") renderSaasAccess();
}
/** Append a new immutable version, refresh metadata, then select exactly the version that was saved. */
async function saveTemplate() {
    const template = await api("/templates/" + state.template.id + "/versions", templateBody());
    await loadBootstrap();
    await selectTemplate(template);
    toast(t("已保存不可变版本 v") + template.version);
    return template;
}
/** Checkpoint a modified draft before extraction/evaluation so every result has a reproducible configuration. */
async function ensureSaved() {
    if (state.dirty) await saveTemplate();
}
/** Switch visible sections and load their metadata. Navigation itself never submits document inference. */
async function navigate(view) {
    state.view = view;
    document.querySelectorAll(".view").forEach(e => e.classList.toggle("hidden", e.id !== "view-" +
        view));
    document.querySelectorAll("nav button").forEach(e => e.classList.toggle("active", e.dataset
        .view === view));
    const [title, heading, description] = names[view].map(value => t(value));
    $("page-title").textContent = title;
    $("view-title").textContent = heading;
    $("view-description").textContent = description;
    const administration = ["team", "platform", "applications"].includes(view);
    $("application-controls").classList.toggle("hidden", administration);
    $("new-template").classList.toggle("hidden", administration);
    $("connection-notice").classList.toggle("hidden", administration || Boolean(state.providerId));
    updateRecognitionMode();
    if (view === "applications") await renderApplications();
    if (view === "jobs") await renderJobs();
    if (view === "team") await renderTeam();
    if (view === "platform") await renderPlatform();
    if (view === "datasets") {
        await loadDataset();
        await renderRuns();
    }
}

/** Recursively edit object properties and object-array columns. Renames update required keys; type changes intentionally rebuild child structure. */
function renderFields() {
    const editor = clear("field-editor");

    function walk(parent, depth = 0) {
        Object.entries(parent.properties || {}).forEach(([key, field]) => {
            const row = node("div", undefined, "field-row");
            const keyWrap = node("div");
            keyWrap.style.paddingLeft = depth * 17 + "px";
            const input = node("input");
            input.value = key;
            input.setAttribute("aria-label", t("字段标识 ") + key);
            keyWrap.append(input);
            input.addEventListener("change", () => {
                const next = input.value.trim();
                if (!/^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(next) || next !== key && Object
                    .hasOwn(parent.properties, next)) {
                    input.value = key;
                    toast(t("字段标识需唯一，使用字母、数字和下划线"));
                    return;
                }
                parent.properties = Object.fromEntries(Object.entries(parent.properties)
                    .map(([k, v]) => [k === key ? next : k, v]));
                parent.required = (parent.required || []).map(k => k === key ? next : k);
                markDirty();
                renderFields();
            });
            const type = node("select");
            type.setAttribute("aria-label", t("字段类型 ") + key);
            [
                ["string", t("文本")],
                ["number", t("数字")],
                ["integer", t("整数")],
                ["boolean", t("布尔值")],
                ["object", t("字段分组")],
                ["array", t("表格 / 数组")],
                ["null", t("空值")]
            ].forEach(([v, n]) => option(type, v, t(n)));
            const kind = Array.isArray(field.type) ? field.type.find(t => t !== "null") ||
                "null" : field.type;
            type.value = kind;
            type.addEventListener("change", () => {
                const next = type.value;
                const desc = field.description || "";
                const fresh = {
                    type: next === "object" || next === "array" || next === "null" ?
                        next : [next, "null"],
                    description: desc
                };
                if (next === "object") Object.assign(fresh, {
                    properties: {},
                    required: [],
                    additionalProperties: false
                });
                if (next === "array") fresh.items = {
                    type: "object",
                    properties: {},
                    required: [],
                    additionalProperties: false
                };
                parent.properties[key] = fresh;
                markDirty();
                renderFields();
            });
            const desc = node("input");
            desc.value = field.description || "";
            desc.placeholder = t("例如：发票号码 / 费用名称");
            desc.setAttribute("aria-label", t("提取说明 ") + key);
            desc.addEventListener("input", () => {
                field.description = desc.value;
                markDirty();
            });
            const required = node("input");
            required.type = "checkbox";
            required.checked = (parent.required || []).includes(key);
            required.title = t("必填 ") + key;
            required.setAttribute("aria-label", t("必填 ") + key);
            required.addEventListener("change", () => {
                parent.required = required.checked ? [...new Set([...(parent.required || []), key])] : (parent.required || []).filter(k => k !== key);
                markDirty();
            });
            const actions = node("div", undefined, "field-actions");
            const child = kind === "object" ? field : kind === "array" && field.items?.type ===
                "object" ? field.items : null;
            if (child) actions.append(button("＋", () => addField(child), ""));
            actions.lastChild?.setAttribute("title", t("添加子字段"));
            if (!["object", "array"].includes(kind)) {
                const choices = button("⋯", () => openEnum(field, key), "");
                choices.title = t("配置可选值 ") + key;
                choices.setAttribute("aria-label", choices.title);
                actions.append(choices);
            }
            const remove = button("×", () => {
                delete parent.properties[key];
                parent.required = (parent.required || []).filter(k => k !== key);
                markDirty();
                renderFields();
            }, "");
            remove.title = t("删除 ") + key;
            actions.append(remove);
            row.append(keyWrap, type, desc, required, actions);
            editor.append(row);
            if (field.enum) editor.append(node("div", t("可选值：") + JSON.stringify(field.enum), "field-enum-preview"));
            if (child) {
                if (kind === "array") editor.append(node("div", key + t("[] · 每一行的列字段"),
                    "field-group-label"));
                walk(child, depth + 1);
            }
            if (kind === "array" && field.items?.type !== "object") editor.append(node("div",
                key + t("[] · 非对象数组请在 JSON Schema 中编辑"), "field-group-label"));
        });
    }
    walk(state.template.schema);
    if (!editor.children.length) editor.append(node("div", t("尚未配置字段，点击右上角添加。"), "empty-list"));
}

/** Allocate a unique local field key within its parent and mark the draft changed. */
function addField(parent) {
    parent.properties ||= {};
    let n = 1;
    while (Object.hasOwn(parent.properties, "field_" + n)) n++;
    parent.properties["field_" + n] = {
        type: ["string", "null"],
        description: ""
    };
    markDirty();
    renderFields();
}

/** Display library metadata only; rerendering never replaces an in-progress dictionary form. */
function renderDictionaries() {
    const list = clear("dictionary-list");
    if (!state.dictionaries.length) list.append(node("p", t("还没有字典。可以先手动配置字段可选值。"), "small"));
    state.dictionaries.forEach(dictionary => {
        const row = node("div", undefined, "dataset-row");
        row.append(node("strong", dictionary.name + " · v" + dictionary.version),
            node("p", dictionary.description, "small"),
            node("pre", json(dictionary.values), "dictionary-values"),
            button(t("编辑为新版本"), () => editDictionary(dictionary)));
        list.append(row);
    });
    $("dictionary-editor-title").textContent = state.dictionaryId ? t("编辑字典 · 保存为新版本") : t("新建字典");
}

/** Editing always appends a version; no saved enum or historical task is mutated. */
function editDictionary(dictionary = null) {
    state.dictionaryId = dictionary?.id || null;
    $("dictionary-name").value = dictionary?.name || "";
    $("dictionary-description").value = dictionary?.description || "";
    $("dictionary-values").value = dictionary ? json(dictionary.values) : "";
    renderDictionaries();
}

/** Open a literal enum editor. Library values are copied, never resolved as live references. */
function openEnum(field, key) {
    state.enumTarget = field;
    state.enumVersions = [];
    $("enum-title").textContent = t("字段可选值") + " · " + key;
    $("enum-values").value = field.enum ? json(field.enum) : "";
    const library = clear("enum-library");
    option(library, "", t("选择字典"));
    state.dictionaries.forEach(dictionary => option(library, dictionary.id, dictionary.name));
    clear("enum-version");
    $("enum-dialog").showModal();
}

/** Validate enum membership against primitive field types before assigning the draft. */
function matchesFieldType(value, field) {
    const types = Array.isArray(field.type) ? field.type : [field.type];
    if (value === null) return types.includes("null");
    if (typeof value === "number") return types.includes("number") || (types.includes("integer") && Number.isInteger(value));
    return types.includes(typeof value === "boolean" ? "boolean" : typeof value === "string" ? "string" : "unsupported");
}

/** Preview a starter; the selected interface language affects only this newly created configuration. */
function renderPreset() {
    const preset = state.presets.find(item => item.id === $("application-preset").value);
    if (!preset) return;
    $("application-name").value = preset.name;
    $("application-code").value = (preset.code || preset.id) + "-" + crypto.randomUUID().slice(0, 6);
    $("application-category").value = preset.category || "general";
    $("application-preset-hint").textContent = preset.hint;
}

/** Render independent selection and preview actions. Checking a file does not submit it to the model. */
function renderFiles() {
    const list = clear("file-list");
    state.files.forEach(file => {
        const row = node("div", undefined, "file-item" + (file.id === state.preview ?
            " selected" : ""));
        const check = node("input");
        check.type = "checkbox";
        check.checked = state.selected.has(file.id);
        check.setAttribute("aria-label", t("选择 ") + file.name);
        check.addEventListener("change", () => {
            if (check.checked) state.selected.add(file.id);
            else state.selected.delete(file.id);
        });
        row.append(check, button(file.name, () => previewFile(file.id), ""), node("span", t(
            "共 {count} 页", {
                count: file.pages.length
            }), "tag"));
        list.append(row);
    });
}

/** Display an authenticated page PNG; preserve the real filename instead of treating it as a translation key. */
function previewFile(id, page = 1) {
    const file = state.files.find(f => f.id === id);
    if (!file) return;
    state.preview = id;
    state.previewPage = page;
    delete $("preview-name").dataset.i18n;
    $("preview-name").textContent = file.name;
    const select = clear("preview-page");
    file.pages.forEach(p => option(select, String(p.page), t("第 {page} 页", {
        page: p.page
    })));
    select.value = String(page);
    $("source-preview").src = "/api/files/" + id + "/pages/" + page;
    $("preview-empty").classList.add("hidden");
    renderSourceOverlay();
    renderFiles();
    if (typeof syncReviewPage === "function") syncReviewPage(id, page);
}
/** Upload files sequentially and select each successful result. Parsing stays on the authenticated server. */
async function uploadFiles(files) {
    if (!files.length) return;
    for (const file of files) {
        if (file.size > 20 * 1024 * 1024) throw Error(t("单文件不能超过 20MB"));
        const form = new FormData();
        form.append("file", file);
        toast(t("正在解析：") + file.name);
        const saved = await api("/files", form);
        state.selected.add(saved.id);
        await loadBootstrap();
        previewFile(saved.id);
    }
    $("file-input").value = "";
    toast(t("文档已上传"));
}

/** Parse bounded one-based ranges such as 1-3,5; normalize duplicates before submitting the API request. */
function parsePages(text) {
    if (!text.trim()) return [];
    const pages = new Set();
    for (const part of text.split(",")) {
        const m = part.trim().match(/^(\d+)(?:-(\d+))?$/);
        if (!m) throw Error(t("页码格式示例：1-3,5"));
        const start = Number(m[1]),
            end = Number(m[2] || m[1]);
        if (start < 1 || end < start || end > 20) throw Error(t("页码范围必须在 1–20 内"));
        for (let n = start; n <= end; n++) pages.add(n);
    }
    return [...pages].sort((a, b) => a - b);
}

/** Translate status identifiers at render time so language changes also update live task states. */
function badge(status) {
    return node("span", t(statusNames[status] || status), "tag result-status-" + status);
}

/** Show inspectable JSON/raw output as plain text in a disclosure, preserving the original content. */
function details(title, value) {
    const e = node("details");
    e.append(node("summary", title), node("pre", typeof value === "string" ? value : json(value),
        "json"));
    return e;
}

/** Render scalar fields and object-array tables. User schema descriptions and model values are displayed verbatim. */
function renderData(container, data, schema) {
    const grid = node("div", undefined, "result-values");
    Object.entries(data).filter(([, v]) => v === null || typeof v !== "object").forEach(([key,
        value
    ]) => {
        const item = node("div", undefined, "result-value");
        item.append(node("span", schema?.properties?.[key]?.description || key), node("b",
            value === null ? t("未提取到") : String(value)));
        grid.append(item);
    });
    container.append(grid);
    Object.entries(data).filter(([, v]) => v !== null && typeof v === "object").forEach(([key,
        value
    ]) => {
        if (Array.isArray(value) && value.length && value.every(v => v && typeof v ===
                "object" && !Array.isArray(v))) {
            container.append(node("p", schema?.properties?.[key]?.description || key, "small"));
            const columns = [...new Set(value.flatMap(v => Object.keys(v)))];
            const table = node("table", undefined, "data-table");
            const head = node("tr");
            columns.forEach(c => head.append(node("th", schema?.properties?.[key]?.items
                ?.properties?.[c]?.description || c)));
            table.append(head);
            value.forEach(v => {
                const row = node("tr");
                columns.forEach(c => row.append(node("td", v[c] == null ? t("未提取到") :
                    typeof v[c] === "object" ? json(v[c]) : String(v[c]))));
                table.append(row);
            });
            const wrap = node("div", undefined, "table-scroll");
            wrap.append(table);
            container.append(wrap);
        } else container.append(details(schema?.properties?.[key]?.description || key, value));
    });
}

/** Display checkpointed unit results and actual input pages. No guessed field coordinates or confidence are generated. */
function renderResult(job) {
    if (typeof renderReviewResult === "function") return renderReviewResult(job);
    // Opening another job should show its own source, not the previous file preview.
    // Later polling/language redraws preserve the page the reviewer selected manually.
    if (state.job?.id !== job.id && job.payload.units.length) {
        state.highlightCell = null;
        state.recognitionTask = job.payload.task || "extract";
        const processing = job.payload.task ? job.payload : job.payload.processing || {};
        $("ocr-rotation").value = String(processing.rotation || 0);
        $("ocr-position-mode").value = processing.position_mode || "direct";
        $("ocr-deskew").value = processing.deskew ? "on" : "off";
        $("table-grid-assist").value = processing.grid_assist === false ? "off" : "on";
        const first = job.payload.units[0];
        previewFile(first.file_id, first.pages[0]);
    }
    state.job = job;
    $("result-state").replaceWith(Object.assign(badge(job.status), {
        id: "result-state"
    }));
    const target = clear("result-content");
    target.className = "";
    $("export-result").disabled = false;
    $("export-recognition-text").classList.toggle("hidden", !job.payload.task);
    if (job.payload.task) {
        renderRecognitionResult(job, target);
        return;
    }
    renderSourceOverlay();
    target.append(node("p", applicationLabel(job.payload.template.name) + " · v" + job.payload.template.version + " · " +
        job.payload.provider_name, "small"));
    if (job.status === "queued" || job.status === "running") target.append(node("p", t(
        "任务处理中，已完成单元将实时显示…"), "small"));
    if (job.error) target.append(node("p", t(job.error), "error"));
    job.results.forEach(result => {
        const unit = node("div", undefined, "result-unit");
        const title = node("div", undefined, "list-action");
        title.append(node("strong", result.filename, "grow"), badge(result.status));
        unit.append(title);
        const meta = node("div", undefined, "result-meta");
        meta.append(node("span", t("输入来源页：") + result.pages.join(", ")));
        result.pages.forEach(p => meta.append(button(t("查看第 {page} 页", {
            page: p
        }), () => previewFile(result.file_id, p))));
        if (result.duration_ms !== undefined) meta.append(node("span", result.duration_ms +
            " ms"));
        unit.append(meta);
        if (result.data) renderData(unit, result.data, job.payload.template.schema);
        if (result.field_locations) renderFieldLocations(unit, result.field_locations, result);
        if (result.preprocessing) unit.append(details(t("图像处理与坐标变换"), result.preprocessing));
        if (result.localization) unit.append(details(t("精细定位记录"), result.localization));
        if (result.error) unit.append(node("p", t(result.error), "error"));
        if (result.validation_errors?.length) unit.append(details(t("Schema 校验问题 · ") + result
            .validation_errors.length + t(" 项"), result.validation_errors));
        if (result.usage) unit.append(details(t("模型用量"), result.usage));
        unit.append(details(t("业务 JSON"), result.data));
        if (result.raw_text !== undefined) unit.append(details(t("原始模型输出"), result.raw_text));
        target.append(unit);
    });
}
/** Save a dirty draft, validate page selection and submit a real-model job. Polling retrieves persisted progress separately. */
async function runExtraction() {
    if (state.recognitionTask !== "extract") return runRecognition();
    if (state.busy) return;
    if (!state.providerId) throw Error(t("请先配置模型服务"));
    if (!state.selected.size) throw Error(t("请先选择至少一个文档"));
    const pages = parsePages($("page-range").value);
    await ensureSaved();
    state.busy = true;
    $("run-extraction").disabled = true;
    try {
        const task = await api("/jobs", {
            template_id: state.template.id,
            version: state.template.version,
            provider_id: state.providerId,
            file_ids: [...state.selected],
            mode: $("extraction-mode").value,
            rotation: Number($("ocr-rotation").value),
            deskew: $("ocr-deskew").value === "on",
            position_mode: $("ocr-position-mode").value,
            pages
        });
        renderResult(task);
    } finally {
        state.busy = false;
        $("run-extraction").disabled = false;
    }
}
/** Render lightweight job history with explicit cancel/rerun actions; full results are fetched only when opened. */
async function renderJobs() {
    const tasks = await api("/jobs");
    const list = clear("job-list");
    if (!tasks.length) list.append(node("div", t("还没有任务。上传文档开始第一次提取。"), "empty-list"));
    tasks.forEach(task => {
        const row = node("div", undefined, "dataset-row list-action");
        const title = node("div", undefined, "grow");
        title.append(node("strong", task.payload.files.map(f => f.name).join("、")), node("p",
            (task.payload.task ? t(task.payload.task === "ocr" ? "全文 OCR · 多模态识别" : task.payload.task === "tables" ? "通用表格识别" : "多证件 / 混合文档") : "v" + task.payload.template.version) + " · " + task.payload.provider_name + " · " + new Date(task.created * 1000).toLocaleString(
                WorkbenchI18n.language() === "en" ? "en-US" : "zh-CN")));
        row.append(title, badge(task.status), button(t("查看"), async () => {
            renderResult(await api("/jobs/" + task.id));
            await navigate("extract");
        }));
        if (["queued", "running"].includes(task.status)) row.append(button(t("取消"),
            async () => {
                await api("/jobs/" + task.id + "/cancel", {});
                await renderJobs();
            }));
        else row.append(button(t("重跑"), async () => {
            renderResult(await api("/jobs/" + task.id + "/retry", {}));
            await navigate("extract");
        }));
        list.append(row);
    });
}

/** Display non-secret connection metadata and persist an explicitly chosen workspace default. */
function renderProviders() {
    const list = clear("provider-list");
    state.providers.forEach(p => {
        const row = node("div", undefined, "provider-card");
        const profileLabel = p.request_profile === "openai" ?
            t("OpenAI 官方 API · 新版参数") : t("兼容服务 · Qwen / vLLM");
        row.append(node("h3", p.name), node("code", p.base_url), node("p", p.model + " · " + (p
            .has_api_key ? t("已保存密钥") : t("未配置密钥")), "small"), node("p", profileLabel, "small"), button(t("使用此连接"),
            async () => {
                await api("/providers/" + p.id + "/default", {});
                state.providerId = p.id;
                $("provider-select").value = p.id;
                updateConnectionState();
                toast(t("已选择 ") + p.name);
            }));
        list.append(row);
    });
}
/** Load actual file/ground-truth pairs; no samples means no accuracy metric. */
async function loadDataset() {
    const id = $("dataset-select").value;
    const list = clear("sample-list");
    if (!id) {
        $("dataset-count").textContent = t("暂无样本");
        return;
    }
    state.dataset = await api("/datasets/" + id);
    $("dataset-count").textContent = t("样本数：{count}", {
        count: state.dataset.samples.length
    });
    state.dataset.samples.forEach(s => {
        const file = state.files.find(f => f.id === s.file_id);
        const row = node("div", undefined, "dataset-row");
        row.append(node("strong", file?.name || s.file_id), details(t("查看标准答案"), s.gold));
        list.append(row);
    });
}
/** List immutable evaluations and track the two runs selected for comparison. */
async function renderRuns() {
    const runs = await api("/runs");
    const list = clear("run-history");
    if (!runs.length) list.append(node("div", t("尚无评测记录。添加文档和标准答案后运行评测。"), "empty-list"));
    for (const run of runs) {
        const row = node("div", undefined, "dataset-row list-action");
        const check = node("input");
        check.type = "checkbox";
        check.checked = state.compare.has(run.id);
        check.setAttribute("aria-label", t("对比评测 ") + run.id);
        check.addEventListener("change", () => {
            if (check.checked) state.compare.add(run.id);
            else state.compare.delete(run.id);
        });
        const title = node("div", undefined, "grow");
        title.append(node("strong", run.dataset_name + " · v" + run.version), node("p", t(
            "样本数：{count}", {
                count: run.samples.length
            }) + " · " + state.providers.find(p => p.id === run.provider_id)?.name));
        row.append(check, title, button(t("查看报告"), async () => {
            state.run = run.id;
            renderReport(await api("/runs/" + run.id));
        }));
        list.append(row);
    }
}

/** Render final/provisional metrics and per-field evidence, keeping missing values distinct from explicit null. */
function renderReport(report) {
    state.reports.set(report.id, report);
    const target = clear("evaluation-report");

    const metrics = node("div", undefined, "metric-grid");
    [
        [t("字段精确匹配率"), report.accuracy === null ? t("等待完成") : (report.accuracy * 100).toFixed(1) + "%"],
        [t("已匹配 / 标准字段"), report.matched + " / " + report.total],
        [t("样本 / 待完成"), report.samples.length + " / " + report.pending]
    ].forEach(([label, value]) => {
        const m = node("div", undefined, "metric");
        m.append(node("strong", value), node("span", label));
        metrics.append(m);
    });
    target.append(metrics);
    report.samples.forEach((sample, i) => {
        const d = node("details");
        d.open = report.samples.length === 1;
        d.append(node("summary", t("样本 ") + (i + 1) + " · " + t(statusNames[sample.status] ||
            sample.status) + " · " + sample.matched + "/" + sample.total));
        const table = node("table", undefined, "data-table");
        const h = node("tr");
        [t("字段路径"), t("标准答案"), t("实际输出"), t("匹配")].forEach(t => h.append(node("th", t)));
        table.append(h);
        sample.fields.forEach(f => {
            const r = node("tr");
            r.append(node("td", f.path), node("td", json(f.expected)), node("td", f
                .missing ? t("缺失") : json(f.actual)), node("td", f.match ? "✓" :
                "×", f.match ? "success" : "error"));
            table.append(r);
        });
        const wrap = node("div", undefined, "table-scroll");
        wrap.append(table);
        d.append(wrap);
        if (sample.extra_paths.length) d.append(node("p", t("额外输出字段：") + sample.extra_paths.join(
            ", "), "small"));
        target.append(d);
    });
    target.append(button(t("↓ 下载评测报告"), () => downloadJSON("evaluation-" + report.id + ".json",
        report)));
}

/** Export current data through a temporary browser Blob URL and revoke it after download. */
function downloadJSON(filename, data) {
    const url = URL.createObjectURL(new Blob([json(data)], {
        type: "application/json"
    }));
    const a = node("a");
    a.href = url;
    a.download = filename;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}
document.querySelectorAll("nav button").forEach(b => b.addEventListener("click", guard(() => navigate(b
    .dataset.view))));
$("login-form").addEventListener("submit", async e => {
    e.preventDefault();
    try {
        await api("/session", {
            ...(state.mode === "saas" ? {
                username: $("login-username").value,
                password: $("login-password").value
            } : {
                token: $("token").value
            })
        });
        $("token").value = "";
        $("login-password").value = "";
        $("login-error").textContent = "";
        // Rebuild all in-memory state after authentication. A different account
        // must never inherit a previous user's draft, team header or result pane.
        state.dirty = false;
        location.reload();
    } catch (err) {
        $("login-error").textContent = err.message;
    }
});
$("logout").addEventListener("click", guard(async () => {
    await api("/session", undefined, "DELETE");
    state.dirty = false;
    location.reload();
}));
$("template-select").addEventListener("change", guard(async () => {
    if (state.dirty && !confirm(t("当前草稿尚未保存，仍然切换应用？"))) {
        $("template-select").value = state.template.id;
        return;
    }
    await selectTemplate(state.templates.find(t => t.id === $("template-select").value));
}));
$("version-select").addEventListener("change", guard(async () => {
    const selected = $("version-select").value;
    if (state.dirty && !confirm(t("当前草稿尚未保存，仍然切换版本？"))) {
        $("version-select").value = String(state.template.version);
        return;
    }
    const versions = await api("/templates/" + state.template.id + "/versions");
    await selectTemplate(versions.find(v => v.version === Number(selected)));
}));
$("provider-select").addEventListener("change", () => {
    state.providerId = $("provider-select").value;
    updateConnectionState();
    if (state.mode === "saas") renderSaasAccess();
});
$("template-name").addEventListener("input", () => {
    state.template.name = $("template-name").value;
    markDirty();
});
$("system-prompt").addEventListener("input", () => {
    state.template.system_prompt = $("system-prompt").value;
    markDirty();
});
$("user-prompt").addEventListener("input", () => {
    state.template.user_prompt = $("user-prompt").value;
    markDirty();
});
$("save-template").addEventListener("click", guard(saveTemplate));
$("new-template").addEventListener("click", guard(async () => {
    if (state.dirty && !confirm(t("当前草稿尚未保存，仍然切换应用？"))) return;
    state.presets = [...await api("/application-presets"), ...(await api("/template-presets")).filter(preset => preset.id !== "identity-card")];
    const select = clear("application-preset");
    state.presets.forEach(preset => option(select, preset.id, preset.name));
    renderPreset();
    $("new-application-dialog").showModal();
}));
$("application-preset").addEventListener("change", renderPreset);
$("new-application-form").addEventListener("submit", guard(async event => {
    event.preventDefault();
    const preset = state.presets.find(item => item.id === $("application-preset").value);
    const created = await api("/applications", {
        code: $("application-code").value.trim(),
        category: $("application-category").value.trim(),
        tags: preset.tags || [],
        template: {
            name: $("application-name").value.trim(),
            schema: preset.schema,
            system_prompt: preset.system_prompt,
            user_prompt: preset.user_prompt
        }
    });
    const template = (await api("/applications/" + created.id)).template;
    await loadBootstrap();
    await selectTemplate(template);
    $("new-application-dialog").close();
    await navigate("schema");
}));
document.querySelectorAll("[data-close-dialog]").forEach(control => {
    control.addEventListener("click", () => $(control.dataset.closeDialog).close());
});
$("new-dictionary").addEventListener("click", () => editDictionary());
$("dictionary-form").addEventListener("submit", guard(async event => {
    event.preventDefault();
    const values = JSON.parse($("dictionary-values").value);
    if (!Array.isArray(values)) throw Error(t("可选值必须是 JSON 数组"));
    const dictionary = await api(state.dictionaryId ? "/dictionaries/" + state.dictionaryId + "/versions" : "/dictionaries", {
        name: $("dictionary-name").value.trim(),
        description: $("dictionary-description").value,
        values
    });
    state.dictionaryId = dictionary.id;
    await loadBootstrap();
    toast(t("已保存字典版本 v") + dictionary.version);
}));
$("enum-library").addEventListener("change", guard(async () => {
    const id = $("enum-library").value;
    state.enumVersions = id ? await api("/dictionaries/" + id + "/versions") : [];
    const versions = clear("enum-version");
    state.enumVersions.forEach(dictionary => option(versions, String(dictionary.version), "v" + dictionary.version));
}));
$("copy-dictionary").addEventListener("click", guard(() => {
    const version = state.enumVersions.find(dictionary => dictionary.version === Number($("enum-version").value));
    if (!version) throw Error(t("请先选择字典版本"));
    $("enum-values").value = json(version.values);
}));
$("enum-form").addEventListener("submit", guard(async event => {
    event.preventDefault();
    const raw = $("enum-values").value.trim();
    if (raw) {
        const values = JSON.parse(raw);
        if (!Array.isArray(values) || !values.length) throw Error(t("可选值必须是非空 JSON 数组"));
        if (!values.every(value => matchesFieldType(value, state.enumTarget))) throw Error(t("可选值与字段类型不匹配"));
        // The backend remains the authority for size/schema validation; the actual field
        // receives a copy only after this complete draft is accepted by the preview route.
        const draft = structuredClone(templateBody());
        const validate = {
            ...state.enumTarget,
            enum: values
        };
        await api("/prompt-preview", {
            ...draft,
            schema: {
                type: "object",
                properties: {
                    value: validate
                }
            }
        });
        state.enumTarget.enum = values;
    } else delete state.enumTarget.enum;
    markDirty();
    renderFields();
    $("enum-dialog").close();
}));
$("add-field").addEventListener("click", () => addField(state.template.schema));
$("import-schema").addEventListener("click", () => {
    $("schema-json").value = json(state.template.schema);
    $("json-dialog").showModal();
});
$("apply-schema").addEventListener("click", guard(async () => {
    const schema = JSON.parse($("schema-json").value);
    await api("/prompt-preview", {
        ...templateBody(),
        schema
    });
    state.template.schema = schema;
    markDirty();
    renderFields();
    $("json-dialog").close();
}));
$("export-schema").addEventListener("click", () => downloadJSON("schema.json", state.template.schema));
$("preview-prompt").addEventListener("click", guard(async () => {
    state.promptPreview = await api("/prompt-preview", templateBody());
    renderPromptPreview();
}));
document.querySelectorAll("[data-config-view]").forEach(control => {
    control.addEventListener("click", guard(() => navigate(control.dataset.configView)));
});
$("file-input").addEventListener("change", guard(e => uploadFiles([...e.target.files])));
["dragover", "dragleave", "drop"].forEach(event => $("drop-zone").addEventListener(event, guard(
    async e => {
        e.preventDefault();
        $("drop-zone").classList.toggle("dragover", event === "dragover");
        if (event === "drop") await uploadFiles([...e.dataTransfer.files]);
    })));
$("preview-page").addEventListener("change", () => previewFile(state.preview, Number($("preview-page")
    .value)));
$("run-extraction").addEventListener("click", guard(runExtraction));
$("export-result").addEventListener("click", () => {
    if (state.job) downloadJSON("extraction-" + state.job.id + ".json", state.job);
});
$("refresh-jobs").addEventListener("click", guard(renderJobs));
$("provider-form").addEventListener("submit", guard(async e => {
    e.preventDefault();
    const p = await api("/providers", {
        name: $("model-name").value,
        kind: "openai",
        base_url: $("model-url").value,
        model: $("model-id").value,
        api_key: $("model-key").value,
        request_profile: $("model-request-profile").value,
        max_tokens: Number($("model-tokens").value),
        timeout_seconds: Number($("model-timeout").value)
    });
    $("model-key").value = "";
    state.providerId = p.id;
    await loadBootstrap();
    toast(t("连接已保存，提交文档后可验证真实识别"));
}));
$("dataset-select").addEventListener("change", guard(loadDataset));
$("new-dataset").addEventListener("click", guard(async () => {
    const name = prompt(t("数据集名称"));
    if (!name?.trim()) return;
    const d = await api("/datasets", {
        name: name.trim()
    });
    await loadBootstrap();
    $("dataset-select").value = d.id;
    await loadDataset();
}));
$("gold-file").addEventListener("change", guard(async e => {
    const file = e.target.files[0];
    if (file) {
        if (file.size > 200000) throw Error(t("标准答案文件不能超过 200KB"));
        $("gold-json").value = json(JSON.parse(await file.text()));
    }
}));
$("add-sample").addEventListener("click", guard(async () => {
    if (!$("dataset-select").value) throw Error(t("先新建数据集"));
    await api("/datasets/" + $("dataset-select").value + "/samples", {
        file_id: $("sample-file").value,
        gold: JSON.parse($("gold-json").value)
    });
    await loadBootstrap();
    await loadDataset();
    toast(t("已添加标准答案样本"));
}));
$("run-evaluation").addEventListener("click", guard(async () => {
    const id = $("dataset-select").value;
    if (!id) throw Error(t("请先选择数据集"));
    if (!state.providerId) throw Error(t("请先配置模型服务"));
    await ensureSaved();
    const run = await api("/datasets/" + id + "/runs", {
        template_id: state.template.id,
        version: state.template.version,
        provider_id: state.providerId
    });
    state.run = run.id;
    await renderRuns();
    renderReport(await api("/runs/" + run.id));
}));
$("compare-runs").addEventListener("click", guard(async () => {
    if (state.compare.size !== 2) throw Error(t("请选择两条评测记录"));
    state.run = null;
    const reports = await Promise.all([...state.compare].map(id => api("/runs/" + id)));
    const target = clear("evaluation-report");
    const table = node("table", undefined, "data-table");
    const head = node("tr");
    [t("评测集"), t("模板版本"), t("字段精确匹配率"), t("匹配 / 总字段"), t("运行模式")].forEach(t => head
        .append(node("th", t)));
    table.append(head);
    reports.forEach(r => {
        const row = node("tr");
        [r.dataset_name, "v" + r.version, r.accuracy === null ? t("运行中") : (r
                .accuracy * 100).toFixed(1) + "%", r.matched + " / " + r.total,
            t("真实模型")
        ].forEach(t => row.append(node("td", t)));
        table.append(row);
    });
    target.append(table, node("p", t("只有相同标准答案快照的评测才适合直接比较；同名数据集在样本变化后，分数可能不可比。"),
        "small"));
}));
window.addEventListener("beforeunload", e => {
    if (state.dirty) {
        e.preventDefault();
        e.returnValue = "";
    }
});
setInterval(async () => {
    if ($("shell").classList.contains("hidden")) return;
    try {
        if (state.job && ["queued", "running"].includes(state.job.status)) renderResult(
            await api("/jobs/" + state.job.id));
        if (state.run) {
            const report = await api("/runs/" + state.run);
            if (state.view === "datasets") renderReport(report);
            if (report.status === "completed") state.run = null;
        }
    } catch (err) {
        toast(err.message);
    }
}, 1200);


$("discover-models").addEventListener("click", guard(async () => {
    const control = $("discover-models");
    control.disabled = true;
    $("connection-status").textContent = t("正在检测连接…");
    try {
        const result = await api("/providers/discover", {
            name: $("model-name").value || "Connection",
            base_url: $("model-url").value,
            model: $("model-id").value,
            api_key: $("model-key").value
        });
        const models = clear("available-models");
        result.models.forEach(model => option(models, model.id, model.id));
        // A cloud account may list text/audio/embedding models too. Only a single
        // advertised model is unambiguous; otherwise let the user choose its ID.
        if (!$("model-id").value && result.models.length === 1) $("model-id").value = result.models[0].id;
        $("connection-status").textContent = t("连接成功，发现模型：") + result.models.map(m => m
            .id).join(", ");
    } catch (error) {
        $("connection-status").textContent = error.message;
        throw error;
    } finally {
        control.disabled = false;
    }
}));

/**
 * Redraw presentation after a language switch, preserving the current draft, source
 * page, sample selection and model configuration. No inference request is made here.
 */
window.addEventListener("workbench:language-changed", guard(async () => {
    if (!state.template) return;
    const dirty = state.dirty;
    const sampleFile = $("sample-file").value;
    const page = Number($("preview-page").value || 1);
    await loadBootstrap();
    renderTemplate();
    renderPromptPreview();
    if (state.preview) previewFile(state.preview, page);
    if (sampleFile) $("sample-file").value = sampleFile;
    if (state.job) renderResult(state.job);
    await navigate(state.view);
    if (state.view === "datasets" && state.reports.size) {
        renderReport([...state.reports.values()].at(-1));
    }
    // The draft is unchanged by translation; preserve both its dirty flag and rendered preview.
    $("dirty-label").textContent = dirty ? t("未保存的草稿") : "";
}));
