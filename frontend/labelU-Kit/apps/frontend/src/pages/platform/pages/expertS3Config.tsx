import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Descriptions, Empty, Form, Input, Modal, Space, Table, Tag, Typography, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState } from 'react';

import request from '@/api/request';
import ServerSearchInput from '@/components/ServerSearchInput';
import { TablePanel, responseStatus } from '../shared';

type Connection = { id: string; name: string; endpoint: string; remark?: string; is_active?: boolean; created_at?: string; updated_at?: string };
type Bundle = { active_connection_id?: string; connections?: Connection[] };
type ConnectionTestResult = {
  connection_id: string;
  endpoint: string;
  bucket?: string;
  ok: boolean;
  endpoint_reachable: boolean;
  head_bucket_ok: boolean;
  list_objects_ok: boolean;
  object_count?: number;
  checked_at?: string;
  error_stage?: string | null;
  message?: string;
};

function errorText(error: unknown) {
  return responseStatus(error) === 403 ? '只有管理人员可以维护专家 S3 配置' : '专家 S3 配置加载失败，请检查后端服务';
}

function testStageText(stage?: string | null) {
  return ({
    endpoint: '地址连接',
    s3_api: 'S3 API 接口',
    bucket: '存储桶发现',
    head_bucket: '存储桶访问',
    list_objects: '对象列表读取',
  } as Record<string, string>)[stage || ''] || stage || '';
}

