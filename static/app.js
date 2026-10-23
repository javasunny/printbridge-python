"use strict";
const $ = (id) => document.getElementById(id);
const statuses = {queued:"等待中",processing:"处理中",submitted:"已提交",failed:"失败",uncertain:"结果待核查",simulated:"模拟完成",canceled:"已取消"};
const sideNames = {simplex:"单面",duplexlong:"双面长边",duplexshort:"双面短边"};
const tokenStorageKey = "printbridge.accessToken";
function savedToken(value) {
  try {
    if (value === undefined) return localStorage.getItem(tokenStorageKey) || "";
    if (value) localStorage.setItem(tokenStorageKey, value);
    else localStorage.removeItem(tokenStorageKey);
    return true;
  } catch (_) { return value === undefined ? "" : false; }
}
function clientIdentity() {
  const storageKey = "printbridge.clientId";
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
  let id = `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  try {
    const saved = localStorage.getItem(storageKey);
    if (saved && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(saved)) id = saved;
    else localStorage.setItem(storageKey, id);
  } catch (_) {}
  return id;
}
const clientId = clientIdentity();
let token = "", timer = null, pending = null, busy = false, maxBytes = 0;
function feedback(message, error = false) { $("feedback").textContent = message; $("feedback").classList.toggle("error", error); }
function updateForm() { $("print-fields").disabled = !token || busy || !!pending; $("submit").disabled = !token || busy; $("submit").textContent = busy ? "正在提交…" : pending ? "重试同一任务" : "提交打印 ↗"; $("discard").hidden = !pending || busy; $("logout").disabled = busy || $("connect").disabled; }
async function request(path, options = {}) {
  const response = await fetch(path, {...options, headers:{...options.headers, Authorization:`Bearer ${token}`}});
  if (!response.ok) {
    if (response.status === 401) { clearInterval(timer); token = ""; savedToken(""); $("logout").hidden = true; $("connection").textContent = "令牌无效，请重新连接"; $("connection").classList.remove("online"); $("refresh").disabled = true; updateForm(); }
    let detail;
    try { detail = (await response.json()).detail; } catch (_) { /* Keep the HTTP status when no JSON body is available. */ }
    const error = new Error(response.status === 401 ? "访问令牌无效。" : typeof detail === "string" ? detail : `请求失败（${response.status}），请检查文件和打印参数或稍后重试。`);
    throw error;
  }
  return response.json();
}
function element(tag, className, content) { const node = document.createElement(tag); node.className = className; node.textContent = content; return node; }
async function refresh() {
  try {
    const activeToken = token;
    await request("/api/heartbeat", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({client_id:clientId})});
    if (!token || token !== activeToken) return;
    const jobs = await request("/api/jobs");
    if (!token || token !== activeToken) return;
    const list = $("jobs"); list.replaceChildren();
    if (!jobs.length) { list.append(element("div", "empty", "还没有任务，上传第一份 PDF 吧。")); return; }
    for (const job of jobs) {
      const row = element("article", "job", ""), top = element("div", "job-top", "");
      top.append(element("h3", "job-name", job.name), element("span", `badge ${Object.hasOwn(statuses, job.status) ? job.status : ""}`, statuses[job.status] || "未知状态"));
      const date = new Date(job.created_at);
      row.append(top, element("p", "job-meta", `${Number.isNaN(date.valueOf()) ? job.created_at : date.toLocaleString("zh-CN")} · ${job.copies} 份 · ${sideNames[job.sides] || job.sides} · A4`));
      if (job.message) row.append(element("p", "job-message", job.message));
      list.append(row);
    }
  } catch (error) { feedback(error.message || "暂时无法获取任务记录。", true); }
}
async function connect(value) {
  clearInterval(timer); token = value; $("connect").disabled = true; updateForm();
  try {
    const settings = await request("/api/settings");
    maxBytes = settings.max_upload_mb * 1024 * 1024;
    $("file-hint").textContent = `仅限 PDF，最大 ${settings.max_upload_mb} MB`;
    $("connection").textContent = settings.mode === "simulate" ? "已连接 · 模拟模式" : "已连接";
    $("connection").classList.add("online"); $("refresh").disabled = false; $("token").value = "";
    $("mode").textContent = settings.mode === "simulate" ? "模拟模式 · 不会实际打印" : "WINDOWS 打印模式";
    const remembered = savedToken(token);
    $("logout").hidden = false;
    feedback(remembered ? (pending ? "已连接，可重试同一任务。" : "已连接，已记住此设备。") : "已连接，但浏览器不允许保存令牌，刷新后需重新输入。", !remembered);
    await refresh(); if (token) timer = setInterval(refresh, 5000);
  } catch (error) { token = ""; feedback(error.message || "连接失败，请确认服务已启动。", true); $("connection").textContent = "连接失败"; $("connection").classList.remove("online"); $("refresh").disabled = true; }
  finally { $("connect").disabled = false; updateForm(); }
}
$("connect-form").addEventListener("submit", (event) => {
  event.preventDefault(); connect($("token").value.trim());
});
$("logout").addEventListener("click", () => {
  if (token) fetch("/api/disconnect", {method:"POST", headers:{"Authorization":`Bearer ${token}`, "Content-Type":"application/json"}, body:JSON.stringify({client_id:clientId}), keepalive:true}).catch(() => {});
  clearInterval(timer); token = ""; const cleared = savedToken("");
  $("token").value = ""; $("logout").hidden = cleared;
  $("connection").textContent = "尚未连接"; $("connection").classList.remove("online");
  $("refresh").disabled = true; $("jobs").replaceChildren();
  updateForm(); feedback(cleared ? "已退出并清除本设备令牌。已提交的打印任务仍会继续执行。" : "已退出，但无法清除保存的令牌，请在浏览器中清除此网站数据。", !cleared);
});
$("file").addEventListener("change", () => { $("file-name").textContent = $("file").files[0]?.name || "选择 PDF 文件"; });
$("refresh").addEventListener("click", refresh);
$("discard").addEventListener("click", () => { pending = null; updateForm(); feedback("已放弃重试。原任务可能已提交，请先查看任务记录，再创建新任务。"); });
$("print-form").addEventListener("submit", async (event) => {
  event.preventDefault(); if (busy || !token) return;
  if (!pending) {
    const file = $("file").files[0];
    if (!file || !file.name.toLowerCase().endsWith(".pdf")) { feedback("请选择 PDF 文件。", true); return; }
    if (file.size > maxBytes) { feedback("文件超过服务允许的大小。", true); return; }
    // Random UUID generation also works over trusted-LAN HTTP without a secure context.
    const bytes = crypto.getRandomValues(new Uint8Array(16)); bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
    pending = {file, key:`${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`, query:new URLSearchParams({copies:$("copies").value,sides:$("sides").value,name:file.name})};
  }
  busy = true; updateForm(); feedback("正在上传并创建任务…");
  try {
    const job = await request(`/api/jobs?${pending.query}`, {method:"POST",headers:{"Content-Type":"application/pdf","Idempotency-Key":pending.key},body:pending.file});
    pending = null; $("print-form").reset(); $("file-name").textContent = "选择 PDF 文件";
    feedback(`任务已接收：${statuses[job.status] || job.status}。`); await refresh();
  } catch (error) { feedback(`${error.message || "网络连接中断。"} 提交结果可能未知，请查看任务记录；点击重试将沿用同一任务标识。`, true); }
  finally { busy = false; updateForm(); }
});

const rememberedToken = savedToken();
if (rememberedToken) {
  $("logout").hidden = false;
  feedback("正在自动连接…");
  connect(rememberedToken);
}
