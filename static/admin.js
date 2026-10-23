"use strict";
const el = id => document.getElementById(id);
let csrf = "", refreshing = false;
function message(text, error = false) { el("admin-feedback").textContent = text; el("admin-feedback").classList.toggle("error", error); }
function node(tag, className, text) { const n = document.createElement(tag); n.className = className; n.textContent = text; return n; }
function date(value) { return new Date(value * 1000).toLocaleString("zh-CN"); }
function hideSecret() { el("secret").value = ""; el("secret-panel").hidden = true; el("generate").disabled = !csrf; }
async function api(path, options = {}) {
  const response = await fetch(`/admin/api/${path}`, {...options, headers:{"Content-Type":"application/json", "X-Admin-CSRF":csrf}});
  if (!response.ok) {
    let detail; try { detail = (await response.json()).detail; } catch (_) {}
    throw new Error(typeof detail === "string" ? detail : `请求失败（${response.status}）`);
  }
  return response.json();
}
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const [keys, clients] = await Promise.all([api("keys"), api("clients")]);
    el("keys").replaceChildren(); el("clients").replaceChildren();
    for (const key of keys) {
      const row = node("article", "admin-row", ""), info = node("div", "", "");
      info.append(node("h3", "", key.name), node("p", "", `${key.prefix}… · 创建于 ${date(key.created_at)}`)); row.append(info);
      if (key.revoked_at) row.append(node("span", "badge", "已撤销"));
      else {
        const button = node("button", "revoke", "撤销"); button.type = "button";
        button.addEventListener("click", async () => {
          if (!confirm(`撤销“${key.name}”的访问令牌？此操作无法恢复，之后可生成新令牌。`)) return;
          button.disabled = true;
          try { await api(`keys/${key.id}`, {method:"DELETE"}); message("令牌已撤销。"); await refresh(); }
          catch (error) { message(error.message, true); button.disabled = false; }
        }); row.append(button);
      }
      el("keys").append(row);
    }
    for (const client of clients) {
      const row = node("article", "admin-row", ""), info = node("div", "", "");
      info.append(node("h3", "", client.name), node("p", "", `${client.ip} · 客户端 ${client.id.slice(0,8)} · 最近在线 ${date(client.last_seen)}`));
      row.append(info, node("span", `badge ${client.online ? "submitted" : ""}`, client.online ? "在线" : "离线")); el("clients").append(row);
    }
    if (!keys.length) el("keys").append(node("p", "empty", "尚无令牌。先为第一台设备生成一个。"));
    if (!clients.length) el("clients").append(node("p", "empty", "尚无客户端连接。"));
  } catch (error) { message(error.message, true); }
  finally { refreshing = false; }
}
el("key-form").addEventListener("submit", async event => {
  event.preventDefault(); if (!el("secret-panel").hidden) return;
  el("generate").disabled = true;
  try {
    const result = await api("keys", {method:"POST", body:JSON.stringify({name:el("device-name").value.trim()})});
    el("secret").value = result.secret; result.secret = ""; el("secret-panel").hidden = false;
    el("device-name").value = ""; message("令牌已生成，请复制保存。本页关闭后无法再次查看。"); await refresh();
  } catch (error) { message(`${error.message} 若连接中断，请先检查令牌列表；无法取回的令牌可撤销后重新生成。`, true); el("generate").disabled = false; }
});
el("copy").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(el("secret").value); hideSecret(); message("已复制到剪贴板，完整令牌已隐藏。"); }
  catch (_) { el("secret").focus(); el("secret").select(); message("自动复制不可用，请按 Ctrl+C 手动复制，复制后自动隐藏。", true); }
});
el("secret").addEventListener("copy", () => { setTimeout(() => { hideSecret(); message("完整令牌已隐藏，请确认已保存。"); }, 0); });
el("hide").addEventListener("click", hideSecret);
window.addEventListener("pagehide", hideSecret);
el("admin-refresh").addEventListener("click", refresh);
(async () => {
  try { csrf = (await api("session")).csrf; el("generate").disabled = false; message("本机管理已就绪。"); await refresh(); setInterval(refresh, 5000); }
  catch (error) { message(error.message, true); }
})();
