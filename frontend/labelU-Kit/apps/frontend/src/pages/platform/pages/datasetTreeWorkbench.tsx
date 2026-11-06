import {
  BranchesOutlined,
  DatabaseOutlined,
  FileOutlined,
  FolderOpenOutlined,
  ReloadOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Input,
  Row,
  Segmented,
  Space,
  Spin,
  Tag,
  Tree,
  Typography,
} from 'antd';
import type { DataNode } from 'antd/es/tree';
import { useEffect, useMemo, useState } from 'react';

import request from '@/api/request';
import { trainingLineageColor, trainingLineageLabel } from '../shared';

const DirectoryTree = Tree.DirectoryTree;

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

function formatMetric(value?: number | null) {
  return value == null ? '-' : Number(value).toFixed(4);
}

function formatTime(value?: string | null) {
  return value ? String(value).replace('T', ' ').slice(0, 19) : '-';
}

function jobState(job?: SummaryJob | null) {
  if (!job) return { text: '未训练', color: 'default' };
  if (job.quality_passed) return { text: '已达标', color: 'green' };
  if (job.status === 'FAILED') return { text: '失败', color: 'red' };
  if (job.status === 'PROCESSING' || job.status === 'RUNNING') return { text: '训练中', color: 'blue' };
  if (job.status === 'PENDING') return { text: '排队中', color: 'blue' };
  return { text: job.quality_status || '不达标', color: 'red' };
}

function modelState(model?: SummaryModel | null) {
  if (!model) return { text: '未注册', color: 'default' };
  if (model.published) return { text: '已发布', color: 'green' };
  return { text: model.status || '候选', color: model.status === 'FAILED' ? 'red' : 'blue' };
}

function datasetNodeTitle(dataset: DatasetSummary, mode: WorkbenchMode) {
  const latest = mode === 'training' ? jobState(dataset.latest_job) : modelState(dataset.latest_model);
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{dataset.dataset_name}</span>
      <Tag color={latest.color} style={{ marginInlineEnd: 0 }}>
        {latest.text}
      </Tag>
    </span>
  );
}

function jobNodeTitle(job: SummaryJob) {
  const status = jobState(job);
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      <span style={{ color: '#64748b' }}>第{job.auto_iteration_round_no || 1}轮</span>
      <span>作业 #{job.job_id}</span>
      <Tag color={trainingLineageColor(job.lineage_kind)} style={{ marginInlineEnd: 0 }}>
        {trainingLineageLabel(job.lineage_kind)}
      </Tag>
      <Tag color={status.color} style={{ marginInlineEnd: 0 }}>
        {status.text}
      </Tag>
      <span style={{ color: '#64748b' }}>{formatMetric(job.metrics?.map50_95)}</span>
    </span>
  );
}

function modelNodeTitle(model: SummaryModel) {
  const state = modelState(model);
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      <span>{model.display_name || `模型 #${model.model_version_id}`}</span>
      <Tag color={state.color} style={{ marginInlineEnd: 0 }}>
        {state.text}
      </Tag>
      <span style={{ color: '#64748b' }}>{formatMetric(model.metrics?.map50_95)}</span>
    </span>
  );
}

