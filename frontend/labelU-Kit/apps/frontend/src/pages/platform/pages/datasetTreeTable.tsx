import { ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  Empty,
  Input,
  Segmented,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useMemo, useState } from 'react';

import request from '@/api/request';

type WorkbenchMode = 'training' | 'model';

type SummaryJob = {
  job_id: number;
  name?: string;
  task_name?: string;
  status?: string;
  created_at?: string;
  auto_iteration_root_id?: number | null;
  auto_iteration_round_no?: number | null;
  parent_job_id?: number | null;
  lineage_kind?: string;
  quality_passed?: boolean;
  quality_status?: string;
  metrics?: { map50_95?: number | null; map50?: number | null; precision?: number | null; recall?: number | null };
};

type SummaryModel = {
  model_version_id: number;
  display_name?: string;
  status?: string;
  created_at?: string;
  version_no?: number | null;
  branch_version_no?: number | null;
  metrics?: { map50_95?: number | null; map50?: number | null; precision?: number | null; recall?: number | null };
  artifacts?: Record<string, string | null>;
  published?: boolean;
};

type DatasetSummary = {
  dataset_id: number;
  dataset_name: string;
  dataset_label_contract_revision?: number | null;
  job_count?: number;
  model_count?: number;
  latest_job?: SummaryJob | null;
  best_job?: SummaryJob | null;
  latest_model?: SummaryModel | null;
  best_model?: SummaryModel | null;
  lineage?: SummaryJob[];
  models?: SummaryModel[];
};

type TreeRow = {
  key: string;
  name: string;
  kind: 'dataset' | 'chain' | 'job' | 'model';
  dataset: DatasetSummary;
  job?: SummaryJob;
  model?: SummaryModel;
  round?: number;
  lineageKind?: string;
  status?: string;
  passed?: boolean;
  map50_95?: number | null;
  map50?: number | null;
  artifacts?: Record<string, string | null>;
  published?: boolean;
  createdAt?: string;
  children?: TreeRow[];
};

function metric(value?: number | null) {
  return value == null ? '-' : Number(value).toFixed(4);
}

function time(value?: string | null) {
  return value ? String(value).replace('T', ' ').slice(0, 19) : '-';
}

function statusMeta(status?: string, passed?: boolean) {
  if (passed) return { text: '已达标', color: 'green' };
  const code = String(status || '').toUpperCase();
  if (code === 'FAILED') return { text: '失败', color: 'red' };
  if (code === 'PROCESSING' || code === 'RUNNING') return { text: '训练中', color: 'blue' };
  if (code === 'PENDING') return { text: '排队中', color: 'blue' };
  if (code === 'COMPLETED') return { text: '不达标', color: 'red' };
  if (code === 'CANCELLED') return { text: '已取消', color: 'gold' };
  if (code === 'PUBLISHED') return { text: '已发布', color: 'green' };
  return { text: status || '未训练', color: 'default' };
}

