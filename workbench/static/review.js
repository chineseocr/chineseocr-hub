/**
 * Document review workspace.
 *
 * The saved job is the only source of recognized content and geometry. Page tabs
 * select existing results; they never run inference. Selection is presentation
 * state, so polling/language changes retain the active page, result tab and box.
 * Every document string is inserted with textContent, never HTML interpolation.
 */
"use strict";
const review = {
    jobId: null,
    pageKey: null,
    mode: "content",
    focus: null,
    hover: null,
    zoom: 1,
    showBoxes: true,
    rendering: false,
    items: new Map()
};
const reviewKey = (file, page) => file + ":" + page;

/** Install durable controls once; moving nodes preserves their existing listeners. */
function initializeReview() {
    const grid = $("view-extract").querySelector(".extract-grid");
    const panel = grid.querySelector(".run-panel");
    const bar = node("div", undefined, "review-command-bar");
    const settings = node("details", undefined, "review-settings");
    settings.id = "review-settings";
    const summary = node("summary");
    summary.append(node("span", t("运行设置")), node("span", "", "review-setting-summary"));
    summary.lastChild.id = "review-setting-summary";
    const action = $("run-extraction");
    settings.append(summary, panel);
    bar.append(settings, action);
    grid.before(bar);
    grid.querySelector(".right-column").replaceWith(grid.querySelector(".result-panel"));
    const source = grid.querySelector(".source-panel");
    const library = node("details", undefined, "review-file-library");
    const librarySummary = node("summary");
    librarySummary.id = "review-file-summary";
    library.append(librarySummary, $("file-list"));
    $("drop-zone").after(library);
    const toolbar = source.querySelector(".preview-toolbar");
    const pageControls = node("div", undefined, "review-page-controls");
    const previous = button("‹", () => stepReviewPage(-1), "review-icon-button");
    previous.id = "review-previous-page";
    const next = button("›", () => stepReviewPage(1), "review-icon-button");
    next.id = "review-next-page";
    pageControls.append(previous, $("preview-page"), next);
    toolbar.append(pageControls);
    const tools = node("div", undefined, "review-image-tools");
    tools.append(button("−", () => setReviewZoom(review.zoom - .25), "review-icon-button"));
    const fit = button("100%", () => setReviewZoom(1), "review-zoom-value");
    fit.id = "review-zoom-value";
    tools.append(fit, button("＋", () => setReviewZoom(review.zoom + .25), "review-icon-button"));
    const boxes = button(t("位置框"), () => {
        review.showBoxes = !review.showBoxes;
        boxes.setAttribute("aria-pressed", String(review.showBoxes));
        drawReviewOverlay();
    }, "review-box-toggle");
    boxes.id = "review-box-toggle";
    boxes.setAttribute("aria-pressed", "true");
    tools.append(boxes);
    toolbar.after(tools);
    const hint = node("div", t("点击结果或图像框，双向查看来源"), "review-source-hint");
    hint.id = "review-source-hint";
    source.append(hint);
    $("source-overlay").removeAttribute("aria-hidden");
    $("source-overlay").setAttribute("aria-label", t("来源位置框"));
    $("source-preview").addEventListener("load", () => {
        setReviewZoom(review.zoom);
        drawReviewOverlay();
    });
    document.querySelectorAll(".run-panel select,.run-panel input").forEach(control => control.addEventListener("change", updateReviewChrome));
    document.addEventListener("click", event => {
        if (!bar.contains(event.target)) settings.open = false;
    });
    updateReviewChrome();
}

