import { useQuery } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Modal,
  Row,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from 'antd';
import { useMemo, useState } from 'react';

import request from '@/api/request';
import { trainingLineageLabel } from '../shared';

type WorkbenchMode = 'training' | 'model';

type SummaryJob = {
  job_id: number;
  name?: string;
  task_name?: string;
  status?: string;
  created_at?: string;
  auto_iteration_round_no?: number | null;
  lineage_kind?: string;
  quality_passed?: boolean;
  quality_status?: string;
  metrics?: { map50_95?: number | null; map50?: number | null };
};

type SummaryModel = {
  model_version_id: number;
  display_name?: string;
  status?: string;
  created_at?: string;
  version_no?: number | null;
  branch_version_no?: number | null;
  metrics?: { map50_95?: number | null; map50?: number | null };
  artifacts?: Record<string, string | null>;
  published?: boolean;
};

type DatasetSummary = {
  dataset_id: number;
  dataset_name: string;
  dataset_label_contract_revision?: number | null;
  dataset_label_contract_status?: string | null;
  version_count?: number;
  job_count?: number;
  model_count?: number;
  last_trained_at?: string | null;
  last_updated_at?: string | null;
  latest_job?: SummaryJob | null;
  best_job?: SummaryJob | null;
  latest_model?: SummaryModel | null;
  best_model?: SummaryModel | null;
  lineage?: SummaryJob[];
  models?: SummaryModel[];
};

function metric(value?: number | null) {
  return value == null ? '-' : Number(value).toFixed(4);
}

function statusColor(value?: string | null) {
  const code = String(value || '').toUpperCase();
  if (code === 'COMPLETED' || code === 'ACTIVE' || code === 'PUBLISHED') return 'green';
  if (code === 'FAILED' || code === 'ERROR') return 'red';
  if (code === 'PROCESSING' || code === 'RUNNING' || code === 'PENDING') return 'blue';
  return 'default';
}

function statusText(value?: string | null, passed?: boolean) {
  if (passed) return '已达标';
  const code = String(value || '').toUpperCase();
  return (
    {
      COMPLETED: '已完成',
      FAILED: '失败',
      PROCESSING: '训练中',
      RUNNING: '训练中',
      PENDING: '排队中',
      CANCELLED: '已取消',
      ACTIVE: '可用',
      PUBLISHED: '已发布',
    }[code] || value || '未知'
  );
}

function formatTime(value?: string | null) {
  if (!value) return '-';
  return String(value).replace('T', ' ').slice(0, 19);
}

