from pathlib import Path


def test_role_execution_playbook_covers_menu_boundaries_and_run_evidence() -> None:
    path = Path(__file__).resolve().parents[3] / "docs" / "7gai_role_execution_playbook.md"
    text = path.read_text(encoding="utf-8")

    for menu in ("任务与标注", "训练中心", "模型中心", "进化中心", "平台设置"):
        assert menu in text
    for role in ("R01", "R02", "R03", "R04", "R05", "R06", "R07", "R08", "R09", "R10"):
        assert role in text
    for evidence in ("专项测试", "本地、预生产、生产证据", "L0-L4", "失败关闭"):
        assert evidence in text