export default function DatasetTreeWorkbench({ mode }: { mode: WorkbenchMode }) {
  const isTraining = mode === 'training';
  const endpoint = isTraining
    ? '/v1/training/datasets/summary'
    : '/v1/models/dataset-summary';
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<'all' | 'passed' | 'untrained' | 'published'>('all');
  const [selectedKey, setSelectedKey] = useState<string>();
  const query = useQuery<DatasetSummary[]>({
    queryKey: ['dataset-tree', mode, search],
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
    if (filter === 'passed') {
      return datasets.filter((dataset) =>
        (dataset.lineage || []).some((job) => job.quality_passed),
      );
    }
    if (filter === 'untrained') {
      return datasets.filter((dataset) => !dataset.job_count && !dataset.model_count);
    }
    return datasets.filter((dataset) => (dataset.models || []).some((model) => model.published));
  }, [datasets, filter]);

  const treeData = useMemo<DataNode[]>(() => {
    if (isTraining) {
      return visibleDatasets.map((dataset) => {
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
          return {
            key: `chain:${dataset.dataset_id}:${rootId}`,
            icon: <BranchesOutlined />,
            title: (
              <span>
                迭代链 #{rootId}
                <Tag color={passed ? 'green' : 'red'} style={{ marginInlineStart: 8 }}>
                  {passed ? '已达标' : '未达标'} · {ordered.length}轮
                </Tag>
              </span>
            ),
            children: ordered.map((job) => ({
              key: `job:${dataset.dataset_id}:${job.job_id}`,
              icon: <FileOutlined />,
              title: jobNodeTitle(job),
              isLeaf: true,
            })),
          };
        });
        return {
          key: `dataset:${dataset.dataset_id}`,
          icon: <FolderOpenOutlined />,
          title: datasetNodeTitle(dataset, mode),
          children: chains.length ? chains : undefined,
          isLeaf: !chains.length,
        };
      });
    }
    return visibleDatasets.map((dataset) => ({
      key: `dataset:${dataset.dataset_id}`,
      icon: <FolderOpenOutlined />,
      title: datasetNodeTitle(dataset, mode),
      children: (dataset.models || []).length
        ? [...(dataset.models || [])]
            .sort((left, right) => String(right.created_at || '').localeCompare(String(left.created_at || '')))
            .map((model) => ({
              key: `model:${dataset.dataset_id}:${model.model_version_id}`,
              icon: <DatabaseOutlined />,
              title: modelNodeTitle(model),
              isLeaf: true,
            }))
        : undefined,
      isLeaf: !(dataset.models || []).length,
    }));
  }, [visibleDatasets, isTraining, mode]);

  useEffect(() => {
    if (selectedKey) return;
    if (datasets[0]) setSelectedKey(`dataset:${datasets[0].dataset_id}`);
  }, [datasets, selectedKey]);

  const selected = useMemo(() => {
    if (!selectedKey) return null;
    const [kind, datasetId, itemId] = selectedKey.split(':');
    const dataset = datasets.find((item) => String(item.dataset_id) === datasetId);
    if (!dataset) return null;
    if (kind === 'dataset') return { kind, dataset } as const;
    if (kind === 'job') {
      const job = (dataset.lineage || []).find((item) => String(item.job_id) === itemId);
      return job ? ({ kind, dataset, job } as const) : null;
    }
    if (kind === 'model') {
      const model = (dataset.models || []).find((item) => String(item.model_version_id) === itemId);
      return model ? ({ kind, dataset, model } as const) : null;
    }
    return { kind: 'chain', dataset } as const;
  }, [datasets, selectedKey]);

  const defaultExpanded = visibleDatasets.flatMap((dataset) => {
    const keys = [`dataset:${dataset.dataset_id}`];
    if (isTraining) {
      const roots = new Set<number>();
      for (const job of dataset.lineage || []) {
        roots.add(job.auto_iteration_root_id || job.job_id);
      }
      for (const root of roots) {
        keys.push(`chain:${dataset.dataset_id}:${root}`);
      }
    }
    return keys;
  });

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <Space style={{ width: '100%', justifyContent: 'space-between', flexWrap: 'wrap' }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>
            {isTraining ? '训练目录树' : '模型目录树'}
          </Typography.Title>
          <Typography.Text type="secondary">
            {isTraining
              ? '按数据集查看迭代链和每一轮训练，点选节点查看详情。'
              : '按数据集查看模型版本和产物，点选节点查看详情。'}
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
            onChange={(value) => {
              setFilter(value as typeof filter);
              setSelectedKey(undefined);
            }}
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
            {isTraining ? '全部训练作业' : '经典模型视图'}
          </Button>
        </Space>
      </Space>

      {query.isLoading ? (
        <div style={{ minHeight: 220, display: 'grid', placeItems: 'center' }}>
          <Spin />
        </div>
      ) : query.error ? (
        <Alert type="error" showIcon message="目录树加载失败，请稍后刷新重试" />
      ) : !visibleDatasets.length ? (
        <Empty description={filter === 'all' ? '暂无数据集' : '当前筛选条件下暂无数据集'} />
      ) : (
        <Row gutter={16} align="top">
          <Col xs={24} lg={8} xl={7}>
            <Card size="small" title="数据集目录" styles={{ body: { maxHeight: 680, overflow: 'auto' } }}>
              <DirectoryTree
                showIcon
                blockNode
                defaultExpandAll={false}
                defaultExpandedKeys={defaultExpanded}
                selectedKeys={selectedKey ? [selectedKey] : []}
                onSelect={(keys) => {
                  const key = String(keys[0] || '');
                  if (key && !key.startsWith('empty:')) {
                    setSelectedKey(key);
                  }
                }}
                treeData={treeData}
              />
            </Card>
          </Col>
          <Col xs={24} lg={16} xl={17}>
            <Card size="small" title={isTraining ? '训练详情' : '模型详情'}>
              {!selected ? (
                <Empty description="请选择左侧数据集、迭代链或记录" />
              ) : selected.kind === 'job' ? (
                <TrainingJobDetail dataset={selected.dataset} job={selected.job} />
              ) : selected.kind === 'model' ? (
                <ModelDetail dataset={selected.dataset} model={selected.model} />
              ) : selected.kind === 'chain' ? (
                <ChainDetail dataset={selected.dataset} />
              ) : (
                <DatasetDetail dataset={selected.dataset} mode={mode} />
              )}
            </Card>
          </Col>
        </Row>
      )}
    </div>
  );
}

