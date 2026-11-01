"""只读实证探测：/api/v1/edge-devices 的真实 status / msg / 字段投影。

本文件仅用于本轮 bug 定位，不修改任何业务代码。所有网络副作用
（SSH 环境检查、Agent 引导）在测试内被替换为记录器，以保证探针确定性。

运行：
    .venv\\Scripts\\python.exe -m pytest \
        labelu/tests/internal/adapter/routers/test_edge_devices_probe.py \
        -q --tb=short -p no:warnings
"""

import json

import pytest

from labelu.internal.application.service import edge_device_registry as service
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob

API = "/api/v1/edge-devices"

PROBES: list[dict] = []

# 前端 edgeDevices.tsx 顶部 EdgeDevice 类型声明的字段（type EdgeDevice = {...}）
FRONTEND_EDGE_DEVICE_KEYS = {
    "id",
    "name",
    "is_active",
    "enabled",
    "host",
    "port",
    "user",
    "password_masked_hint",
    "password_configured",
    "password_runtime_available",
    "private_key_path_masked",
    "private_key_path_configured",
    "private_key_path_runtime_available",
    "edge_root",
    "remote_base",
    "sophon_demo_root",
    "auto_after_export",
    "run_coco_eval",
    "connect_timeout_sec",
    "validation_timeout_sec",
    "docker_container",
    "docker_use_sudo",
    "validation_channel",
    "board_agent_status",
    "board_agent_last_heartbeat_at",
    "board_agent_environment_ready",
    "board_agent_message",
    "agent_bootstrap_status",
    "agent_bootstrap_control_plane_enabled",
    "agent_control_plane_source",
    "agent_control_plane_message",
    "agent_control_plane_public_base_url_configured",
    "agent_bootstrap_stage",
    "agent_bootstrap_code",
    "agent_bootstrap_message",
    "agent_bootstrap_version",
    "agent_bootstrap_updated_at",
    "probe_status",
    "probe_message",
    "probe_checked_at",
    "last_probe_status",
    "last_probe_message",
    "last_probe_checked_at",
    "last_validation_status",
    "last_validation_at",
    "environment_status",
    "environment_message",
    "environment_checked_at",
    "environment_report",
    "last_used_at",
    "scheduling_status",
    "is_busy",
    "active_job_id",
    "available_for_dispatch",
    "created_at",
    "updated_at",
}


def rec(label: str, resp, extra: dict | None = None) -> dict:
    """记录一次真实 HTTP 响应（status + msg + detail + err_code）。"""

    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 - 非 JSON 响应也要留证
        body = {"_raw": resp.text[:400]}
    row = {
        "label": label,
        "status": resp.status_code,
        "msg": body.get("msg") if isinstance(body, dict) else None,
        "detail": body.get("detail") if isinstance(body, dict) else None,
        "err_code": body.get("err_code") if isinstance(body, dict) else None,
        "extra": extra or {},
    }
    PROBES.append(row)
    print(
        "[PROBE] {label} | status={status} | msg={msg!r} | detail={detail!r} | err_code={err_code!r}".format(
            **row
        )
    )
    return body


def _data(resp_or_body):
    body = resp_or_body if isinstance(resp_or_body, dict) else resp_or_body.json()
    assert "data" in body, body
    return body["data"]


@pytest.fixture(autouse=True)
def probe_isolated_side_effects(monkeypatch):
    """把远端副作用替换为内存记录器（不改业务代码）。

    1) SSH 环境检查交给现有用例同样的 mock 方式；
    2) auto_configure_agent=true 时自动触发的 Agent SSH 引导也不真正执行。
    """

    calls: list[tuple] = []
    monkeypatch.setattr(
        service,
        "_run_environment_check_sync",
        lambda device_id, *, provision=True: calls.append(("env_check", device_id)),
    )
    monkeypatch.setattr(
        service,
        "_start_auto_agent_bootstrap",
        lambda device_id: calls.append(("agent_bootstrap", device_id)) or True,
    )
    yield calls


def create_device(client, headers, **payload):
    payload.setdefault("set_active", True)
    return client.post(API, headers=headers, json=payload)


