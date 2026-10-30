import React, { useState, useEffect, useMemo, useRef, useCallback, useLayoutEffect } from 'react';
import { Button, Form } from 'antd';
import { useLocation, useNavigate, useParams, useRevalidator, useRouteLoaderData } from 'react-router-dom';
import _, { filter, isEmpty, size } from 'lodash-es';
import { ArrowLeftOutlined, ArrowRightOutlined } from '@ant-design/icons';
import styled from 'styled-components';
import { Bridge } from 'iframe-message-bridge';
import { FlexLayout } from '@labelu/components-react';
import { useIsFetching, useIsMutating, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from '@labelu/i18n';

import { message, modal } from '@/StaticAnt';
import { createIdempotencyKey } from '@/api/idempotency';
import type { BasicConfigCommand, TaskResponse } from '@/api/types';
import { MediaType, TaskStatus } from '@/api/types';
import { copySamplesByLabels, copySamplesByLabel, createSamplesInBatches, deleteSamples } from '@/api/services/samples';
import {
  createSamplesWithImageCleaning,
  dedupeAttachmentSamples,
  extractImageCleaningDeletedAttachmentIds,
  getImageCleaningCandidateAttachmentIds,
  previewImageCleaning,
  type ImageCleaningPreview,
} from '@/api/services/imageCleaning';
import { applyTaskLabelPreset, applyTaskLabels, deleteFile, deleteTask } from '@/api/services/task';
import { convertAudioAndVideoConfig } from '@/utils/convertAudioAndVideoConfig';
import type { TaskLoaderResult } from '@/loaders/task.loader';
import { useAddTaskMutation, useUpdateTaskConfigMutation } from '@/api/mutations/task';
import { convertImageConfig } from '@/utils/convertImageConfig';
import { createPreAnnotations } from '@/api/services/preAnnotations';
import type { ToolsConfigState } from '@/types/toolConfig';
import { taskKey } from '@/api/queryKeyFactories';

import type { QueuedFile } from './partials/InputData';
import InputData from './partials/InputData';
import AnnotationConfig from './partials/AnnotationConfig';
import InputInfoConfig from './partials/InputInfoConfig';
import type { StepData } from './components/Step';
import Step from './components/Step';
import commonController from '../../utils/common';
import { TaskCreationContext } from './taskCreation.context';
import { ContentWrapper, PreviewFrame, StepRow } from './style';
import { isPreAnnotationFile, UploadStatus } from './partials/InputData/utils';
import { readPersistedAnnotationConfig } from './annotationConfigPersistence';
import { writeTaskLabelDatasetImportState } from '@/utils/taskLabelDatasetImport';

type TranslationHook = () => { t: (key: string, options?: Record<string, unknown>) => string };
const useTranslationCompat = useTranslation as unknown as TranslationHook;

enum StepEnum {
  Basic = 'basic',
  Upload = 'upload',
  Config = 'config',
}

const partialMapping = {
  [StepEnum.Basic]: InputInfoConfig,
  [StepEnum.Upload]: InputData,
  [StepEnum.Config]: AnnotationConfig,
};

const StyledFooter = styled.div`
  display: flex;
  justify-content: end;
  margin-top: 1rem;
  gap: 0.5rem;
`;

function isContainEmptyOption(config: Partial<ToolsConfigState>) {
  for (const tool of config.tools ?? []) {
    const attributes = tool.config?.attributes ?? [];

    if (!attributes || attributes.length === 0) {
      return 'attribute';
    }

    const toolName = tool.tool;

    // 检查全局工具里的属性是否有空选项
    if (['tagTool', 'textTool'].includes(toolName)) {
      for (const attribute of attributes) {
        if (attribute.type === 'enum' && attribute.options.length === 0) {
          return 'option';
        }
      }
    }

    for (const attribute of attributes) {
      if (Array.isArray(attribute.attributes)) {
        for (const subAttribute of attribute.attributes) {
          if (subAttribute.type === 'enum' && subAttribute.options.length === 0) {
            return 'option';
          }
        }
      }
    }
  }
}

function confirmImageCleaning(preview: ImageCleaningPreview): Promise<boolean> {
  return new Promise((resolve) => {
    let settled = false;
    const settle = (value: boolean) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    modal.confirm({
      title: '图片清洗预览',
      content: `本批 ${preview.total_count} 张可读取图片，当前相似度阈值 ${(preview.threshold * 100).toFixed(
        2,
      )}%，预计删除 ${preview.duplicate_count} 张，保留 ${preview.keep_count} 张，跳过 ${
        preview.skipped_count ?? preview.skipped_attachment_ids?.length ?? 0
      } 张。确认后重复图片将被物理删除。`,
      okText: '清洗并继续',
      cancelText: '取消',
      onOk: () => settle(true),
      onCancel: () => settle(false),
    });
  });
}

interface TaskStep extends StepData {
  value: StepEnum;
}

export interface PartialConfigProps {
  task: TaskResponse;
  updateFormData: (field: string) => (value: string) => void;
}

const CreateTask = () => {
  const revalidator = useRevalidator();
  const routerLoaderData = useRouteLoaderData('task') as TaskLoaderResult;
  const taskData = _.get(routerLoaderData, 'task');
  const samples = _.get(routerLoaderData, 'samples');
  const toolsConfig = taskData?.config;
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const routeParams = useParams();
  const location = useLocation();
  const [annotationFormInstance] = Form.useForm();
  const [basicFormInstance] = Form.useForm();
  const modalRef = useRef<any>(null);
  const previewIframeRef = useRef<HTMLIFrameElement>(null);
  const bridgeRef = useRef<Bridge | null>(null);
  const { t } = useTranslationCompat();

  const taskId = routeParams.taskId ? parseInt(routeParams.taskId, 10) : 0;
  const [currentStep, setCurrentStep] = useState<StepEnum>(
    location.hash ? (location.hash.replace('#', '') as StepEnum) : StepEnum.Basic,
  );
  const searchParams = new URLSearchParams(location.search);
  const isCreateNewTask = searchParams.get('isNew') === 'true';
  const [isAnnotationFormValid, toggleAnnotationFormValidation] = useState<boolean>(true);

  const addTask = useAddTaskMutation();
  const updateTaskConfig = useUpdateTaskConfigMutation(taskId);

  const isFetching = useIsFetching();
  const isMutating = useIsMutating();

  // 缓存上传的文件清单
  const [uploadFileList, setUploadFileList] = useState<QueuedFile[]>([]);
  const [labelDatasetImportIds, setLabelDatasetImportIds] = useState<number[]>([]);
  const labelDatasetImportTriggeredRef = useRef(false);
  const [isSubmittingStep, setIsSubmittingStep] = useState(false);
  const submitInProgressRef = useRef(false);
  const taskCreateIdempotencyKeyRef = useRef<string | null>(null);

  const updateCurrentStep = useCallback(
    (step: StepEnum) => {
      setCurrentStep(step);
      navigate({
        pathname: location.pathname,
        hash: step,
        search: location.search,
      });
    },
    [location.pathname, location.search, navigate],
  );

  const stepTitleMapping = useMemo(
    () => ({
      [StepEnum.Basic]: t('basicConfig'),
      [StepEnum.Upload]: t('uploadData'),
      [StepEnum.Config]: t('annotationConfig'),
    }),
    [t],
  );

  const partials = useMemo(() => {
    return _.chain(partialMapping)
      .toPairs()
      .map(([key, Partial], index) => {
        return (
          <FlexLayout.Content key={index} style={{ display: currentStep === key ? 'flex' : 'none' }} flex="column">
            <Partial />
          </FlexLayout.Content>
        );
      })
      .value();
  }, [currentStep]);
  const loading = isMutating > 0 || isFetching > 0 || isSubmittingStep;
  const isExistTask = taskId > 0;
  const taskStatus = _.get(taskData, 'status') as TaskStatus;
  const stepDataSource: TaskStep[] = useMemo(
    () => [
      {
        title: stepTitleMapping[StepEnum.Basic],
        value: StepEnum.Basic,
        isFinished: isExistTask,
      },
      {
        title: stepTitleMapping[StepEnum.Upload],
        value: StepEnum.Upload,
        isFinished: taskStatus && taskStatus !== TaskStatus.DRAFT,
      },
      {
        title: stepTitleMapping[StepEnum.Config],
        value: StepEnum.Config,
        isFinished: [TaskStatus.CONFIGURED, TaskStatus.FINISHED, TaskStatus.INPROGRESS].includes(taskStatus),
      },
    ],
    [isExistTask, stepTitleMapping, taskStatus],
  );

  useEffect(() => {
    if (!location.hash) {
      return;
    }

    setCurrentStep(location.hash.replace('#', '') as StepEnum);
  }, [location.hash]);

  // 将store中的task toolConfig数据同步到本地页面中
  useEffect(() => {
    annotationFormInstance.setFieldsValue(toolsConfig);
  }, [annotationFormInstance, toolsConfig]);

  useEffect(() => {
    basicFormInstance.setFieldsValue(taskData);
  }, [annotationFormInstance, basicFormInstance, taskData]);

  useEffect(() => {
    if (isEmpty(toolsConfig?.tools)) {
      toggleAnnotationFormValidation(false);
    }
  }, [toolsConfig?.tools]);

  const onAnnotationFormChange = useCallback(() => {
    annotationFormInstance.validateFields().then((values) => {
      toggleAnnotationFormValidation(size(values.tools) > 0);
    });
  }, [annotationFormInstance]);

  const [template, setTemplate] = useState<unknown>(null);
  const patchTaskConfigInLoader = useCallback(
    (nextConfig: unknown) => {
      if (!taskId) {
        return;
      }

      queryClient.setQueryData(taskKey.detail(taskId), (prev: any) => {
        if (!prev?.data) {
          return prev;
        }

        return {
          ...prev,
          data: {
            ...prev.data,
            config: nextConfig,
          },
        };
      });
    },
    [queryClient, taskId],
  );

  const importSelectedLabelImages = useCallback(() => {
    if (
      !isCreateNewTask ||
      taskId <= 0 ||
      labelDatasetImportIds.length === 0 ||
      labelDatasetImportTriggeredRef.current
    ) {
      return;
    }
    labelDatasetImportTriggeredRef.current = true;
    const startedAt = Date.now();
    writeTaskLabelDatasetImportState({
      taskId,
      labelIds: labelDatasetImportIds,
      status: 'PROCESSING',
      startedAt,
    });
    void copySamplesByLabels(taskId, labelDatasetImportIds)
      .then((response) => {
        writeTaskLabelDatasetImportState({
          taskId,
          labelIds: labelDatasetImportIds,
          jobId: response?.data?.id,
          status: response?.data?.status === 'FAILED' ? 'FAILED' : 'PROCESSING',
          totalCount: response?.data?.sample_count ?? 0,
          processedCount: response?.data?.processed_count ?? 0,
          startedAt,
          ...(response?.data?.status === 'FAILED' ? { finishedAt: Date.now() } : {}),
        });
      })
      .catch((error: any) => {
        writeTaskLabelDatasetImportState({
          taskId,
          labelIds: labelDatasetImportIds,
          status: 'FAILED',
          errorMessage: error?.response?.data?.msg || error?.message || '标签图片导入失败',
          startedAt,
          finishedAt: Date.now(),
        });
      });
  }, [isCreateNewTask, labelDatasetImportIds, taskId]);

  const handleSave = useCallback(
    async function (isFromCancel?: boolean) {
      try {
        await annotationFormInstance.validateFields();
      } catch (err) {
        commonController.notificationErrorMessage({ message: t('pleaseCheckToolConfig') }, 2);
        return;
      }

      const annotationConfig = readPersistedAnnotationConfig(annotationFormInstance);
      const emptyType = isContainEmptyOption(annotationConfig);

      if (emptyType === 'attribute') {
        commonController.notificationErrorMessage({ message: t('mustHaveAtLeastOneAttribute') }, 2);
        return;
      }

      if (emptyType === 'option') {
        commonController.notificationErrorMessage({ message: t('mustHaveAtLeastOneOption') }, 2);
        return;
      }

      if (
        _.chain(annotationConfig).get('tools').isEmpty().value() &&
        currentStep === StepEnum.Config &&
        !isFromCancel
      ) {
        commonController.notificationErrorMessage({ message: t('pleaseSelectTool') }, 2);
        return;
      }

      return updateTaskConfig
        .mutateAsync({
          config: JSON.stringify(annotationConfig),
        })
        .then(() => {
          if (currentStep === StepEnum.Config) {
            importSelectedLabelImages();
          }
          navigate(`/tasks/${taskData!.id}`);
          revalidator.revalidate();
        });
    },
    [
      annotationFormInstance,
      currentStep,
      importSelectedLabelImages,
      navigate,
      revalidator,
      t,
      taskData,
      updateTaskConfig,
    ],
  );

  const [previewVisible, setPreviewVisible] = useState(false);
  const handleOpenPreview = useCallback(() => {
    annotationFormInstance
      .validateFields()
      .then(() => {
        setPreviewVisible(true);
      })
      .catch(() => {
        commonController.notificationErrorMessage({ message: t('pleaseCheckToolConfig') }, 1);
      });
  }, [annotationFormInstance, t]);

  const updateFileQueue = useCallback((files: any[], sampleIds: number[] | undefined = []) => {
    const fileIdSampleIdMapping = _.chain(files)
      .map((item, index) => {
        return [item.file_id, sampleIds[index]];
      })
      .fromPairs()
      .value();

    setUploadFileList((prev) => {
      return prev.map((item) => {
        if (fileIdSampleIdMapping[item.id!]) {
          return {
            ...item,
            refId: fileIdSampleIdMapping[item.id!],
          };
        }
        return item;
      });
    });
  }, []);

  const correctSampleIdsMappings = useMemo(
    () =>
      _.chain(samples)
        .get('data')
        .mapKeys((item) => {
          return item.file?.id;
        })
        .value(),
    [samples],
  );

  const submitForm: (isFromCancel?: boolean) => Promise<unknown> = useCallback(
    async function (isFromCancel) {
      if (submitInProgressRef.current) {
        return Promise.reject();
      }
      submitInProgressRef.current = true;
      setIsSubmittingStep(true);

      try {
        let basicFormValues: BasicConfigCommand;
        try {
          basicFormValues = await basicFormInstance.validateFields();
        } catch (err) {
          return Promise.reject();
        }

        const mediaFileList: { file_id: number; data: { result: string } }[] = [];
        const preAnnotationFiles: { file_id: number }[] = [];

        for (const file of uploadFileList) {
          if (isPreAnnotationFile(file.file.name) && file.status === UploadStatus.Success && _.isNil(file.refId)) {
            preAnnotationFiles.push({
              file_id: file.id!,
            });
          } else if (file.status === UploadStatus.Success && _.isNil(file.refId)) {
            mediaFileList.push({
              file_id: file.id!,
              data: {
                result: '{}',
              },
            });
          }
        }

        const uniqueMediaFileList = dedupeAttachmentSamples(mediaFileList);

        if (isExistTask) {
          if (currentStep === StepEnum.Upload) {
            if (!_.isEmpty(uniqueMediaFileList)) {
              const shouldCleanImages = (taskData?.media_type || basicFormValues.media_type) === MediaType.IMAGE;
              if (shouldCleanImages) {
                const attachmentIds = uniqueMediaFileList.map((item) => item.file_id);
                const previewResponse = await previewImageCleaning(taskId, attachmentIds);
                const preview = previewResponse?.data;
                if (!preview) {
                  throw new Error('图片清洗预览结果为空');
                }
                const confirmed = await confirmImageCleaning(preview);
                if (!confirmed) {
                  throw new Error('IMAGE_CLEANING_CANCELLED');
                }
                const submittedAttachmentSet = new Set(attachmentIds);
                const candidateAttachmentIds = getImageCleaningCandidateAttachmentIds(preview).filter((id) =>
                  submittedAttachmentSet.has(id),
                );
                const skippedAttachmentIds = (preview.skipped_attachment_ids ?? [])
                  .map(Number)
                  .filter((id) => submittedAttachmentSet.has(id));
                const keepIds = new Set(preview.keep_attachment_ids);
                const cleanedMediaFileList = uniqueMediaFileList.filter((item) => keepIds.has(item.file_id));
                if (candidateAttachmentIds.length > 0) {
                  let response: Awaited<ReturnType<typeof createSamplesWithImageCleaning>>;
                  try {
                    response = await createSamplesWithImageCleaning(taskId, cleanedMediaFileList, {
                      attachment_ids: candidateAttachmentIds,
                      policy_revision: preview.policy_revision,
                      fingerprint: preview.fingerprint,
                    });
                  } catch (error) {
                    // The backend includes this list only after physical deletion
                    // succeeded. Keep failed uploads that still exist retryable.
                    const deletedAttachmentIds = extractImageCleaningDeletedAttachmentIds(
                      error,
                      preview.duplicate_attachment_ids,
                    );
                    if (deletedAttachmentIds.length > 0) {
                      const deletedIds = new Set(deletedAttachmentIds);
                      setUploadFileList((previous) => previous.filter((item) => !deletedIds.has(Number(item.id))));
                      revalidator.revalidate();
                    }
                    throw error;
                  }
                  updateFileQueue(cleanedMediaFileList, response?.data?.ids);
                  const deletedAttachmentIds = response?.data?.deleted_attachment_ids?.length
                    ? response.data.deleted_attachment_ids
                    : preview.duplicate_attachment_ids;
                  const removedIds = new Set([...skippedAttachmentIds, ...deletedAttachmentIds.map(Number)]);
                  if (removedIds.size > 0) {
                    setUploadFileList((previous) => previous.filter((item) => !removedIds.has(Number(item.id))));
                  }
                } else if (skippedAttachmentIds.length > 0) {
                  // The backend rejects an empty cleaning attachment list.  A
                  // preview with only unreadable files is already complete; the
                  // stale queue entries can be removed without creating samples.
                  const skippedIds = new Set(skippedAttachmentIds);
                  setUploadFileList((previous) => previous.filter((item) => !skippedIds.has(Number(item.id))));
                }
              } else {
                const response = await createSamplesInBatches(taskId, uniqueMediaFileList);
                updateFileQueue(uniqueMediaFileList, response?.data?.ids);
              }
            }

            if (!_.isEmpty(preAnnotationFiles)) {
              const response = await createPreAnnotations(taskId, preAnnotationFiles);
              updateFileQueue(preAnnotationFiles, response?.data?.ids);
            }
          }

          const config = readPersistedAnnotationConfig(annotationFormInstance);
          const persistedTaskConfig = taskData?.config ? JSON.stringify(taskData.config) : undefined;
          const nextTaskConfig = currentStep === StepEnum.Config ? JSON.stringify(config) : persistedTaskConfig;

          revalidator.revalidate();

          return updateTaskConfig
            .mutateAsync({
              name: basicFormValues.name,
              description: basicFormValues.description,
              tips: basicFormValues.tips,
              media_type: basicFormValues.media_type,
              dataset_id: basicFormValues.dataset_id,
              dataset_label_contract_id: basicFormValues.dataset_label_contract_id,
              task_label_scope_mode: basicFormValues.task_label_scope_mode,
              config: nextTaskConfig,
            })
            .then(() => {
              if (currentStep === StepEnum.Config) {
                importSelectedLabelImages();
              }
              if (isFromCancel) {
                navigate('/tasks');
              }
            });
        }

        const annotationConfig = readPersistedAnnotationConfig(annotationFormInstance);

        if (!taskCreateIdempotencyKeyRef.current) {
          taskCreateIdempotencyKeyRef.current = createIdempotencyKey('task');
        }
        return addTask.mutateAsync({
          data: basicFormValues,
          idempotencyKey: taskCreateIdempotencyKeyRef.current,
        }).then(async (res) => {
          const newTask = res.data;
          const selectedPresetId = annotationFormInstance.getFieldValue('selectedRectPresetId');
          const sourceLabelId = Number(new URLSearchParams(location.search).get('source_label_id') || 0);

          if (newTask?.id && basicFormValues.media_type === MediaType.IMAGE) {
            const createdTaskId = newTask.id;
            if (sourceLabelId > 0) {
              await applyTaskLabels(createdTaskId, {
                label_ids: [sourceLabelId],
                config: JSON.stringify(annotationConfig),
              });
              const startedAt = Date.now();
              writeTaskLabelDatasetImportState({
                taskId: createdTaskId,
                labelIds: [sourceLabelId],
                status: 'PROCESSING',
                startedAt,
              });
              void copySamplesByLabel(createdTaskId, sourceLabelId)
                .then((response) => {
                  const jobId = response?.data?.id;
                  if (!jobId) {
                    throw new Error('标签图片导入任务创建失败');
                  }
                  writeTaskLabelDatasetImportState({
                    taskId: createdTaskId,
                    labelIds: [sourceLabelId],
                    jobId,
                    status: 'PROCESSING',
                    totalCount: response?.data?.sample_count ?? 0,
                    processedCount: response?.data?.processed_count ?? 0,
                    startedAt,
                  });
                })
                .catch((error: any) => {
                  writeTaskLabelDatasetImportState({
                    taskId: createdTaskId,
                    labelIds: [sourceLabelId],
                    status: 'FAILED',
                    errorMessage: error?.response?.data?.msg || error?.message || '标签图片导入失败',
                    startedAt,
                    finishedAt: Date.now(),
                  });
                });
            } else if (selectedPresetId) {
              await applyTaskLabelPreset(newTask.id, {
                preset_id: selectedPresetId,
              });
            }
          }

          if (isFromCancel) {
            navigate('/tasks');
          } else if (newTask) {
            navigate(`/tasks/${newTask.id}/edit${location.search}#${StepEnum.Upload}`);
          }
          return Promise.reject();
        });
      } finally {
        submitInProgressRef.current = false;
        setIsSubmittingStep(false);
      }
    },
    [
      addTask,
      annotationFormInstance,
      basicFormInstance,
      currentStep,
      isCreateNewTask,
      isExistTask,
      importSelectedLabelImages,
      location.search,
      navigate,
      revalidator,
      taskData,
      taskId,
      updateFileQueue,
      updateTaskConfig,
      uploadFileList,
    ],
  );

  const handleCancel = useCallback(async () => {
    // 在上传数据界面取消时，需要删除已上传的文件\删除已创建的任务
    const uploadedFiles = filter(uploadFileList, (item) => item.status === UploadStatus.Success);
    if (uploadedFiles.length > 0) {
      await deleteFile(
        { task_id: taskId },
        {
          attachment_ids: uploadedFiles.map((item) => item.id!),
        },
      );

      const uploadedSampleIds = uploadedFiles
        .filter((item) => correctSampleIdsMappings[item.id!])
        .map((item) => correctSampleIdsMappings[item.id!].id!);

      if (uploadedSampleIds.length > 0) {
        await deleteSamples(
          {
            task_id: taskId,
          },
          { sample_ids: uploadedSampleIds },
        );
      }
    }

    if (isCreateNewTask && isExistTask) {
      await deleteTask(taskId);
      queryClient.invalidateQueries({ queryKey: taskKey.all });
    }

    modalRef.current.destroy();
    navigate('/tasks');
  }, [correctSampleIdsMappings, isCreateNewTask, isExistTask, navigate, queryClient, taskId, uploadFileList]);

  const handleCancelConfirm = useCallback(() => {
    modalRef.current = modal.confirm({
      title: t('confirm'),
      content: t('saveUnModified'),
      okText: t('saveAndExit'),
      cancelText: t('dontSave'),
      closable: true,
      footer: (
        <StyledFooter>
          <Button onClick={handleCancel}>{t('dontSave')}</Button>
          <Button
            type="primary"
            onClick={async () => {
              modalRef.current.destroy();

              if (currentStep !== StepEnum.Config) {
                await submitForm(true);
              } else {
                await handleSave(true);
              }
            }}
          >
            {t('saveAndExit')}
          </Button>
        </StyledFooter>
      ),
    });
  }, [t, handleCancel, currentStep, submitForm, handleSave]);

  const handleNextStep = useCallback(
    async function (step?: TaskStep | React.MouseEvent) {
      if (submitInProgressRef.current) {
        return;
      }

      let nextStep = step;
      const isClickEvent =
        typeof step === 'object' && step !== null && 'target' in step;
      if (!step || isClickEvent) {
        const stepIndex = stepDataSource.findIndex((item) => item.value === currentStep);
        nextStep = stepDataSource[stepIndex + 1];
      }

      // 如果是从基本信息步骤到下一步，需要校验基本信息表单
      if (currentStep === StepEnum.Basic) {
        try {
          await basicFormInstance.validateFields();
        } catch (err) {
          return;
        }
      }

      // 如果是从「数据导入」到下一步，没有文件时不可进入下一步
      if (
        currentStep === StepEnum.Upload &&
        isEmpty(samples?.data) &&
        filter(uploadFileList, (item) => item.status === UploadStatus.Success && !isPreAnnotationFile(item.name))
          .length === 0
      ) {
        message.error(t('atLeastUploadAFile'));
        return;
      }

      // 文件错误
      if (filter(uploadFileList, (item) => item.status === UploadStatus.Error).length > 0) {
        message.error(t('pleaseHandleErrorFile'));
        return;
      }

      submitForm()
        .then(() => {
          updateCurrentStep((nextStep as TaskStep).value);
          revalidator.revalidate();
        })
        .catch(() => {});
    },
    [
      basicFormInstance,
      currentStep,
      revalidator,
      samples?.data,
      stepDataSource,
      submitForm,
      updateCurrentStep,
      uploadFileList,
      t,
    ],
  );

  const handlePrevStep = async (step: TaskStep, lastStep: TaskStep) => {
    // 如果是从标注配置步骤回到上一步，需要校验配置表单
    if (lastStep.value === StepEnum.Config) {
      try {
        await annotationFormInstance.validateFields();
      } catch (err) {
        message.error(t('pleaseCheckToolConfig'));
        return;
      }

      if (previewVisible) {
        setPreviewVisible(false);
      }
    }
    submitForm()
      .then(() => {
        updateCurrentStep(step.value);
      })
      .catch(() => {});
  };

  const actionNodes = useMemo(() => {
    if (currentStep === StepEnum.Config) {
      if (previewVisible) {
        return (
          <Button onClick={() => setPreviewVisible(false)}>
            <ArrowLeftOutlined />
            {t('exitPreview')}
          </Button>
        );
      }
      const previewDisabled = !isAnnotationFormValid || isEmpty(samples?.data);

      return (
        <FlexLayout gap=".5rem">
          <Button onClick={handleOpenPreview} disabled={previewDisabled}>
            {t('preview')}
            <ArrowRightOutlined />
          </Button>
          <Button onClick={handleCancelConfirm}>{t('cancel')}</Button>
          <Button loading={loading} type="primary" onClick={commonController.debounce(handleSave, 200)}>
            {t('save')}
          </Button>
        </FlexLayout>
      );
    }

    return (
      <FlexLayout gap=".5rem">
        <Button onClick={handleCancelConfirm}>{t('cancel')}</Button>
        <Button loading={loading} type="primary" onClick={() => void handleNextStep()}>
          {t('nextStep')}
        </Button>
      </FlexLayout>
    );
  }, [
    currentStep,
    handleCancelConfirm,
    loading,
    handleNextStep,
    previewVisible,
    isAnnotationFormValid,
    samples?.data,
    handleOpenPreview,
    handleSave,
    t,
  ]);

  const taskCreationContextValue = useMemo(
    () => ({
      uploadFileList,
      setUploadFileList,
      annotationFormInstance,
      selectedTemplate: template,
      onTemplateSelect: setTemplate,
      basicFormInstance,
      task: taskData as NonNullable<TaskLoaderResult['task']>,
      onAnnotationFormChange,
      onTaskConfigPatched: patchTaskConfigInLoader,
      isCreateNewTask,
      labelDatasetImportIds,
      setLabelDatasetImportIds,
    }),
    [
      uploadFileList,
      annotationFormInstance,
      template,
      basicFormInstance,
      taskData,
      onAnnotationFormChange,
      patchTaskConfigInLoader,
      isCreateNewTask,
      labelDatasetImportIds,
      setLabelDatasetImportIds,
    ],
  );

  useLayoutEffect(() => {
    if (!previewIframeRef.current) {
      return;
    }

    if (bridgeRef.current) {
      bridgeRef.current.destroy();
      bridgeRef.current = null;
    }

    bridgeRef.current = new Bridge(previewIframeRef.current.contentWindow!);
    bridgeRef.current.on('ready', () => {
      if (!taskData?.media_type) {
        console.warn('media_type is empty');
        return;
      }

      let _config;

      if ([MediaType.VIDEO, MediaType.AUDIO].includes(taskData?.media_type)) {
        _config = convertAudioAndVideoConfig(annotationFormInstance.getFieldsValue());
      } else if (taskData?.media_type === MediaType.IMAGE) {
        _config = convertImageConfig(annotationFormInstance.getFieldsValue());
      }

      if (bridgeRef.current) {
        bridgeRef.current.post('preview', _config);
      }
    });
  }, [previewVisible, annotationFormInstance, taskData?.media_type]);

  return (
    <FlexLayout.Content flex="column">
      <StepRow flex items="center" justify="space-between">
        <FlexLayout.Header>
          <Step steps={stepDataSource} currentStep={currentStep} onNext={handleNextStep} onPrev={handlePrevStep} />
        </FlexLayout.Header>
        <FlexLayout.Footer>{actionNodes}</FlexLayout.Footer>
      </StepRow>
      <ContentWrapper scroll flex="column">
        <TaskCreationContext.Provider value={taskCreationContextValue}>
          <FlexLayout.Content style={{ display: previewVisible ? 'none' : 'flex' }} flex="column">
            {partials}
          </FlexLayout.Content>

          {previewVisible && (
            <PreviewFrame
              referrerPolicy="no-referrer"
              ref={previewIframeRef}
              src={`/tasks/${taskData!.id}/samples/${samples?.data?.[0].id}?noSave=true`}
            />
          )}
        </TaskCreationContext.Provider>
      </ContentWrapper>
    </FlexLayout.Content>
  );
};

export default CreateTask;