function trainingRows(datasets: DatasetSummary[]): TreeRow[] {
  return datasets.map((dataset) => {
    const rootGroups = new Map<number, SummaryJob[]>();
    for (const job of dataset.lineage || []) {
      const rootId = job.auto_iteration_root_id || job.job_id;
      const group = rootGroups.get(rootId) || [];
      group.push(job);
      rootGroups.set(rootId, group);
    }
    const chains = Array.from(rootGroups.entries()).map(([rootId, jobs]) => {
      const ordered = [...jobs].sort(
        (left, right) => (left.auto_iteration_round_no || 1) - (right.auto_iteration_round_no || 1),
      );
      const passed = ordered.some((job) => job.quality_passed);
      const latest = ordered[ordered.length - 1];
      const best = ordered.reduce<SummaryJob | undefined>(
        (current, item) =>
          (item.metrics?.map50_95 || 0) > (current?.metrics?.map50_95 || 0) ? item : current,
        undefined,
      );
      return {
        key: `chain:${dataset.dataset_id}:${rootId}`,
        name: `迭代链 #${rootId}`,
        kind: 'chain' as const,
        dataset,
        round: ordered.length,
        status: passed ? 'COMPLETED' : latest?.status,
        passed,
        map50_95: best?.metrics?.map50_95,
        map50: best?.metrics?.map50,
        createdAt: latest?.created_at,
        children: ordered.map((job) => ({
          key: `job:${dataset.dataset_id}:${job.job_id}`,
          name: `第${job.auto_iteration_round_no || 1}轮 · 作业 #${job.job_id}`,
          kind: 'job' as const,
          dataset,
          job,
          round: job.auto_iteration_round_no || 1,
          lineageKind: job.lineage_kind,
          status: job.status,
          passed: job.quality_passed,
          map50_95: job.metrics?.map50_95,
          map50: job.metrics?.map50,
          createdAt: job.created_at,
        })),
      };
    });
    return {
      key: `dataset:${dataset.dataset_id}`,
      name: dataset.dataset_name,
      kind: 'dataset' as const,
      dataset,
      status: dataset.latest_job?.status,
      passed:
        dataset.latest_job?.quality_passed || dataset.best_job?.quality_passed,
      map50_95: dataset.best_job?.metrics?.map50_95,
      map50: dataset.best_job?.metrics?.map50,
      createdAt: dataset.latest_job?.created_at,
      children: chains,
    };
  });
}

function modelRows(datasets: DatasetSummary[]): TreeRow[] {
  return datasets.map((dataset) => ({
    key: `dataset:${dataset.dataset_id}`,
    name: dataset.dataset_name,
    kind: 'dataset' as const,
    dataset,
    status: dataset.latest_model?.status,
    passed: dataset.latest_model?.published,
    map50_95: dataset.best_model?.metrics?.map50_95,
    map50: dataset.best_model?.metrics?.map50,
    createdAt: dataset.latest_model?.created_at,
    children: [...(dataset.models || [])]
      .sort((left, right) => String(right.created_at || '').localeCompare(String(left.created_at || '')))
      .map((model) => ({
        key: `model:${dataset.dataset_id}:${model.model_version_id}`,
        name: model.display_name || `模型 #${model.model_version_id}`,
        kind: 'model' as const,
        dataset,
        model,
        status: model.status,
        passed: model.published,
        map50_95: model.metrics?.map50_95,
        map50: model.metrics?.map50,
        artifacts: model.artifacts,
        published: model.published,
        createdAt: model.created_at,
      })),
  }));
}

