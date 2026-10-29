import { EditOutlined, EyeOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Collapse,
  Descriptions,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Pagination,
  Select,
  Space,
  Spin,
  Tag,
  message,
} from 'antd';
import { useEffect, useState } from 'react';

import request from '@/api/request';
import ResourcePage from '../ResourcePage';
import { listData, listTotal, type Row } from '../shared';

type AlgorithmRow = Row & {
  algorithm_key?: string;
  capability_state?: 'REGISTERED' | 'CONFIGURED' | 'RUNTIME_READY' | 'QUALITY_READY' | 'PUBLISHABLE' | string;
  trainable?: boolean;
  quality_ready?: boolean;
  publishable?: boolean;
};
type AlgorithmFormValues = Record<string, any>;

type AlgorithmGroupPickerProps = {
  value?: number | string;
  onChange?: (value: number | undefined) => void;
  groups: AlgorithmRow[];
  selectedGroup?: AlgorithmRow | null;
  total?: number;
  page?: number;
  pageSize?: number;
  searchValue?: string;
  loading?: boolean;
  disabled?: boolean;
  onSearch?: (value: string) => void;
  onPageChange?: (page: number) => void;
  onSelectionChange?: (group: AlgorithmRow | null) => void;
};

/**
 * Algorithm groups are operator-managed data and can grow independently of
 * the algorithm list. Keep the picker server-backed so the registration
 * modal never relies on a fixed-size catalogue snapshot.
 */
export function AlgorithmGroupPicker({
  value,
  onChange,
  groups,
  selectedGroup,
  total = 0,
  page = 0,
  pageSize = 20,
  searchValue = '',
  loading = false,
  disabled = false,
  onSearch,
  onPageChange,
  onSelectionChange,
}: AlgorithmGroupPickerProps) {
  const options =
    selectedGroup && !groups.some((group) => String(group.id) === String(selectedGroup.id))
      ? [...groups, selectedGroup]
      : groups;
  return (
    <Select
      showSearch
      allowClear
      filterOption={false}
      disabled={disabled}
      loading={loading}
      value={value}
      searchValue={searchValue}
      placeholder="搜索并选择算法分组"
      aria-label="搜索算法分组"
      notFoundContent={loading ? '加载中...' : '暂无匹配分组'}
      options={options.map((group) => ({
        value: group.id,
        label: `${group.name} (${group.group_key})`,
      }))}
      dropdownRender={(menu) => (
        <>
          {menu}
          <div
            onMouseDown={(event) => event.preventDefault()}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 8,
              padding: '8px 10px 2px',
              borderTop: '1px solid #f0f0f0',
            }}
          >
            <span style={{ color: '#64748b', fontSize: 12, whiteSpace: 'nowrap' }}>分组：{total}</span>
            {total > pageSize && (
              <Pagination
                size="small"
                current={page + 1}
                pageSize={pageSize}
                total={total}
                showSizeChanger={false}
                showLessItems
                onChange={(nextPage) => onPageChange?.(nextPage - 1)}
              />
            )}
          </div>
        </>
      )}
      onSearch={(next) => onSearch?.(next)}
      onChange={(nextValue) => {
        const nextGroup = options.find((group) => String(group.id) === String(nextValue)) || null;
        onSelectionChange?.(nextGroup);
        onChange?.(nextValue === undefined || nextValue === null || nextValue === '' ? undefined : Number(nextValue));
        if (searchValue) onSearch?.('');
      }}
    />
  );
}

const JSON_FIELDS = ['input_contract', 'output_contract', 'rule_schema', 'min_samples', 'quality_gate'];

export function parseAlgorithmJson(value: string | undefined, field = '配置') {
  if (!value?.trim()) return undefined;
  const parsed = JSON.parse(value);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error(`${field}必须是 JSON 对象`);
  return parsed;
}

function toText(value: unknown) {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  return JSON.stringify(value, null, 2);
}

function toList(value: unknown) {
  return Array.isArray(value) ? value.join(', ') : '';
}

