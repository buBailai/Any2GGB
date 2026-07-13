/* Any2GGB 前端主逻辑：项目/对话/SSE/自愈验证握手/步骤播放/导出/设置。 */
"use strict";
const $ = s => document.querySelector(s);
const api = {
  get: (u) => fetch(u).then(r => r.json()),
  post: (u, body) => fetch(u, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) }).then(r => r.json()),
  del: (u) => fetch(u, { method: "DELETE" }).then(r => r.json()),
};

let PID = null;          // 当前项目
let SSE = null;
let PROVIDERS = [];
let ggbReady = false;
let pendingScript = null;   // 引擎未就绪时暂存待执行脚本 {seq, script}
let attachments = [];       // 待发送附件 [{name, kind:'image'|'text', data, previewUrl}]
let GENERATION_MODES = [];
let GENERATION_SPACES = [];
let selectedMode = "figure";
let selectedSpace = "2d";
let selectedInteractive = false;
let generationBusy = false;

/* ── 状态与对话渲染 ── */
function setStatus(kind, text) {
  const el = $("#statusBar");
  el.className = "status" + (kind === "spin" ? " spin" : "");
  el.textContent = text;
}
function addMsg(role, text) {
  const d = document.createElement("div");
  d.className = "msg " + role;
  d.textContent = text;
  $("#chatBox").appendChild(d);
  $("#chatBox").scrollTop = 1e9;
}
function setGenerationBusy(busy) {
  generationBusy = !!busy;
  $("#sendBtn").disabled = generationBusy;
  $("#stopBtn").hidden = !generationBusy;
  $("#stopBtn").disabled = false;
}

/* ── 项目 ── */
async function loadProjects(selectPid) {
  const list = await api.get("/api/projects");
  const sel = $("#projSelect");
  sel.innerHTML = "";
  list.forEach(p => {
    const o = document.createElement("option");
    o.value = p.id; o.textContent = p.title;
    sel.appendChild(o);
  });
  if (!list.length) { await newProject("我的第一张配图"); return; }
  const pid = selectPid && list.some(p => p.id === selectPid) ? selectPid : list[0].id;
  sel.value = pid;
  await openProject(pid);
}
async function newProject(title) {
  const p = await api.post("/api/projects", { title: title || ("配图 " + new Date().toLocaleDateString("zh-CN")) });
  await loadProjects(p.id);
}
async function openProject(pid) {
  PID = pid;
  attachments = []; renderAttachBar();
  pendingScript = null;
  $("#chatBox").innerHTML = "";
  $("#scriptEditor").value = "";
  $("#planView").textContent = "（生成后显示绘图方案）";
  const d = await api.get("/api/projects/" + pid);
  (d.messages || []).forEach(m => addMsg(m.role, m.content));
  renderTimeline(d.versions || [], d.project.current_version);
  connectSSE(pid);
  const cur = d.project.current_version;
  if (cur) {
    const v = await api.get(`/api/projects/${pid}/version/${cur}`);
    if (v.script) loadVersionIntoWorkspace(v);
  } else if (ggbReady) {          // 新建/无版本项目：清空预览画布，别留上个项目的图
    GGBHost.clear();
    refreshStepbar();
    refreshRestoreBtn();
  }
  setGenerationBusy(Boolean(d.generating || d.has_pending));
  setStatus(d.generating || d.has_pending ? "spin" : "",
    d.generating ? "该项目仍在后台生成，可点“停止”中断"
      : (d.has_pending ? "检测到未完成的生成记录，可点“停止”清理" : "就绪"));
}
function loadVersionIntoWorkspace(v) {
  $("#scriptEditor").value = v.script || "";
  $("#planView").textContent = v.plan || "（无方案记录）";
  if (ggbReady && v.script) { GGBHost.execute(v.script); refreshStepbar(); refreshRestoreBtn(); }
  else if (v.script) pendingScript = { seq: null, script: v.script };
}

/* ── 版本时间线 ── */
function renderTimeline(versions, current) {
  const box = $("#timeline");
  box.innerHTML = "";
  versions.forEach(v => {
    const b = document.createElement("button");
    b.className = "vchip " + v.status + (v.seq === current ? " current" : "");
    b.textContent = `v${v.seq}` + (v.status === "failed" ? " ✗" : (v.status === "cancelled" ? " ■" : ""));
    b.title = (v.prompt || "").slice(0, 60);
    b.onclick = async () => {
      const full = await api.get(`/api/projects/${PID}/version/${v.seq}`);
      if (!full.script) { setStatus("", "该版本没有脚本"); return; }
      loadVersionIntoWorkspace(full);
      await api.post(`/api/projects/${PID}/revert`, { seq: v.seq });
      const d = await api.get("/api/projects/" + PID);
      renderTimeline(d.versions, v.seq);
      setStatus("", `已回到第 ${v.seq} 版`);
    };
    box.appendChild(b);
  });
  box.scrollLeft = 1e9;
}

