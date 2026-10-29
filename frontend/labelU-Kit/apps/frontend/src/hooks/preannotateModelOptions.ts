import type { TagtoolPreannotateModelResponse, YoloPreannotateModelResponse } from '@/api/types';

export type PreannotateType = 'yolo' | 'tagtool' | 'locateanything' | 'vlx_seek' | 'rex_omni' | 'ai_detection';
export type PreannotateModel = YoloPreannotateModelResponse | TagtoolPreannotateModelResponse;

export function recommendedConf(model: PreannotateModel) {
  const value = (model as YoloPreannotateModelResponse).recommended_conf;
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function sortPreannotateModels(models: PreannotateModel[], type: PreannotateType) {
  // The detector endpoint ranks AI candidates by task-label coverage and
  // validation quality. Keep that order for automatic selection; a confidence
  // threshold is not a model-quality score.
  if (type !== 'yolo') {
    return models;
  }
  return [...models].sort((left, right) => {
    const leftConf = recommendedConf(left);
    const rightConf = recommendedConf(right);
    if (leftConf === null) return rightConf === null ? 0 : 1;
    if (rightConf === null) return -1;
    return rightConf - leftConf;
  });
}

export function modelLabel(model: PreannotateModel, type: PreannotateType) {
  const category = model.category || (model.kind === 'trained' ? '本系统训练' : '未分类');
  if (type !== 'yolo' && type !== 'ai_detection') {
    return `${category} / ${model.title}`;
  }
  const conf = recommendedConf(model);
  const imgsz = typeof (model as YoloPreannotateModelResponse).imgsz === 'number'
    ? ` · 输入尺寸 ${(model as YoloPreannotateModelResponse).imgsz}`
    : '';
  return `${category} / ${model.title} · 置信度 ${conf === null ? '未评估' : conf.toFixed(2)}${imgsz}`;
}