export default function DatasetCards({ mode }: { mode: WorkbenchMode }) {
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<DatasetSummary | null>(null);
  const isTraining = mode === 'training';
  const endpoint = isTraining
    ? '/v1/training/datasets/summary'
    : '/v1/models/dataset-summary';
  const query = useQuery<DatasetSummary[]>({
    queryKey: ['dataset-cards', mode, search],
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
  const rows = useMemo(() => query.data || [], [query.data]);
  const selectedRows = selected
    ? isTraining
      ? selected.lineage || []
      : selected.models || []
    : [];

  return (
    <div style={{ display: 'grid', gap: 14, marginBottom: 18 }}>
      <Space
        style={{ width: '100%', justifyContent: 'space-between', flexWrap: 'wrap' }}
        align="center"
      >
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>
            {isTraining ? '按数据集训练' : '按数据集模型'}
          </Typography.Title>
          <Typography.Text type="secondary">
            {isTraining
              ? '先看数据集最近训练结果和历史最佳，再进入训练记录。'
              : '先看数据集最新模型和历史最佳模型，再查看模型版本和产物。'}
          </Typography.Text>
        </div>
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="搜索数据集"
          aria-label="搜索数据集"
          style={{
            width: 240,
            maxWidth: '100%',
            padding: '6px 10px',
            border: '1px solid #d9d9d9',
            borderRadius: 6,
          }}
        />
      </Space>

      {query.isLoading ? (
        <div style={{ minHeight: 120, display: 'grid', placeItems: 'center' }}>
          <Spin />
        </div>
      ) : query.error ? (
        <Alert
          type="error"
          showIcon
          message={isTraining ? '数据集训练汇总加载失败' : '数据集模型汇总加载失败'}
        />
      ) : !rows.length ? (
        <Empty description="暂无数据集" />
      ) : (
        <Row gutter={[14, 14]}>
          {rows.map((dataset) => {
            const latestJob = dataset.latest_job;
            const bestJob = dataset.best_job;
            const latestModel = dataset.latest_model;
            const bestModel = dataset.best_model;
            return (
              <Col xs={24} md={12} xl={8} key={dataset.dataset_id}>
                <Card
                  hoverable
                  onClick={() => setSelected(dataset)}
                  styles={{ body: { display: 'grid', gap: 8 } }}
                >
                  <Space
                    style={{ width: '100%', justifyContent: 'space-between' }}
                    align="start"
                  >
                    <div>
                      <Typography.Text strong>{dataset.dataset_name}</Typography.Text>
                      <div style={{ color: '#64748b', fontSize: 12 }}>
                        数据集 #{dataset.dataset_id}
                        {dataset.dataset_label_contract_revision
                          ? ` · 契约 v${dataset.dataset_label_contract_revision}`
                          : ''}
                      </div>
                    </div>
                    <Tag color={statusColor(isTraining ? latestJob?.status : latestModel?.status)}>
                      {isTraining
                        ? latestJob
                          ? statusText(latestJob.status, latestJob.quality_passed)
                          : '未训练'
                        : statusText(latestModel?.status)}
                    </Tag>
                  </Space>

                  {isTraining ? (
                    <div style={{ display: 'grid', gap: 4, fontSize: 13 }}>
                      <span>
                        最新：{latestJob ? `#${latestJob.job_id} · 第${latestJob.auto_iteration_round_no || 1}轮 · mAP50-95 ${metric(latestJob.metrics?.map50_95)}` : '暂无训练'}
                      </span>
                      <span>
                        最佳：{bestJob ? `#${bestJob.job_id} · mAP50-95 ${metric(bestJob.metrics?.map50_95)}` : '暂无'}
                      </span>
                      <span style={{ color: '#64748b' }}>
                        训练记录 {dataset.job_count || 0} 次 · 最近 {formatTime(dataset.last_trained_at)}
                      </span>
                    </div>
                  ) : (
                    <div style={{ display: 'grid', gap: 4, fontSize: 13 }}>
                      <span>
                        最新模型：{latestModel ? latestModel.display_name || `#${latestModel.model_version_id}` : '暂无'}
                      </span>
                      <span>
                        最佳模型：{bestModel ? `mAP50-95 ${metric(bestModel.metrics?.map50_95)}` : '暂无'}
                      </span>
                      <span style={{ color: '#64748b' }}>
                        模型版本 {dataset.model_count || 0} 个 · 最近 {formatTime(dataset.last_updated_at)}
                      </span>
                    </div>
                  )}
                  <Button type="link" style={{ paddingInline: 0 }}>
                    {isTraining ? '查看训练记录' : '查看模型版本'}
                  </Button>
                </Card>
              </Col>
            );
          })}
        </Row>
      )}

      <Modal
        open={Boolean(selected)}
        onCancel={() => setSelected(null)}
        footer={null}
        width={900}
        title={selected ? `${selected.dataset_name} · ${isTraining ? '训练记录' : '模型版本'}` : ''}
      >
        {isTraining ? (
          <Table<SummaryJob>
            rowKey="job_id"
            size="small"
            dataSource={selectedRows as SummaryJob[]}
            pagination={false}
            columns={[
              {
                title: '轮次',
                key: 'round',
                width: 90,
                render: (_, row) => `第${row.auto_iteration_round_no || 1}轮`,
              },
              { title: '作业', key: 'job', render: (_, row) => `#${row.job_id} ${row.task_name || row.name || ''}` },
              {
                title: '方式',
                key: 'kind',
                width: 100,
                render: (_, row) => trainingLineageLabel(row.lineage_kind),
              },
              {
                title: 'mAP50-95',
                key: 'map',
                width: 110,
                render: (_, row) => metric(row.metrics?.map50_95),
              },
              {
                title: '状态',
                key: 'status',
                width: 100,
                render: (_, row) => (
                  <Tag color={row.quality_passed ? 'green' : statusColor(row.status)}>
                    {statusText(row.status, row.quality_passed)}
                  </Tag>
                ),
              },
              {
                title: '操作',
                key: 'action',
                width: 100,
                render: (_, row) => (
                  <Button type="link" size="small" href={`/training?view=jobs&job_id=${row.job_id}`}>
                    查看作业
                  </Button>
                ),
              },
            ]}
          />
        ) : (
          <Table<SummaryModel>
            rowKey="model_version_id"
            size="small"
            dataSource={selectedRows as SummaryModel[]}
            pagination={false}
            columns={[
              { title: '模型', key: 'name', render: (_, row) => row.display_name || `#${row.model_version_id}` },
              { title: '版本', key: 'version', width: 100, render: (_, row) => row.branch_version_no ?? row.version_no ?? '-' },
              {
                title: 'mAP50-95',
                key: 'map',
                width: 110,
                render: (_, row) => metric(row.metrics?.map50_95),
              },
              {
                title: 'PT',
                key: 'pt',
                width: 70,
                render: (_, row) => row.artifacts?.pt || '-',
              },
              {
                title: 'BModel',
                key: 'bmodel',
                width: 90,
                render: (_, row) => row.artifacts?.bmodel || '-',
              },
              {
                title: '发布',
                key: 'published',
                width: 80,
                render: (_, row) => (row.published ? '已发布' : '未发布'),
              },
            ]}
          />
        )}
      </Modal>
    </div>
  );
}