/* ── SSE 与生成回路 ── */
function connectSSE(pid) {
  if (SSE) SSE.close();
  SSE = new EventSource("/api/events/" + pid);
  SSE.onmessage = (e) => {
    let ev; try { ev = JSON.parse(e.data); } catch { return; }
    handleEvent(ev);
  };
}
async function handleEvent(ev) {
  switch (ev.type) {
    case "version_start":
      setGenerationBusy(true);
      setStatus("spin", ev.has_images ? "读取参考图并生成中…"
        : (ev.demo ? "生成中…（演示模式，无需 Key）" : "生成中…"));
      break;
    case "notice":
      addMsg("system", ev.text || "");
      break;
    case "cache_hit":
      setStatus("spin", "找到完全一致的历史成品，正在重新验证…");
      addMsg("system", "已复用本机历史生成结果，不再调用模型；如需不同方案，请在需求中写“重新生成”。");
      break;
    case "editing": setStatus("spin", "定向修改中…"); break;
    case "edited": setStatus("spin", `已应用 ${ev.applied} 处修改，正在验证…`); break;
    case "regenerating": setStatus("spin", "整体重新生成中…"); break;
    case "planning": setStatus("spin", "正在做教学设计…"); break;
    case "plan_ready":
      $("#planView").textContent = ev.text || "";
      addMsg("system", "—— 绘图方案 ——\n" + (ev.text || ""));
      break;
    case "generating": setStatus("spin", "正在编写 GGB 脚本…"); break;
    case "healing": setStatus("spin", `自动修正中…（第 ${ev.round} 轮 · ${ev.reason || ""}）`); break;
    case "script_ready":
      $("#scriptEditor").value = ev.script || "";
      await execAndVerify(ev.seq, ev.script || "");
      break;
    case "version_done":
      setGenerationBusy(false);
      setStatus("", "✅ 图形已生成，可用工具栏继续手动加工，满意后导出/复制图片");
      addMsg("assistant", "图形已生成 ✅ 不满意的细节可以继续用文字提修改，也可以直接用画布上方的工具栏手动加工，最后「导出 PNG」或「复制图片」插进题目里");
      refreshProjectMeta(false);
      break;
    case "version_failed":
      setGenerationBusy(false);
      setStatus("", "生成失败：" + (ev.error || ""));
      addMsg("assistant", "生成失败：" + (ev.error || "") + "\n（脚本已保留在「脚本」tab，可手动调整后重新执行）");
      if (ev.script) $("#scriptEditor").value = ev.script;
      refreshProjectMeta(false);
      break;
    case "version_cancelled":
      setGenerationBusy(false);
      setStatus("", "已停止当前生成");
      addMsg("system", `已手动停止第 ${ev.seq} 版生成`);
      refreshProjectMeta(false);
      break;
  }
}
async function refreshProjectMeta(syncBusy = true) {
  const d = await api.get("/api/projects/" + PID);
  renderTimeline(d.versions || [], d.project.current_version);
  if (syncBusy) setGenerationBusy(Boolean(d.generating || d.has_pending));
}

/* 前端执行 + 回报（自愈回路的另一半） */
async function execAndVerify(seq, script) {
  if (!ggbReady) { pendingScript = { seq, script }; return; }
  setStatus("spin", "正在画布上执行脚本…");
  const r = GGBHost.execute(script);
  refreshStepbar();
  refreshRestoreBtn();
  const payload = { seq, ok: r.ok, failures: r.failures, objects: r.objects };
  if (r.ok) {
    await new Promise(res => setTimeout(res, 400));   // 等 LaTeX/字体排版完成再截图
    payload.png_base64 = GGBHost.exportPNG(1, true, 72);   // 缩略图用小尺寸
    payload.ggb_base64 = GGBHost.exportGGB();
  }
  await api.post(`/api/projects/${PID}/verify`, payload);
  if (!r.ok) setStatus("spin", `有 ${r.failures.length} 行执行失败，等待自动修正…`);
}

