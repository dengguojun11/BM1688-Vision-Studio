import type { AnnotatorOptions } from '@labelu/image';
import { Annotator } from '@labelu/image';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import ResizeObserver from 'resize-observer-polyfill';

export type ImageAnnotatorOptions = Omit<AnnotatorOptions, 'container'>;

const getEngineOptionsSignature = (options: ImageAnnotatorOptions) => {
  const { image: _image, ...engineOptions } = options || {};

  return JSON.stringify(engineOptions);
};

const isImageAnnotatorPerfEnabled = () => {
  return (
    typeof window !== 'undefined' &&
    (new URLSearchParams(window.location.search).get('perf') === '1' ||
      window.localStorage?.getItem('labelu:image-annotator-perf') === '1')
  );
};

const logImageAnnotatorPerf = (label: string, payload: Record<string, unknown>) => {
  if (!isImageAnnotatorPerfEnabled()) {
    return;
  }

  // eslint-disable-next-line no-console
  console.info(`[labelu:image-annotator] ${label}`, payload);
};

export const useImageAnnotator = (containerRef: React.RefObject<HTMLDivElement>, options: ImageAnnotatorOptions) => {
  const [engine, setAnnotationEngine] = useState<Annotator | null>(null);
  const [optionsState, setOptionsState] = useState<ImageAnnotatorOptions>(options);
  const optionsSignatureRef = useRef<string>(getEngineOptionsSignature(options));
  const ignoredFirstRun = useRef<boolean>(true);

  useLayoutEffect(() => {
    const handleResize = () => {
      if (!containerRef.current || !engine) {
        return;
      }

      const width = containerRef.current.clientWidth;
      const height = containerRef.current.clientHeight;

      engine.resize(width, height);

      // 需要加载图片后才能居中，否则标注坐标计算会有误差
      if (engine.backgroundRenderer?.image) {
        engine.center();
      }
    };

    const resizeObserver = new ResizeObserver((entries) => {
      if (entries.length === 0) {
        return;
      }

      // 忽略第一次运行
      if (ignoredFirstRun.current) {
        ignoredFirstRun.current = false;

        return;
      }

      handleResize();
    });

    resizeObserver.observe(containerRef.current as HTMLElement);

    // 监听 devicePixelRatio 变化（浏览器缩放、全屏切换等场景）
    let dprMediaQuery: MediaQueryList | null = null;
    const handleDprChange = () => {
      handleResize();

      // dpr 变化后需要重新监听新的 dpr 值
      dprMediaQuery?.removeEventListener('change', handleDprChange);
      dprMediaQuery = window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`);
      dprMediaQuery.addEventListener('change', handleDprChange);
    };

    dprMediaQuery = window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`);
    dprMediaQuery.addEventListener('change', handleDprChange);

    return () => {
      resizeObserver.disconnect();
      dprMediaQuery?.removeEventListener('change', handleDprChange);
    };
  }, [containerRef, engine]);

  useEffect(() => {
    const nextSignature = getEngineOptionsSignature(options);
    if (nextSignature === optionsSignatureRef.current) {
      return;
    }

    optionsSignatureRef.current = nextSignature;
    setOptionsState(options);
  }, [options]);

  useLayoutEffect(() => {
    if (!containerRef.current) {
      return;
    }

    const startAt = performance.now();
    const currentEngine = new Annotator({
      ...(optionsState || {}),
      container: containerRef.current!,
      width: containerRef.current!.clientWidth,
      height: containerRef.current!.clientHeight,
    });
    logImageAnnotatorPerf('engine-created', {
      width: containerRef.current!.clientWidth,
      height: containerRef.current!.clientHeight,
      ms: Math.round(performance.now() - startAt),
    });

    setAnnotationEngine(currentEngine);

    return () => {
      const destroyStartAt = performance.now();
      currentEngine.destroy();
      logImageAnnotatorPerf('engine-destroyed', {
        ms: Math.round(performance.now() - destroyStartAt),
      });
      setAnnotationEngine((pre) => (pre === currentEngine ? null : pre));
    };
  }, [optionsState, containerRef]);

  return engine;
};
