import { CheckCircleOutlined, ReloadOutlined, SettingOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Descriptions,
  Empty,
  Image,
  Modal,
  Segmented,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import request from '@/api/request';
import ServerSearchInput from '@/components/ServerSearchInput';
import { listData, listTotal, responseStatus } from '../shared';
import {
  agentBootstrapFailed,
  agentBootstrapInProgress,
  deviceConnectionReady as isDeviceConnectionReady,
} from './edgeDeviceReadiness';

type Bm1688ExportDetail = {
  id: number;
  task_id: number;
  task_name?: string | null;
  yolo_train_job_id: number;
  status: string;
};

type EdgeDevice = {
  id: string;
  name?: string;
  enabled?: boolean;
  validation_channel?: 'ssh' | 'board_agent';
  board_agent_status?: string;
  board_agent_environment_ready?: boolean;
  last_probe_status?: string;
  probe_status?: string;
  environment_status?: string;
  environment_report?: Record<string, unknown>;
  available_for_dispatch?: boolean;
  is_busy?: boolean;
  agent_bootstrap_status?: string | null;
};

type EdgeDeviceList = {
  active_device_id?: string;
  devices?: EdgeDevice[];
  total?: number;
  page?: number | null;
  size?: number;
};

type TaskSample = {
  id: number;
  inner_id?: number | null;
  state?: string | null;
  annotated_count?: number | null;
  data?: Record<string, unknown> | null;
  file?: Record<string, unknown> | null;
  updated_at?: string | null;
};

type EdgeValidationJob = {
  id: number;
  status: string;
  mode: string;
  execution_profile?: string;
  image_count: number;
  labeled_image_count: number;
  unlabeled_image_count: number;
  edge_device_name?: string | null;
  queue_reason?: string | null;
  queue_position?: number | null;
  report_summary?: string | null;
  progress?: {
    stage?: string;
    message?: string;
    updated_at?: string | null;
    history?: Array<{
      stage?: string;
      message?: string;
      at?: string | null;
    }>;
  } | null;
  error_message?: string | null;
  sample_ids?: number[];
  attachment_ids?: number[];
  thresholds?: Record<string, unknown> | null;
  result?: Record<string, unknown> | null;
  started_at?: string | null;
  completed_at?: string | null;
  created_at?: string | null;
};

type VisualEvidenceItem = {
  filename: string;
  status?: string;
  annotation_state?: string;
  gt_count?: number;
  prediction_count?: number;
  tp?: number;
  fp?: number;
  fn?: number;
  output_asset?: string;
  comparison_asset?: string;
  original_asset?: string;
  ground_truth_asset?: string;
  ground_truth_labels?: string[];
  prediction_labels?: string[];
};

type VisualEvidence = {
  available?: boolean;
  match_iou_threshold?: number;
  counts?: {
    total_images?: number;
    matched_images?: number;
    false_positive_images?: number;
    false_negative_images?: number;
    mismatch_images?: number;
    tp?: number;
    fp?: number;
    fn?: number;
  };
  items?: VisualEvidenceItem[];
};

type ValidationMode = 'run_and_eval' | 'run_only';
type ExecutionProfile = 'quick' | 'certification';
type ModalView = 'create' | 'result';

const ACTIVE_JOB_STATUSES = new Set(['PENDING', 'PROCESSING', 'QUEUED', 'RUNNING']);
const MAX_IMAGES = 50;
const DEVICE_PAGE_SIZE = 20;
const SAMPLE_PAGE_SIZE = 20;

function serverMessage(error: unknown, fallback: string) {
  const payload = (error as { response?: { data?: { msg?: string; message?: string } } } | null)?.response?.data;
  if (payload?.msg) return payload.msg;
  if (payload?.message) return payload.message;
  if (responseStatus(error) === 403) return '当前账号没有创建边端检查的权限。';
  return fallback;
}

function deviceConnectionReady(device: EdgeDevice) {
  return isDeviceConnectionReady(device);
}

function deviceEnvironmentReady(device: EdgeDevice) {
  if (device.validation_channel === 'board_agent') {
    return deviceConnectionReady(device) && Boolean(device.board_agent_environment_ready);
  }
  return device.environment_status === 'ready' && device.environment_report?.environment_ready === true;
}

export function deviceReady(device: EdgeDevice) {
  return (
    device.enabled !== false &&
    device.is_busy !== true &&
    !agentBootstrapFailed(device) &&
    !agentBootstrapInProgress(device) &&
    (device.available_for_dispatch === true || (deviceConnectionReady(device) && deviceEnvironmentReady(device)))
  );
}

export function sampleHasRectangleAnnotation(sample: TaskSample) {
  if (Number(sample.annotated_count || 0) > 0) return true;
  const resultValue = sample.data?.result;
  let result = resultValue;
  if (typeof result === 'string') {
    try {
      result = JSON.parse(result);
    } catch {
      return false;
    }
  }
  if (!result || typeof result !== 'object' || Array.isArray(result)) return false;
  const rectTool = (result as Record<string, unknown>).rectTool;
  if (!rectTool || typeof rectTool !== 'object' || Array.isArray(rectTool)) return false;
  return (
    Array.isArray((rectTool as Record<string, unknown>).result) &&
    ((rectTool as Record<string, unknown>).result as unknown[]).length > 0
  );
}

function sampleName(sample: TaskSample) {
  const filename = sample.file?.filename || sample.data?.filename;
  return typeof filename === 'string' && filename.trim() ? filename : `图片 #${sample.inner_id || sample.id}`;
}

function sampleStateLabel(value: unknown) {
  const labels: Record<string, string> = {
    NEW: '未标注',
    DONE: '已完成',
    SKIPPED: '已跳过',
  };
  const key = String(value || '').toUpperCase();
  return labels[key] || '处理中';
}

function visualEvidenceStatusLabel(value: unknown) {
  const labels: Record<string, string> = {
    matched: '正确匹配',
    true_negative: '正确无目标',
    false_positive: '误检',
    false_negative: '漏检',
    mismatch: '错检与漏检',
    unlabeled: '无人工标注',
  };
  return labels[String(value || '').toLowerCase()] || '待确认';
}

function visualEvidenceStatusColor(value: unknown) {
  const status = String(value || '').toLowerCase();
  if (status === 'matched' || status === 'true_negative') return 'green';
  if (status === 'false_positive' || status === 'false_negative' || status === 'mismatch') return 'red';
  return 'default';
}

function VisualEvidenceImage({
  jobId,
  kind,
  filename,
  label,
}: {
  jobId: number;
  kind: 'original' | 'ground_truth' | 'output' | 'compare';
  filename: string;
  label: string;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    void request
      .get(`/v1/yolo-edge-validation/jobs/${jobId}/visual-evidence/${kind}/${encodeURIComponent(filename)}`, { responseType: 'blob' })
      .then((blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob as unknown as Blob);
        setUrl(objectUrl);
      })
      .catch(() => active && setFailed(true));
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [jobId, kind, filename]);
  if (failed) return <Typography.Text type="secondary">图片证据不可用</Typography.Text>;
  if (!url) return <Spin size="small" />;
  return (
    <Image
      src={url}
      alt={label}
      preview={{ mask: '点击放大' }}
      style={{ width: '100%', height: 150, objectFit: 'contain', background: '#f5f7fa', borderRadius: 6 }}
    />
  );
}

function deviceDisplayName(device: EdgeDevice) {
  const name = String(device.name || '').trim();
  return name || device.id;
}

function deviceDispatchLabel(device: EdgeDevice) {
  if (device.is_busy) return { color: 'blue' as const, label: '执行中' };
  if (deviceReady(device)) return { color: 'green' as const, label: '可调度' };
  if (device.enabled === false) return { color: 'default' as const, label: '已停用' };
  if (agentBootstrapInProgress(device)) return { color: 'processing' as const, label: '环境准备中' };
  return { color: 'red' as const, label: '未就绪' };
}

