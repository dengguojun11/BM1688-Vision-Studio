import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined, ApiOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Descriptions, Empty, Form, Input, InputNumber, Modal, Select, Space, Switch, Table, Tag, Typography, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState } from 'react';

import request from '@/api/request';
import ServerSearchInput from '@/components/ServerSearchInput';
import { TablePanel, listTotal, responseStatus } from '../shared';

type ModelAccessConfig = {
  id: string | number;
  name?: string;
  provider_key?: string;
  capability?: string;
  endpoint?: string;
  model?: string;
  enabled?: boolean;
  timeout_seconds?: number;
  verify_tls?: boolean;
  max_new_tokens?: number;
  auth_type?: string;
  auth_header?: string;
  request_protocol?: string;
  is_default?: boolean;
  api_key_configured?: boolean;
  api_key_masked?: string;
  available?: boolean;
  last_check_status?: string | null;
  last_check_message?: string | null;
  last_check_at?: string | null;
};

type ModelAccessConfigForm = {
  name: string;
  provider_key: string;
  capability: string;
  endpoint: string;
  model?: string;
  enabled: boolean;
  timeout_seconds: number;
  verify_tls: boolean;
  max_new_tokens?: number;
  auth_type: string;
  auth_header: string;
  request_protocol: string;
  is_default: boolean;
  api_key?: string;
};

const PROVIDER_OPTIONS = [
  { value: 'tagtool', label: '全局标签模型' },
  { value: 'locateanything', label: 'LocateAnything 检测模型' },
  { value: 'vlx_seek', label: 'VLX-Seek 检测模型' },
  { value: 'rex_omni', label: 'Rex-Omni 检测模型' },
  { value: 'vision_llm', label: '通用视觉模型（自动标注/图文报告）' },
  { value: 'volcengine', label: '火山方舟 Coding Plan 个人版（视觉理解）' },
];

// 选中「火山方舟」时自动预填官方默认接入参数，减少手工输入。
// 注意：必须用 Coding 套餐地址 /api/coding/v3（OpenAI 兼容）或 /api/coding
// （Anthropic 兼容），用 /api/v3 会走按量计费产生额外费用。
const VOLCENGINE_CODING_ENDPOINT = 'https://ark.cn-beijing.volces.com/api/coding/v3';
const VOLCENGINE_DEFAULTS: Partial<ModelAccessConfigForm> = {
  capability: 'vision_reasoning',
  endpoint: VOLCENGINE_CODING_ENDPOINT,
  model: 'doubao-seed-2-1-pro-260915',
  timeout_seconds: 120,
  auth_type: 'bearer',
  request_protocol: 'openai_compatible',
};

const defaultTimeoutSeconds = (capability?: string) => capability === 'bbox_detection' ? 120 : 30;

const CAPABILITY_OPTIONS = [
  { value: 'global_tag', label: '全局标签预标注' },
  { value: 'bbox_detection', label: '目标检测预标注' },
  { value: 'vision_reasoning', label: '视觉理解（自动标注/图文报告）' },
];

const AUTH_OPTIONS = [
  { value: 'none', label: '无需鉴权' },
  { value: 'bearer', label: 'Bearer 令牌' },
  { value: 'api_key', label: 'API 密钥' },
  { value: 'header', label: '自定义请求头' },
];

const REQUEST_PROTOCOL_OPTIONS = [
  { value: 'auto', label: '自动识别' },
  { value: 'openai_compatible', label: 'OpenAI 兼容接口（图文/全局标签）' },
  { value: 'multipart', label: '文件上传接口（图片推理）' },
];

function protocolLabel(value?: string) {
  return REQUEST_PROTOCOL_OPTIONS.find((item) => item.value === value)?.label || value || '自动识别';
}

function providerLabel(value?: string) {
  return PROVIDER_OPTIONS.find((item) => item.value === value)?.label || value || '未设置';
}

function capabilityLabel(value?: string) {
  return CAPABILITY_OPTIONS.find((item) => item.value === value)?.label || value || '未设置';
}

export function validateEndpoint(value: string, providerKey?: string) {
  if (providerKey !== 'volcengine') return undefined;
  const endpoint = String(value || '').trim();
  // Coding Plan has a separate billing route. Keep the pay-as-you-go /api/v3
  // route out of the admin form so an accidental paste cannot incur charges.
  if (![VOLCENGINE_CODING_ENDPOINT, `${VOLCENGINE_CODING_ENDPOINT}/`, `${VOLCENGINE_CODING_ENDPOINT}/chat/completions`].includes(endpoint)) {
    return `当前识别链使用 OpenAI 协议，火山 Coding Plan 地址必须为 ${VOLCENGINE_CODING_ENDPOINT}`;
  }
  return undefined;
}

