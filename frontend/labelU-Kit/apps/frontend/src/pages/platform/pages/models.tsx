import {
  ArrowRightOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EyeOutlined,
  PlusOutlined,
  ReloadOutlined,
  SettingOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Descriptions,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Pagination,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import styled from 'styled-components';

import request from '@/api/request';
import ServerSearchInput from '@/components/ServerSearchInput';
import useTaskSuperuser from '@/hooks/useTaskSuperuser';
import { listData, listTotal, ModuleHeader, responseStatus, TablePanel, type Row } from '../shared';
import EdgeValidationModal from './edgeValidationModal';

const ModelCenterPanel = styled(TablePanel)`
  padding: 0;
  overflow: hidden;

  .model-center-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    flex-wrap: wrap;
    padding: 18px 20px 0;
  }

  .model-center-header .ant-space {
    gap: 6px !important;
  }

  .model-center-description {
    margin: 6px 20px 0;
    color: #64748b;
    font-size: 13px;
    line-height: 1.55;
  }

  .model-center-filter {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
    margin: 12px 20px 0 !important;
    padding: 10px 12px;
    border: 1px solid #e7edf5;
    border-radius: 6px;
    background: #f8fafc;
  }

  .model-center-filter > .ant-space {
    gap: 8px !important;
  }

  .model-center-alert {
    margin: 12px 20px 0 !important;
  }

  .model-center-table {
    margin: 12px 20px 0 !important;
  }

  .model-center-table .ant-table-thead > tr > th {
    background: #f8fafc;
    color: #475467;
    font-size: 12px;
    font-weight: 600;
  }

  .model-center-table .ant-table-tbody > tr > td {
    vertical-align: middle;
  }

  .model-center-table .ant-table-pagination.ant-pagination {
    margin: 14px 0 2px;
  }

  .model-center-expanded {
    padding: 10px 0 14px 20px !important;
    border-top: 1px solid #eef2f7;
    background: #fbfcfe;
  }

  .model-center-expanded > .ant-table-wrapper {
    margin-top: 8px !important;
  }

  @media (max-width: 720px) {
    .model-center-header {
      padding: 14px 14px 0;
    }

    .model-center-description {
      margin: 6px 14px 0;
    }

    .model-center-filter,
    .model-center-alert,
    .model-center-table {
      margin-left: 14px !important;
      margin-right: 14px !important;
    }

    .model-center-filter {
      align-items: stretch;
    }
  }
`;

type ModelArtifact = {
  id: number;
  artifact_type: string;
  relative_path: string;
  sha256: string;
  size_bytes?: number | null;
  chip_type?: string | null;
  precision?: string | null;
  status?: string | null;
  metadata?: Record<string, unknown> | null;
};
type ModelQualityEvidence = {
  id: number;
  gate_type: string;
  revision: number;
  status: string;
  evaluator?: string | null;
  reason?: string | null;
  evidence_uri?: string | null;
  evaluated_at?: string | null;
  metrics?: Record<string, unknown> | null;
};
type ModelEdgeValidationStatus = {
  validation_id?: number | null;
  export_id?: number | null;
  status?: string | null;
  job_status?: string | null;
  edge_result_status?: string | null;
  quality_gate_status?: string | null;
  reasons?: string[];
  report_summary?: string | null;
  completed_at?: string | null;
  publish_gate_status?: string | null;
  publish_gate_reason_codes?: string[];
  publish_blocked_reason?: string | null;
  training_metrics?: Record<string, unknown> | null;
  inference_metrics?: Record<string, unknown> | null;
  pt_bmodel_comparison?: Record<string, unknown> | null;
  comparison_comparable?: boolean;
  improvement_status?: 'IMPROVED' | 'REGRESSED' | 'UNCHANGED' | null;
};
type EdgeVersionComparison = {
  previous_model_version_id?: number | null;
  previous_version_no?: number | null;
  previous_validation_id?: number | null;
  current_map50?: number | null;
  previous_map50?: number | null;
  signed_delta_map50?: number | null;
  delta_ratio?: number | null;
  status?: 'IMPROVED' | 'REGRESSED' | 'UNCHANGED' | null;
  comparable?: boolean;
  baseline_state?: 'NO_PREVIOUS_VERSION' | 'PREVIOUS_EDGE_UNAVAILABLE' | 'CURRENT_EDGE_UNAVAILABLE' | 'COMPARABLE';
  reason?: string | null;
};
type ModelVersion = Row & {
  id: number;
  task_id?: number | null;
  algorithm_id?: number;
  dataset_id?: number | null;
  dataset_version_id?: number | null;
  dataset_version_no?: number | null;
  series_id?: number | null;
  series_name?: string | null;
  model_family?: string | null;
  variant_id?: number | null;
  variant_name?: string | null;
  branch_key?: string | null;
  selection_contract_sha256?: string | null;
  selection_contract?: Record<string, unknown> | null;
  inheritance_mode?: string | null;
  training_job_id?: number | null;
  parent_training_job_id?: number | null;
  training_model_weights?: string | null;
  bm1688_export_id?: number | null;
  parent_model_version_id?: number | null;
  published_catalog_id?: number | null;
  display_name: string;
  branch_version_no?: number | null;
  version_no: number;
  status: string;
  chip_type?: string | null;
  precision?: string | null;
  model_key?: string | null;
  algorithm_key?: string | null;
  quality_gate_status?: string | null;
  metrics?: Record<string, unknown> | null;
  manifest?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
  artifacts?: ModelArtifact[];
  quality_gate?: Record<string, string>;
  quality_evidence?: ModelQualityEvidence[];
  edge_validation_status?: ModelEdgeValidationStatus | null;
  edge_version_comparison?: EdgeVersionComparison | null;
  edge_version_improvement_status?: 'IMPROVED' | 'REGRESSED' | 'UNCHANGED' | null;
};
type ModelFamily = {
  family_key: string;
  display_name: string;
  task_id: number;
  algorithm_id: number;
  series_id?: number | null;
  series_name?: string | null;
  model_family?: string | null;
  latest_version_id: number;
  version_count: number;
  published_version_id?: number | null;
  updated_at?: string | null;
  versions: ModelVersion[];
};
type PublishedModel = {
  catalog_id: number;
  model_id: string;
  aicm_model_set_id?: number | null;
  display_name?: string;
  name?: string;
  status?: string;
  availability_status?: string;
  download_enabled?: boolean;
  local_package_available?: boolean;
  chip_type?: string | null;
  precision?: string | null;
  publisher_username?: string | null;
  published_at?: string | null;
  source_type?: string;
  class_names?: string[];
  class_labels?: { label_key?: string | null; label_value: string }[];
  label_group_version?: string | null;
  label_group_size?: number;
  related_model_count?: number;
  last_synced_at?: string | null;
};

type PublishedModelListEnvelope = {
  data: { models: PublishedModel[] };
  meta_data?: { total?: number };
};

function publishedModelSource(row: PublishedModel) {
  if (row.source_type === 'remote_legacy') return 'AICM历史模型';
  if (row.source_type === 'labelu_publish' || row.source_type === 'labelu_model_version') return '本平台发布';
  return '来源未记录';
}

function publishedModelLabels(row: PublishedModel) {
  if (row.class_labels?.length) return row.class_labels.map((label) => label.label_key || label.label_value);
  return row.class_names || [];
}

function publishedModelVersion(row: PublishedModel) {
  return row.label_group_version || (publishedModelLabels(row).length ? '未参与当前目录分组' : '未记录标签');
}

function publishedModelAvailability(row: PublishedModel) {
  if (row.local_package_available && row.download_enabled) return <Tag color="green">本地可下载</Tag>;
  return statusTag(row.availability_status);
}
type AicmPublishJob = {
  job_id: string;
  status?: string;
  current_step?: string | null;
  result?: {
    ok?: boolean;
    model_set_id?: number | null;
    package_path?: string | null;
  } | null;
  error_message?: string | null;
};
type ExternalImportedModel = {
  id: number;
  display_name?: string | null;
  category?: string | null;
  original_filename?: string | null;
  file_size?: number | null;
  created_at?: string | null;
  creator_email?: string | null;
  can_delete?: boolean;
  registered_model_count?: number;
};

type Bm1688ExportRow = {
  id: number;
  task_id?: number | null;
  task_name?: string | null;
  yolo_train_job_id?: number | null;
  model_version_id?: number | null;
  version_no?: number | null;
  dataset_version_id?: number | null;
  dataset_version_no?: number | null;
  algorithm_id?: number | null;
  algorithm_key?: string | null;
  status: string;
  bmodel_filenames?: string[];
  bmodel_filename?: string | null;
  onnx_filename?: string | null;
  current_verdict_label?: string | null;
  edge_validation_label?: string | null;
  edge_execution_label?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  can_recompile_bmodel?: boolean;
};

type Bm1688ExportFamily = {
  family_key: string;
  display_name?: string | null;
  task_name?: string | null;
  task_id: number;
  algorithm_id?: number | null;
  algorithm_key?: string | null;
  latest_model_version_id?: number | null;
  latest_version_no?: number | null;
  version_count: number;
  compile_task_count: number;
  latest_export_id?: number | null;
  latest_status?: string | null;
  updated_at?: string | null;
  exports: Bm1688ExportRow[];
};

// ONNX_READY is an intermediate state: ONNX has finished, but BModel outputs
// may still be compiling. Keep the family query polling until a terminal state.
const ACTIVE_COMPILE_STATUSES = new Set(['PENDING', 'PROCESSING', 'ONNX_READY']);

export function hasActiveCompileExports(value: unknown) {
  return (listData(value) as Bm1688ExportFamily[]).some((family) =>
    (family.exports || []).some((row) => ACTIVE_COMPILE_STATUSES.has(String(row.status || '').toUpperCase())),
  );
}

type ModelCenterView = 'pt' | 'compile' | 'bmodel' | 'quality-gates' | 'external' | 'published';
type ModelDetailMode = 'pt' | 'bmodel';

const REQUIRED_GATES = ['training', 'generalization', 'conversion', 'edge', 'release'];
const PT_ARTIFACT_TYPES = ['pt'];
const BM1688_PRECISIONS = ['fp32', 'fp16', 'int8'];
const QUALITY_GATE_LABELS: Record<string, string> = {
  training: '训练检查',
  generalization: '泛化检查',
  conversion: '编译检查',
  edge: '设备适配检查',
  release: '发布检查',
};

export function qualityGateLabel(value: unknown) {
  const key = String(value || '')
    .trim()
    .toLowerCase();
  return QUALITY_GATE_LABELS[key] || String(value || '-');
}

export function parseOptionalJson(value: string | undefined) {
  const text = value?.trim();
  if (!text) return undefined;
  const parsed = JSON.parse(text);
  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('JSON 必须是对象');
  return parsed as Record<string, unknown>;
}

export function modelPublishState(
  model: Pick<ModelVersion, 'status' | 'artifacts' | 'quality_gate' | 'edge_validation_status'> | null | undefined,
) {
  if (!model) return { allowed: false, reason: '请选择一个模型版本查看发布条件。' };
  const edge = model.edge_validation_status;
  const publishGate = String(edge?.publish_gate_status || '').toUpperCase();
  if (publishGate && publishGate !== 'ALLOWED' && publishGate !== 'PASS') {
    return {
      allowed: false,
      reason:
        edge?.publish_blocked_reason ||
        '边端验证已完成，但发布所需的编译、性能或稳定性证据尚未完整。',
    };
  }
  const gate = model.quality_gate || {};
  const failedGates = REQUIRED_GATES.filter((name) => String(gate[name] || 'PENDING').toUpperCase() !== 'PASS');
  if (failedGates.length)
    return { allowed: false, reason: `上线检查未完成：${failedGates.map(qualityGateLabel).join('、')}` };
  const readyBModels = new Set(
    (model?.artifacts || [])
      .filter(
        (artifact) =>
          String(artifact.status || '').toUpperCase() === 'READY' &&
          String(artifact.artifact_type || '').toLowerCase() === 'bmodel' &&
          String(artifact.chip_type || '').toLowerCase() === 'bm1688',
      )
      .map((artifact) => String(artifact.precision || '').toLowerCase()),
  );
  if (BM1688_PRECISIONS.some((precision) => !readyBModels.has(precision)))
    return { allowed: false, reason: '模型必须同时具备 READY 状态的 BM1688 FP32、FP16、INT8 三种 BModel，才能发布。' };
  if (String(model.status).toUpperCase() === 'PUBLISHED') return { allowed: false, reason: '该模型版本已经发布。' };
  return { allowed: true, reason: '编译制品和上线检查均已通过。' };
}

function hasReadyArtifact(model: Pick<ModelVersion, 'artifacts'> | null | undefined, types: string[]) {
  const targets = new Set(types.map((item) => item.toLowerCase()));
  return (model?.artifacts || []).some(
    (artifact) =>
      String(artifact.status || '').toUpperCase() === 'READY' &&
      targets.has(String(artifact.artifact_type || '').toLowerCase()),
  );
}

function gateValue(model: Pick<ModelVersion, 'quality_gate'> | null | undefined, gate: string) {
  return String(model?.quality_gate?.[gate] || 'PENDING').toUpperCase();
}

type StageState = 'done' | 'active' | 'blocked' | 'failed';
type ModelStage = {
  key: string;
  title: string;
  description: string;
  state: StageState;
  gate?: string;
};

function stageTag(state: StageState) {
  const map: Record<StageState, { color: string; label: string }> = {
    done: { color: 'green', label: '已完成' },
    active: { color: 'blue', label: '进行中' },
    blocked: { color: 'default', label: '未开始' },
    failed: { color: 'red', label: '未通过' },
  };
  return <Tag color={map[state].color}>{map[state].label}</Tag>;
}

function firstPendingGate(model: Pick<ModelVersion, 'quality_gate'> | null | undefined) {
  return REQUIRED_GATES.find((gate) => gateValue(model, gate) !== 'PASS');
}

function modelWorkflowStages(
  model:
    | Pick<ModelVersion, 'status' | 'artifacts' | 'quality_gate' | 'edge_validation_status' | 'dataset_id' | 'dataset_version_id'>
    | null
    | undefined,
): ModelStage[] {
  const hasPt = hasReadyArtifact(model, PT_ARTIFACT_TYPES);
  const hasDeployable = BM1688_PRECISIONS.every((precision) =>
    (model?.artifacts || []).some(
      (artifact) =>
        String(artifact.status || '').toUpperCase() === 'READY' &&
        String(artifact.artifact_type || '').toLowerCase() === 'bmodel' &&
        String(artifact.chip_type || '').toLowerCase() === 'bm1688' &&
        String(artifact.precision || '').toLowerCase() === precision,
    ),
  );
  const hasDataset = Boolean(model?.dataset_id || model?.dataset_version_id);
  const datasetDescription = model?.dataset_version_id
    ? `已绑定冻结数据集版本 #${model.dataset_version_id}，可复现训练数据。`
    : model?.dataset_id
    ? `已关联数据集 #${model.dataset_id}，外部模型登记不冻结样本。`
    : '先从任务与标注生成可训练数据集。';
  const training = gateValue(model, 'training');
  const generalization = gateValue(model, 'generalization');
  // This flow describes whether the files have been produced. Quality gates
  // are shown separately for the edge model and must not make a real BModel
  // look like it was never compiled.
  const trainingDone = hasPt;
  const compileDone = hasDeployable;
  const hasAnyBmodel = (model?.artifacts || []).some(
    (artifact) => String(artifact.artifact_type || '').toLowerCase() === 'bmodel',
  );
  const published = String(model?.status || '').toUpperCase() === 'PUBLISHED';
  return [
    {
      key: 'dataset',
      title: '数据集',
      description: datasetDescription,
      state: hasDataset ? 'done' : 'active',
    },
    {
      key: 'training',
      title: '训练',
      description: trainingDone ? '已生成 best.pt；训练质量请到训练中心查看。' : '启动训练后生成 best.pt 模型文件。',
      gate:
        firstPendingGate(model) === 'training' || firstPendingGate(model) === 'generalization'
          ? firstPendingGate(model)
          : undefined,
      state:
        training === 'FAIL' || generalization === 'FAIL'
          ? 'failed'
          : trainingDone
          ? 'done'
          : hasDataset
          ? 'active'
          : 'blocked',
    },
    {
      key: 'compile',
      title: '编译',
      description: published
        ? '已完成 BM1688 编译并发布。'
        : compileDone
        ? '已生成 BM1688 FP32、FP16、INT8 三种 BModel，可进入发布。'
        : hasAnyBmodel
        ? 'BM1688 正在生成剩余精度制品。'
        : '训练完成后编译为 BM1688 可运行模型。',
      gate: ['conversion', 'edge', 'release'].includes(firstPendingGate(model) || '')
        ? firstPendingGate(model)
        : undefined,
      state: compileDone || published ? 'done' : hasAnyBmodel || trainingDone ? 'active' : 'blocked',
    },
  ];
}

function modelWorkflowSummary(
  model:
    | Pick<ModelVersion, 'status' | 'artifacts' | 'quality_gate' | 'edge_validation_status' | 'dataset_id' | 'dataset_version_id'>
    | null
    | undefined,
) {
  const stages = modelWorkflowStages(model);
  const current =
    stages.find((stage) => stage.state === 'failed') ||
    stages.find((stage) => stage.state === 'active') ||
    stages[stages.length - 1];
  return {
    stages,
    current,
    actionableGate: firstPendingGate(model),
    doneCount: stages.filter((stage) => stage.state === 'done').length,
  };
}

function isGenerationComplete(
  model:
    | Pick<ModelVersion, 'status' | 'artifacts' | 'quality_gate' | 'edge_validation_status' | 'dataset_id' | 'dataset_version_id'>
    | null
    | undefined,
) {
  const summary = modelWorkflowSummary(model);
  return summary.doneCount === summary.stages.length && !summary.stages.some((stage) => stage.state === 'failed');
}

function ModelStageFlow({ model }: { model: ModelVersion }) {
  const summary = modelWorkflowSummary(model);
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 10 }}>
      {summary.stages.map((stage, index) => (
        <div
          key={stage.key}
          style={{
            border: `1px solid ${
              stage.state === 'active' ? '#1677ff' : stage.state === 'failed' ? '#ff4d4f' : '#f0f0f0'
            }`,
            borderRadius: 10,
            padding: 12,
            background: stage.state === 'done' ? '#f6ffed' : stage.state === 'active' ? '#f0f7ff' : '#fff',
            minHeight: 116,
          }}
        >
          <Space style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start' }}>
            <span style={{ color: '#8c8c8c' }}>0{index + 1}</span>
            {stageTag(stage.state)}
          </Space>
          <div style={{ fontWeight: 600, marginTop: 8 }}>{stage.title}</div>
          <div style={{ color: '#595959', fontSize: 12, marginTop: 6, lineHeight: 1.6 }}>{stage.description}</div>
        </div>
      ))}
    </div>
  );
}

export function modelStatusLabel(value: unknown) {
  const status = String(value || 'UNKNOWN').toUpperCase();
  const labels: Record<string, string> = {
    PUBLISHED: '已发布',
    COMPLETED: '已完成',
    PASS: '通过',
    READY: '已就绪',
    FAIL: '未通过',
    FAILED: '失败',
    RUNNING: '运行中',
    QUEUED: '排队中',
    CANCELLED: '已取消',
    DRAFT: '草稿',
    QUALITY_FAILED: '质量未通过',
    VALIDATED: '已验证',
    PROCESSING: '处理中',
    ONNX_READY: 'ONNX 已就绪',
    PENDING: '待处理',
    CANDIDATE: '候选（待验证）',
    ACTIVE: '活动',
    HIDDEN: '已隐藏',
    REVOKED: '已撤销',
    AVAILABLE: '可用',
    UNAVAILABLE: '不可用',
    UNKNOWN: '待确认',
  };
  return labels[status] || (status ? `待确认（${status}）` : '待确认');
}

