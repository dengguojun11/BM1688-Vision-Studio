import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { message } from 'antd';

import type { PreannotateJobResponse, YoloPreannotateModelResponse } from '@/api/types';
import {
  createTagtoolPreannotateJob,
  autoLabelSample,
  createAutoLabelJob,
  getActiveAutoLabelJob,
  getAutoLabelJobStatus,
  createProviderPreannotateJob,
  createYoloPreannotateJob,
  getActiveProviderPreannotateJob,
  getActiveTagtoolPreannotateJob,
  getActiveYoloPreannotateJob,
  getTagtoolPreannotateJobStatus,
  getTagtoolPreannotateModels,
  getYoloPreannotateJobStatus,
  getYoloPreannotateModels,
  getProviderPreannotateJobStatus,
  type ProviderPreannotateType,
} from '@/api/services/samples';
import {
  modelLabel,
  recommendedConf,
  sortPreannotateModels,
  type PreannotateModel,
  type PreannotateType,
} from './preannotateModelOptions';
import { getYoloPreannotateCounts } from './preannotateProgress';

type PreannotateMode = 'task' | 'sample';

/** Task-level actions target the whole task by default. */
export const DEFAULT_INCLUDE_DONE_SAMPLES = true;

export function getDefaultIncludeDoneSamples(mode: PreannotateMode): boolean {
  return mode === 'task' ? DEFAULT_INCLUDE_DONE_SAMPLES : false;
}

interface StartOptions {
  taskId: number;
  sampleId?: number;
  includeDoneSamples?: boolean;
  conf?: number;
  onCompleted?: (job: PreannotateJobResponse) => void | Promise<void>;
}

interface PreannotateJobOptions {
  type: PreannotateType;
  mode: PreannotateMode;
}

const POLL_INTERVAL_MS = 1200;
const TYPE_ACTION_TEXT = {
  yolo: '检测框预标注',
  tagtool: '全局标签补全',
  locateanything: 'LocateAnything 检测框预标注',
  vlx_seek: 'VLX-Seek 检测框预标注',
  rex_omni: 'Rex-Omni 检测框预标注',
  ai_detection: 'AI目标检测',
} as const;

const MODEL_SELECTION_TYPES = new Set<PreannotateType>(['yolo', 'tagtool']);
const MODEL_LOADING_TYPES = new Set<PreannotateType>(['yolo', 'tagtool', 'ai_detection']);

export const AI_DETECTION_MIN_IMAGE_SIZE = 2560;
export const AI_DETECTION_DEFAULT_CONF = 0.15;

export function aiDetectionImageSize(model?: PreannotateModel) {
  const value = (model as YoloPreannotateModelResponse | undefined)?.imgsz;
  return Math.max(AI_DETECTION_MIN_IMAGE_SIZE, typeof value === 'number' && Number.isFinite(value) ? value : 0);
}

