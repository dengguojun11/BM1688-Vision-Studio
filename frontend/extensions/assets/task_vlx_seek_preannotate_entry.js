/* VLX-Seek 图片预标注入口，复用任务页现有的 AI/LA 工具栏位置。 */
(function () {
  var API = "/api/v1", BTN = "labelu-vlx-seek-preannotate-entry", MODAL = "labelu-vlx-seek-modal";
  var MENU = "labelu-preannotate-menu", MENU_CSS = "labelu-preannotate-menu-css";
  var modalState = { provider: "vlx", taskId: null, sampleId: null, labels: [] };
  var capabilityState = { taskId: null, sampleId: null, items: null, signature: "", failed: false, loading: false };
  function providerConfig(provider) {
    return provider === "rex"
      ? { title: "Rex-Omni 图片预标注", short: "Rex-Omni", route: "rex_omni_preannotate_job", model: "Rex-Omni · 3B AWQ" }
      : { title: "VLX-Seek 图片预标注", short: "VLX-Seek", route: "vlx_seek_preannotate_job", model: "VLX-Seek 1.5 · 10B" };
  }
  function token() { return localStorage.getItem("token") || localStorage.getItem("labelu_token") || ""; }
  function auth() { return token() ? { Authorization: token() } : {}; }
  function taskId() { var m = location.pathname.match(/tasks\/(\d+)/) || location.search.match(/[?&]task_id=(\d+)/); return m ? m[1] : null; }
  function sampleId() { var m = location.pathname.match(/samples\/(\d+)/) || location.search.match(/[?&]sample_id=(\d+)/); return m ? m[1] : null; }
  function capabilityKey(action) { return { ai: ["ai_global_tag", "vision_llm", "ai", "auto_label"], ai_detection: ["ai_bbox_detection"], yolo: ["yolo", "yolo_detection"], tagtool: ["tagtool"], locateanything: ["locateanything"], vlx: ["vlx_seek", "vlx"], rex: ["rex_omni", "rex"] }[action] || []; }
  function capabilityItem(action) {
    if (!Array.isArray(capabilityState.items)) return null;
    var keys = capabilityKey(action);
    return capabilityState.items.find(function (item) { return item && keys.indexOf(String(item.key || item.provider_key || "").toLowerCase()) >= 0; }) || null;
  }
  function capabilityVisible(action) {
    var item = capabilityItem(action);
    // The capability endpoint is the source of truth.  While it is loading or
    // unavailable, keep only the local JSON/JSONL upload entry; do not expose
    // a remote provider that may not be configured or reachable.
    if (!Array.isArray(capabilityState.items)) return false;
    if (!item) return false;
    return item.visible !== false && item.available !== false && item.supports_sample !== false;
  }
  function capabilitySignature() {
    if (!Array.isArray(capabilityState.items)) return "loading";
    return capabilityState.items.map(function (item) { return [item.key, item.visible, item.available, item.supports_sample].join("/"); }).sort().join(",");
  }
  function loadCapabilities() {
    var tid = taskId(), sid = sampleId();
    if (!tid || (capabilityState.taskId === tid && capabilityState.sampleId === sid && (capabilityState.items !== null || capabilityState.failed || capabilityState.loading))) return;
    capabilityState = { taskId: tid, sampleId: sid, items: null, signature: "", failed: false, loading: true };
    var url = API + "/tasks/" + tid + "/preannotation-capabilities" + (sid ? "?sample_id=" + encodeURIComponent(sid) : "");
    fetch(url, { headers: auth(), cache: "no-store" })
      .then(function (response) { return response.json().then(function (payload) { if (!response.ok) throw Error(payload.msg || response.statusText); return payload; }); })
      .then(function (payload) { var data = payload && payload.data ? payload.data : payload; capabilityState.items = Array.isArray(data && data.items) ? data.items : []; capabilityState.loading = false; capabilityState.signature = capabilitySignature(); inject(); })
      .catch(function () { capabilityState.items = null; capabilityState.loading = false; capabilityState.failed = true; capabilityState.signature = "fallback"; inject(); });
  }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>\"']/g, function (c) { return ({"&":"&amp;","<":"&lt;",">":"&gt;",'\"':"&quot;", "'":"&#39;"})[c]; }); }
  function parseTaskConfig(raw) {
    if (raw && typeof raw === "object") return raw;
    try { return JSON.parse(raw || "{}"); } catch (_) { return {}; }
  }
  function taskRectLabels(raw) {
    var config = parseTaskConfig(raw), labels = [];
    (config.tools || []).forEach(function (tool) {
      if (!tool || tool.tool !== "rectTool") return;
      (((tool.config || {}).attributes) || []).forEach(function (item) {
        if (!item || !(item.value || item.key)) return;
        labels.push({ value: String(item.value || item.key), key: String(item.key || item.value) });
      });
    });
    var pool = config.consistent_pool;
    var classNames = pool && typeof pool === "object" && Array.isArray(pool.class_names) ? pool.class_names : [];
    if (!classNames.length) return labels;
    var wanted = {};
    classNames.forEach(function (value) {
      value = String(value == null ? "" : value).trim();
      if (value) wanted[value] = true;
    });
    var scoped = labels.filter(function (item) { return wanted[item.value] || wanted[item.key]; });
    return scoped.length ? scoped : labels;
  }
  function selectedLabels() {
    return Array.from(document.querySelectorAll("#vlx-target-labels input:checked")).map(function (input) { return input.value; });
  }
  function updateTargetDescription() {
    var selected = {};
    selectedLabels().forEach(function (value) { selected[value] = true; });
    var descriptions = modalState.labels.filter(function (item) { return selected[item.value]; }).map(function (item) { return item.key; });
    var input = document.getElementById("vlx-target-description");
    if (input) input.value = descriptions.join("；");
  }
  function renderTargetLabels() {
    var host = document.getElementById("vlx-target-labels");
    if (!host) return;
    if (!modalState.labels.length) {
      host.innerHTML = '<span style="color:#cf1322">当前任务没有可用的拉框标签</span>';
      updateTargetDescription();
      return;
    }
    host.innerHTML = modalState.labels.map(function (item) {
      return '<label style="display:flex;align-items:center;gap:6px;padding:7px 9px;border:1px solid #d9d9d9;border-radius:6px;background:#fff">' +
        '<input type="checkbox" value="' + esc(item.value) + '" checked> <span>' + esc(item.key) + '</span>' +
        '<code style="margin-left:auto;color:#666;font-size:11px">' + esc(item.value) + '</code></label>';
    }).join("");
    host.querySelectorAll("input").forEach(function (input) { input.addEventListener("change", updateTargetDescription); });
    updateTargetDescription();
  }
  function updateModalProvider() {
    var config = providerConfig(modalState.provider);
    document.getElementById("preannotate-title").textContent = config.title;
    document.getElementById("preannotate-overwrite-label").textContent = "覆盖旧的 " + config.short + " 结果";
    document.getElementById("vlx-provider").value = modalState.provider;
  }
  function loadTaskSettings(taskIdValue) {
    var host = document.getElementById("vlx-target-labels");
    host.innerHTML = '<span style="color:#666">正在读取当前任务标签...</span>';
    fetch(API + "/tasks/" + taskIdValue, { headers: auth(), cache: "no-store" })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw Error(j.msg || r.statusText); return j; }); })
      .then(function (j) {
        modalState.labels = taskRectLabels(j.data && j.data.config);
        renderTargetLabels();
      })
      .catch(function (e) {
        modalState.labels = [];
        host.innerHTML = '<span style="color:#cf1322">读取任务标签失败：' + esc(e.message) + '</span>';
      });
  }
  function ensureModal() {
    if (document.getElementById(MODAL)) return;
    var root = document.createElement("div"); root.id = MODAL;
    root.innerHTML = '<div style="position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:99999;padding:5vh 20px;overflow:auto"><div role="dialog" aria-modal="true" style="background:#fff;max-width:620px;margin:auto;padding:22px;border-radius:8px;box-shadow:0 12px 36px rgba(0,0,0,.22)">' +
      '<h3 id="preannotate-title" style="margin:0 0 16px;font-size:18px"></h3>' +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px"><label style="font-weight:600">识别模型<select id="vlx-provider" style="display:block;width:100%;margin-top:6px;padding:8px;border:1px solid #d9d9d9;border-radius:6px"><option value="vlx">VLX-Seek 1.5 · 10B</option><option value="rex">Rex-Omni · 3B AWQ</option></select></label><label style="font-weight:600">感知任务<select disabled style="display:block;width:100%;margin-top:6px;padding:8px;border:1px solid #d9d9d9;border-radius:6px;background:#f5f5f5"><option>开放词汇检测</option></select></label></div>' +
      '<label style="display:block;font-weight:600;margin-bottom:6px">平台目标标签</label><div id="vlx-target-labels" style="display:grid;grid-template-columns:1fr 1fr;gap:7px;max-height:190px;overflow:auto;padding:8px;background:#f7f8fa;border-radius:6px"></div>' +
      '<label for="vlx-target-description" style="display:block;font-weight:600;margin-top:12px">问题或目标描述</label><textarea id="vlx-target-description" readonly rows="2" style="display:block;width:100%;box-sizing:border-box;margin-top:6px;padding:9px;border:1px solid #d9d9d9;border-radius:6px;resize:vertical;background:#fafafa"></textarea>' +
      '<p style="margin:7px 0 12px;color:#666;font-size:12px">模型只检测勾选的任务标签，结果只写入对应的平台标签；全局工具标注不会被修改。</p>' +
      '<p id="vlx-line" style="margin:0 0 10px"></p><label><input id="vlx-done" type="checkbox" checked> 包含已完成样本（批量时）</label><label style="display:block;margin-top:8px"><input id="vlx-overwrite" type="checkbox" checked> <span id="preannotate-overwrite-label"></span></label>' +
      '<pre id="vlx-log" style="height:110px;overflow:auto;background:#f6f7f9;padding:10px;border-radius:6px;white-space:pre-wrap"></pre><div style="display:flex;justify-content:flex-end;gap:8px"><button id="vlx-close" class="ant-btn">关闭</button><button id="vlx-run" class="ant-btn ant-btn-primary">开始标注</button></div></div></div>';
    document.body.appendChild(root); root.style.display = "none";
    document.getElementById("vlx-close").onclick = function () { root.style.display = "none"; };
    document.getElementById("vlx-provider").onchange = function (event) { modalState.provider = event.target.value; updateModalProvider(); };
    document.getElementById("vlx-run").onclick = runJob;
  }
  function open(provider) {
    var tid = taskId();
    if (!tid) {
      window.alert("请先打开一个 LabelU 任务详情页或样本标注页，再使用图片预标注。");
      return;
    }
    ensureModal(); var sid = sampleId(), root = document.getElementById(MODAL), log = document.getElementById("vlx-log");
    modalState.provider = provider;
    modalState.taskId = tid;
    modalState.sampleId = sid;
    modalState.labels = [];
    updateModalProvider();
    document.getElementById("vlx-line").textContent = sid ? "当前图片：" + sid : "整任务批量处理；每张图片发送一次请求";
    document.getElementById("vlx-done").parentElement.style.display = sid ? "none" : "block";
    document.getElementById("vlx-run").disabled = false;
    log.textContent = "";
    root.style.display = "block";
    loadTaskSettings(tid);
  }
  function runJob() {
    var provider = modalState.provider, config = providerConfig(provider);
    var tid = modalState.taskId, sid = modalState.sampleId, log = document.getElementById("vlx-log");
    var labels = selectedLabels();
    if (!labels.length) {
      log.textContent += "错误：至少选择一个平台目标标签。\n";
      return;
    }
    var payload = { filter_by_labels: true, target_labels: labels, overwrite: document.getElementById("vlx-overwrite").checked, include_done_samples: document.getElementById("vlx-done").checked };
    if (sid) { payload.sample_id = Number(sid); payload.sample_ids = [Number(sid)]; }
    var url = API + "/tasks/" + tid + "/" + config.route + (sid ? "?sample_id=" + sid : "");
    document.getElementById("vlx-run").disabled = true;
    log.textContent = "提交 " + config.short + " 作业...\n目标标签：" + document.getElementById("vlx-target-description").value + "\n";
    fetch(url, { method: "POST", headers: Object.assign({"Content-Type":"application/json"}, auth()), body: JSON.stringify(payload) }).then(function (r) { return r.json().then(function (j) { if (!r.ok) throw Error(j.msg || r.statusText); return j; }); }).then(function (j) {
      var jid = j.data.id; log.textContent += "作业 " + jid + " 已创建\n";
      var timer = setInterval(function () { fetch(API + "/tasks/" + tid + "/" + config.route + "/" + jid, { headers: auth(), cache: "no-store" }).then(function (r) { return r.json(); }).then(function (j2) { var s = j2.data; log.textContent += s.status + " " + s.processed_count + "/" + s.sample_count + "\n"; if (s.status === "COMPLETED" || s.status === "PARTIAL_COMPLETED" || s.status === "FAILED") { clearInterval(timer); document.getElementById("vlx-run").disabled = false; var succeeded = Number(s.success_count || 0), failed = Number(s.failed_count || 0); if (succeeded > 0) { log.textContent += failed > 0 ? ("部分完成：成功 " + succeeded + " 个，失败 " + failed + " 个；正在更新成功结果。\n") : "完成，正在更新当前页面的几何标注；全局工具保持不变。\n"; window.dispatchEvent(new CustomEvent("labelu:preannotation-completed", { detail: { taskId: Number(tid), sampleId: sid ? Number(sid) : null, provider: provider } })); } else { log.textContent += "失败：没有生成可用的拉框结果" + (s.error_message ? "（" + s.error_message + "）" : "") + "。请检查模型服务连接后重试。\n"; } } }).catch(function (e) { clearInterval(timer); document.getElementById("vlx-run").disabled = false; log.textContent += "轮询失败：" + e.message; }); }, 1200);
    }).catch(function (e) { document.getElementById("vlx-run").disabled = false; log.textContent += "错误：" + e.message; });
  }
  function pathOnly() {
    return ((location.pathname || "").split("?")[0] || "/").replace(/\/+$/, "") || "/";
  }
  function isSupportedPage() {
    return /^\/tasks\/\d+(?:\/edit|\/samples\/\d+)?$/i.test(pathOnly());
  }
  function text(el) { return (el.textContent || "").replace(/\s+/g, " ").trim(); }
  function isSamplePage() { return /^\/tasks\/\d+\/samples\/\d+$/i.test(pathOnly()); }
  function isTaskPage() { return /^\/tasks\/\d+(?:\/edit)?$/i.test(pathOnly()); }
  function findButton(predicate) {
    var nodes = document.querySelectorAll("button, a.ant-btn, span.ant-btn, .ant-btn, [role='button']");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (!el || (el.closest && el.closest("." + MENU))) continue;
      if (predicate(el, text(el))) return el;
    }
    return null;
  }
  function findAiButton() {
    var control = reactControl("ai");
    if (control) return control;
    if (isSamplePage()) {
      return findButton(function (el, t) {
        return t.indexOf("AI") >= 0 && (t.indexOf("标注") >= 0 || t.indexOf("补全") >= 0) && t.indexOf("批量") < 0 && t.indexOf("设置") < 0;
      });
    }
    return findButton(function (el, t) {
      if ((el.getAttribute && el.getAttribute("data-labelu-preannotate-action") === "ai_detection") || t.indexOf("目标检测") >= 0) return false;
      return (t.indexOf("AI") >= 0 && (t.indexOf("批量") >= 0 || t.indexOf("补全") >= 0)) ||
        (t.indexOf("批量") >= 0 && t.indexOf("补全") >= 0);
    });
  }
  function findYoloButton() {
    return reactControl("yolo") || document.querySelector(".labelu-yolo-preannotate-entry") || findButton(function (el, t) {
      return t.indexOf("检测框") >= 0 && (t.indexOf("生成") >= 0 || t.indexOf("YOLO") >= 0);
    });
  }
  function findTagtoolButton() {
    return reactControl("tagtool") || document.querySelector(".labelu-tagtool-preannotate-entry") || findButton(function (el, t) {
      return t.indexOf("全局标签") >= 0 && (t.indexOf("补全") >= 0 || t.indexOf("预标注") >= 0);
    });
  }
  function findAnchor() {
    if (isSamplePage()) {
      return document.querySelector("[data-labelu-ai-annotate-toolbar]") || findAiButton();
    }
    // The task page exposes a stable toolbar anchor so the menu is mounted
    // before capability-gated React buttons appear.  Falling back to the
    // legacy AI button keeps older builds compatible.
    return document.querySelector("[data-labelu-ai-annotate-extension-anchor]") || findAiButton();
  }
  function ensureFlex(el) {
    var p = el && el.parentElement;
    if (!p) return null;
    var display = window.getComputedStyle(p).display;
    if (display !== "flex" && display !== "inline-flex") {
      p.style.display = "flex";
      p.style.flexWrap = "wrap";
      p.style.alignItems = "center";
      if (!p.style.gap) p.style.gap = "8px";
    }
    return p;
  }
  function reactControl(action) {
    return document.querySelector("[data-labelu-preannotate-action='" + action + "']") ||
      document.querySelector(".labelu-preannotate-control-" + action);
  }
  function ensureCss() {
    if (document.getElementById(MENU_CSS)) return;
    var style = document.createElement("style");
    style.id = MENU_CSS;
    style.textContent =
      "." + MENU + "{position:relative;display:inline-flex;flex:0 0 auto;margin-left:8px;font-family:inherit}" +
      "." + MENU + "-trigger{display:inline-flex;align-items:center;gap:6px;min-height:32px;padding:4px 12px;border:1px solid #1677ff;border-radius:6px;background:#1677ff;color:#fff;font-size:14px;line-height:22px;cursor:pointer;white-space:nowrap;box-shadow:0 2px 6px rgba(0,0,0,.12)}" +
      "." + MENU + "-trigger:after{content:'';width:0;height:0;border-left:4px solid transparent;border-right:4px solid transparent;border-top:5px solid currentColor}" +
      "." + MENU + "-panel{position:absolute;top:calc(100% + 6px);left:0;z-index:10001;min-width:220px;padding:6px;background:#fff;border:1px solid #d9d9d9;border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.16)}" +
      "." + MENU + "-panel-up{top:auto;bottom:calc(100% + 6px)}" +
      "." + MENU + "-panel[hidden]{display:none}" +
      "." + MENU + "-item{display:block;width:100%;padding:8px 10px;border:0;border-radius:5px;background:transparent;color:rgba(0,0,0,.88);font:inherit;text-align:left;cursor:pointer;white-space:nowrap}" +
      "." + MENU + "-item:hover:not(:disabled){background:#f0f6ff;color:#1677ff}" +
      "." + MENU + "-item:disabled{color:#bfbfbf;cursor:not-allowed}" +
      "." + MENU + "-divider{height:1px;margin:5px 4px;background:#f0f0f0}" +
      "[data-labelu-preannotate-hidden='1']{display:none !important}" +
      "@media (max-width:720px){." + MENU + "-panel{left:auto;right:0;min-width:210px}}";
    document.head.appendChild(style);
  }
  function markHidden(el, hidden) {
    if (!el || (el.closest && el.closest("." + MENU))) return;
    if (hidden) el.setAttribute("data-labelu-preannotate-hidden", "1");
    else el.removeAttribute("data-labelu-preannotate-hidden");
  }
  function hideLegacyEntries() {
    var selectors = [
      ".labelu-yolo-preannotate-entry",
      ".labelu-locateanything-preannotate-entry",
      ".labelu-tagtool-preannotate-entry",
      ".labelu-vlx-seek-preannotate-entry",
      ".labelu-rex-omni-preannotate-entry",
      ".labelu-preannotate-control",
      "[data-labelu-preannotate-action]",
    ];
    selectors.forEach(function (selector) {
      document.querySelectorAll(selector).forEach(function (el) { markHidden(el, true); });
    });
    document.querySelectorAll("button, a.ant-btn, span.ant-btn, .ant-btn, [role='button']").forEach(function (el) {
      if (el.closest && el.closest("." + MENU)) return;
      var t = text(el);
      var isAi = t.indexOf("AI") >= 0 && (t.indexOf("批量") >= 0 || t.indexOf("补全") >= 0);
      var isBatchGlobal = t.indexOf("批量") >= 0 && t.indexOf("补全") >= 0;
      if (isAi || isBatchGlobal) markHidden(el, true);
    });
    var yolo = findYoloButton();
    if (yolo) markHidden(yolo, true);
    var tagtool = findTagtoolButton();
    if (tagtool) markHidden(tagtool, true);
  }
  function restoreLegacyEntries() {
    document.querySelectorAll("[data-labelu-preannotate-hidden='1']").forEach(function (el) { markHidden(el, false); });
  }
  function targetFor(action) {
    if (action === "yolo") return findYoloButton();
    if (action === "ai_detection") return reactControl("ai_detection");
    if (action === "locateanything") return reactControl("locateanything") || document.querySelector(".labelu-locateanything-preannotate-entry");
    if (action === "tagtool") return findTagtoolButton();
    if (action === "ai") return findAiButton();
    return null;
  }
  function menuItems() {
    var items = [];
    if (capabilityVisible("ai")) items.push({ action: "ai", label: isSamplePage() ? "AI补全全局标签（当前图片）" : "AI 批量补全全局标签", target: isTaskPage() ? true : targetFor("ai") });
    if (capabilityVisible("ai_detection")) items.push({ action: "ai_detection", label: isSamplePage() ? "AI目标检测(当前图片)" : "AI批量目标检测", target: targetFor("ai_detection") });
    if (capabilityVisible("yolo")) items.push({ action: "yolo", label: isSamplePage() ? "YOLO 检测框（当前图片）" : "YOLO 批量检测框", target: targetFor("yolo") });
    if (capabilityVisible("tagtool")) items.push({ action: "tagtool", label: isSamplePage() ? "TagTool 全局标签（当前图片）" : "TagTool 全局标签批量", target: targetFor("tagtool") });
    if (capabilityVisible("locateanything")) items.push({ action: "locateanything", label: isSamplePage() ? "LocateAnything 标框（当前图片）" : "LocateAnything 批量标框", target: targetFor("locateanything") });
    if (capabilityVisible("vlx")) items.push({ action: "vlx", label: isSamplePage() ? "VLX-Seek 检测框（当前图片）" : "VLX-Seek 批量检测框", target: true });
    if (capabilityVisible("rex")) items.push({ action: "rex", label: isSamplePage() ? "Rex-Omni 检测框（当前图片）" : "Rex-Omni 批量检测框", target: true });
    if (items.length) items.push({ divider: true });
    items.push({ action: "upload", label: isSamplePage() ? "上传 JSON/JSONL（当前图片）" : "上传 JSON/JSONL 预标注文件", target: true });
    return items;
  }
  function menuSignature() {
    return [
      isSamplePage() ? "sample" : "task",
      targetFor("ai") ? "1" : "0",
      targetFor("ai_detection") ? "1" : "0",
      targetFor("yolo") ? "1" : "0",
      targetFor("tagtool") ? "1" : "0",
      targetFor("locateanything") ? "1" : "0",
      capabilityState.signature || capabilitySignature(),
    ].join(":");
  }
  function closeMenu(root) {
    var panel = root && root.querySelector("." + MENU + "-panel");
    if (panel) panel.hidden = true;
    if (panel) panel.classList.remove(MENU + "-panel-up");
    if (root) {
      root.dataset.open = "0";
      var trigger = root.querySelector("." + MENU + "-trigger");
      if (trigger) trigger.setAttribute("aria-expanded", "false");
    }
  }
  function positionPanel(root) {
    var panel = root && root.querySelector("." + MENU + "-panel");
    var trigger = root && root.querySelector("." + MENU + "-trigger");
    if (!panel || !trigger) return;
    panel.classList.remove(MENU + "-panel-up");
    var rect = trigger.getBoundingClientRect();
    var panelHeight = panel.getBoundingClientRect().height;
    if (rect.bottom + panelHeight + 8 > window.innerHeight && rect.top > panelHeight + 8) {
      panel.classList.add(MENU + "-panel-up");
    }
  }
  function runAction(action, root) {
    closeMenu(root);
    if (action === "vlx") return open("vlx");
    if (action === "rex") return open("rex");
    if (action === "yolo") {
      if (window.LabelUYoloPreannotate && typeof window.LabelUYoloPreannotate.open === "function") {
        window.LabelUYoloPreannotate.open();
        return;
      }
      var yoloTarget = targetFor("yolo");
      if (yoloTarget) {
        yoloTarget.click();
        return;
      }
      window.alert("YOLO 预标注模块仍在加载，请稍后重试。");
      return;
    }
    if (action === "ai" && isTaskPage()) {
      window.dispatchEvent(new CustomEvent("labelu:open-ai-batch-preannotation", {
        detail: { taskId: Number(taskId()) },
      }));
      return;
    }
    if (action === "upload") {
      var upload = document.querySelector("[data-labelu-preannotation-upload='1'] input") || document.querySelector("[data-labelu-preannotation-upload='1']");
      if (upload) return upload.click();
      var tid = taskId();
      if (tid) location.href = "/tasks/" + tid + "/edit#upload";
      return;
    }
    var target = targetFor(action);
    if (!target) {
      window.alert("该预标注入口尚未加载，或当前页面不支持此操作。");
      return;
    }
    if (target.disabled) {
      window.alert("当前图片暂不可执行该预标注：页面可能仍在加载，或该图片正由其他用户标注。");
      return;
    }
    target.click();
  }
  function renderMenu(root) {
    var panel = root.querySelector("." + MENU + "-panel");
    panel.innerHTML = "";
    menuItems().forEach(function (item) {
      if (item.divider) {
        var divider = document.createElement("div");
        divider.className = MENU + "-divider";
        panel.appendChild(divider);
        return;
      }
      var button = document.createElement("button");
      button.type = "button";
      button.className = MENU + "-item";
      button.textContent = item.label;
      button.disabled = !item.target;
      button.setAttribute("role", "menuitem");
      button.onclick = function (event) {
        event.preventDefault();
        event.stopPropagation();
        runAction(item.action, root);
      };
      panel.appendChild(button);
    });
  }
  function createMenu() {
    ensureCss();
    var root = document.createElement("div");
    root.className = MENU;
    root.setAttribute("data-labelu-preannotate-menu", "1");
    root.dataset.open = "0";
    root.innerHTML = '<button type="button" class="' + MENU + '-trigger" aria-haspopup="menu" aria-expanded="false">预标注</button><div class="' + MENU + '-panel" role="menu" hidden></div>';
    var trigger = root.querySelector("." + MENU + "-trigger");
    trigger.onclick = function (event) {
      event.preventDefault();
      event.stopPropagation();
      var panel = root.querySelector("." + MENU + "-panel");
      var openState = root.dataset.open === "1";
      if (openState) closeMenu(root);
      else {
        renderMenu(root);
        panel.hidden = false;
        root.dataset.open = "1";
        positionPanel(root);
      }
      trigger.setAttribute("aria-expanded", root.dataset.open === "1" ? "true" : "false");
    };
    return root;
  }
  function ensureExtensionScripts() {
    ["task_yolo_preannotate_entry.js", "task_tagtool_preannotate_entry.js", "task_locateanything_preannotate_entry.js"].forEach(function (name) {
      if (document.querySelector("script[data-labelu-preannotate-script='" + name + "']")) return;
      if (Array.from(document.scripts).some(function (script) { return (script.src || "").indexOf("/assets/" + name) >= 0; })) return;
      var script = document.createElement("script");
      script.defer = true;
      script.src = "/assets/" + name;
      script.setAttribute("data-labelu-preannotate-script", name);
      document.head.appendChild(script);
    });
  }
  function inject() {
    if (!document.body) return;
    var root = document.querySelector("." + MENU);
    if (!isSupportedPage() || !taskId()) {
      if (root) root.remove();
      restoreLegacyEntries();
      return;
    }
    loadCapabilities();
    var anchor = findAnchor();
    if (!anchor || !anchor.parentElement) return;
    if (!root) root = createMenu();
    var isToolbarAnchor = anchor.hasAttribute && anchor.hasAttribute("data-labelu-ai-annotate-extension-anchor");
    var row = isToolbarAnchor ? anchor : ensureFlex(anchor);
    var toolbar = document.querySelector("[data-labelu-ai-annotate-toolbar]");
    var toolbarRow = toolbar && toolbar.parentElement;
    var host = toolbarRow || (isToolbarAnchor ? anchor : row);
    if (host && root.parentElement !== host) {
      if (toolbarRow && toolbar && toolbar.parentElement === host) host.insertBefore(root, toolbar.nextElementSibling);
      else host.appendChild(root);
    }
    ensureExtensionScripts();
    hideLegacyEntries();
    var signature = menuSignature();
    if (root.dataset.signature !== signature || !root.querySelector("." + MENU + "-panel").children.length) {
      renderMenu(root);
      root.dataset.signature = signature;
    }
  }
  function routeChanged() { setTimeout(inject, 30); }
  ["pushState", "replaceState"].forEach(function (name) {
    var original = history[name];
    history[name] = function () { var result = original.apply(this, arguments); window.dispatchEvent(new Event("labelu-vlx-routechange")); return result; };
  });
  window.addEventListener("popstate", routeChanged);
  window.addEventListener("labelu-vlx-routechange", routeChanged);
  document.addEventListener("click", function (event) {
    var root = document.querySelector("." + MENU);
    if (root && root.dataset.open === "1" && !root.contains(event.target)) closeMenu(root);
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", inject); else inject();
  new MutationObserver(function () { setTimeout(inject, 30); }).observe(document.body, { childList: true, subtree: true });
})();
