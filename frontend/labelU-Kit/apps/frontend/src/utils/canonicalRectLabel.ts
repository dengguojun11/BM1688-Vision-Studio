export type TaskLabelOption = { value: string; key?: string };

export function lowercaseFirstLetter(label: string): string {
  if (label.length >= 1 && label[0] >= 'A' && label[0] <= 'Z') {
    return label[0].toLowerCase() + label.slice(1);
  }

  return label;
}

export function canonicalRectLabel(label: string, taskLabels: TaskLabelOption[]): string {
  if (!label) {
    return label;
  }

  const lowered = lowercaseFirstLetter(label);

  for (const opt of taskLabels) {
    const value = opt.value;
    if (value === label || value === lowered) {
      return value;
    }
    if (value.toLowerCase() === label.toLowerCase()) {
      return value;
    }
  }

  return lowered;
}

export function getRectLabelOptionsFromTaskConfig(taskConfig: unknown): TaskLabelOption[] {
  const tools = (taskConfig as { tools?: { tool?: string; config?: { attributes?: TaskLabelOption[] } }[] })
    ?.tools;
  if (!Array.isArray(tools)) {
    return [];
  }

  const rectTool = tools.find((t) => t.tool === 'rectTool' || t.tool === 'rect');
  return rectTool?.config?.attributes ?? [];
}

export function normalizeImageAnnotationRectLabels<T extends Record<string, unknown>>(
  annotations: T,
  taskLabels: TaskLabelOption[],
): T {
  if (!taskLabels.length) {
    return annotations;
  }

  const rects = annotations.rect;
  if (!Array.isArray(rects)) {
    return annotations;
  }

  return {
    ...annotations,
    rect: rects.map((item) => {
      const row = item as { label?: string };
      return {
        ...row,
        label: canonicalRectLabel(String(row.label ?? ''), taskLabels),
      };
    }),
  };
}
