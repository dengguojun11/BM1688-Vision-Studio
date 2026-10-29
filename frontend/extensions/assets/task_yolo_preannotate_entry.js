/**
 * 任务详情 /tasks/:id（样本列表工具栏）、任务编辑 /tasks/:id/edit、单图标注 /tasks/:id/samples/:sid
 * 在「AI 批量标注」或「批量导出」旁插入「YOLO批量标注」。
 * - 单图页：弹窗内选模型并提交当前样本预标注（不跳转）。
 * - 任务列表/编辑页：弹窗内配置并提交整任务批量预标注（与列表分页无关）；关闭弹窗后作业在后台继续，完成后自动刷新当前页。
 */
(function () {
  var BTN_CLASS = "labelu-yolo-preannotate-entry";
  var API = "/api/v1";
  var MODAL_ID = "labelu-yolo-preannotate-modal-root";
  var CSS_ID = "labelu-yolo-preannotate-modal-css";

  /** 单图弹窗：按任务记住上次模型 / conf / 分类筛选 */
  function yoloModalPrefsKey(taskId) {
    return "labelu_yolo_sample_modal_prefs_" + String(taskId);
  }

  function readYoloModalPrefs(taskId) {
    try {
      var raw = localStorage.getItem(yoloModalPrefsKey(taskId));
      if (!raw) return null;
      var o = JSON.parse(raw);
      if (!o || typeof o !== "object") return null;
      return o;
    } catch (e) {
      return null;
    }
  }

  function writeYoloModalPrefs(taskId, o) {
    try {
      localStorage.setItem(yoloModalPrefsKey(taskId), JSON.stringify(o));
    } catch (e) {}
  }

  var modalState = {
    pollTimer: null,
    /** 整任务批量：关闭弹窗后轮询仍继续，直到 COMPLETED/FAILED */
    batchBgTimer: null,
    selected: null,
    allModelItems: [],
    taskLabels: [],
    taskId: null,
    sampleId: null,
    openedPath: null,
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

  function isTaskRootPage() {
    return /^\/tasks\/\d+$/i.test(pathOnly());
  }

  function isTaskEditPage() {
    return /^\/tasks\/\d+\/edit$/i.test(pathOnly());
  }

  function isAnnotationSamplePage() {
    return /^\/tasks\/\d+\/samples\/\d+$/i.test(pathOnly());
  }

  function normalizeText(el) {
    return (el.textContent || "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function findBatchAiButton() {
    var nodes = document.querySelectorAll(
      "button, a.ant-btn, span.ant-btn, .ant-btn, [role='button']"
    );
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.closest && (el.closest("." + BTN_CLASS) || el.closest(".labelu-preannotate-menu"))) continue;
      var t = normalizeText(el);
      if (!t || t.indexOf("YOLO") >= 0) continue;
      if (t.indexOf("AI") < 0) continue;
      if (t.indexOf("批量") >= 0) return el;
      if (/ai\s*batch/i.test(t) || /batch.*ai/i.test(t)) return el;
    }
    return null;
  }

  function findBatchExportButton() {
    var nodes = document.querySelectorAll(
      "button, a.ant-btn, span.ant-btn, .ant-btn, [role='button']"
    );
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.closest && (el.closest("." + BTN_CLASS) || el.closest(".labelu-preannotate-menu"))) continue;
      var t = normalizeText(el);
      if (!t || t.indexOf("YOLO") >= 0) continue;
      if (t.indexOf("批量") >= 0 && t.indexOf("导出") >= 0) return el;
      if (/batch/i.test(t) && /export/i.test(t)) return el;
    }
    return null;
  }

  function findSingleAiLabelButton() {
    var nodes = document.querySelectorAll(
      "button, a.ant-btn, span.ant-btn, .ant-btn, [role='button']"
    );
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.closest && (el.closest("." + BTN_CLASS) || el.closest(".labelu-preannotate-menu"))) continue;
      var t = normalizeText(el);
      if (!t || t.indexOf("YOLO") >= 0) continue;
      if (t.indexOf("AI") < 0) continue;
      if (t.indexOf("批量") >= 0) continue;
      if (t.indexOf("设置") >= 0) continue;
      if (t.indexOf("标注") >= 0 || t.indexOf("补全") >= 0) return el;
      if (/^ai\s*label/i.test(t) || /ai.*label/i.test(t)) return el;
    }
    return null;
  }

  /** 与 React 标注页同一行工具栏末尾对齐，避免按钮插进纵向布局中间 */
  function findAnnotationToolbarInsertPoint() {
    return document.querySelector("[data-labelu-ai-annotate-extension-anchor]");
  }

  function styleNeighbor(yolo, ref) {
    var cs = window.getComputedStyle(ref);
    yolo.style.boxSizing = "border-box";
    yolo.style.minHeight = cs.minHeight;
    yolo.style.padding = cs.padding;
    yolo.style.borderRadius = cs.borderRadius;
    yolo.style.fontSize = cs.fontSize;
    yolo.style.fontWeight = cs.fontWeight || "500";
    yolo.style.lineHeight = cs.lineHeight;
    yolo.style.cursor = "pointer";
    yolo.style.fontFamily = cs.fontFamily;
    yolo.style.background = "#fff";
    yolo.style.border = "1px solid #d9d9d9";
    yolo.style.color = "rgba(0, 0, 0, 0.88)";
    yolo.style.marginLeft = "8px";
    yolo.style.transition = "all 0.2s cubic-bezier(0.645, 0.045, 0.355, 1)";
    yolo.onmouseenter = function () {
      yolo.style.borderColor = "#4096ff";
      yolo.style.color = "#4096ff";
    };
    yolo.onmouseleave = function () {
      yolo.style.borderColor = "#d9d9d9";
      yolo.style.color = "rgba(0, 0, 0, 0.88)";
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

  function guessTaskNameForQuery() {
    var raw = (document.title || "").trim();
    if (!raw) return "";
    var t = raw.split("|")[0].trim();
    if (!t || /^labelu$/i.test(t)) return "";
    if (t.length > 120) return "";
    return t;
  }

  function sameMount(existing, anchor, mode, taskId, sampleId) {
    if (!existing || !anchor) return false;
    if (insertionReference(anchor, 10).nextElementSibling !== existing) return false;
    if (existing.getAttribute("data-mode") !== mode) return false;
    if (existing.getAttribute("data-task") !== String(taskId)) return false;
    if (mode === "sample" && existing.getAttribute("data-sample") !== String(sampleId || "")) return false;
    return true;
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

  /* ---------- 单图页：弹窗 ---------- */

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

  function modelCategory(it) {
    if (it.category) return String(it.category);
    return it.kind === "trained" ? "本系统训练" : "未分类";
  }

  function modelSubline(it) {
    var compatibility = modelCompatibilitySubline(it);
    if (it.kind === "imported") {
      return ["分类：" + modelCategory(it), compatibility].filter(Boolean).join(" · ");
    }
    var d = (it.detail || "").trim();
    return [d, it.imgsz ? "输入尺寸 " + it.imgsz : "", compatibility].filter(Boolean).join(" · ");
  }

  function modelCompatibilitySubline(it) {
    var matched = it.matched_labels || [];
    var missing = it.missing_task_labels || [];
    var extra = it.extra_model_labels || [];
    if (!matched.length) return "";
    if (!missing.length && !extra.length) return "标签完全匹配";
    var parts = ["可使用 " + matched.length + " 个共有标签"];
    if (missing.length) parts.push("需人工补充 " + missing.length + " 个标签");
    if (extra.length) parts.push("忽略模型多出的 " + extra.length + " 个标签");
    return parts.join(" · ");
  }

  function modelMetricsSubline(it) {
    var m = it.training_val_metrics || null;
    if (!m) return it.kind === "imported" ? "未评估（外部导入）" : "未读取到验证指标";
    var parts = [];
    [["map50_95", "mAP50-95"], ["map50", "mAP50"], ["precision", "P"], ["recall", "R"], ["fitness", "综合评分"]].forEach(function (pair) {
      var n = Number(m[pair[0]]);
      if (isFinite(n)) parts.push(pair[1] + " " + (n * 100).toFixed(1) + "%");
    });
    var recommended = recommendedConfValue(it);
    if (recommended !== null) parts.push("建议置信度 " + recommended.toFixed(2));
    return parts.length ? parts.join(" · ") : "未读取到验证指标";
  }

  function recommendedConfValue(item) {
    if (!item || item.recommended_conf == null || item.recommended_conf === "") return null;
    var n = Number(item.recommended_conf);
    return isFinite(n) ? n : null;
  }

  function applyRecommendedConf(item, inputId) {
    var n = recommendedConfValue(item);
    var input = document.getElementById(inputId);
    if (input && n !== null) input.value = n.toFixed(2);
  }

  function ensureModalCss() {
    if (document.getElementById(CSS_ID)) return;
    var st = document.createElement("style");
    st.id = CSS_ID;
    st.textContent =
      "#" +
      MODAL_ID +
      " .lym-backdrop{position:absolute;inset:0;background:rgba(0,0,0,.45)}" +
      "#" +
      MODAL_ID +
      " .lym-dialog{position:relative;margin:48px auto;max-width:560px;width:calc(100% - 32px);background:#fff;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.18);max-height:calc(100vh - 96px);display:flex;flex-direction:column;font-family:system-ui,sans-serif}" +
      "#" +
      MODAL_ID +
      " .lym-head{display:flex;align-items:center;justify-content:space-between;padding:14px 16px;border-bottom:1px solid #f0f0f0;flex-shrink:0}" +
      "#" +
      MODAL_ID +
      " .lym-head h2{margin:0;font-size:16px;font-weight:600}" +
      "#" +
      MODAL_ID +
      " .lym-body{padding:14px 16px;overflow:auto;flex:1}" +
      "#" +
      MODAL_ID +
      " .lym-foot{padding:10px 16px 14px;border-top:1px solid #f0f0f0;text-align:right;flex-shrink:0}" +
      "#" +
      MODAL_ID +
      " label.lym-l{display:block;font-size:12px;color:#666;margin-bottom:4px}" +
      "#" +
      MODAL_ID +
      " input.lym-in, #" +
      MODAL_ID +
      " select.lym-in{width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid #d9d9d9;border-radius:8px;font-size:13px}" +
      "#" +
      MODAL_ID +
      " .lym-models{max-height:min(40vh,320px);overflow:auto;border:1px solid #e8e8e8;border-radius:8px;margin-top:8px}" +
      "#" +
      MODAL_ID +
      " .lym-models label{display:flex;gap:10px;align-items:flex-start;padding:10px 12px;border-bottom:1px solid #f5f5f5;cursor:pointer;margin:0;font-size:13px}" +
      "#" +
      MODAL_ID +
      " .lym-models label:hover{background:#fafafa}" +
      "#" +
      MODAL_ID +
      " .lym-tag{font-size:11px;background:#f5f5f5;padding:2px 6px;border-radius:4px;color:#555}" +
      "#" +
      MODAL_ID +
      " .lym-tagc{background:#e6f4ff;color:#0958d9}" +
      "#" +
      MODAL_ID +
      " pre.lym-log{background:#fafafa;border:1px solid #e8e8e8;border-radius:8px;padding:8px;font-size:12px;max-height:120px;overflow:auto;white-space:pre-wrap;margin:10px 0 0}" +
      "#" +
      MODAL_ID +
      " .lym-muted{color:#666;font-size:12px;margin:0 0 8px}" +
      "#" +
      MODAL_ID +
      " .lym-row{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px;align-items:flex-end}" +
      "#" +
      MODAL_ID +
      " .lym-row > div{flex:1;min-width:120px}" +
      "#" +
      MODAL_ID +
      " button.lym-btn{padding:6px 14px;border-radius:8px;border:1px solid #d9d9d9;background:#fff;cursor:pointer;font-size:13px}" +
      "#" +
      MODAL_ID +
      " button.lym-primary{background:#1677ff;color:#fff;border-color:#1677ff}" +
      "#" +
      MODAL_ID +
      " button:disabled{opacity:.5;cursor:not-allowed}";
    document.head.appendChild(st);
  }

  function clearPoll() {
    if (modalState.pollTimer) {
      clearInterval(modalState.pollTimer);
      modalState.pollTimer = null;
    }
  }

  function clearBatchBackgroundPoll() {
    if (modalState.batchBgTimer) {
      clearInterval(modalState.batchBgTimer);
      modalState.batchBgTimer = null;
    }
  }

  function closeYoloModal() {
    clearPoll();
    /* 整任务批量：不清理 batchBgTimer，关闭弹窗后后台继续轮询 */
    var root = document.getElementById(MODAL_ID);
    if (root) root.style.display = "none";
    document.body.style.overflow = "";
  }

  function modalLog(msg) {
    var el = document.getElementById("lym-log");
    if (!el) return;
    el.textContent +=
      new Date().toLocaleTimeString() + " " + msg + "\n";
    el.scrollTop = el.scrollHeight;
  }

  function ensureModalDom() {
    ensureModalCss();
    var root = document.getElementById(MODAL_ID);
    if (root) return root;
    root = document.createElement("div");
    root.id = MODAL_ID;
    root.style.cssText =
      "display:none;position:fixed;inset:0;z-index:20000;font-family:system-ui,sans-serif";
    root.innerHTML =
      '<div class="lym-backdrop" data-close="1"></div>' +
      '<div class="lym-dialog" role="dialog" aria-modal="true" aria-labelledby="lym-title">' +
      '<div class="lym-head">' +
      '<h2 id="lym-title">YOLO 预标注（当前图片）</h2>' +
      '<button type="button" class="lym-btn" id="lym-close-x" aria-label="关闭">✕</button>' +
      "</div>" +
      '<div class="lym-body">' +
      '<p class="lym-muted">使用与主站相同的登录 Token（localStorage <code>token</code>）。若请求 401，请重新登录。</p>' +
      '<label class="lym-l" for="lym-token">Authorization（可选，修改后会写回 localStorage）</label>' +
      '<input class="lym-in" id="lym-token" type="text" placeholder="Bearer …" autocomplete="off" />' +
      '<div id="lym-taskline" style="margin:10px 0;font-size:14px"></div>' +
      '<div class="lym-row" id="lym-source-row" style="display:none">' +
      "<div>" +
      '<label class="lym-l" for="lym-source">模型来源</label>' +
      '<select class="lym-in" id="lym-source">' +
      '<option value="recommended">当前任务推荐</option>' +
      '<option value="__ALL__">全部匹配模型</option>' +
      '<option value="trained">本系统训练</option>' +
      '<option value="imported">外部导入</option>' +
      "</select></div></div>" +
      '<div class="lym-row" id="lym-cat-row" style="display:none">' +
      "<div>" +
      '<label class="lym-l" for="lym-cat">模型分类</label>' +
      '<select class="lym-in" id="lym-cat"></select>' +
      "</div></div>" +
      '<div class="lym-row" id="lym-batch-only" style="display:none">' +
      "<div>" +
      '<label class="lym-l" for="lym-include-done">包含已标注样本（整任务）</label>' +
      '<select class="lym-in" id="lym-include-done">' +
      '<option value="0">否（仅未标注 NEW）</option>' +
      '<option value="1" selected>是（含已标注 DONE）</option>' +
      "</select></div></div>" +
      '<p class="lym-muted" style="margin-top:4px">只写入模型与任务共有的矩形框标签；模型多出的类别自动忽略，模型缺少的任务标签需人工补充。</p>' +
      '<div class="lym-models" id="lym-models"></div>' +
      '<div class="lym-row" style="margin-top:12px">' +
      "<div>" +
      '<label class="lym-l" for="lym-conf">置信度</label>' +
      '<input class="lym-in" id="lym-conf" type="number" min="0.05" max="1" step="0.05" value="0.25" />' +
      "</div></div>" +
      '<pre class="lym-log" id="lym-log"></pre>' +
      "</div>" +
      '<div class="lym-foot">' +
      '<button type="button" class="lym-btn" id="lym-btn-cancel">取消</button> ' +
      '<button type="button" class="lym-btn lym-primary" id="lym-btn-reload">重新加载模型</button> ' +
      '<button type="button" class="lym-btn lym-primary" id="lym-btn-run" disabled>开始预标注</button>' +
      "</div></div>";

    root.addEventListener("click", function (e) {
      if (e.target && e.target.getAttribute && e.target.getAttribute("data-close"))
        closeYoloModal();
    });
    document.body.appendChild(root);
    document.getElementById("lym-close-x").onclick = closeYoloModal;
    document.getElementById("lym-btn-cancel").onclick = closeYoloModal;
    document.getElementById("lym-token").addEventListener("change", function () {
      setToken(document.getElementById("lym-token").value || "");
      if (modalState.taskId) {
        refreshModalTaskLine(modalState.taskId, guessTaskNameForQuery());
        loadModalModels();
      }
    });
    document.getElementById("lym-cat").addEventListener("change", function () {
      renderModalModelList(null);
    });
    document.getElementById("lym-source").addEventListener("change", function () {
      renderModalModelList(null);
    });
    document.getElementById("lym-conf").addEventListener("change", persistModalPrefsIfReady);
    document.getElementById("lym-include-done").addEventListener("change", persistModalPrefsIfReady);
    document.getElementById("lym-btn-reload").onclick = function () {
      loadModalModels();
    };
    document.getElementById("lym-btn-run").onclick = runModalJob;
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && document.getElementById(MODAL_ID).style.display === "block")
        closeYoloModal();
    });
    return root;
  }

  function buildCategoryOptions(items) {
    var set = {};
    for (var i = 0; i < items.length; i++) set[modelCategory(items[i])] = true;
    var keys = Object.keys(set);
    keys.sort(function (a, b) {
      if (a === "本系统训练") return -1;
      if (b === "本系统训练") return 1;
      if (a === "未分类") return 1;
      if (b === "未分类") return -1;
      return a.localeCompare(b, "zh-CN");
    });
    var sel = document.getElementById("lym-cat");
    sel.innerHTML = "";
    var o0 = document.createElement("option");
    o0.value = "__ALL__";
    o0.textContent = "全部分类（" + items.length + "）";
    sel.appendChild(o0);
    for (var j = 0; j < keys.length; j++) {
      var k = keys[j];
      var n = 0;
      for (var t = 0; t < items.length; t++) if (modelCategory(items[t]) === k) n++;
      var o = document.createElement("option");
      o.value = k;
      o.textContent = k + "（" + n + "）";
      sel.appendChild(o);
    }
  }

  function persistModalPrefsIfReady() {
    var taskId = modalState.taskId;
    if (!taskId || !modalState.selected) return;
    var conf = parseFloat(document.getElementById("lym-conf").value || "0.25");
    if (isNaN(conf)) conf = 0.25;
    var o = {
      model_kind: modalState.selected.kind,
      model_id: modalState.selected.id,
      conf: conf,
      cat: document.getElementById("lym-cat").value,
      source: document.getElementById("lym-source").value,
    };
    var batchRow = document.getElementById("lym-batch-only");
    if (batchRow && batchRow.style.display !== "none") {
      o.include_done = document.getElementById("lym-include-done").value === "1";
    }
    writeYoloModalPrefs(taskId, o);
  }

  /** autoPr: 打开弹窗时从 localStorage 恢复选中；传 null 表示不自动勾选 */
  function renderModalModelList(autoPr) {
    var box = document.getElementById("lym-models");
    var selVal = document.getElementById("lym-cat").value;
    var sourceVal = document.getElementById("lym-source").value;
    box.innerHTML = "";
    modalState.selected = null;
    document.getElementById("lym-btn-run").disabled = true;
    var shown = 0;
    for (var idx = 0; idx < modalState.allModelItems.length; idx++) {
      var it = modalState.allModelItems[idx];
      var cat = modelCategory(it);
      if (selVal !== "__ALL__" && cat !== selVal) continue;
      if (sourceVal === "recommended" && !it.recommended) continue;
      if (sourceVal !== "__ALL__" && sourceVal !== "recommended" && it.kind !== sourceVal) continue;
      shown++;
      var id = "lym_m_" + idx;
      var lab = document.createElement("label");
      var inp = document.createElement("input");
      inp.type = "radio";
      inp.name = "lym_pick";
      inp.id = id;
      var div = document.createElement("div");
      div.innerHTML =
        (it.recommended ? '<span class="lym-tag lym-tagc">当前任务推荐</span> ' : "") +
        '<span class="lym-tag">' +
        esc(it.kind) +
        '</span> <span class="lym-tag lym-tagc">' +
        esc(cat) +
        "</span> <strong>" +
        esc(it.title) +
        '</strong><br/><span style="color:#888;font-size:12px">' +
        esc(modelSubline(it)) +
        '</span><br/><span style="color:#555;font-size:12px">验证指标：' +
        esc(modelMetricsSubline(it)) +
        "</span>" +
        (it.labels && it.labels.length ? '<br/><span style="color:#666;font-size:12px">标签：' + esc(it.labels.join(", ")) + "</span>" : "") +
        "";
      lab.appendChild(inp);
      lab.appendChild(div);
      (function (item) {
        inp.addEventListener("change", function () {
          modalState.selected = { kind: item.kind, id: item.id };
          applyRecommendedConf(item, "lym-conf");
          document.getElementById("lym-btn-run").disabled = false;
          persistModalPrefsIfReady();
        });
      })(it);
      if (
        autoPr &&
        autoPr.model_kind === it.kind &&
        String(autoPr.model_id) === String(it.id)
      ) {
        inp.checked = true;
        modalState.selected = { kind: it.kind, id: it.id };
        applyRecommendedConf(it, "lym-conf");
        document.getElementById("lym-btn-run").disabled = false;
      }
      box.appendChild(lab);
    }
    if (!shown) {
      var empty = document.createElement("div");
      empty.className = "lym-muted";
      empty.style.padding = "14px";
      empty.textContent = "当前筛选条件下没有包含任务标签的模型";
      box.appendChild(empty);
    }
  }

  function refreshModalTaskLine(taskId, hintName) {
    var el = document.getElementById("lym-taskline");
    var t = getToken();
    if (!t) {
      el.innerHTML =
        "<strong>任务 ID " +
        esc(taskId) +
        "</strong> · " +
        esc(hintName || "填写 Token 后可显示任务名称");
      return;
    }
    el.innerHTML = "加载任务信息…";
    fetch(API + "/tasks/" + taskId, {
      headers: { Authorization: t },
      cache: "no-store",
    })
      .then(function (r) {
        return r.json().then(function (j) {
          if (!r.ok) throw new Error(j.msg || r.statusText);
          var name = j.data && j.data.name ? String(j.data.name) : "";
          el.innerHTML =
            "<strong>" +
            esc(name || hintName || "（未命名任务）") +
            "</strong> · 任务 ID " +
            esc(taskId);
        });
      })
      .catch(function (e) {
        el.innerHTML =
          "<strong>任务 ID " +
          esc(taskId) +
          "</strong> · " +
          esc(hintName || "") +
          " <span style=\"color:#c00\">" +
          esc(e.message) +
          "</span>";
      });
  }

  function loadModalModels() {
    var taskId = modalState.taskId;
    if (!taskId) return;
    modalState.selected = null;
    document.getElementById("lym-btn-run").disabled = true;
    document.getElementById("lym-models").innerHTML = "";
    modalState.allModelItems = [];
    modalState.taskLabels = [];
    document.getElementById("lym-cat-row").style.display = "none";
    document.getElementById("lym-source-row").style.display = "none";
    var staleTaskMatch = document.querySelector("[data-yolo-task-match]");
    if (staleTaskMatch) staleTaskMatch.remove();
    modalLog("加载任务 " + taskId + " 的模型列表…");
    var t = getToken();
    if (!t) {
      modalLog("错误: 请先填写 Token");
      return;
    }
    fetch(API + "/tasks/" + taskId + "/yolo_preannotate/models", {
      headers: { Authorization: t },
      cache: "no-store",
    })
      .then(function (r) {
        return r.json().then(function (j) {
          if (!r.ok) throw new Error(j.msg || r.statusText);
          var data = j.data || {};
          var items = data.items || [];
          modalState.taskLabels = data.task_labels || [];
          if (modalState.taskLabels.length) {
            var taskline = document.getElementById("lym-taskline");
            taskline.innerHTML +=
              ' <span data-yolo-task-match style="color:#666;font-size:12px">· 当前标签：' +
              esc(modalState.taskLabels.join(", ")) +
              "</span>";
          }
          if (!items.length) {
            modalLog("当前任务没有包含任一任务标签的模型，请先训练或导入对应模型。");
            return;
          }
          modalState.allModelItems = items;
          var pr = readYoloModalPrefs(taskId);
          buildCategoryOptions(items);
          document.getElementById("lym-source-row").style.display = "flex";
          var catSel = document.getElementById("lym-cat");
          if (pr && pr.cat) {
            var okCat = false;
            for (var ci = 0; ci < catSel.options.length; ci++) {
              if (catSel.options[ci].value === pr.cat) {
                okCat = true;
                break;
              }
            }
            catSel.value = okCat ? pr.cat : "__ALL__";
          } else {
            catSel.value = "__ALL__";
          }
          var sourceSel = document.getElementById("lym-source");
          sourceSel.value = pr && pr.source ? pr.source : "recommended";
          if (sourceSel.value === "recommended" && !items.some(function (item) { return item.recommended; })) {
            sourceSel.value = "__ALL__";
          }
          document.getElementById("lym-cat-row").style.display = "flex";
          renderModalModelList(pr);
          if (
            pr &&
            pr.model_kind != null &&
            pr.model_id != null &&
            !modalState.selected
          ) {
            document.getElementById("lym-cat").value = "__ALL__";
            renderModalModelList(pr);
          }
          if (pr && modalState.selected)
            modalLog("已恢复上次选择的模型与参数。");
          else modalLog("共 " + items.length + " 个模型，请选择一项后点击「开始预标注」。");
        });
      })
      .catch(function (e) {
        modalLog("错误: " + e.message);
      });
  }

  function runModalJob() {
    var taskId = modalState.taskId;
    var sampleId = modalState.sampleId;
    if (!taskId || !modalState.selected) return;
    if (sampleId) {
      var currentSampleId = parseSampleId();
      if (!currentSampleId || String(currentSampleId) !== String(sampleId) || modalState.openedPath !== pathOnly()) {
        modalLog("错误: 当前图片已切换，请重新打开 YOLO 预标注。");
        return;
      }
    }
    var conf = parseFloat(document.getElementById("lym-conf").value || "0.25");
    var isBatch = !sampleId;
    var payload = {
      model_kind: modalState.selected.kind,
      model_id: modalState.selected.id,
      overwrite: true,
      include_done_samples: true,
      conf: conf,
    };
    if (isBatch) {
      payload.include_done_samples =
        document.getElementById("lym-include-done").value === "1";
    } else {
      payload.include_done_samples = true;
      payload.sample_ids = [parseInt(sampleId, 10)];
      payload.sample_id = parseInt(sampleId, 10);
    }
    clearPoll();
    if (isBatch) clearBatchBackgroundPoll();
    modalLog(
      "提交作业: kind=" +
        modalState.selected.kind +
        " id=" +
        modalState.selected.id +
        (isBatch ? "（整任务）" : " sample_id=" + sampleId)
    );
    var t = getToken();
    if (!t) {
      modalLog("错误: 请先填写 Token");
      return;
    }
    var postUrl = API + "/tasks/" + taskId + "/yolo_preannotate_job";
    if (!isBatch)
      postUrl += "?sample_id=" + encodeURIComponent(String(sampleId));
    fetch(postUrl, {
      method: "POST",
      headers: {
        Authorization: t,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    })
      .then(function (r) {
        return r.json().then(function (j) {
          if (!r.ok) throw new Error(j.msg || r.statusText);
          persistModalPrefsIfReady();
          var job = j.data;
          modalLog("作业已创建 id=" + job.id + "，轮询状态…");
          if (isBatch)
            modalLog("可关闭本弹窗，作业将在后台继续直至完成（完成后将刷新本页）。");
          var jid = job.id;
          function pollOnce() {
            fetch(API + "/tasks/" + taskId + "/yolo_preannotate_job/" + jid, {
              headers: { Authorization: getToken() },
              cache: "no-store",
            })
              .then(function (r2) {
                return r2.json().then(function (j2) {
                  if (!r2.ok) throw new Error(j2.msg || r2.statusText);
                  var st = j2.data;
                  modalLog(
                    "status=" +
                      st.status +
                      " processed=" +
                      st.processed_count +
                      "/" +
                      st.sample_count +
                      " ok=" +
                      st.success_count +
                      " fail=" +
                      st.failed_count
                  );
                  if (st.status === "COMPLETED" || st.status === "FAILED") {
                    if (isBatch) {
                      clearBatchBackgroundPoll();
                    } else {
                      clearPoll();
                    }
                    if (st.status === "COMPLETED") {
                      if (st.error_message) modalLog("提示: " + st.error_message);
                      if (isBatch) {
                        modalLog("完成，即将刷新任务页以更新列表…");
                        window.setTimeout(function () {
                          window.location.reload();
                        }, 450);
                      } else {
                        modalLog("完成，正在重新加载当前页…");
                        closeYoloModal();
                        window.setTimeout(function () {
                          window.location.reload();
                        }, 450);
                      }
                    } else {
                      modalLog(
                        "作业失败" +
                          (st.error_message ? ": " + st.error_message : "")
                      );
                    }
                  }
                });
              })
              .catch(function (e) {
                if (isBatch) clearBatchBackgroundPoll();
                else clearPoll();
                modalLog("轮询错误: " + e.message);
              });
          }
          var timer = setInterval(pollOnce, 1200);
          if (isBatch) {
            modalState.batchBgTimer = timer;
            pollOnce();
          } else {
            modalState.pollTimer = timer;
          }
        });
      })
      .catch(function (e) {
        modalLog("错误: " + e.message);
      });
  }

  function openYoloModal(taskId, sampleId) {
    ensureModalDom();
    clearPoll();
    modalState.taskId = taskId;
    modalState.sampleId = sampleId;
    modalState.openedPath = pathOnly();
    modalState.selected = null;
    modalState.allModelItems = [];

    var saved = readYoloModalPrefs(taskId);
    var titleEl = document.getElementById("lym-title");
    var batchOnly = document.getElementById("lym-batch-only");
    var runBtn = document.getElementById("lym-btn-run");
    if (sampleId) {
      titleEl.textContent = "YOLO 预标注（当前图片）";
      if (batchOnly) batchOnly.style.display = "none";
      runBtn.textContent = "开始预标注";
    } else {
      titleEl.textContent = "YOLO 批量预标注（整任务）";
      if (batchOnly) {
        batchOnly.style.display = "flex";
        var inc = document.getElementById("lym-include-done");
        if (inc && saved && typeof saved.include_done === "boolean")
          inc.value = saved.include_done ? "1" : "0";
        else if (inc) inc.value = "1";
      }
      runBtn.textContent = "开始批量预标注";
    }

    var root = document.getElementById(MODAL_ID);
    document.getElementById("lym-log").textContent = "";
    document.getElementById("lym-token").value = getToken();
    var confVal = 0.25;
    if (saved && saved.conf != null) {
      var c0 = parseFloat(saved.conf);
      if (!isNaN(c0) && c0 >= 0.05 && c0 <= 1) confVal = c0;
    }
    document.getElementById("lym-conf").value = String(confVal);
    document.getElementById("lym-btn-run").disabled = true;
    document.getElementById("lym-models").innerHTML = "";
    document.getElementById("lym-cat-row").style.display = "none";

    var hint = guessTaskNameForQuery();
    refreshModalTaskLine(taskId, hint);
    root.style.display = "block";
    document.body.style.overflow = "hidden";
    loadModalModels();
  }

  function attachNextTo(anchor, taskId, mode, sampleId) {
    if (!anchor || !anchor.parentElement) return false;
    var p = anchor.parentElement;
    var toolbarSlot =
      anchor.getAttribute &&
      anchor.getAttribute("data-labelu-ai-annotate-extension-anchor") !== null;

    var yolo = document.createElement("button");
    yolo.type = "button";
    yolo.className = BTN_CLASS + " ant-btn ant-btn-default";
    yolo.setAttribute("data-mode", mode);
    yolo.setAttribute("data-task", String(taskId));
    if (mode === "sample") yolo.setAttribute("data-sample", String(sampleId || ""));
    if (mode === "sample") {
      yolo.setAttribute("aria-label", "YOLO预标注（当前图片）");
      yolo.textContent = "YOLO预标注";
      yolo.title = "仅对当前打开的这一张图写入 YOLO 预标注（弹窗）";
    } else {
      yolo.setAttribute("aria-label", "YOLO批量标注");
      yolo.textContent = "YOLO批量标注";
      yolo.title =
        "弹窗内配置并对任务内全部图片执行 YOLO 批量预标注（与列表当前页无关）；提交后可关闭弹窗，后台继续直至完成";
    }
    styleNeighbor(yolo, anchor);
    ensureFlexParent(anchor);

    yolo.onclick = function (e) {
      e.preventDefault();
      e.stopPropagation();
      var currentSampleId = parseSampleId();
      if (mode === "sample" && !currentSampleId) {
        schedule();
        return;
      }
      if (currentSampleId) openYoloModal(taskId, currentSampleId);
      else openYoloModal(taskId, null);
    };

    if (toolbarSlot) {
      p.insertBefore(yolo, anchor);
      return true;
    }

    var ref = insertionReference(anchor, 10);
    if (ref.nextSibling) p.insertBefore(yolo, ref.nextSibling);
    else p.appendChild(yolo);
    return true;
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

    var sampleId = parseSampleId();
    var isAnnot = isAnnotationSamplePage();
    var isRoot = isTaskRootPage();
    var isEdit = isTaskEditPage();

    if (!isAnnot && !isRoot && !isEdit) {
      clearStale();
      return;
    }

    var mode = isAnnot ? "sample" : "task";
    var anchor = isAnnot
      ? findAnnotationToolbarInsertPoint() || findSingleAiLabelButton()
      : findBatchAiButton() || findBatchExportButton();
    var existing = document.querySelector("." + BTN_CLASS);

    if (existing && anchor && sameMount(existing, anchor, mode, taskId, sampleId))
      return;
    if (existing) existing.remove();
    if (!anchor) return;
    attachNextTo(anchor, taskId, mode, sampleId);
  }

  var timer = null;
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(function () {
      try {
        inject();
      } catch (e) {}
    }, 16);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", schedule);
  } else {
    schedule();
  }

  var obs = new MutationObserver(schedule);
  obs.observe(document.body, { childList: true, subtree: true });

  function handleRouteChanged() {
    clearPoll();
    clearBatchBackgroundPoll();
    closeYoloModal();
    schedule();
  }

  if (window.history) {
    ["pushState", "replaceState"].forEach(function (name) {
      var orig = window.history[name];
      if (typeof orig !== "function" || orig.__labeluYoloWrapped) return;
      var wrapped = function () {
        var ret = orig.apply(this, arguments);
        handleRouteChanged();
        return ret;
      };
      wrapped.__labeluYoloWrapped = true;
      window.history[name] = wrapped;
    });
  }

  window.addEventListener("popstate", handleRouteChanged);
})();