export function AlgorithmDefinitionForm({
  algorithm,
  groups,
  selectedGroup,
  groupTotal,
  groupPage,
  groupPageSize,
  groupSearch,
  groupLoading,
  onGroupSearch,
  onGroupPageChange,
  onGroupSelection,
  onClose,
  onSaved,
}: {
  algorithm?: AlgorithmRow | null;
  groups: AlgorithmRow[];
  selectedGroup?: AlgorithmRow | null;
  groupTotal?: number;
  groupPage?: number;
  groupPageSize?: number;
  groupSearch?: string;
  groupLoading?: boolean;
  onGroupSearch?: (value: string) => void;
  onGroupPageChange?: (page: number) => void;
  onGroupSelection?: (group: AlgorithmRow | null) => void;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form] = Form.useForm<AlgorithmFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const isEdit = Boolean(algorithm?.id);
  const initialValues = {
    ...algorithm,
    required_tools: toList(algorithm?.required_tools),
    required_attributes: toList(algorithm?.required_attributes),
    supported_chips: toList(algorithm?.supported_chips),
    supported_formats: toList(algorithm?.supported_formats),
    input_contract: toText(algorithm?.input_contract),
    output_contract: toText(algorithm?.output_contract),
    rule_schema: toText(algorithm?.rule_schema),
    min_samples: toText(algorithm?.min_samples),
    quality_gate: toText(algorithm?.quality_gate),
  };
  useEffect(() => {
    if (!isEdit && groups.length && !form.getFieldValue('algorithm_group_id')) {
      form.setFieldValue('algorithm_group_id', groups[0].id);
      onGroupSelection?.(groups[0]);
    }
  }, [form, groups, isEdit, onGroupSelection]);
  const submit = async (values: AlgorithmFormValues) => {
    setSubmitting(true);
    try {
      const payload: AlgorithmFormValues = {
        ...values,
        name: values.name.trim(),
        description: values.description?.trim() || undefined,
        required_tools: String(values.required_tools || '')
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean),
        required_attributes: String(values.required_attributes || '')
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean),
        supported_chips: String(values.supported_chips || '')
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean),
        supported_formats: String(values.supported_formats || '')
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean),
      };
      JSON_FIELDS.forEach((field) => {
        payload[field] = parseAlgorithmJson(values[field], field);
      });
      if (isEdit) await request.patch(`/v1/algorithms/${algorithm?.id}`, payload);
      else {
        payload.algorithm_key = String(values.algorithm_key || '')
          .trim()
          .toLowerCase();
        // Seeded installations use a stable first group; retaining the fallback
        // keeps the form usable while the groups query is still settling.
        payload.algorithm_group_id = Number(values.algorithm_group_id || groups[0]?.id || 1);
        await request.post('/v1/algorithms', payload);
      }
      message.success(isEdit ? '算法契约已更新' : '算法已注册');
      onClose();
      onSaved();
    } catch (error: any) {
      const field = JSON_FIELDS.find((item) => error?.message?.includes(item));
      if (field) form.setFields([{ name: field, errors: ['请输入合法的 JSON 对象'] }]);
      else
        message.error(
          error?.response?.status === 409
            ? '算法标识已存在'
            : error?.response?.status === 403
            ? '只有平台管理员可以修改算法目录'
            : '算法保存失败，请检查配置后重试',
        );
    } finally {
      setSubmitting(false);
    }
  };
  return (
    <Modal
      title={isEdit ? `编辑算法契约 · ${algorithm?.name || algorithm?.algorithm_key}` : '注册算法'}
      open
      centered
      destroyOnClose
      width={760}
      confirmLoading={submitting}
      okText={isEdit ? '保存契约' : '注册算法'}
      cancelText="取消"
      onCancel={() => {
        if (!submitting) onClose();
      }}
      onOk={() =>
        void form
          .validateFields()
          .then((values) => submit(values))
          .catch(() => undefined)
      }
    >
      <Form form={form} layout="vertical" initialValues={initialValues} onFinish={(values) => void submit(values)}>
        <Space style={{ display: 'flex' }} size={12} align="start">
          <Form.Item
            name="algorithm_key"
            label="算法标识"
            rules={[
              {
                required: !isEdit,
                validator: async (_rule, value) => {
                  const normalized = String(value || '').trim();
                  if (!normalized || /^[A-Za-z][A-Za-z0-9_.-]*$/.test(normalized)) return;
                  throw new Error('仅允许字母开头及字母、数字、点、下划线、短横线');
                },
              },
            ]}
            style={{ flex: 1 }}
          >
            <Input disabled={isEdit} maxLength={128} />
          </Form.Item>
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, whitespace: true, message: '请输入算法名称' }]}
            style={{ flex: 1 }}
          >
            <Input maxLength={128} />
          </Form.Item>
        </Space>
        <Space style={{ display: 'flex' }} size={12} align="start">
          <Form.Item
            name="task_type"
            label="任务类型"
            rules={[{ required: true, message: '请输入任务类型' }]}
            style={{ flex: 1 }}
          >
            <Input maxLength={64} placeholder="object_detection" />
          </Form.Item>
          <Form.Item name="algorithm_group_id" label="算法分组" rules={[{ required: false }]} style={{ flex: 1 }}>
            <AlgorithmGroupPicker
              disabled={isEdit}
              groups={groups}
              selectedGroup={selectedGroup}
              total={groupTotal}
              page={groupPage}
              pageSize={groupPageSize}
              searchValue={groupSearch}
              loading={groupLoading}
              onSearch={onGroupSearch}
              onPageChange={onGroupPageChange}
              onSelectionChange={onGroupSelection}
            />
          </Form.Item>
        </Space>
        <Form.Item name="description" label="描述">
          <Input.TextArea rows={2} maxLength={4000} />
        </Form.Item>
        <Collapse
          bordered
          defaultActiveKey={['runtime']}
          style={{ marginTop: 4, marginBottom: 8 }}
          items={[
            {
              key: 'runtime',
              label: '运行与能力',
              extra: <span style={{ color: '#64748b', fontSize: 12 }}>适配器、芯片和输入输出资源</span>,
              children: (
                <>
                  <Space style={{ display: 'flex' }} size={12} align="start">
                    <Form.Item
                      name="training_adapter"
                      label="训练适配器"
                      rules={[{ required: true, message: '请输入训练适配器' }]}
                      style={{ flex: 1 }}
                    >
                      <Input maxLength={128} />
                    </Form.Item>
                    <Form.Item
                      name="evaluation_adapter"
                      label="评估适配器"
                      rules={[{ required: true, message: '请输入评估适配器' }]}
                      style={{ flex: 1 }}
                    >
                      <Input maxLength={128} />
                    </Form.Item>
                  </Space>
                  <Form.Item
                    name="supported_chips"
                    label="支持芯片（逗号分隔）"
                    rules={[{ required: true, message: '请至少填写一种芯片' }]}
                  >
                    <Input placeholder="cpu, cuda, bm1688" />
                  </Form.Item>
                  <Space style={{ display: 'flex' }} size={12} align="start">
                    <Form.Item name="algorithm_kind" label="算法类型" style={{ flex: 1 }}>
                      <Select
                        options={[
                          { value: 'model', label: '模型' },
                          { value: 'pipeline', label: '流水线' },
                          { value: 'rule_template', label: '规则模板' },
                        ]}
                      />
                    </Form.Item>
                    <Form.Item name="runtime_adapter" label="运行时适配器" style={{ flex: 1 }}>
                      <Input maxLength={128} />
                    </Form.Item>
                    <Form.Item name="default_recipe" label="默认 Recipe" style={{ flex: 1 }}>
                      <Input maxLength={128} />
                    </Form.Item>
                  </Space>
                </>
              ),
            },
            {
              key: 'contract',
              label: '契约与质量',
              extra: <span style={{ color: '#64748b', fontSize: 12 }}>标签绑定、JSON 契约和质量门禁</span>,
              children: (
                <>
                  <Space style={{ display: 'flex' }} size={12} align="start">
                    <Form.Item name="label_set_signature" label="标签集签名" style={{ flex: 1 }}>
                      <Input maxLength={64} placeholder="用于绑定标签契约版本" />
                    </Form.Item>
                    <Form.Item name="annotation_contract_version" label="标注契约版本" style={{ flex: 1 }}>
                      <Input maxLength={64} placeholder="v1" />
                    </Form.Item>
                    <Form.Item name="catalog_kind" label="目录类型" style={{ flex: 1 }}>
                      <Select
                        options={[
                          { value: 'internal', label: '内部' },
                          { value: 'product', label: '产品能力' },
                        ]}
                      />
                    </Form.Item>
                  </Space>
                  <Space style={{ display: 'flex' }} size={12} align="start">
                    <Form.Item name="required_tools" label="必需工具（逗号分隔）" style={{ flex: 1 }}>
                      <Input />
                    </Form.Item>
                    <Form.Item name="required_attributes" label="必需属性（逗号分隔）" style={{ flex: 1 }}>
                      <Input />
                    </Form.Item>
                    <Form.Item name="supported_formats" label="支持格式（逗号分隔）" style={{ flex: 1 }}>
                      <Input />
                    </Form.Item>
                  </Space>
                  <Space style={{ display: 'flex' }} size={12} align="start">
                    <Form.Item name="input_contract" label="输入契约 JSON" style={{ flex: 1 }}>
                      <Input.TextArea rows={4} />
                    </Form.Item>
                    <Form.Item name="output_contract" label="输出契约 JSON" style={{ flex: 1 }}>
                      <Input.TextArea rows={4} />
                    </Form.Item>
                  </Space>
                  <Space style={{ display: 'flex' }} size={12} align="start">
                    <Form.Item name="min_samples" label="最小样本量 JSON" style={{ flex: 1 }}>
                      <Input.TextArea rows={3} placeholder='{"total":100}' />
                    </Form.Item>
                    <Form.Item name="quality_gate" label="质量门禁 JSON" style={{ flex: 1 }}>
                      <Input.TextArea rows={3} placeholder='{"map50":0.5}' />
                    </Form.Item>
                  </Space>
                  <Form.Item name="rule_schema" label="规则 Schema JSON">
                    <Input.TextArea rows={3} />
                  </Form.Item>
                </>
              ),
            },
            {
              key: 'governance',
              label: '治理与发布',
              extra: <span style={{ color: '#64748b', fontSize: 12 }}>风险级别和目录状态</span>,
              children: (
                <>
                  <Space style={{ display: 'flex' }} size={12} align="start">
                    <Form.Item name="risk_level" label="风险级别" style={{ flex: 1 }}>
                      <Select
                        options={[
                          { value: 'standard', label: '标准' },
                          { value: 'high', label: '高风险' },
                          { value: 'critical', label: '关键' },
                        ]}
                      />
                    </Form.Item>
                    <Form.Item name="status" label="状态" style={{ flex: 1 }}>
                      <Select
                        options={[
                          { value: 'ACTIVE', label: '启用' },
                          { value: 'INACTIVE', label: '停用' },
                          { value: 'DEPRECATED', label: '弃用' },
                        ]}
                      />
                    </Form.Item>
                  </Space>
                </>
              ),
            },
          ]}
        />
      </Form>
    </Modal>
  );
}

