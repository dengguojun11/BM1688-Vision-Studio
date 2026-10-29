(function () {
  "use strict";

  function esc(value) {
    var text = value == null ? "" : String(value);
    return text.replace(/[&<>\"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[ch];
    });
  }

  function host() {
    return document.getElementById("jobObservabilityPanel");
  }

  function value(value, fallback) {
    if (value === null || value === undefined || value === "") return fallback || "—";
    return String(value);
  }

  function number(value, digits) {
    if (value === null || value === undefined || value === "") return "—";
    var parsed = Number(value);
    return isFinite(parsed) ? parsed.toFixed(digits == null ? 3 : digits) : "—";
  }

  function stageClass(status) {
    var s = String(status || "").toLowerCase();
    if (s.indexOf("失败") >= 0 || s.indexOf("不可") >= 0 || s === "failed") return "is-fail";
    if (s.indexOf("进行") >= 0 || s.indexOf("运行") >= 0 || s.indexOf("评估") >= 0 || s === "processing") return "is-running";
    if (s.indexOf("完成") >= 0 || s.indexOf("通过") >= 0 || s.indexOf("达标") >= 0 || s === "completed") return "is-ok";
    return "is-wait";
  }

  function statusLabel(status, map) {
    var raw = String(status || "").toUpperCase();
    return map[raw] || value(status, "未开始");
  }

  function stageHtml(label, status, detail) {
    return '<article class="lu-observe-stage ' + stageClass(status) + '">' +
      '<span class="lu-observe-label">' + esc(label) + '</span>' +
      '<strong class="lu-observe-value" title="' + esc(status) + '">' + esc(value(status, "未开始")) + '</strong>' +
      '<span class="lu-observe-sub" title="' + esc(detail) + '">' + esc(value(detail, "等待前置步骤")) + '</span>' +
      '</article>';
  }

  function metricHtml(label, main, sub) {
    return '<article class="lu-observe-metric"><span class="lu-observe-label">' + esc(label) + '</span>' +
      '<strong class="lu-observe-value" title="' + esc(main) + '">' + esc(main) + '</strong>' +
      '<span class="lu-observe-sub">' + esc(sub || "") + '</span></article>';
  }

  function isCompositePlateJob(job) {
    return !!(job && (
      job.job_type === "plate_recognition" ||
      job.training_profile === "plate_recognition_v1" ||
      job.component_metrics ||
      job.component_progress
    ));
  }

  function compositeSplitCounts(dataset) {
    var split = dataset && dataset.split_counts;
    if (split) return split;
    var composition = dataset && dataset.composition;
    var source = composition && composition.splits || {};
    var count = function (item) {
      return item && item.total_images != null ? item.total_images : 0;
    };
    return {
      train: count(source.train),
      val: count(source.val),
      test: count(source.test)
    };
  }

  function percent(value) {
    if (value === null || value === undefined || value === "") return "—";
    var parsed = Number(value);
    return isFinite(parsed) ? (parsed * 100).toFixed(2) + "%" : "—";
  }

  function manifestHtml(job) {
    var dataset = job && job.dataset_manifest || null;
    var split = compositeSplitCounts(dataset);
    var composite = isCompositePlateJob(job);
    var classes = dataset && Array.isArray(dataset.class_names) ? dataset.class_names.length : 0;
    var splitTotal = Number(split.train || 0) + Number(split.val || 0) + Number(split.test || 0);
    var datasetCount = dataset && dataset.file_count != null ? dataset.file_count : splitTotal;
    var datasetDimension = composite ? "组件 4 个" : ("类别 " + classes + " 个");
    var datasetText = dataset
      ? ("文件 " + value(datasetCount, "0") + " 个 · " + datasetDimension + " · train/val/test " +
        value(split.train, "0") + "/" + value(split.val, "0") + "/" + value(split.test, "0"))
      : "训练尚未写入数据清单";
    var weightsText = job && job.weights_path
      ? ("best.pt 已生成 · " + String(job.weights_path).split(/[\\/]/).pop())
      : (job && job.status === "COMPLETED" ? "训练已完成，未找到 best.pt" : "训练完成后生成 best.pt 与 last.pt");
    var hashes = [];
    if (dataset && dataset.content_sha256) hashes.push("数据清单 " + String(dataset.content_sha256).slice(0, 12));
    if (job && job.data_yaml_sha256) hashes.push("data.yaml " + String(job.data_yaml_sha256).slice(0, 12));
    return '<div class="lu-observe-section-title">训练输入与产物</div>' +
      '<div class="lu-observe-lineage-grid">' +
      '<article class="lu-observe-lineage"><span class="lu-observe-label">训练数据清单</span><p>' + esc(datasetText) + '</p></article>' +
      '<article class="lu-observe-lineage"><span class="lu-observe-label">YOLO 训练产物</span><p>' + esc(weightsText) + '</p></article>' +
      '</div>' +
      '<p class="lu-observe-sub" style="margin-top:7px;white-space:normal;">' + esc(hashes.length ? hashes.join(" · ") : "训练清单哈希将在数据准备完成后显示") + '</p>';
  }

  function alertHtml(job) {
    var messages = [];
    if (job && job.error_message) messages.push(String(job.error_message));
    var composite = isCompositePlateJob(job);
    var compositeAssessment = job && job.composite_assessment || {};
    if (composite && Array.isArray(compositeAssessment.warnings)) {
      messages = messages.concat(compositeAssessment.warnings.map(String));
    }
    if (!messages.length) return "";
    return '<div class="lu-observe-alert" role="alert"><strong>需要处理的问题</strong><ul>' +
      messages.slice(0, 4).map(function (item) { return "<li>" + esc(item) + "</li>"; }).join("") +
      '</ul></div>';
  }

  function render(job) {
    var target = host();
    if (!target) return;
    if (!job) {
      target.innerHTML = "";
      target.style.display = "none";
      return;
    }
    target.style.display = "block";
    var trainingStatus = statusLabel(job.status, {
      PENDING: "排队等待", PROCESSING: "训练中", COMPLETED: "已完成", FAILED: "失败", CANCELLED: "已暂停"
    });
    var val = job.training_val_metrics || {};
    var test = job.test_val_metrics || {};
    var composite = isCompositePlateJob(job);
    var componentProgress = job.component_progress || {};
    var componentMetrics = job.component_metrics || {};
    var compositeAssessment = job.composite_assessment || {};
    var pose = componentMetrics.pose && componentMetrics.pose.metrics || {};
    var ocr = componentMetrics.ocr && componentMetrics.ocr.metrics || {};
    var quality = job.quality_assessment || {};
    var trainingQuality = quality.training_quality || {};
    var validation = job.validation_pass || {};
    var progress = composite && componentProgress.summary
      ? componentProgress.summary
      : job.current_epoch != null && job.epochs
      ? (String(job.current_epoch) + " / " + String(job.epochs))
      : value(job.train_phase_label || job.status, "—");
    var validationStatus = composite
      ? value(compositeAssessment.status, job.status === "COMPLETED" ? "待评估" : "等待训练完成")
      : value(validation.status || trainingQuality.status, job.status === "COMPLETED" ? "待评估" : "等待训练完成");
    var validationDetail = composite
      ? value(compositeAssessment.summary, "等待组件训练指标")
      : value(validation.detail || trainingQuality.summary, "训练完成后依据 YOLO 验证指标判定");
    var datasetManifest = job.dataset_manifest;
    var datasetReady = !!(
      datasetManifest &&
      typeof datasetManifest === "object" &&
      Object.keys(datasetManifest).length
    );
    var datasetStatus = datasetReady ? "已完成" : (job.status === "PENDING" || job.status === "PROCESSING" ? "准备中" : "未生成");
    var datasetDetail = datasetReady ? "data.yaml 与 train/val/test 已固化" : "等待训练数据清单";
    var weightsStatus = job.weights_path ? "已生成" : (job.status === "COMPLETED" ? "未找到" : "等待训练完成");
    var weightsDetail = job.weights_path ? String(job.weights_path).split(/[\\/]/).pop() : "目标产物 best.pt";
    var metricCards = composite
      ? metricHtml("组件进度", progress, "Pose / OCR / 颜色 / 类型") +
        metricHtml("Pose mAP50-95", number(pose.box_map50_95) + " / " + number(pose.pose_map50_95), "车牌框 / 四角点") +
        metricHtml("OCR Test 准确率", percent(ocr.test && ocr.test.accuracy), value(ocr.test && ocr.test.total, "0") + " 张测试图片") +
        metricHtml("训练结论", validationStatus, "复合训练指标判定")
      : metricHtml("训练进度", progress, value(job.train_phase_label, "epoch")) +
        metricHtml("Val mAP50-95", number(val.map50_95), value(val.source, "验证集")) +
        metricHtml("Test mAP50-95", number(test.map50_95), value(test.status, "测试集")) +
        metricHtml("训练结论", validationStatus, "YOLO 验证指标判定");
    var html = '<div class="lu-observe-shell">' +
      '<div class="lu-observe-head"><div><h4>训练运行观察</h4><p>数据准备、训练进度、验证指标与 PT 权重</p></div>' +
      '<span class="lu-observe-refresh">YOLO 训练作业 #' + esc(job.id) + '</span></div>' +
      alertHtml(job) +
      '<div class="lu-observe-pipeline">' +
      stageHtml("数据准备", datasetStatus, datasetDetail) +
      stageHtml(composite ? "组件训练" : "YOLO 训练", trainingStatus, value(job.train_phase_detail || job.progress_snapshot, "当前阶段")) +
      stageHtml(composite ? "复合指标判定" : "指标验证", validationStatus, validationDetail) +
      stageHtml("PT 权重", weightsStatus, weightsDetail) +
      '</div>' +
      '<div class="lu-observe-metrics">' +
      metricCards +
      '</div>' +
      manifestHtml(job) +
      '</div>';
    target.innerHTML = html;
  }

  window.LabeluTrainingObservability = {
    reset: function () {
      var target = host();
      if (target) {
        target.innerHTML = "";
        target.style.display = "none";
      }
    },
    render: function (job) {
      window.__labeluTrainingObservabilityJob = job || null;
      if (!job) {
        this.reset();
        return;
      }
      render(job);
    }
  };
})();
