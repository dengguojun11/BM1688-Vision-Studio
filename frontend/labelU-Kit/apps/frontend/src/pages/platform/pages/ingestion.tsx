import { CloseCircleOutlined, PlusOutlined, ReloadOutlined, UploadOutlined } from '@ant-design/icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Empty, Form, Input, InputNumber, message, Modal, Pagination, Popconfirm, Progress, Select, Space, Spin, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useEffect, useRef, useState } from 'react';

import request from '@/api/request';
import ServerSearchInput from '@/components/ServerSearchInput';
import ResourcePage from '../ResourcePage';
import { listData, listTotal, TablePanel, type Row } from '../shared';

type CreateJobValues = { source_type: string; source_connection_id?: number; selection?: string; upload_session_id?: string };

export const INGESTION_SOURCE_OPTIONS = [
  { label: '文件上传', value: 'UPLOAD' },
  { label: 'S3 / 对象存储', value: 'S3' },
  { label: 'RTSP 视频流', value: 'RTSP' },
  { label: '本地文件', value: 'FILE' },
  { label: '第三方平台', value: 'THIRD_PARTY' },
] as const;

type UploadValues = { ingestion_job_id: number; task_id: number };

const PICKER_PAGE_SIZE_OPTIONS = [20, 50, 100];

type ServerPagedSelectProps = {
  endpoint: string;
  queryKey: string;
  enabled?: boolean;
  id?: string;
  value?: number | string | null;
  onChange?: (value: number | undefined) => void;
  placeholder?: string;
  searchPlaceholder?: string;
  ariaLabel?: string;
  disabled?: boolean;
  allowClear?: boolean;
  params?: Record<string, unknown>;
  labelForRow: (row: Row) => string;
  filterRows?: (row: Row) => boolean;
  autoSelectFirst?: boolean;
};

/** Shared server-backed picker for large account-scoped resource lists. */
export function ServerPagedSelect({
  endpoint,
  queryKey,
  enabled = true,
  id,
  value,
  onChange,
  placeholder = '请选择',
  searchPlaceholder = '搜索',
  ariaLabel,
  disabled = false,
  allowClear = false,
  params = {},
  labelForRow,
  filterRows,
  autoSelectFirst = false,
}: ServerPagedSelectProps) {
  const [searchDraft, setSearchDraft] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(PICKER_PAGE_SIZE_OPTIONS[0]);
  const [rowCache, setRowCache] = useState<Record<number, Row>>({});
  const autoSelectedRef = useRef(false);
  const selectedId = value === undefined || value === null || value === '' ? undefined : Number(value);
  const query = useQuery({
    queryKey: [queryKey, endpoint, params, page, pageSize, search],
    queryFn: async () => {
      const response = await request.get(endpoint, {
        params: {
          ...params,
          page,
          size: pageSize,
          ...(search ? { q: search } : {}),
        },
      });
      const rawRows = listData(response) as Row[];
      const rows = filterRows ? rawRows.filter(filterRows) : rawRows;
      return { rows, total: listTotal(response, rawRows.length) };
    },
    enabled,
    staleTime: 30_000,
    retry: false,
    placeholderData: (previous) => previous,
  });
  const rows = query.data?.rows || [];
  const total = query.data?.total ?? 0;

  useEffect(() => {
    if (!rows.length) return;
    setRowCache((previous) => {
      const next = { ...previous };
      rows.forEach((row) => {
        if (row.id) next[Number(row.id)] = row;
      });
      return next;
    });
  }, [rows]);

  useEffect(() => {
    if (!autoSelectFirst || autoSelectedRef.current || selectedId || !rows.length || query.isLoading) return;
    const first = rows.find((row) => row.id);
    if (!first?.id) return;
    autoSelectedRef.current = true;
    onChange?.(Number(first.id));
  }, [autoSelectFirst, onChange, query.isLoading, rows, selectedId]);

  const selectedRow = selectedId ? rowCache[selectedId] : undefined;
  const optionMap = new Map<number, Row>();
  if (selectedRow?.id) optionMap.set(Number(selectedRow.id), selectedRow);
  rows.forEach((row) => {
    if (row.id) optionMap.set(Number(row.id), row);
  });
  const options = Array.from(optionMap.values()).map((row) => ({ value: Number(row.id), label: labelForRow(row) }));

  const applySearch = (nextSearch: string) => {
    setSearchDraft(nextSearch);
    setSearch(nextSearch.trim());
    setPage(0);
  };

  return (
    <div>
      <Space direction="vertical" size={8} style={{ display: 'flex' }}>
        <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
          <ServerSearchInput
            value={searchDraft}
            onChange={(nextValue) => {
              setSearchDraft(nextValue);
              if (!nextValue.trim()) applySearch('');
            }}
            onSearch={applySearch}
            placeholder={searchPlaceholder}
            ariaLabel={ariaLabel ? `搜索${ariaLabel}` : searchPlaceholder}
            loading={query.isFetching}
            style={{ width: 300, maxWidth: '100%' }}
          />
          <Typography.Text type="secondary">
            {selectedId ? '已选择 1 个' : '未选择'} · {search ? `搜索结果 ${total} 个` : `共 ${total} 个`}
          </Typography.Text>
        </Space>
        <Select
          id={id}
          aria-label={id ? undefined : ariaLabel}
          allowClear={allowClear}
          value={selectedId}
          loading={query.isLoading}
          disabled={disabled || query.isError}
          placeholder={query.isError ? `${ariaLabel || '选项'}加载失败，请重试` : placeholder}
          options={options}
          onChange={(nextValue) => onChange?.(nextValue === undefined ? undefined : Number(nextValue))}
          notFoundContent={query.isLoading ? <Spin size="small" /> : '当前页暂无匹配项'}
        />
        {query.error ? (
          <Alert
            type="error"
            showIcon
            message={`${ariaLabel || '选项'}加载失败`}
            description="请重试；当前已选项不会被清除。"
            action={<Button size="small" onClick={() => void query.refetch()}>重试</Button>}
          />
        ) : rows.length === 0 && !query.isLoading ? (
          <Alert type="warning" showIcon message={search ? '没有匹配项' : `暂无可用${ariaLabel || '选项'}`} />
        ) : (
          <Pagination
            size="small"
            current={page + 1}
            pageSize={pageSize}
            total={total}
            showSizeChanger
            pageSizeOptions={PICKER_PAGE_SIZE_OPTIONS.map(String)}
            hideOnSinglePage
            onChange={(nextPage, nextPageSize) => {
              const resolvedPageSize = nextPageSize || pageSize;
              setPageSize(resolvedPageSize);
              setPage(resolvedPageSize === pageSize ? Math.max(0, nextPage - 1) : 0);
            }}
          />
        )}
      </Space>
    </div>
  );
}

