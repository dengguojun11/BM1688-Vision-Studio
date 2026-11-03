import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Drawer, Empty, Input, Space, Spin, Statistic, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import {
  getPersonnelDashboard,
  getPersonnelDetail,
  type PersonnelDailyRow,
  type PersonnelUserRow,
} from '@/api/services/personnelStatistics';
import { listData } from '../shared';
import { ModuleHeader, QueueHint, QueueToolbar, TablePanel } from '../shared';

const PAGE_SIZE = 20;

function isoDate(value: Date) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, '0');
  const day = String(value.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function daysAgo(days: number) {
  const value = new Date();
  value.setDate(value.getDate() - days);
  return isoDate(value);
}

function formatSeconds(value: number) {
  const seconds = Math.max(0, Number(value || 0));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function formatTime(value?: string | null) {
  if (!value) return '-';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

function statusTag(row: PersonnelUserRow) {
  if (row.online) return <Tag color="green">在线</Tag>;
  return <Tag>离线</Tag>;
}

export default function PersonnelStatisticsPage() {
  const [dateFrom, setDateFrom] = useState(() => daysAgo(6));
  const [dateTo, setDateTo] = useState(() => isoDate(new Date()));
  const [page, setPage] = useState(0);
  const [detailUserId, setDetailUserId] = useState<number | null>(null);

  const query = useQuery({
    queryKey: ['personnel-statistics', dateFrom, dateTo],
    queryFn: async () => {
      const response = await getPersonnelDashboard({ date_from: dateFrom, date_to: dateTo });
      return response.data;
    },
    staleTime: 15_000,
    refetchInterval: 30_000,
  });

  const detail = useQuery({
    queryKey: ['personnel-statistics-detail', detailUserId, dateFrom, dateTo],
    queryFn: async () => {
      const response = await getPersonnelDetail(Number(detailUserId), {
        date_from: dateFrom,
        date_to: dateTo,
      });
      return response.data;
    },
    enabled: detailUserId !== null,
  });

  const columns = useMemo<ColumnsType<PersonnelUserRow>>(
    () => [
      {
        title: '人员',
        key: 'user',
        fixed: 'left',
        width: 160,
        render: (_, row) => (
          <Space direction="vertical" size={0}>
            <Typography.Text strong>{row.username}</Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              用户 #{row.user_id}
            </Typography.Text>
          </Space>
        ),
      },
      {
        title: '状态',
        key: 'status',
        width: 110,
        render: (_, row) => statusTag(row),
      },
      {
        title: '最后活动',
        dataIndex: 'last_active_at',
        key: 'last_active_at',
        width: 180,
        render: (value) => formatTime(value),
      },
      {
        title: '今日在线',
        dataIndex: 'online_seconds',
        key: 'online_seconds',
        width: 110,
        render: (value) => formatSeconds(value),
      },
      {
        title: '有效操作时长',
        dataIndex: 'effective_work_seconds',
        key: 'effective_work_seconds',
        width: 120,
        render: (value) => formatSeconds(value),
      },
      {
        title: '标注图片',
        dataIndex: 'annotation_images',
        key: 'annotation_images',
        width: 100,
      },
      {
        title: '标注框',
        dataIndex: 'annotation_boxes',
        key: 'annotation_boxes',
        width: 90,
      },
      {
        title: '编辑次数',
        dataIndex: 'annotation_edits',
        key: 'annotation_edits',
        width: 90,
      },
      {
        title: '审核图片',
        dataIndex: 'review_images',
        key: 'review_images',
        width: 100,
      },
      {
        title: '审核通过',
        dataIndex: 'review_approved',
        key: 'review_approved',
        width: 90,
      },
      {
        title: '审核驳回',
        dataIndex: 'review_rejected',
        key: 'review_rejected',
        width: 90,
      },
      {
        title: '仲裁/作废',
        key: 'review_other',
        width: 110,
        render: (_, row) => `${row.review_arbitrated} / ${row.review_invalidated}`,
      },
      {
        title: '最后有效操作',
        key: 'last_business',
        width: 180,
        render: (_, row) => formatTime(row.last_business_at),
      },
      {
        title: '操作',
        key: 'actions',
        fixed: 'right',
        width: 90,
        render: (_, row) => (
          <Typography.Link onClick={() => setDetailUserId(row.user_id)}>查看详情</Typography.Link>
        ),
      },
    ],
    [],
  );

  const dailyColumns = useMemo<ColumnsType<PersonnelDailyRow>>(
    () => [
      { title: '日期', dataIndex: 'date', key: 'date', width: 120 },
      { title: '标注图片', dataIndex: 'annotation_images', key: 'annotation_images' },
      { title: '标注框', dataIndex: 'annotation_boxes', key: 'annotation_boxes' },
      { title: '编辑次数', dataIndex: 'annotation_edits', key: 'annotation_edits' },
      { title: '审核图片', dataIndex: 'review_images', key: 'review_images' },
      { title: '通过', dataIndex: 'review_approved', key: 'review_approved' },
      { title: '驳回', dataIndex: 'review_rejected', key: 'review_rejected' },
    ],
    [],
  );

  const summary = query.data?.summary;
  const users = (listData(query.data?.users) as PersonnelUserRow[]) ?? [];

  return (
    <TablePanel>
      <ModuleHeader module="statistics" />
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
        <div>
          <Typography.Title level={2} style={{ margin: 0, fontSize: 20 }}>
            人员标注审核统计
          </Typography.Title>
          <Typography.Text type="secondary">
            统计实际标注内容变化和审核决策；无操作保存不计入有效标注工作量。
          </Typography.Text>
        </div>
        <Space wrap>
          <Input
            type="date"
            value={dateFrom}
            onChange={(event) => {
              setDateFrom(event.target.value);
              setPage(0);
            }}
          />
          <Typography.Text type="secondary">至</Typography.Text>
          <Input
            type="date"
            value={dateTo}
            onChange={(event) => {
              setDateTo(event.target.value);
              setPage(0);
            }}
          />
        </Space>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, margin: '18px 0' }}>
        <Statistic title="当前在线" value={summary?.online_now ?? 0} suffix="人" />
        <Statistic title="范围活跃" value={summary?.active_users ?? 0} suffix="人" />
        <Statistic title="标注图片" value={summary?.annotation_images ?? 0} suffix="张" />
        <Statistic title="标注框" value={summary?.annotation_boxes ?? 0} suffix="个" />
        <Statistic title="审核图片" value={summary?.review_images ?? 0} suffix="张" />
        <Statistic title="审核通过/驳回" value={`${summary?.review_approved ?? 0} / ${summary?.review_rejected ?? 0}`} />
      </div>

      <QueueToolbar>
        <Space>
          <QueueHint>共 {users.length} 人；有效操作时长按业务操作聚类估算</QueueHint>
        </Space>
      </QueueToolbar>

      <Table<PersonnelUserRow>
        rowKey="user_id"
        size="middle"
        loading={query.isLoading || query.isFetching}
        columns={columns}
        dataSource={users}
        scroll={{ x: 1480 }}
        pagination={{
          current: page + 1,
          pageSize: PAGE_SIZE,
          total: users.length,
          showSizeChanger: false,
          onChange: (next) => setPage(Math.max(0, next - 1)),
        }}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前范围暂无人员统计" /> }}
      />

      <Drawer
        title={detail.data?.user ? `${detail.data.user.username} · 统计详情` : '人员统计详情'}
        width={760}
        open={detailUserId !== null}
        onClose={() => setDetailUserId(null)}
      >
        {detail.isLoading ? (
          <div style={{ display: 'grid', placeItems: 'center', minHeight: 200 }}>
            <Spin />
          </div>
        ) : (
          <Table<PersonnelDailyRow>
            rowKey="date"
            size="small"
            columns={dailyColumns}
            dataSource={detail.data?.daily ?? []}
            pagination={false}
            locale={{ emptyText: '当前范围暂无每日数据' }}
          />
        )}
      </Drawer>
    </TablePanel>
  );
}