function updateReviewChrome() {
    document.body.classList.toggle("review-workspace", state.view === "extract");
    if (!$("review-file-summary")) return;
    $("review-file-summary").textContent = t("文档库") + " · " + state.files.length + " " + t("个文件");
    $("review-setting-summary").textContent = [
        $("ocr-deskew").value === "on" ? t("自动纠偏") : t("原始图像"),
        $("ocr-position-mode").value === "contextual" ? t("精细定位") : t("直接定位"),
        $("page-range").value || t("全部页")
    ].join(" · ");
    $("review-previous-page").setAttribute("aria-label", t("上一页"));
    $("review-next-page").setAttribute("aria-label", t("下一页"));
    $("review-box-toggle").textContent = t("位置框");
    $("source-overlay").setAttribute("aria-label", t("来源位置框"));
    $("review-settings").querySelector("summary > span").textContent = t("运行设置");
    const focused = review.items.get(review.focus)?.item;
    $("review-source-hint").textContent = focused ? (focused.box ? (focused.label || t("来源位置")) + " · " + focused.text : t("该内容没有可用坐标，可查看原页核对")) : t("点击结果或图像框，双向查看来源");
    const file = state.files.find(item => item.id === state.preview);
    $("review-previous-page").disabled = !file || state.previewPage <= 1;
    $("review-next-page").disabled = !file || state.previewPage >= file.pages.length;
}

function setReviewZoom(value) {
    review.zoom = Math.max(.5, Math.min(3, Math.round(value * 100) / 100));
    $("source-canvas").style.width = `${review.zoom*100}%`;
    $("review-zoom-value").textContent = Math.round(review.zoom * 100) + "%";
    $("review-zoom-value").setAttribute("aria-label", t("适应宽度"));
}

function stepReviewPage(direction) {
    const file = state.files.find(item => item.id === state.preview);
    if (!file) return;
    const page = Math.max(1, Math.min(file.pages.length, state.previewPage + direction));
    if (page !== state.previewPage) previewFile(file.id, page);
}

/** Called by the shared preview selector, including file-list and result-tab navigation. */
function syncReviewPage(file, page) {
    updateReviewChrome();
    if (!state.job || review.rendering) return;
    const next = reviewKey(file, page);
    if (review.pageKey !== next) {
        review.pageKey = next;
        const url = new URL(location.href);
        url.searchParams.set("page", String(page));
        url.searchParams.set("file", file);
        history.replaceState(null, "", url);
        review.focus = null;
        review.hover = null;
        renderReviewResult(state.job);
    }
}

function reviewPages(job) {
    const pages = [];
    for (const unit of job.payload.units)
        for (const page of unit.pages) {
            const key = reviewKey(unit.file_id, page);
            if (!pages.some(item => item.key === key)) pages.push({
                key,
                file: unit.file_id,
                page,
                name: unit.filename
            });
        }
    return pages;
}

/** Stable DOM-independent keys keep selection safe across page/job/instance changes. */
function reviewItem(item, result, page, prefix = "") {
    return {
        ...item,
        reviewId: reviewKey(result.file_id, page) + ":" + prefix + item.id,
        file: result.file_id,
        page
    };
}

function registerReviewItem(item, element) {
    review.items.set(item.reviewId, {
        item,
        element
    });
    element.dataset.reviewId = item.reviewId;
    element.classList.add("review-linked-item");
    element.classList.toggle("is-selected", review.focus === item.reviewId);
    element.addEventListener("mouseenter", () => {
        review.hover = item.reviewId;
        drawReviewOverlay();
    });
    element.addEventListener("mouseleave", () => {
        review.hover = null;
        drawReviewOverlay();
    });
    element.addEventListener("click", () => focusReviewItem(item.reviewId));
    if (element.tagName !== "BUTTON") {
        element.tabIndex = 0;
        element.addEventListener("keydown", event => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                focusReviewItem(item.reviewId);
            }
        });
    }
}

