import {
  ArrowLeftOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  ClusterOutlined,
  CloseCircleOutlined,
  EyeOutlined,
  ReloadOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Checkbox,
  Descriptions,
  Drawer,
  Empty,
  message,
  Popconfirm,
  Progress,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Timeline,
  Tooltip,
  Typography,
} from 'antd';
import type { ColumnsType, TablePaginationConfig } from 'antd/es/table';
import styled from 'styled-components';
import { useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import request from '@/api/request';
import ServerSearchInput from '@/components/ServerSearchInput';
import { listData, listTotal, responseStatus } from '../shared';
import { usePageVisible } from '../polling';

export const EDGE_VALIDATION_JOBS_PATH = '/v1/yolo-edge-validation/jobs';
export const EDGE_VALIDATION_QUEUE_JOBS_PATH = '/v1/yolo-edge-validation/queue/jobs';
export const EDGE_VALIDATION_QUEUE_SUMMARY_PATH = '/v1/yolo-edge-validation/queue/summary';
export const EDGE_VALIDATION_QUEUE_SUMMARY_COMPAT_PATH = `${EDGE_VALIDATION_JOBS_PATH}/queue-summary`;

export const ACTIVE_EDGE_JOB_STATUSES = new Set([
  'BLOCKED',
  'PENDING',
  'QUEUED',
  'PROCESSING',
  'RUNNING',
  'RETRYING',
  'PREPARING',
]);

// PREPARING and RETRYING are operator-facing projections of a persisted
// PENDING row.  The backend cancellation contract therefore accepts them too.
export const CANCELLABLE_EDGE_JOB_STATUSES = new Set(['BLOCKED', 'PENDING', 'QUEUED', 'PREPARING', 'RETRYING']);
export const RETRYABLE_EDGE_JOB_STATUSES = new Set(['FAILED', 'CANCELLED', 'PARTIAL_COMPLETED']);
const TERMINAL_EDGE_JOB_STATUSES = new Set(['COMPLETED', 'PARTIAL_COMPLETED', 'FAILED', 'CANCELLED']);

export type EdgeValidationJob = {
  id: number;
  export_id?: number;
  task_id?: number;
  task_name?: string | null;
  source_type?: string | null;
  source_system?: string | null;
  pipeline_origin?: string | null;
  status?: string | null;
  /** Persisted lifecycle status plus the queue center's derived operator state. */
  display_status?: string | null;
  mode?: string | null;
  execution_profile?: string | null;
  image_count?: number | null;
  labeled_image_count?: number | null;
  unlabeled_image_count?: number | null;
  edge_device_id?: string | null;
  edge_device_name?: string | null;
  edge_host?: string | null;
  queue_reason?: string | null;
  queue_position?: number | null;
  pack_status?: string | null;
  queued_at?: string | null;
  assigned_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  waiting_seconds?: number | null;
  running_seconds?: number | null;
  estimated_seconds?: number | null;
  created_at?: string | null;
  updated_at?: string | null;
  report_summary?: string | null;
  error_message?: string | null;
  progress?: {
    stage?: string | null;
    message?: string | null;
    percent?: number | null;
    detail?: Record<string, unknown> | null;
    updated_at?: string | null;
    history?: Array<{ stage?: string | null; message?: string | null; at?: string | null }>;
  } | null;
};

export type EdgeQueueDeviceSummary = {
  device_id: string;
  name?: string | null;
  status?: string | null;
  is_online?: boolean;
  is_ready?: boolean;
  is_busy?: boolean;
  current_job_id?: number | null;
  current_job_status?: string | null;
  current_job_started_at?: string | null;
  next_job_id?: number | null;
  next_queue_position?: number | null;
  pending_count?: number | null;
  eta_seconds?: number | null;
  last_heartbeat_at?: string | null;
};

export type EdgeQueueSummaryCounts = {
  preparing?: number;
  queued?: number;
  pending?: number;
  processing?: number;
  running?: number;
  retrying?: number;
  blocked?: number;
  failed?: number;
  completed?: number;
  cancelled?: number;
  active?: number;
  total?: number;
};

export type EdgeQueueSummary = {
  counts: EdgeQueueSummaryCounts;
  oldest_wait_seconds?: number | null;
  oldest_queued_at?: string | null;
  estimated_clear_seconds?: number | null;
  eta_confidence?: string | null;
  eta_basis?: string | null;
  completed_duration_samples?: number | null;
  generated_at?: string | null;
  devices: EdgeQueueDeviceSummary[];
};

const Page = styled.div`
  min-width: 0;
`;

const Header = styled.div`
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 20px;

  @media (max-width: 720px) {
    flex-direction: column;
    gap: 12px;
  }

  h1 {
    margin: 0 0 5px;
    color: #142033;
    font-size: 24px;
    line-height: 1.3;
    font-weight: 650;
  }

  p {
    margin: 0;
    color: #64748b;
    font-size: 13px;
    line-height: 1.55;
  }
`;

const SummaryGrid = styled.div`
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 20px;

  @media (max-width: 1180px) {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  @media (max-width: 600px) {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
`;

const SummaryCard = styled.div`
  min-height: 88px;
  padding: 14px 15px;
  border: 1px solid #e3e8f0;
  border-radius: 6px;
  background: #fff;

  .label {
    display: block;
    margin-bottom: 8px;
    color: #64748b;
    font-size: 12px;
  }

  .value {
    color: #172033;
    font-size: 21px;
    line-height: 1.15;
    font-weight: 650;
  }

  .sub {
    display: block;
    margin-top: 5px;
    overflow: hidden;
    color: #94a3b8;
    font-size: 11px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
`;

const Panel = styled.section`
  margin-bottom: 20px;
  padding: 16px;
  border: 1px solid #e3e8f0;
  border-radius: 6px;
  background: #fff;
`;

const PanelHeading = styled.div`
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;

  h2 {
    margin: 0;
    color: #172033;
    font-size: 16px;
    line-height: 1.35;
    font-weight: 650;
  }

  p {
    margin: 3px 0 0;
    color: #64748b;
    font-size: 12px;
    line-height: 1.5;
  }
`;

const DeviceGrid = styled.div`
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(265px, 1fr));
  gap: 10px;
`;

const DeviceItem = styled.div`
  min-width: 0;
  padding: 12px;
  border: 1px solid #e8edf4;
  border-radius: 6px;
  background: #f8fafc;

  .device-title {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    min-width: 0;
    margin-bottom: 8px;
  }

  .device-name {
    overflow: hidden;
    color: #172033;
    font-size: 13px;
    font-weight: 600;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .device-id {
    margin-top: 2px;
    overflow: hidden;
    color: #94a3b8;
    font-size: 11px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .device-line {
    display: flex;
    justify-content: space-between;
    gap: 10px;
    margin-top: 6px;
    color: #64748b;
    font-size: 12px;

    strong {
      min-width: 0;
      overflow: hidden;
      color: #334155;
      font-weight: 550;
      text-align: right;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }
`;

const FilterBar = styled.div`
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 10px 12px;
  border: 1px solid #e8edf4;
  border-radius: 6px;
  background: #f8fafc;

  .search {
    min-width: 220px;
    flex: 1 1 260px;
  }

  @media (max-width: 600px) {
    .search {
      min-width: 100%;
      flex-basis: 100%;
    }
  }
`;

const QueueCell = styled.div`
  min-width: 130px;

  .position {
    color: #172033;
    font-size: 12px;
    font-weight: 600;
  }

  .reason {
    display: block;
    margin-top: 3px;
    color: #64748b;
    font-size: 11px;
    line-height: 1.35;
  }
`;

const ProgressCell = styled.div`
  min-width: 150px;

  .stage {
    display: block;
    margin-bottom: 4px;
    overflow: hidden;
    color: #334155;
    font-size: 12px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .message {
    display: block;
    margin-top: 3px;
    overflow: hidden;
    color: #94a3b8;
    font-size: 11px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
`;

const STATUS_LABELS: Record<string, string> = {
  BLOCKED: '已阻塞',
  PREPARING: '准备中',
  PENDING: '等待调度',
  QUEUED: '排队中',
  PROCESSING: '执行中',
  RUNNING: '执行中',
  RETRYING: '等待重试',
  COMPLETED: '已完成',
  PARTIAL_COMPLETED: '部分完成',
  FAILED: '失败',
  CANCELLED: '已取消',
};

const STATUS_COLORS: Record<string, string> = {
  BLOCKED: 'orange',
  COMPLETED: 'green',
  PARTIAL_COMPLETED: 'gold',
  FAILED: 'red',
  CANCELLED: 'default',
};

const SOURCE_LABELS: Record<string, string> = {
  internal: '手动验证',
  manual: '手动验证',
  auto_training: '自动训练',
  labelu_auto_training: '自动训练',
  auto_quality: '自动质量门禁',
  manual_diagnostic: '手动诊断',
  evolution: '进化流程',
  strategy_evolution: '进化流程',
  external: '外部任务',
  third_party: '第三方任务',
};

const SOURCE_FILTER_PARAMS: Record<
  string,
  { field: 'source_type' | 'pipeline_origin' | 'source_system'; value: string }
> = {
  auto_quality: { field: 'pipeline_origin', value: 'AUTO_QUALITY' },
  manual_diagnostic: { field: 'pipeline_origin', value: 'MANUAL_DIAGNOSTIC' },
  evolution: { field: 'source_system', value: 'strategy_evolution' },
  strategy_evolution: { field: 'source_system', value: 'strategy_evolution' },
};

const QUEUE_REASON_LABELS: Record<string, string> = {
  waiting_for_dispatch: '等待设备调度',
  waiting_for_idle_device: '在线设备均忙，等待空闲设备',
  waiting_for_online_device: '等待在线设备',
  waiting_for_environment_ready: '等待设备环境就绪',
  waiting_for_agent: '等待边端 Agent 上线',
  waiting_for_pack: '等待检查数据包',
  waiting_for_device: '等待可用设备',
  device_busy: '设备忙',
  no_online_device: '没有在线设备',
  environment_not_ready: '环境未就绪',
  agent_bootstrap_in_progress: 'Agent 引导进行中',
  agent_offline: 'Agent 未上线',
  preparing_pack: '检查数据准备中',
  packing: '检查数据打包中',
  pack_preparing: '检查数据准备中',
  pack_not_ready: '检查数据包尚未就绪',
  pack_failed: '检查数据准备失败',
  retry_scheduled: '等待重试调度',
  retrying: '等待重试',
  cancelled: '任务已取消',
  retry_backoff: '等待重试时间',
  execution_fencing_unavailable: '执行保护未就绪',
};

const BLOCKED_QUEUE_REASONS = new Set([
  'waiting_for_idle_device',
  'waiting_for_online_device',
  'waiting_for_environment_ready',
  'waiting_for_agent',
  'waiting_for_pack',
  'environment_not_ready',
  'agent_bootstrap_in_progress',
  'execution_fencing_unavailable',
]);
const PREPARING_QUEUE_REASONS = new Set(['preparing_pack', 'packing', 'pack_preparing', 'pack_not_ready']);
const RETRY_QUEUE_REASONS = new Set(['retry_scheduled', 'retrying']);

function asRecord(value: unknown): Record<string, any> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, any>) : {};
}

