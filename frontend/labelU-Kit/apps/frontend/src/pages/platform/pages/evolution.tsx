import { CheckOutlined, DeleteOutlined, EyeOutlined, PlusOutlined, ReloadOutlined, SyncOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Empty, Input, InputNumber, Modal, Select, Space, Spin, Table, Tabs, Tag, Tooltip, Typography, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useEffect, useState, type Key } from 'react';

import request from '@/api/request';
import ServerSearchInput from '@/components/ServerSearchInput';
import useTaskSuperuser from '@/hooks/useTaskSuperuser';

import {
  evolutionErrorState,
  evolutionReviewErrorMessage,
  reviewReasonMissing,
} from '../routing';
import {
  DetailGrid,
  listData,
  listTotal,
  ModuleHeader,
  ModuleStatus,
  QueueHint,
  QueueToolbar,
  responseStatus,
  type Row,
  TablePanel,
} from '../shared';

const HARD_EXAMPLE_TYPE_LABELS: Record<string, string> = {
  LOW_CONFIDENCE: '低置信度',
  FALSE_POSITIVE: '误报',
  FALSE_NEGATIVE: '漏检',
  DUPLICATE: '重复样本',
  OUT_OF_DISTRIBUTION: '分布外样本',
  UNKNOWN: '未分类',
};

const REVIEW_STATUS_LABELS: Record<string, string> = {
  PENDING: '待审核',
  ACCEPTED: '已接受',
  REJECTED: '已排除',
};

const TRAINING_STATUS_LABELS: Record<string, string> = {
  READY: '待训练',
  QUEUED: '已排队',
  PROCESSING: '训练中',
  COMPLETED: '已完成',
  FAILED: '训练失败',
  REJECTED: '已排除',
};

const DATASET_VERSION_STATUS_LABELS: Record<string, string> = {
  READY: '已就绪',
  READY_TO_REVIEW: '待评审',
  FROZEN: '已冻结',
  FAILED: '失败',
};

const WORKFLOW_STATUS_LABELS: Record<string, string> = {
  PENDING: '待处理',
  RUNNING: '运行中',
  SUCCEEDED: '已完成',
  FAILED: '失败',
  CANCELLED: '已取消',
};

function statusLabel(value: unknown, labels: Record<string, string>, fallback = '待确认') {
  const code = String(value || '').toUpperCase();
  return labels[code] || (code ? `待确认（${code}）` : fallback);
}

function isImportFailure(row: Row): boolean {
  const statusValues = [row.status, row.status_label, row.stage]
    .map((value) => String(value || '').trim().toUpperCase())
    .filter(Boolean);
  return Boolean(row.success === false)
    || statusValues.some((value) => /(?:FAILED|FAILURE|ERROR|失败|错误)/i.test(value));
}

const IMPORT_FAILURE_PREFIX = /^(?:专家回流数据集)?导入失败\s*[：:]\s*/i;
const GENERIC_IMPORT_FAILURE = /^(?:专家回流数据集)?导入失败$/i;

export interface ImportFailurePresentation {
  summary: string;
  technical: string;
}

function cleanImportFailureReason(value: unknown): string {
  return String(value || '').trim().replace(IMPORT_FAILURE_PREFIX, '').trim();
}

/** Convert common storage/import errors to an operator-facing conclusion. */
export function humanizeImportFailureReason(value: unknown): string {
  const reason = cleanImportFailureReason(value);
  if (!reason || GENERIC_IMPORT_FAILURE.test(reason)) return '未记录具体错误';

  if (/(?:connect(?:ion)?\s*(?:timeout|timed out)|连接(?:第三方)?(?:存储|服务)?(?:超时|失败))/i.test(reason)) {
    return '连接第三方存储服务超时，请检查服务地址和网络后重试';
  }
  if (/(?:read(?:ing)?\s*(?:timeout|timed out)|read timed out|读取(?:第三方)?(?:数据|文件|对象)?超时|请求超时)/i.test(reason)) {
    return '读取第三方数据超时，请检查网络或存储服务后重试';
  }
  if (/(?:connection refused|network is unreachable|name or service not known|temporary failure in name resolution|连接被拒绝|网络不可达|域名解析失败)/i.test(reason)) {
    return '无法连接第三方存储服务，请检查服务地址、端口和网络';
  }
  if (/(?:access denied|permission denied|forbidden|unauthorized|invalid access key|signature.*(?:mismatch|does not match)|权限不足|无权访问|访问被拒绝|认证失败|凭据无效|\b(?:401|403)\b)/i.test(reason)) {
    return '第三方存储访问被拒绝，请检查访问凭据和权限';
  }
  if (/(?:no such key|nosuchkey|not found|does not exist|对象不存在|文件不存在|找不到(?:第三方)?(?:数据|文件|对象)|\b404\b)/i.test(reason)) {
    return '找不到第三方数据文件，请确认对象路径和文件是否已上传';
  }
  if (/(?:missing.*(?:metadata|field|key)|required.*(?:metadata|field|key)|(?:metadata|field|key).*(?:missing|required)|keyerror|字段缺失|缺少.*字段|元数据(?:缺失|不完整)|清单不完整)/i.test(reason)) {
    return '第三方数据清单缺少必填信息，请补齐后重新提交';
  }
  if (/(?:json\s*(?:decode|parse)|invalid\s+json|malformed\s+json|json格式|json解析|格式不正确)/i.test(reason)) {
    return '第三方数据清单格式不正确，请检查 metadata 文件';
  }
  if (/(?:model.*(?:not found|missing|unknown|mismatch)|模型(?:不存在|未找到|未匹配|不匹配))/i.test(reason)) {
    return '未能匹配回流数据对应的模型，请确认模型编号';
  }
  return '第三方数据导入失败，请根据技术详情检查数据包和存储配置';
}

export function importFailurePresentation(row: Row): ImportFailurePresentation | null {
  if (!isImportFailure(row)) return null;

  const candidates = [row.last_error, row.status_detail, row.message, row.error_message]
    .map((value) => {
      const technical = String(value || '').trim();
      return { technical, reason: cleanImportFailureReason(technical) };
    })
    .filter(({ technical }) => Boolean(technical));
  const candidate = candidates.find(({ reason }) => reason && !GENERIC_IMPORT_FAILURE.test(reason)) || candidates[0];
  const technical = candidate?.technical || '未记录具体错误';
  return {
    summary: humanizeImportFailureReason(candidate?.reason || technical),
    technical,
  };
}