def _device_a(
    client,
    headers,
    *,
    name="探测设备A",
    host="10.20.30.40",
    password="pw-12345",
    **extra,
):
    resp = create_device(
        client,
        headers,
        name=name,
        host=host,
        user="linaro",
        password=password,
        auto_configure_agent=False,
        **extra,
    )
    assert resp.status_code == 201, resp.text
    return _data(resp)


def test_01_list_and_detail_projection(client, testadmin_token_headers):
    print(
        "[PROBE] settings.YOLO_TRAIN_ENABLED={!r}（.env 生效值）".format(
            settings.YOLO_TRAIN_ENABLED
        )
    )
    device = _device_a(client, testadmin_token_headers)

    listed = client.get(
        API, headers=testadmin_token_headers, params={"page": 0, "size": 10}
    )
    body = rec("GET /edge-devices (admin, page/size)", listed)
    assert listed.status_code == 200
    list_data = body["data"]
    item = next(d for d in list_data["devices"] if d["id"] == device["id"])

    detail_resp = client.get(f"{API}/{device['id']}", headers=testadmin_token_headers)
    detail_body = rec("GET /edge-devices/{id} (admin)", detail_resp)
    assert detail_resp.status_code == 200
    detail = detail_body["data"]

    print(
        "[PROBE] ids | create.id={!r} type={} | list.id 结构相同={} | detail.id 相同={}".format(
            device["id"],
            type(device["id"]).__name__,
            "id" in item,
            detail.get("id") == device["id"],
        )
    )
    print("[PROBE] list 顶层键={}".format(sorted(list_data.keys())))
    print("[PROBE] list项键={}".format(sorted(item.keys())))
    print(
        "[PROBE] detail键==list项键: {}".format(
            sorted(detail.keys()) == sorted(item.keys())
        )
    )

    frontend_missing = sorted(FRONTEND_EDGE_DEVICE_KEYS - set(item.keys()))
    backend_extra = sorted(set(item.keys()) - FRONTEND_EDGE_DEVICE_KEYS)
    print("[PROBE] 前端类型有/后端未返回(admin)={}".format(frontend_missing))
    print("[PROBE] 后端多出/前端类型未声明={}".format(backend_extra))

    # 非管理员视角：敏感字段应被 response_model_exclude_none 投影掉
    user_list = client.get(API, headers=testadmin_token_headers)
    assert user_list.status_code == 200
    print(
        "[PROBE] admin list 项含 host/port/user: {}".format(
            [k for k in ("host", "port", "user") if k in item]
        )
    )
    print(
        "[PROBE] detail.is_active={} scheduling_status={!r} available_for_dispatch={} is_busy={}".format(
            detail.get("is_active"),
            detail.get("scheduling_status"),
            detail.get("available_for_dispatch"),
            detail.get("is_busy"),
        )
    )

    assert isinstance(device["id"], str)
    assert sorted(detail.keys()) == sorted(item.keys())