function numeric(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const result = Number(value);
  return Number.isFinite(result) ? result : null;
}

export function edgeJobStatusLabel(value: unknown) {
  const status = String(value || '')
    .trim()
    .toUpperCase();
  return STATUS_LABELS[status] || (status ? status : '状态待确认');
}

/**
 * Normalize raw detail responses to the same operator-facing state as queue
 * rows.  New responses provide ``display_status``; the fallback keeps older
 * servers and cached responses consistent while they roll forward.
 */
export function edgeJobDisplayStatus(
  value: Pick<EdgeValidationJob, 'status' | 'display_status' | 'queue_reason' | 'progress'> | null | undefined,
) {
  const explicit = String(value?.display_status || '')
    .trim()
    .toUpperCase();
  if (explicit) return explicit;
  const raw = String(value?.status || '')
    .trim()
    .toUpperCase();
  const reason = String(value?.queue_reason || '')
    .trim()
    .toLowerCase();
  const stage = String(value?.progress?.stage || '')
    .trim()
    .toLowerCase();
  if (BLOCKED_QUEUE_REASONS.has(reason)) return 'BLOCKED';
  if (PREPARING_QUEUE_REASONS.has(reason) || ['pack', 'packing', 'prepare_pack', 'preparing'].includes(stage)) {
    return 'PREPARING';
  }
  if (RETRY_QUEUE_REASONS.has(reason)) return 'RETRYING';
  return raw === 'PROCESSING' ? 'RUNNING' : raw;
}

export function edgeJobStatusColor(value: unknown) {
  const status = String(value || '')
    .trim()
    .toUpperCase();
  return STATUS_COLORS[status] || (ACTIVE_EDGE_JOB_STATUSES.has(status) ? 'blue' : 'default');
}