export default function ExpertS3ConfigPanel() {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Connection | null>(null);
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(null);
  const [testName, setTestName] = useState('');
  const [search, setSearch] = useState('');
  const [searchDraft, setSearchDraft] = useState('');
  const [form] = Form.useForm<{ name: string; endpoint: string; remark?: string }>();
  const apiPath = '/v1/strategy-integration-s3-config';
  const query = useQuery<Bundle>({ queryKey: ['expert-s3-config'], queryFn: async () => (await request.get(apiPath)).data, staleTime: 5000 });
  const bundle = query.data || {};
  const rows = bundle.connections || [];
  const filteredRows = rows.filter((row) => {
    const keyword = search.trim().toLowerCase();
    if (!keyword) return true;
    return [row.name, row.endpoint, row.remark, row.id]
      .some((value) => String(value || '').toLowerCase().includes(keyword));
  });
  const active = rows.find((row) => row.id === bundle.active_connection_id || row.is_active);
  const refresh = () => void client.invalidateQueries({ queryKey: ['expert-s3-config'] });
  const save = useMutation({
    mutationFn: async (values: { name: string; endpoint: string; remark?: string }) => editing
      ? request.patch(`${apiPath}/connections/${editing.id}`, values)
      : request.post(`${apiPath}/connections`, { ...values, set_active: true }),
    onSuccess: () => { message.success(editing ? '配置已更新' : '配置已新增并启用'); setOpen(false); setEditing(null); form.resetFields(); refresh(); },
    onError: (e) => message.error(errorText(e)),
  });
  const setActive = useMutation({
    mutationFn: (id: string) => request.put(`${apiPath}/connections/active`, { connection_id: id }),
    onSuccess: () => { message.success('已切换当前配置'); refresh(); }, onError: (e) => message.error(errorText(e)),
  });
  const remove = useMutation({
    mutationFn: (id: string) => request.delete(`${apiPath}/connections/${id}`),
    onSuccess: () => { message.success('配置已删除'); refresh(); }, onError: (e) => message.error(errorText(e)),
  });
  const testConnection = useMutation({
    mutationFn: async (row: Connection) => {
      const response = await request.post(`${apiPath}/connections/${row.id}/test`);
      return { row, result: response.data as ConnectionTestResult };
    },
    onSuccess: ({ row, result }) => {
      setTestName(row.name);
      setTestResult(result);
    },
    onError: (e) => message.error(errorText(e)),
  });
  const edit = (row?: Connection) => { setEditing(row || null); form.setFieldsValue(row ? { name: row.name, endpoint: row.endpoint, remark: row.remark } : { name: '', endpoint: '', remark: '' }); setOpen(true); };
  const columns: ColumnsType<Connection> = [
    { title: '状态', key: 'status', width: 100, render: (_, row) => <Tag color={row.is_active ? 'green' : 'default'}>{row.is_active ? '启用中' : '未启用'}</Tag> },
    { title: '配置名称', dataIndex: 'name', key: 'name' },
    { title: '服务地址', dataIndex: 'endpoint', key: 'endpoint' },
    { title: '备注', dataIndex: 'remark', key: 'remark', render: (v) => v || '-' },
    { title: '操作', key: 'actions', width: 320, render: (_, row) => <Space wrap>
      {!row.is_active && <Button size="small" onClick={() => setActive.mutate(row.id)} loading={setActive.isPending}>设为启用</Button>}
      <Button size="small" onClick={() => testConnection.mutate(row)} loading={testConnection.isPending && testConnection.variables?.id === row.id}>测试连接</Button>
      <Button size="small" icon={<EditOutlined />} onClick={() => edit(row)}>编辑</Button>
      <Button size="small" danger icon={<DeleteOutlined />} disabled={row.is_active || remove.isPending} onClick={() => Modal.confirm({ title: '删除配置？', content: '当前启用配置不能删除。', okText: '删除', cancelText: '取消', okButtonProps: { danger: true }, onOk: () => remove.mutateAsync(row.id) })}>删除</Button>
    </Space> },
  ];
  return <TablePanel>
    <Space style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
      <div><h2 style={{ marginBottom: 4 }}>专家 S3 回流配置</h2><Typography.Text type="secondary">配置第三方专家问题图片的对象存储地址，供回流任务自动读取。</Typography.Text></div>
      <Space><Button icon={<ReloadOutlined />} loading={query.isFetching} onClick={() => void query.refetch()}>刷新</Button><Button type="primary" icon={<PlusOutlined />} onClick={() => edit()}>新增配置</Button></Space>
    </Space>
    {query.error ? <Alert type="error" showIcon message="配置加载失败" description={errorText(query.error)} /> : <>
      <Descriptions bordered size="small" column={3} style={{ marginBottom: 16 }}>
        <Descriptions.Item label="当前启用">{active?.name || '未配置'}</Descriptions.Item><Descriptions.Item label="服务地址">{active?.endpoint || '-'}</Descriptions.Item><Descriptions.Item label="配置数量">{rows.length}</Descriptions.Item>
      </Descriptions>
      <Space style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
        <ServerSearchInput
          value={searchDraft}
          onChange={(value) => { setSearchDraft(value); if (!value) setSearch(''); }}
          onSearch={(value) => { setSearchDraft(value); setSearch(value.trim()); }}
          placeholder="按配置名称、地址、备注或 ID 搜索"
          ariaLabel="搜索专家 S3 配置"
          loading={query.isFetching}
          style={{ width: 320, maxWidth: '100%' }}
        />
        <Typography.Text type="secondary">{search ? `匹配 ${filteredRows.length} 个配置` : `共 ${rows.length} 个配置`}</Typography.Text>
      </Space>
      <Table<Connection> rowKey="id" size="small" loading={query.isLoading} dataSource={filteredRows} columns={columns} pagination={{ pageSize: 10, showTotal: (total) => `共 ${total} 个配置` }} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={search ? '没有匹配的专家 S3 配置' : '暂无专家 S3 配置'} /> }} />
    </>}
    <Modal title={editing ? '编辑专家 S3 配置' : '新增专家 S3 配置'} open={open} centered destroyOnClose confirmLoading={save.isPending} onCancel={() => { setOpen(false); setEditing(null); form.resetFields(); }} onOk={() => void form.validateFields().then((v) => save.mutate(v))} okText="保存" cancelText="取消">
      <Form form={form} layout="vertical"><Form.Item name="name" label="配置名称" rules={[{ required: true, message: '请输入配置名称' }]}><Input maxLength={64} placeholder="例如：生产专家回流" /></Form.Item><Form.Item name="endpoint" label="服务地址" extra="请填写对象存储 S3 API 地址，不要填写 MinIO Console 管理页面地址；例如 http://192.168.54.190:29100" rules={[{ required: true, message: '请输入服务地址' }]}><Input maxLength={500} placeholder="例如：http://10.10.11.28:29100" /></Form.Item><Form.Item name="remark" label="备注"><Input.TextArea rows={3} maxLength={200} /></Form.Item></Form>
    </Modal>
    <Modal title={`连接测试结果${testName ? `：${testName}` : ''}`} open={Boolean(testResult)} footer={<Button onClick={() => setTestResult(null)}>关闭</Button>} onCancel={() => setTestResult(null)} centered>
      {testResult && <>
        <Alert type={testResult.ok ? 'success' : 'error'} showIcon message={testResult.ok ? '连接成功' : '连接失败'} description={testResult.message || '-'} style={{ marginBottom: 16 }} />
        <Descriptions bordered size="small" column={1}>
          <Descriptions.Item label="服务地址">{testResult.endpoint || '-'}</Descriptions.Item>
          <Descriptions.Item label="存储桶">{testResult.bucket || '-'}</Descriptions.Item>
          <Descriptions.Item label="地址可达">{testResult.endpoint_reachable ? '是' : '否'}</Descriptions.Item>
          <Descriptions.Item label="存储桶可访问">{testResult.head_bucket_ok ? '是' : '否'}</Descriptions.Item>
          <Descriptions.Item label="对象列表可读取">{testResult.list_objects_ok ? '是' : '否'}</Descriptions.Item>
          <Descriptions.Item label="探测到的对象数">{testResult.object_count ?? 0}</Descriptions.Item>
          <Descriptions.Item label="检查时间">{testResult.checked_at || '-'}</Descriptions.Item>
          {testResult.error_stage && <Descriptions.Item label="失败环节">{testStageText(testResult.error_stage)}</Descriptions.Item>}
        </Descriptions>
      </>}
    </Modal>
  </TablePanel>;
}
