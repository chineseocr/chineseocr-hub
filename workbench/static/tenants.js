/**
 * Account/team UI. Authorization is enforced again by the server on every request.
 * The browser keeps no passwords or API keys in localStorage. Team switches reload
 * the application so file selections, drafts and result panes cannot cross teams.
 */
"use strict";
const roleMessages = {
    owner: "所有者",
    admin: "管理员",
    member: "成员",
    viewer: "只读成员"
};
const auditMessages = {
    "tenant.created": "创建团队",
    "tenant.limits_changed": "调整团队额度",
    "member.role_changed": "调整成员角色",
    "member.removed": "移除成员",
    "invitation.created": "创建邀请",
    "invitation.accepted": "接受邀请",
    "invitation.revoked": "撤销邀请",
    "api_key.created": "创建接口密钥",
    "api_key.revoked": "撤销接口密钥"
};
let adminTenants = [];

/** Switch the same shell between a local workspace token and hosted account login. */
function configureLogin(deployment) {
    const online = deployment.mode === "saas";
    $("login-token-field").classList.toggle("hidden", online);
    $("token").disabled = online;
    for (const name of ["account", "password"]) $("login-" + name + "-field").classList.toggle("hidden", !online);
    for (const id of ["login-username", "login-password"]) {
        $(id).disabled = !online;
        $(id).required = online;
    }
    $("community-login-help").classList.toggle("hidden", online);
    $("account-login-help").classList.toggle("hidden", !online);
    const invitation = new URLSearchParams(location.hash.slice(1)).get("invite") || "";
    $("register-invitation").value = invitation;
    $("accept-invite-token").value = invitation;
    $("show-register").classList.toggle("hidden", !online || (!deployment.registration && !invitation));
}

/** Present verified membership metadata; never derive access from a selected label. */
async function renderTenantControls() {
    if (!state.saas || !state.account) return;
    $("tenant-controls").classList.remove("hidden");
    document.querySelectorAll("[data-saas]").forEach(element => element.classList.remove("hidden"));
    document.querySelectorAll("[data-platform-admin]").forEach(element => element.classList.toggle("hidden", !state.saas.user.is_admin));
    const select = clear("tenant-select");
    state.account.tenants.forEach(tenant => option(select, tenant.id, tenant.name));
    select.value = state.saas.tenant.id;
    $("tenant-role").textContent = t(roleMessages[state.saas.role]);
    $("signed-in-user").textContent = state.saas.user.username;
    for (const [id, message] of [
            ["deployment-label", "在线团队"],
            ["edition-badge", "在线版"],
            ["deployment-footer", "团队隔离 · 按权限访问"],
            ["edition-label", "在线版 · 多租户工作空间"],
            ["provider-security-note", "在线版模型密钥加密保存，地址由平台允许列表控制；不自动重试模型请求。"]
        ]) $(id).textContent = t(message);
    renderSaasAccess();
}

/** Disable write affordances for viewers; the gateway remains the security boundary. */
function renderSaasAccess() {
    if (!state.saas) return;
    const viewer = state.saas.role === "viewer";
    for (const id of ["new-template", "save-template", "add-field", "import-schema", "file-input"]) $(id).disabled = viewer;
    for (const id of ["run-extraction", "run-evaluation"]) $(id).disabled = viewer || !state.providerId;
    document.querySelectorAll("#field-editor input,#field-editor select,#field-editor button,#system-prompt,#user-prompt,#template-name").forEach(element => element.disabled = viewer);
    const admin = ["owner", "admin"].includes(state.saas.role);
    $("provider-form").querySelectorAll("input,button").forEach(element => element.disabled = !admin);
    $("dictionary-form").querySelectorAll("input,textarea,button").forEach(element => element.disabled = viewer);
    $("new-dictionary").disabled = viewer;
}

/** Display a newly generated secret once, in memory only, and erase it on close. */
function showSecret(value) {
    $("one-time-secret").value = value;
    $("secret-dialog").showModal();
}
$("secret-dialog").addEventListener("close", () => $("one-time-secret").value = "");

