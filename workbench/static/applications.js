/** Application directory and release management.
 * The catalog never owns a second copy of a prompt: editing opens the existing
 * immutable template editor. Published snapshots are selected explicitly.
 * Only plain DOM text is rendered, including diffs, notes and API examples.
 */
"use strict";
const catalog = {
    offset: 0,
    total: 0,
    current: null,
    limit: 25
};
const catalogStates = {
    draft: "草稿",
    published: "已发布",
    disabled: "已停用"
};
const categoryLabels = {
    general: "通用",
    credentials: "证件"
};

async function renderApplications() {
    const category = $("catalog-category").value;
    const query = new URLSearchParams({
        q: $("catalog-search").value,
        category,
        state: $("catalog-state").value,
        offset: catalog.offset,
        limit: catalog.limit
    });
    const data = await api("/applications?" + query);
    catalog.total = data.total;
    catalog.pageIds = data.items.map(item => item.id);
    const categories = clear("catalog-category");
    option(categories, "", t("全部分类"));
    data.categories.forEach(value => option(categories, value, t(categoryLabels[value] || value)));
    categories.value = category;
    const list = clear("catalog-list");
    for (const app of data.items) {
        const row = node("article", undefined, "catalog-row");
        const title = node("div", undefined, "grow");
        title.append(node("strong", app.source_preset ? t(app.name) : app.name), node("p", app.code + " · " + t(categoryLabels[app.category] || app.category) + " · v" + app.latest_version, "small"));
        if (app.description) title.append(node("p", app.description, "small"));
        const tags = node("div", undefined, "catalog-tags");
        app.tags.forEach(value => tags.append(node("span", t(state.recognitionLabels[value] || value), "tag")));
        title.append(tags);
        row.append(title, node("span", t(catalogStates[app.state]), "tag"));
        if (app.has_changes) row.append(node("span", t("有未发布修改"), "tag"));
        row.append(button(t("管理"), () => openCatalogApplication(app.id, true)));
        list.append(row);
    }
    if (!data.items.length) list.append(node("p", t("没有匹配的应用"), "small"));
    $("catalog-count").textContent = t("共 {count} 个应用", {
        count: data.total
    }) + " · " + (catalog.offset + 1) + "–" + Math.min(catalog.offset + catalog.limit, data.total);
    $("catalog-prev").disabled = catalog.offset === 0;
    $("catalog-next").disabled = catalog.offset + catalog.limit >= data.total;
    $("install-credentials").disabled = state.saas?.role === "viewer";
    $("import-applications").disabled = state.saas?.role === "viewer";
    if (catalog.current) await openCatalogApplication(catalog.current.id);
}

/** Selecting an application does not discard an unsaved prompt or call a model. */
async function openCatalogApplication(id, focus = false) {
    if (catalog.current?.id !== id) $("catalog-diff-output").classList.add("hidden");
    const detail = await api("/applications/" + id);
    catalog.current = detail;
    $("application-detail").classList.remove("hidden");
    $("catalog-detail-name").textContent = detail.source_preset ? t(detail.name) : detail.name;
    $("catalog-detail-state").textContent = t(catalogStates[detail.state]);
    $("catalog-detail-summary").textContent = t("草稿版本") + " v" + detail.latest_version + " · " + t("发布配置版本") + " " + (detail.published ? "v" + detail.published.version : t("尚未发布"));
    for (const [field, value] of Object.entries({
            code: detail.code,
            "edit-category": detail.category,
            tags: detail.tags.join(", "),
            owner: detail.owner,
            description: detail.description
        })) $("catalog-" + field).value = value;
    const actions = clear("application-actions");
    for (const [label, view] of [
            ["配置字段", "schema"],
            ["编辑提示词", "prompts"],
            ["上传样本调试", "extract"],
            ["数据集评测", "datasets"]
        ])
        actions.append(button(t(label), () => openApplicationEditor(detail, view), "secondary"));
    actions.append(button(t("复制应用"), () => {
        if (state.dirty && !confirm(t("当前草稿尚未保存，仍然切换应用？"))) return;
        $("copy-application-name").value = detail.name;
        $("copy-application-code").value = detail.code.slice(0, 59) + "-copy";
        $("copy-application-dialog").showModal();
    }));
    actions.append(button(t("导出此应用"), async () => downloadJSON(detail.code + ".json", await api("/applications/" + detail.id + "/export"))));
    const admin = !state.saas || ["owner", "admin"].includes(state.saas.role);
    const editable = state.saas?.role !== "viewer";
    const toggle = button(t(detail.disabled ? "启用应用" : "停用应用"), async () => {
        await api("/applications/" + detail.id + "/state", {
            disabled: !detail.disabled
        });
        await renderApplications();
    });
    toggle.disabled = !admin;
    actions.append(toggle);
    actions.querySelectorAll("button").forEach(e => {
        if (!editable && e.textContent === t("复制应用")) e.disabled = true;
    });
    $("application-metadata-form").querySelectorAll("input:not([readonly]),textarea,button").forEach(e => e.disabled = !editable);
    const versions = await api("/templates/" + id + "/versions");
    for (const key of ["diff-before", "diff-after", "publish-version"]) {
        const select = clear("catalog-" + key);
        versions.forEach(version => option(select, String(version.version), "v" + version.version));
    }
    $("catalog-diff-before").value = String(versions.length > 1 ? versions[1].version : versions[0].version);
    const providers = clear("catalog-publish-provider");
    state.providers.forEach(provider => option(providers, provider.id, provider.name));
    providers.value = detail.published?.provider_id || state.providerId;
    $("catalog-publish-mode").value = detail.published?.mode || "document";
    $("catalog-publish-task").value = detail.published?.task || "extract";
    $("catalog-publish-position").value = detail.published?.processing?.position_mode || "direct";
    $("catalog-publish-deskew").value = detail.published?.processing?.deskew ? "on" : "off";
    $("catalog-publish-grid").value = detail.published?.processing?.grid_assist === false ? "off" : "on";
    $("application-publish-form").querySelectorAll("input,select,button").forEach(e => e.disabled = !admin || !state.providers.length);
    const releases = clear("catalog-release-list");
    for (const release of detail.releases) {
        const row = node("div", undefined, "catalog-release");
        row.append(node("span", "v" + release.version + " · " + new Date(release.created * 1000).toLocaleString(WorkbenchI18n.language() === "en" ? "en-US" : "zh-CN") + " · " + release.notes));
        const active = release.id === detail.active_release;
        const control = button(t(active ? "当前发布" : "切换至此发布"), async () => {
            await api("/applications/" + id + "/activate", {
                release_id: release.id
            });
            await renderApplications();
        });
        control.disabled = active || !admin;
        row.append(control);
        releases.append(row);
    }
    $("catalog-api-example").textContent = "POST /v1/apps/" + detail.code + "/recognitions\nAuthorization: Bearer <API_KEY>\nContent-Type: application/json\n\n" + JSON.stringify({
        file_ids: ["<UPLOADED_FILE_ID>"]
    }, null, 2);
    $("catalog-api-key").classList.toggle("hidden", !state.saas);
    $("catalog-api-key").disabled = !editable;
    if (focus) $("application-detail").scrollIntoView({
        block: "start",
        behavior: "smooth"
    });
}