def test_02_create_validation_matrix(client, testadmin_token_headers):
    cases = [
        (
            "host 空串(前端 host:'' 路径)",
            {"host": "", "user": "linaro", "password": "pw"},
            422,
        ),
        ("credential 全空", {"host": "10.1.1.1", "user": "linaro"}, 201),
        ("password 空串", {"host": "10.1.1.1", "user": "linaro", "password": ""}, 201),
        (
            "name 65 字符",
            {"name": "n" * 65, "host": "10.1.1.1", "user": "linaro", "password": "pw"},
            422,
        ),
        (
            "port=0",
            {"host": "10.1.1.1", "user": "linaro", "password": "pw", "port": 0},
            422,
        ),
        (
            "port=70000",
            {"host": "10.1.1.1", "user": "linaro", "password": "pw", "port": 70000},
            422,
        ),
        (
            "port='abc'",
            {"host": "10.1.1.1", "user": "linaro", "password": "pw", "port": "abc"},
            422,
        ),
        ("user=''", {"host": "10.1.1.1", "user": "", "password": "pw"}, 422),
        (
            "docker_container=None",
            {
                "host": "10.1.1.1",
                "user": "linaro",
                "password": "pw",
                "docker_container": None,
            },
            422,
        ),
        (
            "validation_channel='telnet'",
            {
                "host": "10.1.1.1",
                "user": "linaro",
                "password": "pw",
                "validation_channel": "telnet",
            },
            422,
        ),
        (
            "validation_channel='board_agent' 无凭据",
            {"validation_channel": "board_agent"},
            201,
        ),
    ]
    observed = []
    for label, payload, expected in cases:
        resp = create_device(
            client, testadmin_token_headers, auto_configure_agent=False, **payload
        )
        body = rec(f"POST /edge-devices [{label}]", resp)
        observed.append((label, resp.status_code, expected))
        if resp.status_code == 422:
            assert "msg" in body, body
            assert "detail" not in body, body
            first_msg = (body.get("errors") or [{}])[0].get("msg")
            print("[PROBE]   422.errors[0].msg={!r}".format(first_msg))
    for label, status, expected in observed:
        assert status == expected, (label, status, expected)

    # 新增设备不填账号/密码时使用部署默认值（linaro / 默认密码）
    default_credential = create_device(
        client, testadmin_token_headers, host="10.1.1.99", auto_configure_agent=False
    )
    default_body = rec("POST [不填账号密码 -> 部署默认凭据]", default_credential)
    assert default_credential.status_code == 201
    default_device = default_body["data"]
    print(
        "[PROBE] 默认凭据 user={!r} password_configured={} masked={!r}".format(
            default_device.get("user"),
            default_device.get("password_configured"),
            default_device.get("password_masked_hint"),
        )
    )
    assert default_device.get("user") == "linaro"
    assert default_device.get("password_configured") is True
    assert default_device.get("password_masked_hint")


def test_03_create_defaults_duplicates_and_frontend_payload(
    client, testadmin_token_headers
):
    # 前端 create 真实 payload 形状：name 可为 null、host 由 trim||'' 得到
    resp = create_device(
        client,
        testadmin_token_headers,
        name=None,
        host="10.2.2.2",
        user="linaro",
        password="pw-2222",
        port=22,
        docker_container="bm1688",
        auto_configure_agent=False,
        set_active=True,
    )
    first = rec("POST [前端形状 name=null]", resp)
    assert resp.status_code == 201
    dev = first["data"]
    print("[PROBE] name=null 落库 name={!r} (回退 host?)".format(dev.get("name")))
    print(
        "[PROBE] 默认值 enabled={} auto_after_export={} run_coco_eval={} docker_container={!r} "
        "validation_channel={!r} is_active={} environment_status={!r}".format(
            dev.get("enabled"),
            dev.get("auto_after_export"),
            dev.get("run_coco_eval"),
            dev.get("docker_container"),
            dev.get("validation_channel"),
            dev.get("is_active"),
            dev.get("environment_status"),
        )
    )

    dup_host = create_device(
        client,
        testadmin_token_headers,
        name="另一个名字",
        host="10.2.2.2",
        user="linaro",
        password="pw",
        auto_configure_agent=False,
        set_active=False,
    )
    rec("POST [重复 host]", dup_host)
    dup_name = create_device(
        client,
        testadmin_token_headers,
        name=dev.get("name"),
        host="10.2.2.3",
        user="linaro",
        password="pw",
        auto_configure_agent=False,
        set_active=False,
    )
    rec("POST [重复 name]", dup_name)
    print(
        "[PROBE] 重复 host/name 是否被拒: host={} name={}".format(
            dup_host.status_code, dup_name.status_code
        )
    )

    # 前端编辑/新增真实会带 auto_configure_agent=true（ssh+host+credential 时）
    auto = create_device(
        client,
        testadmin_token_headers,
        name="自动配置设备",
        host="10.2.2.4",
        user="linaro",
        password="pw-4444",
        auto_configure_agent=True,
        set_active=False,
    )
    auto_body = rec("POST [auto_configure_agent=true，前端真实路径]", auto)
    assert auto.status_code == 201
    auto_dev = auto_body["data"]
    print(
        "[PROBE] auto=true 响应 agent_bootstrap_control_plane_enabled={} agent_bootstrap_status={!r} "
        "environment_status={!r} environment_message={!r} agent_control_plane_source={!r}".format(
            auto_dev.get("agent_bootstrap_control_plane_enabled"),
            auto_dev.get("agent_bootstrap_status"),
            auto_dev.get("environment_status"),
            auto_dev.get("environment_message"),
            auto_dev.get("agent_control_plane_source"),
        )
    )
    assert dup_host.status_code == 201
    assert dup_name.status_code == 201


