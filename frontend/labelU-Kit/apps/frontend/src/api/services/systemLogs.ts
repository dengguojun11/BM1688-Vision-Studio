import request from '@/api/request';

export type SystemLogCategory = 'user' | 'system';

export interface SystemOperationLogItem {
  id: number;
  category: string;
  action: string;
  summary?: string | null;
  user_id?: number | null;
  username?: string | null;
  source?: string | null;
  method: string;
  path: string;
  status_code?: number | null;
  duration_ms: number;
  created_at: string;
}

export interface SystemOperationLogDetail extends SystemOperationLogItem {
  request_id?: string | null;
  detail: Record<string, unknown>;
}

export async function getSystemOperationLogs(params: {
  category: SystemLogCategory;
  page: number;
  size: number;
  q?: string;
}) {
  return await request.get('/v1/system-logs', { params });
}

export async function getSystemOperationLogDetail(logId: number) {
  return await request.get(`/v1/system-logs/${logId}`);
}
