import styled, { css } from 'styled-components';
import { useHotkeys } from 'react-hotkeys-hook';
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import type {
  Attribute,
  ILabel,
  TextAnnotationType,
  TagAnnotationType,
  TextAttribute,
  EnumerableAttribute,
} from '@labelu/interface';
import { useRedoUndo } from '@labelu/components-react';
import { TOOL_NAMES } from '@labelu/image';
import type {
  AnnotationData,
  ToolName,
  Annotator as ImageAnnotatorClass,
  AnnotationToolData,
  EditType,
} from '@labelu/image';
import cloneDeep from 'lodash.clonedeep';

import Sidebar from './Sidebar';
import { AnnotatorToolbar } from './Toolbar';
import { LabelSection, openAttributeModal } from './LabelSection';
import { AttributePanel } from './AttributePanel';
import type { ImageAnnotatorOptions } from './hooks/useImageAnnotator';
import { useImageAnnotator } from './hooks/useImageAnnotator';
import Footer from './Footer';
import type {
  AllAnnotationMapping,
  AllAnnotationType,
  AnnotationDataInUI,
  AnnotationWithTool,
  GlobalAnnotation,
} from './context/annotation.context';
import { AnnotationContext } from './context/annotation.context';
import type { GlobalToolConfig } from './context/tool.context';
import { ToolContext } from './context/tool.context';
import { HistoryContext } from './context/history.context';
import type { ImageSample } from './context/sample.context';
import { SampleContext } from './context/sample.context';
import {
  mergePreAnnotationsIntoSample,
  preserveGeometryAnnotations,
  preserveGlobalAnnotations,
} from './mergePreAnnotations';

function omit<T, K extends keyof T>(obj: T, ...keys: K[]): Omit<T, K> {
  const result = { ...obj };
  keys.forEach((key) => {
    delete result[key];
  });
  return result;
}

function addToolNameToAnnotationData(annotationData: Partial<Record<ToolName, AnnotationData[]>>) {
  const result = [] as AnnotationDataInUI[];

  Object.keys(annotationData).forEach((key) => {
    const _toolName = key as ToolName;
    annotationData[_toolName]?.forEach((item: any) => {
      if (item.tool !== _toolName) {
        item.tool = _toolName;
      }
      result.push(item as AnnotationDataInUI);
    });
  });

  return result;
}

function normalizeGeometryAnnotationOrders<T extends Record<string, any>>(annotationData: T): T {
  const geometryItems: { item: AnnotationData; index: number }[] = [];

  Object.keys(annotationData ?? {}).forEach((key) => {
    if (!TOOL_NAMES.includes(key as ToolName) || !Array.isArray(annotationData[key])) {
      return;
    }

    annotationData[key].forEach((item: AnnotationData) => {
      geometryItems.push({ item, index: geometryItems.length });
    });
  });

  if (geometryItems.length === 0) {
    return annotationData;
  }

  const orderById = new Map<string, number>();
  geometryItems
    .slice()
    .sort((a, b) => {
      const orderA = Number.isFinite(a.item.order) ? a.item.order : Number.MAX_SAFE_INTEGER;
      const orderB = Number.isFinite(b.item.order) ? b.item.order : Number.MAX_SAFE_INTEGER;
      return orderA === orderB ? a.index - b.index : orderA - orderB;
    })
    .forEach(({ item }, index) => {
      orderById.set(item.id, index + 1);
    });

  const normalized: Record<string, any> = { ...annotationData };
  Object.keys(normalized).forEach((key) => {
    if (!TOOL_NAMES.includes(key as ToolName) || !Array.isArray(normalized[key])) {
      return;
    }

    normalized[key] = normalized[key].map((item: AnnotationData) => ({
      ...item,
      order: orderById.get(item.id) ?? item.order,
    }));
  });

  return normalized as T;
}

function normalizeGeometryMappingOrders(mapping: AllAnnotationMapping): AllAnnotationMapping {
  const grouped: Partial<Record<ToolName, AnnotationData[]>> = {};

  Object.values(mapping).forEach((item) => {
    if (!TOOL_NAMES.includes(item.tool as ToolName)) {
      return;
    }

    const toolName = item.tool as ToolName;
    grouped[toolName] = [...(grouped[toolName] ?? []), item as AnnotationData];
  });

  const normalizedGroups = normalizeGeometryAnnotationOrders(grouped);
  const normalizedById = new Map<string, AnnotationData>();

  Object.values(normalizedGroups).forEach((items) => {
    items?.forEach((item) => normalizedById.set(item.id, item));
  });

  const normalizedMapping: AllAnnotationMapping = {};
  Object.entries(mapping).forEach(([id, item]) => {
    normalizedMapping[id] = (normalizedById.get(id) ?? item) as AllAnnotationMapping[string];
  });

  return normalizedMapping;
}