function focusReviewItem(key, fromImage = false) {
    let entry = review.items.get(key);
    if (!entry) return;
    review.focus = key;
    if (fromImage && review.mode !== "content") {
        review.mode = "content";
        renderReviewResult(state.job);
        entry = review.items.get(key) || entry;
    }
    review.hover = null;
    if (entry.item.file !== state.preview || entry.item.page !== state.previewPage) {
        // syncReviewPage normally clears selection; restore it after navigation.
        const target = entry.item;
        previewFile(target.file, target.page);
        review.focus = key;
    }
    document.querySelectorAll("[data-review-id]").forEach(element => element.classList.toggle("is-selected", element.dataset.reviewId === key));
    const current = review.items.get(key) || entry;
    $("review-source-hint").textContent = entry.item.box ? (entry.item.label || t("来源位置")) + " · " + entry.item.text : t("该内容没有可用坐标，可查看原页核对");
    drawReviewOverlay();
    if (fromImage) current.element?.scrollIntoView({
        block: "nearest",
        behavior: "smooth"
    });
    else if (entry.item.box) {
        const file = state.files.find(item => item.id === entry.item.file);
        const page = file?.pages.find(item => item.page === entry.item.page);
        if (page) {
            const stage = $("view-extract").querySelector(".image-stage");
            const scale = $("source-canvas").clientWidth / page.width;
            const box = entry.item.box;
            stage.scrollTo({
                left: (box[0] + box[2]) * scale / 2 - stage.clientWidth / 2,
                top: (box[1] + box[3]) * scale / 2 - stage.clientHeight / 2,
                behavior: "smooth"
            });
        }
    }
}

/** Image hit targets use saved geometry; no text offsets or fake coordinates are generated. */
function drawReviewOverlay() {
    const overlay = clear("source-overlay");
    const file = state.files.find(item => item.id === state.preview);
    const page = file?.pages.find(item => item.page === state.previewPage);
    if (!page) return;
    overlay.setAttribute("viewBox", `0 0 ${page.width} ${page.height}`);
    const selected = review.hover || review.focus;
    // Draw active regions last so their hit target stays reachable above table borders.
    const entries = [...review.items.values()].filter(({ item, element }) => {
        if (item.file !== state.preview || item.page !== state.previewPage || !item.box) return false;
        // A mixed page has two distinct overlay levels: physical documents and
        // text inside each crop. Keep every document boundary visible, but show
        // an instance's text/field box only when that result is selected or
        // hovered. Drawing every crop's OCR boxes together obscures the paper
        // boundaries and makes it hard to tell which instance a box belongs to.
        const inDocument = element.closest(".review-document-card");
        return !inDocument || element.classList.contains("review-document-title") || item.reviewId === selected;
    });
    entries.sort((a, b) => Number(a.item.reviewId === selected) - Number(b.item.reviewId === selected));
    for (const {
            item
        }
        of entries.slice(0, 1000)) {
        const active = item.reviewId === selected;
        if (!review.showBoxes && !active) continue;
        const svg = document.createElementNS("http://www.w3.org/2000/svg", item.polygon?.length === 4 ? "polygon" : "rect");
        if (item.polygon?.length === 4) svg.setAttribute("points", item.polygon.map(point => point.join(",")).join(" "));
        else {
            const [x1, y1, x2, y2] = item.box;
            for (const [name, value] of Object.entries({
                    x: x1,
                    y: y1,
                    width: x2 - x1,
                    height: y2 - y1
                })) svg.setAttribute(name, String(value));
        }
        svg.setAttribute("class", "review-region" + (active ? " is-active" : ""));
        svg.setAttribute("role", "button");
        svg.setAttribute("tabindex", "0");
        svg.setAttribute("aria-label", (item.label || t("来源位置")) + " · " + item.text);
        const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
        title.textContent = item.text || item.label || t("来源位置");
        svg.append(title);
        svg.addEventListener("click", () => focusReviewItem(item.reviewId, true));
        svg.addEventListener("keydown", event => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                focusReviewItem(item.reviewId, true);
            }
        });
        overlay.append(svg);
    }
}

