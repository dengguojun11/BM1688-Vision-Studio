/**
 * 任务详情 /tasks/:id（样本列表）：将「数据库预设」链入顶栏工具区左侧，
 * 与「数据总览」「协同标注」「数据导出」等同一文档流，避免遮挡底部分页。
 */
(function () {
  var EXT_ID = "labelu-task-toolbar-ext";

  function pathOnly() {
    return ((location.pathname || "").split("?")[0] || "/").replace(/\/+$/, "") || "/";
  }

  function isTaskSamplesPage() {
    return /^\/tasks\/\d+$/i.test(pathOnly());
  }

  function findExportLikeButton() {
    var buttons = document.querySelectorAll("button.ant-btn");
    for (var i = 0; i < buttons.length; i++) {
      var b = buttons[i];
      if (!b || b.tagName !== "BUTTON") continue;
      if (b.closest && b.closest("#" + EXT_ID)) continue;
      var t = (b.textContent || "").replace(/\s+/g, " ").trim();
      if (!t) continue;
      if (t.indexOf("数据导出") >= 0) return b;
      if (/^export$/i.test(t) || /^data\s*export$/i.test(t)) return b;
    }
    return null;
  }

  function findCollaborationButton() {
    var buttons = document.querySelectorAll("button.ant-btn");
    for (var i = 0; i < buttons.length; i++) {
      var b = buttons[i];
      if (b.closest && b.closest("#" + EXT_ID)) continue;
      var t = (b.textContent || "").replace(/\s+/g, " ").trim();
      if (t.indexOf("协同标注") >= 0 || /collaborat/i.test(t)) return b;
    }
    return null;
  }

  /**
   * 找到同时包含「数据总览」文案与多个操作按钮的顶栏行。
   */
  function findToolbarRow(anchorBtn) {
    if (!anchorBtn) return null;
    var p = anchorBtn;
    for (var d = 0; d < 18 && p; d++) {
      p = p.parentElement;
      if (!p) break;
      var txt = p.innerText || "";
      var hasOverview = txt.indexOf("数据总览") >= 0 || /\boverview\b/i.test(txt);
      if (!hasOverview) continue;
      var btns = p.querySelectorAll("button.ant-btn");
      if (btns.length >= 2) return p;
    }
    return null;
  }

  function ensureExtHost(row) {
    if (!row) return null;
    var first = row.firstElementChild;
    if (!first || !(first instanceof HTMLElement)) return null;
    var existing = document.getElementById(EXT_ID);
    if (existing) {
      if (!first.contains(existing)) {
        first.appendChild(existing);
      }
      var geometryToggle = document.getElementById("labelu-ai-geometry-toggle-wrap");
      if (geometryToggle) geometryToggle.remove();
      return existing;
    }
    var ext = document.createElement("span");
    ext.id = EXT_ID;
    ext.setAttribute("data-labelu-injected", "1");
    ext.style.cssText = [
      "display:inline-flex",
      "align-items:center",
      "flex-wrap:wrap",
      "gap:6px 12px",
      "margin-left:16px",
      "vertical-align:middle",
      "font-size:13px",
      "line-height:22px",
    ].join(";");
    ext.innerHTML =
      '<a href="/annotation-settings" style="color:#1677ff;text-decoration:none;">数据库：预设 / 全局维度</a>';
    first.appendChild(ext);
    var geometryToggle = document.getElementById("labelu-ai-geometry-toggle-wrap");
    if (geometryToggle) geometryToggle.remove();
    return ext;
  }

  function removeOrphan() {
    var el = document.getElementById(EXT_ID);
    if (el && !isTaskSamplesPage()) {
      el.remove();
    }
  }

  function tryMount() {
    if (!isTaskSamplesPage()) {
      removeOrphan();
      return;
    }

    var anchor = findExportLikeButton() || findCollaborationButton();
    var row = findToolbarRow(anchor);
    if (!row) return;

    var existing = document.getElementById(EXT_ID);
    if (existing && existing.isConnected) {
      if (row.contains(existing)) {
        var geometryToggle = document.getElementById("labelu-ai-geometry-toggle-wrap");
        if (geometryToggle) geometryToggle.remove();
        return;
      }
      existing.remove();
    }

    ensureExtHost(row);

    var legacy = document.getElementById("labelu-quick-links");
    if (legacy) legacy.style.display = "none";
  }

  var timer = null;
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(function () {
      try {
        tryMount();
      } catch (e) {}
    }, 120);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", schedule);
  } else {
    schedule();
  }

  var obs = new MutationObserver(schedule);
  obs.observe(document.getElementById("root") || document.body, { childList: true, subtree: true });
  window.addEventListener("popstate", schedule);
  setTimeout(schedule, 400);
  setTimeout(schedule, 1200);
  setTimeout(schedule, 2800);
})();
