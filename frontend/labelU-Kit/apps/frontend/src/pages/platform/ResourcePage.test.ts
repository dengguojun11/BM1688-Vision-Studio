import { describe, expect, it } from 'vitest';

import { trainingRecipeLabel } from './ResourcePage';

describe('trainingRecipeLabel', () => {
  it('shows the plate localization contract without implying OCR', () => {
    expect(trainingRecipeLabel('plate_detection_v1', 1)).toBe('车牌框 + 四角点（仅定位）（第 1 版）');
  });

  it('keeps legacy plate recognition recipes readable', () => {
    expect(trainingRecipeLabel('plate_recognition')).toBe('历史复合车牌识别（兼容）');
  });
});
