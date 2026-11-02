import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Button, Descriptions, Empty, Modal, Space, Spin, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import ServerSearchInput from '@/components/ServerSearchInput';
import {
  getSystemOperationLogDetail,
  getSystemOperationLogs,
  type SystemLogCategory,
  type SystemOperationLogItem,
} from '@/api/services/systemLogs';
import {
  listData,
  listTotal,
  ModuleHeader,
  QueueHint,
  QueueToolbar,
  TablePanel,
} from '../shared';

const PAGE_SIZE = 20;
const CATEGORY_COPY: Record<SystemLogCategory, { title: string; description: string }> = {
  user: {
    title: '用户操作日志',
    description: '记录登录用户发起的写操作，例如保存标注、创建任务、启动训练。',
  },
  system: {
    title: '系统操作日志',
    description: '记录系统回调、服务间调用和内部任务产生的操作。',
  },
};

function displayTime(value?: string | null) {
  if (!value) return '-';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

function statusTag(value?: number | null) {
  if (value == null) return <Tag>系统</Tag>;
  if (value >= 500) return <Tag color="red">{value}</Tag>;
  if (value >= 400) return <Tag color="orange">{value}</Tag>;
  return <Tag color="green">{value}</Tag>;
}

export default function SystemLogsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const category: SystemLogCategory =
    searchParams.get('category') === 'system' ? 'system' : 'user';
  const copy = CATEGORY_COPY[category];
  const [searchDraft, setSearchDraft] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [detailId, setDetailId] = useState<number | null>(null);

  useEffect(() => {
    setPage(0);
    setSearch('');
    setSearchDraft('');
  }, [category]);

  const query = useQuery({
    queryKey: ['system-operation-logs', category, page, search],
    queryFn: async () => {
      const response = await getSystemOperationLogs({
        category,
        page,
        size: PAGE_SIZE,
        ...(search ? { q: search } : {}),
      });
      const rows = listData(response) as SystemOperationLogItem[];
      return { rows, total: listTotal(response, rows.length) };
    },
    staleTime: 5_000,
  });

  const detailQuery = useQuery({
    queryKey: ['system-operation-log-detail', detailId],
    queryFn: async () => {
      const response = await getSystemOperationLogDetail(Number(detailId));
      return response.data as import('@/api/services/systemLogs').SystemOperationLogDetail;
    },
    enabled: detailId !== null,
  });

  const columns = useMemo<ColumnsType<SystemOperationLogItem>>(
    () => [
      {
        title: '时间',
        dataIndex: 'created_at',
        key: 'created_at',
        width: 180,
        render: (value) => displayTime(value),
      },
      {
        title: '操作',
        key: 'action',
        render: (_, row) => (
          <Space direction="vertical" size={0}>
            <Typography.Text strong>{row.action}</Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {row.summary && row.summary !== row.action ? row.summary : row.path}
            </Typography.Text>
          </Space>
        ),
      },
      {
        title: category === 'user' ? '用户' : '来源',
        key: 'actor',
        width: 170,
        render: (_, row) =>
          category === 'user'
            ? (row.username || (row.user_id ? `用户 #${row.user_id}` : '-'))
            : (row.source || '系统'),
      },
      {
        title: '结果',
        dataIndex: 'status_code',
        key: 'status_code',
        width: 90,
        render: (value) => statusTag(value),
      },
      {
        title: '耗时',
        dataIndex: 'duration_ms',
        key: 'duration_ms',
        width: 90,
        render: (value) => `${Number(value || 0)} ms`,
      },
      {
        title: '操作',
        key: 'detail',
        width: 90,
        render: (_, row) => (
          <Button type="link" size="small" onClick={() => setDetailId(row.id)}>
            查看详细
          </Button>
        ),
      },
    ],
    [category],
  );

  const applySearch = (value: string) => {
    setSearchDraft(value);
    setSearch(value.trim());
    setPage(0);
  };

  return (
    <TablePanel>
      <ModuleHeader module="system-logs" />
      <div style={{ marginBottom: 14 }}>
        <Typography.Title level={2} style={{ margin: 0, fontSize: 20 }}>
          {copy.title}
        </Typography.Title>
        <Typography.Text type="secondary">{copy.description}</Typography.Text>
      </div>
      <QueueToolbar>
        <Space wrap>
          <ServerSearchInput
            value={searchDraft}
            placeholder={category === 'user' ? '搜索操作、用户或路径' : '搜索操作、来源或路径'}
            ariaLabel={`搜索${copy.title}`}
            loading={query.isFetching}
            onChange={(value) => setSearchDraft(value)}
            onSearch={applySearch}
            style={{ width: 300, maxWidth: '100%' }}
          />
          <Button
            size="small"
            type={category === 'user' ? 'primary' : 'default'}
            onClick={() => setSearchParams({ category: 'user' })}
          >
            用户操作日志
          </Button>
          <Button
            size="small"
            type={category === 'system' ? 'primary' : 'default'}
            onClick={() => setSearchParams({ category: 'system' })}
          >
            系统操作日志
          </Button>
          <QueueHint>当前页 {query.data?.rows.length ?? 0} 条 / 共 {query.data?.total ?? 0} 条</QueueHint>
        </Space>
      </QueueToolbar>
      <Table<SystemOperationLogItem>
        rowKey="id"
        size="middle"
        columns={columns}
        dataSource={query.data?.rows ?? []}
        loading={query.isLoading || query.isFetching}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无操作日志" /> }}
        pagination={{
          current: page + 1,
          pageSize: PAGE_SIZE,
          total: query.data?.total ?? 0,
          showSizeChanger: false,
          onChange: (nextPage) => setPage(Math.max(0, nextPage - 1)),
        }}
        scroll={{ x: 900 }}
      />

      <Modal
        title="操作日志详情"
        open={detailId !== null}
        footer={null}
        width={820}
        onCancel={() => setDetailId(null)}
        destroyOnClose
      >
        {detailQuery.isLoading ? (
          <div style={{ display: 'grid', placeItems: 'center', minHeight: 180 }}>
            <Spin />
          </div>
        ) : detailQuery.data ? (
          <>
            <Descriptions bordered column={2} size="small" style={{ marginBottom: 16 }}>
              <Descriptions.Item label="操作">{detailQuery.data.action}</Descriptions.Item>
              <Descriptions.Item label="类别">
                {detailQuery.data.category === 'USER' ? '用户操作' : '系统操作'}
              </Descriptions.Item>
              <Descriptions.Item label="用户">
                {detailQuery.data.username || detailQuery.data.user_id || '-'}
              </Descriptions.Item>
              <Descriptions.Item label="来源">
                {detailQuery.data.source || '-'}
              </Descriptions.Item>
              <Descriptions.Item label="请求">{`${detailQuery.data.method} ${detailQuery.data.path}`}</Descriptions.Item>
              <Descriptions.Item label="结果">{detailQuery.data.status_code ?? '-'}</Descriptions.Item>
              <Descriptions.Item label="耗时">{detailQuery.data.duration_ms} ms</Descriptions.Item>
              <Descriptions.Item label="时间">{displayTime(detailQuery.data.created_at)}</Descriptions.Item>
            </Descriptions>
            <Typography.Text strong>详细日志</Typography.Text>
            <pre
              style={{
                marginTop: 8,
                maxHeight: 360,
                overflow: 'auto',
                padding: 12,
                border: '1px solid #e5e7eb',
                borderRadius: 8,
                background: '#f8fafc',
                fontSize: 12,
                lineHeight: 1.6,
              }}
            >
              {JSON.stringify(detailQuery.data.detail, null, 2)}
            </pre>
          </>
        ) : (
          <Empty description="日志详情加载失败" />
        )}
      </Modal>
    </TablePanel>
  );
}