function importRetryJobId(row: Row | null | undefined): number | null {
  const value = Number(row?.import_job_id ?? row?.id);
  return Number.isInteger(value) && value > 0 ? value : null;
}

function importRetryPackageId(response: unknown): string | null {
  let value: unknown = response;
  for (let depth = 0; depth < 4; depth += 1) {
    if (!value || typeof value !== 'object' || !('data' in value)) break;
    value = (value as { data?: unknown }).data;
  }
  if (!value || typeof value !== 'object') return null;
  const payload = value as Record<string, unknown>;
  const packageId = payload.package_id ?? payload.packageId ?? payload.new_package_id ?? payload.newPackageId;
  const normalized = String(packageId || '').trim();
  return normalized || null;
}

function importRetryResponseMessage(value: unknown): string | null {
  let current: unknown = value;
  for (let depth = 0; depth < 4; depth += 1) {
    if (!current || typeof current !== 'object' || !('data' in current)) break;
    current = (current as { data?: unknown }).data;
  }
  if (!current || typeof current !== 'object') return null;
  const payload = current as Record<string, unknown>;
  const messageValue = payload.message ?? payload.msg ?? payload.detail;
  return typeof messageValue === 'string' && messageValue.trim()
    ? messageValue.trim()
    : null;
}

function importRetryErrorMessage(error: unknown): string {
  const response = (error as { response?: { status?: number; data?: unknown } } | null)?.response;
  const status = response?.status;
  const body = response?.data;
  const bodyRecord = body && typeof body === 'object' ? (body as Record<string, unknown>) : null;
  const nestedData = bodyRecord?.data;
  const nestedRecord = nestedData && typeof nestedData === 'object' ? (nestedData as Record<string, unknown>) : null;
  const candidates = [
    bodyRecord?.msg,
    bodyRecord?.message,
    bodyRecord?.detail,
    nestedRecord?.msg,
    nestedRecord?.message,
    nestedRecord?.detail,
    typeof body === 'string' ? body : null,
  ];
  const detail = candidates.find((candidate) => typeof candidate === 'string' && candidate.trim()) as string | undefined;
  if (detail) return detail.trim().slice(0, 240);

  if (status === 403) return '当前账号无权重新执行专家回流任务';
  if (status === 404) return '失败记录不存在，可能已被清理，请刷新列表';
  if (status === 409) return '该失败记录已被重新执行或状态已变化，请刷新列表';
  if (status === 422) return '当前失败记录不满足重新执行条件，请检查数据包状态';
  return '重新执行失败，请检查数据包状态和存储配置后重试';
}

function importArchiveResponseMessage(value: unknown): string | null {
  let current: unknown = value;
  for (let depth = 0; depth < 4; depth += 1) {
    if (!current || typeof current !== 'object' || !('data' in current)) break;
    current = (current as { data?: unknown }).data;
  }
  if (!current || typeof current !== 'object') return null;
  const payload = current as Record<string, unknown>;
  const messageValue = payload.message ?? payload.msg ?? payload.detail;
  return typeof messageValue === 'string' && messageValue.trim() ? messageValue.trim() : null;
}

function importArchiveErrorMessage(error: unknown): string {
  const response = (error as { response?: { status?: number; data?: unknown } } | null)?.response;
  const detail = importArchiveResponseMessage(response?.data);
  if (detail) return detail;

  if (response?.status === 403) return '当前账号无权删除失败记录';
  if (response?.status === 404) return '失败记录不存在或已归档，请刷新列表';
  if (response?.status === 409) return '该失败记录当前不能归档，请先处理关联的重试或任务';
  return '删除失败记录失败，请刷新列表后重试';
}

