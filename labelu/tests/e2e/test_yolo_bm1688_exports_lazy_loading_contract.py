from pathlib import Path

PAGE = (
    Path(__file__).resolve().parents[3]
    / "frontend"
    / "extensions"
    / "pages"
    / "yolo_bm1688_exports.html"
)


def test_bm1688_page_loads_summary_and_evaluation_on_demand():
    html = PAGE.read_text(encoding="utf-8")

    assert '"/yolo-bm1688-exports/" + exportId + "/summary"' in html
    assert '"/yolo-bm1688-exports/" + exportId + "/evaluation"' in html
    assert "loadTable().then(function ()" not in html
    assert "fillModelEvalPanel(d, evalAssetLoadController && evalAssetLoadController.signal);" in html
    assert "evalResponseSignature = responseSignature;\n    fillModelEvalPanel" in html


def test_bm1688_evaluation_assets_are_cancelled_and_lazy_loaded():
    html = PAGE.read_text(encoding="utf-8")

    assert "evalLoadController = new AbortController();" in html
    assert "active < 4 && queue.length" in html
    assert '"IntersectionObserver" in window' in html
    assert 'rootMargin: "320px 0px"' in html
    assert 'loading="lazy" decoding="async"' in html
    assert "clearEvalAssetObjectUrls();" in html
    assert "evalAssetObjectUrlCache.has(url)" in html
    assert "evalAssetFetchPromises.has(url)" in html
    assert 'cache: "force-cache"' in html
    assert "resetEvalAssetSession(null);" in html


def test_completed_evaluation_is_not_refreshed_by_unrelated_busy_rows():
    html = PAGE.read_text(encoding="utf-8")

    assert "function shouldPollOpenModelEval()" in html
    assert 'if (["PENDING", "PROCESSING", "ONNX_READY"].indexOf(status) >= 0)' in html
    assert 'return ["PENDING", "PROCESSING", "RUNNING"].indexOf(boardStatus) >= 0;' in html
    assert "isModalShown(\"evalModal\") && shouldPollOpenModelEval()" in html
    assert "responseSignature === evalResponseSignature" in html
    assert 'if (!quiet) {\n        document.getElementById("evalTablesHost").innerHTML' in html


def test_manual_edge_sampling_and_class_diagnostics_are_not_reported_as_missing():
    html = PAGE.read_text(encoding="utf-8")

    assert 'String(boardReport.source || "").toLowerCase() === "manual_edge_validation"' in html
    assert '" · 边端人工抽样 " + bmM.images + "（口径不同）"' in html
    assert 'label: manualEdgeSample ? "评估图片口径" : "图片数一致性"' in html
    assert 'label: "类别覆盖 / 分类诊断"' in html
    assert '"分类诊断 0 类（本次未产生可计算类别结果）"' in html
