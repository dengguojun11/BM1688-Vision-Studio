import { BellOutlined, ReloadOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { Alert, Badge, Button, Empty, List, Pagination, Popover, Spin, Tooltip } from 'antd';
import { useState } from 'react';

import request from '@/api/request';
import ServerSearchInput from '@/components/ServerSearchInput';
import { listData, listTotal, responseStatus, type Row } from './shared';

const ALERT_PAGE_SIZE = 10;

export default function NotificationCenter() {
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState(0);
  const [search, setSearch] = useState('');
  const [searchDraft, setSearchDraft] = useState('');
  const query = useQuery({
    queryKey: ['notifications', page, search],
    queryFn: async () => {
      const response = await request.get('/v1/workspace/alerts', {
        params: {
          page,
          size: ALERT_PAGE_SIZE,
          ...(search ? { q: search } : {}),
        },
      });
      // `request` returns the business envelope directly. Keep the envelope
      // here so `meta_data.total` survives; listData/listTotal also accept the
      // outer Axios shape used by legacy test adapters.
      const rows = listData(response);
      return {
        rows,
        total: listTotal(response, rows.length),
      };
    },
    // 通知弹层打开时才读取，避免每个页面常驻轮询告警接口。
    enabled: open && Boolean(localStorage.getItem('token')),
    staleTime: 15_000,
  });
  const alerts = query.data?.rows || [];
  const alertTotal = query.data?.total ?? alerts.length;
  const status = responseStatus(query.error);
  const applySearch = (value: string) => {
    const normalized = value.trim();
    setSearchDraft(value);
    setSearch(normalized);
    setPage(0);
  };
  const content = (
    <div style={{ width: 380, maxWidth: 'calc(100vw - 32px)' }} aria-label="通知中心">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <strong>通知中心</strong>
        <Button
          type="text"
          size="small"
          icon={<ReloadOutlined />}
          aria-label="刷新通知"
          loading={query.isFetching}
          onClick={() => void query.refetch()}
        />
      </div>
      <ServerSearchInput
        value={searchDraft}
        onChange={(value) => {
          setSearchDraft(value);
          if (!value) applySearch('');
        }}
        onSearch={applySearch}
        placeholder="按任务、作业或错误信息搜索"
        ariaLabel="搜索通知"
        loading={query.isFetching}
        style={{ width: '100%', marginBottom: 10 }}
      />
      {query.isLoading ? <div role="status" aria-live="polite" style={{ padding: 24, textAlign: 'center' }}><Spin /></div> : query.error ? (
        <Alert
          type="warning"
          showIcon
          message={status === 403 ? '暂无通知权限' : '通知暂不可用'}
          description={status === 403 ? '当前账号不允许查看告警。' : '请稍后重试。'}
          action={<Button size="small" onClick={() => void query.refetch()}>重试</Button>}
        />
      ) : alerts.length ? (
        <>
          <List
            size="small"
            dataSource={alerts}
            renderItem={(row: Row) => (
              <List.Item>
                <List.Item.Meta
                  title={row.title || row.alert_type || row.type || '系统告警'}
                  description={row.message || row.detail || row.reason || row.status || '请查看对应模块详情'}
                />
              </List.Item>
            )}
          />
          {alertTotal > ALERT_PAGE_SIZE && (
            <Pagination
              size="small"
              current={page + 1}
              pageSize={ALERT_PAGE_SIZE}
              total={alertTotal}
              showSizeChanger={false}
              showLessItems
              style={{ marginTop: 10, textAlign: 'right' }}
              onChange={(nextPage) => setPage(nextPage - 1)}
            />
          )}
        </>
      ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={search ? '没有匹配的通知' : '暂无新通知'} />}
    </div>
  );

  return (
    <Popover
      trigger="click"
      placement="bottomRight"
      open={open}
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen);
      }}
      content={content}
    >
      <Tooltip title="通知中心">
        <Badge count={alertTotal} size="small" overflowCount={99}>
          <Button type="text" icon={<BellOutlined />} aria-label={alertTotal ? `通知中心，有 ${alertTotal} 条` : '通知中心'} />
        </Badge>
      </Tooltip>
    </Popover>
  );
}
