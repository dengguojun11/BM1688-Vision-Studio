/**
 * 任务列表页和单图页提供全局标签预标注入口；统一预标注菜单会调用这里的按钮。
 */
(function () {
  var BTN_CLASS = "labelu-tagtool-preannotate-entry";
  var API = "/api/v1";
  var MODAL_ID = "labelu-tagtool-preannotate-modal-root";
  var CSS_ID = "labelu-tagtool-preannotate-modal-css";

  var state = {
    pollTimer: null,
    batchBgTimer: null,
    selected: null,
    models: [],
    taskId: null,
    sampleId: null,
  };

  function pathOnly() {
    return ((location.pathname || "").split("?")[0] || "/").replace(/\/+$/, "") || "/";
  }
  function parseTaskId() {
    var m = pathOnly().match(/^\/tasks\/(\d+)/i);
    return m ? m[1] : null;
  }
  function parseSampleId() {
    var m = pathOnly().match(/^\/tasks\/\d+\/samples\/(\d+)/i);
    return m ? m[1] : null;
  }
  function isAnnotationSamplePage() {
    return /^\/tasks\/\d+\/samples\/\d+$/i.test(pathOnly());
  }
  function isTaskRootPage() {
    return /^\/tasks\/\d+$/i.test(pathOnly());
  }
  function text(el) {
    return (el.textContent || "").replace(/\s+/g, " ").trim();
  }
  function getToken() {
    return localStorage.getItem("token") || "";
  }
  function setToken(t) {
    if (t) localStorage.setItem("token", t.trim());
    else localStorage.removeItem("token");
  }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function prefsKey(taskId) {
    return "labelu_tagtool_preannotate_prefs_" + String(taskId);
  }
  function readPrefs(taskId) {
    try {
      return JSON.parse(localStorage.getItem(prefsKey(taskId)) || "null");
    } catch (e) {
      return null;
    }
  }
  function writePrefs(taskId, o) {
    try {
      localStorage.setItem(prefsKey(taskId), JSON.stringify(o));
    } catch (e) {}
  }

  function findBatchAiButton() {
    var nodes = document.querySelectorAll("button, a.ant-btn, span.ant-btn, .ant-btn, [role='button']");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.closest && (el.closest("." + BTN_CLASS) || el.closest(".labelu-preannotate-menu"))) continue;
      var t = text(el);
      if (!t || t.indexOf("全局标签") >= 0 || t.indexOf("YOLO") >= 0) continue;
      if (t.indexOf("AI") >= 0 && t.indexOf("批量") >= 0) return el;
      if (t.indexOf("批量") >= 0 && t.indexOf("导出") >= 0) return el;
    }
    return null;
  }
  function findSingleAiLabelButton() {
    var nodes = document.querySelectorAll("button, a.ant-btn, span.ant-btn, .ant-btn, [role='button']");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.closest && (el.closest("." + BTN_CLASS) || el.closest(".labelu-preannotate-menu"))) continue;
      var t = text(el);
      if (!t || t.indexOf("全局标签") >= 0 || t.indexOf("YOLO") >= 0) continue;
      if (t.indexOf("AI") >= 0 && (t.indexOf("标注") >= 0 || t.indexOf("补全") >= 0) && t.indexOf("批量") < 0) return el;
    }
    return null;
  }
  function findAnnotationToolbarInsertPoint() {
    return document.querySelector("[data-labelu-ai-annotate-extension-anchor]");
  }
  function styleNeighbor(btn, ref) {
    var cs = window.getComputedStyle(ref);
    btn.style.boxSizing = "border-box";
    btn.style.minHeight = cs.minHeight;
    btn.style.padding = cs.padding;
    btn.style.borderRadius = cs.borderRadius;
    btn.style.fontSize = cs.fontSize;
    btn.style.fontWeight = cs.fontWeight || "500";
    btn.style.lineHeight = cs.lineHeight;
    btn.style.cursor = "pointer";
    btn.style.fontFamily = cs.fontFamily;
    btn.style.background = "#fff";
    btn.style.border = "1px solid #d9d9d9";
    btn.style.color = "rgba(0, 0, 0, 0.88)";
    btn.style.marginLeft = "8px";
    btn.style.transition = "all .2s cubic-bezier(.645,.045,.355,1)";
    btn.onmouseenter = function () {
      btn.style.borderColor = "#4096ff";
      btn.style.color = "#4096ff";
    };
    btn.onmouseleave = function () {
      btn.style.borderColor = "#d9d9d9";
      btn.style.color = "rgba(0, 0, 0, 0.88)";
    };
  }
  function ensureFlexParent(el) {
    var p = el.parentElement;
    if (!p) return;
    var d = window.getComputedStyle(p).display;
    if (d !== "flex" && d !== "inline-flex") {
      p.style.display = "flex";
      p.style.flexWrap = "wrap";
      p.style.alignItems = "center";
      if (!p.style.gap) p.style.gap = "8px";
    }
  }

  function ensureCss() {
    if (document.getElementById(CSS_ID)) return;
    var st = document.createElement("style");
    st.id = CSS_ID;
    st.textContent =
      "#" + MODAL_ID + " .ltm-backdrop{position:absolute;inset:0;background:rgba(0,0,0,.45)}" +
      "#" + MODAL_ID + " .ltm-dialog{position:relative;margin:48px auto;max-width:560px;width:calc(100% - 32px);background:#fff;border-radius:8px;box-shadow:0 6px 24px rgba(0,0,0,.18);max-height:calc(100vh - 96px);display:flex;flex-direction:column;font-family:system-ui,sans-serif}" +
      "#" + MODAL_ID + " .ltm-head{display:flex;align-items:center;justify-content:space-between;padding:14px 16px;border-bottom:1px solid #f0f0f0;flex-shrink:0}" +
      "#" + MODAL_ID + " .ltm-head h2{margin:0;font-size:16px;font-weight:600}" +
      "#" + MODAL_ID + " .ltm-body{padding:14px 16px;overflow:auto;flex:1}" +
      "#" + MODAL_ID + " .ltm-foot{padding:10px 16px 14px;border-top:1px solid #f0f0f0;text-align:right;flex-shrink:0}" +
      "#" + MODAL_ID + " label.ltm-l{display:block;font-size:12px;color:#666;margin-bottom:4px}" +
      "#" + MODAL_ID + " input.ltm-in,#" + MODAL_ID + " select.ltm-in{width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid #d9d9d9;border-radius:8px;font-size:13px}" +
      "#" + MODAL_ID + " .ltm-models{max-height:min(40vh,320px);overflow:auto;border:1px solid #e8e8e8;border-radius:8px;margin-top:8px}" +
      "#" + MODAL_ID + " .ltm-models label{display:flex;gap:10px;align-items:flex-start;padding:10px 12px;border-bottom:1px solid #f5f5f5;cursor:pointer;margin:0;font-size:13px}" +
      "#" + MODAL_ID + " .ltm-models label:hover{background:#fafafa}" +
      "#" + MODAL_ID + " .ltm-tag{font-size:11px;background:#f5f5f5;padding:2px 6px;border-radius:4px;color:#555}" +
      "#" + MODAL_ID + " .ltm-tagc{background:#f6ffed;color:#237804}" +
      "#" + MODAL_ID + " .ltm-row{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px;align-items:flex-end}" +
      "#" + MODAL_ID + " .ltm-row>div{flex:1;min-width:120px}" +
      "#" + MODAL_ID + " .ltm-muted{color:#666;font-size:12px;margin:0 0 8px}" +
      "#" + MODAL_ID + " pre.ltm-log{background:#fafafa;border:1px solid #e8e8e8;border-radius:8px;padding:8px;font-size:12px;max-height:120px;overflow:auto;white-space:pre-wrap;margin:10px 0 0}" +
      "#" + MODAL_ID + " button.ltm-btn{padding:6px 14px;border-radius:8px;border:1px solid #d9d9d9;background:#fff;cursor:pointer;font-size:13px}" +
      "#" + MODAL_ID + " button.ltm-primary{background:#1677ff;color:#fff;border-color:#1677ff}" +
      "#" + MODAL_ID + " button:disabled{opacity:.5;cursor:not-allowed}";
    document.head.appendChild(st);
  }
  function ensureModal() {
    ensureCss();
    var root = document.getElementById(MODAL_ID);
    if (root) return root;
    root = document.createElement("div");
    root.id = MODAL_ID;
    root.style.cssText = "display:none;position:fixed;inset:0;z-index:20001;font-family:system-ui,sans-serif";
    root.innerHTML =
      '<div class="ltm-backdrop" data-close="1"></div>' +
      '<div class="ltm-dialog" role="dialog" aria-modal="true" aria-labelledby="ltm-title">' +
      '<div class="ltm-head"><h2 id="ltm-title">全局标签预标注</h2><button type="button" class="ltm-btn" id="ltm-close-x" aria-label="关闭">X</button></div>' +
      '<div class="ltm-body">' +
      '<p class="ltm-muted">全局标签仅使用已检查通过的模型服务；列表中的“服务器模型”来自系统管理配置，也兼容历史上传的本地权重。</p>' +
      '<label class="ltm-l" for="ltm-token">访问令牌（可选，修改后会写回浏览器本地存储）</label>' +
      '<input class="ltm-in" id="ltm-token" type="text" placeholder="Bearer ..." autocomplete="off" />' +
      '<div id="ltm-taskline" style="margin:10px 0;font-size:14px"></div>' +
      '<div class="ltm-row" id="ltm-batch-only" style="display:none"><div><label class="ltm-l" for="ltm-include-done">包含已标注样本（整任务）</label><select class="ltm-in" id="ltm-include-done"><option value="0">否（仅未标注 NEW）</option><option value="1" selected>是（含已标注 DONE）</option></select></div></div>' +
      '<div class="ltm-models" id="ltm-models"></div>' +
      '<pre class="ltm-log" id="ltm-log"></pre>' +
      '</div><div class="ltm-foot"><button type="button" class="ltm-btn" id="ltm-btn-cancel">取消</button> <button type="button" class="ltm-btn ltm-primary" id="ltm-btn-reload">重新加载模型</button> <button type="button" class="ltm-btn ltm-primary" id="ltm-btn-run" disabled>开始预标注</button></div></div>';
    document.body.appendChild(root);
    root.addEventListener("click", function (e) {
      if (e.target && e.target.getAttribute && e.target.getAttribute("data-close")) closeModal();
    });
    document.getElementById("ltm-close-x").onclick = closeModal;
    document.getElementById("ltm-btn-cancel").onclick = closeModal;
    document.getElementById("ltm-btn-reload").onclick = loadModels;
    document.getElementById("ltm-btn-run").onclick = runJob;
    document.getElementById("ltm-token").addEventListener("change", function () {
      setToken(document.getElementById("ltm-token").value || "");
      loadModels();
    });
    document.getElementById("ltm-include-done").addEventListener("change", persistPrefs);
    return root;
  }

  function clearPoll() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
  function clearBatchPoll() {
    if (state.batchBgTimer) clearInterval(state.batchBgTimer);
    state.batchBgTimer = null;
  }
  function closeModal() {
    clearPoll();
    var root = document.getElementById(MODAL_ID);
    if (root) root.style.display = "none";
    document.body.style.overflow = "";
  }
  function log(msg) {
    var el = document.getElementById("ltm-log");
    if (!el) return;
    el.textContent += new Date().toLocaleTimeString() + " " + msg + "\n";
    el.scrollTop = el.scrollHeight;
  }
  function persistPrefs() {
    if (!state.taskId || !state.selected) return;
    writePrefs(state.taskId, {
      model_kind: state.selected.kind,
      model_id: state.selected.id,
      include_done: document.getElementById("ltm-include-done").value === "1",
    });
  }
  function renderModels(pref) {
    var box = document.getElementById("ltm-models");
    box.innerHTML = "";
    state.selected = null;
    document.getElementById("ltm-btn-run").disabled = true;
    for (var i = 0; i < state.models.length; i++) {
      var it = state.models[i];
      var lab = document.createElement("label");
      var inp = document.createElement("input");
      inp.type = "radio";
      inp.name = "ltm_pick";
      var div = document.createElement("div");
      div.innerHTML =
        '<span class="ltm-tag">' + esc(it.kind) + '</span> <span class="ltm-tag ltm-tagc">' + esc(it.category || "未分类") + '</span> <strong>' +
        esc(it.title) + '</strong><br/><span style="color:#888;font-size:12px">' +
        esc((it.detail || "") + " · " + ((it.label_heads || []).join(", "))) + "</span>";
      lab.appendChild(inp);
      lab.appendChild(div);
      (function (item) {
        inp.addEventListener("change", function () {
          state.selected = { kind: item.kind, id: item.id };
          document.getElementById("ltm-btn-run").disabled = false;
          persistPrefs();
        });
      })(it);
      if (pref && pref.model_kind === it.kind && String(pref.model_id) === String(it.id)) {
        inp.checked = true;
        state.selected = { kind: it.kind, id: it.id };
        document.getElementById("ltm-btn-run").disabled = false;
      }
      box.appendChild(lab);
    }
  }
  function refreshTaskLine(taskId) {
    var el = document.getElementById("ltm-taskline");
    el.innerHTML = "<strong>任务 ID " + esc(taskId) + "</strong>";
    var token = getToken();
    if (!token) return;
    fetch(API + "/tasks/" + taskId, { headers: { Authorization: token }, cache: "no-store" })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.msg || r.statusText); return j; }); })
      .then(function (j) {
        var name = j.data && j.data.name ? String(j.data.name) : "";
        el.innerHTML = "<strong>" + esc(name || "未命名任务") + "</strong> · 任务 ID " + esc(taskId);
      })
      .catch(function (e) { el.innerHTML += ' <span style="color:#c00">' + esc(e.message) + "</span>"; });
  }
  function loadModels() {
    var taskId = state.taskId;
    if (!taskId) return;
    state.models = [];
    state.selected = null;
    document.getElementById("ltm-models").innerHTML = "";
    document.getElementById("ltm-btn-run").disabled = true;
    var token = getToken();
    if (!token) {
      log("错误: 请先填写 Token");
      return;
    }
    log("加载 TagTool 分类模型列表...");
    fetch(API + "/tasks/" + taskId + "/tagtool_preannotate/models", { headers: { Authorization: token }, cache: "no-store" })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.msg || r.statusText); return j; }); })
      .then(function (j) {
        state.models = (j.data && j.data.items) || [];
        if (!state.models.length) {
          log("列表为空：请到系统管理→模型接入配置配置并检查全局标签模型，或上传历史 TagTool 分类权重。");
          return;
        }
        renderModels(readPrefs(taskId));
        log("共 " + state.models.length + " 个模型，请选择后开始预标注。");
      })
      .catch(function (e) { log("错误: " + e.message); });
  }
  function runJob() {
    if (!state.taskId || !state.selected) return;
    var isBatch = !state.sampleId;
    var payload = {
      model_kind: state.selected.kind,
      model_id: state.selected.id,
      overwrite: true,
      include_done_samples: isBatch ? document.getElementById("ltm-include-done").value === "1" : true,
    };
    if (!isBatch) {
      payload.sample_ids = [parseInt(state.sampleId, 10)];
      payload.sample_id = parseInt(state.sampleId, 10);
    }
    persistPrefs();
    clearPoll();
    if (isBatch) clearBatchPoll();
    var token = getToken();
    if (!token) {
      log("错误: 请先填写 Token");
      return;
    }
    var postUrl = API + "/tasks/" + state.taskId + "/tagtool_preannotate_job";
    if (!isBatch) postUrl += "?sample_id=" + encodeURIComponent(String(state.sampleId));
    log("提交全局标签预标注作业" + (isBatch ? "（整任务）" : " sample_id=" + state.sampleId));
    fetch(postUrl, {
      method: "POST",
      headers: { Authorization: token, "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.msg || r.statusText); return j; }); })
      .then(function (j) {
        var job = j.data;
        log("作业已创建 id=" + job.id + "，轮询状态...");
        var jid = job.id;
        function pollOnce() {
          fetch(API + "/tasks/" + state.taskId + "/tagtool_preannotate_job/" + jid, { headers: { Authorization: getToken() }, cache: "no-store" })
            .then(function (r2) { return r2.json().then(function (j2) { if (!r2.ok) throw new Error(j2.msg || r2.statusText); return j2; }); })
            .then(function (j2) {
              var st = j2.data;
              log("status=" + st.status + " processed=" + st.processed_count + "/" + st.sample_count + " ok=" + st.success_count + " fail=" + st.failed_count);
              if (st.status === "COMPLETED" || st.status === "FAILED") {
                if (isBatch) clearBatchPoll();
                else clearPoll();
                if (st.status === "COMPLETED") {
                  log("完成，正在刷新页面...");
                  closeModal();
                  window.setTimeout(function () { window.location.reload(); }, 450);
                } else {
                  log("作业失败" + (st.error_message ? ": " + st.error_message : ""));
                }
              }
            })
            .catch(function (e) {
              if (isBatch) clearBatchPoll();
              else clearPoll();
              log("轮询错误: " + e.message);
            });
        }
        var timer = setInterval(pollOnce, 1200);
        if (isBatch) state.batchBgTimer = timer;
        else state.pollTimer = timer;
        pollOnce();
      })
      .catch(function (e) { log("错误: " + e.message); });
  }
  function openModal(taskId, sampleId) {
    ensureModal();
    clearPoll();
    state.taskId = taskId;
    state.sampleId = sampleId;
    var saved = readPrefs(taskId);
    document.getElementById("ltm-title").textContent = sampleId ? "全局标签预标注（当前图片）" : "全局标签批量预标注（整任务）";
    document.getElementById("ltm-batch-only").style.display = sampleId ? "none" : "flex";
    if (!sampleId && saved && typeof saved.include_done === "boolean") {
      document.getElementById("ltm-include-done").value = saved.include_done ? "1" : "0";
    }
    document.getElementById("ltm-btn-run").textContent = sampleId ? "开始预标注" : "开始批量预标注";
    document.getElementById("ltm-log").textContent = "";
    document.getElementById("ltm-token").value = getToken();
    refreshTaskLine(taskId);
    document.getElementById(MODAL_ID).style.display = "block";
    document.body.style.overflow = "hidden";
    loadModels();
  }
  function sameMount(existing, anchor, mode, taskId) {
    return existing && anchor && insertionReference(anchor, 20).nextElementSibling === existing &&
      existing.getAttribute("data-mode") === mode &&
      existing.getAttribute("data-task") === String(taskId);
  }
  function hasClass(el, cls) {
    return (" " + (el && el.className ? el.className : "") + " ").indexOf(" " + cls + " ") >= 0;
  }
  function preannotateEntryPriority(el) {
    if (hasClass(el, "labelu-yolo-preannotate-entry")) return 10;
    if (hasClass(el, "labelu-locateanything-preannotate-entry")) return 15;
    if (hasClass(el, "labelu-tagtool-preannotate-entry")) return 20;
    return null;
  }
  function insertionReference(anchor, priority) {
    var ref = anchor;
    var node = anchor ? anchor.nextElementSibling : null;
    while (node) {
      var p = preannotateEntryPriority(node);
      if (p == null || p >= priority) break;
      ref = node;
      node = node.nextElementSibling;
    }
    return ref;
  }
  function attachNextTo(anchor, taskId, mode) {
    if (!anchor || !anchor.parentElement) return;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = BTN_CLASS + " ant-btn ant-btn-default";
    btn.setAttribute("data-mode", mode);
    btn.setAttribute("data-task", String(taskId));
    if (mode === "sample") {
      btn.textContent = "全局标签预标注";
      btn.title = "仅对当前图片写入 tagTool 全局标签预标注";
    } else {
      btn.textContent = "全局标签批量";
      btn.title = "对任务内图片批量写入 tagTool 全局标签预标注";
    }
    styleNeighbor(btn, anchor);
    ensureFlexParent(anchor);
    btn.onclick = function (e) {
      e.preventDefault();
      e.stopPropagation();
      var currentSampleId = parseSampleId();
      openModal(taskId, currentSampleId || null);
    };
    var toolbarSlot =
      anchor.getAttribute &&
      anchor.getAttribute("data-labelu-ai-annotate-extension-anchor") !== null;
    if (toolbarSlot) {
      anchor.parentElement.insertBefore(btn, anchor);
      return;
    }
    var ref = insertionReference(anchor, 20);
    anchor.parentElement.insertBefore(btn, ref.nextSibling);
  }
  function clearStale() {
    var nodes = document.querySelectorAll("." + BTN_CLASS);
    for (var i = 0; i < nodes.length; i++) nodes[i].remove();
  }
  function inject() {
    var taskId = parseTaskId();
    if (!taskId || taskId === "0" || (!isTaskRootPage() && !isAnnotationSamplePage())) {
      clearStale();
      return;
    }
    var mode = isAnnotationSamplePage() ? "sample" : "task";
    var anchor = mode === "sample"
      ? findAnnotationToolbarInsertPoint() || findSingleAiLabelButton()
      : findBatchAiButton();
    var existing = document.querySelector("." + BTN_CLASS);
    if (existing && sameMount(existing, anchor, mode, taskId)) return;
    if (existing) existing.remove();
    if (anchor && anchor.parentElement) attachNextTo(anchor, taskId, mode);
  }
  var timer = null;
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(function () {
      try { inject(); } catch (e) {}
    }, 16);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", schedule);
  else schedule();
  new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true });
  window.addEventListener("popstate", function () {
    clearBatchPoll();
    closeModal();
    schedule();
  });
})();
