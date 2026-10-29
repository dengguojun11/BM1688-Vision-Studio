import { describe, expect, it } from 'vitest';

import { boardAgentHeartbeatFresh, deviceConnectionReady, sshProbeFresh } from './edgeDeviceReadiness';

const NOW = Date.parse('2026-10-25T12:00:00.000Z');

describe('edge device readiness', () => {
  it('requires a fresh heartbeat when only the raw Board Agent status is available', () => {
    const device = {
      validation_channel: 'board_agent',
      board_agent_status: 'online',
      board_agent_last_heartbeat_at: '2026-10-25T11:59:00.000Z',
    };
    expect(boardAgentHeartbeatFresh(device, NOW)).toBe(true);
    expect(deviceConnectionReady(device, NOW)).toBe(true);
    expect(deviceConnectionReady({ ...device, board_agent_last_heartbeat_at: '2026-10-25T11:56:00.000Z' }, NOW)).toBe(
      false,
    );
  });

  it('does not trust an idle server projection over a stale Board Agent heartbeat', () => {
    const stale = {
      validation_channel: 'board_agent',
      board_agent_status: 'offline',
      board_agent_last_heartbeat_at: '2026-10-25T11:50:00.000Z',
    };
    expect(deviceConnectionReady({ ...stale, available_for_dispatch: true }, NOW)).toBe(false);
    expect(deviceConnectionReady({ ...stale, scheduling_status: 'idle' }, NOW)).toBe(false);
  });

  it('uses a fresh Board Agent heartbeat even when the scheduler is idle', () => {
    const idle = {
      validation_channel: 'board_agent',
      board_agent_status: 'online',
      board_agent_last_heartbeat_at: '2026-10-25T11:59:00.000Z',
      scheduling_status: 'idle',
    };
    expect(deviceConnectionReady(idle, NOW)).toBe(true);
  });

  it('does not treat a busy device as connected when its heartbeat is stale', () => {
    const busy = {
      validation_channel: 'board_agent',
      board_agent_status: 'offline',
      board_agent_last_heartbeat_at: '2026-10-25T11:50:00.000Z',
      scheduling_status: 'busy',
      is_busy: true,
    };

    expect(deviceConnectionReady(busy, NOW)).toBe(false);
    expect(
      deviceConnectionReady(
        { ...busy, board_agent_status: 'online', board_agent_last_heartbeat_at: '2026-10-25T11:59:00.000Z' },
        NOW,
      ),
    ).toBe(true);
  });

  it('does not treat a busy SSH device as connected when its probe is stale', () => {
    const busy = {
      validation_channel: 'ssh',
      scheduling_status: 'busy',
      is_busy: true,
      last_probe_status: 'ok',
      last_probe_checked_at: '2026-10-25T11:50:00.000Z',
    };

    expect(deviceConnectionReady(busy, NOW)).toBe(false);
    expect(deviceConnectionReady({ ...busy, last_probe_checked_at: '2026-10-25T11:59:00.000Z' }, NOW)).toBe(true);
  });

  it('fails closed when Agent bootstrap failed despite a fresh Board Agent heartbeat', () => {
    const failed = {
      validation_channel: 'board_agent',
      board_agent_status: 'online',
      board_agent_last_heartbeat_at: '2026-10-25T11:59:00.000Z',
      agent_bootstrap_status: 'FAILED',
      available_for_dispatch: true,
    };
    expect(deviceConnectionReady(failed, NOW)).toBe(false);
  });

  it('requires a fresh SSH probe when only raw probe fields are available', () => {
    const device = {
      validation_channel: 'ssh',
      last_probe_status: 'ok',
      last_probe_checked_at: '2026-10-25T11:59:00.000Z',
    };
    expect(sshProbeFresh(device, NOW)).toBe(true);
    expect(deviceConnectionReady(device, NOW)).toBe(true);
    const stale = { ...device, last_probe_checked_at: '2026-10-25T11:56:00.000Z' };
    expect(sshProbeFresh(stale, NOW)).toBe(false);
    expect(deviceConnectionReady(stale, NOW)).toBe(false);
  });

  it('does not trust an idle SSH readiness projection over a stale probe', () => {
    const stale = {
      validation_channel: 'ssh',
      last_probe_status: 'ok',
      last_probe_checked_at: '2026-10-25T11:50:00.000Z',
      available_for_dispatch: true,
      scheduling_status: 'idle',
    };
    expect(deviceConnectionReady(stale, NOW)).toBe(false);
  });

  it('rejects a future SSH probe timestamp', () => {
    const device = {
      validation_channel: 'ssh',
      probe_status: 'ok',
      probe_checked_at: '2026-10-25T12:01:00.000Z',
    };
    expect(sshProbeFresh(device, NOW)).toBe(false);
    expect(deviceConnectionReady(device, NOW)).toBe(false);
  });
});
