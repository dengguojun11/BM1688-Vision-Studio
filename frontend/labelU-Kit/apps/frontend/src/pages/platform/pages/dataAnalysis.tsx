import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Empty, Progress, Select, Space, Statistic, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import {
  getDataAnalysisOverview,
  type DataAnalysisContractHealth,
  type DataAnalysisDimension,
  type DataAnalysisLabel,
  type DataAnalysisRecommendation,
} from '@/api/services/dataAnalysis';
import { ModuleHeader, QueueHint, QueueToolbar, TablePanel } from '../shared';

const RISK_META: Record<string, { label: string; color: string; bg: string }> = {
  SEVERE: { label: '严重不足', color: '#b42318', bg: '#fef3f2' },
  WARNING: { label: '需要补数', color: '#b54708', bg: '#fffaeb' },
  HEALTHY: { label: '基本健康', color: '#027a48', bg: '#ecfdf3' },
};

function percent(value: number) {
  return `${Math.round(Math.max(0, value) * 100)}%`;
}

function cellStyle(coverage: number) {
  if (coverage >= 0.8) return { background: '#ecfdf3', color: '#027a48' };
  if (coverage >= 0.5) return { background: '#fffaeb', color: '#b54708' };
  if (coverage > 0) return { background: '#fef3f2', color: '#b42318' };
  return { background: '#f2f4f7', color: '#98a2b3' };
}

function ContractCard({ contract }: { contract: DataAnalysisContractHealth }) {
  const meta = RISK_META[contract.risk_level] || RISK_META.WARNING;
  return (
    <div
      style={{
        border: `1px solid ${meta.bg === '#fef3f2' ? '#fecdca' : '#e4e7ec'}`,
        borderRadius: 10,
        padding: 14,
        background: '#fff',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
        <Typography.Text strong ellipsis style={{ maxWidth: '72%' }}>
          {contract.contract_label}
        </Typography.Text>
        <Tag style={{ color: meta.color, background: meta.bg, border: 0 }}>
          {meta.label}
        </Tag>
      </div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, margin: '8px 0' }}>
        <Typography.Title level={3} style={{ margin: 0, color: meta.color }}>
          {contract.health_score}
        </Typography.Title>
        <Typography.Text type="secondary">健康度</Typography.Text>
        <Typography.Text type="secondary" style={{ marginLeft: 'auto' }}>
          {contract.image_count} 张
        </Typography.Text>
      </div>
      <Progress
        percent={contract.health_score}
        showInfo={false}
        strokeColor={meta.color}
        trailColor="#f2f4f7"
        size="small"
      />
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6, marginTop: 10 }}>
        <div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>标签覆盖</Typography.Text>
          <div>{percent(contract.label_coverage)}</div>
        </div>
        <div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>组合覆盖</Typography.Text>
          <div>{percent(contract.combo_coverage)}</div>
        </div>
        <div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>缺口项</Typography.Text>
          <div style={{ color: contract.severe_gap_count ? '#b42318' : undefined }}>
            {contract.gap_count}
          </div>
        </div>
      </div>
    </div>
  );
}

