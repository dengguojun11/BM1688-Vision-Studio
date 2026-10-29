import { describe, expect, it } from 'vitest';

import { PRODUCT_TITLE, PRODUCT_TITLE_ZH, formatDocumentTitle } from '@/utils/product';

describe('7GAI document titles', () => {
  it('uses the 7GAI product title for the root document', () => {
    expect(formatDocumentTitle()).toBe(PRODUCT_TITLE);
    expect(PRODUCT_TITLE).toBe('7GAI 进化平台');
    expect(PRODUCT_TITLE_ZH).toBe('7GAI 进化平台');
  });

  it('keeps route context while using the new product name', () => {
    expect(formatDocumentTitle('工作台')).toBe('工作台 - 7GAI 进化平台');
  });
});