/** Render only the chosen page. Other pages stay in the saved job, not the DOM. */
function renderReviewResult(job) {
    const target = $("result-content");
    const oldScroll = $("review-result-pane")?.scrollTop || 0;
    const fresh = review.jobId !== job.id;
    const pages = reviewPages(job);
    if (fresh) {
        review.jobId = job.id;
        const url = new URL(location.href);
        const remembered = url.searchParams.get("job") === job.id ? pages.find(item => item.page === Number(url.searchParams.get("page")) && (!url.searchParams.get("file") || item.file === url.searchParams.get("file"))) : null;
        url.searchParams.set("job", job.id);
        history.replaceState(null, "", url);
        review.pageKey = remembered?.key || pages[0]?.key;
        review.focus = review.hover = null;
        review.mode = "content";
        review.zoom = 1;
        $("review-settings").open = false;
        state.recognitionTask = job.payload.task || "extract";
        state.selected = new Set(job.payload.files.map(file => file.id));
        if (state.providers.some(provider => provider.id === job.payload.provider_id)) {
            state.providerId = job.payload.provider_id;
            $("provider-select").value = state.providerId;
        }
        $("ocr-output-format").value = job.payload.output_format || "text";
        const options = job.payload.task ? job.payload : job.payload.processing || {};
        $("ocr-rotation").value = String(options.rotation || 0);
        $("ocr-deskew").value = options.deskew ? "on" : "off";
        $("ocr-position-mode").value = options.position_mode || "direct";
        $("table-grid-assist").value = options.grid_assist === false ? "off" : "on";
    }
    state.job = job;
    review.hover = null;
    review.rendering = true;
    const chosen = pages.find(item => item.key === review.pageKey);
    if (chosen) {
        const url = new URL(location.href);
        url.searchParams.set("page", String(chosen.page));
        url.searchParams.set("file", chosen.file);
        history.replaceState(null, "", url);
    }
    if (chosen && (state.preview !== chosen.file || state.previewPage !== chosen.page)) previewFile(chosen.file, chosen.page);
    review.rendering = false;
    updateRecognitionMode();
    $("result-state").replaceWith(Object.assign(badge(job.status), {
        id: "result-state"
    }));
    $("export-result").disabled = false;
    $("export-recognition-text").classList.toggle("hidden", !job.payload.task);
    clear(target);
    target.className = "review-results";
    review.items.clear();
    const tabs = node("div", undefined, "review-page-tabs");
    tabs.setAttribute("role", "tablist");
    tabs.setAttribute("aria-label", t("识别页面"));
    const multipleFiles = new Set(pages.map(item => item.file)).size > 1;
    pages.forEach((item, index) => {
        const tab = button((multipleFiles ? item.name + " · " : "") + t("第 {page} 页", {
            page: item.page
        }), () => {
            review.pageKey = item.key;
            review.focus = null;
            previewFile(item.file, item.page);
            renderReviewResult(job);
        }, "review-page-tab");
        tab.setAttribute("role", "tab");
        tab.setAttribute("aria-selected", String(item.key === review.pageKey));
        tab.setAttribute("aria-controls", "review-result-pane");
        tab.tabIndex = item.key === review.pageKey ? 0 : -1;
        const result = job.results.find(result => result.file_id === item.file && result.pages.includes(item.page));
        const dot = node("span", undefined, "review-page-dot " + (result?.status || "queued"));
        tab.prepend(dot);
        tab.title = item.name;
        tab.addEventListener("keydown", event => {
            const move = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
            if (move) {
                event.preventDefault();
                const next = pages[(index + move + pages.length) % pages.length];
                previewFile(next.file, next.page);
                $("result-content").querySelector('[role="tab"][aria-selected="true"]')?.focus();
            }
        });
        tabs.append(tab);
    });
    target.append(tabs);
    const modes = node("div", undefined, "review-result-modes");
    for (const [id, label] of [
            ["content", job.payload.task === "ocr" ? "文字块" : "结构化结果"],
            ["text", "全文"],
            ["json", "JSON"]
        ]) {
        const control = button(t(label), () => {
            review.mode = id;
            renderReviewResult(job);
        }, "review-mode");
        control.setAttribute("aria-pressed", String(review.mode === id));
        modes.append(control);
    }
    const pageLabel = node("span", t("{count} 页", {
        count: pages.length
    }), "review-page-count");
    modes.append(pageLabel);
    target.append(modes);
    const pane = node("div", undefined, "review-result-pane");
    pane.id = "review-result-pane";
    pane.setAttribute("role", "tabpanel");
    target.append(pane);
    const active = chosen || {
        file: state.preview,
        page: state.previewPage
    };
    const result = job.results.find(unit => unit.file_id === active.file && unit.pages.includes(active.page));
    if (!result) pane.append(node("div", t(["queued", "running"].includes(job.status) ? "该页正在等待识别结果…" : "该页没有识别结果"), "review-empty-page"));
    else {
        const meta = node("div", undefined, "review-result-meta");
        meta.append(badge(result.status), node("span", active.name || result.filename, "review-result-filename"));
        pane.append(meta);
        if (result.pages.length > 1) pane.append(node("p", t("联合提取 · 文档字段共享，来源位置随页面切换"), "review-inline-note"));
        const content = node("div");
        if (job.payload.task === "tables") reviewTables(content, result, job, active.page);
        else if (job.payload.task === "ocr") reviewText(content, result.data, result, active.page);
        else if (job.payload.task === "documents") reviewDocuments(content, result, job, active.page);
        else reviewBusiness(content, result.data, result.field_locations || [], result, active.page, job.payload.template?.schema);
        if (review.mode === "json") pane.append(node("pre", json(result.data), "review-json"));
        else if (review.mode === "text") pane.append(node("pre", result.data?.text || (result.data ? json(result.data) : ""), "review-full-text"));
        else pane.append(content);
        if (result.error) pane.append(node("p", t(result.error), "error"));
        if (result.validation_errors?.length) pane.append(details(t("Schema 校验问题"), result.validation_errors));
        const diagnostics = node("details", undefined, "review-diagnostics");
        diagnostics.append(node("summary", t("处理记录与原始响应")));
        for (const [name, value] of [
                ["图像处理与坐标变换", result.data?.preprocessing || result.preprocessing],
                ["精细定位记录", result.data?.localization || result.localization],
                ["模型用量", result.usage],
                ["原始模型输出", result.raw_text]
            ])
            if (value !== undefined) diagnostics.append(details(t(name), value));
        pane.append(diagnostics);
    }
    if (job.error) pane.append(node("p", t(job.error), "error"));
    pane.scrollTop = fresh ? 0 : oldScroll;
    updateReviewChrome();
    drawReviewOverlay();
}

