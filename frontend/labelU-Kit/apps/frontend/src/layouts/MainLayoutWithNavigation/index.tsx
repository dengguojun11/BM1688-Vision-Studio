import { useEffect } from 'react';
import type { ReactNode } from 'react';
import { Outlet, useNavigate, useSearchParams } from 'react-router-dom';
import styled from 'styled-components';
import { FlexLayout } from '@labelu/components-react';

import Navigate from '@/components/Navigate';
import { isInAppIframe } from '@/utils/inAppIframe';
import NotificationCenter from '@/pages/platform/NotificationCenter';

const LayoutWrapper = styled(FlexLayout)`
  min-height: 100vh;
`;

const MainContent = styled(FlexLayout.Content)`
  background-color: #f4f5f7;
`;

const MainLayout = ({ children }: { children?: ReactNode }) => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  // 标注配置预览、训练 iframe 内嵌主站路由时不需要重复导航头部
  const isPreview = searchParams.get('noSave');
  const hideChrome = isPreview || isInAppIframe();

  useEffect(() => {
    if (window.IS_ONLINE) {
      return;
    }

    const token = localStorage.getItem('token');

    if (!token) {
      navigate('/login');
    }
  }, [navigate]);

  return (
    <LayoutWrapper flex="column">
      {!hideChrome && (
        <FlexLayout.Header>
          <Navigate />
          <NotificationCenter />
        </FlexLayout.Header>
      )}
      <MainContent flex="column">{children || <Outlet />}</MainContent>
    </LayoutWrapper>
  );
};
export default MainLayout;