async function openApplicationEditor(detail, view) {
    if (state.dirty && !confirm(t("当前草稿尚未保存，仍然切换应用？"))) return;
    await selectTemplate(detail.template);
    state.recognitionTask = "extract";
    await navigate(view);
}

$("catalog-search-form").addEventListener("submit", guard(async event => {
    event.preventDefault();
    catalog.offset = 0;
    await renderApplications();
}));
$("catalog-prev").addEventListener("click", guard(async () => {
    catalog.offset = Math.max(0, catalog.offset - catalog.limit);
    await renderApplications();
}));
$("catalog-next").addEventListener("click", guard(async () => {
    catalog.offset += catalog.limit;
    await renderApplications();
}));
$("install-credentials").addEventListener("click", guard(async () => {
    const result = await api("/applications/install-presets", {});
    toast(t("已安装 {count} 个证件配置，已有配置保持不变", {
        count: result.created.length
    }));
    await loadBootstrap();
    catalog.offset = 0;
    await renderApplications();
}));
$("application-metadata-form").addEventListener("submit", guard(async event => {
    event.preventDefault();
    await api("/applications/" + catalog.current.id + "/metadata", {
        code: $("catalog-code").value,
        category: $("catalog-edit-category").value,
        tags: $("catalog-tags").value.split(/[,，]/).map(s => s.trim()).filter(Boolean),
        owner: $("catalog-owner").value,
        description: $("catalog-description").value
    });
    await renderApplications();
    toast(t("目录信息已保存"));
}));
$("application-publish-form").addEventListener("submit", guard(async event => {
    event.preventDefault();
    await api("/applications/" + catalog.current.id + "/publish", {
        version: Number($("catalog-publish-version").value),
        provider_id: $("catalog-publish-provider").value,
        mode: $("catalog-publish-mode").value,
        task: $("catalog-publish-task").value,
        deskew: $("catalog-publish-deskew").value === "on",
        grid_assist: $("catalog-publish-grid").value !== "off",
        position_mode: $("catalog-publish-position").value,
        notes: $("catalog-publish-notes").value
    });
    $("catalog-publish-notes").value = "";
    await renderApplications();
    toast(t("应用版本已发布"));
}));
$("catalog-diff").addEventListener("click", guard(async () => {
    const result = await api("/applications/" + catalog.current.id + "/diff?before=" + $("catalog-diff-before").value + "&after=" + $("catalog-diff-after").value);
    $("catalog-diff-output").classList.remove("hidden");
    $("catalog-diff-output").textContent = Object.entries(result.diff).map(([field, diff]) => field + "\n" + (diff || t("无变化"))).join("\n\n");
}));
$("export-applications").addEventListener("click", guard(async () => {
    if (!catalog.pageIds?.length) return;
    const bundle = await api("/applications/export?ids=" + catalog.pageIds.join(","));
    if (new Blob([JSON.stringify(bundle, null, 2)]).size > 250 * 1024) throw Error(t("当前配置包过大，请使用单应用导出"));
    downloadJSON("applications.json", bundle);
}));
$("import-applications").addEventListener("change", guard(async event => {
    const file = event.target.files[0];
    if (!file) return;
    if (file.size > 250 * 1024) throw Error(t("配置包最多 250KB"));
    const result = await api("/applications/import", JSON.parse(await file.text()));
    event.target.value = "";
    await loadBootstrap();
    await renderApplications();
    toast(t("已导入 {count} 个应用", {
        count: result.ids.length
    }));
}));
$("catalog-api-key").addEventListener("click", guard(async () => {
    const result = await api("/api-keys", {
        name: catalog.current.code,
        role: "member",
        days: 30,
        application_ids: [catalog.current.id]
    });
    showSecret(result.token);
}));
$("copy-application-form").addEventListener("submit", guard(async event => {
    event.preventDefault();
    const saved = await api("/applications/" + catalog.current.id + "/copy", {
        code: $("copy-application-code").value,
        name: $("copy-application-name").value
    });
    $("copy-application-dialog").close();
    await loadBootstrap();
    await renderApplications();
    await openCatalogApplication(saved.id);
}));
