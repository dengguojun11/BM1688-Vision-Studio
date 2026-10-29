import { ClusterOutlined, ReloadOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Empty, Spin, Tag, Typography } from 'antd';
import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import styled from 'styled-components';

import request from '@/api/request';
import useTaskSuperuser from '@/hooks/useTaskSuperuser';
import { workspaceSummaryState } from '../routing';
import { ModuleHeader, responseStatus, StatusItem, StatusStrip, WorkPanel } from '../shared';
import { usePageVisible } from '../polling';
import { useWallboard, WorkbenchActivity } from '../wallboard';
import { WorkspaceSummaryPanel } from '../WorkspaceSummaryPanel';
import EvolutionCockpit from '../evolution/EvolutionCockpit';

const EDGE_QUEUE_SUMMARY_PATH = '/v1/yolo-edge-validation/queue/summary';
const EDGE_QUEUE_SUMMARY_COMPAT_PATH = '/v1/yolo-edge-validation/jobs/queue-summary';

type EdgeWorkspaceSummary = {
  counts: Record<string, number>;
  estimated_clear_seconds?: number | null;
  eta_confidence?: string | null;
  eta_basis?: string | null;
  completed_duration_samples?: number | null;
  devices: Array<{ is_online?: boolean; is_ready?: boolean }>;
};

const SummaryHeader = styled.div`
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
  flex-wrap: wrap;

  h2 {
    margin: 0 0 4px;
    color: #172033;
    font-size: 16px;
    line-height: 1.35;
    font-weight: 650;
  }

  p {
    margin: 0;
    color: #64748b;
    font-size: 12px;
  }
`;

function record(value: unknown): Record<string, any> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, any>) : {};
}

function numberValue(value: unknown) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.max(0, parsed) : 0;
}

export function workspacePendingCount(counts: Record<string, unknown>) {
  return (
    numberValue(counts.queued) +
    numberValue(counts.pending) +
    numberValue(counts.preparing) +
    numberValue(counts.retrying)
  );
}

export function normalizeWorkspaceEdgeSummary(value: unknown): EdgeWorkspaceSummary {
  const root = record(value);
  const payload = record(root.data ?? root);
  const counts = record(payload.counts);
  const devices = Array.isArray(payload.devices) ? payload.devices : [];
  return {
    counts: Object.fromEntries(Object.entries(counts).map(([key, item]) => [key, numberValue(item)])),
    estimated_clear_seconds:
      payload.estimated_clear_seconds == null ? null : numberValue(payload.estimated_clear_seconds),
    eta_confidence: payload.eta_confidence == null ? null : String(payload.eta_confidence),
    eta_basis: payload.eta_basis == null ? null : String(payload.eta_basis),
    completed_duration_samples:
      payload.completed_duration_samples == null ? null : numberValue(payload.completed_duration_samples),
    devices: devices.map((item) => {
      const device = record(item);
      return { is_online: device.is_online !== false, is_ready: device.is_ready === true };
    }),
  };
}