def test_04_patch_secret_actions(client, testadmin_token_headers):
    dev = _device_a(client, testadmin_token_headers, host="10.3.3.3")

    keep = client.patch(
        f"{API}/{dev['id']}",
        headers=testadmin_token_headers,
        json={"name": "改名", "password_secret_action": "keep"},
    )
    keep_data = _data(rec("PATCH [password_secret_action=keep, 不传 password]", keep))
    print(
        "[PROBE] keep 后 password_configured={} password_masked_hint={!r} name={!r}".format(
            keep_data.get("password_configured"),
            keep_data.get("password_masked_hint"),
            keep_data.get("name"),
        )
    )

    set_empty = client.patch(
        f"{API}/{dev['id']}",
        headers=testadmin_token_headers,
        json={"password_secret_action": "set"},
    )
    rec("PATCH [password_secret_action=set 且不传 password]", set_empty)
    assert set_empty.status_code == 400

    set_ok = client.patch(
        f"{API}/{dev['id']}",
        headers=testadmin_token_headers,
        json={"password_secret_action": "set", "password": "new-pw-999"},
    )
    rec("PATCH [password_secret_action=set + password]", set_ok)
    assert set_ok.status_code == 200

    empty_name = client.patch(
        f"{API}/{dev['id']}", headers=testadmin_token_headers, json={"name": ""}
    )
    empty_name_data = _data(rec("PATCH [name='' 试图清空名称]", empty_name))
    print(
        "[PROBE] name='' 后 name={!r}（是否可清空）".format(empty_name_data.get("name"))
    )

    # 只用私钥的 SSH 设备，清除私钥后是否会变成"零凭据"设备
    key_dev = create_device(
        client,
        testadmin_token_headers,
        name="私钥设备",
        host="10.3.3.9",
        user="linaro",
        private_key_path="/opt/labelu/.ssh/id_ed25519",
        auto_configure_agent=False,
        set_active=False,
    )
    key_data = _data(rec("POST [仅 private_key_path]", key_dev))
    assert key_dev.status_code == 201
    cleared = client.patch(
        f"{API}/{key_data['id']}",
        headers=testadmin_token_headers,
        json={"private_key_path_secret_action": "clear"},
    )
    cleared_data = rec(
        "PATCH [private_key_path_secret_action=clear（唯一凭据）]", cleared
    )
    print(
        "[PROBE] clear 后 msg={!r}（期望 400 拦截零凭据）".format(
            cleared_data.get("msg")
        )
    )
    # 修复后：SSH 通道不允许同时清除全部凭据，必须 400 且给出中文可定位提示。
    assert cleared.status_code == 400
    assert "不能同时清除全部凭据" in str(cleared.json().get("msg") or "")

    legacy = client.patch(
        f"{API}/{dev['id']}",
        headers=testadmin_token_headers,
        json={"clear_private_key_path": True},
    )
    rec("PATCH [clear_private_key_path=true（无已存私钥）]", legacy)


def test_05_patch_channel_switch(client, testadmin_token_headers):
    dev = _device_a(client, testadmin_token_headers, host="10.4.4.4")

    to_agent = client.patch(
        f"{API}/{dev['id']}",
        headers=testadmin_token_headers,
        json={"validation_channel": "board_agent"},
    )
    agent_data = _data(rec("PATCH [ssh -> board_agent]", to_agent))
    print(
        "[PROBE] 切换后 channel={!r} password_configured={} private_key_path_configured={} host={!r}".format(
            agent_data.get("validation_channel"),
            agent_data.get("password_configured"),
            agent_data.get("private_key_path_configured"),
            agent_data.get("host"),
        )
    )

    back = client.patch(
        f"{API}/{dev['id']}",
        headers=testadmin_token_headers,
        json={"validation_channel": "ssh"},
    )
    back_body = _data(rec("PATCH [board_agent -> ssh 无凭据 -> 部署默认凭据]", back))
    # 切回 SSH 且用户未填凭据时，服务端使用部署默认账号/密码，避免切换后无法保存。
    assert back.status_code == 200
    assert back_body.get("validation_channel") == "ssh"
    assert back_body.get("password_configured") is True

    back_ok = client.patch(
        f"{API}/{dev['id']}",
        headers=testadmin_token_headers,
        json={
            "validation_channel": "ssh",
            "password_secret_action": "set",
            "password": "pw-4444",
        },
    )
    rec("PATCH [board_agent -> ssh + 新密码]", back_ok)
    assert back_ok.status_code == 200

    alias = client.patch(
        f"{API}/{dev['id']}",
        headers=testadmin_token_headers,
        json={"validation_channel": "board-agent"},
    )
    rec("PATCH [validation_channel='board-agent' 别名]", alias)
    assert alias.status_code == 422