function EvolutionQueue({
  rows,
  total,
  loading,
  error,
  status,
  search,
  searchDraft,
  page,
  pageSize,
  onStatusChange,
  onSearchChange,
  onSearch,
  onPageChange,
  onRefresh,
}: {
  rows: Row[];
  total: number;
  loading: boolean;
  error: unknown;
  status: string;
  search: string;
  searchDraft: string;
  page: number;
  pageSize: number;
  onStatusChange: (value: string) => void;
  onSearchChange: (value: string) => void;
  onSearch: (value: string) => void;
  onPageChange: (page: number, pageSize: number) => void;
  onRefresh: () => void;
}) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Row | null>(null);
  const [decision, setDecision] = useState<'ACCEPTED' | 'REJECTED'>('ACCEPTED');
  const [reason, setReason] = useState('');
  useEffect(() => {
    if (!selected) return undefined;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      setSelected(null);
    };
    document.addEventListener('keydown', closeOnEscape, true);
    return () => document.removeEventListener('keydown', closeOnEscape, true);
  }, [selected]);
  const reasonMissing = reviewReasonMissing(decision, reason);
  const online = typeof navigator === 'undefined' || navigator.onLine;
  const { isTaskSuperuser } = useTaskSuperuser();
  const queueErrorState = evolutionErrorState(responseStatus(error), online);
  const detailQuery = useQuery({
    queryKey: ['edge-hard-example', selected?.id],
    queryFn: async () => {
      const response = await request.get(`/v1/edge-feedback/hard-examples/${selected?.id}`);
      return response.data?.data ?? response.data;
    },
    enabled: Boolean(selected?.id),
    staleTime: 5_000,
  });
  const detailErrorState = evolutionErrorState(responseStatus(detailQuery.error), online);
  const selectedDetail = detailQuery.data || selected;
  const selectedReviewStatus = String(selectedDetail?.review_status || selected?.review_status || '').toUpperCase();
  const isFinalized = selectedReviewStatus === 'ACCEPTED' || selectedReviewStatus === 'REJECTED';
  const reviewMutation = useMutation({
    mutationFn: async ({ id, decision: nextDecision, reason: nextReason }: { id: number; decision: string; reason?: string }) => {
      const response = await request.post(`/v1/edge-feedback/hard-examples/${id}/review`, {
        decision: nextDecision,
        reason: nextReason || undefined,
      });
      return response.data;
    },
    onSuccess: () => {
      message.success(decision === 'ACCEPTED' ? '难例已纳入训练队列' : '难例已排除');
      setSelected(null);
      setReason('');
      queryClient.invalidateQueries({ queryKey: ['edge-hard-examples'] });
    },
    onError: (mutationError) => {
      message.error(evolutionReviewErrorMessage(responseStatus(mutationError), online));
    },
  });
  const pendingCount = rows.filter((row) => row.review_status === 'PENDING').length;
  const readyCount = rows.filter((row) => row.training_status === 'READY').length;
  const columns: ColumnsType<Row> = [
    { title: '设备', dataIndex: 'device_id', key: 'device_id', width: 150, ellipsis: true },
    { title: '难例类型', dataIndex: 'hard_example_type', key: 'hard_example_type', render: (value) => <Tag color="blue">{statusLabel(value, HARD_EXAMPLE_TYPE_LABELS, '未分类')}</Tag> },
    { title: '置信度', dataIndex: 'confidence', key: 'confidence', render: (value) => (value == null ? '-' : `${value}%`) },
    { title: '审核状态', dataIndex: 'review_status', key: 'review_status', render: (value) => <Tag color={value === 'ACCEPTED' ? 'green' : value === 'REJECTED' ? 'red' : 'gold'}>{statusLabel(value, REVIEW_STATUS_LABELS)}</Tag> },
    { title: '训练状态', dataIndex: 'training_status', key: 'training_status', render: (value) => <Tag color={value === 'READY' ? 'cyan' : 'default'}>{statusLabel(value, TRAINING_STATUS_LABELS)}</Tag> },
    { title: '发现时间', dataIndex: 'created_at', key: 'created_at', render: (value) => (value ? new Date(value).toLocaleString() : '-') },
    {
      title: '操作', key: 'actions', fixed: 'right', width: 96,
      render: (_, row) => (
        <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => {
          setSelected(row);
          setDecision(row.review_status === 'REJECTED' ? 'REJECTED' : 'ACCEPTED');
          setReason(row.review_reason || '');
        }}>查看</Button>
      ),
    },
  ];

  return (
    <TablePanel>
      <h2>难例回流队列</h2>
      <p>审核边端低置信度和异常样本。接受后还需重建聚类、生成训练批次并冻结数据集版本，系统才会进入训练队列。</p>
      <QueueToolbar>
        <Space wrap>
          <ServerSearchInput
            value={searchDraft}
            placeholder="按设备、指纹、资源或模型编号搜索"
            ariaLabel="搜索边端难例"
            loading={loading}
            onChange={onSearchChange}
            onSearch={onSearch}
            style={{ width: 280, maxWidth: '100%' }}
          />
          <Select
            aria-label="审核状态筛选"
            value={status || 'ALL'}
            style={{ width: 150 }}
            options={[
              { label: '全部状态', value: 'ALL' },
              { label: '待审核', value: 'PENDING' },
              { label: '已接受', value: 'ACCEPTED' },
              { label: '已排除', value: 'REJECTED' },
            ]}
            onChange={(value) => onStatusChange(value === 'ALL' ? '' : value)}
          />
          <QueueHint>当前页 {rows.length} 条 / 共 {total} 条，待审核 {pendingCount} 条，训练就绪 {readyCount} 条</QueueHint>
        </Space>
        <Button icon={<ReloadOutlined />} loading={loading} onClick={onRefresh}>刷新队列</Button>
      </QueueToolbar>
      {error ? (
        <Alert
          style={{ marginTop: 14 }}
          type={queueErrorState === 'offline' ? 'warning' : 'error'}
          showIcon
          message={queueErrorState === 'forbidden' ? '无权限访问难例队列' : queueErrorState === 'missing' ? '难例队列不存在' : queueErrorState === 'conflict' ? '难例队列版本冲突' : queueErrorState === 'offline' ? '当前网络已断开' : '难例队列加载失败'}
          description={queueErrorState === 'forbidden' ? '当前账号无权查看回流队列，请联系管理人员。' : queueErrorState === 'conflict' ? '队列数据已变化，请刷新后重试。' : queueErrorState === 'offline' ? '恢复网络后重试，页面不会提交任何操作。' : '请稍后重试。'}
          action={<Button size="small" onClick={onRefresh}>重试</Button>}
        />
      ) : rows.length ? (
        <Table<Row>
          style={{ marginTop: 14 }}
          rowKey={(row) => String(row.id)}
          columns={columns}
          dataSource={rows}
          loading={loading}
          pagination={{
            current: page + 1,
            pageSize,
            total,
            showSizeChanger: true,
            onChange: (nextPage, nextPageSize) => onPageChange(nextPage - 1, nextPageSize),
          }}
          size="small"
          scroll={{ x: 850 }}
        />
      ) : loading ? (
        <div role="status" aria-live="polite" style={{ padding: '28px 0', textAlign: 'center' }}><Spin /></div>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={search ? '没有匹配的边端难例' : '当前筛选条件下暂无难例'} />
      )}
      <Modal
        title="审核难例"
        open={Boolean(selected)}
        centered
        keyboard
        okText={isFinalized || !isTaskSuperuser ? '关闭' : '提交审核'}
        cancelText="取消"
        confirmLoading={reviewMutation.isPending}
        okButtonProps={{ disabled: !selected || (!isFinalized && isTaskSuperuser && (reasonMissing || !online)) }}
        onCancel={() => setSelected(null)}
        onOk={() => {
          if (isFinalized || !isTaskSuperuser) {
            setSelected(null);
          } else if (selected?.id && !reasonMissing) {
            reviewMutation.mutate({ id: Number(selected.id), decision, reason: reason.trim() || undefined });
          }
        }}
      >
        {detailQuery.isLoading ? <div role="status" aria-live="polite"><Spin /></div> : detailQuery.error ? (
          <Alert
            type={detailErrorState === 'offline' ? 'warning' : 'error'}
            showIcon
            message={detailErrorState === 'forbidden' ? '无权限查看难例详情' : detailErrorState === 'missing' ? '难例详情不存在' : detailErrorState === 'conflict' ? '难例详情版本冲突' : detailErrorState === 'offline' ? '当前网络已断开' : '难例详情加载失败'}
            description={detailErrorState === 'offline' ? '恢复网络后重试，页面不会提交任何操作。' : detailErrorState === 'conflict' ? '详情已变化，请刷新后重试。' : '请刷新队列后重试。'}
            action={<Button size="small" icon={<ReloadOutlined />} onClick={() => detailQuery.refetch()}>重试</Button>}
          />
        ) : selectedDetail && (
          <>
            <DetailGrid>
              <dt>设备</dt><dd>{selectedDetail.device_id || '-'}</dd>
              <dt>难例类型</dt><dd>{selectedDetail.hard_example_type || '-'}</dd>
              <dt>置信度</dt><dd>{selectedDetail.confidence == null ? '-' : `${selectedDetail.confidence}%`}</dd>
              <dt>指纹</dt><dd>{selectedDetail.fingerprint || '-'}</dd>
              <dt>资源地址</dt><dd>{selectedDetail.asset_uri || '-'}</dd>
            </DetailGrid>
            <Select
              aria-label="审核决策"
              value={decision}
              disabled={isFinalized || !isTaskSuperuser}
              style={{ width: '100%', marginBottom: 12 }}
              options={[
                { label: '接受，纳入训练候选', value: 'ACCEPTED' },
                { label: '排除，不进入训练', value: 'REJECTED' },
              ]}
              onChange={setDecision}
            />
            <Input.TextArea
              aria-label="审核理由"
              aria-invalid={reasonMissing}
              value={reason}
              maxLength={2000}
              showCount
              rows={4}
              status={reasonMissing ? 'error' : undefined}
              placeholder="补充审核理由（可选）"
              onChange={(event) => setReason(event.target.value)}
              disabled={isFinalized || !isTaskSuperuser}
            />
            {isFinalized ? <div role="status" style={{ marginTop: 6, color: '#64748b', fontSize: 12 }}>该难例已完成审核，仅可查看历史结果。</div> : !isTaskSuperuser ? <div role="status" style={{ marginTop: 6, color: '#64748b', fontSize: 12 }}>全局难例由管理人员审核，当前账号仅可查看。</div> : reasonMissing && <div role="alert" style={{ marginTop: 6, color: '#cf1322', fontSize: 12 }}>排除难例必须填写理由</div>}
          </>
        )}
      </Modal>
    </TablePanel>
  );
}

