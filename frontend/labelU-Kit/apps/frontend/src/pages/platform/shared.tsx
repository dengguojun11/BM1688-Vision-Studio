import { CheckCircleOutlined } from '@ant-design/icons';
import { Tag } from 'antd';
import styled from 'styled-components';

import { MODULE_COPY, type PlatformModule } from './modules';

export type Row = Record<string, any> & { id?: number | string };

export interface TrainingFailureGuidance {
  reason: string;
  steps: string[];
};

const TRAINING_STATUS_LABELS: Record<string, string> = {
  PENDING: '排队中',
  PROCESSING: '训练中',
  RUNNING: '训练中',
  COMPLETED: '训练流程完成',
  FAILED: '训练失败',
  ERROR: '训练失败',
  CANCELLED: '已取消',
  STOPPED: '已暂停',
};

/** Translate machine states while retaining the original code for diagnostics. */
export function trainingStatusLabel(value: unknown, fallback = '未知状态') {
  const code = String(value ?? '').trim().toUpperCase();
  if (!code) return fallback;
  return TRAINING_STATUS_LABELS[code] || code;
}

const TRAINING_RUNTIME_STATUS_LABELS: Record<string, string> = {
  RUNNING: '运行中',
  PROCESSING: '运行中',
  COMPLETED: '已完成',
  FAILED: '失败',
  CANCELLED: '已取消',
  STOPPED: '已停止',
  PENDING: '等待运行',
};

export function trainingRuntimeStatusLabel(value: unknown, fallback = '未报告') {
  const code = String(value ?? '').trim().toUpperCase();
  if (!code) return fallback;
  return TRAINING_RUNTIME_STATUS_LABELS[code] || code;
}

export type TrainingQualityGateSummary = {
  level: 'pass' | 'pending' | 'review' | 'blocked';
  label: string;
  reason: string;
};

/**
 * A completed worker run is not necessarily a publishable model. Derive the
 * release conclusion from the structured assessments so the UI never treats
 * COMPLETED + pending/blocked as contradictory states.
 */
export function trainingQualityGateSummary(row?: Row | null): TrainingQualityGateSummary {
  const release = row?.release_gate_assessment as Record<string, any> | undefined;
  const quality = row?.quality_assessment as Record<string, any> | undefined;
  const releaseItem = quality?.release_readiness as Record<string, any> | undefined;
  const releaseCode = String(release?.status_code || '').trim().toLowerCase();
  const releaseStatus = String(releaseItem?.status || '').trim();
  const releaseReason = String(
    release?.reason_summary || release?.summary || releaseItem?.summary || '',
  ).trim();

  if (releaseCode === 'publishable' || releaseStatus === '可发布') {
    return { level: 'pass', label: '质量门禁已通过，可进入发布', reason: releaseReason || '训练、泛化与边端验证均满足当前发布要求。' };
  }
  if (releaseCode === 'blocked' || releaseStatus === '不可发布') {
    return { level: 'blocked', label: '质量门禁未通过，发布已阻断', reason: releaseReason || '至少一项质量门禁未通过，当前模型不可发布。' };
  }
  if (releaseCode === 'review_required' || releaseStatus === '需复核') {
    return { level: 'review', label: '质量门禁待复核，暂不可发布', reason: releaseReason || '质量结果需要人工复核后才能进入发布。' };
  }

  const assessments: Array<[string, Record<string, any> | undefined]> = [
    ['训练质量', row?.training_assessment as Record<string, any> | undefined || quality?.training_quality],
    ['泛化质量', quality?.generalization_quality],
    ['边端可用性', row?.edge_validation_assessment as Record<string, any> | undefined || quality?.edge_usability],
  ];
  for (const [name, item] of assessments) {
    if (!item) continue;
    const code = String(item.status_code || '').trim().toLowerCase();
    const status = String(item.status || item.status_label || '').trim();
    const reason = String(item.reason_summary || item.summary || '').trim();
    if (code === 'failed' || code === 'unusable' || status === '不达标' || status === '不可用' || status === '有风险') {
      return { level: 'blocked', label: '质量门禁未通过，发布已阻断', reason: reason || `${name}未达到发布要求。` };
    }
    if (code === 'pending' || code === 'usable_needs_review' || status === '待验证' || status === '未验证' || status === '可运行但效果待确认') {
      return { level: 'pending', label: '质量门禁待完成，暂不可发布', reason: reason || `${name}尚未完成验证。` };
    }
    if (code === 'conditional_passed' || status === '条件达标') {
      return { level: 'review', label: '质量门禁待复核，暂不可发布', reason: reason || `${name}为条件达标，需要复核。` };
    }
  }

  return { level: 'pending', label: '质量门禁尚未确认，暂不可发布', reason: '训练流程已结束，但尚未获得完整的质量门禁结论。' };
}