function UploadAssetWizard() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [uploadSessionId, setUploadSessionId] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [status, setStatus] = useState<'idle' | 'uploading' | 'success' | 'error'>('idle');
  const [form] = Form.useForm<UploadValues>();

  const reset = () => { setFile(null); setUploadSessionId(null); setProgress(0); setStatus('idle'); form.resetFields(); };
  const submit = async (values: UploadValues) => {
    if (!file) return;
    setStatus('uploading');
    setProgress(0);
    try {
      const response = uploadSessionId
        ? await request.get(`/v1/data-governance/upload-sessions/${uploadSessionId}`)
        : await request.post('/v1/data-governance/upload-sessions', { task_id: values.task_id, filename: file.name, total_size: file.size });
      const session = response.data?.data || response.data;
      const sessionId = session?.session_id;
      const totalChunks = Number(session?.total_chunks || Math.ceil(file.size / (8 * 1024 * 1024)));
      const chunkSize = Number(session?.chunk_size || 8 * 1024 * 1024);
      if (!sessionId) throw new Error('上传会话创建失败');
      setUploadSessionId(sessionId);
      const received = new Set<number>((session?.received_chunks || []).map((item: number) => Number(item)));
      for (let chunkNo = 0; chunkNo < totalChunks; chunkNo += 1) {
        if (received.has(chunkNo)) {
          setProgress(Math.round(((chunkNo + 1) / totalChunks) * 85));
          continue;
        }
        const body = new FormData();
        body.append('file', file.slice(chunkNo * chunkSize, Math.min(file.size, (chunkNo + 1) * chunkSize)), file.name);
        await request.put(`/v1/data-governance/upload-sessions/${sessionId}/chunks/${chunkNo}`, body);
        setProgress(Math.round(((chunkNo + 1) / totalChunks) * 85));
      }
      await request.post(`/v1/data-governance/upload-sessions/${sessionId}/complete`);
      setProgress(92);
      await request.post(`/v1/ingestion-jobs/${values.ingestion_job_id}/assets/from-upload-session`, { upload_session_id: sessionId });
      setProgress(100);
      setStatus('success');
      message.success('文件已上传并登记为媒体资产');
      await queryClient.invalidateQueries({ queryKey: ['ingestion-jobs'] });
    } catch (error: any) {
      setStatus('error');
      message.error(error?.response?.status === 409 ? '上传或扫描尚未完成，请稍后重试' : '文件上传失败，请检查任务和接入作业');
    }
  };

  return (
    <>
      <Button icon={<UploadOutlined />} onClick={() => setOpen(true)}>上传媒体文件</Button>
      <Modal
        title="上传媒体文件"
        open={open}
        centered
        destroyOnClose
        okText={status === 'success' ? '完成' : '开始上传'}
        cancelText="关闭"
        confirmLoading={status === 'uploading'}
        okButtonProps={{ disabled: status !== 'success' && (status === 'uploading' || !file) }}
        onCancel={() => { if (status !== 'uploading') { setOpen(false); reset(); } }}
        onOk={() => { if (status === 'success') { setOpen(false); reset(); } else void form.submit(); }}
      >
        <Form form={form} layout="vertical" onFinish={(values) => void submit(values)}>
          <Form.Item name="ingestion_job_id" label="接入作业" rules={[{ required: true, message: '请选择接入作业' }]}>
            <ServerPagedSelect
              endpoint="/v1/ingestion-jobs"
              queryKey="upload-target-ingestion-jobs"
              enabled={open}
              ariaLabel="接入作业"
              autoSelectFirst
              placeholder="选择当前账号的接入作业"
              searchPlaceholder="按作业 ID、来源或状态搜索"
              filterRows={(job) => !['CANCELLED', 'FAILED'].includes(String(job.status || '').toUpperCase())}
              labelForRow={(job) => `${job.name || `接入作业 #${job.id}`} · ${job.status || 'UNKNOWN'}`}
            />
          </Form.Item>
          <Form.Item name="task_id" label="目标任务" rules={[{ required: true, message: '请选择目标任务' }]}>
            <ServerPagedSelect
              endpoint="/v1/tasks"
              queryKey="upload-target-tasks"
              enabled={open}
              ariaLabel="目标任务"
              autoSelectFirst
              placeholder="选择接收媒体的任务"
              searchPlaceholder="按任务名称、任务 ID、来源或媒体类型搜索"
              labelForRow={(task) => `${task.name || `任务 #${task.id}`} · ${task.media_type || task.status || 'UNKNOWN'}`}
            />
          </Form.Item>
          <Form.Item label="媒体文件" required><Input type="file" accept="image/*,video/*,audio/*" onChange={(event) => { setFile(event.target.files?.[0] || null); setUploadSessionId(null); setStatus('idle'); setProgress(0); }} />{file && <Typography.Text type="secondary">{file.name}（{(file.size / 1024 / 1024).toFixed(2)} MB）</Typography.Text>}</Form.Item>
          {status === 'uploading' && <Progress percent={progress} status="active" />}
          {status === 'success' && <Alert type="success" showIcon message="上传完成，等待接入作业继续处理" />}
          {status === 'error' && <Alert type="error" showIcon message="上传未完成" description="修正任务或作业 ID 后可重试；已完成的分片会被跳过。" />}
          {!file && <Alert type="info" showIcon message="请选择一个媒体文件" />}
        </Form>
      </Modal>
    </>
  );
}

function CancelIngestionJobButton({ jobId, status }: { jobId?: number | string; status?: string }) {
  const queryClient = useQueryClient();
  const [loading, setLoading] = useState(false);
  if (!jobId || !['QUEUED', 'RUNNING'].includes(String(status).toUpperCase())) return null;
  const cancel = async () => {
    setLoading(true);
    try {
      await request.post(`/v1/ingestion-jobs/${jobId}/cancel`);
      message.success('接入作业已取消');
      await queryClient.invalidateQueries({ queryKey: ['ingestion-jobs'] });
    } catch (error: any) {
      message.error(error?.response?.status === 409 ? '作业已进入终态，请刷新列表' : '取消作业失败，请稍后重试');
    } finally {
      setLoading(false);
    }
  };
  return (
    <Popconfirm title="取消该接入作业？" okText="取消作业" cancelText="保留作业" onConfirm={() => void cancel()}>
      <Button type="link" danger size="small" loading={loading} icon={<CloseCircleOutlined />} aria-label="取消接入作业" />
    </Popconfirm>
  );
}

function CreateIngestionJobAction() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [form] = Form.useForm<CreateJobValues>();

  const submit = async (values: CreateJobValues) => {
    setSubmitting(true);
    try {
      let selection: Record<string, unknown> | undefined;
      if (values.selection?.trim()) {
        try {
          const parsed = JSON.parse(values.selection);
          if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('selection must be an object');
          selection = parsed;
        } catch {
          form.setFields([{ name: 'selection', errors: ['筛选条件必须是合法 JSON 对象'] }]);
          return;
        }
      }
      const idempotencyKey = typeof crypto !== 'undefined' && 'randomUUID' in crypto
        ? crypto.randomUUID()
        : `ingestion-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      const created = await request.post('/v1/ingestion-jobs', {
        source_type: values.source_type,
        source_connection_id: values.source_connection_id || undefined,
        selection,
      }, { headers: { 'Idempotency-Key': idempotencyKey } });
      const jobId = created.data?.id ?? created.data?.data?.id;
      if (values.upload_session_id?.trim() && jobId) {
        await request.post(`/v1/ingestion-jobs/${jobId}/assets/from-upload-session`, {
          upload_session_id: values.upload_session_id.trim(),
        });
      }
      message.success('接入作业已创建');
      setOpen(false);
      form.resetFields();
      await queryClient.invalidateQueries({ queryKey: ['ingestion-jobs'] });
    } catch (error: any) {
      const status = error?.response?.status;
      message.error(status === 403 ? '当前账号无权创建接入作业' : status === 409 ? '接入作业状态冲突，请刷新后重试' : '创建接入作业失败，请稍后重试');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <Space style={{ marginBottom: 14 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>新建接入作业</Button>
        <UploadAssetWizard />
      </Space>
      <Modal title="新建数据接入作业" open={open} centered destroyOnClose okText="创建作业" cancelText="取消" confirmLoading={submitting} onCancel={() => { if (!submitting) setOpen(false); }} onOk={() => void form.submit()}>
        <Form form={form} layout="vertical" initialValues={{ source_type: 'UPLOAD' }} onFinish={(values) => void submit(values)}>
          <Form.Item name="source_type" label="来源类型" rules={[{ required: true, message: '请选择来源类型' }]}>
            <Select options={INGESTION_SOURCE_OPTIONS as unknown as { label: string; value: string }[]} />
          </Form.Item>
          <Form.Item
            name="source_connection_id"
            label="数据源连接"
            extra="仅 S3、RTSP 或第三方来源需要；仅展示当前账号可访问的数据源"
          >
            <ServerPagedSelect
              endpoint="/v1/datasources"
              queryKey="ingestion-data-sources"
              enabled={open}
              ariaLabel="数据源连接"
              allowClear
              placeholder="选择当前账号的数据源连接"
              searchPlaceholder="按名称、类型、地址、存储桶或前缀搜索"
              labelForRow={(source) => `${source.name || `数据源 #${source.id}`} · ${source.type || 'S3'}${source.bucket ? ` · ${source.bucket}` : ''}`}
            />
          </Form.Item>
          <Form.Item name="selection" label="筛选条件（JSON，可选）" extra="用于记录本次接入的目录、时间范围或来源过滤条件">
            <Input.TextArea rows={4} placeholder='例如：{"prefix":"2026/09/"}' />
          </Form.Item>
          <Form.Item name="upload_session_id" label="已完成上传会话 ID（可选）" extra="可将旧上传页面完成的分片会话登记到当前接入作业">
            <Input placeholder="例如：a1b2c3..." />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

function MediaAssetPanel() {
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(20);
  const [scanStatus, setScanStatus] = useState('');
  const [search, setSearch] = useState('');
  const [searchDraft, setSearchDraft] = useState('');
  const query = useQuery({
    queryKey: ['media-assets', page, size, scanStatus, search],
    queryFn: async () => request.get('/v1/media-assets', { params: { page, size, scan_status: scanStatus || undefined, q: search || undefined } }),
    staleTime: 10_000,
  });
  const rows = listData(query.data) as Row[];
  const total = listTotal(query.data, rows.length);
  const columns: ColumnsType<Row> = [
    { title: '文件', key: 'filename', ellipsis: true, render: (_, row) => String((row.metadata as Record<string, unknown> | undefined)?.filename || row.source_object_key || row.object_uri || `资产 #${row.id}`) },
    { title: '类型', dataIndex: 'media_type', key: 'media_type', render: (value) => value || '-' },
    { title: '扫描', dataIndex: 'scan_status', key: 'scan_status', render: (value) => <Tag color={value === 'PASSED' ? 'green' : value === 'FAILED' || value === 'QUARANTINED' ? 'red' : 'gold'}>{value || 'PENDING'}</Tag> },
    { title: '质量', dataIndex: 'quality_status', key: 'quality_status', render: (value) => <Tag>{value || 'PENDING'}</Tag> },
    { title: '生命周期', dataIndex: 'lifecycle_status', key: 'lifecycle_status', render: (value) => value || '-' },
    { title: 'SHA256', dataIndex: 'sha256', key: 'sha256', ellipsis: true },
    { title: '更新时间', dataIndex: 'updated_at', key: 'updated_at', render: (value) => value ? new Date(value).toLocaleString() : '-' },
  ];
  const refresh = () => void query.refetch();
  return (
    <TablePanel>
      <Space style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 10 }}>
        <div><h2 style={{ margin: 0 }}>媒体资产</h2><Typography.Text type="secondary">查看上传资产的扫描、质量和生命周期状态</Typography.Text></div>
        <Space>
          <ServerSearchInput
            value={searchDraft}
            onChange={(value) => { setSearchDraft(value); if (!value) { setSearch(''); setPage(0); } }}
            onSearch={(value) => { setSearch(value.trim()); setSearchDraft(value); setPage(0); }}
            placeholder="按文件名、对象路径或摘要搜索"
            ariaLabel="搜索媒体资产"
            loading={query.isFetching}
            style={{ width: 280, maxWidth: '100%' }}
          />
          <Select aria-label="扫描状态筛选" value={scanStatus} onChange={(value) => { setPage(0); setScanStatus(value); }} options={[{ label: '全部扫描状态', value: '' }, 'PENDING', 'SCANNING', 'PASSED', 'FAILED', 'QUARANTINED'].map((item) => typeof item === 'string' ? { label: item, value: item } : item)} style={{ minWidth: 150 }} />
          <Button icon={<ReloadOutlined />} loading={query.isFetching} onClick={refresh}>刷新资产</Button>
        </Space>
      </Space>
      {query.error ? <Alert type="error" showIcon message="媒体资产加载失败" description="请检查当前账号权限或稍后重试。" action={<Button size="small" onClick={refresh}>重试</Button>} /> : query.isLoading ? <div role="status" aria-live="polite"><Typography.Text>正在加载媒体资产</Typography.Text></div> : rows.length ? <Table<Row> rowKey="id" columns={columns} dataSource={rows} loading={query.isFetching} size="small" scroll={{ x: 900 }} pagination={{ current: page + 1, pageSize: size, total, showSizeChanger: true, onChange: (nextPage, nextSize) => { setPage(nextSize !== size ? 0 : nextPage - 1); setSize(nextSize); } }} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={search ? '没有匹配的媒体资产' : '当前账号暂无媒体资产'} />}
    </TablePanel>
  );
}