function normalizeRows(payload: any): ModelAccessConfig[] {
  const candidate = payload?.data ?? payload;
  if (Array.isArray(candidate)) return candidate;
  if (Array.isArray(candidate?.configs)) return candidate.configs;
  if (Array.isArray(candidate?.items)) return candidate.items;
  return [];
}

/**
 * The request interceptor returns the business envelope directly at runtime,
 * while page tests often mock the Axios-shaped `{ data: envelope }` value.
 * Normalize both without changing the public API response shape.
 */
function unwrapRequestPayload(payload: any): any {
  if (payload && typeof payload === 'object' && payload.meta_data !== undefined) return payload;
  if (payload && typeof payload === 'object' && payload.data !== undefined) return payload.data;
  return payload;
}

function errorText(error: unknown) {
  const status = responseStatus(error);
  if (status === 403) return '只有管理人员可以维护模型接入配置';
  if (status === 409) return '同一模型能力已存在配置，请编辑现有配置';
  return '模型接入配置保存失败，请检查后重试';
}

function checkStatusTag(row: ModelAccessConfig) {
  const status = String(row.last_check_status || '').toLowerCase();
  if (['passed', 'pass', 'ok', 'success', 'ready'].includes(status)) return <Tag color="green">连通正常</Tag>;
  if (['failed', 'fail', 'error'].includes(status)) return <Tag color="red">检查失败</Tag>;
  return <Tag color="gold">待检查</Tag>;
}

