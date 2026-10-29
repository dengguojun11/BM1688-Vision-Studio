import { DeleteOutlined, EditOutlined, EyeOutlined, PlusOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Space,
  Table,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState } from 'react';

import request from '@/api/request';
import ServerSearchInput from '@/components/ServerSearchInput';

import { listData, listTotal, type Row } from '../shared';

type GlobalOption = {
  id?: number;
  name_cn: string;
  value_en: string;
  sort_order: number;
};

type GlobalDimension = Row & {
  id: number;
  field_key: string;
  name_cn: string;
  sort_order: number;
  options: GlobalOption[];
};

type DimensionFormValues = {
  field_key: string;
  name_cn: string;
  sort_order: number;
  options: GlobalOption[];
};

function dimensionError(error: any, fallback: string) {
  if (error?.response?.status === 403) return '只有管理人员可以维护全局工具标签';
  if (error?.response?.status === 409 || error?.response?.status === 400) return '英文字段值已存在，请修改后重试';
  return fallback;
}

function normalizeOptions(options: GlobalOption[] | undefined) {
  return (options || [])
    .map((option, index) => ({
      name_cn: String(option?.name_cn || '').trim(),
      value_en: String(option?.value_en || '').trim(),
      sort_order: index,
    }))
    .filter((option) => option.name_cn || option.value_en);
}

