/**
 * 任务编辑页「标注配置」：拉框工具内紧凑单行预设选择（数据库），
 * 仅在实际已应用预设（defaultRectPresetId + 有标签）时高亮对应 Tab；
 * 点击 Tab 才 POST apply-label-preset 并跳转刷新。
 */
(function () {
  return;
  var API = "/api/v1";

  function getToken() {
    return localStorage.getItem("token") || "";
  }

  function parseTaskId() {
    var m = location.pathname.match(/^\/tasks\/(\d+)\/edit/);
    return m ? m[1] : null;
  }

  function shouldRun() {
    var id = parseTaskId();
    if (!id || id === "0") return false;
    var h = location.hash || "";
    if (h.indexOf("upload") >= 0) return false;
    if (h.indexOf("config") >= 0) return true;
    return false;
  }

  function isRectLabelPanel(el) {
    var t = el.innerText || "";
    if (t.indexOf("标签配置") < 0) return false;
    if (
      t.indexOf("最小宽度") >= 0 ||
      t.indexOf("最小尺寸") >= 0 ||
      t.indexOf("最小高度") >= 0
    ) {
      return true;
    }
    return /\bW\b\s*\d|\bH\b\s*\d|宽度|高度/.test(t) && t.indexOf("最小") >= 0;
  }

  function findRectToolPanel() {
    var selectors = [
      '[role="tabpanel"]',
      ".ant-tabs-tabpane-active",
      '[class*="Tabs-tabpane-active"]',
      '[class*="tab-pane-active"]',
    ];
    for (var s = 0; s < selectors.length; s++) {
      var panels = document.querySelectorAll(selectors[s]);
      for (var i = 0; i < panels.length; i++) {
        if (isRectLabelPanel(panels[i])) return panels[i];
      }
    }
    return null;
  }

  /** 插在「最小尺寸」整块之后、「标签配置」之前，减少挤压下方表单 */
  function findInsertBeforeTarget(panel) {
    var all = panel.getElementsByTagName("*");
    var minEl = null;
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      if (
        el.children.length === 0 &&
        el.textContent &&
        el.textContent.trim() === "最小尺寸"
      ) {
        minEl = el;
        break;
      }
    }
    if (minEl) {
      var walk = minEl.parentElement;
      for (var d = 0; d < 12 && walk && panel.contains(walk); d++) {
        var nx = walk.nextElementSibling;
        if (nx && nx.innerText && nx.innerText.indexOf("标签配置") >= 0) {
          return nx;
        }
        if (
          nx &&
          nx.querySelector &&
          nx.innerText &&
          nx.innerText.indexOf("标签配置") >= 0
        ) {
          var lc = nx.querySelector("*");
          var sub = nx.getElementsByTagName("*");
          for (var j = 0; j < sub.length; j++) {
            if (
              sub[j].children.length === 0 &&
              sub[j].textContent &&
              sub[j].textContent.trim() === "标签配置"
            ) {
              return sub[j].parentElement || sub[j];
            }
          }
          return nx;
        }
        walk = walk.parentElement;
      }
    }
    for (var k = 0; k < all.length; k++) {
      var e2 = all[k];
      if (
        e2.children.length === 0 &&
        e2.textContent &&
        e2.textContent.trim() === "标签配置"
      ) {
        return e2.parentElement || e2;
      }
    }
    var h = panel.querySelector("h4, h5, .ant-typography");
    return h || panel.firstElementChild;
  }

  function parseTaskConfig(raw) {
    if (raw == null) return {};
    if (typeof raw === "object") return raw;
    if (typeof raw === "string") {
      try {
        var o = JSON.parse(raw);
        return typeof o === "object" && o ? o : {};
      } catch (e) {
        return {};
      }
    }
    return {};
  }

  function countRectAttributes(cfg) {
    var tools = cfg.tools;
    if (!Array.isArray(tools)) return 0;
    for (var i = 0; i < tools.length; i++) {
      if (tools[i].tool === "rectTool") {
        var c = tools[i].config;
        var a = c && c.attributes;
        return Array.isArray(a) ? a.length : 0;
      }
    }
    return 0;
  }

  function navigateReloadBypassCache() {
    try {
      var u = new URL(window.location.href);
      u.searchParams.set("__labelu_preset", String(Date.now()));
      window.location.replace(u.href);
    } catch (e) {
      window.location.reload();
    }
  }

  async function fetchJson(path, options) {
    var opts = options || {};
    var headers = Object.assign({}, opts.headers || {}, {
      Authorization: getToken(),
      "Content-Type": "application/json",
    });
    var r = await fetch(
      API + path,
      Object.assign({}, opts, {
        headers: headers,
        cache: "no-store",
      })
    );
    var text = await r.text();
    var json = null;
    try {
      json = text ? JSON.parse(text) : null;
    } catch (e) {}
    return { ok: r.ok, status: r.status, json: json, text: text };
  }

  async function resolvePresetsFromApi() {
    var presetRes = await fetchJson(
      "/annotation-taxonomy/label-presets?page=0&size=100"
    );
    if (!presetRes.ok || !presetRes.json || !presetRes.json.data) return [];
    var list = presetRes.json.data;
    return Array.isArray(list) ? list : [];
  }

  async function inject() {
    if (!shouldRun()) return;
    var taskId = parseTaskId();
    var panel = findRectToolPanel();
    if (!panel || panel.querySelector(".labelu-db-preset-tabs")) return;

    var taskRes = await fetchJson("/tasks/" + taskId);
    if (!taskRes.ok || !taskRes.json || !taskRes.json.data) return;
    var taskData = taskRes.json.data;
    if (taskData.media_type !== "IMAGE") return;

    var cfg = parseTaskConfig(taskData.config);
    var rectAttrCount = countRectAttributes(cfg);

    var fromCfg = cfg.rectLabelPresets;
    var presets = [];
    if (Array.isArray(fromCfg)) {
      presets = fromCfg.filter(function (x) {
        return (
          x &&
          typeof x === "object" &&
          typeof x.presetDbId === "number" &&
          x.presetDbId > 0
        );
      });
    }
    if (!presets.length) {
      var apiList = await resolvePresetsFromApi();
      presets = apiList.map(function (p) {
        return {
          id: p.slug,
          presetDbId: p.id,
          name: p.name,
        };
      });
    }
    if (!presets.length) return;

    var activeSlug =
      typeof cfg.defaultRectPresetId === "string"
        ? cfg.defaultRectPresetId
        : null;

    var initialIdx = -1;
    if (activeSlug && rectAttrCount > 0) {
      for (var k = 0; k < presets.length; k++) {
        if ((presets[k].id || presets[k].slug) === activeSlug) {
          initialIdx = k;
          break;
        }
      }
    }

    var wrap = document.createElement("div");
    wrap.className = "labelu-db-preset-tabs";
    wrap.style.cssText =
      "margin:6px 0 8px;padding:4px 8px;display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px;background:#f7f8fa;border:1px solid #e8e8e8;border-radius:4px;max-width:100%;box-sizing:border-box;";
    wrap.title =
      "点击预设名称写入下方「标签配置」并保存到任务；未点击则不会自动填入。";

    var lab = document.createElement("span");
    lab.style.cssText =
      "font-size:12px;color:#666;flex-shrink:0;font-weight:500;";
    lab.textContent = "预设";
    wrap.appendChild(lab);

    var strip = document.createElement("div");
    strip.className = "labelu-preset-tabstrip";
    strip.setAttribute("role", "tablist");
    strip.style.cssText =
      "display:flex;flex-wrap:wrap;align-items:center;gap:4px;flex:1;min-width:0;max-height:72px;overflow-y:auto;";

    var buttons = [];

    function tabStyle(on) {
      return on
        ? "padding:2px 8px;border:1px solid #1677ff;border-radius:4px;background:#e6f4ff;color:#1677ff;font-size:12px;line-height:1.4;cursor:pointer;white-space:nowrap;"
        : "padding:2px 8px;border:1px solid #d9d9d9;border-radius:4px;background:#fff;color:#595959;font-size:12px;line-height:1.4;cursor:pointer;white-space:nowrap;";
    }

    function setActive(idx) {
      for (var j = 0; j < buttons.length; j++) {
        var b = buttons[j];
        var on = idx >= 0 && j === idx;
        b.setAttribute("aria-selected", on ? "true" : "false");
        b.style.cssText = tabStyle(on);
      }
    }

    presets.forEach(function (p, idx) {
      var slug = p.id || p.slug || "";
      var pid = p.presetDbId;
      var btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("role", "tab");
      btn.textContent = p.name || slug || String(pid);
      btn.title = slug ? String(slug) : "点击应用该预设";
      btn.style.cssText = tabStyle(false);
      btn.onclick = async function () {
        setActive(idx);
        btn.disabled = true;
        try {
          var r = await fetchJson(
            "/tasks/" + taskId + "/annotation/apply-label-preset",
            {
              method: "POST",
              body: JSON.stringify({ preset_id: pid }),
            }
          );
          if (!r.ok) {
            var msg =
              (r.json && r.json.msg) || r.text || "HTTP " + r.status;
            throw new Error(msg);
          }
          navigateReloadBypassCache();
        } catch (e) {
          alert("应用预设失败：" + (e.message || String(e)));
          setActive(initialIdx);
        } finally {
          btn.disabled = false;
        }
      };
      buttons.push(btn);
      strip.appendChild(btn);
    });

    wrap.appendChild(strip);

    var a = document.createElement("a");
    a.href = "/annotation-settings";
    a.textContent = "管理";
    a.style.cssText =
      "font-size:12px;color:#1677ff;flex-shrink:0;text-decoration:underline;";
    wrap.appendChild(a);

    setActive(initialIdx);

    var target = findInsertBeforeTarget(panel);
    if (target && target.parentElement) {
      target.parentElement.insertBefore(wrap, target);
    } else {
      panel.insertBefore(wrap, panel.firstChild);
    }
  }

  var timer = null;
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(function () {
      inject().catch(function () {});
    }, 400);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", schedule);
  } else {
    schedule();
  }

  var obs = new MutationObserver(schedule);
  obs.observe(document.body, { childList: true, subtree: true });

  window.addEventListener("hashchange", schedule);
})();
