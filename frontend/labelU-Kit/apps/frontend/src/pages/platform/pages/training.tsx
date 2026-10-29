import { ArrowLeftOutlined, ArrowRightOutlined, DatabaseOutlined, EyeOutlined, LinkOutlined, PauseCircleOutlined, PlayCircleOutlined, PlusOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Checkbox, Descriptions, Divider, Empty, Form, Grid, Input, InputNumber, Modal, Pagination, Popconfirm, Progress, Radio, Select as AntSelect, Space as AntSpace, Spin, Steps, Table, Tabs, Tag, Typography, message } from 'antd';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ComponentProps } from 'react';

import { createIdempotencyKey } from '@/api/idempotency';
import request from '@/api/request';
import { getDatasetAnalysis, type DatasetAnalysisResponse, type DatasetLabelDistributionItem } from '@/api/services/datasetAnalysis';
import { createLabelTrainingJob, createTaskTrainingJob } from '@/api/services/training';
import ServerSearchInput from '@/components/ServerSearchInput';
import useTaskSuperuser from '@/hooks/useTaskSuperuser';
import { ACTIVE_TRAINING_STATUSES } from '../polling';
import ResourcePage from '../ResourcePage';
import { trainingRuntimeStatusLabel, trainingStatusLabel, type Row } from '../shared';
import TrainingAiReportAction from './TrainingAiReport';

type TrainingView = 'jobs' | 'auto-plan' | 'reports';

export const TARGET_CHIP_OPTIONS = [
  { value: 'bm1688', label: 'BM1688' },
  { value: 'bm1684', label: 'BM1684' },
  { value: 'rk1808', label: 'RK1808' },
] as const;

export const PRECISION_OPTIONS = [
  { value: 'fp32', label: 'FP32 · 全精度' },
  { value: 'fp16', label: 'FP16 · 半精度' },
  { value: 'int8', label: 'INT8 · 8 位量化' },
] as const;

/**
 * 一期只开放 YOLOv11 目标检测。算法能力和训练配方仍然属于后端训练
 * 合同，但由系统自动绑定，避免用户在两个含义重叠的下拉框之间误选。
 */
export const DEFAULT_TRAINING_ALGORITHM_KEY = 'yolo_detection';
export const DEFAULT_TRAINING_RECIPE_KEY = 'yolo_detection_v1';
export const DEFAULT_TRAINING_RECIPE_REF = `${DEFAULT_TRAINING_RECIPE_KEY}::1`;

export function resolveDefaultTrainingContract(
  algorithms: Array<Record<string, any>> = [],
  recipes: Array<Record<string, any>> = [],
) {
  const algorithm = algorithms.find(
    (item) => item?.algorithm_key === DEFAULT_TRAINING_ALGORITHM_KEY
      && String(item?.status || 'ACTIVE').toUpperCase() === 'ACTIVE',
  );
  const algorithmKey = algorithm?.algorithm_key || DEFAULT_TRAINING_ALGORITHM_KEY;
  const configuredRecipeKey = String(algorithm?.default_recipe || '').trim();
  const recipeKey = configuredRecipeKey || DEFAULT_TRAINING_RECIPE_KEY;
  const matchingRecipes = recipes
    .filter((item) => item?.recipe_key === recipeKey && String(item?.status || 'active').toLowerCase() === 'active')
    .sort((left, right) => Number(right?.version || 0) - Number(left?.version || 0));
  const fallbackRecipes = recipes
    .filter((item) => item?.job_type === algorithmKey && String(item?.status || 'active').toLowerCase() === 'active')
    .sort((left, right) => Number(right?.version || 0) - Number(left?.version || 0));
  const recipe = matchingRecipes[0] || fallbackRecipes[0];
  const resolvedRecipeKey = recipe?.recipe_key || recipeKey;
  const resolvedVersion = Number(recipe?.version || 1);
  return {
    algorithmKey,
    recipeRef: `${resolvedRecipeKey}::${Number.isInteger(resolvedVersion) && resolvedVersion > 0 ? resolvedVersion : 1}`,
    algorithmName: algorithm?.name || 'YOLO 通用目标检测',
    recipeName: recipe?.name || 'YOLOv11 通用检测',
  };
}

export function precisionOptionLabel(value?: string | null) {
  const normalized = String(value || '').trim().toLowerCase();
  return PRECISION_OPTIONS.find((item) => item.value === normalized)?.label || '';
}

type PlatformSelectProps = ComponentProps<typeof AntSelect>;

/**
 * Keep legacy option declarations readable while ensuring precision choices
 * always have a complete user-facing label and a menu wide enough to show it.
 */
function Select({ options, popupMatchSelectWidth, ...props }: PlatformSelectProps) {
  const normalizedOptions = options?.map((option: any) => {
    const value = String(option?.value || '').trim().toLowerCase();
    return option?.label || !precisionOptionLabel(value)
      ? option
      : { ...option, label: precisionOptionLabel(value) };
  });
  const isPrecisionSelect = normalizedOptions?.some(
    (option: any) => Boolean(precisionOptionLabel(option?.value)),
  );
  return (
    <AntSelect
      {...props}
      options={normalizedOptions}
      popupMatchSelectWidth={popupMatchSelectWidth ?? (isPrecisionSelect ? false : undefined)}
    />
  );
}

type PlatformSpaceProps = ComponentProps<typeof AntSpace>;

/** The two-column parameter rows should use the full form width. */
function Space({ style, ...props }: PlatformSpaceProps) {
  const fullWidth = style?.display === 'flex';
  return (
    <AntSpace
      {...props}
      style={fullWidth
        ? {
          ...style,
          display: 'grid',
          gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
          width: '100%',
        }
        : style}
    />
  );
}

export function trainingChipOptions(algorithm?: { supported_chips?: unknown }) {
  const configuredChips = algorithm?.supported_chips;
  const configured = Array.isArray(configuredChips)
    ? configuredChips.map((value) => String(value).trim().toLowerCase())
    : [];
  const allowed = new Set(configured);
  const filtered = TARGET_CHIP_OPTIONS.filter((chip) => allowed.has(chip.value));
  return (filtered.length ? filtered : TARGET_CHIP_OPTIONS).map((chip) => ({ ...chip }));
}

export type TrainingProgressSummary = {
  currentEpoch: number | null;
  targetEpochs: number | null;
  percent: number | null;
  label: string;
};

export function trainingProgressSummary(row?: Row | null): TrainingProgressSummary {
  const params = (row?.params || row?.hyperparams || {}) as Record<string, any>;
  const toPositiveInt = (value: unknown) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed > 0 ? Math.max(1, Math.round(parsed)) : null;
  };
  const targetEpochs = toPositiveInt(row?.epochs ?? params.epochs);
  const currentRaw = row?.current_epoch
    ?? row?.actual_epochs
    ?? params.current_epoch
    ?? params.actual_epochs
    ?? params.last_epoch_metrics?.epoch;
  const parsedCurrent = Number(currentRaw);
  const currentEpoch = Number.isFinite(parsedCurrent) && parsedCurrent >= 0
    ? Math.max(0, Math.round(parsedCurrent))
    : null;
  const status = String(row?.status || '').trim().toUpperCase();
  const completed = ['COMPLETED', 'SUCCEEDED', 'SUCCESS', 'FINISHED', 'DONE'].includes(status);
  if (currentEpoch === null) {
    return completed && targetEpochs
      ? { currentEpoch: targetEpochs, targetEpochs, percent: 100, label: `${targetEpochs} / ${targetEpochs} 轮` }
      : { currentEpoch: null, targetEpochs, percent: null, label: targetEpochs ? `尚未开始 / ${targetEpochs} 轮` : '暂无进度' };
  }
  const boundedCurrent = targetEpochs ? Math.min(currentEpoch, targetEpochs) : currentEpoch;
  const percent = targetEpochs ? Math.min(100, Math.max(0, Math.round((boundedCurrent / targetEpochs) * 100))) : null;
  return {
    currentEpoch: boundedCurrent,
    targetEpochs,
    percent,
    label: targetEpochs ? `${boundedCurrent} / ${targetEpochs} 轮` : `第 ${boundedCurrent} 轮`,
  };
}

const TRAINING_PANELS: Record<Exclude<TrainingView, 'jobs'>, { title: string; description: string }> = {
  'auto-plan': {
    title: '历史自动训练计划',
    description: '一期只读保留历史计划、样本池、评测集和质量门禁记录。',
  },
  reports: {
    title: '训练报告',
    description: '复用既有训练分析、标注对比和评测报告能力。',
  },
};

export function trainingViewFromLocation(): TrainingView {
  if (typeof window === 'undefined') return 'jobs';
  const view = new URLSearchParams(window.location.search).get('view');
  return view === 'auto-plan' || view === 'reports' ? view : 'jobs';
}

type AutoTrainingPlanSummary = {
  id: number;
  name: string;
  series_type?: string | null;
  status?: string | null;
  qualified_task_count?: number;
  eligible_sample_count?: number;
  pending_review_count?: number;
  active_round_no?: number | null;
  quality_gap_summary?: Record<string, unknown> | null;
  next_evaluation_at?: string | null;
  execution_stage?: string | null;
  active_round_id?: number | null;
  champion_job_id?: number | null;
  current_version_no?: number | null;
  version?: number | null;
  updated_at?: string | null;
  last_round?: {
    id?: number | null;
    round_no?: number | null;
    state?: string | null;
    yolo_train_job_id?: number | null;
    updated_at?: string | null;
  } | null;
};

type TrainingReportSummary = {
  id: number;
  title: string;
  task_name?: string | null;
  report_kind?: string | null;
  summary_level?: string | null;
  summary_brief?: string | null;
  error_message?: string | null;
  training_quality_status?: string | null;
  generalization_quality_status?: string | null;
  conversion_quality_status?: string | null;
  edge_usability_status?: string | null;
  release_readiness?: string | null;
  release_gate_status?: string | null;
  quality_gate_reason?: string | null;
  created_at?: string | null;
  yolo_train_job_id?: number | null;
  task_id?: number | null;
  provider_key?: string | null;
  plan_id?: number | null;
  round_id?: number | null;
};

type TrainingSummaryResponse = {
  training_jobs?: {
    total?: number;
    by_status?: Record<string, number>;
    active_count?: number;
    completed_count?: number;
    failed_count?: number;
  };
  auto_training?: {
    plans?: AutoTrainingPlanSummary[];
    plan_count?: number;
    active_plan_count?: number;
    plans_total?: number;
    page?: number;
    size?: number;
    evaluation_reports?: TrainingReportSummary[];
  };
  reports?: {
    auto?: TrainingReportSummary[];
    legacy?: TrainingReportSummary[];
    legacy_total?: number;
    total?: number;
    page?: number;
    size?: number;
  };
  pagination?: {
    page?: number;
    size?: number;
    plans_total?: number;
    reports_total?: number;
  };
};

type TrainingQualityLayer = {
  key: 'training' | 'generalization' | 'edge' | 'release';
  label: string;
  status?: string | null;
  reason?: string | null;
};

type AutoTrainingRound = {
  id?: number | null;
  round_no?: number | null;
  state?: string | null;
  yolo_train_job_id?: number | null;
  task_id?: number | null;
  evaluation_run_id?: number | null;
  bm1688_export_id?: number | null;
  edge_validation_job_id?: number | null;
  publish_job_id?: number | null;
  published_model_id?: number | null;
  publish_status?: string | null;
  decision_code?: string | null;
  stage_statuses?: Record<string, unknown> | null;
  stage_errors?: Record<string, unknown> | null;
  train_count?: number | null;
  val_count?: number | null;
  test_count?: number | null;
  eligible_sample_count?: number | null;
  updated_at?: string | null;
};

type AutoTrainingQuality = {
  status?: string | null;
  decision_code?: string | null;
  overall_metrics?: Record<string, unknown> | null;
  blocking_reasons?: unknown[] | null;
};

type TrainingJobReference = {
  id?: number | null;
  task_id?: number | null;
  task_name?: string | null;
  status?: string | null;
};

const QUALITY_STATUS_LABELS: Record<string, string> = {
  PASS: '通过',
  PASSED: '通过',
  COMPLETED: '完成',
  SUCCEEDED: '完成',
  PENDING: '待验证',
  BLOCKED: '已阻断',
  FAILED: '失败',
  REVIEW_REQUIRED: '待复核',
  NOT_APPLICABLE: '不适用',
  NOT_STARTED: '未开始',
  UNUSABLE: '不可用',
  USABLE_NEEDS_REVIEW: '可运行但效果待确认',
  CONDITIONAL_PASSED: '条件达标',
  PUBLISHABLE: '可发布',
  UNPUBLISHABLE: '不可发布',
  '需复核': '待复核',
  '不可发布': '不可发布',
  '可发布': '可发布',
  '待验证': '待验证',
  '不达标': '不达标',
  '条件达标': '条件达标',
  '未验证': '未验证',
  '不可用': '不可用',
  '有风险': '有风险',
  '可运行但效果待确认': '可运行但效果待确认',
  AUTO_QUALITY_REACHED: '训练指标达标',
};

const AUTO_PLAN_STATUS_LABELS: Record<string, string> = {
  ACTIVE: '运行中',
  WAITING_FOR_DATA: '等待数据',
  WAITING_FOR_REVIEW: '等待审核',
  WAITING_FOR_COVERAGE: '等待覆盖度',
  PENDING_APPROVAL: '待审批',
  ROUND_ACTIVE: '轮次运行中',
  AUTO_QUALITY_REACHED: '训练指标达标，待发布',
  POLICY_REVIEW_REQUIRED: '策略待复核',
  PAUSED_SAFETY: '安全暂停',
  DISABLED: '已禁用',
  PAUSED: '已暂停',
  PAUSED_MANUAL: '已手动暂停',
  COMPLETED: '已完成',
  FAILED: '失败',
  DRAFT: '草稿',
  DRAFT_INCOMPLETE: '草稿未完成',
  ARCHIVED: '已归档',
};

function summaryStatusLabel(value: unknown, labels: Record<string, string>) {
  const code = String(value ?? '').trim();
  return code ? `${labels[code.toUpperCase()] || code}（${code}）` : '未确认';
}

/** Display the plan state in Chinese while retaining the machine code as a tooltip. */
export function autoTrainingPlanStatusLabel(value: unknown) {
  const code = String(value ?? '').trim();
  return code ? AUTO_PLAN_STATUS_LABELS[code.toUpperCase()] || code : '未确认';
}

export function legacyTrainingJobUrl(taskId?: number | string | null, jobId?: number | string | null) {
  const params = new URLSearchParams({ embedded: '1', panel: 'list' });
  if (taskId) params.set('taskId', String(taskId));
  if (jobId) params.set('jobId', String(jobId));
  return `/yolo-training?${params.toString()}`;
}

export function evaluationHubUrl(taskId?: number | string | null, jobId?: number | string | null) {
  if (!taskId || !jobId) return '/yolo-eval-reports-hub';
  return `/yolo-eval-reports-hub?taskId=${encodeURIComponent(String(taskId))}&jobId=${encodeURIComponent(String(jobId))}`;
}

export function platformTrainingJobUrl(jobId?: number | string | null) {
  const params = new URLSearchParams({ view: 'jobs' });
  if (jobId) params.set('job_id', String(jobId));
  return `/training?${params.toString()}`;
}

export function platformTrainingReportsUrl(jobId?: number | string | null) {
  const params = new URLSearchParams({ view: 'reports' });
  if (jobId) params.set('job_id', String(jobId));
  return `/training?${params.toString()}`;
}

function qualityLayerTag(value?: string | null) {
  return qualityStatusTag(value);
}

function qualityLayers({
  report,
  round,
  quality,
}: {
  report?: TrainingReportSummary | null;
  round?: AutoTrainingRound | null;
  quality?: AutoTrainingQuality | null;
}): TrainingQualityLayer[] {
  const stage = (round?.stage_statuses || {}) as Record<string, any>;
  const trainingStatus = report?.training_quality_status || quality?.decision_code || quality?.status || round?.decision_code;
  const generalizationStatus = report?.generalization_quality_status || (quality?.overall_metrics && (quality.overall_metrics as any).generalization_pass === true ? 'PASS' : undefined);
  const conversionStatus = report?.conversion_quality_status || stage.BM1688 || stage.bm1688;
  const edgeStatus = report?.edge_usability_status || stage.EDGE_VALIDATION || stage.edge_validation;
  const releaseStatus = report?.release_readiness || report?.release_gate_status || (round?.published_model_id && round.publish_status ? round.publish_status : undefined);
  const blocked = (quality?.blocking_reasons || []).filter(Boolean).join('、') || null;
  return [
    { key: 'training', label: '训练质量', status: trainingStatus, reason: report?.summary_brief || null },
    { key: 'generalization', label: '泛化质量', status: generalizationStatus, reason: null },
    { key: 'edge', label: '边端可用性', status: edgeStatus || conversionStatus, reason: conversionStatus && !edgeStatus ? '转换状态纳入边端可用性，仍需完成实机验证' : null },
    { key: 'release', label: '发布就绪', status: releaseStatus || (blocked ? 'BLOCKED' : undefined), reason: report?.quality_gate_reason || blocked },
  ];
}

function QualityLayers({ report, round, quality }: { report?: TrainingReportSummary | null; round?: AutoTrainingRound | null; quality?: AutoTrainingQuality | null }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 8 }}>
      {qualityLayers({ report, round, quality }).map((item) => (
        <div key={item.key} style={{ border: '1px solid #e5e7eb', borderRadius: 6, padding: '9px 10px', minWidth: 0 }}>
          <div style={{ color: '#64748b', fontSize: 12, marginBottom: 5 }}>{item.label}</div>
          {qualityLayerTag(item.status)}
          {item.reason && <div style={{ color: '#64748b', fontSize: 12, lineHeight: 1.45, marginTop: 5, wordBreak: 'break-word' }}>{item.reason}</div>}
        </div>
      ))}
    </div>
  );
}

function qualityStatusTag(value?: string | null) {
  const normalized = String(value || 'PENDING').toUpperCase();
  const color = normalized === 'PASS' || normalized === 'PASSED' || normalized === 'COMPLETED' || normalized === 'SUCCEEDED' || normalized === '可发布' || normalized === '通过' || normalized === '达标'
    ? 'green'
    : normalized === 'BLOCKED' || normalized === 'FAILED' || normalized === '不可发布' || normalized === '不可用' || normalized === '不达标'
    ? 'red'
    : normalized === 'AUTO_QUALITY_REACHED'
    ? 'blue'
    : 'gold';
  return <Tag color={color}>{QUALITY_STATUS_LABELS[normalized] || normalized}</Tag>;
}

function SummaryState({
  loading,
  error,
  empty,
  onRetry,
  children,
}: {
  loading: boolean;
  error: unknown;
  empty: boolean;
  onRetry: () => void;
  children: React.ReactNode;
}) {
  if (loading) return <div role="status" aria-live="polite" style={{ padding: '32px 0', textAlign: 'center' }}><Spin /></div>;
  if (error) return <Alert type="error" showIcon message="摘要加载失败" description="请检查访问权限或稍后重试。" action={<Button size="small" onClick={onRetry}>重试</Button>} />;
  if (empty) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无数据" />;
  return <>{children}</>;
}

