import { BulbOutlined } from '@ant-design/icons';
import { Button, Popover } from 'antd';
import { useRouteLoaderData } from 'react-router';
import { useTranslation } from '@labelu/i18n';

import type { TaskLoaderResult } from '@/loaders/task.loader';

type TranslationHook = () => {
  t: (key: string, options?: Record<string, unknown>) => string;
};
const useTranslationCompat = useTranslation as unknown as TranslationHook;

export default function TaskTip({ visible }: { visible: boolean }) {
  // Keep the route loader result explicit so React Router's route-map types do
  // not expand the generic lookup recursively during the application build.
  const readRouteLoaderData = useRouteLoaderData as unknown as (routeId: string) => unknown;
  const loaderData = readRouteLoaderData('task') as TaskLoaderResult | undefined;
  const task = loaderData?.task;
  const { t } = useTranslationCompat();

  if (!visible) {
    return null;
  }

  return (
    <Popover content={<div style={{ maxWidth: 420 }}>{task?.tips ?? t('noneTaskTip')}</div>}>
      <Button type="link" style={{ color: 'rgba(0, 0, 0, 0.85)' }} icon={<BulbOutlined />}>
        {t('taskTip')}
      </Button>
    </Popover>
  );
}
