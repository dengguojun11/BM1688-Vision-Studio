import { DeleteOutlined, ReloadOutlined } from '@ant-design/icons';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Alert, Button, Modal, Space, Table, Tabs, Tag, Tooltip, Typography, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useEffect, useState, type Key } from 'react';

import request from '@/api/request';
import ServerSearchInput from '@/components/ServerSearchInput';
import useTaskSuperuser from '@/hooks/useTaskSuperuser';

import IntegrationRequestsPanel from './integrationCenter';

import {
  listData,
  listTotal,
  ModuleHeader,
  ModuleStatus,
  QueueHint,
  QueueToolbar,
  type Row,
  TablePanel,
} from '../shared';

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
  const moduleLoading = importsQuery.isLoading;
  const moduleError = importsQuery.error;

  return (
    <>
      <ModuleHeader module="evolution" />
      <ModuleStatus module="evolution" dataState={moduleLoading ? 'loading' : moduleError ? 'error' : 'ready'} rowCount={importsQuery.data?.total || 0} />
      {/*
       * 第三方问题样本回流与模型优化请求是本模块的业务主线。
       * 旧版“难例审核”来自边端推理事件，“回流编排”来自主动学习批次，
       * 两者均不属于第三方优化请求链路，已从本页面移除；对应后端能力保留，
       * 供其原有内部流程使用。
       */}
      <Tabs defaultActiveKey="integration" items={[{
        key: 'integration',
        label: '模型优化请求',
        children: <IntegrationRequestsPanel />,
      }, {
        key: 'third-party', label: '专家回流任务', children: <ThirdPartyImportsPanel />,
      }]} />
    </>
  );
}