/* ── 附件（参考图/文件）── */
const IMG_MAX = 1568;   // 视觉模型常见上限，超过则等比缩小以控制 token
function readImageResized(file) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      let { width: w, height: h } = img;
      const scale = Math.min(1, IMG_MAX / Math.max(w, h));
      w = Math.round(w * scale); h = Math.round(h * scale);
      const c = document.createElement("canvas");
      c.width = w; c.height = h;
      c.getContext("2d").drawImage(img, 0, 0, w, h);
      resolve(c.toDataURL("image/png"));
    };
    img.onerror = () => resolve(null);
    const fr = new FileReader();
    fr.onload = () => { img.src = fr.result; };
    fr.readAsDataURL(file);
  });
}
function readText(file) {
  return new Promise((resolve) => {
    const fr = new FileReader();
    fr.onload = () => resolve(fr.result);
    fr.onerror = () => resolve(null);
    fr.readAsText(file);
  });
}
async function addFiles(fileList) {
  for (const f of fileList) {
    if (attachments.length >= 6) { setStatus("", "最多 6 个附件"); break; }
    if (f.type.startsWith("image/")) {
      const url = await readImageResized(f);
      if (url) attachments.push({ name: f.name, kind: "image", data: url, previewUrl: url });
    } else {
      const txt = await readText(f);
      if (txt != null) attachments.push({ name: f.name, kind: "text", data: txt, previewUrl: "" });
      else setStatus("", `无法读取 ${f.name}（仅支持图片与文本文件）`);
    }
  }
  renderAttachBar();
}
function renderAttachBar() {
  const box = $("#attachBar");
  box.innerHTML = "";
  attachments.forEach((a, i) => {
    const chip = document.createElement("div");
    chip.className = "att-chip";
    chip.innerHTML = a.kind === "image"
      ? `<img src="${a.previewUrl}" alt=""><span>${a.name}</span>`
      : `<span class="att-ico">📄</span><span>${a.name}</span>`;
    const x = document.createElement("button");
    x.className = "att-x"; x.textContent = "×"; x.title = "移除";
    x.onclick = () => { attachments.splice(i, 1); renderAttachBar(); };
    chip.appendChild(x);
    box.appendChild(chip);
  });
}

/* ── 发送 ── */
async function send() {
  const t = $("#promptInput").value.trim();
  if ((!t && !attachments.length) || !PID || generationBusy) return;
  $("#promptInput").value = "";
  const atts = attachments.slice();
  attachments = []; renderAttachBar();
  let shown = t;
  if (atts.length) shown += `　［附 ${atts.filter(a => a.kind === "image").length} 图 / ${atts.filter(a => a.kind === "text").length} 文件］`;
  addMsg("user", shown.trim() || "（仅附件）");
  const r = await api.post(`/api/projects/${PID}/message`,
    { text: t, mode: selectedMode, space: selectedSpace, interactive: selectedInteractive,
      attachments: atts.map(a => ({ name: a.name, kind: a.kind, data: a.data })) });
  if (r.detail) { setStatus("", r.detail); addMsg("system", r.detail); }
  else if (r.ok) setGenerationBusy(true);
}

async function stopGeneration() {
  if (!PID || !generationBusy) return;
  const btn = $("#stopBtn");
  btn.disabled = true;
  setStatus("spin", "正在停止生成…");
  const r = await api.post(`/api/projects/${PID}/cancel`);
  if (r.detail) {
    btn.disabled = false;
    setStatus("", r.detail);
    return;
  }
  setGenerationBusy(false);
  await refreshProjectMeta();
  setStatus("", r.cancelled ? "已停止当前生成" : "当前没有正在进行的生成");
}

function renderGenerationChoices() {
  const render = (box, items, active, onPick) => {
    box.innerHTML = "";
    items.forEach(item => {
      const b = document.createElement("button");
      b.className = "choice" + (item.key === active ? " active" : "");
      b.textContent = item.title;
      b.title = item.description || "";
      b.onclick = () => onPick(item.key);
      box.appendChild(b);
    });
  };
  render($("#modeChoices"), GENERATION_MODES, selectedMode, key => {
    selectedMode = key;
    renderGenerationChoices();
  });
  render($("#spaceChoices"), GENERATION_SPACES, selectedSpace, key => {
    selectedSpace = key;
    renderGenerationChoices();
  });
  const mode = GENERATION_MODES.find(x => x.key === selectedMode);
  if (mode) {
    $("#modeHint").textContent = mode.description || "";
    $("#promptInput").placeholder = mode.placeholder || $("#promptInput").placeholder;
  }
}

