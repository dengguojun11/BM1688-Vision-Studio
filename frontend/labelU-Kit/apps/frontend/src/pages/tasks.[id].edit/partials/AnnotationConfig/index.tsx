import styled from 'styled-components';
import { FlexLayout } from '@labelu/components-react';
import { useTranslation } from '@labelu/i18n';

import FormConfig from './formConfig';

type TranslationHook = () => {
  t: (key: string, options?: Record<string, unknown>) => string;
};
const useTranslationCompat = useTranslation as unknown as TranslationHook;

const Inner = styled(FlexLayout)`
  width: 940px;
  margin: 0 auto;
`;

// 配置页的config统一使用此组件的state
const AnnotationConfig = () => {
  const { t } = useTranslationCompat();

  return (
    <FlexLayout full padding="1rem" flex="column">
      <Inner flex="column" full>
        <FlexLayout.Header flex justify="space-between">
          <h2>{t('annotationConfig')}</h2>
        </FlexLayout.Header>
        <FlexLayout.Content>
          <FormConfig />
        </FlexLayout.Content>
      </Inner>
    </FlexLayout>
  );
};

export default AnnotationConfig;
