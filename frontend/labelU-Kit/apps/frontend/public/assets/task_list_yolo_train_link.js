/**
 * 任务列表页：为具备标注统计权限的管理员注入「标注统计」Tab（弹层大屏）。
 * 兼容旧版 /users/me 未返回 is_task_superuser 的后端；无需重建主站 SPA。
 */
(function () {
  "use strict";

  var DEFAULT_SUPERUSER = "admin@123.com";
  var API = "/api/v1";
  var samplePage = 0;
  var sampleSize = 10;
  var sampleTimingsTotal = 0;
  var samplePager = null;

  function authHeaders() {
    var t = localStorage.getItem("token") || "";
    if (!t) return null;
    return { Authorization: t.indexOf("Bearer") === 0 ? t : "Bearer " + t };
  }

  function onTasksPage() {
    return /^\/tasks\/?$/.test(location.pathname || "");
  }

  function resolveCanViewStats(cb) {
    var headers = authHeaders();
    if (!headers) {
      cb(false);
      return;
    }
    fetch(API + "/users/me", { headers: headers })
      .then(function (r) {
        return r.json();
      })
      .then(function (me) {
        var data = me && me.data;
        if (!data) {
          cb(false);
          return;
        }
        if (data.is_task_superuser === true) {
          cb(true);
          return;
        }
        if (data.is_task_superuser === false) {
          cb(false);
          return;
        }
        var uname = String(data.username || "")
          .trim()
          .toLowerCase();
        if (uname === DEFAULT_SUPERUSER) {
          cb(true);
          return;
        }
        fetch(API + "/tasks/annotation-stats/dashboard?" + new URLSearchParams({ days: "1" }), {
          headers: headers,
        })
          .then(function (r) {
            cb(r.status === 200);
          })
          .catch(function () {
            cb(false);
          });
      })
      .catch(function () {
        cb(false);
      });
  }

  function styleTabButton(btn, active) {
    btn.style.border = "0";
    btn.style.borderRadius = "8px 8px 0 0";
    btn.style.padding = "0.625rem 1.125rem";
    btn.style.font = "inherit";
    btn.style.fontSize = "0.9375rem";
    btn.style.fontWeight = active ? "600" : "500";
    btn.style.color = active ? "#2f6fed" : "#475569";
    btn.style.background = active ? "#fff" : "transparent";
    btn.style.boxShadow = active ? "0 -1px 0 #fff inset, 0 1px 0 #eef1f6" : "none";
    btn.style.cursor = "pointer";
  }

  function fmtDate(d) {
    var y = d.getFullYear();
    var m = String(d.getMonth() + 1).padStart(2, "0");
    var day = String(d.getDate()).padStart(2, "0");
    return y + "-" + m + "-" + day;
  }

  function addDays(d, n) {
    var x = new Date(d);
    x.setDate(x.getDate() + n);
    return x;
  }

  function fmtDuration(sec) {
    if (sec == null || sec < 0) return "—";
    if (sec < 60) return sec + " 秒";
    if (sec < 3600) return Math.round(sec / 60) + " 分钟";
    var h = Math.floor(sec / 3600);
    var m = Math.round((sec % 3600) / 60);
    return h + " 小时" + (m ? " " + m + " 分" : "");
  }

  function fmtDateTime(iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "—";
    return fmtDate(d) + " " + String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  }

  function esc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function sortedDailyRows(rows) {
    var byDate = {};
    rows.forEach(function (r) {
      if (!byDate[r.stat_date]) byDate[r.stat_date] = [];
      byDate[r.stat_date].push(r);
    });
    var out = [];
    Object.keys(byDate)
      .sort()
      .reverse()
      .forEach(function (date) {
        byDate[date].sort(function (a, b) {
          return b.done_count - a.done_count;
        });
        out = out.concat(byDate[date]);
      });
    return out;
  }

  var statsOverlay = null;
  var statsState = { dateFrom: "", dateTo: "", taskId: "", taskOptions: [] };

  function closeStatsOverlay() {
    if (statsOverlay && statsOverlay.parentNode) statsOverlay.parentNode.removeChild(statsOverlay);
    statsOverlay = null;
    var tablist = document.querySelector('[role="tablist"][aria-label="任务页视图"]');
    if (tablist) syncTabActive(tablist, false);
  }

  function loadStatsInto(root) {
    var headers = authHeaders();
    if (!headers) return;
    root.innerHTML = '<div style="padding:2rem;text-align:center;color:#64748b">加载中…</div>';
    var params = new URLSearchParams({
      date_from: statsState.dateFrom,
      date_to: statsState.dateTo,
      sample_page: String(samplePage),
      sample_size: String(sampleSize),
    });
    if (statsState.taskId) params.set("task_id", statsState.taskId);

    fetch(API + "/tasks/annotation-stats/dashboard?" + params.toString(), { headers: headers })
      .then(function (r) {
        if (!r.ok) throw new Error("加载失败 (" + r.status + ")");
        return r.json();
      })
      .then(function (res) {
        renderStatsPanel(root, res.data || {});
      })
      .catch(function (e) {
        root.innerHTML =
          '<div style="padding:1rem;border-radius:8px;background:#fff2f0;border:1px solid #ffccc7;color:#cf1322">' +
          esc(e.message || String(e)) +
          "</div>";
      });
  }

  function renderStatsPanel(root, data) {
    var s = data.summary || {};
    var daily = sortedDailyRows(data.daily_by_user || []);
    var timings = data.sample_timings || [];
    sampleTimingsTotal = data.sample_timings_total || 0;
    var prevDate = null;

    var html =
      '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:0.75rem;margin-bottom:1rem">';
    html += statCard("累计真实修改", s.total_done);
    html += statCard("标注人数", s.total_annotators);
    html += statCard("平均单张处理时长", fmtDuration(s.avg_duration_seconds));
    html += statCard("今日真实修改", s.today_done);
    html += "</div>";

    html +=
      '<div style="background:#fff;border:1px solid #e8edf4;border-radius:10px;padding:1rem;margin-bottom:1rem;overflow:auto"><h3 style="margin:0 0 0.75rem;font-size:1rem">每日真实修改图片数</h3><table style="width:100%;border-collapse:collapse;font-size:0.875rem"><thead><tr><th style="text-align:left;padding:0.5rem;border-bottom:1px solid #eef1f6;color:#64748b">日期</th><th style="text-align:left;padding:0.5rem;border-bottom:1px solid #eef1f6;color:#64748b">修改人</th><th style="text-align:left;padding:0.5rem;border-bottom:1px solid #eef1f6;color:#64748b">真实修改图片张数</th></tr></thead><tbody>';
    daily.forEach(function (r) {
      var top = r.stat_date !== prevDate;
      prevDate = r.stat_date;
      html +=
        "<tr><td style='padding:0.5rem;border-bottom:1px solid #eef1f6'>" +
        esc(r.stat_date) +
        "</td><td style='padding:0.5rem;border-bottom:1px solid #eef1f6'>" +
        esc(r.username) +
        (top ? " <span style='margin-left:6px;font-size:11px;color:#2f6fed;font-weight:600'>当日最多</span>" : "") +
        "</td><td style='padding:0.5rem;border-bottom:1px solid #eef1f6'>" +
        r.done_count +
        "</td></tr>";
    });
    if (!daily.length)
      html += "<tr><td colspan='3' style='padding:0.5rem;color:#64748b'>暂无数据</td></tr>";
    html += "</tbody></table></div>";

    html +=
      '<div style="background:#fff;border:1px solid #e8edf4;border-radius:10px;padding:1rem;overflow:auto"><h3 style="margin:0 0 0.75rem;font-size:1rem">单张真实修改记录 <span style="font-weight:400;color:#64748b;font-size:12px">' +
      esc(data.duration_note || "") +
      '</span></h3><table style="width:100%;border-collapse:collapse;font-size:0.875rem"><thead><tr><th style="text-align:left;padding:0.5rem;border-bottom:1px solid #eef1f6;color:#64748b">任务</th><th style="text-align:left;padding:0.5rem;border-bottom:1px solid #eef1f6;color:#64748b">图片</th><th style="text-align:left;padding:0.5rem;border-bottom:1px solid #eef1f6;color:#64748b">修改人</th><th style="text-align:left;padding:0.5rem;border-bottom:1px solid #eef1f6;color:#64748b">框数</th><th style="text-align:left;padding:0.5rem;border-bottom:1px solid #eef1f6;color:#64748b">估算用时</th><th style="text-align:left;padding:0.5rem;border-bottom:1px solid #eef1f6;color:#64748b">最近真实修改时间</th></tr></thead><tbody>';
    timings.forEach(function (r) {
      html +=
        "<tr><td style='padding:0.5rem;border-bottom:1px solid #eef1f6'>" +
        esc(r.task_name || "#" + r.task_id) +
        "</td><td style='padding:0.5rem;border-bottom:1px solid #eef1f6'>" +
        esc(r.filename) +
        "</td><td style='padding:0.5rem;border-bottom:1px solid #eef1f6'>" +
        esc(r.username) +
        "</td><td style='padding:0.5rem;border-bottom:1px solid #eef1f6'>" +
        (r.annotated_count != null ? r.annotated_count : "—") +
        "</td><td style='padding:0.5rem;border-bottom:1px solid #eef1f6'>" +
        fmtDuration(r.duration_seconds) +
        "</td><td style='padding:0.5rem;border-bottom:1px solid #eef1f6'>" +
        fmtDateTime(r.annotated_at) +
        "</td></tr>";
    });
    if (!timings.length)
      html += "<tr><td colspan='6' style='padding:0.5rem;color:#64748b'>暂无数据</td></tr>";
    html += "</tbody></table>";
    html += '<div id="luStatsSamplePager" class="pager" style="margin-top:0.75rem"></div></div>';

    root.innerHTML = html;
    if (window.LuPager) {
      samplePager = LuPager.mount("luStatsSamplePager", {
        page: samplePage,
        size: sampleSize,
        total: sampleTimingsTotal,
        onChange: function (next) {
          samplePage = next.page;
          sampleSize = next.size;
          loadStatsInto(root);
        },
      });
    }
  }

  function statCard(label, value) {
    return (
      '<div style="background:#fff;border:1px solid #e8edf4;border-radius:10px;padding:1rem 1.125rem"><div style="font-size:0.8125rem;color:#64748b">' +
      esc(label) +
      '</div><div style="font-size:1.5rem;font-weight:700;margin-top:0.25rem">' +
      (value != null ? value : "—") +
      "</div></div>"
    );
  }

  function ensureTaskOptions(selectEl, cb) {
    if (statsState.taskOptions.length) {
      cb();
      return;
    }
    var headers = authHeaders();
    if (!headers) return;
    fetch(API + "/tasks?size=500&page=0", { headers: headers })
      .then(function (r) {
        return r.json();
      })
      .then(function (res) {
        statsState.taskOptions = res.data || [];
        statsState.taskOptions.forEach(function (t) {
          var opt = document.createElement("option");
          opt.value = String(t.id);
          opt.textContent = (t.name || "任务") + " #" + t.id;
          selectEl.appendChild(opt);
        });
        cb();
      })
      .catch(cb);
  }

  function openStatsOverlay() {
    closeStatsOverlay();
    samplePage = 0;
    statsState.dateTo = fmtDate(new Date());
    statsState.dateFrom = fmtDate(new Date());
    statsState.taskId = "";

    var tablist = document.querySelector('[role="tablist"][aria-label="任务页视图"]');
    if (tablist) syncTabActive(tablist, true);

    statsOverlay = document.createElement("div");
    statsOverlay.setAttribute("data-lu-stats-overlay", "1");
    statsOverlay.style.cssText =
      "position:fixed;inset:var(--header-height,56px) 0 0 0;z-index:100;background:#f5f7fb;overflow:auto;padding:0 1.25rem 2rem";

    statsOverlay.innerHTML =
      '<div style="margin:1rem 0 1.25rem;padding:1.25rem 1.5rem;border-radius:12px;background:linear-gradient(135deg,#0f172a 0%,#1e293b 45%,#0f2744 100%);color:#e2e8f0"><h2 style="margin:0 0 0.35rem;font-size:1.375rem;color:#f8fafc">标注统计大屏</h2><p style="margin:0;font-size:0.8125rem;color:#94a3b8">汇总可见任务内每位修改人每日真实修改图片张数与单张图片真实修改记录；已排除 admin@123.com</p></div>' +
      '<div style="display:flex;flex-wrap:wrap;gap:0.75rem;align-items:center;margin-bottom:1rem"><input type="date" data-lu-from style="padding:0.45rem 0.65rem;border:1px solid #e8edf4;border-radius:6px" /><span style="color:#64748b">至</span><input type="date" data-lu-to style="padding:0.45rem 0.65rem;border:1px solid #e8edf4;border-radius:6px" /><select data-lu-task style="padding:0.45rem 0.65rem;border:1px solid #e8edf4;border-radius:6px;min-width:200px"><option value="">全部任务</option></select><button type="button" data-lu-close style="margin-left:auto;padding:0.45rem 0.85rem;border:1px solid #e8edf4;border-radius:6px;background:#fff;cursor:pointer">关闭</button></div>' +
      '<div data-lu-stats-body></div>';

    document.body.appendChild(statsOverlay);

    var fromEl = statsOverlay.querySelector("[data-lu-from]");
    var toEl = statsOverlay.querySelector("[data-lu-to]");
    var taskEl = statsOverlay.querySelector("[data-lu-task]");
    var bodyEl = statsOverlay.querySelector("[data-lu-stats-body]");
    fromEl.value = statsState.dateFrom;
    toEl.value = statsState.dateTo;

    function reload() {
      statsState.dateFrom = fromEl.value;
      statsState.dateTo = toEl.value;
      statsState.taskId = taskEl.value;
      samplePage = 0;
      loadStatsInto(bodyEl);
    }

    fromEl.onchange = reload;
    toEl.onchange = reload;
    taskEl.onchange = reload;
    statsOverlay.querySelector("[data-lu-close]").onclick = closeStatsOverlay;

    ensureTaskOptions(taskEl, function () {
      loadStatsInto(bodyEl);
    });
  }

  function syncTabActive(tablist, statsActive) {
    tablist.querySelectorAll("[data-lu-stats-tab]").forEach(function (btn) {
      styleTabButton(btn, statsActive);
      btn.setAttribute("aria-selected", statsActive ? "true" : "false");
    });
    if (!statsActive) return;
    tablist.querySelectorAll('[role="tab"]:not([data-lu-stats-tab])').forEach(function (btn) {
      styleTabButton(btn, false);
      btn.setAttribute("aria-selected", "false");
    });
  }

  function hasNativeStatsTab(tablist) {
    var tabs = tablist.querySelectorAll('[role="tab"]');
    for (var i = 0; i < tabs.length; i++) {
      if (
        !tabs[i].hasAttribute("data-lu-stats-tab") &&
        String(tabs[i].textContent || "").trim() === "标注统计"
      ) {
        return true;
      }
    }
    return false;
  }

  function removeExtensionStatsTab(tablist) {
    tablist.querySelectorAll("[data-lu-stats-tab]").forEach(function (btn) {
      btn.parentNode.removeChild(btn);
    });
    closeStatsOverlay();
  }

  function injectStatsTab() {
    if (!onTasksPage()) {
      closeStatsOverlay();
      return;
    }
    var tablist = document.querySelector('[role="tablist"][aria-label="任务页视图"]');
    if (!tablist) return;
    if (hasNativeStatsTab(tablist)) {
      removeExtensionStatsTab(tablist);
      return;
    }
    if (tablist.querySelector("[data-lu-stats-tab]")) {
      syncTabActive(tablist, !!statsOverlay);
      return;
    }
    resolveCanViewStats(function (ok) {
      if (!ok || !onTasksPage()) return;
      if (tablist.querySelector("[data-lu-stats-tab]")) return;
      var btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("role", "tab");
      btn.setAttribute("data-lu-stats-tab", "1");
      btn.setAttribute("aria-selected", "false");
      btn.textContent = "标注统计";
      styleTabButton(btn, false);
      btn.addEventListener("click", openStatsOverlay);
      tablist.appendChild(btn);
    });
  }

  var timer = null;
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(function () {
      try {
        injectStatsTab();
      } catch (e) {}
    }, 200);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", schedule);
  } else {
    schedule();
  }

  var obs = new MutationObserver(schedule);
  if (document.body) {
  obs.observe(document.body, { childList: true, subtree: true });
  }
  window.addEventListener("popstate", schedule);
})();
