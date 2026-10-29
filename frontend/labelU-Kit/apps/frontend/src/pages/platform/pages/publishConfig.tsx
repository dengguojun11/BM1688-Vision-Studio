import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Descriptions, Empty, Form, Input, InputNumber, Modal, Select, Space, Switch, Table, Tag, Typography, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState } from 'react';

import request from '@/api/request';
import ServerSearchInput from '@/components/ServerSearchInput';
import { TablePanel, responseStatus } from '../shared';

type PublishEnvironment = {
  id: string;
  name?: string;
  base_url?: string;
  username?: string;
  password_configured?: boolean;
  password_runtime_available?: boolean;
  password_masked?: string;
  chip_type?: string;
  algorithm_type?: string;
  verify_ssl?: boolean;
  ready_to_publish?: boolean;
  missing_fields?: string[];
  readiness_reason?: string;
  is_active?: boolean;
  updated_at?: string | null;
};

type PublishEnvironmentBundle = {
  active_environment_id?: string;
  active_environment?: PublishEnvironment;
  environments?: PublishEnvironment[];
  total?: number;
  ready_count?: number;
  page?: number | null;
  size?: number;
};

const ENVIRONMENT_PAGE_SIZE_OPTIONS = [10, 20, 50];

type PublishEnvironmentForm = {
  name?: string;
  scheme: 'http' | 'https';
  host: string;
  port: number;
  username: string;
  password?: string;
  chip_type?: string;
  algorithm_type?: string;
  verify_ssl?: boolean;
};

function parseAddress(value?: string) {
  const raw = String(value || '').trim();
  try {
    const parsed = new URL(raw.includes('://') ? raw : `http://${raw}`);
    return {
      scheme: parsed.protocol === 'https:' ? 'https' : 'http',
      host: parsed.hostname,
      port: Number(parsed.port || 8102),
    } as const;
  } catch {
    return { scheme: 'http' as const, host: raw, port: 8102 };
  }
}