function DatasetDetail({ dataset, mode }: { dataset: DatasetSummary; mode: WorkbenchMode }) {
  if (mode === 'training') {
    const latest = dataset.latest_job;
    const best = dataset.best_job;
    const status = jobState(latest);
    return (
      <Descriptions column={1} size="small" bordered>
        <Descriptions.Item label="数据集">{dataset.dataset_name}（#{dataset.dataset_id}）</Descriptions.Item>
        <Descriptions.Item label="标签契约">
          {dataset.dataset_label_contract_revision ? `v${dataset.dataset_label_contract_revision}` : '未记录'}
        </Descriptions.Item>
        <Descriptions.Item label="训练状态">
          <Tag color={status.color}>{status.text}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="最新训练">
          {latest ? `作业 #${latest.job_id} · 第${latest.auto_iteration_round_no || 1}轮 · mAP50-95 ${formatMetric(latest.metrics?.map50_95)}` : '暂无'}
        </Descriptions.Item>
        <Descriptions.Item label="历史最佳">
          {best ? `作业 #${best.job_id} · ${jobState(best).text} · mAP50-95 ${formatMetric(best.metrics?.map50_95)}` : '暂无'}
        </Descriptions.Item>
        <Descriptions.Item label="训练记录">{dataset.job_count || 0} 次</Descriptions.Item>
      </Descriptions>
    );
  }
  const latest = dataset.latest_model;
  const best = dataset.best_model;
  const modelStatus = modelState(latest);
  return (
    <Descriptions column={1} size="small" bordered>
      <Descriptions.Item label="数据集">{dataset.dataset_name}（#{dataset.dataset_id}）</Descriptions.Item>
      <Descriptions.Item label="最新模型">{latest?.display_name || '暂无'}</Descriptions.Item>
      <Descriptions.Item label="历史最佳">{best ? `${best.display_name || '模型'} · mAP50-95 ${formatMetric(best.metrics?.map50_95)}` : '暂无'}</Descriptions.Item>
      <Descriptions.Item label="模型版本">{dataset.model_count || 0} 个</Descriptions.Item>
      <Descriptions.Item label="最新状态"><Tag color={modelStatus.color}>{modelStatus.text}</Tag></Descriptions.Item>
    </Descriptions>
  );
}

