import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  CalendarOutlined,
  ClockCircleOutlined,
  FileImageOutlined,
  ReloadOutlined,
  TeamOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { Button, Input, Pagination, Select, Spin, Table, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import styled from 'styled-components';

import {
  getAnnotationStatsDashboard,
  getTaskList,
  type AnnotationDailyUserRow,
  type AnnotationSampleTimingRow,
  type AnnotationStatsDashboard,
} from '@/api/services/task';

function formatDateISO(d: Date) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function addDays(d: Date, days: number) {
  const next = new Date(d);
  next.setDate(next.getDate() + days);
  return next;
}

function defaultDateFrom() {
  return formatDateISO(new Date());
}

function defaultDateTo() {
  return formatDateISO(new Date());
}

function formatDateTime(iso: string) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  const h = String(d.getHours()).padStart(2, '0');
  const min = String(d.getMinutes()).padStart(2, '0');
  return `${y}-${m}-${day} ${h}:${min}`;
}

const Page = styled.div`
  flex: 1;
  min-height: 0;
  overflow: auto;
  background: #f5f7fb;
`;

const PageInner = styled.div`
  max-width: 1280px;
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
  max-width: 36rem;
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
`;

const DateRange = styled.div`
  display: flex;
  align-items: center;
  gap: 0.5rem;
`;

const DateSep = styled.span`
  font-size: 0.8125rem;
  color: #94a3b8;
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

const ContentGrid = styled.div`
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 1rem;
  margin-bottom: 1rem;
  align-items: start;

  @media (max-width: 960px) {
    grid-template-columns: 1fr;
  }
`;

const Panel = styled.section`
  background: #fff;
  border: 1px solid #e8edf4;
  border-radius: 10px;
  padding: 1rem 1.125rem 1.125rem;
  box-shadow: 0 4px 16px rgb(15 23 42 / 4%);
`;

const PanelHead = styled.div`
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 1rem;
  padding-bottom: 0.75rem;
  border-bottom: 1px solid #f1f5f9;
`;

const PanelTitle = styled.h2`
  margin: 0;
  font-size: 0.9375rem;
  font-weight: 600;
  color: #0f172a;
`;

const PanelHint = styled.span`
  font-size: 0.75rem;
  color: #94a3b8;
  white-space: nowrap;
`;

const ChartList = styled.div`
  display: flex;
  flex-direction: column;
  gap: 1rem;
  max-height: 28rem;
  overflow-y: auto;
  padding-right: 0.25rem;
`;

const DayBlock = styled.div`
  padding: 0.75rem;
  background: #f8fafc;
  border-radius: 8px;
  border: 1px solid #f1f5f9;
`;

const DayLabel = styled.div`
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.625rem;
  font-size: 0.8125rem;
  font-weight: 600;
  color: #334155;
`;

const DayTotal = styled.span`
  font-size: 0.75rem;
  font-weight: 500;
  color: #64748b;
`;

const BarRow = styled.div`
  display: grid;
  grid-template-columns: 5.5rem 1fr 2.25rem;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.75rem;
  margin-bottom: 0.375rem;

  &:last-child {
    margin-bottom: 0;
  }
`;

const BarName = styled.span`
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #475569;
`;

const BarTrack = styled.div`
  height: 0.5rem;
  background: #e2e8f0;
  border-radius: 999px;
  overflow: hidden;
`;

const BarFill = styled.div<{ $width: number; $color: string }>`
  height: 100%;
  width: ${(p) => p.$width}%;
  background: ${(p) => p.$color};
  border-radius: 999px;
  transition: width 0.35s ease;
`;

const BarCount = styled.span`
  text-align: right;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  color: #334155;
`;

const EmptyHint = styled.div`
  padding: 2rem 1rem;
  text-align: center;
  font-size: 0.8125rem;
  color: #94a3b8;
`;

const FullWidthPanel = styled(Panel)`
  margin-bottom: 0;
