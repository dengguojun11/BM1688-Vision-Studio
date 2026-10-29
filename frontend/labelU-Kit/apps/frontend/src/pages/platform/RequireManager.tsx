import { Alert, Spin } from 'antd';
import { useEffect } from 'react';
import type { ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import useTaskSuperuser from '@/hooks/useTaskSuperuser';

/**
 * 系统管理是两类账号中仅管理人员可进入的页面。
 * 服务端仍会对每个管理接口做最终鉴权，这里负责避免标注人员先看到管理壳体。
 */
export default function RequireManager({ children }: { children: ReactNode }) {
  const userQuery = useTaskSuperuser();
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    if (userQuery.isSuccess && !userQuery.isTaskSuperuser) {
      navigate('/workspace', { replace: true, state: { from: location.pathname } });
    }
  }, [location.pathname, navigate, userQuery.isSuccess, userQuery.isTaskSuperuser]);

  if (userQuery.isLoading) {
    return (
      <div
        role="status"
        aria-live="polite"
        aria-label="正在校验管理权限"
        style={{ minHeight: 240, display: 'grid', placeItems: 'center' }}
      >
        <Spin tip="正在校验管理权限">
          <span style={{ display: 'inline-block', minHeight: 32 }} />
        </Spin>
      </div>
    );
  }

  if (userQuery.isError || !userQuery.isTaskSuperuser) {
    return (
      <Alert
        type="warning"
        showIcon
        message="当前账号没有系统管理权限"
        description="标注人员可以创建任务、标注、训练和发布模型；标签契约、设备、外部连接和质量门禁由管理人员维护。"
      />
    );
  }

  return <>{children}</>;
}
