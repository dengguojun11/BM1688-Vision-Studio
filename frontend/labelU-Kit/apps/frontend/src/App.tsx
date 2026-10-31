import { App as AntApp, ConfigProvider, Modal } from 'antd';
import enUS from 'antd/es/locale/en_US';
import zhCN from 'antd/es/locale/zh_CN';
import { I18nProvider, useTranslation } from '@labelu/i18n';
import { TooltipGlobalStyle } from '@labelu/components-react';
import { useMemo } from 'react';
import { StyleSheetManager } from 'styled-components';

import RouterContainer from './components/RouterContainer';
import themeToken from './styles/theme.json';
import StaticAnt from './StaticAnt';
import routes from './routes';
import { QueryProvider } from './api/queryClient';
import GlobalStyle from './styles/GlobalStyle';

const antdTheme = { token: themeToken.token };
const antdWave = { disabled: true };

// 普通弹窗统一只通过明确的窗口操作关闭，避免误点遮罩或 Esc 丢失未保存内容。
Modal.defaultProps = {
  ...Modal.defaultProps,
  maskClosable: false,
  keyboard: false,
};
type AppI18n = { i18n: { language: string } };

const App: React.FC = () => {
  const { i18n } = (useTranslation as unknown as () => AppI18n)();
  const locale = useMemo(() => {
    if (['zh', 'zh_CN', 'zh-CN', 'zh-TW', 'zh-HK'].includes(i18n.language)) {
      return 'zh-CN';
    }

    return 'en-US';
  }, [i18n.language]);
  const antdLocale = useMemo(() => {
    if (locale === 'zh-CN') {
      return zhCN;
    }

    return enUS;
  }, [locale]);

  return (
    <StyleSheetManager disableCSSOMInjection={false}>
      <ConfigProvider locale={antdLocale} componentSize="middle" wave={antdWave} theme={antdTheme}>
        <I18nProvider locale={locale}>
          <AntApp>
            <StaticAnt />
            <GlobalStyle />
            <TooltipGlobalStyle />
            {/* @ts-ignore */}
            <QueryProvider>
              <RouterContainer routes={routes} />
            </QueryProvider>
          </AntApp>
        </I18nProvider>
      </ConfigProvider>
    </StyleSheetManager>
  );
};

export default App;
