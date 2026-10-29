import _ from 'lodash';

export const ANNOTATION_DISPLAY_MAX_SIDE = 1672;

function roundDisplaySide(value: number, scale: number) {
  return Math.max(1, Math.floor(value * scale + 0.5));
}

export function getAnnotationDisplaySize(width?: number | null, height?: number | null) {
  if (!width || !height) {
    return { width, height };
  }

  const maxSide = Math.max(width, height);
  if (maxSide <= ANNOTATION_DISPLAY_MAX_SIDE) {
    return { width, height };
  }

  const scale = ANNOTATION_DISPLAY_MAX_SIDE / maxSide;
  return {
    width: roundDisplaySide(width, scale),
    height: roundDisplaySide(height, scale),
  };
}

function getAnnotationDisplayScale(width?: number | null, height?: number | null) {
  if (!width || !height) {
    return { x: 1, y: 1 };
  }

  const displaySize = getAnnotationDisplaySize(width, height);
  const displayWidth = displaySize.width;
  const displayHeight = displaySize.height;

  return {
    x: displayWidth && displayWidth !== width ? displayWidth / width : 1,
    y: displayHeight && displayHeight !== height ? displayHeight / height : 1,
  };
}

function scalePoint<T extends { x?: number; y?: number }>(point: T, scale: { x: number; y: number }): T {
  if (scale.x === 1 && scale.y === 1) {
    return point;
  }

  return {
    ...point,
    x: typeof point.x === 'number' ? point.x * scale.x : point.x,
    y: typeof point.y === 'number' ? point.y * scale.y : point.y,
  };
}

function scaleGeometryData(data: Record<string, any>, scale: { x: number; y: number }) {
  const hasGeometry = ['rect', 'point', 'line', 'polygon', 'cuboid'].some((toolName) => Array.isArray(data[toolName]));

  if ((scale.x === 1 && scale.y === 1) || !hasGeometry) {
    return data;
  }

  const result = { ...data };
  const scaleList = (items: any[] | undefined, mapper: (item: any) => any) => items?.map(mapper) ?? items;
  const scaleCuboidVertex = (vertex: Record<string, any> | undefined) => {
    if (!vertex) {
      return vertex;
    }

    return _.mapValues(vertex, (point) => scalePoint(point, scale));
  };

  if (Array.isArray(data.rect)) {
    result.rect = scaleList(data.rect, (item) => ({
      ...scalePoint(item, scale),
      width: typeof item.width === 'number' ? item.width * scale.x : item.width,
      height: typeof item.height === 'number' ? item.height * scale.y : item.height,
    }));
  }

  if (Array.isArray(data.point)) {
    result.point = scaleList(data.point, (item) => scalePoint(item, scale));
  }

  if (Array.isArray(data.line)) {
    result.line = scaleList(data.line, (item) => ({
      ...item,
      points: item.points?.map((point: any) => scalePoint(point, scale)),
      controlPoints: item.controlPoints?.map((point: any) => scalePoint(point, scale)),
    }));
  }

  if (Array.isArray(data.polygon)) {
    result.polygon = scaleList(data.polygon, (item) => ({
      ...item,
      points: item.points?.map((point: any) => scalePoint(point, scale)),
      controlPoints: item.controlPoints?.map((point: any) => scalePoint(point, scale)),
    }));
  }

  if (Array.isArray(data.cuboid)) {
    result.cuboid = scaleList(data.cuboid, (item) => ({
      ...item,
      front: scaleCuboidVertex(item.front),
      back: scaleCuboidVertex(item.back),
    }));
  }

  return result;
}

function getAxisScale(from?: number | null, to?: number | null) {
  if (!from || !to) {
    return 1;
  }

  return to / from;
}

function toPositiveNumber(value: unknown): number | undefined {
  const num = Number(value);
  return Number.isFinite(num) && num > 0 ? num : undefined;
}

