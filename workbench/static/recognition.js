/** Multimodal OCR UI: no separate OCR engine, no fabricated score or bounding box.
 * All text uses textContent. SVG rectangles display only persisted model positions.
 * Source previews and generated crops stay behind the same tenant authorization.
 */
"use strict";
const recognitionHints = {
    tables: "识别页面上的字段与表格，保留多级表头、表体和合并单元格。可下载 CSV 或保留合并结构的 HTML。",
    ocr: "无需配置字段，使用所选多模态模型逐页转写全文。位置由模型返回，未提供的位置留空，不生成置信度。",
    documents: "每页先定位文档，再逐实例裁剪识别。同类多个实例分别保存；每次定位与识别都会调用所选模型。",
    extract: "按所选应用的固定字段与提示词提取。需要先配置业务结构。"
};

/** Show application fields only when the selected operation actually needs them. */
function updateRecognitionMode() {
    if (typeof updateReviewChrome === "function") updateReviewChrome();
    const extracting = state.view === "extract";
    const custom = state.recognitionTask === "extract";
    $("recognition-toolbar").classList.toggle("hidden", !extracting);
    $("recognition-task").value = state.recognitionTask;
    $("recognition-hint").textContent = t(recognitionHints[state.recognitionTask]);
    const labels = $("application-controls").querySelectorAll("label");
    labels.forEach((label, i) => label.classList.toggle("hidden", extracting && !custom && i < 2));
    $("save-template").classList.toggle("hidden", extracting && !custom);
    $("new-template").classList.toggle("hidden", ["team", "platform"].includes(state.view) || (extracting && !custom));
    $("recognition-options").classList.remove("hidden");
    $("ocr-output-format").parentElement.classList.toggle("hidden", custom || state.recognitionTask === "tables");
    $("table-grid-options").classList.toggle("hidden", state.recognitionTask !== "tables");
    $("document-routing").classList.toggle("hidden", state.recognitionTask !== "documents");
    $("extraction-mode").parentElement.classList.toggle("hidden", !custom);
    // The two explanatory paragraphs and configuration shortcuts apply only to
    // structured extraction; recognition has its own explicit request-cost hint.
    $("view-extract").querySelectorAll(".run-panel > p,.run-panel > .button-group").forEach(el => el.classList.toggle("hidden", !custom));
    if (state.view === "applications") {
        $("application-controls").classList.add("hidden");
        $("new-template").classList.remove("hidden");
    }
    if (state.saas) renderSaasAccess();
}

/** Freeze current template versions when the server admits the recognition job. */
function renderRecognitionRoutes() {
    const list = clear("document-routes");
    Object.entries(state.recognitionLabels).forEach(([label, display]) => {
        const field = node("label", undefined, "route-row");
        field.append(node("span", t(display)));
        const select = node("select");
        select.setAttribute("aria-label", t("类别路由 ") + t(display));
        option(select, "", t("逐实例全文识别"));
        state.templates.forEach(template => option(select, template.id, applicationLabel(template.name) + " · v" + template.version));
        select.value = state.recognitionRoutes[label] || "";
        select.addEventListener("change", () => state.recognitionRoutes[label] = select.value);
        field.append(select);
        list.append(field);
    });
}

async function loadRecognitionCapabilities() {
    const capabilities = await api("/recognition/capabilities");
    state.recognitionLabels = capabilities.labels;
    renderRecognitionRoutes();
}

/** Submit the same durable, quota-bound queue used by extraction. No draft auto-save. */
async function runRecognition() {
    if (state.busy) return;
    if (!state.providerId) throw Error(t("请先配置模型服务"));
    if (!state.selected.size) throw Error(t("请先选择至少一个文档"));
    const routes = {};
    if (state.recognitionTask === "documents")
        for (const [label, id] of Object.entries(state.recognitionRoutes)) {
            const template = state.templates.find(item => item.id === id);
            if (template) routes[label] = {
                template_id: id,
                version: template.version
            };
        }
    state.busy = true;
    $("run-extraction").disabled = true;
    try {
        const job = await api("/recognition/jobs", {
            task: state.recognitionTask,
            provider_id: state.providerId,
            file_ids: [...state.selected],
            pages: parsePages($("page-range").value),
            output_format: $("ocr-output-format").value,
            rotation: Number($("ocr-rotation").value),
            deskew: $("ocr-deskew").value === "on",
            grid_assist: $("table-grid-assist").value !== "off",
            position_mode: $("ocr-position-mode").value,
            routes
        });
        renderResult(job);
    } finally {
        state.busy = false;
        $("run-extraction").disabled = !state.providerId || state.saas?.role === "viewer";
    }
}

