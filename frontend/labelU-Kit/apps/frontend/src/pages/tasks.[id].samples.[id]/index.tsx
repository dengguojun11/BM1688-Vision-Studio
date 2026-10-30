import { useState, createRef, useMemo, useCallback, useRef, useLayoutEffect, useEffect } from 'react';
import _ from 'lodash-es';
import { Empty, Spin, message } from 'antd';
import '@labelu/video-annotator-react/dist/style.css';
import '@labelu/audio-annotator-react/dist/style.css';
import { Annotator } from '@labelu/video-annotator-react';
import type { AudioAndVideoAnnotatorRef } from '@labelu/audio-annotator-react';
import { Annotator as AudioAnnotator } from '@labelu/audio-annotator-react';
import { useSearchParams, useParams, useRouteLoaderData, useNavigation } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Bridge } from 'iframe-message-bridge';
import type { ImageAnnotatorProps, AnnotatorRef as ImageAnnotatorRef } from '@labelu/image-annotator-react';
import { Annotator as ImageAnnotator } from '@labelu/image-annotator-react';
import { FlexLayout } from '@labelu/components-react';
import { decodeImage, type EditType, type ToolName } from '@labelu/image';
import type { ILabel } from '@labelu/interface';
import { useTranslation } from '@labelu/i18n';

type TranslationHook = () => {
  t: (key: string, options?: Record<string, unknown>) => string;
};

const useTranslationCompat = useTranslation as unknown as TranslationHook;

import { MediaType, type SampleResponse } from '@/api/types';
import { preAnnotationKey } from '@/api/queryKeyFactories/preAnnotation';
import { getPreAnnotations } from '@/api/services/preAnnotations';
import { useScrollFetch } from '@/hooks/useScrollFetch';
import { getSample, getSamples } from '@/api/services/samples';
import { convertAudioAndVideoConfig } from '@/utils/convertAudioAndVideoConfig';
import { convertAudioAndVideoSample, convertMediaAnnotations } from '@/utils/convertAudioAndVideoSample';
import type { TaskLoaderResult } from '@/loaders/task.loader';
import { convertImageConfig } from '@/utils/convertImageConfig';
import {
  convertImageAnnotations,
  convertImageSample,
  resolveImageAnnotationSourceSize,
  scaleImageAnnotationsForDisplay,
} from '@/utils/convertImageSample';
import {
  getRectLabelOptionsFromTaskConfig,
  normalizeImageAnnotationRectLabels,
} from '@/utils/canonicalRectLabel';
import { TOOL_NAME } from '@/constants/toolName';
import useMe from '@/hooks/useMe';

import SlideLoader from './components/slideLoader';
import AnnotationRightCorner, { SAMPLE_CHANGED, SAMPLE_NAVIGATION_PENDING } from './components/annotationRightCorner';
import AnnotationContext from './annotation.context';
import { LoadingWrapper, Wrapper } from './style';
import { getVisiblePreAnnotations, preAnnotationsHasGeometry } from './preAnnotationVisibility';
import {
  shouldAutoLoadCanvasPreAnnotationsForSample,
} from './preAnnotationLoading';
import {
  isUploadedPreAnnotationRow,
  selectCanvasPreAnnotationRow,
  type PreAnnotationListRow,
} from './selectCanvasPreAnnotation';
import {
  createCurrentFirstSampleWindow,
  getNextSampleCursor,
  getPreviousSampleCursor,
  prependSampleWindow,
  shouldPreserveSampleWindowForNavigation,
  type SampleNavigationSource,
} from './sampleWindow';
import {
  ANNOTATION_PREFETCH_STALE_TIME_MS,
  getSampleImageUrl,
  prewarmAdjacentSamples,
} from './annotationPrefetch';
import { getCurrentEditingUser } from './currentEditingUser';
import useSampleWs from '../../hooks/useSampleWs';

type AllToolName = ToolName | 'segment' | 'frame' | 'tag' | 'text';
type ImagePreAnnotationTool = NonNullable<ImageAnnotatorProps['replacePreAnnotationTools']>[number];

export const imageAnnotationRef = createRef<ImageAnnotatorRef>();
export const videoAnnotationRef = createRef<AudioAndVideoAnnotatorRef>();
export const audioAnnotationRef = createRef<AudioAndVideoAnnotatorRef>();
export const CANVAS_PRE_ANNOTATION_MANUAL_LOADED = 'canvasPreAnnotationManualLoaded';
export const PRE_ANNOTATION_COMPLETED_EVENT = 'labelu:preannotation-completed';

