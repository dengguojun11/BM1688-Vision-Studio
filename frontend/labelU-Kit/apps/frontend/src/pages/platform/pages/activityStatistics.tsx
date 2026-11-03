import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Empty, Space, Statistic, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import {
  getOnlineUsers,
  getPersonnelDashboard,
  type OnlineUser,
  type PersonnelUserRow,
} from '@/api/services/personnelStatistics';
import { ModuleHeader, QueueHint, QueueToolbar, TablePanel } from '../shared';

function isoDate(value: Date) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, '0');
  const day = String(value.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function formatTime(value?: string | null) {
  if (!value) return '-';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

function formatSeconds(value: number) {
  const seconds = Math.max(0, Number(value || 0));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function isToday(value?: string | null) {
  if (!value) return false;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return false;
  const now = new Date();
  return (
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()
  );
}

interface ActivityRow extends PersonnelUserRow {
  onlineSession?: OnlineUser;
}

export default function ActivityStatisticsPage() {
  const today = isoDate(new Date());
  const query = useQuery({
    queryKey: ['personnel-activity-users', today],
    queryFn: async () => {
      const [onlineResponse, dashboardResponse] = await Promise.all([
        getOnlineUsers(),
        getPersonnelDashboard({ date_from: today, date_to: today }),
      ]);
      return {
        online: onlineResponse.data,
        dashboard: dashboardResponse.data,
      };
    },
    staleTime: 10_000,
    refetchInterval: 15_000,
  });

  const onlineById = useMemo(() => {
    const map = new Map<number, OnlineUser>();
    (query.data?.online?.users ?? []).forEach((user) => map.set(user.user_id, user));
    return map;
  }, [query.data?.online?.users]);

  const users = useMemo<ActivityRow[]>(
    () =>
      (query.data?.dashboard?.users ?? []).map((user) => ({
        ...user,
        onlineSession: onlineById.get(user.user_id),
      })),
    [onlineById, query.data?.dashboard?.users],
  );

  const columns: ColumnsType<ActivityRow> = [
    { title: '人员', dataIndex: 'username', key: 'username', width: 190 },
    {
      title: '状态',
      key: 'status',
      width: 110,
      render: (_, row) => {
        if (row.online) return <Tag color="green">在线</Tag>;
        if (isToday(row.last_active_at)) return <Tag color="blue">今日活跃</Tag>;
        if (row.last_active_at) return <Tag>最近活跃</Tag>;
        return <Tag>离线</Tag>;
      },
    },
    { title: '最后活动', dataIndex: 'last_active_at', key: 'last_active_at', width: 175, render: formatTime },
    {
      title: '今日在线时长',
      dataIndex: 'online_seconds',
      key: 'online_seconds',
      width: 120,
      render: formatSeconds,
    },
    {
      title: '最近页面',
      key: 'last_path',
      ellipsis: true,
      render: (_, row) => row.onlineSession?.last_path || '-',
    },
    {
      title: 'IP',
      key: 'ip',
      width: 145,
      render: (_, row) => row.onlineSession?.ip || '-',
    },
    {
      title: '最后有效操作',
      dataIndex: 'last_business_at',
      key: 'last_business_at',
      width: 175,
      render: formatTime,
    },
  ];

  const onlineUsers = query.data?.online?.users ?? [];
  const dashboard = query.data?.dashboard;
  return (
    <TablePanel>
      <ModuleHeader module="statistics" />
      <Typography.Title level={2} style={{ margin: 0, fontSize: 20 }}>
        在线活跃状态
      </Typography.Title>
      <Typography.Text type="secondary">
        当前在线只看心跳；今日活跃包括今天打开过系统或产生过操作的用户。离线用户也会展示，方便核对全部人员。
      </Typography.Text>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12, margin: '18px 0' }}>
        <Statistic title="当前在线" value={onlineUsers.length} suffix="人" />
        <Statistic title="今日活跃" value={dashboard?.summary.active_users ?? 0} suffix="人" />
        <Statistic title="全部人员" value={users.length} suffix="人" />
        <Statistic title="在线判定窗口" value={query.data?.online?.online_window_seconds ?? 120} suffix="秒" />
      </div>
      <QueueToolbar>
        <Space>
          <QueueHint>每 15 秒刷新；IP 从最近一次页面心跳记录，历史未记录 IP 的会话显示为“-”。</QueueHint>
        </Space>
      </QueueToolbar>
      <Table<ActivityRow>
        rowKey="user_id"
        size="middle"
        columns={columns}
        dataSource={users}
        loading={query.isLoading || query.isFetching}
        pagination={false}
        scroll={{ x: 1050 }}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无人员活跃数据" /> }}
      />
    </TablePanel>
  );
}
