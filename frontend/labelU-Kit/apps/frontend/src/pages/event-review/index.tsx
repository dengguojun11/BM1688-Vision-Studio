import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertOutlined,
  CameraOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  ExclamationCircleOutlined,
  EyeOutlined,
  FileTextOutlined,
  ForwardOutlined,
  PictureOutlined,
  ReloadOutlined,
  SearchOutlined,
  StopOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { Button, Empty, Input, message, Modal, Pagination, Select, Spin, Table, Tag, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import styled from 'styled-components';

import {
  getEventReviewList,
  getEventReviewProgress,
  getEventReviewDetail,
  getEventReviewImageBlob,
  claimEvents,
  performEventAction,
  type EventReviewItem,
  type EventReviewDetail,
  type EventReviewProgress,
} from '@/api/services/eventReview';

/* ---------- styled ---------- */

const Page = styled.div`
  flex: 1;
  min-height: 0;
  overflow: auto;
  background: #f5f7fb;
`;

const PageInner = styled.div`
  max-width: 1360px;
  margin: 0 auto;
  padding: 1.25rem 1.5rem 2rem;
  box-sizing: border-box;
`;

const PageHeader = styled.header`
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem 1.5rem;
  margin-bottom: 1.25rem;
`;

const HeaderText = styled.div`
  flex: 1 1 16rem;
  min-width: 0;
`;

const PageTitle = styled.h1`
  margin: 0 0 0.375rem;
  font-size: 1.25rem;
  font-weight: 600;
  color: #0f172a;
  line-height: 1.35;
`;

const PageDesc = styled.p`
  margin: 0;
  max-width: 42rem;
  font-size: 0.8125rem;
  line-height: 1.55;
  color: #64748b;
`;

const Toolbar = styled.div`
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.625rem;
  padding: 0.75rem 1rem;
  background: #fff;
  border: 1px solid #e8edf4;
  border-radius: 10px;
  box-shadow: 0 4px 16px rgb(15 23 42 / 4%);
  margin-bottom: 1rem;
`;

const KpiGrid = styled.div`
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0.875rem;
  margin-bottom: 1.25rem;

  @media (max-width: 1024px) {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  @media (max-width: 480px) {
    grid-template-columns: 1fr;
  }
`;

const KpiCard = styled.div<{ $accent: string }>`
  display: flex;
  align-items: flex-start;
  gap: 0.875rem;
  padding: 1rem 1.125rem;
  background: #fff;
  border: 1px solid #e8edf4;
  border-radius: 10px;
  box-shadow: 0 4px 16px rgb(15 23 42 / 4%);
  position: relative;
  overflow: hidden;

  &::before {
    content: '';
    position: absolute;
    left: 0;
    top: 0;
    bottom: 0;
    width: 3px;
    background: ${(p) => p.$accent};
  }
`;

const KpiIcon = styled.div<{ $bg: string; $color: string }>`
  flex: 0 0 auto;
  width: 2.5rem;
  height: 2.5rem;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  background: ${(p) => p.$bg};
  color: ${(p) => p.$color};
  font-size: 1.125rem;
`;

const KpiBody = styled.div`
  min-width: 0;
  flex: 1;
`;

const KpiLabel = styled.div`
  font-size: 0.75rem;
  color: #64748b;
  margin-bottom: 0.25rem;
`;

const KpiValue = styled.div`
  font-size: 1.5rem;
  font-weight: 700;
  color: #0f172a;
  line-height: 1.2;
  letter-spacing: -0.02em;
`;

const Panel = styled.section`
  background: #fff;
  border: 1px solid #e8edf4;
  border-radius: 10px;
  padding: 1rem 1.125rem 1.125rem;
  box-shadow: 0 4px 16px rgb(15 23 42 / 4%);
  margin-bottom: 1rem;
`;

const ReviewLayout = styled.div`
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr) 320px;
  gap: 1rem;
  min-height: calc(100vh - 280px);

  @media (max-width: 1200px) {
    grid-template-columns: 240px minmax(0, 1fr) 280px;
  }

  @media (max-width: 900px) {
    grid-template-columns: 1fr;
  }
`;

const ImagePanel = styled.div`
  background: #fff;
  border: 1px solid #e8edf4;
  border-radius: 10px;
  box-shadow: 0 4px 16px rgb(15 23 42 / 4%);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-height: 420px;
`;

const ImageArea = styled.div`
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f1f5f9;
  position: relative;
  min-height: 360px;
  padding: 0.75rem;
`;

const ImageImg = styled.img`
  display: block;
  max-width: 100%;
  max-height: calc(100vh - 260px);
  object-fit: contain;
`;

const ImageNav = styled.div`
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  padding: 0.625rem 1rem;
  border-top: 1px solid #f1f5f9;
  font-size: 0.8125rem;
  color: #64748b;
`;

const ImageNavBtn = styled.button`
  min-width: 4.5rem;
  min-height: 2rem;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #fff;
  padding: 0.25rem 0.75rem;
  font-size: 0.8125rem;
  color: #334155;
  cursor: pointer;
  transition: all 0.15s;

  &:hover:not(:disabled) {
    border-color: #7c3aed;
    color: #7c3aed;
  }

  &:focus-visible {
    outline: 2px solid #a78bfa;
    outline-offset: 2px;
  }

  &:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
`;

const SidePanel = styled.div`
  display: flex;
  flex-direction: column;
  gap: 1rem;
`;

const ActionBar = styled.div`
  display: flex;
  flex-direction: column;
  gap: 0.625rem;
`;

const ActionBtn = styled.button<{ $variant?: 'primary' | 'danger' | 'default' }>`
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  min-height: 2.75rem;
  border-radius: 8px;
  border: 1px solid
    ${(p) => (p.$variant === 'primary' ? '#7c3aed' : p.$variant === 'danger' ? '#fecaca' : '#e2e8f0')};
  background: ${(p) => (p.$variant === 'primary' ? '#7c3aed' : p.$variant === 'danger' ? '#fef2f2' : '#fff')};
  color: ${(p) => (p.$variant === 'primary' ? '#fff' : p.$variant === 'danger' ? '#dc2626' : '#334155')};
  font-size: 0.875rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;

  &:hover:not(:disabled) {
    background: ${(p) =>
      p.$variant === 'primary' ? '#6d28d9' : p.$variant === 'danger' ? '#fee2e2' : '#f8fafc'};
  }

  &:focus-visible {
    outline: 2px solid #a78bfa;
    outline-offset: 2px;
  }

  &:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }
`;

const MetaTable = styled.table`
  width: 100%;
  font-size: 0.8125rem;
  border-collapse: collapse;

  td {
    padding: 0.375rem 0;
    vertical-align: top;
    line-height: 1.55;
  }

  td:first-child {
    width: 4.5rem;
    color: #64748b;
    white-space: nowrap;
  }

  td:last-child {
    color: #334155;
    word-break: break-word;
  }
`;

const SectionTitle = styled.h3`
  display: flex;
  align-items: center;
  gap: 0.375rem;
  margin: 0 0 0.75rem;
  font-size: 0.875rem;
  font-weight: 600;
  color: #0f172a;
`;

const ImageList = styled.div`
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
`;

const ImageListItem = styled.button<{ $active: boolean }>`
  width: 100%;
  min-height: 2.25rem;
  padding: 0.375rem 0.5rem;
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.8125rem;
  text-align: left;
  color: ${(p) => (p.$active ? '#7c3aed' : '#64748b')};
  background: ${(p) => (p.$active ? '#ede9fe' : 'transparent')};
  border: 1px solid ${(p) => (p.$active ? '#c4b5fd' : 'transparent')};
  transition: all 0.15s;

  &:hover {
    border-color: #c4b5fd;
    color: #7c3aed;
  }

  &:focus-visible {
    outline: 2px solid #a78bfa;
    outline-offset: 2px;
  }
`;

const ErrorText = styled.div`
  color: #dc2626;
  font-size: 0.875rem;
  text-align: center;
`;

/* ---------- helpers ---------- */

const statusConfig: Record<string, { label: string; color: string }> = {
  pending: { label: '待审核', color: 'default' },
  reviewing: { label: '审核中', color: 'processing' },
  skipped: { label: '已跳过', color: 'warning' },
  transferred: { label: '已转工单', color: 'blue' },
  cleared: { label: '已消除', color: 'success' },
};

const kpiCards = [
  { key: 'total_pending', label: '待审核', icon: <ExclamationCircleOutlined />, accent: '#f59e0b', bg: '#fef3c7', color: '#d97706' },
  { key: 'reviewing_count', label: '审核中', icon: <EyeOutlined />, accent: '#7c3aed', bg: '#ede9fe', color: '#7c3aed' },
  { key: 'done_today', label: '今日完成', icon: <CheckCircleOutlined />, accent: '#10b981', bg: '#d1fae5', color: '#059669' },
  { key: 'current_batch', label: '当前批次', icon: <ThunderboltOutlined />, accent: '#3b82f6', bg: '#dbeafe', color: '#2563eb' },
];

const actionLabel: Record<'skip' | 'transfer' | 'clear', string> = {
  skip: '跳过',
  transfer: '转工单',
  clear: '消除告警',
};

function formatAlarmValue(value: unknown) {
  if (value == null || value === '') return '—';
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

/* ---------- component ---------- */

const EventReviewPage = () => {
  // list state
  const [list, setList] = useState<EventReviewItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(10);
  const [filterStatus, setFilterStatus] = useState<string | undefined>();
  const [searchText, setSearchText] = useState('');
  const [keyword, setKeyword] = useState('');
  const [listLoading, setListLoading] = useState(false);

  // review state
  const [reviewing, setReviewing] = useState(false);
  const [currentEvent, setCurrentEvent] = useState<EventReviewDetail | null>(null);
  const [currentImageIdx, setCurrentImageIdx] = useState(0);
  const [reviewQueue, setReviewQueue] = useState<EventReviewItem[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [progress, setProgress] = useState<EventReviewProgress | null>(null);
  const [imageObjectUrl, setImageObjectUrl] = useState<string | null>(null);
  const [imageLoading, setImageLoading] = useState(false);
  const [imageError, setImageError] = useState('');

  const fetchList = useCallback(async () => {
    setListLoading(true);
    try {
      const res = await getEventReviewList({ page, size, status: filterStatus, q: keyword || undefined });
      setList(res.data);
      setTotal(res.meta_data.total);
    } catch {
      message.error('加载事件列表失败');
    } finally {
      setListLoading(false);
    }
  }, [page, size, filterStatus, keyword]);

  const fetchProgress = useCallback(async () => {
    try {
      const res = await getEventReviewProgress();
      setProgress(res.data);
    } catch {
      // ignore progress failures; list errors are surfaced separately
    }
  }, []);

  const openEventDetail = useCallback(async (event: EventReviewItem, queue: EventReviewItem[] = []) => {
    setDetailLoading(true);
    try {
      const res = await getEventReviewDetail(event.id);
      if (!res.data) {
        message.warning('没有找到该告警详情');
        return false;
      }
      setCurrentEvent(res.data);
      setCurrentImageIdx(0);
      setReviewQueue(queue);
      setReviewing(true);
      return true;
    } catch {
      message.error('加载事件详情失败');
      return false;
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchList();
    fetchProgress();
  }, [fetchList, fetchProgress]);

  const currentImages = currentEvent?.images || [];
  const currentImg = currentImages[currentImageIdx];

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;

    setImageObjectUrl(null);
    setImageError('');

    if (!currentImg?.url) {
      setImageLoading(false);
      return undefined;
    }

    setImageLoading(true);
    getEventReviewImageBlob(currentImg.url)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setImageObjectUrl(objectUrl);
      })
      .catch(() => {
        if (!cancelled) setImageError('图片加载失败，请刷新或检查后海平台图片是否仍可访问');
      })
      .finally(() => {
        if (!cancelled) setImageLoading(false);
      });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [currentImg?.url]);

  const applyFilters = () => {
    setPage(0);
    setKeyword(searchText.trim());
  };

  const handleClaim = async () => {
    setDetailLoading(true);
    try {
      const res = await claimEvents(10);
      if (res.data.length > 0) {
        message.success(`已取到 ${res.data.length} 条待审核事件`);
        const [first, ...rest] = res.data;
        await openEventDetail(first, rest);
        fetchList();
        fetchProgress();
      } else {
        message.info('暂无可审核事件');
      }
    } catch {
      message.error('开始审核失败');
    } finally {
      setDetailLoading(false);
    }
  };

  const handleEnterReview = async (event: EventReviewItem) => {
    const rowIndex = list.findIndex((item) => item.id === event.id);
    const queue = rowIndex >= 0 ? list.slice(rowIndex + 1) : [];
    await openEventDetail(event, queue);
  };

  const finishCurrentAndAdvance = async (action: 'skip' | 'transfer' | 'clear') => {
    if (!currentEvent) return;
    setActionLoading(true);
    try {
      await performEventAction(currentEvent.id, action, currentEvent.event_type || undefined);
      message.success(`已${actionLabel[action]}`);

      const [next, ...rest] = reviewQueue;
      fetchList();
      fetchProgress();

      if (next) {
        await openEventDetail(next, rest);
      } else {
        setReviewing(false);
        setCurrentEvent(null);
        setCurrentImageIdx(0);
      }
    } catch {
      message.error(`${actionLabel[action]}失败`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleAction = (action: 'skip' | 'transfer' | 'clear') => {
    if (!currentEvent || actionLoading) return;

    if (action === 'skip') {
      finishCurrentAndAdvance(action);
      return;
    }

    Modal.confirm({
      title: `确认${actionLabel[action]}？`,
      content: `此操作会同步调用后海安防老平台，告警 ID：${currentEvent.alarm_id || currentEvent.id}`,
      okText: `确认${actionLabel[action]}`,
      cancelText: '取消',
      okButtonProps: { danger: action === 'clear' },
      onOk: () => finishCurrentAndAdvance(action),
    });
  };

  const handleBack = () => {
    setReviewing(false);
    setCurrentEvent(null);
    setCurrentImageIdx(0);
    setReviewQueue([]);
  };

  /* ---------- table columns ---------- */

  const columns: ColumnsType<EventReviewItem> = useMemo(
    () => [
      {
        title: '告警 ID',
        dataIndex: 'alarm_id',
        key: 'alarm_id',
        width: 120,
        render: (v: string) => <span style={{ fontFamily: 'monospace', fontSize: '0.8125rem' }}>{v}</span>,
      },
      {
        title: '摄像头',
        dataIndex: 'camera_name',
        key: 'camera_name',
        width: 160,
        render: (v: string) => (
          <span>
            <CameraOutlined style={{ marginRight: 6, color: '#94a3b8' }} />
            {v || '—'}
          </span>
        ),
      },
      {
        title: '事件类型',
        dataIndex: 'event_type',
        key: 'event_type',
        width: 120,
        render: (v: string) => (
          <Tag color="purple" style={{ margin: 0 }}>
            {v || '—'}
          </Tag>
        ),
      },
      {
        title: '时间',
        dataIndex: 'event_time',
        key: 'event_time',
        width: 170,
        render: (v: string) => (
          <span style={{ color: '#64748b', fontSize: '0.8125rem' }}>
            <ClockCircleOutlined style={{ marginRight: 4 }} />
            {v || '—'}
          </span>
        ),
      },
      {
        title: '状态',
        dataIndex: 'status',
        key: 'status',
        width: 100,
        render: (status: string) => {
          const cfg = statusConfig[status] || { label: status, color: 'default' };
          return <Tag color={cfg.color}>{cfg.label}</Tag>;
        },
      },
      {
        title: '审核人',
        dataIndex: 'reviewer',
        key: 'reviewer',
        width: 100,
        render: (v: string | null) => v || '—',
      },
      {
        title: '操作',
        key: 'actions',
        width: 110,
        render: (_: unknown, record: EventReviewItem) => (
          <Button
            type="link"
            size="small"
            icon={<EyeOutlined />}
            loading={detailLoading && currentEvent?.id !== record.id}
            onClick={(e) => {
              e.stopPropagation();
              handleEnterReview(record);
            }}
          >
            审核
          </Button>
        ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [detailLoading, currentEvent?.id, list],
  );

  /* ---------- review mode ---------- */

  if (reviewing && currentEvent) {
    return (
      <Page>
        <PageInner>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginBottom: '1rem',
              gap: '1rem',
            }}
          >
            <Button onClick={handleBack} style={{ borderColor: '#e2e8f0' }}>
              返回列表
            </Button>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', fontSize: '0.875rem', color: '#64748b' }}>
              <span>
                告警 ID: <strong style={{ color: '#0f172a' }}>{currentEvent.alarm_id}</strong>
              </span>
              <span>|</span>
              <span>队列剩余: {reviewQueue.length}</span>
              <span>|</span>
              <Tag color={statusConfig[currentEvent.status]?.color}>
                {statusConfig[currentEvent.status]?.label || currentEvent.status}
              </Tag>
            </div>
          </div>

          <ReviewLayout>
            <SidePanel>
              <Panel>
                <SectionTitle>
                  <FileTextOutlined />
                  告警信息
                </SectionTitle>
                <MetaTable>
                  <tbody>
                    <tr>
                      <td>告警 ID</td>
                      <td style={{ fontFamily: 'monospace' }}>{currentEvent.alarm_id || '—'}</td>
                    </tr>
                    <tr>
                      <td>摄像头</td>
                      <td>{currentEvent.camera_name || '—'}</td>
                    </tr>
                    <tr>
                      <td>位置</td>
                      <td>{currentEvent.camera_location || '—'}</td>
                    </tr>
                    <tr>
                      <td>事件类型</td>
                      <td>
                        <Tag color="purple">{currentEvent.event_type || '—'}</Tag>
                      </td>
                    </tr>
                    <tr>
                      <td>时间</td>
                      <td>{currentEvent.event_time || '—'}</td>
                    </tr>
                    {currentEvent.alarm_info &&
                      Object.entries(currentEvent.alarm_info)
                        .filter(([k]) => !['alarmId', 'cameraName', 'cameraLocation', 'alarmType', 'pieces'].includes(k))
                        .slice(0, 6)
                        .map(([k, v]) => (
                          <tr key={k}>
                            <td>{k}</td>
                            <td>{formatAlarmValue(v)}</td>
                          </tr>
                        ))}
                  </tbody>
                </MetaTable>
              </Panel>

              <Panel>
                <SectionTitle>
                  <PictureOutlined />
                  事件图片
                </SectionTitle>
                {currentImages.length > 0 ? (
                  <ImageList>
                    {currentImages.map((img, idx) => (
                      <ImageListItem
                        key={img.id}
                        type="button"
                        $active={idx === currentImageIdx}
                        onClick={() => setCurrentImageIdx(idx)}
                      >
                        {img.filename || `图片 ${idx + 1}`}
                      </ImageListItem>
                    ))}
                  </ImageList>
                ) : (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无图片" />
                )}
              </Panel>
            </SidePanel>

            <ImagePanel>
              <ImageArea>
                {imageLoading ? (
                  <Spin tip="图片加载中" />
                ) : imageError ? (
                  <ErrorText>{imageError}</ErrorText>
                ) : imageObjectUrl ? (
                  <ImageImg src={imageObjectUrl} alt={currentImg?.filename || `事件图片 ${currentImageIdx + 1}`} />
                ) : (
                  <Empty description="暂无图片" />
                )}
              </ImageArea>
              {currentImages.length > 0 && (
                <ImageNav>
                  <ImageNavBtn
                    type="button"
                    disabled={currentImageIdx === 0 || imageLoading}
                    onClick={() => setCurrentImageIdx((i) => Math.max(0, i - 1))}
                  >
                    上一张
                  </ImageNavBtn>
                  <span>
                    {currentImageIdx + 1} / {currentImages.length}
                  </span>
                  <ImageNavBtn
                    type="button"
                    disabled={currentImageIdx >= currentImages.length - 1 || imageLoading}
                    onClick={() => setCurrentImageIdx((i) => Math.min(currentImages.length - 1, i + 1))}
                  >
                    下一张
                  </ImageNavBtn>
                </ImageNav>
              )}
            </ImagePanel>

            <SidePanel>
              <Panel>
                <SectionTitle>
                  <ThunderboltOutlined />
                  审核操作
                </SectionTitle>
                <div style={{ fontSize: '0.8125rem', color: '#64748b', marginBottom: '0.75rem' }}>
                  转工单、消除告警会同步调用后海安防老平台；跳过仅切换到队列下一条。
                </div>
                <ActionBar>
                  <ActionBtn $variant="default" disabled={actionLoading || detailLoading} onClick={() => handleAction('skip')}>
                    <ForwardOutlined />
                    跳过
                  </ActionBtn>
                  <ActionBtn $variant="primary" disabled={actionLoading || detailLoading} onClick={() => handleAction('transfer')}>
                    <AlertOutlined />
                    转工单
                  </ActionBtn>
                  <ActionBtn $variant="danger" disabled={actionLoading || detailLoading} onClick={() => handleAction('clear')}>
                    <StopOutlined />
                    消除告警
                  </ActionBtn>
                </ActionBar>
              </Panel>

              <Panel>
                <SectionTitle>
                  <CheckCircleOutlined />
                  审核进度
                </SectionTitle>
                <div style={{ fontSize: '0.8125rem', color: '#64748b' }}>
                  {progress ? (
                    <>
                      <div style={{ marginBottom: '0.375rem' }}>
                        当前批次: {Math.max(progress.batch_total - reviewQueue.length, 0)} / {progress.batch_total}
                      </div>
                      <div>待审核: {progress.total_pending}</div>
                    </>
                  ) : (
                    '暂无进度数据'
                  )}
                </div>
              </Panel>
            </SidePanel>
          </ReviewLayout>
        </PageInner>
      </Page>
    );
  }

  /* ---------- list mode ---------- */

  return (
    <Page>
      <PageInner>
        <PageHeader>
          <HeaderText>
            <PageTitle>事件审核</PageTitle>
            <PageDesc>审核后海安防平台告警事件。点击「开始审核」进入连续审核流程，图片通过后端鉴权代理加载。</PageDesc>
          </HeaderText>
        </PageHeader>

        {progress && (
          <KpiGrid>
            {kpiCards.map((kpi) => (
              <KpiCard key={kpi.key} $accent={kpi.accent}>
                <KpiIcon $bg={kpi.bg} $color={kpi.color}>
                  {kpi.icon}
                </KpiIcon>
                <KpiBody>
                  <KpiLabel>{kpi.label}</KpiLabel>
                  <KpiValue>{(progress as any)[kpi.key] ?? 0}</KpiValue>
                </KpiBody>
              </KpiCard>
            ))}
          </KpiGrid>
        )}

        <Toolbar>
          <Input
            placeholder="搜索告警名称 / 摄像头 / 告警 ID"
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            onPressEnter={applyFilters}
            prefix={<SearchOutlined style={{ color: '#94a3b8' }} />}
            style={{ width: 280 }}
            allowClear
          />
          <Select
            value={filterStatus}
            onChange={(v) => {
              setFilterStatus(v);
              setPage(0);
            }}
            placeholder="状态筛选"
            allowClear
            style={{ width: 140 }}
            options={[{ value: 'pending', label: '待审核' }]}
          />
          <Button icon={<SearchOutlined />} onClick={applyFilters}>
            查询
          </Button>
          <div style={{ flex: 1 }} />
          <Tooltip title="取当前列表前 10 条并进入连续审核">
            <Button
              type="primary"
              icon={<ThunderboltOutlined />}
              loading={detailLoading}
              onClick={handleClaim}
              style={{ background: '#7c3aed', borderColor: '#7c3aed' }}
            >
              开始审核
            </Button>
          </Tooltip>
          <Button icon={<ReloadOutlined />} onClick={() => { fetchList(); fetchProgress(); }}>
            刷新
          </Button>
        </Toolbar>

        <Panel>
          <Table
            columns={columns}
            dataSource={list}
            rowKey="id"
            loading={listLoading}
            pagination={false}
            size="middle"
            locale={{ emptyText: <Empty description="暂无告警事件" /> }}
            onRow={(record) => ({
              onClick: () => handleEnterReview(record),
              style: { cursor: 'pointer' },
            })}
          />
          <div
            style={{
              display: 'flex',
              justifyContent: 'flex-end',
              paddingTop: '0.75rem',
              borderTop: '1px solid #f1f5f9',
              marginTop: '0.75rem',
            }}
          >
            <Pagination
              current={page + 1}
              pageSize={size}
              total={total}
              onChange={(p, s) => {
                setPage(p - 1);
                setSize(s);
              }}
              showSizeChanger
              showTotal={(t) => `共 ${t} 条`}
            />
          </div>
        </Panel>
      </PageInner>
    </Page>
  );
};

export default EventReviewPage;
