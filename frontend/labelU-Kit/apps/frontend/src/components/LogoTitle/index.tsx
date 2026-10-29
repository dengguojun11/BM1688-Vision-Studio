import styled from 'styled-components';
import { useTranslation } from '@labelu/i18n';
import { FlexLayout } from '@labelu/components-react';

import { PRODUCT_LOGO_SRC } from '@/utils/product';

// Limit this static brand copy to the two translation keys it renders. The
// application uses TypeScript 4.8, which cannot expand the full i18next hook
// overloads here without exceeding its generic instantiation depth.
type BrandTextKey = 'labelUDescription' | 'labelUKeywords';
const useBrandTranslation = useTranslation as unknown as () => {
  t: (key: BrandTextKey) => string;
};

const Logo = styled.img.attrs({ src: PRODUCT_LOGO_SRC, alt: '7G 标志' })`
  width: 62px;
  height: 44px;
  display: block;
  object-fit: contain;
`;

const Description = styled.span`
  text-align: center;
  color: var(--color-text-secondary);
`;

const LogoTitle = () => {
  const { t } = useBrandTranslation();
  return (
    <FlexLayout flex="column" items="center" gap="1rem">
      <Logo />
      <Description>
        <div>{t('labelUDescription')}</div>
        <div>{t('labelUKeywords')}</div>
      </Description>
    </FlexLayout>
  );
};
export default LogoTitle;
