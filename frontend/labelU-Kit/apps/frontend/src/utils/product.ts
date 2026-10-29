export const PRODUCT_TITLE = '7GAI 进化平台';
export const PRODUCT_TITLE_ZH = '7GAI 进化平台';
export const PRODUCT_LOGO_SRC = '/brand/7g-logo-purple.png';

export function formatDocumentTitle(title?: string) {
  return title ? `${title} - ${PRODUCT_TITLE}` : PRODUCT_TITLE;
}