export function modelArtifactTypeLabel(value: unknown) {
  const type = String(value || '').toLowerCase();
  const labels: Record<string, string> = {
    pt: '训练权重（PT）',
    onnx: '中间模型（ONNX）',
    bmodel: 'BM1688 模型（BModel）',
    manifest: '模型说明清单',
  };
  return labels[type] || (type ? `其他制品（${type}）` : '未标注');
}

function statusTag(value: unknown) {
  const status = String(value || 'UNKNOWN').toUpperCase();
  const color =
    status === 'PUBLISHED' || status === 'PASS' || status === 'READY' || status === 'COMPLETED'
      ? 'green'
      : status === 'FAIL' || status === 'FAILED' || status === 'QUALITY_FAILED'
      ? 'red'
      : status === 'VALIDATED' || status === 'PROCESSING' || status === 'RUNNING' || status === 'QUEUED' || status === 'ONNX_READY'
      ? 'blue'
      : 'default';
  return <Tag color={color}>{modelStatusLabel(status)}</Tag>;
}

function ptTrainingStatusTag(model: Pick<ModelVersion, 'status' | 'artifacts' | 'quality_gate'>) {
  const artifact = (model.artifacts || []).find((item) => String(item.artifact_type || '').toLowerCase() === 'pt');
  const artifactStatus = String(artifact?.status || '').toUpperCase();
  if (artifactStatus === 'READY') return <Tag color="green">已生成</Tag>;
  if (artifactStatus === 'PROCESSING' || artifactStatus === 'PENDING') return <Tag color="blue">生成中</Tag>;
  if (artifactStatus === 'FAILED') return <Tag color="red">生成失败</Tag>;
  return statusTag(model.status);
}

type ModelNextAction = {
  label: string;
  description: string;
  color: string;
  kind: 'wait' | 'compile' | 'edge' | 'publish';
};

function modelNextAction(model: ModelVersion): ModelNextAction {
  const ptReady = hasReadyArtifact(model, PT_ARTIFACT_TYPES);
  const bmodelReady = hasBm1688Artifact(model);
  if (!ptReady) {
    const artifact = (model.artifacts || []).find((item) => String(item.artifact_type || '').toLowerCase() === 'pt');
    if (String(artifact?.status || '').toUpperCase() === 'FAILED') {
      return {
        label: '检查训练失败原因',
        description: 'PT 文件生成失败，需要回到训练中心处理。',
        color: 'red',
        kind: 'wait',
      };
    }
    return {
      label: '等待训练完成',
      description: '训练结束并生成 best.pt 后才能进入 BM1688 编译。',
      color: 'default',
      kind: 'wait',
    };
  }
  if (!bmodelReady) {
    return {
      label: '进入 BM1688 编译',
      description: '使用这个 PT 生成 FP32、FP16、INT8 三种边端制品。',
      color: 'blue',
      kind: 'compile',
    };
  }
  const edgeStatus = effectiveEdgeStatus(model);
  if (edgeStatus !== 'PASS') {
    return {
      label: '完成边端验证',
      description: '在 BM1688 真机上确认模型能运行且效果可接受。',
      color: 'gold',
      kind: 'edge',
    };
  }
  const publishState = modelPublishState(model);
  if (publishState.allowed) {
    return {
      label: '可以发布',
      description: '三种 BModel 和质量检查均已满足发布条件。',
      color: 'green',
      kind: 'publish',
    };
  }
  return { label: '检查发布条件', description: publishState.reason, color: 'gold', kind: 'publish' };
}

function ptModelNextAction(model: ModelVersion): ModelNextAction {
  const ptReady = hasReadyArtifact(model, PT_ARTIFACT_TYPES);
  if (!ptReady) {
    const artifact = (model.artifacts || []).find((item) => String(item.artifact_type || '').toLowerCase() === 'pt');
    if (String(artifact?.status || '').toUpperCase() === 'FAILED') {
      return {
        label: '检查训练失败原因',
        description: 'PT 文件生成失败，需要回到训练中心处理。',
        color: 'red',
        kind: 'wait',
      };
    }
    return {
      label: '等待训练完成',
      description: '训练结束并生成 best.pt 后才能下载或进入编译。',
      color: 'default',
      kind: 'wait',
    };
  }
  if (hasBm1688Artifact(model)) {
    return {
      label: '-',
      description: '该版本已有边端制品，下一步请在 BModel 列表查看。',
      color: 'default',
      kind: 'wait',
    };
  }
  return { label: '进入 BM1688 编译', description: '使用这个 PT 生成边端模型。', color: 'blue', kind: 'compile' };
}

type TrainingMetricSummary = {
  recall: number | null;
  confidence: number | null;
  confidenceRecommended: boolean;
};