`;

const StyledTable = styled(Table)`
  .ant-table-thead > tr > th {
    background: #f8fafc !important;
    font-size: 0.8125rem;
    color: #64748b;
    font-weight: 600;
  }

  .ant-table-tbody > tr > td {
    font-size: 0.8125rem;
  }
` as typeof Table;

function formatDuration(sec?: number | null) {
  if (sec == null || Number.isNaN(sec)) return '—';
  const s = Math.round(sec);
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m < 60) return r ? `${m} 分 ${r} 秒` : `${m} 分`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm ? `${h} 时 ${rm} 分` : `${h} 时`;
}

const USER_COLORS = ['#2f6fed', '#0d9488', '#d97706', '#db2777', '#7c3aed', '#16a34a', '#ea580c', '#0891b2'];

function colorForUser(userId: number) {
  return USER_COLORS[Math.abs(userId) % USER_COLORS.length];
}

const KPI_CONFIG = [
  {
    key: 'total',
    label: '统计期内真实修改图片数',
    icon: FileImageOutlined,
    accent: '#2f6fed',
    iconBg: '#eef5ff',
    iconColor: '#2f6fed',
    getValue: (d: AnnotationStatsDashboard | null) => d?.summary.total_done ?? 0,
    format: (v: number) => String(v),
  },
  {
    key: 'today',
    label: '今日真实修改',
    icon: ThunderboltOutlined,
    accent: '#0d9488',
    iconBg: '#ecfdf5',
    iconColor: '#0d9488',
    getValue: (d: AnnotationStatsDashboard | null) => d?.summary.today_done ?? 0,
    format: (v: number) => String(v),
  },
  {
    key: 'annotators',
    label: '参与修改人数',
    icon: TeamOutlined,
    accent: '#7c3aed',
    iconBg: '#f5f3ff',
    iconColor: '#7c3aed',
    getValue: (d: AnnotationStatsDashboard | null) => d?.summary.total_annotators ?? 0,
    format: (v: number) => String(v),
  },
  {
    key: 'avg',
    label: '平均单张处理时长',
    icon: ClockCircleOutlined,
    accent: '#d97706',
    iconBg: '#fffbeb',
    iconColor: '#d97706',
    getValue: (d: AnnotationStatsDashboard | null) => d?.summary.avg_duration_seconds,
    format: (v: number | null | undefined) => formatDuration(v),
  },
] as const;

export default function AnnotationStatsPanel() {
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<AnnotationStatsDashboard | null>(null);
  const [dateFrom, setDateFrom] = useState(defaultDateFrom);
  const [dateTo, setDateTo] = useState(defaultDateTo);
  const [taskId, setTaskId] = useState<number | undefined>(undefined);
  const [taskOptions, setTaskOptions] = useState<{ label: string; value: number }[]>([]);
  const [samplePage, setSamplePage] = useState(1);
  const sampleSize = 50;

  const loadTasks = useCallback(async () => {
    const res = await getTaskList({ page: 1, size: 500 });
    const rows = res.data?.data || [];
    setTaskOptions(rows.map((t) => ({ label: `${t.name || '任务'} #${t.id}`, value: t.id! })));
  }, []);

  const loadStats = useCallback(async () => {
    setLoading(true);
    try {
      const res = await getAnnotationStatsDashboard({
        date_from: dateFrom,
        date_to: dateTo,
        task_id: taskId,
        sample_page: samplePage - 1,
        sample_size: sampleSize,
      });
      setData(res.data);
    } finally {
      setLoading(false);
    }
  }, [dateFrom, dateTo, taskId, samplePage]);

  useEffect(() => {
    loadTasks();
  }, [loadTasks]);

  useEffect(() => {
    loadStats();
  }, [loadStats]);

  const sortedDailyRows = useMemo(() => {
    const rows = data?.daily_by_user || [];
    const byDate = new Map<string, AnnotationDailyUserRow[]>();
    rows.forEach((r) => {
      if (!byDate.has(r.stat_date)) byDate.set(r.stat_date, []);
      byDate.get(r.stat_date)!.push(r);
    });
    const out: AnnotationDailyUserRow[] = [];
    Array.from(byDate.entries())
      .sort((a, b) => b[0].localeCompare(a[0]))
      .forEach(([, items]) => {
        items.sort((a, b) => b.done_count - a.done_count);
        out.push(...items);
      });
    return out;
  }, [data?.daily_by_user]);

  const dailyChartRows = useMemo(() => {
    const byDate = new Map<string, AnnotationDailyUserRow[]>();
    sortedDailyRows.forEach((r) => {
      if (!byDate.has(r.stat_date)) byDate.set(r.stat_date, []);
      byDate.get(r.stat_date)!.push(r);
    });
    return Array.from(byDate.entries())
      .slice(0, 14)
      .map(([statDate, items]) => ({
        statDate,
        items,
        total: items.reduce((s, x) => s + x.done_count, 0),
      }));
  }, [sortedDailyRows]);

  const maxDailyTotal = useMemo(
    () => Math.max(1, ...dailyChartRows.map((r) => r.total)),
    [dailyChartRows],
  );

  const dailyColumns: ColumnsType<AnnotationDailyUserRow> = [
    { title: '日期', dataIndex: 'stat_date', width: 108 },
    {
      title: '修改人',
      dataIndex: 'username',
      width: 130,
      render: (v: string, r) => {
        const idx = sortedDailyRows.findIndex(
          (x) => x.stat_date === r.stat_date && x.user_id === r.user_id,
        );
        const prev = idx > 0 ? sortedDailyRows[idx - 1] : null;
        const isTopOfDay = !prev || prev.stat_date !== r.stat_date;
        return isTopOfDay ? (
          <span>
            <strong>{v}</strong>
            <span
              style={{
                marginLeft: 6,
                fontSize: 11,
                color: '#2f6fed',
                background: '#eef5ff',
                padding: '1px 6px',
                borderRadius: 4,
              }}
            >
              当日最多
            </span>
          </span>
        ) : (
          v
        );
      },
    },
    {
      title: '真实修改图片张数',
      dataIndex: 'done_count',
      width: 96,
      align: 'right',
      render: (v: number) => <span style={{ fontWeight: 600 }}>{v}</span>,
    },
  ];

  const timingColumns: ColumnsType<AnnotationSampleTimingRow> = [
    { title: '任务', dataIndex: 'task_name', ellipsis: true, render: (v, r) => v || `#${r.task_id}` },
    { title: '图片', dataIndex: 'filename', ellipsis: true },
    { title: '修改人', dataIndex: 'username', width: 108 },
    {
      title: '标注框数',
      dataIndex: 'annotated_count',
      width: 88,
      align: 'center',
    },
    {
      title: '用时',
      dataIndex: 'duration_seconds',
      width: 100,
      render: (v: number) => (
        <Tooltip title={data?.duration_note}>
          <span>{formatDuration(v)}</span>
        </Tooltip>
      ),
    },
    {
      title: '完成时间',
      dataIndex: 'annotated_at',
      width: 158,
      render: (v: string) => (v ? formatDateTime(v) : '—'),
    },
  ];

  return (
    <Page>
      <PageInner>
        <PageHeader>
          <HeaderText>
            <PageTitle>标注统计</PageTitle>
            <PageDesc>
              汇总可见任务内每位修改人每日真实修改图片张数与单张图片真实修改记录；已排除 admin@123.com；每日按真实修改张数从高到低排序。
            </PageDesc>
          </HeaderText>
          <Toolbar>
            <DateRange>
              <CalendarOutlined style={{ color: '#94a3b8' }} />
              <Input
                type="date"
                value={dateFrom}
                onChange={(e) => {
                  setDateFrom(e.target.value);
                  setSamplePage(1);
                }}
                style={{ width: 148 }}
              />
              <DateSep>至</DateSep>
              <Input
                type="date"
                value={dateTo}
                onChange={(e) => {
                  setDateTo(e.target.value);
                  setSamplePage(1);
                }}
                style={{ width: 148 }}
              />
            </DateRange>
            <Select
              allowClear
              placeholder="全部任务"
              style={{ minWidth: 200 }}
              options={taskOptions}
              value={taskId}
              onChange={(v) => {
                setTaskId(v);
                setSamplePage(1);
              }}
            />
            <Button
              icon={<ReloadOutlined />}
              onClick={() => loadStats()}
              loading={loading}
            >
              刷新
            </Button>
          </Toolbar>
        </PageHeader>

        <Spin spinning={loading}>
          <KpiGrid>
            {KPI_CONFIG.map((kpi) => {
              const Icon = kpi.icon;
              const raw = kpi.getValue(data);
              const display =
                kpi.key === 'avg'
                  ? kpi.format(raw as number | null | undefined)
                  : kpi.format(raw as number);
              return (
                <KpiCard key={kpi.key} $accent={kpi.accent}>
                  <KpiIcon $bg={kpi.iconBg} $color={kpi.iconColor}>
                    <Icon />
                  </KpiIcon>
                  <KpiBody>
                    <KpiLabel>{kpi.label}</KpiLabel>
                    <KpiValue>{display}</KpiValue>
                  </KpiBody>
                </KpiCard>
              );
            })}
          </KpiGrid>

          <ContentGrid>
            <Panel>
              <PanelHead>
                <PanelTitle>每日真实修改趋势</PanelTitle>
                <PanelHint>近 14 天 · 按修改人分组</PanelHint>
              </PanelHead>
              <ChartList>
                {dailyChartRows.length === 0 ? (
                  <EmptyHint>所选范围内暂无真实修改记录</EmptyHint>
                ) : (
                  dailyChartRows.map((row) => (
                    <DayBlock key={row.statDate}>
                      <DayLabel>
                        <span>{row.statDate}</span>
                        <DayTotal>合计 {row.total} 张图</DayTotal>
                      </DayLabel>
                      {row.items.map((item) => (
                        <BarRow key={`${row.statDate}-${item.user_id}`}>
                          <BarName title={item.username}>{item.username}</BarName>
                          <BarTrack>
                            <BarFill
                              $width={(item.done_count / maxDailyTotal) * 100}
                              $color={colorForUser(item.user_id)}
                            />
                          </BarTrack>
                          <BarCount>{item.done_count}</BarCount>
                        </BarRow>
                      ))}
                    </DayBlock>
                  ))
                )}
              </ChartList>
            </Panel>

            <Panel>
              <PanelHead>
                <PanelTitle>每人每日真实修改明细</PanelTitle>
                <PanelHint>共 {sortedDailyRows.length} 条记录</PanelHint>
              </PanelHead>
              <StyledTable<AnnotationDailyUserRow>
                size="small"
                rowKey={(r) => `${r.stat_date}-${r.user_id}`}
                columns={dailyColumns}
                dataSource={sortedDailyRows}
                pagination={{ pageSize: 8, showSizeChanger: false, size: 'small' }}
                scroll={{ y: 360 }}
              />
            </Panel>
          </ContentGrid>

          <FullWidthPanel>
            <PanelHead>
              <PanelTitle>单张图片真实修改记录</PanelTitle>
              <PanelHint>
                共 {data?.sample_timings_total ?? 0} 条记录
                {data?.duration_note ? ` · ${data.duration_note}` : ''}
              </PanelHint>
            </PanelHead>
            <StyledTable<AnnotationSampleTimingRow>
              size="small"
              rowKey="sample_id"
              columns={timingColumns}
              dataSource={data?.sample_timings || []}
              pagination={false}
            />
            {(data?.sample_timings_total ?? 0) > 0 && (
              <div style={{ marginTop: 12, display: 'flex', justifyContent: 'flex-end' }}>
                <Pagination
                  current={samplePage}
                  pageSize={sampleSize}
                  total={data?.sample_timings_total ?? 0}
                  showSizeChanger={false}
                  size="small"
                  onChange={(p) => setSamplePage(p)}
                />
              </div>
            )}
          </FullWidthPanel>
        </Spin>
      </PageInner>
    </Page>
  );
}
