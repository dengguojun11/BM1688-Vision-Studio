export type EdgeDeviceReadinessSnapshot = {
  validation_channel?: string | null;
  board_agent_status?: string | null;
  board_agent_last_heartbeat_at?: string | null;
  last_probe_status?: string | null;
  probe_status?: string | null;
  last_probe_checked_at?: string | null;
  probe_checked_at?: string | null;
  scheduling_status?: string | null;
  available_for_dispatch?: boolean | null;
  is_busy?: boolean | null;
  agent_bootstrap_status?: string | null;
};

// Keep the client fallback aligned with the server default.  A canonical
// scheduling status is preferred; the timestamp protects older responses that
// only expose the raw Board Agent status.
export const BOARD_AGENT_HEARTBEAT_TTL_MS = 120_000;
export const SSH_PROBE_TTL_MS = 120_000;

export const ACTIVE_AGENT_BOOTSTRAP_STATUSES = new Set([
  'QUEUED',
  'PREFLIGHT',
  'RUNTIME_PROVISION',
  'UPLOADING',
  'INSTALLING',
  'STARTING',
  'WAITING_HEARTBEAT',
]);

export function agentBootstrapInProgress(device: EdgeDeviceReadinessSnapshot) {
  return ACTIVE_AGENT_BOOTSTRAP_STATUSES.has(
    String(device.agent_bootstrap_status || '')
      .trim()
      .toUpperCase(),
  );
}

export function agentBootstrapFailed(device: EdgeDeviceReadinessSnapshot) {
  return (
    String(device.agent_bootstrap_status || '')
      .trim()
      .toUpperCase() === 'FAILED'
  );
}

function usesBoardAgent(device: EdgeDeviceReadinessSnapshot) {
  return (
    String(device.validation_channel || 'ssh')
      .trim()
      .toLowerCase() === 'board_agent'
  );
}

export function boardAgentHeartbeatFresh(
  device: EdgeDeviceReadinessSnapshot,
  now = Date.now(),
  ttlMs = BOARD_AGENT_HEARTBEAT_TTL_MS,
) {
  const raw = String(device.board_agent_last_heartbeat_at || '').trim();
  const timestamp = Date.parse(raw);
  if (!Number.isFinite(timestamp)) return false;
  const age = now - timestamp;
  return age >= 0 && age <= ttlMs;
}

export function sshProbeFresh(device: EdgeDeviceReadinessSnapshot, now = Date.now(), ttlMs = SSH_PROBE_TTL_MS) {
  const status = String(device.last_probe_status || device.probe_status || '')
    .trim()
    .toLowerCase();
  if (status !== 'ok') return false;
  const raw = String(device.last_probe_checked_at || device.probe_checked_at || '').trim();
  const timestamp = Date.parse(raw);
  if (!Number.isFinite(timestamp)) return false;
  const age = now - timestamp;
  return age >= 0 && age <= ttlMs;
}

export function deviceConnectionReady(device: EdgeDeviceReadinessSnapshot, now = Date.now()) {
  if (agentBootstrapInProgress(device)) return false;
  // A fresh heartbeat/probe and a stale ``available_for_dispatch`` projection
  // must not resurrect a device whose durable Agent enrollment failed.  An
  // absent/empty status remains valid for manually installed Board Agents.
  if (agentBootstrapFailed(device) && device.is_busy !== true) return false;
  // Board Agent liveness is proved only by its status plus a fresh heartbeat.
  // Scheduler availability is a dispatch projection and must not be used as
  // a substitute for the device's own heartbeat, including while idle.
  if (usesBoardAgent(device)) {
    return (
      String(device.board_agent_status || '')
        .trim()
        .toLowerCase() === 'online' && boardAgentHeartbeatFresh(device, now)
    );
  }

  // SSH health is also time-bounded.  Dispatch availability is a derived
  // scheduler projection and cannot replace a recent successful probe.
  return sshProbeFresh(device, now);
}
