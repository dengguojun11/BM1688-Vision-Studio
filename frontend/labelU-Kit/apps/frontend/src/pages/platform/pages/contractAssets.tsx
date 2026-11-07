import { ArrowLeftOutlined, ArrowRightOutlined, ExperimentOutlined, ReloadOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Descriptions, Drawer, Empty, Segmented, Space, Spin, Tag, Typography } from 'antd';
import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import request from '@/api/request';
import { listData, listTotal } from '../shared';

type Mode = 'model' | 'training' | 'evolution';
type Stage = 'pt' | 'compile' | 'bmodel' | 'published' | 'external';

type LabelItem = { label_key?: string; label_value?: string; label_id?: number };
type JobRow = {
  id: number;
  task_id: number;
  dataset_version_id?: number | null;
  status?: string;
  source_system?: string | null;
  workflow_source?: string | null;
  workflow_source_label?: string | null;
  source_mode?: string | null;
  auto_iteration_root_id?: number | null;
  auto_iteration_round_no?: number | null;
  params?: {
    dataset_manifest?: {
      dataset_id?: number;
      dataset_ids?: number[];
      dataset_label_contract_id?: number;
      dataset_contract_revision?: number;
      labels_snapshot_json?: string;
      class_names?: string[];
    };
  } | null;
};
type Artifact = { artifact_type?: string; precision?: string; status?: string; id?: number };
type Version = {
  id: number;
  display_name: string;
  version_no: number;
  branch_version_no?: number | null;
  training_job_id?: number | null;
  dataset_id?: number | null;
  dataset_version_id?: number | null;
  bm1688_export_id?: number | null;
  published_catalog_id?: number | null;
  status?: string;
  updated_at?: string | null;
  created_at?: string | null;
  artifacts?: Artifact[];
  manifest?: Record<string, unknown>;
  training_metrics?: Record<string, unknown>;
  edge_validation_status?: { status?: string; job_status?: string; publish_gate_status?: string; publish_blocked_reason?: string } | null;
  edge_version_comparison?: { status?: string } | null;
};
type Family = { task_id: number; display_name: string; versions: Version[]; updated_at?: string | null };
type Contract = {
  key: string;
  name: string;
  revision?: number | null;
  taskIds: number[];
  datasetIds: number[];
  labels: LabelItem[];
  jobs: JobRow[];
  versions: Version[];
  manualJobs: JobRow[];
  flexibleJobs: JobRow[];
  autoJobs: JobRow[];
  compileCount: number;
  bmodelVersions: Version[];
  publishedVersions: Version[];
  updatedAt?: string | null;
};


async function fetchAllRows(path: string) {
  const first = await request.get(path, { params: { page: 0, size: 100 } });
  const rows = listData(first);
  const total = listTotal(first, rows.length);
  let page = 1;
  while (rows.length < total && page < 50) {
    const next = await request.get(path, { params: { page, size: 100 } });
    const nextRows = listData(next);
    if (!nextRows.length) break;
    rows.push(...nextRows);
    page += 1;
  }
  return { data: rows, meta_data: { total } };
}

const statusLabels: Record<string, string> = {
  PENDING: '排队中', PROCESSING: '训练中', RUNNING: '训练中', COMPLETED: '训练完成',
  FAILED: '训练失败', CANCELLED: '已取消', PUBLISHED: '已发布',
};

function json<T>(value: string | undefined, fallback: T): T {
  if (!value) return fallback;
  try {
    return JSON.parse(value) as T;
  } catch {
    return fallback;
  }
}

function versionLabel(version: Version) {
  return `v${version.branch_version_no ?? version.version_no}`;
}

function jobIsFlexible(job: JobRow) {
  return job.source_system === 'strategy_evolution' || job.workflow_source === 'flexible_workflow';
}

function jobIsAuto(job: JobRow) {
  return Boolean(job.auto_iteration_root_id && Number(job.auto_iteration_round_no || 1) > 1);
}

