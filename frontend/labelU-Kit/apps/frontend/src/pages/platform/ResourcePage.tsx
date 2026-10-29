import { DownloadOutlined, EyeOutlined, FileImageOutlined, QuestionCircleOutlined, ReloadOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Collapse, Descriptions, Empty, Image, Modal, Popover, Progress, Space, Spin, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import React, { useEffect, useRef, useState } from 'react';

import request from '@/api/request';
import ServerSearchInput from '@/components/ServerSearchInput';
import { getYoloTrainAnalysisAsset, type YoloTrainAnalysisPreviewImage } from '@/api/services/trainingAnalysis';
import { MODULE_COPY, type PlatformModule } from './modules';
import { ACTIVE_TRAINING_STATUSES, usePageVisible } from './polling';
import { usePlatformProject } from './project';
import { isWallboardPollingAllowed, projectQueryKey } from './routing';
import { getTrainingFailureGuidance, listData, listTotal, moduleDataState, ModuleHeader, ModuleStatus, responseStatus, trainingRuntimeStatusLabel, trainingStatusLabel, type Row, TablePanel } from './shared';

const DETAIL_LABELS: Record<string, string> = {
  id: '编号',
  name: '名称',
  display_name: '显示名称',
  algorithm_key: '算法标识',
  task_name: '标注任务',
  task_type: '任务类型',
  training_adapter: '训练适配器',
  status: '状态',
  version_no: '版本',
  chip_type: '芯片',
  precision: '精度',
  release_key: '发布标识',
  target_device_ids: '目标设备',
  gray_ratio: '灰度比例',
  source_type: '来源类型',
  source_connection_id: '来源连接',
  total_count: '总数量',
  accepted_count: '已接收',
  quarantined_count: '已隔离',
  failed_count: '失败数量',
  manifest_sha256: '清单 SHA256',
  provider_key: 'Provider 标识',
  provider_kind: 'Provider 类型',
  runtime_adapter: '运行时适配器',
  production_ready: '生产就绪',
  runtime_registered: '运行时已注册',
  runtime_healthy: '运行时健康',
  runtime_ready: '运行时就绪',
  hil_verified: 'HIL 已验证',
  image_digest: '镜像摘要',
  toolchain_version: '工具链版本',
  runtime_last_error: '运行时错误',
  quality_status: '质量状态',
  quality_gate_status: '质量门禁',
  training_assessment: '训练质量评估',
  edge_validation_assessment: '边端验证评估',
  release_gate_assessment: '发布门禁评估',
  quality_assessment: '质量门禁摘要',
  model_set_id: '模型集',
  export_id: '导出制品',
  source_training_job_id: '来源训练作业',
  task_id: '任务编号',
  dataset_version_id: '数据集版本',
  dataset_manifest: '数据集清单',
  metrics_summary: '评估指标',
  error_message: '失败原因',
  failure_reason: '失败原因',
  metrics: '评估指标',
  runtime_status: '运行态',
  execution_mode: '执行模式',
  train_node_id: '训练节点',
  model_weights: '初始权重',
  epochs: '训练轮数',
  imgsz: '输入尺寸',
  train_sample_count: '训练样本数',
  val_sample_count: '验证样本数',
  current_epoch: '当前轮次',
  params: '训练参数',
  hyperparams: '训练参数',
  available: '可用性',
  availability_reason: '可用性说明',
  workflow_type: '工作流类型',
  current_step: '当前步骤',
  attempts: '尝试次数',
  callback_url: '回调地址',
};

function resourceErrorCopy(status: number | undefined, online: boolean) {
  if (!online) return { message: '当前网络已断开', description: '恢复网络后重试，页面不会提交任何操作。' };
  if (status === 403) return { message: '数据加载失败：当前账号无权访问', description: '请联系管理人员确认数据权限。' };
  if (status === 404) return { message: '数据加载失败：请求的数据不存在', description: '该资源可能已删除或当前账号无权访问。' };
  if (status === 409) return { message: '数据加载失败：数据版本冲突', description: '数据已变化，请刷新后重试。' };
  return { message: '数据加载失败', description: '请稍后重试。' };
}

const QUALITY_LAYER_LABELS = [
  ['training', '训练质量'],
] as const;

const QUALITY_STATUS_LABELS: Record<string, string> = {
  PASS: '通过', PASSED: '通过', COMPLETED: '完成', SUCCEEDED: '完成',
  PENDING: '待验证', BLOCKED: '已阻断', FAILED: '失败',
  REVIEW_REQUIRED: '待复核', CONDITIONAL_PASSED: '条件达标',
  PUBLISHABLE: '可发布', UNPUBLISHABLE: '不可发布',
  AUTO_QUALITY_REACHED: '训练指标达标', NOT_STARTED: '未开始',
  UNUSABLE: '不可用', USABLE_NEEDS_REVIEW: '效果待确认',
};

function qualityLayerValue(item: unknown) {
  if (!item) return { status: 'PENDING', reason: '', summary: '', score: null as number | null, reasons: [] as string[] };
  if (typeof item === 'string') return { status: item, reason: '', summary: '', score: null as number | null, reasons: [] as string[] };
  if (typeof item !== 'object') return { status: String(item), reason: '', summary: '', score: null as number | null, reasons: [] as string[] };
  const value = item as Record<string, unknown>;
  const reasons = Array.isArray(value.reasons)
    ? value.reasons.map((reason) => String(reason || '')).filter(Boolean)
    : Array.isArray((value.evidence as Record<string, unknown> | undefined)?.reason_details)
    ? ((value.evidence as Record<string, unknown>).reason_details as unknown[]).map((reason) => String(reason || '')).filter(Boolean)
    : [];
  const rawScore = value.score ?? (value.evidence as Record<string, unknown> | undefined)?.score;
  const score = rawScore === null || rawScore === undefined || rawScore === '' ? null : Number(rawScore);
  return {
    status: String(value.status_code || value.status || value.status_label || 'PENDING'),
    reason: String(value.reason_summary || value.summary || value.detail || ''),
    summary: String(value.summary || ''),
    score: Number.isFinite(score) ? score : null,
    reasons,
  };
}

function trainingQualityLayers(row?: Row | null) {
  const quality = row?.quality_assessment as Record<string, unknown> | undefined;
  // quality_assessment is the unified gate result and carries the numeric score;
  // training_assessment remains a compatibility field for older jobs.
  const training = quality?.training_quality || row?.training_assessment || row?.training_quality_status;
  const generalization = quality?.generalization_quality || row?.generalization_quality_status;
  const edge = row?.edge_validation_assessment || quality?.edge_usability || row?.edge_usability_status;
  const release = row?.release_gate_assessment || quality?.release_readiness || row?.release_readiness || row?.release_gate_status;
  return { training, generalization, edge, release };
}

function trainingMetricsSummary(value: unknown) {
  let metrics: Record<string, unknown> | null = null;
  if (value && typeof value === 'object' && !Array.isArray(value)) metrics = value as Record<string, unknown>;
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) metrics = parsed as Record<string, unknown>;
    } catch {
      return value;
    }
  }
  if (!metrics) return String(value ?? '');
  const overall = metrics.overall_metrics && typeof metrics.overall_metrics === 'object'
    ? metrics.overall_metrics as Record<string, unknown>
    : metrics;
  const labels: Array<[string, string]> = [
    ['overall_recall', '整体召回率'],
    ['map50', 'mAP50'],
    ['map50_95', 'mAP50-95'],
    ['precision', '精确率'],
    ['recall', '召回率'],
    ['fitness', 'Fitness'],
  ];
  const parts = labels
    .filter(([key]) => overall[key] !== undefined && overall[key] !== null)
    .map(([key, label]) => `${label} ${Number.isFinite(Number(overall[key])) ? Number(overall[key]).toFixed(4) : String(overall[key])}`);
  if (parts.length) return parts.join(' · ');
  const source = metrics.authoritative_source || metrics.metrics_source;
  return source ? `已记录指标（来源：${String(source)}）` : `已记录 ${Object.keys(metrics).length} 项指标`;
}