function loadImageAnnotationsToEngine(
  engine: ImageAnnotatorClass | null | undefined,
  annotations: AllAnnotationMapping,
) {
  if (!engine) {
    return;
  }

  engine.clearData();
  const imageAnnotations: Partial<Record<ToolName, AnnotationDataInUI[]>> = {};

  Object.values(annotations).forEach((item) => {
    if (TOOL_NAMES.includes(item.tool as ToolName)) {
      const toolName = item.tool as ToolName;
      if (!imageAnnotations[toolName]) {
        imageAnnotations[toolName] = [];
      }

      imageAnnotations[toolName]!.push(item as AnnotationDataInUI);
    }
  });

  Object.keys(imageAnnotations).forEach((key) => {
    engine.loadData(key as ToolName, imageAnnotations[key as ToolName] as AnnotationToolData<ToolName>);
  });
}

function isImageAnnotatorPerfEnabled() {
  return (
    typeof window !== 'undefined' &&
    (new URLSearchParams(window.location.search).get('perf') === '1' ||
      window.localStorage?.getItem('labelu:image-annotator-perf') === '1')
  );
}

function countImageAnnotations(annotations: AllAnnotationMapping) {
  return Object.values(annotations).filter((item) => TOOL_NAMES.includes(item.tool as ToolName)).length;
}

function getAnnotationsSignature(annotations: AllAnnotationMapping) {
  return JSON.stringify(annotations);
}

function logImageAnnotatorPerf(label: string, payload: Record<string, unknown>) {
  if (!isImageAnnotatorPerfEnabled()) {
    return;
  }

  // eslint-disable-next-line no-console
  console.info(`[labelu:image-annotator] ${label} ${JSON.stringify(payload)}`);
}

const Wrapper = styled.div.attrs((props) => {
  return {
    ...props,
    className: 'labelu-image-annotator',
  };
})<{ $fillViewport?: boolean }>`
  display: flex;
  flex-direction: column;
  width: 100%;
  min-width: 0;
  flex-grow: 1;
  background-color: #fff;

  .labelu-video-wrapper {
    flex: 1;
  }

  ${({ $fillViewport }) =>
    $fillViewport &&
    css`
      height: 100%;
      min-height: 0;
      overflow: hidden;

      .labelu-image-annotator__content {
        flex: 1;
        min-height: 0;
        min-width: 0;
        overflow: hidden;
      }

      .labelu-image-sidebar,
      .labelu-image-attribute-panel {
        height: 100%;
      }

      .labelu-image-sidebar-cards {
        height: 100%;
      }

      /* The canvas engine may briefly report a wider intrinsic size while a
         new image is decoded.  Let the middle column shrink instead of pushing
         the attribute panel outside the clipped viewport. */
      .labelu-image-annotator__content-mid {
        min-width: 0;
        overflow: hidden;
      }

      .labelu-image-annotator__canvas {
        min-width: 0;
        overflow: hidden;
      }

      /* When the annotator fills the viewport, allow the top toolbar and its
         right-side actions to wrap instead of overflowing horizontally. */
      > div:first-child {
        height: auto;
        min-height: 56px;
        flex-wrap: wrap;
        align-items: flex-start;
        row-gap: 4px;
        padding-top: 4px;
        padding-bottom: 4px;
      }

      > div:first-child > div:first-child {
        flex-wrap: wrap;
        min-width: 0;
      }

      > div:first-child > div:last-child {
        display: flex;
        flex: 1;
        min-width: 0;
        justify-content: flex-end;
        flex-wrap: wrap;
        gap: 0.5rem;
      }
    `}
`;

const Content = styled.div`
  flex: 1;
  display: flex;
  min-height: 0;
  min-width: 0;
`;

const ContentMid = styled.div.attrs({ className: 'labelu-image-annotator__content-mid' })`
  position: relative;
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
`;

const AnnotationContainer = styled.div.attrs({ className: 'labelu-image-annotator__canvas' })`
  flex: 1;
  width: auto !important;
  height: auto !important;
  min-width: 0;
  overflow: hidden;
`;

export type { ImageAnnotatorOptions } from './hooks/useImageAnnotator';

export interface AnnotatorRef {
  getAnnotations: () => Partial<Record<AllAnnotationType, AnnotationData[] | GlobalAnnotation[]>> | undefined;
  getAnnotationsSnapshot: () =>
    | {
        annotationsWithGlobal: AllAnnotationMapping;
        sample: ImageSample | undefined;
        originalWidth: number | undefined;
        originalHeight: number | undefined;
        displayWidth: number | undefined;
        displayHeight: number | undefined;
        rotate: number | undefined;
      }
    | undefined;
  getSample: () => ImageSample | undefined;

  getEngine: () => ImageAnnotatorClass | null;
}

export interface ImageAnnotatorProps {
  samples?: ImageSample[];
  config?: ImageAnnotatorOptions & Partial<GlobalToolConfig>;

  /**
   * 预标注的标签配置
   */
  preAnnotationLabels?: Partial<
    Record<ToolName | TextAnnotationType | TagAnnotationType, ILabel[] | TextAttribute[] | EnumerableAttribute[]>
  >;

  preAnnotations?: Partial<
    Record<ToolName | TextAnnotationType | TagAnnotationType, AnnotationData[] | GlobalAnnotation[]>
  >;

  overwriteExistingAnnotations?: boolean;
  replacePreAnnotationTools?: AllAnnotationType[];

  renderSidebar?: null | (() => React.ReactNode);
  renderAttributes?: () => React.ReactNode;
  editingSample?: ImageSample;

