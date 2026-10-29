import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams, useRevalidator, useRouteLoaderData, useSearchParams } from 'react-router-dom';
import type { ColumnsType, TableProps } from 'antd/es/table';
import type { RcFile } from 'antd/lib/upload/interface';
import { Alert, Table, Pagination, Button, Checkbox, Popconfirm, Tag, Tooltip, Avatar, Popover, Progress, message, Modal } from 'antd';
import { VideoCard, FlexLayout } from '@labelu/components-react';
import _ from 'lodash-es';
import formatter from '@labelu/formatter';
import styled from 'styled-components';
import { DownOutlined, QuestionCircleOutlined } from '@ant-design/icons';
import { useTranslation } from '@labelu/i18n';

import type { AutoLabelJobResponse, PreAnnotationFileResponse, PreannotateJobResponse, SampleResponse } from '@/api/types';
import { MediaType, TaskStatus } from '@/api/types';
import ExportPortal from '@/components/ExportPortal';
import type { TaskLoaderResult } from '@/loaders/task.loader';
import BlockContainer from '@/layouts/BlockContainer';
import { downloadFromUrl, jsonParse } from '@/utils';
import { deletePreAnnotationFile, uploadPreAnnotationFile } from '@/api/services/preAnnotations';
import {
  deleteSamples,
  createAutoLabelJob,
  getActiveAutoLabelJob,
  getAutoLabelJobStatus,
  cancelAutoLabelJob,
  getLabelDatasetImportJob,
  getActiveLabelDatasetImportJob,
  getPreannotationCapabilities,
} from '@/api/services/samples';
import { UserAvatar } from '@/components/UserAvatar';
import ServerSearchInput from '@/components/ServerSearchInput';
import PreannotateControl from '@/components/PreannotateControl';
import NativeUpload from '@/components/NativeUpload';
import useMe from '@/hooks/useMe';
import useTaskSuperuser from '@/hooks/useTaskSuperuser';
import { useSampleBoundsCheckJob } from '@/hooks/useSampleBoundsCheckJob';
import {
  readTaskLabelDatasetImportState,
  writeTaskLabelDatasetImportState,
  type TaskLabelDatasetImportState,
} from '@/utils/taskLabelDatasetImport';
import { isPreannotationCapabilityVisible, normalizePreannotationCapabilities, type PreannotationCapabilityMap } from '@/utils/preannotationCapabilities';
import {
  readBatchAutoLabelStates,
  writeBatchAutoLabelStates,
  type BatchAutoLabelSampleState,
} from './batchAutoLabelStates';

import type { TaskStatusProps } from './components/Statistical';
import Statistical, { TaskStatus as TaskStatusComponent } from './components/Statistical';
import GoToEditTask from './components/GoToEditTask';
import type { TaskSampleUser } from '../../hooks/useSampleWs';

type TranslationHook = () => { t: (key: string, options?: Record<string, unknown>) => string; i18n: typeof import('@labelu/i18n').i18n };
const useTranslationCompat = useTranslation as unknown as TranslationHook;

const isPreAnnotationFileResponse = (
  record: SampleResponse | PreAnnotationFileResponse,
): record is PreAnnotationFileResponse => Array.isArray((record as PreAnnotationFileResponse).sample_names);

const HeaderWrapper = styled(FlexLayout.Header)`
  background-color: #fff;
  height: auto;
  min-height: 3.5rem;
  padding: 0;
`;

const HeaderStatContainer = styled(BlockContainer)`
  padding: 0;
`;

const ActionArea = styled.div`
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.5rem;
  min-width: 0;
`;

const ActionRow = styled.div`
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.5rem;
`;

const ActionJobSlot = styled.div`
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  min-width: 0;
`;

const ProgressItem = styled.div`
  display: grid;
  grid-template-columns: minmax(5.5rem, auto) 8rem auto;
  align-items: center;
  gap: 0.5rem;
  min-height: 2.25rem;
  padding: 0.375rem 0.625rem;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  background: #fff;
  box-shadow: 0 1px 2px rgb(15 23 42 / 4%);
  max-width: min(36rem, calc(100vw - 2rem));
`;

const ProgressTitle = styled.div`
  min-width: 0;
  color: #1f2937;
  font-size: 0.8125rem;
  font-weight: 500;
  white-space: nowrap;
`;

const ProgressMeta = styled.div`
  display: flex;
  align-items: center;
  gap: 0.5rem;
  color: #4b5563;
  font-size: 0.75rem;
  white-space: nowrap;
`;

const SuccessText = styled.span`
  color: #15803d;
`;

const WarningText = styled.span`
  color: #b45309;
`;

const ErrorText = styled.span`
  color: #dc2626;
`;

const isActiveJobStatus = (status?: string | null) => status === 'PENDING' || status === 'PROCESSING';

interface TaskProgressItemProps {
  title: string;
  status?: string;
  processed: number;
  total: number;
  success?: number;
  failed?: number;
  normal?: number;
  incomplete?: number;
  abnormal?: number;
  onCancel?: () => void;
  cancelling?: boolean;
}

function TaskProgressItem({
  title,
  status,
  processed,
  total,
  success,
  failed = 0,
  normal,
  incomplete,
  abnormal,
  onCancel,
  cancelling,
}: TaskProgressItemProps) {
  const percent = Math.round((processed / Math.max(total, 1)) * 100);
  const progressStatus = status === 'FAILED' || failed > 0 || abnormal ? 'exception' : isActiveJobStatus(status) ? 'active' : 'success';

  return (
    <ProgressItem>
      <ProgressTitle>{title}</ProgressTitle>
      <Progress percent={percent} size="small" status={progressStatus} showInfo={false} />
      <ProgressMeta>
        <span>
          {processed}/{total}
        </span>
        {typeof success === 'number' && <SuccessText>成功 {success}</SuccessText>}
        {typeof normal === 'number' && <SuccessText>正常 {normal}</SuccessText>}
        {typeof incomplete === 'number' && <WarningText>不完整 {incomplete}</WarningText>}
        {typeof abnormal === 'number' && <ErrorText>异常 {abnormal}</ErrorText>}
        {failed > 0 && <ErrorText>失败 {failed}</ErrorText>}
        {onCancel && (
          <Button type="link" danger size="small" loading={cancelling} onClick={onCancel} style={{ padding: 0 }}>
            取消
          </Button>
        )}
      </ProgressMeta>
    </ProgressItem>
  );
}