function ChainDetail({ dataset }: { dataset: DatasetSummary }) {
  const jobs = dataset.lineage || [];
  const best = dataset.best_job;
  return (
    <Descriptions column={1} size="small" bordered>
      <Descriptions.Item label="迭代链">{dataset.dataset_name}</Descriptions.Item>
      <Descriptions.Item label="总轮次">{jobs.length} 轮</Descriptions.Item>
      <Descriptions.Item label="当前最佳">
        {best ? `作业 #${best.job_id} · 第${best.auto_iteration_round_no || 1}轮 · mAP50-95 ${formatMetric(best.metrics?.map50_95)}` : '暂无'}
      </Descriptions.Item>
      <Descriptions.Item label="链状态">
        {jobs.some((job) => job.quality_passed) ? <Tag color="green">已达标</Tag> : <Tag color="red">未达标</Tag>}
      </Descriptions.Item>
    </Descriptions>
  );
}

function TrainingJobDetail({ dataset, job }: { dataset: DatasetSummary; job: SummaryJob }) {
  const status = jobState(job);
  return (
    <div style={{ display: 'grid', gap: 14 }}>
      <Descriptions column={2} size="small" bordered>
        <Descriptions.Item label="数据集">{dataset.dataset_name}</Descriptions.Item>
        <Descriptions.Item label="作业">#{job.job_id}</Descriptions.Item>
        <Descriptions.Item label="轮次">第{job.auto_iteration_round_no || 1}轮</Descriptions.Item>
        <Descriptions.Item label="创建方式">{trainingLineageLabel(job.lineage_kind)}</Descriptions.Item>
        <Descriptions.Item label="父作业">{job.parent_job_id ? `#${job.parent_job_id}` : '-'}</Descriptions.Item>
        <Descriptions.Item label="状态"><Tag color={status.color}>{status.text}</Tag></Descriptions.Item>
        <Descriptions.Item label="mAP50-95">{formatMetric(job.metrics?.map50_95)}</Descriptions.Item>
        <Descriptions.Item label="mAP50">{formatMetric(job.metrics?.map50)}</Descriptions.Item>
        <Descriptions.Item label="精确率">{formatMetric(job.metrics?.precision)}</Descriptions.Item>
        <Descriptions.Item label="召回率">{formatMetric(job.metrics?.recall)}</Descriptions.Item>
        <Descriptions.Item label="创建时间">{formatTime(job.created_at)}</Descriptions.Item>
      </Descriptions>
      <Space wrap>
        <Button type="primary" href={`/training?view=jobs&job_id=${job.job_id}`}>查看训练作业</Button>
        <Button href={`/yolo-eval-reports-hub?taskId=21&jobId=${job.job_id}`}>查看 AI 报告</Button>
      </Space>
    </div>
  );
}

function ModelDetail({ dataset, model }: { dataset: DatasetSummary; model: SummaryModel }) {
  const state = modelState(model);
  return (
    <Descriptions column={2} size="small" bordered>
      <Descriptions.Item label="数据集">{dataset.dataset_name}</Descriptions.Item>
      <Descriptions.Item label="模型">{model.display_name || `#${model.model_version_id}`}</Descriptions.Item>
      <Descriptions.Item label="状态"><Tag color={state.color}>{state.text}</Tag></Descriptions.Item>
      <Descriptions.Item label="版本">{model.branch_version_no ?? model.version_no ?? '-'}</Descriptions.Item>
      <Descriptions.Item label="mAP50-95">{formatMetric(model.metrics?.map50_95)}</Descriptions.Item>
      <Descriptions.Item label="mAP50">{formatMetric(model.metrics?.map50)}</Descriptions.Item>
      <Descriptions.Item label="PT">{model.artifacts?.pt || '-'}</Descriptions.Item>
      <Descriptions.Item label="BModel">{model.artifacts?.bmodel || '-'}</Descriptions.Item>
      <Descriptions.Item label="发布">{model.published ? '已发布' : '未发布'}</Descriptions.Item>
      <Descriptions.Item label="创建时间" span={2}>{formatTime(model.created_at)}</Descriptions.Item>
    </Descriptions>
  );
}