export default function GlobalTagDimensionsPanel() {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<DimensionFormValues>();
  const [selected, setSelected] = useState<GlobalDimension | null>(null);
  const [modalMode, setModalMode] = useState<'create' | 'edit' | 'view' | null>(null);
  const [search, setSearch] = useState('');
  const [searchDraft, setSearchDraft] = useState('');
  const [tablePage, setTablePage] = useState(0);
  const [tablePageSize, setTablePageSize] = useState(10);

  const dimensionsQuery = useQuery({
    queryKey: ['governance-global-tag-dimensions', search, tablePage, tablePageSize],
    queryFn: async () =>
      // Preserve meta_data.total for server-side pagination. The shared list
      // helpers normalize both this envelope and raw Axios test responses.
      await request.get('/v1/annotation-taxonomy/global-tag-dimensions', {
        params: { page: tablePage, size: tablePageSize, ...(search ? { q: search } : {}) },
      }),
    staleTime: 10_000,
  });
  const dimensions = listData(dimensionsQuery.data) as GlobalDimension[];
  const dimensionsCount = dimensionsQuery.data ? listTotal(dimensionsQuery.data, dimensions.length) : undefined;

  const saveMutation = useMutation({
    mutationFn: async (values: DimensionFormValues) => {
      const options = normalizeOptions(values.options);
      if (modalMode === 'edit' && selected) {
        return request.patch(`/v1/annotation-taxonomy/global-tag-dimensions/${selected.id}`, {
          name_cn: values.name_cn.trim(),
          sort_order: Number(values.sort_order || 0),
          options,
        });
      }
      return request.post('/v1/annotation-taxonomy/global-tag-dimensions', {
        field_key: values.field_key.trim(),
        name_cn: values.name_cn.trim(),
        sort_order: Number(values.sort_order || 0),
        options,
      });
    },
    onSuccess: () => {
      message.success(modalMode === 'edit' ? '全局工具标签已保存' : '全局工具标签已创建');
      closeModal();
      void queryClient.invalidateQueries({ queryKey: ['governance-global-tag-dimensions'] });
    },
    onError: (error) => message.error(dimensionError(error, '保存全局工具标签失败，请稍后重试')),
  });

  const disableMutation = useMutation({
    mutationFn: async (row: GlobalDimension) =>
      request.delete(`/v1/annotation-taxonomy/global-tag-dimensions/${row.id}`),
    onSuccess: () => {
      message.success('全局工具标签已停用，历史任务仍保留原配置');
      void queryClient.invalidateQueries({ queryKey: ['governance-global-tag-dimensions'] });
    },
    onError: (error) => message.error(dimensionError(error, '停用全局工具标签失败，请刷新后重试')),
  });

  function closeModal() {
    setModalMode(null);
    setSelected(null);
    form.resetFields();
  }

  function openModal(mode: 'create' | 'edit' | 'view', row?: GlobalDimension) {
    setSelected(row || null);
    setModalMode(mode);
    const nextSortOrder = Math.max(
      dimensionsCount ?? 0,
      ...dimensions
        .map((dimension) => Number(dimension.sort_order))
        .filter(Number.isFinite)
        .map((sortOrder) => sortOrder + 1),
    );
    form.setFieldsValue(
      row
        ? {
            field_key: row.field_key,
            name_cn: row.name_cn,
            sort_order: row.sort_order,
            options: row.options || [],
          }
        : { field_key: '', name_cn: '', sort_order: nextSortOrder, options: [] },
    );
  }

  function submit(values: DimensionFormValues) {
    const options = normalizeOptions(values.options);
    const duplicateValue = options.find((option, index) =>
      options.some((other, otherIndex) => otherIndex !== index && other.value_en === option.value_en),
    );
    if (duplicateValue) {
      message.error(`选项英文值“${duplicateValue.value_en}”重复，请修改后再保存`);
      return;
    }
    if (modalMode === 'edit' && selected?.field_key) {
      saveMutation.mutate({ ...values, options });
      return;
    }
    saveMutation.mutate({ ...values, options });
  }

  function applySearch(value: string) {
    const next = value.trim();
    setSearchDraft(value);
    setSearch(next);
    setTablePage(0);
  }

  const columns: ColumnsType<GlobalDimension> = [
    { title: '中文名称', dataIndex: 'name_cn', key: 'name_cn', width: 190 },
    {
      title: '英文字段值',
      dataIndex: 'field_key',
      key: 'field_key',
      width: 180,
      render: (value) => <Typography.Text code>{value}</Typography.Text>,
    },
    { title: '选项数', key: 'options', width: 100, render: (_, row) => row.options?.length || 0 },
    { title: '排序', dataIndex: 'sort_order', key: 'sort_order', width: 90 },
    {
      title: '操作',
      key: 'actions',
      width: 250,
      render: (_, row) => (
        <Space size={2}>
          <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => openModal('view', row)}>
            查看
          </Button>
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openModal('edit', row)}>
            编辑
          </Button>
          <Popconfirm
            title="停用这个全局工具标签？"
            description="停用后不会出现在新建任务中，历史任务的已保存配置不受影响。"
            okText="停用"
            cancelText="取消"
            onConfirm={() => disableMutation.mutate(row)}
          >
            <Button type="link" danger size="small" icon={<DeleteOutlined />} loading={disableMutation.isPending}>
              停用
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const readOnly = modalMode === 'view';
  return (
    <div data-testid="admin-global-tool-tags">
      <Space style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 8 }}>
        <div style={{ minWidth: 0, flex: '1 1 220px' }}>
          <h2 style={{ marginBottom: 4 }}>全局工具标签</h2>
          <Typography.Text type="secondary">
            这里维护任务级的场景、光照、天气等信息。它只属于全局工具，不是目标检测标签库。
          </Typography.Text>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => openModal('create')}>
          新建全局工具标签
        </Button>
      </Space>
      <Alert
        type="info"
        showIcon
        message="新建任务会自动读取启用的全局工具标签"
        description="英文字段值创建后不建议修改，它会作为历史任务和训练数据中的稳定字段；选项的中文名称和英文值可以在编辑弹窗中一起维护。"
        style={{ margin: '14px 0' }}
      />
      {dimensionsQuery.error ? (
        <Alert type="error" showIcon message="全局工具标签加载失败" description="请刷新页面后重试。" />
      ) : (
        <>
          <Space style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', marginBottom: 12, width: '100%' }}>
            <ServerSearchInput
              value={searchDraft}
              placeholder="按中文名称、英文字段值或选项搜索"
              ariaLabel="搜索全局工具标签"
              style={{ minWidth: 0, flex: '1 1 240px' }}
              loading={dimensionsQuery.isFetching}
              onChange={(value) => {
                setSearchDraft(value);
                if (!value) applySearch('');
              }}
              onSearch={applySearch}
            />
            <Typography.Text type="secondary" style={{ flex: '0 1 auto' }}>
              {search ? `搜索“${search}”匹配 ${dimensionsCount ?? dimensions.length} 个维度` : `共 ${dimensionsCount ?? dimensions.length} 个启用维度`}
            </Typography.Text>
          </Space>
          <Table<GlobalDimension>
            rowKey="id"
            columns={columns}
            dataSource={dimensions}
            loading={dimensionsQuery.isLoading || dimensionsQuery.isFetching}
            pagination={{
              current: tablePage + 1,
              pageSize: tablePageSize,
              total: dimensionsCount,
              showSizeChanger: true,
              onChange: (page, pageSize) => {
                if (pageSize !== tablePageSize) {
                  setTablePageSize(pageSize);
                  setTablePage(0);
                  return;
                }
                setTablePage(Math.max(0, page - 1));
              },
            }}
            scroll={{ x: 860 }}
            locale={{
              emptyText: search ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配的全局工具标签" />
              ) : (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无全局工具标签" />
              ),
            }}
            summary={() => (
              <Table.Summary.Row>
                <Table.Summary.Cell index={0} colSpan={5}>
                  共 {dimensionsCount ?? dimensions.length} 个启用维度
                </Table.Summary.Cell>
              </Table.Summary.Row>
            )}
          />
        </>
      )}
      <Modal
        title={
          modalMode === 'view' ? '查看全局工具标签' : modalMode === 'edit' ? '编辑全局工具标签' : '新建全局工具标签'
        }
        open={Boolean(modalMode)}
        centered
        width={720}
        destroyOnClose
        confirmLoading={saveMutation.isPending}
        onCancel={() => !saveMutation.isPending && closeModal()}
        onOk={() => (readOnly ? closeModal() : void form.submit())}
        okText={readOnly ? '关闭' : modalMode === 'edit' ? '保存' : '创建'}
        cancelButtonProps={{ style: readOnly ? { display: 'none' } : undefined }}
        cancelText="取消"
      >
        <Form form={form} layout="vertical" onFinish={submit} disabled={readOnly}>
          <Space align="start" style={{ display: 'flex' }}>
            <Form.Item
              name="field_key"
              label="英文字段值"
              style={{ flex: 1 }}
              extra={
                modalMode === 'edit' || readOnly
                  ? '用于任务数据长期保存，创建后不可修改。'
                  : '只能使用英文字母、数字和下划线，且以字母开头。'
              }
              rules={[{ required: true, pattern: /^[A-Za-z][A-Za-z0-9_]*$/, message: '请输入正确的英文字段值' }]}
            >
              <Input disabled={modalMode !== 'create'} maxLength={64} />
            </Form.Item>
            <Form.Item
              name="sort_order"
              label="显示顺序"
              rules={[{ required: true, type: 'number', min: 0, max: 9999 }]}
            >
              <InputNumber min={0} max={9999} precision={0} />
            </Form.Item>
          </Space>
          <Form.Item
            name="name_cn"
            label="中文名称"
            rules={[{ required: true, whitespace: true, message: '请输入中文名称' }]}
          >
            <Input maxLength={128} placeholder="例如：场景类型" />
          </Form.Item>
          <Typography.Title level={5} style={{ marginTop: 8 }}>
            选项列表
          </Typography.Title>
          <Typography.Paragraph type="secondary">
            每个选项包含给标注人员看的中文名称，以及给接口和训练数据保存的英文值。
          </Typography.Paragraph>
          <Form.List name="options">
            {(fields, { add, remove }) => (
              <>
                {fields.map((field, index) => (
                  <Space key={field.key} align="baseline" style={{ display: 'flex', marginBottom: 8 }}>
                    <Form.Item
                      {...field}
                      name={[field.name, 'name_cn']}
                      rules={[{ required: true, whitespace: true, message: '请输入中文名称' }]}
                    >
                      <Input placeholder="中文名称" style={{ width: 240 }} />
                    </Form.Item>
                    <Form.Item
                      {...field}
                      name={[field.name, 'value_en']}
                      rules={[{ required: true, pattern: /^[A-Za-z][A-Za-z0-9_]*$/, message: '请输入英文值' }]}
                    >
                      <Input placeholder="英文值" style={{ width: 240 }} />
                    </Form.Item>
                    {!readOnly && (
                      <Button
                        type="text"
                        danger
                        icon={<DeleteOutlined />}
                        aria-label={`删除第${index + 1}个选项`}
                        onClick={() => remove(field.name)}
                      />
                    )}
                  </Space>
                ))}
                {!readOnly && (
                  <Button
                    type="dashed"
                    block
                    icon={<PlusOutlined />}
                    onClick={() => add({ name_cn: '', value_en: '' })}
                  >
                    新增选项
                  </Button>
                )}
              </>
            )}
          </Form.List>
        </Form>
      </Modal>
    </div>
  );
}