export function resolveImageAnnotationSourceSize(
  annotationWidth?: unknown,
  annotationHeight?: unknown,
  fallbackWidth?: unknown,
  fallbackHeight?: unknown,
) {
  return {
    width: toPositiveNumber(annotationWidth) ?? toPositiveNumber(fallbackWidth),
    height: toPositiveNumber(annotationHeight) ?? toPositiveNumber(fallbackHeight),
  };
}

function pointFitsWithinSize(point: any, width: number, height: number, epsilon: number) {
  const x = typeof point?.x === 'number' ? point.x : undefined;
  const y = typeof point?.y === 'number' ? point.y : undefined;

  if (x === undefined || y === undefined) {
    return true;
  }

  return x >= -epsilon && y >= -epsilon && x <= width + epsilon && y <= height + epsilon;
}

export function imageAnnotationsFitWithinSize(
  data: Record<string, any>,
  width?: number | null,
  height?: number | null,
  epsilon = 1e-6,
) {
  if (!width || !height) {
    return false;
  }

  let hasGeometry = false;
  const allFit = (items: any[] | undefined, check: (item: any) => boolean) => {
    if (!Array.isArray(items)) {
      return true;
    }

    hasGeometry = hasGeometry || items.length > 0;
    return items.every(check);
  };
  const pointsFit = (points: any[] | undefined) => allFit(points, (point) => pointFitsWithinSize(point, width, height, epsilon));
  const cuboidVertexFits = (vertex: Record<string, any> | undefined) => {
    if (!vertex) {
      return true;
    }

    return Object.values(vertex).every((point) => pointFitsWithinSize(point, width, height, epsilon));
  };

  return (
    allFit(data.rect, (item) => {
      const x = typeof item.x === 'number' ? item.x : 0;
      const y = typeof item.y === 'number' ? item.y : 0;
      const itemWidth = typeof item.width === 'number' ? item.width : 0;
      const itemHeight = typeof item.height === 'number' ? item.height : 0;
      return (
        x >= -epsilon &&
        y >= -epsilon &&
        x + itemWidth <= width + epsilon &&
        y + itemHeight <= height + epsilon
      );
    }) &&
    allFit(data.point, (item) => pointFitsWithinSize(item, width, height, epsilon)) &&
    allFit(data.line, (item) => pointsFit(item.points) && pointsFit(item.controlPoints)) &&
    allFit(data.polygon, (item) => pointsFit(item.points) && pointsFit(item.controlPoints)) &&
    allFit(data.cuboid, (item) => cuboidVertexFits(item.front) && cuboidVertexFits(item.back)) &&
    hasGeometry
  );
}

export function scaleImageAnnotationsBetweenSizes<T extends Record<string, any>>(
  data: T,
  fromWidth?: number | null,
  fromHeight?: number | null,
  toWidth?: number | null,
  toHeight?: number | null,
): T {
  return scaleGeometryData(data, {
    x: getAxisScale(fromWidth, toWidth),
    y: getAxisScale(fromHeight, toHeight),
  }) as T;
}

export function scaleImageAnnotationsForDisplay<T extends Record<string, any>>(
  data: T,
  originalWidth?: number | null,
  originalHeight?: number | null,
): T {
  return scaleGeometryData(data, getAnnotationDisplayScale(originalWidth, originalHeight)) as T;
}

export function scaleImageAnnotationsToOriginal<T extends Record<string, any>>(
  data: T,
  originalWidth?: number | null,
  originalHeight?: number | null,
  displayWidth?: number | null,
  displayHeight?: number | null,
): T {
  if (displayWidth && displayHeight && originalWidth && originalHeight) {
    return scaleImageAnnotationsBetweenSizes(
      data,
      displayWidth,
      displayHeight,
      originalWidth,
      originalHeight,
    );
  }

  const scale = getAnnotationDisplayScale(originalWidth, originalHeight);

  return scaleGeometryData(data, {
    x: scale.x === 1 ? 1 : 1 / scale.x,
    y: scale.y === 1 ? 1 : 1 / scale.y,
  }) as T;
}