export function edgeQueueReasonLabel(value: unknown) {
  const reason = String(value || '').trim();
  return QUEUE_REASON_LABELS[reason] || reason || '未记录';
}

export function edgeSourceLabel(value: unknown) {
  const source = String(value || '')
    .trim()
    .toLowerCase();
  return SOURCE_LABELS[source] || (source ? source : '平台任务');
}

export function edgeJobSourceValue(
  value: Pick<EdgeValidationJob, 'pipeline_origin' | 'source_system' | 'source_type'> | null | undefined,
) {
  return value?.pipeline_origin || value?.source_system || value?.source_type;
}

export function isActiveEdgeJob(
  value: Pick<EdgeValidationJob, 'status' | 'display_status' | 'queue_reason' | 'progress'> | null | undefined,
) {
  return ACTIVE_EDGE_JOB_STATUSES.has(edgeJobDisplayStatus(value));
}

export function canCancelEdgeJob(
  value: Pick<EdgeValidationJob, 'status' | 'display_status' | 'queue_reason' | 'progress'> | null | undefined,
) {
  return CANCELLABLE_EDGE_JOB_STATUSES.has(edgeJobDisplayStatus(value));
}

export function canRetryEdgeJob(value: Pick<EdgeValidationJob, 'status'> | null | undefined) {
  return RETRYABLE_EDGE_JOB_STATUSES.has(String(value?.status || '').toUpperCase());
}

export function formatDuration(seconds: unknown, empty = '暂无估算') {
  const total = numeric(seconds);
  if (total === null || total < 0) return empty;
  const rounded = Math.max(0, Math.round(total));
  if (rounded < 60) return `${rounded} 秒`;
  const minutes = Math.round(rounded / 60);
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} 小时 ${rest} 分钟` : `${hours} 小时`;
}

export function edgeJobTiming(
  job: Pick<
    EdgeValidationJob,
    | 'status'
    | 'waiting_seconds'
    | 'running_seconds'
    | 'estimated_seconds'
    | 'queued_at'
    | 'created_at'
    | 'started_at'
    | 'completed_at'
  >,
  now = Date.now(),
) {
  const terminal = TERMINAL_EDGE_JOB_STATUSES.has(
    String(job.status || '')
      .trim()
      .toUpperCase(),
  );
  const timestampMs = (value: string | null | undefined) => {
    if (!value) return null;
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : null;
  };
  const elapsedSeconds = (start: string | null | undefined, end: string | null | undefined) => {
    const startMs = timestampMs(start);
    const endMs = timestampMs(end) ?? now;
    if (startMs === null || !Number.isFinite(endMs)) return null;
    return Math.max(0, Math.floor((endMs - startMs) / 1000));
  };
  const waitingSeconds =
    numeric(job.waiting_seconds) ?? elapsedSeconds(job.queued_at || job.created_at, job.started_at || job.completed_at);
  const runningSeconds = numeric(job.running_seconds) ?? elapsedSeconds(job.started_at, job.completed_at);
  return {
    waiting: formatDuration(waitingSeconds, '未记录'),
    running: formatDuration(runningSeconds, terminal ? '未执行' : '尚未开始'),
    remaining: terminal ? '已结束' : formatDuration(job.estimated_seconds, '暂无估算'),
  };
}

export function queueDevicePoolSummary(devices: EdgeQueueDeviceSummary[]) {
  const online = devices.filter((device) => device.is_online !== false).length;
  const ready = devices.filter((device) => device.is_online !== false && device.is_ready !== false).length;
  return {
    ready,
    online,
    offline: Math.max(0, devices.length - online),
  };
}

export function formatDate(value: unknown, empty = '未记录') {
  if (!value) return empty;
  const date = new Date(String(value));
  return Number.isNaN(date.getTime())
    ? String(value).replace('T', ' ')
    : date.toLocaleString('zh-CN', { hour12: false });
}

export type EdgeTaskContext = {
  exportId?: number;
  taskId?: number;
};

function positiveInteger(value: unknown): number | undefined {
  const normalized = String(value ?? '').trim();
  if (!/^\d+$/.test(normalized)) return undefined;
  const parsed = Number(normalized);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : undefined;
}

export function edgeTaskContextFromSearchParams(searchParams: URLSearchParams): EdgeTaskContext {
  return {
    exportId: positiveInteger(searchParams.get('export_id')),
    taskId: positiveInteger(searchParams.get('task_id')),
  };
}

export function edgeTaskContextParams({ exportId, taskId }: EdgeTaskContext) {
  const params: Record<string, number> = {};
  const normalizedExportId = positiveInteger(exportId);
  const normalizedTaskId = positiveInteger(taskId);
  if (normalizedExportId !== undefined) params.export_id = normalizedExportId;
  if (normalizedTaskId !== undefined) params.task_id = normalizedTaskId;
  return params;
}

export function edgeTaskContextLabel({ exportId, taskId }: EdgeTaskContext) {
  const labels: string[] = [];
  if (exportId !== undefined) labels.push(`边端模型导出 #${exportId}`);
  if (taskId !== undefined) labels.push(`所属任务 #${taskId}`);
  return labels.join(' · ');
}

export function buildEdgeJobQueryParams({
  page,
  size,
  status,
  deviceId,
  sourceType,
  keyword,
  q,
  activeOnly = false,
  exportId,
  taskId,
}: {
  page: number;
  size: number;
  status?: string;
  deviceId?: string;
  sourceType?: string;
  keyword?: string;
  /** Canonical server-side search parameter; keyword remains for legacy callers. */
  q?: string;
  activeOnly?: boolean;
  exportId?: number;
  taskId?: number;
}) {
  const params: Record<string, string | number> = { page, size, ...edgeTaskContextParams({ exportId, taskId }) };
  if (activeOnly) params.active_only = 'true';
  if (status && status !== 'all') params.status = status;
  if (deviceId && deviceId !== 'all') params.device_id = deviceId;
  const normalizedSourceType = sourceType?.trim().toLowerCase();
  if (normalizedSourceType && normalizedSourceType !== 'all') {
    const sourceFilter = SOURCE_FILTER_PARAMS[normalizedSourceType];
    if (sourceFilter) {
      params[sourceFilter.field] = sourceFilter.value;
    } else {
      params.source_type = normalizedSourceType;
    }
  }
  const normalizedKeyword = (q ?? keyword)?.trim();
  if (normalizedKeyword) params.q = normalizedKeyword;
  return params;
}