function copyReviewText(text) {
    return navigator.clipboard.writeText(String(text)).then(() => toast(t("已复制")));
}

function reviewField(container, item, result, page, prefix = "") {
    const hasValue = Boolean(item.text?.trim()) && !item.unavailable;
    const record = reviewItem({
        ...item,
        box: hasValue ? item.box : null,
        polygon: hasValue ? item.polygon : null
    }, result, item.page || page, prefix);
    const row = node("div", undefined, "review-field");
    const label = node("span", item.label || t("文字"), "review-field-label");
    const value = button(item.text || "—", () => {}, "review-field-value");
    registerReviewItem(record, value);
    const status = node("span", record.box ? "↗" : t("无坐标"), "review-location-state");
    const copy = button("⧉", () => copyReviewText(item.text), "review-copy");
    copy.setAttribute("aria-label", t("复制内容"));
    copy.disabled = !hasValue;
    row.append(label, value, status, copy);
    container.append(row);
}

function reviewText(container, data, result, page, prefix = "") {
    if (!data) return;
    const rows = node("div", undefined, "review-text-blocks");
    if (data.blocks?.length) data.blocks.forEach((block, index) => {
        const item = reviewItem({
            ...block,
            label: t("文字块 {number}", {
                number: index + 1
            })
        }, result, page, prefix);
        const row = button("", () => {}, "review-text-row");
        row.append(node("span", String(index + 1).padStart(2, "0"), "review-line-number"), node("span", block.text, "review-line-text"));
        if (!block.box) row.append(node("small", t("无坐标")));
        registerReviewItem(item, row);
        rows.append(row);
    });
    else rows.append(node("pre", data.text || t("该图像未返回可见文字。"), "review-full-text"));
    container.append(rows);
}

