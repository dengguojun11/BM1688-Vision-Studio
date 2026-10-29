import type { UserResponse } from '@/api/types';

/** 与后端 bootstrap_admin.DEFAULT_ADMIN_USERNAME 一致 */
export const DEFAULT_TASK_SUPERUSER_USERNAME = 'admin@123.com';

/** 解析当前用户是否为任务超管（兼容旧版 /users/me 未返回 is_task_superuser） */
export function resolveIsTaskSuperuser(user?: UserResponse | null): boolean {
  if (!user?.username) {
    return false;
  }
  if (user.is_task_superuser === true) {
    return true;
  }
  if (user.is_task_superuser === false) {
    return false;
  }
  return user.username.trim().toLowerCase() === DEFAULT_TASK_SUPERUSER_USERNAME;
}
