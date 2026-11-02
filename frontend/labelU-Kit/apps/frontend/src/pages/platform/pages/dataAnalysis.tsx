import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Empty, InputNumber, Space, Statistic, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import {
  getDataAnalysisOverview,
  type DataAnalysisGap,
  type DataAnalysisGlobalTool,
  type DataAnalysisLabel,
  type DataAnalysisState,
} from '@/api/services/dataAnalysis';
import { ModuleHeader, QueueHint, QueueToolbar, TablePanel } from '../shared';

const STATE_LABELS: Record<string, string> = {
  DONE: '已完成',
  NEW: '未标注',
  INCOMPLETE: '未完成',
  SKIPPED: '已跳过',
  ABNORMAL: '异常',
};
const STATE_COLORS = ['#1677ff', '#94a3b8', '#f59e0b', '#a855f7', '#ef4444'];

function BarList({
  items,
  emptyText,
}: {
  items: Array<{ label: string; value: number; color?: string }>;
  emptyText: string;
}) {
  const max = Math.max(1, ...items.map((item) => item.value));
  if (!items.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={emptyText} />;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {items.map((item, index) => (
        <div key={`${item.label}-${index}`}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, marginBottom: 4 }}>
            <Typography.Text ellipsis style={{ maxWidth: '72%' }}>{item.label}</Typography.Text>
            <Typography.Text type="secondary">{item.value}</Typography.Text>
          </div>
          <div style={{ height: 8, borderRadius: 999, background: '#eef2f7', overflow: 'hidden' }}>
            <div
              style={{
                width: `${Math.max(2, (item.value / max) * 100)}%`,
                height: '100%',
                borderRadius: 999,
                background: item.color || '#1677ff',
              }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

function Donut({ items }: { items: DataAnalysisState[] }) {
  const total = items.reduce((sum, item) => sum + item.count, 0);
  if (!total) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无图片状态数据" />;
  let cursor = 0;
  const segments = items.map((item, index) => {
    const share = item.count / total;
    const start = cursor;
    cursor += share;
    return {
      ...item,
      color: STATE_COLORS[index % STATE_COLORS.length],
      start,
      end: cursor,
    };
  });
  const gradient = segments
    .map((segment) => `${segment.color} ${segment.start * 100}% ${segment.end * 100}%`)
    .join(', ');
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 22, flexWrap: 'wrap' }}>
      <div
        style={{
          width: 150,
          height: 150,
          borderRadius: '50%',
          background: `conic-gradient(${gradient})`,
          display: 'grid',
          placeItems: 'center',
          flex: '0 0 auto',
        }}
      >
        <div
          style={{
            width: 92,
            height: 92,
            borderRadius: '50%',
            background: '#fff',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>图片总数</Typography.Text>
          <Typography.Text strong style={{ fontSize: 22 }}>{total}</Typography.Text>
        </div>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {segments.map((segment) => (
          <Space key={segment.state} size={8}>
            <span style={{ width: 10, height: 10, borderRadius: 3, background: segment.color }} />
            <Typography.Text>{STATE_LABELS[segment.state] || segment.state}</Typography.Text>
            <Typography.Text type="secondary">{segment.count} 张</Typography.Text>
          </Space>
        ))}
      </div>
    </div>
  );
}

function GlobalToolChart({ tool }: { tool: DataAnalysisGlobalTool }) {
  return (
    <div style={{ marginBottom: 18 }}>
      <Typography.Text strong>{tool.name_cn}</Typography.Text>
      <div style={{ marginTop: 8 }}>
        <BarList
          items={tool.values.map((item) => ({ label: item.name_cn, value: item.count }))}
          emptyText="暂无全局工具数据"
        />
      </div>
    </div>
  );
}

export default function DataAnalysisPage() {
  const [targetPerLabel, setTargetPerLabel] = useState(100);
  const query = useQuery({
    queryKey: ['data-analysis-overview', targetPerLabel],
    queryFn: async () => {
      const response = await getDataAnalysisOverview(targetPerLabel);
      return response.data;
    },
    staleTime: 30_000,
  });

  const labels = useMemo(
    () =>
      (query.data?.label_distribution ?? []).slice(0, 12).map((item: DataAnalysisLabel) => ({
        label: item.label_key || item.label_value,
        value: item.count,
        color: item.color,
      })),
    [query.data?.label_distribution],
  );

  const contracts = useMemo(
    () =>
      (query.data?.contracts ?? [])
        .filter((contract) => contract.labels.length > 0)
        .slice(0, 12),
    [query.data?.contracts],
  );

  const gapColumns: ColumnsType<DataAnalysisGap> = [
    { title: '任务', dataIndex: 'task_name', key: 'task_name', width: 220, ellipsis: true },
    { title: '标签', key: 'label', width: 180, render: (_, row) => row.label_key || row.label_value },
    { title: '当前图片', dataIndex: 'count', key: 'count', width: 100 },
    { title: '目标图片', dataIndex: 'target', key: 'target', width: 100 },
    {
      title: '缺口',
      dataIndex: 'gap',
      key: 'gap',
      width: 100,
      render: (value) => <Tag color={value > 0 ? 'orange' : 'green'}>{value}</Tag>,
    },
  ];

  return (
    <TablePanel>
      <ModuleHeader module="data-analysis" />
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <div>
          <Typography.Title level={2} style={{ margin: 0, fontSize: 20 }}>
            数据覆盖与补数建议
          </Typography.Title>
          <Typography.Text type="secondary">
            按标签契约、标签和全局工具分析图片数据，定位每个契约需要补充的图片。
          </Typography.Text>
        </div>
        <Space>
          <Typography.Text type="secondary">每个标签目标图片数</Typography.Text>
          <InputNumber
            min={1}
            max={100000}
            value={targetPerLabel}
            onChange={(value) => setTargetPerLabel(Number(value || 100))}
            style={{ width: 110 }}
          />
        </Space>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, margin: '18px 0' }}>
        <Statistic title="图片总数" value={query.data?.summary.total_images ?? 0} suffix="张" />
        <Statistic title="已标注图片" value={query.data?.summary.labeled_images ?? 0} suffix="张" />
        <Statistic title="标签契约" value={query.data?.summary.contract_count ?? 0} suffix="个" />
        <Statistic title="涉及标签" value={query.data?.summary.label_count ?? 0} suffix="个" />
        <Statistic title="待补充标签" value={query.data?.summary.gap_count ?? 0} suffix="项" />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(280px, 1fr) minmax(320px, 1.4fr)', gap: 18, marginBottom: 18 }}>
        <section style={{ border: '1px solid #e5e7eb', borderRadius: 10, padding: 16 }}>
          <Typography.Title level={4} style={{ marginTop: 0 }}>图片状态</Typography.Title>
          <Donut items={query.data?.state_counts ?? []} />
        </section>
        <section style={{ border: '1px solid #e5e7eb', borderRadius: 10, padding: 16 }}>
          <Typography.Title level={4} style={{ marginTop: 0 }}>标签图片分布</Typography.Title>
          <BarList items={labels} emptyText="暂无标签图片数据" />
        </section>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(320px, 1fr) minmax(320px, 1fr)', gap: 18, marginBottom: 18 }}>
        <section style={{ border: '1px solid #e5e7eb', borderRadius: 10, padding: 16 }}>
          <Typography.Title level={4} style={{ marginTop: 0 }}>标签契约覆盖</Typography.Title>
          <Space direction="vertical" style={{ width: '100%' }} size={12}>
            {contracts.length ? contracts.map((contract) => (
              <div key={`${contract.contract_id}-${contract.task_id}`}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                  <Typography.Text ellipsis style={{ maxWidth: '72%' }}>{contract.task_name}</Typography.Text>
                  <Typography.Text type="secondary">{Math.round(contract.coverage * 100)}%</Typography.Text>
                </div>
                <div style={{ height: 8, marginTop: 4, borderRadius: 999, background: '#eef2f7', overflow: 'hidden' }}>
                  <div style={{ width: `${Math.max(2, contract.coverage * 100)}%`, height: '100%', background: contract.gap_count ? '#f59e0b' : '#1677ff' }} />
                </div>
              </div>
            )) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无标签契约数据" />}
          </Space>
        </section>
        <section style={{ border: '1px solid #e5e7eb', borderRadius: 10, padding: 16, maxHeight: 420, overflow: 'auto' }}>
          <Typography.Title level={4} style={{ marginTop: 0 }}>全局工具分布</Typography.Title>
          {(query.data?.global_tools ?? []).length
            ? (query.data?.global_tools ?? []).map((tool) => <GlobalToolChart key={tool.field_key} tool={tool} />)
            : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无全局工具数据" />}
        </section>
      </div>

      <QueueToolbar>
        <Space>
          <QueueHint>以下为需要补充数据的标签契约明细，按缺口从大到小排序。</QueueHint>
        </Space>
      </QueueToolbar>
      <Table<DataAnalysisGap>
        rowKey={(row) => `${row.contract_id}-${row.task_id}-${row.label_id}`}
        size="middle"
        loading={query.isLoading || query.isFetching}
        columns={gapColumns}
        dataSource={query.data?.gaps ?? []}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        scroll={{ x: 760 }}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前所有标签均已达到目标图片数" /> }}
      />
    </TablePanel>
  );
}