/**
 * Keep failure handling useful even when a worker only returns a free-form
 * error string. The suggestions are deliberately diagnostic and reversible:
 * they never imply that a failed job was changed automatically.
 */
export function getTrainingFailureGuidance(row?: Row | null): TrainingFailureGuidance | null {
  if (!row || !['FAILED', 'ERROR'].includes(String(row.status || '').toUpperCase())) return null;
  const reason = String(row.failure_reason || row.error_message || row.runtime_last_error || '').trim() || '训练 Worker 未返回具体错误信息';
  const normalized = reason.toLowerCase();
  let steps: string[];
  if (/(dataset|manifest|sample|annotation|数据集|清单|样本|标注)/i.test(normalized)) {
    steps = ['确认冻结数据集版本仍可访问，且样本与标注契约一致', '修复异常样本后重新生成数据集版本，再重新训练'];
  } else if (/(oom|out of memory|cuda out|显存|内存)/i.test(normalized)) {
    steps = ['降低输入尺寸或批次大小，确认 Worker 有足够显存/内存', '使用相同数据集版本重新提交，先用小规模参数验证'];
  } else if (/(image|container|pull|镜像|容器|registry)/i.test(normalized)) {
    steps = ['确认容器镜像地址、摘要和凭据可用，并检查标准 Worker 健康状态', '镜像修复后重新提交，不要复用已损坏的运行实例'];
  } else if (/(timeout|timed out|node|worker|heartbeat|connection|超时|节点|连接|心跳)/i.test(normalized)) {
    steps = ['检查训练节点或 Worker 心跳、队列和磁盘空间', '节点恢复后使用“继续”或重新提交，并保留本次失败日志'];
  } else {
    steps = ['先查看 Worker 日志和运行态，确认失败发生在数据、训练还是评估阶段', '修复根因后再使用“继续”或重新提交，避免重复消耗同一资源'];
  }
  return { reason, steps };
}

export type ModuleDataState = 'pending' | 'loading' | 'ready' | 'empty' | 'error' | 'offline';

/** Derive the status-strip state from the same query state rendered below it. */
export function moduleDataState({
  loading,
  error,
  rowCount,
  online = true,
}: {
  loading: boolean;
  error: unknown;
  rowCount: number;
  online?: boolean;
}): ModuleDataState {
  if (!online) return 'offline';
  if (loading) return 'loading';
  if (error) return 'error';
  return rowCount > 0 ? 'ready' : 'empty';
}

export type EdgeAgentRow = {
  agent_id: string;
  name: string;
  project_id?: number | null;
  status?: string | null;
  agent_version?: string | null;
  platform?: string | null;
  capabilities?: Record<string, unknown>;
  reported_state?: Record<string, unknown>;
  last_heartbeat_at?: string | null;
};

export function responseStatus(error: unknown): number | undefined {
  const status = (error as { response?: { status?: number } } | null)?.response?.status;
  return typeof status === 'number' ? status : undefined;
}

export function listData(value: any): Row[] {
  // Runtime requests return the business envelope directly, while a number
  // of test adapters (and a few legacy callers) still wrap it in Axios's
  // outer `data` property. Walk the small set of supported envelope shapes so
  // every list page gets the same behavior without each caller unwrapping it.
  const candidates = [
    value,
    value?.data,
    value?.data?.data,
    value?.data?.data?.data,
    value?.payload,
    value?.payload?.data,
  ];
  const rows = candidates.find((candidate) => Array.isArray(candidate));
  if (Array.isArray(rows)) return rows;
  return [];
}

export function listTotal(value: any, fallback = 0): number {
  const candidates = [
    value?.meta_data?.total,
    value?.meta?.total,
    value?.total,
    value?.data?.meta_data?.total,
    value?.data?.meta?.total,
    value?.data?.total,
    value?.data?.data?.meta_data?.total,
    value?.data?.data?.meta?.total,
    value?.data?.data?.total,
    value?.payload?.meta_data?.total,
    value?.payload?.meta?.total,
    value?.payload?.total,
    value?.payload?.data?.meta_data?.total,
    value?.payload?.data?.meta?.total,
  ];
  const total = candidates.find((item) => typeof item === 'number');
  return typeof total === 'number' ? total : fallback;
}

