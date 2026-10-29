import styled from 'styled-components';
import { useTranslation } from '@labelu/i18n';

import { ReactComponent as NotFoundIcon } from '@/assets/svg/not-found.svg';
import Navigate from '@/components/Navigate';

type TranslationHook = () => {
  t: (key: string, options?: Record<string, unknown>) => string;
};
const useTranslationCompat = useTranslation as unknown as TranslationHook;

const NotFoundWrapper = styled.div`
  display: flex;
  width: 100vw;
  height: 100vh;
  flex-direction: column;

  .content {
    flex: 1;
    display: flex;
    width: 100%;
    background-color: var(--color-fill-quaternary);
  }

  .inner {
    margin: auto;
    display: flex;
    flex-direction: column;
    align-items: center;
  }
`;

const NotFoundPage: React.FC<Record<string, unknown>> = () => {
  const { t } = useTranslationCompat();
  return (
    <NotFoundWrapper>
      <Navigate />
      <div className="content">
        <div className="inner">
          <NotFoundIcon />
          <h3>{t('404notFound')}</h3>
        </div>
      </div>
    </NotFoundWrapper>
  );
};

export default NotFoundPage;