const Samples = () => {
  const routerData = useRouteLoaderData('task') as TaskLoaderResult;
  const samples = _.get(routerData, 'samples.data');
  const revalidator = useRevalidator();
  const preAnnotations = _.get(routerData, 'preAnnotations.data') as any;
  const task = routerData.task;
  const metaData = routerData?.samples?.meta_data;
  const routeParams = useParams();
  const taskId = +routeParams.taskId!;
  const { t, i18n } = useTranslationCompat();
  const me = useMe();
  const { isTaskSuperuser } = useTaskSuperuser();
  const isMeTheCreator = task?.created_by?.id === me?.data?.id;
  const canManageTask = isMeTheCreator || isTaskSuperuser;
  const canDeleteSample = Boolean(me?.data?.id);

  // 查询参数
  const [searchParams, setSearchParams] = useSearchParams(
    new URLSearchParams({
      // 默认按照最后更新时间倒序
      page: '1',
      size: '10',
    }),
  );
  const sampleSearch = searchParams.get('q') || '';
  const [sampleSearchDraft, setSampleSearchDraft] = useState(sampleSearch);

  const taskStatus = _.get(task, 'status');
  const isTaskReadyToAnnotate =
    ![TaskStatus.DRAFT, TaskStatus.IMPORTED].includes(taskStatus!) &&
    task?.config &&
    Object.keys(task?.config).length > 0;
  const [enterRowId, setEnterRowId] = useState<any>(undefined);
  const [selectedSampleIds, setSelectedSampleIds] = useState<number[]>([]);
  const {
    progress: boundsCheckProgress,
    isChecking: isBoundsChecking,
    start: startBoundsCheck,
  } = useSampleBoundsCheckJob({
    taskId,
    onCompleted: () => revalidator.revalidate(),
  });

  const [batchJob, setBatchJob] = useState<AutoLabelJobResponse | null>(null);
  const [yoloPreannotateJob, setYoloPreannotateJob] = useState<PreannotateJobResponse | null>(null);
  const [aiDetectionPreannotateJob, setAiDetectionPreannotateJob] = useState<PreannotateJobResponse | null>(null);
  const [locateAnythingPreannotateJob, setLocateAnythingPreannotateJob] = useState<PreannotateJobResponse | null>(null);
  const [vlxSeekPreannotateJob, setVlxSeekPreannotateJob] = useState<PreannotateJobResponse | null>(null);
  const [rexOmniPreannotateJob, setRexOmniPreannotateJob] = useState<PreannotateJobResponse | null>(null);
  const [preannotationCapabilities, setPreannotationCapabilities] = useState<PreannotationCapabilityMap>({});
  const [preannotationCapabilitiesResolved, setPreannotationCapabilitiesResolved] = useState(false);
  const [isUploadingPreAnnotation, setIsUploadingPreAnnotation] = useState(false);
  const [isBatchLabeling, setIsBatchLabeling] = useState(false);
  const [autoLabelModalOpen, setAutoLabelModalOpen] = useState(false);
  const [currentAutoLabelJobId, setCurrentAutoLabelJobId] = useState<number | null>(null);
  const [isCancellingBatchLabel, setIsCancellingBatchLabel] = useState(false);
  const [filterByLabels, setFilterByLabels] = useState(() => {
    return localStorage.getItem('ai_filter_by_labels') !== 'false';
  });
  const [labelDatasetImportState, setLabelDatasetImportState] = useState<TaskLabelDatasetImportState | null>(() =>
    readTaskLabelDatasetImportState(taskId),
  );
  const labelDatasetImportSignatureRef = useRef<string>('');

  useEffect(() => {
    setSampleSearchDraft(sampleSearch);
  }, [sampleSearch]);

  // The extension menu is rendered outside React.  Listen for its explicit
  // action event so opening AI batch labeling does not depend on finding and
  // clicking a hidden Ant Design button in the DOM.
  useEffect(() => {
    const handleOpenAiBatch = (event: Event) => {
      const detail = (event as CustomEvent<{ taskId?: number }>).detail;
      if (detail?.taskId && detail.taskId !== taskId) return;
      setAutoLabelModalOpen(true);
    };
    window.addEventListener('labelu:open-ai-batch-preannotation', handleOpenAiBatch);
    return () => window.removeEventListener('labelu:open-ai-batch-preannotation', handleOpenAiBatch);
  }, [taskId]);

  useEffect(() => {
    let disposed = false;
    setPreannotationCapabilitiesResolved(false);
    void getPreannotationCapabilities(taskId)
      .then((response) => {
        if (!disposed) setPreannotationCapabilities(normalizePreannotationCapabilities(response.data));
      })
      .catch(() => {
        if (!disposed) setPreannotationCapabilities({});
      })
      .finally(() => {
        if (!disposed) setPreannotationCapabilitiesResolved(true);
      });
    return () => {
      disposed = true;
    };
  }, [taskId]);

  const capabilityVisible = useCallback(
    (keys: string[], fallback: boolean) => {
      if (!preannotationCapabilitiesResolved) return fallback;
      const item = keys.map((key) => preannotationCapabilities[key]).find(Boolean);
      return item ? isPreannotationCapabilityVisible(item) : fallback;
    },
    [preannotationCapabilities, preannotationCapabilitiesResolved],
  );

  const showAiPreannotation = capabilityVisible(['ai_global_tag', 'vision_llm', 'ai'], false);
  const showAiDetectionPreannotation = capabilityVisible(['ai_bbox_detection'], false);
  const showYoloPreannotation = capabilityVisible(['yolo'], false);
  const showLocateAnythingPreannotation = capabilityVisible(['locateanything'], false);
  const showVlxSeekPreannotation = capabilityVisible(['vlx_seek'], false);
  const showRexOmniPreannotation = capabilityVisible(['rex_omni'], false);

  useEffect(() => {
    let disposed = false;
    let activeLookupCompleted = false;
    let inFlight = false;
    let timer: number | undefined;

    const syncImportState = async () => {
      if (disposed || inFlight) return readTaskLabelDatasetImportState(taskId);
      inFlight = true;
      let next = readTaskLabelDatasetImportState(taskId);
      try {
        if ((!next || (next.status === 'PROCESSING' && !next.jobId)) && !activeLookupCompleted) {
          try {
            const activeResponse = await getActiveLabelDatasetImportJob(taskId);
            activeLookupCompleted = true;
            const activeJob = activeResponse.data;
            if (activeJob) {
              next = {
                taskId,
                labelIds: [],
                jobId: activeJob.id,
                status: activeJob.status === 'FAILED' ? 'FAILED' : 'PROCESSING',
                totalCount: activeJob.sample_count,
                processedCount: activeJob.processed_count,
                importedCount: activeJob.processed_count,
                startedAt: Date.now(),
              };
              writeTaskLabelDatasetImportState(next);
            }
          } catch {
            // A missing active-job response must not affect the sample list.
          }
        }
        if (disposed) return next;
        setLabelDatasetImportState(next);
        if (next?.status === 'PROCESSING' && next.jobId) {
          try {
            const response = await getLabelDatasetImportJob(taskId, next.jobId);
            const job = response.data;
            const terminal = job.status === 'COMPLETED' || job.status === 'FAILED';
            const updated: TaskLabelDatasetImportState = {
              ...next,
              status: job.status === 'COMPLETED' ? 'COMPLETED' : job.status === 'FAILED' ? 'FAILED' : 'PROCESSING',
              totalCount: job.sample_count,
              processedCount: job.processed_count,
              importedCount: job.processed_count,
              ...(job.error_message ? { errorMessage: job.error_message } : {}),
              ...(terminal ? { finishedAt: Date.now() } : {}),
            };
            writeTaskLabelDatasetImportState(updated);
            setLabelDatasetImportState(updated);
            next = updated;
          } catch {
            // A transient poll failure must not hide the import state or interrupt the page.
          }
        }
        const signature = next
          ? `${next.status}:${next.finishedAt || next.startedAt}:${next.importedCount || 0}`
          : '';
        if (next?.status === 'COMPLETED' && signature !== labelDatasetImportSignatureRef.current) {
          labelDatasetImportSignatureRef.current = signature;
          revalidator.revalidate();
        }
        return next;
      } finally {
        inFlight = false;
      }
    };

    const scheduleNext = (state: TaskLabelDatasetImportState | null | undefined) => {
      if (disposed || document.visibilityState === 'hidden' || timer !== undefined) return;
      if (state?.status !== 'PROCESSING' || !state.jobId) return;
      timer = window.setTimeout(async () => {
        timer = undefined;
        const next = await syncImportState();
        scheduleNext(next);
      }, 5000);
    };

    void syncImportState().then(scheduleNext);
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'hidden') {
        if (timer !== undefined) {
          window.clearTimeout(timer);
          timer = undefined;
        }
        return;
      }
      void syncImportState().then(scheduleNext);
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      disposed = true;
      if (timer !== undefined) window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [revalidator, taskId]);
  const [selectedAutoLabelStates, setSelectedAutoLabelStates] = useState<BatchAutoLabelSampleState[]>(() => {
    return readBatchAutoLabelStates();
  });
  const [skipCompleteGlobalTags, setSkipCompleteGlobalTags] = useState(() => {
    return localStorage.getItem('ai_skip_complete_global_tags') !== 'false';
  });
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const activeTaskJobType = useMemo<'ai' | 'ai_detection' | 'yolo' | 'locateanything' | 'vlx_seek' | 'rex_omni' | 'bounds' | null>(() => {
    if (isBatchLabeling || isActiveJobStatus(batchJob?.status)) return 'ai';
    if (isActiveJobStatus(aiDetectionPreannotateJob?.status)) return 'ai_detection';
    if (isActiveJobStatus(yoloPreannotateJob?.status)) return 'yolo';
    if (isActiveJobStatus(locateAnythingPreannotateJob?.status)) return 'locateanything';
    if (isActiveJobStatus(vlxSeekPreannotateJob?.status)) return 'vlx_seek';
    if (isActiveJobStatus(rexOmniPreannotateJob?.status)) return 'rex_omni';
    if (isBoundsChecking || isActiveJobStatus(boundsCheckProgress?.status)) return 'bounds';
    return null;
  }, [
    batchJob?.status,
    aiDetectionPreannotateJob?.status,
    boundsCheckProgress?.status,
    isBatchLabeling,
    isBoundsChecking,
    locateAnythingPreannotateJob?.status,
    rexOmniPreannotateJob?.status,
    vlxSeekPreannotateJob?.status,
    yoloPreannotateJob?.status,
  ]);
  const isTaskJobBlocked = useCallback(
    (type: 'ai' | 'ai_detection' | 'yolo' | 'locateanything' | 'vlx_seek' | 'rex_omni' | 'bounds') =>
      Boolean(activeTaskJobType && activeTaskJobType !== type),
    [activeTaskJobType],
  );

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const finishBatchJob = useCallback(
    async (job: AutoLabelJobResponse, showMessage = true) => {
      stopPolling();
      setIsBatchLabeling(false);
      setCurrentAutoLabelJobId(null);
      setIsCancellingBatchLabel(false);
      if (showMessage) {
        if (job.status === 'CANCELLED') {
          message.warning(t('batchAutoLabelCancelled', { success: job.success_count, failed: job.failed_count }));
        } else if (job.status === 'FAILED') {
          message.error(job.error_message || t('aiAutoLabelFailed'));
        } else {
          message.success(t('batchAutoLabelCompleted', { success: job.success_count, failed: job.failed_count }));
        }
      }
      revalidator.revalidate();
      window.setTimeout(() => setBatchJob(null), 3000);
    },
    [revalidator, stopPolling, t],
  );

  const pollBatchAutoLabel = useCallback(
    (jobId: number) => {
      stopPolling();
      pollTimerRef.current = setInterval(async () => {
        try {
          const statusResp = await getAutoLabelJobStatus(taskId, jobId);
          const job = statusResp.data;
          setBatchJob(job);

          if (!isActiveJobStatus(job.status)) {
            void finishBatchJob(job);
          }
        } catch {
          stopPolling();
          setIsBatchLabeling(false);
          setCurrentAutoLabelJobId(null);
          setIsCancellingBatchLabel(false);
          setBatchJob(null);
        }
      }, 2500);
    },
    [finishBatchJob, stopPolling, taskId],
  );

  const handleBatchAutoLabel = useCallback(async () => {
    if (!taskId) return;
    if (!selectedAutoLabelStates.length) {
      message.warning(t('batchAutoLabelNoSampleTypes'));
      return;
    }
    setIsBatchLabeling(true);
    try {
      const response = await createAutoLabelJob(taskId, {
        mode: 'TAGS_ONLY',
        filter_by_labels: filterByLabels,
        include_geometry_tools: false,
        sample_states: selectedAutoLabelStates,
        skip_complete_global_tags: skipCompleteGlobalTags,
        overwrite_existing_annotations: false,
      });
      const job = response.data;
      setAutoLabelModalOpen(false);
      setCurrentAutoLabelJobId(job.id);
      setBatchJob(job);
      message.success(t('batchAutoLabelStarted'));
      pollBatchAutoLabel(job.id);
    } catch (error: any) {
      setIsBatchLabeling(false);
      setCurrentAutoLabelJobId(null);
      const msg = error?.response?.data?.msg;
      message.error(msg || t('aiAutoLabelFailed'));
    }
  }, [filterByLabels, selectedAutoLabelStates, skipCompleteGlobalTags, taskId, t, pollBatchAutoLabel]);

  const handleCancelBatchAutoLabel = useCallback(async () => {
    if (!taskId || !currentAutoLabelJobId) return;
    setIsCancellingBatchLabel(true);
    try {
      const response = await cancelAutoLabelJob(taskId, currentAutoLabelJobId);
      const job = response.data;
      setBatchJob(job);
      if (job.status === 'CANCELLED') {
        void finishBatchJob(job);
      } else {
        message.warning(t('batchAutoLabelCancelRequested'));
      }
    } catch (error: any) {
      setIsCancellingBatchLabel(false);
      message.error(error?.response?.data?.msg || t('aiAutoLabelFailed'));
    }
  }, [currentAutoLabelJobId, finishBatchJob, taskId, t]);

  useEffect(() => {
    return () => stopPolling();
  }, [stopPolling]);

  useEffect(() => {
    let ignore = false;

    async function resumeBatchAutoLabel() {
      if (!taskId) return;
      try {
        const response = await getActiveAutoLabelJob(taskId);
        if (ignore || !response.data) {
          return;
        }
        setBatchJob(response.data);
        setCurrentAutoLabelJobId(response.data.id);
        setIsBatchLabeling(true);
        pollBatchAutoLabel(response.data.id);
      } catch {
        if (!ignore) {
          setIsBatchLabeling(false);
        }
      }
    }

    void resumeBatchAutoLabel();

    return () => {
      ignore = true;
    };
  }, [pollBatchAutoLabel, taskId]);

  const handleTaskJobCompleted = useCallback(async () => {
    revalidator.revalidate();
  }, [revalidator]);

  const handlePreannotateProgress = useCallback(
    (
      job: PreannotateJobResponse | null,
      type: 'yolo' | 'tagtool' | 'locateanything' | 'vlx_seek' | 'rex_omni' | 'ai_detection',
    ) => {
      if (type === 'yolo') setYoloPreannotateJob(job);
      if (type === 'ai_detection') setAiDetectionPreannotateJob(job);
      if (type === 'locateanything') setLocateAnythingPreannotateJob(job);
      if (type === 'vlx_seek') setVlxSeekPreannotateJob(job);
      if (type === 'rex_omni') setRexOmniPreannotateJob(job);
    },
    [],
  );

  const handlePreAnnotationUpload = useCallback(
    async (files: RcFile[]) => {
      const file = files[0];
      if (!file || isUploadingPreAnnotation) return;
      setIsUploadingPreAnnotation(true);
      try {
        await uploadPreAnnotationFile(taskId, file);
        message.success('预标注文件已上传，正在导入');
        revalidator.revalidate();
      } catch (error: any) {
        message.error(error?.response?.data?.msg || error?.message || '预标注文件上传失败');
      } finally {
        setIsUploadingPreAnnotation(false);
      }
    },
    [isUploadingPreAnnotation, revalidator, taskId],
  );

  const handleDeleteJsonl = async (id: number) => {
    await deletePreAnnotationFile({
      task_id: taskId,
      file_id: id,
    });

    revalidator.revalidate();
  };

  const handleDeleteSample = async (ids: number[]) => {
    await deleteSamples({ task_id: taskId }, { sample_ids: ids });
    setSelectedSampleIds((currentIds) => currentIds.filter((id) => !ids.includes(id)));
    revalidator.revalidate();
  };

  const columns: ColumnsType<SampleResponse | PreAnnotationFileResponse> = [
    {
      title: t('innerId'),
      dataIndex: 'inner_id',
      key: 'inner_id',
      align: 'left',
      sorter: true,
    },
    {
      title: t('filename'),
      dataIndex: ['file', 'filename'],
      key: 'filename',
      align: 'left',
      render: (filename, record) => {
        const _filename = isPreAnnotationFileResponse(record) ? '' : record.file?.filename ?? '';

        if (isPreAnnotationFileResponse(record)) {
          return (
            <span>
              {formatter.format('ellipsis', _.get(record, 'filename'), { maxWidth: 160, type: 'tooltip' })}
              &nbsp;
              <Tag color="processing">{t('preAnnotation')}</Tag>
            </span>
          );
        }
        return formatter.format('ellipsis', _filename, { maxWidth: 160, type: 'tooltip' });
      },
    },
    {
      title: t('dataPreview'),
      dataIndex: 'file',
      key: 'file',
      align: 'left',
      render: (data, record) => {
        if (isPreAnnotationFileResponse(record)) {
          return '-';
        }

        if (task!.media_type === MediaType.IMAGE) {
          const thumbnailUrl = data.thumbnail_url || data.url;
          return <img src={thumbnailUrl} style={{ width: '116px', height: '70px', objectFit: 'cover' }} />;
        } else if (task!.media_type === MediaType.AUDIO) {
          return <audio src={data?.url} controls />;
        } else {
          return (
            <VideoCard
              size={{ width: 116, height: 70 }}
              src={data?.url}
              title={data?.filename ?? ''}
              showPlayIcon
              showDuration
            />
          );
        }
      },
    },
    {
      title: (
        <>
          {t('preAnnotation')} &nbsp;
          <Tooltip
            title={
              <>
                {t('preAnnotationDescription')}{' '}
                <a
                  href="/docs"
                >
                  {t('example')}
                </a>
              </>
            }
          >
            <QuestionCircleOutlined />
          </Tooltip>
        </>
      ),
      dataIndex: 'is_pre_annotated',
      key: 'is_pre_annotated',
      align: 'left',
      render: (value: boolean, record) => {
        const sampleNames = _.get(record, 'sample_names');

        if (sampleNames) {
          return '';
        }

        return value ? t('yes') : '';
      },
    },
    {
      title: t('annotationState'),
      dataIndex: 'state',
      key: 'state',
      align: 'left',
      width: 90,
      render: (text, record) => {
        if (!isPreAnnotationFileResponse(record) && record.file?.filename?.endsWith('.jsonl')) {
          return '-';
        }

        if (!isTaskReadyToAnnotate) {
          return '-';
        }

        return <TaskStatusComponent status={_.lowerCase(text) as TaskStatusProps['status']} />;
      },
      sorter: true,
    },
    {
      title: t('annotationCount'),
      dataIndex: 'annotated_count',
      key: 'annotated_count',
      align: 'left',
      render: (_unused, record) => {
        if (isPreAnnotationFileResponse(record)) {
          return '';
        }

        let result = 0;
        const resultJson = jsonParse(record?.data?.result);
        for (const key in resultJson) {
          if (key.indexOf('Tool') > -1 && key !== 'textTool' && key !== 'tagTool') {
            const tool = resultJson[key];
            if (!tool.result) {
              let _temp = 0;
              if (tool.length) {
                _temp = tool.length;
              }
              result = result + _temp;
            } else {
              result = result + tool.result.length;
            }
          }
        }
        return result;
      },
      sorter: true,
      width: 90,
    },
    {
      title: t('updaters'),
      dataIndex: 'updaters',
      key: 'updaters',
      align: 'center',
      render: (updaters: TaskSampleUser[], record) => {
        const sampleNames = _.get(record, 'sample_names');

        if (sampleNames) {
          return '-';
        }

        if (!isTaskReadyToAnnotate) {
          return '-';
        }

        if (!updaters || updaters.length === 0) {
          return '-';
        }

        if (updaters.length === 1) {
          return <UserAvatar user={updaters[0]} />;
        }

        return (
          <Button type="text" style={{ padding: '0.25rem', cursor: 'default' }}>
            <FlexLayout items="center" gap=".5rem">
              <Avatar.Group>
                {updaters?.slice(0, 3).map((updater, index) => (
                  <UserAvatar key={index} user={updater} />
                ))}
              </Avatar.Group>
              {updaters.length > 3 && <span>{updaters.length}</span>}
              <Popover
                title={null}
                arrow={false}
                placement="bottom"
                content={
                  <FlexLayout flex="column" gap="1rem">
                    {updaters.map((updater) => (
                      <UserAvatar
                        key={updater.user_id}
                        user={updater}
                        showTooltip={false}
                        shortName={false}
                        style={{ backgroundColor: 'var(--color-primary)', color: '#fff' }}
                      />
                    ))}
                  </FlexLayout>
                }
              >
                <DownOutlined />
              </Popover>
            </FlexLayout>
          </Button>
        );
      },
    },
    {
      title: t('updatedAt'),
      dataIndex: 'updated_at',
      key: 'updated_at',
      align: 'left',
      sorter: true,
      render: (updated_at, record) => {
        const sampleNames = _.get(record, 'sample_names');

        if (sampleNames) {
          return '-';
        }

        if (!isTaskReadyToAnnotate) {
          return '';
        }

        return formatter.format('dateTime', new Date(updated_at), { style: 'YYYY-MM-DD HH:mm' });
      },
    },
    {
      title: '',
      dataIndex: 'option',
      key: 'option',
      width: 140,
      align: 'center',
      fixed: 'right',
      render: (x, record) => {
        if (record.id !== enterRowId) {
          return '';
        }

        if (isPreAnnotationFileResponse(record)) {
          return (
            <FlexLayout items="center">
              <Button
                type="link"
                onClick={() => {
                  if (record.url) void downloadFromUrl(record.url, record.filename ?? '');
                }}
              >
                {t('download')}
              </Button>
              {canManageTask && (
                <Popconfirm title={t('deleteConfirm')} onConfirm={() => handleDeleteJsonl(record.id!)}>
                  <Button type="link" danger>
                    {t('delete')}
                  </Button>
                </Popconfirm>
              )}
            </FlexLayout>
          );
        }

        return (
          <FlexLayout items="center" gap="0.5rem">
            {isTaskReadyToAnnotate && (
              <Link to={`/tasks/${taskId}/samples/${record.id}`}>
                <Button type="link">{t('startAnnotate')}</Button>
              </Link>
            )}
            {canDeleteSample && (
              <Popconfirm title={t('deleteConfirm')} onConfirm={() => handleDeleteSample([record.id!])}>
                <Button type="link" danger>
                  {t('delete')}
                </Button>
              </Popconfirm>
            )}
          </FlexLayout>
        );
      },
    },
  ];

  const rowSelection: TableProps<SampleResponse>['rowSelection'] = {
    columnWidth: 58,
    selectedRowKeys: selectedSampleIds,
    getCheckboxProps: (record) => ({
      disabled: Boolean((record as unknown as PreAnnotationFileResponse).sample_names),
    }),
    onChange: (selectedKeys) => {
      setSelectedSampleIds(selectedKeys.map(Number).filter(Number.isFinite));
    },
  };

  const handleTableChange: TableProps<SampleResponse>['onChange'] = (pagination, filters, sorter) => {
    if (!_.isEmpty(pagination)) {
      searchParams.set('page', `${pagination.current}`);
      searchParams.set('size', `${pagination.pageSize}`);
    }

    if (sorter) {
      let sortValue = '';
      // @ts-ignore
      switch (sorter.order) {
        case 'ascend':
          sortValue = 'asc';
          break;
        case 'descend':
          sortValue = 'desc';
          break;
        case undefined:
          sortValue = 'desc';
          break;
      }
      searchParams.set('sort', `${_.get(sorter, 'field')}:${sortValue}`);
    } else {
      searchParams.delete('sort');
    }

    setSearchParams(searchParams);
  };
  const handlePaginationChange = (page: number, pageSize: number) => {
    const next = new URLSearchParams(searchParams);
    next.set('page', `${page}`);
    next.set('size', `${pageSize}`);
    setSearchParams(next);
  };

  const applySampleSearch = (value: string) => {
    const next = value.trim();
    const params = new URLSearchParams(searchParams);
    params.set('page', '1');
    if (next) params.set('q', next);
    else params.delete('q');
    setSelectedSampleIds([]);
    setSampleSearchDraft(value);
    setSearchParams(params);
  };

  const onMouseEnterRow = (rowId: any) => {
    setEnterRowId(rowId);
  };
  const onRow = (record: any) => {
    return {
      onMouseLeave: () => setEnterRowId(undefined),
      onMouseOver: () => {
        onMouseEnterRow(record.id);
      },
    };
  };

  useLayoutEffect(() => {
    if (task?.media_type !== MediaType.AUDIO) {
      return;
    }

    const handleOnPlay = (e: Event) => {
      const audios = document.getElementsByTagName('audio');
      // 使当前只有一条音频在播放
      for (let i = 0, len = audios.length; i < len; i++) {
        if (audios[i] !== e.target) {
          (audios[i] as HTMLAudioElement).pause();
        }
      }
    };

    document.addEventListener('play', handleOnPlay, true);

    return () => {
      document.removeEventListener('play', handleOnPlay, true);
    };
  }, [task?.media_type]);

  const data = useMemo(() => {
    return [...(preAnnotations ?? []), ...(samples ?? [])];
  }, [preAnnotations, samples]);

  return (
    <FlexLayout flex="column" full gap="1rem">
      <HeaderWrapper flex items="center">
        <FlexLayout.Content full>
          <HeaderStatContainer>
            {isTaskReadyToAnnotate ? (
              <Statistical
                canDeleteSample={canDeleteSample}
                selectedSampleCount={selectedSampleIds.length}
                onDeleteSelectedSamples={() => handleDeleteSample(selectedSampleIds)}
              />
            ) : (
              <GoToEditTask taskStatus={taskStatus} />
            )}
          </HeaderStatContainer>
        </FlexLayout.Content>
      </HeaderWrapper>

      <FlexLayout.Content scroll>
        <FlexLayout justify="space-between" flex="column" gap="1rem" padding="0 1.5rem 1.5rem">
          {labelDatasetImportState?.status === 'PROCESSING' && (
            <Alert
              type="info"
              showIcon
              message="标签图片导入中"
              description={
                <FlexLayout items="center" gap="0.5rem">
                  <Progress
                    percent={
                      labelDatasetImportState.totalCount
                        ? Math.min(
                            100,
                            Math.round(
                              ((labelDatasetImportState.processedCount || 0) /
                                labelDatasetImportState.totalCount) *
                                100,
                            ),
                          )
                        : 0
                    }
                    status="active"
                    showInfo
                    style={{ maxWidth: '18rem', flex: 1 }}
                  />
                  <span>后台处理中，当前页面可以继续查看已有数据。</span>
                </FlexLayout>
              }
            />
          )}
          {labelDatasetImportState?.status === 'COMPLETED' && (
            <Alert
              type="success"
              showIcon
              closable
              message="标签图片导入完成"
              description={`本次已导入 ${labelDatasetImportState.importedCount || 0} 个样本，数据集已自动刷新。`}
            />
          )}
          {labelDatasetImportState?.status === 'FAILED' && (
            <Alert
              type="error"
              showIcon
              closable
              message="标签图片导入失败"
              description={labelDatasetImportState.errorMessage || '请检查标签配置后重试。'}
            />
          )}
          <FlexLayout justify="space-between" items="center" gap="1rem" style={{ flexWrap: 'wrap' }}>
            <ServerSearchInput
              value={sampleSearchDraft}
              onChange={(value) => {
                setSampleSearchDraft(value);
                if (!value) applySampleSearch('');
              }}
              onSearch={applySampleSearch}
              placeholder="按文件名或样本编号搜索"
              ariaLabel="搜索样本"
              style={{ width: 360, maxWidth: '100%' }}
            />
            {sampleSearch && (
              <span style={{ color: '#64748b', fontSize: 13 }}>
                匹配 {metaData?.total ?? 0} 个样本
              </span>
            )}
          </FlexLayout>
          <Table
            columns={columns}
            dataSource={data}
            pagination={false}
            rowKey={(record) => record.id!}
            rowSelection={rowSelection}
            onRow={onRow}
            onChange={handleTableChange}
          />
          <FlexLayout justify="space-between" gap="1rem" items="flex-start">
            <ActionArea>
              <ActionRow data-labelu-ai-annotate-extension-anchor>
                <ExportPortal
                  taskId={+taskId!}
                  sampleIds={selectedSampleIds}
                  mediaType={task!.media_type!}
                  tools={task?.config?.tools}
                >
                  <Button type="link" disabled={selectedSampleIds.length === 0}>
                    {t('batchExport')}
                  </Button>
                </ExportPortal>
                {task?.media_type === MediaType.IMAGE && (
                  <NativeUpload
                    data-labelu-preannotation-upload="1"
                    aria-hidden="true"
                    tabIndex={-1}
                    style={{
                      position: 'absolute',
                      width: 1,
                      height: 1,
                      padding: 0,
                      margin: '-1px',
                      overflow: 'hidden',
                      clip: 'rect(0, 0, 0, 0)',
                      whiteSpace: 'nowrap',
                      border: 0,
                    }}
                    accept=".json,.jsonl,application/json,application/jsonl"
                    multiple={false}
                    disabled={isUploadingPreAnnotation}
                    onChange={(files) => void handlePreAnnotationUpload(files)}
                  />
                )}
                {task?.media_type === MediaType.IMAGE && showAiPreannotation && (
                  <ActionJobSlot>
                    <Button
                      type="link"
                      className="labelu-preannotate-control labelu-preannotate-control-ai"
                      data-labelu-preannotate-action="ai"
                      onClick={() => setAutoLabelModalOpen(true)}
                      disabled={isBatchLabeling || isTaskJobBlocked('ai')}
                      loading={isBatchLabeling}
                    >
                      {t('batchAutoLabel')}
                    </Button>
                    {batchJob && (
                      <TaskProgressItem
                        title="AI批量补全全局标签"
                        status={batchJob.status}
                        processed={batchJob.processed_count}
                        total={batchJob.sample_count}
                        success={batchJob.success_count}
                        failed={batchJob.failed_count}
                        onCancel={
                          isActiveJobStatus(batchJob.status) && currentAutoLabelJobId ? handleCancelBatchAutoLabel : undefined
                        }
                        cancelling={isCancellingBatchLabel}
                      />
                    )}
                  </ActionJobSlot>
                )}
                {task?.media_type === MediaType.IMAGE && isTaskReadyToAnnotate && showAiDetectionPreannotation && (
                  <ActionJobSlot>
                    <PreannotateControl
                      taskId={taskId}
                      type="ai_detection"
                      mode="task"
                      disabled={isTaskJobBlocked('ai_detection')}
                      onProgress={handlePreannotateProgress}
                      onCompleted={handleTaskJobCompleted}
                    />
                    {aiDetectionPreannotateJob && (
                      <TaskProgressItem
                        title="AI批量目标检测"
                        status={aiDetectionPreannotateJob.status}
                        processed={aiDetectionPreannotateJob.processed_count}
                        total={aiDetectionPreannotateJob.sample_count}
                        success={aiDetectionPreannotateJob.success_count}
                        failed={aiDetectionPreannotateJob.failed_count}
                      />
                    )}
                  </ActionJobSlot>
                )}
                {task?.media_type === MediaType.IMAGE && isTaskReadyToAnnotate && showYoloPreannotation && (
                  <>
                    <ActionJobSlot>
                      <PreannotateControl
                        taskId={taskId}
                        type="yolo"
                        mode="task"
                        disabled={isTaskJobBlocked('yolo')}
                        onProgress={handlePreannotateProgress}
                        onCompleted={handleTaskJobCompleted}
                      />
                      {yoloPreannotateJob && (
                        <TaskProgressItem
                          title="批量生成检测框"
                          status={yoloPreannotateJob.status}
                          processed={yoloPreannotateJob.processed_count}
                          total={yoloPreannotateJob.sample_count}
                          success={yoloPreannotateJob.success_count}
                          failed={yoloPreannotateJob.failed_count}
                        />
                      )}
                    </ActionJobSlot>
                  </>
                )}
                {task?.media_type === MediaType.IMAGE && isTaskReadyToAnnotate && showLocateAnythingPreannotation && (
                  <ActionJobSlot>
                    <PreannotateControl
                      taskId={taskId}
                      type="locateanything"
                      mode="task"
                      disabled={isTaskJobBlocked('locateanything')}
                      onProgress={handlePreannotateProgress}
                      onCompleted={handleTaskJobCompleted}
                    />
                    {locateAnythingPreannotateJob && (
                      <TaskProgressItem
                        title="LocateAnything 批量生成检测框"
                        status={locateAnythingPreannotateJob.status}
                        processed={locateAnythingPreannotateJob.processed_count}
                        total={locateAnythingPreannotateJob.sample_count}
                        success={locateAnythingPreannotateJob.success_count}
                        failed={locateAnythingPreannotateJob.failed_count}
                      />
                    )}
                  </ActionJobSlot>
                )}
                {task?.media_type === MediaType.IMAGE && isTaskReadyToAnnotate && showVlxSeekPreannotation && (
                  <ActionJobSlot>
                    <PreannotateControl
                      taskId={taskId}
                      type="vlx_seek"
                      mode="task"
                      disabled={isTaskJobBlocked('vlx_seek')}
                      onProgress={handlePreannotateProgress}
                      onCompleted={handleTaskJobCompleted}
                    />
                    {vlxSeekPreannotateJob && (
                      <TaskProgressItem
                        title="VLX-Seek 批量生成检测框"
                        status={vlxSeekPreannotateJob.status}
                        processed={vlxSeekPreannotateJob.processed_count}
                        total={vlxSeekPreannotateJob.sample_count}
                        success={vlxSeekPreannotateJob.success_count}
                        failed={vlxSeekPreannotateJob.failed_count}
                      />
                    )}
                  </ActionJobSlot>
                )}
                {task?.media_type === MediaType.IMAGE && isTaskReadyToAnnotate && showRexOmniPreannotation && (
                  <ActionJobSlot>
                    <PreannotateControl
                      taskId={taskId}
                      type="rex_omni"
                      mode="task"
                      disabled={isTaskJobBlocked('rex_omni')}
                      onProgress={handlePreannotateProgress}
                      onCompleted={handleTaskJobCompleted}
                    />
                    {rexOmniPreannotateJob && (
                      <TaskProgressItem
                        title="Rex-Omni 批量生成检测框"
                        status={rexOmniPreannotateJob.status}
                        processed={rexOmniPreannotateJob.processed_count}
                        total={rexOmniPreannotateJob.sample_count}
                        success={rexOmniPreannotateJob.success_count}
                        failed={rexOmniPreannotateJob.failed_count}
                      />
                    )}
                  </ActionJobSlot>
                )}
                {task?.media_type === MediaType.IMAGE && isTaskReadyToAnnotate && (
                  <ActionJobSlot>
                    <Popconfirm title="确认开始完整性检查？" onConfirm={startBoundsCheck}>
                      <Button type="link" disabled={isBoundsChecking || isTaskJobBlocked('bounds')} loading={isBoundsChecking}>
                        完整性检查
                      </Button>
                    </Popconfirm>
                    {boundsCheckProgress && (
                      <TaskProgressItem
                        title="完整性检查"
                        status={boundsCheckProgress.status}
                        processed={boundsCheckProgress.processed_count}
                        total={boundsCheckProgress.sample_count}
                        normal={boundsCheckProgress.normal_count}
                        incomplete={boundsCheckProgress.incomplete_count ?? 0}
                        abnormal={boundsCheckProgress.abnormal_count}
                        failed={boundsCheckProgress.failed_count}
                      />
                    )}
                  </ActionJobSlot>
                )}
              </ActionRow>
            </ActionArea>
            <Pagination
              current={parseInt(searchParams.get('page') || '1')}
              pageSize={parseInt(searchParams.get('size') || '10')}
              total={metaData?.total}
              showSizeChanger
              showQuickJumper
              onChange={handlePaginationChange}
            />
          </FlexLayout>
        </FlexLayout>
      </FlexLayout.Content>
      <Modal
        title={t('batchAutoLabel')}
        open={autoLabelModalOpen}
        confirmLoading={isBatchLabeling}
        okText={t('start')}
        cancelText={t('cancel')}
        onOk={handleBatchAutoLabel}
        onCancel={() => setAutoLabelModalOpen(false)}
        okButtonProps={{ disabled: isTaskJobBlocked('ai') }}
      >
        <FlexLayout flex="column" gap="0.75rem">
          <div>
            <div style={{ marginBottom: 8, fontWeight: 500 }}>{t('aiBatchSampleTypes')}</div>
            <Checkbox.Group
              value={selectedAutoLabelStates}
              options={[
                { label: t('aiBatchSampleNew'), value: 'NEW' },
                { label: t('aiBatchSampleIncomplete'), value: 'INCOMPLETE' },
                { label: t('aiBatchSampleDone'), value: 'DONE' },
              ]}
              onChange={(values) => {
                const nextStates = values as BatchAutoLabelSampleState[];
                setSelectedAutoLabelStates(nextStates);
                writeBatchAutoLabelStates(nextStates);
              }}
            />
          </div>
          <Checkbox
            checked={skipCompleteGlobalTags}
            onChange={(event) => {
              setSkipCompleteGlobalTags(event.target.checked);
              localStorage.setItem('ai_skip_complete_global_tags', String(event.target.checked));
            }}
          >
            {t('aiSkipCompleteGlobalTags')}
          </Checkbox>
          <Checkbox
            checked={filterByLabels}
            onChange={(event) => {
              setFilterByLabels(event.target.checked);
              localStorage.setItem('ai_filter_by_labels', String(event.target.checked));
            }}
          >
            {t('filterByLabels')}
          </Checkbox>
        </FlexLayout>
      </Modal>
    </FlexLayout>
  );
};

export default Samples;