function statusTag(status?: string | null) {
  const code = String(status || '').toUpperCase();
  const color = code === 'COMPLETED' ? 'green' : code === 'FAILED' ? 'red' : code === 'PROCESSING' || code === 'RUNNING' ? 'blue' : 'default';
  return <Tag color={color}>{statusLabels[code] || status || '-'}</Tag>;
}

function edgeTag(version: Version) {
  const status = String(version.edge_validation_status?.status || '').toUpperCase();
  if (status === 'PASS') return <Tag color="green">已验证</Tag>;
  if (status === 'FAIL') return <Tag color="red">未通过</Tag>;
  if (status === 'PROCESSING') return <Tag color="blue">验证中</Tag>;
  return <Tag color="gold">待验证</Tag>;
}

function publishTag(version: Version) {
  if (version.published_catalog_id || String(version.status || '').toUpperCase() === 'PUBLISHED') return <Tag color="green">已发布</Tag>;
  const blocked = Boolean(version.edge_validation_status?.publish_gate_status && !['ALLOWED', 'PASS'].includes(String(version.edge_validation_status.publish_gate_status).toUpperCase()));
  return blocked ? <Tag color="red">暂不可发布</Tag> : <Tag color="gold">待完善证据</Tag>;
}

function labelsOf(job: JobRow): LabelItem[] {
  const manifest = job.params?.dataset_manifest;
  const parsed = json<LabelItem[]>(manifest?.labels_snapshot_json, []);
  if (parsed.length) return parsed;
  return (manifest?.class_names || []).map((value) => ({ label_key: value, label_value: value }));
}

function contractKey(job: JobRow) {
  const manifest = job.params?.dataset_manifest;
  if (manifest?.dataset_label_contract_id) return `contract:${manifest.dataset_label_contract_id}`;
  if (manifest?.dataset_id) return `dataset:${manifest.dataset_id}`;
  return `task:${job.task_id}`;
}

function resolvedContractKey(job: JobRow, knownByTask: Map<number, string>) {
  const manifest = job.params?.dataset_manifest;
  if (manifest?.dataset_label_contract_id) return `contract:${manifest.dataset_label_contract_id}`;
  return knownByTask.get(Number(job.task_id)) || contractKey(job);
}

