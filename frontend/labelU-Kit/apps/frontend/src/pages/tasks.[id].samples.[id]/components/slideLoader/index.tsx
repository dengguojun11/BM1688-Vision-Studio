import React, { useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef } from 'react';
import styled from 'styled-components';
import { Button, Popconfirm, Progress, Spin } from 'antd';
import { useParams } from 'react-router-dom';
import { useTranslation } from '@labelu/i18n';

type TranslationHook = () => {
  t: (key: string, options?: Record<string, unknown>) => string;
};

const useTranslationCompat = useTranslation as unknown as TranslationHook;

import type { SampleResponse } from '@/api/types';
import { MediaType } from '@/api/types';
import { getSample } from '@/api/services/samples';
import { useSampleBoundsCheckJob } from '@/hooks/useSampleBoundsCheckJob';
import type { TaskInLoader } from '@/loaders/task.loader';
import type { TaskSampleUser } from '@/hooks/useSampleWs';

import SliderCard from '../sliderCard';
import { SAMPLE_CHANGED } from '../annotationRightCorner';
import AnnotationContext from '../../annotation.context';

const LeftWrapper = styled.div`
  overflow-y: auto;
  overflow-x: hidden;
  height: calc(100vh - var(--offset-top));
  display: flex;
  flex-direction: column;
  gap: 1rem;
  box-sizing: border-box;
  padding: 1rem 0;
  align-items: center;
`;

const PreviousLoading = styled.div`
  display: flex;
  justify-content: center;
  width: 100%;
  padding: 0.25rem 0;
`;

const LOAD_PREVIOUS_THRESHOLD = 24;

const SlideLoader = () => {
  const routeParams = useParams();
  const currentSampleId = Number(routeParams.sampleId);
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const resetSampleIdRef = useRef<number | undefined>();
  const handleSampleClick = useCallback((sample: SampleResponse) => {
    document.dispatchEvent(
      new CustomEvent(SAMPLE_CHANGED, {
        detail: {
          sampleId: sample.id,
          source: 'list',
        },
      }),
    );
  }, []);

  /**
   * 切换文件时
   * 1. 下一张或者上一张时，需要将当前的标注结果更新到当前文件后，再进行切换
   *    1.1 如果当前文件是「跳过」的状态，那么不需要更新标注结果
   *    1.2 如果当前文件是「完成」的状态，那么需要更新标注结果，并且将当前文件的状态改为「完成」
   * 2. 将当前文件标记为「跳过」，更新文件状态为「跳过」，然后跳到下一张
   * 3. 将当前文件标记为「取消跳过」，更新文件状态为「新」
   */
  // context中的samples会随着「跳过」、「取消跳过」、「完成」的操作而更新，但上面的useScrollFetch只有滚动的时候才会触发更新
  const {
    samples: samplesFromContext,
    setSamples,
    task = {} as NonNullable<TaskInLoader>,
    taskConnections,
    isStart,
    loadingPrevious,
    fetchPreviousSamples,
  } = useContext(AnnotationContext);
  const { t } = useTranslationCompat();
  const taskId = Number(routeParams.taskId);
  const {
    progress: boundsCheckProgress,
    isChecking: isBoundsChecking,
    start: startBoundsCheck,
  } = useSampleBoundsCheckJob({
    taskId,
    onCompleted: async () => {
      const refreshed = await Promise.all(
        samplesFromContext.map(async (sample) => {
          if (!sample.id) {
            return sample;
          }
          const response = await getSample({ task_id: taskId, sample_id: sample.id });
          return response.data;
        }),
      );
      setSamples(refreshed);
    },
  });
  const userSampleMapping = useMemo(() => {
    const mapping: Record<string, TaskSampleUser[]> = {};
    taskConnections.forEach((collaborator) => {
      if (!mapping[collaborator.sample_id]) {
        mapping[collaborator.sample_id] = [];
      }

      mapping[collaborator.sample_id].push(collaborator);
    });

    return mapping;
  }, [taskConnections]);

  const handleScroll = useCallback(() => {
    const wrapper = wrapperRef.current;

    if (!wrapper || isStart || loadingPrevious || wrapper.scrollTop > LOAD_PREVIOUS_THRESHOLD) {
      return;
    }

    fetchPreviousSamples?.(wrapper);
  }, [fetchPreviousSamples, isStart, loadingPrevious]);

  useEffect(() => {
    const wrapper = wrapperRef.current;

    if (!wrapper) {
      return;
    }

    wrapper.addEventListener('scroll', handleScroll, { passive: true });

    return () => {
      wrapper.removeEventListener('scroll', handleScroll);
    };
  }, [handleScroll]);

  useLayoutEffect(() => {
    const wrapper = wrapperRef.current;

    if (!wrapper || samplesFromContext?.[0]?.id !== currentSampleId || resetSampleIdRef.current === currentSampleId) {
      return;
    }

    wrapper.scrollTop = 0;
    resetSampleIdRef.current = currentSampleId;
  }, [currentSampleId, samplesFromContext]);

  useEffect(() => {
    const wrapper = wrapperRef.current;
    if (!wrapper) return;

    const card = wrapper.querySelector(`[data-sample-id="${currentSampleId}"]`);
    if (card) {
      card.scrollIntoView({ block: 'nearest' });
    }
  }, [currentSampleId]);

  return (
    <LeftWrapper ref={wrapperRef}>
      {task.media_type === MediaType.IMAGE && (
        <PreviousLoading>
          <Popconfirm title="确认开始完整性检查？" onConfirm={startBoundsCheck}>
            <Button size="small" type="link" loading={isBoundsChecking} disabled={isBoundsChecking}>
              完整性检查
            </Button>
          </Popconfirm>
        </PreviousLoading>
      )}
      {boundsCheckProgress && (
        <PreviousLoading>
          <Progress
            percent={Math.round(
              (boundsCheckProgress.processed_count / Math.max(boundsCheckProgress.sample_count, 1)) * 100,
            )}
            size="small"
            status={
              boundsCheckProgress.failed_count > 0 || boundsCheckProgress.abnormal_count > 0
                ? 'exception'
                : 'active'
            }
            style={{ width: 220 }}
            format={() =>
              `${boundsCheckProgress.incomplete_count ?? 0} 不完整 / ${boundsCheckProgress.abnormal_count} ${t('abnormal')}`
            }
          />
        </PreviousLoading>
      )}
      {loadingPrevious && (
        <PreviousLoading>
          <Spin size="small" />
        </PreviousLoading>
      )}
      {samplesFromContext?.map((item: SampleResponse, index) => {
        return (
          <div key={item.id} data-sample-id={item.id}>
            <SliderCard
              editingUser={userSampleMapping?.[item.id!]?.[0]}
              cardInfo={item}
              type={task.media_type}
              onClick={handleSampleClick}
              index={index}
              isActive={item.id === currentSampleId}
            />
          </div>
        );
      })}
    </LeftWrapper>
  );
};

export default SlideLoader;