function jobStatusLabel(value: unknown) {
  const labels: Record<string, string> = {
    PENDING: '等待调度',
    QUEUED: '排队中',
    PROCESSING: '执行中',
    RUNNING: '执行中',
    COMPLETED: '已完成',
    PARTIAL_COMPLETED: '部分完成',
    FAILED: '失败',
    CANCELLED: '已取消',
  };
  const key = String(value || '').toUpperCase();
  return labels[key] || '状态待确认';
}

function jobStatusColor(value: unknown) {
  const key = String(value || '').toUpperCase();
  if (key === 'COMPLETED') return 'green';
  if (key === 'FAILED' || key === 'CANCELLED') return 'red';
  if (key === 'PARTIAL_COMPLETED') return 'gold';
  return 'blue';
}

function qualityGateReasons(job: EdgeValidationJob) {
  const result = asRecord(job.result);
  const gate = asRecord(result.quality_gate || asRecord(result.eval_summary).quality_gate);
  const fixedEval = asRecord(result.fixed_eval_dataset || gate.fixed_eval);
  const reasons = Array.isArray(gate.reasons)
    ? gate.reasons.map(String)
    : Array.isArray(fixedEval.binding_reasons)
    ? fixedEval.binding_reasons.map(String)
    : [];
  const labels: Record<string, string> = {
    fixed_eval_labels_missing: '固定评估集缺少标签数据',
    fixed_eval_label_manifest_incomplete: '固定评估集的标签清单不完整',
    fixed_eval_signature_missing: '固定评估集校验信息缺失',
    fixed_eval_manifest_missing: '固定评估集清单缺失',
    fixed_eval_signature_mismatch: '固定评估集与本次检查不一致',
    int8_map50_drop_exceeded: 'INT8 相对 FP32 的精度下降超过阈值',
    fp16_map50_drop_exceeded: 'FP16 相对 FP32 的精度下降超过阈值',
    required_model_missing_or_failed: '三种精度模型没有全部完成检查',
  };
  return reasons.map((reason) => labels[reason] || reason).filter(Boolean);
}

function plotWarnings(job: EdgeValidationJob) {
  const result = asRecord(job.result);
  const execution = asRecord(result.execution);
  const warningValues = [
    ...(Array.isArray(result.warnings) ? result.warnings : []),
    ...(Array.isArray(execution.warnings) ? execution.warnings : []),
  ];
  if (warningValues.length) {
    const uniqueWarnings = new Map<string, string>();
    warningValues.forEach((item) => {
      const record = typeof item === 'string' ? {} : asRecord(item);
      const message = String(typeof item === 'string' ? item : record.message || '').trim();
      if (!message) return;
      const code = String(typeof item === 'string' ? message : record.code || message).trim();
      uniqueWarnings.set(code || message, message);
    });
    return Array.from(uniqueWarnings.values());
  }
  const error = String(job.error_message || execution.error || '');
  return /matplotlib/i.test(error) && /曲线图/.test(error)
    ? ['曲线图未生成：边端缺少 matplotlib（不影响推理和精度计算）']
    : [];
}