def test_06_patch_and_get_error_paths(
    client, testadmin_token_headers, testuser_token_headers
):
    rec(
        "PATCH [不存在 id]",
        client.patch(
            f"{API}/edge_not_exist", headers=testadmin_token_headers, json={"name": "x"}
        ),
    )
    rec(
        "GET [不存在 id]",
        client.get(f"{API}/edge_not_exist", headers=testadmin_token_headers),
    )
    rec(
        "DELETE [不存在 id]",
        client.delete(f"{API}/edge_not_exist", headers=testadmin_token_headers),
    )
    rec(
        "PUT /active [不存在 id]",
        client.put(
            f"{API}/active",
            headers=testadmin_token_headers,
            json={"device_id": "edge_not_exist"},
        ),
    )
    rec(
        "PUT /active [缺 device_id]",
        client.put(f"{API}/active", headers=testadmin_token_headers, json={}),
    )

    dev = _device_a(client, testadmin_token_headers, host="10.5.5.5")
    for label, payload in (
        ("port=0", {"port": 0}),
        ("connect_timeout_sec=0", {"connect_timeout_sec": 0}),
        ("validation_timeout_sec=90000", {"validation_timeout_sec": 90000}),
        ("validation_channel='telnet'", {"validation_channel": "telnet"}),
        (
            "private_key_path_secret_action='set' 无路径",
            {"private_key_path_secret_action": "set"},
        ),
    ):
        resp = client.patch(
            f"{API}/{dev['id']}", headers=testadmin_token_headers, json=payload
        )
        body = rec(f"PATCH [{label}]", resp)
        assert resp.status_code == 422
        assert "msg" in body and "detail" not in body

    # 非管理员（前端 actionError 在 403 时给固定提示）
    rec(
        "POST [testuser 非管理员]",
        create_device(
            client,
            testuser_token_headers,
            name="u",
            host="10.6.6.6",
            user="linaro",
            password="pw",
        ),
    )
    rec(
        "DELETE [testuser 非管理员]",
        client.delete(f"{API}/{dev['id']}", headers=testuser_token_headers),
    )
    user_list = client.get(API, headers=testuser_token_headers)
    rec("GET list [testuser 非管理员]", user_list)
    user_item = user_list.json()["data"]["devices"][0]
    print(
        "[PROBE] 非管理员 list 项敏感字段 host/port/user 是否存在: {}".format(
            [k for k in ("host", "port", "user") if k in user_item]
        )
    )
    user_detail = client.get(f"{API}/{dev['id']}", headers=testuser_token_headers)
    rec("GET detail [testuser 非管理员]", user_detail)
    print(
        "[PROBE] 非管理员 detail 敏感字段: {}".format(
            [k for k in ("host", "port", "user") if k in user_detail.json()["data"]]
        )
    )