export default function ContractAssetCenter({ mode = 'model' }: { mode?: Mode }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [selectedVersion, setSelectedVersion] = useState<Version | null>(null);
  const stageParam = searchParams.get('contract_stage') as Stage | null;
  const stage: Stage = stageParam && ['pt', 'compile', 'bmodel', 'published', 'external'].includes(stageParam) ? stageParam : 'pt';
  const selectedKey = searchParams.get('contract');
  const familiesQuery = useQuery({
    queryKey: ['contract-assets', 'families'],
    queryFn: async () => await fetchAllRows('/v1/models/families'),
    staleTime: 30_000,
  });
  const jobsQuery = useQuery({
    queryKey: ['contract-assets', 'jobs'],
    queryFn: async () => await fetchAllRows('/v1/training/jobs'),
    staleTime: 30_000,
  });

  const contracts = useMemo<Contract[]>(() => {
    const families = listData(familiesQuery.data) as unknown as Family[];
    const jobs = listData(jobsQuery.data) as unknown as JobRow[];
    const versionsByJob = new Map<number, Version>();
    const familyNameByJob = new Map<number, string>();
    for (const family of families) {
      for (const version of family.versions || []) {
        if (version.training_job_id) versionsByJob.set(Number(version.training_job_id), version);
        const manifest = version.manifest || {};
        if (version.training_job_id && manifest.task_name) familyNameByJob.set(Number(version.training_job_id), String(manifest.task_name));
      }
    }
    const knownByTask = new Map<number, string>();
    for (const job of jobs) {
      const contractId = job.params?.dataset_manifest?.dataset_label_contract_id;
      if (contractId && !knownByTask.has(Number(job.task_id))) {
        knownByTask.set(Number(job.task_id), `contract:${contractId}`);
      }
    }
    const grouped = new Map<string, Contract>();
    for (const job of jobs) {
      const key = resolvedContractKey(job, knownByTask);
      const version = versionsByJob.get(Number(job.id));
      let contract = grouped.get(key);
      if (!contract) {
        const familyName = familyNameByJob.get(Number(job.id));
        contract = {
          key,
          name: familyName || `标签契约 #${key.split(':')[1]}`,
          revision: job.params?.dataset_manifest?.dataset_contract_revision ?? null,
          taskIds: [], datasetIds: [], labels: [], jobs: [], versions: [],
          manualJobs: [], flexibleJobs: [], autoJobs: [], compileCount: 0,
          bmodelVersions: [], publishedVersions: [], updatedAt: null,
        };
        grouped.set(key, contract);
      }
      contract.jobs.push(job);
      if (!contract.taskIds.includes(Number(job.task_id))) contract.taskIds.push(Number(job.task_id));
      const datasetId = job.params?.dataset_manifest?.dataset_id;
      if (datasetId && !contract.datasetIds.includes(Number(datasetId))) contract.datasetIds.push(Number(datasetId));
      if (!contract.labels.length) contract.labels = labelsOf(job);
      if (jobIsFlexible(job)) contract.flexibleJobs.push(job); else contract.manualJobs.push(job);
      if (jobIsAuto(job)) contract.autoJobs.push(job);
      if (version) {
        contract.versions.push(version);
        if (version.bm1688_export_id) contract.compileCount += 1;
        if ((version.artifacts || []).some((artifact) => artifact.artifact_type === 'bmodel')) contract.bmodelVersions.push(version);
        if (version.published_catalog_id || String(version.status || '').toUpperCase() === 'PUBLISHED') contract.publishedVersions.push(version);
        const updated = version.updated_at || version.created_at;
        if (updated && (!contract.updatedAt || updated > contract.updatedAt)) contract.updatedAt = updated;
      }
    }
    return [...grouped.values()].filter((contract) => contract.jobs.length).sort((left, right) => String(right.updatedAt || '').localeCompare(String(left.updatedAt || '')));
  }, [familiesQuery.data, jobsQuery.data]);

  const loading = familiesQuery.isLoading || jobsQuery.isLoading;
  const error = familiesQuery.error || jobsQuery.error;
  const selected = contracts.find((contract) => contract.key === selectedKey) || null;
  const openContract = (contract: Contract) => {
    const next = new URLSearchParams(searchParams);
    next.set('contract', contract.key);
    next.delete('contract_stage');
    setSearchParams(next);
  };
  const closeContract = () => {
    const next = new URLSearchParams(searchParams);
    next.delete('contract');
    next.delete('contract_stage');
    setSearchParams(next);
  };
  const changeStage = (nextStage: Stage) => {
    const next = new URLSearchParams(searchParams);
    next.set('contract_stage', nextStage);
    setSearchParams(next);
  };

  if (selected) {
    return (
      <>
        <ContractDetail
          contract={selected}
          stage={stage}
          onStage={changeStage}
          onBack={closeContract}
          onOpenModel={setSelectedVersion}
        />
        <ModelSnapshotDrawer version={selectedVersion} onClose={() => setSelectedVersion(null)} />
      </>
    );
  }

  return (
    <>
    <div style={{ display: 'grid', gap: 14 }}>
      <Alert
        type="info"
        showIcon
        message={mode === 'model' ? '按标签契约查看模型资产' : mode === 'training' ? '从已有训练资产进入训练' : '按标签契约查看进化与回流'}
        description="一个卡片 = 一套标签契约；进入后按 训练权重 → 编译 → 边端 BModel → 已发布 → 外部导入 查看。来源区分为人工创建和第三方柔性工作流，自动迭代挂在对应模型链下。"
      />
      <Space wrap>
        <Typography.Text strong>共 {contracts.length} 个有训练数据的标签契约</Typography.Text>
        <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={() => { void familiesQuery.refetch(); void jobsQuery.refetch(); }}>刷新</Button>
      </Space>
      {loading && contracts.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
      ) : error && contracts.length === 0 ? (
        <Alert type="error" showIcon message="标签契约资产加载失败" />
      ) : contracts.length === 0 ? (
        <Empty description="暂无已经产生训练数据的标签契约" />
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(330px, 1fr))', gap: 14 }}>
          {contracts.map((contract) => (
            <Card
              key={contract.key}
              hoverable
              onClick={() => openContract(contract)}
              style={{ borderTop: `3px solid ${contract.flexibleJobs.length ? '#6d28d9' : '#0d53de'}` }}
              bodyStyle={{ display: 'grid', gap: 10 }}
            >
              <Space style={{ width: '100%', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <Space direction="vertical" size={2}>
                  <Typography.Title level={5} style={{ margin: 0 }}>{contract.name}</Typography.Title>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    契约 rev.{contract.revision ?? '-'} · 任务 {contract.taskIds.map((id) => `#${id}`).join('、')} · 数据集 {contract.datasetIds.map((id) => `#${id}`).join('、') || '-'}
                  </Typography.Text>
                </Space>
                {contract.publishedVersions.length ? <Tag color="green">已发布 {contract.publishedVersions.length}</Tag> : contract.bmodelVersions.length ? edgeTag(contract.bmodelVersions[0]) : <Tag>待编译</Tag>}
              </Space>
              <Space wrap size={[4, 4]}>
                {contract.labels.slice(0, 8).map((label, index) => (
                  <Tag key={`${label.label_id || index}-${label.label_value || label.label_key}`} style={{ marginInlineEnd: 0 }}>
                    {label.label_key || label.label_value}
                  </Tag>
                ))}
                {contract.labels.length > 8 && <Tag>+{contract.labels.length - 8}</Tag>}
              </Space>
              <Space wrap size={[4, 4]}>
                <Tag color="blue">人工创建 {contract.manualJobs.length}</Tag>
                <Tag color="purple">柔性工作流 {contract.flexibleJobs.length}</Tag>
                <Tag color="gold">自动迭代 {contract.autoJobs.length}</Tag>
              </Space>
              <Space size={[6, 6]} wrap>
                <Tag>PT {contract.versions.length}</Tag>
                <Tag>编译 {contract.compileCount}</Tag>
                <Tag>BModel {contract.bmodelVersions.length}</Tag>
                <Tag>已发布 {contract.publishedVersions.length}</Tag>
              </Space>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #eef2f7', paddingTop: 9 }}>
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                  更新 {contract.updatedAt ? new Date(contract.updatedAt).toLocaleString() : '-'}
                </Typography.Text>
                <Button type="link" style={{ paddingInline: 0 }}>进入资产 <ArrowRightOutlined /></Button>
              </div>
            </Card>
          ))}
        </div>
      )}
      <Typography.Text type="secondary" style={{ fontSize: 11, textAlign: 'center' }}>
        只显示已产生训练数据的标签契约；卡片可以继续进入编译、边端、已发布和外部导入。
      </Typography.Text>
      <ModelSnapshotDrawer version={selectedVersion} onClose={() => setSelectedVersion(null)} />
    </div>
    </>
  );
}