function PipelinePanel() {
  const queryClient = useQueryClient();
  const [threshold, setThreshold] = useState(60);
  const [quota, setQuota] = useState(100);
  const [selectedClusterIds, setSelectedClusterIds] = useState<Key[]>([]);
  const [clusterSearch, setClusterSearch] = useState('');
  const [clusterSearchDraft, setClusterSearchDraft] = useState('');
  const [clusterPage, setClusterPage] = useState(0);
  const [clusterPageSize, setClusterPageSize] = useState(20);
  const [batchId, setBatchId] = useState<number>();
  const [workflowSearch, setWorkflowSearch] = useState('');
  const [workflowSearchDraft, setWorkflowSearchDraft] = useState('');
  const [workflowPage, setWorkflowPage] = useState(0);
  const [workflowPageSize, setWorkflowPageSize] = useState(20);
  const [versionSearch, setVersionSearch] = useState('');
  const [versionSearchDraft, setVersionSearchDraft] = useState('');
  const [versionPage, setVersionPage] = useState(0);
  const [versionPageSize, setVersionPageSize] = useState(20);

  const clustersQuery = useQuery({
    queryKey: ['active-learning-clusters', clusterSearch, clusterPage, clusterPageSize],
    queryFn: async () => {
      const response = await request.get('/v1/active-learning/clusters/search', {
        params: {
          page: clusterPage,
          size: clusterPageSize,
          ...(clusterSearch ? { q: clusterSearch } : {}),
        },
      });
      const rows = listData(response);
      return { rows, total: listTotal(response, rows.length) };
    },
    enabled: true,
    staleTime: 10_000,
  });
  const workflowsQuery = useQuery({
    queryKey: ['evolution-workflows', workflowSearch, workflowPage, workflowPageSize],
    queryFn: async () => {
      const response = await request.get('/v1/workflows', {
        params: {
          page: workflowPage,
          size: workflowPageSize,
          workflow_type: 'expert_feedback',
          ...(workflowSearch ? { q: workflowSearch } : {}),
        },
      });
      const rows = listData(response);
      return { rows, total: listTotal(response, rows.length) };
    },
    enabled: true,
    staleTime: 5_000,
    refetchInterval: 10_000,
  });
  const datasetsQuery = useQuery({
    queryKey: ['evolution-dataset-versions', versionSearch, versionPage, versionPageSize],
    queryFn: async () => {
      const response = await request.get('/v1/dataset-versions', {
        params: {
          page: versionPage,
          size: versionPageSize,
          active_learning_only: true,
          ...(versionSearch ? { q: versionSearch } : {}),
        },
      });
      const rows = listData(response);
      return { rows, total: listTotal(response, rows.length) };
    },
    enabled: true,
    staleTime: 5_000,
  });
  const batchQuery = useQuery({
    queryKey: ['active-learning-batch', batchId],
    queryFn: async () => (await request.get(`/v1/active-learning/batches/${batchId}`)).data,
    enabled: Boolean(batchId),
    staleTime: 3_000,
  });
  const rebuild = useMutation({
    mutationFn: async () => (await request.post('/v1/active-learning/clusters/rebuild', { confidence_threshold: threshold })).data,
    onSuccess: () => {
      setSelectedClusterIds([]);
      message.success('反馈聚类已重建');
      void clustersQuery.refetch();
    },
    onError: () => message.error('聚类重建失败，请检查管理权限或稍后重试'),
  });
  const createBatch = useMutation({
    mutationFn: async () => (await request.post('/v1/active-learning/batches', {
      quota,
      cluster_ids: selectedClusterIds.length ? selectedClusterIds.map(Number) : undefined,
    })).data,
    onSuccess: (response) => {
      const data = response?.data || response;
      setBatchId(Number(data.id));
      message.success(`已生成主动学习批次 #${data.id}`);
      void queryClient.invalidateQueries({ queryKey: ['evolution-dataset-versions'] });
    },
    onError: () => message.error('批次生成失败，请先确认已有已接受且未消费的难例'),
  });
  const acceptBatch = useMutation({
    mutationFn: async () => (await request.post(`/v1/active-learning/batches/${batchId}/accept`, { reason: '进化中心审核通过' })).data,
    onSuccess: () => {
      message.success('批次已接受，已创建数据集版本和专家回流工作流；冻结后才会进入训练队列');
      void batchQuery.refetch();
      void queryClient.invalidateQueries({ queryKey: ['evolution-dataset-versions'] });
      void queryClient.invalidateQueries({ queryKey: ['evolution-workflows'] });
    },
    onError: () => message.error('批次接受失败，可能存在审核状态变化，请刷新后重试'),
  });
  const freezeVersion = useMutation({
    mutationFn: async (versionId: number) => (await request.post(`/v1/dataset-versions/${versionId}/freeze`)).data,
    onSuccess: () => {
      message.success('数据集版本已冻结，关联工作流将自动推进');
      void datasetsQuery.refetch();
      void workflowsQuery.refetch();
    },
    onError: () => message.error('冻结失败：版本校验未通过或状态已变化'),
  });
  const clusters: Row[] = (clustersQuery.data?.rows || []) as Row[];
  const clusterTotal = clustersQuery.data?.total ?? 0;
  const versions: Row[] = (datasetsQuery.data?.rows || []) as Row[];
  const versionTotal = datasetsQuery.data?.total ?? 0;
  const workflows = workflowsQuery.data?.rows || [];
  const workflowTotal = workflowsQuery.data?.total ?? 0;
  const batch = batchQuery.data?.data || batchQuery.data;
  const pipelineBusy = rebuild.isPending || createBatch.isPending || acceptBatch.isPending || freezeVersion.isPending;
  const applyWorkflowSearch = (value: string) => {
    setWorkflowSearchDraft(value);
    setWorkflowSearch(value.trim());
    setWorkflowPage(0);
  };
  const applyVersionSearch = (value: string) => {
    setVersionSearchDraft(value);
    setVersionSearch(value.trim());
    setVersionPage(0);
  };
  const applyClusterSearch = (value: string) => {
    setClusterSearchDraft(value);
    setClusterSearch(value.trim());
    setClusterPage(0);
  };

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <TablePanel>
        <h2>主动学习编排</h2>
        <p>将边端难例按场景和设备聚类，生成可审计批次；接受后自动创建数据集版本，冻结后推进重训工作流。</p>
        <>
            <QueueToolbar>
              <Space wrap>
                <ServerSearchInput
                  value={clusterSearchDraft}
                  placeholder="按聚类、设备、模型、来源或状态搜索"
                  ariaLabel="搜索反馈聚类"
                  loading={clustersQuery.isLoading || clustersQuery.isFetching}
                  onChange={(value) => {
                    setClusterSearchDraft(value);
                    if (!value) applyClusterSearch('');
                  }}
                  onSearch={applyClusterSearch}
                  style={{ width: 320, maxWidth: '100%' }}
                />
                <QueueHint>当前页 {clusters.length} 条 / 共 {clusterTotal} 条</QueueHint>
                <span>置信度阈值</span>
                <InputNumber disabled={pipelineBusy} min={0} max={100} value={threshold} onChange={(value) => setThreshold(Number(value ?? 60))} />
                <Button icon={<SyncOutlined />} loading={rebuild.isPending} disabled={pipelineBusy} onClick={() => rebuild.mutate()}>重建聚类</Button>
                <span>批次配额</span>
                <InputNumber disabled={pipelineBusy} min={1} max={5000} value={quota} onChange={(value) => setQuota(Number(value ?? 100))} />
                <Button type="primary" icon={<PlusOutlined />} loading={createBatch.isPending} disabled={pipelineBusy || (!clusterTotal && !selectedClusterIds.length)} onClick={() => createBatch.mutate()}>生成训练批次</Button>
              </Space>
            </QueueToolbar>
            {clustersQuery.error ? <Alert style={{ marginTop: 14 }} type="error" showIcon message="聚类加载失败" action={<Button size="small" onClick={() => void clustersQuery.refetch()}>重试</Button>} /> : (
              <Table<Row>
                style={{ marginTop: 14 }}
                rowKey={(row) => String(row.id)}
                size="small"
                loading={clustersQuery.isLoading || clustersQuery.isFetching}
                dataSource={clusters}
                rowSelection={{ selectedRowKeys: selectedClusterIds, preserveSelectedRowKeys: true, onChange: setSelectedClusterIds, getCheckboxProps: () => ({ disabled: pipelineBusy }) }}
                columns={[
                  { title: '聚类', dataIndex: 'cluster_key', key: 'cluster_key', ellipsis: true },
                  { title: '设备', dataIndex: 'device_id', key: 'device_id' },
                  { title: '样本数', dataIndex: 'sample_count', key: 'sample_count' },
                  { title: '平均置信度', dataIndex: 'average_confidence', key: 'average_confidence', render: (value) => value == null ? '-' : `${value}%` },
                  { title: '优先级', dataIndex: 'priority_score', key: 'priority_score', sorter: (a, b) => Number(a.priority_score) - Number(b.priority_score) },
                  { title: '状态', dataIndex: 'status', key: 'status', render: (value) => <Tag color={value === 'READY' ? 'green' : 'default'}>{statusLabel(value, TRAINING_STATUS_LABELS)}</Tag> },
                ] as ColumnsType<Row>}
                pagination={{
                  current: clusterPage + 1,
                  pageSize: clusterPageSize,
                  total: clusterTotal,
                  showSizeChanger: true,
                  onChange: (nextPage, nextPageSize) => {
                    if (nextPageSize !== clusterPageSize) {
                      setClusterPageSize(nextPageSize);
                      setClusterPage(0);
                      return;
                    }
                    setClusterPage(Math.max(0, nextPage - 1));
                  },
                }}
                locale={{ emptyText: clusterSearch ? '没有匹配的反馈聚类' : '暂无聚类，请先重建' }}
              />
            )}
        </>
      </TablePanel>

      <TablePanel>
        <h2>当前批次</h2>
        {!batchId ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="生成批次后在此查看批次快照" /> : batchQuery.isLoading ? <Spin /> : batchQuery.error ? <Alert type="error" showIcon message="批次详情加载失败" /> : (
          <DescriptionsLike data={batch} onAccept={() => acceptBatch.mutate()} accepting={acceptBatch.isPending} disabled={pipelineBusy} />
        )}
      </TablePanel>

      <TablePanel>
        <h2>数据集版本与冻结</h2>
        <QueueToolbar>
          <Space wrap>
            <ServerSearchInput
              value={versionSearchDraft}
              placeholder="按数据集、版本号、状态或清单搜索"
              ariaLabel="搜索数据集版本"
              loading={datasetsQuery.isLoading || datasetsQuery.isFetching}
              onChange={(value) => {
                setVersionSearchDraft(value);
                if (!value) applyVersionSearch('');
              }}
              onSearch={applyVersionSearch}
              style={{ width: 320, maxWidth: '100%' }}
            />
            <QueueHint>当前页 {versions.length} 条 / 共 {versionTotal} 条</QueueHint>
          </Space>
          <Button icon={<ReloadOutlined />} loading={datasetsQuery.isFetching} onClick={() => void datasetsQuery.refetch()}>刷新版本</Button>
        </QueueToolbar>
        {datasetsQuery.error ? <Alert style={{ marginTop: 14 }} type="error" showIcon message="数据集版本加载失败" description="请检查登录状态或刷新页面重试。" action={<Button size="small" onClick={() => void datasetsQuery.refetch()}>重试</Button>} /> : <Table<Row>
          rowKey={(row) => String(row.id)}
          size="small"
          loading={datasetsQuery.isLoading || datasetsQuery.isFetching}
          dataSource={versions}
          columns={[
            { title: '数据集', dataIndex: 'dataset_name', key: 'dataset_name', ellipsis: true },
            { title: '版本', key: 'version', render: (_, row) => `v${row.version_no} (#${row.id})` },
            { title: '样本数', dataIndex: 'sample_count', key: 'sample_count' },
            { title: '质量状态', dataIndex: 'quality_status', key: 'quality_status' },
            { title: '版本状态', dataIndex: 'status', key: 'status', render: (value) => <Tag color={value === 'FROZEN' ? 'green' : value === 'READY_TO_REVIEW' ? 'gold' : 'default'}>{statusLabel(value, DATASET_VERSION_STATUS_LABELS)}</Tag> },
            { title: '操作', key: 'actions', render: (_, row) => row.status === 'READY_TO_REVIEW' ? <Button size="small" icon={<CheckOutlined />} loading={freezeVersion.isPending} disabled={pipelineBusy} onClick={() => freezeVersion.mutate(Number(row.id))}>校验并冻结</Button> : row.frozen_at ? new Date(row.frozen_at).toLocaleString() : '-' },
          ] as ColumnsType<Row>}
          pagination={{
            current: versionPage + 1,
            pageSize: versionPageSize,
            total: versionTotal,
            showSizeChanger: true,
            onChange: (nextPage, nextPageSize) => {
              if (nextPageSize !== versionPageSize) {
                setVersionPageSize(nextPageSize);
                setVersionPage(0);
                return;
              }
              setVersionPage(Math.max(0, nextPage - 1));
            },
          }}
          locale={{ emptyText: versionSearch ? '没有匹配的数据集版本' : '接受批次后自动生成数据集版本' }}
        />}
      </TablePanel>

      <TablePanel>
        <h2>关联重训工作流</h2>
        <QueueToolbar>
          <Space wrap>
            <ServerSearchInput
              value={workflowSearchDraft}
              placeholder="按工作流编号、状态、步骤或错误信息搜索"
              ariaLabel="搜索重训工作流"
              loading={workflowsQuery.isLoading || workflowsQuery.isFetching}
              onChange={(value) => {
                setWorkflowSearchDraft(value);
                if (!value) applyWorkflowSearch('');
              }}
              onSearch={applyWorkflowSearch}
              style={{ width: 320, maxWidth: '100%' }}
            />
            <QueueHint>当前页 {workflows.length} 条 / 共 {workflowTotal} 条</QueueHint>
          </Space>
          <Button icon={<ReloadOutlined />} loading={workflowsQuery.isFetching} onClick={() => void workflowsQuery.refetch()}>刷新工作流</Button>
        </QueueToolbar>
        <Table<Row>
          rowKey={(row) => String(row.id)}
          size="small"
          loading={workflowsQuery.isLoading || workflowsQuery.isFetching}
          dataSource={workflows}
          columns={[
            { title: '工作流', key: 'workflow', render: (_, row) => `#${row.id}` },
            { title: '状态', dataIndex: 'status', key: 'status', render: (value) => <Tag color={value === 'SUCCEEDED' ? 'green' : value === 'FAILED' ? 'red' : 'processing'}>{statusLabel(value, WORKFLOW_STATUS_LABELS)}</Tag> },
            { title: '当前步骤', dataIndex: 'current_step', key: 'current_step' },
            { title: '最近错误', dataIndex: 'last_error', key: 'last_error', ellipsis: true },
            { title: '更新时间', dataIndex: 'updated_at', key: 'updated_at', render: (value) => value ? new Date(value).toLocaleString() : '-' },
          ] as ColumnsType<Row>}
          pagination={{
            current: workflowPage + 1,
            pageSize: workflowPageSize,
            total: workflowTotal,
            showSizeChanger: true,
            onChange: (nextPage, nextPageSize) => {
              if (nextPageSize !== workflowPageSize) {
                setWorkflowPageSize(nextPageSize);
                setWorkflowPage(0);
                return;
              }
              setWorkflowPage(Math.max(0, nextPage - 1));
            },
          }}
          locale={{ emptyText: workflowSearch ? '没有匹配的专家回流工作流' : '暂无专家回流工作流' }}
        />
      </TablePanel>
    </Space>
  );
}