function buildAddress(values: PublishEnvironmentForm) {
  const host = values.host.trim().replace(/^https?:\/\//i, '').replace(/\/$/, '');
  return `${values.scheme}://${host}:${Number(values.port || 8102)}`;
}

function environmentError(error: unknown) {
  const status = responseStatus(error);
  if (status === 403) return '只有管理人员可以维护发布配置';
  return '发布配置保存失败，请检查连接信息后重试';
}

export default function PublishConfigPanel() {
  const queryClient = useQueryClient();
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<PublishEnvironment | null>(null);
  const [search, setSearch] = useState('');
  const [searchDraft, setSearchDraft] = useState('');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(ENVIRONMENT_PAGE_SIZE_OPTIONS[0]);
  const [form] = Form.useForm<PublishEnvironmentForm>();
  const environmentsQuery = useQuery<PublishEnvironmentBundle>({
    queryKey: ['aicm-publish-environments', page, pageSize, search],
    queryFn: async () =>
      (
        await request.get('/v1/yolo-aicm-publish/environments', {
          params: { page, size: pageSize, ...(search ? { q: search } : {}) },
        })
      ).data,
    staleTime: 5_000,
  });
  const bundle = environmentsQuery.data || {};
  const environments = bundle.environments || [];
  const active = bundle.active_environment || environments.find((item) => item.id === bundle.active_environment_id);
  const total = Number(bundle.total ?? environments.length);
  const readyCount = Number(bundle.ready_count ?? environments.filter((item) => item.ready_to_publish).length);

  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['aicm-publish-environments'] });
  const saveMutation = useMutation({
    mutationFn: async (values: PublishEnvironmentForm) => {
      const body = {
        name: values.name?.trim() || undefined,
        base_url: buildAddress(values),
        username: values.username.trim(),
        chip_type: values.chip_type?.trim() || undefined,
        algorithm_type: values.algorithm_type?.trim() || undefined,
        verify_ssl: Boolean(values.verify_ssl),
        ...(editing
          ? {
              password_secret_action: values.password?.trim() ? 'set' : 'keep',
              ...(values.password?.trim() ? { password: values.password.trim() } : {}),
            }
          : { password: values.password?.trim() || '' }),
      };
      return editing
        ? (await request.patch(`/v1/yolo-aicm-publish/environments/${editing.id}`, body)).data
        : (await request.post('/v1/yolo-aicm-publish/environments', body)).data;
    },
    onSuccess: () => {
      message.success(editing ? '发布环境已更新' : '发布环境已新增');
      setEditorOpen(false);
      setEditing(null);
      form.resetFields();
      refresh();
    },
    onError: (error) => message.error(environmentError(error)),
  });
  const activeMutation = useMutation({
    mutationFn: async (environmentId: string) =>
      (await request.put('/v1/yolo-aicm-publish/environments/active', { environment_id: environmentId })).data,
    onSuccess: () => {
      message.success('当前发布环境已切换');
      refresh();
    },
    onError: (error) => message.error(environmentError(error)),
  });
  const deleteMutation = useMutation({
    mutationFn: async (environmentId: string) =>
      (await request.delete(`/v1/yolo-aicm-publish/environments/${environmentId}`)).data,
    onSuccess: () => {
      message.success('发布环境已删除');
      refresh();
    },
    onError: (error) => message.error(environmentError(error)),
  });

  const openEditor = (row?: PublishEnvironment) => {
    setEditing(row || null);
    const address = parseAddress(row?.base_url);
    form.setFieldsValue(
      row
        ? {
            name: row.name,
            scheme: address.scheme,
            host: address.host,
            port: address.port,
            username: row.username || '',
            chip_type: row.chip_type || 'SOPHGO,BM1688,NPU',
            algorithm_type: row.algorithm_type || 'Detection',
            verify_ssl: Boolean(row.verify_ssl),
          }
        : {
            scheme: 'http',
            host: '',
            port: 8102,
            username: '',
            chip_type: 'SOPHGO,BM1688,NPU',
            algorithm_type: 'Detection',
            verify_ssl: false,
          },
    );
    setEditorOpen(true);
  };

  const columns: ColumnsType<PublishEnvironment> = [
    {
      title: '环境',
      key: 'environment',
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{row.name || '未命名环境'}</Typography.Text>
          <Typography.Text type="secondary">{row.id}</Typography.Text>
          {row.is_active ? <Tag color="blue">当前环境</Tag> : null}
        </Space>
      ),
    },
    {
      title: '连接信息',
      key: 'connection',
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Typography.Text>{row.base_url || '-'}</Typography.Text>
          <Typography.Text type="secondary">账号：{row.username || '-'}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '密码状态',
      key: 'password',
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Tag color={row.password_runtime_available ? 'green' : 'gold'}>
            {row.password_runtime_available ? '凭据可用' : row.password_configured ? '已保存，待检查' : '未配置'}
          </Tag>
          <Typography.Text type="secondary">{row.password_masked || '密码不回显'}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '发布就绪',
      key: 'ready',
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Tag color={row.ready_to_publish ? 'green' : 'gold'}>{row.ready_to_publish ? '可发布' : '未就绪'}</Tag>
          <Typography.Text type="secondary">{row.readiness_reason || '需要补齐连接信息或密码'}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 250,
      render: (_, row) => (
        <Space wrap>
          {!row.is_active ? (
            <Button size="small" onClick={() => activeMutation.mutate(row.id)} loading={activeMutation.isPending}>
              设为当前
            </Button>
          ) : null}
          <Button size="small" icon={<EditOutlined />} onClick={() => openEditor(row)}>
            编辑
          </Button>
          <Button
            size="small"
            danger
            icon={<DeleteOutlined />}
            disabled={row.is_active || deleteMutation.isPending}
            onClick={() =>
              Modal.confirm({
                title: '删除发布环境？',
                content: '删除后不能再使用该环境发布模型；当前环境不能直接删除。',
                okText: '删除',
                cancelText: '取消',
                okButtonProps: { danger: true },
                onOk: () => deleteMutation.mutateAsync(row.id),
              })
            }
          >
            删除
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <TablePanel>
      <Space style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
        <div>
          <h2 style={{ marginBottom: 4 }}>发布配置</h2>
          <Typography.Text type="secondary">
            配置 AICM 发布地址和加密凭据。模型发布时自动使用当前环境，AICM 模型编号由系统返回并保存。
          </Typography.Text>
        </div>
        <Space>
          <Button onClick={() => window.history.back()}>返回系统管理</Button>
          <Button icon={<ReloadOutlined />} loading={environmentsQuery.isFetching} onClick={() => void environmentsQuery.refetch()}>
            刷新状态
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => openEditor()}>
            新增环境
          </Button>
        </Space>
      </Space>
      {environmentsQuery.error ? (
        <Alert type="error" showIcon message="发布配置加载失败" description={environmentError(environmentsQuery.error)} />
      ) : (
        <>
          <Descriptions bordered size="small" column={3} style={{ marginBottom: 16 }}>
            <Descriptions.Item label="当前发布环境">{active?.name || '未设置'}</Descriptions.Item>
            <Descriptions.Item label="环境总数">{environments.length}</Descriptions.Item>
            <Descriptions.Item label="可发布环境">{readyCount}</Descriptions.Item>
          </Descriptions>
          {active ? (
            <Alert
              type={active.ready_to_publish ? 'success' : 'warning'}
              showIcon
              style={{ marginBottom: 16 }}
              message={active.ready_to_publish ? '当前发布环境已就绪' : '当前发布环境未就绪'}
              description={active.readiness_reason || '模型中心发布时会自动使用此环境。'}
            />
          ) : (
            <Alert type="info" showIcon style={{ marginBottom: 16 }} message="尚未设置当前发布环境" description="请新增一个 AICM 环境并设为当前，模型中心才能执行自动发布。" />
          )}
          <Space style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', margin: '8px 0 12px' }}>
            <div>
              <h3 style={{ margin: 0 }}>发布环境列表</h3>
              <Typography.Text type="secondary">连接地址、账号和加密密码均可用时，环境状态才会显示为“可发布”。</Typography.Text>
            </div>
            <Space wrap>
              <ServerSearchInput
                value={searchDraft}
                onChange={(value) => {
                  setSearchDraft(value);
                  if (!value.trim()) {
                    setSearch('');
                    setPage(0);
                  }
                }}
                onSearch={(value) => {
                  const normalized = value.trim();
                  setSearchDraft(value);
                  setSearch(normalized);
                  setPage(0);
                }}
                placeholder="按环境名称、地址、账号或状态搜索"
                ariaLabel="搜索发布环境"
                loading={environmentsQuery.isFetching}
                style={{ width: 300, maxWidth: '100%' }}
              />
              <Typography.Text type="secondary">{search ? `匹配 ${total} 个环境` : `共 ${total} 个环境`}</Typography.Text>
            </Space>
          </Space>
          <Table<PublishEnvironment>
            style={{ marginTop: 12 }}
            rowKey="id"
            size="small"
            loading={environmentsQuery.isLoading}
            dataSource={environments}
            columns={columns}
            pagination={{
              current: page + 1,
              pageSize,
              total,
              showSizeChanger: true,
              pageSizeOptions: ENVIRONMENT_PAGE_SIZE_OPTIONS.map(String),
              onChange: (nextPage, nextPageSize) => {
                const resolvedSize = nextPageSize || pageSize;
                setPageSize(resolvedSize);
                setPage(resolvedSize === pageSize ? Math.max(0, nextPage - 1) : 0);
              },
            }}
            scroll={{ x: 980 }}
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={search ? '没有匹配的发布环境' : '暂无发布环境'} /> }}
          />
        </>
      )}
      <Modal
        title={editing ? '编辑发布环境' : '新增发布环境'}
        open={editorOpen}
        centered
        destroyOnClose
        confirmLoading={saveMutation.isPending}
        onCancel={() => {
          setEditorOpen(false);
          setEditing(null);
          form.resetFields();
        }}
        onOk={() => void form.validateFields().then((values) => saveMutation.mutate(values))}
        okText="保存环境"
        cancelText="取消"
        width={640}
      >
        <Typography.Paragraph type="secondary">填写 AICM 主机、端口、账号和密码。密码只在保存时提交，页面不会回显明文。</Typography.Paragraph>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="环境名称">
            <Input placeholder="例如：本地 AICM、测试环境" maxLength={64} />
          </Form.Item>
          <Space style={{ display: 'flex' }} align="start">
            <Form.Item name="scheme" label="协议" rules={[{ required: true }]} style={{ width: 120 }}>
              <Select options={[{ value: 'http', label: 'HTTP' }, { value: 'https', label: 'HTTPS' }]} />
            </Form.Item>
            <Form.Item name="host" label="IP 地址或主机名" rules={[{ required: true, message: '请输入 IP 地址或主机名' }]} style={{ flex: 1 }}>
              <Input placeholder="例如：10.10.11.20" />
            </Form.Item>
            <Form.Item name="port" label="端口" rules={[{ required: true, type: 'number', min: 1, max: 65535 }]} style={{ width: 120 }}>
              <InputNumber style={{ width: '100%' }} precision={0} />
            </Form.Item>
          </Space>
          <Space style={{ display: 'flex' }} align="start">
            <Form.Item name="username" label="账号" rules={[{ required: true, whitespace: true, message: '请输入账号' }]} style={{ flex: 1 }}>
              <Input autoComplete="username" />
            </Form.Item>
            <Form.Item name="password" label={editing ? '密码（留空表示不修改）' : '密码'} rules={editing ? [] : [{ required: true, whitespace: true, message: '请输入密码' }]} style={{ flex: 1 }}>
              <Input.Password autoComplete={editing ? 'new-password' : 'current-password'} />
            </Form.Item>
          </Space>
          <Space style={{ display: 'flex' }} align="start">
            <Form.Item name="chip_type" label="芯片类型" style={{ flex: 1 }}>
              <Input placeholder="SOPHGO,BM1688,NPU" />
            </Form.Item>
            <Form.Item name="algorithm_type" label="算法类型" style={{ flex: 1 }}>
              <Input placeholder="Detection" />
            </Form.Item>
          </Space>
          <Form.Item name="verify_ssl" label="校验 HTTPS 证书" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </TablePanel>
  );
}