export default function IngestionPage() {
  const [search, setSearch] = useState('');
  return <>
    <ResourcePage module="ingestion" needsProject={false} queryKey="ingestion-jobs" queryFn={async (_projectId, page = 0, size = 20, query) => await request.get('/v1/ingestion-jobs', { params: { page, size, q: query || undefined } })} pagination pageSize={20} search={search} onSearchChange={setSearch} searchPlaceholder="按作业 ID、来源、状态或清单搜索" searchAriaLabel="搜索接入作业" beforeTable={<CreateIngestionJobAction />} staleTime={15_000} detailPath={(row) => row.id ? `/v1/ingestion-jobs/${row.id}` : null} columns={[
      { title: '来源', dataIndex: 'source_type', key: 'source_type' },
      { title: '状态', dataIndex: 'status', key: 'status', render: (value) => <Tag color={value === 'SUCCEEDED' || value === 'PARTIAL' ? 'green' : value === 'FAILED' ? 'red' : 'blue'}>{value || 'UNKNOWN'}</Tag> },
      { title: '总数 / 接收', key: 'counts', render: (_, row) => `${row.total_count ?? 0} / ${row.accepted_count ?? 0}` },
      { title: '隔离 / 失败', key: 'quarantine', render: (_, row) => `${row.quarantined_count ?? 0} / ${row.failed_count ?? 0}` },
      { title: '更新时间', dataIndex: 'updated_at', key: 'updated_at', render: (value) => (value ? new Date(value).toLocaleString() : '-') },
      { title: '操作', key: 'actions', width: 72, render: (_, row) => <CancelIngestionJobButton jobId={row.id} status={row.status} /> },
    ]} />
    <MediaAssetPanel />
  </>;
}
