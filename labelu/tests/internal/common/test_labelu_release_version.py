from labelu.internal.common import labelu_frontend_integration as fe


def test_read_frontend_release_version_from_file(tmp_path, monkeypatch):
    ext = tmp_path / "extensions"
    ext.mkdir()
    (ext / "FRONTEND_VERSION").write_text("2.0.1\n", encoding="utf-8")
    monkeypatch.setattr(fe, "EXTENSIONS_DIR", ext)
    monkeypatch.setattr(fe, "FRONTEND_VERSION_FILE", ext / "FRONTEND_VERSION")
    assert fe.read_frontend_release_version() == "2.0.1"


def test_write_runtime_version_scripts(tmp_path):
    fe.write_runtime_version_scripts(tmp_path, log=lambda *a, **k: None)
    fe_js = (tmp_path / "frontend_version.js").read_text(encoding="utf-8")
    be_js = (tmp_path / "backend_version.js").read_text(encoding="utf-8")
    assert "__frontend" in fe_js
    assert "__backend" in be_js


def test_sync_extension_scripts_removes_managed_spa_injection_scripts(tmp_path, monkeypatch):
    assets = tmp_path / "extensions" / "assets"
    assets.mkdir(parents=True)
    (assets / "task_yolo_preannotate_entry.js").write_text("// yolo", encoding="utf-8")
    (assets / "task_tagtool_preannotate_entry.js").write_text("// tagtool", encoding="utf-8")

    kit = tmp_path / "kit"
    public_assets = kit / "apps" / "frontend" / "public" / "assets"
    public_assets.mkdir(parents=True)
    (kit / "package.json").write_text("{}", encoding="utf-8")
    (kit / "apps" / "frontend" / "package.json").write_text("{}", encoding="utf-8")
    index = kit / "apps" / "frontend" / "index.html"
    index.write_text(
        "<html><head>"
        f"{fe.EXTENSION_MARKER}\n"
        '  <script defer src="/assets/task_locateanything_preannotate_entry.js"></script>\n'
        '  <script defer src="/assets/task_yolo_preannotate_entry.js"></script>\n'
        "</head><body></body></html>",
        encoding="utf-8",
    )

    monkeypatch.setattr(fe, "KIT_DIR", kit)
    monkeypatch.setattr(fe, "KIT_INDEX_HTML", index)
    monkeypatch.setattr(fe, "KIT_PUBLIC_ASSETS", public_assets)
    monkeypatch.setattr(fe, "EXTENSIONS_ASSETS", assets)
    monkeypatch.setattr(
        fe,
        "SPA_EXTENSION_SCRIPTS",
        (),
    )
    monkeypatch.setattr(
        fe,
        "EXTENSION_ASSET_FILES",
        ("task_yolo_preannotate_entry.js", "task_tagtool_preannotate_entry.js"),
    )

    fe.sync_extensions_to_kit(log=lambda *a, **k: None)

    html = index.read_text(encoding="utf-8")
    assert "task_locateanything_preannotate_entry.js" not in html
    assert "task_yolo_preannotate_entry.js" not in html
    assert "task_tagtool_preannotate_entry.js" not in html