function DescriptionsLike({ data, onAccept, accepting, disabled }: { data: Row; onAccept: () => void; accepting: boolean; disabled: boolean }) {
  return (
    <>
      <DetailGrid>
        <dt>批次</dt><dd>#{data?.id}</dd>
        <dt>状态</dt><dd><Tag color={data?.status === 'ACCEPTED' ? 'green' : 'gold'}>{statusLabel(data?.status, { ...REVIEW_STATUS_LABELS, READY: '待处理' })}</Tag></dd>
        <dt>选择数量</dt><dd>{data?.selected_count ?? 0} / {data?.quota ?? 0}</dd>
        <dt>数据集版本</dt><dd>{data?.dataset_version_id ? `#${data.dataset_version_id}` : '接受后生成'}</dd>
        <dt>工作流</dt><dd>{data?.workflow_id ? `#${data.workflow_id}` : '接受后生成'}</dd>
      </DetailGrid>
      {data?.status === 'READY' && <Button type="primary" icon={<CheckOutlined />} loading={accepting} disabled={disabled} onClick={onAccept}>接受批次并创建工作流</Button>}
    </>
  );
}

function ThirdPartyImportsPanel() {
  const [search, setSearch] = useState('');
  const [searchDraft, setSearchDraft] = useState('');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(20);
  const [retryTarget, setRetryTarget] = useState<Row | null>(null);
  const [archiveTarget, setArchiveTarget] = useState<Row | null>(null);
  const { isTaskSuperuser } = useTaskSuperuser();
  const query = useQuery({
    queryKey: ['evolution-third-party-imports', search, page, pageSize],
    queryFn: async () => {
      const response = await request.get('/v1/evolution/imports', {
        params: {
          page,
          size: pageSize,
          ...(search ? { q: search } : {}),
        },
      });
      // `request` returns the business envelope in production; test adapters
      // may still return an Axios-shaped `{ data: envelope }`. The shared
      // parsers accept both shapes, so never unwrap `.data` prematurely here.
      const rows = listData(response);
      return { rows, total: listTotal(response, rows.length) };
    },
    staleTime: 5_000,
    refetchInterval: 10_000,
  });
  const retryMutation = useMutation({
    mutationFn: async (importJobId: number) => request.post(`/v1/evolution/imports/${importJobId}/retry`, {}),
    onSuccess: (response) => {
      const packageId = importRetryPackageId(response);
      const detail = importRetryResponseMessage(response);
      setRetryTarget(null);
      message.success(
        detail
          ? (packageId && !detail.includes(packageId) ? `${detail}（数据包：${packageId}）` : detail)
          : (packageId ? `已提交重新执行，新数据包：${packageId}` : '已提交重新执行，请刷新列表查看最新状态'),
      );
      void query.refetch();
    },
    onError: (error) => {
      setRetryTarget(null);
      message.error(importRetryErrorMessage(error));
    },
  });
  const archiveMutation = useMutation({
    mutationFn: async (importJobId: number) => request.delete(`/v1/evolution/imports/${importJobId}`),
    onSuccess: (response) => {
      const detail = importArchiveResponseMessage(response);
      setArchiveTarget(null);
      message.success(detail || '失败记录已归档，原始失败原因仍保留在历史记录中');
      void query.refetch();
    },
    onError: (error) => {
      setArchiveTarget(null);
      message.error(importArchiveErrorMessage(error));
    },
  });
  const rows = (query.data?.rows || []) as Row[];
  const total = query.data?.total ?? 0;
  const applySearch = (value: string) => {
    const normalized = value.trim();
    setSearchDraft(value);
    setSearch(normalized);
    setPage(0);
  };
  const columns: ColumnsType<Row> = [
    { title: '数据包', dataIndex: 'package_id', key: 'package_id', ellipsis: true },
    { title: '日期', dataIndex: 'dataset_date', key: 'dataset_date' },
    { title: '模型', key: 'model_id', render: (_, row) => row.modelId || row.model_id || '未提供' },
    { title: '有效图片', key: 'valid', render: (_, row) => `${row.valid_sample_count ?? 0} / ${row.required_sample_count ?? 50}` },
    {
      title: '状态',
      dataIndex: 'status_label',
      key: 'status_label',
      render: (value, row) => {
        return <Tag color={isImportFailure(row) ? 'red' : 'gold'}>{value || row.status || '待确认'}</Tag>;
      },
    },
    {
      title: '失败原因',
      key: 'failure_reason',
      width: 340,
      render: (_, row) => {
        const presentation = importFailurePresentation(row);
        if (!presentation) return <Typography.Text type="secondary">-</Typography.Text>;
        const failureDetail = `原因：${presentation.summary}；技术详情：${presentation.technical}`;
        return (
          <Tooltip title={failureDetail} placement="topLeft">
            <Typography.Text
              type="danger"
              title={failureDetail}
              style={{ display: 'block', maxWidth: 320, whiteSpace: 'normal', wordBreak: 'break-word', lineHeight: 1.5 }}
            >
              {presentation.summary}
            </Typography.Text>
          </Tooltip>
        );
      },
    },
    { title: '正式任务', key: 'task', render: (_, row) => row.formal_task_ids?.length ? row.formal_task_ids.map((id: number) => `#${id}`).join('、') : row.draft_task_id ? `草稿 #${row.draft_task_id}` : '未创建' },
    {
      title: '操作',
      key: 'actions',
      width: 240,
      render: (_, row) => {
        if (!isTaskSuperuser || !isImportFailure(row)) return <Typography.Text type="secondary">-</Typography.Text>;
        const importJobId = importRetryJobId(row);
        return (
          <Space size={0} wrap>
            <Button
              type="link"
              size="small"
              icon={<ReloadOutlined />}
              loading={retryMutation.isPending && retryMutation.variables === importJobId}
              disabled={retryMutation.isPending || archiveMutation.isPending || importJobId === null}
              title={importJobId === null ? '失败记录缺少任务编号，无法重新执行' : '重新执行此失败的数据包'}
              onClick={() => setRetryTarget(row)}
            >
              重新执行
            </Button>
            <Button
              type="link"
              danger
              size="small"
              icon={<DeleteOutlined />}
              loading={archiveMutation.isPending && archiveMutation.variables === importJobId}
              disabled={retryMutation.isPending || archiveMutation.isPending || importJobId === null}
              title={importJobId === null ? '失败记录缺少任务编号，无法归档' : '删除失败记录（仅软删除，保留历史审计）'}
              onClick={() => setArchiveTarget(row)}
            >
              删除失败记录
            </Button>
          </Space>
        );
      },
    },
  ];
  return <TablePanel>
    <h2>专家回流任务</h2>
    <p>这里展示第三方专家回流请求。数据包先进入累计，达到 50 张后才自动创建正式标注任务。</p>
    <QueueToolbar>
      <Space wrap>
        <ServerSearchInput
          value={searchDraft}
          placeholder="按数据包、来源、日期、模型或状态搜索"
          ariaLabel="搜索专家回流任务"
          loading={query.isLoading || query.isFetching}
          onChange={(value) => {
            setSearchDraft(value);
            if (!value) applySearch('');
          }}
          onSearch={applySearch}
          style={{ width: 320, maxWidth: '100%' }}
        />
        <QueueHint>当前页 {rows.length} 条 / 共 {total} 条</QueueHint>
      </Space>
      <Button icon={<ReloadOutlined />} loading={query.isFetching} onClick={() => void query.refetch()}>刷新回流任务</Button>
    </QueueToolbar>
    {query.error ? <Alert type="error" showIcon message="专家回流任务加载失败" description="请检查登录状态或刷新页面重试。" action={<Button size="small" onClick={() => void query.refetch()}>重试</Button>} /> : <Table<Row>
      rowKey={(row) => String(row.package_id)}
      columns={columns}
      dataSource={rows}
      loading={query.isLoading || query.isFetching}
      pagination={{
        current: page + 1,
        pageSize,
        total,
        showSizeChanger: true,
        onChange: (nextPage, nextPageSize) => {
          if (nextPageSize !== pageSize) {
            setPageSize(nextPageSize);
            setPage(0);
            return;
          }
          setPage(Math.max(0, nextPage - 1));
        },
      }}
      scroll={{ x: 900 }}
      locale={{ emptyText: search ? '没有匹配的专家回流任务' : '暂无专家回流任务' }}
    />}
    <Modal
      title="确认重新执行专家回流任务"
      open={Boolean(retryTarget)}
      okText="重新执行"
      cancelText="取消"
      confirmLoading={retryMutation.isPending}
      onCancel={() => {
        if (!retryMutation.isPending) setRetryTarget(null);
      }}
      onOk={() => {
        const importJobId = importRetryJobId(retryTarget);
        if (importJobId === null) {
          setRetryTarget(null);
          message.error('失败记录缺少任务编号，无法重新执行');
          return;
        }
        retryMutation.mutate(importJobId);
      }}
    >
      <p>
        将按原数据包配置重新提交此次失败记录。系统会创建新的数据包编号，原失败记录保留用于审计。
      </p>
      {retryTarget?.package_id ? <p>原数据包：{String(retryTarget.package_id)}</p> : null}
    </Modal>
    <Modal
      title="确认删除失败记录"
      open={Boolean(archiveTarget)}
      okText="删除失败记录"
      okButtonProps={{ danger: true }}
      cancelText="取消"
      confirmLoading={archiveMutation.isPending}
      onCancel={() => {
        if (!archiveMutation.isPending) setArchiveTarget(null);
      }}
      onOk={() => {
        const importJobId = importRetryJobId(archiveTarget);
        if (importJobId === null) {
          setArchiveTarget(null);
          message.error('失败记录缺少任务编号，无法归档');
          return;
        }
        archiveMutation.mutate(importJobId);
      }}
    >
      <p>此操作只会从当前列表隐藏失败记录，数据库仍保留原始失败原因和审计记录。</p>
      <p>如需再次创建，请使用新的数据包编号，不能复用原来的 package_id。</p>
      {archiveTarget?.package_id ? <p>原数据包：{String(archiveTarget.package_id)}</p> : null}
    </Modal>
  </TablePanel>;
}

