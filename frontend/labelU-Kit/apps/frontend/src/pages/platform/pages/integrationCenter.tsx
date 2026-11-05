import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Descriptions,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Progress,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Timeline,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import styled from 'styled-components';

import request from '@/api/request';
import useTaskSuperuser from '@/hooks/useTaskSuperuser';

import { listData, listTotal } from '../shared';

const { RangePicker } = DatePicker;

const Toolbar = styled.div`
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
`;

const FilterRow = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
`;

const SummaryGrid = styled.div`
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
  margin-bottom: 12px;
`;

const STAGE_COLORS: Record<string, string> = {
  DATA_COLLECTING: 'blue',
  WAITING_FOR_SAMPLES: 'gold',
  WAITING_FOR_DATA: 'gold',
  WAITING_FOR_MODEL: 'gold',
  DATA_READY: 'cyan',
  DATASET_CREATED: 'cyan',
  WAITING_REVIEW: 'purple',
  TRAINING: 'blue',
  EXPORTING: 'blue',
  EDGE_VALIDATING: 'blue',
  WAITING_PUBLISH_DECISION: 'orange',
  WAITING_MANUAL_PUBLISH: 'orange',
  PUBLISHING: 'blue',
  COMPLETED: 'green',
  AUTO_PUBLISHED: 'green',
  FAILED: 'red',
  CANCELLED: 'default',
};

const REQUEST_TYPE_LABELS: Record<string, string> = {
  model_optimization: '模型优化',
  threshold_experiment: '阈值实验',
  scene_training: '场景训练',
};

const START_REASON_LABELS: Record<string, string> = {
  data_not_ready: '数据尚未就绪，请等待图片或数据包累计完成',
  package_or_source_not_ready: '尚未绑定可用数据包或模型映射，无法启动流程',
  already_started: '流程已经启动',
};

function formatDateTime(value: unknown) {
  if (!value) return '-';
  const text = String(value);
  return text.replace('T', ' ').replace('Z', '').slice(0, 19);
}

type IntegrationRow = Record<string, any>;

function asRows(response: any): IntegrationRow[] {
  return (listData(response) || []) as IntegrationRow[];
}

function asTotal(response: any, fallback: number): number {
  return Number(listTotal(response, fallback) || fallback);
}

function stageTag(row: IntegrationRow) {
  return (
    <Space direction="vertical" size={0}>
      <Tag color={STAGE_COLORS[row.stage] || 'default'}>{row.stage_label || row.stage}</Tag>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {row.status_label || row.status}
      </Typography.Text>
    </Space>
  );
}

function modelDisplayText(row: IntegrationRow): string {
  const display = row.external_model_display || {};
  const name = String(display.display_name || '').trim();
  const raw = String(display.external_model_id || row.external_model_id || '').trim();
  if (name) return `${name} (ID ${raw || '-'})`;
  return raw || '-';
}

function formatSceneValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '-';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) {
    return value
      .map((item) =>
        typeof item === 'object' && item !== null ? JSON.stringify(item) : String(item),
      )
      .join('、');
  }
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

function sceneEntries(row: IntegrationRow): { key: string; value: unknown }[] {
  const items = row.scene_items;
  if (Array.isArray(items)) {
    return items
      .filter((item) => item && typeof item === 'object')
      .map((item) => ({
        key: String((item as IntegrationRow).key ?? ''),
        value: (item as IntegrationRow).value,
      }));
  }
  const scene = row.scene;
  if (scene && typeof scene === 'object' && !Array.isArray(scene)) {
    return Object.entries(scene as Record<string, unknown>).map(([key, value]) => ({
      key,
      value,
    }));
  }
  return [];
}

function originalPayloadText(data: IntegrationRow | null): string {
  if (!data) return '';
  const value = data.payload ?? data.composed ?? null;
  if (value === null || value === undefined) return '暂无数据';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function IntegrationRequestsPanel() {
  const [rows, setRows] = useState<IntegrationRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [summary, setSummary] = useState<IntegrationRow>({});
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [typeFilter, setTypeFilter] = useState<string | undefined>();
  const [sourceFilter, setSourceFilter] = useState('');
  const [keyword, setKeyword] = useState('');
  const [lookupMode, setLookupMode] = useState<'keyword' | 'image' | 'package'>('keyword');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(20);
  const [selected, setSelected] = useState<IntegrationRow | null>(null);
  const [detail, setDetail] = useState<IntegrationRow | null>(null);
  const [originalRequest, setOriginalRequest] = useState<IntegrationRow | null>(null);
  const [originalRequestId, setOriginalRequestId] = useState<number | null>(null);
  const [originalLoading, setOriginalLoading] = useState(false);
  const [originalError, setOriginalError] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [bindOpen, setBindOpen] = useState(false);
  const [availabilityOpen, setAvailabilityOpen] = useState(false);
  const [availability, setAvailability] = useState<IntegrationRow | null>(null);
  const [alerts, setAlerts] = useState<IntegrationRow>({ counts: {} });
  const [alertsOpen, setAlertsOpen] = useState(false);
  const [accumulationConfig, setAccumulationConfig] = useState<IntegrationRow>({});
  const [configOpen, setConfigOpen] = useState(false);
  const { isTaskSuperuser } = useTaskSuperuser();
  const [streamLive, setStreamLive] = useState(false);
  const [createForm] = Form.useForm();
  const [bindForm] = Form.useForm();
  const [availabilityForm] = Form.useForm();
  const [configForm] = Form.useForm();

  const loadSummary = async () => {
    try {
      const response = await request.get('/v2/integrations/requests/summary');
      setSummary((response as any)?.data || {});
    } catch {
      // Keep the list usable when the summary endpoint is temporarily unavailable.
    }
  };

  const loadAlerts = async () => {
    try {
      const response = await request.get('/v2/integrations/requests/alerts', {
        params: { stale_minutes: 30 },
      });
      setAlerts((response as any)?.data || { counts: {} });
    } catch {
      // Alerts are operational decoration; do not break the request console.
    }
  };

  const loadAccumulationConfig = async () => {
    try {
      const response = await request.get('/v1/integration-settings/accumulation');
      setAccumulationConfig((response as any)?.data || {});
    } catch {
      setAccumulationConfig({});
    }
  };

  const loadRows = async () => {
    setLoading(true);
    try {
      if (keyword.trim() && lookupMode !== 'keyword') {
        const path =
          lookupMode === 'image'
            ? `/v2/integrations/requests/by-image/${encodeURIComponent(keyword.trim())}`
            : `/v2/integrations/requests/by-package/${encodeURIComponent(keyword.trim())}`;
        const response = await request.get(path);
        const nextRows = asRows(response);
        setRows(nextRows);
        setTotal(nextRows.length);
        return;
      }
      const response = await request.get('/v2/integrations/requests', {
        params: {
          page,
          size: pageSize,
          ...(statusFilter ? { status: statusFilter } : {}),
          ...(typeFilter ? { request_type: typeFilter } : {}),
          ...(sourceFilter ? { source_system: sourceFilter.trim() } : {}),
          ...(keyword ? { q: keyword.trim() } : {}),
        },
      });
      const nextRows = asRows(response);
      setRows(nextRows);
      setTotal(asTotal(response, nextRows.length));
    } catch (error: any) {
      message.error(error?.message || '优化请求加载失败');
    } finally {
      setLoading(false);
    }
  };

  const loadDetail = async (id: number, showError = false) => {
    try {
      const response = await request.get(`/v2/integrations/requests/${id}`);
      setDetail((response as any)?.data || null);
    } catch (error: any) {
      if (showError) message.error(error?.message || '请求详情加载失败');
    }
  };

  // 原始请求体积大且仅管理员可见，按需加载一次，绝不进入 3 秒详情轮询。
  const loadOriginalRequest = async (id: number) => {
    setOriginalLoading(true);
    setOriginalError('');
    try {
      const response = await request.get(`/v2/integrations/requests/${id}/original-request`);
      setOriginalRequest(((response as any)?.data || null) as IntegrationRow | null);
      setOriginalRequestId(id);
    } catch (error: any) {
      setOriginalRequest(null);
      setOriginalRequestId(id);
      setOriginalError(
        error?.response?.status === 403
          ? '仅系统管理员可查看原始请求报文'
          : error?.response?.data?.msg || error?.message || '原始请求加载失败',
      );
    } finally {
      setOriginalLoading(false);
    }
  };

  useEffect(() => {
    void loadSummary();
    void loadAlerts();
    void loadAccumulationConfig();
    void loadRows();
    // Refresh summary/list together so the page can be used as a lightweight
    // real-time monitor even before the SSE endpoint is enabled.
    const timer = window.setInterval(() => {
      void loadSummary();
      void loadAlerts();
      void loadRows();
    }, 5000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, pageSize, statusFilter, typeFilter, sourceFilter, keyword, lookupMode]);

  useEffect(() => {
    if (keyword.trim() && lookupMode !== 'keyword') {
      setStreamLive(false);
      return undefined;
    }
    const controller = new AbortController();
    let closed = false;
    const run = async () => {
      try {
        const params = new URLSearchParams({
          size: '50',
          ...(statusFilter ? { status: statusFilter } : {}),
          ...(typeFilter ? { request_type: typeFilter } : {}),
          ...(sourceFilter ? { source_system: sourceFilter.trim() } : {}),
          ...(keyword ? { q: keyword.trim() } : {}),
        });
        const response = await fetch(`/api/v2/integrations/requests/stream?${params.toString()}`, {
          headers: {
            Accept: 'text/event-stream',
            ...(localStorage.token ? { Authorization: localStorage.token } : {}),
          },
          signal: controller.signal,
        });
        if (!response.ok || !response.body) throw new Error(`stream ${response.status}`);
        setStreamLive(true);
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (!closed) {
          const chunk = await reader.read();
          if (chunk.done) break;
          buffer += decoder.decode(chunk.value, { stream: true });
          let boundary = buffer.indexOf('\n\n');
          while (boundary >= 0) {
            const block = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            const dataLine = block
              .split('\n')
              .find((line) => line.startsWith('data:'));
            if (dataLine) {
              try {
                const payload = JSON.parse(dataLine.slice(5).trim());
                if (payload && Array.isArray(payload.rows)) {
                  setRows(payload.rows as IntegrationRow[]);
                  setTotal(Number(payload.total || payload.rows.length || 0));
                  if (payload.summary) setSummary(payload.summary);
                }
              } catch {
                // Ignore malformed snapshots; polling remains the fallback.
              }
            }
            boundary = buffer.indexOf('\n\n');
          }
        }
      } catch (error: any) {
        if (error?.name !== 'AbortError') setStreamLive(false);
      }
    };
    void run();
    return () => {
      closed = true;
      controller.abort();
      setStreamLive(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter, typeFilter, sourceFilter, keyword, lookupMode]);

  useEffect(() => {
    if (!selected) return;
    void loadDetail(Number(selected.id));
    const timer = window.setInterval(() => {
      void loadDetail(Number(selected.id));
    }, 3000);
    return () => window.clearInterval(timer);
  }, [selected]);

  const columns: ColumnsType<IntegrationRow> = useMemo(
    () => [
      {
        title: '请求',
        key: 'request',
        width: 220,
        render: (_, row) => (
          <Space direction="vertical" size={0}>
            <Typography.Text strong>#{row.id}</Typography.Text>
            <Typography.Text type="secondary" copyable style={{ fontSize: 12 }}>
              {row.external_request_id}
            </Typography.Text>
          </Space>
        ),
      },
      {
        title: '类型',
        dataIndex: 'request_type',
        key: 'request_type',
        width: 110,
        render: (value) => REQUEST_TYPE_LABELS[value] || value,
      },
      {
        title: '模型 / 事件',
        key: 'model_event',
        width: 240,
        render: (_, row) => {
          const title = String(row.request_title || '').trim() || row.external_request_id || '-';
          return (
            <Space direction="vertical" size={0} style={{ maxWidth: 230 }}>
              <Typography.Text strong ellipsis={{ tooltip: title }}>
                {title}
              </Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                事件：{row.event_label || '-'}
              </Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                模型：{modelDisplayText(row)}
              </Typography.Text>
            </Space>
          );
        },
      },
      {
        title: '当前阶段',
        key: 'stage',
        width: 140,
        render: (_, row) => stageTag(row),
      },
      {
        title: '数据覆盖',
        key: 'coverage',
        width: 190,
        render: (_, row) => {
          const coverage = row.coverage || {};
          const percent = Math.round(Number(coverage.coverage_ratio || 0) * 100);
          return (
            <Space direction="vertical" size={0} style={{ width: '100%' }}>
              <Progress percent={percent} size="small" />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                已到 {coverage.available || 0}/{coverage.requested || 0} · 待补{' '}
                {coverage.pending || 0}
              </Typography.Text>
            </Space>
          );
        },
      },
      {
        title: '批次',
        key: 'packages',
        width: 110,
        render: (_, row) => `${(row.package_ids || []).length} 个`,
      },
      {
        title: '更新时间',
        dataIndex: 'updated_at',
        key: 'updated_at',
        width: 170,
        render: (value) => formatDateTime(value),
      },
      {
        title: '操作',
        key: 'action',
        width: 130,
        render: (_, row) => (
          <Space>
            <Button type="link" size="small" onClick={() => setSelected(row)}>
              查看
            </Button>
            <Popconfirm
              title="确认取消该请求？"
              onConfirm={async () => {
                try {
                  await request.post(`/v2/integrations/requests/${row.id}/cancel`, {});
                  message.success('请求已取消');
                  await Promise.all([loadRows(), loadSummary()]);
                } catch (error: any) {
                  message.error(error?.message || '取消失败');
                }
              }}
            >
              <Button type="link" size="small" danger disabled={['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(row.status)}>
                取消
              </Button>
            </Popconfirm>
          </Space>
        ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [page, pageSize, statusFilter, typeFilter, sourceFilter, keyword],
  );

  const createRequest = async () => {
    const values = await createForm.validateFields();
    const range = values.event_time as [any, any] | undefined;
    if (values.request_type === 'threshold_experiment') {
      if (!values.external_model_id?.trim() || !values.event_label?.trim()) {
        message.warning('阈值实验必须填写模型 ID 和事件');
        return;
      }
      if (!range?.[0] || !range?.[1]) {
        message.warning('阈值实验必须填写事件时间范围');
        return;
      }
    }
    let scene: Record<string, unknown> = {};
    if (values.scene_json) {
      try {
        scene = JSON.parse(values.scene_json);
      } catch {
        message.error('场景必须是有效 JSON');
        return;
      }
    }
    if (values.request_type === 'scene_training' && !Object.keys(scene).length) {
      message.warning('场景训练必须填写场景 JSON');
      return;
    }
    let images: unknown[] = [];
    if (values.images_json) {
      try {
        images = JSON.parse(values.images_json);
      } catch {
        message.error('图片映射必须是有效 JSON 数组');
        return;
      }
    }
    try {
      await request.post('/v2/integrations/requests', {
        external_request_id: values.external_request_id.trim(),
        source_system: values.source_system?.trim() || 'third_party',
        request_type: values.request_type,
        external_model_id: values.external_model_id?.trim() || undefined,
        event_label: values.event_label?.trim() || undefined,
        scene,
        min_sample_count: values.min_sample_count || 50,
        event_time_from: range?.[0]?.toISOString(),
        event_time_to: range?.[1]?.toISOString(),
        images,
      });
      message.success('优化请求已创建');
      setCreateOpen(false);
      createForm.resetFields();
      setPage(0);
      await Promise.all([loadRows(), loadSummary()]);
    } catch (error: any) {
      message.error(error?.message || '创建失败');
    }
  };

  const bindPackages = async () => {
    if (!selected) return;
    const values = await bindForm.validateFields();
    let images: unknown[] = [];
    if (values.images_json) {
      try {
        images = JSON.parse(values.images_json);
      } catch {
        message.error('图片映射必须是有效 JSON 数组');
        return;
      }
    }
    try {
      await request.post(`/v2/integrations/requests/${selected.id}/packages`, {
        package_ids: String(values.package_ids || '')
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean),
        images,
      });
      message.success('数据已绑定，覆盖度已刷新');
      setBindOpen(false);
      bindForm.resetFields();
      await loadDetail(Number(selected.id), true);
      await Promise.all([loadRows(), loadSummary()]);
    } catch (error: any) {
      message.error(error?.message || '绑定失败');
    }
  };

  const detailSceneEntries = detail ? sceneEntries(detail) : [];

  return (
    <>
      <SummaryGrid>
        <Card size="small">
          <Statistic title="优化请求总数" value={summary.total || 0} />
        </Card>
        <Card size="small">
          <Statistic title="待人工审核" value={summary.waiting_review || 0} />
        </Card>
        <Card size="small">
          <Statistic title="待人工发布" value={summary.waiting_manual_publish || 0} />
        </Card>
        <Card size="small">
          <Statistic title="已完成" value={summary.completed || 0} />
        </Card>
        <Card size="small">
          <Statistic title="失败" value={summary.failed || 0} />
        </Card>
      </SummaryGrid>

      <Card size="small">
        <Toolbar>
          <FilterRow>
            <Select
              value={lookupMode}
              style={{ width: 120 }}
              onChange={(value) => {
                setLookupMode(value);
                setKeyword('');
                setPage(0);
              }}
              options={[
                { value: 'keyword', label: '请求搜索' },
                { value: 'image', label: '按图片查' },
                { value: 'package', label: '按批次查' },
              ]}
            />
            <Input
              allowClear
              placeholder={
                lookupMode === 'image'
                  ? '输入外部图片 ID，回车或自动查询'
                  : lookupMode === 'package'
                    ? '输入数据包 ID，回车或自动查询'
                    : '搜索请求 ID / 模型 / 事件'
              }
              style={{ width: 230 }}
              value={keyword}
              onChange={(event) => {
                setKeyword(event.target.value);
                setPage(0);
              }}
            />
            <Input
              allowClear
              placeholder="来源系统"
              style={{ width: 150 }}
              value={sourceFilter}
              onChange={(event) => {
                setSourceFilter(event.target.value);
                setPage(0);
              }}
            />
            <Select
              allowClear
              placeholder="请求类型"
              style={{ width: 140 }}
              value={typeFilter}
              onChange={(value) => {
                setTypeFilter(value);
                setPage(0);
              }}
              options={Object.entries(REQUEST_TYPE_LABELS).map(([value, label]) => ({ value, label }))}
            />
            <Select
              allowClear
              placeholder="状态"
              style={{ width: 120 }}
              value={statusFilter}
              onChange={(value) => {
                setStatusFilter(value);
                setPage(0);
              }}
              options={[
                { value: 'PENDING', label: '已受理' },
                { value: 'RUNNING', label: '执行中' },
                { value: 'WAITING', label: '等待中' },
                { value: 'SUCCEEDED', label: '已完成' },
                { value: 'FAILED', label: '失败' },
                { value: 'CANCELLED', label: '已取消' },
              ]}
            />
          </FilterRow>
          <Space>
            <Tag color={streamLive ? 'green' : 'default'}>{streamLive ? '实时' : '轮询'}</Tag>
            {isTaskSuperuser ? (
              <Button
                onClick={() => {
                  configForm.setFieldsValue({
                    min_sample_count: accumulationConfig.min_sample_count || 50,
                  });
                  setConfigOpen(true);
                }}
              >
                累计配置
              </Button>
            ) : null}
            <Button danger={Boolean(alerts?.counts?.total)} onClick={() => setAlertsOpen(true)}>
              异常 {alerts?.counts?.total || 0}
            </Button>
            <Button onClick={() => void Promise.all([loadRows(), loadSummary(), loadAlerts()])} loading={loading}>
              刷新
            </Button>
            <Button onClick={() => { setAvailability(null); setAvailabilityOpen(true); }}>
              数据可用性
            </Button>
            <Button
              type="primary"
              onClick={() => {
                createForm.setFieldsValue({
                  min_sample_count: accumulationConfig.min_sample_count || 50,
                });
                setCreateOpen(true);
              }}
            >
              新建优化请求
            </Button>
          </Space>
        </Toolbar>
        <Table
          rowKey="id"
          size="small"
          loading={loading}
          columns={columns}
          dataSource={rows}
          pagination={{
            current: page + 1,
            pageSize,
            total,
            showSizeChanger: true,
            onChange: (nextPage, nextPageSize) => {
              setPage(nextPage - 1);
              setPageSize(nextPageSize);
            },
          }}
          locale={{ emptyText: '暂无优化请求，可点击“新建优化请求”或等待第三方提交数据' }}
        />
      </Card>

      <Modal
        title="新建优化请求"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => void createRequest()}
        okText="创建"
        width={720}
        destroyOnClose
      >
        <Form form={createForm} layout="vertical" initialValues={{ request_type: 'model_optimization', min_sample_count: 50 }}>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="external_request_id" label="第三方请求 ID" rules={[{ required: true }]}>
                <Input placeholder="external_request_id" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="source_system" label="来源系统" initialValue="third_party">
                <Input />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="request_type" label="请求类型" rules={[{ required: true }]}>
                <Select options={Object.entries(REQUEST_TYPE_LABELS).map(([value, label]) => ({ value, label }))} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="external_model_id" label="外部模型 ID">
                <Input placeholder="model_id" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="event_label" label="事件 / 标签">
                <Input placeholder="person / vehicle ..." />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="min_sample_count" label="最小样本数">
                <InputNumber min={1} max={100000} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="event_time" label="事件时间范围">
                <RangePicker showTime style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="scene_json" label="场景筛选（JSON，可选）">
                <Input placeholder='{"lighting":"evening"}' />
              </Form.Item>
            </Col>
            <Col span={24}>
              <Form.Item
                name="images_json"
                label="图片映射（JSON 数组，可选）"
                extra='支持 [{"external_image_id":"img-1","task_id":123,"sample_id":456}]；无数据时留空即可'
              >
                <Input.TextArea rows={3} placeholder='[{"external_image_id":"img-1","task_id":123,"sample_id":456}]' />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </Modal>

      <Modal
        title="第三方累计配置"
        open={configOpen}
        onCancel={() => setConfigOpen(false)}
        onOk={async () => {
          const values = await configForm.validateFields();
          try {
            await request.put('/v1/integration-settings/accumulation', {
              min_sample_count: Number(values.min_sample_count),
            });
            message.success('累计配置已保存，新数据包将按新配置累计');
            setConfigOpen(false);
            await loadAccumulationConfig();
          } catch (error: any) {
            message.error(error?.message || '累计配置保存失败');
          }
        }}
        okText="保存"
        width={520}
        destroyOnClose
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="该配置在新建累计组时生效"
          description="修改后只影响之后新建的累计组；已经达到旧阈值的累计组不会回退。"
        />
        <Form form={configForm} layout="vertical">
          <Form.Item
            name="min_sample_count"
            label="最小累计图片数"
            rules={[{ required: true, message: '请输入最小累计图片数' }]}
          >
            <InputNumber min={1} max={100000} style={{ width: '100%' }} />
          </Form.Item>
          <Typography.Text type="secondary">
            当前生效值：{accumulationConfig.min_sample_count || 50} 张
            {accumulationConfig.source === 'environment_default' ? '（环境变量默认值）' : '（数据库配置）'}
          </Typography.Text>
        </Form>
      </Modal>

      <Modal
        title="异常监控"
        open={alertsOpen}
        onCancel={() => setAlertsOpen(false)}
        footer={null}
        width={860}
        destroyOnClose
      >
        <Descriptions column={4} size="small" bordered style={{ marginBottom: 12 }}>
          <Descriptions.Item label="已超截止">{alerts?.counts?.deadline || 0}</Descriptions.Item>
          <Descriptions.Item label="长时间停滞">{alerts?.counts?.stale || 0}</Descriptions.Item>
          <Descriptions.Item label="失败">{alerts?.counts?.failed || 0}</Descriptions.Item>
          <Descriptions.Item label="回调失败">{alerts?.counts?.callback_failed || 0}</Descriptions.Item>
        </Descriptions>
        <Table
          rowKey={(row: IntegrationRow) => `${row.id}-${row.stage || ''}-${row.status || ''}`}
          size="small"
          pagination={false}
          dataSource={[
            ...(alerts?.deadline || []),
            ...(alerts?.stale || []),
            ...(alerts?.failed || []),
          ].filter(
            (row: IntegrationRow, index: number, list: IntegrationRow[]) =>
              list.findIndex((candidate) => candidate.id === row.id) === index,
          )}
          columns={[
            { title: '请求', dataIndex: 'id', width: 70 },
            { title: '第三方请求 ID', dataIndex: 'external_request_id' },
            { title: '类型', dataIndex: 'request_type', width: 120 },
            { title: '阶段', dataIndex: 'stage', width: 150 },
            { title: '状态', dataIndex: 'status', width: 90 },
            { title: '更新时间', dataIndex: 'updated_at', width: 170 },
            { title: '截止时间', dataIndex: 'deadline_at', width: 170 },
          ]}
          locale={{ emptyText: '当前没有异常请求' }}
        />
      </Modal>

      <Modal
        title="数据可用性预检查"
        open={availabilityOpen}
        onCancel={() => setAvailabilityOpen(false)}
        onOk={async () => {
          const values = await availabilityForm.validateFields();
          const range = values.event_time as [any, any] | undefined;
          try {
            const response: any = await request.get('/v2/integrations/data-availability', {
              params: {
                ...(values.model_id ? { model_id: values.model_id.trim() } : {}),
                ...(values.event_label ? { event_label: values.event_label.trim() } : {}),
                ...(range?.[0] ? { event_time_from: range[0].toISOString() } : {}),
                ...(range?.[1] ? { event_time_to: range[1].toISOString() } : {}),
              },
            });
            setAvailability(response?.data || {});
          } catch (error: any) {
            message.error(error?.message || '数据可用性查询失败');
          }
        }}
        okText="查询"
        width={620}
        destroyOnClose
      >
        <Form form={availabilityForm} layout="vertical">
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="model_id" label="模型 ID">
                <Input placeholder="model_id" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="event_label" label="事件 / 标签">
                <Input placeholder="person / vehicle" />
              </Form.Item>
            </Col>
            <Col span={24}>
              <Form.Item name="event_time" label="事件时间范围">
                <RangePicker showTime style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          {availability ? (
            <Descriptions column={4} size="small" bordered>
              <Descriptions.Item label="应到">{availability.requested || 0}</Descriptions.Item>
              <Descriptions.Item label="已到">{availability.available || 0}</Descriptions.Item>
              <Descriptions.Item label="待补齐">{availability.pending || 0}</Descriptions.Item>
              <Descriptions.Item label="未知">{availability.unknown || 0}</Descriptions.Item>
              <Descriptions.Item label="是否完整" span={2}>
                {availability.complete ? '数据已完整' : '数据尚未完整'}
              </Descriptions.Item>
              <Descriptions.Item label="数据水位" span={2}>
                {availability.watermark || '-'}
              </Descriptions.Item>
            </Descriptions>
          ) : null}
        </Form>
      </Modal>

      <Modal
        title="绑定数据包 / 图片"
        open={bindOpen}
        onCancel={() => setBindOpen(false)}
        onOk={() => void bindPackages()}
        okText="绑定"
        width={720}
        destroyOnClose
      >
        <Form form={bindForm} layout="vertical">
          <Form.Item name="package_ids" label="数据包 ID（逗号分隔）">
            <Input placeholder="pkg-1, pkg-2" />
          </Form.Item>
          <Form.Item name="images_json" label="外部图片映射（JSON 数组，可选）">
            <Input.TextArea rows={5} placeholder='[{"external_image_id":"img-1","event_time":"2026-11-01T10:00:00Z"}]' />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title={detail ? `优化请求 #${detail.id}` : '优化请求详情'}
        width={860}
        open={Boolean(selected)}
        onClose={() => {
          setSelected(null);
          setDetail(null);
          setOriginalRequest(null);
          setOriginalRequestId(null);
          setOriginalError('');
        }}
        extra={
          detail ? (
            <Space>
              <Button size="small" onClick={() => void loadDetail(Number(detail.id), true)}>
                刷新
              </Button>
              {!detail.strategy_external_task_id ? (
                <Button
                  size="small"
                  type="primary"
                  onClick={async () => {
                    try {
                      const response: any = await request.post(
                        `/v2/integrations/requests/${detail.id}/start-pipeline`,
                        {},
                      );
                      const result = response?.data || {};
                      if (result.started) {
                        message.success('已启动训练/验证流程');
                      } else {
                        message.warning(
                          START_REASON_LABELS[String(result.reason)] ||
                            result.reason ||
                            '当前数据暂不满足启动条件',
                        );
                      }
                      await loadDetail(Number(detail.id), true);
                      await Promise.all([loadRows(), loadSummary()]);
                    } catch (error: any) {
                      message.error(error?.message || '启动失败');
                    }
                  }}
                >
                  启动流程
                </Button>
              ) : null}
              <Button
                size="small"
                onClick={() => {
                  bindForm.setFieldsValue({ package_ids: detail.package_ids?.join(', ') || '' });
                  setBindOpen(true);
                }}
              >
                绑定数据
              </Button>
            </Space>
          ) : null
        }
      >
        {!detail ? (
          <Typography.Text type="secondary">加载中…</Typography.Text>
        ) : (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Space wrap>
              <Tag color={STAGE_COLORS[detail.stage] || 'default'}>{detail.stage_label || detail.stage}</Tag>
              <Tag>{detail.status_label || detail.status}</Tag>
              <Tag>{REQUEST_TYPE_LABELS[detail.request_type] || detail.request_type}</Tag>
              {detail.coverage?.partial ? <Tag color="orange">部分数据验证</Tag> : null}
            </Space>
            <Progress
              percent={Number(detail.progress?.percent || 0)}
              status={detail.status === 'FAILED' ? 'exception' : undefined}
              format={() => `${detail.progress?.processed || 0}/${detail.progress?.total || 0}`}
            />
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label="任务名称" span={2}>
                {detail.request_title || detail.external_request_id || '-'}
              </Descriptions.Item>
              <Descriptions.Item label="第三方请求 ID">{detail.external_request_id}</Descriptions.Item>
              <Descriptions.Item label="来源系统">{detail.source_system}</Descriptions.Item>
              <Descriptions.Item label="模型">
                <Space size={4} wrap>
                  <span>{modelDisplayText(detail)}</span>
                  {detail.external_model_display?.catalog_id ? (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      目录 #{detail.external_model_display.catalog_id}
                    </Typography.Text>
                  ) : null}
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="事件">{detail.event_label || '-'}</Descriptions.Item>
              <Descriptions.Item label="场景" span={2}>
                {detailSceneEntries.length ? (
                  <Space direction="vertical" size={0}>
                    {detailSceneEntries.map((item) => (
                      <div key={item.key}>
                        <Typography.Text type="secondary">{item.key}</Typography.Text>：
                        {formatSceneValue(item.value)}
                      </div>
                    ))}
                  </Space>
                ) : (
                  '-'
                )}
              </Descriptions.Item>
              <Descriptions.Item label="最小样本数">{detail.min_sample_count}</Descriptions.Item>
              <Descriptions.Item label="工作流">{detail.workflow_id || '-'}</Descriptions.Item>
              <Descriptions.Item label="训练编排">
                {detail.strategy_external_task_id || '-'}
              </Descriptions.Item>
              <Descriptions.Item label="回调地址">{detail.callback_url || '-'}</Descriptions.Item>
            </Descriptions>

            <Card size="small" title="数据覆盖度">
              <Row gutter={12}>
                <Col span={6}><Statistic title="应到" value={detail.coverage?.requested || 0} /></Col>
                <Col span={6}><Statistic title="已到" value={detail.coverage?.available || 0} /></Col>
                <Col span={6}><Statistic title="待补齐" value={detail.coverage?.pending || 0} /></Col>
                <Col span={6}><Statistic title="未知" value={detail.coverage?.unknown || 0} /></Col>
              </Row>
              <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
                数据水位：{detail.coverage?.watermark || '-'}；完整时间段：
                {detail.coverage?.complete_windows?.length
                  ? detail.coverage.complete_windows.map((item: string[]) => `${item[0]} ~ ${item[1]}`).join('；')
                  : '暂无'}
              </Typography.Paragraph>
            </Card>

            <Card size="small" title="人工审核进度">
              <Row gutter={12}>
                <Col span={8}><Statistic title="样本总数" value={detail.review?.total || 0} /></Col>
                <Col span={8}><Statistic title="已完成" value={detail.review?.done || 0} /></Col>
                <Col span={8}><Statistic title="待审核" value={detail.review?.pending || 0} /></Col>
              </Row>
              <Progress
                percent={
                  detail.review?.total
                    ? Math.round((Number(detail.review.done || 0) / Number(detail.review.total)) * 100)
                    : 0
                }
                style={{ marginTop: 12 }}
              />
            </Card>

            <Card size="small" title="批次与图片">
              <Space wrap style={{ marginBottom: 8 }}>
                <Typography.Text>批次：{(detail.package_ids || []).join(', ') || '-'}</Typography.Text>
                <Button size="small" onClick={() => { bindForm.setFieldsValue({ package_ids: detail.package_ids?.join(', ') || '' }); setBindOpen(true); }}>
                  绑定新批次
                </Button>
              </Space>
              <Table
                rowKey="external_image_id"
                size="small"
                pagination={false}
                dataSource={(detail.images || []).slice(0, 100)}
                columns={[
                  { title: '外部图片', dataIndex: 'external_image_id' },
                  { title: '事件 ID', dataIndex: 'external_event_id' },
                  { title: '数据包', dataIndex: 'package_id' },
                  {
                    title: '任务',
                    dataIndex: 'task_id',
                    render: (value) =>
                      value ? (
                        <Typography.Link href={`/tasks/${value}`} target="_blank">
                          #{value}
                        </Typography.Link>
                      ) : (
                        '-'
                      ),
                  },
                  {
                    title: '样本',
                    dataIndex: 'sample_id',
                    render: (value, row: IntegrationRow) =>
                      value ? (
                        <Typography.Link
                          href={row.task_id ? `/tasks/${row.task_id}/samples/${value}` : undefined}
                          target="_blank"
                        >
                          #{value}
                        </Typography.Link>
                      ) : (
                        '-'
                      ),
                  },
                  { title: '事件时间', dataIndex: 'event_time' },
                ]}
                locale={{ emptyText: '暂无图片映射' }}
              />
            </Card>

            {detail.request_type === 'threshold_experiment' ? (
              <Card size="small" title="阈值实验结果">
                {detail.result ? (
                  <Space direction="vertical" style={{ width: '100%' }}>
                    {Array.isArray(detail.result.f1_curve) && detail.result.f1_curve.length ? (
                      <Table
                        rowKey={(row: IntegrationRow) => String(row.threshold)}
                        size="small"
                        pagination={false}
                        dataSource={detail.result.f1_curve}
                        columns={[
                          { title: '阈值', dataIndex: 'threshold' },
                          { title: 'F1', dataIndex: 'f1' },
                          { title: 'Precision', dataIndex: 'precision' },
                          { title: 'Recall', dataIndex: 'recall' },
                        ]}
                      />
                    ) : null}
                    <Descriptions column={3} size="small" bordered>
                      <Descriptions.Item label="最高阈值">{detail.result.best_threshold ?? '-'}</Descriptions.Item>
                      <Descriptions.Item label="平均阈值">{detail.result.average_threshold ?? '-'}</Descriptions.Item>
                      <Descriptions.Item label="最低阈值">{detail.result.minimum_threshold ?? '-'}</Descriptions.Item>
                    </Descriptions>
                    {detail.result.partial || detail.coverage?.partial ? (
                      <Tag color="orange">部分数据验证，结果可能随后续数据变化</Tag>
                    ) : null}
                    {!Array.isArray(detail.result.f1_curve) &&
                    detail.result.best_threshold == null &&
                    detail.result.average_threshold == null ? (
                      <>
                        <Typography.Text type="secondary">原始验证结果</Typography.Text>
                        <pre
                          style={{
                            maxHeight: 220,
                            overflow: 'auto',
                            margin: 0,
                            padding: 8,
                            background: '#fafafa',
                            borderRadius: 4,
                            fontSize: 12,
                          }}
                        >
                          {JSON.stringify(detail.result, null, 2).slice(0, 4000)}
                        </pre>
                      </>
                    ) : null}
                  </Space>
                ) : (
                  <Typography.Text type="secondary">
                    等待边端验证结果；数据补齐后会自动重试。
                  </Typography.Text>
                )}
              </Card>
            ) : null}

            <Card size="small" title="发布决策">
              {detail.publish_decision ? (
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Descriptions column={2} size="small" bordered>
                    <Descriptions.Item label="决策">{detail.publish_decision.decision}</Descriptions.Item>
                    <Descriptions.Item label="自动发布">{detail.publish_decision.auto_publish_eligible ? '是' : '否'}</Descriptions.Item>
                    <Descriptions.Item label="候选模型">{detail.publish_decision.candidate_model_id || '-'}</Descriptions.Item>
                    <Descriptions.Item label="基线模型">{detail.publish_decision.baseline_model_id || '-'}</Descriptions.Item>
                    <Descriptions.Item label="原因" span={2}>{detail.publish_decision.reason || '-'}</Descriptions.Item>
                  </Descriptions>
                  {detail.publish_decision.decision === 'MANUAL_PENDING' ? (
                    <Space>
                      <Button
                        type="primary"
                        onClick={async () => {
                          try {
                            await request.post(`/v2/integrations/requests/${detail.id}/publish-decision/approve`, { decision: 'APPROVED', reason: '页面人工批准' });
                            message.success('已批准发布');
                            await loadDetail(Number(detail.id), true);
                          } catch (error: any) {
                            message.error(error?.message || '批准失败');
                          }
                        }}
                      >
                        批准发布
                      </Button>
                      <Button
                        danger
                        onClick={async () => {
                          try {
                            await request.post(`/v2/integrations/requests/${detail.id}/publish-decision/approve`, { decision: 'REJECTED', reason: '页面人工拒绝' });
                            message.success('已拒绝发布');
                            await loadDetail(Number(detail.id), true);
                          } catch (error: any) {
                            message.error(error?.message || '操作失败');
                          }
                        }}
                      >
                        拒绝
                      </Button>
                    </Space>
                  ) : null}
                  {detail.publish_decision.decision === 'APPROVED' ? (
                    <Button
                      type="primary"
                      onClick={async () => {
                        try {
                          await request.post(`/v2/integrations/requests/${detail.id}/publish`, {});
                          message.success('已提交 AICM 发布');
                          await loadDetail(Number(detail.id), true);
                        } catch (error: any) {
                          message.error(error?.message || '发布失败，请检查边端验证和导出证据');
                        }
                      }}
                    >
                      执行发布
                    </Button>
                  ) : null}
                </Space>
              ) : (
                <Space>
                  <Typography.Text type="secondary">尚未生成发布决策</Typography.Text>
                  <Button
                    size="small"
                    onClick={async () => {
                      try {
                        await request.post(`/v2/integrations/requests/${detail.id}/publish-decision`, {
                          decision: 'MANUAL_PENDING',
                          metrics: {},
                          auto_publish_eligible: false,
                          reason: '页面标记待人工发布',
                        });
                        message.success('已标记待人工发布');
                        await loadDetail(Number(detail.id), true);
                      } catch (error: any) {
                        message.error(error?.message || '操作失败');
                      }
                    }}
                  >
                    标记待人工发布
                  </Button>
                </Space>
              )}
            </Card>

            <Card size="small" title="执行时间线">
              <Timeline
                items={(detail.timeline || []).map((event: IntegrationRow) => ({
                  color: event.to_status === 'FAILED' ? 'red' : event.to_status === 'SUCCEEDED' ? 'green' : 'blue',
                  children: (
                    <Space direction="vertical" size={0}>
                      <Typography.Text strong>
                        {String(event.event_type_label || '').trim() || event.event_type}
                      </Typography.Text>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {String(event.stage_label || '').trim() || event.stage || '-'} ·{' '}
                        {formatDateTime(event.created_at)}
                      </Typography.Text>
                    </Space>
                  ),
                }))}
              />
            </Card>

            <Card
              size="small"
              title="原始请求"
              extra={
                <Button
                  size="small"
                  loading={originalLoading}
                  onClick={() => void loadOriginalRequest(Number(detail.id))}
                >
                  {originalRequestId === Number(detail.id) ? '重新加载' : '查看原始请求'}
                </Button>
              }
            >
              {originalError ? <Alert type="error" showIcon message={originalError} /> : null}
              {originalLoading && !originalRequest ? (
                <Typography.Text type="secondary">加载中…</Typography.Text>
              ) : null}
              {originalRequest && originalRequestId === Number(detail.id) ? (
                <Space direction="vertical" size={10} style={{ width: '100%' }}>
                  <Space wrap size={4}>
                    <Tag color={originalRequest.raw_available ? 'green' : 'orange'}>
                      {originalRequest.raw_available ? '原始' : '还原'}
                    </Tag>
                    {originalRequest.source ? <Tag>{originalRequest.source}</Tag> : null}
                    {originalRequest.truncated ? <Tag color="red">已截断</Tag> : null}
                  </Space>
                  <Descriptions column={2} size="small" bordered>
                    <Descriptions.Item label="SHA256" span={2}>
                      <Typography.Text
                        copyable={{ text: String(originalRequest.raw_sha256 || '') }}
                        style={{ wordBreak: 'break-all' }}
                      >
                        {originalRequest.raw_sha256 || '-'}
                      </Typography.Text>
                    </Descriptions.Item>
                    <Descriptions.Item label="字节数">
                      {originalRequest.raw_bytes ?? '-'}
                    </Descriptions.Item>
                    <Descriptions.Item label="接收时间">
                      {formatDateTime(originalRequest.received_at)}
                    </Descriptions.Item>
                    <Descriptions.Item label="接入路径" span={2}>
                      {originalRequest.intake?.method || '-'}{' '}
                      {originalRequest.intake?.path || '-'}
                      {originalRequest.intake?.outcome
                        ? ` · ${originalRequest.intake.outcome}`
                        : ''}
                    </Descriptions.Item>
                  </Descriptions>
                  {!originalRequest.raw_available && originalRequest.warning ? (
                    <Alert type="warning" showIcon message={originalRequest.warning} />
                  ) : null}
                  <div style={{ position: 'relative' }}>
                    <Button
                      size="small"
                      style={{ position: 'absolute', right: 8, top: 8, zIndex: 1 }}
                      onClick={() => {
                        const text = originalPayloadText(originalRequest);
                        if (navigator.clipboard?.writeText) {
                          navigator.clipboard
                            .writeText(text)
                            .then(() => message.success('原始请求已复制'))
                            .catch(() => message.error('复制失败，请手动选择文本'));
                        }
                      }}
                    >
                      复制 JSON
                    </Button>
                    <pre
                      style={{
                        maxHeight: 360,
                        overflow: 'auto',
                        margin: 0,
                        padding: 12,
                        background: '#fafafa',
                        borderRadius: 4,
                        fontSize: 12,
                      }}
                    >
                      {originalPayloadText(originalRequest)}
                    </pre>
                  </div>
                </Space>
              ) : !originalError && !originalLoading ? (
                <Typography.Text type="secondary">
                  按需加载，不参与详情轮询；仅系统管理员可见。
                </Typography.Text>
              ) : null}
            </Card>
          </Space>
        )}
      </Drawer>
    </>
  );
}

export default IntegrationRequestsPanel;