export function usePreannotateJob({ type, mode }: PreannotateJobOptions) {
  const pollTimerRef = useRef<number | null>(null);
  const [models, setModels] = useState<PreannotateModel[]>([]);
  const [selectedModelKey, setSelectedModelKey] = useState<string>();
  const [progress, setProgress] = useState<PreannotateJobResponse | null>(null);
  const [loadingModels, setLoadingModels] = useState(false);
  const [modelsResolved, setModelsResolved] = useState(false);
  const [isRunning, setIsRunning] = useState(false);

  const selectedModel = useMemo(() => {
    return models.find((model) => `${model.kind}:${model.id}` === selectedModelKey);
  }, [models, selectedModelKey]);

  const selectedRecommendedConf = useMemo(() => {
    return type === 'yolo' && selectedModel ? recommendedConf(selectedModel) : null;
  }, [selectedModel, type]);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current !== null) {
      window.clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const loadModels = useCallback(
    async (taskId: number) => {
      setLoadingModels(true);
      setModelsResolved(false);
      setModels([]);
      if (type === 'ai_detection') setSelectedModelKey(undefined);
      try {
        if (!MODEL_LOADING_TYPES.has(type)) {
          setModels([]);
          setSelectedModelKey(undefined);
          setModelsResolved(true);
          return;
        }
        const response = type === 'tagtool' ? await getTagtoolPreannotateModels(taskId) : await getYoloPreannotateModels(taskId);
        const items = sortPreannotateModels(response.data.items || [], type);
        setModels(items);
        setSelectedModelKey((oldKey) => {
          if (type !== 'ai_detection' && oldKey && items.some((model) => `${model.kind}:${model.id}` === oldKey)) {
            return oldKey;
          }
          // The server ranks compatible detectors. A partial YOLO model still
          // produces materially better geometry than the generic vision
          // fallback; the control surfaces missing task labels for review.
          const first = items[0];
          return first ? `${first.kind}:${first.id}` : undefined;
        });
        setModelsResolved(true);
      } catch (error: any) {
        message.error(error?.response?.data?.msg || '模型列表加载失败');
      } finally {
        setLoadingModels(false);
      }
    },
    [type],
  );

  const pollJob = useCallback(
    async (taskId: number, jobId: number, backend: PreannotateType = type) => {
      return await new Promise<PreannotateJobResponse>((resolve, reject) => {
        stopPolling();
        pollTimerRef.current = window.setInterval(async () => {
          try {
            const response =
              backend === 'ai_detection'
                ? await getAutoLabelJobStatus(taskId, jobId)
                : MODEL_SELECTION_TYPES.has(backend)
                  ? backend === 'yolo'
                    ? await getYoloPreannotateJobStatus(taskId, jobId)
                    : await getTagtoolPreannotateJobStatus(taskId, jobId)
                  : await getProviderPreannotateJobStatus(taskId, backend as ProviderPreannotateType, jobId);
            const job = response.data as PreannotateJobResponse & { status: string };
            setProgress(job);
            const status = String(job.status);
            if (status === 'COMPLETED' || status === 'FAILED' || status === 'CANCELLED') {
              stopPolling();
              resolve(job);
            }
          } catch (error) {
            stopPolling();
            reject(error);
          }
        }, POLL_INTERVAL_MS);
      });
    },
    [stopPolling, type],
  );

  const resumeActive = useCallback(
    async (taskId: number, onCompleted?: (job: PreannotateJobResponse) => void | Promise<void>) => {
      if (mode !== 'task') {
        return;
      }

      try {
        const response =
          type === 'ai_detection'
            ? await getActiveAutoLabelJob(taskId)
            : MODEL_SELECTION_TYPES.has(type)
              ? type === 'yolo'
                ? await getActiveYoloPreannotateJob(taskId)
                : await getActiveTagtoolPreannotateJob(taskId)
              : await getActiveProviderPreannotateJob(taskId, type as ProviderPreannotateType);
        if (!response.data) {
          return;
        }
        setProgress(response.data as PreannotateJobResponse);
        setIsRunning(true);
        const finalJob = await pollJob(taskId, response.data.id);
        setIsRunning(false);
        if (finalJob.status === 'COMPLETED') {
          await onCompleted?.(finalJob);
        }
        setTimeout(() => setProgress(null), 3000);
      } catch {
        setIsRunning(false);
      }
    },
    [mode, pollJob, type],
  );

  const start = useCallback(
    async ({
      taskId,
      sampleId,
      includeDoneSamples = DEFAULT_INCLUDE_DONE_SAMPLES,
      conf = type === 'ai_detection' ? AI_DETECTION_DEFAULT_CONF : 0.25,
      onCompleted,
    }: StartOptions) => {
      const selected = selectedModel;
      if (
        (MODEL_SELECTION_TYPES.has(type) && !selected)
        || (type === 'ai_detection' && (!modelsResolved || loadingModels))
        || isRunning
      ) {
        return;
      }
      const useYolo = type === 'yolo' || (type === 'ai_detection' && Boolean(selected));

      setIsRunning(true);
      setProgress(null);
      try {
        const commonBody = {
          model_id: selected?.id || 0,
          overwrite: true,
          include_done_samples: mode === 'task' ? includeDoneSamples : true,
          sample_ids: mode === 'sample' && sampleId ? [sampleId] : undefined,
          sample_id: mode === 'sample' ? sampleId : undefined,
        };
        if (type === 'ai_detection' && mode === 'sample' && !sampleId) {
          throw new Error('当前图片不存在');
        }
        if (type === 'ai_detection' && mode === 'sample' && !useYolo) {
          if (!sampleId) throw new Error('当前图片不存在');
          const response = await autoLabelSample(taskId, sampleId, {
            filter_by_labels: true,
            include_geometry_tools: true,
            overwrite_existing_annotations: false,
          });
          if (response.data.status !== 'COMPLETED') {
            throw new Error(response.data.warning_message || 'AI目标检测未完成');
          }
          if (response.data.warning_message) {
            message.warning(response.data.warning_message);
          } else {
            message.success('AI目标检测完成');
          }
          await onCompleted?.({
            id: 0,
            task_id: taskId,
            status: 'COMPLETED',
            sample_count: 1,
            processed_count: 1,
            success_count: 1,
            failed_count: 0,
          });
          setIsRunning(false);
          return;
        }
        const response = useYolo
          ? await createYoloPreannotateJob(
              taskId,
              {
                ...commonBody,
                model_kind: selected?.kind as 'imported' | 'trained',
                conf,
                ...(type === 'ai_detection' ? {
                  imgsz: aiDetectionImageSize(selected),
                  agnostic_nms: true,
                  // A current-image action is an explicit request to replace
                  // the visible geometry preview. It still writes only a
                  // pre-annotation; formal sample data changes only after
                  // the annotator reviews and saves it. Batch runs stay
                  // protective of existing completed annotations.
                  apply_to_canvas_overwrite: mode === 'sample',
                } : {}),
              },
              sampleId,
            )
          : type === 'ai_detection'
            ? await createAutoLabelJob(taskId, {
                mode: 'BOX_AND_TAGS',
                filter_by_labels: true,
                include_geometry_tools: true,
                include_done_samples: mode === 'task' ? includeDoneSamples : true,
                overwrite_existing_annotations: false,
                skip_complete_global_tags: false,
              })
            : type === 'tagtool'
              ? await createTagtoolPreannotateJob(
                  taskId,
                  {
                    ...commonBody,
                    model_kind: 'imported',
                  },
                  sampleId,
                )
              : await createProviderPreannotateJob(taskId, type as ProviderPreannotateType, {
                  include_done_samples: mode === 'task' ? includeDoneSamples : true,
                  overwrite: true,
                  sample_ids: mode === 'sample' && sampleId ? [sampleId] : undefined,
                  sample_id: mode === 'sample' ? sampleId : undefined,
                });
        setProgress(response.data as PreannotateJobResponse);
        message.success(`${TYPE_ACTION_TEXT[type]}任务已开始`);
        const finalJob = await pollJob(taskId, response.data.id, useYolo ? 'yolo' : type);
        setIsRunning(false);
        if (finalJob.status === 'COMPLETED') {
          if (useYolo) {
            const { annotatedCount, emptyCount } = getYoloPreannotateCounts(finalJob);
            message.success(
              `检测框生成完成：实际有框 ${annotatedCount} 张，零框 ${emptyCount} 张，处理失败 ${finalJob.failed_count} 张`,
            );
          } else {
            message.success(
              `${TYPE_ACTION_TEXT[type]}完成，成功 ${finalJob.success_count} 个，失败 ${finalJob.failed_count} 个`,
            );
          }
          await onCompleted?.(finalJob);
        } else {
          message.error(finalJob.error_message || `${TYPE_ACTION_TEXT[type]}失败`);
        }
        setTimeout(() => setProgress(null), 3000);
      } catch (error: any) {
        setIsRunning(false);
        message.error(error?.response?.data?.msg || error?.message || `${TYPE_ACTION_TEXT[type]}失败`);
      }
    },
    [isRunning, loadingModels, mode, modelsResolved, pollJob, selectedModel, type],
  );

  useEffect(() => {
    return () => stopPolling();
  }, [stopPolling]);

  return {
    models,
    modelOptions: models.map((model) => ({
      label: modelLabel(model, type),
      value: `${model.kind}:${model.id}`,
    })),
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
  };
}