def test_06b_patch_field_validation(client, testadmin_token_headers):
    dev = _device_a(
        client, testadmin_token_headers, name="字段校验设备", host="10.5.5.10"
    )
    host_change = client.patch(
        f"{API}/{dev['id']}",
        headers=testadmin_token_headers,
        json={"host": "10.5.5.11"},
    )
    host_data = _data(
        rec("PATCH [只改 host，password_secret_action=keep]", host_change)
    )
    print(
        "[PROBE] 改 host 后 host={!r} password_configured={} environment_status={!r}".format(
            host_data.get("host"),
            host_data.get("password_configured"),
            host_data.get("environment_status"),
        )
    )

    empty_host = client.patch(
        f"{API}/{dev['id']}", headers=testadmin_token_headers, json={"host": ""}
    )
    empty_host_body = rec("PATCH [host='']", empty_host)
    print(
        "[PROBE] host='' 后 msg={!r}（期望后端拦截空 host）".format(
            empty_host_body.get("msg")
        )
    )
    assert empty_host.status_code == 400
    assert "必须填写主机地址" in str(empty_host_body.get("msg") or "")

    empty_user = client.patch(
        f"{API}/{dev['id']}", headers=testadmin_token_headers, json={"user": ""}
    )
    empty_user_data = _data(rec("PATCH [user='']", empty_user))
    print(
        "[PROBE] user='' 后 user={!r}（create 要求 min_length=1）".format(
            empty_user_data.get("user")
        )
    )

    long_name = client.patch(
        f"{API}/{dev['id']}", headers=testadmin_token_headers, json={"name": "n" * 65}
    )
    rec("PATCH [name=65 字符]", long_name)

    cleared_pw = client.patch(
        f"{API}/{dev['id']}",
        headers=testadmin_token_headers,
        json={"password_secret_action": "clear"},
    )
    cleared_pw_body = rec(
        "PATCH [password_secret_action=clear（唯一凭据）]", cleared_pw
    )
    print(
        "[PROBE] password clear 后 msg={!r}（期望拦截零凭据）".format(
            cleared_pw_body.get("msg")
        )
    )
    assert cleared_pw.status_code == 400
    assert "不能同时清除全部凭据" in str(cleared_pw_body.get("msg") or "")


def test_07_active_and_delete_semantics(client, testadmin_token_headers):
    first = _device_a(client, testadmin_token_headers, name="设备1", host="10.7.7.1")
    second = _device_a(
        client, testadmin_token_headers, name="设备2", host="10.7.7.2", set_active=False
    )

    rec(
        "DELETE [当前设备，尚有其它设备]",
        client.delete(f"{API}/{first['id']}", headers=testadmin_token_headers),
    )

    switched = client.put(
        f"{API}/active",
        headers=testadmin_token_headers,
        json={"device_id": second["id"]},
    )
    rec("PUT /active [切换到设备2]", switched)
    assert switched.status_code == 200

    deleted = client.delete(f"{API}/{first['id']}", headers=testadmin_token_headers)
    deleted_body = rec("DELETE [非当前设备]", deleted)
    print(
        "[PROBE] 删除返回体类型 data.active_device_id={!r} 剩余 devices={}".format(
            deleted_body["data"].get("active_device_id"),
            len(deleted_body["data"].get("devices") or []),
        )
    )

    last = client.delete(f"{API}/{second['id']}", headers=testadmin_token_headers)
    last_body = rec("DELETE [唯一且当前设备]", last)
    print(
        "[PROBE] 删除唯一设备后 active_device_id={!r} devices={}".format(
            last_body.get("data", {}).get("active_device_id"),
            last_body.get("data", {}).get("devices"),
        )
    )
    assert last.status_code in (200, 400)


