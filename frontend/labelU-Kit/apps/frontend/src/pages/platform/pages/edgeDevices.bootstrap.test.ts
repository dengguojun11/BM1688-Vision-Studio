import { describe, expect, it } from 'vitest';

import {
  bootstrapMessage,
  bootstrapNeedsAction,
  deviceStatusNeedsPolling,
  deviceHeartbeatNeedsPolling,
  deviceSaveSuccessMessage,
  isDispatchable,
  readinessReason,
  readinessTitle,
  shouldAutoConfigureAgent,
} from './edgeDevices';

describe('edge device agent bootstrap copy', () => {
  it('keeps watching a board-agent device until its first heartbeat arrives', () => {
    expect(
      deviceStatusNeedsPolling({
        id: 'board-agent-pending',
        validation_channel: 'board_agent',
        agent_bootstrap_status: 'NOT_STARTED',
        board_agent_status: 'unknown',
        board_agent_environment_ready: false,
      }),
    ).toBe(true);
    expect(
      deviceStatusNeedsPolling({
        id: 'board-agent-ready',
        validation_channel: 'board_agent',
        agent_bootstrap_status: 'READY',
        board_agent_status: 'online',
        board_agent_last_heartbeat_at: new Date().toISOString(),
        board_agent_environment_ready: true,
      }),
    ).toBe(false);
    expect(
      deviceHeartbeatNeedsPolling({
        id: 'board-agent-ready',
        validation_channel: 'board_agent',
        agent_bootstrap_status: 'READY',
        board_agent_status: 'online',
        board_agent_environment_ready: true,
      }),
    ).toBe(true);
  });

  it('keeps watching an automatically enrolled SSH device after a delayed queue write', () => {
    expect(
      deviceStatusNeedsPolling({
        id: 'ssh-auto-bootstrap-pending',
        validation_channel: 'ssh',
        agent_bootstrap_control_plane_enabled: true,
        agent_bootstrap_status: 'NOT_STARTED',
      }),
    ).toBe(true);
    expect(
      deviceStatusNeedsPolling({
        id: 'ssh-auto-bootstrap-failed',
        validation_channel: 'ssh',
        agent_bootstrap_control_plane_enabled: true,
        agent_bootstrap_status: 'FAILED',
      }),
    ).toBe(false);
  });

  it('does not tell device-program connections to install over SSH', () => {
    expect(
      bootstrapMessage({
        id: 'board-1',
        validation_channel: 'board_agent',
        agent_bootstrap_status: 'NOT_STARTED',
      }),
    ).toContain('等待设备程序首次心跳');
    expect(
      bootstrapMessage({
        id: 'board-1',
        validation_channel: 'board_agent',
        agent_bootstrap_status: 'NOT_STARTED',
      }),
    ).not.toContain('SSH');
  });

  it('keeps neutral onboarding guidance for an SSH device', () => {
    expect(
      bootstrapMessage({
        id: 'ssh-1',
        validation_channel: 'ssh',
        agent_bootstrap_status: 'NOT_STARTED',
      }),
    ).toContain('尚未完成 Agent 引导');
  });

  it('prioritizes an active task over stale readiness fields', () => {
    const device = {
      id: 'busy-1',
      validation_channel: 'ssh' as const,
      is_busy: true,
      active_job_id: 77,
      environment_status: 'not_ready',
    };

    expect(readinessReason(device)).toContain('正在执行边端任务 #77');
    expect(readinessTitle(device)).toContain('正在执行边端任务 #77');
    expect(readinessTitle(device)).not.toContain('尚未就绪');
  });

  it('does not offer Agent restart while a device is executing a task', () => {
    expect(
      bootstrapNeedsAction({
        id: 'busy-bootstrap-1',
        validation_channel: 'ssh',
        is_busy: true,
        active_job_id: 88,
        agent_bootstrap_status: 'FAILED',
      }),
    ).toBe(false);
  });

  it('does not expose a stale dispatchable projection during active bootstrap', () => {
    expect(
      isDispatchable({
        available_for_dispatch: true,
        is_busy: false,
        agent_bootstrap_status: 'WAITING_HEARTBEAT',
      }),
    ).toBe(false);
  });

  it('does not trust availability when the persisted Agent bootstrap failed', () => {
    expect(
      isDispatchable({
        available_for_dispatch: true,
        is_busy: false,
        agent_bootstrap_status: 'FAILED',
      }),
    ).toBe(false);
  });

  it('offers a retry after a board-agent install fails', () => {
    expect(
      bootstrapNeedsAction({
        id: 'board-agent-failed',
        validation_channel: 'board_agent',
        agent_bootstrap_status: 'FAILED',
        is_busy: false,
      }),
    ).toBe(true);
  });

  it('prioritizes the bootstrap stage and heartbeat wait over stale SSH readiness fields', () => {
    const device = {
      id: 'ssh-bootstrap-1',
      validation_channel: 'ssh' as const,
      agent_bootstrap_status: 'WAITING_HEARTBEAT',
      agent_bootstrap_message: 'Agent 已启动，等待带设备身份的有效心跳',
      password_configured: true,
      password_runtime_available: true,
      last_probe_status: 'failed',
      last_probe_message: 'SSH 连接尚未通过',
      environment_status: 'not_ready',
    };

    expect(readinessReason(device)).toContain('等待 Agent 心跳');
    expect(readinessReason(device)).toContain('等待带设备身份的有效心跳');
    expect(readinessReason(device)).not.toContain('SSH 连接尚未通过');
    expect(readinessTitle(device)).toContain('引导进行中');
    expect(readinessTitle(device)).toContain('等待 Agent 心跳');
    expect(readinessTitle(device)).not.toContain('尚未就绪');
  });

  it('keeps bootstrap priority after the channel switches to board-agent for heartbeat', () => {
    const device = {
      id: 'board-agent-bootstrap-1',
      validation_channel: 'board_agent' as const,
      agent_bootstrap_status: 'WAITING_HEARTBEAT',
      agent_bootstrap_message: 'Agent 已启动，等待带设备身份的有效心跳',
      board_agent_status: 'offline',
      board_agent_message: 'Board Agent 已安装，等待首次有效心跳',
      board_agent_environment_ready: false,
    };

    expect(readinessReason(device)).toContain('Board Agent 引导进行中');
    expect(readinessReason(device)).toContain('等待 Agent 心跳');
    expect(readinessTitle(device)).toContain('等待 Agent 心跳');
    expect(readinessTitle(device)).not.toContain('当前设备尚未就绪');
  });

  it('surfaces bootstrap failure details before legacy SSH readiness fields', () => {
    const device = {
      id: 'ssh-bootstrap-failed',
      validation_channel: 'ssh' as const,
      agent_bootstrap_status: 'FAILED',
      agent_bootstrap_code: 'ssh_credentials_missing',
      agent_bootstrap_message: 'SSH 凭据不可用',
      password_configured: true,
      password_runtime_available: true,
      last_probe_status: 'failed',
      last_probe_message: 'SSH 连接尚未通过',
    };

    expect(readinessReason(device)).toBe('Board Agent 引导失败：SSH 凭据不可用（ssh_credentials_missing）');
    expect(readinessReason(device)).not.toContain('当前设备尚未就绪');
    expect(readinessTitle(device)).toBe('Board Agent 引导失败');
  });

  it('does not offer server-side Agent installation when the control plane is disabled', () => {
    const device = {
      id: 'ssh-bootstrap-disabled',
      validation_channel: 'ssh' as const,
      agent_bootstrap_status: 'FAILED',
      agent_bootstrap_control_plane_enabled: false,
      available_for_dispatch: true,
      password_configured: true,
      password_runtime_available: true,
    };

    expect(bootstrapNeedsAction(device)).toBe(false);
    expect(isDispatchable(device)).toBe(false);
    expect(bootstrapMessage(device)).toContain('Agent 自动配置当前不可用');
    expect(bootstrapMessage(device)).not.toContain('BOARD_AGENT_TOKEN');
    expect(readinessReason(device)).toContain('自动配置');
    expect(readinessReason(device)).not.toContain('BOARD_AGENT_PRIMARY_ENABLED');
    expect(readinessTitle(device)).toBe('Agent 自动配置未完成');
  });

  it('keeps a legacy SSH device dispatchable when its environment is ready', () => {
    const device = {
      id: 'ssh-legacy-ready',
      validation_channel: 'ssh' as const,
      agent_bootstrap_status: 'NOT_STARTED',
      agent_bootstrap_control_plane_enabled: false,
      available_for_dispatch: true,
      password_configured: true,
      password_runtime_available: true,
      last_probe_status: 'ok',
      last_probe_checked_at: new Date().toISOString(),
      environment_status: 'ready',
      environment_report: { environment_ready: true },
    };

    expect(bootstrapNeedsAction(device)).toBe(false);
    expect(readinessTitle(device)).toBe('当前设备可执行边端检查');
    expect(readinessReason(device)).toBe('连接、凭据和 BM1688 环境均已就绪');
    expect(bootstrapMessage(device)).toContain('Agent 自动配置当前不可用');
  });

  it('describes which onboarding path was queued after adding a device', () => {
    expect(deviceSaveSuccessMessage({ validation_channel: 'ssh', agent_bootstrap_control_plane_enabled: true }, false)).toBe(
      '设备已新增，Agent 自动配置已排队',
    );
    expect(deviceSaveSuccessMessage({ validation_channel: 'ssh', agent_bootstrap_control_plane_enabled: false }, false)).toBe(
      '设备已新增，SSH 环境检查已排队',
    );
    expect(deviceSaveSuccessMessage({ validation_channel: 'board_agent' }, false)).toBe('设备已新增，等待设备 Agent 心跳');
    expect(deviceSaveSuccessMessage(undefined, true)).toBe('设备配置已更新');
  });

  it('surfaces a synchronous automatic setup failure without claiming it was queued', () => {
    expect(
      deviceSaveSuccessMessage(
        {
          validation_channel: 'ssh',
          agent_bootstrap_control_plane_enabled: false,
          agent_bootstrap_status: 'FAILED',
          agent_bootstrap_code: 'public_base_url_missing',
        },
        false,
      ),
    ).toBe('设备已新增，自动配置失败：服务端没有可被边端访问的回连地址，请检查服务监听地址和局域网网络。');
  });

  it('uses the safe callback status when automatic setup cannot reach the server', () => {
    expect(
      bootstrapMessage({
        id: 'ssh-callback-missing',
        validation_channel: 'ssh',
        agent_bootstrap_control_plane_enabled: false,
        agent_control_plane_public_base_url_configured: false,
      }),
    ).toContain('边端可访问的回连地址');
  });

  it('never renders control-plane environment names from a backend diagnostic', () => {
    const device = {
      id: 'ssh-secret-safe',
      validation_channel: 'ssh' as const,
      agent_bootstrap_control_plane_enabled: false,
      agent_control_plane_message: '请配置 BOARD_AGENT_TOKEN=super-secret',
    };
    expect(bootstrapMessage(device)).not.toContain('BOARD_AGENT_TOKEN');
    expect(bootstrapMessage(device)).not.toContain('super-secret');
  });

  it('opts SSH saves into server-side automatic Agent configuration', () => {
    expect(
      shouldAutoConfigureAgent({
        validation_channel: 'ssh',
        host: '192.168.54.111',
        user: 'linaro',
        password: 'password',
      }),
    ).toBe(true);
    expect(
      shouldAutoConfigureAgent({
        validation_channel: 'ssh',
        host: '192.168.54.111',
        user: 'linaro',
      }, { password_configured: true }),
    ).toBe(true);
    expect(
      shouldAutoConfigureAgent({
        validation_channel: 'board_agent',
        host: '',
        user: '',
        password: '',
      }),
    ).toBe(false);
    expect(
      shouldAutoConfigureAgent({
        validation_channel: 'ssh',
        host: '192.168.54.111',
        user: 'linaro',
      }),
    ).toBe(false);
  });
});
