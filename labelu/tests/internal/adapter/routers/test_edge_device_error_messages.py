"""边端设备管理：中文提示与凭据保护回归测试。

覆盖本次修复的四类问题：
1. 字段级 422 错误提示中文化（不再向中文界面抛 pydantic 英文原文）；
2. 编辑不能同时清除 SSH 通道全部凭据（避免保存成功但设备不可用）；
3. PATCH 不允许把 SSH 设备的 host 清空（与创建校验对齐）；
4. 中文业务提示中的「密码/私钥」字样不再被前端兜底吞掉
   （前端由 edgeDevices.flows.test.tsx 覆盖，这里保证后端 msg 可定位）。
"""

from __future__ import annotations

API = "/api/v1/edge-devices"


def _create(client, headers, **overrides):
    payload = {
        "name": "消息校验设备",
        "host": "10.10.10.10",
        "port": 22,
        "user": "linaro",
        "password": "pw-123456",
        "auto_configure_agent": False,
        "set_active": False,
    }
    payload.update(overrides)
    return client.post(API, headers=headers, json=payload)


def test_create_field_errors_are_localized_chinese(client, testadmin_token_headers):
    out_of_range = _create(client, testadmin_token_headers, port=0)
    assert out_of_range.status_code == 422, out_of_range.text
    body = out_of_range.json()
    assert "SSH 端口" in body["msg"]
    assert "不能小于" in body["msg"]
    assert "Input should be" not in body["msg"]
    assert isinstance(body.get("errors"), list) and body["errors"]

    too_long = _create(client, testadmin_token_headers, name="n" * 65)
    assert too_long.status_code == 422, too_long.text
    long_body = too_long.json()
    assert "设备名称" in long_body["msg"]
    assert "最多 64 个字符" in long_body["msg"]
    assert "String should have" not in long_body["msg"]


def test_create_root_validator_message_drops_english_prefix(
    client, testadmin_token_headers
):
    missing_credential = _create(client, testadmin_token_headers, password=None)
    assert missing_credential.status_code == 422, missing_credential.text
    msg = missing_credential.json()["msg"]
    assert "SSH 通道必须填写 SSH 密码或私钥路径" in msg
    assert not msg.startswith("Value error,")


def test_edit_cannot_clear_the_only_ssh_credential(client, testadmin_token_headers):
    created = _create(client, testadmin_token_headers, password="pw-only")
    assert created.status_code == 201, created.text
    device_id = created.json()["data"]["id"]

    cleared = client.patch(
        f"{API}/{device_id}",
        headers=testadmin_token_headers,
        json={"password_secret_action": "clear"},
    )
    assert cleared.status_code == 400, cleared.text
    assert "不能同时清除全部凭据" in cleared.json()["msg"]

    # 原凭据保持不变，设备仍可用
    detail = client.get(f"{API}/{device_id}", headers=testadmin_token_headers)
    assert detail.status_code == 200
    assert detail.json()["data"]["password_configured"] is True


def test_edit_cannot_blank_ssh_host(client, testadmin_token_headers):
    created = _create(client, testadmin_token_headers)
    device_id = created.json()["data"]["id"]

    blanked = client.patch(
        f"{API}/{device_id}",
        headers=testadmin_token_headers,
        json={"host": ""},
    )
    assert blanked.status_code == 400, blanked.text
    assert "SSH 通道必须填写主机地址" in blanked.json()["msg"]


def test_board_agent_switch_does_not_need_ssh_host(client, testadmin_token_headers):
    """切到设备程序主动连接时允许没有 host，也不能被 host 校验挡住。"""
    created = _create(client, testadmin_token_headers, host="10.10.10.20")
    device_id = created.json()["data"]["id"]

    switched = client.patch(
        f"{API}/{device_id}",
        headers=testadmin_token_headers,
        json={"validation_channel": "board_agent"},
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["data"]["validation_channel"] == "board_agent"