export function summarizeImportGroups(rows: Row[]) {
  const groups = Array.from(
    rows.reduce((map, row) => {
      const key = String(row.aggregation_key || row.package_id || '');
      const current = map.get(key);
      const currentCount = Number(current?.valid_sample_count || 0);
      const rowCount = Number(row.valid_sample_count || 0);
      const statusRank = (value: unknown) => ['COMPLETED', 'PARTIAL_COMPLETED'].includes(String(value || '').toUpperCase()) ? 1 : 0;
      if (!current || rowCount > currentCount || (rowCount === currentCount && statusRank(row.status) > statusRank(current.status))) {
        map.set(key, row);
      }
      return map;
    }, new Map<string, Row>()).values(),
  );
  return {
    groups,
    total: groups.reduce((sum, row) => sum + Number(row.valid_sample_count || 0), 0),
    ready: groups.filter((row) => ['COMPLETED', 'PARTIAL_COMPLETED'].includes(String(row.status || '').toUpperCase())).length,
  };
}

export default function EvolutionPage() {
  const [status, setStatus] = useState('');
  const [search, setSearch] = useState('');
  const [searchDraft, setSearchDraft] = useState('');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(20);
  const { isTaskSuperuser } = useTaskSuperuser();
  const query = useQuery({
    queryKey: ['edge-hard-examples', status, search, page, pageSize],
    queryFn: async () => {
      const response = await request.get('/v1/edge-feedback/hard-examples', {
        params: {
          page,
          size: pageSize,
          ...(status ? { status } : {}),
          ...(search ? { q: search } : {}),
        },
      });
      const rows = listData(response);
      return { rows, total: listTotal(response, rows.length) };
    },
    enabled: true,
    staleTime: 15_000,
  });
  const importsQuery = useQuery({
    queryKey: ['evolution-third-party-import-count'],
    queryFn: async () => {
      const response = await request.get('/v1/evolution/imports', { params: { page: 0, size: 1 } });
      const rows = listData(response);
      return { rows, total: listTotal(response, rows.length) };
    },
    staleTime: 5_000,
    refetchInterval: 10_000,
  });
  const moduleLoading = query.isLoading || importsQuery.isLoading;
  const moduleError = query.error || importsQuery.error;
  const hardExampleRows = query.data?.rows || [];
  const hardExampleTotal = query.data?.total ?? 0;
  const applySearch = (value: string) => {
    setSearchDraft(value);
    setSearch(value.trim());
    setPage(0);
  };

  return (
    <>
      <ModuleHeader module="evolution" />
      <ModuleStatus module="evolution" dataState={moduleLoading ? 'loading' : moduleError ? 'error' : 'ready'} rowCount={hardExampleRows.length + (importsQuery.data?.total || 0)} />
      {/*
       * Third-party S3 imports are the primary data source for this module.
       * Opening the review queue by default made a successful import look like
       * "no data" because the imported packages were hidden in the second tab.
       * Keep the review and orchestration tabs available, but land on the
       * package list so the user can immediately see the received data and its
       * 50-image accumulation state.
       */}
      <Tabs defaultActiveKey="third-party" items={[{
        key: 'review', label: '难例审核', children: <EvolutionQueue
          rows={hardExampleRows}
          total={hardExampleTotal}
          loading={query.isLoading || query.isFetching}
          error={query.error}
          status={status}
          search={search}
          searchDraft={searchDraft}
          page={page}
          pageSize={pageSize}
          onStatusChange={(value) => {
            setStatus(value);
            setPage(0);
          }}
          onSearchChange={(value) => {
            setSearchDraft(value);
            if (!value) applySearch('');
          }}
          onSearch={applySearch}
          onPageChange={(nextPage, nextPageSize) => {
            if (nextPageSize !== pageSize) {
              setPageSize(nextPageSize);
              setPage(0);
              return;
            }
            setPage(Math.max(0, nextPage));
          }}
          onRefresh={() => { void query.refetch(); }}
        />,
      }, {
        key: 'third-party', label: '专家回流任务', children: <ThirdPartyImportsPanel />,
      }, ...(isTaskSuperuser ? [{
        key: 'pipeline', label: '回流编排', children: <PipelinePanel />,
      }] : [])]} />
    </>
  );
}