function AlgorithmCapabilityModal({ algorithm, onClose }: { algorithm: AlgorithmRow | null; onClose: () => void }) {
  const query = useQuery({
    queryKey: ['algorithm-capabilities', algorithm?.algorithm_key],
    queryFn: async () => {
      const response = await request.get(
        `/v1/algorithms/${encodeURIComponent(String(algorithm?.algorithm_key))}/capabilities`,
      );
      return response?.data?.data ?? response?.data ?? response;
    },
    enabled: Boolean(algorithm?.algorithm_key),
    staleTime: 30_000,
  });
  const data = query.data;
  return (
    <Modal
      title={`${algorithm?.name || '算法'}能力`}
      open={Boolean(algorithm)}
      centered
      onCancel={onClose}
      width={720}
      footer={null}
      destroyOnClose
      styles={{ body: { maxHeight: 'calc(100vh - 180px)', overflowY: 'auto' } }}
    >
      {query.isLoading ? (
        <div role="status" aria-live="polite">
          <Spin />
        </div>
      ) : query.error ? (
        <Alert
          type="error"
          showIcon
          message="算法能力加载失败"
          description="请检查注册信息或稍后重试。"
          action={
            <Button size="small" icon={<ReloadOutlined />} onClick={() => query.refetch()}>
              重试
            </Button>
          }
        />
      ) : data ? (
        <>
          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label="算法标识">{data.algorithm_key || algorithm?.algorithm_key}</Descriptions.Item>
            <Descriptions.Item label="适配器">
              {data.adapter_key || algorithm?.training_adapter || '-'}
            </Descriptions.Item>
            <Descriptions.Item label="执行模式">{data.execution_mode || '-'}</Descriptions.Item>
            <Descriptions.Item label="任务类型">{data.task_type || algorithm?.task_type || '-'}</Descriptions.Item>
            <Descriptions.Item label="支持芯片">
              {Array.isArray(data.supported_chips) ? data.supported_chips.join(', ') || '-' : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="协议版本">{data.protocol_version || '-'}</Descriptions.Item>
            <Descriptions.Item label="Worker">{data.worker || '-'}</Descriptions.Item>
            <Descriptions.Item label="可用性">
              <Tag color={data.available ? 'green' : 'gold'}>{data.available ? '可用' : '不可用'}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="能力状态">
              <Tag
                color={data.publishable ? 'green' : data.quality_ready ? 'cyan' : data.trainable ? 'blue' : 'default'}
              >
                {data.capability_state || 'REGISTERED'}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="质量门禁">
              <Tag color={data.quality_ready ? 'cyan' : 'default'}>{data.quality_ready ? '已配置' : '未配置'}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="模型用途">
              {data.trainable
                ? data.publishable
                  ? '可训练、可作为发布候选（仍需模型版本门禁）'
                  : data.quality_ready
                  ? '可训练，已配置质量门禁；具体模型仍需质量证据与制品门禁'
                  : '可训练，尚未配置质量门禁'
                : '不可用于模型训练（运行时能力）'}
            </Descriptions.Item>
          </Descriptions>
          <h3 style={{ marginTop: 18 }}>执行阶段</h3>
          {Array.isArray(data.stages) && data.stages.length ? (
            <ul>
              {data.stages.map((stage: string) => (
                <li key={stage}>{stage}</li>
              ))}
            </ul>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无执行阶段" />
          )}
          <h3 style={{ marginTop: 18 }}>已实现能力</h3>
          {Array.isArray(data.implemented) && data.implemented.length ? (
            <ul>
              {data.implemented.map((item: string) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无能力记录" />
          )}
        </>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无能力数据" />
      )}
    </Modal>
  );
}

export default function AlgorithmsPage() {
  const [selected, setSelected] = useState<AlgorithmRow | null>(null);
  const [editing, setEditing] = useState<AlgorithmRow | null | undefined>(undefined);
  const [search, setSearch] = useState('');
  const [groupSearch, setGroupSearch] = useState('');
  const [groupPage, setGroupPage] = useState(0);
  const groupPageSize = 20;
  const [selectedGroup, setSelectedGroup] = useState<AlgorithmRow | null>(null);
  const queryClient = useQueryClient();
  const groupsQuery = useQuery<{ rows: AlgorithmRow[]; total: number }>({
    queryKey: ['algorithm-groups', groupSearch, groupPage, groupPageSize],
    queryFn: async () => {
      const response = await request.get('/v1/algorithm-groups', {
        params: {
          status: 'ACTIVE',
          page: groupPage,
          size: groupPageSize,
          q: groupSearch || undefined,
        },
      });
      // `request` already unwraps Axios's outer response, while test adapters
      // may still return an extra `data` layer. Parse the whole value so the
      // pagination metadata is retained in both shapes.
      const rows = listData(response) as AlgorithmRow[];
      return { rows, total: listTotal(response, rows.length) };
    },
    staleTime: 60_000,
  });
  const groups = groupsQuery.data?.rows || [];
  const groupTotal = groupsQuery.data?.total || 0;
  const openEditor = (algorithm: AlgorithmRow | null) => {
    setSelectedGroup(
      algorithm?.algorithm_group_id
        ? {
            id: algorithm.algorithm_group_id,
            name: algorithm.group_name || `分组 #${algorithm.algorithm_group_id}`,
            group_key: algorithm.group_key || String(algorithm.algorithm_group_id),
          }
        : null,
    );
    setEditing(algorithm);
  };
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['algorithms'] });
  };
  return (
    <>
      <ResourcePage
        module="algorithms"
        queryKey="algorithms"
        needsProject={false}
        queryFn={async (_projectId, page = 0, size = 20, query) =>
          await request.get('/v1/algorithms', {
            params: { status: 'ACTIVE', page, size, q: query || undefined },
          })
        }
        pagination
        pageSize={20}
        search={search}
        onSearchChange={setSearch}
        searchPlaceholder="按算法名称、标识或适配器搜索"
        searchAriaLabel="搜索算法"
        staleTime={60_000}
        beforeTable={
          <Space style={{ marginBottom: 14 }}>
            {editing === undefined ? (
              <Button type="primary" icon={<PlusOutlined />} aria-label="注册算法" onClick={() => openEditor(null)}>
                注册算法
              </Button>
            ) : null}
            {groupsQuery.error && <TypographyError message="算法分组加载失败" />}
          </Space>
        }
        rowActions={(row) => (
          <Space size={4}>
            <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => setSelected(row as AlgorithmRow)}>
              查看能力
            </Button>
            <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openEditor(row as AlgorithmRow)}>
              编辑契约
            </Button>
          </Space>
        )}
        columns={[
          { title: '算法', dataIndex: 'name', key: 'name', ellipsis: true },
          { title: '标识', dataIndex: 'algorithm_key', key: 'algorithm_key', ellipsis: true },
          { title: '任务类型', dataIndex: 'task_type', key: 'task_type' },
          { title: '适配器', dataIndex: 'training_adapter', key: 'training_adapter' },
          {
            title: '契约 / 门禁',
            key: 'contract',
            render: (_: unknown, row: Row) =>
              `${row.annotation_contract_version || 'v1'} / ${row.quality_gate ? '已配置' : '未配置'}`,
          },
          {
            title: '状态',
            dataIndex: 'status',
            key: 'status',
            render: (value, row: AlgorithmRow) => (
              <Space size={4}>
                <Tag color={value === 'ACTIVE' ? 'green' : 'default'}>{value}</Tag>
                <Tag
                  color={row.publishable ? 'green' : row.quality_ready ? 'cyan' : row.trainable ? 'blue' : 'default'}
                >
                  {row.capability_state || 'REGISTERED'}
                </Tag>
                <Tag color={row.quality_ready ? 'cyan' : 'default'}>
                  {row.quality_ready ? '门禁已配置' : '门禁未配置'}
                </Tag>
              </Space>
            ),
          },
        ]}
      />
      <AlgorithmCapabilityModal algorithm={selected} onClose={() => setSelected(null)} />
      {editing !== undefined && (
        <AlgorithmDefinitionForm
          algorithm={editing}
          groups={groups}
          selectedGroup={selectedGroup}
          groupTotal={groupTotal}
          groupPage={groupPage}
          groupPageSize={groupPageSize}
          groupSearch={groupSearch}
          groupLoading={groupsQuery.isLoading || groupsQuery.isFetching}
          onGroupSearch={(value) => {
            setGroupSearch(value);
            setGroupPage(0);
          }}
          onGroupPageChange={setGroupPage}
          onGroupSelection={setSelectedGroup}
          onClose={() => {
            setEditing(undefined);
            setSelectedGroup(null);
          }}
          onSaved={refresh}
        />
      )}
    </>
  );
}

function TypographyError({ message: text }: { message: string }) {
  return <Alert type="error" showIcon message={text} />;
}