function LegacyTrainingPanel({ view }: { view: Exclude<TrainingView, 'jobs'> }) {
  const config = TRAINING_PANELS[view];
  const [selectedPlan, setSelectedPlan] = useState<AutoTrainingPlanSummary | null>(null);
  const [selectedReport, setSelectedReport] = useState<TrainingReportSummary | null>(null);
  const [searchDraft, setSearchDraft] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [page, setPage] = useState(0);
  const pageSize = 20;
  const query = useQuery({
    queryKey: ['training-summary', view, searchQuery, page, pageSize],
    queryFn: async () => {
      // Keep the initial request compatible with clients and tests that use
      // the original no-parameter summary endpoint. Search and pagination
      // become explicit server-side query parameters after the first screen.
      const params = (searchQuery || page > 0)
        ? { q: searchQuery || undefined, page, size: pageSize }
        : undefined;
      const response = params
        ? await request.get('/v1/training/summary', { params })
        : await request.get('/v1/training/summary');
      return (response.data?.data ?? response.data) as TrainingSummaryResponse;
    },
    enabled: Boolean(localStorage.getItem('token')),
    staleTime: 15_000,
    retry: false,
  });
  const autoBody = query.data?.auto_training ?? {};
  const summaryReportBody = query.data?.reports ?? {};
  const planRows = Array.isArray(autoBody.plans) ? autoBody.plans : [];
  const autoReportRows = Array.isArray(summaryReportBody.auto)
    ? summaryReportBody.auto
    : Array.isArray(autoBody.evaluation_reports)
      ? autoBody.evaluation_reports
      : [];
  const legacyReportRows = Array.isArray(summaryReportBody.legacy) ? summaryReportBody.legacy : [];
  const rows = (view === 'auto-plan' ? planRows : [...autoReportRows, ...legacyReportRows]) as Array<AutoTrainingPlanSummary | TrainingReportSummary>;
  const total = view === 'auto-plan'
    ? Number(autoBody.plans_total ?? query.data?.pagination?.plans_total ?? autoBody.plan_count ?? planRows.length)
    : Number(summaryReportBody.total ?? query.data?.pagination?.reports_total ?? summaryReportBody.legacy_total ?? rows.length);
  const submitSearch = (value: string) => {
    const normalized = value.trim();
    setSearchDraft(normalized);
    setSearchQuery(normalized);
    setPage(0);
  };
  const updateSearchDraft = (value: string) => {
    setSearchDraft(value);
    // Clearing the control is an explicit reset action; restore the first
    // unfiltered page immediately while ordinary typing remains local-only.
    if (!value.trim() && searchQuery) submitSearch('');
  };
  const planDetailQuery = useQuery({
    queryKey: ['training-plan-detail', selectedPlan?.id],
    queryFn: async () => {
      const response = await request.get(`/v1/auto-training-plans/${selectedPlan?.id}`);
      return (response.data?.data ?? response.data) as AutoTrainingPlanSummary & { last_round?: AutoTrainingRound; current_version?: Record<string, any> };
    },
    enabled: view === 'auto-plan' && Boolean(selectedPlan?.id),
    staleTime: 10_000,
    retry: false,
  });
  const selectedPlanRoundId = planDetailQuery.data?.last_round?.id || selectedPlan?.last_round?.id || selectedPlan?.active_round_id;
  const planRoundQuery = useQuery({
    queryKey: ['training-plan-round-detail', selectedPlanRoundId],
    queryFn: async () => {
      const response = await request.get(`/v1/auto-training-rounds/${selectedPlanRoundId}`);
      return (response.data?.data ?? response.data) as AutoTrainingRound;
    },
    enabled: view === 'auto-plan' && Boolean(selectedPlanRoundId),
    staleTime: 10_000,
    retry: false,
  });
  const planQualityQuery = useQuery({
    queryKey: ['training-plan-round-quality', selectedPlanRoundId],
    queryFn: async () => {
      const response = await request.get(`/v1/auto-training-rounds/${selectedPlanRoundId}/quality`);
      return (response.data?.data ?? response.data) as AutoTrainingQuality;
    },
    enabled: view === 'auto-plan' && Boolean(selectedPlanRoundId),
    staleTime: 10_000,
    retry: false,
  });
  const reportDetailQuery = useQuery({
    queryKey: ['training-report-detail', selectedReport?.id],
    queryFn: async () => {
      const response = await request.get(`/v1/yolo-eval-reports/${selectedReport?.id}`);
      return (response.data?.data ?? response.data) as TrainingReportSummary & { body?: string | null; suggested_params?: string | null };
    },
    enabled: view === 'reports' && Boolean(selectedReport?.id) && selectedReport?.report_kind !== 'auto_training_evaluation',
    staleTime: 10_000,
    retry: false,
  });
  const reportRoundQuery = useQuery({
    queryKey: ['training-report-round-detail', selectedReport?.round_id],
    queryFn: async () => {
      const response = await request.get(`/v1/auto-training-rounds/${selectedReport?.round_id}`);
      return (response.data?.data ?? response.data) as AutoTrainingRound;
    },
    enabled: view === 'reports' && Boolean(selectedReport?.round_id),
    staleTime: 10_000,
    retry: false,
  });
  const reportQualityQuery = useQuery({
    queryKey: ['training-report-round-quality', selectedReport?.round_id],
    queryFn: async () => {
      const response = await request.get(`/v1/auto-training-rounds/${selectedReport?.round_id}/quality`);
      return (response.data?.data ?? response.data) as AutoTrainingQuality;
    },
    enabled: view === 'reports' && Boolean(selectedReport?.round_id),
    staleTime: 10_000,
    retry: false,
  });
  const report = (reportDetailQuery.data || selectedReport) as (TrainingReportSummary & { body?: string | null; suggested_params?: string | null }) | null;
  const reportRound = reportRoundQuery.data;
  const linkedJobId = planDetailQuery.data?.last_round?.yolo_train_job_id || selectedPlan?.last_round?.yolo_train_job_id || planDetailQuery.data?.champion_job_id || selectedPlan?.champion_job_id || report?.yolo_train_job_id || reportRound?.yolo_train_job_id;
  const linkedJobQuery = useQuery({
    queryKey: ['training-associated-job', linkedJobId],
    queryFn: async () => {
      const response = await request.get(`/v1/training/jobs/${linkedJobId}`);
      return (response.data?.data ?? response.data) as TrainingJobReference;
    },
    enabled: Boolean(linkedJobId) && Boolean(selectedPlan || selectedReport),
    staleTime: 10_000,
    retry: false,
  });
  const reportJobId = report?.yolo_train_job_id || reportRound?.yolo_train_job_id || linkedJobQuery.data?.id;
  const reportTaskId = report?.task_id || reportRound?.task_id || linkedJobQuery.data?.task_id;
  const reportBody = report?.body || report?.summary_brief || '';
  return (
    <>
      <section aria-label={config.title} style={{ marginTop: 14 }}>
      <Alert
        type="info"
        showIcon
        message={config.title}
        description={`${config.description} 当前页面展示历史摘要、质量门禁和关联作业。`}
      />
      <div style={{ marginTop: 14 }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12 }}>
          <ServerSearchInput
            value={searchDraft}
            onChange={updateSearchDraft}
            onSearch={submitSearch}
            loading={query.isFetching}
            placeholder={view === 'auto-plan' ? '按计划名称、系列或状态搜索' : '按报告、任务或提供方搜索'}
            ariaLabel={view === 'auto-plan' ? '搜索历史自动训练计划' : '搜索训练报告'}
            style={{ maxWidth: 420 }}
          />
          {searchQuery && <Tag color="blue">已筛选</Tag>}
        </div>
        <SummaryState loading={query.isLoading} error={query.error} empty={!rows.length && !total} onRetry={() => void query.refetch()}>
          {view === 'auto-plan' ? (
            <Table<AutoTrainingPlanSummary>
              size="small"
              rowKey="id"
              pagination={false}
              dataSource={rows as AutoTrainingPlanSummary[]}
              columns={[
                { title: '计划', dataIndex: 'name', key: 'name', ellipsis: true },
                { title: '系列', dataIndex: 'series_type', key: 'series_type', render: (value) => value || '-' },
                { title: '状态', dataIndex: 'status', key: 'status', render: (value) => <Tag title={String(value || '')}>{autoTrainingPlanStatusLabel(value)}</Tag> },
                { title: '合格任务 / 图片', key: 'eligible', render: (_, row) => `${row.qualified_task_count ?? 0} / ${row.eligible_sample_count ?? 0}` },
                { title: '待审核', dataIndex: 'pending_review_count', key: 'pending_review_count', render: (value) => value ?? 0 },
                { title: '当前轮次', dataIndex: 'active_round_no', key: 'active_round_no', render: (value, row) => value ? `第 ${value} 轮` : row.last_round?.round_no ? `第 ${row.last_round.round_no} 轮（${row.last_round.state || '未知'}）` : '-' },
                { title: '最近更新', dataIndex: 'updated_at', key: 'updated_at', render: (value, row) => { const timestamp = value || row.last_round?.updated_at; return timestamp ? new Date(timestamp).toLocaleString() : '-'; } },
                { title: '详情', key: 'detail', width: 140, render: (_, row) => <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => setSelectedPlan(row)}>查看计划</Button> },
              ]}
            />
          ) : (
            <Table<TrainingReportSummary>
              size="small"
              rowKey="id"
              pagination={false}
              dataSource={rows as TrainingReportSummary[]}
              columns={[
                { title: '报告', dataIndex: 'title', key: 'title', ellipsis: true },
                { title: '标注任务', dataIndex: 'task_name', key: 'task_name', render: (value) => value || '-' },
                { title: '摘要', dataIndex: 'summary_brief', key: 'summary_brief', ellipsis: true, render: (value, row) => row.error_message || value || '等待生成' },
                { title: '训练评估', dataIndex: 'training_quality_status', key: 'training_quality_status', render: (value, row) => qualityStatusTag(value || row.summary_level) },
                { title: '发布就绪', dataIndex: 'release_readiness', key: 'release_readiness', render: (value, row) => <span title={row.quality_gate_reason || undefined}>{qualityStatusTag(value || row.release_gate_status)}</span> },
                { title: '生成时间', dataIndex: 'created_at', key: 'created_at', render: (value) => value ? new Date(value).toLocaleString() : '-' },
                { title: '详情', key: 'detail', width: 150, render: (_, row) => <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => setSelectedReport(row)}>查看报告</Button> },
              ]}
            />
          )}
        </SummaryState>
        {total > pageSize && (
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 12 }}>
            <Pagination
              size="small"
              current={page + 1}
              pageSize={pageSize}
              total={total}
              showSizeChanger={false}
              showTotal={(value) => `共 ${value} 条`}
              onChange={(nextPage) => setPage(nextPage - 1)}
            />
          </div>
        )}
      </div>
    </section>
      <Modal
        title={selectedPlan ? `${selectedPlan.name} · 历史自动训练计划` : '历史自动训练计划'}
        open={Boolean(selectedPlan)}
        onCancel={() => setSelectedPlan(null)}
        footer={null}
        centered
        width={620}
        destroyOnClose
      >
        {planDetailQuery.isLoading ? <div role="status" aria-live="polite"><Spin /></div> : planDetailQuery.error ? <Alert type="error" showIcon message="计划详情加载失败" description="请刷新后重试。" action={<Button size="small" onClick={() => void planDetailQuery.refetch()}>重试</Button>} /> : planDetailQuery.data ? (() => {
          const detail = planDetailQuery.data;
          const round = planRoundQuery.data || detail.last_round;
          const quality = planQualityQuery.data;
           const planJobId = round?.yolo_train_job_id || detail.champion_job_id;
           const planTaskId = linkedJobQuery.data?.task_id;
          return <>
            <Alert type={String(detail.status || '').toUpperCase().includes('FAILED') ? 'error' : 'info'} showIcon message={autoTrainingPlanStatusLabel(detail.status)} description={detail.quality_gap_summary && typeof detail.quality_gap_summary === 'object' ? Object.values(detail.quality_gap_summary).filter(Boolean).join('；') : '计划状态、数据准入和最近轮次均来自系统历史记录。'} />
            <Descriptions bordered size="small" column={1} style={{ marginTop: 14 }}>
              <Descriptions.Item label="计划编号">#{detail.id}</Descriptions.Item>
              <Descriptions.Item label="系列">{detail.series_type || '-'}</Descriptions.Item>
              <Descriptions.Item label="配置版本">v{detail.current_version_no ?? detail.version ?? '-'}</Descriptions.Item>
              <Descriptions.Item label="合格任务 / 样本">{detail.qualified_task_count ?? 0} / {detail.eligible_sample_count ?? 0}</Descriptions.Item>
              <Descriptions.Item label="待审核">{detail.pending_review_count ?? 0}</Descriptions.Item>
              <Descriptions.Item label="当前轮次">{round?.round_no ? `第 ${round.round_no} 轮（${round.state || '未知'}）` : '暂无轮次'}</Descriptions.Item>
            </Descriptions>
            <Divider orientation="left" plain>质量四层</Divider>
            <QualityLayers round={round} quality={quality} />
          {planJobId && <Space wrap style={{ marginTop: 14 }}><Button type="primary" size="small" icon={<LinkOutlined />} href={platformTrainingJobUrl(planJobId)}>查看关联训练作业</Button><Button size="small" href={platformTrainingReportsUrl(planJobId)}>查看训练报告</Button></Space>}
          </>;
        })() : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无计划详情" />}
      </Modal>
      <Modal
        title={report?.title || selectedReport?.title || '训练报告'}
        open={Boolean(selectedReport)}
        onCancel={() => setSelectedReport(null)}
        footer={null}
        centered
        width={720}
        destroyOnClose
      >
        {reportDetailQuery.isLoading || (selectedReport?.report_kind === 'auto_training_evaluation' && reportQualityQuery.isLoading) ? <div role="status" aria-live="polite"><Spin /></div> : reportDetailQuery.error && selectedReport?.report_kind !== 'auto_training_evaluation' ? <Alert type="error" showIcon message="报告详情加载失败" description="请刷新后重试。" action={<Button size="small" onClick={() => void reportDetailQuery.refetch()}>重试</Button>} /> : report ? <>
          <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="报告编号">#{report.id}</Descriptions.Item>
            <Descriptions.Item label="任务">{report.task_name || (reportTaskId ? `任务 #${reportTaskId}` : '-')}</Descriptions.Item>
            <Descriptions.Item label="报告类型">{report.report_kind === 'auto_training_evaluation' ? '自动训练评估' : report.report_kind || '-'}</Descriptions.Item>
            <Descriptions.Item label="生成时间">{report.created_at ? new Date(report.created_at).toLocaleString() : '-'}</Descriptions.Item>
          </Descriptions>
          <Divider orientation="left" plain>质量四层</Divider>
          <QualityLayers report={report} round={reportRound} quality={reportQualityQuery.data} />
          {report.quality_gate_reason && <Alert type="warning" showIcon style={{ marginTop: 14 }} message="发布门禁说明" description={report.quality_gate_reason} />}
          <Divider orientation="left" plain>报告摘要</Divider>
          <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.65, color: '#334155', maxHeight: 280, overflow: 'auto' }}>{reportBody || '暂无报告正文，等待评估 Worker 回报。'}</div>
          {report.suggested_params && <><Divider orientation="left" plain>下一轮建议</Divider><div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.55, color: '#475569', maxHeight: 180, overflow: 'auto' }}>{report.suggested_params}</div></>}
          {reportJobId && <Space wrap style={{ marginTop: 14 }}><Button type="primary" size="small" icon={<LinkOutlined />} href={platformTrainingJobUrl(reportJobId)}>查看关联训练作业</Button><Button size="small" href={platformTrainingReportsUrl(reportJobId)}>查看训练报告</Button></Space>}
        </> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无报告详情" />}
      </Modal>
    </>
  );
}

type TrainingCandidate = {
  task_id: number;
  task_name?: string | null;
  training_ready?: boolean | null;
  task_status?: string | null;
  training_state?: string | null;
  done_count?: number;
  total_count?: number;
  sample_count?: number;
  total_sample_count?: number;
  required_sample_count?: number;
  done_sample_count?: number;
  completed_sample_count?: number;
  trainable_sample_count?: number;
  eligible_sample_count?: number;
  pending_sample_count?: number;
  incomplete_sample_count?: number;
  excluded_sample_count?: number;
  completion_ratio?: number | null;
  quality_pass_ratio?: number | null;
  minimum_threshold_met?: boolean | null;
  minimum_threshold_reason?: string | null;
  excluded_reasons?: Record<string, number> | null;
  reason?: string | null;
  instance_count?: number;
  dataset_id?: number | null;
  dataset_label_contract_id?: number | null;
  task_label_scope_mode?: 'FULL' | 'SUBSET';
  label_set_signature?: string | null;
  class_order_signature?: string | null;
  contract_labels?: Array<{
    label_id?: number;
    label_key?: string | null;
    label_value?: string | null;
    color?: string | null;
  }>;
};

/**
 * Task-first training can merge candidates only when their complete label
 * contracts are identical. The signatures are authoritative when present:
 * the label-set signature captures membership, while the class-order
 * signature captures the model class index mapping. Older API responses may
 * omit signatures, so retain the legacy label/contract-id fallback below.
 */
export function trainingCandidateContractKey(candidate: TrainingCandidate): string | null {
  const labelSetSignature = String(candidate.label_set_signature ?? '').trim();
  const classOrderSignature = String(candidate.class_order_signature ?? '').trim();
  if (labelSetSignature && classOrderSignature) {
    return `signatures:${labelSetSignature}:${classOrderSignature}`;
  }
  const labels = (candidate.contract_labels || [])
    .map((label) => String(label.label_id ?? label.label_value ?? label.label_key ?? '').trim())
    .filter(Boolean)
    .sort();
  if (labels.length) return `labels:${labels.join('|')}`;
  const contractId = Number(candidate.dataset_label_contract_id);
  return Number.isInteger(contractId) && contractId > 0 ? `contract:${contractId}` : null;
}

export function trainingCandidatesShareLabelContract(
  left: TrainingCandidate,
  right: TrainingCandidate,
): boolean {
  const leftKey = trainingCandidateContractKey(left);
  const rightKey = trainingCandidateContractKey(right);
  // Older candidate responses do not include contract metadata. Let the
  // server perform the authoritative check when either side is unannotated.
  return !leftKey || !rightKey || leftKey === rightKey;
}

/**
 * Build a stable identity for the candidate snapshot used by iteration
 * preview.  Candidate objects are refreshed independently from the preview
 * query, so the preview must be invalidated when eligibility or the label
 * contract changes even if the task id stays the same.
 */
export function trainingCandidateSnapshotKey(candidate?: TrainingCandidate | null): string {
  if (!candidate) return 'missing';
  const value = (item: unknown) => String(item ?? '').trim();
  return [
    value(candidate.task_id),
    value(candidate.task_name),
    value(candidate.dataset_id),
    value(trainingCandidateContractKey(candidate)),
    value(candidate.task_label_scope_mode),
    value(candidate.label_set_signature),
    value(candidate.class_order_signature),
    value(candidate.training_state),
    value(candidate.training_ready),
    value(candidate.minimum_threshold_met),
    value(candidate.minimum_threshold_reason),
    value(candidate.done_sample_count ?? candidate.completed_sample_count ?? candidate.done_count),
    value(candidate.total_sample_count ?? candidate.sample_count ?? candidate.total_count),
    value(candidate.trainable_sample_count ?? candidate.eligible_sample_count),
    value(candidate.pending_sample_count),
    value(candidate.excluded_sample_count),
    value(candidate.completion_ratio),
    value(candidate.quality_pass_ratio),
  ].join('|');
}

/** Return an order-stable key for all currently selected candidate snapshots. */
export function trainingCandidateSelectionSnapshotKey(
  candidates: TrainingCandidate[],
  selectedIds: number[],
): string {
  const byId = new Map(candidates.map((candidate) => [Number(candidate.task_id), candidate]));
  return selectedIds
    .map((taskId) => `${taskId}:${trainingCandidateSnapshotKey(byId.get(Number(taskId)))}`)
    .join('||');
}

type TrainingCreateErrorResponse = {
  response?: {
    status?: number;
    data?: unknown;
  };
};

function firstTrainingErrorMessage(value: unknown): string | undefined {
  if (typeof value === 'string') {
    const text = value.trim();
    return text || undefined;
  }
  if (!value || typeof value !== 'object') return undefined;
  const payload = value as Record<string, unknown>;
  // The LabelU API uses `msg`; keep it ahead of the generic Axios message.
  for (const key of ['msg', 'message', 'detail']) {
    const text = firstTrainingErrorMessage(payload[key]);
    if (text) return text;
  }
  return undefined;
}

/** Resolve the actionable server reason before applying status fallbacks. */
export function trainingCreateErrorMessage(error: unknown): string {
  const response = (error as TrainingCreateErrorResponse | null)?.response;
  const serverMessage = firstTrainingErrorMessage(response?.data);
  if (serverMessage) return serverMessage;
  const status = Number(response?.status);
  if (status === 403) return '当前账号无权创建训练作业';
  if (status === 409) return '任务或训练资源状态已变化，请刷新候选任务后重试';
  if (status === 422) return '训练参数不符合服务端约束，请检查后重试';
  return '创建训练作业失败，请稍后重试';
}

export type TrainingCandidateState = 'COMPLETE_READY' | 'PARTIAL_READY' | 'NOT_READY';

export type TrainingCandidateStats = {
  total: number;
  completed: number;
  trainable: number;
  pending: number;
  excluded: number;
  completionRatio: number | null;
  qualityPassRatio: number | null;
};

function firstFiniteCandidateNumber(candidate: TrainingCandidate, keys: string[]): number | undefined {
  for (const key of keys) {
    const value = Number((candidate as Record<string, unknown>)[key]);
    if (Number.isFinite(value) && value >= 0) return value;
  }
  return undefined;
}

/**
 * Normalize the candidate statistics from both the current API and the
 * incremental task-admission API. The old response only exposes done_count;
 * new responses expose the full sample-level training accounting.
 */
export function trainingCandidateStats(candidate: TrainingCandidate): TrainingCandidateStats {
  const completed = firstFiniteCandidateNumber(candidate, [
    'completed_sample_count',
    'done_sample_count',
    'done_count',
  ]) ?? 0;
  const trainable = firstFiniteCandidateNumber(candidate, [
    'trainable_sample_count',
    'eligible_sample_count',
    'done_count',
  ]) ?? 0;
  const total = firstFiniteCandidateNumber(candidate, [
    'total_sample_count',
    'required_sample_count',
    'total_count',
    'sample_count',
  ]) ?? Math.max(completed, trainable);
  const pending = firstFiniteCandidateNumber(candidate, [
    'pending_sample_count',
    'incomplete_sample_count',
  ]) ?? Math.max(total - completed, 0);
  const excluded = firstFiniteCandidateNumber(candidate, [
    'excluded_sample_count',
    'invalid_image_count',
  ]) ?? Math.max(completed - trainable, 0);
  const rawCompletion = Number(candidate.completion_ratio);
  const completionRatio = Number.isFinite(rawCompletion)
    ? (rawCompletion > 1 ? rawCompletion / 100 : rawCompletion)
    : total > 0 ? completed / total : null;
  const rawQuality = Number(candidate.quality_pass_ratio);
  const qualityPassRatio = Number.isFinite(rawQuality)
    ? (rawQuality > 1 ? rawQuality / 100 : rawQuality)
    : completed > 0 ? trainable / completed : null;
  return {
    total,
    completed,
    trainable,
    pending,
    excluded,
    completionRatio,
    qualityPassRatio,
  };
}

export function trainingCandidateState(candidate: TrainingCandidate): TrainingCandidateState {
  const explicit = String(candidate.training_state || '').trim().toUpperCase();
  if (candidate.minimum_threshold_met === false) return 'NOT_READY';
  if (['NOT_READY', 'NOT_TRAINABLE', 'UNAVAILABLE', 'BLOCKED', 'NONE'].includes(explicit)) {
    return 'NOT_READY';
  }
  if (['COMPLETE_READY', 'FULL_READY', 'FULLY_READY', 'COMPLETE', 'READY'].includes(explicit)) {
    return 'COMPLETE_READY';
  }
  if (['PARTIAL_READY', 'PARTIAL_TRAINABLE', 'PARTIALLY_READY', 'PARTIAL'].includes(explicit)) {
    return 'PARTIAL_READY';
  }
  const stats = trainingCandidateStats(candidate);
  if (stats.trainable <= 0) return 'NOT_READY';
  if (
    candidate.training_ready === true
    || (stats.total > 0 && stats.completed >= stats.total && stats.excluded <= 0)
  ) {
    return 'COMPLETE_READY';
  }
  return 'PARTIAL_READY';
}

export function trainingCandidateCanTrain(candidate: TrainingCandidate): boolean {
  return trainingCandidateState(candidate) !== 'NOT_READY'
    && trainingCandidateStats(candidate).trainable > 0;
}

export function trainingCandidateStateLabel(state: TrainingCandidateState): string {
  if (state === 'COMPLETE_READY') return '完整可训练';
  if (state === 'PARTIAL_READY') return '部分可训练';
  return '暂不可训练';
}

export function trainingCandidateRatioLabel(value: number | null): string {
  return value == null ? '-' : `${(value * 100).toFixed(1)}%`;
}

function TrainingCandidateSummary({
  candidate,
  compact = false,
}: {
  candidate: TrainingCandidate;
  compact?: boolean;
}) {
  const stats = trainingCandidateStats(candidate);
  const state = trainingCandidateState(candidate);
  const stateColor = state === 'COMPLETE_READY' ? 'green' : state === 'PARTIAL_READY' ? 'gold' : 'default';
  const metricStyle = { color: '#334155', fontSize: 12, lineHeight: 1.55 };
  return (
    <div style={{ width: '100%', minWidth: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <Tag color="blue" style={{ marginInlineEnd: 0 }}>标注任务</Tag>
        <strong style={{ color: '#0f172a', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {candidate.task_name || `任务 #${candidate.task_id}`}
        </strong>
        <Tag color={stateColor} style={{ marginInlineEnd: 0 }}>{trainingCandidateStateLabel(state)}</Tag>
      </div>
      <div style={{ color: '#64748b', fontSize: 12, lineHeight: 1.55, marginTop: 3 }}>
        ID {candidate.task_id}
        {candidate.task_status ? ` · 任务状态：${candidate.task_status}` : ''}
      </div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: compact ? 'repeat(2, minmax(0, 1fr))' : 'repeat(4, minmax(0, 1fr))',
          gap: '3px 12px',
          marginTop: 7,
        }}
      >
        <span style={metricStyle}>总图片 <strong>{stats.total.toLocaleString('zh-CN')}</strong></span>
        <span style={metricStyle}>已完成 <strong>{stats.completed.toLocaleString('zh-CN')}</strong></span>
        <span style={{ ...metricStyle, color: state === 'NOT_READY' ? '#64748b' : '#166534' }}>
          可训练 <strong>{stats.trainable.toLocaleString('zh-CN')}</strong>
        </span>
        <span style={metricStyle}>待处理 <strong>{stats.pending.toLocaleString('zh-CN')}</strong></span>
      </div>
      <div style={{ color: '#64748b', fontSize: 12, lineHeight: 1.55, marginTop: 3 }}>
        完成率 {trainingCandidateRatioLabel(stats.completionRatio)}
        {' · '}
        合格率 {trainingCandidateRatioLabel(stats.qualityPassRatio)}
        {stats.excluded > 0 ? ` · 排除 ${stats.excluded.toLocaleString('zh-CN')} 张` : ''}
      </div>
      {state === 'NOT_READY' && (
        <div style={{ color: '#b45309', fontSize: 12, lineHeight: 1.55, marginTop: 3 }}>
          {candidate.minimum_threshold_reason || candidate.reason || '当前没有达到最低训练数据要求'}
        </div>
      )}
      {candidate.contract_labels && candidate.contract_labels.length > 0 && (
        <div style={{ marginTop: 5 }}>
          {candidate.contract_labels.map((label) => (
            <Tag key={label.label_id || label.label_value || label.label_key} color={label.color || undefined} style={{ marginBottom: 2 }}>
              {label.label_value || label.label_key}
            </Tag>
          ))}
        </div>
      )}
    </div>
  );
}

function TrainingCandidateBrowserControls({
  searchInput,
  placeholder,
  candidateLabel,
  page,
  pageSize,
  total,
  currentCount,
  selectedCount,
  loading,
  onSearchInputChange,
  onSearch,
  onPageChange,
}: {
  searchInput: string;
  placeholder: string;
  candidateLabel: string;
  page: number;
  pageSize: number;
  total: number;
  currentCount: number;
  selectedCount: number;
  loading?: boolean;
  onSearchInputChange: (value: string) => void;
  onSearch: (value: string) => void;
  onPageChange: (page: number) => void;
}) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <Input.Search
          allowClear
          enterButton="搜索"
          value={searchInput}
          placeholder={placeholder}
          loading={loading}
          onChange={(event) => onSearchInputChange(event.target.value)}
          onSearch={onSearch}
          style={{ flex: '1 1 280px', minWidth: 220 }}
        />
        <span style={{ color: '#64748b', fontSize: 12, whiteSpace: 'nowrap' }}>
          共 {Number(total || 0).toLocaleString('zh-CN')} 个{candidateLabel} · 当前页 {currentCount} 个 · 已选 {selectedCount} 个
        </span>
      </div>
      {total > pageSize && (
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 8 }}>
          <Pagination
            size="small"
            current={page + 1}
            pageSize={pageSize}
            total={total}
            showSizeChanger={false}
            showTotal={(value) => `共 ${value.toLocaleString('zh-CN')} 个${candidateLabel}`}
            onChange={(nextPage) => onPageChange(nextPage - 1)}
          />
        </div>
      )}
    </div>
  );
}

