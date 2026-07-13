/* GGB 引擎宿主：applet 生命周期 / 逐行执行判错 / 步骤播放器 / 导出。
 *
 * 判错基元（阶段0 实测）：
 * - 创建类命令（name=…）：evalCommandGetLabels 为空 且 exists(name)=false → 该行失败；
 * - 脚本类命令（SetColor 等）：evalCommand 成功也返回 false，布尔不可用 →
 *   失败靠后端静态 lint 提前拦，这里只负责执行；
 * - StartAnimation 行执行后补 JS API setAnimating+startAnimation 确保动画真的跑。
 */
window.GGBHost = (function () {
  let api = null;
  let readyCbs = [];
  let steps = [];          // [{idx,label,lines:[{n,text}]}]
  let curStep = -1;        // -1 = 未执行；steps.length-1 = 全部
  let lastScript = "";
  let backupState = null;  // 每次整体重绘前的 .ggb 快照（手动绘制内容的一步恢复）
  let toolbarOn = true;
  let activeSpace = "2d";
  let activeView2d = null; // 脚本请求的原始视窗；尺寸变化时据此重算等比例范围
  let resizeFrame = 0;
  let lineObjects = new Set();       // 需要在白底上保持深色可见的对象
  let polygonEdges = new Map();      // Polygon 标签 -> GeoGebra 自动生成的边标签

  const ASSIGN_RE = /^([A-Za-z][A-Za-z0-9_']*)\s*(?:\([a-zA-Z ,]*\))?\s*=/;
  const STEP_RE = /^#\s*step[_ ]?0*(\d+)\s*[:：|｜]?\s*(.*)$/i;
  const ANIM_RE = /^StartAnimation\(\s*([A-Za-z][A-Za-z0-9_]*)\s*\)/;
  const PERSPECTIVE_RE = /^#\s*perspective\s*:\s*(2d|3d)\s*$/i;
  const VIEW3D_RE = /^#\s*view3d\s*:\s*(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)/i;
  const CREATION_RE = /^\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*([A-Za-z][A-Za-z0-9_]*)\s*\(/;
  const LINE_STYLE_RE = /^\s*(SetColor|SetLineThickness|SetLineStyle)\s*\(\s*([A-Za-z][A-Za-z0-9_]*)\s*,\s*(.*?)\s*\)\s*$/i;
  const RGB_RE = /^(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)$/;
  const LINE_TYPES = new Set([
    "segment", "line", "ray", "vector", "polyline", "circle", "semicircle",
    "arc", "circulararc", "circularsector", "ellipse", "hyperbola", "parabola",
    "conic", "tangent", "perpendicularline", "orthogonalline",
    "perpendicularbisector", "linebisector", "anglebisector", "angularbisector"
  ]);
  const POLYGON_TYPES = new Set(["polygon", "regularpolygon"]);

  function _resetLineStyleState() {
    lineObjects = new Set();
    polygonEdges = new Map();
  }

  function _lineTargets(label) {
    return [label, ...(polygonEdges.get(label) || [])];
  }

  function _setObjectColor(label, red, green, blue) {
    try {
      if (typeof api.setColor === "function") api.setColor(label, red, green, blue);
      else api.evalCommand(`SetColor(${label},${red},${green},${blue})`);
    } catch (e) { }
  }

  function _setObjectLineStyle(command, label, value) {
    try {
      const numeric = Number(value);
      if (command === "setlinethickness" && typeof api.setLineThickness === "function") {
        api.setLineThickness(label, numeric);
      } else if (command === "setlinestyle" && typeof api.setLineStyle === "function") {
        api.setLineStyle(label, numeric);
      } else {
        api.evalCommand(`${command === "setlinethickness" ? "SetLineThickness" : "SetLineStyle"}(${label},${value})`);
      }
    } catch (e) { }
  }

  function _registerCreatedLine(s, label, command, beforeNames, labels) {
    const kind = command.toLowerCase();
    if (!LINE_TYPES.has(kind) && !POLYGON_TYPES.has(kind)) return;
    lineObjects.add(label);
    if (POLYGON_TYPES.has(kind)) {
      let candidates = String(labels || "").split(",").map(x => x.trim()).filter(Boolean);
      try {
        const after = api.getAllObjectNames() || [];
        candidates = candidates.concat(after.filter(name => !beforeNames.has(name)));
      } catch (e) { }
      const edges = [...new Set(candidates)].filter(name => {
        if (!name || name === label) return false;
        try { return String(api.getObjectType(name) || "").toLowerCase() === "segment"; }
        catch (e) { return false; }
      });
      // Polygon 默认用 a/b/c/... 命名边，后续 `c=Circle(...)` 会覆盖边并
      // 级联删除整个多边形。边是内部对象，创建后立即换到安全命名空间。
      const safeEdges = edges.map((edge, index) => {
        if (typeof api.renameObject !== "function") return edge;
        const stem = `a2g_${label}_edge${index + 1}`;
        let safe = stem, suffix = 2;
        try {
          while (api.exists(safe)) { safe = `${stem}_${suffix++}`; }
          return api.renameObject(edge, safe) === false ? edge : safe;
        } catch (e) { return edge; }
      });
      polygonEdges.set(label, safeEdges);
      safeEdges.forEach(edge => lineObjects.add(edge));
      (safeEdges.length ? safeEdges : [label]).forEach(edge => {
        _setObjectColor(edge, 35, 35, 35);
        _setObjectLineStyle("setlinethickness", edge, 3);
      });
      return;
    }
    _setObjectColor(label, 35, 35, 35);
    _setObjectLineStyle("setlinethickness", label, 3);
  }

  function _applyDeterministicLineStyle(s) {
    const style = s.match(LINE_STYLE_RE);
    if (!style || !lineObjects.has(style[2])) return false;
    const command = style[1].toLowerCase();
    const targets = _lineTargets(style[2]);
    if (command === "setcolor") {
      const rgb = style[3].match(RGB_RE);
      if (!rgb) return false;
      let [red, green, blue] = rgb.slice(1).map(Number);
      if (0.2126 * red + 0.7152 * green + 0.0722 * blue >= 180) {
        red = green = blue = 35;
      }
      targets.forEach(label => _setObjectColor(label, red, green, blue));
      return true;
    }
    targets.forEach(label => _setObjectLineStyle(command, label, style[3]));
    return true;
  }

  function _viewProperties2d() {
    if (!api || typeof api.getViewProperties !== "function") return null;
    try {
      const raw = api.getViewProperties(1);
      const props = typeof raw === "string" ? JSON.parse(raw) : raw;
      return props && Number(props.width) > 0 && Number(props.height) > 0 ? props : null;
    } catch (e) { return null; }
  }

  /* GeoGebra setCoordSystem 会把 x/y 范围分别铺满矩形视图，直接调用会把正方形拉成长方形。
   * 先应用题目范围，再根据真实 Graphics View 像素宽高扩展较短一边，
   * 保证原范围完整可见，且 x/y 的每单位像素数一致。 */
  function _setEqual2dView(view) {
    if (!api || !view || activeSpace !== "2d") return;
    const { xmin, xmax, ymin, ymax } = view;
    const spanX = xmax - xmin, spanY = ymax - ymin;
    if (!(spanX > 0 && spanY > 0)) return;
    try { api.setCoordSystem(xmin, xmax, ymin, ymax); } catch (e) { return; }
    const props = _viewProperties2d();
    if (!props) return;
    const width = Number(props.width), height = Number(props.height);
    const unitsPerPixel = Math.max(spanX / width, spanY / height);
    const fittedX = unitsPerPixel * width;
    const fittedY = unitsPerPixel * height;
    const centerX = (xmin + xmax) / 2;
    const centerY = (ymin + ymax) / 2;
    try {
      api.setCoordSystem(
        centerX - fittedX / 2, centerX + fittedX / 2,
        centerY - fittedY / 2, centerY + fittedY / 2
      );
    } catch (e) { }
  }

  function _syncSize(containerId) {
    if (!api) return;
    const box = document.getElementById(containerId);
    if (!box || box.clientWidth <= 100 || box.clientHeight <= 100) return;
    try { api.setSize(Math.round(box.clientWidth), Math.round(box.clientHeight)); } catch (e) { }
    if (activeView2d && activeSpace === "2d") _setEqual2dView(activeView2d);
  }

  function _scheduleSizeSync(containerId) {
    const raf = typeof window.requestAnimationFrame === "function"
      ? window.requestAnimationFrame.bind(window)
      : (cb => window.setTimeout(cb, 0));
    if (resizeFrame && typeof window.cancelAnimationFrame === "function") {
      window.cancelAnimationFrame(resizeFrame);
    }
    resizeFrame = raf(() => {
      resizeFrame = 0;
      _syncSize(containerId);
      // 工具栏收展时 Graphics View 比外层容器晚一帧完成布局。
      raf(() => {
        if (activeView2d && activeSpace === "2d") _setEqual2dView(activeView2d);
      });
    });
  }

  function init(containerId, onReady) {
    if (onReady) readyCbs.push(onReady);
    if (api) { readyCbs.splice(0).forEach(cb => cb(api)); return; }
    const el = document.getElementById(containerId);
    const w = Math.max(480, el.clientWidth || 760);
    const h = Math.max(360, el.clientHeight || 480);
    const applet = new GGBApplet({
      appName: "classic", width: w, height: h,
      showToolBar: true, showToolBarHelp: false,
      showAlgebraInput: false, showMenuBar: false,
      enableRightClick: true, showResetIcon: false, errorDialogsActive: false,
      language: "zh-CN", borderColor: "#2A2D3E",
      appletOnLoad: function (a) {
        api = a;
        _scheduleSizeSync(containerId);
        readyCbs.splice(0).forEach(cb => cb(api));
      },
    }, true);
    applet.setHTML5Codebase("vendor/ggb/GeoGebra/HTML5/5.0/web3d/");
    applet.inject(containerId);
    window.addEventListener("resize", () => _scheduleSizeSync(containerId));
    if (typeof window.ResizeObserver === "function") {
      const observer = new window.ResizeObserver(() => _scheduleSizeSync(containerId));
      observer.observe(el);
    }
  }

  function parseSteps(script) {
    const lines = script.split("\n");
    const out = [];
    let cur = null;
    lines.forEach((raw, i) => {
      const m = raw.trim().match(STEP_RE);
      if (m) {
        cur = { idx: parseInt(m[1], 10), label: m[2] || ("第" + m[1] + "步"), lines: [] };
        out.push(cur);
      } else {
        if (!cur) { cur = { idx: out.length + 1, label: "第" + (out.length + 1) + "步", lines: [] }; out.push(cur); }
        cur.lines.push({ n: i + 1, text: raw });
      }
    });
    return out.filter(s => s.lines.some(l => l.text.trim() && !l.text.trim().startsWith("#")));
  }

  const VIEW_RE = /^#\s*view\s*:\s*(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)/i;

  function scriptSpace(script) {
    const m = script.match(/^\s*#\s*perspective\s*:\s*(2d|3d)\s*$/im);
    return m ? m[1].toLowerCase() : "2d";
  }

  function setPerspective(space) {
    activeSpace = space === "3d" ? "3d" : "2d";
    if (!api) return;
    try { api.enable3D(true); } catch (e) { }
    try { api.setPerspective(activeSpace === "3d" ? "T" : "G"); } catch (e) { }
  }

  function _execLine(n, raw, failures) {
    const s = raw.trim();
    const pm = s.match(PERSPECTIVE_RE);
    if (pm) { setPerspective(pm[1].toLowerCase()); return; }
    const v3 = s.match(VIEW3D_RE);
    if (v3) {
      try { api.setCoordSystem(+v3[1], +v3[4], +v3[2], +v3[5], +v3[3], +v3[6], true); } catch (e) { }
      return;
    }
    const vm = s.match(VIEW_RE);   // 视窗指令：# view: xmin ymin xmax ymax
    if (vm) {                       // （ZoomIn 命令会让 LaTeX 文本消失，故走 JS API）
      activeView2d = { xmin: +vm[1], xmax: +vm[3], ymin: +vm[2], ymax: +vm[4] };
      _setEqual2dView(activeView2d);
      return;
    }
    if (!s || s.startsWith("#")) return;
    const asn = s.match(ASSIGN_RE);
    if (asn) {
      const creation = s.match(CREATION_RE);
      let beforeNames = new Set();
      if (creation && POLYGON_TYPES.has(creation[2].toLowerCase())) {
        try { beforeNames = new Set(api.getAllObjectNames() || []); } catch (e) { }
      }
      let labels = null;
      try { labels = api.evalCommandGetLabels(s); } catch (e) { labels = null; }
      if ((labels === null || labels === undefined || labels === "") && !api.exists(asn[1])) {
        failures.push({ line: n, cmd: s });
      }
      if (creation) _registerCreatedLine(s, creation[1], creation[2], beforeNames, labels);
      return;
    }
    if (_applyDeterministicLineStyle(s)) return;
    try { api.evalCommand(s); } catch (e) { failures.push({ line: n, cmd: s }); return; }
    const anim = s.match(ANIM_RE);
    if (anim) { try { api.setAnimating(anim[1], true); api.startAnimation(); } catch (e) { } }
  }

  /* 全量执行：返回 {ok, failures:[{line,cmd}], objects:[…]}
   * 重绘前自动快照当前画布（含手动绘制内容），可用 restoreBackup 找回。 */
  function execute(script) {
    if (!api) return { ok: false, failures: [{ line: 0, cmd: "(引擎未就绪)" }], objects: [] };
    try {
      const names = api.getAllObjectNames() || [];
      if (names.length) backupState = api.getBase64();
    } catch (e) { }
    lastScript = script;
    steps = parseSteps(script);
    activeView2d = null;
    try { api.newConstruction(); } catch (e) { }
    _resetLineStyleState();
    setPerspective(scriptSpace(script));
    const failures = [];
    script.split("\n").forEach((raw, i) => _execLine(i + 1, raw, failures));
    curStep = steps.length - 1;
    let objects = [];
    try { objects = api.getAllObjectNames() || []; } catch (e) { }
    return { ok: failures.length === 0, failures, objects };
  }

  /* 步骤播放：重放 0..k 段 */
  function stepTo(k) {
    if (!api || !steps.length) return curStep;
    k = Math.max(-1, Math.min(k, steps.length - 1));
    try { api.newConstruction(); } catch (e) { }
    _resetLineStyleState();
    setPerspective(activeSpace);
    const failures = [];
    for (let i = 0; i <= k; i++) steps[i].lines.forEach(l => _execLine(l.n, l.text, failures));
    curStep = k;
    return curStep;
  }

  function stepNext() { return stepTo(curStep + 1); }
  function stepPrev() { return stepTo(curStep - 1); }
  function stepInfo() {
    return { cur: curStep, total: steps.length,
             label: curStep >= 0 && steps[curStep] ? steps[curStep].label : "" };
  }

  /* withCoords=false（默认）导出时临时隐藏坐标轴+网格，导出后恢复。 */
  function exportPNG(scale, transparent, dpi, withCoords) {
    if (!api) return "";
    let restore = null;
    if (withCoords === false || withCoords === undefined) {
      let prevGrid = true;
      const view = activeSpace === "3d" ? 3 : 1;
      try { if (typeof api.getGridVisible === "function") prevGrid = api.getGridVisible(view); } catch (e) { }
      try {
        if (activeSpace === "3d") api.setAxesVisible(3, false, false, false);
        else api.setAxesVisible(false, false);
        api.setGridVisible(view, false);
      } catch (e) { }
      restore = () => { try {
        if (activeSpace === "3d") api.setAxesVisible(3, true, true, true);
        else api.setAxesVisible(true, true);
        api.setGridVisible(view, prevGrid);
      } catch (e) { } };
    }
    try {
      return api.getPNGBase64(scale || 2, transparent !== false, dpi || 300);
    } catch (e) {
      return "";
    } finally {
      if (restore) restore();
    }
  }
  function exportGGB() { try { return api ? api.getBase64() : "" } catch (e) { return ""; } }
  function _escapeHTML(value) {
    return String(value || "").replace(/[&<>"']/g, ch => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[ch]);
  }
  function exportInteractiveHTML(title) {
    const base64 = exportGGB();
    if (!base64) return "";
    const safeTitle = _escapeHTML(title || "Any2GGB 互动图形");
    const appName = activeSpace === "3d" ? "3d" : "classic";
    return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>${safeTitle}</title>
  <script src="https://www.geogebra.org/apps/deployggb.js"><\/script>
  <style>
    html,body,#ggb{width:100%;height:100%;margin:0;overflow:hidden;background:#fff}
    .note{position:fixed;inset:auto 12px 12px;z-index:3;padding:7px 10px;border-radius:8px;
      background:rgba(20,22,31,.82);color:#fff;font:12px/1.4 system-ui,sans-serif}
  </style>
</head>
<body>
  <div id="ggb"></div>
  <div class="note" id="note">由 Any2GGB 导出 · 可拖动图形并使用工具栏继续探索</div>
  <script>
    const applet = new GGBApplet({
      id: "ggbApplet", appName: ${JSON.stringify(appName)},
      ggbBase64: ${JSON.stringify(base64)},
      width: Math.max(320, innerWidth), height: Math.max(320, innerHeight),
      showToolBar: true, showMenuBar: false, showAlgebraInput: false,
      showZoomButtons: true, enableRightClick: true, language: "zh-CN"
    }, true);
    applet.inject("ggb");
    addEventListener("resize", () => {
      try { window.ggbApplet.setSize(Math.max(320,innerWidth),Math.max(320,innerHeight)); } catch (e) {}
    });
    setTimeout(() => document.getElementById("note")?.remove(), 4200);
  <\/script>
</body>
</html>`;
  }
  function ready() { return !!api; }

  function toggleToolbar() {
    if (!api) return toolbarOn;
    toolbarOn = !toolbarOn;
    try { api.showToolBar(toolbarOn); } catch (e) { }
    _scheduleSizeSync("ggbApplet");
    return toolbarOn;
  }
  function hasBackup() { return !!backupState; }
  function restoreBackup() {
    if (!api || !backupState) return false;
    try { api.setBase64(backupState); backupState = null; return true; } catch (e) { return false; }
  }

  /* 清空画布（新建/切换空项目用）：不产生 backup，重置步骤状态。 */
  function clear() {
    if (api) { try { api.newConstruction(); } catch (e) { } }
    _resetLineStyleState();
    setPerspective("2d");
    steps = []; curStep = -1; lastScript = ""; backupState = null; activeView2d = null;
  }

  return { init, execute, stepTo, stepNext, stepPrev, stepInfo, parseSteps,
           exportPNG, exportGGB, exportInteractiveHTML, ready, toggleToolbar,
           hasBackup, restoreBackup, clear };
})();