export function normalizeQueueSummary(value: unknown): EdgeQueueSummary {
  let candidate = asRecord(value);
  if (asRecord(candidate.data).counts || Array.isArray(candidate.data?.devices)) candidate = asRecord(candidate.data);
  const counts = asRecord(candidate.counts);
  const devices = Array.isArray(candidate.devices) ? candidate.devices : [];
  return {
    counts: {
      preparing: numeric(counts.preparing) ?? undefined,
      queued: numeric(counts.queued) ?? undefined,
      pending: numeric(counts.pending) ?? undefined,
      processing: numeric(counts.processing) ?? undefined,
      running: numeric(counts.running) ?? undefined,
      retrying: numeric(counts.retrying) ?? undefined,
      blocked: numeric(counts.blocked) ?? undefined,
      failed: numeric(counts.failed) ?? undefined,
      completed: numeric(counts.completed) ?? undefined,
      cancelled: numeric(counts.cancelled) ?? undefined,
      active: numeric(counts.active) ?? undefined,
      total: numeric(counts.total) ?? undefined,
    },
    oldest_wait_seconds: numeric(candidate.oldest_wait_seconds),
    oldest_queued_at: candidate.oldest_queued_at ? String(candidate.oldest_queued_at) : null,
    estimated_clear_seconds: numeric(candidate.estimated_clear_seconds),
    eta_confidence: candidate.eta_confidence ? String(candidate.eta_confidence) : null,
    eta_basis: candidate.eta_basis ? String(candidate.eta_basis) : null,
    completed_duration_samples: numeric(candidate.completed_duration_samples),
    generated_at: candidate.generated_at ? String(candidate.generated_at) : null,
    devices: devices
      .map((item) => {
        const row = asRecord(item);
        return {
          device_id: String(row.device_id || row.id || ''),
          name: row.name || row.device_name ? String(row.name || row.device_name) : null,
          status: row.status ? String(row.status) : null,
          is_online: row.is_online === undefined ? undefined : Boolean(row.is_online),
          is_ready: row.is_ready === undefined ? undefined : Boolean(row.is_ready),
          is_busy: row.is_busy === undefined ? undefined : Boolean(row.is_busy),
          current_job_id: numeric(row.current_job_id),
          current_job_status: row.current_job_status ? String(row.current_job_status) : null,
          current_job_started_at: row.current_job_started_at ? String(row.current_job_started_at) : null,
          next_job_id: numeric(row.next_job_id),
          next_queue_position: numeric(row.next_queue_position),
          pending_count: numeric(row.pending_count),
          eta_seconds: numeric(row.eta_seconds),
          last_heartbeat_at: row.last_heartbeat_at ? String(row.last_heartbeat_at) : null,
        };
      })
      .filter((row) => row.device_id),
  };
}

export function queueEtaBasisLabel(
  summary: Pick<EdgeQueueSummary, 'eta_confidence' | 'eta_basis' | 'completed_duration_samples'>,
) {
  const parts: string[] = [];
  if (summary.eta_confidence) parts.push(`估算可信度：${summary.eta_confidence}`);
  if (summary.eta_basis) parts.push(`依据：${summary.eta_basis}`);
  const samples = numeric(summary.completed_duration_samples);
  if (samples != null && samples > 0) parts.push(`历史样本：${Math.round(samples)} 条`);
  return parts.join(' · ') || '暂无同类历史任务';
}

/**
 * Polling follows the queue-wide activity snapshot, not the currently
 * visible page. A terminal-only page is common once the queue grows beyond
 * one page, and must still update while another page has active work.
 */
export function hasActiveEdgeQueueSummary(value: EdgeQueueSummary | unknown) {
  const summary =
    value && typeof value === 'object' && 'counts' in (value as Record<string, unknown>)
      ? (value as EdgeQueueSummary)
      : normalizeQueueSummary(value);
  const counts = summary.counts || {};
  return [
    counts.active,
    counts.queued,
    counts.pending,
    counts.processing,
    counts.running,
    counts.preparing,
    counts.retrying,
  ].some((count) => Number(count || 0) > 0);
}

export function edgeQueuePollingInterval(pageVisible: boolean, summary: EdgeQueueSummary | unknown) {
  return pageVisible && hasActiveEdgeQueueSummary(summary) ? 5_000 : false;
}

export function normalizeQueueJobs(value: unknown) {
  const raw = asRecord(value);
  const nested = asRecord(raw.data);
  const queuePayload = Array.isArray(nested.items) ? nested : Array.isArray(raw.items) ? raw : null;
  if (queuePayload) {
    const items = queuePayload.items as EdgeValidationJob[];
    return { items, total: numeric(queuePayload.total) ?? items.length };
  }
  const items = listData(value) as EdgeValidationJob[];
  return { items, total: listTotal(value, items.length) };
}

function responsePayload(value: unknown) {
  const record = asRecord(value);
  return record.data && typeof record.data === 'object' && !Array.isArray(record.data) ? record.data : value;
}

export function queueMutationError(error: unknown, action: 'cancel' | 'retry') {
  if (responseStatus(error) === 409) return '任务状态已变化，队列已刷新，请确认后重试';
  const response = asRecord((error as { response?: unknown } | null)?.response);
  const payload = asRecord(response.data);
  const backendMessage = String(payload.msg || payload.message || payload.detail || '').trim();
  if (backendMessage) return backendMessage;
  if (responseStatus(error) === 403) return '当前账号无权操作该边端任务';
  return action === 'cancel' ? '取消边端任务失败，请稍后重试' : '重试边端任务失败，请稍后重试';
}

function progressPercent(job: EdgeValidationJob) {
  const detail = asRecord(job.progress?.detail);
  const value = numeric(job.progress?.percent ?? detail.percent ?? detail.progress);
  if (value === null) return null;
  return Math.max(0, Math.min(100, value <= 1 ? value * 100 : value));
}

function progressStage(job: EdgeValidationJob) {
  const stage = String(job.progress?.stage || '').trim();
  const labels: Record<string, string> = {
    prepare: '准备检查任务',
    packaging: '准备检查数据',
    pack: '准备检查数据',
    queued: '等待设备',
    ssh_connect: '连接边端设备',
    environment_probe: '检查运行环境',
    environment_provision: '准备运行环境',
    uploading: '上传模型和图片',
    upload: '上传模型和图片',
    remote_run: '执行边端验证',
    running: '执行模型推理',
    inference: '执行模型推理',
    downloading: '回传检查结果',
    download: '回传检查结果',
    evaluating: '计算精度结果',
    evaluation: '计算精度结果',
    finalize: '整理检查结论',
    completed: '检查完成',
    failed: '检查失败',
  };
  return labels[stage.toLowerCase()] || stage || edgeJobStatusLabel(job.status);
}