function ContractDetail({ contract, stage, onStage, onBack, onOpenModel }: {
  contract: Contract;
  stage: Stage;
  onStage: (stage: Stage) => void;
  onBack: () => void;
  onOpenModel: (version: Version) => void;
}) {
  const counts = {
    pt: contract.versions.length,
    compile: contract.compileCount,
    bmodel: contract.bmodelVersions.length,
    published: contract.publishedVersions.length,
    external: 0,
  };
  const options = [
    { label: `PT ${counts.pt}`, value: 'pt' },
    { label: `编译 ${counts.compile}`, value: 'compile' },
    { label: `BModel ${counts.bmodel}`, value: 'bmodel' },
    { label: `已发布 ${counts.published}`, value: 'published' },
    { label: `外部导入 ${counts.external}`, value: 'external' },
  ];
  const bySource = (jobs: JobRow[]) => jobs
    .map((job) => ({ job, version: contract.versions.find((version) => Number(version.training_job_id) === Number(job.id)) }))
    .sort((left, right) => Number(right.job.id) - Number(left.job.id));
  const renderJob = ({ job, version }: { job: JobRow; version?: Version }) => (
    <div key={job.id} style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) 110px 130px', gap: 10, padding: '10px 0', borderBottom: '1px solid #f1f4f8', alignItems: 'center' }}>
      <Space direction="vertical" size={1}>
        <Space wrap size={[6, 4]}>
          <Typography.Text strong>{version?.display_name || `训练作业 #${job.id}`}</Typography.Text>
          {version && <Tag color="blue">{versionLabel(version)}</Tag>}
          {jobIsAuto(job) && <Tag color="gold">自动迭代 #{job.auto_iteration_round_no}</Tag>}
          <Tag color={jobIsFlexible(job) ? 'purple' : 'blue'}>{jobIsFlexible(job) ? '柔性工作流' : '人工创建'}</Tag>
        </Space>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          训练作业 #{job.id} · 任务 #{job.task_id} · 数据集 {job.params?.dataset_manifest?.dataset_id ? `#${job.params.dataset_manifest.dataset_id}` : '-'} · 更新 {job.id ? new Date().toLocaleDateString() : '-'}
        </Typography.Text>
      </Space>
      <div>{statusTag(job.status)}</div>
      <Space size={4}>
        {version && <Button size="small" type="link" onClick={() => onOpenModel(version)}>查看模型</Button>}
      </Space>
    </div>
  );

  return (
    <div style={{ display: 'grid', gap: 14 }}>
      <Space wrap>
        <Button icon={<ArrowLeftOutlined />} onClick={onBack}>返回契约卡片</Button>
        <Typography.Title level={4} style={{ margin: 0 }}>{contract.name}</Typography.Title>
        <Tag>契约 rev.{contract.revision ?? '-'}</Tag>
        <Tag>任务 {contract.taskIds.map((id) => `#${id}`).join('、')}</Tag>
        <Tag>数据集 {contract.datasetIds.map((id) => `#${id}`).join('、') || '-'}</Tag>
      </Space>
      <Space wrap size={[4, 4]}>
        {contract.labels.map((label, index) => <Tag key={`${label.label_id || index}-${label.label_value || label.label_key}`}>{label.label_key || label.label_value}</Tag>)}
      </Space>
      <Segmented options={options} value={stage} onChange={(value) => onStage(value as Stage)} />
      {stage === 'pt' ? (
        <div style={{ display: 'grid', gap: 12 }}>
          <Alert type="info" showIcon message="PT 模型按来源分组" description="人工创建训练与第三方柔性工作流分开显示；自动迭代挂在对应模型链下。" />
          {[
            { title: '系统人工创建训练', color: 'blue', jobs: bySource(contract.manualJobs) },
            { title: '第三方柔性工作流', color: 'purple', jobs: bySource(contract.flexibleJobs) },
          ].map((group) => group.jobs.length ? (
            <Card key={group.title} size="small" title={<Space><Tag color={group.color}>{group.jobs.length}</Tag>{group.title}</Space>}>
              {group.jobs.map(renderJob)}
            </Card>
          ) : null)}
        </div>
      ) : stage === 'compile' ? (
        <Card size="small" title="编译任务">
          {contract.versions.filter((version) => version.bm1688_export_id).length ? contract.versions.filter((version) => version.bm1688_export_id).map((version) => (
            <div key={version.id} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '8px 0', borderBottom: '1px solid #f1f4f8' }}>
              <Space><Typography.Text strong>{version.display_name}</Typography.Text><Tag>{versionLabel(version)}</Tag></Space>
              <Space><Tag>export #{version.bm1688_export_id}</Tag>{statusTag(version.status)}</Space>
            </div>
          )) : <Empty description="暂无编译任务" />}
        </Card>
      ) : stage === 'bmodel' ? (
        <Card size="small" title="边端模型 BModel">
          {contract.bmodelVersions.length ? contract.bmodelVersions.map((version) => {
            const precision = new Map<string, Artifact>();
            for (const artifact of version.artifacts || []) {
              if (artifact.artifact_type === 'bmodel' && artifact.precision) precision.set(artifact.precision.toUpperCase(), artifact);
            }
            return (
              <Card key={version.id} size="small" style={{ marginBottom: 10 }} title={<Space><span>{version.display_name}</span><Tag>{versionLabel(version)}</Tag><Tag>export #{version.bm1688_export_id || '-'}</Tag></Space>}>
                <Space wrap>{['FP32', 'FP16', 'INT8'].map((name) => <Tag key={name} color={precision.has(name) ? 'green' : 'default'}>{name}</Tag>)}</Space>
                <Space style={{ marginTop: 8 }}>{edgeTag(version)}{publishTag(version)}</Space>
              </Card>
            );
          }) : <Empty description="暂无 BModel 制品" />}
        </Card>
      ) : stage === 'published' ? (
        <Card size="small" title="已发布模型">
          {contract.publishedVersions.length ? contract.publishedVersions.map((version) => <div key={version.id}>{version.display_name} · {versionLabel(version)} <Tag color="green">已发布</Tag></div>) : <Empty description="暂未发布" />}
        </Card>
      ) : (
        <Card size="small" title="外部导入模型"><Empty description="当前契约暂无外部导入模型" /></Card>
      )}
      <Typography.Text type="secondary" style={{ fontSize: 11 }}><ExperimentOutlined /> 自动迭代与编译历史仍保留在训练中心/编译任务中，详情字段不删除。</Typography.Text>
    </div>
  );
}

