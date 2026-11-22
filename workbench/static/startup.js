/**
 * Start the HTTP application, or explain how to launch it when index.html is opened
 * as a local file. This small entry point has no imports, fetches or model calls in
 * file mode. Relative loading lets the guidance work before an app server exists.
 */
"use strict";
(async () => {
    const english = new URLSearchParams(location.search).get("lang") === "en" ||
        (!new URLSearchParams(location.search).has("lang") &&
            !navigator.language.toLowerCase().startsWith("zh"));

    /** Keep bootstrap failures visible even when the full translation catalog cannot load. */
    function showHelp(failed = false) {
        const messages = english ? {
            "startup-title": failed ? "Unable to load the workspace" : "Start the workspace service first",
            "startup-description": failed ?
                "Check that the application is running, then reload this page. No document has been sent." : "Run the command below from the project directory, then open the localhost address. Opening index.html directly cannot run the backend.",
            "startup-link": "Open local workspace"
        } : {
            "startup-title": failed ? "工作台资源加载失败" : "请通过应用服务打开工作台",
            "startup-description": failed ?
                "请检查应用服务是否运行，然后刷新页面。没有发送任何文档。" : "在项目目录启动服务，然后打开本机地址。直接打开网页文件无法运行后端功能。",
            "startup-link": "打开本机工作台"
        };
        for (const [id, message] of Object.entries(messages)) {
            document.getElementById(id).textContent = message;
        }
        document.getElementById("language-select").classList.add("hidden");
        document.getElementById("startup-help").classList.remove("hidden");
    }

    if (location.protocol === "file:") {
        showHelp();
        return;
    }
    try {
        // Preserve dependency order: app.js expects WorkbenchI18n to exist.
        for (const path of ["/assets/i18n.js", "/assets/app.js", "/assets/tenants.js", "/assets/recognition.js", "/assets/applications.js", "/assets/review.js"]) {
            await new Promise((resolve, reject) => {
                const script = document.createElement("script");
                script.src = path;
                script.onload = resolve;
                script.onerror = reject;
                document.head.append(script);
            });
        }
        await WorkbenchI18n.ready;
        await start();
    } catch {
        showHelp(true);
    }
})();