/** Fetch independent team metadata in parallel; raw extraction data stays in its own views. */
async function renderTeam() {
    if (!state.saas) return;
    const admin = ["owner", "admin"].includes(state.saas.role);
    const [report, members, keys] = await Promise.all([api("/usage"), api("/tenants/members"), api("/api-keys")]);
    const usage = clear("tenant-usage");
    for (const [label, value] of [
            ["统计月份", report.usage.period],
            ["已提交页 / 月额度", report.usage.admitted_pages + " / " + report.tenant.limits.monthly_pages],
            ["已用存储 / 字节", report.usage.storage_bytes + " / " + report.tenant.limits.storage_bytes],
            ["模型已报告的输入词元", report.usage.prompt_tokens],
            ["模型已报告的输出词元", report.usage.completion_tokens]
        ]) {
        const item = node("div", undefined, "result-value");
        item.append(node("span", t(label)), node("b", String(value)));
        usage.append(item);
    }
    const list = clear("tenant-members");
    members.forEach(member => {
        const row = node("div", undefined, "dataset-row list-action");
        row.append(node("strong", member.username, "grow"), node("span", t(roleMessages[member.role]), "tag"));
        if (admin && member.role !== "owner" && (state.saas.role === "owner" || member.role !== "admin")) {
            const select = node("select");
            select.setAttribute("aria-label", t("成员角色 ") + member.username);
            ["viewer", "member", ...(state.saas.role === "owner" ? ["admin"] : [])].forEach(role => option(select, role, t(roleMessages[role])));
            select.value = member.role;
            select.addEventListener("change", guard(async () => {
                await api("/tenants/members/" + member.id + "/role", {
                    role: select.value
                });
                await renderTeam();
            }));
            row.append(select, button(t("移除成员"), async () => {
                if (!confirm(t("移除后，该成员及其团队接口密钥将失去访问权限。"))) return;
                await api("/tenants/members/" + member.id + "/remove", {});
                await renderTeam();
            }));
        }
        list.append(row);
    });
    $("invite-form").classList.toggle("hidden", !admin);
    const roles = clear("invite-role");
    ["member", "viewer", ...(state.saas.role === "owner" ? ["admin"] : [])].forEach(role => option(roles, role, t(roleMessages[role])));
    const keyRoles = clear("api-key-role");
    (state.saas.role === "viewer" ? ["viewer"] : ["member", "viewer"]).forEach(role => option(keyRoles, role, t(roleMessages[role])));
    const keyList = clear("tenant-api-keys");
    keys.forEach(key => {
        const row = node("div", undefined, "dataset-row");
        row.append(node("strong", key.name), node("p", key.prefix + "… · " + t(roleMessages[key.role]) + " · " + new Date(key.expires * 1000).toLocaleDateString(WorkbenchI18n.language() === "en" ? "en-US" : "zh-CN"), "small"));
        if (key.application_ids) row.append(node("span", t("仅限指定应用") + " · " + key.application_ids.length, "tag"));
        row.append(key.revoked ? node("span", t("已撤销"), "tag") : button(t("撤销密钥"), async () => {
            await api("/api-keys/" + key.id + "/revoke", {});
            await renderTeam();
        }));
        keyList.append(row);
    });
    const invitations = clear("tenant-invitations");
    const audit = clear("tenant-audit");
    if (admin) {
        const [invites, events] = await Promise.all([api("/invitations"), api("/audit")]);
        invites.forEach(invite => {
            const row = node("div", undefined, "dataset-row");
            row.append(node("span", t(roleMessages[invite.role]) + " · " + new Date(invite.expires * 1000).toLocaleDateString(WorkbenchI18n.language() === "en" ? "en-US" : "zh-CN")));
            if (!invite.revoked && !invite.accepted_by) row.append(button(t("撤销邀请"), async () => {
                await api("/invitations/" + invite.id + "/revoke", {});
                await renderTeam();
            }));
            else row.append(node("span", t(invite.accepted_by ? "已使用" : "已撤销"), "tag"));
            invitations.append(row);
        });
        events.forEach(event => {
            const row = node("div", undefined, "dataset-row");
            row.append(node("strong", t(auditMessages[event.event] || (event.event.startsWith("workspace.") ? "工作空间写入" : "账户操作"))), node("p", new Date(event.created * 1000).toLocaleString(WorkbenchI18n.language() === "en" ? "en-US" : "zh-CN") + " · " + event.resource, "small"));
            audit.append(row);
        });
    } else audit.append(node("p", t("审计记录仅团队管理员可查看。"), "small"));
}