function isQualityGateOnlyFailure(job: EdgeValidationJob, modelRows: ModelResultRow[]) {
  if (String(job.status || '').toUpperCase() !== 'FAILED' || modelRows.length < 3) return false;
  const allModelsCompleted = modelRows.every((row) => String(row.status || '').toLowerCase() === 'ok');
  if (!allModelsCompleted) return false;
  const result = asRecord(job.result);
  const gate = asRecord(result.quality_gate || asRecord(result.eval_summary).quality_gate);
  return (
    String(gate.status || asRecord(result.eval_summary).status || '').toLowerCase() === 'fail' ||
    numberValue(asRecord(result.execution_summary).remote_exit_code) !== 0
  );
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function numberValue(value: unknown) {
  if (value === null || value === undefined || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function metricPercent(value: unknown) {
  const number = numberValue(value);
  return number === null ? '未采集' : `${(number <= 1 ? number * 100 : number).toFixed(2)}%`;
}

function metricNumber(value: unknown, digits = 2, unit = '') {
  const number = numberValue(value);
  return number === null ? '未采集' : `${number.toFixed(digits)}${unit}`;
}

function dateText(value: unknown) {
  if (!value) return '未记录';
  const date = new Date(String(value));
  return Number.isNaN(date.getTime())
    ? String(value).replace('T', ' ')
    : date.toLocaleString('zh-CN', { hour12: false });
}

function dimensionStatus(value: unknown) {
  const key = String(value || '').toLowerCase();
  if (key === 'pass' || key === 'computed' || key === 'ok') return <Tag color="green">通过</Tag>;
  if (key === 'fail' || key === 'failed') return <Tag color="red">失败</Tag>;
  if (key === 'partial') return <Tag color="gold">部分覆盖</Tag>;
  return <Tag>未确认</Tag>;
}

function progressStageLabel(value: unknown) {
  const labels: Record<string, string> = {
    prepare: '准备检查任务',
    queued: '等待设备',
    packaging: '准备检查数据',
    pack: '准备检查数据',
    ssh_connect: '连接边端设备',
    environment_probe: '检查运行环境',
    environment_provision: '准备边端运行环境',
    environment_verify: '验证边端运行环境',
    uploading: '上传模型和图片',
    upload: '上传模型和图片',
    upload_retry: '重试上传模型和图片',
    probe_demo: '探测边端推理环境',
    remote_run: '执行边端验证',
    running: '执行模型推理',
    inference: '执行模型推理',
    pull_output: '回传边端结果',
    downloading: '回传检查结果',
    download: '回传检查结果',
    ingest: '解析边端结果',
    pt_same_batch_eval: '计算 PT 对照结果',
    evaluating: '计算精度结果',
    evaluation: '计算精度结果',
    finalize: '整理检查结论',
    completed: '检查完成',
    failed: '检查失败',
  };
  const key = String(value || '').toLowerCase();
  return labels[key] || String(value || '执行阶段');
}

function modelResultRows(job: EdgeValidationJob) {
  const result = asRecord(job.result);
  const evalSummary = asRecord(result.eval_summary);
  const models = asRecord(result.eval_models || evalSummary.models);
  return ['fp32', 'fp16', 'int8'].flatMap((precision) => {
    const raw = asRecord(models[precision]);
    if (!Object.keys(raw).length) return [];
    const metrics = Object.keys(asRecord(raw.metrics)).length ? asRecord(raw.metrics) : raw;
    const runtime = asRecord(metrics.runtime_metrics || raw.runtime_metrics);
    const resources = asRecord(metrics.resource_usage || raw.resource_usage);
    const stageMetrics = asRecord(runtime.stage_metrics);
    const inferenceStage = asRecord(stageMetrics.inference_ms);
    const resourceMetric = (key: string, field: string) => {
      const value = resources[key];
      if (typeof value === 'number' || typeof value === 'string') return value;
      return asRecord(value)[field];
    };
    const runtimeValue = (keys: string[], fallback?: unknown) => {
      for (const key of keys) {
        if (runtime[key] !== undefined && runtime[key] !== null) return runtime[key];
        if (metrics[key] !== undefined && metrics[key] !== null) return metrics[key];
      }
      return fallback;
    };
    const flatResourceValue = (key: string, field: string) => metrics[`${key}_${field}`] ?? metrics[key];
    const resourceSampleCount = resources.sample_count ?? metrics.resource_sample_count;
    return [
      {
        key: precision,
        precision: precision.toUpperCase(),
        filename: String(raw.model_filename || metrics.model_filename || ''),
        status: String(raw.status || metrics.status || ''),
        map50_95: metrics.map50_95,
        map50: metrics.map50,
        precisionMetric: metrics.precision,
        recall: metrics.recall,
        f1: metrics.f1,
        images: metrics.images ?? metrics.image_count ?? raw.image_count,
        avgMs: runtimeValue(
          ['avg_inference_ms', 'average_inference_ms', 'latency_avg_ms'],
          metrics.speed_avg_ms ?? inferenceStage.avg,
        ),
        p95Ms: runtimeValue(['p95_inference_ms', 'p95_ms', 'latency_p95_ms', 'infer_p95_ms'], inferenceStage.p95),
        p99Ms: runtimeValue(['p99_inference_ms', 'p99_ms', 'latency_p99_ms', 'infer_p99_ms'], inferenceStage.p99),
        fps: runtimeValue(['throughput_fps', 'fps', 'throughput']),
        cpuUsage: resourceMetric('cpu_usage', 'avg') ?? flatResourceValue('cpu_usage', 'avg'),
        memoryMb: resourceMetric('memory_mb', 'avg') ?? metrics.cpu_memory_mb ?? flatResourceValue('memory_mb', 'avg'),
        npuUsage: resourceMetric('npu_usage', 'avg') ?? flatResourceValue('npu_usage', 'avg'),
        temperature:
          resourceMetric('temperature_c', 'max') ??
          resourceMetric('temperature_c', 'avg') ??
          flatResourceValue('temperature_c', 'max'),
        power: resourceMetric('power_w', 'avg') ?? flatResourceValue('power_w', 'avg'),
        repeatCount: runtime.repeat_count_completed ?? runtime.repeat_count_requested,
        failedCount: runtime.failed_count,
        resourceSampleCount,
        unavailableFields: Array.isArray(resources.unavailable_fields) ? resources.unavailable_fields.map(String) : [],
        unavailableReasons: asRecord(resources.unavailable_reasons),
      },
    ];
  });
}

type ModelResultRow = ReturnType<typeof modelResultRows>[number];

const resourceFieldLabels: Record<string, string> = {
  cpu_usage: 'CPU',
  process_cpu_usage: '进程 CPU',
  memory_mb: '内存',
  system_memory_usage: '系统内存',
  npu_usage: 'NPU',
  temperature_c: '温度',
  power_w: '功耗',
};

function EdgeValidationResult({ job, samples }: { job: EdgeValidationJob; samples: TaskSample[] }) {
  const processRef = useRef<HTMLDivElement>(null);
  const resultRef = useRef<HTMLDivElement>(null);
  const result = asRecord(job.result);
  const execution = asRecord(result.execution_summary);
  const prediction = asRecord(result.prediction_summary);
  const dimensions = asRecord(result.validation_dimensions);
  const environment = asRecord(dimensions.environment);
  const inference = asRecord(dimensions.inference);
  const precisionEvaluation = asRecord(dimensions.precision_evaluation);
  const coverage = asRecord(dimensions.class_coverage);
  const durations = asRecord(execution.stage_durations_seconds);
  const modelRows = modelResultRows(job);
  const visualEvidenceQuery = useQuery<VisualEvidence>({
    queryKey: ['edge-validation-visual-evidence', job.id],
    queryFn: async () => (await request.get(`/v1/yolo-edge-validation/jobs/${job.id}/visual-evidence`)).data,
    enabled: Boolean(job.id) && ['COMPLETED', 'PARTIAL_COMPLETED'].includes(String(job.status || '').toUpperCase()),
    staleTime: 30_000,
  });
  const visualEvidence = visualEvidenceQuery.data;
  const visualItems = visualEvidence?.items || [];
  const visualCounts = visualEvidence?.counts || {};
  const performanceProbeStatus = String(
    result.performance_probe_status || asRecord(result.eval_summary).performance_probe_status || '',
  ).toLowerCase();
  const performanceMissingLabel =
    performanceProbeStatus === 'disabled' || job.execution_profile !== 'certification'
      ? '快速检查未采集'
      : performanceProbeStatus && performanceProbeStatus !== 'ok'
      ? '性能采集失败'
      : '设备未提供';
  const selectedIds = new Set((job.sample_ids || []).map(Number));
  const checkedSamples = samples.filter((sample) => selectedIds.has(sample.id));
  const missingSampleIds = (job.sample_ids || []).filter((id) => !checkedSamples.some((sample) => sample.id === id));
  const missingClasses = Array.isArray(coverage.missing_classes) ? coverage.missing_classes.map(String) : [];
  const coveredClasses = Array.isArray(coverage.covered_classes) ? coverage.covered_classes.map(String) : [];
  const performanceCollected = modelRows.some(
    (row) =>
      numberValue(row.avgMs) !== null ||
      numberValue(row.p95Ms) !== null ||
      numberValue(row.fps) !== null ||
      numberValue(row.cpuUsage) !== null ||
      numberValue(row.memoryMb) !== null ||
      numberValue(row.npuUsage) !== null ||
      numberValue(row.temperature) !== null ||
      numberValue(row.power) !== null,
  );
  const unavailableResourceFields = Array.from(new Set(modelRows.flatMap((row) => row.unavailableFields || [])));
  const unavailableResourceReasons = Array.from(
    new Set(
      modelRows.flatMap((row) =>
        Object.entries(row.unavailableReasons || {}).map(
          ([key, value]) => `${resourceFieldLabels[key] || key}${value ? `：${value}` : ''}`,
        ),
      ),
    ),
  );
  const performanceValue = (value: unknown, digits: number, unit: string) =>
    numberValue(value) === null ? performanceMissingLabel : metricNumber(value, digits, unit);
  const rawProgressHistory = job.progress?.history;
  const progressHistory = Array.isArray(rawProgressHistory) ? rawProgressHistory : [];
  const gateOnlyFailure = isQualityGateOnlyFailure(job, modelRows);
  const gateReasons = qualityGateReasons(job);
  const warnings = plotWarnings(job);
  const displayedStatus = gateOnlyFailure ? 'PARTIAL_COMPLETED' : job.status;

  const precisionColumns: ColumnsType<ModelResultRow> = [
    {
      title: '模型精度',
      key: 'model',
      width: 150,
      render: (_, row) => (
        <div>
          <strong>{row.precision}</strong>
          <Typography.Text
            type="secondary"
            ellipsis={{ tooltip: row.filename }}
            style={{ display: 'block', maxWidth: 135, fontSize: 12 }}
          >
            {row.filename || '文件名未记录'}
          </Typography.Text>
        </div>
      ),
    },
    { title: '状态', dataIndex: 'status', key: 'status', width: 80, render: (value) => dimensionStatus(value) },
    { title: 'mAP50-95', dataIndex: 'map50_95', key: 'map50_95', render: metricPercent },
    { title: 'mAP50', dataIndex: 'map50', key: 'map50', render: metricPercent },
    { title: '精确率', dataIndex: 'precisionMetric', key: 'precisionMetric', render: metricPercent },
    { title: '召回率', dataIndex: 'recall', key: 'recall', render: metricPercent },
    { title: 'F1', dataIndex: 'f1', key: 'f1', render: metricPercent },
    {
      title: '评估图片',
      dataIndex: 'images',
      key: 'images',
      render: (value) => (numberValue(value) === null ? '未采集' : `${value} 张`),
    },
  ];

  const precisionKeyColumn = { title: '模型精度', dataIndex: 'precision', key: 'precision', width: 90 };
  const inferenceColumns: ColumnsType<ModelResultRow> = [
    precisionKeyColumn,
    { title: '平均推理', dataIndex: 'avgMs', key: 'avgMs', render: (value) => performanceValue(value, 3, ' 毫秒') },
    { title: 'P95 推理', dataIndex: 'p95Ms', key: 'p95Ms', render: (value) => performanceValue(value, 3, ' 毫秒') },
    { title: 'P99 推理', dataIndex: 'p99Ms', key: 'p99Ms', render: (value) => performanceValue(value, 3, ' 毫秒') },
    { title: '吞吐量', dataIndex: 'fps', key: 'fps', render: (value) => performanceValue(value, 2, ' 帧/秒') },
  ];
  const resourceColumns: ColumnsType<ModelResultRow> = [
    precisionKeyColumn,
    {
      title: 'CPU 平均占用',
      dataIndex: 'cpuUsage',
      key: 'cpuUsage',
      render: (value) => (numberValue(value) === null ? performanceMissingLabel : metricPercent(value as number)),
    },
    {
      title: '内存平均占用',
      dataIndex: 'memoryMb',
      key: 'memoryMb',
      render: (value) => performanceValue(value, 1, ' MB'),
    },
    {
      title: 'NPU 平均占用',
      dataIndex: 'npuUsage',
      key: 'npuUsage',
      render: (value) => (numberValue(value) === null ? performanceMissingLabel : metricPercent(value as number)),
    },
  ];
  const telemetryColumns: ColumnsType<ModelResultRow> = [
    precisionKeyColumn,
    {
      title: '最高温度',
      dataIndex: 'temperature',
      key: 'temperature',
      render: (value) => performanceValue(value, 1, '℃'),
    },
    { title: '平均功耗', dataIndex: 'power', key: 'power', render: (value) => performanceValue(value, 2, ' 瓦') },
    {
      title: '正式推理次数',
      dataIndex: 'repeatCount',
      key: 'repeatCount',
      render: (value) => (numberValue(value) === null ? performanceMissingLabel : `${value} 次`),
    },
    {
      title: '资源采样次数',
      dataIndex: 'resourceSampleCount',
      key: 'resourceSampleCount',
      render: (value) => (numberValue(value) === null ? performanceMissingLabel : `${value} 次`),
    },
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Alert
        type={
          gateOnlyFailure
            ? 'warning'
            : job.status === 'FAILED'
            ? 'error'
            : job.status === 'COMPLETED'
            ? 'success'
            : 'warning'
        }
        showIcon
        message={`检查任务 #${job.id}：${
          gateOnlyFailure ? '检查完成（质量门禁未通过）' : jobStatusLabel(displayedStatus)
        }`}
        description={
          gateOnlyFailure
            ? '三种 BModel 均已完成边端推理和精度计算，但固定评估集门禁未通过，模型暂不可发布。'
            : job.error_message || job.report_summary || '检查已执行，但没有返回文字摘要。'
        }
      />
      {gateOnlyFailure && gateReasons.length ? (
        <Alert type="warning" showIcon message="质量门禁未通过的原因" description={gateReasons.join('；')} />
      ) : null}
      {warnings.length ? <Alert type="info" showIcon message="辅助结果提醒" description={warnings.join('；')} /> : null}
      <Space wrap size={8}>
        <Button size="small" onClick={() => processRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>
          查看检查过程
        </Button>
        <Button size="small" onClick={() => resultRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>
          查看检查结果
        </Button>
      </Space>

      <div>
        <Typography.Title level={5} style={{ margin: '0 0 10px' }}>
          检查了什么
        </Typography.Title>
        <Descriptions bordered size="small" column={{ xs: 1, sm: 2 }}>
          <Descriptions.Item label="检查方式">
            {job.mode === 'run_only' ? '仅检查运行' : '运行并评估'}
          </Descriptions.Item>
          <Descriptions.Item label="检查档位">
            {job.execution_profile === 'certification' ? '完整检查' : '快速检查'}
          </Descriptions.Item>
          <Descriptions.Item label="执行设备">{job.edge_device_name || '未分配设备'}</Descriptions.Item>
          <Descriptions.Item label="检查模型">
            {modelRows.length ? modelRows.map((row) => row.precision).join('、') : '未回传模型清单'}
          </Descriptions.Item>
          <Descriptions.Item label="检查图片">{job.image_count} 张</Descriptions.Item>
          <Descriptions.Item label="人工拉框">{job.labeled_image_count} 张</Descriptions.Item>
          <Descriptions.Item label="置信度阈值">{metricNumber(job.thresholds?.conf_thres, 2)}</Descriptions.Item>
          <Descriptions.Item label="去重阈值">{metricNumber(job.thresholds?.nms_thres, 2)}</Descriptions.Item>
          <Descriptions.Item label="匹配阈值">{metricNumber(job.thresholds?.match_iou_thres, 2)}</Descriptions.Item>
          <Descriptions.Item label="执行时间">
            {dateText(job.started_at)} 至 {dateText(job.completed_at)}
          </Descriptions.Item>
        </Descriptions>
        <Table<TaskSample>
          style={{ marginTop: 10 }}
          rowKey="id"
          size="small"
          pagination={{ pageSize: 6, showSizeChanger: false }}
          dataSource={checkedSamples}
          columns={[
            { title: '已检查图片', key: 'name', ellipsis: true, render: (_, sample) => sampleName(sample) },
            { title: '样本编号', dataIndex: 'id', key: 'id', width: 100, render: (value) => `#${value}` },
            {
              title: '标注情况',
              key: 'annotation',
              width: 110,
              render: (_, sample) => (sampleHasRectangleAnnotation(sample) ? '有人工拉框' : '无人工拉框'),
            },
          ]}
          locale={{
            emptyText: missingSampleIds.length
              ? `已检查样本：${missingSampleIds.map((id) => `#${id}`).join('、')}`
              : '未记录具体图片',
          }}
        />
      </div>

      <div>
        <Typography.Title level={5} style={{ margin: '0 0 10px' }}>
          图片检查对比
        </Typography.Title>
        {visualEvidenceQuery.isLoading ? <Spin /> : visualEvidence?.available && visualItems.length ? (
          <>
            <Alert
              type="info"
              showIcon
              message={`已生成 ${visualItems.length} 张图片证据`}
              description={`绿色框表示人工标注与边端识别匹配，红色框表示误检，橙色框表示漏检；匹配 IoU 阈值 ${metricNumber(visualEvidence.match_iou_threshold, 2)}。`}
            />
            <Space wrap size={8} style={{ margin: '10px 0' }}>
              <Tag color="green">匹配图片 {visualCounts.matched_images || 0}</Tag>
              <Tag color="red">误检图片 {visualCounts.false_positive_images || 0}</Tag>
              <Tag color="red">漏检图片 {visualCounts.false_negative_images || 0}</Tag>
              <Tag>人工目标 {(visualCounts.tp || 0) + (visualCounts.fn || 0)}</Tag>
              <Tag>识别目标 {(visualCounts.tp || 0) + (visualCounts.fp || 0)}</Tag>
            </Space>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))',
                gap: 14,
                marginTop: 12,
              }}
            >
              {visualItems.map((item) => {
                const labels = [...new Set([...(item.ground_truth_labels || []), ...(item.prediction_labels || [])])];
                const image = (kind: 'original' | 'ground_truth' | 'output' | 'compare', asset?: string, label?: string) =>
                  asset ? <VisualEvidenceImage jobId={job.id} kind={kind} filename={asset} label={label || ''} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未生成" />;
                return (
                  <div key={item.filename} style={{ border: '1px solid #e5e7eb', borderRadius: 8, padding: 12, background: '#fff' }}>
                    <Space direction="vertical" size={8} style={{ width: '100%' }}>
                      <Space style={{ width: '100%', justifyContent: 'space-between' }} align="start">
                        <Typography.Text strong ellipsis={{ tooltip: item.filename }} style={{ maxWidth: '70%' }}>{item.filename}</Typography.Text>
                        <Tag color={visualEvidenceStatusColor(item.status)}>{visualEvidenceStatusLabel(item.status)}</Tag>
                      </Space>
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 10 }}>
                        <div><Typography.Text type="secondary" style={{ display: 'block', marginBottom: 5 }}>原始图</Typography.Text>{image('original', item.original_asset || item.output_asset, '原始图片')}</div>
                        <div><Typography.Text type="secondary" style={{ display: 'block', marginBottom: 5 }}>系统人工标注</Typography.Text>{image('ground_truth', item.ground_truth_asset || item.comparison_asset, '系统人工标注')}</div>
                        <div><Typography.Text type="secondary" style={{ display: 'block', marginBottom: 5 }}>边端识别结果</Typography.Text>{image('output', item.output_asset, '边端识别结果')}</div>
                        <div><Typography.Text type="secondary" style={{ display: 'block', marginBottom: 5 }}>综合对比</Typography.Text>{image('compare', item.comparison_asset, '人工标注与边端识别综合对比')}</div>
                      </div>
                      <Space wrap size={[6, 4]}>
                        <Tag>人工目标 {item.gt_count ?? 0}</Tag>
                        <Tag>识别目标 {item.prediction_count ?? 0}</Tag>
                        <Tag>匹配 / 误检 / 漏检 {item.tp || 0} / {item.fp || 0} / {item.fn || 0}</Tag>
                      </Space>
                      <Typography.Text type="secondary">标签目标：{labels.join('、') || '无'}</Typography.Text>
                    </Space>
                  </div>
                );
              })}
            </div>
          </>
        ) : (
          <Alert type="warning" showIcon message="暂无逐图片视觉证据" description="当前检查报告只保存了汇总指标，或检查包未生成可回传的图片证据。重新执行完整检查后，系统会生成原图、边端识别图和标注对比图。" />
        )}
      </div>

      <div ref={processRef}>
        <Typography.Title level={5} style={{ margin: '0 0 10px' }}>
          检查过程
        </Typography.Title>
        {progressHistory.length ? (
          <>
            <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 8, fontSize: 12 }}>
              共 {progressHistory.length} 条执行记录，最新记录在前；上传进度中的百分比记录可翻页查看。
            </Typography.Text>
            <Table
              rowKey={(_, index) => String(index)}
              size="small"
              pagination={{
                pageSize: 8,
                showSizeChanger: false,
                showTotal: (total) => `共 ${total} 条`,
              }}
              dataSource={[...progressHistory].reverse()}
              columns={[
                { title: '时间', dataIndex: 'at', key: 'at', width: 180, render: dateText },
                {
                  title: '检查阶段',
                  dataIndex: 'stage',
                  key: 'stage',
                  width: 170,
                  render: progressStageLabel,
                },
                { title: '执行结果', dataIndex: 'message', key: 'message', render: (value) => value || '未记录' },
              ]}
            />
          </>
        ) : (
          <Alert type="info" showIcon message={job.progress?.message || '本次检查没有保存分阶段执行记录。'} />
        )}
      </div>

      <div ref={resultRef}>
        <Typography.Title level={5} style={{ margin: '0 0 10px' }}>
          执行结论
        </Typography.Title>
        <Descriptions bordered size="small" column={{ xs: 1, sm: 2 }}>
          <Descriptions.Item label="设备环境">{dimensionStatus(environment.status)}</Descriptions.Item>
          <Descriptions.Item label="模型运行">
            {gateOnlyFailure ? <Tag color="green">通过</Tag> : dimensionStatus(inference.status)}
          </Descriptions.Item>
          <Descriptions.Item label="精度计算">{dimensionStatus(precisionEvaluation.status)}</Descriptions.Item>
          <Descriptions.Item label="标签覆盖">{dimensionStatus(coverage.status)}</Descriptions.Item>
          <Descriptions.Item label="质量门禁">
            {gateOnlyFailure ? <Tag color="orange">未通过，已阻止发布</Tag> : <Tag>未确认</Tag>}
          </Descriptions.Item>
          <Descriptions.Item label="设备退出码">
            {numberValue(execution.remote_exit_code) ?? '未采集'}
          </Descriptions.Item>
          <Descriptions.Item label="总耗时">{metricNumber(durations.total, 2, ' 秒')}</Descriptions.Item>
          <Descriptions.Item label="命中模型缓存">
            {execution.model_cache_hit === true ? '是' : execution.model_cache_hit === false ? '否' : '未采集'}
          </Descriptions.Item>
          <Descriptions.Item label="回传结果文件">
            {numberValue(execution.files_pulled) === null ? '未采集' : `${execution.files_pulled} 个`}
          </Descriptions.Item>
          <Descriptions.Item label="有检测框图片">
            {numberValue(prediction.with_boxes) === null ? '未采集' : `${prediction.with_boxes} 张`}
          </Descriptions.Item>
          <Descriptions.Item label="检测框总数">
            {numberValue(prediction.total_boxes) === null ? '未采集' : `${prediction.total_boxes} 个`}
          </Descriptions.Item>
        </Descriptions>
      </div>

      <div>
        <Typography.Title level={5} style={{ margin: '0 0 10px' }}>
          三种模型精度结果
        </Typography.Title>
        {modelRows.length ? (
          <Table<ModelResultRow>
            rowKey="key"
            size="small"
            pagination={false}
            scroll={{ x: 900 }}
            dataSource={modelRows}
            columns={precisionColumns}
          />
        ) : (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={job.mode === 'run_only' ? '本次只检查运行，没有计算精度' : '本次没有回传精度结果'}
          />
        )}
        {coveredClasses.length || missingClasses.length ? (
          <Alert
            style={{ marginTop: 10 }}
            type={missingClasses.length ? 'warning' : 'success'}
            showIcon
            message={`标签覆盖 ${coverage.covered_class_count || coveredClasses.length}/${
              coverage.total_class_count || coveredClasses.length + missingClasses.length
            }`}
            description={
              <Space direction="vertical" size={4}>
                <span>已覆盖：{coveredClasses.length ? coveredClasses.join('、') : '未记录'}</span>
                <span>未覆盖：{missingClasses.length ? missingClasses.join('、') : '无'}</span>
              </Space>
            }
          />
        ) : null}
      </div>

      <div>
        <Typography.Title level={5} style={{ margin: '0 0 10px' }}>
          边端性能结果
        </Typography.Title>
        <Typography.Text strong>推理性能</Typography.Text>
        <Table<ModelResultRow>
          rowKey="key"
          size="small"
          pagination={false}
          style={{ marginTop: 6 }}
          dataSource={modelRows}
          columns={inferenceColumns}
        />
        <Typography.Text strong style={{ display: 'block', marginTop: 14 }}>
          资源占用
        </Typography.Text>
        <Table<ModelResultRow>
          rowKey="key"
          size="small"
          pagination={false}
          style={{ marginTop: 6 }}
          dataSource={modelRows}
          columns={resourceColumns}
        />
        <Typography.Text strong style={{ display: 'block', marginTop: 14 }}>
          设备遥测与采集次数
        </Typography.Text>
        <Table<ModelResultRow>
          rowKey="key"
          size="small"
          pagination={false}
          style={{ marginTop: 6 }}
          dataSource={modelRows}
          columns={telemetryColumns}
        />
        {!performanceCollected ? (
          <Alert
            style={{ marginTop: 10 }}
            type="info"
            showIcon
            message={
              performanceProbeStatus === 'disabled' || job.execution_profile !== 'certification'
                ? '本次为快速检查，未开启性能数据采集。'
                : performanceProbeStatus && performanceProbeStatus !== 'ok'
                ? '本次性能数据采集失败。'
                : '本次未返回可用的性能数据。'
            }
            description="重新选择“完整检查”后，系统会采集三种模型的平均延迟、P95、P99、吞吐量、CPU、内存、NPU、温度和功耗；设备不提供的硬件遥测会单独标明。"
          />
        ) : null}
        {performanceCollected && unavailableResourceFields.length ? (
          <Alert
            style={{ marginTop: 10 }}
            type="info"
            showIcon
            message={`部分设备遥测未提供：${unavailableResourceFields
              .map((field) => resourceFieldLabels[field] || field)
              .join('、')}`}
            description={
              unavailableResourceReasons.length
                ? unavailableResourceReasons.join('；')
                : '这些字段不会用 0 代替，表示边端没有返回对应硬件遥测。'
            }
          />
        ) : null}
      </div>
    </Space>
  );
}