export const PageHeader = styled.div`
  margin-bottom: 22px;

  h1 {
    margin: 0 0 6px;
    color: #142033;
    font-size: 24px;
    line-height: 1.3;
    font-weight: 650;
  }

  p {
    margin: 0;
    color: #64748b;
    font-size: 14px;
  }
`;

export const StatusStrip = styled.div`
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 20px;

  @media (max-width: 680px) {
    grid-template-columns: 1fr;
  }
`;

export const StatusItem = styled.div`
  min-height: 72px;
  padding: 15px 16px;
  border: 1px solid #e3e8f0;
  border-radius: 6px;
  background: #fff;

  .status-label {
    display: block;
    margin-bottom: 7px;
    color: #64748b;
    font-size: 12px;
  }

  .status-value {
    color: #1e293b;
    font-size: 14px;
    font-weight: 600;
  }
`;

export const WorkPanel = styled.section`
  min-width: 0;
  padding: 20px;
  border: 1px solid #e3e8f0;
  border-radius: 6px;
  background: #fff;

  h2 {
    margin: 0 0 8px;
    color: #172033;
    font-size: 16px;
    font-weight: 650;
  }

  p {
    margin: 0;
    color: #64748b;
    font-size: 13px;
    line-height: 1.6;
  }

  .ant-table-wrapper {
    max-width: 100%;
    overflow-x: auto;
  }

  .ant-table-content {
    overflow-x: auto !important;
  }
`;

export const TablePanel = styled(WorkPanel)`
  padding: 16px;

  .ant-table-wrapper {
    margin-top: 14px;
  }
`;

export const WallboardPanel = styled(WorkPanel)`
  margin-bottom: 20px;
  padding: 16px;

  h2 {
    margin: 0;
    color: #172033;
    font-size: 16px;
    font-weight: 650;
  }
`;

export const QueueToolbar = styled.div`
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 16px;
  padding: 10px 12px;
  border: 1px solid #e8edf4;
  border-radius: 6px;
  background: #f8fafc;
`;

export const QueueHint = styled.span`
  color: #64748b;
  font-size: 12px;
`;

export const DetailGrid = styled.dl`
  display: grid;
  grid-template-columns: 120px minmax(0, 1fr);
  gap: 10px 16px;
  margin: 0 0 18px;
  font-size: 13px;

  dt {
    color: #64748b;
  }

  dd {
    min-width: 0;
    margin: 0;
    color: #172033;
    word-break: break-word;
  }
`;

export function ModuleHeader({ module }: { module: PlatformModule }) {
  const copy = MODULE_COPY[module];
  return (
    <PageHeader>
      <h1>{copy.title}</h1>
      <p>{copy.description}</p>
    </PageHeader>
  );
}

export function ModuleStatus({
  module,
  dataState = 'pending',
  rowCount = 0,
}: {
  module: PlatformModule;
  dataState?: ModuleDataState;
  rowCount?: number;
}) {
  const copy = MODULE_COPY[module];
  const dataStatus = {
    pending: { label: '等待数据请求', color: 'default' as const },
    loading: { label: '加载中', color: 'processing' as const },
    ready: { label: `已同步 ${rowCount} 条`, color: 'success' as const },
    empty: { label: '暂无数据', color: 'default' as const },
    error: { label: '加载失败', color: 'error' as const },
    offline: { label: '网络离线', color: 'warning' as const },
  }[dataState];
  return (
    <StatusStrip>
      <StatusItem>
        <span className="status-label">平台状态</span>
        <span className="status-value">
          <CheckCircleOutlined
            style={{
              color: dataState === 'error' || dataState === 'offline' ? 'var(--color-warning)' : 'var(--color-success)',
              marginRight: 6,
            }}
          />
          {dataState === 'error' ? '接口异常' : dataState === 'offline' ? '网络离线' : '基础服务可用'}
        </span>
      </StatusItem>
      <StatusItem>
        <span className="status-label">当前模块</span>
        <span className="status-value">{copy.title}</span>
      </StatusItem>
      <StatusItem>
        <span className="status-label">数据状态</span>
        <span className="status-value">
          <Tag color={dataStatus.color}>{dataStatus.label}</Tag>
        </span>
      </StatusItem>
    </StatusStrip>
  );
}
