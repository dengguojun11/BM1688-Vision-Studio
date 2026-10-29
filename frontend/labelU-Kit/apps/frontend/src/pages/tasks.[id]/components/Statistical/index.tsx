import { UploadOutlined, SettingOutlined } from '@ant-design/icons';
import { useNavigate, useRouteLoaderData } from 'react-router';
import { Button, Popconfirm } from 'antd';
import _ from 'lodash-es';
import styled from 'styled-components';
import { FlexLayout } from '@labelu/components-react';
import { useTranslation } from '@labelu/i18n';

type TranslationHook = () => {
  t: (key: string, options?: Record<string, unknown>) => string;
};

const useTranslationCompat = useTranslation as unknown as TranslationHook;

import type { TaskLoaderResult } from '@/loaders/task.loader';
import { MediaType, SampleState } from '@/api/types';
import useMe from '@/hooks/useMe';
import useTaskSuperuser from '@/hooks/useTaskSuperuser';

import commonController from '../../../../utils/common';
import ExportPortal from '../../../../components/ExportPortal';
import CollaboratorPortal from '../CollaboratorModal';
import { buildTaskStatusSamplesPath } from './taskStatusNavigation';

const Circle = styled.div<{
  color: string;
}>`
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 50%;
  background-color: ${({ color }) => color};
`;

const StatisticalShell = styled.div`
  display: flex;
  justify-content: space-between;
  align-items: stretch;
  gap: 1rem;
  width: 100%;
  min-width: 0;
`;

const OverviewCard = styled.div`
  display: flex;
  align-items: center;
  gap: 2.5rem;
  flex-wrap: nowrap;
  flex: 1 1 auto;
  min-width: 0;
  padding-left: 1.5rem;
`;

const OverviewItem = styled.div<{ $clickable?: boolean }>`
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-start;
  min-width: 0;
  gap: 0.25rem;
  font-weight: 600;
  border: 0;
  padding: 0;
  color: inherit;
  background: transparent;
  font: inherit;
  cursor: ${({ $clickable }) => ($clickable ? 'pointer' : 'default')};
`;

const OverviewLabel = styled.div`
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.375rem;
  white-space: nowrap;
`;

const OverviewCount = styled.div``;

const ActionRow = styled.div`
  display: flex;
  align-items: center;
  align-self: center;
  justify-content: flex-end;
  flex: 1 1 auto;
  gap: 0.5rem;
  white-space: nowrap;
`;

export interface TaskStatusProps {
  status?: 'done' | 'new' | 'skipped' | 'abnormal' | 'incomplete';
  count?: number;
  onClick?: () => void;
}

export function TaskStatus({ children, status, count }: React.PropsWithChildren<TaskStatusProps>) {
  const { t } = useTranslationCompat();
  const colorMapping = {
    done: 'var(--color-primary)',
    new: 'var(--color-text-tertiary)',
    skipped: 'var(--orange)',
    abnormal: 'var(--color-error)',
    incomplete: '#d97706',
  };

  const textMapping = {
    done: t('annotated'),
    new: t('new'),
    skipped: t('skipped'),
    abnormal: t('abnormal'),
    incomplete: '不完整',
  };

  const color = status ? colorMapping[status] : colorMapping.new;
  const title = status ? textMapping[status] : children;

  return (
    <FlexLayout items="center" gap=".5rem">
      {status && <Circle color={color} />}
      <b>{title}</b>
      {count && <b>{count}</b>}
    </FlexLayout>
  );
}

function TaskStatusOverview({ children, status, count, onClick }: React.PropsWithChildren<TaskStatusProps>) {
  const { t } = useTranslationCompat();
  const colorMapping = {
    done: 'var(--color-primary)',
    new: 'var(--color-text-tertiary)',
    skipped: 'var(--orange)',
    abnormal: 'var(--color-error)',
    incomplete: '#d97706',
  };

  const textMapping = {
    done: t('annotated'),
    new: t('new'),
    skipped: t('skipped'),
    abnormal: t('abnormal'),
    incomplete: '不完整',
  };

  const color = status ? colorMapping[status] : colorMapping.new;
  const title = status ? textMapping[status] : children;

  return (
    <OverviewItem
      as={onClick ? 'button' : 'div'}
      type={onClick ? 'button' : undefined}
      $clickable={Boolean(onClick)}
      onClick={onClick}
      aria-label={onClick ? `查看${title}数据` : undefined}
    >
      <OverviewLabel>
        {status && <Circle color={color} />}
        <span>{title}</span>
      </OverviewLabel>
      <OverviewCount>{count ?? 0}</OverviewCount>
    </OverviewItem>
  );
}

