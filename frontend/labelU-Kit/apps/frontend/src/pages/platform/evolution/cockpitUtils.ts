import type { EvolutionNode, EvolutionStatus, StageStatus, TrainingMetrics } from '@/api/services/evolution';

export const STATUS_LABELS: Record<string, string> = {
  RUNNING: '进化中',
  PROCESSING: '训练中',
  PENDING: '排队中',
  QUEUED: '排队中',
  COMPLETED: '已完成',
  ATTENTION: '待介入',
  IDLE: '待命',
  FAILED: '失败',
  FAIL: '已阻断',
  ERROR: '错误',
  PASSED: '已通过',
  PASS: '已通过',
  BLOCKED: '已阻断',
  WAITING: '等待中',
  NOT_STARTED: '未开始',
  SKIPPED: '已跳过',
};

export const GATE_LABELS: Record<string, string> = {
  training: '训练质量',
  generalization: '泛化质量',
  conversion: '转换完整性',
  edge: '真机可用性',
  release: '发布准入',
};

export function statusLabel(status?: string | null) {
  const code = String(status || 'IDLE').toUpperCase();
  return STATUS_LABELS[code] || code;
}

export function statusTone(status?: string | null): 'active' | 'success' | 'danger' | 'warning' | 'idle' {
  const code = String(status || '').toUpperCase();
  if (['RUNNING', 'PROCESSING', 'PENDING', 'QUEUED'].includes(code)) return 'active';
  if (['COMPLETED', 'PASS', 'PASSED', 'PUBLISHED', 'CHAMPION'].includes(code)) return 'success';
  if (['FAIL', 'FAILED', 'BLOCKED', 'ERROR'].includes(code)) return 'danger';
  if (['ATTENTION', 'WAITING', 'WAITING_FOR_DATA', 'WAITING_FOR_REVIEW', 'POLICY_REVIEW_REQUIRED'].includes(code)) return 'warning';
  return 'idle';
}

export function isPassedStatus(status?: string | null) {
  return ['PASS', 'PASSED', 'PUBLISHED', 'COMPLETED', 'CHAMPION'].includes(String(status || '').toUpperCase());
}

export function formatMetric(value?: number | null, percent = true) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return percent ? `${(value * 100).toFixed(1)}%` : value.toFixed(3);
}

export function formatTime(value?: string | null) {
  if (!value) return '时间未记录';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '时间未记录' : date.toLocaleString('zh-CN', { hour12: false });
}

export function automationCopy(automation?: { status: string; training_enabled: boolean; plan_writes_enabled: boolean; auto_iteration_enabled: boolean }) {
  if (!automation || automation.status === 'DISABLED') {
    return { label: '自动进化未开启', description: '当前仅呈现真实训练与进化记录，不会自动创建训练任务。' };
  }
  if (automation.status === 'PARTIAL') {
    return { label: '自动化部分启用', description: '自动执行范围受平台策略控制，未启用的环节保持只读。' };
  }
  return { label: '自主进化策略运行中', description: '数据、训练、评估与发布按平台质量策略自动衔接。' };
}

export function nodeProgress(node: EvolutionNode) {
  const percent = node.progress?.percent;
  if (typeof percent === 'number' && Number.isFinite(percent)) return Math.max(0, Math.min(100, percent > 1 ? percent : percent * 100));
  const current = node.progress?.current_epoch;
  const target = node.progress?.target_epochs;
  if (typeof current === 'number' && typeof target === 'number' && target > 0) return Math.max(0, Math.min(100, current / target * 100));
  return node.status === 'COMPLETED' ? 100 : 0;
}

export function mergeMetricHistory(current: TrainingMetrics[], incoming: TrainingMetrics[]) {
  const rows = new Map<number, TrainingMetrics>();
  [...current, ...incoming].forEach((item, index) => {
    const epoch = typeof item.epoch === 'number' ? item.epoch : index;
    rows.set(epoch, { ...rows.get(epoch), ...item, epoch });
  });
  return [...rows.values()].sort((a, b) => Number(a.epoch || 0) - Number(b.epoch || 0));
}

export function metricHistoryAvailable(
  mode: 'live' | 'snapshot',
  historyLength: number,
  responseAvailable?: boolean,
) {
  return mode === 'snapshot' || historyLength > 0 || responseAvailable !== false;
}

export function pollingInterval(status?: EvolutionStatus | StageStatus, activeJobs = 0) {
  return activeJobs > 0 || status === 'RUNNING' ? 8_000 : 30_000;
}