function ModelSnapshotDrawer({ version, onClose }: { version: Version | null; onClose: () => void }) {
  const bmodel = (version?.artifacts || []).filter((artifact) => artifact.artifact_type === 'bmodel');
  const precision = [...new Set(bmodel.map((artifact) => String(artifact.precision || '').toUpperCase()).filter(Boolean))];
  return (
    <Drawer title={version ? `${version.display_name} · ${versionLabel(version)}` : '模型详情'} open={Boolean(version)} onClose={onClose} width={680} destroyOnClose>
      {version && (
        <Descriptions column={1} bordered size="small">
          <Descriptions.Item label="模型">{version.display_name}</Descriptions.Item>
          <Descriptions.Item label="版本">{versionLabel(version)}</Descriptions.Item>
          <Descriptions.Item label="训练作业">{version.training_job_id ? `#${version.training_job_id}` : '-'}</Descriptions.Item>
          <Descriptions.Item label="数据集版本">{version.dataset_version_id ? `#${version.dataset_version_id}` : '-'}</Descriptions.Item>
          <Descriptions.Item label="训练状态">{statusTag(version.status)}</Descriptions.Item>
          <Descriptions.Item label="系列 / 分支">
            {String((version.manifest || {}).series_id || '-')} / {String((version.manifest || {}).variant_id || '-')}
          </Descriptions.Item>
          <Descriptions.Item label="BModel 精度">{precision.length ? precision.join(' / ') : '未生成'}</Descriptions.Item>
          <Descriptions.Item label="边端验证">{edgeTag(version)}</Descriptions.Item>
          <Descriptions.Item label="发布状态">{publishTag(version)}</Descriptions.Item>
          <Descriptions.Item label="更新时间">{version.updated_at || version.created_at || '-'}</Descriptions.Item>
          <Descriptions.Item label="训练权重">{String((version.manifest || {}).weights_path || '-')}</Descriptions.Item>
        </Descriptions>
      )}
    </Drawer>
  );
}
