import { useQuery } from '@tanstack/react-query';
import { Empty, Space, Statistic, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import { getOnlineUsers, type OnlineUser } from '@/api/services/personnelStatistics';
import { listData, ModuleHeader, QueueHint, QueueToolbar, TablePanel } from '../shared';

function formatTime(value?: string | null) {
  if (!value) return '-';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

export default function ActivityStatisticsPage() {
  const query = useQuery({
    queryKey: ['personnel-online-users'],
    queryFn: async () => {
      const response = await getOnlineUsers();
      return response.data;
    },
    staleTime: 10_000,
    refetchInterval: 15_000,
  });

  const columns: ColumnsType<OnlineUser> = [
    { title: '人员', dataIndex: 'username', key: 'username', width: 180 },
    {
      title: '状态',
      key: 'status',
      width: 100,
      render: () => <Tag color="green">在线</Tag>,
    },
    { title: '本次登录', dataIndex: 'login_at', key: 'login_at', render: formatTime },
    { title: '最近心跳', dataIndex: 'last_seen_at', key: 'last_seen_at', render: formatTime },
    { title: '最近页面', dataIndex: 'last_path', key: 'last_path', ellipsis: true, render: (value) => value || '-' },
    { title: 'IP', dataIndex: 'ip', key: 'ip', width: 140, render: (value) => value || '-' },
  ];

  const users = (listData(query.data?.users) as OnlineUser[]) ?? [];
  return (
    <TablePanel>
      <ModuleHeader module="statistics" />
      <Typography.Title level={2} style={{ margin: 0, fontSize: 20 }}>
        在线活跃状态
      </Typography.Title>
      <Typography.Text type="secondary">
        在线状态来自页面心跳；登录后不操作但页面保持打开，仍会算在线，但不会算有效标注工作。
      </Typography.Text>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12, margin: '18px 0' }}>
        <Statistic title="当前在线" value={users.length} suffix="人" />
        <Statistic title="在线判定窗口" value={query.data?.online_window_seconds ?? 120} suffix="秒" />
      </div>
      <QueueToolbar>
        <Space>
          <QueueHint>每 15 秒刷新；无操作但页面开着的心跳仍算在线</QueueHint>
        </Space>
      </QueueToolbar>
      <Table<OnlineUser>
        rowKey="session_id"
        size="middle"
        columns={columns}
        dataSource={users}
        loading={query.isLoading || query.isFetching}
        pagination={false}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有在线用户" /> }}
      />
    </TablePanel>
  );
}