export default function DatasetTreeTable({ mode }: { mode: WorkbenchMode }) {
  const isTraining = mode === 'training';
  const endpoint = isTraining ? '/v1/training/datasets/summary' : '/v1/models/dataset-summary';
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<'all' | 'passed' | 'untrained' | 'published'>('all');
  const [selected, setSelected] = useState<TreeRow | null>(null);
  const query = useQuery<DatasetSummary[]>({
    queryKey: ['dataset-tree-table', mode, search],
    queryFn: async () => {
      const response = await request.get(endpoint, {
        params: { page: 0, size: 100, q: search || undefined },
      });
      const body = response.data?.data ?? response.data;
      return Array.isArray(body?.datasets) ? body.datasets : [];
    },
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
  const datasets = useMemo(() => query.data || [], [query.data]);
  const visibleDatasets = useMemo(() => {
    if (filter === 'all') return datasets;
    if (filter === 'passed') return datasets.filter((dataset) => (dataset.lineage || []).some((job) => job.quality_passed));
    if (filter === 'untrained') return datasets.filter((dataset) => !dataset.job_count && !dataset.model_count);
    return datasets.filter((dataset) => (dataset.models || []).some((model) => model.published));
  }, [datasets, filter]);
  const rows = useMemo(
    () => (isTraining ? trainingRows(visibleDatasets) : modelRows(visibleDatasets)),
    [visibleDatasets, isTraining],
  );

  const columns: ColumnsType<TreeRow> = [
    {
      title: isTraining ? '数据集 / 迭代链 / 训练轮次' : '数据集 / 模型版本',
      dataIndex: 'name',
      key: 'name',
      width: 360,
      render: (value, row) => (
        <Space size={6}>
          <span>{value}</span>
          {row.kind === 'chain' && <Tag color={row.passed ? 'green' : 'red'}>{row.passed ? '已达标' : '未达标'}</Tag>}
          {row.kind === 'job' && (
            <Tag color={row.lineageKind === 'AUTO' ? 'purple' : 'default'}>
              {row.lineageKind === 'AUTO' ? '自动迭代' : '人工创建'}
            </Tag>
          )}
        </Space>
      ),
    },
    {
      title: '类型',
      key: 'kind',
      width: 100,
      render: (_, row) => (row.kind === 'dataset' ? '数据集' : row.kind === 'chain' ? '迭代链' : row.kind === 'job' ? '训练轮次' : '模型版本'),
    },
    {
      title: '状态',
      key: 'status',
      width: 100,
      render: (_, row) => {
        const meta = statusMeta(row.status, row.passed);
        return <Tag color={meta.color}>{meta.text}</Tag>;
      },
    },
    {
      title: 'mAP50-95',
      key: 'map50_95',
      width: 110,
      align: 'right',
      render: (_, row) => metric(row.map50_95),
    },
    {
      title: 'mAP50',
      key: 'map50',
      width: 100,
      align: 'right',
      render: (_, row) => metric(row.map50),
    },
    {
      title: isTraining ? '最近训练' : '更新时间',
      key: 'created_at',
      width: 180,
      render: (_, row) => time(row.createdAt),
    },
    {
      title: '操作',
      key: 'action',
      width: 120,
      render: (_, row) => (
        <Button type="link" onClick={() => setSelected(row)}>
          详情
        </Button>
      ),
    },
  ];

  return (
    <div style={{ display: 'grid', gap: 14 }}>
      <Space style={{ width: '100%', justifyContent: 'space-between', flexWrap: 'wrap' }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>
            {isTraining ? '训练目录' : '模型目录'}
          </Typography.Title>
          <Typography.Text type="secondary">
            {isTraining ? '按数据集展开迭代链和训练轮次。' : '按数据集展开模型版本。'}
          </Typography.Text>
        </div>
        <Space wrap>
          <Input
            allowClear
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索数据集"
            prefix={<SearchOutlined />}
            style={{ width: 220 }}
          />
          <Segmented
            value={filter}
            onChange={(value) => setFilter(value as typeof filter)}
            options={
              isTraining
                ? [
                    { value: 'all', label: '全部' },
                    { value: 'passed', label: '已达标' },
                    { value: 'untrained', label: '未训练' },
                  ]
                : [
                    { value: 'all', label: '全部' },
                    { value: 'published', label: '已发布' },
                  ]
            }
          />
          <Button icon={<ReloadOutlined />} onClick={() => void query.refetch()} loading={query.isFetching}>
            刷新
          </Button>
          <Button href={isTraining ? '/training?view=jobs' : '/models?view=pt'}>
            {isTraining ? '全部作业' : '经典模型视图'}
          </Button>
        </Space>
      </Space>

      {query.isLoading ? (
        <div style={{ minHeight: 220, display: 'grid', placeItems: 'center' }}>
          <Spin />
        </div>
      ) : query.error ? (
        <Alert type="error" showIcon message="目录加载失败，请稍后刷新重试" />
      ) : !rows.length ? (
        <Empty description="当前筛选条件下暂无数据" />
      ) : (
        <Table<TreeRow>
          rowKey="key"
          size="middle"
          columns={columns}
          dataSource={rows}
          pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (count) => `共 ${count} 条` }}
          scroll={{ x: 1000 }}
          expandable={{
            defaultExpandAllRows: false,
            defaultExpandedRowKeys: rows.length ? [rows[0].key] : [],
          }}
          onRow={(row) => ({
            onClick: (event) => {
              if ((event.target as HTMLElement).closest('button')) return;
              setSelected(row);
            },
          })}
        />
      )}

      <Drawer
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        width={520}
        title={selected ? `${selected.dataset.dataset_name} / ${selected.name}` : ''}
      >
        {selected ? <DetailPanel row={selected} mode={mode} /> : null}
      </Drawer>
    </div>
  );
}

function DetailPanel({ row, mode }: { row: TreeRow; mode: WorkbenchMode }) {
  if (row.kind === 'model' && row.model) {
    const model = row.model;
    return (
      <Descriptions column={1} size="small" bordered>
        <Descriptions.Item label="模型">{model.display_name || `#${model.model_version_id}`}</Descriptions.Item>
        <Descriptions.Item label="状态">{model.status || '-'}</Descriptions.Item>
        <Descriptions.Item label="版本">{model.branch_version_no ?? model.version_no ?? '-'}</Descriptions.Item>
        <Descriptions.Item label="mAP50-95">{metric(model.metrics?.map50_95)}</Descriptions.Item>
        <Descriptions.Item label="mAP50">{metric(model.metrics?.map50)}</Descriptions.Item>
        <Descriptions.Item label="PT">{model.artifacts?.pt || '-'}</Descriptions.Item>
        <Descriptions.Item label="ONNX">{model.artifacts?.onnx || '-'}</Descriptions.Item>
        <Descriptions.Item label="BModel">{model.artifacts?.bmodel || '-'}</Descriptions.Item>
        <Descriptions.Item label="发布">{model.published ? '已发布' : '未发布'}</Descriptions.Item>
        <Descriptions.Item label="时间">{time(model.created_at)}</Descriptions.Item>
      </Descriptions>
    );
  }
  if (row.kind === 'job' && row.job) {
    const job = row.job;
    return (
      <Descriptions column={1} size="small" bordered>
        <Descriptions.Item label="作业">#{job.job_id}</Descriptions.Item>
        <Descriptions.Item label="轮次">第{job.auto_iteration_round_no || 1}轮</Descriptions.Item>
        <Descriptions.Item label="方式">{job.lineage_kind === 'AUTO' ? '自动迭代' : '人工创建'}</Descriptions.Item>
        <Descriptions.Item label="状态">{statusMeta(job.status, job.quality_passed).text}</Descriptions.Item>
        <Descriptions.Item label="mAP50-95">{metric(job.metrics?.map50_95)}</Descriptions.Item>
        <Descriptions.Item label="mAP50">{metric(job.metrics?.map50)}</Descriptions.Item>
        <Descriptions.Item label="精确率">{metric(job.metrics?.precision)}</Descriptions.Item>
        <Descriptions.Item label="召回率">{metric(job.metrics?.recall)}</Descriptions.Item>
        <Descriptions.Item label="时间">{time(job.created_at)}</Descriptions.Item>
        <Descriptions.Item label="操作" span={1}>
          <Button type="primary" href={`/training?view=jobs&job_id=${job.job_id}`}>
            查看训练作业
          </Button>
        </Descriptions.Item>
      </Descriptions>
    );
  }
  return (
    <Descriptions column={1} size="small" bordered>
      <Descriptions.Item label="数据集">{row.dataset.dataset_name}</Descriptions.Item>
      <Descriptions.Item label="类型">{mode === 'training' ? '训练目录' : '模型目录'}</Descriptions.Item>
      <Descriptions.Item label="节点">{row.name}</Descriptions.Item>
      <Descriptions.Item label="最近更新">{time(row.createdAt)}</Descriptions.Item>
      <Descriptions.Item label="mAP50-95">{metric(row.map50_95)}</Descriptions.Item>
      <Descriptions.Item label="mAP50">{metric(row.map50)}</Descriptions.Item>
    </Descriptions>
  );
}