function TrainingQualityLayers({ row }: { row?: Row | null }) {
  const values = trainingQualityLayers(row);
  const item = qualityLayerValue(values.training);
  const assessment = values.training && typeof values.training === 'object'
    ? values.training as Record<string, unknown>
    : {};
  const evidence = assessment.evidence && typeof assessment.evidence === 'object'
    ? assessment.evidence as Record<string, unknown>
    : {};
  const rawMetrics = row?.training_val_metrics || evidence.training_val_metrics || row?.metrics || row?.metrics_summary;
  const parsedMetrics = trainingJson(rawMetrics);
  const metrics = parsedMetrics && typeof parsedMetrics === 'object' && !Array.isArray(parsedMetrics)
    ? parsedMetrics as Record<string, unknown>
    : {};
  const overall = metrics.overall_metrics && typeof metrics.overall_metrics === 'object'
    ? metrics.overall_metrics as Record<string, unknown>
    : metrics.best_pt_val_metrics && typeof metrics.best_pt_val_metrics === 'object'
    ? metrics.best_pt_val_metrics as Record<string, unknown>
    : metrics.final_metrics && typeof metrics.final_metrics === 'object'
    ? metrics.final_metrics as Record<string, unknown>
    : metrics;
  const score = item.score ?? (overall.map50_95 !== undefined ? Number(overall.map50_95) : overall.map50 !== undefined ? Number(overall.map50) : overall.overall_recall !== undefined ? Number(overall.overall_recall) : null);
  const metricItems: Array<[string, string, boolean]> = [
    ['map50_95', 'mAP50-95', true],
    ['map50', 'mAP50', true],
    ['precision', '精确率', true],
    ['recall', '召回率', true],
    ['overall_recall', '整体召回率', true],
    ['total_sample_count', '样本总数', false],
  ];
  const displayedMetrics = metricItems
    .map(([key, label, ratio]) => {
      const source = key === 'total_sample_count' ? row?.total_sample_count ?? evidence.total_sample_count : overall[key];
      if (source === undefined || source === null || source === '') return null;
      const number = Number(source);
      if (!Number.isFinite(number)) return null;
      return [label, ratio ? `${number.toFixed(4)}（${(number * 100).toFixed(1)}%）` : String(Math.round(number))] as [string, string];
    })
    .filter(Boolean) as Array<[string, string]>;
  const reasons = item.reasons.length ? item.reasons : (item.reason ? [item.reason] : []);
  return (
    <div data-testid="training-quality-layers" style={{ marginBottom: 18 }}>
      {QUALITY_LAYER_LABELS.map(([key, label]) => {
        const normalized = item.status.toUpperCase();
        const tone = ['PASS', 'PASSED', 'COMPLETED', 'SUCCEEDED', '达标'].includes(normalized)
          ? '#16a34a'
          : ['FAILED', 'BLOCKED', 'UNPUBLISHABLE', 'UNUSABLE', '不达标'].includes(normalized)
          ? '#dc2626'
          : '#d97706';
        return (
          <div key={key} style={{ border: '1px solid #e2e8f0', borderLeft: `3px solid ${tone}`, borderRadius: 8, background: '#fff', padding: '13px 14px', minWidth: 0, boxShadow: '0 1px 2px rgba(15,23,42,.04)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
              <div style={{ color: '#64748b', fontSize: 12 }}>{label}</div>
              <strong style={{ color: tone, fontSize: 14 }}>{QUALITY_STATUS_LABELS[normalized] || item.status || '待验证'}</strong>
            </div>
            {item.summary && <div style={{ color: '#475569', fontSize: 13, lineHeight: 1.5, marginTop: 8 }}>{item.summary}</div>}
            {displayedMetrics.length ? (
              <Descriptions column={{ xs: 1, sm: 2, md: 3 }} size="small" bordered style={{ marginTop: 10 }}>
                {score !== null && Number.isFinite(score) && <Descriptions.Item label={<MetricHelp metric="质量得分" />}>{score.toFixed(4)}（{(score * 100).toFixed(1)}%）</Descriptions.Item>}
                {displayedMetrics.map(([metricLabel, metricValue]) => <Descriptions.Item key={metricLabel} label={<MetricHelp metric={metricLabel} />}>{metricValue}</Descriptions.Item>)}
              </Descriptions>
            ) : (
              <div style={{ color: '#94a3b8', fontSize: 12, marginTop: 8 }}>当前作业尚未返回训练验证指标。</div>
            )}
            {reasons.length ? <div style={{ color: '#64748b', fontSize: 12, lineHeight: 1.5, marginTop: 8 }}>判定说明：{reasons.join('；')}</div> : null}
          </div>
        );
      })}
    </div>
  );
}

function TrainingDetailHero({ row }: { row?: Row | null }) {
  const params = trainingParams(row);
  const status = String(row?.status || '').trim().toUpperCase();
  const completed = ['COMPLETED', 'SUCCEEDED', 'SUCCESS', 'FINISHED', 'DONE'].includes(status);
  const target = trainingNumber(row?.epochs ?? params.epochs);
  const current = trainingNumber(row?.current_epoch ?? row?.actual_epochs ?? params.current_epoch ?? params.actual_epochs) ?? (completed ? target : null);
  const bounded = current !== null && target !== null ? Math.min(current, target) : current;
  const percentValue = trainingNumber(row?.progress_percent);
  const percent = percentValue !== null ? Math.min(100, percentValue) : target && bounded !== null ? Math.min(100, Math.round((bounded / target) * 100)) : null;
  const statusTone = ['FAILED', 'CANCELLED'].includes(status) ? 'red' : completed ? 'green' : 'blue';
  return (
    <div style={{ border: '1px solid #dbe4ee', borderRadius: 10, background: 'linear-gradient(180deg, #f8fbff 0%, #ffffff 100%)', padding: '16px 18px', marginBottom: 18 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ color: '#64748b', fontSize: 12, marginBottom: 5 }}>训练作业详情 · #{row?.id ?? '-'}</div>
          <div style={{ color: '#0f172a', fontSize: 20, lineHeight: 1.3, fontWeight: 700, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{row?.name || (row?.id ? `训练作业 #${row.id}` : '未命名训练作业')}</div>
          <div style={{ color: '#475569', fontSize: 13, marginTop: 5 }}>标注任务：{row?.task_name || '未关联'}{row?.task_id ? ` · 任务 #${row.task_id}` : ''}</div>
        </div>
        <Space wrap size={6}>
          <Tag color={statusTone}>{trainingStatusLabel(row?.status)}</Tag>
          <Tag>{trainingRuntimeStatusLabel(row?.runtime_status || row?.status)}</Tag>
        </Space>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(145px, 1fr))', gap: 10, marginTop: 16 }}>
        <div><div style={{ color: '#64748b', fontSize: 12 }}>训练进度</div><strong style={{ color: '#0f172a', fontSize: 16 }}>{bounded !== null ? `${bounded} / ${target || '-'} 轮` : '尚未开始'}</strong></div>
        <div><div style={{ color: '#64748b', fontSize: 12 }}>完成比例</div><strong style={{ color: '#0f172a', fontSize: 16 }}>{percent !== null ? `${percent}%` : '待计算'}</strong></div>
        <div><div style={{ color: '#64748b', fontSize: 12 }}>训练方案</div><strong style={{ color: '#0f172a', fontSize: 14 }}>{trainingAlgorithmLabel(row?.algorithm_key || params.algorithm_key || row?.adapter_key)}</strong></div>
        <div><div style={{ color: '#64748b', fontSize: 12 }}>执行资源</div><strong style={{ color: '#0f172a', fontSize: 14 }}>{trainingExecutionLabel(row?.execution_mode)}</strong></div>
      </div>
      {percent !== null && <Progress percent={percent} status={status === 'FAILED' ? 'exception' : completed ? 'success' : 'active'} size="small" showInfo={false} style={{ marginTop: 14, marginBottom: 0 }} />}
    </div>
  );
}

function DetailSection({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  return (
    <section style={{ marginBottom: 22 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', borderBottom: '1px solid #e2e8f0', paddingBottom: 8, marginBottom: 10 }}>
        <h3 style={{ margin: 0, color: '#0f172a', fontSize: 15, lineHeight: 1.4 }}>{title}</h3>
        {description && <span style={{ color: '#94a3b8', fontSize: 12 }}>{description}</span>}
      </div>
      {children}
    </section>
  );
}

const trainingDetailLabelStyle: React.CSSProperties = {
  width: 92,
  minWidth: 92,
  whiteSpace: 'nowrap',
};

const trainingDetailContentStyle: React.CSSProperties = {
  minWidth: 0,
  wordBreak: 'break-word',
};

function trainingParams(row?: Row | null): Record<string, any> {
  const params = row?.params || row?.hyperparams;
  return params && typeof params === 'object' && !Array.isArray(params) ? params : {};
}

function trainingNumber(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? Math.round(parsed) : null;
}

function trainingJson(value: unknown): any {
  if (typeof value !== 'string') return value;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function trainingAlgorithmLabel(value: unknown): string {
  const code = String(value || '').trim().toLowerCase();
  return ({
    yolo_detection: 'YOLO 目标检测',
    yolo_detection_v1: 'YOLO 通用目标检测',
  } as Record<string, string>)[code] || (code ? String(value) : '未指定');
}

function trainingChipLabel(value: unknown): string {
  const code = String(value || '').trim().toLowerCase();
  return ({ bm1688: 'BM1688', bm1684: 'BM1684', rk1808: 'RK1808' } as Record<string, string>)[code] || String(value || '未指定');
}

function trainingPrecisionLabel(value: unknown): string {
  const code = String(value || '').trim().toLowerCase();
  return ({ fp32: 'FP32 全精度', fp16: 'FP16 半精度', int8: 'INT8 量化' } as Record<string, string>)[code] || String(value || '未指定');
}

function trainingExecutionLabel(value: unknown): string {
  const code = String(value || '').trim().toLowerCase();
  return ({ local: '本机训练', remote: '远程训练节点', container: '标准容器训练' } as Record<string, string>)[code] || String(value || '未指定');
}

function trainingRecipeLabel(value: unknown, version?: unknown): string {
  const code = String(value || '').trim().toLowerCase();
  const label = code === 'yolo_detection_v1' ? 'YOLO 通用目标检测配方' : code || '未指定';
  const suffix = version !== undefined && version !== null && String(version).trim() ? `（第 ${version} 版）` : '';
  return `${label}${suffix}`;
}

type TrainingLabelImageCount = {
  index?: number;
  label?: string;
  train_images?: number | null;
  val_images?: number | null;
  test_images?: number | null;
};

function TrainingDatasetDetail({ row, classPerformance }: { row?: Row | null; classPerformance?: TrainingLabelImageCount[] }) {
  const manifest = row?.dataset_manifest && typeof row.dataset_manifest === 'object' ? row.dataset_manifest as Record<string, any> : {};
  const params = trainingParams(row);
  const labels = trainingJson(manifest.labels_snapshot_json);
  const labelNames = Array.isArray(labels) ? labels.map((item) => String(item?.label_key || item?.name || item?.label_value || '')).filter(Boolean) : [];
  const sampleCount = manifest.sample_count ?? ((row?.train_sample_count !== undefined || row?.val_sample_count !== undefined)
    ? (Number(row?.train_sample_count) || 0) + (Number(row?.val_sample_count) || 0)
    : undefined);
  const version = manifest.version_no ? `v${manifest.version_no}` : row?.dataset_version_id ? `版本 ${row.dataset_version_id}` : '未绑定';
  const digest = String(manifest.manifest_sha256 || '').trim();
  return (
    <DetailSection title="训练数据" description="数据快照、划分与标签契约">
      <Descriptions column={2} size="small" bordered labelStyle={trainingDetailLabelStyle} contentStyle={trainingDetailContentStyle}>
        <Descriptions.Item label="数据集版本">{version}</Descriptions.Item>
        <Descriptions.Item label="样本数量">{sampleCount !== undefined ? `${sampleCount} 张` : '未记录'}</Descriptions.Item>
        <Descriptions.Item label="训练集">{row?.train_sample_count !== undefined ? `${row.train_sample_count} 张` : '未记录'}</Descriptions.Item>
        <Descriptions.Item label="验证集">{row?.val_sample_count !== undefined ? `${row.val_sample_count} 张` : '未记录'}</Descriptions.Item>
        <Descriptions.Item label="标签契约" span={2}>{labelNames.length ? <Space wrap size={[4, 4]}>{labelNames.map((name) => <Tag key={name}>{name}</Tag>)}</Space> : '未记录'}</Descriptions.Item>
        <Descriptions.Item label="清单校验码" span={2}>{digest ? `${digest.slice(0, 16)}...` : '未记录'}</Descriptions.Item>
      </Descriptions>
      {classPerformance?.length ? (
        <div style={{ marginTop: 12 }}>
          <div style={{ color: '#475569', fontSize: 13, fontWeight: 600, marginBottom: 8 }}>各标签标注图片数量</div>
          <Table<TrainingLabelImageCount>
            rowKey={(item) => String(item.index ?? item.label ?? 'unknown')}
            size="small"
            bordered
            pagination={false}
            dataSource={classPerformance}
            columns={[
              { title: '标签', dataIndex: 'label', key: 'label', render: (value: unknown) => String(value || '-') },
              { title: '训练图片数', dataIndex: 'train_images', key: 'train_images', render: (value: unknown) => value ?? '未记录' },
              { title: '验证图片数', dataIndex: 'val_images', key: 'val_images', render: (value: unknown) => value ?? '未记录' },
              { title: '测试图片数', dataIndex: 'test_images', key: 'test_images', render: (value: unknown) => value ?? '未记录' },
              { title: '合计图片数', key: 'total_images', render: (_: unknown, item: TrainingLabelImageCount) => {
                const values = [item.train_images, item.val_images, item.test_images];
                return values.some((value) => value !== null && value !== undefined) ? values.reduce<number>((sum, value) => sum + Number(value ?? 0), 0) : '未记录';
              } },
            ]}
          />
          <div style={{ color: '#94a3b8', fontSize: 12, marginTop: 6 }}>图片数量按去重后的图片统计；同一张图片中有多个同类拉框只计 1 张。</div>
        </div>
      ) : null}
      {!manifest.sample_count && !params.dataset_manifest && <div style={{ color: '#94a3b8', fontSize: 12, marginTop: 6 }}>该历史作业没有保存数据集快照，仅展示作业自身统计。</div>}
    </DetailSection>
  );
}

function TrainingConfigDetail({ row }: { row?: Row | null }) {
  const params = trainingParams(row);
  const modelFamily = String(params.model_family || '').toLowerCase() === 'yolov11' ? 'YOLO 第 11 代目标检测模型' : String(params.model_family || '未指定');
  const annotationKind = String(params.annotation_kind || '').toLowerCase() === 'bbox' ? '矩形框' : String(params.annotation_kind || '未指定');
  const recipe = trainingRecipeLabel(params.training_recipe_key, params.training_recipe_version);
  return (
    <DetailSection title="训练配置" description="算法、芯片与初始权重">
      <Descriptions column={2} size="small" bordered labelStyle={trainingDetailLabelStyle} contentStyle={trainingDetailContentStyle}>
        <Descriptions.Item label="算法能力">{trainingAlgorithmLabel(row?.algorithm_key || params.algorithm_key || row?.adapter_key)}</Descriptions.Item>
        <Descriptions.Item label="模型系列">{modelFamily}</Descriptions.Item>
        <Descriptions.Item label="标注类型">{annotationKind}</Descriptions.Item>
        <Descriptions.Item label="训练配方">{recipe}</Descriptions.Item>
        <Descriptions.Item label="芯片">{trainingChipLabel(params.chip_type)}</Descriptions.Item>
        <Descriptions.Item label="精度">{trainingPrecisionLabel(params.precision)}</Descriptions.Item>
        <Descriptions.Item label="输入尺寸">{row?.imgsz ? `${row.imgsz} × ${row.imgsz}` : '未指定'}</Descriptions.Item>
        <Descriptions.Item label="初始权重" span={2}>{row?.model_weights || params.weights_ref || params.model_weights || '基础权重'}</Descriptions.Item>
      </Descriptions>
    </DetailSection>
  );
}

function TrainingMetricsDetail({ row }: { row?: Row | null }) {
  const raw = trainingJson(row?.metrics || row?.metrics_summary);
  const metrics = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw as Record<string, any> : {};
  const overall = metrics.overall_metrics && typeof metrics.overall_metrics === 'object'
    ? metrics.overall_metrics
    : metrics.best_pt_val_metrics && typeof metrics.best_pt_val_metrics === 'object'
      ? metrics.best_pt_val_metrics
      : metrics.final_metrics && typeof metrics.final_metrics === 'object'
        ? metrics.final_metrics
        : metrics;
  const known: Array<[string, string]> = [['map50', 'mAP50'], ['map50_95', 'mAP50-95'], ['precision', '精确率'], ['recall', '召回率'], ['fitness', '综合适应度'], ['best_epoch', '最佳轮次']];
  const rows = known.filter(([key]) => {
    if (key === 'best_epoch') return metrics.best_epoch_metrics?.epoch !== undefined || overall[key] !== undefined;
    return overall[key] !== undefined && overall[key] !== null;
  });
  return (
    <DetailSection title="评估指标" description="训练完成后用于判断效果">
      {rows.length ? <Descriptions column={2} size="small" bordered labelStyle={trainingDetailLabelStyle} contentStyle={trainingDetailContentStyle}>{rows.map(([key, label]) => {
        const value = key === 'best_epoch' ? (overall[key] ?? metrics.best_epoch_metrics?.epoch) : overall[key];
        return <Descriptions.Item key={key} label={label}>{key === 'best_epoch' ? `${value} 轮` : Number.isFinite(Number(value)) ? Number(value).toFixed(4) : String(value)}</Descriptions.Item>;
      })}</Descriptions> : <div style={{ color: '#64748b', fontSize: 13 }}>当前作业尚未记录可展示的评估指标。</div>}
    </DetailSection>
  );
}

type TrainingVersionComparisonPayload = {
  available?: boolean;
  status?: string;
  current_job_id?: number;
  previous_job_id?: number | null;
  summary?: string;
  same_dataset?: boolean | null;
  metrics?: Array<{
    key: string;
    label: string;
    previous?: number | null;
    current?: number | null;
    delta?: number | null;
  }>;
};

const COMPARISON_STATUS = {
  improved: { label: '整体提升', color: 'green', alert: 'success' },
  regressed: { label: '整体下降', color: 'red', alert: 'error' },
  mixed: { label: '部分提升 · 有取舍', color: 'gold', alert: 'warning' },
  unchanged: { label: '基本持平', color: 'blue', alert: 'info' },
  insufficient_metrics: { label: '指标不足', color: 'default', alert: 'info' },
  no_previous_version: { label: '首个版本', color: 'default', alert: 'info' },
} as const;

function comparisonPercent(value?: number | null) {
  return value === null || value === undefined || !Number.isFinite(Number(value)) ? '—' : `${(Number(value) * 100).toFixed(2)}%`;
}

function comparisonDelta(value?: number | null) {
  return value === null || value === undefined || !Number.isFinite(Number(value)) ? '—' : `${Number(value) > 0 ? '+' : ''}${(Number(value) * 100).toFixed(2)} pts`;
}

export function TrainingVersionComparisonDetail({ payload }: { payload?: TrainingVersionComparisonPayload | null }) {
  if (!payload) return null;
  const status = COMPARISON_STATUS[payload.status as keyof typeof COMPARISON_STATUS] || COMPARISON_STATUS.insufficient_metrics;
  const rows = payload.metrics || [];
  return (
    <DetailSection title="与上一版本对比" description={payload.previous_job_id ? `上一版：训练作业 #${payload.previous_job_id} · 对比两版 best.pt 的权威验证集指标` : '记录模型系列每一版的真实效果变化'}>
      <Alert
        type={status.alert}
        showIcon
        message={<Space size={8}><Tag color={status.color} style={{ marginInlineEnd: 0 }}>{status.label}</Tag><span>{payload.summary || '暂无可对比结果。'}</span></Space>}
        style={{ marginBottom: rows.length ? 12 : 0 }}
      />
      {rows.length > 0 && <>
        <Table
          rowKey="key"
          size="small"
          pagination={false}
          dataSource={rows}
          columns={[
            { title: '验证指标', dataIndex: 'label', key: 'label' },
            { title: '上一版', dataIndex: 'previous', key: 'previous', render: comparisonPercent },
            { title: '本版', dataIndex: 'current', key: 'current', render: comparisonPercent },
            { title: '变化', dataIndex: 'delta', key: 'delta', render: (value: number | null | undefined) => {
              const numeric = Number(value);
              const color = value === null || value === undefined || Math.abs(numeric) <= 1e-9 ? '#64748b' : numeric > 0 ? '#15803d' : '#b91c1c';
              return <span style={{ color, fontWeight: 600 }}>{comparisonDelta(value)}</span>;
            } },
          ]}
        />
        <div style={{ color: '#64748b', fontSize: 12, lineHeight: 1.6, marginTop: 8 }}>
          差值单位为百分点（pts）。主结论优先看 mAP50-95，其次看 mAP50；精确率与召回率需结合判断，不能仅凭单项指标认定全面提升。
        </div>
      </>}
    </DetailSection>
  );
}

type TrainingHistoryItem = Row & { epoch?: number };
type TrainingHistoryPayload = {
  available?: boolean;
  message?: string | null;
  source?: string | null;
  current_epoch?: number | null;
  best_epoch?: number | null;
  history?: TrainingHistoryItem[];
};

type TrainingClassPerformance = {
  index?: number;
  label?: string;
  train_instances?: number | null;
  val_instances?: number | null;
  test_instances?: number | null;
  precision?: number | null;
  recall?: number | null;
  map50?: number | null;
  map50_95?: number | null;
  test_precision?: number | null;
  test_recall?: number | null;
  test_map50?: number | null;
  test_map50_95?: number | null;
  train_images?: number | null;
  val_images?: number | null;
  test_images?: number | null;
};

type TrainingClassAnalysisPayload = {
  class_performance?: TrainingClassPerformance[];
  preview_images?: YoloTrainAnalysisPreviewImage[];
  health_diagnosis?: {
    status?: string;
    summary?: string;
    notices?: Array<{ severity?: string; title?: string; detail?: string; action?: string }>;
  };
};

function trainingAssetLabel(asset: YoloTrainAnalysisPreviewImage): string {
  if (asset.kind === 'training_summary') return '训练曲线';
  if (asset.kind === 'confusion_matrix') return '混淆矩阵';
  if (asset.kind === 'metric_curve') return '精度与召回曲线';
  if (asset.kind === 'validation_preview') return '验证集预测样例';
  if (asset.kind === 'prediction') return '预测样例';
  if (asset.kind === 'label_distribution') return '标签分布';
  return '训练结果';
}

function trainingAssetDescription(asset: YoloTrainAnalysisPreviewImage): string {
  if (asset.kind === 'training_summary') return '看损失是否下降、验证指标是否稳定，以及是否出现过拟合。';
  if (asset.kind === 'confusion_matrix') return '看哪些标签容易互相混淆，定位误检和漏检的主要类别。';
  if (asset.kind === 'metric_curve') return '看不同置信度下精确率和召回率的取舍，辅助设置阈值。';
  if (asset.kind === 'validation_preview') return '看实际框的位置、漏框、错框和框大小是否合理。';
  if (asset.kind === 'prediction') return '查看模型在样例图片上的实际检测结果。';
  if (asset.kind === 'label_distribution') return '看训练过程中标签分布是否偏斜。';
  return '训练过程生成的辅助结果文件。';
}

function TrainingResultAsset({ asset }: { asset: YoloTrainAnalysisPreviewImage }) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    setSrc(null);
    setFailed(false);
    getYoloTrainAnalysisAsset(asset.url)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [asset.url]);

  return (
    <div style={{ border: '1px solid #e2e8f0', borderRadius: 8, background: '#fff', overflow: 'hidden', minWidth: 0 }}>
      <div style={{ padding: '10px 12px 8px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
          <Space size={6}><FileImageOutlined style={{ color: '#2563eb' }} /><strong style={{ color: '#0f172a', fontSize: 13 }}>{trainingAssetLabel(asset)}</strong></Space>
          {src && <a href={src} download={asset.filename} title="下载结果文件" aria-label={`下载${asset.filename}`}><DownloadOutlined /></a>}
        </div>
        <div style={{ color: '#64748b', fontSize: 12, lineHeight: 1.5, marginTop: 5 }}>{trainingAssetDescription(asset)}</div>
      </div>
      <div style={{ minHeight: 150, background: '#f8fafc', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 8 }}>
        {src ? <Image src={src} alt={`${trainingAssetLabel(asset)} ${asset.filename}`} preview={{ mask: '查看大图' }} style={{ maxWidth: '100%', maxHeight: 250, objectFit: 'contain' }} /> : failed ? <span style={{ color: '#b91c1c', fontSize: 12 }}>结果文件加载失败</span> : <Spin size="small" />}
      </div>
      <div style={{ padding: '6px 12px 9px', color: '#94a3b8', fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={asset.filename}>{asset.filename}</div>
    </div>
  );
}

function TrainingResultAssetsDetail({
  payload,
  loading,
  error,
  onRetry,
}: {
  payload?: TrainingClassAnalysisPayload | null;
  loading: boolean;
  error?: unknown;
  onRetry: () => void;
}) {
  const assets = payload?.preview_images || [];
  const priority: Record<string, number> = {
    training_summary: 1,
    confusion_matrix: 2,
    metric_curve: 3,
    validation_preview: 4,
    prediction: 5,
    label_distribution: 6,
    other: 7,
  };
  const ordered = [...assets].sort((a, b) => (priority[a.kind || 'other'] || 99) - (priority[b.kind || 'other'] || 99));
  const featured = ordered.slice(0, 6);
  const remaining = ordered.slice(6);
  return (
    <DetailSection title="训练结果" description="训练结束后生成的图表和样例，用于判断模型好坏与定位改进方向">
      {loading ? <div role="status" aria-live="polite"><Spin /></div> : error ? (
        <Alert type="error" showIcon message="训练结果加载失败" description="训练作业本身不会受到影响，请稍后重试。" action={<Button size="small" onClick={onRetry}>重试</Button>} />
      ) : assets.length ? (
        <>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message="先看训练曲线，再看混淆矩阵和验证集预测样例"
            description="图表只能辅助判断，最终是否可用还要结合独立测试集和 BM1688 真机验证。"
          />
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>
            {featured.map((asset) => <TrainingResultAsset key={asset.asset_id || asset.url} asset={asset} />)}
          </div>
          {remaining.length ? (
            <Collapse
              ghost
              items={[{ key: 'more-results', label: `其他训练结果（${remaining.length} 个）`, children: <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>{remaining.map((asset) => <TrainingResultAsset key={asset.asset_id || asset.url} asset={asset} />)}</div> }]}
              style={{ marginTop: 8 }}
            />
          ) : null}
        </>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无训练图表或预测样例" />
      )}
    </DetailSection>
  );
}

type TrainingAnalysis = {
  best: TrainingHistoryItem;
  latest: TrainingHistoryItem;
  bestMap50?: number;
  bestMap50_95?: number;
  latestMap50?: number;
  latestMap50_95?: number;
  mapGap?: number;
  recentSpread?: number;
  state: 'converged' | 'overfit-risk' | 'improving' | 'review';
  stateLabel: string;
  summary: string;
  recommendation: string;
};

function metricValue(item: TrainingHistoryItem | undefined, key: string): number | undefined {
  const value = item?.[key];
  if (value === null || value === undefined || value === '') return undefined;
  const number = Number(value);
  return Number.isFinite(number) ? number : undefined;
}

function displayMetric(value: number | undefined): string {
  return value === undefined ? '-' : value.toFixed(4);
}

function displayRate(value: unknown): string {
  if (value === null || value === undefined || value === '') return '-';
  const number = Number(value);
  return Number.isFinite(number) ? `${(number * 100).toFixed(1)}%` : '-';
}

const TRAINING_METRIC_HELP: Record<string, { title: string; description: string }> = {
  '质量得分': {
    title: '质量得分',
    description: '平台兼容展示字段，当前通常取验证集 mAP50-95，不是额外的一套独立指标。',
  },
  'mAP50-95': {
    title: 'mAP50-95',
    description: '主要判断整体检测和定位质量。数值低说明目标位置或类别判断整体不稳定，应优先查看最佳轮次和标签级指标。',
  },
  mAP50: {
    title: 'mAP50',
    description: '判断宽松条件下是否能找到目标。mAP50 高但 mAP50-95 低，通常表示能找到目标，但框的位置还不够准确。',
  },
  精确率: {
    title: '精确率',
    description: '判断误检多不多。数值低表示背景容易被识别成目标，需要补充负样本或检查标签边界。',
  },
  召回率: {
    title: '召回率',
    description: '判断漏检多不多。数值低表示目标找不全，需要补充漏检场景、远小目标和遮挡样本。',
  },
  '验证精确率': {
    title: '验证精确率',
    description: '当前标签在验证集上的误检控制能力，越低说明该标签更容易把背景识别成目标。',
  },
  '验证召回率': {
    title: '验证召回率',
    description: '当前标签在验证集上的漏检控制能力，越低说明该标签还有较多目标没有被找到。',
  },
  '验证 mAP50': {
    title: '验证 mAP50',
    description: '当前标签在宽松 IoU 条件下的检测能力，用来辅助判断是否能找到该类目标。',
  },
  '验证 mAP50-95': {
    title: '验证 mAP50-95',
    description: '当前标签在多个 IoU 条件下的检测和定位质量，用来发现该类框的位置是否准确。',
  },
  '测试精确率': {
    title: '测试精确率',
    description: '当前标签在独立测试集上的误检控制能力，用于判断模型对未参与调参的数据是否稳定。',
  },
  '测试召回率': {
    title: '测试召回率',
    description: '当前标签在独立测试集上的漏检控制能力，用于判断模型对新数据的泛化能力。',
  },
  '测试 mAP50-95': {
    title: '测试 mAP50-95',
    description: '当前标签在独立测试集上的综合检测和定位质量，是判断泛化能力的重要依据。',
  },
  整体召回率: {
    title: '整体召回率',
    description: '按全部目标汇总计算的召回率，用于观察整体漏检情况；仍需结合每个标签的召回率。',
  },
  样本总数: {
    title: '样本总数',
    description: '本次训练使用的样本数量，不是效果指标；样本过少时，精确率和召回率的稳定性会降低。',
  },
};

function MetricHelp({ metric }: { metric: string }) {
  const help = TRAINING_METRIC_HELP[metric];
  if (!help) return <span>{metric}</span>;
  return (
    <Space size={4}>
      <span>{metric}</span>
      <Popover
        trigger="click"
        title={help.title}
        content={<div style={{ maxWidth: 300, lineHeight: 1.6 }}>{help.description}</div>}
      >
        <button
          type="button"
          aria-label={`查看${metric}说明`}
          title={`查看${metric}说明`}
          style={{ border: 0, padding: 0, background: 'transparent', color: '#64748b', cursor: 'pointer', lineHeight: 1 }}
        >
          <QuestionCircleOutlined />
        </button>
      </Popover>
    </Space>
  );
}

function classConclusion(item: TrainingClassPerformance): { label: string; color: string } {
  const valInstances = Number(item.val_instances || 0);
  if (!valInstances) return { label: '无验证样本', color: 'orange' };
  const recall = Number(item.recall);
  const map = Number(item.map50_95);
  if (Number.isFinite(recall) && recall < 0.8) return { label: '召回偏低', color: 'orange' };
  if (Number.isFinite(map) && map < 0.8) return { label: '定位质量偏低', color: 'orange' };
  return { label: '可用，需结合测试集', color: 'green' };
}

function TrainingClassAnalysisDetail({
  payload,
  loading,
  error,
}: {
  payload?: TrainingClassAnalysisPayload | null;
  loading: boolean;
  error?: unknown;
}) {
  const rows: TrainingClassPerformance[] = payload?.class_performance || [];
  if (loading) return <DetailSection title="标签级效果" description="各标签的检测效果"><div role="status" aria-live="polite"><Spin /></div></DetailSection>;
  if (error || !rows.length) return null;
  const notices = payload?.health_diagnosis?.notices || [];
  const columns: ColumnsType<TrainingClassPerformance> = [
    { title: '标签', dataIndex: 'label', key: 'label', fixed: 'left', width: 170, render: (value: unknown) => String(value || '-') },
    { title: '训练框数', dataIndex: 'train_instances', key: 'train_instances', width: 90, render: (value: unknown) => value ?? '-' },
    { title: '验证框数', dataIndex: 'val_instances', key: 'val_instances', width: 90, render: (value: unknown) => value ?? '-' },
    { title: '测试框数', dataIndex: 'test_instances', key: 'test_instances', width: 90, render: (value: unknown) => value ?? '-' },
    { title: <MetricHelp metric="验证精确率" />, dataIndex: 'precision', key: 'precision', width: 125, render: displayRate },
    { title: <MetricHelp metric="验证召回率" />, dataIndex: 'recall', key: 'recall', width: 125, render: displayRate },
    { title: <MetricHelp metric="验证 mAP50" />, dataIndex: 'map50', key: 'map50', width: 120, render: displayRate },
    { title: <MetricHelp metric="验证 mAP50-95" />, dataIndex: 'map50_95', key: 'map50_95', width: 135, render: displayRate },
    { title: <MetricHelp metric="测试精确率" />, dataIndex: 'test_precision', key: 'test_precision', width: 125, render: displayRate },
    { title: <MetricHelp metric="测试召回率" />, dataIndex: 'test_recall', key: 'test_recall', width: 125, render: displayRate },
    { title: <MetricHelp metric="测试 mAP50-95" />, dataIndex: 'test_map50_95', key: 'test_map50_95', width: 135, render: displayRate },
    { title: '平台提示', key: 'conclusion', width: 145, render: (_: unknown, item: TrainingClassPerformance) => { const result = classConclusion(item); return <Tag color={result.color}>{result.label}</Tag>; } },
  ];
  return (
    <DetailSection title="标签级效果" description="按标签查看精确率、召回率和 mAP">
      {notices.length ? (
        <Alert
          type={notices.some((notice) => notice.severity === 'critical') ? 'warning' : 'info'}
          showIcon
          style={{ marginBottom: 12 }}
          message="训练数据或类别效果需要关注"
          description={<Space direction="vertical" size={2}>{notices.slice(0, 3).map((notice, index) => <span key={`${notice.title || 'notice'}-${index}`}>{notice.title || '提示'}：{notice.detail || notice.action || '请结合样本复核。'}</span>)}</Space>}
        />
      ) : null}
      <Table<TrainingClassPerformance>
        rowKey={(item) => String(item.index ?? item.label ?? 'unknown')}
        size="small"
        bordered
        pagination={false}
        scroll={{ x: 1250 }}
        dataSource={rows}
        columns={columns}
      />
      <div style={{ color: '#64748b', fontSize: 13, lineHeight: 1.7, marginTop: 8 }}>
        精确率、召回率、mAP50、mAP50-95 是 YOLO 官方检测评估指标；验证集用于调参与选最佳权重，测试集用于独立判断泛化。平台提示仅用于定位短板，不直接替代质量门禁。
      </div>
    </DetailSection>
  );
}

function deriveTrainingAnalysis(history: TrainingHistoryItem[]): TrainingAnalysis | null {
  if (!history.length) return null;
  const scored = history.filter((item) => metricValue(item, 'fitness') !== undefined || metricValue(item, 'map50_95') !== undefined);
  const candidates = scored.length ? scored : history;
  const best = candidates.reduce((winner, item) => {
    const winnerScore = metricValue(winner, 'fitness') ?? metricValue(winner, 'map50_95') ?? -1;
    const itemScore = metricValue(item, 'fitness') ?? metricValue(item, 'map50_95') ?? -1;
    return itemScore > winnerScore ? item : winner;
  }, candidates[0]);
  const latest = history[history.length - 1];
  const bestMap50 = metricValue(best, 'map50');
  const bestMap50_95 = metricValue(best, 'map50_95');
  const latestMap50 = metricValue(latest, 'map50');
  const latestMap50_95 = metricValue(latest, 'map50_95');
  const mapGap = bestMap50_95 !== undefined && latestMap50_95 !== undefined ? latestMap50_95 - bestMap50_95 : undefined;
  const recent = history.slice(-5).map((item) => metricValue(item, 'map50_95')).filter((value): value is number => value !== undefined);
  const recentSpread = recent.length > 1 ? Math.max(...recent) - Math.min(...recent) : undefined;
  const tail = history.slice(-3);
  const firstTail = tail[0];
  const trainLossDropped = metricValue(firstTail, 'train_box_loss') !== undefined && metricValue(latest, 'train_box_loss') !== undefined
    ? (metricValue(latest, 'train_box_loss') as number) < (metricValue(firstTail, 'train_box_loss') as number)
    : false;
  const valLossRose = metricValue(firstTail, 'val_box_loss') !== undefined && metricValue(latest, 'val_box_loss') !== undefined
    ? (metricValue(latest, 'val_box_loss') as number) > (metricValue(firstTail, 'val_box_loss') as number) * 1.03
    : false;
  const likelyOverfit = Boolean(mapGap !== undefined && mapGap < -0.02 && trainLossDropped && valLossRose);
  const isStable = Boolean(recentSpread !== undefined && recentSpread <= 0.01 && best.epoch !== undefined && latest.epoch !== undefined && latest.epoch - best.epoch >= 3);
  const isStillImproving = best.epoch !== undefined && latest.epoch !== undefined && latest.epoch >= best.epoch - 1;
  const state = likelyOverfit ? 'overfit-risk' : isStable ? 'converged' : isStillImproving ? 'improving' : 'review';
  const stateLabel = state === 'overfit-risk' ? '可能过拟合' : state === 'converged' ? '已基本收敛' : state === 'improving' ? '仍在提升' : '需要复核';
  const bestEpoch = best.epoch ? `第 ${best.epoch} 轮` : '最佳轮次未知';
  const summary = bestMap50_95 !== undefined
    ? `训练验证集最佳 mAP50-95 为 ${displayMetric(bestMap50_95)}，出现在${bestEpoch}；当前为 ${displayMetric(latestMap50_95)}。`
    : `最佳结果出现在${bestEpoch}，当前已记录 ${latest.epoch ?? history.length} 轮。`;
  const recommendation = likelyOverfit
    ? '训练损失还在下降，但验证损失上升且当前指标低于最佳值。建议优先检查困难样本、标签质量和数据增强，再决定是否继续训练。'
    : isStable
      ? '最近几轮指标波动很小，继续增加轮数的收益有限。建议进入独立验证和 BM1688 真机验证，不要只看训练指标发布。'
      : isStillImproving
        ? '最佳结果出现在最后一轮附近，模型还可能继续提升。建议先完成独立验证，再决定是否增加训练轮数。'
        : '训练过程与最佳结果存在差距，建议结合类别指标、错误样本和独立验证集复核后再调整配置。';
  return { best, latest, bestMap50, bestMap50_95, latestMap50, latestMap50_95, mapGap, recentSpread, state, stateLabel, summary, recommendation };
}

function TrainingHistoryDetail({
  payload,
  loading,
  error,
  onRetry,
}: {
  payload?: TrainingHistoryPayload | null;
  loading: boolean;
  error?: unknown;
  onRetry: () => void;
}) {
  const history: TrainingHistoryItem[] = payload && Array.isArray(payload.history) ? payload.history : [];
  const analysis = deriveTrainingAnalysis(history);
  return (
    <DetailSection title="训练分析" description="根据最佳轮次和近期指标判断训练状态">
      {loading ? <div role="status" aria-live="polite"><Spin /></div> : error ? (
        <Alert type="error" showIcon message="逐轮训练记录加载失败" description="请稍后重试，训练作业本身不会受到影响。" action={<Button size="small" onClick={onRetry}>重试</Button>} />
      ) : analysis ? (
        <>
          <Alert
            type={analysis.state === 'overfit-risk' ? 'warning' : analysis.state === 'converged' ? 'success' : 'info'}
            showIcon
            message={<Space size={8}><span>{analysis.stateLabel}</span><Tag color={analysis.state === 'overfit-risk' ? 'orange' : analysis.state === 'converged' ? 'green' : 'blue'}>{analysis.latest.epoch ?? history.length} 轮</Tag></Space>}
            description={<div><div style={{ marginBottom: 6 }}>{analysis.summary}</div><span>{analysis.recommendation}</span></div>}
            style={{ marginBottom: 12 }}
          />
          <Descriptions column={2} size="small" bordered labelStyle={trainingDetailLabelStyle} contentStyle={trainingDetailContentStyle} style={{ marginBottom: 12 }}>
            <Descriptions.Item label="训练验证最佳 mAP50-95">{displayMetric(analysis.bestMap50_95)}</Descriptions.Item>
            <Descriptions.Item label="训练验证当前 mAP50-95">{displayMetric(analysis.latestMap50_95)}</Descriptions.Item>
            <Descriptions.Item label="训练验证最佳 mAP50">{displayMetric(analysis.bestMap50)}</Descriptions.Item>
            <Descriptions.Item label="训练验证当前 mAP50">{displayMetric(analysis.latestMap50)}</Descriptions.Item>
            <Descriptions.Item label="最佳轮次">{analysis.best.epoch ? `${analysis.best.epoch} 轮` : '未计算'}</Descriptions.Item>
            <Descriptions.Item label="最近 5 轮波动">{analysis.recentSpread === undefined ? '-' : analysis.recentSpread <= 0.01 ? '稳定' : analysis.recentSpread.toFixed(4)}</Descriptions.Item>
            <Descriptions.Item label="训练记录">{payload?.source || '训练记录'}，共 {history.length} 轮</Descriptions.Item>
            <Descriptions.Item label="最佳与当前差值">{analysis.mapGap === undefined ? '-' : `${analysis.mapGap >= 0 ? '+' : ''}${analysis.mapGap.toFixed(4)}`}</Descriptions.Item>
          </Descriptions>
          <div style={{ color: '#64748b', fontSize: 13, lineHeight: 1.7 }}>
            分析依据：最佳与当前验证指标、最近几轮指标波动、训练损失与验证损失的变化关系。逐轮原始记录仍保存在训练目录中，不在详情页展开。
          </div>
        </>
      ) : <Alert type="info" showIcon message={payload?.message || '当前作业没有可分析的训练记录'} description="训练详情仍会展示当前轮次、汇总指标和数据集信息。" />}
    </DetailSection>
  );
}

function ResourceDetailModal({
  module,
  row,
  projectId,
  detailPath,
  detailActions,
  onClose,
}: {
  module: PlatformModule;
  row: Row | null;
  projectId?: number;
  detailPath?: (row: Row) => string | null;
  detailActions?: (row: Row) => React.ReactNode;
  onClose: () => void;
}) {
  const path = row && detailPath ? detailPath(row) : null;
  const online = typeof navigator === 'undefined' || navigator.onLine;
  const detailQuery = useQuery({
    queryKey: projectId == null
      ? ['platform-detail', module, row?.id]
      : projectQueryKey('platform-detail', projectId, module, row?.id),
    queryFn: async () => {
      const response = await request.get(path as string);
      return response.data?.data ?? response.data;
    },
    enabled: Boolean(path),
    staleTime: 5_000,
    refetchInterval: (query: any) => {
      const current = query.state.data || row;
      const activeStatuses = module === 'training'
        ? ACTIVE_TRAINING_STATUSES
        : null;
      return activeStatuses?.has(String(current?.status || '').toUpperCase()) ? 10_000 : false;
    },
  });
  const trainingHistoryQuery = useQuery<TrainingHistoryPayload>({
    queryKey: ['platform-training-history', row?.id],
    queryFn: async () => {
      const response = await request.get(`/v1/training/jobs/${row?.id}/history`);
      return response.data?.data ?? response.data;
    },
    enabled: module === 'training' && Boolean(row?.id),
    staleTime: 5_000,
    refetchInterval: (query: any) => {
      const current = query.state.data || row;
      return module === 'training' && ACTIVE_TRAINING_STATUSES.has(String(current?.status || '').toUpperCase()) ? 10_000 : false;
    },
  });
  const trainingClassAnalysisQuery = useQuery<TrainingClassAnalysisPayload>({
    queryKey: ['platform-training-class-analysis', row?.id],
    queryFn: async () => {
      const response = await request.get(`/v1/yolo-train-jobs/${row?.id}/analysis`);
      return response.data?.data ?? response.data;
    },
    enabled: module === 'training' && Boolean(row?.id) && String(row?.algorithm_key || '').toLowerCase().includes('yolo'),
    staleTime: 5_000,
    refetchInterval: (query: any) => {
      const current = query.state.data || row;
      return module === 'training' && ACTIVE_TRAINING_STATUSES.has(String(current?.status || '').toUpperCase()) ? 10_000 : false;
    },
  });
  const detailRow = detailQuery.data || row;
  const trainingFailureGuidance = module === 'training' ? getTrainingFailureGuidance(detailRow) : null;
  const detailStatus = responseStatus(detailQuery.error);
  const errorCopy = !online
    ? { message: '当前网络已断开', description: '恢复网络后重试，页面不会提交任何操作。' }
    : detailStatus === 403
    ? { message: '无权限查看详情', description: '当前账号无权访问此资源。' }
    : detailStatus === 404
    ? { message: '详情不存在', description: '该记录可能已删除或当前账号无权访问。' }
    : detailStatus === 409
    ? { message: '详情版本冲突', description: '详情已变化，请刷新后重试。' }
    : { message: '详情加载失败', description: '请检查网络或稍后重试。' };
  const governanceKeys = module === 'providers'
    ? ['provider_key', 'provider_version', 'status', 'available', 'production_ready', 'runtime_registered', 'runtime_healthy', 'runtime_ready', 'hil_verified', 'image_digest', 'toolchain_version', 'availability_reason', 'runtime_last_error']
    : module === 'models'
    ? ['id', 'name', 'status', 'quality_status', 'quality_gate_status', 'model_set_id', 'export_id', 'source_training_job_id', 'manifest_sha256', 'release_key']
    : module === 'training'
    ? ['id', 'task_name', 'task_id', 'algorithm_key', 'status', 'runtime_status', 'training_assessment', 'edge_validation_assessment', 'release_gate_assessment', 'quality_assessment', 'error_message', 'failure_reason', 'dataset_version_id', 'dataset_manifest', 'manifest_sha256', 'train_sample_count', 'val_sample_count', 'metrics_summary', 'metrics', 'execution_mode', 'train_node_id', 'model_weights', 'epochs', 'imgsz', 'params', 'hyperparams']
    : [];
  const entries = detailRow
    ? Object.entries(detailRow)
        .filter(([key, value]) => value !== null && value !== undefined && !['created_at', 'updated_at'].includes(key) && !(module === 'training' && [
          'training_assessment', 'edge_validation_assessment', 'release_gate_assessment', 'quality_assessment',
          'current_epoch', 'progress_percent', 'epochs', 'imgsz', 'params', 'hyperparams', 'dataset_manifest',
          'metrics', 'metrics_summary', 'previous_version_comparison', 'model_weights', 'failure_reason', 'error_message', 'dataset_version_id',
          'train_sample_count', 'val_sample_count', 'status',
          'algorithm_key', 'adapter_key', 'execution_mode', 'runtime_status', 'train_node_id', 'task_name', 'task_id', 'id', 'name',
        ].includes(key)))
        .sort(([a], [b]) => {
          const ai = governanceKeys.indexOf(a);
          const bi = governanceKeys.indexOf(b);
          return (ai < 0 ? 999 : ai) - (bi < 0 ? 999 : bi);
        })
        .slice(0, governanceKeys.length ? 18 : 12)
        .map(([key, value]) => {
          if (key === 'status' && module === 'training') {
            return [DETAIL_LABELS[key], `${trainingStatusLabel(value)}（${String(value)}）`] as [string, string];
          }
          if (key === 'runtime_status' && module === 'training') {
            return [DETAIL_LABELS[key], `${trainingRuntimeStatusLabel(value)}（${String(value)}）`] as [string, string];
          }
          if (module === 'training' && ['training_assessment', 'edge_validation_assessment', 'release_gate_assessment'].includes(key) && value && typeof value === 'object') {
            const assessment = value as Record<string, any>;
            const label = assessment.status_label || assessment.status_code || assessment.status || '未确认';
            const reason = assessment.reason_summary || assessment.summary;
            return [DETAIL_LABELS[key], reason ? `${label}：${reason}` : String(label)] as [string, string];
          }
          if (key === 'quality_assessment' && value && typeof value === 'object') {
            const assessment = value as Record<string, any>;
            const item = assessment.release_readiness || assessment.training_quality || assessment.edge_usability;
            if (item && typeof item === 'object') {
              return [DETAIL_LABELS[key], `${item.status || item.status_label || '未确认'}${item.summary ? `：${item.summary}` : ''}`] as [string, string];
            }
          }
          if (key === 'dataset_manifest' && value && typeof value === 'object') {
            const manifest = value as Record<string, unknown>;
            const version = manifest.version_no ? `v${manifest.version_no}` : '未标版本';
            const samples = manifest.sample_count !== undefined ? `${manifest.sample_count} 个样本` : '样本数未知';
            const sha = manifest.manifest_sha256 ? ` · SHA256 ${String(manifest.manifest_sha256).slice(0, 12)}...` : '';
            return [DETAIL_LABELS[key], `${version} · ${samples}${sha}`] as [string, string];
          }
          if (module === 'training' && ['metrics_summary', 'metrics'].includes(key)) {
            return [DETAIL_LABELS[key], trainingMetricsSummary(value)] as [string, string];
          }
          const rendered = typeof value === 'object' ? JSON.stringify(value) : String(value);
          return [DETAIL_LABELS[key] || key, rendered] as [string, string];
        })
    : [];

  return (
    <Modal
      title={`${MODULE_COPY[module].title}详情`}
      open={Boolean(row)}
      centered
      onCancel={onClose}
      width={module === 'training' ? 1020 : 760}
      footer={null}
      destroyOnClose
      styles={{ body: { maxHeight: 'calc(100vh - 150px)', overflowY: 'auto', padding: module === 'training' ? '4px 20px 20px' : undefined } }}
    >
      {detailQuery.isLoading ? (
        <div role="status" aria-live="polite"><Spin /></div>
      ) : detailQuery.error ? (
        <Alert
          type={online ? 'error' : 'warning'}
          showIcon
          message={errorCopy.message}
          description={errorCopy.description}
          action={<Button size="small" icon={<ReloadOutlined />} onClick={() => detailQuery.refetch()}>重试</Button>}
        />
      ) : detailRow ? (
        <>
          {module === 'training' && <TrainingDetailHero row={detailRow} />}
          {module === 'training' && (
            <DetailSection title="训练质量" description="仅展示 PT 训练阶段的验证集结果；边端和发布检查在模型中心查看">
              <TrainingQualityLayers row={detailRow} />
            </DetailSection>
          )}
          {module === 'training' && <TrainingVersionComparisonDetail payload={detailRow.previous_version_comparison} />}
          {module === 'training' && trainingFailureGuidance && (
            <Alert
              type="error"
              showIcon
              style={{ marginBottom: 18 }}
              message="训练失败，需要人工确认后恢复"
              description={
                <div>
                  <div style={{ marginBottom: 8 }}>{trainingFailureGuidance.reason}</div>
                  <strong>建议处理</strong>
                  <ol style={{ margin: '6px 0 0 18px', padding: 0 }}>
                    {trainingFailureGuidance.steps.map((step) => <li key={step}>{step}</li>)}
                  </ol>
                </div>
              }
            />
          )}
          {module === 'training' && <TrainingHistoryDetail payload={trainingHistoryQuery.data} loading={trainingHistoryQuery.isLoading} error={trainingHistoryQuery.error} onRetry={() => void trainingHistoryQuery.refetch()} />}
          {module === 'training' && <TrainingResultAssetsDetail payload={trainingClassAnalysisQuery.data} loading={trainingClassAnalysisQuery.isLoading} error={trainingClassAnalysisQuery.error} onRetry={() => void trainingClassAnalysisQuery.refetch()} />}
          {module === 'training' && <TrainingClassAnalysisDetail payload={trainingClassAnalysisQuery.data} loading={trainingClassAnalysisQuery.isLoading} error={trainingClassAnalysisQuery.error} />}
          {module === 'training' && <TrainingDatasetDetail row={detailRow} classPerformance={trainingClassAnalysisQuery.data?.class_performance} />}
          {module === 'training' && <TrainingConfigDetail row={detailRow} />}
          {module === 'training' && !trainingHistoryQuery.isLoading && !(trainingHistoryQuery.data?.history?.length) && <TrainingMetricsDetail row={detailRow} />}
          {entries.length ? (
            <Collapse
              ghost
              items={[{ key: 'advanced', label: module === 'training' ? '其他系统字段（高级）' : '更多信息', children: <Descriptions column={1} size="small" bordered>{entries.map(([key, value]) => <Descriptions.Item key={key} label={key}>{value}</Descriptions.Item>)}</Descriptions> }]}
              style={{ marginBottom: 18 }}
            />
          ) : null}
          {detailActions && (
            <div style={{ marginTop: 18 }}>
              {detailActions(detailRow)}
            </div>
          )}
        </>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无详情数据" />
      )}
    </Modal>
  );
}

export type ResourcePageProps = {
  module: PlatformModule;
  queryKey: string;
  queryFn: (scopeId?: number, page?: number, size?: number, search?: string) => Promise<Row[] | { data?: Row[]; meta_data?: { total?: number }; meta?: { total?: number }; total?: number }>;
  columns: ColumnsType<Row>;
  needsProject?: boolean;
  staleTime?: number;
  activeStatuses?: ReadonlySet<string>;
  pollInterval?: number;
  detailPath?: (row: Row) => string | null;
  detailActions?: (row: Row) => React.ReactNode;
  beforeTable?: React.ReactNode;
  afterTable?: React.ReactNode;
  pagination?: boolean;
  pageSize?: number;
  rowActions?: (row: Row) => React.ReactNode;
  actionColumnWidth?: number;
  detailColumnWidth?: number;
  tableLayout?: 'auto' | 'fixed';
  scrollX?: number | string;
  headerActions?: React.ReactNode;
  hideModuleHeader?: boolean;
  hideModuleStatus?: boolean;
  titleOverride?: string;
  nextOverride?: string;
  /** Optional server-backed search. The parent owns the applied value. */
  search?: string;
  onSearchChange?: (value: string) => void;
  searchPlaceholder?: string;
  searchAriaLabel?: string;
  /** URL parameter used to persist the applied server search. Defaults to q. */
  searchParam?: string;
};

function updateResourceSearchUrl(param: string, value: string) {
  if (typeof window === 'undefined') return;
  const key = String(param || 'q').trim() || 'q';
  const url = new URL(window.location.href);
  const normalized = String(value || '').trim();
  if (normalized) url.searchParams.set(key, normalized);
  else url.searchParams.delete(key);
  const query = url.searchParams.toString();
  const next = `${url.pathname}${query ? `?${query}` : ''}${url.hash}`;
  window.history.replaceState(window.history.state, '', next);
}

export default function ResourcePage({
  module,
  queryKey,
  queryFn,
  columns,
  needsProject = true,
  staleTime = 30_000,
  activeStatuses,
  pollInterval = 15_000,
  detailPath,
  detailActions,
  beforeTable,
  afterTable,
  pagination = false,
  pageSize = 20,
  rowActions,
  actionColumnWidth,
  detailColumnWidth,
  tableLayout,
  scrollX,
  headerActions,
  hideModuleHeader = false,
  hideModuleStatus = false,
  titleOverride,
  nextOverride,
  search = '',
  onSearchChange,
  searchPlaceholder = '搜索',
  searchAriaLabel = '搜索',
  searchParam = 'q',
}: ResourcePageProps) {
  const [selectedRow, setSelectedRow] = useState<Row | null>(null);
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(pageSize);
  const [searchDraft, setSearchDraft] = useState(search);
  const searchUrlInitializedRef = useRef(false);
  const pageVisible = usePageVisible();
  const { projectId, projectsQuery } = usePlatformProject(needsProject);
  useEffect(() => {
    if (!onSearchChange || searchUrlInitializedRef.current || typeof window === 'undefined') return;
    searchUrlInitializedRef.current = true;
    const fromUrl = String(new URLSearchParams(window.location.search).get(searchParam) || '').trim();
    if (!search.trim() && fromUrl) {
      setSearchDraft(fromUrl);
      onSearchChange(fromUrl);
    }
  }, [onSearchChange, search, searchParam]);
  useEffect(() => {
    setSearchDraft(search);
    setPage(0);
  }, [search]);
  useEffect(() => {
    if (!onSearchChange || !searchUrlInitializedRef.current) return;
    updateResourceSearchUrl(searchParam, search);
  }, [onSearchChange, search, searchParam]);
  const normalizedSearch = search.trim();
  const applySearch = (value: string) => {
    const next = value.trim();
    setSearchDraft(value);
    setPage(0);
    updateResourceSearchUrl(searchParam, next);
    onSearchChange?.(next);
  };
  const searchKeyParts = onSearchChange ? [normalizedSearch] : [];
  const resourceQuery = useQuery({
    queryKey: needsProject
      ? projectQueryKey(queryKey, projectId, ...(pagination ? [page, size] : []), ...searchKeyParts)
      : [queryKey, ...(pagination ? [page, size] : []), ...searchKeyParts],
    queryFn: async () => {
      const result = await queryFn(
        projectId,
        pagination ? page : undefined,
        pagination ? size : undefined,
        onSearchChange ? normalizedSearch || undefined : undefined,
      );
      return { rows: listData(result), total: listTotal(result, listData(result).length) };
    },
    enabled: needsProject ? Boolean(projectId) : Boolean(localStorage.getItem('token')),
    staleTime,
    refetchInterval: activeStatuses
      ? (query: any) => {
          const rows = Array.isArray(query.state.data?.rows) ? query.state.data.rows : [];
          const hasActiveWork = rows.some((row: Row) => activeStatuses.has(String(row.status || '').toUpperCase()));
          return isWallboardPollingAllowed({ visible: pageVisible, hasActiveWork }) ? pollInterval : false;
        }
      : false,
    // Keep the current page visible while a new server search/page is loading.
    // This prevents pagination controls from disappearing between requests.
    placeholderData: (previous) => previous,
  });
  const online = typeof navigator === 'undefined' || navigator.onLine;
  const errorCopy = resourceErrorCopy(responseStatus(resourceQuery.error), online);
  const resourceRows = resourceQuery.data?.rows || [];
  const dataState = moduleDataState({
    loading: resourceQuery.isLoading,
    error: resourceQuery.error,
    // Status strips should describe the complete server result, not only the
    // current page. This keeps the count truthful while paging or searching.
    rowCount: resourceQuery.data?.total ?? resourceRows.length,
    online,
  });
  const tableColumns: ColumnsType<Row> = [
        ...columns,
        ...(rowActions ? [{
          title: '操作',
          key: 'actions',
          width: actionColumnWidth ?? 180,
          fixed: 'right' as const,
          render: (_value: unknown, row: Row) => rowActions(row),
        }] : []),
        ...(detailPath ? [{
          title: '详情',
          key: 'detail',
          width: detailColumnWidth ?? 72,
          fixed: 'right' as const,
          render: (_value: unknown, row: Row) => (
            <Button type="link" size="small" icon={<EyeOutlined />} aria-label="查看详情" onClick={() => setSelectedRow(row)} />
          ),
        }] : []),
      ];

  return (
    <>
      {!hideModuleHeader && <ModuleHeader module={module} />}
      {!hideModuleStatus && <ModuleStatus module={module} dataState={dataState} rowCount={resourceQuery.data?.total ?? resourceRows.length} />}
      {beforeTable}
      <TablePanel>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
          <h2 style={{ margin: 0 }}>{titleOverride || MODULE_COPY[module].title}</h2>
          <Space wrap>
            {onSearchChange ? (
              <ServerSearchInput
                value={searchDraft}
                placeholder={searchPlaceholder}
                ariaLabel={searchAriaLabel}
                loading={resourceQuery.isFetching}
                onChange={(value) => {
                  setSearchDraft(value);
                  if (!value) applySearch('');
                }}
                onSearch={applySearch}
              />
            ) : null}
            {headerActions}
          </Space>
        </div>
        <p>{nextOverride || MODULE_COPY[module].next}</p>
        {needsProject && !projectsQuery.isLoading && !projectId ? (
          <Alert style={{ marginTop: 14 }} type="warning" showIcon message="暂无可访问数据" description="当前账号没有可用的数据权限。" />
        ) : resourceQuery.error ? (
          <Alert
            style={{ marginTop: 14 }}
            type={online ? 'error' : 'warning'}
            showIcon
            message={errorCopy.message}
            description={errorCopy.description}
            action={<Button size="small" icon={<ReloadOutlined />} onClick={() => resourceQuery.refetch()}>重试</Button>}
          />
        ) : resourceQuery.data?.rows?.length ? (
          <Table<Row>
            rowKey={(row) => String(row.id ?? JSON.stringify(row))}
            columns={tableColumns}
            dataSource={resourceQuery.data.rows}
            loading={resourceQuery.isFetching}
            pagination={pagination ? {
              current: page + 1,
              pageSize: size,
              total: resourceQuery.data.total,
              showSizeChanger: true,
              // Ant Design renders icon-only previous/next controls by default.
              // Preserve its styling while exposing names to keyboard and AT users.
              itemRender: (_page, type, element) => {
                if (type !== 'prev' && type !== 'next') return element;
                return React.cloneElement(element as React.ReactElement, {
                  'aria-label': type === 'prev' ? '上一页' : '下一页',
                  title: type === 'prev' ? '上一页' : '下一页',
                });
              },
              onChange: (nextPage, nextSize) => {
                setPage(Math.max(0, nextPage - 1));
                if (nextSize !== size) {
                  setSize(nextSize);
                  setPage(0);
                }
              },
            } : false}
            size="small"
            tableLayout={tableLayout}
            scroll={{ x: scrollX ?? 'max-content' }}
          />
        ) : resourceQuery.isLoading ? (
          <div role="status" aria-live="polite" style={{ padding: '28px 0', textAlign: 'center' }}><Spin /></div>
        ) : (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={normalizedSearch ? '没有匹配的结果' : '暂无数据'}
          />
        )}
      </TablePanel>
      {afterTable}
      <ResourceDetailModal module={module} projectId={projectId} row={selectedRow} detailPath={detailPath} detailActions={detailActions} onClose={() => setSelectedRow(null)} />
    </>
  );
}