/** Render local text and coordinates; Markdown output is displayed as inert source. */
function renderOcrText(container, data) {
    if (!data) return;
    container.append(node("pre", data.text, "ocr-text"));
    if (!data.text) container.append(node("p", t("该图像未返回可见文字。"), "small"));
    if (data.preprocessing) container.append(details(t("图像处理与坐标变换"), data.preprocessing));
    if (data.localization) container.append(details(t("精细定位记录"), data.localization));
    if (data.blocks?.length) container.append(details(t("文字块与来源坐标"), data.blocks));
}

function renderRecognitionResult(job, target) {
    target.append(node("p", t(job.payload.task === "ocr" ? "全文 OCR · 多模态识别" : job.payload.task === "tables" ? "通用表格识别" : "多证件 / 混合文档") + " · " + job.payload.provider_name, "small"));
    target.append(node("p", t("定位与文字位置由多模态模型返回，尚未经人工核验；没有校准置信度。"), "notice"));
    if (["queued", "running"].includes(job.status)) target.append(node("p", t("任务处理中，已完成实例将实时显示…"), "small"));
    if (job.error) target.append(node("p", t(job.error), "error"));
    for (const [unitIndex, result] of job.results.entries()) {
        const unit = node("section", undefined, "result-unit");
        unit.append(node("strong", result.filename + " · " + t("第 {page} 页", {
            page: result.pages[0]
        })), badge(result.status));
        unit.append(button(t("查看来源页"), () => previewFile(result.file_id, result.pages[0])));
        if (job.payload.task === "tables") renderTables(unit, result.data, job, unitIndex, result);
        else if (job.payload.task === "ocr") renderOcrText(unit, result.data);
        else {
            if (result.data?.empty_detection) unit.append(node("p", t("模型未定位到文档；请检查输入或改用全文识别。"), "small"));
            for (const instance of result.instances || []) {
                const card = node("article", undefined, "document-instance");
                card.append(node("strong", t(state.recognitionLabels[instance.label] || instance.label) + " · " + instance.id.slice(0, 8)), badge(instance.status));
                const crop = node("img", undefined, "document-crop");
                crop.src = "/api/jobs/" + job.id + "/regions/" + instance.id + "/crop";
                crop.alt = t("文档实例裁剪图");
                card.append(crop);
                card.append(button(t("定位此实例"), () => {
                    state.highlightRegion = instance.id;
                    previewFile(result.file_id, result.pages[0]);
                }));
                if (instance.template) card.append(node("p", instance.template.name + " · v" + instance.template.version, "small"));
                if (instance.template && instance.data) renderData(card, instance.data, job.payload.routes[instance.label]?.schema);
                else renderOcrText(card, instance.data);
                if (instance.field_locations) renderFieldLocations(card, instance.field_locations, result);
                if (instance.error) card.append(node("p", t(instance.error), "error"));
                if (instance.validation_errors?.length) card.append(details(t("Schema 校验问题"), instance.validation_errors));
                if (instance.raw_text !== undefined) card.append(details(t("原始模型输出"), instance.raw_text));
                unit.append(card);
            }
            if (result.detection) unit.append(details(t("原始定位响应"), result.detection));
        }
        if (result.error) unit.append(node("p", t(result.error), "error"));
        if (result.raw_text !== undefined) unit.append(details(t("原始模型输出"), result.raw_text));
        if (result.usage) unit.append(details(t("模型用量"), result.usage));
        target.append(unit);
    }
    renderSourceOverlay();
}

/** Overlay in preview coordinates; the source image and SVG share one aspect ratio. */
function renderSourceOverlay() {
    if (typeof drawReviewOverlay === "function") return drawReviewOverlay();
    const overlay = clear("source-overlay");
    const file = state.files.find(item => item.id === state.preview);
    const page = file?.pages.find(item => item.page === state.previewPage);
    if (!page || !state.job) return;
    overlay.setAttribute("viewBox", `0 0 ${page.width} ${page.height}`);
    const result = state.job.results.find(unit => unit.file_id === state.preview && unit.pages.includes(state.previewPage));
    const data = result?.data;
    const tableItems = [...(data?.fields || []), ...(data?.tables || []).flatMap(table => table.cells)];
    const selectedFields = [...(result?.field_locations || []), ...(result?.instances || []).flatMap(instance => instance.field_locations || [])].filter(item => (item.overlay_id || item.id) === state.highlightCell && (!item.page || item.page === state.previewPage));
    const boxes = selectedFields.length ? selectedFields : result?.instances || (state.job.payload.task === "tables" ? tableItems.filter(item => item.id === state.highlightCell) : data?.blocks || []);
    for (const item of boxes) {
        if (!item.box) continue;
        if (item.polygon?.length === 4) {
            const shape = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
            shape.setAttribute("points", item.polygon.map(point => point.join(",")).join(" "));
            shape.setAttribute("fill", "none");
            shape.setAttribute("stroke", "#6855df");
            shape.setAttribute("stroke-width", "1.5");
            overlay.append(shape);
            continue;
        }
        const [x1, y1, x2, y2] = item.box;
        const rectangle = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        for (const [name, value] of Object.entries({
                x: x1,
                y: y1,
                width: x2 - x1,
                height: y2 - y1
            })) rectangle.setAttribute(name, String(value));
        rectangle.setAttribute("class", item.id === state.highlightRegion ? "highlight" : "");
        const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
        title.textContent = item.text || t(state.recognitionLabels[item.label] || item.label);
        rectangle.append(title);
        overlay.append(rectangle);
    }
}
$("recognition-task").addEventListener("change", guard(async () => {
    state.recognitionTask = $("recognition-task").value;
    if (state.recognitionTask === "documents" && !Object.keys(state.recognitionLabels).length) await loadRecognitionCapabilities();
    updateRecognitionMode();
}));
$("export-recognition-text").addEventListener("click", () => {
    if (!state.job || ["queued", "running"].includes(state.job.status)) return toast(t("请等待识别任务结束后导出"));
    const format = state.job.payload.output_format === "markdown" ? "md" : "txt";
    const link = node("a");
    link.href = "/api/jobs/" + state.job.id + "/export?format=" + format;
    link.download = state.job.id + "." + format;
    link.click();
});