async function saveManualVersion() {
  const btn = $("#saveScriptBtn");
  const script = $("#scriptEditor").value;
  const r = GGBHost.execute(script);
  refreshStepbar(); refreshRestoreBtn();
  if (!r.ok) {
    setStatus("", `无法保存：有 ${r.failures.length} 行执行失败（` +
      r.failures.slice(0, 3).map(f => "L" + f.line).join(", ") + "）");
    return;
  }
  btn.disabled = true;
  setStatus("spin", "正在保存手动修改…");
  await new Promise(res => setTimeout(res, 350));
  const planText = $("#planView").textContent.startsWith("（") ? "" : $("#planView").textContent;
  const saved = await api.post(`/api/projects/${PID}/manual-version`, {
    script, plan: planText,
    png_base64: GGBHost.exportPNG(1, true, 72),
    ggb_base64: GGBHost.exportGGB(),
  });
  btn.disabled = false;
  if (saved.detail) { setStatus("", saved.detail); return; }
  addMsg("system", `已保存手动修改为第 ${saved.seq} 版`);
  await refreshProjectMeta();
  setStatus("", `✅ 手动修改已保存为第 ${saved.seq} 版`);
}

/* ── 画布恢复（找回被 AI 重绘覆盖的手动内容） ── */
function refreshRestoreBtn() {
  $("#restoreBtn").hidden = !GGBHost.hasBackup();
}

/* ── 步骤播放器 ── */
function refreshStepbar() {
  const info = GGBHost.stepInfo();
  $("#stepLabel").textContent = info.total
    ? `步骤 ${info.cur + 1}/${info.total}` + (info.label ? `：${info.label}` : "")
    : "未加载";
  $("#stepPrevBtn").disabled = info.cur < 0;
  $("#stepNextBtn").disabled = info.cur >= info.total - 1;
}

