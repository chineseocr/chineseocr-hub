"use strict";

/**
 * UI localization without modifying business data.
 *
 * 中文文案作为稳定的消息 key；英文译文集中在 locales/en.json。
 * 只翻译带 data-i18n 的界面节点和显式调用 t() 的交互文案，不扫描/替换
 * 用户输入、提示词、Schema 描述、文件名或模型原始输出。这样切换语言不会
 * 破坏配置，也不会把识别原文误当作界面文字翻译。
 */
window.WorkbenchI18n = (() => {
    const storageKey = "workbench.language";
    const supported = new Set(["zh", "en"]);
    const queryLanguage = new URLSearchParams(location.search).get("lang");
    let storedLanguage;
    try {
        storedLanguage = localStorage.getItem(storageKey);
    } catch {
        // Browser privacy settings may disable storage; the current session still works.
    }
    let currentLanguage = supported.has(queryLanguage) ? queryLanguage :
        supported.has(storedLanguage) ? storedLanguage :
        navigator.language.toLowerCase().startsWith("zh") ? "zh" : "en";
    let english = {};

    /** Resolve a UI message and interpolate named values as plain text, never HTML. */
    function t(message, values = {}) {
        if (typeof message !== "string") return String(message ?? "");
        let result = currentLanguage === "en" ? english[message] ?? message : message;
        // Framework validation prefixes and HTTP status errors contain variable data.
        // Keep the actual status code, while translating the surrounding instruction.
        if (message.startsWith("Value error, ")) {
            result = t("配置无效：") + t(message.slice("Value error, ".length));
        }
        const status = message.match(/^模型服务返回 HTTP (\d+)；请检查服务日志与模型配置$/);
        if (currentLanguage === "en" && status) {
            result = "Model service returned HTTP " + status[1] +
                ". Check the service logs and configuration.";
        }
        if (message === "Field required") result = t("字段不能为空");
        for (const [name, value] of Object.entries(values)) {
            result = result.replaceAll("{" + name + "}", String(value));
        }
        return result;
    }

    /** Translate explicit presentation attributes; form values and API payloads are untouched. */
    function apply() {
        document.documentElement.lang = currentLanguage === "en" ? "en" : "zh-CN";
        document.title = t("文档提取工作台");
        document.querySelectorAll("[data-i18n]").forEach(element => {
            element.textContent = t(element.dataset.i18n);
        });
        for (const attribute of ["placeholder", "aria-label", "alt"]) {
            document.querySelectorAll("[data-i18n-" + attribute + "]").forEach(element => {
                element.setAttribute(attribute, t(element.getAttribute("data-i18n-" +
                    attribute)));
            });
        }
        document.getElementById("language-select").value = currentLanguage;
        const modelGuide = document.getElementById("model-guide-link");
        if (modelGuide) modelGuide.href = "https://github.com/chineseocr/chineseocr-hub/blob/main/docs/" +
            (currentLanguage === "en" ? "model-connections.en.md" : "model-connections.md");
    }

    const ready = fetch("/assets/locales/en.json")
        .then(response => {
            if (!response.ok) throw new Error("Unable to load interface translations");
            return response.json();
        })
        .then(messages => {
            english = messages;
            apply();
            document.getElementById("language-select").addEventListener("change", event => {
                currentLanguage = event.target.value;
                try {
                    localStorage.setItem(storageKey, currentLanguage);
                } catch {
                    /* Session-only preference. */
                }
                const url = new URL(location.href);
                url.searchParams.set("lang", currentLanguage);
                history.replaceState(null, "", url);
                apply();
                window.dispatchEvent(new CustomEvent("workbench:language-changed"));
            });
        });

    return {
        t,
        apply,
        ready,
        language: () => currentLanguage
    };
})();
