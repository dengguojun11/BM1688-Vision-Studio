import { EditOutlined, ExperimentOutlined, ReloadOutlined } from '@ant-design/icons';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Checkbox, Form, Input, Modal, Space, Tag, message } from 'antd';
import { useEffect, useState } from 'react';

import request from '@/api/request';
import ResourcePage from '../ResourcePage';

type ProviderEvidenceValues = {
  image_digest?: string;
  toolchain_version?: string;
  hil_verified?: boolean;
  hil_evidence?: string;
  runtime_evidence?: string;
  availability_reason?: string;
  status?: string;
};

function jsonText(value: unknown) {
  return value && typeof value === 'object' ? JSON.stringify(value, null, 2) : '';
}

export function ProviderEvidenceEditor({ provider, open, onClose }: { provider: any | null; open: boolean; onClose: () => void }) {
  const [form] = Form.useForm<ProviderEvidenceValues>();
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: async (values: ProviderEvidenceValues) => {
      const parse = (value?: string) => {
        if (!value?.trim()) return undefined;
        const parsed = JSON.parse(value);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('证据必须是 JSON 对象');
        return parsed;
      };
      return request.patch(`/v1/providers/${encodeURIComponent(String(provider?.provider_key).trim().toLowerCase())}/evidence`, {
        provider_version: provider?.provider_version,
        image_digest: values.image_digest?.trim() || undefined,
        toolchain_version: values.toolchain_version?.trim() || undefined,
        hil_verified: values.hil_verified,
        hil_evidence: parse(values.hil_evidence),
        runtime_evidence: parse(values.runtime_evidence),
        availability_reason: values.availability_reason?.trim() || undefined,
        status: values.status,
      });
    },
    onSuccess: () => {
      message.success('Provider 证据已更新');
      onClose();
      void queryClient.invalidateQueries({ queryKey: ['providers'] });
    },
    onError: (error: any) => message.error(error?.response?.status === 403 ? '只有平台管理员可以更新 Provider 证据' : error?.message || 'Provider 证据更新失败'),
  });
  useEffect(() => {
    if (!open || !provider) return;
    form.setFieldsValue({
      image_digest: provider.image_digest?.replace(/^sha256:/, '') || '',
      toolchain_version: provider.toolchain_version || '',
      hil_verified: provider.hil_verified,
      hil_evidence: jsonText(provider.hil_evidence),
      runtime_evidence: jsonText(provider.runtime_evidence),
      availability_reason: provider.availability_reason || '',
      status: provider.status || 'ACTIVE',
    });
  }, [form, open, provider]);
  return <Modal title={`Provider 证据 · ${provider?.display_name || provider?.provider_key || ''}`} open={open} centered width={680} destroyOnClose confirmLoading={mutation.isPending} okText="保存证据" cancelText="取消" onCancel={() => { if (!mutation.isPending) onClose(); }} onOk={() => void form.validateFields().then((values) => mutation.mutate(values)).catch(() => undefined)}>
    <Alert type="info" showIcon message="证据仅用于发布门禁；运行时健康状态仍由服务端适配器探测决定。" style={{ marginBottom: 16 }} />
    <Form form={form} layout="vertical">
      <Form.Item name="image_digest" label="镜像 SHA256" rules={[{ pattern: /^(?:sha256:)?[a-fA-F0-9]{64}$/, message: '请输入 64 位 SHA256，可带 sha256: 前缀' }]}><Input placeholder="sha256:..." /></Form.Item>
      <Form.Item name="toolchain_version" label="工具链版本"><Input placeholder="例如 sophgo-3.2" /></Form.Item>
      <Form.Item name="hil_verified" label="HIL 验证结果" valuePropName="checked"><Checkbox>已通过 HIL 验证</Checkbox></Form.Item>
      <Form.Item name="hil_evidence" label="HIL 证据 JSON"><Input.TextArea rows={4} placeholder='{"status":"PASSED","evidence_id":"hil-20260904"}' /></Form.Item>
      <Form.Item name="runtime_evidence" label="运行时证据 JSON"><Input.TextArea rows={3} placeholder='{"status":"READY","agent":"edge-sim-01"}' /></Form.Item>
      <Form.Item name="availability_reason" label="可用性说明"><Input.TextArea rows={2} maxLength={2000} /></Form.Item>
      <Form.Item name="status" label="目录状态"><Input placeholder="ACTIVE / DISABLED / DEPRECATED" /></Form.Item>
    </Form>
  </Modal>;
}

