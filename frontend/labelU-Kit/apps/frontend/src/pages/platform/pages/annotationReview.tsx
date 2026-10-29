import { CheckOutlined, EditOutlined, EyeOutlined, ReloadOutlined, UploadOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Descriptions, Empty, Input, Modal, Select, Space, Spin, Table, Tag, Tooltip, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type React from 'react';
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import request from '@/api/request';
import ServerSearchInput from '@/components/ServerSearchInput';
import {
  buildAnnotationCanvasPath,
  canBatchTransition,
  summarizeBatchResults,
  type ReviewBatchTargetStatus,
} from '../reviewBatch';
import { reviewReasonMissing } from '../routing';
import {
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

function AnnotationReviewQueue({
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
  const navigate = useNavigate();
  const [selected, setSelected] = useState<Row | null>(null);
  const [targetStatus, setTargetStatus] = useState('IN_REVIEW');
  const [reason, setReason] = useState('');
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [batchTarget, setBatchTarget] = useState<ReviewBatchTargetStatus | null>(null);
  const [batchReason, setBatchReason] = useState('');
  const [batchLoading, setBatchLoading] = useState(false);
  const reasonMissing = reviewReasonMissing(targetStatus, reason);
  const selectedRows = rows.filter(
    (row) => row.id != null && selectedRowKeys.some((key) => String(key) === String(row.id)),
  );
  const selectedIds = selectedRowKeys.filter((key): key is string | number => typeof key === 'string' || typeof key === 'number');
  const canBatchEnterReview = canBatchTransition(rows, selectedIds, 'IN_REVIEW');
  const canBatchApprove = canBatchTransition(rows, selectedIds, 'APPROVED');
  const canBatchReject = canBatchTransition(rows, selectedIds, 'REJECTED');
  const batchReasonMissing = reviewReasonMissing(batchTarget || 'APPROVED', batchReason);
  const canvasPath = selected
    ? buildAnnotationCanvasPath(selected.task_id, selected.sample_id)
    : null;

  useEffect(() => {
    setSelectedRowKeys((current) =>
      current.filter((key) => rows.some((row) => row.id != null && String(row.id) === String(key))),
    );
  }, [rows, status]);

  const handleStatusChange = (value: string) => {
    setSelectedRowKeys([]);
    setBatchTarget(null);
    setBatchReason('');
    onStatusChange(value);
  };
  const transitionMutation = useMutation({
    mutationFn: async ({ id, target_status, nextReason }: { id: number; target_status: string; nextReason?: string }) => {
      const response = await request.post(`/v1/annotation-reviews/${id}/transition`, {
        target_status,
        reason: nextReason || undefined,
        ...(target_status === 'ARBITRATED' ? { arbitration_decision: 'APPROVED' } : {}),
      });
      return response.data;
    },
    onSuccess: () => {
      message.success('审核状态已更新');
      setSelected(null);
      setReason('');
      queryClient.invalidateQueries({ queryKey: ['annotation-reviews'] });
    },
  });

  const executeBatch = async (nextTarget: ReviewBatchTargetStatus, nextReason?: string) => {
    if (!selectedRows.length || !canBatchTransition(rows, selectedIds, nextTarget)) return;
    setBatchLoading(true);
    const settled = await Promise.allSettled(
      selectedRows.map(async (row) => {
        await request.post(`/v1/annotation-reviews/${row.id}/transition`, {
          target_status: nextTarget,
          reason: nextReason || undefined,
        });
        return row.id as number | string;
      }),
    );
    const summary = summarizeBatchResults(
      settled.map((result, index) => ({
        id: selectedRows[index].id as number | string,
        ok: result.status === 'fulfilled',
        error: result.status === 'rejected' ? result.reason : undefined,
      })),
    );
    setBatchLoading(false);
    setBatchTarget(null);
    setBatchReason('');
    setSelectedRowKeys(summary.failedIds);
    await queryClient.invalidateQueries({ queryKey: ['annotation-reviews'] });
    if (summary.failed === 0) {
      message.success(`已批量处理 ${summary.succeeded} 条审核记录`);
    } else {
      message.warning(`批量处理完成：成功 ${summary.succeeded} 条，失败 ${summary.failed} 条，失败项仍保持选中`);
    }
  };
  const columns: ColumnsType<Row> = [
    { title: '任务', dataIndex: 'task_id', key: 'task_id', width: 90 },
    { title: '样本', dataIndex: 'sample_id', key: 'sample_id', width: 90 },
    { title: '来源', dataIndex: 'source_type', key: 'source_type', render: (value) => <Tag>{value}</Tag> },
    { title: '状态', dataIndex: 'review_status', key: 'review_status', render: (value) => <Tag color={value === 'APPROVED' ? 'green' : value === 'REJECTED' ? 'red' : 'gold'}>{value}</Tag> },
    { title: '更新时间', dataIndex: 'updated_at', key: 'updated_at', render: (value) => (value ? new Date(value).toLocaleString() : '-') },
    {
      title: '操作', key: 'actions', width: 88, fixed: 'right',
      render: (_, row) => <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => {
        setSelected(row);
        setTargetStatus(row.review_status === 'PENDING' ? 'IN_REVIEW' : row.review_status === 'IN_REVIEW' ? 'APPROVED' : 'INVALIDATED');
        setReason(row.review_reason || '');
      }}>处理</Button>,
    },
  ];

  return (
    <TablePanel>
      <h2>标注审核队列</h2>
      <p>审核记录绑定具体标注快照，状态变更由后端状态机和当前账号权限控制。</p>
      <QueueToolbar>
        <Space wrap>
          <ServerSearchInput
            value={searchDraft}
            placeholder="按任务、样本或来源搜索"
            ariaLabel="搜索标注审核"
            loading={loading}
            onChange={onSearchChange}
            onSearch={onSearch}
          />
          <Select
            aria-label="审核状态筛选"
            value={status || 'ALL'}
            style={{ width: 150 }}
            options={[
              { label: '全部状态', value: 'ALL' },
              { label: '待处理', value: 'PENDING' },
              { label: '处理中', value: 'IN_REVIEW' },
              { label: '已通过', value: 'APPROVED' },
              { label: '已驳回', value: 'REJECTED' },
            ]}
            onChange={(value) => handleStatusChange(value === 'ALL' ? '' : value)}
          />
          <QueueHint>当前页 {rows.length} 条 / 共 {total} 条审核记录，已选 {selectedRowKeys.length} 条</QueueHint>
        </Space>
        <Space wrap>
          <Tooltip title="仅当所选记录状态一致时可用">
            <Button size="small" disabled={!canBatchEnterReview || batchLoading} loading={batchLoading && batchTarget === 'IN_REVIEW'} onClick={() => {
              setBatchTarget('IN_REVIEW');
              void executeBatch('IN_REVIEW');
            }}>批量进入审核</Button>
          </Tooltip>
          <Button size="small" type="primary" disabled={!canBatchApprove || batchLoading} loading={batchLoading && batchTarget === 'APPROVED'} onClick={() => {
            setBatchTarget('APPROVED');
            void executeBatch('APPROVED');
          }}>批量通过</Button>
          <Button size="small" danger disabled={!canBatchReject || batchLoading} loading={batchLoading && batchTarget === 'REJECTED'} onClick={() => {
            setBatchReason('');
            setBatchTarget('REJECTED');
          }}>批量驳回</Button>
          <Button icon={<ReloadOutlined />} loading={loading || batchLoading} onClick={onRefresh}>刷新队列</Button>
        </Space>
      </QueueToolbar>
      {error ? (
        <Alert
          style={{ marginTop: 14 }}
          type="error"
          showIcon
          message={responseStatus(error) === 403 ? '无权限访问审核队列' : '审核队列加载失败'}
          description={responseStatus(error) === 403 ? '当前账号无权查看该队列，请联系管理人员。' : '请稍后重试。'}
          action={<Button size="small" onClick={onRefresh}>重试</Button>}
        />
      ) : rows.length ? (
        <Table<Row>
          style={{ marginTop: 14 }}
          rowKey={(row) => String(row.id)}
          rowSelection={{
            selectedRowKeys,
            onChange: (keys) => setSelectedRowKeys(keys),
            getCheckboxProps: (row) => ({
              disabled: !['PENDING', 'IN_REVIEW'].includes(String(row.review_status || '').toUpperCase()),
            }),
          }}
          columns={columns}
          dataSource={rows}
          loading={loading || batchLoading}
          pagination={{
            current: page + 1,
            pageSize,
            total,
            showSizeChanger: true,
            showTotal: (value, range) => `${range[0]}-${range[1]} / ${value}`,
            onChange: (nextPage, nextPageSize) => onPageChange(nextPage - 1, nextPageSize),
          }}
          size="small"
          scroll={{ x: 720 }}
        />
      ) : loading ? (
        <div role="status" aria-live="polite" style={{ padding: '28px 0', textAlign: 'center' }}><Spin /></div>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={
          <Space direction="vertical" size={6}>
            <span>{search ? '没有匹配的审核记录' : '当前筛选条件下暂无审核记录'}</span>
            <Space size={12}>
              <Button type="link" size="small" icon={<EditOutlined />} onClick={() => navigate('/tasks')}>进入标注任务</Button>
              <Button type="link" size="small" icon={<UploadOutlined />} onClick={() => navigate('/ingestion')}>去数据接入</Button>
            </Space>
          </Space>
        } />
      )}
      <Modal
        title="处理标注审核"
        open={Boolean(selected)}
        centered
        okText="提交"
        cancelText="取消"
        confirmLoading={transitionMutation.isPending}
        okButtonProps={{ disabled: !selected || reasonMissing }}
        onCancel={() => setSelected(null)}
        onOk={() => {
          if (!selected?.id || reasonMissing) return;
          transitionMutation.mutate({ id: Number(selected.id), target_status: targetStatus, nextReason: reason.trim() || undefined });
        }}
      >
        <Descriptions column={1} size="small" bordered>
          <Descriptions.Item label="任务 / 样本">{selected?.task_id || '-'} / {selected?.sample_id || '-'}</Descriptions.Item>
          <Descriptions.Item label="当前状态">{selected?.review_status || '-'}</Descriptions.Item>
          <Descriptions.Item label="标注摘要">{selected?.annotation_sha256 || '-'}</Descriptions.Item>
          <Descriptions.Item label="标注画布">
            {canvasPath ? (
              <Button type="link" size="small" icon={<EditOutlined />} onClick={() => navigate(canvasPath)}>打开标注画布</Button>
            ) : (
              <Tooltip title="任务或样本编号无效">
                <span style={{ color: '#94a3b8' }}>当前不可用</span>
              </Tooltip>
            )}
          </Descriptions.Item>
        </Descriptions>
        <Select
          aria-label="目标审核状态"
          value={targetStatus}
          style={{ width: '100%', marginTop: 14 }}
          options={[
            { label: '进入审核', value: 'IN_REVIEW' },
            { label: '通过', value: 'APPROVED' },
            { label: '驳回', value: 'REJECTED' },
            { label: '作废', value: 'INVALIDATED' },
          ]}
          onChange={setTargetStatus}
        />
        <Input.TextArea
          aria-label="审核理由"
          aria-invalid={reasonMissing}
          value={reason}
          maxLength={2000}
          showCount
          rows={4}
          status={reasonMissing ? 'error' : undefined}
          style={{ marginTop: 12 }}
          placeholder="驳回或作废时必须填写原因"
          onChange={(event) => setReason(event.target.value)}
        />
        {reasonMissing && <div role="alert" style={{ marginTop: 6, color: '#cf1322', fontSize: 12 }}>驳回、仲裁或作废必须填写理由</div>}
      </Modal>
      <Modal
        title="批量驳回标注审核"
        open={batchTarget === 'REJECTED'}
        centered
        okText="确认批量驳回"
        cancelText="取消"
        confirmLoading={batchLoading}
        okButtonProps={{ danger: true, disabled: !selectedRows.length || batchReasonMissing }}
        onCancel={() => {
          if (!batchLoading) {
            setBatchTarget(null);
            setBatchReason('');
          }
        }}
        onOk={() => {
          if (!batchReasonMissing) void executeBatch('REJECTED', batchReason.trim());
        }}
      >
        <p>将驳回当前选中的 {selectedRows.length} 条处于“审核中”的记录。</p>
        <Input.TextArea
          aria-label="批量驳回原因"
          aria-invalid={batchReasonMissing}
          value={batchReason}
          maxLength={2000}
          showCount
          rows={4}
          status={batchReasonMissing ? 'error' : undefined}
          placeholder="请输入批量驳回原因"
          onChange={(event) => setBatchReason(event.target.value)}
        />
        {batchReasonMissing && <div role="alert" style={{ marginTop: 6, color: '#cf1322', fontSize: 12 }}>批量驳回必须填写原因</div>}
      </Modal>
    </TablePanel>
  );
}

export function PredictionReviewQueue() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState('');
  const [search, setSearch] = useState('');
  const [searchDraft, setSearchDraft] = useState('');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(20);
  const query = useQuery({
    queryKey: ['prediction-sets-review', status, search, page, pageSize],
    queryFn: async () => {
      const response = await request.get('/v1/prediction-sets', {
        params: {
          page,
          size: pageSize,
          ...(status ? { review_status: status } : {}),
          ...(search ? { q: search } : {}),
        },
      });
      const rows = listData(response);
      return { rows, total: listTotal(response, rows.length) };
    },
    enabled: true,
    staleTime: 10_000,
    refetchInterval: 15_000,
  });
  const transition = useMutation({
    mutationFn: async ({ id, target, reason }: { id: number; target: string; reason?: string }) => {
      const response = await request.post(`/v1/prediction-sets/${id}/review`, {
        target_status: target,
        reason: reason || undefined,
      }, { headers: { 'Idempotency-Key': `prediction-review-ui:${id}:${target}` } });
      return response.data;
    },
    onSuccess: () => {
      message.success('预测审核状态已更新');
      void queryClient.invalidateQueries({ queryKey: ['prediction-sets-review'] });
    },
    onError: (error) => message.error(responseStatus(error) === 409 ? '预测状态已变化，请刷新后重试' : '预测审核失败，请稍后重试'),
  });
  const project = useMutation({
    mutationFn: async (id: number) => (await request.post(`/v1/prediction-sets/${id}/project-legacy`, {}, { headers: { 'Idempotency-Key': `prediction-projection-ui:${id}` } })).data,
    onSuccess: () => {
      message.success('预测结果已投影到标注画布');
      void queryClient.invalidateQueries({ queryKey: ['prediction-sets-review'] });
      void queryClient.invalidateQueries({ queryKey: ['annotation-reviews'] });
    },
    onError: (error) => message.error(responseStatus(error) === 409 ? '预测尚未满足投影条件，请确认审核通过且绑定样本' : '预测投影失败，请稍后重试'),
  });
  const rows = query.data?.rows || [];
  const total = query.data?.total ?? 0;
  const applySearch = (value: string) => {
    setSearchDraft(value);
    setSearch(value.trim());
    setPage(0);
  };
  return (
    <TablePanel>
      <h2>AI 预测审核</h2>
      <p>Provider 预测结果先经过人工审核，审核通过后才允许投影到旧标注画布并进入后续回流。</p>
      <QueueToolbar>
        <Space wrap>
          <ServerSearchInput
            value={searchDraft}
            placeholder="按预测集、Provider 作业或样本搜索"
            ariaLabel="搜索 AI 预测审核"
            loading={query.isFetching}
            onChange={(value) => {
              setSearchDraft(value);
              if (!value) applySearch('');
            }}
            onSearch={applySearch}
          />
          <Select aria-label="预测审核状态筛选" value={status || 'ALL'} style={{ width: 150 }} onChange={(value) => { setStatus(value === 'ALL' ? '' : value); setPage(0); }} options={[{ label: '全部状态', value: 'ALL' }, { label: '待审核', value: 'PENDING' }, { label: '审核中', value: 'IN_REVIEW' }, { label: '已通过', value: 'ACCEPTED' }, { label: '已驳回', value: 'REJECTED' }]} />
          <QueueHint>当前页 {rows.length} 条 / 共 {total} 条预测</QueueHint>
          <Button icon={<ReloadOutlined />} loading={query.isFetching} onClick={() => void query.refetch()}>刷新预测</Button>
        </Space>
      </QueueToolbar>
      {query.error ? <Alert style={{ marginTop: 14 }} type="error" showIcon message="预测审核队列加载失败" description="请检查账号权限或稍后重试。" action={<Button size="small" onClick={() => void query.refetch()}>重试</Button>} /> : rows.length ? (
        <Table<Row> style={{ marginTop: 14 }} rowKey={(row) => String(row.id)} dataSource={rows} loading={query.isFetching} size="small" scroll={{ x: 900 }} columns={[
          { title: '预测集', dataIndex: 'id', key: 'id', width: 84, render: (value) => `#${value}` },
          { title: 'Provider 作业', dataIndex: 'provider_job_id', key: 'provider_job_id' },
          { title: '样本', dataIndex: 'sample_id', key: 'sample_id', render: (value) => value ? `#${value}` : '未绑定' },
          { title: '结果数量', dataIndex: 'item_count', key: 'item_count' },
          { title: '状态', dataIndex: 'review_status', key: 'review_status', render: (value) => <Tag color={value === 'ACCEPTED' ? 'green' : value === 'REJECTED' ? 'red' : 'gold'}>{value}</Tag> },
          { title: '操作', key: 'actions', width: 260, render: (_, row) => {
            const current = String(row.review_status || '').toUpperCase();
            const busy = transition.isPending || project.isPending;
            return <Space size={4} wrap>
              {current === 'PENDING' ? <Button type="link" size="small" disabled={busy} onClick={() => transition.mutate({ id: Number(row.id), target: 'IN_REVIEW' })}>进入审核</Button> : null}
              {current === 'IN_REVIEW' ? <><Button type="link" size="small" icon={<CheckOutlined />} disabled={busy} onClick={() => transition.mutate({ id: Number(row.id), target: 'ACCEPTED' })}>通过</Button><Button type="link" danger size="small" disabled={busy} onClick={() => transition.mutate({ id: Number(row.id), target: 'REJECTED', reason: '预测结果未达到审核标准' })}>驳回</Button></> : null}
              {current === 'ACCEPTED' ? <Button type="link" size="small" icon={<UploadOutlined />} disabled={busy || !row.sample_id} onClick={() => project.mutate(Number(row.id))}>投影到画布</Button> : null}
            </Space>;
          } },
        ] as ColumnsType<Row>} pagination={{
          current: page + 1,
          pageSize,
          total,
          showSizeChanger: true,
          showTotal: (value, range) => `${range[0]}-${range[1]} / ${value}`,
          onChange: (nextPage, nextPageSize) => {
            if (nextPageSize !== pageSize) {
              setPageSize(nextPageSize);
              setPage(0);
              return;
            }
            setPage(Math.max(0, nextPage - 1));
          },
        }} />
      ) : query.isLoading || query.isFetching ? <div role="status" aria-live="polite" style={{ padding: '28px 0', textAlign: 'center' }}><Spin /></div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={search ? '没有匹配的 AI 预测' : '当前筛选条件下暂无 AI 预测。完成 Provider 预标注后，结果会出现在这里。'} />}
    </TablePanel>
  );
}

export default function AnnotationReviewPage() {
  const [status, setStatus] = useState('');
  const [search, setSearch] = useState('');
  const [searchDraft, setSearchDraft] = useState('');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(20);
  const query = useQuery({
    queryKey: ['annotation-reviews', status, search, page, pageSize],
    queryFn: async () => {
      const response = await request.get('/v1/annotation-reviews', {
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
    staleTime: 10_000,
    refetchInterval: 15_000,
  });
  const applySearch = (value: string) => {
    setSearchDraft(value);
    setSearch(value.trim());
    setPage(0);
  };

  return (
    <>
      <ModuleHeader module="annotation-review" />
      <ModuleStatus module="annotation-review" />
      <AnnotationReviewQueue
        rows={query.data?.rows || []}
        total={query.data?.total ?? 0}
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
        onRefresh={() => {
          void query.refetch();
        }}
      />
      <PredictionReviewQueue />
    </>
  );
}
