import { useEffect, useCallback, useContext, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate, useParams, useRevalidator, useRouteLoaderData, useSearchParams } from 'react-router-dom';
import { Button, Checkbox, Modal, Pagination, Popconfirm, Select, Tooltip } from 'antd';
import _, { debounce } from 'lodash-es';
import { set } from 'lodash/fp';
import { useTranslation } from '@labelu/i18n';
import { useHotkeys } from 'react-hotkeys-hook';
import { FlexLayout } from '@labelu/components-react';
import { ArrowLeftOutlined, CloseOutlined, QuestionCircleOutlined } from '@ant-design/icons';

import { ReactComponent as SparklesIcon } from '@/assets/svg/spark.svg';
import commonController from '@/utils/common';
import {
  CANVAS_PRE_ANNOTATION_MANUAL_LOADED,
  PRE_ANNOTATION_COMPLETED_EVENT,
  imageAnnotationRef,
  videoAnnotationRef,
  audioAnnotationRef,
} from '@/pages/tasks.[id].samples.[id]';
import type {
  SampleListResponse,
  SampleResponse,
  TaskRectPresetSummaryItem,
  TaskResponseWithStatics,
} from '@/api/types';
import { MediaType, SampleState } from '@/api/types';
import type { getSample } from '@/api/services/samples';
import {
  autoLabelSample,
  copySampleToTask,
  deleteSamples,
  getSamples,
  updateSampleState,
  updateSampleAnnotationResult,
} from '@/api/services/samples';
import { getPreAnnotations } from '@/api/services/preAnnotations';
import { getTaskList, getTaskRectPresetSummary } from '@/api/services/task';
import queryClient from '@/api/queryClient';
import { sampleKey } from '@/api/queryKeyFactories';
import { taskKey } from '@/api/queryKeyFactories/task';
import { preAnnotationKey } from '@/api/queryKeyFactories/preAnnotation';
import { message } from '@/StaticAnt';
import useMe from '@/hooks/useMe';
import { UserAvatar } from '@/components/UserAvatar';
import PreannotateControl from '@/components/PreannotateControl';
import ServerSearchInput from '@/components/ServerSearchInput';
import { generateDefaultValues } from '@/utils/generateGlobalToolDefaultValues';
import { scaleImageAnnotationsToOriginal } from '@/utils/convertImageSample';

import AnnotationContext from '../../annotation.context';
import {
  getImageBackgroundSaveFingerprint,
  runImageBackgroundSaveOnce,
  shouldSkipUnchangedImageBackgroundSave,
} from './imageBackgroundSave';
import { resolveDeleteNavigationTarget } from './deleteNavigation';
import { cacheSampleDetail, prefetchUpcomingSample, warmSampleDetailCacheFromWindow } from '../../annotationPrefetch';
import { hasRectAnnotations, isExplicitNegativeSample, withExplicitNegativeSample } from './negativeSample';
import { AiAnnotateRow, ImageDeleteOverlay, NegativeSampleControl, ToolbarActionsRow } from './style';
import SamplePreAnnotationUpload from './SamplePreAnnotationUpload';
import { shouldSaveBeforeSampleNavigation } from './navigationSavePolicy';

type TranslationHook = () => { t: (key: string, options?: Record<string, unknown>) => string };
const useTranslationCompat = useTranslation as unknown as TranslationHook;

interface AnnotationRightCornerProps {
  // 用于标注预览
  noSave?: boolean;

  fetchNext?: () => Promise<SampleResponse[] | void>;

  totalSize: number;

  isLastPage: boolean;
}

const COPY_TASK_PAGE_SIZE = 20;

export const SAMPLE_CHANGED = 'sampleChanged';
export const SAMPLE_NAVIGATION_PENDING = 'sampleNavigationPending';

type ImageAnnotatorRefWithSnapshot = {
  /** Added by the local image annotator build; older package declarations omit it. */
  getAnnotationsSnapshot?: () => any;
};

function getAnnotationCount(_result: string | object) {
  const resultParsed = typeof _result !== 'object' ? JSON.parse(_result as string) : _result;
  let result = 0;

  for (const key in resultParsed) {
    if (key.indexOf('Tool') > -1 && key !== 'textTool' && key !== 'tagTool') {
      const tool = resultParsed[key];

      if (!tool.result) {
        let temp = 0;

        if (tool.length) {
          temp = tool.length;
        }

        result = result + temp;
      } else {
        result = result + tool.result.length;
      }
    }
  }

  return result;
}

export interface AnnotationLoaderData {
  sample: SampleResponse;
  samples: SampleListResponse;
}

const exportImageAnnotations = (annotationsWithGlobal: Record<string, any>): Partial<Record<string, any[]>> => {
  const result: Partial<Record<string, any[]>> = {};

  Object.values(annotationsWithGlobal).forEach((item: any) => {
    const { visible, tool, ...rest } = item;
    const toolName = tool || item.type;

    if (!toolName) {
      return;
    }

    if (!result[toolName]) {
      result[toolName] = [];
    }

    result[toolName]!.push(rest);
  });

  return result;
};