export function formatWorkspaceQueueDuration(seconds: number | null | undefined) {
  if (seconds == null) return '暂无估算';
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `${total} 秒`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} 小时 ${rest} 分钟` : `${hours} 小时`;
}

export function canViewWorkspaceEdgeQueue({
  isSuccess,
  isTaskSuperuser,
}: {
  isSuccess?: boolean;
  isTaskSuperuser?: boolean;
}) {
  return isSuccess === true && isTaskSuperuser === true;
}

export function EdgeQueueWorkspaceSummary() {
  const navigate = useNavigate();
  const pageVisible = usePageVisible();
  const userQuery = useTaskSuperuser();
  const canViewQueue = canViewWorkspaceEdgeQueue(userQuery);
  const query = useQuery<EdgeWorkspaceSummary>({
    queryKey: ['workspace-edge-queue-summary'],
    queryFn: async () => {
      try {
        return normalizeWorkspaceEdgeSummary((await request.get(EDGE_QUEUE_SUMMARY_PATH)).data);
      } catch (error) {
        if (responseStatus(error) !== 404) throw error;
        return normalizeWorkspaceEdgeSummary((await request.get(EDGE_QUEUE_SUMMARY_COMPAT_PATH)).data);
      }
    },
    staleTime: 5_000,
    retry: 1,
    enabled: canViewQueue,
    refetchInterval: (current) => {
      const counts = current.state.data?.counts || {};
      const active =
        numberValue(counts.active) ||
        numberValue(counts.queued) ||
        numberValue(counts.pending) ||
        numberValue(counts.processing) ||
        numberValue(counts.preparing) ||
        numberValue(counts.retrying);
      return pageVisible && active > 0 ? 5_000 : false;
    },
    refetchIntervalInBackground: false,
  });

  const summary = query.data;
  const counts = summary?.counts || {};
  const total = useMemo(
    () =>
      numberValue(counts.total) ||
      numberValue(counts.queued) +
        numberValue(counts.preparing) +
        numberValue(counts.retrying) +
        numberValue(counts.processing) +
        numberValue(counts.failed) +
        numberValue(counts.completed) +
        numberValue(counts.cancelled),
    [counts],
  );
  const online = summary?.devices.filter((device) => device.is_online !== false).length || 0;
  const ready = summary?.devices.filter((device) => device.is_ready === true).length || 0;
  const etaBasis = summary?.eta_basis ? `依据：${summary.eta_basis}` : '';
  const etaSamples = summary?.completed_duration_samples
    ? `历史样本：${Math.round(summary.completed_duration_samples)} 条`
    : '';

  // The queue summary is an administrator-only operational view. Fail closed
  // while the account permission is loading or unavailable so regular users do
  // not issue a forbidden request or see a misleading error panel.
  if (!canViewQueue) return null;

  return (
    <WorkPanel aria-label="边端执行摘要" style={{ marginBottom: 20 }}>
      <SummaryHeader>
        <div>
          <h2>
            <ClusterOutlined style={{ marginRight: 7 }} />
            边端执行摘要
          </h2>
          <p>队列、设备就绪和预计清空时间来自同一份服务端快照。</p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <Tag color={pageVisible ? 'green' : 'default'}>{pageVisible ? '实时刷新' : '已暂停刷新'}</Tag>
          <Button icon={<ReloadOutlined />} loading={query.isFetching} onClick={() => void query.refetch()}>
            刷新
          </Button>
          <Button type="link" onClick={() => navigate('/edge-task-center')}>
            查看任务中心
          </Button>
        </div>
      </SummaryHeader>
      {query.isLoading ? (
        <div
          role="status"
          aria-label="正在加载边端执行摘要"
          style={{ minHeight: 60, display: 'grid', placeItems: 'center' }}
        >
          <Spin />
        </div>
      ) : query.error ? (
        <Alert
          type="warning"
          showIcon
          message={responseStatus(query.error) === 403 ? '当前账号无权查看边端队列' : '边端队列摘要暂不可用'}
          description="任务中心仍可单独打开并重试；当前工作台不会用旧数据冒充实时状态。"
          action={
            <Button size="small" onClick={() => void query.refetch()}>
              重试
            </Button>
          }
        />
      ) : summary && summary.devices.length === 0 && total === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无边端任务或已登记设备" />
      ) : (
        <StatusStrip style={{ marginBottom: 0 }}>
          <StatusItem>
            <span className="status-label">待处理</span>
            <span className="status-value">{workspacePendingCount(counts)} 条</span>
          </StatusItem>
          <StatusItem>
            <span className="status-label">执行中</span>
            <span className="status-value">{numberValue(counts.processing) + numberValue(counts.running)} 条</span>
          </StatusItem>
          <StatusItem>
            <span className="status-label">阻塞 / 失败</span>
            <span className="status-value">
              {numberValue(counts.blocked)} / {numberValue(counts.failed)}
            </span>
          </StatusItem>
          <StatusItem>
            <span className="status-label">设备池</span>
            <span className="status-value">
              {ready} / {online} 就绪 / 在线
            </span>
          </StatusItem>
          <StatusItem>
            <span className="status-label">预计清空</span>
            <span className="status-value">{formatWorkspaceQueueDuration(summary?.estimated_clear_seconds)}</span>
          </StatusItem>
          <StatusItem>
            <span className="status-label">队列总量</span>
            <span className="status-value">
              {total} 条
              {summary?.eta_confidence ? ` · 可信度：${summary.eta_confidence}` : ''}
            </span>
            {etaBasis || etaSamples ? (
              <span className="status-detail">
                {[etaBasis, etaSamples].filter(Boolean).join(' · ')}
              </span>
            ) : null}
          </StatusItem>
        </StatusStrip>
      )}
      {summary && query.isFetching && !query.isLoading ? (
        <Typography.Text type="secondary" style={{ display: 'block', marginTop: 8, fontSize: 11 }}>
          正在同步最新队列快照…
        </Typography.Text>
      ) : null}
    </WorkPanel>
  );
}

export default function WorkspacePage() {
  const navigate = useNavigate();
  const wallboard = useWallboard();
  const summary = wallboard.summaryQuery.data;
  const workspaceState = workspaceSummaryState({
    loading: wallboard.summaryQuery.isLoading,
    hasData: Boolean(summary),
    status: responseStatus(wallboard.summaryQuery.error),
    online: typeof navigator === 'undefined' || navigator.onLine,
  });

  return (
    <>
      <ModuleHeader module="workspace" />
      <WorkspaceSummaryPanel
        state={workspaceState}
        summary={summary}
        refreshing={wallboard.summaryQuery.isFetching}
        onRetry={() => void wallboard.summaryQuery.refetch()}
      />
      <EdgeQueueWorkspaceSummary />
      <WorkbenchActivity
        jobs={wallboard.jobs}
        jobsTotal={wallboard.jobsTotal}
        jobsPage={wallboard.jobsPage}
        jobsPageSize={wallboard.jobsPageSize}
        onJobsPageChange={(page) => wallboard.setJobsPage(page)}
        alerts={wallboard.alerts}
        alertTotal={wallboard.alertTotal}
        activitySearchDraft={wallboard.activitySearchDraft}
        onActivitySearchChange={wallboard.setActivitySearchDraft}
        onActivitySearchSubmit={wallboard.applyActivitySearch}
        loading={
          wallboard.summaryQuery.isFetching || wallboard.jobsQuery.isFetching || wallboard.alertsQuery.isFetching
        }
        error={wallboard.alertsQuery.error || wallboard.jobsQuery.error}
        onRefresh={wallboard.refresh}
      />
      <EvolutionCockpit />
      <WorkPanel>
        <h2>开始使用 7GAI</h2>
        <p>从任务与标注开始：上传数据、完成标注，系统会在训练提交时自动生成不可变训练快照。</p>
        <Button type="primary" style={{ marginTop: 16 }} onClick={() => navigate('/tasks')}>
          进入任务与标注
        </Button>
      </WorkPanel>
    </>
  );
}