function countRows(rows: EdgeValidationJob[], statusValues: string[]) {
  const wanted = new Set(statusValues);
  return rows.filter((row) => wanted.has(String(row.status || '').toUpperCase())).length;
}

function deviceStatus(device: EdgeQueueDeviceSummary) {
  if (device.is_busy) return { label: '执行中', color: 'blue' };
  if (device.is_online === false) return { label: '离线', color: 'red' };
  if (device.is_ready === false) return { label: '未就绪', color: 'gold' };
  if (device.is_ready || device.status === 'ready' || device.status === 'online')
    return { label: '空闲可调度', color: 'green' };
  return { label: device.status || '状态待确认', color: 'default' };
}

function QueueSummaryCards({
  summary,
  rows,
  total,
}: {
  summary: EdgeQueueSummary;
  rows: EdgeValidationJob[];
  total: number;
}) {
  const counts = summary.counts;
  const queuedBase = counts.queued ?? counts.pending ?? countRows(rows, ['PENDING', 'QUEUED']);
  const queued = queuedBase + (counts.preparing || 0) + (counts.retrying || 0);
  const processing = counts.processing ?? counts.running ?? countRows(rows, ['PROCESSING', 'RUNNING']);
  const blocked = counts.blocked ?? 0;
  const failed = counts.failed ?? countRows(rows, ['FAILED']);
  // `queued` already includes preparing/retrying above; do not count those
  // buckets twice when an older snapshot omits the server-provided active total.
  const active = counts.active ?? queued + processing;
  const devicePool = queueDevicePoolSummary(summary.devices);
  return (
    <SummaryGrid aria-label="边端队列摘要">
      <SummaryCard>
        <span className="label">待处理</span>
        <span className="value">{queued.toLocaleString()}</span>
        <span className="sub">
          {summary.oldest_wait_seconds == null
            ? '准备中与排队任务'
            : `最老任务已等 ${formatDuration(summary.oldest_wait_seconds)}`}
        </span>
      </SummaryCard>
      <SummaryCard>
        <span className="label">执行中</span>
        <span className="value">{processing.toLocaleString()}</span>
        <span className="sub">当前活动任务 {active.toLocaleString()}</span>
      </SummaryCard>
      <SummaryCard>
        <span className="label">阻塞</span>
        <span className="value">{blocked.toLocaleString()}</span>
        <span className="sub">需要设备或人工处理</span>
      </SummaryCard>
      <SummaryCard>
        <span className="label">失败</span>
        <span className="value">{failed.toLocaleString()}</span>
        <span className="sub">可在详情中查看原因</span>
      </SummaryCard>
      <SummaryCard>
        <span className="label">设备池</span>
        <span className="value">
          {devicePool.ready}/{devicePool.online}
        </span>
        <span className="sub">
          {summary.devices.length === 0
            ? '暂无已登记设备'
            : devicePool.offline > 0
            ? `${devicePool.offline} 台离线 · 就绪 / 在线设备`
            : '就绪 / 在线设备'}
        </span>
      </SummaryCard>
      <SummaryCard>
        <span className="label">预计清空</span>
        <span className="value">{formatDuration(summary.estimated_clear_seconds)}</span>
        <span className="sub">{queueEtaBasisLabel(summary)}</span>
      </SummaryCard>
    </SummaryGrid>
  );
}

function DeviceQueuePanel({ devices, loading }: { devices: EdgeQueueDeviceSummary[]; loading: boolean }) {
  return (
    <Panel aria-label="边端设备队列">
      <PanelHeading>
        <div>
          <h2>设备执行概览</h2>
          <p>每台设备只保留一个执行槽位，并展示当前任务与下一任务。</p>
        </div>
        <ClusterOutlined style={{ color: '#64748b', fontSize: 18 }} aria-hidden="true" />
      </PanelHeading>
      {loading && !devices.length ? (
        <div role="status" style={{ padding: '18px 0', textAlign: 'center' }}>
          <Spin size="small" />
        </div>
      ) : devices.length ? (
        <DeviceGrid>
          {devices.map((device) => {
            const state = deviceStatus(device);
            return (
              <DeviceItem key={device.device_id}>
                <div className="device-title">
                  <div style={{ minWidth: 0 }}>
                    <div className="device-name">{device.name || device.device_id}</div>
                    <div className="device-id">{device.device_id}</div>
                  </div>
                  <Tag color={state.color}>{state.label}</Tag>
                </div>
                <div className="device-line">
                  <span>当前任务</span>
                  <strong>
                    {device.current_job_id
                      ? `#${device.current_job_id} · ${edgeJobStatusLabel(device.current_job_status)}`
                      : '无'}
                  </strong>
                </div>
                <div className="device-line">
                  <span>下一任务</span>
                  <strong>
                    {device.next_job_id
                      ? `#${device.next_job_id} · 队列第 ${device.next_queue_position || '-'} 位`
                      : '无'}
                  </strong>
                </div>
                <div className="device-line">
                  <span>待处理</span>
                  <strong>{device.pending_count == null ? '-' : `${device.pending_count} 个`}</strong>
                </div>
                {device.eta_seconds != null && (
                  <div className="device-line">
                    <span>预计可用</span>
                    <strong>{formatDuration(device.eta_seconds, '现在')}</strong>
                  </div>
                )}
              </DeviceItem>
            );
          })}
        </DeviceGrid>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无已登记的边端设备" />
      )}
    </Panel>
  );
}