export default function ModelAccessConfigPanel() {
  const queryClient = useQueryClient();
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<ModelAccessConfig | null>(null);
  const [form] = Form.useForm<ModelAccessConfigForm>();
  const selectedProvider = Form.useWatch('provider_key', form);
  const selectedCapability = Form.useWatch('capability', form);
  const [search, setSearch] = useState('');
  const [searchDraft, setSearchDraft] = useState('');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(10);
  const configsQuery = useQuery({
    queryKey: ['ai-model-configs', search, page, pageSize],
    queryFn: async () => {
      const response = await request.get('/v1/ai-model-configs', {
        params: { page, size: pageSize, ...(search ? { q: search } : {}) },
      });
      return unwrapRequestPayload(response);
    },
    staleTime: 5_000,
    placeholderData: (previous) => previous,
  });
  const configs = normalizeRows(configsQuery.data);
  const configTotal = configsQuery.data ? listTotal(configsQuery.data, configs.length) : 0;
  const enabledCount = configs.filter((item) => item.enabled !== false).length;
  const checkedCount = configs.filter((item) => item.available === true || (item.enabled !== false && ['passed', 'pass', 'ok', 'success', 'ready'].includes(String(item.last_check_status || '').toLowerCase()))).length;
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['ai-model-configs'] });
  const applySearch = (value: string) => {
    const next = value.trim();
    setSearchDraft(value);
    setSearch(next);
    setPage(0);
  };

  const saveMutation = useMutation({
    mutationFn: async (values: ModelAccessConfigForm) => {
      const body: Record<string, unknown> = {
        name: values.name.trim(),
        provider_key: values.provider_key,
        capability: values.capability,
        endpoint: values.endpoint.trim(),
        model: values.model?.trim() || undefined,
        enabled: Boolean(values.enabled),
        timeout_seconds: values.timeout_seconds,
        verify_tls: Boolean(values.verify_tls),
        max_new_tokens: values.max_new_tokens || undefined,
        auth_type: values.auth_type,
        auth_header: values.auth_header.trim() || 'Authorization',
        request_protocol: values.request_protocol,
        is_default: Boolean(values.is_default),
      };
      if (values.api_key?.trim()) body.api_key = values.api_key.trim();
      return editing
        ? (await request.patch(`/v1/ai-model-configs/${encodeURIComponent(String(editing.id))}`, body)).data
        : (await request.post('/v1/ai-model-configs', body)).data;
    },
    onSuccess: () => {
      message.success(editing ? '模型接入配置已更新' : '模型接入配置已新增');
      setEditorOpen(false);
      setEditing(null);
      form.resetFields();
      refresh();
    },
    onError: (error) => message.error(errorText(error)),
  });
  const toggleMutation = useMutation({
    mutationFn: async (row: ModelAccessConfig) =>
      (await request.patch(`/v1/ai-model-configs/${encodeURIComponent(String(row.id))}`, { enabled: row.enabled === false })).data,
    onSuccess: () => {
      message.success('模型接入状态已更新');
      refresh();
    },
    onError: (error) => message.error(errorText(error)),
  });
  const checkMutation = useMutation({
    mutationFn: async (row: ModelAccessConfig) =>
      (await request.post(`/v1/ai-model-configs/${encodeURIComponent(String(row.id))}/test`)).data,
    onSuccess: (payload: any) => {
      const data = payload?.data || payload;
      message.success(data?.message || '模型服务连通性检查已完成');
      refresh();
    },
    onError: (error: any) => message.error(error?.response?.data?.message || error?.message || '模型服务连通性检查失败'),
  });
  const deleteMutation = useMutation({
    mutationFn: async (row: ModelAccessConfig) => (await request.delete(`/v1/ai-model-configs/${encodeURIComponent(String(row.id))}`)).data,
    onSuccess: () => {
      message.success('模型接入配置已删除');
      refresh();
    },
    onError: (error) => message.error(errorText(error)),
  });

  const openEditor = (row?: ModelAccessConfig) => {
    setEditing(row || null);
    form.setFieldsValue(
      row
        ? {
            name: row.name || '',
            provider_key: row.provider_key || 'vision_llm',
            capability: row.capability || 'vision_reasoning',
            endpoint: row.endpoint || '',
            model: row.model || '',
            enabled: row.enabled !== false,
            timeout_seconds: row.timeout_seconds || defaultTimeoutSeconds(row.capability),
            verify_tls: row.verify_tls !== false,
            max_new_tokens: row.max_new_tokens || 1024,
            auth_type: row.auth_type || 'none',
            auth_header: row.auth_header || 'Authorization',
            request_protocol: row.request_protocol || 'auto',
            is_default: Boolean(row.is_default),
            api_key: '',
          }
        : {
            name: '',
            provider_key: 'vision_llm',
            capability: 'vision_reasoning',
            endpoint: '',
            model: '',
            enabled: true,
            timeout_seconds: defaultTimeoutSeconds('vision_reasoning'),
            verify_tls: true,
            max_new_tokens: 1024,
            auth_type: 'none',
            auth_header: 'Authorization',
            request_protocol: 'auto',
            is_default: false,
            api_key: '',
          },
    );
    setEditorOpen(true);
  };

  const columns: ColumnsType<ModelAccessConfig> = [
    {
      title: '配置名称',
      key: 'name',
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{row.name || '未命名配置'}</Typography.Text>
          <Typography.Text type="secondary">{providerLabel(row.provider_key)}</Typography.Text>
          {row.is_default ? <Tag color="purple">当前默认</Tag> : null}
        </Space>
      ),
    },
    { title: '模型能力', key: 'capability', render: (_, row) => <Tag color="blue">{capabilityLabel(row.capability)}</Tag> },
    {
      title: '服务地址 / 模型',
      key: 'endpoint',
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Typography.Text ellipsis style={{ maxWidth: 280 }}>{row.endpoint || '-'}</Typography.Text>
          <Typography.Text type="secondary">{row.model || '未填写模型标识'}</Typography.Text>
        </Space>
      ),
    },
    { title: '请求协议', key: 'request_protocol', render: (_, row) => <Tag>{protocolLabel(row.request_protocol)}</Tag> },
    { title: '启用状态', key: 'enabled', render: (_, row) => <Tag color={row.enabled === false ? 'default' : 'green'}>{row.enabled === false ? '已停用' : '已启用'}</Tag> },
    {
      title: '连通性',
      key: 'check',
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          {checkStatusTag(row)}
          {row.last_check_message ? <Typography.Text type="secondary" ellipsis style={{ maxWidth: 220 }}>{row.last_check_message}</Typography.Text> : null}
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 300,
      render: (_, row) => (
        <Space size={0} wrap>
          <Button type="link" size="small" loading={checkMutation.isPending} onClick={() => checkMutation.mutate(row)}>连通性检查</Button>
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openEditor(row)}>编辑</Button>
          <Button type="link" size="small" onClick={() => toggleMutation.mutate(row)} loading={toggleMutation.isPending}>{row.enabled === false ? '启用' : '停用'}</Button>
          <Button type="link" danger size="small" icon={<DeleteOutlined />} onClick={() => Modal.confirm({ title: '删除模型接入配置？', content: '删除后任务预标注将无法继续使用该模型服务。', okText: '删除', cancelText: '取消', okButtonProps: { danger: true }, onOk: () => deleteMutation.mutateAsync(row) })}>删除</Button>
        </Space>
      ),
    },
  ];

  return (
    <TablePanel>
      <Space style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
        <div>
          <h2 style={{ marginBottom: 4 }}>模型接入配置</h2>
          <Typography.Text type="secondary">统一配置部署在服务器上的视觉模型服务，任务预标注只会使用已启用且连通检查通过的配置。</Typography.Text>
        </div>
        <Space>
          <Button onClick={() => window.history.back()}>返回系统管理</Button>
          <Button icon={<ReloadOutlined />} loading={configsQuery.isFetching} onClick={() => void configsQuery.refetch()}>刷新状态</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => openEditor()}>新增模型配置</Button>
        </Space>
      </Space>
      <Alert
        type="info"
        showIcon
        icon={<ApiOutlined />}
        message="配置范围"
        description="目标检测模型可选择“目标检测预标注”，全局标签模型选择“全局标签预标注”。火山方舟两种能力都使用同一 Coding Plan 地址，但请分别新增配置并填写对应模型；YOLO 检测框和上传 JSON/JSONL 预标注文件不依赖大模型。"
        style={{ marginBottom: 16 }}
      />
      <Space style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', marginBottom: 12 }}>
        <ServerSearchInput
          value={searchDraft}
          onChange={(value) => {
            setSearchDraft(value);
            if (!value) applySearch('');
          }}
          onSearch={applySearch}
          placeholder="按配置名称、模型、地址或能力搜索"
          ariaLabel="搜索模型接入配置"
          loading={configsQuery.isFetching}
          style={{ width: 320, maxWidth: '100%' }}
        />
        <Typography.Text type="secondary">
          {search ? `搜索“${search}”匹配 ${configTotal} 个配置` : `共 ${configTotal} 个配置`}
        </Typography.Text>
      </Space>
      <Descriptions bordered size="small" column={3} style={{ marginBottom: 16 }}>
        <Descriptions.Item label="配置总数">{configTotal}</Descriptions.Item>
        <Descriptions.Item label="已启用">{enabledCount}</Descriptions.Item>
        <Descriptions.Item label="当前可用">{checkedCount}</Descriptions.Item>
      </Descriptions>
      {configsQuery.error ? <Alert type="error" showIcon message="模型接入配置加载失败" description={errorText(configsQuery.error)} /> : null}
      <Table<ModelAccessConfig>
        style={{ marginTop: 12 }}
        rowKey={(row) => String(row.id)}
        size="small"
        loading={configsQuery.isLoading}
        dataSource={configs}
        columns={columns}
        pagination={{
          current: page + 1,
          pageSize,
          total: configTotal,
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
        scroll={{ x: 1320 }}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={search ? '没有匹配的模型接入配置' : '暂无模型接入配置，请先新增一个模型服务'} /> }}
      />
      <Modal
        title={editing ? '编辑模型接入配置' : '新增模型接入配置'}
        open={editorOpen}
        centered
        destroyOnClose
        width={680}
        confirmLoading={saveMutation.isPending}
        onCancel={() => { setEditorOpen(false); setEditing(null); form.resetFields(); }}
        onOk={() => void form.validateFields().then((values) => saveMutation.mutate(values))}
        okText="保存配置"
        cancelText="取消"
      >
        <Typography.Paragraph type="secondary">保存后请执行“连通性检查”。只有启用且检查通过的服务才会出现在任务预标注可选能力中。</Typography.Paragraph>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="配置名称" rules={[{ required: true, whitespace: true, message: '请输入配置名称' }]}><Input maxLength={64} placeholder="例如：生产 VLX-Seek" /></Form.Item>
          <Space style={{ display: 'flex' }} align="start">
            <Form.Item name="provider_key" label="模型类型" rules={[{ required: true }]} style={{ flex: 1 }}>
              <Select
                options={PROVIDER_OPTIONS}
                onChange={(value: string) => {
                  if (value === 'volcengine') {
                    form.setFieldsValue({ ...VOLCENGINE_DEFAULTS, name: form.getFieldValue('name') });
                  }
                }}
              />
            </Form.Item>
            <Form.Item name="capability" label="提供能力" rules={[{ required: true }]} style={{ flex: 1 }}>
              <Select
                options={CAPABILITY_OPTIONS}
                onChange={(value: string) => {
                  if (value === 'bbox_detection' && Number(form.getFieldValue('timeout_seconds') || 0) < 120) {
                    form.setFieldsValue({ timeout_seconds: 120 });
                  }
                }}
              />
            </Form.Item>
          </Space>
          {selectedProvider === 'volcengine' ? (
            <Alert
              type="warning"
              showIcon
              message="火山方舟 Coding Plan 个人版"
              description="当前识别链使用 OpenAI 兼容地址 /api/coding/v3。/api/v3 为按量计费地址，不允许保存。套餐适用范围以火山官方说明为准。"
              style={{ marginBottom: 16 }}
            />
          ) : null}
          <Form.Item
            name="endpoint"
            label="服务地址"
            dependencies={['provider_key']}
            rules={[
              { required: true, type: 'url', message: '请输入完整的 HTTP/HTTPS 服务地址' },
              ({ getFieldValue }) => ({
                validator: async (_, value) => {
                  const error = validateEndpoint(value, getFieldValue('provider_key'));
                  if (error) return Promise.reject(new Error(error));
                  return Promise.resolve();
                },
              }),
            ]}
            extra={selectedProvider === 'volcengine' ? 'Coding Plan：填写 https://ark.cn-beijing.volces.com/api/coding/v3。' : '填写部署模型服务的完整地址，例如 https://服务器地址:端口/api/infer'}
          >
            <Input placeholder={selectedProvider === 'volcengine' ? VOLCENGINE_CODING_ENDPOINT : 'https://服务器地址:端口/api/infer'} />
          </Form.Item>
          <Form.Item name="request_protocol" label="请求协议" extra={selectedProvider === 'volcengine' ? '当前视觉识别链路使用 OpenAI 兼容 Chat Completions，请选择该协议。' : '18081 图文/全局标签服务通常选择 OpenAI 兼容接口；8099 检测服务按对应模型接口填写。'}><Select options={REQUEST_PROTOCOL_OPTIONS} /></Form.Item>
          <Form.Item name="model" label="模型标识"><Input maxLength={128} placeholder={selectedProvider === 'volcengine' ? '例如：doubao-seed-2-1-pro-260915' : '按服务支持的模型填写；不需要时可留空'} /></Form.Item>
          <Space style={{ display: 'flex' }} align="start">
            <Form.Item name="auth_type" label="鉴权方式" rules={[{ required: true }]} style={{ flex: 1 }}><Select options={AUTH_OPTIONS} /></Form.Item>
            <Form.Item name="api_key" label={editing ? '鉴权密钥（留空表示不修改）' : '鉴权密钥'} style={{ flex: 1 }}><Input.Password autoComplete="new-password" placeholder="只在保存时提交，页面不回显" /></Form.Item>
          </Space>
          <Space style={{ display: 'flex' }} align="start">
            <Form.Item name="auth_header" label="鉴权请求头名称" extra="仅自定义请求头方式使用；默认 Authorization" style={{ flex: 1 }}><Input maxLength={128} /></Form.Item>
            <Form.Item name="is_default" label="设为该能力默认配置" valuePropName="checked" style={{ flex: 1 }}><Switch /></Form.Item>
          </Space>
          <Space style={{ display: 'flex' }} align="start">
            <Form.Item
              name="timeout_seconds"
              label="超时时间（秒）"
              extra={selectedCapability === 'bbox_detection' ? '目标检测至少需要 120 秒，避免图像理解响应尚未返回就超时。' : undefined}
              rules={[
                { required: true, type: 'number', min: selectedCapability === 'bbox_detection' ? 120 : 1, max: 600 },
              ]}
              style={{ flex: 1 }}
            >
              <InputNumber style={{ width: '100%' }} precision={0} min={selectedCapability === 'bbox_detection' ? 120 : 1} />
            </Form.Item>
            <Form.Item name="max_new_tokens" label="最大输出长度" rules={[{ type: 'number', min: 1, max: 32768 }]} style={{ flex: 1 }}><InputNumber style={{ width: '100%' }} precision={0} /></Form.Item>
          </Space>
          <Space>
            <Form.Item name="enabled" label="启用配置" valuePropName="checked"><Switch /></Form.Item>
            <Form.Item name="verify_tls" label="校验 HTTPS 证书" valuePropName="checked"><Switch /></Form.Item>
          </Space>
        </Form>
      </Modal>
    </TablePanel>
  );
}
