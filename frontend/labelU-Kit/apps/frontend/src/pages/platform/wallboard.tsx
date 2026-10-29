import { ReloadOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Empty, Space, Spin, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { Fragment, useState } from 'react';

import request from '@/api/request';
import ServerSearchInput from '@/components/ServerSearchInput';
import { calculateOnlineRate, isWallboardPollingAllowed, projectQueryKey } from './routing';
import {
  type EdgeAgentRow,
  listData,
  QueueHint,
  QueueToolbar,
  responseStatus,
  type Row,
  listTotal,
  StatusItem,
  StatusStrip,
  TablePanel,
  WallboardPanel,
} from './shared';
import { ACTIVE_RELEASE_STATUSES, ACTIVE_TRAINING_STATUSES, usePageVisible } from './polling';

const TRAINING_JOB_TYPES = new Set([
  'TRAINING',
  'YOLO_TRAIN',
  'YOLO_TRAINING',
  'YOLO_DETECTION',
  'PLATE_DETECTION',
  'PLATE_RECOGNITION',
]);

export function countActiveTrainingJobs(jobs: Row[]) {
  return jobs.filter((job) => {
    const jobType = String(job.job_type || '').toUpperCase();
    return TRAINING_JOB_TYPES.has(jobType) && ACTIVE_TRAINING_STATUSES.has(String(job.status || '').toUpperCase());
  }).length;
}

function formatHeartbeat(value?: string | null) {
  if (!value) return '从未上报';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

export function DeviceMonitor({
  rows,
  loading,
  error,
  onRefresh,
}: {
  rows: EdgeAgentRow[];
  loading: boolean;
  error: unknown;
  onRefresh: () => void;
}) {
  const columns: ColumnsType<EdgeAgentRow> = [
    { title: '设备', dataIndex: 'name', key: 'name', render: (value, row) => `${value || '-'} (${row.agent_id})`, ellipsis: true },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (value) => {
        const normalized = String(value || 'UNKNOWN').toUpperCase();
        const color = ['ONLINE', 'ACTIVE', 'HEALTHY'].includes(normalized)
          ? 'green'
          : ['OFFLINE', 'FAILED', 'UNHEALTHY'].includes(normalized)
          ? 'red'
          : 'default';
        return <Tag color={color}>{normalized}</Tag>;
      },
    },
    { title: '平台 / Agent 版本', key: 'runtime', render: (_, row) => `${row.platform || '-'} / ${row.agent_version || '-'}` },
    { title: '最近心跳', dataIndex: 'last_heartbeat_at', key: 'last_heartbeat_at', render: (value) => formatHeartbeat(value) },
    {
      title: '运行指标',
      key: 'metrics',
      render: (_, row) => {
        const state = row.reported_state || {};
        const cpu = state.cpu_percent ?? state.cpu ?? '-';
        const memory = state.memory_percent ?? state.memory ?? '-';
        return `CPU ${cpu}${typeof cpu === 'number' ? '%' : ''} / 内存 ${memory}${typeof memory === 'number' ? '%' : ''}`;
      },
    },
  ];

  return (
    <TablePanel>
      <QueueToolbar>
        <div>
          <h2 style={{ margin: 0 }}>设备监控</h2>
          <QueueHint>展示平台边端代理最近心跳和上报状态</QueueHint>
        </div>
        <Button icon={<ReloadOutlined />} loading={loading} onClick={onRefresh}>刷新设备</Button>
      </QueueToolbar>
      {error ? (
        <Alert
          style={{ marginTop: 14 }}
          type="warning"
          showIcon
          message={responseStatus(error) === 403 ? '无权限查看设备监控' : '设备监控暂不可用'}
          description={responseStatus(error) === 403 ? '设备列表仅对系统管理员开放，请联系管理员。' : '请稍后重试，设备发布状态不受影响。'}
          action={<Button size="small" onClick={onRefresh}>重试</Button>}
        />
      ) : rows.length ? (
        <Table<EdgeAgentRow>
          style={{ marginTop: 14 }}
          rowKey={(row) => row.agent_id}
          columns={columns}
          dataSource={rows}
          loading={loading}
          pagination={false}
          size="small"
          scroll={{ x: 760 }}
        />
      ) : loading ? (
        <div role="status" aria-live="polite" style={{ padding: '28px 0', textAlign: 'center' }}><Spin /></div>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="平台暂无已注册设备" />
      )}
    </TablePanel>
  );
}

export function useWallboard(projectId?: number, enabled = true) {
  const pageVisible = usePageVisible();
  const [jobsPage, setJobsPage] = useState(0);
  const [activitySearch, setActivitySearch] = useState('');
  const [activitySearchDraft, setActivitySearchDraft] = useState('');
  const jobsPageSize = 10;
  const summaryQuery = useQuery({
    queryKey: projectQueryKey('workspace-summary', projectId),
    queryFn: async () => {
      const response = await request.get('/v1/workspace/summary', {
        params: projectId ? { project_id: projectId } : undefined,
      });
      return response.data as {
        task_count: number;
        pending_annotation_task_count: number;
        model_count: number;
        published_model_count: number;
        download_disabled_model_count: number;
        active_training_count: number;
        failed_training_count: number;
        pending_feedback_count: number;
      };
    },
    enabled: enabled && Boolean(localStorage.getItem('token')),
    staleTime: 30_000,
    retry: 1,
    refetchInterval: (query: any) =>
      isWallboardPollingAllowed({
        visible: pageVisible,
        hasActiveWork: Number(query.state.data?.active_training_count || 0) > 0,
      })
        ? 15_000
        : false,
  });
  const agentsQuery = useQuery({
    queryKey: projectQueryKey('edge-agents', projectId),
    queryFn: async () =>
      (await request.get('/v1/edge-agents/monitor', {
        params: { project_id: projectId, page: 0, size: 100 },
      })).data,
    enabled: enabled && Boolean(projectId),
    staleTime: 10_000,
    refetchInterval: enabled && pageVisible ? 15_000 : false,
  });
  const jobsQuery = useQuery({
    queryKey: [...projectQueryKey('workspace-recent-jobs', projectId), jobsPage, jobsPageSize, activitySearch],
    queryFn: async () =>
      request.get('/v1/workspace/recent-jobs', { params: { page: jobsPage, size: jobsPageSize, q: activitySearch || undefined } }),
    enabled: enabled && Boolean(localStorage.getItem('token')),
    staleTime: 10_000,
    refetchInterval: (query: any) => {
      const rows = listData(query.state.data);
      const hasActiveWork = rows.some((row) => {
        const status = String(row.status || '').toUpperCase();
        return ACTIVE_TRAINING_STATUSES.has(status) || ACTIVE_RELEASE_STATUSES.has(status);
      });
      return isWallboardPollingAllowed({ visible: pageVisible, hasActiveWork }) ? 10_000 : false;
    },
  });
  const alertsQuery = useQuery({
    queryKey: [...projectQueryKey('workspace-alerts', projectId), activitySearch],
    queryFn: async () =>
      await request.get('/v1/workspace/alerts', { params: { page: 0, size: 5, q: activitySearch || undefined } }),
    enabled: enabled && Boolean(localStorage.getItem('token')),
    staleTime: 10_000,
    refetchInterval: false,
  });
  const alertsPayload = alertsQuery.data as { data?: Row[]; meta_data?: { total?: number } } | undefined;
  const alerts = listData(alertsPayload?.data);
  const jobsPayload = jobsQuery.data;
  const jobs = listData(jobsPayload);
  const jobsTotal = listTotal(jobsPayload, jobs.length);

  return {
    summaryQuery,
    agentsQuery,
    jobsQuery,
    alertsQuery,
    jobs,
    jobsTotal,
    jobsPage,
    jobsPageSize,
    setJobsPage,
    alerts,
    alertTotal: Number(alertsPayload?.meta_data?.total ?? alerts.length),
    activitySearch,
    activitySearchDraft,
    setActivitySearchDraft: (value: string) => {
      setActivitySearchDraft(value);
    },
    applyActivitySearch: (value: string) => {
      const next = value.trim();
      setActivitySearchDraft(value);
      setActivitySearch(next);
      setJobsPage(0);
    },
    refresh: () => {
      void summaryQuery.refetch();
      void jobsQuery.refetch();
      void alertsQuery.refetch();
      void agentsQuery.refetch();
    },
  };
}

const WORK_TYPE_LABELS: Record<string, string> = {
  TRAINING: '模型训练',
  BM1688_COMPILE: 'BM1688 编译',
  EDGE_VALIDATION: '真机验证',
};

const WORK_STATUS_LABELS: Record<string, string> = {
  PENDING: '等待处理',
  PROCESSING: '处理中',
  RUNNING: '运行中',
  COMPLETED: '已完成',
  SUCCEEDED: '已完成',
  FAILED: '失败',
  CANCELLED: '已取消',
};

export function WorkbenchActivity({
  jobs,
  jobsTotal,
  jobsPage = 0,
  jobsPageSize = 10,
  onJobsPageChange,
  alerts,
  alertTotal,
  activitySearchDraft = '',
  onActivitySearchChange,
  onActivitySearchSubmit,
  loading,
  error,
  onRefresh,
}: {
  jobs: Row[];
  jobsTotal?: number;
  jobsPage?: number;
  jobsPageSize?: number;
  onJobsPageChange?: (page: number, pageSize: number) => void;
  alerts: Row[];
  alertTotal?: number;
  activitySearchDraft?: string;
  onActivitySearchChange?: (value: string) => void;
  onActivitySearchSubmit?: (value: string) => void;
  loading: boolean;
  error: unknown;
  onRefresh: () => void;
}) {
  const submitActivitySearch = onActivitySearchSubmit || onActivitySearchChange;
  const columns: ColumnsType<Row> = [
    { title: '环节', dataIndex: 'job_type', key: 'job_type', render: (value) => WORK_TYPE_LABELS[String(value)] || '其他任务' },
    { title: '任务编号', dataIndex: 'task_id', key: 'task_id', width: 110, render: (value) => value ? `#${value}` : '-' },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 110,
      render: (value) => {
        const status = String(value || '').toUpperCase();
        const color = status === 'FAILED' ? 'red' : ['COMPLETED', 'SUCCEEDED'].includes(status) ? 'green' : 'blue';
        return <Tag color={color}>{WORK_STATUS_LABELS[status] || '状态未知'}</Tag>;
      },
    },
    { title: '更新时间', dataIndex: 'updated_at', key: 'updated_at', width: 180, render: (value, row) => formatHeartbeat(value || row.created_at as string) },
  ];

  return (
    <WallboardPanel aria-label="最近运行" children={<>
      <QueueToolbar style={{ marginTop: 0 }}>
        <div>
          <h2 style={{ margin: 0 }}>最近运行</h2>
          <QueueHint>模型训练、BM1688 编译和真机验证的最新状态</QueueHint>
        </div>
        <Space wrap>
          {onActivitySearchChange ? <ServerSearchInput
            value={activitySearchDraft}
            onChange={(value) => {
              onActivitySearchChange(value);
              if (!value) submitActivitySearch?.('');
            }}
            onSearch={(value) => submitActivitySearch?.(value)}
            placeholder="按任务、作业、状态或错误信息搜索"
            ariaLabel="搜索运行记录"
            loading={loading}
            style={{ width: 300, maxWidth: '100%' }}
          /> : null}
          <Button icon={<ReloadOutlined />} loading={loading} onClick={onRefresh}>刷新</Button>
        </Space>
      </QueueToolbar>
      {error && (
        <Alert
          style={{ marginTop: 14 }}
          type="warning"
          showIcon
          message="运行记录暂不可用"
          description="请稍后重试，页面不会提交新的训练、编译或验证任务。"
        />
      )}
      {alerts.length > 0 && (
        <Alert
          style={{ marginTop: 14 }}
          type="error"
          showIcon
          message={`${Number(alertTotal || alerts.length)} 条失败记录待处理`}
          description={alerts[0].message ? String(alerts[0].message) : '请进入对应业务详情查看失败原因。'}
        />
      )}
      {jobs.length > 0 ? (
        <Table<Row>
          style={{ marginTop: 14 }}
          rowKey={(row) => String(row.id)}
          columns={columns}
          dataSource={jobs}
          loading={loading}
          pagination={onJobsPageChange ? {
            current: jobsPage + 1,
            pageSize: jobsPageSize,
            total: jobsTotal,
            showSizeChanger: false,
            showQuickJumper: true,
            showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条，共 ${total} 条`,
            onChange: (page, pageSize) => onJobsPageChange(page - 1, pageSize),
          } : false}
          size="small"
          scroll={{ x: 640 }}
        />
      ) : !loading && !error ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无训练、编译或真机验证记录" style={{ margin: '18px 0 0' }} />
      ) : null}
    </>} />
  );
}

export function Wallboard({
  summary,
  jobs,
  alerts,
  alertTotal,
  agents,
  loading,
  error,
  onRefresh,
}: {
  summary?: { active_training_count?: number; failed_deployment_count?: number } | null;
  jobs: Row[];
  alerts: Row[];
  alertTotal?: number;
  agents: EdgeAgentRow[];
  loading: boolean;
  error: unknown;
  onRefresh: () => void;
}) {
  const onlineRate = calculateOnlineRate(agents);
  const latestRelease = jobs.find((job) => String(job.job_type || '').toUpperCase() === 'DEPLOYMENT');
  const activeTraining = countActiveTrainingJobs(jobs);
  const trainingCount = Math.max(activeTraining, Number(summary?.active_training_count || 0));
  const releaseStatus = latestRelease?.status ? String(latestRelease.status).toUpperCase() : '暂无发布';
  const failedAlerts = Math.max(Number(alertTotal || 0), alerts.length);

  return (
    <WallboardPanel aria-label="运行墙" children={<>
        <QueueToolbar
          style={{ marginTop: 0 }}
          children={<div style={{ display: 'contents' }}>
            <div>
              <h2>运行墙</h2>
              <QueueHint>平台运行摘要</QueueHint>
            </div>
            <Button icon={<ReloadOutlined />} loading={loading} onClick={onRefresh}>刷新</Button>
          </div>}
        />
        {error && (
          <Alert
            style={{ marginTop: 14 }}
            type="warning"
            showIcon
            message="部分运行摘要暂不可用"
            description={responseStatus(error) === 403 ? '当前账号无权查看设备状态，其他摘要仍可用。' : '请稍后重试。'}
          />
        )}
        <StatusStrip style={{ marginTop: 14, marginBottom: 0 }}>
          <StatusItem><span className="status-label">设备在线率</span><span className="status-value">{onlineRate == null ? '暂无设备' : `${onlineRate}%`}</span></StatusItem>
          <StatusItem><span className="status-label">当前发布阶段</span><span className="status-value">{releaseStatus}</span></StatusItem>
          <StatusItem><span className="status-label">告警摘要</span><span className="status-value">{failedAlerts ? `${failedAlerts} 条待处理` : '无告警'}</span></StatusItem>
          <StatusItem><span className="status-label">训练队列</span><span className="status-value">{trainingCount ? `${trainingCount} 个进行中` : '队列空闲'}</span></StatusItem>
        </StatusStrip>
        {!loading && !alerts.length && !jobs.length && !agents.length && !error && (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="平台暂无运行记录" style={{ margin: '18px 0 0' }} />
        )}
    </>} />
  );
}