function Heatmap({
  dimensions,
  current,
  active,
  onChange,
}: {
  dimensions: DataAnalysisDimension[];
  current?: DataAnalysisDimension;
  active: string;
  onChange: (key: string) => void;
}) {
  return (
    <section style={{ border: '1px solid #e4e7ec', borderRadius: 10, padding: 16, marginBottom: 18 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>标签 × 全局工具覆盖热力图</Typography.Title>
          <Typography.Text type="secondary">
            单元格为实际图片数；目标 = 标签目标 ÷ 该维度取值数量。颜色越红缺口越大。
          </Typography.Text>
        </div>
        <Space wrap>
          {dimensions.map((item) => (
            <Tag.CheckableTag
              key={item.field_key}
              checked={active === item.field_key}
              onChange={() => onChange(item.field_key)}
            >
              {item.name_cn}
            </Tag.CheckableTag>
          ))}
        </Space>
      </div>
      {!current ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无标签 × 全局工具交叉数据" style={{ marginTop: 20 }} />
      ) : (
        <div style={{ overflowX: 'auto', marginTop: 14 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr>
                <th style={{ textAlign: 'left', padding: '8px 10px', minWidth: 150, color: '#667085' }}>标签</th>
                {current.values.map((value) => (
                  <th key={value.value} style={{ padding: '8px 6px', color: '#667085', fontWeight: 500, minWidth: 70 }}>
                    {value.name_cn}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {current.rows.map((row) => (
                <tr key={`${row.contract_id}-${row.label_id}`}>
                  <td style={{ padding: '7px 10px', borderTop: '1px solid #f2f4f7' }}>
                    <Typography.Text style={{ fontSize: 12 }}>{row.contract_label}</Typography.Text>
                    <br />
                    <Typography.Text type="secondary" style={{ fontSize: 11 }}>{row.label_key}</Typography.Text>
                  </td>
                  {current.values.map((value) => {
                    const cell = row.cells[value.value] || { actual: 0, target: 0, coverage: 0, gap: 0 };
                    return (
                      <td key={value.value} style={{ padding: 3, borderTop: '1px solid #f2f4f7' }}>
                        <div
                          title={`${row.label_key} × ${value.name_cn}\n当前 ${cell.actual} / 目标 ${cell.target}\n缺口 ${cell.gap}`}
                          style={{
                            borderRadius: 5,
                            padding: '7px 4px',
                            textAlign: 'center',
                            fontWeight: cell.gap ? 600 : 400,
                            ...cellStyle(cell.coverage),
                          }}
                        >
                          {cell.actual}
                        </div>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function LabelCoverage({ labels }: { labels: DataAnalysisLabel[] }) {
  const rows = labels.slice(0, 12);
  return (
    <section style={{ border: '1px solid #e4e7ec', borderRadius: 10, padding: 16, marginBottom: 18 }}>
      <Typography.Title level={4} style={{ marginTop: 0 }}>标签覆盖：实际 vs 目标</Typography.Title>
      {!rows.length ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无标签覆盖数据" />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 11 }}>
          {rows.map((label) => (
            <div key={`${label.label_id}-${label.label_value}`}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <Typography.Text ellipsis style={{ maxWidth: '58%' }}>{label.label_key}</Typography.Text>
                <Typography.Text type="secondary">
                  {label.count} / {label.target}（{percent(label.coverage)}）
                </Typography.Text>
              </div>
              <div style={{ height: 8, marginTop: 4, borderRadius: 999, background: '#f2f4f7', overflow: 'hidden' }}>
                <div
                  style={{
                    width: `${Math.min(100, label.coverage * 100)}%`,
                    height: '100%',
                    borderRadius: 999,
                    background: label.gap ? '#f79009' : '#12b76a',
                  }}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function GlobalTools({ tools }: { tools: Array<{ field_key: string; name_cn: string; values: Array<{ value: string; name_cn: string; count: number }> }> }) {
  return (
    <section style={{ border: '1px solid #e4e7ec', borderRadius: 10, padding: 16, marginBottom: 18 }}>
      <Typography.Title level={4} style={{ marginTop: 0 }}>全局工具数据分布</Typography.Title>
      {!tools.length ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无全局工具数据" />
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 18 }}>
          {tools.map((tool) => {
            const max = Math.max(1, ...tool.values.map((item) => item.count));
            return (
              <div key={tool.field_key}>
                <Typography.Text strong>{tool.name_cn}</Typography.Text>
                <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 7 }}>
                  {tool.values.map((item) => (
                    <div key={item.value}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
                        <Typography.Text style={{ fontSize: 12 }}>{item.name_cn}</Typography.Text>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>{item.count}</Typography.Text>
                      </div>
                      <div style={{ height: 5, background: '#f2f4f7', borderRadius: 999, marginTop: 2 }}>
                        <div style={{ width: `${Math.max(2, (item.count / max) * 100)}%`, height: '100%', background: '#667085', borderRadius: 999 }} />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

export default function DataAnalysisPage() {
  const [contractId, setContractId] = useState<number | undefined>();
  const query = useQuery({
    queryKey: ['data-analysis-overview', contractId],
    queryFn: async () => {
      const response = await getDataAnalysisOverview(0, contractId);
      return response.data;
    },
    staleTime: 60_000,
  });
  const [activeDimension, setActiveDimension] = useState('');
  const dimensions = query.data?.dimensions ?? [];
  const currentDimension = useMemo(
    () => dimensions.find((item) => item.field_key === activeDimension) || dimensions[0],
    [activeDimension, dimensions],
  );
  const summary = query.data?.summary;
  const risk = RISK_META[summary?.risk_level || 'WARNING'] || RISK_META.WARNING;
  const recommendations = query.data?.recommendations ?? [];
  const topActions = recommendations.slice(0, 3);

  const recommendationColumns: ColumnsType<DataAnalysisRecommendation> = [
    {
      title: '优先级',
      dataIndex: 'priority',
      key: 'priority',
      width: 80,
      render: (value) => <Tag color={value === 'P0' ? 'red' : value === 'P1' ? 'orange' : 'default'}>{value}</Tag>,
    },
    { title: '标签契约', dataIndex: 'contract_label', key: 'contract_label', width: 230, ellipsis: true },
    {
      title: '标签',
      key: 'label',
      width: 170,
      render: (_, row) => row.label_key || row.label_value,
    },
    {
      title: '缺口条件',
      key: 'condition',
      width: 190,
      render: (_, row) => `${row.dimension_name}：${row.value_name}`,
    },
    { title: '当前', dataIndex: 'actual', key: 'actual', width: 70 },
    { title: '目标', dataIndex: 'target', key: 'target', width: 70 },
    {
      title: '缺口',
      dataIndex: 'gap',
      key: 'gap',
      width: 70,
      render: (value) => <Typography.Text type="danger" strong>{value}</Typography.Text>,
    },
    {
      title: '建议补图',
      dataIndex: 'gap',
      key: 'batch',
      width: 100,
      render: (value) => `${value} 张`,
    },
  ];

  return (
    <TablePanel>
      <ModuleHeader module="data-analysis" />
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <div>
          <Typography.Title level={2} style={{ margin: 0, fontSize: 20 }}>
            数据健康与补数分析
          </Typography.Title>
          <Typography.Text type="secondary">
            从契约目标出发，定位标签与场景/光照/角度组合的缺口，直接生成补数优先级。
          </Typography.Text>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <Select
            allowClear
            style={{ width: 340 }}
            placeholder="选择标签契约"
            value={contractId ?? 0}
            onChange={(value) => setContractId(value ? Number(value) : undefined)}
            options={[
              { value: 0, label: '全部标签契约' },
              ...(query.data?.contract_options ?? []).map((option) => ({
                value: Number(option.contract_id),
                label: `${option.contract_label} · 标签契约 r${option.contract_revision ?? '-'}（${option.label_count} 个标签）`,
              })),
            ]}
          />
          <Tag style={{ color: risk.color, background: risk.bg, border: 0, padding: '4px 10px' }}>
            {risk.label} · 目标策略：自动均衡
          </Tag>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, margin: '18px 0' }}>
        <Statistic title="图片总数" value={summary?.total_images ?? 0} suffix="张" />
        <Statistic title="已标注图片" value={summary?.labeled_images ?? 0} suffix="张" />
        <Statistic title="标注覆盖率" value={percent(summary?.labeled_rate ?? 0)} />
        <Statistic title="严重标签缺口" value={summary?.severe_gap_count ?? 0} suffix="项" valueStyle={{ color: '#b42318' }} />
        <Statistic title="组合缺口" value={summary?.combo_gap_count ?? 0} suffix="项" />
        <Statistic title="建议补图" value={summary?.recommended_images ?? 0} suffix="张" />
      </div>

      {topActions.length > 0 && (
        <div style={{ background: '#fffaeb', border: '1px solid #fedf89', borderRadius: 10, padding: 14, marginBottom: 18 }}>
          <Typography.Text strong>最该补的 3 项</Typography.Text>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 10, marginTop: 10 }}>
            {topActions.map((item) => (
              <div key={`${item.contract_label}-${item.label_id}-${item.dimension_key}-${item.value}`} style={{ background: '#fff', borderRadius: 8, padding: 10 }}>
                <Tag color={item.priority === 'P0' ? 'red' : 'orange'}>{item.priority}</Tag>
                <Typography.Text strong>{item.label_key}</Typography.Text>
                <div style={{ marginTop: 4 }}>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {item.dimension_name}：{item.value_name} · 当前 {item.actual} / 目标 {item.target}
                  </Typography.Text>
                </div>
                <div style={{ marginTop: 4 }}>建议补 <Typography.Text type="danger" strong>{item.gap} 张</Typography.Text></div>
              </div>
            ))}
          </div>
        </div>
      )}

      <Typography.Title level={4} style={{ marginTop: 0 }}>契约健康度</Typography.Title>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 12, marginBottom: 18 }}>
        {(query.data?.contract_health ?? []).map((contract) => (
          <ContractCard key={`${contract.contract_id}-${contract.contract_label}`} contract={contract} />
        ))}
        {!query.data?.contract_health?.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无标签契约" />}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(420px, 1.35fr) minmax(320px, 1fr)', gap: 18 }}>
        <Heatmap
          dimensions={dimensions}
          current={currentDimension}
          active={currentDimension?.field_key ?? ''}
          onChange={setActiveDimension}
        />
        <LabelCoverage labels={query.data?.label_distribution ?? []} />
      </div>

      <GlobalTools tools={query.data?.global_tools ?? []} />

      <QueueToolbar>
        <Space>
          <QueueHint>
            按优先级和缺口排序；目标自动取契约图片均值的一半，最低 30 张、最高 200 张。
          </QueueHint>
        </Space>
      </QueueToolbar>
      <Table<DataAnalysisRecommendation>
        rowKey={(row) => `${row.contract_label}-${row.label_id}-${row.dimension_key}-${row.value}`}
        size="middle"
        loading={query.isLoading || query.isFetching}
        columns={recommendationColumns}
        dataSource={recommendations}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        scroll={{ x: 1100 }}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前所有标签和组合均达到目标" /> }}
      />
    </TablePanel>
  );
}
