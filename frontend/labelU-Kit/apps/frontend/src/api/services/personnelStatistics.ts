import request from '@/api/request';

export interface PersonnelSummary {
  date_from: string;
  date_to: string;
  online_now: number;
  active_users: number;
  annotation_images: number;
  annotation_boxes: number;
  review_images: number;
  review_approved: number;
  deleted_images: number;
  effective_work_seconds: number;
}

export interface PersonnelUserRow {
  user_id: number;
  username: string;
  online: boolean;
  last_seen_at?: string | null;
  last_active_at?: string | null;
  last_business_at?: string | null;
  online_seconds: number;
  effective_work_seconds: number;
  annotation_images: number;
  annotation_boxes: number;
  review_images: number;
  review_approved: number;
  deleted_images: number;
  task_count: number;
}

export interface PersonnelDashboard {
  summary: PersonnelSummary;
  users: PersonnelUserRow[];
}

export interface PersonnelDailyRow {
  date: string;
  annotation_images: number;
  annotation_boxes: number;
  review_images: number;
  review_approved: number;
  deleted_images: number;
}

export interface PersonnelDetail {
  user: PersonnelUserRow | null;
  daily: PersonnelDailyRow[];
}

export interface OnlineUser {
  user_id: number;
  username: string;
  login_at: string;
  last_seen_at: string;
  last_path?: string | null;
  ip?: string | null;
  session_id: string;
}

export async function getPersonnelDashboard(params: {
  date_from: string;
  date_to: string;
  user_id?: number;
}) {
  return await request.get('/v1/personnel-statistics/dashboard', { params });
}

export async function getPersonnelDetail(
  userId: number,
  params: { date_from: string; date_to: string },
) {
  return await request.get(`/v1/personnel-statistics/users/${userId}`, { params });
}

export async function getOnlineUsers() {
  return await request.get('/v1/personnel-statistics/online');
}
