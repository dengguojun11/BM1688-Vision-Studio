import React, { useMemo, useState } from 'react';
import { Button, Checkbox, Spin, Tooltip } from 'antd';
import { useNavigate, useNavigation, useRevalidator } from 'react-router';
import Icon from '@ant-design/icons';
import formatter from '@labelu/formatter';
import { useTranslation } from '@labelu/i18n';
import { useQueryClient } from '@tanstack/react-query';

import { modal } from '@/StaticAnt';
import { ReactComponent as DeleteIcon } from '@/assets/svg/delete.svg';
import { ReactComponent as OutputIcon } from '@/assets/svg/outputData.svg';
import { deleteTask } from '@/api/services/task';
import { taskKey } from '@/api/queryKeyFactories';
import ExportPortal from '@/components/ExportPortal';
import { MediaTypeText } from '@/constants/mediaType';
import type { MediaType, TaskResponse } from '@/api/types';
import { TaskStatus } from '@/api/types';
import { jsonParse } from '@/utils';
import { trainingEntryPath } from '@/utils/trainingEntry';
import useMe from '@/hooks/useMe';
import useTaskSuperuser from '@/hooks/useTaskSuperuser';

import {
  CardActionGroup,
  CardFooter,
  CardIdentity,
  CardTopline,
  CardWrapper,
  FooterAction,
  FooterMeta,
  LoadingMask,
  LoadingText,
  MediaBadge,
  MetricGrid,
  MetricItem,
  ProgressFill,
  ProgressSummary,
  ProgressTrack,
  TagRow,
  TaskId,
  TaskName,
  TaskTitleBlock,
} from './style';
import { isTaskNavigationPending } from './taskNavigation';

type TranslationHook = () => { t: (key: string, options?: Record<string, unknown>) => string };
const useTranslationCompat = useTranslation as unknown as TranslationHook;

function MediaTypeTag({ type }: { type: MediaType }) {
  return (
    <MediaBadge $color="#2563eb" $bg="#eff6ff">
      {MediaTypeText[type] || type}
    </MediaBadge>
  );
}

function TrainingReadyTag({ trainingReady }: { trainingReady: boolean }) {
  return (
    <MediaBadge
      data-task-card-own-tooltip
      $color={trainingReady ? 'var(--color-success-active)' : 'var(--color-error-active)'}
      $bg={trainingReady ? 'var(--color-success-bg)' : 'var(--color-error-bg)'}
    >
      {trainingReady ? '可训练' : '待完善'}
    </MediaBadge>
  );
}

function TaskStateTag({
  state,
}: {
  state: 'draft' | 'pending' | 'processing' | 'completed';
}) {
  const labels = {
    draft: ['草稿', 'var(--color-warning-active)', 'var(--color-warning-bg)'],
    pending: ['待标注', '#64748b', '#f1f5f9'],
    processing: ['进行中', 'var(--color-primary)', 'var(--color-primary-bg)'],
    completed: ['已完成', 'var(--color-success-active)', 'var(--color-success-bg)'],
  } as const;
  const [label, color, bg] = labels[state];
  return <MediaBadge $color={color} $bg={bg}>{label}</MediaBadge>;
}

function ThirdPartyTag({ externalTaskId }: { externalTaskId?: string | null }) {
  return (
    <Tooltip placement="top" title={externalTaskId || 'strategy_evolution'}>
      <MediaBadge data-task-card-own-tooltip $color="var(--color-success-active)" $bg="var(--color-success-bg)">
        第三方
      </MediaBadge>
    </Tooltip>
  );
}

export interface TaskCardProps {
  cardInfo: TaskResponse;
  className?: string;
  selected?: boolean;
  onSelectedChange?: (checked: boolean) => void;
}