/* ── 导出 ── */
function downloadB64(b64, name, mime) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([bytes], { type: mime }));
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}
function downloadText(text, name, mime) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: mime || "text/plain;charset=utf-8" }));
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}
function projTitle() {
  const sel = $("#projSelect");
  return (sel.options[sel.selectedIndex]?.textContent || "配图").replace(/[\\/:*?"<>|]/g, "_");
}
async function copyPNGToClipboard() {
  const b64 = GGBHost.exportPNG(2, $("#pngTransparent").checked, 300, $("#pngWithCoords").checked);
  if (!b64) { setStatus("", "画布还没有内容"); return; }
  try {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    const blob = new Blob([bytes], { type: "image/png" });
    await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
    setStatus("", "✅ 已复制到剪贴板，去 Word/PPT 里直接粘贴");
  } catch (e) {
    setStatus("", "复制失败（浏览器限制），请用「导出 PNG」");
  }
}

/* ── 设置 ── */
async function openSettings() {
  $("#settingsModal").hidden = false;
  if (!PROVIDERS.length) PROVIDERS = await api.get("/api/providers");
  const sel = $("#cfgProvider");
  sel.innerHTML = "";
  PROVIDERS.forEach(p => {
    const o = document.createElement("option");
    o.value = p.key; o.textContent = p.label;
    sel.appendChild(o);
  });
  const c = await api.get("/api/config");
  if (c.active) sel.value = c.active;
  fillProviderDefaults(c);
  refreshUpdatePanel();
}
function fillProviderDefaults(saved) {
  const p = PROVIDERS.find(x => x.key === $("#cfgProvider").value) || {};
  const savedP = saved && saved.providers ? saved.providers[$("#cfgProvider").value] : null;
  $("#cfgBase").value = (savedP && savedP.base_url) || p.base_url || "";
  $("#cfgModel").value = (savedP && savedP.model) || p.default_model || "";
  $("#cfgKey").value = "";
  $("#cfgKey").placeholder = savedP && savedP.has_key ? "已保存 Key（留空=不改）" : (p.needs_key === false ? "该厂商无需 Key" : "sk-…");
  $("#cfgMsg").textContent = p.hint || "";
  $("#cfgMsg").className = "cfg-msg";
}
async function saveConfig() {
  const body = { provider: $("#cfgProvider").value, base_url: $("#cfgBase").value, api_key: $("#cfgKey").value, model: $("#cfgModel").value };
  const r = await api.post("/api/config", body);
  $("#cfgMsg").textContent = r.demo ? "已保存，但配置不完整（仍为演示模式）" : "已保存并启用 ✅";
  $("#cfgMsg").className = "cfg-msg " + (r.demo ? "err" : "ok");
  $("#demoChip").hidden = !r.demo;
}
async function testConfig() {
  $("#cfgMsg").textContent = "测试中…"; $("#cfgMsg").className = "cfg-msg";
  const body = { provider: $("#cfgProvider").value, base_url: $("#cfgBase").value, api_key: $("#cfgKey").value, model: $("#cfgModel").value };
  const r = await api.post("/api/config/test", body);
  $("#cfgMsg").textContent = (r.ok ? "✅ " : "✗ ") + r.msg;
  $("#cfgMsg").className = "cfg-msg " + (r.ok ? "ok" : "err");
}

/* ── 在线更新 ── */
async function refreshUpdatePanel() {
  const s = await api.get("/api/update/status");
  $("#upStatus").textContent = `当前版本 v${s.version}` + (s.portable ? "（免安装包）" : "（开发模式）");
  $("#upSource").value = s.update_url || "";
  if (s.using_default_update_url) $("#upSource").placeholder = "已内置官方更新源，可不填";
  $("#upApplyBtn").hidden = !s.pending;
}
async function upCheck() {
  const src = $("#upSource").value.trim();
  await api.post("/api/update/source", { update_url: src });
  $("#upMsg").textContent = "检查中…"; $("#upMsg").className = "cfg-msg";
  const r = await api.post("/api/update/check");
  if (!r.ok) { $("#upMsg").textContent = r.msg; $("#upMsg").className = "cfg-msg err"; return; }
  if (!r.newer) { $("#upMsg").textContent = `已是最新版（v${r.current}）`; $("#upMsg").className = "cfg-msg ok"; return; }
  $("#upMsg").textContent = `发现新版 v${r.latest}：${r.notes || ""}`;
  $("#upMsg").className = "cfg-msg ok";
  $("#upDownloadBtn").hidden = false;
}
async function upDownload() {
  await api.post("/api/update/download");
  $("#upDownloadBtn").disabled = true;
  const timer = setInterval(async () => {
    const p = await api.get("/api/update/progress");
    $("#upMsg").textContent = `${p.msg}（${p.pct}%）`;
    if (p.state === "ready") { clearInterval(timer); $("#upApplyBtn").hidden = false; $("#upDownloadBtn").disabled = false; }
    if (p.state === "error") { clearInterval(timer); $("#upMsg").className = "cfg-msg err"; $("#upDownloadBtn").disabled = false; }
  }, 800);
}
async function upApply() {
  const r = await api.post("/api/update/apply");
  $("#upMsg").textContent = r.msg;
  $("#upMsg").className = "cfg-msg " + (r.ok ? "ok" : "err");
  if (r.ok) setTimeout(() => location.reload(), 12000);
}

/* ── 更新日志 ── */
async function openChangelog() {
  const d = await api.get("/api/changelog");
  $("#changelogBody").textContent = d.markdown || "（暂无）";
  $("#changelogModal").hidden = false;
}

/* ── 初始化 ── */
async function boot() {
  const cl = await api.get("/api/changelog");
  $("#verChip").textContent = "v" + cl.version;
  const cfg = await api.get("/api/config");
  $("#demoChip").hidden = !cfg.demo;

  const modeData = await api.get("/api/modes");
  GENERATION_MODES = modeData.modes || [];
  GENERATION_SPACES = modeData.spaces || [];
  selectedMode = modeData.default_mode || "figure";
  selectedSpace = modeData.default_space || "2d";
  selectedInteractive = modeData.default_interactive === true;
  $("#interactiveToggle").checked = selectedInteractive;
  $("#interactionHint").textContent = selectedInteractive ? "按需求设计控件" : "默认纯画图";
  renderGenerationChoices();

  GGBHost.init("ggbApplet", () => {
    ggbReady = true;
    if (pendingScript) { const p = pendingScript; pendingScript = null;
      if (p.seq != null) execAndVerify(p.seq, p.script);
      else { GGBHost.execute(p.script); refreshStepbar(); } }
  });

  await loadProjects();

  // 事件绑定
  $("#sendBtn").onclick = send;
  $("#stopBtn").onclick = stopGeneration;
  $("#interactiveToggle").onchange = e => {
    selectedInteractive = e.target.checked;
    $("#interactionHint").textContent = selectedInteractive ? "按需求设计控件" : "默认纯画图";
  };
  $("#promptInput").addEventListener("keydown", e => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send();
  });
  $("#attachBtn").onclick = () => $("#fileInput").click();
  $("#fileInput").onchange = async (e) => {
    await addFiles([...e.target.files]);
    e.target.value = "";   // 允许重复选同一文件
  };
  // 粘贴图片直接作为参考图（老师常复制截图）
  $("#promptInput").addEventListener("paste", async (e) => {
    const imgs = [...(e.clipboardData?.items || [])].filter(it => it.type.startsWith("image/"));
    if (!imgs.length) return;
    e.preventDefault();
    await addFiles(imgs.map(it => it.getAsFile()).filter(Boolean));
  });
  $("#newProjBtn").onclick = () => {
    const t = prompt("新配图项目名称：", "未命名配图");
    if (t !== null) newProject(t.trim() || "未命名配图");
  };
  $("#projSelect").onchange = e => openProject(e.target.value);

  document.querySelectorAll(".tab").forEach(t => t.onclick = () => {
    document.querySelectorAll(".tab").forEach(x => x.classList.toggle("active", x === t));
    document.querySelectorAll(".pane").forEach(p => p.classList.toggle("active", p.id === "pane-" + t.dataset.tab));
  });

  $("#stepPrevBtn").onclick = () => { GGBHost.stepPrev(); refreshStepbar(); };
  $("#stepNextBtn").onclick = () => { GGBHost.stepNext(); refreshStepbar(); };
  $("#stepAllBtn").onclick = () => { GGBHost.execute($("#scriptEditor").value); refreshStepbar(); refreshRestoreBtn(); };
  $("#runScriptBtn").onclick = () => {
    const r = GGBHost.execute($("#scriptEditor").value);
    refreshStepbar();
    refreshRestoreBtn();
    setStatus("", r.ok ? "✅ 执行成功" : `有 ${r.failures.length} 行执行失败：` +
      r.failures.slice(0, 3).map(f => "L" + f.line).join(", "));
    document.querySelector('[data-tab="preview"]').click();
  };
  $("#saveScriptBtn").onclick = saveManualVersion;

  $("#exportGGBBtn").onclick = () => {
    const b64 = GGBHost.exportGGB();
    if (b64) downloadB64(b64, projTitle() + ".ggb", "application/vnd.geogebra.file");
  };
  $("#exportHTMLBtn").onclick = () => {
    const html = GGBHost.exportInteractiveHTML(projTitle());
    if (!html) { setStatus("", "画布还没有内容"); return; }
    downloadText(html, projTitle() + "-互动版.html", "text/html;charset=utf-8");
    setStatus("", "✅ 已导出互动网页（打开时需要联网加载 GeoGebra）");
  };
  $("#exportPNGBtn").onclick = () => {
    const b64 = GGBHost.exportPNG(2, $("#pngTransparent").checked, 300, $("#pngWithCoords").checked);
    if (b64) downloadB64(b64, projTitle() + ".png", "image/png");
  };
  $("#copyPNGBtn").onclick = copyPNGToClipboard;
  $("#toolbarBtn").onclick = () => {
    const on = GGBHost.toggleToolbar();
    setStatus("", on ? "已显示绘图工具栏" : "已隐藏绘图工具栏");
  };
  $("#restoreBtn").onclick = () => {
    if (GGBHost.restoreBackup()) setStatus("", "✅ 已恢复重绘前的画布");
    refreshRestoreBtn();
  };

  $("#settingsBtn").onclick = openSettings;
  $("#cfgCloseBtn").onclick = () => $("#settingsModal").hidden = true;
  $("#cfgProvider").onchange = async () => fillProviderDefaults(await api.get("/api/config"));
  $("#cfgSaveBtn").onclick = saveConfig;
  $("#cfgTestBtn").onclick = testConfig;
  $("#upCheckBtn").onclick = upCheck;
  $("#upDownloadBtn").onclick = upDownload;
  $("#upApplyBtn").onclick = upApply;

  $("#changelogBtn").onclick = openChangelog;
  $("#changelogCloseBtn").onclick = () => $("#changelogModal").hidden = true;

  document.querySelectorAll(".modal").forEach(m => m.addEventListener("click", e => {
    if (e.target === m) m.hidden = true;
  }));
}
boot();