function reviewBusiness(container, data, locations, result, page, schema, prefix = "") {
    if (!data) return;
    const grid = node("div", undefined, "review-fields");
    const empty = node("div", undefined, "review-fields");

    function fieldPosition(path) {
        const candidates = locations.filter(item => item.id === "field" + path);
        return candidates.find(item => item.page === page) || candidates.find(item => item.box) || candidates[0];
    }

    function walk(value, path = "", definition = schema) {
        if (Array.isArray(value) && value.length && value.every(row => row && !Array.isArray(row) && typeof row === "object" && Object.values(row).every(cell => cell === null || typeof cell !== "object"))) {
            const section = node("section", undefined, "review-table-section");
            section.append(node("h3", definition?.description || path.split("/").filter(Boolean).join(" › ")));
            const scroll = node("div", undefined, "table-scroll");
            const table = node("table", undefined, "review-table");
            const keys = [...new Set(value.flatMap(row => Object.keys(row)))];
            const headings = node("tr");
            keys.forEach(key => headings.append(node("th", definition?.items?.properties?.[key]?.description || key)));
            table.append(headings);
            value.slice(0, 100).forEach((row, index) => {
                const tr = node("tr");
                keys.forEach(key => {
                    const pointer = path + "/" + index + "/" + key.replaceAll("~", "~0").replaceAll("/", "~1");
                    const text = row[key] === null || row[key] === undefined ? "—" : String(row[key]);
                    const cell = node("td", text);
                    registerReviewItem(reviewItem({
                        ...fieldPosition(pointer),
                        id: "field" + pointer,
                        label: definition?.items?.properties?.[key]?.description || key,
                        text
                    }, result, fieldPosition(pointer)?.page || page, prefix), cell);
                    tr.append(cell);
                });
                table.append(tr);
            });
            scroll.append(table);
            section.append(scroll);
            grid.append(section);
            if (value.length > 100) section.append(node("p", t("页面仅预览前 100 行，导出包含全部行。"), "small"));
            return;
        }
        if (value !== null && typeof value === "object") {
            for (const [key, child] of Object.entries(value)) walk(child, path + "/" + String(key).replaceAll("~", "~0").replaceAll("/", "~1"), Array.isArray(value) ? definition?.items : definition?.properties?.[key]);
            if (!Object.keys(value).length) reviewField(grid, {
                id: "field" + path,
                label: definition?.description || path,
                text: json(value),
                box: null
            }, result, page, prefix);
            return;
        }
        const match = fieldPosition(path);
        reviewField(value === null || value === "" ? empty : grid, {
            ...match,
            id: "field" + path,
            label: definition?.description || path.split("/").filter(Boolean).join(" › "),
            text: value === null ? t("未提取到值") : String(value),
            unavailable: value === null || value === ""
        }, result, page, prefix);
    }
    walk(data);
    container.append(grid);
    if (empty.children.length) {
        const missing = node("details", undefined, "review-empty-fields");
        missing.append(node("summary", t("空值字段 ({count})", {
            count: empty.children.length
        })), empty);
        container.append(missing);
    }
}

