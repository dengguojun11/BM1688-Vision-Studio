import { useMutation, useQueryClient } from '@tanstack/react-query';

import {
  addCollaborator,
  batchAddCollaborators,
  batchRemoveCollaborators,
  createTaskWithBasicConfig,
  removeCollaborator,
  updateTaskConfig,
} from '@/api/services/task';
import { taskKey } from '@/api/queryKeyFactories';

import type { BasicConfigCommand, UpdateCommand } from '../types';

export type CreateTaskMutationVariables =
  | BasicConfigCommand
  | { data: BasicConfigCommand; idempotencyKey?: string };

export function useAddTaskMutation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (variables: CreateTaskMutationVariables) => {
      if ('data' in variables && variables.data) {
        return createTaskWithBasicConfig(variables.data, variables.idempotencyKey);
      }
      return createTaskWithBasicConfig(variables as BasicConfigCommand);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: taskKey.all });
    },
  });
}

export function useUpdateTaskConfigMutation(taskId: number | string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: UpdateCommand) => updateTaskConfig(+taskId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: taskKey.all });
    },
  });
}

export function useAddCollaborator(taskId: number) {
  return useMutation({
    mutationFn: (userId: number) => addCollaborator(taskId, userId),
  });
}

export function useRemoveCollaborator(taskId: number) {
  return useMutation({
    mutationFn: (userId: number) => removeCollaborator(taskId, userId),
  });
}

export function useBatchAddCollaborators(taskId: number) {
  return useMutation({
    mutationFn: (userIds: number[]) => batchAddCollaborators(taskId, userIds),
  });
}

export function useBatchRemoveCollaborators(taskId: number) {
  return useMutation({
    mutationFn: (userIds: number[]) => batchRemoveCollaborators(taskId, userIds),
  });
}
