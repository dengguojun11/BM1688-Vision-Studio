/**
 * 任务详情 / 单图标注页插入“LA标注”。
 * LocateAnything-3B 是 grounding 标框模型，不走通用视觉大模型提示词链。
 */
(function () {
  var BTN_CLASS = "labelu-locateanything-preannotate-entry";
  var API = "/api/v1";
  var MODAL_ID = "labelu-la-preannotate-modal-root";
  var CSS_ID = "labelu-la-preannotate-modal-css";
  var state = { taskId: null, sampleId: null, pollTimer: null, batchTimer: null };

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
  function isTaskRootPage() { return /^\/tasks\/\d+$/i.test(pathOnly()); }
  function isTaskEditPage() { return /^\/tasks\/\d+\/edit$/i.test(pathOnly()); }
  function isAnnotationSamplePage() { return /^\/tasks\/\d+\/samples\/\d+$/i.test(pathOnly()); }
  function text(el) { return (el.textContent || "").replace(/\s+/g, " ").trim(); }
  function getToken() { return localStorage.getItem("token") || ""; }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function findBatchAiButton() {
    var nodes = document.querySelectorAll("button, a.ant-btn, span.ant-btn, .ant-btn, [role='button']");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.closest && (el.closest("." + BTN_CLASS) || el.closest(".labelu-preannotate-menu"))) continue;
      var t = text(el);
      if (!t || t.indexOf("YOLO") >= 0 || t.indexOf("LA") >= 0) continue;
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
      if (!t || t.indexOf("YOLO") >= 0 || t.indexOf("LA") >= 0) continue;
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
    btn.onmouseenter = function () { btn.style.borderColor = "#4096ff"; btn.style.color = "#4096ff"; };
    btn.onmouseleave = function () { btn.style.borderColor = "#d9d9d9"; btn.style.color = "rgba(0, 0, 0, 0.88)"; };
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
      "#" + MODAL_ID + " .lam-backdrop{position:absolute;inset:0;background:rgba(0,0,0,.45)}" +
      "#" + MODAL_ID + " .lam-dialog{position:relative;margin:56px auto;max-width:520px;width:calc(100% - 32px);background:#fff;border-radius:8px;box-shadow:0 6px 24px rgba(0,0,0,.18);font-family:system-ui,sans-serif}" +
      "#" + MODAL_ID + " .lam-head{display:flex;align-items:center;justify-content:space-between;padding:14px 16px;border-bottom:1px solid #f0f0f0}" +
      "#" + MODAL_ID + " .lam-head h2{margin:0;font-size:16px;font-weight:600}" +
      "#" + MODAL_ID + " .lam-body{padding:14px 16px}" +
      "#" + MODAL_ID + " .lam-foot{padding:10px 16px 14px;border-top:1px solid #f0f0f0;text-align:right}" +
      "#" + MODAL_ID + " label.lam-l{display:block;font-size:12px;color:#666;margin-bottom:4px}" +
      "#" + MODAL_ID + " input.lam-in,#" + MODAL_ID + " select.lam-in{width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid #d9d9d9;border-radius:8px;font-size:13px}" +
      "#" + MODAL_ID + " .lam-row{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px;align-items:flex-end}" +
      "#" + MODAL_ID + " .lam-row>div{flex:1;min-width:160px}" +
      "#" + MODAL_ID + " .lam-muted{color:#666;font-size:12px;margin:0 0 10px}" +
      "#" + MODAL_ID + " pre.lam-log{background:#fafafa;border:1px solid #e8e8e8;border-radius:8px;padding:8px;font-size:12px;max-height:140px;overflow:auto;white-space:pre-wrap;margin:10px 0 0}" +
      "#" + MODAL_ID + " button.lam-btn{padding:6px 14px;border-radius:8px;border:1px solid #d9d9d9;background:#fff;cursor:pointer;font-size:13px}" +
      "#" + MODAL_ID + " button.lam-primary{background:#1677ff;color:#fff;border-color:#1677ff}";
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
      '<div class="lam-backdrop" data-close="1"></div>' +
      '<div class="lam-dialog" role="dialog" aria-modal="true" aria-labelledby="lam-title">' +
      '<div class="lam-head"><h2 id="lam-title">LA标注</h2><button type="button" class="lam-btn" id="lam-close-x" aria-label="关闭">X</button></div>' +
      '<div class="lam-body">' +
      '<p class="lam-muted">使用本系统内置 LocateAnything 服务按任务矩形框标签生成候选框。</p>' +
      '<div id="lam-taskline" style="margin:10px 0;font-size:14px"></div>' +
      '<div class="lam-row"><div><label class="lam-l" for="lam-overwrite">覆盖已有 LA 结果</label><select class="lam-in" id="lam-overwrite"><option value="1" selected>是</option><option value="0">否</option></select></div>' +
      '<div id="lam-batch-only"><label class="lam-l" for="lam-include-done">包含已标注样本</label><select class="lam-in" id="lam-include-done"><option value="0">否（仅未标注 NEW）</option><option value="1" selected>是（含 DONE）</option></select></div></div>' +
      '<pre class="lam-log" id="lam-log"></pre>' +
      '</div><div class="lam-foot"><button type="button" class="lam-btn" id="lam-btn-cancel">取消</button> <button type="button" class="lam-btn lam-primary" id="lam-btn-run">开始标注</button></div></div>';
    document.body.appendChild(root);
    root.addEventListener("click", function (e) {
      if (e.target && e.target.getAttribute && e.target.getAttribute("data-close")) closeModal();
    });
    document.getElementById("lam-close-x").onclick = closeModal;
    document.getElementById("lam-btn-cancel").onclick = closeModal;
    document.getElementById("lam-btn-run").onclick = runJob;
    return root;
  }
  function clearPoll() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    if (state.batchTimer) clearInterval(state.batchTimer);
    state.pollTimer = null;
    state.batchTimer = null;
  }
  function closeModal() {
    clearPoll();
    var root = document.getElementById(MODAL_ID);
    if (root) root.style.display = "none";
    document.body.style.overflow = "";
  }
  function log(msg) {
    var el = document.getElementById("lam-log");
    if (!el) return;
    el.textContent += new Date().toLocaleTimeString() + " " + msg + "\n";
    el.scrollTop = el.scrollHeight;
  }
  function refreshTaskLine(taskId) {
    var el = document.getElementById("lam-taskline");
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
  function runJob() {
    if (!state.taskId) return;
    clearPoll();
    var token = getToken();
    if (!token) {
      log("错误: 登录状态已失效，请重新登录后再运行 LA 标注");
      return;
    }
    var isBatch = !state.sampleId;
    var payload = {
      filter_by_labels: true,
      overwrite: document.getElementById("lam-overwrite").value === "1",
      include_done_samples: isBatch ? document.getElementById("lam-include-done").value === "1" : true,
    };
    if (!isBatch) {
      payload.sample_ids = [parseInt(state.sampleId, 10)];
      payload.sample_id = parseInt(state.sampleId, 10);
    }
    var postUrl = API + "/tasks/" + state.taskId + "/locateanything_preannotate_job";
    if (!isBatch) postUrl += "?sample_id=" + encodeURIComponent(String(state.sampleId));
    log("提交 LA 标注作业" + (isBatch ? "（整任务）" : " sample_id=" + state.sampleId));
    fetch(postUrl, {
      method: "POST",
      headers: { Authorization: token, "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.msg || r.statusText); return j; }); })
      .then(function (j) {
        var jid = j.data.id;
        log("作业已创建 id=" + jid + "，轮询状态...");
        function pollOnce() {
          fetch(API + "/tasks/" + state.taskId + "/locateanything_preannotate_job/" + jid, { headers: { Authorization: getToken() }, cache: "no-store" })
            .then(function (r2) { return r2.json().then(function (j2) { if (!r2.ok) throw new Error(j2.msg || r2.statusText); return j2; }); })
            .then(function (j2) {
              var st = j2.data;
              log("status=" + st.status + " processed=" + st.processed_count + "/" + st.sample_count + " ok=" + st.success_count + " fail=" + st.failed_count);
              if (st.status === "COMPLETED" || st.status === "FAILED") {
                clearPoll();
                if (st.status === "COMPLETED") {
                  if (st.success_count > 0) log("完成，正在刷新页面...");
                  else log("完成，但没有写入候选框；请确认图片内容与任务矩形框标签匹配。");
                  closeModal();
                  window.setTimeout(function () { window.location.reload(); }, 450);
                } else {
                  log("作业失败" + (st.error_message ? ": " + st.error_message : ""));
                }
              }
            })
            .catch(function (e) { clearPoll(); log("轮询错误: " + e.message); });
        }
        var timer = setInterval(pollOnce, 1200);
        if (isBatch) state.batchTimer = timer; else state.pollTimer = timer;
        pollOnce();
      })
      .catch(function (e) { log("错误: " + e.message); });
  }
  function openModal(taskId, sampleId) {
    ensureModal();
    clearPoll();
    state.taskId = taskId;
    state.sampleId = sampleId;
    document.getElementById("lam-title").textContent = sampleId ? "LA标注（当前图片）" : "LA批量标注（整任务）";
    document.getElementById("lam-batch-only").style.display = sampleId ? "none" : "block";
    document.getElementById("lam-btn-run").textContent = sampleId ? "开始标注" : "开始批量标注";
    document.getElementById("lam-log").textContent = "";
    refreshTaskLine(taskId);
    document.getElementById(MODAL_ID).style.display = "block";
    document.body.style.overflow = "hidden";
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
  function sameMount(existing, anchor, mode, taskId) {
    return existing && anchor && insertionReference(anchor, 15).nextElementSibling === existing &&
      existing.getAttribute("data-mode") === mode &&
      existing.getAttribute("data-task") === String(taskId);
  }
  function attachNextTo(anchor, taskId, mode) {
    if (!anchor || !anchor.parentElement) return;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = BTN_CLASS + " ant-btn ant-btn-default";
    btn.setAttribute("data-mode", mode);
    btn.setAttribute("data-task", String(taskId));
    btn.textContent = mode === "sample" ? "LA标注" : "LA批量";
    btn.title = mode === "sample" ? "对当前图片运行 LocateAnything 标框" : "对任务图片批量运行 LocateAnything 标框";
    styleNeighbor(btn, anchor);
    ensureFlexParent(anchor);
    btn.onclick = function (e) {
      e.preventDefault();
      e.stopPropagation();
      openModal(taskId, parseSampleId() || null);
    };
    if (anchor.getAttribute && anchor.getAttribute("data-labelu-ai-annotate-extension-anchor") !== null) {
      anchor.parentElement.insertBefore(btn, anchor);
      return;
    }
    var ref = insertionReference(anchor, 15);
    anchor.parentElement.insertBefore(btn, ref.nextSibling);
  }
  function clearStale() {
    var nodes = document.querySelectorAll("." + BTN_CLASS);
    for (var i = 0; i < nodes.length; i++) nodes[i].remove();
  }
  function inject() {
    var taskId = parseTaskId();
    if (!taskId || taskId === "0") {
      clearStale();
      return;
    }
    var isAnnot = isAnnotationSamplePage();
    if (!isAnnot && !isTaskRootPage() && !isTaskEditPage()) {
      clearStale();
      return;
    }
    var mode = isAnnot ? "sample" : "task";
    var anchor = isAnnot ? findAnnotationToolbarInsertPoint() || findSingleAiLabelButton() : findBatchAiButton();
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
    closeModal();
    schedule();
  });
})();