function reviewTables(container, result, job, page) {
    const data = result.data;
    if (!data) return;
    const fields = node("div", undefined, "review-fields");
    const empty = node("div", undefined, "review-fields");
    data.fields.forEach(item => reviewField(item.text.trim() ? fields : empty, item, result, page));
    container.append(fields);
    if (empty.children.length) {
        const missing = node("details", undefined, "review-empty-fields");
        missing.append(node("summary", t("空值字段 ({count})", {
            count: empty.children.length
        })), empty);
        container.append(missing);
    }
    data.tables.forEach((table, tableIndex) => {
        const section = node("section", undefined, "review-table-section");
        const heading = node("div", undefined, "review-table-heading");
        heading.append(node("h3", table.title || t("表格 {number}", {
            number: tableIndex + 1
        })), node("span", `${table.row_count} × ${table.column_count}`, "tag"));
        for (const format of ["csv", "html"]) {
            const link = node("a", format.toUpperCase(), "review-export");
            link.href = `/api/jobs/${job.id}/tables/${job.results.indexOf(result)}/${tableIndex}/export?format=${format}`;
            link.download = `${table.id}.${format}`;
            heading.append(link);
        }
        section.append(heading);
        const scroll = node("div", undefined, "table-scroll");
        const grid = node("table", undefined, "review-table");
        const anchors = new Map(table.cells.map(cell => [`${cell.row},${cell.column}`, cell]));
        const covered = new Set();
        for (let r = 0; r < Math.min(100, table.row_count); r++) {
            const tr = node("tr");
            for (let c = 0; c < table.column_count; c++) {
                if (covered.has(`${r},${c}`)) continue;
                const cell = anchors.get(`${r},${c}`);
                const td = node(cell?.is_header ? "th" : "td");
                if (cell) {
                    td.rowSpan = cell.row_span;
                    td.colSpan = cell.column_span;
                    td.textContent = cell.text;
                    registerReviewItem(reviewItem({
                        ...cell,
                        label: t("表格 {number}", {
                            number: tableIndex + 1
                        }) + ` · ${r+1},${c+1}`
                    }, result, page), td);
                    for (let y = r; y < r + cell.row_span; y++)
                        for (let x = c; x < c + cell.column_span; x++) covered.add(`${y},${x}`);
                }
                tr.append(td);
            }
            grid.append(tr);
        }
        scroll.append(grid);
        section.append(scroll);
        if (table.unrecognized_grid_cells?.length) section.append(node("p", t("有线框单元格未返回内容，请核对原图。"), "review-inline-note"));
        if (table.row_count > 100) section.append(node("p", t("页面仅预览前 100 行，导出包含全部行。"), "small"));
        container.append(section);
    });
    if (!data.tables.length) container.append(node("p", t("未识别到表格；已保留页面文字和字段。"), "review-inline-note"));
}

function reviewDocuments(container, result, job, page) {
    for (const instance of result.instances || []) {
        const card = node("article", undefined, "review-document-card");
        const title = button(t(state.recognitionLabels[instance.label] || instance.label), () => {}, "review-document-title");
        registerReviewItem(reviewItem({
            ...instance,
            text: t(state.recognitionLabels[instance.label] || instance.label)
        }, result, page, "document:"), title);
        card.append(title, badge(instance.status));
        const image = node("img", undefined, "review-document-crop");
        image.src = `/api/jobs/${job.id}/regions/${instance.id}/crop`;
        image.alt = t("文档实例裁剪图");
        card.append(image);
        if (instance.template) reviewBusiness(card, instance.data, instance.field_locations || [], result, page, job.payload.routes[instance.label]?.schema, instance.id + ":");
        else reviewText(card, instance.data, result, page, instance.id + ":");
        if (instance.error) card.append(node("p", t(instance.error), "error"));
        container.append(card);
    }
    if (!result.instances?.length) container.append(node("p", t("模型未定位到文档；请检查输入或改用全文识别。"), "review-inline-note"));
}
initializeReview();
