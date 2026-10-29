import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Button, Checkbox, InputNumber, Modal, Progress, Select, Typography } from 'antd';

import type { PreannotateJobResponse, YoloPreannotateModelResponse } from '@/api/types';
import type { PreannotationCapabilityItem } from '@/api/types';
import { getPreannotationCapabilities } from '@/api/services/samples';
import {
  AI_DETECTION_DEFAULT_CONF,
  aiDetectionImageSize,
  getDefaultIncludeDoneSamples,
  usePreannotateJob,
} from '@/hooks/usePreannotateJob';
import { formatPreannotateProgressSummary } from '@/hooks/preannotateProgress';
import { isPreannotationCapabilityVisible, normalizePreannotationCapabilities } from '@/utils/preannotationCapabilities';

type PreannotateType = 'yolo' | 'tagtool' | 'locateanything' | 'vlx_seek' | 'rex_omni' | 'ai_detection';
type PreannotateMode = 'task' | 'sample';

interface PreannotateControlProps {
  taskId: number;
  sampleId?: number;
  type: PreannotateType;
  mode: PreannotateMode;
  buttonType?: 'link' | 'text' | 'default';
  disabled?: boolean;
  onProgress?: (job: PreannotateJobResponse | null, type: PreannotateType) => void;
  onCompleted?: (job: PreannotateJobResponse) => void | Promise<void>;
}

