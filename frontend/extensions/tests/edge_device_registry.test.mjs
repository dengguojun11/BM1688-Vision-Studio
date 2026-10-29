import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const repoRoot = path.resolve(import.meta.dirname, "../../..");
const page = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/pages/edge_device_registry.html"),
  "utf8",
);

assert.match(page, /id="fieldValidationChannel"/);
assert.match(page, /SSH 兼容验证/);
assert.match(page, /板端主动拉取/);
assert.match(page, /validation_channel:channel/);
assert.match(page, /function shouldAutoConfigureAgent\(body,current\)/);
assert.match(page, /String\(body\.validation_channel\|\|"ssh"\)\.toLowerCase\(\)!=="ssh"/);
assert.match(page, /String\(body\.password\|\|""\)\.trim\(\)\|\|\(current&&\(current\.password_configured===true\|\|current\.private_key_path_configured===true\)\)/);
assert.match(page, /body\.auto_configure_agent=shouldAutoConfigureAgent\(body,current\)/);
assert.match(page, /!current\.password_configured&&!current\.private_key_path_configured/);
assert.match(page, /function agentSaveToast\(saved,body\)/);
assert.match(page, /Agent 自动配置已排队，等待远端心跳/);
assert.match(page, /Agent 自动配置失败：/);
assert.match(page, /Agent 自动配置未启用：/);
assert.match(page, /function toggleValidationChannel\(\)/);
assert.match(page, /board_agent_status/);
assert.match(page, /board_agent_last_heartbeat_at/);
assert.match(page, /board_agent_environment_ready/);
assert.match(page, /function deviceConnectionReady\(item\)/);
assert.match(page, /if\(usesBoardAgent\(item\)\)return String\(item\.board_agent_status\|\|""\)\.toLowerCase\(\)==="online"&&boardAgentHeartbeatFresh\(item\)/);
assert.match(page, /return sshProbeFresh\(item\);/);
assert.match(page, /任务执行中 · 心跳待确认/);
assert.match(page, /连接状态/);
assert.doesNotMatch(page, /item\.available_for_dispatch===true\|\|item\.is_busy===true/);
assert.doesNotMatch(page, /scheduling==="idle"\|\|scheduling==="busy"/);
assert.doesNotMatch(page, /item\.is_busy!==true&&\(item\.available_for_dispatch===true\|\|scheduling==="idle"\)/);
assert.match(page, /板端心跳在线/);
assert.match(page, /板端未在线/);
assert.match(page, /Board Agent 引导进行中/);
assert.match(page, /等待 Agent 心跳/);
assert.match(page, /Board Agent 引导失败/);
assert.match(page, /bootstrapSummaryTitle/);
assert.match(page, /bootstrapDetailTitle/);
assert.match(page, /id="btnBootstrap"/);
assert.match(page, /function bootstrapNeedsAction\(item\)/);
assert.match(page, /async function bootstrapDevice\(id\)/);
assert.match(page, /data-action="bootstrap"/);
assert.match(page, /设备正在执行边端任务，暂不能安装 Agent/);
assert.match(page, /function devicePollingDelay\(\)/);
assert.match(page, /function runtimeLocked\(item\)/);
assert.match(page, /设备正在执行边端任务，暂不能编辑/);
assert.match(page, /btnDetailEdit\"\)\.disabled=runtimeLocked\(item\)/);
assert.match(page, /data-action=\"active\".*runtimeLocked\(item\).*任务结束后再操作/);
assert.match(page, /data-action=\"probe\".*runtimeLocked\(item\).*任务结束后再操作/);
assert.match(page, /data-action=\"delete\".*runtimeLocked\(item\).*任务结束后再操作/);
assert.match(page, /async function setActiveDevice\(id\)\{var item=deviceById\(id\);if\(runtimeLocked\(item\)\)/);
assert.match(page, /async function probeDevice\(id\)\{var item=deviceById\(id\);if\(runtimeLocked\(item\)\)/);
assert.match(page, /function openDelete\(id\)\{var item=deviceById\(id\);if\(!item\)return;if\(runtimeLocked\(item\)/);
assert.match(page, /async function deleteDevice\(\)\{if\(!state\.deletingId\)return;var item=deviceById\(state\.deletingId\);if\(runtimeLocked\(item\)/);
assert.match(page, /切换到 SSH 通道时必须填写 SSH 密码/);
assert.doesNotMatch(page, /抽屉|drawer/i);

console.log("边端设备页面契约：SSH 与板端主动拉取通道分离");