function TrainingGlobalTagConditionSelector({
  dimensions,
  conditions,
  loading,
  error,
  disabled,
  onChange,
}: {
  dimensions: TrainingGlobalTagDimension[];
  conditions: Record<string, string[]>;
  loading?: boolean;
  error?: unknown;
  disabled?: boolean;
  onChange: (fieldKey: string, values: string[]) => void;
}) {
  return (
    <div
      style={{
        border: '1px solid #dbeafe',
        borderRadius: 8,
        background: '#f8fbff',
        padding: '12px 14px',
        marginBottom: 14,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
        <strong style={{ color: '#0f172a' }}>全局工具标签筛选（可选）</strong>
        <Tag color={Object.keys(conditions).length ? 'blue' : 'default'} style={{ marginInlineEnd: 0 }}>
          {Object.keys(conditions).length ? `已选 ${Object.keys(conditions).length} 个维度` : '未启用'}
        </Tag>
      </div>
      <div style={{ color: '#64748b', fontSize: 12, lineHeight: 1.6, margin: '4px 0 10px' }}>
        作为样本范围过滤器与任务或标签训练组合；同一维度多选为“或”，不同维度之间为“且”。不改变模型类别。
      </div>
      {loading ? (
        <div role="status" aria-live="polite" style={{ color: '#64748b', fontSize: 12, padding: '8px 0' }}>
          <Spin size="small" /> <span style={{ marginLeft: 6 }}>正在加载全局工具标签</span>
        </div>
      ) : error ? (
        <Alert
          type="warning"
          showIcon
          message="全局工具标签暂时无法加载"
          description="不影响按任务或按标签训练；刷新后可重新尝试添加条件。"
        />
      ) : !dimensions.length ? (
        <div style={{ color: '#94a3b8', fontSize: 12 }}>当前尚未配置可用的全局工具标签维度。</div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: 10 }}>
          {dimensions.map((dimension) => {
            const fieldKey = String(dimension.field_key || '').trim();
            if (!fieldKey) return null;
            const selectedValues = conditions[fieldKey] || [];
            return (
              <div key={dimension.id || fieldKey} style={{ minWidth: 0 }}>
                <div style={{ color: '#334155', fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
                  {dimension.name_cn || fieldKey}
                  <span style={{ color: '#94a3b8', fontWeight: 400, marginLeft: 5 }}>{fieldKey}</span>
                </div>
                <Select
                  mode="multiple"
                  allowClear
                  showSearch
                  optionFilterProp="label"
                  disabled={disabled}
                  value={selectedValues}
                  maxTagCount="responsive"
                  placeholder={disabled ? '请先选择逻辑数据集' : `选择${dimension.name_cn || fieldKey}`}
                  options={(dimension.options || []).map((option) => ({
                    value: String(option.value_en || '').trim(),
                    label: `${option.name_cn || option.value_en}${option.name_cn && option.value_en ? `（${option.value_en}）` : ''}`,
                  })).filter((option) => option.value)}
                  onChange={(values) => onChange(fieldKey, (values as unknown[]).map((value) => String(value)).filter(Boolean))}
                  style={{ width: '100%' }}
                />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

const TRAINING_PREVIEW_COLORS = ['#1677ff', '#13c2c2', '#52c41a', '#faad14', '#eb2f96', '#722ed1', '#fa541c', '#2f54eb'];

function TrainingLabelDistributionPreview({
  labels,
  totalImages,
  scope,
  loading,
  error,
}: {
  labels: DatasetLabelDistributionItem[];
  totalImages: number;
  scope: string;
  loading: boolean;
  error?: unknown;
}) {
  if (loading) {
    return <div role="status" aria-live="polite" style={{ padding: '18px 0', textAlign: 'center' }}><Spin size="small" /> <span style={{ color: '#64748b', fontSize: 12 }}>正在统计训练前标签框分布</span></div>;
  }
  if (error) {
    return <Alert type="warning" showIcon message="训练前标签框分布暂时无法加载" description="不影响创建训练；请刷新数据集或稍后重试。" />;
  }
  const entries = labels
    .map((item, index) => ({
      label: item.label,
      value: Math.max(0, Number(item.instance_count || 0)),
      color: TRAINING_PREVIEW_COLORS[index % TRAINING_PREVIEW_COLORS.length],
    }))
    .sort((left, right) => right.value - left.value || left.label.localeCompare(right.label));
  const totalBoxes = entries.reduce((sum, item) => sum + item.value, 0);
  let offset = 0;
  return (
    <div style={{ border: '1px solid #dbeafe', borderRadius: 8, background: '#f8fbff', padding: 12, marginBottom: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, flexWrap: 'wrap', marginBottom: 4 }}>
        <strong style={{ color: '#0f172a' }}>训练前标签框分布</strong>
        <span style={{ color: '#64748b', fontSize: 12 }}>{scope}</span>
      </div>
      <div style={{ color: '#64748b', fontSize: 12, marginBottom: 10 }}>按当前训练范围统计已存在的拉框数量；扇形大小表示框数量占比，不代表模型精度。</div>
      {!totalBoxes ? (
        <div style={{ color: '#94a3b8', fontSize: 13, padding: '20px 0', textAlign: 'center' }}>当前范围暂无已完成拉框数据</div>
      ) : (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <svg viewBox="0 0 100 100" width="112" height="112" role="img" aria-label={`训练前标签框分布，共 ${totalBoxes} 个框`}>
            <circle cx="50" cy="50" r="34" fill="none" stroke="#e2e8f0" strokeWidth="18" />
            {entries.map((entry) => {
              if (!entry.value) return null;
              const percent = (entry.value / totalBoxes) * 100;
              const slice = <circle key={entry.label} cx="50" cy="50" r="34" fill="none" stroke={entry.color} strokeWidth="18" pathLength="100" strokeDasharray={`${percent} ${100 - percent}`} strokeDashoffset={-offset} />;
              offset += percent;
              return slice;
            })}
            <text x="50" y="48" textAnchor="middle" fontSize="12" fontWeight="600" fill="#0f172a">{totalBoxes}</text>
            <text x="50" y="60" textAnchor="middle" fontSize="7" fill="#64748b">个框</text>
          </svg>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(145px, 1fr))', gap: '5px 12px', flex: '1 1 320px', minWidth: 260 }}>
            {entries.map((entry) => <div key={entry.label} style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0, fontSize: 12 }}><span aria-hidden="true" style={{ width: 8, height: 8, borderRadius: '50%', background: entry.color, flex: '0 0 auto' }} /><span title={entry.label} style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{entry.label}</span><span style={{ color: '#475569', flex: '0 0 auto' }}>{entry.value}（{((entry.value / totalBoxes) * 100).toFixed(1)}%）</span></div>)}
          </div>
        </div>
      )}
      <div style={{ color: '#64748b', fontSize: 12, marginTop: 8 }}>统计图片数：{Number(totalImages || 0).toLocaleString('zh-CN')} 张 · 总框数：{totalBoxes.toLocaleString('zh-CN')} 个</div>
    </div>
  );
}

export type TrainingDimension = 'task' | 'label';

export function trainingDimensionLabel(value: TrainingDimension): string {
  return value === 'label' ? '按标签训练' : '按任务训练';
}

type TrainingDataset = {
  id: number;
  name: string;
  description?: string | null;
  dataset_category_id?: number | null;
  dataset_category_name?: string | null;
  status?: string | null;
  version_count?: number | null;
  dataset_label_contract_revision?: number | null;
  dataset_label_contract_status?: string | null;
  dataset_label_ids?: number[];
  dataset_labels?: Array<TrainingAnnotationLabel & { label_id?: number }>;
};

type TrainingDatasetVersion = {
  id: number;
  dataset_id: number;
  version_no: number;
  status?: string | null;
  sample_count?: number | null;
};

type TrainingAnnotationLabel = {
  id: number;
  label_key: string;
  label_value: string;
  color?: string | null;
};

/**
 * Return the label contract visible to a multi-dataset training selection.
 *
 * The dataset browser is paged, so callers must pass the selected rows (which
 * are retained in ``datasets`` even after changing the browser page). Keep the
 * first occurrence of a label id to preserve the server-provided contract
 * order while avoiding duplicate options when compatible datasets share it.
 */
export function trainingDatasetLabelsForSelection(
  datasets: TrainingDataset[] = [],
  selectedDatasetIds: number[] = [],
): TrainingAnnotationLabel[] {
  const byDatasetId = new Map(
    datasets.map((dataset) => [Number(dataset.id), dataset]),
  );
  const byLabelId = new Map<number, TrainingAnnotationLabel>();
  selectedDatasetIds.forEach((rawDatasetId) => {
    const dataset = byDatasetId.get(Number(rawDatasetId));
    (dataset?.dataset_labels || []).forEach((item) => {
      const labelId = Number(item.id || item.label_id);
      if (!Number.isInteger(labelId) || labelId <= 0 || byLabelId.has(labelId)) return;
      byLabelId.set(labelId, { ...item, id: labelId });
    });
  });
  return Array.from(byLabelId.values());
}

/**
 * Global tool-tag dimensions are orthogonal sample filters.  They do not
 * change the detection class contract; the selected values are snapshotted
 * into the training request as ``sample_conditions``.
 */
export type TrainingGlobalTagOption = {
  id: number;
  name_cn: string;
  value_en: string;
  sort_order?: number;
};

export type TrainingGlobalTagDimension = {
  id: number;
  field_key: string;
  name_cn: string;
  sort_order?: number;
  options: TrainingGlobalTagOption[];
};

export type TrainingGlobalTagDimensionBrowserResult = {
  items: TrainingGlobalTagDimension[];
  total: number;
  page: number;
  size: number;
};

/** Parse the global-tag taxonomy envelope without assuming an Axios shape. */
export function trainingGlobalTagDimensionBrowserResult(payload: any): TrainingGlobalTagDimensionBrowserResult {
  const envelope = payload?.data ?? payload ?? {};
  const items = Array.isArray(envelope?.data)
    ? envelope.data
    : Array.isArray(envelope)
      ? envelope
      : [];
  const metadata = payload?.meta_data ?? envelope?.meta_data ?? {};
  return {
    items: items as TrainingGlobalTagDimension[],
    total: Number(metadata?.total ?? items.length ?? 0),
    page: Number(metadata?.page ?? 0),
    size: Number(metadata?.size ?? items.length ?? 0),
  };
}

/**
 * Keep condition objects deterministic for query keys, idempotency boundaries
 * and readable test assertions even when the user selects dimensions in a
 * different order.
 */
export function trainingSampleConditionsKey(
  conditions: Record<string, string[]> = {},
): string {
  return Object.entries(conditions)
    .map(([dimension, values]) => [
      String(dimension || '').trim(),
      Array.from(new Set((values || []).map((value) => String(value || '').trim()).filter(Boolean))).sort(),
    ] as const)
    .filter(([dimension, values]) => dimension && values.length > 0)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([dimension, values]) => `${dimension}:${values.join(',')}`)
    .join('|');
}

/** Serialize global-tag filters in the same deterministic order as the branch contract. */
export function trainingSampleConditionsJson(
  conditions: Record<string, string[]> = {},
): string {
  const normalized = Object.entries(conditions)
    .map(([dimension, values]) => [
      String(dimension || '').trim(),
      Array.from(new Set((values || []).map((value) => String(value || '').trim()).filter(Boolean))).sort(),
    ] as const)
    .filter(([dimension, values]) => dimension && values.length > 0)
    .sort(([left], [right]) => left.localeCompare(right))
    .reduce<Record<string, string[]>>((result, [dimension, values]) => {
      result[dimension] = values;
      return result;
    }, {});
  return JSON.stringify(normalized);
}

function trainingSampleConditionsLabel(
  conditions: Record<string, string[]> = {},
  dimensions: TrainingGlobalTagDimension[] = [],
): string {
  const dimensionsByKey = new Map(dimensions.map((dimension) => [dimension.field_key, dimension]));
  const entries = Object.entries(conditions).filter(([, values]) => values?.length);
  if (!entries.length) return '未启用（纳入全部符合主训练条件的样本）';
  return entries
    .map(([fieldKey, values]) => {
      const dimension = dimensionsByKey.get(fieldKey);
      const optionNames = new Map((dimension?.options || []).map((option) => [option.value_en, option.name_cn]));
      const displayValues = values.map((value) => {
        const name = optionNames.get(value);
        return name && name !== value ? `${name}（${value}）` : value;
      });
      return `${dimension?.name_cn || fieldKey}：${displayValues.join('、')}`;
    })
    .join('；');
}

export function trainingDatasetLabelCount(dataset: TrainingDataset): number {
  const ids = (dataset.dataset_label_ids || [])
    .map((value) => Number(value))
    .filter((value) => Number.isInteger(value) && value > 0);
  if (ids.length) return new Set(ids).size;
  return new Set(
    (dataset.dataset_labels || [])
      .map((item) => Number(item.id || item.label_id))
      .filter((value) => Number.isInteger(value) && value > 0),
  ).size || dataset.dataset_labels?.length || 0;
}

export function trainingDatasetContractLabel(dataset: TrainingDataset): string {
  const revision = Number(dataset.dataset_label_contract_revision || 0);
  const labelCount = trainingDatasetLabelCount(dataset);
  if (revision > 0) {
    return `标签契约 v${revision}${labelCount ? ` · ${labelCount} 个标签` : ''}`;
  }
  return '按任务契约自动固化';
}

/** Keep same-named logical datasets distinguishable in every selector. */
export function trainingDatasetDisplayLabel(dataset: TrainingDataset): string {
  const name = String(dataset.name || '').trim() || '未命名数据集';
  const id = Number(dataset.id);
  return Number.isInteger(id) && id > 0 ? `${name} #${id}` : name;
}

export function trainingDatasetVersionLabel(
  frozenVersion?: TrainingDatasetVersion,
  pendingVersion?: TrainingDatasetVersion,
): string {
  if (frozenVersion) {
    return `已有冻结版本 v${frozenVersion.version_no} · ${Number(frozenVersion.sample_count || 0).toLocaleString('zh-CN')} 个样本`;
  }
  if (pendingVersion) {
    return `已有待治理版本 v${pendingVersion.version_no} · ${Number(pendingVersion.sample_count || 0).toLocaleString('zh-CN')} 个样本（训练时会生成新快照）`;
  }
  return '首次训练时自动生成冻结快照';
}

export function trainingDatasetSearchLabel(
  dataset: TrainingDataset,
  frozenVersion?: TrainingDatasetVersion,
  pendingVersion?: TrainingDatasetVersion,
): string {
  return [
    dataset.name,
    trainingDatasetContractLabel(dataset),
    trainingDatasetVersionLabel(frozenVersion, pendingVersion),
  ].join(' · ');
}

type TrainingDatasetBrowserResult = {
  items: TrainingDataset[];
  total: number;
  page: number;
  size: number;
};

const TRAINING_DATASET_BROWSER_PAGE_SIZE = 20;
export const TRAINING_LABEL_PAGE_SIZE = 50;
const TRAINING_MODEL_FAMILY_PAGE_SIZE = 20;
export const TRAINING_IMPORTED_MODEL_PAGE_SIZE = 20;

/** Parse the shared OkRespWithMeta envelope without losing the total count. */
export function trainingDatasetBrowserResult(payload: any): TrainingDatasetBrowserResult {
  const envelope = payload?.data ?? payload ?? {};
  const items = Array.isArray(envelope?.data)
    ? envelope.data
    : Array.isArray(envelope)
      ? envelope
      : [];
  const metadata = payload?.meta_data ?? envelope?.meta_data ?? {};
  return {
    items: items as TrainingDataset[],
    total: Number(metadata?.total || items.length || 0),
    page: Number(metadata?.page || 0),
    size: Number(metadata?.size || items.length || 0),
  };
}

/**
 * Parse both the runtime-unwrapped request response and the Axios-shaped test
 * response while retaining the family pagination metadata. The default
 * request interceptor returns the business envelope directly, so callers must
 * pass the complete response here instead of reading `.data` first.
 */
export function trainingModelFamilyBrowserResult(payload: any): TrainingModelFamilyBrowserResult {
  const nestedEnvelope = payload?.data && !Array.isArray(payload.data) && typeof payload.data === 'object'
    ? payload.data
    : undefined;
  const envelope = nestedEnvelope?.data !== undefined ? nestedEnvelope : payload;
  const items = Array.isArray(envelope?.data)
    ? envelope.data
    : Array.isArray(payload?.data)
      ? payload.data
      : Array.isArray(payload)
        ? payload
        : [];
  const metadata = envelope?.meta_data ?? payload?.meta_data ?? nestedEnvelope?.meta_data ?? {};
  return {
    items: items as TrainingModelFamily[],
    total: Number(metadata?.total ?? items.length ?? 0),
    page: Number(metadata?.page ?? 0),
    size: Number(metadata?.size ?? items.length ?? 0),
  };
}

export type TrainingImportedModelBrowserResult = {
  items: TrainingImportedModel[];
  total: number;
  page: number;
  size: number;
};

/** Parse imported-model pages from both the runtime and Axios-shaped test envelopes. */
export function trainingImportedModelBrowserResult(payload: any): TrainingImportedModelBrowserResult {
  const nestedEnvelope = payload?.data && !Array.isArray(payload.data) && typeof payload.data === 'object'
    ? payload.data
    : undefined;
  const envelope = nestedEnvelope?.data !== undefined ? nestedEnvelope : payload;
  const items = Array.isArray(envelope?.data)
    ? envelope.data
    : Array.isArray(payload?.data)
      ? payload.data
      : Array.isArray(payload)
        ? payload
        : [];
  const metadata = envelope?.meta_data ?? payload?.meta_data ?? nestedEnvelope?.meta_data ?? {};
  return {
    items: items as TrainingImportedModel[],
    total: Number(metadata?.total ?? items.length ?? 0),
    page: Number(metadata?.page ?? 0),
    size: Number(metadata?.size ?? items.length ?? 0),
  };
}

function trainingDatasetStatusLabel(dataset: TrainingDataset): string {
  const status = String(dataset.status || '').trim().toLowerCase();
  if (status === 'disabled' || status === 'inactive') return '已停用';
  if (status === 'archived') return '已归档';
  return '可用';
}

function trainingIterationDate(value?: string | null): string {
  if (!value) return '未记录';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString('zh-CN', { hour12: false });
}

type CreateTrainingValues = {
  training_name?: string;
  model_weights: string;
  weights_source: 'preset' | 'imported' | 'trained' | 'auto';
  weights_ref_id?: number;
  training_recipe_ref?: string;
  epochs: number;
  imgsz: number;
  execution_mode: 'local' | 'remote' | 'container';
  train_node_id?: string;
  algorithm_key?: string;
  dataset_version_id?: number;
  container_image?: string;
};

type TrainingModelVersion = {
  id: number;
  version_no: number;
  /** User-facing sequence within a series branch; legacy rows may omit it. */
  branch_version_no?: number | null;
  display_name: string;
  training_job_id?: number | null;
  training_model_weights?: string | null;
  dataset_version_no?: number | null;
  dataset_version_id?: number | null;
  dataset_id?: number | null;
  training_recipe_key?: string | null;
  training_recipe_version?: number | null;
  label_set_signature?: string | null;
  class_order_signature?: string | null;
  status?: string | null;
  created_at?: string | null;
  manifest?: Record<string, unknown> | null;
  artifacts?: Array<{
    artifact_type?: string | null;
    relative_path?: string | null;
    status?: string | null;
    metadata?: Record<string, unknown> | null;
  }>;
};

type TrainingModelFamily = {
  family_key: string;
  display_name: string;
  task_id?: number | null;
  algorithm_id?: number | null;
  versions: TrainingModelVersion[];
};

export type TrainingModelVersionCompatibility = {
  compatible: boolean;
  disabled: boolean;
  reason?: 'contract_missing' | 'contract_mismatch';
};

/**
 * Render the version that operators can compare inside one branch.
 * ``version_no`` is an algorithm-global legacy sequence and is only a
 * fallback for historical rows that predate branch numbering.
 */
export function trainingModelVersionLabel(
  version?: {
    version_no?: number | null;
    branch_version_no?: number | null;
  } | null,
): string {
  if (!version) return '版本未记录';
  const branchVersion = Number(version.branch_version_no);
  const globalVersion = Number(version.version_no);
  const value = Number.isInteger(branchVersion) && branchVersion > 0
    ? branchVersion
    : Number.isInteger(globalVersion) && globalVersion > 0
      ? globalVersion
      : null;
  return value == null ? '版本未记录' : `v${value}`;
}

/**
 * Keep the model picker aligned with the server's explicit-parent gate.
 * Once the selected task has a complete contract, an old model without both
 * signatures is unknown rather than compatible: the create endpoint cannot
 * prove that its class mapping is safe to reuse.
 */
export function trainingModelVersionCompatibility(
  version: TrainingModelVersion,
  expectedLabelSetSignature?: string | null,
  expectedClassOrderSignature?: string | null,
): TrainingModelVersionCompatibility {
  const expectedLabel = String(expectedLabelSetSignature || '').trim();
  const expectedOrder = String(expectedClassOrderSignature || '').trim();
  if (!expectedLabel || !expectedOrder) {
    return { compatible: true, disabled: false };
  }

  const actualLabel = String(version.label_set_signature || '').trim();
  const actualOrder = String(version.class_order_signature || '').trim();
  if (!actualLabel || !actualOrder) {
    return { compatible: false, disabled: true, reason: 'contract_missing' };
  }
  if (actualLabel !== expectedLabel || actualOrder !== expectedOrder) {
    return { compatible: false, disabled: true, reason: 'contract_mismatch' };
  }
  return { compatible: true, disabled: false };
}

export type TrainingModelFamilyBrowserResult = {
  items: TrainingModelFamily[];
  total: number;
  page: number;
  size: number;
};

/** Return the concrete PT filename when the model API has recorded one. */
export function trainingModelVersionWeightFile(version?: TrainingModelVersion | null): string {
  if (!version) return '待预检确认';
  const readyArtifact = (version.artifacts || []).find((artifact) => (
    String(artifact.artifact_type || '').trim().toLowerCase() === 'pt'
    && String(artifact.status || 'READY').trim().toUpperCase() === 'READY'
    && String(artifact.relative_path || '').trim()
  ));
  const manifest = version.manifest && typeof version.manifest === 'object' ? version.manifest : {};
  const raw = readyArtifact?.relative_path
    || (typeof manifest.weights_filename === 'string' ? manifest.weights_filename : '')
    || (typeof manifest.weights_path === 'string' ? manifest.weights_path : '')
    || '';
  const normalized = String(raw).trim();
  if (normalized) return normalized.split(/[\\/]/).pop() || normalized;
  return '待预检确认';
}

type TrainingImportedModel = {
  id: number;
  display_name?: string | null;
  original_filename?: string | null;
  category?: string | null;
};

type TrainingIterationPreview = {
  weights_source: 'preset' | 'trained';
  weights_ref_id?: number | null;
  model_weights: string;
  selection_policy?: string | null;
  selection_scope?: string | null;
  candidate_count?: number | null;
  compatible_candidate_count?: number | null;
  fallback_reason?: string | null;
  decision_fingerprint?: string | null;
  parent?: {
    job_id: number;
    task_id: number;
    task_name?: string | null;
    model_version_id?: number | null;
    version_no?: number | null;
    branch_version_no?: number | null;
    display_name: string;
    dataset_id?: number | null;
    dataset_name?: string | null;
    dataset_version_no?: number | null;
    dataset_version_id?: number | null;
    dataset_content_signature?: string | null;
    weights_filename: string;
    created_at?: string | null;
    job_status?: string | null;
    job_type?: string | null;
    category?: string | null;
    training_recipe_key?: string | null;
    training_recipe_version?: number | null;
    label_set_signature?: string | null;
    class_order_signature?: string | null;
    source_task_ids?: number[];
    model_key?: string | null;
    model_status?: string | null;
    series_id?: number | null;
    series_name?: string | null;
    variant_id?: number | null;
    variant_name?: string | null;
    branch_key?: string | null;
  } | null;
  ai_report_id?: number | null;
  ai_provider_key?: string | null;
  suggested_params: Record<string, unknown>;
};

const AI_TRAIN_PARAM_KEYS = new Set([
  'batch', 'workers', 'patience', 'lr0', 'lrf', 'momentum', 'weight_decay',
  'warmup_epochs', 'warmup_momentum', 'warmup_bias_lr', 'optimizer', 'cos_lr',
  'close_mosaic', 'label_smoothing', 'dropout', 'freeze', 'seed', 'deterministic',
  'single_cls', 'rect', 'cache', 'amp', 'fraction', 'hsv_h', 'hsv_s', 'hsv_v',
  'degrees', 'translate', 'scale', 'shear', 'perspective', 'flipud', 'fliplr',
  'mosaic', 'mixup', 'copy_paste', 'erasing', 'crop_fraction', 'max_det', 'iou',
  'conf', 'save_period', 'cutmix', 'multi_scale', 'box', 'cls', 'dfl',
]);

export function partitionAiIterationParams(raw: Record<string, unknown> = {}) {
  const topLevel: { epochs?: number; imgsz?: number } = {};
  const hyperparams: Record<string, unknown> = {};
  const epochs = Number(raw.epochs);
  const imgsz = Number(raw.imgsz);
  if (Number.isFinite(epochs) && epochs >= 1 && epochs <= 3000) topLevel.epochs = Math.round(epochs);
  if (Number.isFinite(imgsz) && imgsz >= 320 && imgsz <= 1280) topLevel.imgsz = Math.round(imgsz);
  Object.entries(raw).forEach(([key, value]) => {
    if (AI_TRAIN_PARAM_KEYS.has(key) && value !== null && value !== '') hyperparams[key] = value;
  });
  return { topLevel, hyperparams };
}

export function trainingIterationParentLabel(parent?: TrainingIterationPreview['parent']): string {
  if (!parent) return 'yolo11n.pt（平台预训练权重）';
  const versionLabel = trainingModelVersionLabel(parent);
  return `${parent.display_name}${versionLabel === '版本未记录' ? '' : ` · ${versionLabel}`} · ${parent.weights_filename || 'best.pt'}`;
}

export function trainingIterationParentLineageLabel(parent?: TrainingIterationPreview['parent']): string {
  if (!parent) return '未关联模型系列';
  const series = parent.series_name || (parent.series_id ? `系列 #${parent.series_id}` : '未记录系列');
  const variant = parent.variant_name || (parent.variant_id ? `分支 #${parent.variant_id}` : '未记录分支');
  return `${series} · ${variant}${parent.branch_key ? ` · ${parent.branch_key}` : ''}`;
}

export function trainingIterationPolicyLabel(preview?: TrainingIterationPreview | null): string {
  if (!preview?.selection_policy) {
    return preview?.weights_source === 'trained' ? '已按指定父作业解析' : '按所选任务和训练配方自动查找';
  }
  const labels: Record<string, string> = {
    source_task_then_latest: '优先所选来源任务，再按作业编号倒序取最新兼容模型',
    explicit_parent: '使用用户指定的已完成父作业',
    fallback_preset: '没有兼容父模型，回退平台预训练权重',
    auto_prefer_selected_tasks_then_latest: '优先所选来源任务，再按作业编号倒序取最新兼容模型',
    auto_compatible_parent: '在当前任务契约和训练配方范围内查找兼容父模型',
  };
  return labels[preview.selection_policy] || preview.selection_policy;
}

export function trainingIterationScopeLabel(scope?: string | null): string {
  const labels: Record<string, string> = {
    selected_tasks: '所选任务',
    primary_task: '主任务',
    cross_task_fallback: '同标签契约的其他可见任务（回退）',
    explicit_parent: '指定父模型',
    none: '未检索父模型',
  };
  const normalized = String(scope || '').trim();
  return labels[normalized] || normalized || '未记录';
}

export function trainingIterationFallbackLabel(reason?: string | null): string {
  const labels: Record<string, string> = {
    no_compatible_completed_parent: '没有找到已完成且标签契约、训练配方均兼容的父模型',
    resolved_parent_missing: '预览命中的父模型已不存在，请刷新后重试',
  };
  const normalized = String(reason || '').trim();
  return labels[normalized] || normalized || '未找到兼容父模型';
}

/**
 * Freeze the parent selected by the preview into the create payload.  An
 * automatic lookup is intentionally converted to either the exact completed
 * job shown to the operator or the preset fallback shown by the preview, so
 * a second lookup during submission cannot silently pick another model.
 */
export function pinIterationWeights(
  source: CreateTrainingValues['weights_source'],
  modelWeights: string | undefined,
  weightsRefId: number | undefined,
  preview?: TrainingIterationPreview | null,
) {
  if (source === 'auto') {
    const parentId = Number(preview?.parent?.job_id || 0);
    if (Number.isInteger(parentId) && parentId > 0) {
      return {
        weights_source: 'trained' as const,
        weights_ref_id: parentId,
        model_weights: `trained:${parentId}`,
      };
    }
    return {
      weights_source: 'preset' as const,
      weights_ref_id: undefined,
      model_weights: String(preview?.model_weights || modelWeights || 'yolo11n.pt').trim() || 'yolo11n.pt',
    };
  }
  if (source === 'trained' && Number(weightsRefId) > 0) {
    const parentId = Number(weightsRefId);
    return {
      weights_source: 'trained' as const,
      weights_ref_id: parentId,
      model_weights: `trained:${parentId}`,
    };
  }
  if (source === 'imported' && Number(weightsRefId) > 0) {
    const modelId = Number(weightsRefId);
    return {
      weights_source: 'imported' as const,
      weights_ref_id: modelId,
      model_weights: `imported:${modelId}`,
    };
  }
  // A trained/imported source without its required reference is an invalid
  // form state. Never pass a stale `trained:<id>` token through as a preset
  // weight if the user changed the source or selected task context.
  return {
    weights_source: 'preset' as const,
    weights_ref_id: undefined,
    model_weights: source === 'preset'
      ? String(modelWeights || 'yolo11n.pt').trim() || 'yolo11n.pt'
      : 'yolo11n.pt',
  };
}

/**
 * Keep the legacy YOLO creator behind an explicit compatibility boundary.
 * The platform contract can create YOLO jobs once a frozen dataset version is
 * attached; remote-node jobs and historical task-only jobs still need the
 * legacy service until their runtime contract is migrated.
 */
export function shouldUseLegacyYoloCreate(
  algorithmKey?: string,
  executionMode?: CreateTrainingValues['execution_mode'],
  datasetVersionId?: number,
) {
  return algorithmKey === 'yolo_detection' && (executionMode === 'remote' || !datasetVersionId);
}

export function parseTrainingRecipeRef(value?: string) {
  const [key, version] = String(value || '').split('::');
  const parsedVersion = Number(version);
  return {
    training_recipe_key: key?.trim() || undefined,
    training_recipe_version: Number.isInteger(parsedVersion) && parsedVersion > 0 ? parsedVersion : undefined,
  };
}

export function isRemoteNodeMissing(executionMode: CreateTrainingValues['execution_mode'], trainNodeId?: string) {
  return executionMode === 'remote' && !trainNodeId?.trim();
}

export function isUnsupportedGenericExecution(algorithmKey?: string, executionMode?: CreateTrainingValues['execution_mode']) {
  return Boolean(algorithmKey && algorithmKey !== 'yolo_detection' && executionMode !== 'container');
}

export function mergeTrainingFormValues(
  preservedValues: Partial<CreateTrainingValues>,
  validatedValues: Partial<CreateTrainingValues>,
): CreateTrainingValues {
  return { ...preservedValues, ...validatedValues } as CreateTrainingValues;
}

function unwrapList<T = TrainingCandidate>(payload: any): T[] {
  const body = payload?.data ?? payload;
  if (Array.isArray(body?.data)) return body.data;
  return Array.isArray(body) ? body : [];
}

export type TrainingLabelBrowserResult = {
  items: TrainingAnnotationLabel[];
  total: number;
  page: number;
  size: number;
};

/** Parse the labels endpoint envelope without losing its server-side total. */
export function trainingLabelBrowserResult(payload: any): TrainingLabelBrowserResult {
  const envelope = payload?.data ?? payload ?? {};
  const items = Array.isArray(envelope?.data)
    ? envelope.data
    : Array.isArray(envelope)
      ? envelope
      : [];
  const metadata = payload?.meta_data ?? envelope?.meta_data ?? {};
  return {
    items: items as TrainingAnnotationLabel[],
    total: Number(metadata?.total ?? items.length ?? 0),
    page: Number(metadata?.page ?? 0),
    size: Number(metadata?.size ?? items.length ?? 0),
  };
}

export type TrainingCandidateBrowserResult = {
  items: TrainingCandidate[];
  total: number;
  page: number;
  size: number;
};

/** Preserve candidate pagination metadata so large task catalogs are not silently truncated. */
export function trainingCandidateBrowserResult(payload: any): TrainingCandidateBrowserResult {
  const envelope = payload?.data ?? payload ?? {};
  const items = Array.isArray(envelope?.data)
    ? envelope.data
    : Array.isArray(envelope)
      ? envelope
      : [];
  const metadata = payload?.meta_data ?? envelope?.meta_data ?? {};
  return {
    items: items as TrainingCandidate[],
    total: Number(metadata?.total ?? items.length ?? 0),
    page: Number(metadata?.page ?? 0),
    size: Number(metadata?.size ?? items.length ?? 0),
  };
}

export function trainingCandidateQueryParams(
  labelIds: number[] = [],
  datasetId?: number,
  page = 0,
  size = 100,
  search?: string,
  datasetIds: number[] = [],
) {
  const scopedDatasetIds = Array.from(new Set([
    ...(datasetIds || []).map((value) => Number(value)).filter((value) => Number.isInteger(value) && value > 0),
    ...(datasetId ? [Number(datasetId)] : []),
  ]));
  return {
    page,
    size,
    ...(labelIds.length ? { label_ids: labelIds } : {}),
    ...(scopedDatasetIds.length > 1
      ? { dataset_ids: scopedDatasetIds }
      : scopedDatasetIds.length === 1
        ? { dataset_id: scopedDatasetIds[0] }
        : {}),
    ...(search?.trim() ? { q: search.trim() } : {}),
  };
}

export function trainingCandidateQueryParamsForDimension(
  dimension: TrainingDimension,
  labelIds: number[] = [],
  datasetId?: number,
  page = 0,
  size = 100,
  search?: string,
  datasetIds: number[] = [],
) {
  return trainingCandidateQueryParams(
    dimension === 'label' ? labelIds : [],
    datasetId,
    page,
    size,
    search,
    datasetIds,
  );
}

/**
 * Task-dimension selections use the merge-snapshot endpoint whenever the
 * selection spans more than one task or more than one logical dataset. The
 * endpoint still preserves the first dataset as the legacy primary id, while
 * the complete dataset scope is persisted in the immutable contract.
 */
export function taskTrainingUsesMergedEndpoint(
  selectedTaskIds: number[] = [],
  selectedDatasetIds: number[] = [],
): boolean {
  const taskCount = new Set(
    (selectedTaskIds || [])
      .map((value) => Number(value))
      .filter((value) => Number.isInteger(value) && value > 0),
  ).size;
  const datasetCount = new Set(
    (selectedDatasetIds || [])
      .map((value) => Number(value))
      .filter((value) => Number.isInteger(value) && value > 0),
  ).size;
  return taskCount > 1 || datasetCount > 1;
}

/**
 * Kept as a compatibility export for extensions that imported the old
 * client-side guard. Multi-dataset task training is now supported; the
 * authoritative contract/permission check remains on the server.
 */
export function taskTrainingDatasetSelectionError(
  selectedTaskIds: number[] = [],
  selectedDatasetIds: number[] = [],
): string | null {
  void selectedTaskIds;
  void selectedDatasetIds;
  return null;
}

export function trainingLabelIdsForCandidates(candidates: TrainingCandidate[]): number[] {
  return Array.from(
    new Set(
      candidates.flatMap((candidate) =>
        (candidate.contract_labels || [])
          .map((label) => Number(label.label_id))
          .filter((labelId) => Number.isInteger(labelId) && labelId > 0),
      ),
    ),
  ).sort((left, right) => left - right);
}

function CreateTrainingAction() {
  const TRAINING_CANDIDATE_PAGE_SIZE = 20;
  const queryClient = useQueryClient();
  const isLoggedIn = typeof window === 'undefined' || Boolean(localStorage.getItem('token'));
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);
  const [trainingDimension, setTrainingDimension] = useState<TrainingDimension>('task');
  const initialTaskId = useMemo(() => {
    if (typeof window === 'undefined') return undefined;
    const value = Number(new URLSearchParams(window.location.search).get('task_id'));
    return Number.isInteger(value) && value > 0 ? value : undefined;
  }, []);
  // Keep the first selected task as the primary task for lineage/model
  // resolution while allowing task-first training to merge compatible tasks.
  const [selectedTaskIds, setSelectedTaskIds] = useState<number[]>(initialTaskId ? [initialTaskId] : []);
  const [selectedSourceTaskIds, setSelectedSourceTaskIds] = useState<number[]>([]);
  const [datasetId, setDatasetId] = useState<number | undefined>();
  const [selectedDatasetIds, setSelectedDatasetIds] = useState<number[]>([]);
  const [selectedDatasetOverride, setSelectedDatasetOverride] = useState<TrainingDataset | null>(null);
  const [datasetBrowserOpen, setDatasetBrowserOpen] = useState(false);
  const [datasetBrowserPage, setDatasetBrowserPage] = useState(0);
  const [datasetBrowserSearch, setDatasetBrowserSearch] = useState('');
  const [datasetBrowserInput, setDatasetBrowserInput] = useState('');
  const [labelPage, setLabelPage] = useState(0);
  const [labelSearch, setLabelSearch] = useState('');
  const [labelSearchInput, setLabelSearchInput] = useState('');
  const [candidatePage, setCandidatePage] = useState(0);
  const [candidateSearch, setCandidateSearch] = useState('');
  const [candidateSearchInput, setCandidateSearchInput] = useState('');
  const [modelFamilyPage, setModelFamilyPage] = useState(0);
  const [modelFamilySearch, setModelFamilySearch] = useState('');
  const [modelFamilySearchInput, setModelFamilySearchInput] = useState('');
  const [importedModelPage, setImportedModelPage] = useState(0);
  const [importedModelSearch, setImportedModelSearch] = useState('');
  const [importedModelSearchInput, setImportedModelSearchInput] = useState('');
  // Keep the selected version visible when its family is no longer on the
  // current server-side page (for example after searching or paging).
  const [selectedTrainedModelOption, setSelectedTrainedModelOption] = useState<{
    value: number;
    label: string;
    searchText: string;
    family: TrainingModelFamily;
    version: TrainingModelVersion;
  } | null>(null);
  // Keep candidates already selected on another page so pagination/search
  // never drops them from the training payload or summary statistics.
  const [candidateCache, setCandidateCache] = useState<Record<string, TrainingCandidate>>({});
  // Keep labels already selected on another server page so Ant Select can
  // continue rendering their names after a search or page change.
  const [labelCache, setLabelCache] = useState<Record<number, TrainingAnnotationLabel>>({});
  const [importedModelCache, setImportedModelCache] = useState<Record<number, TrainingImportedModel>>({});
  const [selectedLabelIds, setSelectedLabelIds] = useState<number[]>([]);
  const [sampleConditions, setSampleConditions] = useState<Record<string, string[]>>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitConflict, setSubmitConflict] = useState(false);
  // One key covers an unchanged submission, including an ambiguous network
  // retry. A confirmed conflict or an edited payload starts a new operation.
  const createTrainingIdempotencyKeyRef = useRef<string | null>(null);
  const trainingModalRef = useRef<HTMLDivElement | null>(null);
  const [form] = Form.useForm<CreateTrainingValues>();
  const weightsSource = Form.useWatch('weights_source', form) || 'auto';
  const weightsRefId = Form.useWatch('weights_ref_id', form);
  const [aiIterationParams, setAiIterationParams] = useState<Record<string, unknown>>({});
  const appliedAiPreviewRef = useRef('');
  const labelsQuery = useQuery<TrainingLabelBrowserResult>({
    queryKey: ['training-annotation-labels', labelPage, labelSearch],
    queryFn: async () => trainingLabelBrowserResult(await request.get('/v1/annotation-taxonomy/labels', {
      params: {
        page: labelPage,
        size: TRAINING_LABEL_PAGE_SIZE,
        include_disabled: false,
        annotation_kind: 'bbox',
        ...(labelSearch.trim() ? { q: labelSearch.trim() } : {}),
      },
    })),
    enabled: open && isLoggedIn && trainingDimension === 'label',
    staleTime: 60_000,
    retry: false,
    placeholderData: (previous) => previous,
  });
  const globalTagDimensionsQuery = useQuery<TrainingGlobalTagDimensionBrowserResult>({
    queryKey: ['training-global-tag-dimensions'],
    queryFn: async () => trainingGlobalTagDimensionBrowserResult(await request.get('/v1/annotation-taxonomy/global-tag-dimensions', {
      params: { page: 0, size: 500 },
    })),
    enabled: open && isLoggedIn,
    staleTime: 5 * 60_000,
    retry: false,
  });
  const globalTagDimensions = useMemo(
    () => [...(globalTagDimensionsQuery.data?.items || [])]
      .filter((dimension) => String(dimension.field_key || '').trim())
      .sort((left, right) => Number(left.sort_order || 0) - Number(right.sort_order || 0) || left.name_cn.localeCompare(right.name_cn)),
    [globalTagDimensionsQuery.data?.items],
  );
  const sampleConditionsKey = useMemo(
    () => trainingSampleConditionsKey(sampleConditions),
    [sampleConditions],
  );
  // 标签筛选只属于“按标签训练”。任务训练始终使用所选任务的完整标签契约。
  const candidateLabelIds = trainingDimension === 'label' ? selectedLabelIds : [];
  // The selected order is part of the label-dimension class contract. Keep it
  // in the query/context key so a reordered class list cannot reuse a model
  // catalog or iteration preview computed for a different class order.
  const labelIdsKey = candidateLabelIds.join(',');
  const candidatesQuery = useQuery({
    queryKey: ['training-eligible-tasks', trainingDimension, datasetId ?? null, selectedDatasetIds.join(','), labelIdsKey, candidatePage, candidateSearch],
    queryFn: async () => {
      const raw = await request.get('/v1/training/eligible-tasks', {
      params: {
        ...trainingCandidateQueryParamsForDimension(
          trainingDimension,
          candidateLabelIds,
          datasetId,
          candidatePage,
          TRAINING_CANDIDATE_PAGE_SIZE,
          candidateSearch,
          selectedDatasetIds,
        ),
        training_dimension: trainingDimension,
      },
      // FastAPI's List[int] query parser expects repeated keys
      // (label_ids=1&label_ids=2), not Axios' default bracket form.
      paramsSerializer: { indexes: null },
      });
      return trainingCandidateBrowserResult(raw);
    },
    enabled: open && isLoggedIn && Boolean(datasetId) && (trainingDimension === 'task' || candidateLabelIds.length > 0),
    staleTime: 15_000,
    retry: false,
    placeholderData: (previous) => previous,
  });
  const algorithmsQuery = useQuery({
    queryKey: ['training-algorithms'],
    queryFn: async () => {
      const response = await request.get('/v1/training/catalog');
      const body = response.data?.data ?? response.data;
      return body && typeof body === 'object' ? body : { algorithms: [], dataset_versions: [] };
    },
    enabled: open && isLoggedIn, staleTime: 60_000, retry: false,
  });
  const annotationLabels = labelsQuery.data?.items ?? [];
  const annotationLabelTotal = labelsQuery.data?.total ?? annotationLabels.length;
  useEffect(() => {
    if (!annotationLabels.length) return;
    setLabelCache((current) => {
      let changed = false;
      const next = { ...current };
      annotationLabels.forEach((label) => {
        const id = Number(label.id);
        if (!Number.isInteger(id) || id <= 0) return;
        if (!next[id] || next[id] !== label) {
          next[id] = label;
          changed = true;
        }
      });
      return changed ? next : current;
    });
  }, [annotationLabels]);
  const algorithms = algorithmsQuery.data?.algorithms ?? [];
  const catalogDatasets = (algorithmsQuery.data?.datasets ?? []) as TrainingDataset[];
  const datasetBrowserQuery = useQuery<TrainingDatasetBrowserResult>({
    queryKey: ['training-dataset-browser', datasetBrowserPage, datasetBrowserSearch],
    queryFn: async () => trainingDatasetBrowserResult(await request.get('/v1/datasets', {
      params: {
        page: datasetBrowserPage,
        size: TRAINING_DATASET_BROWSER_PAGE_SIZE,
        ...(datasetBrowserSearch.trim() ? { q: datasetBrowserSearch.trim() } : {}),
      },
    })),
    enabled: open && isLoggedIn && datasetBrowserOpen,
    staleTime: 30_000,
    retry: false,
    placeholderData: (previous) => previous,
  });
  const datasets = useMemo(() => {
    const byId = new Map<number, TrainingDataset>();
    [...catalogDatasets, ...(datasetBrowserQuery.data?.items || []), ...(selectedDatasetOverride ? [selectedDatasetOverride] : [])]
      .forEach((item) => byId.set(Number(item.id), item));
    return Array.from(byId.values());
  }, [catalogDatasets, datasetBrowserQuery.data?.items, selectedDatasetOverride]);
  const frozenDatasetVersions = (algorithmsQuery.data?.dataset_versions ?? []) as TrainingDatasetVersion[];
  const pendingDatasetVersions = (algorithmsQuery.data?.pending_dataset_versions ?? []) as TrainingDatasetVersion[];
  const trainingRecipes = algorithmsQuery.data?.training_recipes ?? [];
  const defaultTrainingContract = useMemo(
    () => resolveDefaultTrainingContract(algorithms, trainingRecipes),
    [algorithms, trainingRecipes],
  );
  // A label-dimension job trains on the selected label subset and may reuse a
  // compatible parent from any selected source task. The iteration preview is
  // authoritative for that subset contract, so do not narrow the repository
  // to the first source task here.
  const modelSelectorTaskId = trainingDimension === 'task'
    ? selectedTaskIds[0]
    : undefined;
  const modelSelectorCandidate = useMemo(() => {
    const candidateId = modelSelectorTaskId ? String(modelSelectorTaskId) : '';
    return candidateId ? candidateCache[candidateId] : undefined;
  }, [candidateCache, modelSelectorTaskId]);
  const modelSelectorLabelSignature = trainingDimension === 'task'
    ? String(modelSelectorCandidate?.label_set_signature || '').trim()
    : '';
  const modelSelectorOrderSignature = trainingDimension === 'task'
    ? String(modelSelectorCandidate?.class_order_signature || '').trim()
    : '';
  const modelSelectorLabelIds = trainingDimension === 'label' ? selectedLabelIds : [];
  const modelSelectorLabelIdsKey = modelSelectorLabelIds.join(',');
  const modelFamiliesQuery = useQuery<TrainingModelFamilyBrowserResult>({
    queryKey: [
      'training-model-families', modelFamilyPage, modelFamilySearch,
      defaultTrainingContract.recipeRef, modelSelectorLabelSignature,
      modelSelectorOrderSignature, modelSelectorLabelIdsKey,
    ],
    queryFn: async () => trainingModelFamilyBrowserResult(await request.get('/v1/models/families', {
      params: {
        page: modelFamilyPage,
        size: TRAINING_MODEL_FAMILY_PAGE_SIZE,
        artifact_type: 'pt',
        ...parseTrainingRecipeRef(defaultTrainingContract.recipeRef),
        ...(modelFamilySearch.trim() ? { q: modelFamilySearch.trim() } : {}),
        ...(modelSelectorLabelIds.length ? { label_ids: modelSelectorLabelIds } : {}),
        ...(modelSelectorLabelSignature ? { label_set_signature: modelSelectorLabelSignature } : {}),
        ...(modelSelectorOrderSignature ? { class_order_signature: modelSelectorOrderSignature } : {}),
      },
      // FastAPI's List[int] query parser expects repeated keys for label_ids.
      paramsSerializer: { indexes: null },
    })),
    enabled: open && isLoggedIn && weightsSource === 'trained',
    staleTime: 30_000,
    retry: false,
    placeholderData: (previous) => previous,
  });
  const importedModelsQuery = useQuery<TrainingImportedModelBrowserResult>({
    queryKey: ['training-imported-models', importedModelPage, importedModelSearch],
    queryFn: async () => trainingImportedModelBrowserResult(await request.get('/v1/yolo-imported-models', {
      params: {
        page: importedModelPage,
        size: TRAINING_IMPORTED_MODEL_PAGE_SIZE,
        ...(importedModelSearch.trim() ? { q: importedModelSearch.trim() } : {}),
      },
    })),
    enabled: open && isLoggedIn && weightsSource === 'imported',
    staleTime: 30_000,
    retry: false,
    placeholderData: (previous) => previous,
  });
  const trainedModelOptions = useMemo(() => {
    const pageOptions = (modelFamiliesQuery.data?.items || []).flatMap((family) =>
      (family.versions || [])
      .filter((version) => Number(version.training_job_id) > 0)
      .map((version) => {
        const compatibility = trainingModelVersionCompatibility(
          version,
          modelSelectorLabelSignature,
          modelSelectorOrderSignature,
        );
        const reasonLabel = compatibility.reason === 'contract_missing'
          ? '标签契约缺失，不能确认类别映射'
          : compatibility.reason === 'contract_mismatch'
            ? '标签契约不匹配'
            : '';
        return {
          value: Number(version.training_job_id),
          compatible: compatibility.compatible,
          disabled: compatibility.disabled,
          label: `${family.display_name} · ${trainingModelVersionLabel(version)}${family.task_id ? ` · 来源标注任务 #${family.task_id}` : ''} · ${trainingModelVersionWeightFile(version)}${reasonLabel ? ` · ${reasonLabel}` : ''}`,
          searchText: `${family.display_name} ${version.display_name} ${trainingModelVersionLabel(version)} v${version.version_no} ${version.branch_version_no || ''} ${family.task_id || ''} ${reasonLabel}`,
          family,
          version,
        };
      }),
    );
    if (!selectedTrainedModelOption || pageOptions.some((option) => option.value === selectedTrainedModelOption.value)) {
      return pageOptions;
    }
    return [selectedTrainedModelOption, ...pageOptions];
  }, [modelFamiliesQuery.data?.items, modelSelectorLabelSignature, modelSelectorOrderSignature, selectedTrainedModelOption]);
  const importedModels = importedModelsQuery.data?.items || [];
  const importedModelTotal = importedModelsQuery.data?.total ?? importedModels.length;
  useEffect(() => {
    if (!importedModels.length) return;
    setImportedModelCache((current) => {
      let changed = false;
      const next = { ...current };
      importedModels.forEach((model) => {
        const id = Number(model.id);
        if (!Number.isInteger(id) || id <= 0 || next[id] === model) return;
        next[id] = model;
        changed = true;
      });
      return changed ? next : current;
    });
  }, [importedModels]);
  const importedModelOptions = useMemo(() => {
    const selectedId = Number(weightsRefId);
    const byId = new Map<number, TrainingImportedModel>();
    Object.values(importedModelCache).forEach((model) => byId.set(Number(model.id), model));
    importedModels.forEach((model) => byId.set(Number(model.id), model));
    const rows = Array.from(byId.values());
    if (selectedId > 0 && !byId.has(selectedId)) rows.unshift({ id: selectedId });
    return rows.map((model) => ({
      value: Number(model.id),
      label: model.display_name || model.original_filename || `外部模型 ${model.id}`,
      searchText: `${model.display_name || ''} ${model.original_filename || ''} ${model.category || ''}`,
      model,
    }));
  }, [importedModelCache, importedModels, weightsRefId]);
  const candidates = candidatesQuery.data?.items ?? [];
  const candidateTotal = candidatesQuery.data?.total ?? candidates.length;
  const candidateById = useMemo(() => {
    const merged = new Map<number, TrainingCandidate>();
    Object.values(candidateCache).forEach((candidate) => merged.set(Number(candidate.task_id), candidate));
    candidates.forEach((candidate) => merged.set(Number(candidate.task_id), candidate));
    return merged;
  }, [candidateCache, candidates]);

  useEffect(() => {
    if (!candidates.length) return;
    setCandidateCache((current) => {
      const next = { ...current };
      let changed = false;
      candidates.forEach((candidate) => {
        const key = String(candidate.task_id);
        if (next[key] !== candidate) {
          next[key] = candidate;
          changed = true;
        }
      });
      return changed ? next : current;
    });
  }, [candidates]);

  const invalidateTrainingSelectionQueries = async () => {
    // A 409 means the server rejected the snapshot/task state represented by
    // the wizard. Mark candidate and catalog metadata stale so a retry does
    // not continue to use the old task or dataset-version state.
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['training-eligible-tasks'] }),
      queryClient.invalidateQueries({ queryKey: ['training-algorithms'] }),
    ]);
  };

  const refreshTrainingCandidates = async () => {
    // A deliberate refresh starts a new user operation. Reusing the previous
    // key with a newly selected candidate would correctly be rejected by the
    // idempotency contract as a different request payload.
    createTrainingIdempotencyKeyRef.current = null;
    setSubmitError(null);
    setSubmitConflict(false);
    setSelectedTaskIds([]);
    setSelectedSourceTaskIds([]);
    // A refreshed candidate snapshot can invalidate the previously resolved
    // parent model. Clear both the visible selection and submitted reference
    // so a retry cannot accidentally reuse stale lineage.
    clearExplicitTrainedModel();
    resetIterationPreviewState();
    setCandidateCache({});
    setCandidatePage(0);
    setCandidateSearch('');
    setCandidateSearchInput('');
    resetImportedModelBrowser();
    setStep(0);
    try {
      await invalidateTrainingSelectionQueries();
    } catch {
      // Keep the modal usable even when a background refresh itself fails;
      // the candidate query's error state exposes its own retry action.
    }
  };
  const frozenVersionByDataset = useMemo(() => {
    const versions = new Map<number, TrainingDatasetVersion>();
    frozenDatasetVersions.forEach((version) => {
      const current = versions.get(Number(version.dataset_id));
      if (!current || Number(version.version_no) > Number(current.version_no)) {
        versions.set(Number(version.dataset_id), version);
      }
    });
    return versions;
  }, [frozenDatasetVersions]);
  const pendingVersionByDataset = useMemo(() => {
    const versions = new Map<number, TrainingDatasetVersion>();
    pendingDatasetVersions.forEach((version) => {
      const current = versions.get(Number(version.dataset_id));
      if (!current || Number(version.version_no) > Number(current.version_no)) {
        versions.set(Number(version.dataset_id), version);
      }
    });
    return versions;
  }, [pendingDatasetVersions]);
  const selectedDataset = useMemo(
    () => datasets.find((item) => Number(item.id) === Number(datasetId)),
    [datasets, datasetId],
  );
  const selectedDatasets = useMemo(
    () => selectedDatasetIds
      .map((id) => datasets.find((item) => Number(item.id) === Number(id)))
      .filter(Boolean) as TrainingDataset[],
    [datasets, selectedDatasetIds],
  );
  const clearExplicitTrainedModel = () => {
    setSelectedTrainedModelOption(null);
    // Dataset/task changes invalidate a selected trained parent, but they do
    // not invalidate a user-supplied preset or an imported model reference.
    // Keep those values intact so browsing the wizard cannot silently replace
    // custom initial weights with an empty field.
    if (weightsSource === 'trained') {
      form.setFieldsValue({ weights_ref_id: undefined, model_weights: undefined });
    } else if (weightsSource === 'auto') {
      form.setFieldsValue({ weights_ref_id: undefined, model_weights: 'yolo11n.pt' });
    }
  };
  const updateSampleConditions = (fieldKey: string, values: string[]) => {
    const normalizedKey = String(fieldKey || '').trim();
    if (!normalizedKey) return;
    const normalizedValues = Array.from(new Set(values.map((value) => String(value || '').trim()).filter(Boolean)));
    createTrainingIdempotencyKeyRef.current = null;
    setSubmitError(null);
    setSubmitConflict(false);
    // A condition changes the immutable sample snapshot and therefore starts
    // a new lineage decision. Keep the visible parent honest until preview
    // resolves again for the edited selection.
    clearExplicitTrainedModel();
    resetIterationPreviewState();
    setSampleConditions((current) => {
      const next = { ...current };
      if (normalizedValues.length) next[normalizedKey] = normalizedValues;
      else delete next[normalizedKey];
      return next;
    });
  };
  const resetModelFamilyBrowser = useCallback(() => {
    setModelFamilyPage(0);
    setModelFamilySearch('');
    setModelFamilySearchInput('');
  }, []);
  const resetImportedModelBrowser = useCallback(() => {
    setImportedModelPage(0);
    setImportedModelSearch('');
    setImportedModelSearchInput('');
    setImportedModelCache({});
  }, []);
  const selectDataset = (value: unknown, dataset?: TrainingDataset) => {
    const normalizedId = Number(value);
    const next = Number.isInteger(normalizedId) && normalizedId > 0
      ? dataset || datasets.find((item) => Number(item.id) === normalizedId)
      : undefined;
    createTrainingIdempotencyKeyRef.current = null;
    setSubmitError(null);
    setSubmitConflict(false);
    setSelectedDatasetIds(next ? [normalizedId] : []);
    setDatasetId(next ? normalizedId : undefined);
    setSelectedDatasetOverride(next || null);
    setLabelPage(0);
    setLabelSearch('');
    setLabelSearchInput('');
    setLabelCache({});
    setSelectedLabelIds([]);
    setSelectedTaskIds([]);
    setSelectedSourceTaskIds([]);
    clearExplicitTrainedModel();
    resetModelFamilyBrowser();
    resetImportedModelBrowser();
  };
  const selectDatasets = (values: unknown, selectedRows?: TrainingDataset[]) => {
    const rawValues = Array.isArray(values) ? values : values == null ? [] : [values];
    const normalizedIds = Array.from(new Set(rawValues
      .map((value) => Number(value))
      .filter((value) => Number.isInteger(value) && value > 0)));
    const rowsById = new Map<number, TrainingDataset>(
      [...datasets, ...(selectedRows || [])].map((row) => [Number(row.id), row]),
    );
    const primary = normalizedIds[0];
    createTrainingIdempotencyKeyRef.current = null;
    setSubmitError(null);
    setSubmitConflict(false);
    setSelectedDatasetIds(normalizedIds);
    setDatasetId(primary);
    setSelectedDatasetOverride(primary ? rowsById.get(primary) || null : null);
    setLabelPage(0);
    setLabelSearch('');
    setLabelSearchInput('');
    setLabelCache({});
    setSelectedLabelIds([]);
    setSelectedTaskIds([]);
    setSelectedSourceTaskIds([]);
    clearExplicitTrainedModel();
    resetModelFamilyBrowser();
    resetImportedModelBrowser();
  };
  const datasetBrowserRows = datasetBrowserQuery.data?.items || [];
  const datasetBrowserScreens = Grid.useBreakpoint();
  const datasetBrowserNarrow = datasetBrowserScreens.sm === false;
  const datasetBrowserLoading = datasetBrowserQuery.isLoading || datasetBrowserQuery.isFetching;
  const datasetBrowserPagination = {
    current: (datasetBrowserQuery.data?.page ?? datasetBrowserPage) + 1,
    // The API reports the number of rows returned on the current page.
    // Keep the requested page size stable when the last page is short.
    pageSize: TRAINING_DATASET_BROWSER_PAGE_SIZE,
    total: datasetBrowserQuery.data?.total || 0,
    showSizeChanger: false,
    showTotal: (total: number) => `共 ${total.toLocaleString('zh-CN')} 个数据集`,
    onChange: (page: number) => setDatasetBrowserPage(page - 1),
  };
  const renderDatasetBrowserAction = (row: TrainingDataset) => {
    const selected = selectedDatasetIds.includes(Number(row.id));
    return (
      <Button
        type={selected ? 'default' : 'primary'}
        size="small"
        block={datasetBrowserNarrow}
        onClick={() => {
          const nextIds = selected
            ? selectedDatasetIds.filter((id) => id !== Number(row.id))
            : [...selectedDatasetIds, Number(row.id)];
          selectDatasets(nextIds, [row]);
        }}
      >
        {selected ? '取消选择' : datasetBrowserNarrow ? '选择此数据集' : '选择'}
      </Button>
    );
  };
  const datasetBrowserModal = (
    <Modal
      title="浏览全部逻辑数据集"
      open={datasetBrowserOpen}
      centered
      width="min(820px, calc(100vw - 24px))"
       footer={<Space style={{ width: '100%', justifyContent: 'space-between' }}><Typography.Text type="secondary">已选 {selectedDatasetIds.length} 个数据集</Typography.Text><Button type="primary" onClick={() => setDatasetBrowserOpen(false)} disabled={!selectedDatasetIds.length}>完成选择</Button></Space>}
      destroyOnClose={false}
      styles={{ body: { maxHeight: 'calc(100vh - 180px)', overflowY: 'auto', paddingRight: 4 } }}
      onCancel={() => setDatasetBrowserOpen(false)}
    >
      <div style={{ color: '#64748b', fontSize: 12, lineHeight: 1.6, marginBottom: 12 }}>
         数据集采用服务端搜索和分页；可跨页选择多个标签契约兼容的数据集，系统会在提交前校验契约并合并快照。
      </div>
      <Input.Search
        allowClear
        enterButton="搜索"
        placeholder="按数据集名称、分类或标签契约搜索"
        value={datasetBrowserInput}
        onSearch={(value) => {
          setDatasetBrowserSearch(value.trim());
          setDatasetBrowserInput(value);
          setDatasetBrowserPage(0);
        }}
        onChange={(event) => {
          setDatasetBrowserInput(event.target.value);
          if (!event.target.value) {
            setDatasetBrowserSearch('');
            setDatasetBrowserPage(0);
          }
        }}
        style={{ marginBottom: 12 }}
      />
      {datasetBrowserQuery.isError ? (
        <Alert
          type="error"
          showIcon
          message="数据集列表加载失败"
          description="请检查网络或权限后重试。"
          action={<Button size="small" onClick={() => void datasetBrowserQuery.refetch()}>重试</Button>}
          style={{ marginBottom: 12 }}
        />
      ) : datasetBrowserNarrow ? (
        <Spin spinning={datasetBrowserLoading} tip="正在加载数据集">
          {datasetBrowserRows.length ? (
            <div style={{ display: 'grid', gap: 8 }}>
              {datasetBrowserRows.map((row) => {
                const selected = selectedDatasetIds.includes(Number(row.id));
                return (
                  <div
                    key={row.id}
                    style={{
                      border: selected ? '1px solid #91caff' : '1px solid #e2e8f0',
                      borderRadius: 8,
                      background: selected ? '#f0f7ff' : '#fff',
                      padding: 12,
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8 }}>
                      <div style={{ minWidth: 0 }}>
                        <div style={{ color: '#0f172a', fontWeight: 600, overflowWrap: 'anywhere' }}>
                          {row.name || `数据集 #${row.id}`}
                        </div>
                        <div style={{ color: '#94a3b8', fontSize: 11, marginTop: 2 }}>逻辑数据集 #{row.id}</div>
                      </div>
                      <Tag color={String(row.status || '').toLowerCase() === 'active' || !row.status ? 'green' : 'default'} style={{ marginInlineEnd: 0, flex: '0 0 auto' }}>
                        {trainingDatasetStatusLabel(row)}
                      </Tag>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: '8px 12px', margin: '12px 0' }}>
                      <div style={{ minWidth: 0 }}>
                        <div style={{ color: '#94a3b8', fontSize: 11 }}>分类</div>
                        <div style={{ color: '#334155', fontSize: 12, overflowWrap: 'anywhere' }}>{row.dataset_category_name || '未分类'}</div>
                      </div>
                      <div style={{ minWidth: 0 }}>
                        <div style={{ color: '#94a3b8', fontSize: 11 }}>版本</div>
                        <div style={{ color: '#334155', fontSize: 12 }}>{Number(row.version_count || 0)} 个版本</div>
                      </div>
                      <div style={{ gridColumn: '1 / -1', minWidth: 0 }}>
                        <div style={{ color: '#94a3b8', fontSize: 11 }}>标签契约</div>
                        <div style={{ color: '#334155', fontSize: 12, overflowWrap: 'anywhere' }}>{trainingDatasetContractLabel(row)}</div>
                      </div>
                    </div>
                    {renderDatasetBrowserAction(row)}
                  </div>
                );
              })}
            </div>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={datasetBrowserSearch ? '没有匹配的数据集' : '暂无可用逻辑数据集'} />
          )}
        </Spin>
      ) : (
        <Table<TrainingDataset>
          rowKey={(row) => String(row.id)}
          size="small"
          loading={datasetBrowserLoading}
          dataSource={datasetBrowserRows}
          scroll={{ x: 650 }}
          locale={{ emptyText: datasetBrowserSearch ? '没有匹配的数据集' : '暂无可用逻辑数据集' }}
          pagination={datasetBrowserPagination}
          columns={[
            {
              title: '数据集',
              key: 'dataset',
              render: (_, row) => (
                <div style={{ minWidth: 180 }}>
                  <div style={{ color: '#0f172a', fontWeight: 600 }}>{row.name}</div>
                  <div style={{ color: '#94a3b8', fontSize: 11 }}>ID #{row.id}</div>
                </div>
              ),
            },
            {
              title: '分类 / 契约',
              key: 'contract',
              render: (_, row) => (
                <div style={{ minWidth: 160 }}>
                  <div>{row.dataset_category_name || '未分类'}</div>
                  <div style={{ color: '#64748b', fontSize: 12 }}>{trainingDatasetContractLabel(row)}</div>
                </div>
              ),
            },
            {
              title: '状态',
              key: 'status',
              render: (_, row) => (
                <div>
                  <Tag color={String(row.status || '').toLowerCase() === 'active' || !row.status ? 'green' : 'default'}>
                    {trainingDatasetStatusLabel(row)}
                  </Tag>
                  <div style={{ color: '#64748b', fontSize: 12 }}>{Number(row.version_count || 0)} 个版本</div>
                </div>
              ),
            },
            {
              title: '操作',
              key: 'action',
              width: 90,
              render: (_, row) => renderDatasetBrowserAction(row),
            },
          ]}
        />
      )}
      {datasetBrowserNarrow && datasetBrowserPagination.total > 0 && (
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 12, overflowX: 'auto', paddingBottom: 2 }}>
          <Pagination {...datasetBrowserPagination} size="small" />
        </div>
      )}
    </Modal>
  );
  const trainingLabelOptions = useMemo(() => {
    const datasetLabels = trainingDatasetLabelsForSelection(datasets, selectedDatasetIds);
    const selectedCachedLabels = selectedLabelIds
      .map((id) => labelCache[Number(id)])
      .filter((item): item is TrainingAnnotationLabel => Boolean(item));
    const source = datasetLabels.length
      ? datasetLabels
      : [...annotationLabels, ...selectedCachedLabels];
    const byId = new Map<number, TrainingAnnotationLabel>();
    source.forEach((item) => {
      const id = Number(item.id);
      if (Number.isInteger(id) && id > 0 && !byId.has(id)) {
        byId.set(id, item);
      }
    });
    // A stale selection can outlive a filtered server page. Keep a readable
    // fallback option so the value remains removable and submit-safe.
    selectedLabelIds.forEach((id) => {
      const normalizedId = Number(id);
      if (Number.isInteger(normalizedId) && normalizedId > 0 && !byId.has(normalizedId)) {
        byId.set(normalizedId, {
          id: normalizedId,
          label_key: `标签 #${normalizedId}`,
          label_value: `标签 #${normalizedId}`,
        });
      }
    });
    return Array.from(byId.values());
  }, [annotationLabels, datasets, labelCache, selectedDatasetIds, selectedLabelIds]);
  const selectedTaskCandidates = useMemo(
    () => selectedTaskIds
      .map((taskId) => candidateById.get(taskId))
      .filter((candidate): candidate is TrainingCandidate => Boolean(candidate)),
    [candidateById, selectedTaskIds],
  );
  const selectedTaskContractsCompatible = useMemo(() => {
    if (selectedTaskCandidates.length < 2) return true;
    const baseCandidate = selectedTaskCandidates[0];
    return selectedTaskCandidates.slice(1).every((candidate) => (
      trainingCandidatesShareLabelContract(baseCandidate, candidate)
    ));
  }, [selectedTaskCandidates]);
  const selectedTaskSelectionReady = !candidatesQuery.isError
    && selectedTaskIds.length > 0
    && selectedTaskCandidates.length === selectedTaskIds.length
    && selectedTaskCandidates.every((candidate) => trainingCandidateCanTrain(candidate))
    && selectedTaskContractsCompatible;
  const selectedCandidate = selectedTaskCandidates[0];
  const selectedSourceCandidates = useMemo(
    () => selectedSourceTaskIds
      .map((taskId) => candidateById.get(taskId))
      .filter((candidate): candidate is TrainingCandidate => Boolean(candidate)),
    [candidateById, selectedSourceTaskIds],
  );
  const selectedSourceSelectionReady = !candidatesQuery.isError
    && selectedSourceTaskIds.length > 0
    && selectedSourceCandidates.length === selectedSourceTaskIds.length
    && selectedSourceCandidates.every((candidate) => trainingCandidateCanTrain(candidate));
  const selectedCandidateSnapshotKey = useMemo(
    () => trainingCandidateSelectionSnapshotKey(
      Array.from(candidateById.values()),
      trainingDimension === 'task' ? selectedTaskIds : selectedSourceTaskIds,
    ),
    [candidateById, selectedSourceTaskIds, selectedTaskIds, trainingDimension],
  );
  const previousCandidateSnapshotRef = useRef('');
  useEffect(() => {
    if (!open) {
      previousCandidateSnapshotRef.current = '';
      return;
    }
    const previous = previousCandidateSnapshotRef.current;
    if (previous && previous !== selectedCandidateSnapshotKey) {
      // A candidate response can change eligibility or its label contract
      // without changing the task id. Never keep a parent preview that was
      // resolved against the old candidate snapshot.
      clearExplicitTrainedModel();
      resetIterationPreviewState();
    }
    previousCandidateSnapshotRef.current = selectedCandidateSnapshotKey;
  }, [open, selectedCandidateSnapshotKey]);
  const selectedTrainingLabels = useMemo(
    () => trainingLabelOptions.filter((label) => selectedLabelIds.includes(Number(label.id))),
    [selectedLabelIds, trainingLabelOptions],
  );
  const distributionTaskIds = useMemo(
    () => trainingDimension === 'task'
      ? selectedTaskIds.slice()
      : selectedSourceTaskIds.slice().sort((left, right) => left - right),
    [selectedSourceTaskIds, selectedTaskIds, trainingDimension],
  );
  const distributionTaskKey = distributionTaskIds.join(',');
  const primaryTrainingTaskId = trainingDimension === 'task' ? selectedTaskIds[0] : selectedSourceTaskIds[0];
  // The task-dimension merge endpoint has its own YOLO contract. Keep the
  // catalog-backed recipe for single-task jobs, but use the endpoint's
  // registered recipe for label-dimension and multi-task jobs.
  const iterationRecipeRef = trainingDimension === 'label'
    || (trainingDimension === 'task' && selectedTaskIds.length > 1)
    ? 'yolov11_detection::1'
    : defaultTrainingContract.recipeRef;
  const iterationRecipe = parseTrainingRecipeRef(iterationRecipeRef);
  const iterationPreviewQuery = useQuery<TrainingIterationPreview>({
    queryKey: [
      'training-iteration-preview', primaryTrainingTaskId ?? null, distributionTaskKey,
      labelIdsKey, sampleConditionsKey, iterationRecipeRef, weightsSource, weightsRefId ?? null,
      selectedCandidateSnapshotKey,
    ],
    queryFn: async () => {
      const response = await request.get('/v1/training/iteration-preview', {
        params: {
          task_id: primaryTrainingTaskId,
          source_task_ids: distributionTaskIds,
          ...(trainingDimension === 'label' ? { label_ids: selectedLabelIds } : {}),
          ...(weightsSource === 'trained' ? { parent_job_id: weightsRefId } : {}),
          ...(sampleConditionsKey ? { sample_conditions: trainingSampleConditionsJson(sampleConditions) } : {}),
          ...iterationRecipe,
        },
        paramsSerializer: { indexes: null },
      });
      return response.data?.data ?? response.data;
    },
    enabled: open && isLoggedIn && step >= 1 && Boolean(primaryTrainingTaskId)
      && (weightsSource === 'auto' || (weightsSource === 'trained' && Number(weightsRefId) > 0)),
    staleTime: 15_000,
    retry: false,
  });
  const [appliedAiReportId, setAppliedAiReportId] = useState<number | null>(null);
  const resetIterationPreviewState = () => {
    queryClient.removeQueries({ queryKey: ['training-iteration-preview'] });
    appliedAiPreviewRef.current = '';
    setAiIterationParams({});
    setAppliedAiReportId(null);
  };
  const iterationContextKey = `${weightsSource}:${weightsRefId || ''}:${primaryTrainingTaskId || ''}:${distributionTaskKey}:${labelIdsKey}:${sampleConditionsKey}:${iterationRecipeRef}:${selectedCandidateSnapshotKey}`;
  useEffect(() => {
    appliedAiPreviewRef.current = '';
    setAiIterationParams({});
    setAppliedAiReportId(null);
  }, [iterationContextKey]);
  const applyAiPreview = (preview: TrainingIterationPreview, force = false) => {
    const key = `${preview.weights_ref_id || 0}:${preview.ai_report_id || 0}`;
    if (!force && appliedAiPreviewRef.current === key) return;
    appliedAiPreviewRef.current = key;
    const { topLevel, hyperparams } = partitionAiIterationParams(preview.suggested_params);
    setAiIterationParams(hyperparams);
    setAppliedAiReportId(preview.ai_report_id || null);
    if (Object.keys(topLevel).length) form.setFieldsValue(topLevel);
  };
  useEffect(() => {
    if (iterationPreviewQuery.data) applyAiPreview(iterationPreviewQuery.data);
  }, [iterationPreviewQuery.data]);
  const trainingDistributionQuery = useQuery<DatasetAnalysisResponse>({
    queryKey: ['training-box-distribution', datasetId ?? null, distributionTaskKey],
    queryFn: async () => {
      const response = await getDatasetAnalysis(Number(datasetId), distributionTaskIds);
      const body = (response as any)?.data?.data ?? (response as any)?.data ?? response;
      return body as DatasetAnalysisResponse;
    },
    enabled: open && isLoggedIn && Boolean(datasetId),
    staleTime: 15_000,
    retry: false,
  });
  const trainingDistributionLabels = useMemo(() => {
    const contractLabels = trainingDimension === 'label' && selectedLabelIds.length
      ? selectedTrainingLabels
      : trainingDatasetLabelsForSelection(datasets, selectedDatasetIds);
    const aliases = new Map<string, string>();
    contractLabels.forEach((item) => {
      const display = String(item.label_value || item.label_key || '').trim();
      if (!display) return;
      [item.label_value, item.label_key].forEach((value) => {
        const key = String(value || '').trim();
        if (key) aliases.set(key, display);
      });
    });
    const rows = new Map<string, DatasetLabelDistributionItem>();
    (trainingDistributionQuery.data?.labels || []).forEach((item) => {
      const label = aliases.get(String(item.label || '').trim()) || String(item.label || '').trim();
      if (!label) return;
      const current = rows.get(label);
      rows.set(label, current
        ? { ...current, image_count: current.image_count + Number(item.image_count || 0), instance_count: current.instance_count + Number(item.instance_count || 0) }
        : { ...item, label });
    });
    contractLabels.forEach((item) => {
      const label = String(item.label_value || item.label_key || '').trim();
      if (label && !rows.has(label)) rows.set(label, { label, image_count: 0, instance_count: 0, percentage: 0, target_count: 0, gap_count: 0 });
    });
    return Array.from(rows.values());
  }, [datasets, selectedDatasetIds, selectedLabelIds.length, selectedTrainingLabels, trainingDimension, trainingDistributionQuery.data?.labels]);
  const trainingDistributionScope = trainingDimension === 'task'
    ? selectedTaskCandidates.length
      ? `任务：${selectedTaskCandidates.map((candidate) => candidate.task_name || `任务 #${candidate.task_id}`).join('、')}`
      : `数据集：${selectedDataset?.name || '当前数据集'}（全部任务）`
    : selectedSourceTaskIds.length
      ? `来源任务：${selectedSourceCandidates.map((item) => item.task_name || `任务 #${item.task_id}`).join('、')}`
      : `数据集：${selectedDataset?.name || '当前数据集'}（全部任务）`;
  const trainingDistributionPanel = datasetId ? (
    <TrainingLabelDistributionPreview
      labels={trainingDistributionLabels}
      totalImages={trainingDistributionQuery.data?.totals?.total_images || 0}
      scope={trainingDistributionScope}
      loading={trainingDistributionQuery.isLoading}
      error={trainingDistributionQuery.error}
    />
  ) : null;
  useEffect(() => {
    if (!open) {
      setStep(0);
      setTrainingDimension('task');
      setSelectedTaskIds(initialTaskId ? [initialTaskId] : []);
      setSelectedSourceTaskIds([]);
      setDatasetId(undefined);
      setSelectedDatasetIds([]);
      setSelectedDatasetOverride(null);
      setDatasetBrowserOpen(false);
      setDatasetBrowserPage(0);
      setDatasetBrowserSearch('');
      setDatasetBrowserInput('');
      setCandidatePage(0);
      setCandidateSearch('');
      setCandidateSearchInput('');
      setLabelPage(0);
      setLabelSearch('');
      setLabelSearchInput('');
       setModelFamilyPage(0);
       setModelFamilySearch('');
       setModelFamilySearchInput('');
       setSelectedTrainedModelOption(null);
      setCandidateCache({});
      setLabelCache({});
      setSelectedLabelIds([]);
      setSampleConditions({});
      setSubmitError(null);
      setSubmitConflict(false);
    }
  }, [open]);
  useEffect(() => {
    if (!open) return;
    // Ant Design keeps the modal wrapper scroll position while the wizard
    // replaces one step with another. Reset it so every step starts at its
    // heading, especially when a long step was scrolled on a phone.
    const wrap = trainingModalRef.current?.closest<HTMLElement>('.ant-modal-wrap');
    if (wrap) wrap.scrollTop = 0;
  }, [open, step]);
  useEffect(() => {
    if (!open || algorithmsQuery.isError) return;
    // 目录加载完成后，以服务端登记的默认配方为准；表单不可见字段仍
    // 保留这两个值，提交时继续生成完整训练血缘。
    form.setFieldsValue({
      algorithm_key: defaultTrainingContract.algorithmKey,
      training_recipe_ref: defaultTrainingContract.recipeRef,
    });
  }, [algorithmsQuery.isError, defaultTrainingContract, form, open]);
  useEffect(() => {
    setSelectedTaskIds([]);
    setSelectedSourceTaskIds([]);
    setCandidateCache({});
    setCandidatePage(0);
    setCandidateSearch('');
    setCandidateSearchInput('');
    resetModelFamilyBrowser();
  }, [datasetId, selectedDatasetIds.join(','), labelIdsKey, resetModelFamilyBrowser, trainingDimension]);

  useEffect(() => {
    // Candidate metadata and label selections define the compatibility scope
    // of the trained-model browser. A changed scope must start from page one
    // and a blank search, otherwise a valid family can appear to be missing.
    if (!open) return;
    resetModelFamilyBrowser();
  }, [
    open,
    modelSelectorLabelSignature,
    modelSelectorOrderSignature,
    modelSelectorLabelIdsKey,
    resetModelFamilyBrowser,
  ]);
  useEffect(() => {
    setSelectedTaskIds((current) => {
      const next = current.filter((id) => candidateById.has(id));
      return next.length === current.length ? current : next;
    });
    setSelectedSourceTaskIds((current) => {
      const next = current.filter((id) => candidateById.has(id));
      return next.length === current.length ? current : next;
    });
  }, [candidateById]);
  useEffect(() => {
    if (open && initialTaskId && !selectedTaskIds.length && candidateById.has(initialTaskId)) {
      setSelectedTaskIds([initialTaskId]);
    }
  }, [candidateById, initialTaskId, open, selectedTaskIds.length]);
  const next = async () => {
    setSubmitError(null);
    setSubmitConflict(false);
    if (step === 0) {
      if (
        trainingDimension === 'task'
        && !selectedTaskSelectionReady
      ) {
        if (!selectedTaskContractsCompatible) {
          message.warning('只能合并标签契约一致的任务，请取消不兼容任务');
          return;
        }
        message.warning('请选择至少一个达到训练门槛的任务');
        return;
      }
      if (trainingDimension === 'label' && !selectedLabelIds.length) {
        message.warning('请选择至少一个目标标签');
        return;
      }
      if (trainingDimension === 'label' && !selectedSourceSelectionReady) {
        message.warning('请选择至少一个达到训练门槛的来源任务');
        return;
      }
      setStep(1);
      return;
    }
    if (step === 1) {
      if (algorithmsQuery.isLoading) {
        setSubmitError('训练目录仍在加载，请稍候重试');
        return;
      }
      if ((weightsSource === 'auto' || weightsSource === 'trained') && iterationPreviewQuery.isLoading) {
        message.warning('正在确认迭代模型和 AI 参数，请稍候');
        return;
      }
      if ((weightsSource === 'auto' || weightsSource === 'trained') && (iterationPreviewQuery.error || !iterationPreviewQuery.data)) {
        message.warning('迭代预检失败，请重试后再继续');
        return;
      }
      try { await form.validateFields(); setStep(2); } catch { /* field messages are rendered by Ant Design */ }
    }
  };
  const submit = async () => {
    if (
      (trainingDimension === 'task' && !selectedTaskSelectionReady)
      || (trainingDimension === 'label' && !selectedSourceSelectionReady)
    ) {
      if (trainingDimension === 'task') {
        setSubmitError(
          candidatesQuery.isError
            ? '候选任务加载失败，请刷新候选任务后重试'
            : !selectedTaskContractsCompatible
              ? '所选任务的标签契约已变化，请返回重新选择'
              : '所选任务已不满足训练门槛，请返回重新选择',
        );
      } else {
        setSubmitError(
          candidatesQuery.isError
            ? '来源任务加载失败，请刷新候选任务后重试'
            : '所选来源任务已不满足训练门槛，请返回重新选择',
        );
      }
      return;
    }
    try {
      // The wizard unmounts the parameter step before the confirmation step.
      // Ant Design's validateFields() only returns currently registered
      // fields, so hidden values such as dataset_version_id can be omitted
      // even though the confirmation summary still reads them from the form
      // store. Merge the full preserved form store back into the validated
      // values before constructing the platform training contract.
      const validatedValues = await form.validateFields();
      const values = mergeTrainingFormValues(form.getFieldsValue(true), validatedValues);
      if (isRemoteNodeMissing(values.execution_mode, values.train_node_id)) {
        setSubmitError('远程训练必须填写节点 ID');
        return;
      }
      if (isUnsupportedGenericExecution(values.algorithm_key, values.execution_mode)) {
        setSubmitError('通用算法必须使用标准容器 Worker');
        return;
      }
      const taskMergeTraining = trainingDimension === 'task'
        && taskTrainingUsesMergedEndpoint(selectedTaskIds, selectedDatasetIds);
      if (taskMergeTraining && values.execution_mode !== 'local') {
        setSubmitError('任务范围合并训练目前仅支持本机 GPU 队列，请切换执行资源后再开始训练');
        return;
      }
      setSubmitting(true); setSubmitError(null); setSubmitConflict(false);
      if (!createTrainingIdempotencyKeyRef.current) {
        createTrainingIdempotencyKeyRef.current = createIdempotencyKey('training');
      }
      const recipe = parseTrainingRecipeRef(values.training_recipe_ref);
      const pinnedWeights = pinIterationWeights(
        values.weights_source,
        values.model_weights,
        values.weights_ref_id ? Number(values.weights_ref_id) : undefined,
        iterationPreviewQuery.data,
      );
      const modelWeights = pinnedWeights.model_weights;
      const lineage = {
        weights_source: pinnedWeights.weights_source,
        weights_ref_id: pinnedWeights.weights_ref_id,
        ...recipe,
      };
      if (trainingDimension === 'label') {
        const labelNames = selectedTrainingLabels.map((label) => label.label_value || label.label_key).filter(Boolean);
        const generatedName = `标签训练 · ${labelNames.join('、') || selectedLabelIds.join('、')}`;
        await createLabelTrainingJob({
          name: (values.training_name?.trim() || generatedName).slice(0, 128),
          selected_task_ids: selectedSourceTaskIds,
          dataset_id: datasetId,
          dataset_ids: selectedDatasetIds,
          label_ids: selectedLabelIds,
          // The label-dimension endpoint is intentionally YOLO-only.  Keep
          // the contract deterministic even if a stale form value survives
          // a dimension switch.
          training_recipe_key: 'yolov11_detection',
          training_recipe_version: 1,
          runtime_profile: 'yolov11_detection',
          runtime_interface_version: 1,
          sample_conditions: sampleConditions,
          model_weights: modelWeights,
          weights_source: pinnedWeights.weights_source,
          weights_ref_id: pinnedWeights.weights_ref_id,
          epochs: Number(values.epochs),
          imgsz: Number(values.imgsz),
          hyperparams: {
            ...aiIterationParams,
            execution_mode: values.execution_mode,
            ...(values.train_node_id ? { train_node_id: values.train_node_id.trim() } : {}),
            ...(values.container_image ? { container_image: values.container_image.trim() } : {}),
            dataset_id: datasetId,
            dataset_ids: selectedDatasetIds,
            source_task_ids: selectedSourceTaskIds,
          },
        }, createTrainingIdempotencyKeyRef.current || undefined);
      } else if (taskMergeTraining) {
        const generatedName = selectedTaskIds.length > 1
          ? `任务合并训练 · ${selectedTaskCandidates.map((candidate) => candidate.task_name || `任务 #${candidate.task_id}`).join('、')}`
          : `多数据集任务训练 · ${selectedTaskCandidates[0]?.task_name || `任务 #${selectedTaskIds[0]}`}`;
        await createLabelTrainingJob({
          name: (values.training_name?.trim() || generatedName).slice(0, 128),
          selected_task_ids: selectedTaskIds,
          ...(datasetId ? { dataset_id: datasetId } : {}),
          dataset_ids: selectedDatasetIds,
          // Task-first mode derives the complete label contract from the
          // selected tasks; sending label_ids would switch the server into
          // label-first semantics and weaken the full-contract gate.
          training_recipe_key: 'yolov11_detection',
          training_recipe_version: 1,
          runtime_profile: 'yolov11_detection',
          runtime_interface_version: 1,
          sample_conditions: sampleConditions,
          model_weights: modelWeights,
          weights_source: pinnedWeights.weights_source,
          weights_ref_id: pinnedWeights.weights_ref_id,
          epochs: Number(values.epochs),
          imgsz: Number(values.imgsz),
          hyperparams: {
            ...aiIterationParams,
            execution_mode: 'local',
            dataset_id: datasetId,
            dataset_ids: selectedDatasetIds,
            source_task_ids: selectedTaskIds,
          },
        }, createTrainingIdempotencyKeyRef.current || undefined);
      } else {
        await createTaskTrainingJob({
          task_id: selectedCandidate!.task_id, algorithm_key: values.algorithm_key,
          ...(datasetId ? { dataset_id: datasetId } : {}),
          dataset_ids: selectedDatasetIds,
          ...(values.dataset_version_id ? { dataset_version_id: Number(values.dataset_version_id) } : {}),
          sample_conditions: sampleConditions,
          model_weights: modelWeights,
          epochs: Number(values.epochs), imgsz: Number(values.imgsz), execution_mode: values.execution_mode,
          train_node_id: values.train_node_id?.trim() || undefined,
          ...lineage,
          params: aiIterationParams,
          container_image: values.container_image?.trim() || undefined,
        }, createTrainingIdempotencyKeyRef.current || undefined);
      }
      message.success('训练作业已创建，已进入训练队列');
      setOpen(false);
      await queryClient.invalidateQueries({ queryKey: ['training-jobs'] });
    } catch (error: any) {
      const status = Number(error?.response?.status);
      const conflict = status === 409;
      const text = trainingCreateErrorMessage(error);
      if (conflict) {
        // A 409 is a definitive rejection rather than an ambiguous network
        // failure. The next submit must use the refreshed state and a new key.
        createTrainingIdempotencyKeyRef.current = null;
        clearExplicitTrainedModel();
        resetIterationPreviewState();
        setSelectedTaskIds([]);
        setSelectedSourceTaskIds([]);
        setCandidateCache({});
        setCandidatePage(0);
        setCandidateSearch('');
        setCandidateSearchInput('');
        setStep(0);
        void invalidateTrainingSelectionQueries().catch(() => undefined);
      }
      setSubmitConflict(conflict);
      setSubmitError(text); message.error(text);
    } finally { setSubmitting(false); }
  };
  const prepareTrainingModal = () => {
    form.resetFields();
    createTrainingIdempotencyKeyRef.current = null;
    setStep(0);
    setTrainingDimension('task');
    setDatasetId(undefined);
    setSelectedDatasetIds([]);
    setSelectedDatasetOverride(null);
    setSelectedTaskIds(initialTaskId ? [initialTaskId] : []);
    setSelectedSourceTaskIds([]);
    setSelectedLabelIds([]);
    setSampleConditions({});
    setDatasetBrowserOpen(false);
    setDatasetBrowserPage(0);
    setDatasetBrowserSearch('');
    setDatasetBrowserInput('');
    setLabelPage(0);
    setLabelSearch('');
    setLabelSearchInput('');
    setCandidatePage(0);
    setCandidateSearch('');
    setCandidateSearchInput('');
    setModelFamilyPage(0);
    setModelFamilySearch('');
    setModelFamilySearchInput('');
    setImportedModelPage(0);
    setImportedModelSearch('');
    setImportedModelSearchInput('');
    setSelectedTrainedModelOption(null);
    setCandidateCache({});
    setLabelCache({});
    setImportedModelCache({});
    setSubmitError(null);
    setSubmitConflict(false);
  };
  return <>
    <Space style={{ marginBottom: 14 }}><Button type="primary" icon={<PlusOutlined />} disabled={!isLoggedIn} onClick={() => { prepareTrainingModal(); setOpen(true); }}>新建训练作业</Button>{!isLoggedIn && <Alert type="warning" showIcon message="请先登录后创建训练作业" />}</Space>
     <Modal title="新建训练作业" open={open} centered width="min(820px, calc(100vw - 24px))" destroyOnClose okText={step === 2 ? '开始训练' : '下一步'} cancelText="取消" confirmLoading={submitting} onCancel={() => { if (!submitting) setOpen(false); }} onOk={() => (step === 2 ? void submit() : void next())} modalRender={(node) => <div ref={trainingModalRef}>{node}</div>} footer={(_, { OkBtn, CancelBtn }) => <Space><CancelBtn />{step > 0 && <Button icon={<ArrowLeftOutlined />} disabled={submitting} onClick={() => setStep((value) => value - 1)}>上一步</Button>}{step < 2 ? <Button type="primary" icon={<ArrowRightOutlined />} disabled={submitting || (step === 0 && ((trainingDimension === 'task' && (candidatesQuery.isLoading || !selectedTaskSelectionReady)) || (trainingDimension === 'label' && (candidatesQuery.isLoading || !selectedSourceSelectionReady)))) || (step === 1 && (algorithmsQuery.isLoading || algorithmsQuery.isError))} onClick={() => void next()}>下一步</Button> : <OkBtn />}</Space>}>
      <Steps current={step} size="small" items={[{ title: '选择数据' }, { title: '训练参数' }, { title: '确认训练' }]} style={{ marginBottom: 22 }} />
      {submitError && (
        <Alert
          type="error"
          showIcon
          message={submitError}
          action={submitConflict ? <Button size="small" loading={candidatesQuery.isFetching} onClick={() => void refreshTrainingCandidates()}>刷新候选任务</Button> : undefined}
          style={{ marginBottom: 14 }}
        />
      )}
      <Form<CreateTrainingValues> form={form} layout="vertical" onValuesChange={(changed) => {
        // Editing any field creates a new payload, so do not reuse a key from
        // a previous failed submission.
        createTrainingIdempotencyKeyRef.current = null;
        setSubmitError(null);
        setSubmitConflict(false);
        if (changed.weights_source) {
          setSelectedTrainedModelOption(null);
          setModelFamilyPage(0);
          setModelFamilySearch('');
          setModelFamilySearchInput('');
          setImportedModelPage(0);
          setImportedModelSearch('');
          setImportedModelSearchInput('');
          setImportedModelCache({});
          form.setFieldsValue({
            weights_ref_id: undefined,
            model_weights: ['preset', 'auto'].includes(changed.weights_source)
              ? 'yolo11n.pt'
              : undefined,
          });
        }
      }} initialValues={{ model_weights: 'yolo11n.pt', weights_source: 'auto', epochs: 50, imgsz: 640, execution_mode: 'local', algorithm_key: DEFAULT_TRAINING_ALGORITHM_KEY, training_recipe_ref: DEFAULT_TRAINING_RECIPE_REF }}>
        {step === 0 && <div>
          <div style={{ marginBottom: 16 }}>
            <div style={{ color: '#0f172a', fontWeight: 700, marginBottom: 4 }}>选择训练维度</div>
            <div style={{ color: '#64748b', fontSize: 12, marginBottom: 10 }}>
              两种训练方式相互独立：任务训练保留完整标签契约，可合并标签契约一致的多个任务；标签训练跨多个任务汇总指定标签。
            </div>
            <Radio.Group
              value={trainingDimension}
              onChange={(event) => {
                const nextDimension = event.target.value as TrainingDimension;
                setTrainingDimension(nextDimension);
                createTrainingIdempotencyKeyRef.current = null;
                setSubmitError(null);
                setSubmitConflict(false);
                setSelectedTaskIds([]);
                setSelectedSourceTaskIds([]);
                setSelectedLabelIds([]);
                setSampleConditions({});
                setLabelPage(0);
                setLabelSearch('');
                setLabelSearchInput('');
                setLabelCache({});
                clearExplicitTrainedModel();
                resetModelFamilyBrowser();
                form.setFieldsValue({
                  algorithm_key: defaultTrainingContract.algorithmKey,
                  training_recipe_ref: defaultTrainingContract.recipeRef,
                });
              }}
              style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 10, width: '100%' }}
            >
              <Radio.Button value="task" style={{ height: 'auto', padding: 0, borderRadius: 8 }}>
                <div style={{ padding: '12px 14px' }}>
                  <div style={{ color: '#0f172a', fontWeight: 700 }}>按任务训练</div>
                  <div style={{ color: '#64748b', fontSize: 12, lineHeight: 1.55, marginTop: 4 }}>
                     选择一个或多个标注任务，系统使用所选任务中已完成且质量合格的图片训练。
                  </div>
                </div>
              </Radio.Button>
              <Radio.Button value="label" style={{ height: 'auto', padding: 0, borderRadius: 8 }}>
                <div style={{ padding: '12px 14px' }}>
                  <div style={{ color: '#0f172a', fontWeight: 700 }}>按标签训练</div>
                  <div style={{ color: '#64748b', fontSize: 12, lineHeight: 1.55, marginTop: 4 }}>
                    选择目标标签和多个来源任务，只保留这些标签的标注样本。
                  </div>
                </div>
              </Radio.Button>
            </Radio.Group>
          </div>
          {algorithmsQuery.isError && (
            <Alert
              type="error"
              showIcon
              message="训练目录加载失败"
              description="数据集、算法和训练配方暂时无法读取；请重试，不要把空列表当成没有数据。"
              action={<Button size="small" onClick={() => void algorithmsQuery.refetch()}>重试</Button>}
              style={{ marginBottom: 14 }}
            />
          )}
          {!algorithmsQuery.isError && Boolean(algorithmsQuery.data?.datasets_truncated) && (
            <Alert
              type="info"
              showIcon
              message={`数据集较多：快速下拉展示 ${catalogDatasets.length.toLocaleString('zh-CN')} / ${Number(algorithmsQuery.data?.datasets_total || 0).toLocaleString('zh-CN')} 个`}
              description="请点击每个选择框旁的“浏览全部”，使用服务端搜索和分页选择；搜索结果选择后会保留在当前训练作业中。"
              style={{ marginBottom: 14 }}
            />
          )}
          {trainingDimension === 'task' ? <div>
          <Alert
            type="info"
            showIcon
             message="任务维度：逻辑数据集和标注任务均可多选"
             description="可选择一个或多个标签契约兼容的逻辑数据集，再勾选一个或多个标注任务；系统会跨页保留选择并在提交前合并为一份不可变快照。如果只训练某几个标签，请切换到“按标签训练”。"
            style={{ marginBottom: 14 }}
          />
          <Space direction="vertical" size={4} style={{ width: '100%', marginBottom: 14 }}>
             <span style={{ color: '#334155', fontWeight: 600 }}>逻辑数据集</span>
              <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
               <Select
              mode="multiple"
              value={selectedDatasetIds}
              allowClear
              showSearch
              optionLabelProp="datasetName"
              filterOption={(input, option) => String((option as any)?.searchText || '').toLowerCase().includes(input.trim().toLowerCase())}
               style={{ width: '100%', flex: 1, minWidth: 0 }}
              popupMatchSelectWidth={500}
              loading={algorithmsQuery.isLoading}
              placeholder="请选择逻辑数据集"
              notFoundContent={algorithmsQuery.isLoading ? <Spin size="small" /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可用逻辑数据集" />}
              options={datasets.map((item) => {
                const frozenVersion = frozenVersionByDataset.get(Number(item.id));
                const pendingVersion = pendingVersionByDataset.get(Number(item.id));
                const hasContract = Number(item.dataset_label_contract_revision || 0) > 0;
                const statusLabel = frozenVersion ? '可直接训练' : '训练时自动固化';
                const statusColor = frozenVersion ? 'green' : 'blue';
                const searchText = trainingDatasetSearchLabel(item, frozenVersion, pendingVersion);
                return {
                  value: item.id,
                   datasetName: trainingDatasetDisplayLabel(item),
                  searchText,
                  label: (
                    <div style={{ padding: '4px 0', minWidth: 430 }}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
                         <span title={trainingDatasetDisplayLabel(item)} style={{ color: '#0f172a', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.name || '未命名数据集'}</span>
                         <span style={{ color: '#64748b', fontSize: 12, flex: 'none' }}>#{item.id}</span>
                        <Tag color={statusColor} style={{ flex: 'none', marginInlineEnd: 0 }}>{statusLabel}</Tag>
                      </div>
                      <div style={{ color: '#64748b', fontSize: 12, lineHeight: 1.6, whiteSpace: 'normal' }}>
                        <span>{trainingDatasetContractLabel(item)}</span>
                      </div>
                      <div style={{ color: hasContract ? '#475569' : '#2563eb', fontSize: 12, lineHeight: 1.6, whiteSpace: 'normal' }}>
                        {trainingDatasetVersionLabel(frozenVersion, pendingVersion)}
                      </div>
                    </div>
                  ),
                };
              })}
               onChange={(value) => selectDatasets(value)}
            />
             <Button
               icon={<DatabaseOutlined />}
               onClick={() => { setDatasetBrowserPage(0); setDatasetBrowserOpen(true); }}
               style={{ flex: 'none' }}
             >浏览全部</Button>
             </div>
            {selectedDatasets.length > 0 && (
              <div
                role="status"
                style={{
                  border: '1px solid #dbeafe',
                  borderRadius: 8,
                  background: '#f8fbff',
                  padding: '8px 10px',
                  color: '#475569',
                  fontSize: 12,
                  lineHeight: 1.65,
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                   <strong style={{ color: '#0f172a' }}>已选 {selectedDatasets.length} 个数据集</strong>
                   <span style={{ color: '#64748b' }}>{selectedDatasets.map((item) => `${item.name} #${item.id}`).join('、')}</span>
                  <Tag color="blue" style={{ marginInlineEnd: 0 }}>
                    提交时校验标签契约
                  </Tag>
                </div>
                 <div>
                  {selectedDatasets.map((item) => `${trainingDatasetContractLabel(item)} · ${trainingDatasetVersionLabel(frozenVersionByDataset.get(Number(item.id)), pendingVersionByDataset.get(Number(item.id)))}`).join('；')}
                </div>
              </div>
            )}
          </Space>
          {!datasetId
            ? <Alert type="warning" showIcon message="请选择逻辑数据集后加载候选任务" description="这是第一层筛选条件，可避免一次性加载全部已标注任务。" />
            : <div>
              <TrainingCandidateBrowserControls
                searchInput={candidateSearchInput}
                placeholder="按任务名称或数据集名称搜索"
                candidateLabel="候选任务"
                page={candidatePage}
                pageSize={TRAINING_CANDIDATE_PAGE_SIZE}
                total={candidateTotal}
                currentCount={candidates.length}
                selectedCount={selectedTaskIds.length}
                loading={candidatesQuery.isFetching}
                onSearchInputChange={(value) => {
                  setCandidateSearchInput(value);
                  if (!value) {
                    setCandidateSearch('');
                    setCandidatePage(0);
                  }
                }}
                onSearch={(value) => {
                  setCandidateSearch(value.trim());
                  setCandidatePage(0);
                }}
                onPageChange={setCandidatePage}
              />
              {candidatesQuery.isLoading
                ? <div role="status" aria-live="polite" style={{ padding: '28px 0', textAlign: 'center' }}><Space direction="vertical" size={8}><Spin /><span style={{ color: '#64748b', fontSize: 13 }}>正在按数据集查找可训练任务</span></Space></div>
                : candidatesQuery.error
                  ? <Alert type="error" showIcon message="候选任务加载失败" description="请刷新后重试。" action={<Button size="small" onClick={() => void candidatesQuery.refetch()}>重试</Button>} />
                   : candidateTotal === 0
                     ? <div><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={candidateSearch ? '没有匹配的候选任务' : '没有符合数据集条件的可训练任务'} /><div style={{ textAlign: 'center', marginTop: -8, color: '#64748b', fontSize: 13 }}>请检查数据集、标签契约和已完成样本；需要调整时回到“任务与标注”处理。</div></div>
                      : <div>
                         <Space direction="vertical" size={8} style={{ width: '100%', maxHeight: 360, overflowY: 'auto', paddingRight: 4 }}>
                          {candidates.map((candidate) => {
                            const canTrain = trainingCandidateCanTrain(candidate);
                            const compatible = !selectedTaskCandidates.length
                              || trainingCandidatesShareLabelContract(selectedTaskCandidates[0], candidate);
                            const checked = selectedTaskIds.includes(candidate.task_id);
                            return (
                              <Checkbox
                                key={candidate.task_id}
                                checked={checked}
                                // Keep a stale selection uncheckable after a
                                // candidate refresh changes its eligibility.
                                disabled={!checked && (!canTrain || !compatible)}
                                onChange={(event) => {
                                  if (event.target.checked && !compatible) return;
                                  createTrainingIdempotencyKeyRef.current = null;
                                  setSubmitError(null);
                                  setSubmitConflict(false);
                                   clearExplicitTrainedModel();
                                  resetModelFamilyBrowser();
                                  setSelectedTaskIds((current) => event.target.checked
                                    ? (current.includes(candidate.task_id) ? current : [...current, candidate.task_id])
                                    : current.filter((id) => id !== candidate.task_id));
                                }}
                                style={{
                                  width: '100%',
                                  marginInlineEnd: 0,
                                  border: `1px solid ${canTrain && compatible ? '#e2e8f0' : '#f1f5f9'}`,
                                  borderRadius: 8,
                                  padding: '10px 12px',
                                  background: canTrain && compatible ? '#fff' : '#f8fafc',
                                }}
                              >
                                <TrainingCandidateSummary candidate={candidate} />
                              </Checkbox>
                            );
                          })}
                        </Space>
                        {selectedTaskCandidates.length > 0 && (
                          <div style={{ marginTop: 10, color: '#475569', fontSize: 12 }}>
                           已选 {selectedTaskCandidates.length} 个标注任务，将合并约 {selectedTaskCandidates.reduce((sum, item) => sum + trainingCandidateStats(item).trainable, 0).toLocaleString('zh-CN')} 张可训练图片。
                          </div>
                        )}
                        {taskTrainingUsesMergedEndpoint(selectedTaskIds, selectedDatasetIds) && (
                          <Alert
                            type="info"
                            showIcon
                            style={{ marginTop: 10 }}
                            message="任务范围合并训练仅支持本机 GPU 队列"
                            description="当前选择包含多个任务或多个逻辑数据集，系统会创建一份包含完整数据集范围的合并快照；如需远程或容器执行，请先只选择一个任务和一个数据集。"
                          />
                        )}
                       </div>}
            </div>}
          </div> : <div>
            <Alert
              type="info"
              showIcon
              message="标签维度：选择目标标签，再选择来源任务"
              description="系统会跨所选来源任务提取目标标签对应的已完成样本，其他标签会从训练数据中清理，不会混入本次模型。"
              style={{ marginBottom: 14 }}
            />
             <Space direction="vertical" size={4} style={{ width: '100%', marginBottom: 14 }}>
               <span style={{ color: '#334155', fontWeight: 600 }}>逻辑数据集</span>
               <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
               <Select
                mode="multiple"
                value={selectedDatasetIds}
                allowClear
                showSearch
                optionLabelProp="datasetName"
                filterOption={(input, option) => String((option as any)?.searchText || '').toLowerCase().includes(input.trim().toLowerCase())}
                 style={{ width: '100%', flex: 1, minWidth: 0 }}
                popupMatchSelectWidth={500}
                loading={algorithmsQuery.isLoading}
                placeholder="请选择标签训练的数据集范围"
                options={datasets.map((item) => ({
                  value: item.id,
                   datasetName: trainingDatasetDisplayLabel(item),
                  searchText: trainingDatasetSearchLabel(item, frozenVersionByDataset.get(Number(item.id)), pendingVersionByDataset.get(Number(item.id))),
                   label: <div style={{ padding: '4px 0', minWidth: 430 }}><div style={{ fontWeight: 600 }}><span>{item.name || '未命名数据集'}</span><span style={{ color: '#64748b', fontSize: 12, marginLeft: 6 }}>#{item.id}</span></div><div style={{ color: '#64748b', fontSize: 12 }}>{trainingDatasetContractLabel(item)}</div></div>,
                }))}
                 onChange={(value) => selectDatasets(value)}
              />
               <Button
                 icon={<DatabaseOutlined />}
                 onClick={() => { setDatasetBrowserPage(0); setDatasetBrowserOpen(true); }}
                 style={{ flex: 'none' }}
               >浏览全部</Button>
                </div>
             </Space>
             {selectedDatasets.length > 0 && (
               <div role="status" style={{ border: '1px solid #dbeafe', borderRadius: 8, background: '#f8fbff', padding: '8px 10px', color: '#475569', fontSize: 12, lineHeight: 1.65, marginBottom: 14 }}>
                 <strong style={{ color: '#0f172a' }}>已选 {selectedDatasets.length} 个数据集</strong>
                 <div>{selectedDatasets.map((item) => `${item.name} #${item.id}`).join('、')}</div>
                 <div>{selectedDatasets.map((item) => `${trainingDatasetContractLabel(item)} · ${trainingDatasetVersionLabel(frozenVersionByDataset.get(Number(item.id)), pendingVersionByDataset.get(Number(item.id)))}`).join('；')}</div>
               </div>
             )}
             <Space direction="vertical" size={4} style={{ width: '100%', marginBottom: 14 }}>
              <span style={{ color: '#334155', fontWeight: 600 }}>目标标签 <span style={{ color: '#94a3b8', fontWeight: 400 }}>（可多选）</span></span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <ServerSearchInput
                  value={labelSearchInput}
                  onChange={(value) => {
                    setLabelSearchInput(value);
                    if (!value) {
                      setLabelSearch('');
                      setLabelPage(0);
                    }
                  }}
                  onSearch={(value) => {
                    const normalized = value.trim();
                    setLabelSearchInput(normalized);
                    setLabelSearch(normalized);
                    setLabelPage(0);
                  }}
                  placeholder="按标签名称、标识或描述搜索"
                  ariaLabel="搜索目标标签"
                  loading={labelsQuery.isFetching}
                  style={{ flex: '1 1 280px', minWidth: 220 }}
                />
                <span style={{ color: '#64748b', fontSize: 12, whiteSpace: 'nowrap' }}>
                  共 {Number(annotationLabelTotal || 0).toLocaleString('zh-CN')} 个标签 · 当前页 {annotationLabels.length} 个 · 已选 {selectedLabelIds.length} 个
                </span>
              </div>
              {annotationLabelTotal > TRAINING_LABEL_PAGE_SIZE && (
                <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 4, overflowX: 'auto', paddingBottom: 2 }}>
                  <Pagination
                    size="small"
                    current={(labelsQuery.data?.page ?? labelPage) + 1}
                    pageSize={TRAINING_LABEL_PAGE_SIZE}
                    total={annotationLabelTotal}
                    showSizeChanger={false}
                    showTotal={(total) => `共 ${total.toLocaleString('zh-CN')} 个标签`}
                    onChange={(nextPage) => setLabelPage(nextPage - 1)}
                  />
                </div>
              )}
              <Select
                mode="multiple"
                value={selectedLabelIds}
                showSearch
                optionFilterProp="label"
                style={{ width: '100%' }}
                loading={labelsQuery.isLoading || labelsQuery.isFetching}
                disabled={!datasetId}
                placeholder={datasetId ? '选择要训练的标签' : '请先选择逻辑数据集'}
                options={trainingLabelOptions.map((item) => ({ value: Number(item.id), label: item.label_value || item.label_key }))}
                onChange={(values) => {
                  const normalizedValues = (values as number[]).map(Number);
                  setLabelCache((current) => {
                    const next = { ...current };
                    trainingLabelOptions.forEach((label) => {
                      const id = Number(label.id);
                      if (Number.isInteger(id) && id > 0) next[id] = label;
                    });
                    return next;
                  });
                  createTrainingIdempotencyKeyRef.current = null;
                  setSubmitError(null);
                  setSubmitConflict(false);
                  clearExplicitTrainedModel();
                  resetModelFamilyBrowser();
                  setSelectedLabelIds(normalizedValues);
                  setSelectedSourceTaskIds([]);
                }}
                maxTagCount="responsive"
              />
            </Space>
            {!selectedLabelIds.length
              ? <Alert type="warning" showIcon message="先选择目标标签" description="标签训练不会默认使用整个任务契约，必须明确指定本次模型要学习的标签。" />
              : candidatesQuery.isLoading
                ? <div role="status" aria-live="polite" style={{ padding: '28px 0', textAlign: 'center' }}><Space direction="vertical" size={8}><Spin /><span style={{ color: '#64748b', fontSize: 13 }}>正在查找覆盖所选标签的来源任务</span></Space></div>
                : candidatesQuery.error
                  ? <Alert type="error" showIcon message="来源任务加载失败" description="请刷新后重试。" action={<Button size="small" onClick={() => void candidatesQuery.refetch()}>重试</Button>} />
                  : <div>
                    <TrainingCandidateBrowserControls
                      searchInput={candidateSearchInput}
                      placeholder="按来源任务名称或数据集名称搜索"
                      candidateLabel="来源任务"
                      page={candidatePage}
                      pageSize={TRAINING_CANDIDATE_PAGE_SIZE}
                      total={candidateTotal}
                      currentCount={candidates.length}
                      selectedCount={selectedSourceTaskIds.length}
                      loading={candidatesQuery.isFetching}
                      onSearchInputChange={(value) => {
                        setCandidateSearchInput(value);
                        if (!value) {
                          setCandidateSearch('');
                          setCandidatePage(0);
                        }
                      }}
                      onSearch={(value) => {
                        setCandidateSearch(value.trim());
                        setCandidatePage(0);
                      }}
                      onPageChange={setCandidatePage}
                    />
                    {candidateTotal === 0
                      ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={candidateSearch ? '没有匹配的来源任务' : '没有覆盖所选标签的可训练任务'} />
                      : <div>
                       <Space direction="vertical" size={8} style={{ width: '100%', maxHeight: 360, overflowY: 'auto', paddingRight: 4 }}>
                        {candidates.map((candidate) => (
                          <Checkbox
                            key={candidate.task_id}
                            checked={selectedSourceTaskIds.includes(candidate.task_id)}
                            // Keep a stale selected row removable so the user
                            // can recover without resetting the whole wizard.
                            disabled={!trainingCandidateCanTrain(candidate) && !selectedSourceTaskIds.includes(candidate.task_id)}
                            onChange={(event) => {
                              createTrainingIdempotencyKeyRef.current = null;
                              setSubmitError(null);
                              setSubmitConflict(false);
                              clearExplicitTrainedModel();
                              resetModelFamilyBrowser();
                              setSelectedSourceTaskIds((current) => event.target.checked
                                ? (current.includes(candidate.task_id) ? current : [...current, candidate.task_id])
                                : current.filter((id) => id !== candidate.task_id));
                            }}
                            style={{ width: '100%', border: `1px solid ${trainingCandidateCanTrain(candidate) ? '#e2e8f0' : '#f1f5f9'}`, borderRadius: 8, padding: '9px 10px', background: trainingCandidateCanTrain(candidate) ? '#fff' : '#f8fafc' }}
                          >
                            <span style={{ display: 'inline-flex', flexDirection: 'column', gap: 2 }}>
                              <TrainingCandidateSummary candidate={candidate} compact />
                            </span>
                          </Checkbox>
                        ))}
                      </Space>
                       {selectedSourceTaskIds.length > 0 && <div style={{ marginTop: 10, color: '#475569', fontSize: 12 }}>已选 {selectedSourceTaskIds.length} 个来源标注任务，将合并约 {selectedSourceCandidates.reduce((sum, item) => sum + trainingCandidateStats(item).trainable, 0).toLocaleString('zh-CN')} 张可训练图片。</div>}
                      </div>}
                  </div>}
         </div>}
          <TrainingGlobalTagConditionSelector
            dimensions={globalTagDimensions}
            conditions={sampleConditions}
            loading={globalTagDimensionsQuery.isLoading || globalTagDimensionsQuery.isFetching}
            error={globalTagDimensionsQuery.error}
            disabled={!datasetId}
            onChange={updateSampleConditions}
          />
          {trainingDistributionPanel}
         </div>}
        {step === 1 && (
          <>
            {(trainingDimension === 'label' || (trainingDimension === 'task' && taskTrainingUsesMergedEndpoint(selectedTaskIds, selectedDatasetIds))) && (
              <Form.Item
                name="training_name"
                label="训练任务名称"
                rules={[
                  { required: true, message: trainingDimension === 'label' ? '请输入标签维度训练任务名称' : '请输入任务范围训练名称' },
                  { max: 128, message: '名称不能超过 128 个字符' },
                ]}
              >
                <Input placeholder={trainingDimension === 'label' ? '例如：占道经营标签模型 · 2026-09' : '例如：道路目标多数据集训练 · 2026-10'} />
              </Form.Item>
            )}
            {trainingDimension === 'label' ? (
              <div
                role="status"
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 10,
                  border: '1px solid #bfdbfe',
                  borderRadius: 8,
                  background: '#eff6ff',
                  padding: '10px 12px',
                  marginBottom: 14,
                }}
              >
                <Tag color="blue" style={{ marginInlineEnd: 0, flex: 'none' }}>YOLO 检测</Tag>
                <div>
                  <div style={{ color: '#1e3a8a', fontWeight: 600 }}>标签维度训练固定使用 YOLO 检测适配器</div>
                  <div style={{ color: '#475569', fontSize: 12, lineHeight: 1.6, marginTop: 2 }}>
                    该模式的训练任务由目标标签和来源任务聚合生成，不显示通用算法与容器配方，避免提交不兼容的训练合同。
                  </div>
                </div>
              </div>
            ) : (
              <>
                <Form.Item name="algorithm_key" hidden>
                  <Input />
                </Form.Item>
                <Form.Item name="training_recipe_ref" hidden>
                  <Input />
                </Form.Item>
                <div
                  role="status"
                  style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: 10,
                    border: '1px solid #bfdbfe',
                    borderRadius: 8,
                    background: '#eff6ff',
                    padding: '10px 12px',
                    marginBottom: 14,
                  }}
                >
                  <Tag color="blue" style={{ marginInlineEnd: 0, flex: 'none' }}>训练方案</Tag>
                  <div>
                    <div style={{ color: '#1e3a8a', fontWeight: 600 }}>
                      YOLOv11 目标检测（系统默认）
                    </div>
                    <div style={{ color: '#475569', fontSize: 12, lineHeight: 1.6, marginTop: 2 }}>
                      系统将使用“{defaultTrainingContract.algorithmName}”适配器，并按“{defaultTrainingContract.recipeName}”配方执行训练。算法和配方已自动绑定，无需重复选择。
                    </div>
                  </div>
                </div>
              </>
            )}
            <Alert type="success" showIcon message="训练数据将由系统自动固化" description="提交后系统会根据当前任务的已完成样本自动生成不可变训练快照，保留清单、标签契约和数据血缘。" style={{ marginBottom: 14 }} />
            <Form.Item name="weights_source" label="初始权重来源" rules={[{ required: true, message: '请选择权重来源' }]}>
              <Select options={[{ value: 'auto', label: '同系列迭代（推荐）' }, { value: 'preset', label: '平台预训练权重' }, { value: 'imported', label: '外部导入模型' }, { value: 'trained', label: '指定本系统已训练模型' }]} />
            </Form.Item>
            {weightsSource === 'trained' && (
              <>
                {modelFamiliesQuery.isError && (
                  <Alert
                    type="error"
                    showIcon
                    message="已训练模型列表加载失败"
                    description="无法确认可用于迭代的模型版本，请检查权限或网络后重试。"
                    action={<Button size="small" onClick={() => void modelFamiliesQuery.refetch()}>重试</Button>}
                    style={{ marginBottom: 10 }}
                  />
                )}
                <TrainingCandidateBrowserControls
                  searchInput={modelFamilySearchInput}
                  placeholder="按模型系列、版本名称或模型键搜索"
                  candidateLabel="模型系列"
                  page={modelFamilyPage}
                  pageSize={TRAINING_MODEL_FAMILY_PAGE_SIZE}
                  total={modelFamiliesQuery.data?.total ?? 0}
                  currentCount={modelFamiliesQuery.data?.items?.length ?? 0}
                  selectedCount={selectedTrainedModelOption ? 1 : 0}
                  loading={modelFamiliesQuery.isFetching}
                  onSearchInputChange={(value) => {
                    setModelFamilySearchInput(value);
                    if (!value) {
                      setModelFamilySearch('');
                      setModelFamilyPage(0);
                    }
                  }}
                  onSearch={(value) => {
                    setModelFamilySearch(value.trim());
                    setModelFamilyPage(0);
                  }}
                  onPageChange={setModelFamilyPage}
                />
                <Form.Item name="weights_ref_id" label="选择本系统已训练模型" rules={[{ required: true, message: '请选择用于迭代的模型版本' }]}>
                  <Select
                    options={trainedModelOptions}
                    loading={modelFamiliesQuery.isLoading || modelFamiliesQuery.isFetching}
                    placeholder="选择模型系列和版本"
                    notFoundContent={modelFamiliesQuery.isLoading ? <Spin size="small" /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={modelFamilySearch ? '没有匹配的已训练模型' : '暂无可训练迭代的模型版本'} />}
                    onChange={(value) => {
                      const option = trainedModelOptions.find((item) => item.value === Number(value));
                      setSelectedTrainedModelOption(option || null);
                      form.setFieldValue('model_weights', `trained:${Number(value)}`);
                    }}
                    popupMatchSelectWidth={false}
                  />
                </Form.Item>
                <div style={{ color: '#64748b', fontSize: 12, lineHeight: 1.6, marginTop: -8, marginBottom: 12 }}>
                  列出当前账号可访问的同一训练配方模型系列，来源任务会显示在版本名称后；按任务训练会禁用标签契约不匹配或缺失的版本，避免提交后才发现类别映射无法确认。
                </div>
                {selectedTrainedModelOption && (
                  <div role="status" style={{ border: '1px solid #dbeafe', borderRadius: 8, background: '#f8fbff', padding: '8px 10px', color: '#475569', fontSize: 12, lineHeight: 1.65, marginBottom: 14 }}>
                    <strong style={{ color: '#0f172a' }}>已选模型：{selectedTrainedModelOption.label}</strong>
                    <span> · 训练作业 #{selectedTrainedModelOption.value}</span>
                    {selectedTrainedModelOption.family.task_id ? <span> · 来源标注任务 #{selectedTrainedModelOption.family.task_id}</span> : null}
                    <span> · 权重文件 {trainingModelVersionWeightFile(selectedTrainedModelOption.version)}</span>
                    {selectedTrainedModelOption.version.dataset_version_no ? <span> · 数据快照 v{selectedTrainedModelOption.version.dataset_version_no}</span> : null}
                    {selectedTrainedModelOption.version.training_recipe_key ? <span> · 配方 {selectedTrainedModelOption.version.training_recipe_key}{selectedTrainedModelOption.version.training_recipe_version ? ` v${selectedTrainedModelOption.version.training_recipe_version}` : ''}</span> : null}
                    {selectedTrainedModelOption.version.status ? <span> · 状态 {selectedTrainedModelOption.version.status}</span> : null}
                    <div>{selectedTrainedModelOption.version.display_name || selectedTrainedModelOption.label}</div>
                  </div>
                )}
              </>
            )}
            {weightsSource === 'imported' && (
              <>
                <Space wrap style={{ width: '100%', justifyContent: 'space-between', marginBottom: 8 }}>
                  <ServerSearchInput
                    value={importedModelSearchInput}
                    onChange={(value) => {
                      setImportedModelSearchInput(value);
                      if (!value.trim()) {
                        setImportedModelSearch('');
                        setImportedModelPage(0);
                      }
                    }}
                    onSearch={(value) => {
                      setImportedModelSearch(value.trim());
                      setImportedModelPage(0);
                    }}
                    placeholder="按模型名称、文件名或分类搜索"
                    ariaLabel="搜索外部导入模型"
                    loading={importedModelsQuery.isFetching}
                    style={{ width: 360, maxWidth: '100%' }}
                  />
                  <Typography.Text type="secondary">
                    {importedModelSearch ? `搜索结果 ${importedModelTotal} 个` : `共 ${importedModelTotal} 个模型`}
                  </Typography.Text>
                </Space>
                <Form.Item name="weights_ref_id" label="选择外部导入模型" rules={[{ required: true, message: '请选择外部导入模型' }]}>
                  <Select
                    showSearch
                    optionFilterProp="searchText"
                    options={importedModelOptions}
                    loading={importedModelsQuery.isLoading || importedModelsQuery.isFetching}
                    placeholder="选择外部导入模型"
                    notFoundContent={importedModelsQuery.isLoading ? <Spin size="small" /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={importedModelSearch ? '没有匹配的外部模型' : '暂无外部导入模型'} />}
                    onChange={(value) => form.setFieldValue('model_weights', `imported:${Number(value)}`)}
                    popupMatchSelectWidth={false}
                  />
                </Form.Item>
                {importedModelTotal > TRAINING_IMPORTED_MODEL_PAGE_SIZE && (
                  <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: -8, marginBottom: 12 }}>
                    <Pagination
                      size="small"
                      current={importedModelPage + 1}
                      pageSize={TRAINING_IMPORTED_MODEL_PAGE_SIZE}
                      total={importedModelTotal}
                      showSizeChanger={false}
                      onChange={(nextPage) => setImportedModelPage(nextPage - 1)}
                    />
                  </div>
                )}
              </>
            )}
            {weightsSource === 'preset' && (
              <Form.Item name="model_weights" label="初始权重名称" rules={[{ required: true, message: '请输入权重名称' }, { max: 256, message: '权重名称不能超过 256 个字符' }]}>
                <Input placeholder="例如：yolo11n.pt" />
              </Form.Item>
            )}
            {(weightsSource === 'auto' || weightsSource === 'trained') && (
              <div style={{ marginBottom: 14 }}>
                {iterationPreviewQuery.isLoading ? (
                  <Alert type="info" showIcon message="正在确认本次迭代模型和 AI 参数" />
                ) : iterationPreviewQuery.error ? (
                  <Alert type="warning" showIcon message="迭代预检暂时失败" description="提交前请重试，确保页面显示的父模型与实际训练一致。" action={<Button size="small" onClick={() => void iterationPreviewQuery.refetch()}>重试</Button>} />
                ) : iterationPreviewQuery.data?.parent ? (
                  <div role="status" style={{ border: '1px solid #bfdbfe', borderRadius: 8, background: '#f8fbff', padding: '10px 12px' }}>
                    <div style={{ color: '#0f172a', fontWeight: 650 }}>
                      {weightsSource === 'auto' ? '将继承' : '本次迭代模型'}：{trainingIterationParentLabel(iterationPreviewQuery.data.parent)}
                    </div>
                    <div style={{ color: '#475569', fontSize: 12, lineHeight: 1.6, marginTop: 4 }}>
                      选择规则：{trainingIterationPolicyLabel(iterationPreviewQuery.data)}
                    </div>
                    <Descriptions bordered size="small" column={{ xs: 1, sm: 2, md: 3 }} style={{ marginTop: 8 }}>
                      <Descriptions.Item label="来源任务">{iterationPreviewQuery.data.parent.task_name || `任务 #${iterationPreviewQuery.data.parent.task_id}`}（#{iterationPreviewQuery.data.parent.task_id}）</Descriptions.Item>
                      <Descriptions.Item label="逻辑数据集">{iterationPreviewQuery.data.parent.dataset_name ? `${iterationPreviewQuery.data.parent.dataset_name}（#${iterationPreviewQuery.data.parent.dataset_id ?? '未记录'}）` : iterationPreviewQuery.data.parent.dataset_id ? `#${iterationPreviewQuery.data.parent.dataset_id}` : '未记录'}</Descriptions.Item>
                      <Descriptions.Item label="训练作业">#{iterationPreviewQuery.data.parent.job_id}</Descriptions.Item>
                      <Descriptions.Item label="模型版本">{iterationPreviewQuery.data.parent.model_version_id ? `#${iterationPreviewQuery.data.parent.model_version_id}` : '未关联版本'}{trainingModelVersionLabel(iterationPreviewQuery.data.parent) !== '版本未记录' ? ` · ${trainingModelVersionLabel(iterationPreviewQuery.data.parent)}` : ''}</Descriptions.Item>
                      <Descriptions.Item label="模型系列与分支">{trainingIterationParentLineageLabel(iterationPreviewQuery.data.parent)}</Descriptions.Item>
                      <Descriptions.Item label="创建时间">{trainingIterationDate(iterationPreviewQuery.data.parent.created_at)}</Descriptions.Item>
                      <Descriptions.Item label="数据快照">{iterationPreviewQuery.data.parent.dataset_version_no ? `v${iterationPreviewQuery.data.parent.dataset_version_no}` : '未记录'}</Descriptions.Item>
                      <Descriptions.Item label="数据内容指纹"><span style={{ fontFamily: 'monospace', wordBreak: 'break-all' }}>{iterationPreviewQuery.data.parent.dataset_content_signature || '未记录'}</span></Descriptions.Item>
                      <Descriptions.Item label="权重文件">{iterationPreviewQuery.data.parent.weights_filename || 'best.pt'}</Descriptions.Item>
                      <Descriptions.Item label="训练配方">{iterationPreviewQuery.data.parent.training_recipe_key ? `${iterationPreviewQuery.data.parent.training_recipe_key}${iterationPreviewQuery.data.parent.training_recipe_version ? ` · v${iterationPreviewQuery.data.parent.training_recipe_version}` : ''}` : '当前配方已校验'}</Descriptions.Item>
                      <Descriptions.Item label="模型状态">{iterationPreviewQuery.data.parent.model_status || iterationPreviewQuery.data.parent.job_status || '已完成'}</Descriptions.Item>
                    </Descriptions>
                    <div style={{ color: '#64748b', fontSize: 12, lineHeight: 1.6, marginTop: 6 }}>
                      {iterationPreviewQuery.data.compatible_candidate_count ?? iterationPreviewQuery.data.candidate_count
                        ? `已在 ${iterationPreviewQuery.data.compatible_candidate_count ?? iterationPreviewQuery.data.candidate_count} 个兼容候选中完成选择。`
                        : '标签集合、标签顺序和训练配方已由服务端校验。'}
                      {iterationPreviewQuery.data.selection_scope ? ` 检索范围：${trainingIterationScopeLabel(iterationPreviewQuery.data.selection_scope)}` : ''}
                    </div>
                  </div>
                ) : weightsSource === 'auto' ? (
                  <Alert
                    type="info"
                    showIcon
                    message="没有发现兼容的同系列模型，将回退平台预训练权重"
                    description={`${trainingIterationFallbackLabel(iterationPreviewQuery.data?.fallback_reason)}；本次将使用 yolo11n.pt，不会跨类别继承。`}
                  />
                ) : null}
              </div>
            )}
            {(weightsSource === 'auto' || weightsSource === 'trained') && iterationPreviewQuery.data?.parent && !iterationPreviewQuery.error && (
              appliedAiReportId ? (
                <div style={{ border: '1px solid #bbf7d0', borderRadius: 8, background: '#f0fdf4', padding: 12, marginBottom: 14 }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
                    <strong style={{ color: '#166534' }}>已应用 AI 报告 #{appliedAiReportId} 的下一轮参数</strong>
                    <Button size="small" onClick={() => applyAiPreview(iterationPreviewQuery.data!, true)}>重新应用 AI 参数</Button>
                  </div>
                  <div style={{ color: '#475569', fontSize: 12, marginBottom: 10 }}>
                    训练轮数和输入尺寸已填入上方表单，其余参数会随训练作业真实提交；你仍可在提交前修改轮数和尺寸。
                  </div>
                  <Descriptions bordered size="small" column={{ xs: 1, sm: 2, md: 3 }}>
                    {Object.entries({
                      ...partitionAiIterationParams(iterationPreviewQuery.data.suggested_params).topLevel,
                      ...aiIterationParams,
                    }).map(([key, value]) => (
                      <Descriptions.Item key={key} label={key}>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</Descriptions.Item>
                    ))}
                  </Descriptions>
                </div>
              ) : (
                <Alert type="warning" showIcon message="该模型暂无 AI 下轮参数" description="将使用当前表单参数；可先在训练作业的 AI 报告中生成下一轮建议。" style={{ marginBottom: 14 }} />
              )
            )}
            <Space size={16} style={{ display: 'flex' }}>
              <Form.Item name="epochs" label="训练轮数（epochs）" rules={[{ required: true, message: '请输入训练轮数' }, { type: 'number', min: 1, max: 3000, message: '训练轮数范围为 1-3000' }]} style={{ flex: 1 }}><InputNumber min={1} max={3000} precision={0} style={{ width: '100%' }} /></Form.Item>
              <Form.Item name="imgsz" label="输入尺寸（imgsz）" rules={[{ required: true, message: '请输入输入尺寸' }, { type: 'number', min: 320, max: 1280, message: '输入尺寸范围为 320-1280' }]} style={{ flex: 1 }}><InputNumber min={320} max={1280} step={32} precision={0} style={{ width: '100%' }} /></Form.Item>
            </Space>
            <Form.Item name="execution_mode" label="执行资源" rules={[{ required: true }]}>
              <Select options={[{ value: 'local', label: '本机 GPU 队列' }, { value: 'remote', label: '远程训练节点' }, { value: 'container', label: '标准容器 Worker' }]} />
            </Form.Item>
            <Form.Item noStyle shouldUpdate={(previous, current) => previous.execution_mode !== current.execution_mode}>
              {({ getFieldValue }) => getFieldValue('execution_mode') === 'remote' ? <Form.Item name="train_node_id" label="远程节点 ID" rules={[{ required: true, message: '远程训练必须填写节点 ID' }, { max: 64 }]}><Input placeholder="例如：gpu-node-01" /></Form.Item> : null}
            </Form.Item>
            <Alert type="warning" showIcon message="训练开始后，候选样本范围会被冻结" description="暂停后可从 last.pt 继续；远程节点必须已在平台注册。" />
          </>
        )}
        {step === 2 && (selectedCandidate || selectedTaskIds.length > 0 || selectedSourceTaskIds.length > 0) && (
          <div>
            <Alert
              type="success"
              showIcon
              message="请确认配置后开始训练"
               description={trainingDimension === 'label'
                 ? '本次将按目标标签跨来源任务合并合格样本，并生成独立的标签维度训练任务。'
                 : taskTrainingUsesMergedEndpoint(selectedTaskIds, selectedDatasetIds)
                   ? '本次将按相同标签契约合并所选任务和数据集范围内的合格样本，并生成一份不可变训练快照。'
                   : '本次只使用所选任务当前已完成且质量合格的图片，并自动生成不可变训练快照。'}
              style={{ marginBottom: 14 }}
            />
            <Descriptions bordered size="small" column={1}>
              <Descriptions.Item label="训练维度">{trainingDimensionLabel(trainingDimension)}</Descriptions.Item>
              <Descriptions.Item label="训练方案">YOLOv11 目标检测（系统默认）</Descriptions.Item>
              <Descriptions.Item label="逻辑数据集" span={2}>
                {selectedDatasets.length
                  ? <Space wrap size={[4, 4]}>{selectedDatasets.map((item) => <Tag key={item.id}>{trainingDatasetDisplayLabel(item)}</Tag>)}</Space>
                  : selectedDataset?.name || (datasetId ? `数据集 #${datasetId}` : '未指定')}
              </Descriptions.Item>
              <Descriptions.Item label="全局工具标签筛选">
                {trainingSampleConditionsLabel(sampleConditions, globalTagDimensions)}
              </Descriptions.Item>
              {trainingDimension === 'label' ? (
                <>
                  <Descriptions.Item label="目标标签">{selectedTrainingLabels.map((label) => label.label_value || label.label_key).join('、') || selectedLabelIds.map((id) => `标签 #${id}`).join('、')}</Descriptions.Item>
                  <Descriptions.Item label="来源任务">{selectedSourceCandidates.map((candidate) => candidate.task_name || `任务 #${candidate.task_id}`).join('、')}</Descriptions.Item>
                  <Descriptions.Item label="本次实际纳入训练">{selectedSourceCandidates.reduce((sum, item) => sum + trainingCandidateStats(item).trainable, 0).toLocaleString('zh-CN')} 张</Descriptions.Item>
                </>
              ) : (
                <>
                  <Descriptions.Item label="训练任务">{selectedTaskCandidates.map((candidate) => `${candidate.task_name || `任务 #${candidate.task_id}`}（#${candidate.task_id}）`).join('、')}</Descriptions.Item>
                  {selectedTaskCandidates.length > 0 && (() => {
                    const stats = selectedTaskCandidates.reduce((total, candidate) => {
                      const current = trainingCandidateStats(candidate);
                      return {
                        total: total.total + current.total,
                        completed: total.completed + current.completed,
                        trainable: total.trainable + current.trainable,
                        pending: total.pending + current.pending,
                        excluded: total.excluded + current.excluded,
                      };
                    }, { total: 0, completed: 0, trainable: 0, pending: 0, excluded: 0 });
                    return <>
                      <Descriptions.Item label="总图片">{stats.total.toLocaleString('zh-CN')} 张</Descriptions.Item>
                      <Descriptions.Item label="已完成样本">{stats.completed.toLocaleString('zh-CN')} 张</Descriptions.Item>
                      <Descriptions.Item label="本次实际纳入训练">{stats.trainable.toLocaleString('zh-CN')} 张</Descriptions.Item>
                      <Descriptions.Item label="待处理 / 排除">{stats.pending.toLocaleString('zh-CN')} / {stats.excluded.toLocaleString('zh-CN')} 张</Descriptions.Item>
                    </>;
                  })()}
                </>
              )}
              <Descriptions.Item label="训练数据">系统只冻结本次实际纳入的合格图片；后续新增标注不会自动混入</Descriptions.Item>
              <Descriptions.Item label="初始权重">
                {weightsSource === 'auto' || weightsSource === 'trained'
                  ? trainingIterationParentLabel(iterationPreviewQuery.data?.parent)
                  : weightsSource === 'imported'
                    ? (importedModelOptions.find((option) => option.value === Number(weightsRefId))?.label || '请选择外部导入模型')
                    : (form.getFieldValue('model_weights') || 'yolo11n.pt')}
              </Descriptions.Item>
              {(weightsSource === 'auto' || weightsSource === 'trained') && iterationPreviewQuery.data && (
                <>
                  <Descriptions.Item label="父模型来源">
                    {iterationPreviewQuery.data.parent
                      ? `${iterationPreviewQuery.data.parent.task_name || `任务 #${iterationPreviewQuery.data.parent.task_id}`} · 作业 #${iterationPreviewQuery.data.parent.job_id} · ${iterationPreviewQuery.data.parent.weights_filename || 'best.pt'}`
                      : `回退 yolo11n.pt：${trainingIterationFallbackLabel(iterationPreviewQuery.data.fallback_reason)}`}
                  </Descriptions.Item>
                  {iterationPreviewQuery.data.parent && (
                    <Descriptions.Item label="父模型系列与分支">
                      {trainingIterationParentLineageLabel(iterationPreviewQuery.data.parent)}
                    </Descriptions.Item>
                  )}
                  <Descriptions.Item label="选择规则">{trainingIterationPolicyLabel(iterationPreviewQuery.data)}</Descriptions.Item>
                </>
              )}
              {(weightsSource === 'auto' || weightsSource === 'trained') && iterationPreviewQuery.data?.parent && (
                <Descriptions.Item label="AI 迭代参数">
                  {appliedAiReportId
                    ? `已应用 AI 报告 #${appliedAiReportId}，共 ${Object.keys(aiIterationParams).length + Object.keys(partitionAiIterationParams(iterationPreviewQuery.data.suggested_params).topLevel).length} 项参数`
                    : '该模型暂无可用建议，使用当前表单参数'}
                </Descriptions.Item>
              )}
              <Descriptions.Item label="训练轮数">{form.getFieldValue('epochs') || 50}</Descriptions.Item>
              <Descriptions.Item label="输入尺寸">{form.getFieldValue('imgsz') || 640}</Descriptions.Item>
              <Descriptions.Item label="执行资源">{form.getFieldValue('execution_mode') === 'remote' ? `远程节点 ${form.getFieldValue('train_node_id') || '-'}` : '本机 GPU 队列'}</Descriptions.Item>
            </Descriptions>
          </div>
        )}
       </Form>
       {datasetBrowserModal}
    </Modal>
  </>;
}

export default function TrainingPage() {
  const queryClient = useQueryClient();
  const { isTaskSuperuser, isFetched: isTaskSuperuserFetched = true } = useTaskSuperuser();
  const screens = Grid.useBreakpoint();
  const compactActions = !screens.sm;
  const [activeView, setActiveView] = useState<TrainingView>(trainingViewFromLocation);
  const [actionError, setActionError] = useState<string | null>(null);
  const [jobSearch, setJobSearch] = useState('');
  const action = useMutation({
    mutationFn: async ({ row, kind }: { row: Row; kind: 'pause' | 'restart' }) => {
      const path = `/v1/training/jobs/${row.id}/${kind}`;
      return (await request.post(path)).data;
    },
    onSuccess: () => { setActionError(null); message.success('训练作业状态已更新'); void queryClient.invalidateQueries({ queryKey: ['training-jobs'] }); },
    onError: (error: any) => { const status = error?.response?.status; const text = status === 403 ? '当前账号无权操作该训练作业' : status === 400 ? '当前作业状态不支持该操作' : '训练作业操作失败，请刷新后重试'; setActionError(text); message.error(text); },
  });
  const trainingDetailActions = (row: Row) => {
    const completed = String(row.status || '').toUpperCase() === 'COMPLETED';
    const compileNotice = (() => {
      if (!completed) {
        return <Alert type="info" showIcon message="训练完成后自动进入 BM1688 编译和 AI 报告分析" description="训练作业完成并生成 best.pt 后，系统会自动创建编译任务，并调用已配置的视觉模型生成训练分析与迭代建议。" />;
      }
      const compileStatus = String(row.bm1688_compile_status || 'not_started');
      const compileLabel = String(row.bm1688_compile_status_label || '未编译');
      if (compileStatus === 'completed') {
        return <Alert type="success" showIcon message="BM1688 编译已完成" description="已生成 BM1688 边端模型，可在模型中心查看、验证和决定是否发布。" />;
      }
      if (compileStatus === 'failed') {
        return <Alert type="error" showIcon message="BM1688 编译失败" description={<><div>{row.bm1688_compile_error || '编译任务执行失败，请到模型中心查看编译日志。'}</div><div>训练得到的 best.pt 仍然保留，修复编译环境后可重新编译，不需要重新训练。</div></>} />;
      }
      if (compileStatus === 'compiling') {
        return <Alert type="info" showIcon message="BM1688 正在自动编译" description="系统正在自动导出 ONNX 并生成 FP32、FP16、INT8 三种 BModel，请稍候。" />;
      }
      if (compileStatus === 'queued') {
        return <Alert type="info" showIcon message="BM1688 编译已自动排队" description="系统已接收 best.pt，编译任务将在可用的 BM1688 编译环境中自动执行。" />;
      }
      return (
        <Alert
          type="warning"
          showIcon
          message={`best.pt 已生成，BM1688 状态：${compileLabel}`}
          description="系统会自动尝试创建编译任务。当前尚未检测到可用的 BM1688 编译环境，配置完成后会由后台自动补偿，不需要重新训练。"
        />
      );
    })();
    return <div style={{ display: 'grid', gap: 14 }}><div><TrainingAiReportAction row={row} /></div>{compileNotice}</div>;
  };
  const jobsPanel = <ResourcePage module="training" queryKey="training-jobs" needsProject={false} queryFn={async (_scopeId, page = 0, size = 20, query) => await request.get('/v1/training/jobs', { params: { page, size, q: query || undefined } })} pagination pageSize={20} search={jobSearch} onSearchChange={setJobSearch} searchPlaceholder="按作业、任务、数据集、权重或节点搜索" searchAriaLabel="搜索训练作业" actionColumnWidth={compactActions ? 72 : undefined} detailColumnWidth={compactActions ? 60 : undefined} tableLayout={compactActions ? 'fixed' : undefined} scrollX={compactActions ? '100%' : undefined} beforeTable={<><CreateTrainingAction />{actionError && <Alert type="error" showIcon message={actionError} style={{ marginBottom: 14 }} />}</>} staleTime={15_000} pollInterval={10_000} activeStatuses={ACTIVE_TRAINING_STATUSES} detailPath={(row) => row.id ? `/v1/training/jobs/${row.id}` : null} detailActions={trainingDetailActions} rowActions={(row) => {
    const status = String(row.status || '').toUpperCase();
    const busy = action.isPending;
    if (status === 'COMPLETED') return <TrainingAiReportAction row={row} compact />;
    if (status === 'PENDING' || status === 'PROCESSING') {
      return <Popconfirm title="暂停后训练进程会在当前边界停止" okText="确认暂停" cancelText="保留" onConfirm={() => action.mutate({ row, kind: 'pause' })}><Button type="link" size="small" aria-label="暂停" title="暂停" icon={<PauseCircleOutlined />} disabled={busy}>{compactActions ? null : '暂停'}</Button></Popconfirm>;
    }
    if (status === 'FAILED') {
      return <Button type="link" size="small" aria-label="重试训练" title="重试训练" icon={<PlayCircleOutlined />} disabled={busy} onClick={() => action.mutate({ row, kind: 'restart' })}>{compactActions ? null : '重试训练'}</Button>;
    }
    if (status === 'CANCELLED' || Boolean(row.is_restartable)) {
      return <Button type="link" size="small" aria-label="继续训练" title="继续训练" icon={<PlayCircleOutlined />} disabled={busy} onClick={() => action.mutate({ row, kind: 'restart' })}>{compactActions ? null : '继续训练'}</Button>;
    }
    return <span aria-label="无可用操作">—</span>;
  }} columns={[{ title: '作业', dataIndex: 'name', key: 'name', width: compactActions ? 70 : undefined, ellipsis: true }, { title: '任务', dataIndex: 'task_name', key: 'task_name', ellipsis: true, responsive: ['md'] }, { title: '算法', key: 'algorithm_key', ellipsis: true, responsive: ['md'], render: (_, row) => row.algorithm_key || row.adapter_key || '-' }, { title: '执行资源', key: 'runtime', responsive: ['lg'], render: (_, row) => row.execution_mode === 'remote' ? `远程 ${row.train_node_id || '-'}` : row.execution_mode === 'container' ? '标准容器 Worker' : '本机 GPU' }, { title: '训练进度', key: 'training_progress', width: compactActions ? 96 : 170, responsive: ['md'], render: (_, row) => { const progress = trainingProgressSummary(row); return <Space direction="vertical" size={2} style={{ width: '100%' }}><span style={{ fontSize: 12, color: '#334155', whiteSpace: 'nowrap' }}>{progress.label}</span>{progress.percent !== null ? <Progress percent={progress.percent} size="small" showInfo={false} status={progress.percent >= 100 ? 'success' : 'active'} /> : <span style={{ color: '#94a3b8', fontSize: 11 }}>等待训练结果</span>}</Space>; } }, { title: '状态', dataIndex: 'status', key: 'status', width: compactActions ? 60 : undefined, render: (value) => { const code = String(value || '').toUpperCase(); const mobileLabel: Record<string, string> = { COMPLETED: '完成', PROCESSING: '训练中', RUNNING: '训练中', FAILED: '失败', CANCELLED: '已取消', PENDING: '排队' }; return <Tag color={code === 'COMPLETED' ? 'green' : code === 'FAILED' ? 'red' : code === 'CANCELLED' ? 'gold' : 'blue'} style={compactActions ? { marginInlineEnd: 0 } : undefined}>{compactActions ? mobileLabel[code] || code : trainingStatusLabel(value)}</Tag>; } }, { title: '运行态', dataIndex: 'runtime_status', key: 'runtime_status', responsive: ['sm'], render: (value) => <Tag>{trainingRuntimeStatusLabel(value)}</Tag> }]} />;
  useEffect(() => {
    if (activeView !== 'auto-plan' || !isTaskSuperuserFetched || isTaskSuperuser) return;
    setActiveView('jobs');
    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search);
      params.delete('view');
      const query = params.toString();
      window.history.replaceState(window.history.state, '', `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`);
    }
  }, [activeView, isTaskSuperuser, isTaskSuperuserFetched]);
  const handleViewChange = (key: string) => {
    const requested: TrainingView = key === 'auto-plan' || key === 'reports' ? key : 'jobs';
    const next: TrainingView = requested === 'auto-plan' && isTaskSuperuserFetched && !isTaskSuperuser ? 'jobs' : requested;
    setActiveView(next);
    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search);
      if (next === 'jobs') params.delete('view');
      else params.set('view', next);
      const query = params.toString();
      window.history.replaceState(window.history.state, '', `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`);
    }
  };
  return <>
    <Tabs
      activeKey={activeView === 'auto-plan' && isTaskSuperuserFetched && !isTaskSuperuser ? 'jobs' : activeView}
      onChange={handleViewChange}
      destroyInactiveTabPane
      items={[
        { key: 'jobs', label: '训练作业', children: jobsPanel },
        ...(isTaskSuperuser || !isTaskSuperuserFetched
          ? [{ key: 'auto-plan', label: '历史自动训练计划', children: <LegacyTrainingPanel view="auto-plan" /> }]
          : []),
        { key: 'reports', label: '训练报告', children: <LegacyTrainingPanel view="reports" /> },
      ]}
    />
  </>;
}