function JobDetailDrawer({ jobId, open, onClose }: { jobId: number | null; open: boolean; onClose: () => void }) {
  const query = useQuery<EdgeValidationJob>({
    queryKey: ['edge-task-center-job', jobId],
    queryFn: async () =>
      responsePayload(await request.get(`${EDGE_VALIDATION_JOBS_PATH}/${jobId}`)) as EdgeValidationJob,
    enabled: open && jobId != null,
    staleTime: 2_000,
    refetchInterval: (current) => (open && isActiveEdgeJob(current.state.data) ? 3_000 : false),
  });
  const job = query.data;
  const displayStatus = job ? edgeJobDisplayStatus(job) : '';
  const historyValue = job?.progress?.history;
  const history = Array.isArray(historyValue) ? historyValue : [];
  const displayedHistory = history.slice(-12);
  const timing = job ? edgeJobTiming(job) : null;
  return (
    <Drawer
      title={job ? `边端任务 #${job.id}` : '边端任务详情'}
      open={open}
      onClose={onClose}
      width={Math.min(620, typeof window === 'undefined' ? 620 : window.innerWidth - 24)}
      destroyOnClose
    >
      {query.isLoading ? (
        <div role="status" style={{ padding: 36, textAlign: 'center' }}>
          <Spin />
        </div>
      ) : query.error ? (
        <Alert
          type="error"
          showIcon
          message="任务详情加载失败"
          description={responseStatus(query.error) === 403 ? '当前账号无权查看该任务。' : '请刷新后重试。'}
        />
      ) : job ? (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="状态">
              <Tag color={edgeJobStatusColor(displayStatus)}>{edgeJobStatusLabel(displayStatus)}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="来源">{edgeSourceLabel(edgeJobSourceValue(job))}</Descriptions.Item>
            <Descriptions.Item label="模型任务">
              {job.task_name || (job.task_id ? `任务 #${job.task_id}` : '未记录')}
            </Descriptions.Item>
            <Descriptions.Item label="执行设备">
              {job.edge_device_name || job.edge_device_id || '尚未分配'}
            </Descriptions.Item>
            <Descriptions.Item label="排队信息">
              {job.queue_position ? `队列第 ${job.queue_position} 位` : edgeQueueReasonLabel(job.queue_reason)}
            </Descriptions.Item>
            <Descriptions.Item label="创建时间">{formatDate(job.created_at)}</Descriptions.Item>
            <Descriptions.Item label="开始时间">{formatDate(job.started_at)}</Descriptions.Item>
            <Descriptions.Item label="完成时间">{formatDate(job.completed_at)}</Descriptions.Item>
            <Descriptions.Item label="等待时长">{timing?.waiting || '未记录'}</Descriptions.Item>
            <Descriptions.Item label="执行时长">{timing?.running || '未记录'}</Descriptions.Item>
            <Descriptions.Item label="预计剩余">{timing?.remaining || '暂无估算'}</Descriptions.Item>
            <Descriptions.Item label="图片数量">
              {job.image_count == null ? '-' : `${job.image_count} 张（${job.labeled_image_count || 0} 张有标注）`}
            </Descriptions.Item>
          </Descriptions>
          <div>
            <Typography.Text strong>当前进度</Typography.Text>
            <Progress
              percent={progressPercent(job) ?? undefined}
              status={displayStatus === 'FAILED' ? 'exception' : undefined}
              format={() =>
                progressPercent(job) == null ? progressStage(job) : `${Math.round(progressPercent(job) as number)}%`
              }
            />
            <Typography.Text type="secondary">
              {job.progress?.message || job.report_summary || '等待边端上报进度。'}
            </Typography.Text>
          </div>
          {history.length > 0 && (
            <div>
              <Typography.Text strong>执行时间线</Typography.Text>
              <Timeline
                style={{ marginTop: 12 }}
                items={displayedHistory.map((item, index) => ({
                  key: `${item.at || ''}-${index}`,
                  color: index === displayedHistory.length - 1 ? 'blue' : 'gray',
                  children: (
                    <div>
                      <div style={{ color: '#334155', fontSize: 13 }}>
                        {progressStage({ ...job, progress: { stage: item.stage, message: item.message } })}
                      </div>
                      <div style={{ color: '#64748b', fontSize: 12 }}>
                        {item.message || '状态已更新'} · {formatDate(item.at)}
                      </div>
                    </div>
                  ),
                }))}
              />{' '}
            </div>
          )}
          {job.error_message && <Alert type="error" showIcon message="失败原因" description={job.error_message} />}
          {job.report_summary && !job.error_message && (
            <Alert type="info" showIcon message="任务摘要" description={job.report_summary} />
          )}
        </Space>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未找到任务" />
      )}
    </Drawer>
  );
}