def test_08_busy_device_guards(client, testadmin_token_headers, db):
    dev = _device_a(client, testadmin_token_headers, name="忙碌设备", host="10.8.8.8")

    from labelu.internal.adapter.persistence import crud_user

    owner = crud_user.get_user_by_username(db=db, username="test@example.com")
    with begin_transaction(db):
        task = Task(
            name="probe busy task",
            description="d",
            tips="t",
            config=json.dumps({}, ensure_ascii=False),
            created_by=owner.id,
            updated_by=owner.id,
            media_type="IMAGE",
        )
        db.add(task)
        db.flush()
        job = YoloEdgeValidationJob(
            user_id=owner.id,
            task_id=task.id,
            export_id=1,
            status="PROCESSING",
            mode="run_only",
            source_type="internal",
            image_count=0,
            labeled_image_count=0,
            unlabeled_image_count=0,
            edge_device_id=dev["id"],
        )
        db.add(job)
        db.flush()
        job_id = job.id

    detail = client.get(f"{API}/{dev['id']}", headers=testadmin_token_headers)
    rec("GET detail [执行中任务]", detail)
    d = detail.json()["data"]
    print(
        "[PROBE] busy 投影 is_busy={} active_job_id={} scheduling_status={!r} available_for_dispatch={}".format(
            d.get("is_busy"),
            d.get("active_job_id"),
            d.get("scheduling_status"),
            d.get("available_for_dispatch"),
        )
    )

    rec(
        f"DELETE [执行中任务 job#{job_id}]",
        client.delete(f"{API}/{dev['id']}", headers=testadmin_token_headers),
    )
    rec(
        "PUT /active [执行中任务]",
        client.put(
            f"{API}/active",
            headers=testadmin_token_headers,
            json={"device_id": dev["id"]},
        ),
    )
    rec(
        "PATCH [执行中任务 改 host]",
        client.patch(
            f"{API}/{dev['id']}",
            headers=testadmin_token_headers,
            json={"host": "10.8.8.9"},
        ),
    )
    name_only = client.patch(
        f"{API}/{dev['id']}", headers=testadmin_token_headers, json={"name": "只改名字"}
    )
    rec("PATCH [执行中任务 只改 name]", name_only)
    rec(
        "POST environment-check [执行中任务]",
        client.post(
            f"{API}/{dev['id']}/environment-check", headers=testadmin_token_headers
        ),
    )


def test_09_probe_envcheck_bootstrap_endpoints(client, testadmin_token_headers):
    rec(
        "POST /{id}/probe [不存在 id]",
        client.post(f"{API}/edge_not_exist/probe", headers=testadmin_token_headers),
    )
    rec(
        "GET /{id}/agent-bootstrap [不存在 id]",
        client.get(
            f"{API}/edge_not_exist/agent-bootstrap", headers=testadmin_token_headers
        ),
    )
    rec(
        "POST /agent-bootstrap [不存在 id]",
        client.post(
            f"{API}/agent-bootstrap",
            headers=testadmin_token_headers,
            json={"device_id": "edge_not_exist", "confirm_install": True},
        ),
    )

    dev = _device_a(client, testadmin_token_headers, host="10.9.9.9")
    rec(
        "POST /{id}/agent-bootstrap [body.device_id 与路径不一致]",
        client.post(
            f"{API}/{dev['id']}/agent-bootstrap",
            headers=testadmin_token_headers,
            json={"device_id": "edge_other", "confirm_install": True},
        ),
    )

    board = create_device(
        client,
        testadmin_token_headers,
        name="主动连接设备",
        validation_channel="board_agent",
        set_active=False,
    )
    board_dev = _data(rec("POST [board_agent]", board))
    rec(
        "POST /{id}/environment-check [board_agent]",
        client.post(
            f"{API}/{board_dev['id']}/environment-check",
            headers=testadmin_token_headers,
        ),
    )

    all_checks = client.post(
        f"{API}/environment-check-all",
        headers=testadmin_token_headers,
        params={"only_pending": True},
    )
    rec("POST /environment-check-all", all_checks)


def test_99_summary():
    statuses = [row["status"] for row in PROBES if row["status"] >= 100]
    print("[PROBE] ---- 汇总：共 {} 次真实请求 ----".format(len(statuses)))
    print(
        "[PROBE] status 分布={}".format(
            {code: statuses.count(code) for code in sorted(set(statuses))}
        )
    )
    five_hundred = [row for row in PROBES if row["status"] >= 500]
    print(
        "[PROBE] 500 响应={}".format(
            [(r["label"], r["status"], r["msg"] or r["detail"]) for r in five_hundred]
        )
    )
    empty_msg_errors = [
        (row["label"], row["status"])
        for row in PROBES
        if 400 <= row["status"] < 600 and not (row["msg"] or row["detail"])
    ]
    print("[PROBE] 无 msg/detail 的错误响应={}".format(empty_msg_errors))
    english_details = [
        (row["label"], row["msg"] or row["detail"])
        for row in PROBES
        if 400 <= row["status"] < 600 and row["detail"] and not row["msg"]
    ]
    print("[PROBE] 只有 detail(英文) 的错误响应={}".format(english_details))
    assert not five_hundred, five_hundred
    assert not empty_msg_errors, empty_msg_errors