const AnnotationRightCorner = ({ noSave, fetchNext, totalSize, isLastPage }: AnnotationRightCornerProps) => {
  const navigate = useNavigate();
  const routeParams = useParams();
  const revalidator = useRevalidator();
  const taskId = routeParams.taskId;
  const sampleId = routeParams.sampleId;
  const { samples, setSamples, task, currentEditingUser, isStart, fetchPreviousSamples, isCompareAccessMode } =
    useContext(AnnotationContext);
  const sampleIndex = _.findIndex(samples, (sample: SampleResponse) => sample.id === +sampleId!);
  const isCurrentSampleLoaded = sampleIndex >= 0;
  const isLastSample = isCurrentSampleLoaded && sampleIndex === samples.length - 1;
  const isFirstSample = isCurrentSampleLoaded && sampleIndex === 0;
  const canGoPrevious = isCurrentSampleLoaded && (!isFirstSample || !isStart);
  const handlePrefetchNext = useCallback(() => {
    if (task?.media_type !== MediaType.IMAGE || !taskId || sampleIndex < 0 || isLastSample) {
      return;
    }

    prefetchUpcomingSample(+taskId, samples, sampleIndex);
  }, [isLastSample, sampleIndex, samples, task?.media_type, taskId]);
  const routeSample = (useRouteLoaderData('annotation') as any).sample as Awaited<ReturnType<typeof getSample>>;
  const currentSample = routeSample.data;
  const isSampleSkipped = currentSample?.state === SampleState.SKIPPED;
  const [searchParams] = useSearchParams();
  const compareAccessToken = searchParams.get('compare_access_token') || undefined;
  const { t } = useTranslationCompat();
  const me = useMe();
  const isMeTheCurrentUser = currentEditingUser && me.data && currentEditingUser?.user_id === me.data?.id;
  const [isAutoLabeling, setIsAutoLabeling] = useState(false);
  const [isActionPending, setIsActionPending] = useState(false);
  const [isNavigatingSample, setIsNavigatingSample] = useState(false);
  const [copyModalOpen, setCopyModalOpen] = useState(false);
  const [isCopying, setIsCopying] = useState(false);
  const [copyPresetSlug, setCopyPresetSlug] = useState<string>();
  const [copyTargetTaskId, setCopyTargetTaskId] = useState<number>();
  const [rectPresetOptions, setRectPresetOptions] = useState<TaskRectPresetSummaryItem[]>([]);
  const [copyTaskOptions, setCopyTaskOptions] = useState<TaskResponseWithStatics[]>([]);
  const [copyTaskSearchDraft, setCopyTaskSearchDraft] = useState('');
  const [copyTaskSearch, setCopyTaskSearch] = useState('');
  const [copyTaskPage, setCopyTaskPage] = useState(0);
  const [copyTaskTotal, setCopyTaskTotal] = useState(0);
  const [copyTaskLoading, setCopyTaskLoading] = useState(false);
  const copyTaskRequestSequence = useRef(0);
  const [imageDeleteHost, setImageDeleteHost] = useState<HTMLElement | null>(null);
  const [isNegativeSample, setIsNegativeSample] = useState(() => isExplicitNegativeSample(currentSample));
  const isGlobalLoading = isActionPending || isAutoLabeling;
  const isNavigationLoading = isGlobalLoading || isNavigatingSample;
  const supportsNegativeSample =
    task?.media_type === MediaType.IMAGE && task?.config?.tools?.some((tool) => tool.tool === 'rectTool');

  useEffect(() => {
    setIsNegativeSample(isExplicitNegativeSample(currentSample));
  }, [currentSample]);

  const fetchCurrentCanvasPreAnnotation = useCallback(async () => {
    const sampleFilename = routeSample?.data?.file?.filename;

    if (!taskId || !sampleFilename) {
      return;
    }

    if (compareAccessToken) {
      return;
    }

    await queryClient.fetchQuery({
      queryKey: preAnnotationKey.listWithSampleName({
        task_id: +taskId,
        sample_name: sampleFilename,
        canvas_only: true,
      }),
      queryFn: () =>
        getPreAnnotations({
          task_id: +taskId,
          sample_name: sampleFilename,
          canvas_only: true,
        }),
    });
    document.dispatchEvent(
      new CustomEvent(CANVAS_PRE_ANNOTATION_MANUAL_LOADED, { detail: { sampleId: currentSample.id } }),
    );
  }, [compareAccessToken, currentSample.id, routeSample?.data?.file?.filename, taskId]);

  const withActionPending = useCallback(
    async (action: () => Promise<void>) => {
      if (isActionPending) {
        return;
      }

      setIsActionPending(true);
      try {
        await action();
      } finally {
        setIsActionPending(false);
      }
    },
    [isActionPending],
  );

  const navigateWithSearch = useCallback(
    (to: string) => {
      const searchStr = searchParams.toString();

      if (searchStr) {
        navigate(`${to}?${searchStr}`);
      } else {
        navigate(to);
      }
    },
    [navigate, searchParams],
  );

  const validateNegativeSampleResult = useCallback(
    (result: unknown) => {
      if (!isNegativeSample || !hasRectAnnotations(result)) {
        return true;
      }
      message.warning('本图已标记为无目标，请先删除全部矩形框或取消负样本标记');
      return false;
    },
    [isNegativeSample],
  );

  const handleNegativeSampleChange = useCallback((checked: boolean) => {
    if (checked) {
      const canvasResult = imageAnnotationRef.current?.getAnnotations?.() ?? {};
      if (hasRectAnnotations(canvasResult)) {
        message.warning('当前图片已有矩形框，删除全部矩形框后才能标记为无目标');
        return;
      }
    }
    setIsNegativeSample(checked);
  }, []);

  useEffect(() => {
    if (task?.media_type !== MediaType.IMAGE || noSave) {
      setImageDeleteHost(null);
      return;
    }

    let cancelled = false;
    let cleanup: (() => void) | undefined;

    const bindHost = () => {
      const canvas = document.querySelector('.labelu-image-annotator canvas') as HTMLCanvasElement | null;
      const fallbackHost = document.querySelector('.labelu-image__sidebar')?.nextElementSibling as HTMLElement | null;
      const host = (canvas?.parentElement as HTMLElement | null) || fallbackHost;

      if (!host) {
        return false;
      }

      const originalInlinePosition = host.style.position;
      if (window.getComputedStyle(host).position === 'static') {
        host.style.position = 'relative';
      }

      cleanup = () => {
        if (originalInlinePosition === '') {
          host.style.removeProperty('position');
        } else {
          host.style.position = originalInlinePosition;
        }
      };

      if (!cancelled) {
        setImageDeleteHost(host);
      }
      return true;
    };

    if (!bindHost()) {
      const timer = window.setTimeout(() => {
        bindHost();
      }, 80);
      cleanup = () => window.clearTimeout(timer);
    }

    return () => {
      cancelled = true;
      cleanup?.();
    };
  }, [noSave, sampleId, task?.media_type]);

  const saveCurrentSample = useCallback(async () => {
    if (
      currentSample?.state === SampleState.SKIPPED ||
      noSave ||
      !task?.media_type ||
      // 非当前用户标注的文件，不保存
      !isMeTheCurrentUser
    ) {
      return false;
    }

    const result: any = {};
    let innerSample: any;

    if (task.media_type === MediaType.IMAGE) {
      // @ts-ignore
      const exportedResult = imageAnnotationRef.current?.getAnnotations() ?? {};
      const engine = imageAnnotationRef.current?.getEngine();

      innerSample = imageAnnotationRef?.current?.getSample();

      const originalWidth = innerSample?.meta?.originalWidth ?? engine?.backgroundRenderer?.image?.width ?? 0;
      const originalHeight = innerSample?.meta?.originalHeight ?? engine?.backgroundRenderer?.image?.height ?? 0;
      const displayWidth = engine?.backgroundRenderer?.image?.width ?? originalWidth;
      const displayHeight = engine?.backgroundRenderer?.image?.height ?? originalHeight;
      const originalAnnotations = scaleImageAnnotationsToOriginal(
        exportedResult,
        originalWidth,
        originalHeight,
        displayWidth,
        displayHeight,
      );

      result.width = originalWidth;
      result.height = originalHeight;
      result.rotate = engine?.backgroundRenderer?.rotate ?? 0;

      Object.entries(originalAnnotations).forEach(([item, annotations]) => {
        if (annotations?.length) {
          result[item + 'Tool'] = {
            toolName: item + 'Tool',
            result: annotations,
          };
        }
      });
    } else if (task.media_type === MediaType.VIDEO) {
      const videoAnnotations = await videoAnnotationRef.current?.getAnnotations();
      const player = await videoAnnotationRef.current?.getPlayer();

      const appendMediaAnnotations = (sourceKey: string, resultKey: string) => {
        const values = (videoAnnotations as Record<string, unknown[] | undefined> | undefined)?.[sourceKey];
        if (!Array.isArray(values)) {
          return;
        }

        if (!result[resultKey]) {
          result[resultKey] = { toolName: resultKey, result: [] };
        }
        result[resultKey].result.push(...values);
      };

      result.width = player?.videoWidth();
      result.height = player?.videoHeight();
      result.duration = player?.duration();

      Object.keys(videoAnnotations ?? {}).forEach((toolName) => {
        if (toolName === 'tag') {
          appendMediaAnnotations(toolName, 'tagTool');
        }

        if (toolName === 'text') {
          appendMediaAnnotations(toolName, 'textTool');
        }

        if (toolName === 'frame') {
          appendMediaAnnotations(toolName, 'videoFrameTool');
        }

        if (toolName === 'segment') {
          appendMediaAnnotations(toolName, 'videoSegmentTool');
        }
      });

      innerSample = await videoAnnotationRef?.current?.getSample();
    } else if (task.media_type === MediaType.AUDIO) {
      const audioAnnotations = await audioAnnotationRef.current?.getAnnotations();
      const player = await audioAnnotationRef.current?.getPlayer();

      const appendMediaAnnotations = (sourceKey: string, resultKey: string) => {
        const values = (audioAnnotations as Record<string, unknown[] | undefined> | undefined)?.[sourceKey];
        if (!Array.isArray(values)) {
          return;
        }

        if (!result[resultKey]) {
          result[resultKey] = { toolName: resultKey, result: [] };
        }
        result[resultKey].result.push(...values);
      };

      result.duration = player?.getDuration();

      Object.keys(audioAnnotations ?? {}).forEach((toolName) => {
        if (toolName === 'tag') {
          appendMediaAnnotations(toolName, 'tagTool');
        }

        if (toolName === 'text') {
          appendMediaAnnotations(toolName, 'textTool');
        }

        if (toolName === 'frame') {
          appendMediaAnnotations(toolName, 'audioFrameTool');
        }

        if (toolName === 'segment') {
          appendMediaAnnotations(toolName, 'audioSegmentTool');
        }
      });

      innerSample = await audioAnnotationRef?.current?.getSample();
    }

    // 全局标注没有值的话，填充默认值
    const tagConfig = task.config.tools.find((tool) => tool.tool === 'tagTool');
    if (!result.tagTool?.result?.length && tagConfig) {
      result.tagTool = {
        toolName: 'tagTool',
        result: generateDefaultValues(tagConfig?.config.attributes),
      };
    }

    const textConfig = task.config.tools.find((tool) => tool.tool === 'textTool');
    if (!result.textTool?.result?.length && textConfig) {
      result.textTool = {
        toolName: 'textTool',
        result: generateDefaultValues(textConfig?.config.attributes),
      };
    }

    // 防止sampleid保存错乱，使用标注时传入的sampleid
    if (!innerSample?.id) {
      return;
    }

    if (!validateNegativeSampleResult(result)) {
      return false;
    }
    const body = withExplicitNegativeSample(
      set('data.result')(JSON.stringify(result))(currentSample),
      isNegativeSample,
    );

    const savedSample = {
      ...body,
      annotated_count: getAnnotationCount(body.data!.result),
      state: SampleState.DONE,
    };

    const updateResponse = (await updateSampleAnnotationResult(+taskId!, +innerSample.id!, savedSample, {
      compareAccessToken,
      taskContractRevision: task.annotation_contract_revision,
    })) as any;
    const updatedSample = (updateResponse?.inner_id ? updateResponse : updateResponse?.data) ?? savedSample;
    const sampleDetailKeys = [sampleKey.detail(innerSample.id), sampleKey.detail(String(innerSample.id))];

    setSamples(
      samples.map((sample: SampleResponse) =>
        sample.id === innerSample.id ? { ...sample, ...updatedSample } : sample,
      ),
    );
    sampleDetailKeys.forEach((queryKey) => queryClient.setQueryData(queryKey, { data: updatedSample }));
    return true;
  }, [
    currentSample,
    isMeTheCurrentUser,
    noSave,
    samples,
    setSamples,
    task?.config?.tools,
    task?.media_type,
    task?.annotation_contract_revision,
    compareAccessToken,
    isNegativeSample,
    taskId,
    validateNegativeSampleResult,
  ]);

  const handleComplete = useCallback(async () => {
    await withActionPending(async () => {
      // 已跳过样本无需再次保存，允许从最后一张跳过样本正常完成任务。
      const saved = currentSample?.state === SampleState.SKIPPED ? true : await saveCurrentSample();
      if (!saved) {
        return;
      }
      if (isCompareAccessMode) {
        message.success(t('saved'));
        return;
      }
      navigateWithSearch(`/tasks/${taskId}/samples/finished`);
      setTimeout(revalidator.revalidate);
    });
  }, [
    isCompareAccessMode,
    currentSample?.state,
    navigateWithSearch,
    revalidator.revalidate,
    saveCurrentSample,
    t,
    taskId,
    withActionPending,
  ]);

  const saveCurrentImageSampleInBackground = useCallback(async () => {
    if (
      currentSample?.state === SampleState.SKIPPED ||
      noSave ||
      task?.media_type !== MediaType.IMAGE ||
      !isMeTheCurrentUser
    ) {
      return false;
    }

    const sampleToSave = currentSample;
    const taskTools = task.config.tools;
    const snapshot = (imageAnnotationRef.current as ImageAnnotatorRefWithSnapshot | null)?.getAnnotationsSnapshot?.();
    const targetSampleId = Number(snapshot?.sample?.id);

    if (!Number.isFinite(targetSampleId)) {
      return false;
    }

    const canvasResult = exportImageAnnotations(snapshot!.annotationsWithGlobal);
    if (!validateNegativeSampleResult(canvasResult)) {
      return false;
    }
    const negativeSampleSnapshot = isNegativeSample;

    const exportedResult = canvasResult;
    const innerSample = snapshot!.sample;
    const originalWidth = snapshot!.originalWidth ?? innerSample?.meta?.originalWidth ?? 0;
    const originalHeight = snapshot!.originalHeight ?? innerSample?.meta?.originalHeight ?? 0;
    const displayWidth = snapshot!.displayWidth ?? originalWidth;
    const displayHeight = snapshot!.displayHeight ?? originalHeight;
    const rotate = snapshot!.rotate ?? 0;
    const result: any = {
      width: originalWidth,
      height: originalHeight,
      rotate,
    };
    const originalAnnotations = scaleImageAnnotationsToOriginal(
      exportedResult,
      originalWidth,
      originalHeight,
      displayWidth,
      displayHeight,
    );

    Object.keys(originalAnnotations).forEach((item) => {
      if (originalAnnotations?.[item]?.length) {
        result[item + 'Tool'] = {
          toolName: item + 'Tool',
          result: originalAnnotations[item],
        };
      }
    });

    const tagConfig = taskTools.find((tool) => tool.tool === 'tagTool');
    if (!result.tagTool?.result?.length && tagConfig) {
      result.tagTool = {
        toolName: 'tagTool',
        result: generateDefaultValues(tagConfig?.config.attributes),
      };
    }

    const textConfig = taskTools.find((tool) => tool.tool === 'textTool');
    if (!result.textTool?.result?.length && textConfig) {
      result.textTool = {
        toolName: 'textTool',
        result: generateDefaultValues(textConfig?.config.attributes),
      };
    }

    const body = withExplicitNegativeSample(
      set('data.result')(JSON.stringify(result))(sampleToSave),
      negativeSampleSnapshot,
    );
    const savedSample = {
      ...body,
      annotated_count: getAnnotationCount(body.data!.result),
      state: SampleState.DONE,
    };

    if (shouldSkipUnchangedImageBackgroundSave(sampleToSave, savedSample)) {
      return true;
    }

    const fingerprint = getImageBackgroundSaveFingerprint(targetSampleId, savedSample);

    await runImageBackgroundSaveOnce(fingerprint, async () => {
      await Promise.all([
        queryClient.cancelQueries({ queryKey: sampleKey.detail(targetSampleId), exact: true }),
        queryClient.cancelQueries({ queryKey: sampleKey.detail(String(targetSampleId)), exact: true }),
      ]);

      return updateSampleAnnotationResult(+taskId!, +targetSampleId, savedSample, {
        compareAccessToken,
        taskContractRevision: task?.annotation_contract_revision,
      }).then((updateResponse: any) => {
        const updatedSample = (updateResponse?.inner_id ? updateResponse : updateResponse?.data) ?? savedSample;

        setSamples((currentSamples) =>
          currentSamples.map((sample: SampleResponse) =>
            sample.id === targetSampleId ? { ...sample, ...updatedSample } : sample,
          ),
        );
        cacheSampleDetail(targetSampleId, { data: updatedSample } as Awaited<ReturnType<typeof getSample>>);
      });
    });
    return true;
  }, [
    compareAccessToken,
    currentSample,
    isMeTheCurrentUser,
    isNegativeSample,
    noSave,
    setSamples,
    task?.config?.tools,
    task?.media_type,
    task?.annotation_contract_revision,
    taskId,
    validateNegativeSampleResult,
  ]);

  const saveCurrentSampleInBackground = useCallback(async () => {
    if (noSave) {
      return false;
    }

    try {
      if (task?.media_type === MediaType.IMAGE) {
        return await saveCurrentImageSampleInBackground();
      }

      return await saveCurrentSample();
    } catch (error: any) {
      const backendMsg = error?.response?.data?.msg;
      commonController.notificationErrorMessage({ message: backendMsg || '保存当前样本失败' }, 2);
      return false;
    }
  }, [noSave, saveCurrentImageSampleInBackground, saveCurrentSample, task?.media_type]);

  const handleBackToTask = useCallback(async () => {
    if (!taskId) {
      return;
    }

    await withActionPending(async () => {
      const shouldSave =
        !noSave && !isCompareAccessMode && isMeTheCurrentUser && currentSample?.state !== SampleState.SKIPPED;

      if (shouldSave && !(await saveCurrentSampleInBackground())) {
        return;
      }

      navigate(`/tasks/${taskId}/`);
    });
  }, [
    currentSample?.state,
    isCompareAccessMode,
    isMeTheCurrentUser,
    navigate,
    noSave,
    saveCurrentSampleInBackground,
    taskId,
    withActionPending,
  ]);

  const handleCancelSkipSample = useCallback(async () => {
    if (noSave || !isMeTheCurrentUser) {
      return;
    }

    await withActionPending(async () => {
      await updateSampleState(
        {
          task_id: +taskId!,
          sample_id: +sampleId!,
          ...(compareAccessToken ? { compare_access_token: compareAccessToken } : {}),
        },
        {
          ...currentSample,
          state: SampleState.DONE,
          ...(task?.annotation_contract_revision
            ? { expected_task_contract_revision: task.annotation_contract_revision }
            : {}),
        },
      );

      setSamples(
        samples.map((sample: SampleResponse) =>
          sample.id === +sampleId! ? { ...sample, state: SampleState.NEW } : sample,
        ),
      );

      revalidator.revalidate();
    });
  }, [
    compareAccessToken,
    currentSample,
    isMeTheCurrentUser,
    noSave,
    revalidator,
    sampleId,
    samples,
    setSamples,
    taskId,
    task?.annotation_contract_revision,
    withActionPending,
  ]);

  const handleSkipSample = useCallback(async () => {
    if (noSave || !isMeTheCurrentUser || !isCurrentSampleLoaded || isCompareAccessMode) {
      return;
    }

    await withActionPending(async () => {
      await updateSampleState(
        {
          task_id: +taskId!,
          sample_id: +sampleId!,
          ...(compareAccessToken ? { compare_access_token: compareAccessToken } : {}),
        },
        {
          ...currentSample,
          state: SampleState.SKIPPED,
          ...(task?.annotation_contract_revision
            ? { expected_task_contract_revision: task.annotation_contract_revision }
            : {}),
        },
      );

      setSamples(
        samples.map((sample: SampleResponse) =>
          sample.id === +sampleId! ? { ...sample, state: SampleState.SKIPPED } : sample,
        ),
      );

      // 切换到下一个文件
      if (!isLastSample) {
        navigateWithSearch(`/tasks/${taskId}/samples/${_.get(samples, `[${sampleIndex + 1}].id`)}`);
      } else if (!isLastPage && samples.length < totalSize) {
        const nextSamples = await fetchNext?.();
        const nextSampleId = nextSamples?.[0]?.id;
        if (nextSampleId) {
          navigateWithSearch(`/tasks/${taskId}/samples/${nextSampleId}`);
        }
      } else {
        navigateWithSearch(`/tasks/${taskId}/samples/finished`);
      }
    });
  }, [
    compareAccessToken,
    currentSample,
    fetchNext,
    isCurrentSampleLoaded,
    isCompareAccessMode,
    isLastPage,
    isLastSample,
    isMeTheCurrentUser,
    navigateWithSearch,
    noSave,
    sampleId,
    sampleIndex,
    samples,
    setSamples,
    taskId,
    task?.annotation_contract_revision,
    totalSize,
    withActionPending,
  ]);

  const handleDeleteCurrentSample = useCallback(async () => {
    if (noSave || !isMeTheCurrentUser || !isCurrentSampleLoaded || isCompareAccessMode || !taskId || !sampleId) {
      return;
    }

    await withActionPending(async () => {
      await deleteSamples({ task_id: +taskId }, { sample_ids: [+sampleId] });

      const remainingSamples = samples.filter((sample: SampleResponse) => sample.id !== +sampleId);
      setSamples(remainingSamples);

      let nextFetchedSampleId: number | undefined;
      if (!isLastPage && samples.length < totalSize) {
        const nextSamples = await fetchNext?.();
        nextFetchedSampleId = nextSamples?.[0]?.id;
      }

      let previousFetchedSampleId: number | undefined;
      if (sampleIndex === 0 && canGoPrevious) {
        const previousSamples = await fetchPreviousSamples?.();
        previousFetchedSampleId = previousSamples?.[previousSamples.length - 1]?.id;
      }

      const navigationTarget = resolveDeleteNavigationTarget({
        taskId,
        samples,
        sampleIndex,
        nextFetchedSampleId,
        previousFetchedSampleId,
      });

      if (navigationTarget.source) {
        const siblingSample =
          navigationTarget.source === 'next'
            ? (samples[sampleIndex + 1] as SampleResponse | undefined)
            : (samples[sampleIndex - 1] as SampleResponse | undefined);
        warmSampleDetailCacheFromWindow(siblingSample);
      }

      navigateWithSearch(navigationTarget.path);
    });
  }, [
    canGoPrevious,
    fetchNext,
    fetchPreviousSamples,
    isCompareAccessMode,
    isCurrentSampleLoaded,
    isLastPage,
    isMeTheCurrentUser,
    navigateWithSearch,
    noSave,
    sampleId,
    sampleIndex,
    samples,
    setSamples,
    taskId,
    totalSize,
    withActionPending,
  ]);

  const handleNextSample = useCallback(async () => {
    if (isCompareAccessMode || !taskId || !sampleId || isNavigatingSample) {
      return;
    }

    setIsNavigatingSample(true);
    document.dispatchEvent(new CustomEvent(SAMPLE_NAVIGATION_PENDING));
    try {
      await withActionPending(async () => {
      // 跳过状态已经由“跳过”操作持久化，不需要再次保存画布。
      // 之前这里仍调用保存函数，而保存函数对 SKIPPED 返回 false，导致
      // 直接打开一个已跳过样本时点击“下一个”没有任何导航反应。
      if (
        shouldSaveBeforeSampleNavigation({
          sampleState: currentSample?.state,
          noSave,
          isCompareAccessMode,
          isCurrentUser: !!isMeTheCurrentUser,
        })
      ) {
        void saveCurrentSampleInBackground().catch(() => undefined);
      }

      // 窗口内有下一张 → 直接导航
      if (isCurrentSampleLoaded && sampleIndex < samples.length - 1) {
        const nextSample = _.get(samples, `[${sampleIndex + 1}]`) as SampleResponse | undefined;
        const nextId = nextSample?.id;
        if (nextId) {
          warmSampleDetailCacheFromWindow(nextSample);
          document.dispatchEvent(new CustomEvent(SAMPLE_CHANGED, { detail: { source: 'next' } }));
          navigateWithSearch(`/tasks/${taskId}/samples/${nextId}`);
          return;
        }
      }

      // 路由样本尚未进入窗口或游标已失效时，先尝试窗口游标，再按当前 ID 直接查询。
      const nextSamples = await fetchNext?.();
      const nextSampleId = nextSamples?.[0]?.id;
      if (nextSampleId && nextSampleId !== Number(sampleId)) {
        warmSampleDetailCacheFromWindow(nextSamples[0]);
        document.dispatchEvent(new CustomEvent(SAMPLE_CHANGED, { detail: { source: 'next' } }));
        navigateWithSearch(`/tasks/${taskId}/samples/${nextSampleId}`);
        return;
      }

      const directNext = await getSamples({
        task_id: +taskId,
        after: +sampleId,
        size: 1,
      });
      const directNextId = directNext.data?.[0]?.id;
      if (directNextId) {
        warmSampleDetailCacheFromWindow(directNext.data[0]);
        document.dispatchEvent(new CustomEvent(SAMPLE_CHANGED, { detail: { source: 'next' } }));
        navigateWithSearch(`/tasks/${taskId}/samples/${directNextId}`);
        return;
      }

      navigateWithSearch(`/tasks/${taskId}/samples/finished`);
      setTimeout(revalidator.revalidate);
      });
    } catch {
      setIsNavigatingSample(false);
      message.error('下一张加载失败，请重试');
    }
  }, [
    isCompareAccessMode,
    sampleIndex,
    samples,
    totalSize,
    isLastPage,
    isCurrentSampleLoaded,
    currentSample?.state,
    isMeTheCurrentUser,
    noSave,
    fetchNext,
    sampleId,
    navigateWithSearch,
    taskId,
    saveCurrentSampleInBackground,
    isNavigatingSample,
    revalidator.revalidate,
    withActionPending,
  ]);

  useEffect(() => {
    setIsNavigatingSample(false);
  }, [sampleId]);

  const handlePrevSample = useCallback(async () => {
    if (!canGoPrevious || isCompareAccessMode) {
      return;
    }

    await withActionPending(async () => {
      if (
        shouldSaveBeforeSampleNavigation({
          sampleState: currentSample?.state,
          noSave,
          isCompareAccessMode,
          isCurrentUser: !!isMeTheCurrentUser,
        }) &&
        !(await saveCurrentSampleInBackground())
      ) {
        return;
      }

      // 窗口内有上一张 → 直接导航
      if (sampleIndex > 0) {
        const prevSample = _.get(samples, `[${sampleIndex - 1}]`) as SampleResponse | undefined;
        const prevId = prevSample?.id;
        if (prevId) {
          warmSampleDetailCacheFromWindow(prevSample);
          document.dispatchEvent(new CustomEvent(SAMPLE_CHANGED, { detail: { source: 'prev' } }));
          navigateWithSearch(`/tasks/${taskId}/samples/${prevId}`);
          return;
        }
      }

      // 窗口开头，拉上一批
      const previousSamples = await fetchPreviousSamples?.();
      const previousSampleId = previousSamples?.[previousSamples.length - 1]?.id;
      if (previousSampleId) {
        navigateWithSearch(`/tasks/${taskId}/samples/${previousSampleId}`);
      }
    });
  }, [
    canGoPrevious,
    currentSample?.state,
    isMeTheCurrentUser,
    noSave,
    fetchPreviousSamples,
    isCompareAccessMode,
    navigateWithSearch,
    samples,
    sampleIndex,
    saveCurrentSampleInBackground,
    taskId,
    withActionPending,
  ]);

  const handleAutoLabel = useCallback(async () => {
    if (noSave || !isMeTheCurrentUser || !taskId || !sampleId || task?.media_type !== MediaType.IMAGE) {
      return;
    }

    await withActionPending(async () => {
      setIsAutoLabeling(true);
      try {
        if (!noSave) {
          const saved = await saveCurrentSample();
          if (!saved) {
            return;
          }
        }

        const response = await autoLabelSample(
          +taskId,
          +sampleId,
          {
            overwrite: true,
            filter_by_labels: true,
            include_geometry_tools: false,
            overwrite_existing_annotations: false,
          },
          compareAccessToken ? { compareAccessToken } : undefined,
        );
        await fetchCurrentCanvasPreAnnotation();
        await revalidator.revalidate();
        if (response.data.warning_message) {
          message.warning(response.data.warning_message);
        } else {
          message.success(t('aiAutoLabelSuccess'));
        }
      } catch (error: any) {
        const backendMsg = error?.response?.data?.msg;
        commonController.notificationErrorMessage({ message: backendMsg || t('aiAutoLabelFailed') }, 2);
      } finally {
        setIsAutoLabeling(false);
      }
    });
  }, [
    compareAccessToken,
    isMeTheCurrentUser,
    fetchCurrentCanvasPreAnnotation,
    noSave,
    saveCurrentSample,
    revalidator,
    sampleId,
    t,
    task?.media_type,
    taskId,
    withActionPending,
  ]);

  const loadCopyPresetOptions = useCallback(async () => {
    const resp = await queryClient.fetchQuery({
      queryKey: taskKey.rectPresetSummary(),
      queryFn: () => getTaskRectPresetSummary(),
      staleTime: 60 * 1000,
    });
    const presets = (resp?.data?.presets ?? []).filter((item) => item.count > 0);
    setRectPresetOptions(presets);
    return presets;
  }, []);

  const loadCopyTaskOptions = useCallback(
    async (presetSlug: string, page = 0, search = '') => {
      const sequence = ++copyTaskRequestSequence.current;
      const normalizedPage = Math.max(0, Number(page) || 0);
      const normalizedSearch = search.trim();
      if (!presetSlug) {
        setCopyTaskOptions([]);
        setCopyTaskTotal(0);
        setCopyTargetTaskId(undefined);
        return;
      }
      setCopyTaskLoading(true);
      try {
        const resp = await getTaskList({
          page: normalizedPage + 1,
          size: COPY_TASK_PAGE_SIZE,
          media_type: MediaType.IMAGE,
          rect_preset_slug: presetSlug,
          q: normalizedSearch || undefined,
        });
        if (sequence !== copyTaskRequestSequence.current) return;
        const currentTaskId = Number(taskId);
        const nextTasks = (resp.data.data ?? []).filter(
          (item: TaskResponseWithStatics) => item.id && item.id !== currentTaskId,
        );
        setCopyTaskOptions(nextTasks);
        setCopyTaskTotal(Number(resp.data.meta_data?.total ?? nextTasks.length));
        setCopyTaskPage(normalizedPage);
        setCopyTargetTaskId((prev) => (prev && nextTasks.some((item) => item.id === prev) ? prev : nextTasks[0]?.id));
      } catch {
        if (sequence === copyTaskRequestSequence.current) {
          setCopyTaskOptions([]);
          setCopyTaskTotal(0);
          setCopyTargetTaskId(undefined);
          message.error('目标任务加载失败，请重试');
        }
      } finally {
        if (sequence === copyTaskRequestSequence.current) setCopyTaskLoading(false);
      }
    },
    [taskId],
  );

  const openCopyModal = useCallback(async () => {
    if (!taskId || !sampleId) {
      return;
    }
    try {
      const presets = await loadCopyPresetOptions();
      const initialSlug = copyPresetSlug || presets[0]?.slug;
      setCopyPresetSlug(initialSlug);
      setCopyTaskSearchDraft('');
      setCopyTaskSearch('');
      setCopyTaskPage(0);
      setCopyModalOpen(true);
      if (initialSlug) {
        await loadCopyTaskOptions(initialSlug, 0, '');
      }
    } catch (error: any) {
      const backendMsg = error?.response?.data?.msg;
      commonController.notificationErrorMessage({ message: backendMsg || '加载复制目标失败' }, 2);
    }
  }, [copyPresetSlug, loadCopyPresetOptions, loadCopyTaskOptions, sampleId, taskId]);

  const handleCopyPresetChange = useCallback(
    (value: string) => {
      setCopyPresetSlug(value);
      setCopyTaskSearchDraft('');
      setCopyTaskSearch('');
      setCopyTaskPage(0);
      setCopyTargetTaskId(undefined);
      void loadCopyTaskOptions(value, 0, '');
    },
    [loadCopyTaskOptions],
  );

  const handleCopyTaskSearch = useCallback(
    (value: string) => {
      const normalized = value.trim();
      setCopyTaskSearchDraft(value);
      setCopyTaskSearch(normalized);
      setCopyTaskPage(0);
      if (copyPresetSlug) void loadCopyTaskOptions(copyPresetSlug, 0, normalized);
    },
    [copyPresetSlug, loadCopyTaskOptions],
  );

  const handleCopyTaskSearchChange = useCallback(
    (value: string) => {
      setCopyTaskSearchDraft(value);
      if (!value) {
        setCopyTaskSearch('');
        setCopyTaskPage(0);
        if (copyPresetSlug) void loadCopyTaskOptions(copyPresetSlug, 0, '');
      }
    },
    [copyPresetSlug, loadCopyTaskOptions],
  );

  const handleCopyTaskPageChange = useCallback(
    (nextPage: number) => {
      const page = Math.max(0, nextPage - 1);
      setCopyTaskPage(page);
      if (copyPresetSlug) void loadCopyTaskOptions(copyPresetSlug, page, copyTaskSearch);
    },
    [copyPresetSlug, copyTaskSearch, loadCopyTaskOptions],
  );

  const handleCopyConfirm = useCallback(async () => {
    if (!taskId || !sampleId || !copyTargetTaskId) {
      message.warning('请选择目标任务');
      return;
    }
    setIsCopying(true);
    try {
      await copySampleToTask(copyTargetTaskId, {
        source_task_id: Number(taskId),
        source_sample_id: Number(sampleId),
      });
      message.success('原图已复制到目标任务');
      setCopyModalOpen(false);
    } catch (error: any) {
      const backendMsg = error?.response?.data?.msg;
      commonController.notificationErrorMessage({ message: backendMsg || '复制图片失败' }, 2);
    } finally {
      setIsCopying(false);
    }
  }, [copyTargetTaskId, sampleId, taskId]);

  const onKeyDown = debounce(
    useCallback(
      (e: KeyboardEvent) => {
        const key = e.key;
        if (key === 'a' && canGoPrevious) {
          handlePrevSample();
        } else if (key === 'd') {
          handleNextSample();
        }
      },
      [canGoPrevious, handleNextSample, handlePrevSample],
    ),
    500,
  );

  useHotkeys(
    'ctrl+space, meta+space',
    () => {
      if (noSave || isCompareAccessMode) {
        return;
      }

      if (currentSample.state === SampleState.SKIPPED) {
        handleCancelSkipSample();
      } else {
        handleSkipSample();
      }
    },
    {
      keyup: true,
      keydown: false,
    },
    [currentSample, handleCancelSkipSample, handleSkipSample, isCompareAccessMode],
  );

  useEffect(() => {
    document.addEventListener('keydown', onKeyDown);

    return () => {
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [onKeyDown]);

  useHotkeys(
    'ctrl+s,meta+s',
    () => {
      if (noSave) {
        return;
      }

      withActionPending(async () => {
        const saved = await saveCurrentSample();
        if (saved) {
          message.success(t('saved'));
        }
      });
    },
    {
      preventDefault: true,
    },
    [saveCurrentSample, noSave, withActionPending, t],
  );

  // 从外部触发上下翻页，比如快捷键，不知道上下sample的id
  // 监听标注主页的左侧文件切换
  useEffect(() => {
    const saveCurrentSampleFromOutside = async (e: CustomEvent) => {
      const _sampleId = _.get(e, 'detail.sampleId');

      if (_.isNil(_sampleId)) {
        return;
      }

      if (noSave) {
        const targetSample = samples.find((sample: SampleResponse) => sample.id === +_sampleId);
        warmSampleDetailCacheFromWindow(targetSample);
        navigateWithSearch(`/tasks/${taskId}/samples/${_sampleId}`);

        return;
      }

      const targetSample = samples.find((sample: SampleResponse) => sample.id === +_sampleId);
      warmSampleDetailCacheFromWindow(targetSample);
      if (
        shouldSaveBeforeSampleNavigation({
          sampleState: currentSample?.state,
          noSave,
          isCompareAccessMode,
          isCurrentUser: !!isMeTheCurrentUser,
        }) &&
        !(await saveCurrentSampleInBackground())
      ) {
        return;
      }
      navigateWithSearch(`/tasks/${taskId}/samples/${_sampleId}`);
    };

    document.addEventListener(SAMPLE_CHANGED, saveCurrentSampleFromOutside as unknown as EventListener);

    return () => {
      document.removeEventListener(SAMPLE_CHANGED, saveCurrentSampleFromOutside as unknown as EventListener);
    };
  }, [
    currentSample?.state,
    isCompareAccessMode,
    isMeTheCurrentUser,
    navigateWithSearch,
    noSave,
    samples,
    saveCurrentSampleInBackground,
    taskId,
  ]);

  const canDeleteCurrentSample =
    task?.media_type === MediaType.IMAGE &&
    !noSave &&
    !isGlobalLoading &&
    !isCompareAccessMode &&
    isCurrentSampleLoaded &&
    !!taskId &&
    !!sampleId &&
    !!isMeTheCurrentUser;

  const imageDeletePortal =
    canDeleteCurrentSample && imageDeleteHost
      ? createPortal(
          <ImageDeleteOverlay>
            <Popconfirm
              title={t('deleteConfirm')}
              okText={t('delete')}
              cancelText={t('cancel')}
              onConfirm={() => void handleDeleteCurrentSample()}
            >
              <Button aria-label="delete-current-sample" danger icon={<CloseOutlined />} />
            </Popconfirm>
          </ImageDeleteOverlay>,
          imageDeleteHost,
        )
      : null;

  if (noSave) {
    return null;
  }

  return (
    <>
      {imageDeletePortal}
      <ToolbarActionsRow>
        <FlexLayout items="center" gap=".5rem">
          {currentEditingUser && (
            <>
              {currentEditingUser.user_id !== me.data?.id && (
                <>
                  <UserAvatar key={currentEditingUser.user_id} user={currentEditingUser} />
                  {t('isAnnotating')}
                  <Tooltip title={t('collaboratorTips')} placement="bottom">
                    <QuestionCircleOutlined />
                  </Tooltip>
                </>
              )}
            </>
          )}
        </FlexLayout>
        <Button icon={<ArrowLeftOutlined />} onClick={() => void handleBackToTask()} disabled={isGlobalLoading}>
          返回任务详情
        </Button>
        {task?.media_type === MediaType.IMAGE && (
          <AiAnnotateRow data-labelu-ai-annotate-toolbar>
            <Button
              type="text"
              onClick={commonController.debounce(openCopyModal, 100)}
              disabled={isGlobalLoading || !isMeTheCurrentUser}
              style={{ flexShrink: 0 }}
            >
              复制
            </Button>
            <Button
              type="text"
              onClick={commonController.debounce(handleAutoLabel, 100)}
              disabled={isGlobalLoading || isAutoLabeling || !isMeTheCurrentUser}
              style={{ flexShrink: 0 }}
            >
              <FlexLayout items="center" gap="0.5rem">
                <SparklesIcon />
                {isAutoLabeling ? t('aiAutoLabeling') : t('aiAutoLabel')}
              </FlexLayout>
            </Button>
            <PreannotateControl
              taskId={Number(taskId)}
              sampleId={Number(sampleId)}
              type="yolo"
              mode="sample"
              buttonType="text"
              disabled={isGlobalLoading || !isMeTheCurrentUser}
              onCompleted={() => {
                window.dispatchEvent(
                  new CustomEvent(PRE_ANNOTATION_COMPLETED_EVENT, {
                    detail: { taskId: Number(taskId), sampleId: Number(sampleId) },
                  }),
                );
                void fetchCurrentCanvasPreAnnotation().finally(() => revalidator.revalidate());
              }}
            />
            <PreannotateControl
              taskId={Number(taskId)}
              sampleId={Number(sampleId)}
              type="ai_detection"
              mode="sample"
              buttonType="text"
              disabled={isGlobalLoading || !isMeTheCurrentUser}
              onCompleted={() => {
                window.dispatchEvent(
                  new CustomEvent(PRE_ANNOTATION_COMPLETED_EVENT, {
                    detail: { taskId: Number(taskId), sampleId: Number(sampleId) },
                  }),
                );
                void fetchCurrentCanvasPreAnnotation().finally(() => revalidator.revalidate());
              }}
            />
            <SamplePreAnnotationUpload
              taskId={Number(taskId)}
              sampleFilename={currentSample?.file?.filename || ''}
              disabled={isGlobalLoading || !isMeTheCurrentUser}
              onCompleted={async () => {
                window.dispatchEvent(
                  new CustomEvent(PRE_ANNOTATION_COMPLETED_EVENT, {
                    detail: { taskId: Number(taskId), sampleId: Number(sampleId) },
                  }),
                );
                await fetchCurrentCanvasPreAnnotation();
                await revalidator.revalidate();
              }}
            />
            {supportsNegativeSample && (
              <Tooltip title="确认图中不存在任何检测目标；训练时将以空标签作为背景负样本">
                <NegativeSampleControl $active={isNegativeSample}>
                  <Checkbox
                    checked={isNegativeSample}
                    disabled={isGlobalLoading || !isMeTheCurrentUser || isSampleSkipped || !!isCompareAccessMode}
                    onChange={(event) => handleNegativeSampleChange(event.target.checked)}
                  >
                    本图无目标
                  </Checkbox>
                </NegativeSampleControl>
              </Tooltip>
            )}
          </AiAnnotateRow>
        )}
        {isSampleSkipped ? (
          <Button
            type="text"
            onClick={commonController.debounce(handleCancelSkipSample, 100)}
            disabled={isGlobalLoading || !isMeTheCurrentUser || !!isCompareAccessMode}
          >
            {t('cancelSkip')}
          </Button>
        ) : (
          <Button
            type="text"
            onClick={commonController.debounce(handleSkipSample, 100)}
            disabled={isGlobalLoading || !isMeTheCurrentUser || !!isCompareAccessMode}
          >
            {t('skip')}
          </Button>
        )}
        {!isCompareAccessMode && canGoPrevious && (
          <Button onClick={commonController.debounce(handlePrevSample, 100)} disabled={isGlobalLoading}>
            {t('previous')}
          </Button>
        )}
        {isCompareAccessMode ? (
          <Button
            type="primary"
            onClick={commonController.debounce(handleComplete, 100)}
            disabled={isGlobalLoading || !isMeTheCurrentUser}
          >
            {t('save')}
          </Button>
        ) : isLastSample && isLastPage ? (
          <Button
            type="primary"
            onClick={commonController.debounce(handleComplete, 100)}
            disabled={isGlobalLoading || !isMeTheCurrentUser}
          >
            {t('finish')}
          </Button>
        ) : (
          <Button
            type="primary"
            onClick={() => void handleNextSample()}
            onMouseEnter={handlePrefetchNext}
            onFocus={handlePrefetchNext}
            loading={isNavigationLoading}
            disabled={isNavigationLoading}
          >
            {t('next')}
          </Button>
        )}
        <Modal
          title="复制原图到任务"
          open={copyModalOpen}
          onOk={() => void handleCopyConfirm()}
          onCancel={() => setCopyModalOpen(false)}
          okText="复制"
          cancelText="取消"
          okButtonProps={{ loading: isCopying, disabled: !copyTargetTaskId }}
          destroyOnClose={false}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div>
              <div style={{ marginBottom: 6, color: '#6b7280', fontSize: 13 }}>分类</div>
              <Select
                style={{ width: '100%' }}
                placeholder="请选择分类"
                value={copyPresetSlug}
                options={rectPresetOptions.map((item) => ({
                  label: item.name,
                  value: item.slug,
                }))}
                onChange={handleCopyPresetChange}
              />
            </div>
            <div>
              <div style={{ marginBottom: 6, color: '#6b7280', fontSize: 13 }}>任务</div>
              <ServerSearchInput
                value={copyTaskSearchDraft}
                placeholder="搜索目标任务名称、编号或状态"
                ariaLabel="搜索目标任务"
                loading={copyTaskLoading}
                style={{ width: '100%', marginBottom: 8 }}
                onChange={handleCopyTaskSearchChange}
                onSearch={handleCopyTaskSearch}
              />
              <Select
                style={{ width: '100%' }}
                showSearch
                optionFilterProp="label"
                placeholder="请选择目标任务"
                value={copyTargetTaskId}
                loading={copyTaskLoading}
                options={copyTaskOptions.map((item) => ({
                  label: item.name || `任务 #${item.id}`,
                  value: item.id!,
                }))}
                onChange={(value) => setCopyTargetTaskId(value)}
                notFoundContent={copyTaskLoading ? '加载中...' : '当前分类下没有可选任务'}
              />
              {copyTaskTotal > COPY_TASK_PAGE_SIZE && (
                <Pagination
                  size="small"
                  current={copyTaskPage + 1}
                  pageSize={COPY_TASK_PAGE_SIZE}
                  total={copyTaskTotal}
                  showSizeChanger={false}
                  showLessItems
                  style={{ marginTop: 8, textAlign: 'right' }}
                  onChange={handleCopyTaskPageChange}
                />
              )}
            </div>
          </div>
        </Modal>
      </ToolbarActionsRow>
    </>
  );
};
export default AnnotationRightCorner;