export function ProviderRuntimeActions({ provider }: { provider: any }) {
  const queryClient = useQueryClient();
  const runtimeMutation = useMutation({
    mutationFn: () => request.post(`/v1/providers/${encodeURIComponent(String(provider?.provider_key).trim().toLowerCase())}/runtime-check`, null, { params: { provider_version: provider?.provider_version } }),
    onSuccess: () => {
      message.success('运行时探测已完成');
      void queryClient.invalidateQueries({ queryKey: ['providers'] });
    },
    onError: (error: any) => message.error(error?.response?.data?.message || error?.message || '运行时探测失败'),
  });
  const hilMutation = useMutation({
    mutationFn: () => request.post(`/v1/providers/${encodeURIComponent(String(provider?.provider_key).trim().toLowerCase())}/local-hil`, {
      provider_version: provider?.provider_version,
      sample_uri: 'file:///7gai-hil/sample.jpg',
      labels: [{ name: 'person', tool: 'rectTool' }],
    }),
    onSuccess: () => {
      message.success('本地 HIL 合同探测已完成；不会授予生产 HIL 资格');
      void queryClient.invalidateQueries({ queryKey: ['providers'] });
    },
    onError: (error: any) => message.error(error?.response?.data?.message || error?.message || '本地 HIL 探测失败'),
  });
  const busy = runtimeMutation.isPending || hilMutation.isPending;
  return <Space size={0} wrap>
    <Button type="link" size="small" icon={<ReloadOutlined />} loading={runtimeMutation.isPending} disabled={busy} onClick={() => runtimeMutation.mutate()}>运行探测</Button>
    <Button type="link" size="small" icon={<ExperimentOutlined />} loading={hilMutation.isPending} disabled={busy} onClick={() => hilMutation.mutate()}>本地 HIL</Button>
  </Space>;
}

export default function ProvidersPage() {
  const [editing, setEditing] = useState<any | null>(null);
  const [search, setSearch] = useState('');
  const runtimeTag = (row: any) => {
    if (row.runtime_ready === true || (row.available === true && row.runtime_healthy === true)) {
      return <Tag color="green">运行时就绪</Tag>;
    }
    if (row.runtime_registered === false) return <Tag color="gold">未注册</Tag>;
    if (row.runtime_healthy === false) return <Tag color="red">不健康</Tag>;
    return <Tag color="default">待探测</Tag>;
  };
  return (
    <>
      <ResourcePage
      module="providers"
      queryKey="providers"
      needsProject={false}
      queryFn={async (_projectId, page = 0, size = 20, query) => await request.get('/v1/providers', { params: { status: 'ACTIVE', page, size, q: query || undefined } })}
      pagination
      pageSize={20}
      search={search}
      onSearchChange={setSearch}
      searchPlaceholder="按 Provider 名称、标识或能力搜索"
      searchAriaLabel="搜索 Provider"
      staleTime={60_000}
      beforeTable={<Alert type="info" showIcon message="目录状态与运行时状态分离" description="ACTIVE 仅表示 Provider 已登记；只有运行时注册、健康检查和 HIL 证据均满足时，才可用于训练或发布。" />}
      detailPath={(row) => row.id && row.provider_key ? `/v1/providers/${encodeURIComponent(String(row.provider_key))}` : null}
      rowActions={(row) => <Space size={0} wrap><ProviderRuntimeActions provider={row} /><Button type="link" size="small" icon={<EditOutlined />} onClick={() => setEditing(row)}>管理证据</Button></Space>}
      columns={[
        { title: 'Provider', dataIndex: 'display_name', key: 'display_name', ellipsis: true },
        { title: '标识 / 版本', key: 'provider', render: (_, row) => `${row.provider_key || '-'} / ${row.provider_version || '-'}`, ellipsis: true },
        { title: '运行时', dataIndex: 'runtime_adapter', key: 'runtime_adapter', ellipsis: true },
        { title: '目录状态', dataIndex: 'status', key: 'status', render: (value) => <Tag color={value === 'ACTIVE' ? 'blue' : 'default'}>{value || 'UNKNOWN'}</Tag> },
        { title: '运行时状态', key: 'runtime', render: (_, row) => runtimeTag(row) },
        { title: '发布资格', dataIndex: 'production_ready', key: 'production_ready', render: (value, row) => <Tag color={value ? 'green' : 'gold'}>{value ? '生产就绪' : row.availability_reason || '证据不足'}</Tag> },
      ]}
    />
    <ProviderEvidenceEditor provider={editing} open={Boolean(editing)} onClose={() => setEditing(null)} />
    </>
  );
}
