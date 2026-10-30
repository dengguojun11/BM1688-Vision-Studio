import { ApiOutlined, CloudServerOutlined, ReloadOutlined, SafetyCertificateOutlined, SettingOutlined, TeamOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Empty, Form, Input, Modal, Pagination, Space, Table, Tag, Tabs, Typography, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import request from '@/api/request';
import ServerSearchInput from '@/components/ServerSearchInput';

import { listData, listTotal, ModuleHeader, ModuleStatus, responseStatus, TablePanel, type Row } from '../shared';
import DatasetsPage from './datasets';
import EdgeDevicesPanel from './edgeDevices';
import GlobalTagDimensionsPanel from './globalTagDimensions';
import PublishConfigPanel from './publishConfig';
import ExpertS3ConfigPanel from './expertS3Config';
import ModelAccessConfigPanel from './modelAccessConfig';
import ImageCleaningConfigPanel from './imageCleaningConfig';

function errorText(error: unknown) {
  const status = responseStatus(error);
  if (status === 403) return '当前账号没有系统管理权限';
  if (status === 404) return '管理资源不存在';
  return '管理数据加载失败，请稍后重试';
}

function hslToHex(hue: number, saturation: number, lightness: number): string {
  const s = saturation / 100;
  const l = lightness / 100;
  const chroma = (1 - Math.abs(2 * l - 1)) * s;
  const section = hue / 60;
  const x = chroma * (1 - Math.abs((section % 2) - 1));
  const match = l - chroma / 2;
  let red = 0;
  let green = 0;
  let blue = 0;

  if (section < 1) {
    red = chroma;
    green = x;
  } else if (section < 2) {
    red = x;
    green = chroma;
  } else if (section < 3) {
    green = chroma;
    blue = x;
  } else if (section < 4) {
    green = x;
    blue = chroma;
  } else if (section < 5) {
    red = x;
    blue = chroma;
  } else {
    red = chroma;
    blue = x;
  }

  return `#${[red, green, blue]
    .map((channel) =>
      Math.round((channel + match) * 255)
        .toString(16)
        .padStart(2, '0'),
    )
    .join('')}`;
}

const ANNOTATION_KIND_LABELS: Record<string, string> = {
  bbox: '矩形框（目标检测）',
  class_label: '整图分类',
  polygon: '多边形（实例分割）',
  keypoints: '关键点（姿态）',
  custom: '自定义标注',
};

function annotationKindLabel(value: unknown): string {
  const kind = String(value || '').trim();
  return ANNOTATION_KIND_LABELS[kind] || kind || '未设置';
}

/**
 * Pick a readable preview color that is not already used by the loaded labels.
 * The API remains the source of truth: create requests intentionally omit color
 * so the server can repeat this uniqueness check inside the transaction.
 */
export function nextAvailableLabelColor(rows: Row[], excludeId?: Row['id']): string {
  const used = new Set(
    rows
      .filter((row) => row.id !== excludeId)
      .map((row) =>
        String(row.color || '')
          .trim()
          .toLowerCase(),
      )
      .filter(Boolean),
  );
  for (let index = 0; index < 4096; index += 1) {
    const color = hslToHex((index * 137.508) % 360, 68, 48);
    if (!used.has(color)) return color;
  }
  return '#1677ff';
}

function GovernancePanel() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const [editingLabel, setEditingLabel] = useState<Row | null>(null);
  const [labelOpen, setLabelOpen] = useState(false);
  const [labelSearch, setLabelSearch] = useState('');
  const [labelSearchDraft, setLabelSearchDraft] = useState('');
  const [labelPage, setLabelPage] = useState(0);
  const [labelPageSize, setLabelPageSize] = useState(10);
  const requestedTab = new URLSearchParams(location.search).get('tab');
  const [activeTab, setActiveTab] = useState(
    requestedTab === 'datasets' || requestedTab === 'global-tools' ? requestedTab : 'labels',
  );
  useEffect(() => {
    const nextTab = requestedTab === 'datasets' || requestedTab === 'global-tools' ? requestedTab : 'labels';
    if (nextTab !== activeTab) setActiveTab(nextTab);
  }, [activeTab, requestedTab]);
  const [labelForm] = Form.useForm<{
    label_key: string;
    label_value: string;
    annotation_kind?: string;
    color?: string;
    description?: string;
  }>();
  const labelsQuery = useQuery({
    queryKey: ['governance-labels', labelSearch, labelPage, labelPageSize],
    queryFn: async () =>
      // Keep the business envelope so server-side pagination can use
      // meta_data.total. listData/listTotal also accept the raw Axios shape
      // used by the page tests, so this remains compatible with both forms.
      await request.get('/v1/annotation-taxonomy/labels', {
        params: {
          page: labelPage,
          size: labelPageSize,
          include_disabled: true,
          ...(labelSearch ? { q: labelSearch } : {}),
        },
      }),
    enabled: activeTab === 'labels',
    staleTime: 10_000,
  });
  const labels = listData(labelsQuery.data);
  const labelsCount = labelsQuery.data ? listTotal(labelsQuery.data, labels.length) : undefined;
  const labelSaveMutation = useMutation({
    mutationFn: async (values: { label_key: string; label_value: string; color?: string; description?: string }) => {
      if (editingLabel)
        return request.patch(`/v1/annotation-taxonomy/labels/${editingLabel.id}`, {
          version: editingLabel.version,
          label_key: values.label_key.trim(),
          label_value: values.label_value.trim(),
          color: values.color?.trim() || undefined,
          description: values.description?.trim() || null,
          status: editingLabel.status,
        });
      return request.post('/v1/annotation-taxonomy/labels', {
        label_key: values.label_key.trim(),
        label_value: values.label_value.trim(),
        annotation_kind: 'bbox',
        description: values.description?.trim() || undefined,
      });
    },
    onSuccess: () => {
      message.success(editingLabel ? '标签已更新；已冻结数据版本和已发布模型仍保留原英文值' : '标签已创建');
      setLabelOpen(false);
      setEditingLabel(null);
      labelForm.resetFields();
      void queryClient.invalidateQueries({ queryKey: ['governance-labels'] });
    },
    onError: (error: any) =>
      message.error(
        error?.response?.status === 409
          ? '标签标识或颜色已存在'
          : error?.response?.status === 403
          ? '只有管理人员可以维护标签'
          : '保存标签失败，请检查后重试',
      ),
  });
  const labelStatusMutation = useMutation({
    mutationFn: async (row: Row) =>
      request.patch(`/v1/annotation-taxonomy/labels/${row.id}`, {
        version: row.version,
        status: row.status === 'active' ? 'disabled' : 'active',
      }),
    onSuccess: () => {
      message.success('标签状态已更新');
      void queryClient.invalidateQueries({ queryKey: ['governance-labels'] });
    },
    onError: () => message.error('标签状态更新失败，请刷新后重试'),
  });
  const openLabelEditor = (row?: Row) => {
    setEditingLabel(row || null);
    labelForm.setFieldsValue(
      row
        ? {
            label_key: row.label_key,
            label_value: row.label_value,
            annotation_kind: row.annotation_kind || 'bbox',
            color: row.color,
            description: row.description,
          }
        : { annotation_kind: 'bbox', color: nextAvailableLabelColor(labels) },
    );
    setLabelOpen(true);
  };
  const changeTab = (key: string) => {
    const nextTab = key === 'datasets' || key === 'global-tools' ? key : 'labels';
    setActiveTab(nextTab);
    const params = new URLSearchParams(location.search);
    params.set('view', 'governance');
    params.set('tab', nextTab);
    navigate(`/admin?${params.toString()}`, { replace: true });
  };
  const applyLabelSearch = (value: string) => {
    const next = value.trim();
    setLabelSearchDraft(value);
    setLabelSearch(next);
    setLabelPage(0);
  };
  return (
    <TablePanel>
      <Space style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 12 }}>
        <div style={{ minWidth: 0, flex: '1 1 220px' }}>
          <h2 style={{ marginBottom: 4 }}>标注与数据治理</h2>
          <Typography.Text type="secondary">
            标签库维护可复用标签；数据集管理维护标签契约、数据版本和训练数据来源。
          </Typography.Text>
        </div>
        <Space wrap>
          <Button onClick={() => navigate('/admin')}>返回系统管理</Button>
          {activeTab === 'labels' ? (
            <Button type="primary" onClick={() => openLabelEditor()}>
              新建标签
            </Button>
          ) : null}
        </Space>
      </Space>
      <Tabs
        activeKey={activeTab}
        onChange={changeTab}
        items={[
          { key: 'labels', label: labelsCount === undefined ? '标签库' : `标签库（${labelsCount}）` },
          { key: 'datasets', label: '数据集管理' },
          { key: 'global-tools', label: '全局工具标签' },
        ]}
      />
      {activeTab === 'datasets' && (
        <div data-testid="admin-dataset-management">
          <DatasetsPage embedded />
        </div>
      )}
      {activeTab === 'global-tools' && <GlobalTagDimensionsPanel />}
      {activeTab === 'labels' &&
        (labelsQuery.error ? (
          <Alert type="error" showIcon message="标签库加载失败" />
        ) : (
          <>
            <Space style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', marginBottom: 12, width: '100%' }}>
              <ServerSearchInput
                value={labelSearchDraft}
                onChange={(value) => {
                  setLabelSearchDraft(value);
                  if (!value) applyLabelSearch('');
                }}
                onSearch={applyLabelSearch}
                placeholder="按中文名称或英文系统值搜索"
                ariaLabel="搜索标签库"
                loading={labelsQuery.isFetching}
                style={{ minWidth: 0, flex: '1 1 240px' }}
              />
              <Typography.Text type="secondary" style={{ flex: '0 1 auto' }}>
                {labelSearch ? `搜索“${labelSearch}”匹配 ${labelsCount ?? labels.length} 个标签` : `共 ${labelsCount ?? labels.length} 个标签`}
              </Typography.Text>
            </Space>
            <Table<Row>
              rowKey="id"
              loading={labelsQuery.isLoading || labelsQuery.isFetching}
              dataSource={labels}
              pagination={{
                current: labelPage + 1,
                pageSize: labelPageSize,
                total: labelsCount,
                showSizeChanger: true,
                onChange: (page, pageSize) => {
                  if (pageSize !== labelPageSize) {
                    setLabelPageSize(pageSize);
                    setLabelPage(0);
                    return;
                  }
                  setLabelPage(Math.max(0, page - 1));
                },
              }}
              scroll={{ x: 820 }}
              locale={{ emptyText: labelSearch ? '没有匹配的标签' : '暂无标签' }}
              columns={[
                { title: '中文名称', dataIndex: 'label_key', key: 'label_key' },
                { title: '系统值（英文）', dataIndex: 'label_value', key: 'label_value' },
                {
                  title: '标注类型',
                  dataIndex: 'annotation_kind',
                  key: 'annotation_kind',
                  render: (value) => <Tag color="blue">{annotationKindLabel(value)}</Tag>,
                },
                { title: '引用数', dataIndex: 'reference_count', key: 'reference_count' },
                {
                  title: '状态',
                  dataIndex: 'status',
                  key: 'status',
                  render: (value) => (
                    <Tag color={value === 'active' ? 'green' : 'default'}>{value === 'active' ? '启用' : '停用'}</Tag>
                  ),
                },
                {
                  title: '操作',
                  key: 'actions',
                  render: (_, row) => (
                    <Space>
                      <Button type="link" size="small" onClick={() => openLabelEditor(row)}>
                        编辑
                      </Button>
                      <Button
                        type="link"
                        size="small"
                        loading={labelStatusMutation.isPending}
                        onClick={() => labelStatusMutation.mutate(row)}
                      >
                        {row.status === 'active' ? '停用' : '启用'}
                      </Button>
                    </Space>
                  ),
                },
              ]}
            />
          </>
        ))}
      <Modal
        title={editingLabel ? '编辑标签' : '新建标签'}
        open={labelOpen}
        centered
        destroyOnClose
        confirmLoading={labelSaveMutation.isPending}
        onCancel={() => {
          if (!labelSaveMutation.isPending) {
            setLabelOpen(false);
            setEditingLabel(null);
          }
        }}
        onOk={() => void labelForm.submit()}
        okText={editingLabel ? '保存' : '创建'}
        cancelText="取消"
      >
        <Form form={labelForm} layout="vertical" onFinish={(values) => labelSaveMutation.mutate(values)}>
          <Form.Item
            name="label_key"
            label="中文名称"
            rules={[{ required: true, whitespace: true, message: '请输入标签中文名称' }]}
          >
            <Input maxLength={128} />
          </Form.Item>
          <Form.Item
            name="label_value"
            label="系统值（英文）"
            extra={
              editingLabel
                ? '用于模型类别和接口传递；修改只影响后续新任务和新训练，已冻结数据版本、训练作业和已发布模型保留原值。只能输入英文字母、数字和下划线，且以字母开头。'
                : '用于模型类别和接口传递；只能输入英文字母、数字和下划线，且以字母开头。'
            }
            rules={[{ required: true, pattern: /^[A-Za-z][A-Za-z0-9_]*$/, message: '请输入系统值' }]}
          >
            <Input maxLength={128} />
          </Form.Item>
          {editingLabel ? (
            <Alert
              type="warning"
              showIcon
              message="修改英文系统值后不会重写历史模型"
              description="新建任务、后续训练和新的边端制品会使用新值；已冻结的数据版本、训练作业、发布记录和模型文件继续使用保存时的旧值。"
              style={{ marginBottom: 16 }}
            />
          ) : null}
          <Form.Item
            label="标注类型"
            extra="一期训练链路使用 YOLO11 目标检测并部署到 BM1688，只支持矩形框；系统内部仍使用 bbox 保存。"
          >
            <Input disabled aria-label="当前标注类型" value="矩形框（目标检测）" />
          </Form.Item>
          <Form.Item
            name="color"
            label="颜色（自动分配）"
            extra={
              editingLabel
                ? '修改颜色后，系统会校验全局唯一，不能与其他标签重复。'
                : '系统会自动分配未使用的色号，保存时再次校验，避免与已有标签重复。'
            }
            rules={editingLabel ? [{ pattern: /^#[0-9A-Fa-f]{6}$/, message: '请输入 #RRGGBB 颜色' }] : []}
          >
            <Input disabled={!editingLabel} maxLength={7} />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={3} maxLength={512} />
          </Form.Item>
        </Form>
      </Modal>
    </TablePanel>
  );
}

export default function AdminPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const currentView = new URLSearchParams(location.search).get('view');
  const governance = currentView === 'governance';
  const edgeDevices = currentView === 'edge-devices';
  const publishConfig = currentView === 'publish-config';
  const expertS3Config = currentView === 'expert-s3-config';
  const modelAccessConfig = currentView === 'model-access-config';
  const imageCleaningConfig = currentView === 'image-cleaning';
  const [userSearch, setUserSearch] = useState('');
  const [userSearchDraft, setUserSearchDraft] = useState('');
  const [userPage, setUserPage] = useState(0);
  const [userPageSize, setUserPageSize] = useState(20);
  const normalizedUserSearch = userSearch.trim();
  const usersQuery = useQuery({
    queryKey: ['system-users', userPage, userPageSize, normalizedUserSearch],
    queryFn: async () => {
      const params: Record<string, unknown> = { page: userPage, size: userPageSize };
      if (normalizedUserSearch) params.q = normalizedUserSearch;
      return request.get('/v1/users', { params });
    },
    enabled:
      !governance &&
      !edgeDevices &&
      !publishConfig &&
      !expertS3Config &&
      !modelAccessConfig &&
      !imageCleaningConfig,
    staleTime: 30_000,
    placeholderData: (previous) => previous,
  });
  const rows = listData(usersQuery.data);
  const userTotal = listTotal(usersQuery.data, rows.length);
  const applyUserSearch = (value: string) => {
    const next = value.trim();
    setUserSearchDraft(value);
    setUserPage(0);
    setUserSearch(next);
  };
  const columns: ColumnsType<Row> = [
    {
      title: '账号',
      key: 'account',
      render: (_, row) => row.username || row.email || `用户 #${row.id}`,
    },
    {
      title: '角色',
      key: 'role',
      width: 130,
      render: (_, row) => {
        const manager = Boolean(row.is_superuser || row.is_admin || row.role === 'admin');
        return <Tag color={manager ? 'blue' : 'default'}>{manager ? '管理人员' : '标注人员'}</Tag>;
      },
    },
    {
      title: '状态',
      key: 'status',
      width: 120,
      render: (_, row) => {
        const active = row.status == null || ['ACTIVE', 'active', 'ENABLED', 'enabled'].includes(String(row.status));
        return <Tag color={active ? 'green' : 'default'}>{active ? '正常' : '停用'}</Tag>;
      },
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 190,
      render: (value) => (value ? new Date(value).toLocaleString() : '-'),
    },
  ];

  if (governance) {
    return (
      <>
        <ModuleHeader module="admin" />
        <GovernancePanel />
      </>
    );
  }

  if (edgeDevices) {
    return (
      <>
        <ModuleHeader module="admin" />
        <EdgeDevicesPanel />
      </>
    );
  }

  if (publishConfig) {
    return (
      <>
        <ModuleHeader module="admin" />
        <PublishConfigPanel />
      </>
    );
  }

  if (expertS3Config) {
    return (
      <>
        <ModuleHeader module="admin" />
        <ExpertS3ConfigPanel />
      </>
    );
  }

  if (modelAccessConfig) {
    return (
      <>
        <ModuleHeader module="admin" />
        <ModelAccessConfigPanel />
      </>
    );
  }

  if (imageCleaningConfig) {
    return (
      <>
        <ModuleHeader module="admin" />
        <ImageCleaningConfigPanel />
      </>
    );
  }

  const dataState = usersQuery.error ? 'error' : usersQuery.isLoading ? 'loading' : userTotal ? 'ready' : 'empty';
  return (
    <>
      <ModuleHeader module="admin" />
      <ModuleStatus module="admin" dataState={dataState} rowCount={userTotal} />
      <Space style={{ marginBottom: 14, flexWrap: 'wrap' }}>
        <ServerSearchInput
          value={userSearchDraft}
          onChange={(value) => {
            setUserSearchDraft(value);
            if (!value) applyUserSearch('');
          }}
          onSearch={applyUserSearch}
          placeholder="搜索账号、用户 ID 或账号类型"
          ariaLabel="搜索系统账号"
          loading={usersQuery.isFetching}
          style={{ width: 260, maxWidth: '100%' }}
        />
        <Button icon={<ReloadOutlined />} loading={usersQuery.isFetching} onClick={() => void usersQuery.refetch()}>
          刷新账号
        </Button>
        <Button icon={<SafetyCertificateOutlined />} onClick={() => navigate('/admin?view=governance')}>
          标注与数据治理
        </Button>
        <Button icon={<CloudServerOutlined />} onClick={() => navigate('/admin?view=edge-devices')}>
          边端设备管理
        </Button>
        <Button icon={<SettingOutlined />} onClick={() => navigate('/admin?view=publish-config')}>
          发布配置
        </Button>
        <Button icon={<ApiOutlined />} onClick={() => navigate('/admin?view=expert-s3-config')}>
          专家 S3 回流配置
        </Button>
        <Button icon={<ApiOutlined />} onClick={() => navigate('/admin?view=model-access-config')}>
          模型接入配置
        </Button>
        <Button icon={<SettingOutlined />} onClick={() => navigate('/admin?view=image-cleaning')}>
          图片清洗配置
        </Button>
      </Space>
      {usersQuery.error ? (
        <Alert
          type="error"
          showIcon
          message={errorText(usersQuery.error)}
          description="只有管理人员可以查看系统账号。"
          action={
            <Button size="small" onClick={() => void usersQuery.refetch()}>
              重试
            </Button>
          }
          style={{ marginBottom: 16 }}
        />
      ) : null}
      <TablePanel>
        <h2>
          <TeamOutlined style={{ marginRight: 8 }} />
          账号与角色
        </h2>
        <p>平台只使用管理人员和标注人员两类角色；任务创建者与协作者权限由任务关系决定。</p>
        <Table<Row>
          style={{ marginTop: 14 }}
          rowKey={(row) => String(row.id ?? row.username ?? row.email)}
          columns={columns}
          dataSource={rows}
          loading={usersQuery.isLoading || usersQuery.isFetching}
          size="small"
          pagination={false}
          scroll={{ x: 680 }}
          locale={{ emptyText: normalizedUserSearch ? '没有匹配的账号' : '暂无账号记录' }}
        />
        {userTotal > 0 ? (
          <Pagination
            style={{ marginTop: 14, textAlign: 'right' }}
            current={userPage + 1}
            total={userTotal}
            pageSize={userPageSize}
            showSizeChanger
            onChange={(page, pageSize) => {
              setUserPage(page - 1);
              if (pageSize !== userPageSize) {
                setUserPageSize(pageSize);
                setUserPage(0);
              }
            }}
          />
        ) : null}
      </TablePanel>
    </>
  );
}
