
  (function () {
    "use strict";
    var state = { activePolicyName: "", activePolicyNames: {}, selected: null, rows: [], recommending: false };

    function esc(s) {
      return String(s == null ? "" : s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }

    function toast(text) {
      var el = document.getElementById("toast");
      el.textContent = text;
      el.style.display = "block";
      clearTimeout(window.__luToastTimer);
      window.__luToastTimer = setTimeout(function () {
        el.style.display = "none";
      }, 2200);
    }

    function setRecommendBusy(busy) {
      state.recommending = !!busy;
      var btn = document.getElementById("btnRecommend");
      if (!btn) return;
      btn.disabled = !!busy;
      btn.textContent = busy ? "AI 鐢熸垚涓?.." : "AI 鎺ㄨ崘鑽夌";
    }

    function token() {
      if (window.LabeluPortal && LabeluPortal.getAuthToken) return LabeluPortal.getAuthToken();
      return localStorage.getItem("token") || "";
    }

    async function api(path, options) {
      options = options || {};
      var headers = options.headers || {};
      headers["Content-Type"] = "application/json";
      var t = token();
      if (t) headers["Authorization"] = /^Bearer /i.test(t) ? t : ("Bearer " + t);
      var res = await fetch("/api/v1" + path, {
        method: options.method || "GET",
        headers: headers,
        body: options.body ? JSON.stringify(options.body) : undefined
      });
      var json = null;
      try { json = await res.json(); } catch (e) {}
      return { ok: res.ok, status: res.status, json: json };
    }

    function policyPayloadFromForm() {
      var parsed = {};
      var raw = (document.getElementById("policyJson").value || "").trim();
      if (raw) parsed = JSON.parse(raw);
      return {
        policy_name: document.getElementById("policyName").value.trim(),
        scene_name: document.getElementById("sceneName").value.trim(),
        rect_preset_slug: document.getElementById("rectPresetSlug").value.trim(),
        status: document.getElementById("policyStatus").value,
        source: document.getElementById("policySource").value,
        version: parsed.version || 1,
        source_summary: parsed.source_summary || {},
        training_quality: parsed.training_quality || {},
        generalization_quality: parsed.generalization_quality || {},
        edge_usability: parsed.edge_usability || {},
        release_readiness: parsed.release_readiness || {},
        key_classes: parsed.key_classes || [],
        reasons: parsed.reasons || [],
        risks: parsed.risks || []
      };
    }

    function activePolicyNameForSlug(slug) {
      var key = String(slug || "").trim();
      return (state.activePolicyNames && state.activePolicyNames[key]) || "";
    }

    function statusMeta(row) {
      if (row && row.is_active) return { cls: "ok", text: "宸插惎鐢? };
      if (String(row && row.status || "").trim() === "active") return { cls: "warn", text: "鏈敓鏁? };
      return { cls: "na", text: "鑽夌" };
    }

    function sceneDisplayName(slug) {
      var key = String(slug || "").trim();
      return key === "__default__" ? "榛樿鍏滃簳" : (key || "鏈缃?);
    }

    function renderActiveSceneSummary() {
      var el = document.getElementById("activeSceneSummary");
      var activeMap = state.activePolicyNames || {};
      var keys = Object.keys(activeMap).sort();
      if (!keys.length) {
        el.textContent = "褰撳墠杩樻病鏈夊惎鐢ㄤ腑鐨勮川閲忔爣鍑嗐€?;
        return;
      }
      el.innerHTML =
        "<div class=\"scene-active-list\">" +
        keys.map(function (slug) {
          return (
            "<div class=\"scene-active-item\">" +
            "<span class=\"pill ok\">" + esc(sceneDisplayName(slug)) + "</span>" +
            "<strong>" + esc(activeMap[slug] || "-") + "</strong>" +
            "</div>"
          );
        }).join("") +
        "</div>" +
        "<div class=\"muted\" style=\"margin-top:8px;\">涓嶅悓鍦烘櫙鍙互鍚屾椂鍚勮嚜鍚敤涓€濂楁爣鍑嗭紝涓嶄細浜掔浉瑕嗙洊銆?/div>";
    }

    function fillForm(row) {
      state.selected = row;
      document.getElementById("policyName").value = row.policy_name || "";
      document.getElementById("sceneName").value = row.scene_name || "";
      document.getElementById("rectPresetSlug").value = row.rect_preset_slug || "";
      document.getElementById("policyStatus").value = row.status || "draft";
      document.getElementById("policySource").value = row.source || "manual";
      document.getElementById("policyJson").value = JSON.stringify({
        version: row.version,
        source_summary: row.source_summary || {},
        training_quality: row.training_quality || {},
        generalization_quality: row.generalization_quality || {},
        edge_usability: row.edge_usability || {},
        release_readiness: row.release_readiness || {},
        key_classes: row.key_classes || [],
        reasons: row.reasons || [],
        risks: row.risks || []
      }, null, 2);
      var meta = statusMeta(row);
      var activeName = activePolicyNameForSlug(row.rect_preset_slug);
      var sceneHint = "";
      if (activeName && activeName === row.policy_name) {
        sceneHint = "褰撳墠鍦烘櫙宸插惎鐢ㄦ湰绛栫暐銆?;
      } else if (activeName) {
        sceneHint = "褰撳墠鍦烘櫙宸插惎鐢細" + activeName;
      } else {
        sceneHint = "褰撳墠鍦烘櫙灏氭湭閰嶇疆鍚敤绛栫暐銆?;
      }
      document.getElementById("currentSummary").innerHTML =
        "<div>褰撳墠绛栫暐锛?strong>" + esc(row.policy_name) + "</strong> " +
        "<span class=\"pill " + esc(meta.cls) + "\">" + esc(meta.text) + "</span></div>" +
        "<div class=\"muted\" style=\"margin-top:6px;\">鍦烘櫙锛? + esc(row.scene_name || "-") +
        " / 棰勮锛? + esc(row.rect_preset_slug || "-") + "</div>" +
        "<div class=\"muted\" style=\"margin-top:6px;\">" + esc(sceneHint) + "</div>";
      document.getElementById("currentJson").textContent = JSON.stringify(row, null, 2);
    }

    function renderRows() {
      var tbody = document.getElementById("policyRows");
      if (!state.rows.length) {
        tbody.innerHTML = "<tr><td colspan=\"6\" class=\"muted\">鏆傛棤绛栫暐銆?/td></tr>";
        return;
      }
      tbody.innerHTML = state.rows.map(function (row) {
        var meta = statusMeta(row);
        return "<tr>" +
          "<td><strong>" + esc(row.policy_name) + "</strong></td>" +
          "<td>" + esc(row.scene_name || "-") + "</td>" +
          "<td>" + esc(row.rect_preset_slug || "-") + "</td>" +
          "<td><span class=\"pill " + esc(meta.cls) + "\">" + esc(meta.text) + "</span></td>" +
          "<td>" + esc(String(row.version || 1)) + "</td>" +
          "<td class=\"mini-actions\">" +
          "<button type=\"button\" class=\"btn\" data-act=\"view\" data-name=\"" + esc(row.policy_name) + "\">鏌ョ湅</button>" +
          "<button type=\"button\" class=\"btn\" data-act=\"activate\" data-name=\"" + esc(row.policy_name) + "\">鍚敤</button>" +
          "</td>" +
          "</tr>";
      }).join("");

      Array.prototype.forEach.call(tbody.querySelectorAll("button[data-act]"), function (btn) {
        btn.onclick = async function () {
          var name = btn.getAttribute("data-name");
          var row = state.rows.find(function (item) { return item.policy_name === name; });
          if (!row) return;
          if (btn.getAttribute("data-act") === "view") {
            fillForm(row);
            return;
          }
          var res = await api("/yolo-quality-policies/activate", {
            method: "POST",
            body: { policy_name: name }
          });
          if (!res.ok) {
            toast("鍚敤澶辫触");
            return;
          }
          toast("宸插惎鐢?);
          await loadPolicies();
        };
      });
    }

    async function loadPolicies() {
      var res = await api("/yolo-quality-policies");
      if (!res.ok || !res.json || !res.json.data) {
        document.getElementById("policyRows").innerHTML = "<tr><td colspan=\"6\" class=\"muted\">鍔犺浇澶辫触銆?/td></tr>";
        document.getElementById("activeSceneSummary").textContent = "鍔犺浇澶辫触銆?;
        return;
      }
      var selectedName = state.selected && state.selected.policy_name ? state.selected.policy_name : "";
      state.activePolicyName = res.json.data.active_policy_name || "";
      state.activePolicyNames = res.json.data.active_policy_names || {};
      state.rows = res.json.data.policies || [];
      renderRows();
      renderActiveSceneSummary();
      var active = state.rows.find(function (item) { return item.policy_name === selectedName; }) ||
        state.rows.find(function (item) { return item.is_active; }) ||
        state.rows[0];
      if (active) fillForm(active);
    }

    async function savePolicy() {
      var payload;
      try {
        payload = policyPayloadFromForm();
      } catch (e) {
        toast("绛栫暐 JSON 鏍煎紡涓嶆纭?);
        return;
      }
      if (!payload.policy_name || !payload.scene_name || !payload.rect_preset_slug) {
        toast("绛栫暐鍚嶃€佸満鏅€侀璁句笉鑳戒负绌?);
        return;
      }
      var res = await api("/yolo-quality-policies", { method: "POST", body: payload });
      if (!res.ok) {
        toast("淇濆瓨澶辫触");
        return;
      }
      toast("宸蹭繚瀛?);
      await loadPolicies();
    }

    async function activateCurrent() {
      var name = document.getElementById("policyName").value.trim();
      if (!name) {
        toast("璇峰厛閫夋嫨绛栫暐");
        return;
      }
      var res = await api("/yolo-quality-policies/activate", {
        method: "POST",
        body: { policy_name: name }
      });
      if (!res.ok) {
        toast("鍚敤澶辫触");
        return;
      }
      toast("宸插惎鐢?);
      await loadPolicies();
    }

    async function recommend() {
      if (state.recommending) return;
      var scene = document.getElementById("sceneName").value.trim();
      var slug = document.getElementById("rectPresetSlug").value.trim();
      if (!scene || !slug) {
        toast("璇峰厛濉啓鍦烘櫙鍚嶇О鍜岄璁?slug");
        return;
      }
      var payload = {
        scene_name: scene,
        rect_preset_slug: slug,
        benchmark_name: document.getElementById("recommendBenchmark").value.trim() || null,
        benchmark_image_count: Number(document.getElementById("recommendImages").value || 0) || null,
        benchmark_box_count: Number(document.getElementById("recommendBoxes").value || 0) || null,
        include_historical_training: true,
        include_edge_validation_history: true,
        include_ai_eval_reports: true,
        business_goal: document.getElementById("recommendGoal").value.trim() || null,
        key_classes: (document.getElementById("recommendClasses").value || "")
          .split(",")
          .map(function (s) { return s.trim(); })
          .filter(Boolean),
        business_constraints: {}
      };
      setRecommendBusy(true);
      toast("AI 姝ｅ湪鐢熸垚璐ㄩ噺鏍囧噯寤鸿锛岃绋嶅€?..");
      try {
      var res = await api("/yolo-quality-policies/recommend", { method: "POST", body: payload });
      if (!res.ok || !res.json || !res.json.data || !res.json.data.recommendation) {
        toast("AI 鎺ㄨ崘澶辫触");
        return;
      }
      fillForm(res.json.data.recommendation);
      toast(res.json.data.fallback_used ? "AI 涓嶅彲鐢紝宸插洖閫€榛樿鑽夌" : "AI 鑽夌宸茬敓鎴?);
    }

      } finally {
        setRecommendBusy(false);
      }
    }

    document.getElementById("btnReload").onclick = loadPolicies;
    document.getElementById("btnSavePolicy").onclick = savePolicy;
    document.getElementById("btnActivatePolicy").onclick = activateCurrent;
    document.getElementById("btnRecommend").onclick = recommend;
    loadPolicies();
  })();
  