interface StatisticalProps {
  canDeleteSample: boolean;
  selectedSampleCount: number;
  onDeleteSelectedSamples: () => Promise<void>;
}

const Statistical = ({
  canDeleteSample,
  selectedSampleCount,
  onDeleteSelectedSamples,
}: StatisticalProps) => {
  const routerLoaderData = useRouteLoaderData('task') as TaskLoaderResult;
  const taskData = routerLoaderData.task;
  const { stats = {} } = taskData || {};
  const taskId = _.get(taskData, 'id');
  const mediaType = _.get(taskData, 'media_type', MediaType.IMAGE);
  const { t } = useTranslationCompat();
  const me = useMe();
  const { isTaskSuperuser } = useTaskSuperuser();
  const isMeTheCreator = taskData?.created_by?.id === me.data?.id;
  const canManageTask = isMeTheCreator || isTaskSuperuser;

  const samples = routerLoaderData.samples;

  const navigate = useNavigate();

  const handleGoStatusSamples = (state: SampleState) => {
    if (!taskId) {
      return;
    }
    navigate(buildTaskStatusSamplesPath(taskId, state));
  };

  const handleGoAnnotation = () => {
    if (!samples || samples.data.length === 0) {
      return;
    }

    navigate(`/tasks/${taskId}/samples/${samples.data[0].id}`);
  };
  const handleGoConfig = () => {
    navigate(`/tasks/${taskId}/edit#config`);
  };
  const handleGoUpload = () => {
    navigate(`/tasks/${taskId}/edit#upload`);
  };

  /** 扩展页由 FastAPI 提供，不在 React Router 中，须整页跳转 */
  const openExtensionPage = (path: string) => {
    window.location.assign(path);
  };

  const total =
    (stats.done ?? 0) + (stats.new ?? 0) + (stats.skipped ?? 0) + (stats.incomplete ?? 0) + (stats.abnormal ?? 0);

  return (
    <StatisticalShell>
      <OverviewCard>
        <TaskStatusOverview status="done" count={stats.done} />
        <TaskStatusOverview
          status="new"
          count={stats.new}
          onClick={stats.new ? () => handleGoStatusSamples(SampleState.NEW) : undefined}
        />
        <TaskStatusOverview
          status="skipped"
          count={stats.skipped}
          onClick={stats.skipped ? () => handleGoStatusSamples(SampleState.SKIPPED) : undefined}
        />
        <TaskStatusOverview
          status="incomplete"
          count={stats.incomplete}
          onClick={stats.incomplete ? () => handleGoStatusSamples(SampleState.INCOMPLETE) : undefined}
        />
        <TaskStatusOverview
          status="abnormal"
          count={stats.abnormal}
          onClick={stats.abnormal ? () => handleGoStatusSamples(SampleState.ABNORMAL) : undefined}
        />
        <TaskStatusOverview count={total}>{t('total')}</TaskStatusOverview>
      </OverviewCard>
      <ActionRow>
        <Button type="text" onClick={() => openExtensionPage('/annotation-settings')}>
          数据库：预设 / 全局维度
        </Button>
        {canManageTask && <CollaboratorPortal />}
        {canManageTask && (
          <Button type="text" icon={<SettingOutlined />} onClick={handleGoConfig}>
            {t('taskConfig')}
          </Button>
        )}
        <ExportPortal taskId={+taskId!} mediaType={mediaType} tools={taskData?.config?.tools}>
          <Button type="text" icon={<UploadOutlined />}>
            {t('export')}
          </Button>
        </ExportPortal>
        {canDeleteSample && (
          <Popconfirm
            title={t('batchDeleteConfirm').replace('{count}', String(selectedSampleCount))}
            onConfirm={onDeleteSelectedSamples}
          >
            <Button type="text" danger disabled={selectedSampleCount === 0}>
              {t('batchDelete')}
            </Button>
          </Popconfirm>
        )}
        <Button type="primary" ghost onClick={handleGoUpload}>
          {t('uploadData')}
        </Button>
        <Button type="primary" onClick={commonController.debounce(handleGoAnnotation, 100)}>
          {t('startAnnotate')}
        </Button>
      </ActionRow>
    </StatisticalShell>
  );
};
export default Statistical;