export default function EdgeValidationModal({
  open,
  exportId,
  modelName,
  initialView = 'create',
  embedded = false,
  onClose,
}: {
  open: boolean;
  exportId: number | null;
  modelName: string;
  initialView?: ModalView;
  embedded?: boolean;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const [view, setView] = useState<ModalView>(initialView);
  const [mode, setMode] = useState<ValidationMode>('run_and_eval');
  const [executionProfile, setExecutionProfile] = useState<ExecutionProfile>('certification');
  const [selectedSampleIds, setSelectedSampleIds] = useState<number[]>([]);
  const [devicePage, setDevicePage] = useState(0);
  const [deviceSearchInput, setDeviceSearchInput] = useState('');
  const [deviceSearch, setDeviceSearch] = useState('');
  const [samplePage, setSamplePage] = useState(0);
  const [sampleSearchInput, setSampleSearchInput] = useState('');
  const [sampleSearch, setSampleSearch] = useState('');
  const [sampleCache, setSampleCache] = useState<Record<number, TaskSample>>({});
  const [initializedKey, setInitializedKey] = useState('');
  const [createdJob, setCreatedJob] = useState<EdgeValidationJob | null>(null);
  const [selectedJobId, setSelectedJobId] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);
  const wasOpenRef = useRef(false);
  const sampleContextRef = useRef('');

  const exportQuery = useQuery<Bm1688ExportDetail>({
    queryKey: ['bm1688-export-detail', exportId],
    queryFn: async () => (await request.get(`/v1/yolo-bm1688-exports/${exportId}`)).data,
    enabled: open && Boolean(exportId),
    staleTime: 10_000,
  });
  const devicesQuery = useQuery<EdgeDeviceList>({
    queryKey: ['edge-devices', 'validation', devicePage, deviceSearch],
    queryFn: async () =>
      (
        await request.get('/v1/edge-devices', {
          params: {
            page: devicePage,
            size: DEVICE_PAGE_SIZE,
            q: deviceSearch || undefined,
            status: 'all',
          },
        })
      ).data,
    enabled: open && view === 'create',
    staleTime: 10_000,
    placeholderData: (previous) => previous,
  });
  // The visible device directory is paged/searchable, but dispatch readiness
  // must remain global: searching for a blocked device must not disable a
  // job when another ready device exists on a different page.
  const deviceReadinessQuery = useQuery<EdgeDeviceList>({
    queryKey: ['edge-devices', 'validation-readiness'],
    queryFn: async () =>
      (
        await request.get('/v1/edge-devices', {
          // The total is the global readiness summary; fetching one row keeps
          // this check bounded even when the registry is very large.
          params: { page: 0, size: 1, status: 'ready' },
        })
      ).data,
    enabled: open && view === 'create',
    staleTime: 10_000,
  });
  const taskId = exportQuery.data?.task_id;
  const samplesQuery = useQuery<{ rows: TaskSample[]; total: number }>({
    queryKey: ['edge-validation-samples', taskId, samplePage, sampleSearch],
    queryFn: async () => {
      const response = await request.get(`/v1/tasks/${taskId}/samples`, {
        params: {
          page: samplePage,
          size: SAMPLE_PAGE_SIZE,
          ...(sampleSearch ? { q: sampleSearch } : {}),
        },
      });
      const rows = listData(response) as TaskSample[];
      return { rows, total: listTotal(response, rows.length) };
    },
    enabled: open && Boolean(taskId),
    staleTime: 10_000,
    placeholderData: (previous) => previous,
  });
  const historyQuery = useQuery<unknown>({
    queryKey: ['edge-validation-jobs', exportId],
    queryFn: async () =>
      (
        await request.get('/v1/yolo-edge-validation/jobs', {
          params: { page: 0, size: 20, export_id: exportId },
        })
      ).data,
    enabled: open && view === 'result' && Boolean(exportId),
    staleTime: 5_000,
  });
  const historyJobs = useMemo(() => listData(historyQuery.data) as EdgeValidationJob[], [historyQuery.data]);
  const jobId = selectedJobId || createdJob?.id || null;
  const jobQuery = useQuery<EdgeValidationJob>({
    queryKey: ['edge-validation-job', jobId],
    queryFn: async () => (await request.get(`/v1/yolo-edge-validation/jobs/${jobId}`)).data,
    enabled: open && Boolean(jobId),
    refetchInterval: (query) => {
      const job = query.state.data as EdgeValidationJob | undefined;
      return job && ACTIVE_JOB_STATUSES.has(String(job.status || '').toUpperCase()) ? 2_000 : false;
    },
  });

  const samples = samplesQuery.data?.rows || [];
  const sampleTotal = samplesQuery.data?.total ?? 0;
  const devices = devicesQuery.data?.devices || [];
  const readinessDevices = deviceReadinessQuery.data?.devices || [];
  const readyDeviceTotal = deviceReadinessQuery.data?.total ?? readinessDevices.filter(deviceReady).length;
  const deviceTotal = devicesQuery.data?.total ?? devices.length;
  const knownSamples = useMemo(() => {
    const rows = { ...sampleCache };
    samples.forEach((sample) => {
      if (sample.id) rows[Number(sample.id)] = sample;
    });
    return Object.values(rows);
  }, [sampleCache, samples]);
  const selectedSamples = useMemo(
    () =>
      selectedSampleIds.map((id) => knownSamples.find((sample) => sample.id === id)).filter(Boolean) as TaskSample[],
    [knownSamples, selectedSampleIds],
  );
  const selectedLabeledCount = selectedSamples.filter(sampleHasRectangleAnnotation).length;
  const currentJob = jobQuery.data || createdJob;

  useEffect(() => {
    if (!samples.length) return;
    setSampleCache((previous) => {
      let changed = false;
      const next = { ...previous };
      samples.forEach((sample) => {
        if (!sample.id) return;
        const id = Number(sample.id);
        if (next[id] !== sample) {
          next[id] = sample;
          changed = true;
        }
      });
      return changed ? next : previous;
    });
  }, [samples]);

  useEffect(() => {
    if (!open) {
      sampleContextRef.current = '';
      setDevicePage(0);
      setDeviceSearchInput('');
      setDeviceSearch('');
      setSamplePage(0);
      setSampleSearchInput('');
      setSampleSearch('');
      setSampleCache({});
      setSelectedSampleIds([]);
      return;
    }
    const contextKey = `${exportId || ''}:${taskId || ''}`;
    if (sampleContextRef.current === contextKey) return;
    sampleContextRef.current = contextKey;
    setDevicePage(0);
    setDeviceSearchInput('');
    setDeviceSearch('');
    setSamplePage(0);
    setSampleSearchInput('');
    setSampleSearch('');
    setSampleCache({});
    setSelectedSampleIds([]);
    setInitializedKey('');
  }, [exportId, open, taskId]);

  useEffect(() => {
    if (open && !wasOpenRef.current) {
      setView(initialView);
      setSelectedJobId(null);
      setCreatedJob(null);
    }
    wasOpenRef.current = open;
    if (!open) {
      setInitializedKey('');
      setCreatedJob(null);
      setSelectedJobId(null);
      setSubmitting(false);
      submittingRef.current = false;
      return;
    }
    if (!samples.length || !taskId) return;
    const key = `${exportId}:${taskId}`;
    if (initializedKey === key) return;
    const labeled = samples.filter(sampleHasRectangleAnnotation);
    const initial = (labeled.length ? labeled : samples).slice(0, 10).map((sample) => sample.id);
    setSelectedSampleIds(initial);
    setInitializedKey(key);
  }, [exportId, initialView, initializedKey, open, samples, taskId]);

  useEffect(() => {
    if (view !== 'result' || selectedJobId || createdJob?.id || !historyJobs.length) return;
    setSelectedJobId(historyJobs[0].id);
  }, [createdJob?.id, historyJobs, selectedJobId, view]);

  const loading =
    exportQuery.isLoading ||
    (view === 'create' && (devicesQuery.isLoading || deviceReadinessQuery.isLoading)) ||
    (Boolean(taskId) && samplesQuery.isLoading) ||
    (view === 'result' && historyQuery.isLoading) ||
    (view === 'result' && Boolean(jobId) && jobQuery.isLoading);
  const loadError =
    exportQuery.error ||
    samplesQuery.error ||
    (view === 'create' ? devicesQuery.error || deviceReadinessQuery.error : historyQuery.error || jobQuery.error);
  const canSubmit =
    !submitting &&
    String(exportQuery.data?.status || '').toUpperCase() === 'COMPLETED' &&
    readyDeviceTotal > 0 &&
    selectedSampleIds.length > 0 &&
    selectedSampleIds.length <= MAX_IMAGES &&
    (mode === 'run_only' || selectedLabeledCount > 0);

  const startValidation = async () => {
    if (submittingRef.current) return;
    if (!canSubmit || !exportId || !taskId) {
      if (!readyDeviceTotal) message.warning('没有已连接且环境就绪的边端设备，请先配置设备。');
      else if (!selectedSampleIds.length) message.warning('请至少选择一张图片。');
      else if (mode === 'run_and_eval' && !selectedLabeledCount)
        message.warning('运行并评估至少需要一张有人工拉框的图片。');
      return;
    }
    submittingRef.current = true;
    setSubmitting(true);
    let created: EdgeValidationJob | null = null;
    try {
      created = (
        await request.post('/v1/yolo-edge-validation/jobs', {
          export_id: exportId,
          task_id: taskId,
          mode,
          execution_profile: executionProfile,
          sample_ids: selectedSampleIds,
          attachment_ids: [],
          external_attachments: [],
          thresholds: {},
          threshold_source: 'system_default',
          ai_thresholds: null,
        })
      ).data as EdgeValidationJob;
      setCreatedJob(created);
      const started = (await request.post(`/v1/yolo-edge-validation/jobs/${created.id}/run`)).data as EdgeValidationJob;
      setCreatedJob(started);
      setSelectedJobId(started.id);
      setView('result');
      message.success(`边端检查任务 #${created.id} 已启动`);
    } catch (error) {
      message.error(serverMessage(error, created ? '任务已创建，但启动失败，请查看完整任务。' : '创建边端检查失败。'));
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  const deviceColumns: ColumnsType<EdgeDevice> = [
    {
      title: '设备',
      key: 'device',
      render: (_, device) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{deviceDisplayName(device)}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {device.id}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '连接方式',
      dataIndex: 'validation_channel',
      key: 'validation_channel',
      width: 110,
      render: (value: unknown) => (String(value || '').toLowerCase() === 'board_agent' ? 'Board Agent' : 'SSH'),
    },
    {
      title: '调度状态',
      key: 'dispatch_status',
      width: 110,
      render: (_, device) => {
        const status = deviceDispatchLabel(device);
        return <Tag color={status.color}>{status.label}</Tag>;
      },
    },
  ];

  const columns: ColumnsType<TaskSample> = [
    {
      title: '图片',
      key: 'name',
      ellipsis: true,
      render: (_, sample) => sampleName(sample),
    },
    {
      title: '标注情况',
      key: 'annotation',
      width: 110,
      render: (_, sample) =>
        sampleHasRectangleAnnotation(sample) ? <Tag color="green">有人工拉框</Tag> : <Tag>无人工拉框</Tag>,
    },
    {
      title: '任务状态',
      dataIndex: 'state',
      key: 'state',
      width: 90,
      render: sampleStateLabel,
    },
  ];

  const footer = (
    <Space wrap>
      <Button icon={<SettingOutlined />} onClick={() => navigate('/admin?view=edge-devices')}>
        配置边端设备
      </Button>
      {view === 'result' && currentJob ? (
        <Button
          onClick={() => {
            const params = new URLSearchParams({ export_id: String(exportId) });
            if (taskId) params.set('task_id', String(taskId));
            navigate(`/edge-task-center?${params.toString()}`);
          }}
        >
          查看边端任务队列
        </Button>
      ) : null}
      {view === 'result' ? (
        <Button
          onClick={() => {
            setView('create');
            setCreatedJob(null);
            setSelectedJobId(null);
          }}
        >
          新建检查
        </Button>
      ) : (
        <Button onClick={() => setView('result')}>查看检查结果</Button>
      )}
      <Button onClick={onClose}>关闭</Button>
      {view === 'create' ? (
        <Button type="primary" loading={submitting} disabled={!canSubmit} onClick={() => void startValidation()}>
          创建并启动
        </Button>
      ) : null}
    </Space>
  );

  const content = (
    <>
      {loading ? (
        <div role="status" aria-live="polite" style={{ padding: 32, textAlign: 'center' }}>
          <Spin />
        </div>
      ) : loadError ? (
        <Alert
          type="error"
          showIcon
          message="边端检查信息加载失败"
          description={serverMessage(loadError, '请刷新后重试。')}
          action={
            <Button
              size="small"
              icon={<ReloadOutlined />}
              onClick={() => {
                void exportQuery.refetch();
                if (view === 'create') {
                  void devicesQuery.refetch();
                  void deviceReadinessQuery.refetch();
                }
                if (taskId) void samplesQuery.refetch();
                if (view === 'result') {
                  void historyQuery.refetch();
                  if (jobId) void jobQuery.refetch();
                }
              }}
            >
              重试
            </Button>
          }
        />
      ) : view === 'result' ? (
        <Space direction="vertical" size={14} style={{ width: '100%' }}>
          {historyJobs.length ? (
            <div>
              <Typography.Text strong>检查记录</Typography.Text>
              <Select
                style={{ width: '100%', marginTop: 8 }}
                value={jobId || undefined}
                onChange={(value) => {
                  setCreatedJob(null);
                  setSelectedJobId(Number(value));
                }}
                options={historyJobs.map((job) => ({
                  value: job.id,
                  label: `任务 #${job.id} · ${
                    isQualityGateOnlyFailure(
                      currentJob && currentJob.id === job.id ? currentJob : job,
                      modelResultRows(currentJob && currentJob.id === job.id ? currentJob : job),
                    )
                      ? '检查完成（质量门禁未通过）'
                      : jobStatusLabel(job.status)
                  } · ${dateText(job.created_at)} · ${job.image_count} 张图片`,
                }))}
              />
            </div>
          ) : null}
          {currentJob ? (
            <EdgeValidationResult job={currentJob} samples={knownSamples} />
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该 BModel 还没有边端检查记录" />
          )}
        </Space>
      ) : (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Descriptions bordered size="small" column={{ xs: 1, sm: 2 }}>
            <Descriptions.Item label="边端模型">{modelName}</Descriptions.Item>
            <Descriptions.Item label="编译记录">#{exportQuery.data?.id || exportId}</Descriptions.Item>
            <Descriptions.Item label="来源任务">
              {exportQuery.data?.task_name || `任务 #${taskId || '-'}`}
            </Descriptions.Item>
            <Descriptions.Item label="设备状态">
              {readyDeviceTotal ? (
                <Space size={6} wrap>
                  <Tag color="green" icon={<CheckCircleOutlined />}>
                    {readyDeviceTotal} 台已就绪
                  </Tag>
                </Space>
              ) : (
                <Tag color="red">无就绪设备</Tag>
              )}
            </Descriptions.Item>
          </Descriptions>

          {!readyDeviceTotal ? (
            <Alert
              type="warning"
              showIcon
              message="暂时不能启动边端检查"
              description="请先在系统管理中完成设备连接和 BM1688 环境检查。"
              action={
                <Button size="small" onClick={() => navigate('/admin?view=edge-devices')}>
                  配置设备
                </Button>
              }
            />
          ) : null}

          <div>
            <Space wrap style={{ width: '100%', justifyContent: 'space-between', marginBottom: 8 }}>
              <Typography.Text strong>边端设备目录</Typography.Text>
              <Typography.Text type="secondary">
                {deviceSearch ? `搜索结果 ${deviceTotal} 台` : `共 ${deviceTotal} 台`}
              </Typography.Text>
            </Space>
            <ServerSearchInput
              value={deviceSearchInput}
              onChange={(value) => {
                setDeviceSearchInput(value);
                if (!value.trim()) {
                  setDeviceSearch('');
                  setDevicePage(0);
                }
              }}
              onSearch={(value) => {
                setDeviceSearch(value);
                setDevicePage(0);
              }}
              placeholder="按设备名称、ID、地址或账号搜索"
              ariaLabel="搜索边端设备"
              loading={devicesQuery.isFetching}
              style={{ width: 360, maxWidth: '100%', marginBottom: 10 }}
            />
            <Table<EdgeDevice>
              rowKey="id"
              size="small"
              columns={deviceColumns}
              dataSource={devices}
              loading={devicesQuery.isFetching}
              pagination={{
                current: devicePage + 1,
                pageSize: DEVICE_PAGE_SIZE,
                total: deviceTotal,
                showSizeChanger: false,
                onChange: (nextPage) => setDevicePage(nextPage - 1),
              }}
              scroll={{ x: 520 }}
              locale={{ emptyText: deviceSearch ? '没有匹配的边端设备' : '暂无边端设备' }}
            />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 16 }}>
            <div>
              <Typography.Text strong>检查方式</Typography.Text>
              <Segmented
                block
                style={{ marginTop: 8 }}
                value={mode}
                onChange={(value) => setMode(value as ValidationMode)}
                options={[
                  { value: 'run_and_eval', label: '运行并评估' },
                  { value: 'run_only', label: '仅检查运行' },
                ]}
              />
              <Typography.Text type="secondary" style={{ display: 'block', marginTop: 6, fontSize: 12 }}>
                {mode === 'run_and_eval'
                  ? '检查能否运行，并用人工拉框计算精度。'
                  : '只检查模型能否在设备上运行，不代表识别精度通过。'}
              </Typography.Text>
            </div>
            <div>
              <Typography.Text strong>检查档位</Typography.Text>
              <Segmented
                block
                style={{ marginTop: 8 }}
                value={executionProfile}
                onChange={(value) => setExecutionProfile(value as ExecutionProfile)}
                options={[
                  { value: 'quick', label: '快速检查' },
                  { value: 'certification', label: '完整检查' },
                ]}
              />
              <Typography.Text type="secondary" style={{ display: 'block', marginTop: 6, fontSize: 12 }}>
                快速检查用于日常确认；完整检查用于发布前留存完整证据。
              </Typography.Text>
            </div>
          </div>

          <div>
            <div
              style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 8 }}
            >
              <Typography.Text strong>选择检查图片</Typography.Text>
              <Typography.Text type="secondary">
                已选 {selectedSampleIds.length} 张，其中 {selectedLabeledCount} 张有人工拉框，当前结果 {samples.length}{' '}
                / 共 {sampleTotal} 张，最多 {MAX_IMAGES} 张
              </Typography.Text>
            </div>
            {mode === 'run_and_eval' && selectedSampleIds.length > 0 && selectedLabeledCount === 0 ? (
              <Alert
                style={{ marginBottom: 10 }}
                type="warning"
                showIcon
                message="请至少选择一张有人工拉框的图片，才能计算精度。"
              />
            ) : null}
            <Space wrap style={{ width: '100%', justifyContent: 'space-between', marginBottom: 10 }}>
              <ServerSearchInput
                value={sampleSearchInput}
                onChange={(value) => {
                  setSampleSearchInput(value);
                  if (!value.trim()) {
                    setSampleSearch('');
                    setSamplePage(0);
                  }
                }}
                onSearch={(value) => {
                  setSampleSearch(value);
                  setSamplePage(0);
                }}
                placeholder="按文件名或样本编号搜索"
                ariaLabel="搜索检查图片"
                loading={samplesQuery.isFetching}
                style={{ width: 360, maxWidth: '100%' }}
              />
              <Typography.Text type="secondary">
                {sampleSearch ? `搜索结果 ${sampleTotal} 张` : `共 ${sampleTotal} 张图片`}
              </Typography.Text>
            </Space>
            <Table<TaskSample>
              rowKey="id"
              size="small"
              columns={columns}
              dataSource={samples}
              loading={samplesQuery.isFetching}
              pagination={{
                current: samplePage + 1,
                pageSize: SAMPLE_PAGE_SIZE,
                total: sampleTotal,
                showSizeChanger: false,
                onChange: (nextPage) => setSamplePage(nextPage - 1),
              }}
              scroll={{ x: 620 }}
              locale={{ emptyText: sampleSearch ? '没有匹配的检查图片' : '当前任务没有可用于边端检查的图片' }}
              rowSelection={{
                selectedRowKeys: selectedSampleIds,
                preserveSelectedRowKeys: true,
                onChange: (keys) => {
                  const visibleIds = new Set(samples.map((sample) => Number(sample.id)));
                  const next = selectedSampleIds.filter((id) => !visibleIds.has(id));
                  keys.forEach((key) => {
                    const id = Number(key);
                    if (Number.isFinite(id) && !next.includes(id)) next.push(id);
                  });
                  if (next.length > MAX_IMAGES) message.warning(`单次最多选择 ${MAX_IMAGES} 张图片。`);
                  setSelectedSampleIds(next.slice(0, MAX_IMAGES));
                },
              }}
            />
          </div>
        </Space>
      )}
    </>
  );

  if (embedded) {
    return (
      <div style={{ minHeight: 420 }}>
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
            <Typography.Title level={4} style={{ margin: 0 }}>
              {view === 'create' ? '新建边端检查' : '边端检查结果'}
            </Typography.Title>
            {view === 'result' ? <Button onClick={() => setView('create')}>返回新建检查</Button> : null}
          </Space>
          {content}
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>{footer}</div>
        </Space>
      </div>
    );
  }

  return (
    <Modal
      title={view === 'create' ? '新建边端检查' : '边端检查结果'}
      open={open}
      width={900}
      centered
      destroyOnClose
      onCancel={onClose}
      styles={{ body: { maxHeight: 'calc(100vh - 160px)', overflowY: 'auto' } }}
      footer={footer}
    >
      {content}
    </Modal>
  );
}