const TaskCard = (props: TaskCardProps) => {
  const { cardInfo, className, selected, onSelectedChange } = props;
  const [nameTooltipOpen, setNameTooltipOpen] = useState(false);
  const revalidator = useRevalidator();
  const { t } = useTranslationCompat();
  const { id, status } = cardInfo;
  const stats = cardInfo.stats || {};
  const queryClient = useQueryClient();
  const abnormalSample = stats.abnormal ?? 0;
  const incompleteSample = stats.incomplete ?? 0;
  const unDoneSample = (stats.new ?? 0) + abnormalSample + incompleteSample;
  const doneSample = (stats?.done ?? 0) + (stats?.skipped ?? 0);
  const total = unDoneSample + doneSample;
  const samplesFullyProcessed = total > 0 && doneSample === total;
  const taskActuallyFinished = status === TaskStatus.FINISHED;
  const progressPercent = total > 0 ? Math.round((doneSample * 100) / total) : 0;
  const taskState: 'draft' | 'pending' | 'processing' | 'completed' =
    status === TaskStatus.DRAFT || status === TaskStatus.IMPORTED
      ? 'draft'
      : taskActuallyFinished && samplesFullyProcessed
        ? 'completed'
        : doneSample > 0
          ? 'processing'
          : 'pending';
  const tools = useMemo(() => {
    return jsonParse(cardInfo?.config ?? '{}')?.tools;
  }, [cardInfo]);
  const navigate = useNavigate();
  const navigation = useNavigation();
  const taskPath = `/tasks/${id}`;
  const isEnteringTask = isTaskNavigationPending(navigation.state, navigation.location?.pathname, id);
  const me = useMe();
  const { isTaskSuperuser } = useTaskSuperuser();
  const isImageTask = cardInfo.media_type === 'IMAGE';
  const showTrainingBadge =
    isImageTask &&
    status !== TaskStatus.DRAFT &&
    status !== TaskStatus.IMPORTED &&
    total > 0 &&
    cardInfo.training_ready != null;
  const isMeTheCreator = cardInfo?.created_by?.id === me?.data?.id;
  const canManageTask = isMeTheCreator || isTaskSuperuser;
  const canStartTraining =
    isImageTask &&
    taskActuallyFinished &&
    cardInfo.training_ready === true;
  const turnToAnnotation = (e: React.MouseEvent<HTMLElement>) => {
    if (isEnteringTask) {
      return;
    }
    if (!(e.target instanceof Node) || !e.currentTarget.contains(e.target)) {
      return;
    }

    e.stopPropagation();
    e.nativeEvent.stopImmediatePropagation();

    setNameTooltipOpen(false);
    // eslint-disable-next-line no-console
    console.info(`[7GAI][task-navigation] start ${JSON.stringify({ task_id: id, task_name: cardInfo.name })}`);
    navigate(taskPath);
  };

  const openTask = (event: React.MouseEvent<HTMLElement>) => {
    event.stopPropagation();
    navigate(taskPath);
  };

  const openTraining = (event: React.MouseEvent<HTMLElement>) => {
    event.stopPropagation();
    navigate(trainingEntryPath(Number(id), cardInfo.dataset_id, cardInfo.config));
  };

  const handleCardKeyDown = (event: React.KeyboardEvent<HTMLElement>) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      navigate(taskPath);
    }
  };

  const handleDeleteTask = (e: React.MouseEvent) => {
    e.stopPropagation();

    modal.confirm({
      title: t('deleteTask'),
      content: t('confirmDeleteTask'),
      okText: t('ok'),
      cancelText: t('cancel'),
      onOk: async () => {
        await deleteTask(id!);
        queryClient.invalidateQueries({ queryKey: taskKey.all });
        revalidator.revalidate();
      },
    });
  };

  const updateNameTooltip = (event: React.MouseEvent<HTMLElement>) => {
    const target = event.target as Element;
    const hasOwnInteraction = !!target.closest('button, input, [title], [data-task-card-own-tooltip]');
    setNameTooltipOpen(!hasOwnInteraction);
  };

  return (
    <Tooltip
      placement="topLeft"
      title={cardInfo.name}
      open={nameTooltipOpen}
      overlayStyle={{ maxWidth: 'min(28rem, calc(100vw - 2rem))' }}
    >
      <CardWrapper
        className={className}
        onClick={turnToAnnotation}
        onKeyDown={handleCardKeyDown}
        onMouseEnter={updateNameTooltip}
        onMouseMove={updateNameTooltip}
        onMouseLeave={() => setNameTooltipOpen(false)}
        aria-label={cardInfo.name || String(cardInfo.id)}
        aria-busy={isEnteringTask}
        role="button"
        tabIndex={0}
      >
        <CardTopline>
          <CardIdentity>
            {onSelectedChange && (
              <Checkbox
                checked={!!selected}
                onClick={(event) => event.stopPropagation()}
                onChange={(event) => {
                  event.stopPropagation();
                  onSelectedChange(event.target.checked);
                }}
                aria-label={`选择任务 ${cardInfo.name || cardInfo.id}`}
              />
            )}
            <TaskStateTag state={taskState} />
            <MediaTypeTag type={cardInfo.media_type as MediaType} />
          </CardIdentity>
          <CardActionGroup>
            {cardInfo.config && (
              <ExportPortal taskId={cardInfo.id!} mediaType={cardInfo.media_type} tools={tools}>
                <Tooltip placement={'top'} title={t('export')}>
                  <Button size="small" type="text" icon={<Icon component={OutputIcon} />} />
                </Tooltip>
              </ExportPortal>
            )}
            {canManageTask && (
              <Tooltip title={t('deleteTask')} placement={'top'}>
                <Button onClick={handleDeleteTask} size="small" type="text" icon={<Icon component={DeleteIcon} />} />
              </Tooltip>
            )}
          </CardActionGroup>
        </CardTopline>

        <TaskTitleBlock>
          <TaskName>{cardInfo.name || `任务 ${id}`}</TaskName>
          <TaskId>任务 ID：{id}</TaskId>
        </TaskTitleBlock>

        <TagRow>
          {cardInfo.dataset_category_name && (
            <MediaBadge $color="#0f766e" $bg="#ecfdf5" title={cardInfo.dataset_category_key || undefined}>
              {cardInfo.dataset_category_name}
            </MediaBadge>
          )}
          {cardInfo.source_type === 'third_party' && (
            <ThirdPartyTag externalTaskId={cardInfo.source_external_task_id} />
          )}
          {showTrainingBadge && (
            <Tooltip
              placement="top"
              title={
                cardInfo.training_ready
                  ? '全局标签、有效拉框或明确负样本、标框几何均已达标，可用于训练'
                  : '存在未选全局标签、未确认空框、无效拉框或标框几何异常的样本，不可用于训练'
              }
            >
              <TrainingReadyTag trainingReady={!!cardInfo.training_ready} />
            </Tooltip>
          )}
        </TagRow>

        <MetricGrid>
          <MetricItem>
            <span className="metric-label">已标注样本</span>
            <span className="metric-value">{doneSample.toLocaleString()} / {total.toLocaleString()}</span>
          </MetricItem>
          <MetricItem>
            <span className="metric-label">标注完成度</span>
            <span className="metric-value">{total > 0 ? `${progressPercent}%` : '暂无样本'}</span>
          </MetricItem>
        </MetricGrid>

        {status !== TaskStatus.DRAFT && status !== TaskStatus.IMPORTED && total > 0 && (
          <ProgressSummary>
            <div className="progress-label">
              <span>{taskActuallyFinished && samplesFullyProcessed ? '标注已完成' : '标注进度'}</span>
              <span className="progress-value">{doneSample.toLocaleString()} / {total.toLocaleString()}</span>
            </div>
            <ProgressTrack aria-label={`标注完成度 ${progressPercent}%`}>
              <ProgressFill $percent={progressPercent} $complete={taskActuallyFinished && samplesFullyProcessed} />
            </ProgressTrack>
          </ProgressSummary>
        )}

        <CardFooter>
          <FooterMeta>
            <div className="creator">{cardInfo.created_by?.username || '未记录创建人'}</div>
            <div>{formatter.format('dateTime', cardInfo.created_at, { style: 'YYYY-MM-DD HH:mm' })}</div>
          </FooterMeta>
          <FooterAction>
            {canStartTraining ? (
              <Button type="primary" size="small" onClick={openTraining}>
                开始训练
              </Button>
            ) : (
              <Button size="small" onClick={openTask}>
                进入标注
              </Button>
            )}
          </FooterAction>
        </CardFooter>

        {isEnteringTask && (
          <LoadingMask role="status" aria-live="polite">
            <Spin size="small" />
            <LoadingText>正在进入任务...</LoadingText>
          </LoadingMask>
        )}
      </CardWrapper>
    </Tooltip>
  );
};
export default TaskCard;