  maxHistoryCount?: number;
  primaryColor?: string;
  toolbarExtra?: React.ReactNode;
  toolbarRight?: React.ReactNode;
  /** Fill the parent container height and let the canvas/side panels scroll internally. */
  fillViewport?: boolean;

  onError?: (error: { type: string; message: string; value?: any }) => void;

  onLoad?: (engine: ImageAnnotatorClass, url?: string | null) => void;

  onReady?: (engine: ImageAnnotatorClass, url?: string | null) => void;

  onLabelChange?: (toolName: ToolName | undefined, label: ILabel | undefined) => void;

  onToolChange?: (toolName: ToolName) => void;

  selectedLabel?: string;

  selectedTool?: ToolName;

  /**
   * 是否禁用
   */
  disabled?: boolean;

  /**
   * 标注是否可编辑，权重低于全局的disabled，粒度更细
   */
  requestEdit?: (type: EditType, payload: { toolName: ToolName; label?: string; modifiedProperty?: string }) => boolean;

  /**
   * 顶部距离
   *
   * @default 0
   */
  offsetTop?: number;
}

function ForwardAnnotator(
  {
    samples: propsSamples,
    renderSidebar,
    config,
    renderAttributes,
    disabled,
    offsetTop = 0,
    editingSample,
    maxHistoryCount = 20,
    primaryColor = '#007aff',
    toolbarExtra,
    toolbarRight,
    fillViewport,
    preAnnotationLabels,
    preAnnotations,
    overwriteExistingAnnotations,
    replacePreAnnotationTools,
    requestEdit,
    onLabelChange: propsOnLabelChange,
    selectedLabel: propsSelectedLabel,
    onToolChange: propsOnToolChange,
    selectedTool: propsSelectedTool,
    onError,
    onLoad,
    onReady,
  }: ImageAnnotatorProps,
  ref: React.Ref<AnnotatorRef>,
) {
  // ================== sample ==================
  const [currentSample, setCurrentSample] = useState<ImageSample | undefined>(editingSample);
  const samples = useMemo(() => propsSamples ?? [], [propsSamples]);
  // remember last tool
  const memorizeToolLabel = useRef<Record<ToolName, Attribute>>({} as Record<ToolName, Attribute>);
  const [attributeModalOpen, setAttributeModalOpen] = useState<boolean>(false);
  const isPreAnnotationEmpty = useMemo(() => {
    if (typeof preAnnotations === 'undefined') {
      return true;
    }

    return Object.values(preAnnotations).every((item) => item.length === 0);
  }, [preAnnotations]);

  useEffect(() => {
    setCurrentSample(editingSample || samples?.[0]);
  }, [editingSample, samples, setCurrentSample]);

  // ================== tool ==================
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [currentTool, setCurrentTool] = useState<ToolName | undefined>(propsSelectedTool);

  useEffect(() => {
    setCurrentTool(propsSelectedTool);
  }, [propsSelectedTool]);

  const tools = useMemo(() => {
    const result: ToolName[] = [];

    TOOL_NAMES.forEach((tool) => {
      if (config?.[tool]) {
        result.push(tool);
      }
    });

    return result;
  }, [config]);

  const globalToolConfig = useMemo(() => {
    return {
      text: config?.text,
      tag: config?.tag,
    };
  }, [config?.tag, config?.text]);

  const annotationOptions = useMemo(() => {
    const configWithPreAnnotationLabels = cloneDeep(config ?? {}) as ImageAnnotatorOptions & Partial<GlobalToolConfig>;

    TOOL_NAMES.forEach((tool) => {
      const preLabels = preAnnotationLabels?.[tool];
      const _labels = [...(configWithPreAnnotationLabels?.[tool]?.labels ?? [])];

      for (const preLabel of preLabels ?? []) {
        if (!_labels?.find((label) => label.value === preLabel.value)) {
          _labels?.push(preLabel as ILabel);
        }
      }

      if (preLabels) {
        if (!configWithPreAnnotationLabels![tool]) {
          configWithPreAnnotationLabels![tool] = {};
        }

        configWithPreAnnotationLabels![tool]!.labels = _labels;
      }
    });

    return {
      showOrder: true,
      requestEdit,
      ...configWithPreAnnotationLabels,
      image: {
        url: currentSample?.url ?? '',
        rotate: currentSample?.meta?.rotate ?? 0,
      },
    };
  }, [config, requestEdit, currentSample?.url, currentSample?.meta?.rotate, preAnnotationLabels]);

  const engine = useImageAnnotator(containerRef, annotationOptions);

  useEffect(() => {
    if (engine?.config) {
      engine.setEditable(!disabled);
    }
  }, [engine, disabled]);

  const [orderVisible, setOrderVisible] = useState<boolean>(true);

  const onOrderVisibleChange = useCallback(
    (value: boolean) => {
      setOrderVisible(value);

      if (engine) {
        engine.showOrder = value;
      }
    },
    [engine],
  );

  const labels = useMemo(() => {
    if (!currentTool) {
      return [];
    }

    return config?.[currentTool]?.labels ?? [];
  }, [config, currentTool]);

  const selectedLabelFromProps = useMemo(() => {
    return labels.find((item: Attribute) => item.value === propsSelectedLabel);
  }, [labels, propsSelectedLabel]);

  const [selectedLabel, setSelectedLabel] = useState<Attribute | undefined>(
    propsSelectedLabel ? selectedLabelFromProps : labels[0],
  );

  useEffect(() => {
    setSelectedLabel(selectedLabelFromProps);
  }, [selectedLabelFromProps]);

  const onToolChange = useCallback(
    (toolName: ToolName) => {
      propsOnToolChange?.(toolName);
      setCurrentTool(toolName);
    },
    [propsOnToolChange],
  );

  useEffect(() => {
    engine?.on('toolChange', onToolChange);

    return () => {
      engine?.off('toolChange', onToolChange);
    };
  }, [engine, onToolChange]);

  const [selectedAnnotation, setSelectedAnnotation] = useState<AnnotationDataInUI | undefined>();
  const currentSampleId = currentSample?.id;
  const annotationsFromSample = useMemo(() => {
    return normalizeGeometryAnnotationOrders(currentSample?.data ?? {});
  }, [currentSample]);
  const preAnnotationTools = useMemo(() => {
    if (!overwriteExistingAnnotations) {
      return new Set<AllAnnotationType>();
    }

    return new Set<AllAnnotationType>([
      ...(replacePreAnnotationTools ?? []),
      ...(Object.keys(preAnnotations ?? {}) as AllAnnotationType[]),
    ]);
  }, [overwriteExistingAnnotations, preAnnotations, replacePreAnnotationTools]);

  const lastLoadedImageUrlRef = useRef<string | null>(null);
  const lastLoadedAnnotationsKeyRef = useRef<string | null>(null);

  const selectedIndexRef = useRef<number>(-1);

  const onRedoUndo = useCallback(
    (currentAnnotations: AllAnnotationMapping) => {
      loadImageAnnotationsToEngine(engine, currentAnnotations);
    },
    [engine],
  );

  const geometrySnapshotRef = useRef<{ sampleId?: string | number; snapshot: AllAnnotationMapping }>({ snapshot: {} });
  const annotationsWithGlobalRef = useRef<AllAnnotationMapping>({});
  const previousSampleIdRef = useRef<string | number | undefined>(currentSampleId);
  const sampleSwitchPendingRef = useRef(false);

  if (previousSampleIdRef.current !== currentSampleId) {
    geometrySnapshotRef.current = { sampleId: currentSampleId, snapshot: {} };
    annotationsWithGlobalRef.current = {};
    previousSampleIdRef.current = currentSampleId;
    sampleSwitchPendingRef.current = true;
    lastLoadedAnnotationsKeyRef.current = null;
  }

  const annotationsMapping = useMemo(() => {
    const _data = currentSample?.data ?? {};
    const _preData = preAnnotations ?? {};

    const merged = normalizeGeometryMappingOrders(
      mergePreAnnotationsIntoSample(_data, _preData, isPreAnnotationEmpty, {
        overwriteExistingAnnotations,
        replaceTools: replacePreAnnotationTools,
      }),
    );

    if (overwriteExistingAnnotations) {
      const replacesGlobalTools = replacePreAnnotationTools?.some(
        (tool) => tool === 'tag' || tool === 'text',
      );
      return replacesGlobalTools
        ? merged
        : preserveGlobalAnnotations(merged, annotationsWithGlobalRef.current);
    }

    const snapshot =
      geometrySnapshotRef.current.sampleId === currentSampleId ? geometrySnapshotRef.current.snapshot : {};

    return preserveGeometryAnnotations(merged, snapshot);
  }, [
    currentSample?.data,
    currentSampleId,
    isPreAnnotationEmpty,
    overwriteExistingAnnotations,
    preAnnotations,
    replacePreAnnotationTools,
  ]);

  const [annotationsWithGlobal, updateAnnotationsWithGlobal, redo, undo, pastRef, futureRef, reset] =
    useRedoUndo<AllAnnotationMapping>(annotationsMapping, {
      maxHistory: maxHistoryCount,
      onRedo: onRedoUndo,
      onUndo: onRedoUndo,
    });

  annotationsWithGlobalRef.current = annotationsWithGlobal;
  const annotationsMappingRef = useRef(annotationsMapping);

  useEffect(() => {
    annotationsMappingRef.current = annotationsMapping;
  }, [annotationsMapping]);

  const toolsRef = useRef(tools);
  const configRef = useRef(config);
  const onReadyRef = useRef(onReady);
  const selectedToolRef = useRef(propsSelectedTool);
  const selectedLabelRef = useRef(propsSelectedLabel);

  useEffect(() => {
    toolsRef.current = tools;
    configRef.current = config;
    onReadyRef.current = onReady;
    selectedToolRef.current = propsSelectedTool;
    selectedLabelRef.current = propsSelectedLabel;
  }, [config, onReady, propsSelectedLabel, propsSelectedTool, tools]);

  useEffect(() => {
    if (!engine || !engine.renderer) {
      return;
    }

    const url = currentSample?.url ?? '';
    const rotate = currentSample?.meta?.rotate ?? 0;
    const hasBackgroundImage = !!engine.backgroundRenderer?.image;
    const reloadImage = lastLoadedImageUrlRef.current !== url || !hasBackgroundImage;
    const startAt = performance.now();
    const loadStartAt = performance.now();
    const loadTask = reloadImage ? engine.loadImage(url, { rotate }) : Promise.resolve();

    let isCancelled = false;

    loadTask.then(() => {
      if (isCancelled) return;

      const imageLoadedAt = performance.now();
      lastLoadedImageUrlRef.current = url;
      const annotationCount = countImageAnnotations(annotationsMappingRef.current);
      loadImageAnnotationsToEngine(engine, annotationsMappingRef.current);
      lastLoadedAnnotationsKeyRef.current = `${currentSampleId}:${getAnnotationsSignature(
        annotationsMappingRef.current,
      )}`;
      const annotationsLoadedAt = performance.now();
      const currentTools = toolsRef.current;
      const currentConfig = configRef.current;

      if (reloadImage && currentTools[0] && currentConfig?.[currentTools[0]]?.labels?.length) {
        engine.switch(selectedToolRef.current || currentTools[0], selectedLabelRef.current);
      }

      onReadyRef.current?.(engine, url);
      logImageAnnotatorPerf('ready', {
        sampleId: currentSampleId,
        url,
        reloadImage,
        annotationCount,
        imageMs: Math.round(imageLoadedAt - loadStartAt),
        annotationsMs: Math.round(annotationsLoadedAt - imageLoadedAt),
        totalMs: Math.round(performance.now() - startAt),
      });
    });

    return () => {
      isCancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentSampleId, currentSample?.url, currentSample?.meta?.rotate, engine]);

  useEffect(() => {
    if (!engine?.renderer || lastLoadedImageUrlRef.current !== currentSample?.url) {
      return;
    }

    const nextKey = `${currentSampleId}:${getAnnotationsSignature(annotationsMapping)}`;
    if (lastLoadedAnnotationsKeyRef.current === nextKey) {
      return;
    }

    const startAt = performance.now();
    const annotationCount = countImageAnnotations(annotationsMapping);
    loadImageAnnotationsToEngine(engine, annotationsMapping);
    lastLoadedAnnotationsKeyRef.current = nextKey;
    logImageAnnotatorPerf('annotations-updated', {
      sampleId: currentSampleId,
      annotationCount,
      annotationsMs: Math.round(performance.now() - startAt),
    });
  }, [annotationsMapping, currentSample?.url, currentSampleId, engine]);

  useLayoutEffect(() => {
    if (sampleSwitchPendingRef.current && annotationsWithGlobal !== annotationsMapping) {
      return;
    }

    sampleSwitchPendingRef.current = false;
    const geometryTools = new Set<AllAnnotationType>(['rect', 'polygon', 'point', 'line']);
    const snapshot: AllAnnotationMapping = {};

    Object.values(annotationsWithGlobal).forEach((annotation) => {
      if (geometryTools.has(annotation.tool as AllAnnotationType)) {
        snapshot[annotation.id] = annotation;
      }
    });
    geometrySnapshotRef.current = { sampleId: currentSampleId, snapshot };
  }, [annotationsMapping, annotationsWithGlobal, currentSampleId]);

  const prevPreAnnotationsRef = useRef(preAnnotations);
  const prevPreAnnotationsSampleIdRef = useRef(currentSampleId);
  useEffect(() => {
    const sampleChanged = prevPreAnnotationsSampleIdRef.current !== currentSampleId;
    prevPreAnnotationsSampleIdRef.current = currentSampleId;

    if (!engine?.renderer) {
      prevPreAnnotationsRef.current = preAnnotations;
      return;
    }

    const preAnnotationsChanged = prevPreAnnotationsRef.current !== preAnnotations;
    prevPreAnnotationsRef.current = preAnnotations;

    if (sampleChanged || !preAnnotationsChanged) {
      return;
    }

    loadImageAnnotationsToEngine(engine, annotationsMappingRef.current);
  }, [currentSampleId, engine, preAnnotations]);

  const onAnnotationClear = useCallback(() => {
    engine?.clearData();
    updateAnnotationsWithGlobal((pre) => {
      const globalAnnotations: AllAnnotationMapping = {};

      Object.values(pre ?? {}).forEach((annotation) => {
        if (!TOOL_NAMES.includes(annotation.tool as ToolName)) {
          globalAnnotations[annotation.id] = annotation;
        }
      });

      return globalAnnotations;
    });
    setSelectedAnnotation(undefined);
  }, [engine, updateAnnotationsWithGlobal]);

  const onAnnotationRemove = useCallback(
    (_annotation: AnnotationWithTool) => {
      if (!_annotation?.tool) {
        return;
      }

      updateAnnotationsWithGlobal((pre) => (pre ? omit(pre, _annotation.id) : pre));
      setSelectedAnnotation(undefined);
    },
    [updateAnnotationsWithGlobal],
  );

  const onAnnotationsRemove = useCallback(
    (_annotations: AnnotationWithTool[]) => {
      updateAnnotationsWithGlobal((pre) => {
        let newAnnotationWithGlobal = pre;

        if (newAnnotationWithGlobal) {
          _annotations.forEach((item) => {
            newAnnotationWithGlobal = omit(newAnnotationWithGlobal!, item.id);
          });
        }

        return newAnnotationWithGlobal;
      });

      setSelectedAnnotation(undefined);
    },
    [updateAnnotationsWithGlobal],
  );

  const sortedImageAnnotations = useMemo(() => {
    const result = Object.values(annotationsWithGlobal).filter((item) => {
      return TOOL_NAMES.includes(item.tool as ToolName);
    }) as AnnotationDataInUI[];

    result.sort((a, b) => a.order - b.order);

    return result;
  }, [annotationsWithGlobal]);

  const onAnnotationsChange = useCallback(
    (_annotations: AnnotationWithTool[]) => {
      const annotationGroupByTool: AllAnnotationMapping = {};

      _annotations.forEach((item) => {
        annotationGroupByTool[item.id] = item;
      });

      updateAnnotationsWithGlobal((pre) => {
        return {
          ...pre!,
          ...annotationGroupByTool,
        };
      });
    },
    [updateAnnotationsWithGlobal],
  );

  const onAnnotationChange = useCallback(
    (_annotation: AnnotationWithTool, skipHistory?: boolean) => {
      updateAnnotationsWithGlobal((pre) => {
        return {
          ...pre!,
          [_annotation.id]: _annotation,
        };
      }, skipHistory);
    },
    [updateAnnotationsWithGlobal],
  );

  useEffect(() => {
    const handleOpenAttributePanel = (e: MouseEvent, selectedId: string) => {
      if (!selectedId) {
        return;
      }

      const annotation = sortedImageAnnotations.find((item) => item.id === selectedId);
      // 按住shift键时，调起属性框
      if (engine?.keyboard?.Shift && annotation) {
        e.preventDefault();
        e.stopPropagation();
        const labelConfig = labels.find((item: Attribute) => item.value === annotation?.label);
        openAttributeModal({
          labelValue: annotation.label,
          e,
          openModalAnyway: true,
          engine,
          labelConfig,
        });
      }
    };

    engine?.on('rightClick', handleOpenAttributePanel);

    return () => {
      engine?.off('rightClick', handleOpenAttributePanel);
    };
  }, [engine, labels, sortedImageAnnotations]);

  useEffect(() => {
    const handleUnSelect = () => {
      setSelectedAnnotation(undefined);
    };

    engine?.on('unselect', handleUnSelect);

    return () => {
      engine?.off('unselect', handleUnSelect);
    };
  }, [engine]);

  const labelMappingByTool = useMemo(() => {
    const mapping: Record<string, Record<string, Attribute>> = {};

    TOOL_NAMES.forEach((key) => {
      const _labels: Attribute[] = config?.[key]?.labels ?? [];
      mapping[key] = {};
      _labels.reduce((acc, cur) => {
        acc[cur.value] = cur;
        return acc;
      }, mapping[key]);
    });

    return mapping;
  }, [config]);

  const preLabelsMappingByTool = useMemo(() => {
    const mapping: Record<string, Record<string, Attribute>> = {};

    [...TOOL_NAMES, 'tag' as const, 'text' as const].forEach((key) => {
      const _labels: Attribute[] = (preAnnotationLabels?.[key] as unknown as Attribute[]) ?? [];
      mapping[key] = {};
      _labels.reduce((acc, cur) => {
        acc[cur.value] = cur;
        return acc;
      }, mapping[key]);
    });

    return mapping;
  }, [preAnnotationLabels]);

  const onLabelChange = useCallback(
    (label: ILabel) => {
      engine?.setLabel(label.value);
      engine?.setAttributes({});
      setSelectedLabel(label);
      propsOnLabelChange?.(currentTool, label);

      if (currentTool) {
        memorizeToolLabel.current[currentTool] = label;
      }
    },
    [currentTool, engine, propsOnLabelChange],
  );

  // effects
  useEffect(() => {
    // 删除标记
    const handleDelete = (annotation: AnnotationData) => {
      updateAnnotationsWithGlobal((pre) => {
        return omit(pre!, annotation.id);
      });
      setSelectedAnnotation((pre) => {
        if (pre?.id === annotation.id) {
          return undefined;
        }

        return pre;
      });
    };

    engine?.on('delete', handleDelete);

    return () => {
      engine?.off('delete', handleDelete);
    };
  }, [annotationsWithGlobal, engine, updateAnnotationsWithGlobal]);

  useEffect(() => {
    const handleRelationDelete = (relations: AnnotationData[]) => {
      updateAnnotationsWithGlobal((pre) => {
        return relations.reduce((acc, item) => {
          return omit(acc, item.id);
        }, pre!);
      });
    };

    engine?.on('relatedRelationDelete', handleRelationDelete);

    return () => {
      engine?.off('relatedRelationDelete', handleRelationDelete);
    };
  }, [annotationsWithGlobal, engine, updateAnnotationsWithGlobal]);

  useEffect(() => {
    const handleAttributesChange = (annotation: AnnotationData) => {
      if (!engine) {
        return;
      }

      setSelectedAnnotation({
        ...annotation,
        tool: engine.activeToolName!,
      });
    };

    engine?.on('attributesChange', handleAttributesChange);

    return () => {
      engine?.off('attributesChange', handleAttributesChange);
    };
  }, [engine, labelMappingByTool, onAnnotationsChange]);

  useEffect(() => {
    const handleAnnotationAdded = (annotations: AnnotationData[]) => {
      if (!engine) {
        return;
      }

      onAnnotationsChange(addToolNameToAnnotationData(engine!.getDataByTool()));
      setSelectedAnnotation({
        // 默认选中第一个
        ...annotations[0],
        tool: engine.activeToolName!,
      });
    };
    // 添加标记
    engine?.on('add', handleAnnotationAdded);

    return () => {
      engine?.off('add', handleAnnotationAdded);
    };
  }, [engine, onAnnotationsChange]);

  useEffect(() => {
    let rafId: number | null = null;
    const _onAnnotationsChange = () => {
      if (rafId) return;
      rafId = requestAnimationFrame(() => {
        rafId = null;
        onAnnotationsChange(addToolNameToAnnotationData(engine!.getDataByTool()));
      });
    };

    // 标记变更，如移动，编辑等
    engine?.on('change', _onAnnotationsChange);

    return () => {
      if (rafId) cancelAnimationFrame(rafId);
      engine?.off('change', _onAnnotationsChange);
    };
  }, [engine, onAnnotationsChange]);

  useEffect(() => {
    const handleLabelChange = (label: string) => {
      if (label === selectedLabel?.value && engine?.activeToolName === currentTool) {
        return;
      }

      onAnnotationsChange(addToolNameToAnnotationData(engine!.getDataByTool()));
      const _label = engine?.activeToolName ? labelMappingByTool[engine.activeToolName][label] : undefined;

      setSelectedLabel(_label);
      propsOnLabelChange?.(engine!.activeToolName!, _label);
    };
    // 改变标签
    engine?.on('labelChange', handleLabelChange);

    return () => {
      engine?.off('labelChange', handleLabelChange);
    };
  }, [currentTool, engine, labelMappingByTool, onAnnotationsChange, propsOnLabelChange, selectedLabel?.value]);

  useEffect(() => {
    const handleSelectAnnotation = (annotation: AnnotationData, toolName: ToolName) => {
      // 选中了隐藏的标记，需要显示
      engine?.toggleAnnotationsVisibility(toolName, [annotation.id], true);
      if (annotation.label) {
        if (engine?.activeToolName === toolName) {
          engine.setLabel(annotation.label);
        } else {
          engine?.switch(toolName, annotation.label);
        }
      }
      const newAnnotation = {
        ...annotation,
        tool: toolName,
        visible: true,
      };
      setSelectedAnnotation(newAnnotation);
      onAnnotationChange(newAnnotation, true);
      selectedIndexRef.current = sortedImageAnnotations.findIndex((item) => item.id === annotation.id);
    };

    engine?.on('select', handleSelectAnnotation);

    return () => {
      engine?.off('select', handleSelectAnnotation);
    };
  }, [engine, labels, onAnnotationChange, sortedImageAnnotations]);

  useEffect(() => {
    if (!onError) {
      return;
    }

    engine?.on('error', onError);

    return () => {
      engine?.off('error', onError);
    };
  }, [engine, onError]);

  useEffect(() => {
    if (!onLoad) {
      return;
    }

    const handleOnLoad = (_image: HTMLImageElement, url?: string | null) => {
      onLoad(engine!, url);
    };

    engine?.on('backgroundImageLoaded', handleOnLoad);

    return () => {
      engine?.off('backgroundImageLoaded', handleOnLoad);
    };
  }, [engine, onLoad]);

  // 重置历史记录
  useEffect(() => {
    setSelectedAnnotation(undefined);
    reset();
  }, [currentSampleId, reset]);

  // ================== sample ==================

  const onSampleSelect = useCallback(
    (sample: ImageSample) => {
      setCurrentSample(sample);
      setSelectedAnnotation(undefined);
      reset();
    },
    [reset, setCurrentSample],
  );

  // ================== 快捷键 ==================
  useHotkeys(
    'escape',
    () => {
      engine?.selectAnnotation(undefined, undefined);
    },
    {
      preventDefault: true,
    },
    [engine],
  );

  // 上一个标记
  useHotkeys(
    'ArrowUp',
    () => {
      selectedIndexRef.current = Math.max(selectedIndexRef.current - 1, 0);
      engine?.selectAnnotation(
        sortedImageAnnotations[selectedIndexRef.current].tool,
        sortedImageAnnotations[selectedIndexRef.current].id,
      );
    },
    {
      keyup: true,
      keydown: false,
      enabled: !disabled && !attributeModalOpen,
    },
    [sortedImageAnnotations, engine, disabled, attributeModalOpen],
  );

  // 下一个标记
  useHotkeys(
    'ArrowDown',
    () => {
      selectedIndexRef.current = Math.min(selectedIndexRef.current + 1, sortedImageAnnotations.length - 1);
      engine?.selectAnnotation(
        sortedImageAnnotations[selectedIndexRef.current].tool,
        sortedImageAnnotations[selectedIndexRef.current].id,
      );
    },
    {
      keyup: true,
      keydown: false,
      enabled: !disabled && !attributeModalOpen,
    },
    [sortedImageAnnotations, engine, disabled, attributeModalOpen],
  );

  // 1 ~ 9 设置标签
  useHotkeys(
    '1,2,3,4,5,6,7,8,9',
    (e) => {
      const index = Number(e.key) - 1;

      if (index < labels.length) {
        engine?.setAttributes({});
        engine?.setLabel(labels[index].value);
        // 清空上一次的属性
        openAttributeModal({
          labelValue: labels[index].value,
          engine,
          labelConfig: labels[index],
        });
      }
    },
    {
      enabled: !disabled && !attributeModalOpen,
    },
    [attributeModalOpen, engine, labels, disabled],
  );

  useImperativeHandle(ref, () => ({
    getAnnotations: () => {
      const result: Partial<Record<AllAnnotationType, AnnotationData[] | GlobalAnnotation[]>> = {};

      Object.values(annotationsWithGlobal).forEach((item) => {
        // @ts-ignore
        // eslint-disable-next-line no-unused-vars, @typescript-eslint/no-unused-vars
        const { visible, tool, ...rest } = item;
        const _toolName = tool || (item as GlobalAnnotation).type;

        if (!result[_toolName]) {
          result[_toolName] = [];
        }

        result![_toolName]!.push(rest as any);
      });

      return result;
    },
    getAnnotationsSnapshot: () => ({
      annotationsWithGlobal,
      sample: currentSample,
      originalWidth: currentSample?.meta?.originalWidth ?? engine?.backgroundRenderer?.image?.width,
      originalHeight: currentSample?.meta?.originalHeight ?? engine?.backgroundRenderer?.image?.height,
      displayWidth: engine?.backgroundRenderer?.image?.width,
      displayHeight: engine?.backgroundRenderer?.image?.height,
      rotate: engine?.backgroundRenderer?.rotate,
    }),
    getSample: () => currentSample,

    getEngine: () => engine,
  }));

  const annotationContextValue = useMemo(
    () => ({
      annotationsWithGlobal,
      sortedImageAnnotations,
      selectedAnnotation,
      allAnnotationsMapping: annotationsMapping,
      onAnnotationChange,
      onAnnotationsChange,
      onAnnotationRemove,
      onAnnotationsRemove,
      onAnnotationClear,
      orderVisible,
      disabled,
      preAnnotationsWithGlobal: preAnnotations,
      onOrderVisibleChange,
    }),
    [
      annotationsWithGlobal,
      sortedImageAnnotations,
      selectedAnnotation,
      annotationsMapping,
      onAnnotationChange,
      onAnnotationsChange,
      onAnnotationRemove,
      onAnnotationsRemove,
      onAnnotationClear,
      orderVisible,
      preAnnotations,
      disabled,
      onOrderVisibleChange,
    ],
  );

  const toolContextValue = useMemo(
    () => ({
      engine: engine!,
      currentTool,
      config,
      selectedLabel,
      memorizeToolLabel,
      requestEdit,
      onLabelChange,
      tools,
      globalToolConfig,
      labelMapping: labelMappingByTool,
      preLabelMapping: preLabelsMappingByTool,
      labels,
      attributeModalOpen,
      setAttributeModalOpen,
    }),
    [
      engine,
      currentTool,
      config,
      selectedLabel,
      requestEdit,
      onLabelChange,
      tools,
      globalToolConfig,
      labelMappingByTool,
      preLabelsMappingByTool,
      labels,
      attributeModalOpen,
      setAttributeModalOpen,
    ],
  );

  const historyContextValue = useMemo(
    () => ({
      redo,
      undo,
      pastRef,
      futureRef,
    }),
    [futureRef, pastRef, redo, undo],
  );

  const sampleContextValue = useMemo(() => {
    return {
      currentSample,
      samples,
      onSampleSelect,
    };
  }, [currentSample, onSampleSelect, samples]);

  const attributeSide = useMemo(() => {
    return typeof renderAttributes === 'function' ? renderAttributes() : <AttributePanel />;
  }, [renderAttributes]);

  return (
    <SampleContext.Provider value={sampleContextValue}>
      <AnnotationContext.Provider value={annotationContextValue}>
        <ToolContext.Provider value={toolContextValue}>
          <HistoryContext.Provider value={historyContextValue}>
            {/* @ts-ignore */}
            <Wrapper
              style={{ '--color-primary': primaryColor, '--offset-top': `${offsetTop}px` } as React.CSSProperties}
              $fillViewport={fillViewport}
            >
              <AnnotatorToolbar extra={toolbarExtra} right={toolbarRight} />
              <LabelSection />
              <Content className="labelu-image-annotator__content">
                <Sidebar renderSidebar={renderSidebar} />
                <ContentMid>
                  <AnnotationContainer ref={containerRef} />
                  <Footer />
                </ContentMid>
                {attributeSide}
              </Content>
            </Wrapper>
          </HistoryContext.Provider>
        </ToolContext.Provider>
      </AnnotationContext.Provider>
    </SampleContext.Provider>
  );
}

export const Annotator = forwardRef<AnnotatorRef, ImageAnnotatorProps>(ForwardAnnotator);