/** Show platform metadata only; platform administration does not grant document access. */
async function renderPlatform() {
    if (!state.saas?.user.is_admin) return;
    const [tenants, users] = await Promise.all([api("/admin/tenants"), api("/admin/users")]);
    adminTenants = tenants;
    const select = clear("admin-tenant");
    tenants.forEach(tenant => option(select, tenant.id, tenant.name));
    renderLimits();
    const list = clear("platform-users");
    users.forEach(user => {
        const row = node("div", undefined, "dataset-row list-action");
        row.append(node("strong", user.username, "grow"), node("span", t(user.disabled ? "已停用" : "正常"), "tag"));
        if (user.id !== state.saas.user.id) row.append(button(t(user.disabled ? "恢复账户" : "停用账户"), async () => {
            await api("/admin/users/" + user.id + "/status", {
                disabled: !user.disabled
            });
            await renderPlatform();
        }));
        list.append(row);
    });
}

function renderLimits() {
    const tenant = adminTenants.find(item => item.id === $("admin-tenant").value);
    if (!tenant) return;
    $("admin-plan").value = tenant.plan;
    $("admin-pages").value = tenant.limits.monthly_pages;
    $("admin-storage").value = tenant.limits.storage_bytes;
    $("admin-members").value = tenant.limits.members;
}

$("tenant-select").addEventListener("change", guard(async () => {
    if (state.dirty && !confirm(t("当前草稿尚未保存，仍然切换团队？"))) {
        $("tenant-select").value = state.saas.tenant.id;
        return;
    }
    await api("/account/active", {
        tenant_id: $("tenant-select").value
    });
    state.dirty = false;
    location.reload();
}));
$("new-tenant").addEventListener("click", guard(async () => {
    if (state.dirty && !confirm(t("当前草稿尚未保存，仍然切换团队？"))) return;
    const name = prompt(t("新团队名称"));
    if (!name?.trim()) return;
    const tenant = await api("/tenants", {
        name: name.trim()
    });
    await api("/account/active", {
        tenant_id: tenant.id
    });
    state.dirty = false;
    location.reload();
}));
$("show-register").addEventListener("click", () => $("register-dialog").showModal());
$("register-form").addEventListener("submit", guard(async event => {
    event.preventDefault();
    await api("/account/register", {
        username: $("register-username").value,
        password: $("register-password").value,
        invitation: $("register-invitation").value
    });
    $("login-username").value = $("register-username").value;
    $("register-password").value = "";
    $("register-dialog").close();
    toast(t("账户已创建，请登录。"));
}));
$("invite-form").addEventListener("submit", guard(async event => {
    event.preventDefault();
    const result = await api("/invitations", {
        role: $("invite-role").value
    });
    showSecret(location.origin + "/#invite=" + encodeURIComponent(result.token));
    await renderTeam();
}));
$("accept-invite-form").addEventListener("submit", guard(async event => {
    event.preventDefault();
    const tenant = await api("/invitations/accept", {
        token: $("accept-invite-token").value
    });
    await api("/account/active", {
        tenant_id: tenant.id
    });
    state.dirty = false;
    location.href = location.pathname + location.search;
}));
$("api-key-form").addEventListener("submit", guard(async event => {
    event.preventDefault();
    const key = await api("/api-keys", {
        name: $("api-key-name").value,
        role: $("api-key-role").value,
        days: Number($("api-key-days").value)
    });
    showSecret(key.token);
    await renderTeam();
}));
$("password-change-form").addEventListener("submit", guard(async event => {
    event.preventDefault();
    await api("/account/password", {
        current_password: $("current-password").value,
        new_password: $("new-password").value
    });
    $("current-password").value = $("new-password").value = "";
    state.dirty = false;
    location.reload();
}));
$("admin-tenant").addEventListener("change", renderLimits);
$("limits-form").addEventListener("submit", guard(async event => {
    event.preventDefault();
    await api("/admin/tenants/" + $("admin-tenant").value + "/limits", {
        plan: $("admin-plan").value,
        monthly_pages: Number($("admin-pages").value),
        storage_bytes: Number($("admin-storage").value),
        members: Number($("admin-members").value)
    });
    toast(t("团队额度已保存"));
}));

// A registered account can recover from losing its final team membership. These
// routes require a session but deliberately do not require existing membership.
$("first-team-form").addEventListener("submit", guard(async event => {
    event.preventDefault();
    const team = await api("/tenants", {
        name: $("first-team-name").value
    });
    await api("/account/active", {
        tenant_id: team.id
    });
    location.reload();
}));
$("first-invite-form").addEventListener("submit", guard(async event => {
    event.preventDefault();
    const team = await api("/invitations/accept", {
        token: $("first-invite-token").value
    });
    await api("/account/active", {
        tenant_id: team.id
    });
    location.reload();
}));
$("no-team-logout").addEventListener("click", guard(async () => {
    await api("/session", null, "DELETE");
    location.reload();
}));
