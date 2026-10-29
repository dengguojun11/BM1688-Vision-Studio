export interface PreviewSize {
  width: number;
  height: number;
}

export function fitContainSize(
  containerWidth: number,
  containerHeight: number,
  imageWidth: number,
  imageHeight: number,
): PreviewSize {
  const values = [containerWidth, containerHeight, imageWidth, imageHeight];
  if (values.some((value) => !Number.isFinite(value) || value <= 0)) {
    return { width: 0, height: 0 };
  }

  const scale = Math.min(containerWidth / imageWidth, containerHeight / imageHeight);
  return {
    width: imageWidth * scale,
    height: imageHeight * scale,
  };
}