function metricObject(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

const INHERITANCE_MODE_LABELS: Record<string, string> = {
  from_scratch: '从头训练',
  fine_tune: '微调父模型',
  warm_start: '暖启动',
  parent_model: '继承父模型',
  same_series: '同系列迭代',
  new_branch: '新建分支',
};

function lineageReference(name: string | null | undefined, id: number | null | undefined, kind: string) {
  const cleanName = String(name || '').trim();
  if (cleanName) return cleanName;
  return id == null ? '未记录' : `${kind} #${id}`;
}

function selectionContractSummary(contract: Record<string, unknown> | null | undefined) {
  if (!contract) return '未记录';
  const mode = String(contract.mode || '').trim();
  const modeLabel = mode === 'label' || mode === 'label_dimension' ? '标签维度' : mode === 'task' || mode === 'task_dimension' ? '任务维度' : mode ? mode : '';
  const conditions = Array.isArray(contract.global_conditions)
    ? contract.global_conditions
        .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
        .map((item) => {
          const dimension = String(item.dimension || '').trim();
          const values = Array.isArray(item.values) ? item.values.map((value) => String(value)).filter(Boolean) : [];
          return dimension && values.length ? `${dimension}:${values.join('/')}` : '';
        })
        .filter(Boolean)
    : [];
  const selectedLabels = Array.isArray(contract.selected_label_ids) ? contract.selected_label_ids.length : 0;
  const pieces = [modeLabel, conditions.length ? `全局条件 ${conditions.join('，')}` : '', selectedLabels ? `已选 ${selectedLabels} 个标签` : ''].filter(Boolean);
  return pieces.join(' · ') || '已记录合同';
}

function metricNumber(source: Record<string, unknown> | null, keys: string[]) {
  if (!source) return null;
  for (const key of keys) {
    const value = Number(source[key]);
    if (Number.isFinite(value)) return value;
  }
  return null;
}

/**
 * PT 列表只展示训练结果。召回率优先取 best.pt 的验证集结果；
 * 置信度优先取训练/推理链路明确保存的阈值，否则沿用预标注链路的
 * mAP50-95 分档规则生成“推荐置信度”，不把精确率冒充置信度。
 */
export function modelTrainingMetrics(model: Pick<ModelVersion, 'metrics' | 'manifest'>): TrainingMetricSummary {
  const metrics = metricObject(model.metrics);
  const manifest = metricObject(model.manifest);
  const best = metricObject(metrics?.best_pt_val_metrics);
  const final = metricObject(metrics?.final_metrics);
  const test = metricObject(metrics?.test_val_metrics);
  const candidates = [best, final, test, metrics, manifest].filter((item): item is Record<string, unknown> =>
    Boolean(item),
  );
  const recall =
    candidates
      .map((item) => metricNumber(item, ['recall', 'overall_recall']))
      .find((value): value is number => value !== null) ?? null;
  const explicitConfidence = candidates
    .map((item) =>
      metricNumber(item, [
        'confidence',
        'confidence_threshold',
        'conf_threshold',
        'recommended_conf',
        'recommended_confidence',
      ]),
    )
    .find((value): value is number => value !== null);
  if (explicitConfidence !== undefined) {
    return { recall, confidence: explicitConfidence, confidenceRecommended: false };
  }

  const map50_95 = candidates
    .map((item) => metricNumber(item, ['map50_95', 'map50-95']))
    .find((value): value is number => value !== null);
  if (map50_95 === undefined) return { recall, confidence: null, confidenceRecommended: false };
  const score = Math.min(Math.max(map50_95, 0.05), 1);
  const recommended = Math.min(1, Math.ceil((score - 1e-9) / 0.05) * 0.05);
  return { recall, confidence: Number(recommended.toFixed(2)), confidenceRecommended: true };
}

function metricPercent(value: number | null) {
  if (value === null) return '未记录';
  const normalized = value <= 1 ? value * 100 : value;
  return `${normalized.toFixed(1)}%`;
}

function modelTrainingMetric(model: ModelVersion, key: 'map50_95' | 'map50' | 'precision' | 'recall') {
  const metrics = metricObject(model.metrics);
  const best = metricObject(metrics?.best_pt_val_metrics);
  const final = metricObject(metrics?.final_metrics);
  const test = metricObject(metrics?.test_val_metrics);
  const keys = key === 'map50_95' ? ['map50_95', 'map50-95'] : [key];
  return [best, final, test, metrics]
    .map((source) => metricNumber(source, keys))
    .find((value): value is number => value !== null) ?? null;
}

function modelTrainingSampleCount(model: ModelVersion) {
  const metrics = metricObject(model.metrics);
  const manifest = metricObject(model.manifest);
  const sources = [metricObject(metrics?.dataset), metricObject(manifest?.sample_counts)];
  for (const source of sources) {
    if (!source) continue;
    const counts = ['train', 'val', 'test'].map((key) => metricNumber(source, [key]));
    if (counts.some((value) => value !== null)) {
      return counts.reduce<number>((total, value) => total + (value || 0), 0);
    }
  }
  return null;
}

function modelTrainingMode(model: ModelVersion, versions: ModelVersion[]) {
  const parent = model.parent_model_version_id
    ? versions.find((item) => item.id === model.parent_model_version_id) || null
    : null;
  if (parent) return { label: `基于 ${modelVersionLabel(parent)} 迭代`, parent };
  const weights = String(model.training_model_weights || '').trim();
  if (weights.startsWith('trained:')) return { label: '继承训练（父版本未关联）', parent: null };
  if (!weights) return { label: '独立训练（未继承上一版）', parent: null };
  if (/^yolo[\w.-]*\.pt$/i.test(weights)) return { label: '基础权重训练', parent: null };
  return { label: '外部／指定权重训练', parent: null };
}

type TrainingComparisonStatus = 'improved' | 'regressed' | 'unchanged' | 'insufficient' | 'first';

type TrainingComparison = {
  previous: ModelVersion | null;
  metric: 'mAP50-95' | 'mAP50' | '精确率' | '召回率' | null;
  current: number | null;
  previousValue: number | null;
  status: TrainingComparisonStatus;
};

function modelVersionTime(model: ModelVersion) {
  const value = Date.parse(String(model.created_at || model.updated_at || ''));
  return Number.isFinite(value) ? value : 0;
}

/**
 * Prefer the explicit lineage when it is present. Older/manual training rows
 * often have no parent id, so fall back to the immediately older version in
 * the already grouped family instead of pretending there is no baseline.
 */
export function previousModelVersion(model: ModelVersion, versions: ModelVersion[]) {
  const explicitParent = model.parent_model_version_id
    ? versions.find((item) => item.id === model.parent_model_version_id)
    : undefined;
  if (explicitParent) return explicitParent;
  // A series can contain independent branches. Without an explicit parent,
  // only use the previous row from the same branch; otherwise a newer
  // night/rain branch can be incorrectly reported as the baseline for a day
  // branch. Explicit cross-branch parents remain valid and are handled above.
  const branchVersions = versions.filter((item) => sameModelBranch(model, item));
  const ordered = [...branchVersions].sort(
    (left, right) => modelVersionTime(right) - modelVersionTime(left) || right.id - left.id,
  );
  const index = ordered.findIndex((item) => item.id === model.id);
  return index >= 0 ? ordered[index + 1] || null : null;
}

export function modelTrainingComparison(model: ModelVersion, versions: ModelVersion[]): TrainingComparison {
  const previous = previousModelVersion(model, versions);
  if (!previous) {
    return { previous: null, metric: null, current: null, previousValue: null, status: 'first' };
  }
  const candidates: Array<['mAP50-95' | 'mAP50' | '精确率' | '召回率', 'map50_95' | 'map50' | 'precision' | 'recall']> = [
    ['mAP50-95', 'map50_95'],
    ['mAP50', 'map50'],
    ['精确率', 'precision'],
    ['召回率', 'recall'],
  ];
  const comparable = candidates.find(([, key]) => {
    const current = modelTrainingMetric(model, key);
    const previousValue = modelTrainingMetric(previous, key);
    return current !== null && previousValue !== null;
  });
  if (!comparable) {
    return { previous, metric: null, current: null, previousValue: null, status: 'insufficient' };
  }
  const [metric, key] = comparable;
  const current = modelTrainingMetric(model, key);
  const previousValue = modelTrainingMetric(previous, key);
  if (current === null || previousValue === null) {
    return { previous, metric, current, previousValue, status: 'insufficient' };
  }
  const normalizedCurrent = current <= 1 ? current : current / 100;
  const normalizedPrevious = previousValue <= 1 ? previousValue : previousValue / 100;
  const delta = normalizedCurrent - normalizedPrevious;
  const epsilon = 1e-9;
  return {
    previous,
    metric,
    current,
    previousValue,
    status: delta > epsilon ? 'improved' : delta < -epsilon ? 'regressed' : 'unchanged',
  };
}

function metricDeltaPoints(current: number, previous: number) {
  const normalizedCurrent = current <= 1 ? current * 100 : current;
  const normalizedPrevious = previous <= 1 ? previous * 100 : previous;
  const delta = normalizedCurrent - normalizedPrevious;
  return `${delta >= 0 ? '+' : ''}${delta.toFixed(2)} pts`;
}

function algorithmDisplayLabel(model: Pick<ModelVersion, 'algorithm_key' | 'algorithm_id'>) {
  const key = String(model.algorithm_key || '').toLowerCase();
  if (key === 'yolo_detection') return 'YOLO 目标检测';
  return model.algorithm_key || (model.algorithm_id ? `算法 #${model.algorithm_id}` : '-');
}

function gateStatusTag(value: unknown) {
  const status = String(value || 'PENDING').toUpperCase();
  const label = status === 'PASS' ? '通过' : status === 'FAIL' ? '不通过' : '待验证';
  return <Tag color={status === 'PASS' ? 'green' : status === 'FAIL' ? 'red' : 'gold'}>{label}</Tag>;
}

function hasBm1688Artifact(model: Pick<ModelVersion, 'artifacts'> | null | undefined) {
  return (model?.artifacts || []).some((artifact) => {
    const artifactType = String(artifact.artifact_type || '').toLowerCase();
    const chipType = String(artifact.chip_type || '').toLowerCase();
    return artifactType === 'bmodel' && (!chipType || chipType === 'bm1688');
  });
}

export function modelBm1688ExportId(
  model:
    | Pick<ModelVersion, 'artifacts' | 'bm1688_export_id' | 'edge_validation_status'>
    | null
    | undefined,
) {
  const directExportId = Number(model?.bm1688_export_id || model?.edge_validation_status?.export_id);
  if (Number.isInteger(directExportId) && directExportId > 0) return directExportId;
  const exportIds = new Set(
    (model?.artifacts || [])
      .filter(
        (artifact) =>
          String(artifact.status || '').toUpperCase() === 'READY' &&
          String(artifact.artifact_type || '').toLowerCase() === 'bmodel' &&
          (!artifact.chip_type || String(artifact.chip_type).toLowerCase() === 'bm1688'),
      )
      .map((artifact) => Number(artifact.metadata?.export_id))
      .filter((value) => Number.isInteger(value) && value > 0),
  );
  return exportIds.size === 1 ? Array.from(exportIds)[0] : null;
}

export function edgeTaskCenterPath(exportId?: number | null, taskId?: number | null) {
  const params = new URLSearchParams();
  const normalizedExportId = Number(exportId);
  const normalizedTaskId = Number(taskId);
  if (Number.isSafeInteger(normalizedExportId) && normalizedExportId > 0) {
    params.set('export_id', String(normalizedExportId));
  }
  if (Number.isSafeInteger(normalizedTaskId) && normalizedTaskId > 0) {
    params.set('task_id', String(normalizedTaskId));
  }
  const query = params.toString();
  return `/edge-task-center${query ? `?${query}` : ''}`;
}

function modelEdgeRuntimeLabel(model: Pick<ModelVersion, 'artifacts'> | null | undefined) {
  const bmodels = (model?.artifacts || []).filter(
    (artifact) => String(artifact.artifact_type || '').toLowerCase() === 'bmodel',
  );
  if (!bmodels.length) return '尚未编译';
  const chip = String(bmodels.find((artifact) => artifact.chip_type)?.chip_type || 'BM1688').toUpperCase();
  const precisionOrder = new Map(BM1688_PRECISIONS.map((precision, index) => [precision, index]));
  const precisions = Array.from(
    new Set(
      bmodels
        .map((artifact) =>
          String(artifact.precision || '')
            .trim()
            .toUpperCase(),
        )
        .filter(Boolean),
    ),
  ).sort((a, b) => (precisionOrder.get(a.toLowerCase()) ?? 99) - (precisionOrder.get(b.toLowerCase()) ?? 99));
  return `${chip} / ${precisions.length ? precisions.join('、') : '精度待确认'}`;
}

function modelTypeLabel(model: Pick<ModelVersion, 'artifacts'> | null | undefined) {
  const artifactTypes = new Set(
    (model?.artifacts || []).map((artifact) => String(artifact.artifact_type || '').toLowerCase()),
  );
  const hasPt = artifactTypes.has('pt');
  const hasBmodel = artifactTypes.has('bmodel');
  if (hasPt && hasBmodel) return '训练权重（PT）+ BM1688 边端模型';
  if (hasBmodel) return 'BM1688 边端模型';
  if (hasPt) return '训练权重（PT）';
  return '模型版本';
}

function artifactRuntimeLabel(artifact: Pick<ModelArtifact, 'artifact_type' | 'chip_type' | 'precision'>) {
  if (String(artifact.artifact_type || '').toLowerCase() !== 'bmodel') return '-';
  return `${artifact.chip_type || 'BM1688'} / ${
    artifact.precision ? String(artifact.precision).toUpperCase() : '精度待确认'
  }`;
}

function compileVerdictLabel(row: Bm1688ExportRow) {
  const value = row.current_verdict_label || row.edge_execution_label || row.edge_validation_label;
  if (!value) return '等待结果';
  return String(value).replace('待完成边端验证', '边端验证待完成');
}

function qualityEvidenceForGate(model: ModelVersion, gateType: string) {
  return [...(model.quality_evidence || [])]
    .filter((item) => String(item.gate_type || '').toLowerCase() === gateType.toLowerCase())
    .sort((a, b) => Number(b.revision || 0) - Number(a.revision || 0))[0];
}

function effectiveEdgeStatus(model: ModelVersion) {
  const evidence = qualityEvidenceForGate(model, 'edge');
  if (evidence?.status) return String(evidence.status).toUpperCase();
  return String(model.edge_validation_status?.status || model.quality_gate?.edge || 'PENDING').toUpperCase();
}

function qualityEvidenceMetricsText(evidence?: ModelQualityEvidence) {
  if (!evidence?.metrics || typeof evidence.metrics !== 'object') return '';
  const labels: Record<string, string> = {
    map50: 'mAP50',
    map50_95: 'mAP50-95',
    precision: '精确率',
    recall: '召回率',
    latency_ms: '延迟',
    throughput: '吞吐量',
    drop_ratio: '掉点比例',
  };
  return Object.entries(evidence.metrics)
    .filter(([, value]) => value !== null && value !== undefined && value !== '')
    .slice(0, 4)
    .map(([key, value]) => {
      const number = Number(value);
      const rendered =
        Number.isFinite(number) && key !== 'latency_ms' && key !== 'throughput' ? number.toFixed(4) : String(value);
      return `${labels[key] || key} ${rendered}`;
    })
    .join(' · ');
}

function EdgeModelQualitySummary({ model }: { model: ModelVersion }) {
  if (!hasBm1688Artifact(model)) return null;
  const projected = model.edge_validation_status;
  const comparison = projected?.pt_bmodel_comparison;
  const ptMap50 = metricNumber(projected?.training_metrics || null, ['map50']);
  const edgeMap50 = metricNumber(projected?.inference_metrics || null, ['map50']);
  const signedDelta = metricNumber(comparison || null, ['signed_delta_map50', 'delta_map50']);
  const improvementLabel =
    projected?.improvement_status === 'IMPROVED'
      ? '相对 PT 有提升'
      : projected?.improvement_status === 'REGRESSED'
      ? '相对 PT 有下降'
      : projected?.improvement_status === 'UNCHANGED'
      ? '相对 PT 基本持平'
      : '暂无可比结果';
  const improvementColor = projected?.improvement_status === 'IMPROVED' ? '#15803d' : projected?.improvement_status === 'REGRESSED' ? '#b42318' : '#667085';
  const items = [
    { key: 'generalization', label: '泛化质量', description: '独立测试集上的识别稳定性' },
    { key: 'edge', label: '边端可用性', description: 'BM1688 真机运行和效果验证' },
    { key: 'release', label: '发布就绪', description: '制品、验证和发布条件是否齐全' },
  ];
  return (
    <>
      <h3 style={{ margin: '18px 0 10px' }}>边端模型质量</h3>
      <div style={{ border: '1px solid #dbe4ee', borderRadius: 8, padding: '12px 13px', background: '#f8fafc', marginBottom: 10 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
          <strong>边端推理效果（与 PT 同批次对比）</strong>
          <span style={{ color: improvementColor, fontWeight: 600 }}>{improvementLabel}</span>
        </div>
        <div style={{ color: '#475569', fontSize: 12, marginTop: 6 }}>
          mAP50：{metricPercent(ptMap50)} → {metricPercent(edgeMap50)}
          {signedDelta !== null ? `（${signedDelta >= 0 ? '+' : ''}${(signedDelta * 100).toFixed(2)} 个百分点）` : ''}
        </div>
        <div style={{ color: '#94a3b8', fontSize: 12, marginTop: 5 }}>
          {projected?.comparison_comparable === true
            ? '同一批次、同一阈值和预处理合同，可直接比较。'
            : '缺少同批次、阈值或预处理证据，不能判定边端是否提升。'}
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 10 }}>
        {items.map((item) => {
          const publishBlocked =
            item.key === 'release' &&
            Boolean(projected?.publish_gate_status) &&
            !['ALLOWED', 'PASS'].includes(String(projected?.publish_gate_status || '').toUpperCase());
          const status = publishBlocked
            ? 'FAIL'
            : item.key === 'edge'
            ? effectiveEdgeStatus(model)
            : model.quality_gate?.[item.key] || 'PENDING';
          const evidence = qualityEvidenceForGate(model, item.key);
          const edgeProjection = item.key === 'edge' || item.key === 'release' ? projected : null;
          const reason =
            evidence?.reason ||
            (item.key === 'release' ? edgeProjection?.publish_blocked_reason : edgeProjection?.report_summary) ||
            (edgeProjection?.reasons?.length ? edgeProjection.reasons.join('、') : '') ||
            (evidence?.evaluator
              ? `评估器：${evidence.evaluator}`
              : status === 'PASS'
              ? item.key === 'generalization'
                ? '系统已根据训练作业的独立测试集结果自动确认。'
                : item.key === 'release'
                ? '系统已根据制品和各项检查结果自动确认。'
                : '系统已根据已保存的检查结果自动确认。'
              : '尚未完成该项检查。');
          const metrics = qualityEvidenceMetricsText(evidence);
          return (
            <div
              key={item.key}
              style={{
                border: '1px solid #e2e8f0',
                borderRadius: 8,
                padding: '12px 13px',
                background: '#fff',
                minHeight: 102,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                <strong style={{ color: '#0f172a', fontSize: 14 }}>{item.label}</strong>
                {gateStatusTag(status)}
              </div>
              <div style={{ color: '#64748b', fontSize: 12, lineHeight: 1.45, marginTop: 7 }}>{item.description}</div>
              {metrics && (
                <div style={{ color: '#475569', fontSize: 12, lineHeight: 1.45, marginTop: 5 }}>指标：{metrics}</div>
              )}
              <div style={{ color: '#94a3b8', fontSize: 12, lineHeight: 1.45, marginTop: 5 }}>{reason}</div>
            </div>
          );
        })}
      </div>
    </>
  );
}

function formatVersion(value: unknown) {
  const text = String(value ?? '').trim();
  return text ? (text.toLowerCase().startsWith('v') ? text : `v${text}`) : '-';
}

export function modelVersionLabel(model: Pick<ModelVersion, 'version_no' | 'branch_version_no'>) {
  const branch = model.branch_version_no;
  return branch == null ? formatVersion(model.version_no) : formatVersion(branch);
}

/**
 * A family is intentionally grouped at series + algorithm level, so it may
 * contain several branches. The API's latest_version_id is authoritative for
 * the series-wide latest row; only fall back to timestamps when an old API
 * response omitted it or returned an id outside the current page.
 */
export function latestModelFamilyVersion(family: Pick<ModelFamily, 'latest_version_id' | 'versions'>): ModelVersion | null {
  const versions = Array.isArray(family.versions) ? family.versions : [];
  const byId = versions.find((row) => Number(row.id) === Number(family.latest_version_id));
  if (byId) return byId;
  return [...versions].sort(
    (left, right) => modelVersionTime(right) - modelVersionTime(left) || Number(right.id) - Number(left.id),
  )[0] || null;
}

function modelBranchIdentity(model: Pick<ModelVersion, 'series_id' | 'variant_id' | 'branch_key'>): string | null {
  const variantId = Number(model.variant_id);
  if (Number.isInteger(variantId) && variantId > 0) return `variant:${variantId}`;
  const branchKey = String(model.branch_key || '').trim();
  if (branchKey) return `branch:${branchKey}`;
  const seriesId = Number(model.series_id);
  if (Number.isInteger(seriesId) && seriesId > 0) return `series:${seriesId}`;
  return null;
}

/** Return whether an implicit comparison may use the same branch as model. */
export function sameModelBranch(
  left: Pick<ModelVersion, 'series_id' | 'variant_id' | 'branch_key'>,
  right: Pick<ModelVersion, 'series_id' | 'variant_id' | 'branch_key'>,
): boolean {
  const leftIdentity = modelBranchIdentity(left);
  const rightIdentity = modelBranchIdentity(right);
  // Legacy rows have no lineage metadata. Preserve the old family-local
  // comparison only when both rows are legacy; a governed row must not use a
  // metadata-less row as an implicit baseline because its branch is unknown.
  return !leftIdentity && !rightIdentity ? true : leftIdentity === rightIdentity;
}
function actionError(error: unknown) {
  const payload = (error as {
    response?: { data?: { msg?: string; message?: string } };
  } | null)?.response?.data;
  if (payload?.msg) return payload.msg;
  if (payload?.message) return payload.message;
  const status = responseStatus(error);
  if (status === 403) return '当前账号无权执行此操作。';
  if (status === 409) return '模型质量状态已变化，请刷新详情后重试。';
  if (status === 400) return '发布前检查未通过，请先完成完整边端检查并刷新模型详情。';
  if (error instanceof Error && error.message) return error.message;
  return '模型发布失败，请稍后重试。';
}

function externalModelActionError(error: unknown, fallback: string) {
  const serverMessage = (error as { response?: { data?: { msg?: string } } } | null)?.response?.data?.msg;
  if (serverMessage) return serverMessage;
  if (responseStatus(error) === 403) return '当前账号无权执行此操作。';
  if (responseStatus(error) === 404) return '外部导入模型不存在或已被删除。';
  return fallback;
}

function formatFileSize(value: number | null | undefined) {
  const bytes = Number(value || 0);
  if (!bytes) return '-';
  if (bytes < 1024) return `${bytes} 字节`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

async function downloadModelArtifact(
  model: Pick<ModelVersion, 'id' | 'display_name' | 'version_no'>,
  artifactType: string,
) {
  try {
    const blob = (await request.get(`/v1/model-versions/${model.id}/artifacts/${artifactType}/download`, {
      responseType: 'blob',
    })) as unknown as Blob;
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = `${model.display_name || `模型-${model.id}`}-v${model.version_no}-${artifactType}.pt`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(objectUrl);
  } catch (error) {
    message.error(responseStatus(error) === 404 ? 'PT 制品尚未生成或文件不存在。' : 'PT 模型下载失败，请稍后重试。');
  }
}

function formatDateTime(value: string | null | undefined) {
  if (!value) return '-';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

type ExternalDataset = {
  id: number;
  name?: string;
  status?: string;
  dataset_label_contract_id?: number | null;
  dataset_label_contract_revision?: number | null;
  dataset_label_contract_status?: string | null;
  dataset_label_contract_annotation_kind?: string | null;
  dataset_label_set_signature?: string | null;
  dataset_labels?: DatasetLabelSnapshot[];
};

type ExternalAlgorithm = {
  id: number;
  name?: string;
  algorithm_key?: string;
  label_set_signature?: string | null;
  task_type?: string | null;
  training_adapter?: string | null;
  adapter_available?: boolean;
  trainable?: boolean;
  status?: string;
};

type DatasetLabelSnapshot = {
  label_key?: string;
  label_value?: string;
  annotation_kind?: string;
};

function parseDatasetLabels(value: unknown): DatasetLabelSnapshot[] {
  if (!value) return [];
  try {
    const parsed = typeof value === 'string' ? JSON.parse(value) : value;
    return Array.isArray(parsed)
      ? parsed.filter((item): item is DatasetLabelSnapshot => Boolean(item && typeof item === 'object'))
      : [];
  } catch {
    return [];
  }
}

function datasetAnnotationKind(dataset: ExternalDataset | undefined) {
  const label = parseDatasetLabels(dataset?.dataset_labels)[0];
  return (
    String(dataset?.dataset_label_contract_annotation_kind || label?.annotation_kind || '')
      .trim()
      .toLowerCase() || 'bbox'
  );
}

function datasetAnnotationKindLabel(dataset: ExternalDataset | undefined) {
  const labels: Record<string, string> = {
    bbox: '矩形框',
    rect: '矩形框',
    class_label: '分类',
    classification: '分类',
    polygon: '多边形',
    keypoint: '关键点',
    keypoints: '关键点',
  };
  const kind = datasetAnnotationKind(dataset);
  return labels[kind] || '未识别类型';
}

function datasetLabelSummary(dataset: ExternalDataset) {
  const labels = parseDatasetLabels(dataset.dataset_labels);
  const names = labels
    .map((item) => String(item.label_key || item.label_value || '').trim())
    .filter(Boolean)
    .slice(0, 4);
  const suffix = labels.length > names.length ? ` 等 ${labels.length} 个标签` : '';
  return names.length ? `${names.join('、')}${suffix}` : '标签契约未返回明细';
}

const ANNOTATION_KIND_ALGORITHM_KEY: Record<string, string> = {
  bbox: 'yolo_detection',
  rect: 'yolo_detection',
  class_label: 'classification',
  classification: 'classification',
  polygon: 'segmentation',
  keypoints: 'pose',
  keypoint: 'pose',
};

export function resolveExternalModelAlgorithm(dataset: ExternalDataset | undefined, algorithms: ExternalAlgorithm[]) {
  if (!dataset?.dataset_label_contract_id) return null;
  const usable = algorithms.filter(
    (item) =>
      String(item.status || 'ACTIVE').toUpperCase() === 'ACTIVE' &&
      item.adapter_available !== false &&
      item.trainable !== false,
  );
  const signature = String(dataset.dataset_label_set_signature || '').trim();
  const expectedAdapter = {
    bbox: 'yolo_detection',
    rect: 'yolo_detection',
    class_label: 'classification',
    classification: 'classification',
    polygon: 'segmentation',
    keypoints: 'pose',
    keypoint: 'pose',
  }[datasetAnnotationKind(dataset)];
  if (signature) {
    const exact = usable.find(
      (item) =>
        String(item.label_set_signature || '').trim() === signature &&
        (!expectedAdapter ||
          String(item.training_adapter || '')
            .trim()
            .toLowerCase() === expectedAdapter),
    );
    if (exact) return exact;
  }
  const preferredKey = ANNOTATION_KIND_ALGORITHM_KEY[datasetAnnotationKind(dataset)] || 'yolo_detection';
  return usable.find((item) => item.algorithm_key === preferredKey) || null;
}

type ExternalImportSelectOption = {
  value: number;
  label: string;
};

type ExternalImportPagedSelectProps = {
  value?: number;
  onChange?: (value: number | undefined) => void;
  onSelectionChange?: (value: number | undefined) => void;
  options: ExternalImportSelectOption[];
  total: number;
  page: number;
  pageSize: number;
  searchValue: string;
  loading?: boolean;
  disabled?: boolean;
  placeholder: string;
  ariaLabel: string;
  countLabel: string;
  onSearch: (value: string) => void;
  onPageChange: (page: number) => void;
};

/**
 * A compact remote-backed selector used by the external model registration
 * form. The footer keeps pagination inside the popup so a long catalog never
 * turns the form into a giant list, while the controlled search value lets the
 * parent preserve a query independently for each selector.
 */
function ExternalImportPagedSelect({
  value,
  onChange,
  onSelectionChange,
  options,
  total,
  page,
  pageSize,
  searchValue,
  loading = false,
  disabled = false,
  placeholder,
  ariaLabel,
  countLabel,
  onSearch,
  onPageChange,
}: ExternalImportPagedSelectProps) {
  // Form values can arrive as strings after API hydration while catalog
  // options use numeric IDs. Resolve the controlled value against the current
  // option set so rc-select renders the label instead of the raw ID.
  const resolvedValue = value === undefined
    ? undefined
    : options.find((option) => String(option.value) === String(value))?.value ?? value;

  return (
    <Select
      allowClear
      showSearch
      filterOption={false}
      value={resolvedValue}
      searchValue={searchValue}
      options={options}
      loading={loading}
      disabled={disabled}
      placeholder={placeholder}
      aria-label={ariaLabel}
      notFoundContent={loading ? <Spin size="small" /> : '暂无匹配项'}
      dropdownRender={(menu) => (
        <>
          {menu}
          <div
            onMouseDown={(event) => event.preventDefault()}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 8,
              padding: '8px 10px 2px',
              borderTop: '1px solid #f0f0f0',
            }}
          >
            <span style={{ color: '#64748b', fontSize: 12, whiteSpace: 'nowrap' }}>
              {countLabel}：{total}
            </span>
            {total > pageSize && (
              <Pagination
                size="small"
                current={page + 1}
                pageSize={pageSize}
                total={total}
                showSizeChanger={false}
                showLessItems
                onChange={(nextPage) => onPageChange(nextPage - 1)}
              />
            )}
          </div>
        </>
      )}
      onSearch={onSearch}
      onChange={(nextValue: number | string | undefined) => {
        const normalizedValue = nextValue === undefined ? undefined : Number(nextValue);
        onSelectionChange?.(normalizedValue);
        onChange?.(normalizedValue);
        // A selected option is now represented in the field itself. Clear the
        // remote query so reopening the selector starts from the first page,
        // while the parent keeps the selected row in its option set.
        if (searchValue) onSearch('');
      }}
    />
  );
}

function ExternalModelImportModal({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const SELECT_PAGE_SIZE = 20;
  const [form] = Form.useForm();
  const [importedPage, setImportedPage] = useState(0);
  const [importedSearch, setImportedSearch] = useState('');
  const [importedSearchValue, setImportedSearchValue] = useState('');
  const [selectedImportedModel, setSelectedImportedModel] = useState<ExternalImportedModel | null>(null);
  const [algorithmPage, setAlgorithmPage] = useState(0);
  const [algorithmSearch, setAlgorithmSearch] = useState('');
  const [algorithmSearchValue, setAlgorithmSearchValue] = useState('');
  const [selectedAlgorithmOption, setSelectedAlgorithmOption] = useState<ExternalAlgorithm | null>(null);
  const [datasetPage, setDatasetPage] = useState(0);
  const [datasetSearch, setDatasetSearch] = useState('');
  const [datasetSearchValue, setDatasetSearchValue] = useState('');
  const [selectedDatasetOption, setSelectedDatasetOption] = useState<ExternalDataset | null>(null);
  const importedQuery = useQuery({
    queryKey: ['external-model-import', 'weights', importedPage, importedSearch],
    queryFn: () => request.get('/v1/yolo-imported-models', {
      params: {
        page: importedPage,
        size: SELECT_PAGE_SIZE,
        ...(importedSearch ? { q: importedSearch } : {}),
      },
    }),
    enabled: open,
    staleTime: 15_000,
  });
  const algorithmsQuery = useQuery({
    queryKey: ['external-model-import', 'algorithms', algorithmPage, algorithmSearch],
    queryFn: () => request.get('/v1/algorithms', {
      params: {
        status: 'ACTIVE',
        page: algorithmPage,
        size: SELECT_PAGE_SIZE,
        ...(algorithmSearch ? { q: algorithmSearch } : {}),
      },
    }),
    enabled: open,
    staleTime: 60_000,
  });
  const datasetsQuery = useQuery({
    queryKey: ['external-model-import', 'datasets', datasetPage, datasetSearch],
    queryFn: () => request.get('/v1/datasets', {
      params: {
        page: datasetPage,
        size: SELECT_PAGE_SIZE,
        ...(datasetSearch ? { q: datasetSearch } : {}),
      },
    }),
    enabled: open,
    staleTime: 15_000,
  });
  const uploadMutation = useMutation({
    mutationFn: async (file: File) => {
      const payload = new FormData();
      payload.append('file', file);
      return (
        await request.post('/v1/yolo-imported-models', payload, { headers: { 'Content-Type': 'multipart/form-data' } })
      ).data;
    },
    onSuccess: async (response) => {
      // `request` unwraps Axios once, so the normal success value is the
      // uploaded row itself. Keep the nested forms for callers/tests that
      // still provide an Axios-like or business-envelope response.
      const uploaded = response?.id
        ? response
        : response?.data?.id
        ? response.data
        : response?.data?.data?.id
        ? response.data.data
        : null;
      const importedId = uploaded?.id;
      // Set the form value before publishing the fallback option. The watcher
      // clears an option only when the field is genuinely empty; ordering these
      // updates avoids briefly treating a successful upload as a clear action.
      if (importedId) form.setFieldValue('imported_model_id', importedId);
      if (uploaded?.id) setSelectedImportedModel(uploaded as ExternalImportedModel);
      await importedQuery.refetch();
      message.success('权重上传成功，请继续选择关联数据集');
    },
    onError: (error) => message.error(error instanceof Error ? error.message : '权重上传失败，请检查文件格式和大小'),
  });
  const mutation = useMutation({
    mutationFn: async (values: Record<string, unknown>) =>
      request.post('/v1/models/import', {
        ...values,
        imported_model_id: Number(values.imported_model_id),
        algorithm_id: Number(values.algorithm_id),
        dataset_id: Number(values.dataset_id),
        display_name: String(values.display_name || '').trim() || undefined,
      }),
    onSuccess: () => {
      message.success('外部模型已登记到模型仓库');
      form.resetFields();
      onClose();
      onSaved();
    },
    onError: (error) =>
      message.error(error instanceof Error ? error.message : '外部模型登记失败，请检查文件和账号权限'),
  });
  const importedPageItems = useMemo(
    () => listData(importedQuery.data).filter((item) => item && item.id) as ExternalImportedModel[],
    [importedQuery.data],
  );
  const algorithmsPageItems = useMemo(
    () => listData(algorithmsQuery.data).filter((item) => item && item.id) as ExternalAlgorithm[],
    [algorithmsQuery.data],
  );
  const datasetsPageItems = useMemo(
    () => listData(datasetsQuery.data).filter((item) => item && item.id) as ExternalDataset[],
    [datasetsQuery.data],
  );
  const importedTotal = listTotal(importedQuery.data, importedPageItems.length);
  const algorithmTotal = listTotal(algorithmsQuery.data, algorithmsPageItems.length);
  const datasetTotal = listTotal(datasetsQuery.data, datasetsPageItems.length);
  const importedModels = useMemo(() => {
    const byId = new Map<number, ExternalImportedModel>();
    [...importedPageItems, selectedImportedModel].forEach((item) => {
      if (item?.id) byId.set(Number(item.id), item);
    });
    return Array.from(byId.values());
  }, [importedPageItems, selectedImportedModel]);
  const algorithms = useMemo(() => {
    const byId = new Map<number, ExternalAlgorithm>();
    [...algorithmsPageItems, selectedAlgorithmOption].forEach((item) => {
      if (item?.id) byId.set(Number(item.id), item);
    });
    return Array.from(byId.values());
  }, [algorithmsPageItems, selectedAlgorithmOption]);
  const datasets = useMemo(() => {
    const byId = new Map<number, ExternalDataset>();
    [...datasetsPageItems, selectedDatasetOption].forEach((item) => {
      if (item?.id) byId.set(Number(item.id), item);
    });
    return Array.from(byId.values());
  }, [datasetsPageItems, selectedDatasetOption]);
  const importedModelId = Form.useWatch('imported_model_id', form) as number | undefined;
  const selectedDatasetId = Form.useWatch('dataset_id', form) as number | undefined;
  const selectedAlgorithmId = Form.useWatch('algorithm_id', form) as number | undefined;
  useEffect(() => {
    const selected = importedModels.find((item) => Number(item.id) === Number(importedModelId));
    if (selected && selectedImportedModel?.id !== selected.id) setSelectedImportedModel(selected);
    if (!importedModelId && selectedImportedModel) setSelectedImportedModel(null);
  }, [importedModelId, importedModels, selectedImportedModel]);
  useEffect(() => {
    const selected = datasets.find((item) => Number(item.id) === Number(selectedDatasetId));
    if (selected && selectedDatasetOption?.id !== selected.id) setSelectedDatasetOption(selected);
    if (!selectedDatasetId && selectedDatasetOption) setSelectedDatasetOption(null);
  }, [datasets, selectedDatasetId, selectedDatasetOption]);
  useEffect(() => {
    const selected = algorithms.find((item) => Number(item.id) === Number(selectedAlgorithmId));
    if (selected && selectedAlgorithmOption?.id !== selected.id) setSelectedAlgorithmOption(selected);
    if (!selectedAlgorithmId && selectedAlgorithmOption) setSelectedAlgorithmOption(null);
  }, [algorithms, selectedAlgorithmId, selectedAlgorithmOption]);
  const selectedDataset = datasets.find((item) => Number(item.id) === Number(selectedDatasetId));
  const selectedAlgorithm = algorithms.find((item) => Number(item.id) === Number(selectedAlgorithmId))
    || selectedAlgorithmOption
    || resolveExternalModelAlgorithm(selectedDataset, algorithms);
  const previousAutoDatasetId = useRef<number | undefined>();
  useEffect(() => {
    if (!selectedDatasetId) {
      previousAutoDatasetId.current = undefined;
      form.setFieldValue('algorithm_id', undefined);
      setSelectedAlgorithmOption(null);
      return;
    }
    if (previousAutoDatasetId.current === selectedDatasetId || algorithmsQuery.isFetching) return;
    const auto = resolveExternalModelAlgorithm(selectedDataset, algorithms);
    form.setFieldValue('algorithm_id', auto?.id);
    setSelectedAlgorithmOption(auto);
    previousAutoDatasetId.current = selectedDatasetId;
  }, [algorithms, algorithmsQuery.isFetching, form, selectedDataset, selectedDatasetId]);
  return (
    <Modal
      title="登记外部模型"
      open={open}
      centered
      destroyOnClose
      confirmLoading={mutation.isPending}
      onCancel={onClose}
      onOk={() =>
        form
          .validateFields()
          .then((values) => mutation.mutate(values))
          .catch(() => undefined)
      }
      okText="登记到模型仓库"
      cancelText="取消"
    >
      <Alert
        type="info"
        showIcon
        message="外部模型必须先通过权重上传接口进入平台"
        description="登记时直接关联数据集，不冻结样本；算法能力由数据集当前标签契约自动匹配，并生成已就绪的训练权重制品。"
        style={{ marginBottom: 14 }}
      />
      <Form form={form} layout="vertical">
        <Form.Item label="上传外部权重">
          <Upload
            accept=".pt,.pth"
            maxCount={1}
            showUploadList={false}
            beforeUpload={(file) => {
              uploadMutation.mutate(file as File);
              return false;
            }}
          >
            <Button icon={<UploadOutlined />} loading={uploadMutation.isPending}>
              选择 .pt / .pth 文件
            </Button>
          </Upload>
        </Form.Item>
        <Form.Item
          name="imported_model_id"
          label="外部权重"
          rules={[{ required: true, message: '请选择已上传的外部权重' }]}
        >
          <ExternalImportPagedSelect
            page={importedPage}
            pageSize={SELECT_PAGE_SIZE}
            total={importedTotal}
            searchValue={importedSearchValue}
            loading={importedQuery.isLoading || importedQuery.isFetching}
            placeholder="搜索并选择已上传权重"
            ariaLabel="搜索外部权重"
            countLabel="外部权重"
            options={importedModels.map((item) => ({
              value: item.id,
              label: `${item.display_name || item.original_filename || `模型 #${item.id}`} · #${item.id}`,
            }))}
            onSelectionChange={(id) =>
              setSelectedImportedModel(importedModels.find((item) => Number(item.id) === Number(id)) || null)
            }
            onSearch={(value) => {
              setImportedSearchValue(value);
              setImportedSearch(value.trim());
              setImportedPage(0);
            }}
            onPageChange={setImportedPage}
          />
        </Form.Item>
        <Form.Item
          name="dataset_id"
          label="关联数据集"
          extra="这里只记录模型适用的标签定义，不冻结数据集样本。"
          rules={[{ required: true, message: '请选择关联数据集' }]}
        >
          <ExternalImportPagedSelect
            page={datasetPage}
            pageSize={SELECT_PAGE_SIZE}
            total={datasetTotal}
            searchValue={datasetSearchValue}
            loading={datasetsQuery.isLoading || datasetsQuery.isFetching}
            placeholder="搜索并选择包含标签契约的数据集"
            ariaLabel="搜索关联数据集"
            countLabel="数据集"
            options={datasets.map((item) => ({
              value: item.id,
              label: `${item.name || `数据集 #${item.id}`} · ${
                item.dataset_label_contract_revision
                  ? `标签契约第 ${item.dataset_label_contract_revision} 版 · ${datasetLabelSummary(item)}`
                  : '尚未配置标签契约'
              }`,
            }))}
            onSelectionChange={(id) =>
              setSelectedDatasetOption(datasets.find((item) => Number(item.id) === Number(id)) || null)
            }
            onSearch={(value) => {
              setDatasetSearchValue(value);
              setDatasetSearch(value.trim());
              setDatasetPage(0);
            }}
            onPageChange={setDatasetPage}
          />
        </Form.Item>
        {!datasetsQuery.isLoading && datasetsQuery.isError && (
          <Alert
            type="error"
            showIcon
            style={{ marginTop: 8 }}
            message="数据集加载失败"
            description="请检查当前账号的数据集权限或网络状态。"
            action={
              <Button size="small" onClick={() => void datasetsQuery.refetch()}>
                重试
              </Button>
            }
          />
        )}
        {!datasetsQuery.isLoading && !datasetsQuery.isError && datasets.length === 0 && (
          <Alert
            type="warning"
            showIcon
            style={{ marginTop: 8 }}
            message="当前没有可关联的数据集"
            description="请先在数据集管理中创建数据集并配置标签契约，然后返回这里刷新。"
            action={
              <Space size={4}>
                <Button size="small" type="link" href="/datasets">
                  去数据集管理
                </Button>
                <Button size="small" onClick={() => void datasetsQuery.refetch()}>
                  刷新
                </Button>
              </Space>
            }
          />
        )}
        <Form.Item
          name="algorithm_id"
          label="算法能力"
          extra={selectedDataset ? `默认按 ${datasetAnnotationKindLabel(selectedDataset)} 标签契约自动匹配，可在可用能力中调整。` : '选择关联数据集后自动匹配'}
          rules={[{ required: true, message: '当前标签数据集暂无可用算法能力' }]}
        >
          <ExternalImportPagedSelect
            page={algorithmPage}
            pageSize={SELECT_PAGE_SIZE}
            total={algorithmTotal}
            searchValue={algorithmSearchValue}
            loading={algorithmsQuery.isLoading || algorithmsQuery.isFetching}
            disabled={!selectedDataset}
            placeholder={selectedDataset ? '搜索并选择算法能力' : '先选择关联数据集'}
            ariaLabel="搜索算法能力"
            countLabel="算法"
            options={algorithms
              .filter((item) => String(item.status || 'ACTIVE').toUpperCase() === 'ACTIVE' && item.adapter_available !== false && item.trainable !== false)
              .map((item) => ({
                value: item.id,
                label: `${item.name || item.algorithm_key || `算法 #${item.id}`} · ${item.algorithm_key || '未命名'}`,
              }))}
            onSelectionChange={(id) =>
              setSelectedAlgorithmOption(algorithms.find((item) => Number(item.id) === Number(id)) || null)
            }
            onSearch={(value) => {
              setAlgorithmSearchValue(value);
              setAlgorithmSearch(value.trim());
              setAlgorithmPage(0);
            }}
            onPageChange={setAlgorithmPage}
          />
        </Form.Item>
        {selectedDataset && !selectedAlgorithm && !algorithmsQuery.isFetching && (
          <Alert
            type="warning"
            showIcon
            style={{ marginTop: -8, marginBottom: 14 }}
            message="当前标签数据集没有匹配的算法能力"
            description="请先在系统管理中为该标签契约配置可训练算法，再登记外部模型。"
          />
        )}
        {selectedDataset && selectedAlgorithm && (
          <div style={{ color: '#475569', fontSize: 12, marginTop: -8, marginBottom: 14 }}>
            当前自动匹配：{selectedAlgorithm.name || selectedAlgorithm.algorithm_key || `算法 #${selectedAlgorithm.id}`} · {datasetAnnotationKindLabel(selectedDataset)}标注
          </div>
        )}
        <Form.Item name="display_name" label="模型显示名称">
          <Input maxLength={256} placeholder="留空则使用外部权重名称" />
        </Form.Item>
        <Space style={{ display: 'flex' }} size={12}>
          <Form.Item name="chip_type" label="目标芯片" style={{ flex: 1 }}>
            <Input placeholder="例如 BM1688" />
          </Form.Item>
          <Form.Item name="precision" label="精度" style={{ flex: 1 }}>
            <Select allowClear options={['fp32', 'fp16', 'int8'].map((value) => ({ value }))} />
          </Form.Item>
        </Space>
      </Form>
    </Modal>
  );
}

function QualityGateQueue({
  rows,
  loading,
  search,
  searchDraft,
  onSearchChange,
  onSearch,
  onSelect,
}: {
  rows: ModelVersion[];
  loading: boolean;
  search: string;
  searchDraft: string;
  onSearchChange: (value: string) => void;
  onSearch: (value: string) => void;
  onSelect: (model: ModelVersion) => void;
}) {
  const pendingRows = rows.filter(
    (row) => !isGenerationComplete(row) && String(row.status).toUpperCase() !== 'PUBLISHED',
  );
  return (
    <TablePanel>
      <Alert
        type="info"
        showIcon
        message="模型生成流程"
        description="这里仅查看本平台训练模型从数据集、训练到编译的生成进度；外部导入模型只用于预标注和下载，不进入编译或边端验证。训练质量在训练中心查看，边端验证和发布状态在 BModel 详情查看。"
        style={{ marginBottom: 14 }}
      />
      <Space
        wrap
        style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 14, width: '100%' }}
      >
        <ServerSearchInput
          value={searchDraft}
          onChange={onSearchChange}
          onSearch={onSearch}
          loading={loading}
          placeholder="按任务、模型名称、模型键或训练作业搜索"
          ariaLabel="搜索质量门禁模型"
          style={{ width: 320, maxWidth: '100%' }}
        />
        <span style={{ color: '#64748b', fontSize: 12 }}>
          搜索在服务端执行，结果仅保留未完成的生成流程
        </span>
      </Space>
      {pendingRows.length ? (
        <Table<ModelVersion>
          rowKey="id"
          size="small"
          loading={loading}
          scroll={{ x: 760 }}
          dataSource={pendingRows}
          pagination={false}
          columns={[
            { title: '模型', dataIndex: 'display_name', key: 'display_name', ellipsis: true },
            { title: '版本', dataIndex: 'version_no', key: 'version_no', render: formatVersion },
            {
              title: '来源训练作业',
              dataIndex: 'training_job_id',
              key: 'training_job_id',
              render: (value) => (value ? `#${value}` : '-'),
            },
            {
              title: '数据来源',
              key: 'dataset_lineage',
              render: (_, row) =>
                row.dataset_version_id
                  ? `冻结数据集版本 #${row.dataset_version_id}`
                  : row.dataset_id
                  ? `关联数据集 #${row.dataset_id}`
                  : '-',
            },
            {
              title: '当前步骤',
              key: 'stage',
              render: (_, row) => {
                const summary = modelWorkflowSummary(row);
                return (
                  <Space direction="vertical" size={2}>
                    <Space size={6}>
                      {stageTag(summary.current.state)}
                      <span>{summary.current.title}</span>
                    </Space>
                    <span style={{ color: '#8c8c8c', fontSize: 12 }}>
                      已完成 {summary.doneCount} / {summary.stages.length}
                    </span>
                  </Space>
                );
              },
            },
            {
              title: '操作',
              key: 'action',
              width: 120,
              fixed: 'right',
              render: (_, row) => (
                <Button type="link" size="small" onClick={() => onSelect(row)}>
                  查看处理
                </Button>
              ),
            },
          ]}
        />
      ) : (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={search ? '当前搜索暂无待处理的生成流程' : '暂无待处理的生成流程'}
        />
      )}
    </TablePanel>
  );
}

export function ModelLineage({
  model,
  mode = 'bmodel',
}: {
  model: Pick<
    ModelVersion,
    | 'algorithm_id'
    | 'algorithm_key'
    | 'display_name'
    | 'dataset_id'
    | 'dataset_version_id'
    | 'series_id'
    | 'series_name'
    | 'model_family'
    | 'variant_id'
    | 'variant_name'
    | 'branch_key'
    | 'selection_contract_sha256'
    | 'selection_contract'
    | 'inheritance_mode'
    | 'training_job_id'
    | 'parent_training_job_id'
    | 'parent_model_version_id'
    | 'published_catalog_id'
    | 'manifest'
  >;
  mode?: ModelDetailMode;
}) {
  const reference = (value?: number | null) => (value == null ? '-' : `#${value}`);
  if (mode === 'pt') {
    const manifest = metricObject(model.manifest);
    const datasetManifest = metricObject(manifest?.dataset_manifest);
    const splitCounts = metricObject(datasetManifest?.split_counts);
    const classNames =
      datasetManifest && Array.isArray(datasetManifest.class_names)
        ? datasetManifest.class_names.map((item) => String(item)).filter(Boolean)
        : [];
    const taskName = String(manifest?.task_name || model.display_name || '未记录训练任务');
    const splitText = splitCounts
      ? `训练 ${String(splitCounts.train ?? '-')} 张，验证 ${String(splitCounts.val ?? '-')} 张，测试 ${String(
          splitCounts.test ?? '-',
        )} 张`
      : '数据划分信息未记录';
    return (
      <>
        <h3 style={{ margin: '18px 0 4px' }}>训练来源</h3>
        <div style={{ color: '#667085', fontSize: 12, marginBottom: 10 }}>
          这里说明这个 PT 模型是用哪项任务、哪批图片和哪些标签训练出来的。
        </div>
        <Descriptions column={1} size="small" bordered>
          <Descriptions.Item label="训练任务">{taskName}</Descriptions.Item>
          <Descriptions.Item label="模型系列">
            {lineageReference(model.series_name, model.series_id, '系列')}
            {model.model_family ? ` · ${model.model_family}` : ''}
          </Descriptions.Item>
          <Descriptions.Item label="模型分支">
            {lineageReference(model.variant_name, model.variant_id, '分支')}
          </Descriptions.Item>
          <Descriptions.Item label="训练合同">
            {selectionContractSummary(model.selection_contract)}
          </Descriptions.Item>
          <Descriptions.Item label="继承方式">
            {INHERITANCE_MODE_LABELS[String(model.inheritance_mode || '').trim()] || model.inheritance_mode || '未记录'}
          </Descriptions.Item>
          <Descriptions.Item label="父模型版本">
            {reference(model.parent_model_version_id)}
            {model.parent_training_job_id != null ? ` · 父作业 #${model.parent_training_job_id}` : ''}
          </Descriptions.Item>
          <Descriptions.Item label="训练图片">{splitText}</Descriptions.Item>
          <Descriptions.Item label="训练标签">
            {classNames.length ? `${classNames.length} 类目标（标签详情请到任务与标注查看）` : '标签数量未记录'}
          </Descriptions.Item>
          <Descriptions.Item label="训练记录">本次训练已完成</Descriptions.Item>
        </Descriptions>
      </>
    );
  }
  return (
    <>
      <h3 style={{ margin: '18px 0 4px' }}>模型来源</h3>
      <div style={{ color: '#667085', fontSize: 12, marginBottom: 10 }}>
        下面信息说明这个边端模型由哪个训练版本转换而来。
      </div>
      <Descriptions column={1} size="small" bordered>
        <Descriptions.Item label="算法能力">{model.algorithm_key || reference(model.algorithm_id)}</Descriptions.Item>
        <Descriptions.Item label="模型系列">
          {lineageReference(model.series_name, model.series_id, '系列')}
          {model.model_family ? ` · ${model.model_family}` : ''}
        </Descriptions.Item>
        <Descriptions.Item label="模型分支">
          {lineageReference(model.variant_name, model.variant_id, '分支')}
        </Descriptions.Item>
        <Descriptions.Item label="分支键">
          {model.branch_key ? (
            <span title={model.branch_key} style={{ fontFamily: 'monospace' }}>
              {model.branch_key.length > 16 ? `${model.branch_key.slice(0, 16)}…` : model.branch_key}
            </span>
          ) : '未记录'}
        </Descriptions.Item>
        <Descriptions.Item label="训练合同">
          {selectionContractSummary(model.selection_contract)}
          {model.selection_contract_sha256 ? (
            <span style={{ color: '#667085', marginLeft: 6 }} title={model.selection_contract_sha256}>
              ({model.selection_contract_sha256.slice(0, 12)}…)
            </span>
          ) : null}
        </Descriptions.Item>
        <Descriptions.Item label="继承方式">
          {INHERITANCE_MODE_LABELS[String(model.inheritance_mode || '').trim()] || model.inheritance_mode || '未记录'}
        </Descriptions.Item>
        <Descriptions.Item label="关联数据集编号">{reference(model.dataset_id)}</Descriptions.Item>
        <Descriptions.Item label="数据集版本编号">{reference(model.dataset_version_id)}</Descriptions.Item>
        <Descriptions.Item label="训练作业编号">{reference(model.training_job_id)}</Descriptions.Item>
        <Descriptions.Item label="父模型版本编号">
          {reference(model.parent_model_version_id)}
          {model.parent_training_job_id != null ? ` · 父作业 #${model.parent_training_job_id}` : ''}
        </Descriptions.Item>
        <Descriptions.Item label="发布记录编号">{reference(model.published_catalog_id)}</Descriptions.Item>
      </Descriptions>
    </>
  );
}

function ModelLineageCell({ row }: { row: ModelVersion }) {
  const hasLineage = Boolean(
    row.series_id != null ||
      row.series_name ||
      row.variant_id != null ||
      row.variant_name ||
      row.parent_model_version_id != null,
  );
  if (!hasLineage) return <span style={{ color: '#98a2b3' }}>未记录系列</span>;
  return (
    <Space direction="vertical" size={0}>
      <span>{lineageReference(row.series_name, row.series_id, '系列')}</span>
      <span style={{ color: '#667085', fontSize: 12 }}>
        {lineageReference(row.variant_name, row.variant_id, '分支')}
        {row.parent_model_version_id != null ? ` · 父模型 #${row.parent_model_version_id}` : ''}
      </span>
    </Space>
  );
}

function PtTrainingSummary({ model }: { model: ModelVersion }) {
  const metrics = metricObject(model.metrics);
  const best = metricObject(metrics?.best_pt_val_metrics) || metricObject(metrics?.final_metrics);
  const dataset = metricObject(metrics?.dataset);
  const classes: unknown[] = metrics && Array.isArray(metrics.classes) ? metrics.classes : [];
  const summary = modelTrainingMetrics(model);
  const metricItems = [
    { label: '召回率', value: metricPercent(summary.recall), hint: '验证集目标漏检情况' },
    { label: '精确率', value: metricPercent(metricNumber(best, ['precision'])), hint: '验证集误检情况' },
    { label: 'mAP50-95', value: metricPercent(metricNumber(best, ['map50_95', 'map50-95'])), hint: '主要综合指标' },
    { label: 'mAP50', value: metricPercent(metricNumber(best, ['map50'])), hint: '宽松定位指标' },
  ];
  return (
    <>
      <h3 style={{ margin: '18px 0 10px' }}>训练结果</h3>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 8 }}>
        {metricItems.map((item) => (
          <div
            key={item.label}
            style={{
              minWidth: 0,
              padding: '10px 12px',
              border: '1px solid #e5e7eb',
              borderRadius: 6,
              background: '#fafafa',
            }}
          >
            <div style={{ color: '#667085', fontSize: 12 }}>{item.label}</div>
            <strong style={{ display: 'block', marginTop: 3, color: '#172033', fontSize: 18 }}>{item.value}</strong>
            <div style={{ marginTop: 2, color: '#98a2b3', fontSize: 11 }}>{item.hint}</div>
          </div>
        ))}
      </div>
      <Descriptions column={2} size="small" bordered style={{ marginTop: 10 }}>
        <Descriptions.Item label="推荐置信度">{metricPercent(summary.confidence)}</Descriptions.Item>
        <Descriptions.Item label="训练状态">
          {hasReadyArtifact(model, PT_ARTIFACT_TYPES) ? '训练完成' : '训练文件生成中'}
        </Descriptions.Item>
        <Descriptions.Item label="训练集图片">{dataset?.train == null ? '-' : String(dataset.train)}</Descriptions.Item>
        <Descriptions.Item label="验证集图片">{dataset?.val == null ? '-' : String(dataset.val)}</Descriptions.Item>
        <Descriptions.Item label="测试集图片">{dataset?.test == null ? '-' : String(dataset.test)}</Descriptions.Item>
        <Descriptions.Item label="标签数量">{classes.length || '-'}</Descriptions.Item>
      </Descriptions>
    </>
  );
}

function ModelConclusion({ model, mode }: { model: ModelVersion; mode: ModelDetailMode }) {
  const hasPt = hasReadyArtifact(model, PT_ARTIFACT_TYPES);
  const hasBmodel = mode === 'bmodel' && hasBm1688Artifact(model);
  const next = modelNextAction(model);
  const trainingMetrics = modelTrainingMetrics(model);
  const kindLabel = hasBmodel ? 'BM1688 边端模型' : '训练权重（PT）';
  const stateLabel = hasBmodel
    ? next.kind === 'publish'
      ? next.label
      : next.kind === 'edge'
      ? '已编译，等待边端验证'
      : '已生成边端制品'
    : hasPt
    ? '已生成，可下载'
    : '训练文件尚未生成';
  const stateColor =
    mode === 'bmodel' && next.kind === 'publish' && next.label === '可以发布'
      ? '#16a34a'
      : hasPt
      ? '#1677ff'
      : '#d97706';
  return (
    <section
      aria-label="模型结论"
      style={{
        marginBottom: 16,
        padding: '15px 16px',
        borderLeft: `4px solid ${stateColor}`,
        borderRadius: '0 8px 8px 0',
        background: hasPt ? '#f8fbff' : '#fffbe6',
      }}
    >
      <Space wrap size={[8, 5]}>
        <Tag color={hasBmodel ? 'purple' : 'blue'}>{kindLabel}</Tag>
        <Tag color={stateColor === '#16a34a' ? 'green' : stateColor === '#d97706' ? 'gold' : 'blue'}>{stateLabel}</Tag>
      </Space>
      <h2 style={{ margin: '8px 0 4px', color: '#172033', fontSize: 19 }}>
        {model.display_name || '未命名模型'} · {modelVersionLabel(model)}
      </h2>
      <div style={{ color: '#475569', fontSize: 13, lineHeight: 1.6 }}>
        {!hasBmodel && hasPt ? '训练权重已生成，可用于后续 BM1688 编译。' : next.description}
      </div>
      {!hasBmodel && hasPt && (
        <Space size={14} style={{ marginTop: 9 }}>
          <strong style={{ color: '#172033', fontSize: 13 }}>召回率：{metricPercent(trainingMetrics.recall)}</strong>
          <span style={{ color: '#475569', fontSize: 13 }}>
            置信度：{metricPercent(trainingMetrics.confidence)}
            {trainingMetrics.confidenceRecommended ? '（推荐阈值）' : ''}
          </span>
        </Space>
      )}
    </section>
  );
}

function ModelDetailModal({
  model,
  mode,
  onClose,
  onRefresh,
}: {
  model: ModelVersion | null;
  mode: ModelDetailMode;
  onClose: () => void;
  onRefresh: () => void;
}) {
  const navigate = useNavigate();
  const detailQuery = useQuery<ModelVersion>({
    queryKey: ['model-detail', model?.id],
    // The request interceptor already unwraps the Axios response to the API
    // envelope, so `.data` is the model payload returned by the backend.
    queryFn: async () => (await request.get(`/v1/models/${model?.id}`)).data,
    enabled: Boolean(model?.id),
    staleTime: 5_000,
  });
  const detail = detailQuery.data || model;
  const isPt = mode === 'pt';
  const publishState = modelPublishState(detail);
  const edgeExportId = modelBm1688ExportId(detail);
  const [artifactOpen, setArtifactOpen] = useState(false);
  const [publishOpen, setPublishOpen] = useState(false);
  const [edgeValidationView, setEdgeValidationView] = useState<'create' | 'result' | null>(null);
  const [artifactForm] = Form.useForm();
  const [publishForm] = Form.useForm();
  useEffect(() => {
    if (!model || artifactOpen || publishOpen || edgeValidationView !== null) return undefined;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      onClose();
    };
    document.addEventListener('keydown', closeOnEscape, true);
    return () => document.removeEventListener('keydown', closeOnEscape, true);
  }, [artifactOpen, edgeValidationView, model, onClose, publishOpen]);
  const refreshDetail = () => {
    onRefresh();
    void detailQuery.refetch();
  };
  const artifactMutation = useMutation({
    mutationFn: async (values: Record<string, unknown>) => {
      if (!detail) throw new Error('模型详情不存在');
      return (await request.post(`/v1/model-versions/${detail.id}/artifacts`, values)).data;
    },
    onSuccess: () => {
      message.success('模型制品已登记');
      setArtifactOpen(false);
      artifactForm.resetFields();
      refreshDetail();
    },
    onError: (error) => message.error(actionError(error)),
  });
  const recompileMutation = useMutation({
    mutationFn: async () => {
      if (!detail) throw new Error('模型详情不存在');
      const exportId = modelBm1688ExportId(detail);
      if (!exportId) throw new Error('缺少 BM1688 编译记录，无法重新编译');
      return (await request.post(`/v1/yolo-bm1688-exports/${exportId}/recompile`)).data;
    },
    onSuccess: () => {
      message.success('已提交重新编译任务');
      refreshDetail();
    },
    onError: (error) => message.error(actionError(error)),
  });
  const publishMutation = useMutation({
    mutationFn: async (values: Record<string, unknown>) => {
      if (!detail) throw new Error('模型详情不存在');
      const exportId = modelBm1688ExportId(detail);
      if (!exportId) throw new Error('缺少 BM1688 编译记录，无法发布');
      const environments = (await request.get('/v1/yolo-aicm-publish/environments')).data as {
        active_environment_id?: string;
        environments?: Array<{ id: string; name?: string; ready_to_publish?: boolean }>;
      };
      const activeId = String(environments.active_environment_id || '').trim();
      const active = (environments.environments || []).find((item) => item.id === activeId);
      if (!activeId || !active?.ready_to_publish) {
        throw new Error('请先在系统管理的 AICM 发布环境中配置并启用可用环境');
      }
      const started = (await request.post('/v1/yolo-aicm-publish/publish', {
        export_id: exportId,
        environment_id: activeId,
        bmodel_precision: String(values.precision || 'int8').toLowerCase(),
      })).data as AicmPublishJob;
      if (!started?.job_id) throw new Error('发布任务未返回任务编号');
      let job = started;
      for (let attempt = 0; attempt < 600; attempt += 1) {
        const status = String(job.status || '').toUpperCase();
        if (status === 'COMPLETED' || status === 'FAILED') break;
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
        job = (await request.get(`/v1/yolo-aicm-publish/publish-jobs/${started.job_id}`)).data as AicmPublishJob;
      }
      if (String(job.status || '').toUpperCase() !== 'COMPLETED' || !job.result?.ok || !job.result.model_set_id) {
        throw new Error(job.error_message || 'AICM 发布未完成，请查看发布记录');
      }
      return job;
    },
    onSuccess: () => {
      message.success('模型已发布，系统已自动取得 AICM 编号并生成平台下载记录');
      setPublishOpen(false);
      publishForm.resetFields();
      refreshDetail();
    },
    onError: (error) => message.error(actionError(error)),
  });
  const submitArtifact = async () => {
    try {
      const values = await artifactForm.validateFields();
      artifactMutation.mutate({
        ...values,
        size_bytes: values.size_bytes == null ? undefined : Number(values.size_bytes),
        metadata: parseOptionalJson(values.metadata),
      });
    } catch (error) {
      if (error instanceof SyntaxError || (error instanceof Error && error.message === 'JSON 必须是对象'))
        message.error('制品元数据必须是 JSON 对象');
    }
  };
  return (
    <Modal
      title={`模型详情${detail?.id ? ` #${detail.id}` : ''}`}
      open={Boolean(model)}
      onCancel={onClose}
      width={920}
      footer={null}
      centered
      keyboard
      destroyOnClose
      styles={{ body: { maxHeight: 'calc(100vh - 160px)', overflowY: 'auto' } }}
    >
      {detailQuery.isLoading ? (
        <div role="status" aria-live="polite">
          <Spin />
        </div>
      ) : detailQuery.error ? (
        <Alert
          type="error"
          showIcon
          message={
            responseStatus(detailQuery.error) === 403
              ? '无权限查看详情'
              : responseStatus(detailQuery.error) === 404
              ? '详情不存在'
              : '模型详情加载失败'
          }
          description={
            responseStatus(detailQuery.error) === 403
              ? '当前账号无权访问此模型。'
              : responseStatus(detailQuery.error) === 404
              ? '该模型可能已删除。'
              : actionError(detailQuery.error)
          }
          action={
            <Button size="small" icon={<ReloadOutlined />} onClick={() => void detailQuery.refetch()}>
              重试
            </Button>
          }
        />
      ) : detail ? (
        edgeValidationView !== null && !isPt ? (
          <EdgeValidationModal
            open
            embedded
            exportId={modelBm1688ExportId(detail)}
            modelName={`${detail.display_name || '未命名模型'} · ${modelVersionLabel(detail)}`}
            initialView={edgeValidationView}
            onClose={() => setEdgeValidationView(null)}
          />
        ) : (
        <>
          <ModelConclusion model={detail} mode={mode} />
          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label="模型">{detail.display_name || '-'}</Descriptions.Item>
            <Descriptions.Item label="版本">{modelVersionLabel(detail)}</Descriptions.Item>
            <Descriptions.Item label={isPt ? '训练状态' : '状态'}>
              {isPt
                ? hasReadyArtifact(detail, PT_ARTIFACT_TYPES)
                  ? '训练完成'
                  : '训练文件生成中'
                : String(detail.status || '').toUpperCase() === 'PUBLISHED'
                ? statusTag(detail.status)
                : publishState.allowed
                ? <Tag color="green">可发布</Tag>
                : <Tag title="模型已登记到仓库，但尚未完成全部训练、转换、边端和发布门禁">候选（待验证）</Tag>}
            </Descriptions.Item>
            <Descriptions.Item label="算法能力">{algorithmDisplayLabel(detail)}</Descriptions.Item>
            {!isPt && hasBm1688Artifact(detail) && (
              <Descriptions.Item label="发布资格">
                <Space size={6} wrap>
                  {publishState.allowed ? <Tag color="green">已满足发布条件</Tag> : <Tag color="gold">待验证</Tag>}
                  {!publishState.allowed && <Typography.Text type="secondary" style={{ fontSize: 12 }}>{publishState.reason}</Typography.Text>}
                </Space>
              </Descriptions.Item>
            )}
            {!isPt && hasBm1688Artifact(detail) && (
              <Descriptions.Item label="边端检查">
                {statusTag(effectiveEdgeStatus(detail))}
              </Descriptions.Item>
            )}
            <Descriptions.Item label="模型标识">{detail.model_key || '-'}</Descriptions.Item>
            <Descriptions.Item label="模型类型">{isPt ? '训练权重（PT）' : modelTypeLabel(detail)}</Descriptions.Item>
            {!isPt && hasBm1688Artifact(detail) && (
              <Descriptions.Item label="边端运行环境">{modelEdgeRuntimeLabel(detail)}</Descriptions.Item>
            )}
          </Descriptions>
          {isPt && <PtTrainingSummary model={detail} />}
          <ModelLineage model={detail} mode={mode} />
          {!isPt && <EdgeModelQualitySummary model={detail} />}
          <Alert
            style={{ marginTop: 10 }}
            type={!isPt && hasBm1688Artifact(detail) ? (publishState.allowed ? 'success' : 'warning') : 'info'}
            showIcon
            message={
              !isPt && hasBm1688Artifact(detail)
                ? publishState.reason
                : isPt && hasReadyArtifact(detail, PT_ARTIFACT_TYPES)
                ? 'PT 训练权重已生成，可下载或用于后续 BM1688 编译。'
                : '训练权重尚未生成，暂时不能下载 PT。'
            }
          />
          {!isPt && hasBm1688Artifact(detail) ? (
            <Space style={{ marginTop: 10 }} wrap>
              <Button
                loading={recompileMutation.isPending}
                onClick={() =>
                  Modal.confirm({
                    title: '重新编译 BM1688 模型',
                    centered: true,
                    okText: '确认重新编译',
                    cancelText: '取消',
                    content: '将使用当前 best.pt 重新生成并覆盖 BM1688 编译制品。旧边端验证会失效，完成后需要重新验证。是否继续？',
                    onOk: () => recompileMutation.mutateAsync(),
                  })
                }
              >
                重新编译
              </Button>
              <Button
                type="primary"
                disabled={!modelBm1688ExportId(detail)}
                onClick={() => setEdgeValidationView('create')}
                title={modelBm1688ExportId(detail) ? undefined : '缺少编译记录关联，无法创建边端检查'}
              >
                新建边端检查
              </Button>
              <Button
                disabled={!modelBm1688ExportId(detail)}
                onClick={() => setEdgeValidationView('result')}
                title={modelBm1688ExportId(detail) ? undefined : '缺少编译记录关联，无法查询边端检查'}
              >
                查看检查结果
              </Button>
              <Button
                icon={<ArrowRightOutlined />}
                aria-label="查看边端任务"
                disabled={!edgeExportId}
                onClick={() => edgeExportId && navigate(edgeTaskCenterPath(edgeExportId, detail?.task_id))}
                title={edgeExportId ? undefined : '缺少编译记录关联，无法查看边端任务'}
              >
                查看边端任务
              </Button>
              <Button icon={<SettingOutlined />} onClick={() => navigate('/admin?view=edge-devices')}>
                配置边端检查设备
              </Button>
            </Space>
          ) : null}
          <h3 style={{ margin: '18px 0 10px' }}>{isPt ? 'PT 文件' : '模型制品'}</h3>
          {(detail.artifacts || []).filter(
            (artifact) => !isPt || String(artifact.artifact_type || '').toLowerCase() === 'pt',
          ).length ? (
            <Table<ModelArtifact>
              rowKey="id"
              size="small"
              pagination={false}
              scroll={{ x: 620 }}
              dataSource={(detail.artifacts || []).filter(
                (artifact) => !isPt || String(artifact.artifact_type || '').toLowerCase() === 'pt',
              )}
              columns={[
                { title: '类型', dataIndex: 'artifact_type', key: 'artifact_type', render: modelArtifactTypeLabel },
                { title: '路径', dataIndex: 'relative_path', key: 'relative_path', ellipsis: true },
                { title: '状态', dataIndex: 'status', key: 'status', render: statusTag },
                {
                  title: '运行环境',
                  key: 'runtime',
                  render: (_, row) => artifactRuntimeLabel(row),
                },
                { title: 'SHA256', dataIndex: 'sha256', key: 'sha256', ellipsis: true },
              ]}
            />
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无模型制品" />
          )}
          <Space style={{ marginTop: 18 }}>
            <Button icon={<ReloadOutlined />} onClick={refreshDetail}>
              刷新详情
            </Button>
            <Button
              icon={<DownloadOutlined />}
              disabled={!hasReadyArtifact(detail, PT_ARTIFACT_TYPES)}
              onClick={() => detail && void downloadModelArtifact(detail, 'pt')}
            >
              下载 PT
            </Button>
            {!isPt && (
              <Button
                type="primary"
                disabled={!publishState.allowed || publishMutation.isPending}
                loading={publishMutation.isPending}
                onClick={() => setPublishOpen(true)}
              >
                发布模型
              </Button>
            )}
            {!isPt && (
              <Button disabled={!detail || detail.status === 'PUBLISHED'} onClick={() => setArtifactOpen(true)}>
                登记制品
              </Button>
            )}
          </Space>
          <Modal
            centered
            title="登记模型制品"
            open={artifactOpen}
            confirmLoading={artifactMutation.isPending}
            destroyOnClose
            onCancel={() => setArtifactOpen(false)}
            onOk={() => void submitArtifact()}
            okText="登记"
            cancelText="取消"
          >
            <Form form={artifactForm} layout="vertical" initialValues={{ artifact_type: 'pt' }}>
              <Form.Item name="artifact_type" label="制品类型" rules={[{ required: true }]}>
                <Select
                  options={['pt', 'onnx', 'bmodel', 'manifest'].map((value) => ({
                    value,
                    label:
                      value === 'pt'
                        ? '训练权重 PT'
                        : value === 'onnx'
                        ? '中间模型 ONNX'
                        : value === 'bmodel'
                        ? 'BM1688 BModel'
                        : '模型说明清单',
                  }))}
                />
              </Form.Item>
              <Form.Item
                name="relative_path"
                label="相对路径"
                rules={[{ required: true, message: '请输入安全的相对路径' }]}
              >
                <Input placeholder="artifacts/model.onnx" />
              </Form.Item>
              <Form.Item
                name="sha256"
                label="SHA256"
                rules={[{ required: true, len: 64, message: '请输入 64 位 SHA256' }]}
              >
                <Input />
              </Form.Item>
              <Space style={{ display: 'flex' }} size={12}>
                <Form.Item name="size_bytes" label="大小（字节）" style={{ flex: 1 }}>
                  <InputNumber min={0} precision={0} style={{ width: '100%' }} />
                </Form.Item>
                <Form.Item name="chip_type" label="芯片" style={{ flex: 1 }}>
                  <Input placeholder="例如：BM1688" />
                </Form.Item>
                <Form.Item name="precision" label="精度" style={{ flex: 1 }}>
                  <Input placeholder="例如：INT8" />
                </Form.Item>
              </Space>
              <Form.Item name="metadata" label="元数据 JSON">
                <Input.TextArea rows={3} placeholder='例如：{"source":"training-job-42"}' />
              </Form.Item>
            </Form>
          </Modal>
          <Modal
            centered
            title="发布模型到 AICM"
            open={publishOpen}
            confirmLoading={publishMutation.isPending}
            destroyOnClose
            onCancel={() => setPublishOpen(false)}
            onOk={() =>
              publishForm
                .validateFields()
                .then((values) => publishMutation.mutate(values))
                .catch(() => undefined)
            }
            okText="确认发布"
            cancelText="取消"
          >
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message="系统将自动上传并取得 AICM 模型编号"
              description="系统会使用已配置的当前发布环境，发布选定精度的 BModel；编号、发布记录和平台下载包由系统自动保存。"
            />
            <Form form={publishForm} layout="vertical">
              <Form.Item
                name="precision"
                label="发布精度"
                initialValue="int8"
                rules={[{ required: true, message: '请选择发布精度' }]}
              >
                <Select
                  options={BM1688_PRECISIONS.map((precision) => ({
                    value: precision,
                    label: precision === 'int8' ? 'INT8（推荐边端部署）' : precision.toUpperCase(),
                  }))}
                />
              </Form.Item>
            </Form>
          </Modal>
        </>
        )
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无模型详情" />
      )}
    </Modal>
  );
}

export default function ModelsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { isTaskSuperuser } = useTaskSuperuser();
  const [searchParams] = useSearchParams();
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(20);
  const [externalPage, setExternalPage] = useState(0);
  const [externalSize, setExternalSize] = useState(20);
  const [publishedPage, setPublishedPage] = useState(0);
  const [publishedSize, setPublishedSize] = useState(20);
  const [compileSearchInput, setCompileSearchInput] = useState('');
  const [compileTaskName, setCompileTaskName] = useState('');
  const [compileStatus, setCompileStatus] = useState('');
  // Keep free-form search text local until the user explicitly submits it.
  // The committed values below are the only values that participate in query
  // keys, so typing does not issue one request per character.
  const [modelSearch, setModelSearch] = useState('');
  const [modelSearchDraft, setModelSearchDraft] = useState('');
  const [qualityGateSearchInput, setQualityGateSearchInput] = useState('');
  const [qualityGateSearch, setQualityGateSearch] = useState('');
  const [externalSearch, setExternalSearch] = useState('');
  const [externalSearchDraft, setExternalSearchDraft] = useState('');
  const [publishedSearch, setPublishedSearch] = useState('');
  const [publishedSearchDraft, setPublishedSearchDraft] = useState('');
  const [publishedSource, setPublishedSource] = useState('');
  const [selectedPublished, setSelectedPublished] = useState<PublishedModel | null>(null);
  const [selected, setSelected] = useState<ModelVersion | null>(null);
  const [selectedDetailMode, setSelectedDetailMode] = useState<ModelDetailMode>('pt');
  const [externalImportOpen, setExternalImportOpen] = useState(false);
  const [expandedFamilyKeys, setExpandedFamilyKeys] = useState<string[]>([]);
  const [expandedCompileFamilyKeys, setExpandedCompileFamilyKeys] = useState<string[]>([]);
  const requestedView = searchParams.get('view');
  const linkedTrainingJobId = Number(searchParams.get('training_job_id')) || undefined;
  const linkedExportId = Number(searchParams.get('export_id')) || undefined;
  const linkedModelId = Number(searchParams.get('model_id')) || undefined;
  const view: ModelCenterView =
    requestedView === 'compile' ||
    requestedView === 'bmodel' ||
    requestedView === 'quality-gates' ||
    requestedView === 'external' ||
    requestedView === 'published'
      ? requestedView
      : 'pt';
  const groupedView = view === 'pt' || view === 'bmodel';
  const activeModelSearch = view === 'quality-gates' ? qualityGateSearch : modelSearch;
  const searchViewRef = useRef<ModelCenterView | null>(null);
  useEffect(() => {
    const query = String(searchParams.get('q') || '').trim();
    const viewChanged = searchViewRef.current !== view;
    if (query && (searchViewRef.current === null || viewChanged)) {
      if (view === 'quality-gates') {
        setQualityGateSearchInput(query);
        setQualityGateSearch(query);
      } else if (view === 'compile') {
        setCompileSearchInput(query);
        setCompileTaskName(query);
      } else if (view === 'external') {
        setExternalSearchDraft(query);
        setExternalSearch(query);
      } else if (view === 'published') {
        setPublishedSearchDraft(query);
        setPublishedSearch(query);
      } else {
        setModelSearchDraft(query);
        setModelSearch(query);
      }
    }
    searchViewRef.current = view;
  }, [searchParams, view]);
  const updateSearchQuery = (value: string) => {
    const params = new URLSearchParams(searchParams);
    const normalized = String(value || '').trim();
    if (normalized) params.set('q', normalized);
    else params.delete('q');
    navigate(
      { pathname: '/models', search: params.toString() ? `?${params.toString()}` : '' },
      { replace: true },
    );
  };
  const selectModel = (model: ModelVersion, mode: ModelDetailMode = view === 'bmodel' ? 'bmodel' : 'pt') => {
    setSelectedDetailMode(mode);
    setSelected(model);
  };
  const query = useQuery({
    queryKey: ['models', view, page, view === 'quality-gates' ? 100 : size, activeModelSearch, linkedModelId, linkedExportId, linkedTrainingJobId],
    queryFn: async () => {
      const params: Record<string, number | string> = {
        page: view === 'quality-gates' ? 0 : page,
        size: view === 'quality-gates' ? 100 : size,
      };
      // 外部导入模型只用于平台内预标注和下载，不进入本平台的
      // BM1688 编译、边端验证和发布流程。因此 PT 列表和待处理流程
      // 都只读取本平台训练生成的模型版本。
      if (view === 'quality-gates') {
        params.source = 'training';
        if (qualityGateSearch.trim()) params.q = qualityGateSearch.trim();
      }
      if (groupedView) {
        if (modelSearch.trim()) params.q = modelSearch.trim();
        if (view === 'bmodel') params.artifact_type = 'bmodel';
        if (linkedModelId) params.model_id = linkedModelId;
        else if (linkedExportId) params.export_id = linkedExportId;
        else if (linkedTrainingJobId) params.training_job_id = linkedTrainingJobId;
      }
      // The response interceptor already removes the Axios wrapper. Keep the
      // business envelope so family pagination can read meta_data.total.
      return request.get(groupedView ? '/v1/models/families' : '/v1/models', { params });
    },
    enabled: view === 'pt' || view === 'bmodel' || view === 'quality-gates',
    staleTime: 15_000,
  });
  const compileQuery = useQuery({
    queryKey: [
      'bm1688-export-families',
      page,
      size,
      linkedExportId,
      linkedTrainingJobId,
      compileTaskName,
      compileStatus,
    ],
    // Keep the unwrapped business envelope; taking `.data` here would discard
    // meta_data.total and make every server-side page look like the last page.
    queryFn: () =>
      request.get('/v1/yolo-bm1688-exports/families', {
        params: {
          page,
          size,
          export_id: linkedExportId,
          yolo_train_job_id: linkedTrainingJobId,
          // `q` is the unified search contract. Keep the historical
          // `task_name` alias populated while a term is active so older
          // gateways and bookmarked requests continue to filter correctly.
          task_name: compileTaskName || undefined,
          ...(compileTaskName ? { q: compileTaskName } : {}),
          export_status: compileStatus || undefined,
        },
      }),
    enabled: view === 'compile',
    staleTime: 10_000,
    refetchInterval: (current) => (hasActiveCompileExports(current.state.data) ? 5_000 : false),
  });
  const externalQuery = useQuery({
    queryKey: ['external-models', externalPage, externalSize, externalSearch],
    queryFn: async () =>
      request.get('/v1/yolo-imported-models', { params: {
        page: externalPage,
        size: externalSize,
        ...(externalSearch.trim() ? { q: externalSearch.trim() } : {}),
      } }),
    enabled: view === 'external',
    staleTime: 15_000,
  });
  const publishedQuery = useQuery({
    queryKey: ['published-models', publishedPage, publishedSize, publishedSearch, publishedSource, linkedExportId, linkedTrainingJobId],
    // request 的响应拦截器已移除 Axios 外层；保留分页信息所在的完整业务响应。
    queryFn: () => request.get<PublishedModelListEnvelope, PublishedModelListEnvelope>('/v1/published-models', { params: {
      page: publishedPage,
      size: publishedSize,
      q: publishedSearch || undefined,
      source_type: publishedSource || undefined,
      export_id: linkedExportId,
      yolo_train_job_id: linkedTrainingJobId,
    } }),
    enabled: view === 'published',
    // 发布完成后，用户通常会立即切到此页查看；不能继续沿用之前的空列表缓存。
    staleTime: 0,
    refetchOnMount: 'always',
  });
  const syncPublishedMutation = useMutation({
    mutationFn: async () => (await request.post('/v1/published-models/sync', { include_remote_models: true })).data,
    onSuccess: async (payload) => {
      const result = (payload as { data?: Record<string, number> })?.data || payload as Record<string, number>;
      message.success(`AICM同步完成：读取 ${Number(result.remote_scanned || 0)} 个模型，新增 ${Number(result.created || 0)} 条，更新 ${Number(result.updated || 0)} 条`);
      setPublishedPage(0);
      await queryClient.invalidateQueries({ queryKey: ['published-models'] });
    },
    onError: (error) => message.error(externalModelActionError(error, 'AICM模型同步失败，请检查发布配置和网络连接后重试。')),
  });
  const payloadRows = useMemo(() => listData(query.data), [query.data]);
  const familyRows = useMemo(
    () => (groupedView ? (payloadRows as ModelFamily[]) : []),
    [groupedView, payloadRows],
  );
  const rows = useMemo(
    () => (groupedView ? familyRows.flatMap((family) => family.versions) : (payloadRows as ModelVersion[])),
    [familyRows, groupedView, payloadRows],
  );
  const compileFamilyRows = useMemo(
    () => listData(compileQuery.data) as Bm1688ExportFamily[],
    [compileQuery.data],
  );
  const externalRows = useMemo(() => listData(externalQuery.data) as ExternalImportedModel[], [externalQuery.data]);
  const publishedRows = useMemo<PublishedModel[]>(() => {
    const payload = publishedQuery.data as any;
    if (Array.isArray(payload)) return payload;
    if (Array.isArray(payload?.data)) return payload.data;
    if (Array.isArray(payload?.data?.models)) return payload.data.models;
    return Array.isArray(payload?.models) ? payload.models : [];
  }, [publishedQuery.data]);
  const publishedTotal = listTotal(publishedQuery.data, publishedRows.length);
  const confirmRecompile = (row: Bm1688ExportRow) => {
    Modal.confirm({
      title: '重新编译 BM1688 模型',
      centered: true,
      okText: '确认重新编译',
      cancelText: '取消',
      content: '系统将使用当前 best.pt 重新生成 ONNX、FP32、FP16、INT8 BModel，并覆盖本次编译制品。已有自动编译完成后，手动结果会接管；旧边端验证需要重新执行。是否继续？',
      onOk: async () => {
        try {
          await request.post(`/v1/yolo-bm1688-exports/${row.id}/recompile`);
          message.success('已提交重新编译任务');
          if (groupedView) {
            await query.refetch();
          } else {
            await compileQuery.refetch();
          }
        } catch (error) {
          message.error(actionError(error));
          throw error;
        }
      },
    });
  };
  const total = listTotal(query.data, groupedView ? familyRows.length : rows.length);
  const externalTotal = listTotal(externalQuery.data, externalRows.length);
  const previousModelById = useMemo(() => {
    const result = new Map<number, TrainingComparison>();
    for (const family of familyRows) {
      for (const model of family.versions || []) {
        result.set(model.id, modelTrainingComparison(model, family.versions || []));
      }
    }
    return result;
  }, [familyRows]);
  useEffect(() => {
    const availableKeys = new Set(familyRows.map((family) => family.family_key));
    const focusedFamily = linkedModelId
      ? familyRows.find((family) => family.versions.some((row) => row.id === linkedModelId))
      : linkedExportId
      ? familyRows.find((family) =>
          family.versions.some((row) => row.bm1688_export_id === linkedExportId || modelBm1688ExportId(row) === linkedExportId),
        )
      : linkedTrainingJobId
      ? familyRows.find((family) => family.versions.some((row) => row.training_job_id === linkedTrainingJobId))
      : undefined;
    setExpandedFamilyKeys((current) => {
      const next = current.filter((key) => availableKeys.has(key));
      if (focusedFamily && !next.includes(focusedFamily.family_key)) next.push(focusedFamily.family_key);
      return next.length === current.length && next.every((key, index) => key === current[index]) ? current : next;
    });
  }, [familyRows, linkedExportId, linkedModelId, linkedTrainingJobId]);
  useEffect(() => {
    const availableKeys = new Set(compileFamilyRows.map((family) => family.family_key));
    const focusedFamily = linkedExportId
      ? compileFamilyRows.find((family) => (family.exports || []).some((row) => row.id === linkedExportId))
      : linkedTrainingJobId
      ? compileFamilyRows.find((family) =>
          (family.exports || []).some((row) => row.yolo_train_job_id === linkedTrainingJobId),
        )
      : undefined;
    setExpandedCompileFamilyKeys((current) => {
      const next = current.filter((key) => availableKeys.has(key));
      if (focusedFamily && !next.includes(focusedFamily.family_key)) next.push(focusedFamily.family_key);
      return next.length === current.length && next.every((key, index) => key === current[index]) ? current : next;
    });
  }, [compileFamilyRows, linkedExportId, linkedTrainingJobId]);
  useEffect(() => {
    const raw = searchParams.get('model_id');
    const modelId = Number(raw);
    if (!modelId) return;
    const listed = rows.find((item) => Number(item.id) === modelId);
    if (listed) {
      selectModel(listed);
      return;
    }
    // A direct link may target a model outside the current page. Fetch the
    // detail once so registration can always land on the exact version.
    void request
      .get(`/v1/models/${modelId}`)
      .then((response) => {
        const detail = response.data as ModelVersion;
        if (detail) selectModel(detail);
      })
      .catch(() => undefined);
  }, [rows, searchParams, view]);
  const columns: ColumnsType<ModelVersion> = [
    {
      title: '版本',
      key: 'version_no',
      width: 100,
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <strong>{modelVersionLabel(row)}</strong>
          <span style={{ color: '#98a2b3', fontSize: 12 }}>
            {row.training_job_id ? `来源作业 #${row.training_job_id}` : '外部登记'}
          </span>
        </Space>
      ),
    },
    {
      title: '系列 / 分支',
      key: 'model_lineage',
      width: 190,
      render: (_, row) => <ModelLineageCell row={row} />,
    },
    {
      title: '训练方式',
      key: 'training_mode',
      width: 180,
      render: (_, row) => modelTrainingMode(row, rows).label,
    },
    {
      title: '数据快照',
      key: 'dataset_snapshot',
      width: 180,
      render: (_, row) => {
        const sampleCount = modelTrainingSampleCount(row);
        return (
          <Space direction="vertical" size={0}>
            <span>
              {row.dataset_version_no
                ? `数据快照 ${formatVersion(row.dataset_version_no)}`
                : row.dataset_version_id
                ? `数据快照 #${row.dataset_version_id}`
                : '未关联数据快照'}
            </span>
            <span style={{ color: '#667085', fontSize: 12 }}>
              {sampleCount === null ? '样本数未记录' : `${sampleCount} 张样本`}
            </span>
          </Space>
        );
      },
    },
    {
      title: '训练效果',
      key: 'training_metrics',
      width: 250,
      render: (_, row) => {
        const comparison = previousModelById.get(row.id);
        const current = comparison?.metric ? comparison.current : modelTrainingMetric(row, 'map50_95');
        const previous = comparison?.metric ? comparison.previousValue : null;
        const statusLabel: Record<TrainingComparisonStatus, string> = {
          improved: '有提升',
          regressed: '有下降',
          unchanged: '基本持平',
          insufficient: '指标不足',
          first: '首个版本',
        };
        const statusColor: Record<TrainingComparisonStatus, string> = {
          improved: 'green',
          regressed: 'red',
          unchanged: 'blue',
          insufficient: 'default',
          first: 'default',
        };
        return (
          <Space direction="vertical" size={0}>
            <Space size={6} wrap>
              <span>
                {previous !== null && current !== null
                  ? `${metricPercent(previous)} → ${metricPercent(current)}`
                  : `${comparison?.metric || 'mAP50-95'}：${metricPercent(current)}`}
              </span>
              {comparison && <Tag color={statusColor[comparison.status]}>{statusLabel[comparison.status]}</Tag>}
            </Space>
            <span style={{ color: '#667085', fontSize: 12 }}>
              {comparison?.status === 'insufficient'
                ? `较 ${formatVersion(comparison.previous?.version_no)} 暂无可比指标`
                : previous !== null && current !== null && comparison?.previous
                ? `较 ${formatVersion(comparison.previous.version_no)} ${metricDeltaPoints(current, previous)}`
                : comparison?.status === 'first'
                ? '没有上一版可对比'
                : 'best.pt 验证集结果'}
            </span>
          </Space>
        );
      },
    },
    { title: '当前状态', key: 'training_status', width: 150, render: (_, row) => ptTrainingStatusTag(row) },
    {
      title: '完成时间',
      key: 'created_at',
      width: 170,
      render: (_, row) => formatDateTime(row.created_at),
    },
    {
      title: '操作',
      key: 'actions',
      width: 220,
      fixed: 'right',
      render: (_, row) => {
        const next = ptModelNextAction(row);
        return (
          <Space size={2}>
            {next.kind === 'compile' && (
              <Button
                type="link"
                size="small"
                icon={<ArrowRightOutlined />}
                onClick={() => row.bm1688_export_id
                  ? navigate(`/models?view=compile&export_id=${row.bm1688_export_id}`)
                  : navigate(`/models?view=compile&training_job_id=${row.training_job_id || ''}`)}
              >
                {row.bm1688_export_id ? '查看编译任务' : '进行编译任务'}
              </Button>
            )}
            {next.kind !== 'compile' && next.kind !== 'wait' && (
              <Button type="link" size="small" onClick={() => selectModel(row, 'pt')}>
                去处理
              </Button>
            )}
            <Button
              type="link"
              size="small"
              icon={<DownloadOutlined />}
              disabled={!hasReadyArtifact(row, PT_ARTIFACT_TYPES)}
              onClick={() => void downloadModelArtifact(row, 'pt')}
            >
              下载 PT
            </Button>
            <Button
              type="link"
              size="small"
              icon={<EyeOutlined />}
              aria-label="查看详情"
              onClick={() => selectModel(row, 'pt')}
            >
              详情
            </Button>
          </Space>
        );
      },
    },
  ];
  const renderBmodelEdgeStatus = (row: ModelVersion) => {
    const edge = qualityEvidenceForGate(row, 'edge');
    const status = String(edge?.status || row.edge_validation_status?.status || 'PENDING').toUpperCase();
    const stale = String(row.edge_validation_status?.job_status || '').toUpperCase() === 'STALE';
    const label = stale ? '需重新验证' : status === 'PASS' ? '已验证' : status === 'FAIL' ? '未通过' : status === 'PROCESSING' ? '验证中' : '待验证';
    return (
      <Tag color={stale ? 'orange' : status === 'PASS' ? 'green' : status === 'FAIL' ? 'red' : status === 'PROCESSING' ? 'blue' : 'gold'}>{label}</Tag>
    );
  };
  const renderBmodelPublishStatus = (row: ModelVersion) => {
    const state = modelPublishState(row);
    const published = String(row.status || '').toUpperCase() === 'PUBLISHED' || row.published_catalog_id != null;
    const edgeFailed = String(row.edge_validation_status?.status || '').toUpperCase() === 'FAIL';
    const publishBlocked = Boolean(
      row.edge_validation_status?.publish_gate_status &&
        !['ALLOWED', 'PASS'].includes(String(row.edge_validation_status.publish_gate_status).toUpperCase()),
    );
    if (published) return <Tag color="green">已发布</Tag>;
    if (state.allowed) return <Tag color="green">可发布</Tag>;
    const publishReason = row.edge_validation_status?.publish_blocked_reason || undefined;
    if (edgeFailed || publishBlocked || String(row.status || '').toUpperCase() === 'QUALITY_FAILED') {
      return <Tag color="red" title={publishReason}>暂不可发布</Tag>;
    }
    return <Tag color="gold" title={publishReason}>待完善证据</Tag>;
  };
  const renderEdgeVersionTrend = (row: ModelVersion) => {
    const comparison = row.edge_version_comparison;
    if (!comparison) {
      return row.parent_model_version_id != null ? (
        <Tag color="orange">上一版边端未完成验证</Tag>
      ) : (
        <Tag>首个边端版本</Tag>
      );
    }
    if (comparison.baseline_state === 'NO_PREVIOUS_VERSION' || comparison.previous_model_version_id == null) {
      return <Tag>首个边端版本</Tag>;
    }
    if (comparison.baseline_state === 'PREVIOUS_EDGE_UNAVAILABLE') {
      return <Tag color="orange" title={comparison.reason || undefined}>上一版边端未完成验证</Tag>;
    }
    if (comparison.baseline_state === 'CURRENT_EDGE_UNAVAILABLE') {
      return <Tag color="default" title={comparison.reason || undefined}>待当前版本边端验证</Tag>;
    }
    if (comparison.comparable !== true || !comparison.status) {
      return <Tag color="default" title={comparison.reason || undefined}>待上一版边端验证</Tag>;
    }
    const current = Number(comparison.current_map50);
    const previous = Number(comparison.previous_map50);
    const points = Number.isFinite(current) && Number.isFinite(previous) ? metricDeltaPoints(current, previous) : '';
    if (comparison.status === 'IMPROVED') {
      return <Tag color="green" title={comparison.reason || undefined}>较上一版边端提升 {points}</Tag>;
    }
    if (comparison.status === 'REGRESSED') {
      return <Tag color="red" title={comparison.reason || undefined}>较上一版边端下降 {points}</Tag>;
    }
    return <Tag color="blue" title={comparison.reason || undefined}>较上一版边端持平</Tag>;
  };
  const bmodelColumns: ColumnsType<ModelVersion> = [
    {
      title: '模型',
      key: 'display_name',
      width: 280,
      ellipsis: true,
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <strong>{row.display_name}</strong>
          <span style={{ color: '#667085', fontSize: 12 }}>分支版本 {modelVersionLabel(row)}</span>
        </Space>
      ),
    },
    {
      title: '系列 / 分支',
      key: 'model_lineage',
      width: 190,
      render: (_, row) => <ModelLineageCell row={row} />,
    },
    {
      title: 'BModel 制品',
      key: 'artifacts',
      width: 220,
      render: (_, row) => {
        const artifacts = (row.artifacts || []).filter(
          (artifact) => String(artifact.artifact_type || '').toLowerCase() === 'bmodel',
        );
        return (
          <Space wrap size={[4, 4]}>
            {artifacts.map((artifact) => (
              <Tag key={artifact.id} color={artifact.status === 'READY' ? 'green' : 'gold'}>
                {String(artifact.precision || '未知').toUpperCase()}
              </Tag>
            ))}
            {!artifacts.length && <Tag>未生成</Tag>}
          </Space>
        );
      },
    },
    { title: '边端状态', key: 'edge', width: 130, render: (_, row) => renderBmodelEdgeStatus(row) },
    { title: '边端版本效果', key: 'edge_version_trend', width: 190, render: (_, row) => renderEdgeVersionTrend(row) },
    { title: '发布状态', key: 'release', width: 130, render: (_, row) => renderBmodelPublishStatus(row) },
    { title: '更新时间', dataIndex: 'updated_at', key: 'updated_at', width: 180, render: formatDateTime },
    {
      title: '操作',
      key: 'actions',
      width: 330,
      fixed: 'right',
      render: (_, row) => {
        const edgeExportId = modelBm1688ExportId(row);
        return (
        <Space size={0} wrap>
          <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => selectModel(row, 'bmodel')}>
            详情
          </Button>
          <Button
            type="link"
            size="small"
            icon={<ArrowRightOutlined />}
            aria-label="查看边端任务"
            disabled={!edgeExportId}
            onClick={() => edgeExportId && navigate(edgeTaskCenterPath(edgeExportId, row.task_id))}
            title={edgeExportId ? undefined : '缺少编译记录关联，无法查看边端任务'}
          >
            查看边端任务
          </Button>
          {row.published_catalog_id ? (
            <Button type="link" size="small" onClick={() => navigate(`/models?view=published&export_id=${modelBm1688ExportId(row) || ''}`)}>
              查看已发布模型
            </Button>
          ) : (
            <Button type="link" size="small" onClick={() => selectModel(row, 'bmodel')}>
              发布边端模型
            </Button>
          )}
        </Space>
        );
      },
    },
  ];
  const familyColumns: ColumnsType<ModelFamily> = [
    {
      title: '模型系列',
      key: 'display_name',
      width: 300,
      ellipsis: true,
      render: (_, family) => {
        const published = family.versions.find((row) => row.id === family.published_version_id);
        return (
          <Space direction="vertical" size={2}>
            <strong>{family.display_name || `任务 #${family.task_id}`}</strong>
            <Space size={6} wrap>
              <span style={{ color: '#667085', fontSize: 12 }}>共 {family.version_count} 个版本</span>
              {published && <Tag color="green">已发布 {modelVersionLabel(published)}</Tag>}
            </Space>
          </Space>
        );
      },
    },
    {
      title: '最新版本',
      key: 'latest_version',
      width: 150,
      render: (_, family) => {
        const latest = latestModelFamilyVersion(family);
        return latest ? (
          <Space direction="vertical" size={0}>
            <Space size={6}><span>{modelVersionLabel(latest)}</span><Tag color="blue">系列最新</Tag></Space>
            {(latest.variant_name || latest.variant_id) && (
              <span style={{ color: '#667085', fontSize: 12 }}>
                分支 {latest.variant_name || `#${latest.variant_id}`}
              </span>
            )}
          </Space>
        ) : '-';
      },
    },
    {
      title: view === 'bmodel' ? '最新边端 / 发布状态' : '最新训练状态',
      key: 'latest_status',
      width: 210,
      render: (_, family) => {
        const latest = latestModelFamilyVersion(family);
        if (!latest) return '-';
        return view === 'bmodel' ? (
          <Space size={4} wrap>{renderBmodelEdgeStatus(latest)}{renderEdgeVersionTrend(latest)}{renderBmodelPublishStatus(latest)}</Space>
        ) : ptTrainingStatusTag(latest);
      },
    },
    { title: '更新时间', dataIndex: 'updated_at', key: 'updated_at', width: 180, render: formatDateTime },
    {
      title: '操作',
      key: 'actions',
      width: view === 'bmodel' ? 280 : 140,
      render: (_, family) => {
        const latest = latestModelFamilyVersion(family);
        if (!latest) return null;
        const edgeExportId = view === 'bmodel' ? modelBm1688ExportId(latest) : null;
        const edgeTaskId = latest.task_id || family.task_id;
        return (
          <Space size={0} wrap>
            <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => selectModel(latest, view === 'bmodel' ? 'bmodel' : 'pt')}>
              最新详情
            </Button>
            {view === 'bmodel' && Number(edgeTaskId) > 0 ? (
              <Button
                type="link"
                size="small"
                icon={<ArrowRightOutlined />}
                aria-label="查看边端任务"
                onClick={() => navigate(edgeTaskCenterPath(edgeExportId, edgeTaskId))}
              >
                查看边端任务
              </Button>
            ) : null}
          </Space>
        );
      },
    },
  ];
  const compileColumns: ColumnsType<Bm1688ExportRow> = [
    {
      title: '模型版本',
      key: 'version',
      width: 180,
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <strong>{row.version_no == null ? '版本未关联' : formatVersion(row.version_no)}</strong>
          <span style={{ color: '#667085', fontSize: 12 }}>
            {row.dataset_version_no != null
              ? `数据快照 ${formatVersion(row.dataset_version_no)}`
              : row.dataset_version_id
              ? `数据快照 #${row.dataset_version_id}`
              : row.model_version_id
              ? `模型版本 #${row.model_version_id}`
              : '历史任务未关联模型版本'}
          </span>
        </Space>
      ),
    },
    {
      title: '编译任务',
      key: 'compile_task',
      width: 170,
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <strong>#{row.id}</strong>
          <span style={{ color: '#667085', fontSize: 12 }}>训练作业 #{row.yolo_train_job_id || '-'}</span>
        </Space>
      ),
    },
    { title: '编译状态', dataIndex: 'status', key: 'status', width: 130, render: statusTag },
    {
      title: 'BModel 数量',
      key: 'bmodels',
      width: 140,
      render: (_, row) => {
        const names = row.bmodel_filenames?.length
          ? row.bmodel_filenames
          : row.bmodel_filename
          ? [row.bmodel_filename]
          : [];
        return names.length ? <Tag color="green">{names.length} 个制品</Tag> : <Tag>未生成</Tag>;
      },
    },
    {
      title: '当前结论',
      key: 'verdict',
      width: 180,
      render: (_, row) => compileVerdictLabel(row),
    },
    {
      title: '时间',
      key: 'updated_at',
      width: 170,
      render: (_, row) => formatDateTime(row.updated_at || row.created_at),
    },
    {
      title: '操作',
      key: 'actions',
      width: 190,
      fixed: 'right',
      render: (_, row) => (
        <Space size={0}>
          <Button
            type="link"
            size="small"
            disabled={row.can_recompile_bmodel === false}
            onClick={() => void confirmRecompile(row)}
          >
            {row.status === 'PROCESSING' ? '排队重新编译' : '重新编译'}
          </Button>
          <Button
            type="link"
            size="small"
            onClick={() => {
              const hasBmodel = Number(row.bmodel_filenames?.length || (row.bmodel_filename ? 1 : 0)) > 0;
              // Historical exports created before ModelVersion governance do
              // not have a canonical model family for the bmodel view. Keep
              // those records in the compile family history instead of
              // navigating to a guaranteed-empty model-family deep link.
              const hasCanonicalModelVersion = Number(row.model_version_id) > 0;
              navigate(
                hasBmodel && hasCanonicalModelVersion
                  ? `/models?view=bmodel&export_id=${row.id}`
                  : `/models?view=compile&export_id=${row.id}`,
              );
            }}
          >
            {Number(row.bmodel_filenames?.length || (row.bmodel_filename ? 1 : 0)) > 0
              ? Number(row.model_version_id) > 0
                ? '查看边端模型'
                : '查看编译历史'
              : '进行编译任务'}
          </Button>
        </Space>
      ),
    },
  ];
  const compileFamilyColumns: ColumnsType<Bm1688ExportFamily> = [
    {
      title: '模型系列',
      key: 'display_name',
      width: 300,
      ellipsis: true,
      render: (_, family) => (
        <Space direction="vertical" size={2}>
          <strong>{family.display_name || family.task_name || `任务 #${family.task_id}`}</strong>
          <Space size={6} wrap>
            <span style={{ color: '#667085', fontSize: 12 }}>共 {family.version_count} 个版本</span>
            <span style={{ color: '#667085', fontSize: 12 }}>{family.compile_task_count} 个编译任务</span>
            {family.algorithm_key ? <Tag>{family.algorithm_key}</Tag> : null}
          </Space>
        </Space>
      ),
    },
    {
      title: '最新版本',
      key: 'latest_version',
      width: 150,
      render: (_, family) =>
        family.latest_version_no == null ? '-' : (
          <Space size={6}>
            <span>{formatVersion(family.latest_version_no)}</span>
            <Tag color="blue">最新</Tag>
          </Space>
        ),
    },
    {
      title: '最新编译状态',
      key: 'latest_status',
      width: 200,
      render: (_, family) => (
        <Space direction="vertical" size={0}>
          {statusTag(family.latest_status)}
          <span style={{ color: '#667085', fontSize: 12 }}>
            {family.latest_export_id ? `编译任务 #${family.latest_export_id}` : '暂无编译任务'}
          </span>
        </Space>
      ),
    },
    { title: '更新时间', dataIndex: 'updated_at', key: 'updated_at', width: 180, render: formatDateTime },
  ];
  const activeQuery =
    view === 'external'
      ? externalQuery
      : view === 'published'
      ? publishedQuery
      : view === 'compile'
      ? compileQuery
      : query;
  const activeRowCount =
    view === 'external'
      ? externalRows.length
      : view === 'published'
      ? publishedRows.length
      : view === 'compile'
      ? compileFamilyRows.length
      : view === 'quality-gates'
      ? rows.length
      : familyRows.length;
  const dataState = activeQuery.isLoading
    ? 'loading'
    : activeQuery.error
    ? 'error'
    : activeRowCount
    ? 'ready'
    : 'empty';
  const changeView = (nextView: ModelCenterView) => {
    const params = new URLSearchParams(searchParams);
    params.delete('model_id');
    params.delete('export_id');
    params.delete('training_job_id');
    if (nextView === 'pt') params.delete('view');
    else params.set('view', nextView);
    setPage(0);
    navigate({ pathname: '/models', search: params.toString() ? `?${params.toString()}` : '' });
  };
  const changePublishedView = () => {
    changeView('published');
  };
  const changeExternalView = () => {
    changeView('external');
  };
  const downloadExternalModel = async (row: ExternalImportedModel) => {
    try {
      const blob = (await request.get(`/v1/yolo-imported-models/${row.id}/download`, {
        responseType: 'blob',
      })) as unknown as Blob;
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = objectUrl;
      link.download = row.original_filename || `${row.display_name || `外部模型-${row.id}`}.pt`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (error) {
      message.error(externalModelActionError(error, '模型下载失败，请稍后重试。'));
    }
  };
  const confirmDeleteExternalModel = (row: ExternalImportedModel) => {
    Modal.confirm({
      title: '删除外部导入模型',
      centered: true,
      okText: '确认删除',
      cancelText: '取消',
      okButtonProps: { danger: true },
      content:
        Number(row.registered_model_count || 0) > 0
          ? `“${
              row.display_name || row.original_filename || `模型 #${row.id}`
            }”已登记到模型仓库。删除后将从外部导入列表移除，模型仓库中的版本和权重制品会保留。`
          : `确认删除“${
              row.display_name || row.original_filename || `模型 #${row.id}`
            }”吗？外部导入记录和服务器权重文件都会删除，且无法恢复。`,
      onOk: async () => {
        try {
          await request.delete(`/v1/yolo-imported-models/${row.id}`);
          message.success('外部导入模型已删除');
          if (externalRows.length === 1 && externalPage > 0) setExternalPage((current) => current - 1);
          else await externalQuery.refetch();
        } catch (error) {
          message.error(externalModelActionError(error, '删除失败，请稍后重试。'));
          throw error;
        }
      },
    });
  };
  return (
    <>
      <ModuleHeader module="models" />
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
          flexWrap: 'wrap',
          minWidth: 0,
          marginBottom: 14,
          color: '#64748b',
          fontSize: 12,
        }}
      >
        <span style={{ minWidth: 0, flex: '1 1 240px', overflowWrap: 'anywhere' }}>
          流程：训练中心生成 PT → BM1688 编译 → 边端验证 → 发布。训练历史展示训练效果、召回率和推荐置信度；编译与边端历史保留每个版本及任务状态，完整曲线请到训练中心查看。
        </span>
        <Tag color={dataState === 'error' ? 'red' : dataState === 'loading' ? 'processing' : 'green'}>
          {dataState === 'error'
            ? '模型数据加载失败'
            : dataState === 'loading'
            ? '正在加载模型数据'
            : `当前页 ${activeRowCount} 条记录`}
        </Tag>
      </div>
      <ModelCenterPanel className="model-center-panel" style={{ minWidth: 0, width: '100%', overflowX: 'hidden' }}>
        <Space
          className="model-center-header"
          style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', width: '100%' }}
          align="center"
        >
          <div>
            <h2 style={{ margin: 0 }}>模型仓库</h2>
            <span style={{ color: '#64748b', fontSize: 12 }}>按阶段查看模型，不需要记内部状态码</span>
          </div>
          <Space wrap>
            <Button type={view === 'pt' ? 'primary' : 'default'} onClick={() => changeView('pt')}>
              训练权重（PT）
            </Button>
            <Button type={view === 'compile' ? 'primary' : 'default'} onClick={() => changeView('compile')}>
              编译任务
            </Button>
            <Button type={view === 'bmodel' ? 'primary' : 'default'} onClick={() => changeView('bmodel')}>
              边端模型（BModel）
            </Button>
            <Button type={view === 'published' ? 'primary' : 'default'} onClick={changePublishedView}>
              已发布模型
            </Button>
            <Button type={view === 'external' ? 'primary' : 'default'} onClick={changeExternalView}>
              外部导入
            </Button>
            {view === 'external' && (
              <Button type="primary" icon={<PlusOutlined />} onClick={() => setExternalImportOpen(true)}>
                登记外部模型
              </Button>
            )}
          </Space>
        </Space>
        <p className="model-center-description">
          {view === 'quality-gates'
            ? '这里列出还没有走完流程的模型，按“当前步骤”处理即可。训练质量到训练中心看，边端验证到 BModel 详情看。'
            : view === 'compile'
            ? '同一任务与算法的编译记录归为一个模型系列；展开后可查看每个模型版本的全部编译历史。'
            : view === 'bmodel'
            ? '同一任务与算法的边端模型归为一个系列；展开后可查看每个版本的 FP32、FP16、INT8 制品及独立状态。'
            : view === 'published'
            ? '查看本平台发布和AICM历史模型。第三方回流可通过这里保存的模型编号查找对应模型。'
            : view === 'external'
            ? '查看所有有权限访问的外部权重；管理人员可查看并删除全部记录，标注人员可删除自己上传的记录。'
            : '同一任务与算法的训练迭代归为一个模型系列；展开查看历史版本、召回率和推荐置信度，完整曲线请到训练中心查看。'}
        </p>
        {groupedView && (
          <Space className="model-center-filter" wrap style={{ display: 'flex', justifyContent: 'space-between', width: '100%' }}>
            <ServerSearchInput
              value={modelSearchDraft}
              onChange={(value) => {
                setModelSearchDraft(value);
                if (!value) {
                  setModelSearch('');
                  setPage(0);
                  updateSearchQuery('');
                }
              }}
              onSearch={(value) => {
                const next = value.trim();
                setModelSearchDraft(next);
                setModelSearch(next);
                setPage(0);
                updateSearchQuery(next);
              }}
              loading={query.isFetching}
              placeholder="按任务、模型系列、模型键或训练作业搜索"
              ariaLabel="搜索模型系列"
              style={{ width: 320, maxWidth: '100%' }}
            />
            <span style={{ color: '#64748b', fontSize: 12 }}>
              搜索在服务端执行，结果包含全部历史模型系列
            </span>
          </Space>
        )}
        {view === 'published' && (
          <Space className="model-center-filter" wrap style={{ display: 'flex', justifyContent: 'space-between' }}>
            <Space wrap>
              <ServerSearchInput
                value={publishedSearchDraft}
                ariaLabel="搜索已发布模型"
                placeholder="搜索模型编号或名称"
                style={{ width: 260 }}
                loading={publishedQuery.isFetching}
                onChange={(value) => {
                  setPublishedSearchDraft(value);
                  if (!value) {
                    setPublishedSearch('');
                    setPublishedPage(0);
                    updateSearchQuery('');
                  }
                }}
                onSearch={(value) => {
                  const next = value.trim();
                  setPublishedSearchDraft(next);
                  setPublishedSearch(next);
                  setPublishedPage(0);
                  updateSearchQuery(next);
                }}
              />
              <Select
                aria-label="模型来源"
                value={publishedSource}
                style={{ width: 160 }}
                options={[
                  { value: '', label: '全部来源' },
                  { value: 'labelu_publish', label: '本平台发布' },
                  { value: 'remote_legacy', label: 'AICM历史模型' },
                ]}
                onChange={(value) => {
                  setPublishedSource(value);
                  setPublishedPage(0);
                }}
              />
            </Space>
            <Space wrap>
              <span style={{ color: '#64748b', fontSize: 12 }}>共 {publishedTotal} 个模型</span>
              <Button icon={<ReloadOutlined />} loading={publishedQuery.isFetching} onClick={() => void publishedQuery.refetch()}>
                刷新列表
              </Button>
              {isTaskSuperuser && (
                <Button type="primary" loading={syncPublishedMutation.isPending} onClick={() => syncPublishedMutation.mutate()}>
                  同步AICM模型
                </Button>
              )}
            </Space>
          </Space>
        )}
        {view === 'compile' && (
          <Space className="model-center-filter" wrap style={{ display: 'flex', justifyContent: 'space-between', width: '100%' }}>
            <Space wrap>
              <ServerSearchInput
                value={compileSearchInput}
                ariaLabel="搜索编译任务"
                placeholder="搜索任务名称"
                style={{ width: 240 }}
                loading={compileQuery.isFetching}
                onChange={(value) => {
                  setCompileSearchInput(value);
                  if (!value) {
                    setCompileTaskName('');
                    setPage(0);
                    updateSearchQuery('');
                  }
                }}
                onSearch={(value) => {
                  const next = value.trim();
                  setCompileSearchInput(next);
                  setCompileTaskName(next);
                  setPage(0);
                  updateSearchQuery(next);
                }}
              />
              <Select
                aria-label="编译状态"
                value={compileStatus}
                style={{ width: 150 }}
                options={[
                  { value: '', label: '全部状态' },
                  { value: 'PENDING', label: '待处理' },
                  { value: 'PROCESSING', label: '处理中' },
                  { value: 'ONNX_READY', label: 'ONNX 已就绪' },
                  { value: 'COMPLETED', label: '已完成' },
                  { value: 'FAILED', label: '失败' },
                ]}
                onChange={(value) => {
                  setCompileStatus(value);
                  setPage(0);
                }}
              />
              <Button
                disabled={!compileTaskName && !compileStatus && !compileSearchInput}
                onClick={() => {
                  setCompileSearchInput('');
                  setCompileTaskName('');
                  setCompileStatus('');
                  setPage(0);
                  updateSearchQuery('');
                }}
              >
                重置筛选
              </Button>
            </Space>
            <Space wrap>
              <span style={{ color: '#64748b', fontSize: 12 }}>
                共 {listTotal(compileQuery.data, compileFamilyRows.length)} 个模型系列
              </span>
              <Button
                icon={<ReloadOutlined />}
                loading={compileQuery.isFetching}
                onClick={() => void compileQuery.refetch()}
              >
                刷新列表
              </Button>
            </Space>
          </Space>
        )}
        {view === 'external' && (
          <Space className="model-center-filter" wrap style={{ display: 'flex', justifyContent: 'space-between', width: '100%' }}>
            <ServerSearchInput
              value={externalSearchDraft}
              onChange={(value) => {
                setExternalSearchDraft(value);
                if (!value) {
                  setExternalSearch('');
                  setExternalPage(0);
                  updateSearchQuery('');
                }
              }}
              onSearch={(value) => {
                const next = value.trim();
                setExternalSearchDraft(next);
                setExternalSearch(next);
                setExternalPage(0);
                updateSearchQuery(next);
              }}
              loading={externalQuery.isFetching}
              placeholder="按模型名称或原始文件名搜索"
              ariaLabel="搜索外部导入模型"
              style={{ width: 280, maxWidth: '100%' }}
            />
            <span style={{ color: '#64748b', fontSize: 12 }}>
              共 {externalTotal} 个外部模型
            </span>
          </Space>
        )}
        {activeQuery.error ? (
          <Alert
            className="model-center-alert"
            type="error"
            showIcon
            message={
              responseStatus(activeQuery.error) === 403 ? '数据加载失败：当前账号无权访问模型数据' : '模型列表加载失败'
            }
            description={responseStatus(activeQuery.error) === 403 ? '请联系管理人员或检查账号权限。' : '请稍后重试。'}
            action={
              <Button size="small" icon={<ReloadOutlined />} onClick={() => void activeQuery.refetch()}>
                重试
              </Button>
            }
          />
        ) : null}
        {view === 'external' ? (
          externalQuery.isLoading ? (
            <div role="status" aria-live="polite" style={{ padding: '28px 0', textAlign: 'center' }}>
              <Spin />
            </div>
          ) : externalRows.length ? (
            <Table<ExternalImportedModel>
              className="model-center-table"
              rowKey="id"
              size="small"
              tableLayout="fixed"
              scroll={{ x: 1320 }}
              dataSource={externalRows}
              loading={externalQuery.isFetching}
              pagination={{
                current: externalPage + 1,
                pageSize: externalSize,
                total: externalTotal,
                showSizeChanger: true,
                showTotal: (count) => `共 ${count} 个外部模型`,
                onChange: (nextPage, nextSize) => {
                  setExternalPage(nextSize !== externalSize ? 0 : nextPage - 1);
                  setExternalSize(nextSize);
                },
              }}
              columns={[
                {
                  title: '模型名称',
                  key: 'display_name',
                  width: 220,
                  ellipsis: true,
                  render: (_, row) => row.display_name || row.original_filename || `模型 #${row.id}`,
                },
                { title: '原始文件', dataIndex: 'original_filename', key: 'original_filename', width: 220, ellipsis: true },
                { title: '文件大小', dataIndex: 'file_size', key: 'file_size', width: 100, render: formatFileSize },
                { title: '模型分类', dataIndex: 'category', key: 'category', width: 130, render: (value) => value || '-' },
                {
                  title: '仓库登记',
                  dataIndex: 'registered_model_count',
                  key: 'registered_model_count',
                  width: 150,
                  render: (value) =>
                    Number(value || 0) > 0 ? <Tag color="green">已登记 {value} 个版本</Tag> : <Tag>未登记</Tag>,
                },
                { title: '上传人', dataIndex: 'creator_email', key: 'creator_email', width: 180, ellipsis: true, render: (value) => value || '-' },
                { title: '导入时间', dataIndex: 'created_at', key: 'created_at', width: 170, render: formatDateTime },
                {
                  title: '操作',
                  key: 'actions',
                  width: 150,
                  fixed: 'right',
                  render: (_, row) => (
                    <Space size={0}>
                      <Button
                        type="link"
                        size="small"
                        icon={<DownloadOutlined />}
                        onClick={() => void downloadExternalModel(row)}
                      >
                        下载
                      </Button>
                      {row.can_delete ? (
                        <Button
                          type="link"
                          size="small"
                          danger
                          icon={<DeleteOutlined />}
                          onClick={() => confirmDeleteExternalModel(row)}
                        >
                          删除
                        </Button>
                      ) : null}
                    </Space>
                  ),
                },
              ]}
            />
          ) : !externalQuery.error ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无外部导入模型" />
          ) : null
        ) : view === 'published' ? (
          publishedQuery.isLoading ? (
            <div role="status" aria-live="polite" style={{ padding: '28px 0', textAlign: 'center' }}>
              <Spin />
            </div>
          ) : publishedRows.length ? (
            <Table<PublishedModel>
              className="model-center-table"
              rowKey="catalog_id"
              size="small"
              tableLayout="fixed"
              scroll={{ x: 1360 }}
              dataSource={publishedRows}
              loading={publishedQuery.isFetching}
              pagination={{
                current: publishedPage + 1,
                pageSize: publishedSize,
                total: publishedTotal,
                showSizeChanger: true,
                showTotal: (count) => `共 ${count} 个模型`,
                onChange: (nextPage, nextSize) => {
                  setPublishedPage(nextSize !== publishedSize ? 0 : nextPage - 1);
                  setPublishedSize(nextSize);
                },
              }}
              columns={[
                { title: '模型名称', key: 'name', width: 220, ellipsis: true, render: (_, row) => row.display_name || row.name || row.model_id },
                {
                  title: '模型编号',
                  key: 'model_id',
                  width: 120,
                  render: (_, row) => row.aicm_model_set_id ?? row.model_id,
                },
                { title: '来源', key: 'source', width: 130, render: (_, row) => publishedModelSource(row) },
                {
                  title: '同标签版本',
                  key: 'label_version',
                  width: 150,
                  render: (_, row) => row.label_group_version ? `${row.label_group_version} / ${row.label_group_size || 1}个版本` : publishedModelVersion(row),
                },
                {
                  title: '识别标签',
                  key: 'labels',
                  width: 240,
                  render: (_, row) => {
                    const labels = publishedModelLabels(row);
                    return labels.length ? <span title={labels.join('、')}>{labels.slice(0, 3).join('、')}{labels.length > 3 ? `等${labels.length}类` : ''}</span> : '未记录';
                  },
                },
                { title: '状态', key: 'availability_status', width: 140, render: (_, row) => publishedModelAvailability(row) },
                {
                  title: '时间',
                  dataIndex: 'published_at',
                  key: 'published_at',
                  width: 180,
                  render: (value) => <span title="优先显示发布时间；历史模型未提供时显示创建时间">{formatDateTime(value)}</span>,
                },
                {
                  title: '操作',
                  key: 'actions',
                  width: 180,
                  render: (_, row) => (
                    <Space size={0}>
                      <Button type="link" onClick={() => setSelectedPublished(row)}>详情</Button>
                      <Button
                      type="link"
                      disabled={!row.download_enabled}
                      onClick={async () => {
                        try {
                          const response = await request.get(
                            `/v1/published-models/${encodeURIComponent(row.model_id)}/download-url`,
                          );
                          const url = (response.data as { data?: { download_url?: string } })?.data?.download_url;
                          if (url && typeof window !== 'undefined') window.open(url, '_blank', 'noopener,noreferrer');
                        } catch (error) {
                          message.error(externalModelActionError(error, '模型下载失败，请稍后重试。'));
                        }
                      }}
                    >
                      下载模型
                      </Button>
                    </Space>
                  ),
                },
              ]}
            />
          ) : !publishedQuery.error ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={publishedSearch || publishedSource ? '当前筛选条件下暂无模型' : '暂无已发布模型，可同步AICM历史模型'} />
          ) : null
        ) : view === 'compile' ? (
          compileQuery.isLoading ? (
            <div role="status" aria-live="polite" style={{ padding: '28px 0', textAlign: 'center' }}>
              <Spin />
            </div>
          ) : compileFamilyRows.length ? (
            <Table<Bm1688ExportFamily>
              className="model-center-table"
              rowKey="family_key"
              columns={compileFamilyColumns}
              dataSource={compileFamilyRows}
              loading={compileQuery.isFetching}
              size="small"
              tableLayout="fixed"
              scroll={{ x: 830 }}
              expandedRowKeys={expandedCompileFamilyKeys}
              onExpandedRowsChange={(keys) => setExpandedCompileFamilyKeys(keys.map(String))}
              expandable={{
                expandedRowRender: (family) => (
                  <div className="model-center-expanded" style={{ minWidth: 0, maxWidth: '100%', overflowX: 'auto' }}>
                    <div style={{ marginBottom: 8, color: '#475467', fontSize: 13 }}>
                      <strong>编译历史</strong>
                      <span style={{ marginLeft: 10, color: '#98a2b3', fontSize: 12 }}>
                        同一模型版本的多次编译分别保留，编译任务编号用于定位执行记录
                      </span>
                    </div>
                    <Table<Bm1688ExportRow>
                      rowKey="id"
                      columns={compileColumns}
                      dataSource={family.exports || []}
                      pagination={false}
                      size="small"
                      tableLayout="fixed"
                      scroll={{ x: 1160 }}
                    />
                  </div>
                ),
              }}
              pagination={{
                current: page + 1,
                pageSize: size,
                total: listTotal(compileQuery.data, compileFamilyRows.length),
                showSizeChanger: true,
                showTotal: (count) => `共 ${count} 个模型系列`,
                onChange: (nextPage, nextSize) => {
                  setPage(nextSize !== size ? 0 : nextPage - 1);
                  setSize(nextSize);
                },
              }}
            />
          ) : !compileQuery.error ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={compileTaskName || compileStatus ? '当前筛选条件下暂无编译任务' : '暂无编译任务'}
            />
          ) : null
        ) : view === 'quality-gates' ? (
          <QualityGateQueue
            rows={rows}
            loading={query.isLoading || query.isFetching}
            search={qualityGateSearch}
            searchDraft={qualityGateSearchInput}
            onSearchChange={(value) => {
              setQualityGateSearchInput(value);
              if (!value) {
                setQualityGateSearch('');
                setPage(0);
                updateSearchQuery('');
              }
            }}
            onSearch={(value) => {
              const next = value.trim();
              setQualityGateSearchInput(next);
              setQualityGateSearch(next);
              setPage(0);
              updateSearchQuery(next);
            }}
            onSelect={(model) => selectModel(model, hasBm1688Artifact(model) ? 'bmodel' : 'pt')}
          />
        ) : query.isLoading ? (
          <div role="status" aria-live="polite" style={{ padding: '28px 0', textAlign: 'center' }}>
            <Spin />
          </div>
        ) : familyRows.length ? (
          <Table<ModelFamily>
            className="model-center-table"
            rowKey="family_key"
            columns={familyColumns}
            dataSource={familyRows}
            loading={query.isFetching}
            size="small"
            tableLayout="fixed"
            scroll={{ x: view === 'bmodel' ? 1020 : 950 }}
            expandedRowKeys={expandedFamilyKeys}
            onExpandedRowsChange={(keys) => setExpandedFamilyKeys(keys.map(String))}
            expandable={{
              expandedRowRender: (family) => (
                <div className="model-center-expanded" style={{ minWidth: 0, maxWidth: '100%', overflowX: 'auto' }}>
                  <div style={{ marginBottom: 8, color: '#475467', fontSize: 13 }}>
                    <strong>版本历史</strong>
                    <span style={{ marginLeft: 10, color: '#98a2b3', fontSize: 12 }}>按真实继承关系查看每版的数据快照、训练效果和完成时间；来源作业编号仅用于技术追溯</span>
                  </div>
                  <Table<ModelVersion>
                    rowKey="id"
                    columns={view === 'bmodel' ? bmodelColumns : columns}
                    dataSource={family.versions}
                    pagination={false}
                    size="small"
                    tableLayout="fixed"
                    scroll={{ x: view === 'bmodel' ? 1140 : 1300 }}
                  />
                </div>
              ),
            }}
            pagination={{
              current: page + 1,
              pageSize: size,
              total,
              showSizeChanger: true,
              showTotal: (count) => `共 ${count} 个模型系列`,
              onChange: (nextPage, nextSize) => {
                setPage(nextSize !== size ? 0 : nextPage - 1);
                setSize(nextSize);
              },
            }}
          />
        ) : !query.error ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={view === 'bmodel' ? '暂无已生成的 BModel 模型' : '暂无 PT 模型'}
          />
        ) : null}
      </ModelCenterPanel>
      <Modal
        title="已发布模型详情"
        open={Boolean(selectedPublished)}
        onCancel={() => setSelectedPublished(null)}
        footer={<Button onClick={() => setSelectedPublished(null)}>关闭</Button>}
        width={720}
        centered
        destroyOnClose
      >
        {selectedPublished && (
          <>
            <Descriptions bordered size="small" column={2}>
              <Descriptions.Item label="模型名称" span={2}>{selectedPublished.display_name || selectedPublished.name || selectedPublished.model_id}</Descriptions.Item>
              <Descriptions.Item label="模型编号">{selectedPublished.aicm_model_set_id ?? selectedPublished.model_id}</Descriptions.Item>
              <Descriptions.Item label="来源">{publishedModelSource(selectedPublished)}</Descriptions.Item>
              <Descriptions.Item label="同标签版本">{publishedModelVersion(selectedPublished)}</Descriptions.Item>
              <Descriptions.Item label="下载状态">{publishedModelAvailability(selectedPublished)}</Descriptions.Item>
              <Descriptions.Item label="相同标签的模型数">{selectedPublished.label_group_size || 0}</Descriptions.Item>
              <Descriptions.Item label="标签有交集的其他模型">{selectedPublished.related_model_count || 0}</Descriptions.Item>
              <Descriptions.Item label="芯片 / 精度">{selectedPublished.chip_type || '未记录'} / {selectedPublished.precision || '未记录'}</Descriptions.Item>
              <Descriptions.Item label="发布或创建时间">{formatDateTime(selectedPublished.published_at)}</Descriptions.Item>
              <Descriptions.Item label="最近同步时间">{formatDateTime(selectedPublished.last_synced_at)}</Descriptions.Item>
              <Descriptions.Item label="识别标签" span={2}>
                {publishedModelLabels(selectedPublished).length ? publishedModelLabels(selectedPublished).join('、') : '未记录'}
              </Descriptions.Item>
              <Descriptions.Item label="标签英文值" span={2}>
                {selectedPublished.class_names?.length ? selectedPublished.class_names.join('、') : '未记录'}
              </Descriptions.Item>
            </Descriptions>
            {selectedPublished.availability_status === 'unavailable' && selectedPublished.local_package_available && (
              <p style={{ margin: '12px 0 0', color: '#64748b', fontSize: 12 }}>
                AICM当前列表未列出该模型，本平台仍保留模型文件；下载以本平台的下载开关为准。该记录不参与AICM当前目录的同标签版本分组。
              </p>
            )}
            <p style={{ margin: '12px 0 0', color: '#64748b', fontSize: 12 }}>
              同标签版本按时间从早到晚编号，只表示标签集合相同，不代表后一个版本效果更好。历史模型没有发布时间时使用创建时间。
            </p>
          </>
        )}
      </Modal>
      <ModelDetailModal
        model={selected}
        mode={selectedDetailMode}
        onClose={() => setSelected(null)}
        onRefresh={() => {
          void query.refetch();
        }}
      />
      <ExternalModelImportModal
        open={externalImportOpen}
        onClose={() => setExternalImportOpen(false)}
        onSaved={() => {
          void query.refetch();
          void externalQuery.refetch();
        }}
      />
    </>
  );
}