const PREVIEW_OFFSET_TOP = 102;
const OFFSET_TOP = 158;
const PAGE_SIZE = 12;
const PREVIOUS_LIMIT = 3;

type ParsedPreAnnotation = Record<string, any> | null;

const parsePreAnnotationData = (data: unknown): ParsedPreAnnotation => {
  if (typeof data !== 'string') {
    return null;
  }

  try {
    return JSON.parse(data);
  } catch {
    return null;
  }
};

const AnnotationPage = () => {
  const routeParams = useParams();
  const [searchParams] = useSearchParams();
  const compareAccessToken = searchParams.get('compare_access_token') || undefined;
  const isCompareAccessMode = !!compareAccessToken;
  const { task } = useRouteLoaderData('task') as TaskLoaderResult;
  const sample = (useRouteLoaderData('annotation') as any).sample as Awaited<ReturnType<typeof getSample>>;
  const loaderPreAnnotation = (useRouteLoaderData('annotation') as any).preAnnotation;
  const { t } = useTranslationCompat();
  const sampleFilename = sample?.data?.file?.filename;
  const [manualPreAnnotationLoaded, setManualPreAnnotationLoaded] = useState(false);
  // Geometry previews are session-scoped: only an explicit current-image run
  // (or a manual upload) may replace the saved canvas for this page session.
  // A refresh must fall back to the sample's formal data so deleted boxes do
  // not reappear from a stale pre-annotation row.
  const [forcePreAnnotationPreview, setForcePreAnnotationPreview] = useState(false);
  const canUseCanvasPreAnnotation = shouldAutoLoadCanvasPreAnnotationsForSample(sample?.data, {
    manual: manualPreAnnotationLoaded,
  });
  const preAnnotationQueryKey = useMemo(
    () =>
      sampleFilename
        ? preAnnotationKey.listWithSampleName({
            task_id: +routeParams.taskId!,
            sample_name: sampleFilename,
            canvas_only: true,
          })
        : undefined,
    [routeParams.taskId, sampleFilename],
  );
  const { data: queriedPreAnnotation, refetch: refetchPreAnnotation } = useQuery({
    queryKey: preAnnotationQueryKey ?? preAnnotationKey.listWithSampleName({ task_id: 0, sample_name: '' }),
    queryFn: () =>
      getPreAnnotations({
        task_id: +routeParams.taskId!,
        sample_name: sampleFilename,
        canvas_only: true,
      }),
    enabled: !!routeParams.taskId && !!sampleFilename && canUseCanvasPreAnnotation && !isCompareAccessMode,
    initialData: loaderPreAnnotation,
    staleTime: ANNOTATION_PREFETCH_STALE_TIME_MS,
  });
  useEffect(() => {
    const handleCompleted = (event: Event) => {
      const detail = (event as CustomEvent<{ taskId?: number; sampleId?: number | null }>).detail;
      const currentTaskId = Number(routeParams.taskId);
      const currentSampleId = Number(routeParams.sampleId);
      if (detail?.taskId !== currentTaskId || detail?.sampleId !== currentSampleId) {
        return;
      }

      setManualPreAnnotationLoaded(true);
      setForcePreAnnotationPreview(true);
      void refetchPreAnnotation();
    };

    window.addEventListener(PRE_ANNOTATION_COMPLETED_EVENT, handleCompleted);
    return () => window.removeEventListener(PRE_ANNOTATION_COMPLETED_EVENT, handleCompleted);
  }, [refetchPreAnnotation, routeParams.sampleId, routeParams.taskId]);
  const preAnnotation = canUseCanvasPreAnnotation ? queriedPreAnnotation ?? loaderPreAnnotation : undefined;
  const preAnnotationRows = useMemo(
    () => (preAnnotation?.data ?? []) as PreAnnotationListRow[],
    [preAnnotation?.data],
  );
  const canvasPreAnnotation = useMemo(
    () => selectCanvasPreAnnotationRow(preAnnotationRows),
    [preAnnotationRows],
  );
  const parsedCanvasPreAnnotation = useMemo(
    () => parsePreAnnotationData(_.get(canvasPreAnnotation, 'data')),
    [canvasPreAnnotation?.data],
  );
  const hasUploadedPreAnnotation = useMemo(
    () => preAnnotationRows.some(isUploadedPreAnnotationRow),
    [preAnnotationRows],
  );
  const rectLabelOptions = useMemo(
    () => getRectLabelOptionsFromTaskConfig(task?.config),
    [task?.config],
  );

  const preAnnotationConfig = useMemo(() => {
    const result: Partial<Record<AllToolName, any>> = {};

    if (parsedCanvasPreAnnotation) {
      const config = parsedCanvasPreAnnotation.config;

      if (!config) {
        return {};
      }

      Object.keys(config).forEach((key) => {
        let toolName = key.replace(/Tool$/, '') as AllToolName;

        if (key.includes('audio') || key.includes('video')) {
          // audioSegmentTool => segment
          toolName = toolName.replace(/audio|video/, '').toLowerCase() as AllToolName;
        }

        result[toolName] = config[key as keyof typeof config];
      });
    }

    return result;
  }, [parsedCanvasPreAnnotation]);
  const preAnnotations = useMemo(() => {
    if (!canvasPreAnnotation || !parsedCanvasPreAnnotation) {
      return {};
    }

    let _annotations = _.get(parsedCanvasPreAnnotation, 'annotations', {});
    const preAnnotationFile = (_.get(canvasPreAnnotation, 'file', {}) ?? {}) as {
      filename?: string;
    };
    // 兼容json预标注
    if (preAnnotationFile.filename?.endsWith('.json')) {
      const jsonAnnotations = _.get(parsedCanvasPreAnnotation, 'result.annotations', []) as any[];
      _annotations = _.fromPairs(
        jsonAnnotations.map((item) => [
          item.toolName,
          {
            toolName: item.toolName,
            result: item.result,
          },
        ]),
      );
    }

    if (task?.media_type === MediaType.IMAGE) {
      const converted = convertImageAnnotations(_annotations);
      const normalized = normalizeImageAnnotationRectLabels(converted, rectLabelOptions);
      const annotationMeta = (_.get(parsedCanvasPreAnnotation, 'meta', {}) ?? {}) as {
        width?: unknown;
        height?: unknown;
      };
      const sampleFile = sample.data.file as typeof sample.data.file & {
        width?: unknown;
        height?: unknown;
      };
      const sourceSize = resolveImageAnnotationSourceSize(
        annotationMeta.width,
        annotationMeta.height,
        sampleFile?.original_width ?? sampleFile?.width,
        sampleFile?.original_height ?? sampleFile?.height,
      );
      return scaleImageAnnotationsForDisplay(
        normalized,
        sourceSize.width,
        sourceSize.height,
      );
    } else if (task?.media_type === MediaType.VIDEO || task?.media_type === MediaType.AUDIO) {
      return convertMediaAnnotations(task.media_type, _annotations);
    }

    return {};
  }, [
    canvasPreAnnotation,
    parsedCanvasPreAnnotation,
    rectLabelOptions,
    sample.data.file,
    task?.media_type,
  ]);
  const preAnnotationMeta = useMemo(() => {
    if (!parsedCanvasPreAnnotation || hasUploadedPreAnnotation) {
      return {};
    }

    return _.get(parsedCanvasPreAnnotation, 'meta', {}) ?? {};
  }, [hasUploadedPreAnnotation, parsedCanvasPreAnnotation]);
  const effectiveOverwriteExistingAnnotations = useMemo(
    () =>
      !hasUploadedPreAnnotation &&
      !!preAnnotationMeta.overwrite_existing_annotations &&
      !!preAnnotationMeta.include_geometry_tools,
    [
      hasUploadedPreAnnotation,
      preAnnotationMeta.include_geometry_tools,
      preAnnotationMeta.overwrite_existing_annotations,
    ],
  );

  const navigation = useNavigation();
  const taskConfig = _.get(task, 'config');
  const me = useMe();
  const [currentSampleConns, taskConns] = useSampleWs();
  const currentEditingUser = useMemo(() => {
    if (isCompareAccessMode && me.data?.id && routeParams.taskId && routeParams.sampleId) {
      return {
        user_id: me.data.id,
        username: me.data.username ?? '',
        task_id: Number(routeParams.taskId),
        sample_id: Number(routeParams.sampleId),
      };
    }
    return getCurrentEditingUser(currentSampleConns, me.data, routeParams.taskId, routeParams.sampleId);
  }, [currentSampleConns, isCompareAccessMode, me.data, routeParams.sampleId, routeParams.taskId]);
  const isMeTheCurrentEditingUser = currentEditingUser?.user_id === me.data?.id;
  const [readySampleId, setReadySampleId] = useState<string | number>();
  const annotationPageRef = useRef<HTMLDivElement | null>(null);
  const [annotationViewportHeight, setAnnotationViewportHeight] = useState<number | null>(null);

  // Fit the annotation workspace into the visible viewport so the toolbar,
  // sample list and attribute panel stay reachable without page scrolling.
  useEffect(() => {
    const measure = () => {
      const element = annotationPageRef.current;
      if (!element) {
        return;
      }
      const rect = element.getBoundingClientRect();
      const main = element.closest('main');
      const bottomPadding = main
        ? Number.parseFloat(window.getComputedStyle(main).paddingBottom || '0') || 0
        : 0;
      const nextHeight = Math.max(320, Math.floor(window.innerHeight - rect.top - bottomPadding));
      setAnnotationViewportHeight((previous) => (previous === nextHeight ? previous : nextHeight));
    };

    measure();
    window.addEventListener('resize', measure);
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measure) : null;
    if (observer && document.body) {
      observer.observe(document.body);
    }

    return () => {
      window.removeEventListener('resize', measure);
      observer?.disconnect();
    };
  }, [task?.media_type]);

  useEffect(() => {
    setManualPreAnnotationLoaded(false);
    setForcePreAnnotationPreview(false);
    setReadySampleId(undefined);
  }, [sample?.data.id]);

  useEffect(() => {
    const handleManualPreAnnotationLoaded = (event: Event) => {
      const loadedSampleId = _.get(event, 'detail.sampleId');

      if (loadedSampleId === sample?.data.id) {
        setManualPreAnnotationLoaded(true);
      }
    };

    document.addEventListener(CANVAS_PRE_ANNOTATION_MANUAL_LOADED, handleManualPreAnnotationLoaded);

    return () => {
      document.removeEventListener(CANVAS_PRE_ANNOTATION_MANUAL_LOADED, handleManualPreAnnotationLoaded);
    };
  }, [sample?.data.id]);

  // TODO： labelu/image中的错误定义
  const onError = useCallback(
    (err: any) => {
      const value = err.value;

      if (err.type === 'rotate') {
        message.error(t('cannotRotateWhenAnnotationExist'));
      }

      if (err.type === 'minPointAmount') {
        message.error(`${t('minPointAmountCannotSmallThan')} ${value}`);
      }

      if (err.type === 'maxPointAmount') {
        message.error(`${t('maxPointAmountCannotExceed')} ${value}`);
      }

      if (err.type === 'minWidth') {
        message.error(`${t('minWidthCannotSmallThan')}${value}`);
      }

      if (err.type === 'minHeight') {
        message.error(`${t('minHeightCannotSmallThan')} ${value}`);
      }
    },
    [t],
  );

  // 滚动加载
  const [totalCount, setTotalCount] = useState<number>(0);
  const nextCursorRef = useRef<number | undefined>(sample?.data.id);
  const previousCursorRef = useRef<number | undefined>(sample?.data.id);
  const hasNextSamplesRef = useRef<boolean>(true);
  const hasPreviousSamplesRef = useRef<boolean>(true);
  const fetchingPreviousRef = useRef<boolean>(false);
  const initializedSampleIdRef = useRef<number | undefined>();
  const navigationSourceRef = useRef<SampleNavigationSource | undefined>();
  const samplesRef = useRef<SampleResponse[]>([]);
  const lastPrewarmKeyRef = useRef<string>();
  const [loadingPrevious, setLoadingPrevious] = useState(false);

  const fetchInitialSamples = useCallback(async () => {
    if (!routeParams.taskId || !sample?.data.id) {
      return [];
    }

    if (isCompareAccessMode) {
      setTotalCount(1);
      nextCursorRef.current = sample.data.id;
      previousCursorRef.current = sample.data.id;
      hasNextSamplesRef.current = false;
      hasPreviousSamplesRef.current = false;
      return [sample.data];
    }

    const [previousResponse, nextResponse] = await Promise.all([
      getSamples({
        task_id: +routeParams.taskId!,
        before: sample.data.id,
        size: PREVIOUS_LIMIT + 1,
      }),
      getSamples({
        task_id: +routeParams.taskId!,
        after: sample.data.id,
        size: PAGE_SIZE + 1,
      }),
    ]);

    const previousSamples = previousResponse.data.slice(0, PREVIOUS_LIMIT);
    const nextSamples = nextResponse.data.slice(0, PAGE_SIZE);
    nextCursorRef.current = getNextSampleCursor(nextSamples) ?? sample.data.id;
    previousCursorRef.current = getPreviousSampleCursor(previousSamples) ?? sample.data.id;
    hasNextSamplesRef.current = nextResponse.data.length > PAGE_SIZE;
    hasPreviousSamplesRef.current = previousResponse.data.length > 0;
    setTotalCount(nextResponse.meta_data?.total ?? previousResponse.meta_data?.total ?? 0);
    return [...previousSamples, sample.data, ...nextSamples];
  }, [isCompareAccessMode, routeParams.taskId, sample?.data]);

  const fetchSamples = useCallback(
    async (isReset?: boolean) => {
      if (!routeParams.taskId || !sample?.data.id) {
        return [] as SampleResponse[];
      }

      if (isReset || initializedSampleIdRef.current !== sample.data.id) {
        if (
          shouldPreserveSampleWindowForNavigation(
            navigationSourceRef.current,
            sample.data.id,
            initializedSampleIdRef.current,
          )
        ) {
          initializedSampleIdRef.current = sample.data.id;
          navigationSourceRef.current = undefined;
          return samplesRef.current;
        }

        initializedSampleIdRef.current = sample.data.id;
        navigationSourceRef.current = undefined;
        fetchingPreviousRef.current = false;
        setLoadingPrevious(false);
        return fetchInitialSamples();
      }

      if (isCompareAccessMode) {
        return [] as SampleResponse[];
      }

      const nextCursor = nextCursorRef.current;
      if (typeof nextCursor !== 'number') {
        return [] as SampleResponse[];
      }

      const { data, meta_data } = await getSamples({
        task_id: +routeParams.taskId!,
        after: nextCursor,
        size: PAGE_SIZE + 1,
      });

      const nextSamples = data.slice(0, PAGE_SIZE);
      nextCursorRef.current = getNextSampleCursor(nextSamples);
      hasNextSamplesRef.current = data.length > PAGE_SIZE;
      setTotalCount(meta_data?.total ?? 0);
      return nextSamples;
    },
    [fetchInitialSamples, isCompareAccessMode, routeParams.taskId, sample?.data.id],
  );

  const [samples = [] as SampleResponse[], loading, setSamples, svc] = useScrollFetch(
    fetchSamples,
    () =>
      document.querySelector('.labelu-image__sidebar div') ||
      document.querySelector('.labelu-audio__sidebar div') ||
      document.querySelector('.labelu-video__sidebar div'),
    {
      isEnd: () => !hasNextSamplesRef.current,
      watch: sample?.data.id,
    },
  );

  useEffect(() => {
    samplesRef.current = samples;
  }, [samples]);

  useEffect(() => {
    if (isCompareAccessMode || task?.media_type !== MediaType.IMAGE || !routeParams.taskId) {
      return;
    }

    const currentIndex = samples.findIndex((item) => item.id === sample.data.id);

    if (currentIndex < 0) {
      return;
    }

    const prewarmKey = `${sample.data.id}:${samples[0]?.id ?? ''}:${samples[samples.length - 1]?.id ?? ''}`;
    if (lastPrewarmKeyRef.current === prewarmKey) {
      return;
    }

    const prefetch = () => {
      lastPrewarmKeyRef.current = prewarmKey;
      prewarmAdjacentSamples(+routeParams.taskId!, samples, sample.data.id);
    };

    if ('requestIdleCallback' in window) {
      const id = window.requestIdleCallback(prefetch, { timeout: 300 });
      return () => window.cancelIdleCallback(id);
    }

    const id = window.setTimeout(prefetch, 30);
    return () => window.clearTimeout(id);
  }, [isCompareAccessMode, routeParams.taskId, sample.data.id, samples, task?.media_type]);

  useEffect(() => {
    const handleSampleChanged = (event: Event) => {
      const source = _.get(event, 'detail.source') as SampleNavigationSource | undefined;

      if (source === 'list' || source === 'prev' || source === 'next') {
        navigationSourceRef.current = source;
      }
    };

    document.addEventListener(SAMPLE_CHANGED, handleSampleChanged);

    return () => {
      document.removeEventListener(SAMPLE_CHANGED, handleSampleChanged);
    };
  }, []);

  useEffect(() => {
    const handleNavigationPending = () => setIsNavigationPending(true);
    document.addEventListener(SAMPLE_NAVIGATION_PENDING, handleNavigationPending);
    return () => document.removeEventListener(SAMPLE_NAVIGATION_PENDING, handleNavigationPending);
  }, []);

  const fetchPreviousSamples = useCallback(
    async (scrollContainer?: HTMLElement | null) => {
      if (
        isCompareAccessMode ||
        !routeParams.taskId ||
        !sample?.data.id ||
        !hasPreviousSamplesRef.current ||
        fetchingPreviousRef.current
      ) {
        return [] as SampleResponse[];
      }

      const previousCursor = previousCursorRef.current;
      if (typeof previousCursor !== 'number') {
        return [] as SampleResponse[];
      }

      fetchingPreviousRef.current = true;
      setLoadingPrevious(true);
      const previousScrollHeight = scrollContainer?.scrollHeight ?? 0;
      const previousScrollTop = scrollContainer?.scrollTop ?? 0;

      try {
        const { data, meta_data } = await getSamples({
          task_id: +routeParams.taskId!,
          before: previousCursor,
          size: PAGE_SIZE + 1,
        });
        const previousSamples = data.length > PAGE_SIZE ? data.slice(1) : data;

        previousCursorRef.current = getPreviousSampleCursor(previousSamples) ?? previousCursor;
        hasPreviousSamplesRef.current = data.length > PAGE_SIZE;
        setTotalCount(meta_data?.total ?? 0);

        if (previousSamples.length > 0) {
          setSamples((currentSamples) => prependSampleWindow(previousSamples, currentSamples));

          if (scrollContainer) {
            requestAnimationFrame(() => {
              scrollContainer.scrollTop = scrollContainer.scrollHeight - previousScrollHeight + previousScrollTop;
            });
          }
        }

        return previousSamples;
      } finally {
        fetchingPreviousRef.current = false;
        setLoadingPrevious(false);
      }
    },
    [isCompareAccessMode, routeParams.taskId, sample?.data.id, setSamples],
  );

  const leftSiderContent = useMemo(() => <SlideLoader />, []);

  const topActionContent = (
    <AnnotationRightCorner
      totalSize={totalCount}
      fetchNext={svc}
      isLastPage={!hasNextSamplesRef.current}
      noSave={!!searchParams.get('noSave')}
    />
  );

  const annotationContextValue = useMemo(() => {
    return {
      samples,
      setSamples,
      taskConnections: taskConns,
      task,
      currentEditingUser,
      isCompareAccessMode,
      isEnd: !hasNextSamplesRef.current,
      isStart: !hasPreviousSamplesRef.current,
      loadingPrevious,
      fetchPreviousSamples,
    };
  }, [currentEditingUser, fetchPreviousSamples, isCompareAccessMode, loadingPrevious, taskConns, samples, setSamples, task, totalCount]);

  let content = null;

  const editorConfig = useMemo(() => {
    if (task?.media_type === MediaType.VIDEO || task?.media_type === MediaType.AUDIO) {
      return convertAudioAndVideoConfig(taskConfig);
    }

    return convertImageConfig(taskConfig);
  }, [task?.media_type, taskConfig]);

  const editingSample = useMemo(() => {
    if (task?.media_type === MediaType.IMAGE) {
      return convertImageSample(sample?.data, taskConfig, task);
    } else if (task?.media_type === MediaType.VIDEO || task?.media_type === MediaType.AUDIO) {
      return convertAudioAndVideoSample(sample?.data, task.media_type);
    }
  }, [sample?.data, task?.media_type, taskConfig]);

  const mediaSamples = useMemo(
    () =>
      samples
        .map((item) => convertAudioAndVideoSample(item, task?.media_type))
        .filter(Boolean),
    [samples, task?.media_type],
  );

  useEffect(() => {
    if (task?.media_type !== MediaType.IMAGE || !editingSample?.url) {
      return;
    }

    void decodeImage(editingSample.url, 'high');
  }, [editingSample?.url, task?.media_type]);

  const visiblePreAnnotations = useMemo(
    () =>
      getVisiblePreAnnotations(sample.data.state, preAnnotations, {
        globalOnlyForAnnotated: true,
        overwriteExistingAnnotations:
          forcePreAnnotationPreview && effectiveOverwriteExistingAnnotations,
        // Uploaded pre-annotations stay reviewable after a reload; generated
        // batch rows only appear for unannotated samples or an explicit run.
        allowGeometryForAnnotated: forcePreAnnotationPreview || hasUploadedPreAnnotation,
      }),
    [
      effectiveOverwriteExistingAnnotations,
      forcePreAnnotationPreview,
      hasUploadedPreAnnotation,
      sample.data.state,
      preAnnotations,
    ],
  );
  const defaultPreAnnotations = useMemo(
    () => getVisiblePreAnnotations(sample.data.state, preAnnotations, { globalOnlyForAnnotated: false }),
    [sample.data.state, preAnnotations],
  );
  const replacePreAnnotationTools = useMemo<ImagePreAnnotationTool[]>(() => {
    const tools: ImagePreAnnotationTool[] = preAnnotationMeta.preserve_global_annotations ? [] : ['tag', 'text'];

    if (preAnnotationMeta.include_geometry_tools || preAnnotationsHasGeometry(preAnnotations)) {
      tools.push('rect', 'polygon', 'point', 'line');
    }

    return tools;
  }, [preAnnotationMeta.include_geometry_tools, preAnnotationMeta.preserve_global_annotations, preAnnotations]);

  const renderSidebar = useMemo(() => {
    return () => leftSiderContent;
  }, [leftSiderContent]);

  // =================== preview config ===================
  const [configFromParent, setConfigFromParent] = useState<any>();
  useLayoutEffect(() => {
    const bridge = new Bridge(window.parent);

    bridge.on('preview', (data) => {
      setConfigFromParent(data);
    });

    bridge.post('ready').catch(() => {});

    return () => bridge.destroy();
  }, []);

  const isSwitchingSample = useMemo(() => {
    const nextPathname = navigation.location?.pathname;

    if (!nextPathname || navigation.state === 'idle') {
      return false;
    }

    const matched = nextPathname.match(/^\/tasks\/([^/]+)\/samples\/([^/]+)/);

    return matched?.[1] === routeParams.taskId && matched?.[2] !== routeParams.sampleId;
  }, [navigation.location?.pathname, navigation.state, routeParams.sampleId, routeParams.taskId]);
  const isEditorReady = task?.media_type !== MediaType.IMAGE || readySampleId === sample.data.id;
  // 图片编辑器的 onReady 可能因单张损坏或超大图片迟迟不回调，不能用它阻塞整页导航。
  const [isNavigationPending, setIsNavigationPending] = useState(false);
  const isLoading = (loading && samples.length === 0) || isSwitchingSample || isNavigationPending;

  useEffect(() => {
    setIsNavigationPending(false);
  }, [sample.data.id]);

  const config = useMemo(() => {
    return configFromParent || editorConfig;
  }, [configFromParent, editorConfig]);

  const handleImageReady = useCallback<NonNullable<ImageAnnotatorProps['onReady']>>(
    (_engine, url) => {
      const normalizeImageUrl = (value?: string | null) => (value || '').split('?')[0];
      if (
        url === editingSample?.url ||
        normalizeImageUrl(url) === normalizeImageUrl(editingSample?.url)
      ) {
        setReadySampleId(sample.data.id);
      }
    },
    [editingSample?.url, sample.data.id],
  );

  useEffect(() => {
    if (me.data && currentSampleConns?.[0] && !isMeTheCurrentEditingUser) {
      message.destroy();
      message.error(t('currentSampleIsAnnotating'));
    }
  }, [currentSampleConns, isMeTheCurrentEditingUser, me.data, t]);

  type RequestEditPayload = {
    toolName?: ToolName | 'frame' | 'segment';
    label?: string;
    modifiedProperty?: string;
  };

  const requestEdit = useCallback((editType: EditType, { toolName, label }: RequestEditPayload) => {
      if (!toolName) {
        return false;
      }

      const toolConfig = config[toolName];
      const toolNameKey =
        (toolName.includes('frame') || toolName.includes('segment')
          ? task?.media_type?.toLowerCase() + _.upperFirst(toolName)
          : toolName) + 'Tool';

      if (editType === 'create' && !toolConfig?.labels?.find((item: ILabel) => item.value === label)) {
        message.destroy();
        message.error(`${t('currentTool')}【${TOOL_NAME[toolNameKey]}】${t('doesntInclude')}【${label}】`);

        return false;
      }

      if (editType === 'update' && !config[toolName]) {
        message.destroy();
        message.error(`${t('currentConfigDoesntInclude')}【${TOOL_NAME[toolNameKey]}】`);
        return false;
      }

      return true;
    }, [config, task, t]);

  const [currentTool, setCurrentTool] = useState<any>();
  const [labelMapping, setLabelMapping] = useState<Record<any, string>>();

  const handleLabelChange = useCallback(
    (toolName: ToolName | 'frame' | 'segment' | 'text' | 'tag' | undefined, label: ILabel | undefined) => {
    if (!toolName || !label) {
      return;
    }

    // 缓存当前标签
    setLabelMapping((prev) => {
      return {
        ...prev,
        [toolName]: label.value,
      };
    });
    },
    [],
  );

  const handleToolChange = useCallback((toolName: any) => {
    setCurrentTool(toolName);
  }, []);

  const currentLabel = useMemo(() => {
    return labelMapping?.[currentTool];
  }, [currentTool, labelMapping]);

  const disabled = useMemo(() => {
    return me.data && currentSampleConns[0] && !isMeTheCurrentEditingUser;
  }, [currentSampleConns, isMeTheCurrentEditingUser, me.data]);

  if (task?.media_type === MediaType.IMAGE) {
    content = (
      <ImageAnnotator
        key={`sample-${sample.data.id}`}
        renderSidebar={renderSidebar}
        toolbarRight={topActionContent}
        ref={imageAnnotationRef}
        onError={onError}
        fillViewport
        // windows platform pixel issue
        offsetTop={configFromParent ? PREVIEW_OFFSET_TOP : OFFSET_TOP}
        editingSample={editingSample as ImageAnnotatorProps['editingSample']}
        config={config}
        disabled={disabled}
        requestEdit={requestEdit}
        onLabelChange={handleLabelChange}
        onToolChange={handleToolChange}
        selectedTool={disabled ? undefined : currentTool}
        selectedLabel={disabled ? undefined : currentLabel}
        preAnnotationLabels={preAnnotationConfig}
        preAnnotations={visiblePreAnnotations}
        overwriteExistingAnnotations={forcePreAnnotationPreview && effectiveOverwriteExistingAnnotations}
        replacePreAnnotationTools={replacePreAnnotationTools}
        onReady={handleImageReady}
      />
    );
  } else if (task?.media_type === MediaType.VIDEO) {
    content = (
      <Annotator
        samples={mediaSamples as any}
        primaryColor="#0d53de"
        ref={videoAnnotationRef}
        offsetTop={configFromParent ? PREVIEW_OFFSET_TOP : OFFSET_TOP}
        editingSample={editingSample}
        config={config}
        toolbarRight={topActionContent}
        renderSidebar={renderSidebar}
        disabled={disabled}
        requestEdit={requestEdit}
        onLabelChange={handleLabelChange}
        onToolChange={handleToolChange}
        selectedTool={disabled ? undefined : currentTool}
        selectedLabel={disabled ? undefined : currentLabel}
        preAnnotationLabels={preAnnotationConfig}
        preAnnotations={defaultPreAnnotations}
      />
    );
  } else if (task?.media_type === MediaType.AUDIO) {
    content = (
      <AudioAnnotator
        samples={mediaSamples as any}
        primaryColor="#0d53de"
        ref={audioAnnotationRef}
        offsetTop={configFromParent ? PREVIEW_OFFSET_TOP : OFFSET_TOP}
        editingSample={editingSample}
        config={config}
        disabled={disabled}
        toolbarRight={topActionContent}
        renderSidebar={renderSidebar}
        requestEdit={requestEdit}
        onLabelChange={handleLabelChange}
        onToolChange={handleToolChange}
        selectedTool={disabled ? undefined : currentTool}
        selectedLabel={disabled ? undefined : currentLabel}
        preAnnotationLabels={preAnnotationConfig}
        preAnnotations={defaultPreAnnotations}
      />
    );
  }

  if (_.isEmpty(sample.data.file)) {
    return (
      <FlexLayout.Content items="center" justify="center" flex>
        <Empty description={t('noSample')} />
      </FlexLayout.Content>
    );
  }

  if (_.isEmpty(taskConfig?.tools) && _.isEmpty(configFromParent)) {
    return (
      <FlexLayout.Content items="center" justify="center" flex>
        <Empty description={t('noTool')} />
      </FlexLayout.Content>
    );
  }

  return (
    <AnnotationContext.Provider value={annotationContextValue}>
      {isLoading && (
        <LoadingWrapper items="center" justify="center" flex>
          <Spin spinning />
        </LoadingWrapper>
      )}
      <div
        ref={annotationPageRef}
        style={
          task?.media_type === MediaType.IMAGE && annotationViewportHeight
            ? {
                height: `${annotationViewportHeight}px`,
                minHeight: 0,
                display: 'flex',
                flexDirection: 'column',
                overflow: 'hidden',
              }
            : undefined
        }
      >
        <Wrapper flex="column" full loading={isLoading}>
          {content}
        </Wrapper>
      </div>
    </AnnotationContext.Provider>
  );
};

export default AnnotationPage;