export default function PreannotateControl({
  taskId,
  sampleId,
  type,
  mode,
  buttonType = 'link',
  disabled = false,
  onProgress,
  onCompleted,
}: PreannotateControlProps) {
  const [open, setOpen] = useState(false);
  const [providerCapability, setProviderCapability] = useState<PreannotationCapabilityItem | null>(null);
  const [capabilityResolved, setCapabilityResolved] = useState(type !== 'ai_detection');
  const [capabilityVisible, setCapabilityVisible] = useState(type !== 'ai_detection');
  // Task-level pre-annotation covers the whole task by default. The checkbox
  // remains available when an operator intentionally wants to limit a run to
  // newly added samples.
  const [includeDoneSamples, setIncludeDoneSamples] = useState(() => getDefaultIncludeDoneSamples(mode));
  const [conf, setConf] = useState(() => type === 'ai_detection' ? AI_DETECTION_DEFAULT_CONF : 0.25);
  const {
    modelOptions,
    selectedModel,
    selectedRecommendedConf,
    selectedModelKey,
    setSelectedModelKey,
    loadingModels,
    modelsResolved,
    isRunning,
    progress,
    loadModels,
    resumeActive,
    start,
  } = usePreannotateJob({ type, mode });

  const actionText = useMemo(() => {
    if (type === 'yolo') {
      return mode === 'task' ? '批量生成检测框' : '生成检测框';
    }
    if (type === 'locateanything') return mode === 'task' ? '批量生成检测框（LocateAnything）' : '生成检测框（LocateAnything）';
    if (type === 'vlx_seek') return mode === 'task' ? '批量生成检测框（VLX-Seek）' : '生成检测框（VLX-Seek）';
    if (type === 'rex_omni') return mode === 'task' ? '批量生成检测框（Rex-Omni）' : '生成检测框（Rex-Omni）';
    if (type === 'ai_detection') return mode === 'task' ? 'AI批量目标检测' : 'AI目标检测(当前图片)';
    return mode === 'task' ? '批量补全全局标签' : '补全全局标签';
  }, [mode, type]);

  const modalTitle = useMemo(() => {
    if (type === 'yolo') {
      return mode === 'task' ? '批量生成检测框' : '为当前图片生成检测框';
    }
    if (type === 'locateanything') return mode === 'task' ? '批量生成检测框（LocateAnything）' : '为当前图片生成检测框（LocateAnything）';
    if (type === 'vlx_seek') return mode === 'task' ? '批量生成检测框（VLX-Seek）' : '为当前图片生成检测框（VLX-Seek）';
    if (type === 'rex_omni') return mode === 'task' ? '批量生成检测框（Rex-Omni）' : '为当前图片生成检测框（Rex-Omni）';
    if (type === 'ai_detection') return mode === 'task' ? 'AI批量目标检测' : 'AI目标检测(当前图片)';
    return mode === 'task' ? '批量补全全局标签' : '为当前图片补全全局标签';
  }, [mode, type]);

  const requiresModelSelection = type === 'yolo' || type === 'tagtool';
  const aiYoloModel = type === 'ai_detection' ? selectedModel as YoloPreannotateModelResponse | undefined : undefined;
  const missingDetectionLabels = aiYoloModel?.missing_task_labels || [];

  const providerSummary = useMemo(() => {
    if (requiresModelSelection) {
      return '';
    }
    if (type === 'ai_detection') {
      if (loadingModels) return '正在选择兼容的目标检测模型...';
      if (!modelsResolved) return '目标检测模型列表读取失败，请关闭后重新打开';
      if (aiYoloModel) {
        const coverage = aiYoloModel.matched_labels?.length;
        const total = typeof coverage === 'number' ? coverage + missingDetectionLabels.length : undefined;
        return [
          `YOLO · ${aiYoloModel.title}`,
          `输入尺寸 ${aiDetectionImageSize(aiYoloModel)}`,
          `置信度阈值 ${conf.toFixed(2)}`,
          typeof coverage === 'number' ? `覆盖任务类别 ${coverage}/${total}` : '',
        ].filter(Boolean).join(' · ');
      }
    }
    const model = providerCapability?.models?.[0];
    const modelName =
      typeof model === 'string'
        ? model
        : model && typeof model === 'object'
          ? String((model as { model?: unknown; name?: unknown }).model || (model as { name?: unknown }).name || '')
          : '';
    const endpoint = providerCapability?.endpoint
      ? providerCapability.endpoint.replace(/^https?:\/\//, '').replace(/\/.*$/, '')
      : '';
    return [type === 'ai_detection' ? '火山方舟' : providerCapability?.label, modelName, endpoint].filter(Boolean).join(' · ') || '使用系统配置的服务';
  }, [aiYoloModel, conf, loadingModels, missingDetectionLabels.length, modelsResolved, providerCapability, requiresModelSelection, type]);

  const handleOpen = useCallback(() => {
    setOpen(true);
    void loadModels(taskId);
    if (requiresModelSelection) {
      setProviderCapability(null);
      return;
    }
    setProviderCapability(null);
    void getPreannotationCapabilities(taskId)
      .then((response) => {
        const capabilities = normalizePreannotationCapabilities(response.data);
        setProviderCapability(capabilities[type === 'ai_detection' ? 'ai_bbox_detection' : type] || null);
      })
      .catch(() => {
        // The button is already gated by the task page capability check. If
        // the detail lookup fails, keep the provider action usable and show a
        // neutral service description instead of an empty model selector.
        setProviderCapability(null);
      });
  }, [loadModels, requiresModelSelection, taskId, type]);

  // The single-image toolbar is rendered independently of the task page. Keep
  // the Ark detection action hidden until the server confirms that the
  // administrator configured a usable bbox_detection connection; otherwise a
  // dead action would appear for every image task.
  useEffect(() => {
    if (type !== 'ai_detection') {
      setCapabilityResolved(true);
      setCapabilityVisible(true);
      return;
    }
    let disposed = false;
    setCapabilityResolved(false);
    void getPreannotationCapabilities(taskId)
      .then((response) => {
        if (disposed) return;
        const item = normalizePreannotationCapabilities(response.data).ai_bbox_detection;
        setCapabilityVisible(isPreannotationCapabilityVisible(item));
      })
      .catch(() => {
        if (!disposed) setCapabilityVisible(false);
      })
      .finally(() => {
        if (!disposed) setCapabilityResolved(true);
      });
    return () => {
      disposed = true;
    };
  }, [taskId, type]);

  const handleRun = useCallback(async () => {
    await start({
      taskId,
      sampleId,
      includeDoneSamples,
      conf,
      onCompleted: async (job) => {
        await onCompleted?.(job);
        setOpen(false);
      },
    });
  }, [conf, includeDoneSamples, onCompleted, sampleId, start, taskId]);

  const progressPercent = progress
    ? Math.round((progress.processed_count / Math.max(progress.sample_count, 1)) * 100)
    : 0;
  const progressSummary = useMemo(() => {
    if (!progress) {
      return '';
    }
    return formatPreannotateProgressSummary(progress, type);
  }, [progress, type]);

  useEffect(() => {
    onProgress?.(progress, type);
  }, [onProgress, progress, type]);

  useEffect(() => {
    if (type === 'yolo' && selectedRecommendedConf !== null) {
      setConf(selectedRecommendedConf);
    }
  }, [selectedRecommendedConf, type]);

  useEffect(() => {
    // The legacy active-job endpoint is shared by global-tag and bbox runs
    // and does not carry the requested mode. Do not let the detection control
    // adopt an unrelated global-tag job after a page refresh.
    if (mode !== 'task' || type === 'ai_detection') {
      return;
    }
    void resumeActive(taskId, onCompleted);
  }, [mode, onCompleted, resumeActive, taskId]);

  if (type === 'ai_detection' && (!capabilityResolved || !capabilityVisible)) {
    return null;
  }

  return (
    <>
      <Button
        type={buttonType}
        className={`labelu-preannotate-control labelu-preannotate-control-${type}`}
        data-labelu-preannotate-action={type}
        onClick={handleOpen}
        loading={isRunning}
        disabled={disabled || isRunning}
      >
        {actionText}
      </Button>
      <Modal
        title={modalTitle}
        open={open}
        okText={isRunning ? '执行中...' : '开始执行'}
        cancelText="取消"
        onOk={handleRun}
        onCancel={() => setOpen(false)}
        okButtonProps={{
          disabled: (requiresModelSelection && !selectedModel)
            || (type === 'ai_detection' && (!modelsResolved || loadingModels))
            || isRunning,
          loading: isRunning || (type === 'ai_detection' && loadingModels),
        }}
        confirmLoading={isRunning}
        destroyOnClose
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {requiresModelSelection && (
            <div>
              <Typography.Text type="secondary">选择模型</Typography.Text>
              <Select
                style={{ width: '100%', marginTop: 6 }}
                placeholder="请选择模型"
                loading={loadingModels}
                options={modelOptions}
                value={selectedModelKey}
                onChange={setSelectedModelKey}
                notFoundContent={loadingModels ? '加载中...' : '暂无可用模型'}
              />
            </div>
          )}

          {!requiresModelSelection && (
            <div style={{ padding: '10px 12px', border: '1px solid #f0f0f0', borderRadius: 6, background: '#fafafa' }}>
              <Typography.Text type="secondary">服务配置</Typography.Text>
              <Typography.Text style={{ display: 'block', marginTop: 4 }}>{providerSummary || '正在读取服务配置...'}</Typography.Text>
            </div>
          )}

          {type === 'ai_detection' && aiYoloModel && missingDetectionLabels.length > 0 && (
            <Alert
              type="warning"
              showIcon
              message="当前模型未覆盖全部任务类别"
              description={`本次仅检测模型支持的类别，缺少：${missingDetectionLabels.join('、')}。这些类别需要人工补充或使用支持它们的模型。`}
            />
          )}

          {type === 'yolo' && (
            <div>
              <Typography.Text type="secondary">置信度阈值</Typography.Text>
              <InputNumber
                min={0.05}
                max={1}
                step={0.05}
                value={conf}
                onChange={(value) => setConf(Number(value ?? 0.25))}
                style={{ width: '100%', marginTop: 6 }}
              />
            </div>
          )}

          {mode === 'task' && (
            <Checkbox checked={includeDoneSamples} onChange={(event) => setIncludeDoneSamples(event.target.checked)}>
              包含已完成图片（重新处理全部非跳过图片）
            </Checkbox>
          )}

          {progress && (
            <div style={{ minWidth: 0, maxWidth: '100%' }}>
              <Progress
                percent={progressPercent}
                size="small"
                status={progress.failed_count > 0 ? 'exception' : 'active'}
                showInfo={false}
              />
              <Typography.Text
                type="secondary"
                style={{
                  display: 'block',
                  marginTop: 6,
                  whiteSpace: 'normal',
                  overflowWrap: 'anywhere',
                  lineHeight: 1.6,
                }}
              >
                {progressSummary}
              </Typography.Text>
            </div>
          )}
        </div>
      </Modal>
    </>
  );
}