export default function EdgeTaskCenter() {
  const pageVisible = usePageVisible();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const searchParamsKey = searchParams.toString();
  const context = useMemo(
    () => edgeTaskContextFromSearchParams(new URLSearchParams(searchParamsKey)),
    [searchParamsKey],
  );
  const contextParams = useMemo(() => edgeTaskContextParams(context), [context.exportId, context.taskId]);
  const hasContext = Object.keys(contextParams).length > 0;
  const contextTitle = context.exportId ? '模型关联的边端任务' : '任务关联的边端任务';
  const contextLabel = edgeTaskContextLabel(context);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(20);
  const [status, setStatus] = useState('all');
  const [deviceId, setDeviceId] = useState('all');
  const [sourceType, setSourceType] = useState('all');
  const [activeOnly, setActiveOnly] = useState(false);
  const [keywordInput, setKeywordInput] = useState('');
  const [keyword, setKeyword] = useState('');
  const [selectedJobId, setSelectedJobId] = useState<number | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);

  const params = useMemo(
    () =>
      buildEdgeJobQueryParams({
        page,
        size: pageSize,
        status,
        deviceId,
        sourceType,
        keyword,
        activeOnly,
        exportId: context.exportId,
        taskId: context.taskId,
      }),
    [activeOnly, context.exportId, context.taskId, deviceId, keyword, page, pageSize, sourceType, status],
  );
  const summaryQuery = useQuery<unknown>({
    queryKey: hasContext ? ['edge-task-center-summary', contextParams] : ['edge-task-center-summary'],
    queryFn: async () => {
      try {
        return (
          await (hasContext
            ? request.get(EDGE_VALIDATION_QUEUE_SUMMARY_PATH, { params: contextParams })
            : request.get(EDGE_VALIDATION_QUEUE_SUMMARY_PATH))
        ).data;
      } catch (error) {
        // Keep rolling upgrades compatible with a server that has the legacy
        // alias but not the canonical queue route yet.
        if (responseStatus(error) !== 404) throw error;
        return (
          await (hasContext
            ? request.get(EDGE_VALIDATION_QUEUE_SUMMARY_COMPAT_PATH, { params: contextParams })
            : request.get(EDGE_VALIDATION_QUEUE_SUMMARY_COMPAT_PATH))
        ).data;
      }
    },
    staleTime: 3_000,
    retry: 1,
    refetchOnWindowFocus: true,
    refetchInterval: (current) => edgeQueuePollingInterval(pageVisible, current.state.data),
  });
  const summary = useMemo(() => normalizeQueueSummary(responsePayload(summaryQuery.data)), [summaryQuery.data]);
  const jobsQuery = useQuery<unknown>({
    queryKey: ['edge-task-center-jobs', params],
    queryFn: async () => (await request.get(EDGE_VALIDATION_QUEUE_JOBS_PATH, { params })).data,
    staleTime: 3_000,
    refetchOnWindowFocus: true,
    placeholderData: (previous: unknown) => previous,
    // Use the queue-wide snapshot so a terminal-only page still refreshes
    // while active work is running on another page.
    refetchInterval: () => edgeQueuePollingInterval(pageVisible, summary),
  });
  const queueRows = useMemo(() => normalizeQueueJobs(jobsQuery.data), [jobsQuery.data]);
  const rows = queueRows.items;
  const total = queueRows.total;
  const devices = summary.devices;
  const deviceOptions = useMemo(
    () => [
      { value: 'all', label: '全部设备' },
      ...devices.map((device) => ({
        value: device.device_id,
        label: device.name ? `${device.name} (${device.device_id})` : device.device_id,
      })),
    ],
    [devices],
  );

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['edge-task-center-jobs'] });
    void queryClient.invalidateQueries({ queryKey: ['edge-task-center-summary'] });
  };
  const queueMutation = useMutation({
    mutationFn: async ({ jobId, action }: { jobId: number; action: 'cancel' | 'retry' }) => {
      const reason = action === 'cancel' ? '操作员取消' : '操作员重新排队';
      return (
        await request.post(`${EDGE_VALIDATION_JOBS_PATH}/${jobId}/${action}`, {
          reason,
        })
      ).data;
    },
    onSuccess: async (_result, variables) => {
      message.success(variables.action === 'cancel' ? '边端任务已取消' : '边端任务已重新排队');
      refresh();
      await queryClient.invalidateQueries({ queryKey: ['edge-task-center-job', variables.jobId] });
    },
    onError: (error, variables) => {
      if (responseStatus(error) === 409) refresh();
      message.error(queueMutationError(error, variables.action));
    },
  });
  const resetPage = () => setPage(0);
  const openDetail = (jobId: number) => {
    setSelectedJobId(jobId);
    setDetailOpen(true);
  };
  const columns: ColumnsType<EdgeValidationJob> = useMemo(
    () => [
      {
        title: '边端任务',
        key: 'job',
        width: 190,
        fixed: 'left',
        render: (_, row) => (
          <div style={{ minWidth: 0 }}>
            <Button
              type="link"
              size="small"
              style={{ padding: 0, height: 'auto', fontWeight: 600 }}
              onClick={() => openDetail(row.id)}
            >
              #{row.id}
            </Button>
            <Typography.Text
              ellipsis={{ tooltip: row.task_name || undefined }}
              style={{ display: 'block', maxWidth: 170, fontSize: 12 }}
            >
              {row.task_name || (row.task_id ? `任务 #${row.task_id}` : '未命名任务')}
            </Typography.Text>
          </div>
        ),
      },
      {
        title: '来源',
        key: 'source',
        width: 100,
        render: (_, row) => <Tag>{edgeSourceLabel(edgeJobSourceValue(row))}</Tag>,
      },
      {
        title: '状态',
        dataIndex: 'status',
        key: 'status',
        width: 100,
        render: (value) => <Tag color={edgeJobStatusColor(value)}>{edgeJobStatusLabel(value)}</Tag>,
      },
      {
        title: '队列信息',
        key: 'queue',
        width: 170,
        render: (_, row) => (
          <QueueCell>
            {row.queue_position ? (
              <span className="position">队列第 {row.queue_position} 位</span>
            ) : (
              <span className="position">{edgeJobStatusLabel(row.status)}</span>
            )}
            <span className="reason">{edgeQueueReasonLabel(row.queue_reason)}</span>
          </QueueCell>
        ),
      },
      {
        title: '执行设备',
        key: 'device',
        width: 150,
        render: (_, row) => (
          <Typography.Text ellipsis={{ tooltip: row.edge_device_name || row.edge_device_id || undefined }}>
            {row.edge_device_name || row.edge_device_id || '尚未分配'}
          </Typography.Text>
        ),
      },
      {
        title: '阶段进度',
        key: 'progress',
        width: 220,
        render: (_, row) => {
          const percent = progressPercent(row);
          return (
            <ProgressCell>
              <span className="stage">{progressStage(row)}</span>
              {percent == null ? (
                <span className="message">{row.progress?.message || '等待进度上报'}</span>
              ) : (
                <Progress
                  percent={percent}
                  size="small"
                  showInfo
                  format={(value) => `${Math.round(value || 0)}%`}
                  status={String(row.status).toUpperCase() === 'FAILED' ? 'exception' : undefined}
                />
              )}{' '}
              {row.progress?.message && percent != null && <span className="message">{row.progress.message}</span>}
            </ProgressCell>
          );
        },
      },
      {
        title: '时间',
        key: 'time',
        width: 220,
        render: (_, row) => {
          const timing = edgeJobTiming(row);
          return (
            <div style={{ color: '#64748b', fontSize: 12, lineHeight: 1.55 }}>
              <div>排队：{formatDate(row.queued_at || row.created_at)}</div>
              <div>等待：{timing.waiting}</div>
              <div>执行：{timing.running}</div>
              <div>预计剩余：{timing.remaining}</div>
              <div>更新：{formatDate(row.updated_at || row.started_at)}</div>
            </div>
          );
        },
      },
      {
        title: '操作',
        key: 'action',
        width: 132,
        fixed: 'right',
        render: (_, row) => {
          const cancelable = canCancelEdgeJob(row);
          const retryable = canRetryEdgeJob(row);
          const mutationPending = queueMutation.isPending && queueMutation.variables?.jobId === row.id;
          return (
            <Space size={2}>
              <Tooltip title="查看任务详情">
                <Button
                  type="text"
                  aria-label={`查看任务 ${row.id} 详情`}
                  icon={<EyeOutlined />}
                  onClick={() => openDetail(row.id)}
                />
              </Tooltip>
              {cancelable ? (
                <Tooltip title="取消排队任务">
                  <Popconfirm
                    title="确认取消此边端任务？"
                    description="取消后任务会从设备队列移除，可在终态重新排队。"
                    okText="确认取消"
                    cancelText="返回"
                    onConfirm={() => queueMutation.mutate({ jobId: row.id, action: 'cancel' })}
                  >
                    <Button
                      type="text"
                      danger
                      aria-label={`取消边端任务 ${row.id}`}
                      icon={<CloseCircleOutlined />}
                      loading={mutationPending && queueMutation.variables?.action === 'cancel'}
                    />
                  </Popconfirm>
                </Tooltip>
              ) : null}
              {retryable ? (
                <Tooltip title="重新排队任务">
                  <Button
                    type="text"
                    aria-label={`重试边端任务 ${row.id}`}
                    icon={<ReloadOutlined />}
                    loading={mutationPending && queueMutation.variables?.action === 'retry'}
                    onClick={() => queueMutation.mutate({ jobId: row.id, action: 'retry' })}
                  />
                </Tooltip>
              ) : null}
            </Space>
          );
        },
      },
    ],
    [openDetail, queueMutation],
  );

  const onTableChange = (pagination: TablePaginationConfig) => {
    const nextSize = Number(pagination.pageSize || pageSize);
    if (nextSize !== pageSize) {
      setPageSize(nextSize);
      setPage(0);
      return;
    }
    setPage(Math.max(0, Number(pagination.current || 1) - 1));
  };

  return (
    <Page>
      <Header>
        <div>
          <h1>{hasContext ? contextTitle : '统一 BM1688 边端验证任务中心'}</h1>
          <p>
            {hasContext
              ? `仅显示 ${contextLabel} 的边端验证记录；大批量任务仍按服务端分页加载。`
              : '统一查看样本准备、设备排队、执行进度和失败原因；大批量任务按服务端分页加载。'}
          </p>
        </div>
        <Space wrap>
          <Tag
            icon={pageVisible ? <CheckCircleOutlined /> : <ClockCircleOutlined />}
            color={pageVisible ? 'green' : 'default'}
          >
            {pageVisible ? '页面实时刷新' : '页面已暂停刷新'}
          </Tag>
          {hasContext && (
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/edge-task-center')}>
              返回全局任务中心
            </Button>
          )}
          <Button icon={<ClusterOutlined />} onClick={() => navigate('/admin?view=edge-devices')}>
            边端设备管理
          </Button>
          <Button icon={<ReloadOutlined />} loading={jobsQuery.isFetching || summaryQuery.isFetching} onClick={refresh}>
            刷新队列
          </Button>
        </Space>
      </Header>

      {hasContext && (
        <Alert
          style={{ marginBottom: 16 }}
          type="info"
          showIcon
          message={`当前查看范围：${contextLabel}`}
          description="任务列表、队列摘要、排名和耗时估算均按当前模型/任务上下文加载；设备卡片展示全局设备池的实时占用状态。"
        />
      )}

      {(summaryQuery.error || jobsQuery.error) && (
        <Alert
          style={{ marginBottom: 16 }}
          type="warning"
          showIcon
          icon={<WarningOutlined />}
          message={summaryQuery.error && jobsQuery.error ? '边端队列暂时不可用' : '边端队列部分数据暂不可用'}
          description={
            responseStatus(summaryQuery.error || jobsQuery.error) === 403
              ? '当前账号无权查看边端任务，请联系管理员。'
              : '汇总或列表接口暂时失败，已保留可用数据；可点击刷新重试。'
          }
          action={
            <Button size="small" onClick={refresh}>
              重试
            </Button>
          }
        />
      )}

      <QueueSummaryCards summary={summary} rows={rows} total={total} />
      <DeviceQueuePanel devices={devices} loading={summaryQuery.isLoading} />

      <Panel aria-label="边端任务列表">
        <PanelHeading>
          <div>
            <h2>任务队列</h2>
            <p>按状态、设备和来源筛选；点击任务编号查看执行时间线。</p>
          </div>
          <Typography.Text type="secondary">共 {total.toLocaleString()} 条</Typography.Text>
        </PanelHeading>
        <FilterBar>
          <ServerSearchInput
            className="search"
            value={keywordInput}
            onChange={(value) => {
              setKeywordInput(value);
              if (!value) {
                setKeyword('');
                resetPage();
              }
            }}
            onSearch={(value) => {
              setKeyword(value);
              setKeywordInput(value);
              resetPage();
            }}
            placeholder="搜索任务名称、任务编号或设备"
            ariaLabel="搜索边端任务"
            loading={jobsQuery.isFetching}
          />
          <Select
            aria-label="任务状态筛选"
            value={status}
            style={{ width: 132 }}
            options={[
              { value: 'all', label: '全部状态' },
              ...Object.entries(STATUS_LABELS).map(([value, label]) => ({ value, label })),
            ]}
            onChange={(value) => {
              setStatus(value);
              resetPage();
            }}
          />
          <Select
            aria-label="设备筛选"
            value={deviceId}
            style={{ minWidth: 150, maxWidth: 240 }}
            options={deviceOptions}
            onChange={(value) => {
              setDeviceId(value);
              resetPage();
            }}
          />
          <Select
            aria-label="任务来源筛选"
            value={sourceType}
            style={{ width: 132 }}
            options={[
              { value: 'all', label: '全部来源' },
              ...Object.entries(SOURCE_LABELS)
                .filter(([key]) =>
                  [
                    'internal',
                    'auto_training',
                    'auto_quality',
                    'manual_diagnostic',
                    'evolution',
                    'external',
                    'third_party',
                  ].includes(key),
                )
                .map(([value, label]) => ({ value, label })),
            ]}
            onChange={(value) => {
              setSourceType(value);
              resetPage();
            }}
          />
          <Checkbox
            checked={activeOnly}
            onChange={(event) => {
              setActiveOnly(event.target.checked);
              resetPage();
            }}
          >
            仅活动任务
          </Checkbox>
        </FilterBar>
        <Table<EdgeValidationJob>
          style={{ marginTop: 14 }}
          rowKey={(row) => String(row.id)}
          columns={columns}
          dataSource={rows}
          loading={jobsQuery.isLoading}
          size="small"
          scroll={{ x: 1280 }}
          locale={{
            emptyText: jobsQuery.isLoading ? (
              <Spin size="small" />
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无符合条件的边端任务" />
            ),
          }}
          pagination={{
            current: page + 1,
            pageSize,
            total,
            showSizeChanger: true,
            pageSizeOptions: [20, 50, 100],
            showQuickJumper: true,
            showTotal: (value, range) => `第 ${range[0]}-${range[1]} 条，共 ${value} 条`,
          }}
          onChange={onTableChange}
        />
      </Panel>

      <JobDetailDrawer jobId={selectedJobId} open={detailOpen} onClose={() => setDetailOpen(false)} />
    </Page>
  );
}
