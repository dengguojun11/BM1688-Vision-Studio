import styled from 'styled-components';
import { FlexLayout } from '@labelu/components-react';

import { PRODUCT_LOGO_SRC } from '@/utils/product';

export const NavigationWrapper = styled(FlexLayout)`
  height: var(--header-height);
  border-bottom: solid var(--color-border-secondary) 1px;
  box-sizing: border-box;

  @media (max-width: 48rem) {
    padding-inline: 0.75rem !important;
    justify-content: flex-start;
    gap: 1rem;
    overflow-x: auto;
    overflow-y: hidden;
  }
`;

export const LabeluLogo = styled.img.attrs({ src: PRODUCT_LOGO_SRC, alt: '7G 标志' })`
  width: 45px;
  height: 32px;
  display: block;
  object-fit: contain;
`;