/** Render real cell spans with DOM nodes; model text is never interpreted as HTML. */
function renderTables(container, data, job, unitIndex, result) {
    if (!data) return;
    const locate = item => {
        state.highlightCell = item.id;
        previewFile(result.file_id, result.pages[0]);
        renderSourceOverlay();
    };
    if (data.fields?.length) {
        const fields = node("div", undefined, "data-grid");
        for (const field of data.fields) {
            const card = node("div", undefined, "data-cell");
            card.append(node("small", field.label), button(field.text || "—", () => locate(field)));
            fields.append(card);
        }
        container.append(fields);
    }
    for (const [tableIndex, table] of data.tables.entries()) {
        const card = node("article", undefined, "document-instance");
        card.append(node("strong", table.title || t("表格 {number}", {
            number: tableIndex + 1
        })));
        for (const format of ["csv", "html"]) {
            const link = node("a", format.toUpperCase(), "text-button");
            link.href = `/api/jobs/${job.id}/tables/${unitIndex}/${tableIndex}/export?format=${format}`;
            link.download = `${table.id}.${format}`;
            card.append(link);
        }
        const grid = node("table");
        const anchors = new Map(table.cells.map(cell => [`${cell.row},${cell.column}`, cell]));
        const covered = new Set();
        for (let row = 0; row < Math.min(table.row_count, 100); row++) {
            const tr = node("tr");
            for (let col = 0; col < table.column_count; col++) {
                const key = `${row},${col}`;
                if (covered.has(key)) continue;
                const cell = anchors.get(key);
                const td = node(cell?.is_header ? "th" : "td");
                if (cell) {
                    td.rowSpan = cell.row_span;
                    td.colSpan = cell.column_span;
                    td.textContent = cell.text;
                    td.tabIndex = 0;
                    td.addEventListener("click", () => locate(cell));
                    td.addEventListener("keydown", event => {
                        if (event.key === "Enter") locate(cell);
                    });
                    td.title = t(cell.box ? "点击查看来源位置" : "该单元格没有可用位置");
                    for (let r = row; r < row + cell.row_span; r++)
                        for (let c = col; c < col + cell.column_span; c++) covered.add(`${r},${c}`);
                }
                tr.append(td);
            }
            grid.append(tr);
        }
        const scroll = node("div", undefined, "table-scroll");
        scroll.append(grid);
        card.append(scroll);
        if (table.row_count > 100) card.append(node("p", t("页面仅预览前 100 行，导出包含全部行。"), "small"));
        container.append(card);
    }
    if (!data.tables.length) container.append(node("p", t("未识别到表格；已保留页面文字和字段。"), "small"));
    container.append(details(t("图像处理与坐标变换"), data.preprocessing));
    container.append(details(t("精细定位记录"), data.localization));
    container.append(details(t("完整表格与来源坐标"), data));
}

/** Separate location metadata keeps business JSON unchanged and page attribution explicit. */
function renderFieldLocations(container, fields, result) {
    const list = node("details");
    list.append(node("summary", t("字段来源坐标")));
    for (const field of fields) {
        const control = button(field.label + " · " + field.text, () => {
            state.highlightCell = field.overlay_id || field.id;
            previewFile(result.file_id, field.page || result.pages[0]);
            renderSourceOverlay();
        });
        control.disabled = !field.box;
        list.append(control);
    }
    list.append(details(t("完整来源坐标"), fields));
    container.append(list);
}
