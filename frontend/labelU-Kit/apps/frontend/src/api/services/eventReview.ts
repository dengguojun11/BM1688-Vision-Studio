import request from '../request';

/* ---------- types ---------- */

export interface EventReviewItem {
  id: number;
  alarm_id?: string | null;
  camera_name?: string | null;
  event_type?: string | null;
  event_time?: string | null;
  status: 'pending' | 'reviewing' | 'skipped' | 'transferred' | 'cleared';
  reviewer?: string | null;
  thumbnail_url?: string | null;
  created_at?: string | null;
}

export interface EventReviewImage {
  id: number;
  url: string;
  filename?: string | null;
  width?: number | null;
  height?: number | null;
}

export interface EventReviewDetail {
  id: number;
  alarm_id?: string | null;
  camera_name?: string | null;
  camera_location?: string | null;
  event_type?: string | null;
  event_time?: string | null;
  alarm_info?: Record<string, unknown> | null;
  status: string;
  reviewer?: string | null;
  images: EventReviewImage[];
  created_at?: string | null;
}

export interface EventReviewProgress {
  total_pending: number;
  reviewing_count: number;
  done_today: number;
  current_batch: number;
  batch_total: number;
}

export interface EventReviewListResponse {
  meta_data: { total: number; page: number; size: number };
  data: EventReviewItem[];
}

export interface EventReviewDetailResponse {
  data: EventReviewDetail;
}

export interface EventReviewProgressResponse {
  data: EventReviewProgress;
}

export interface EventReviewClaimResponse {
  meta_data: { total: number; page: number; size: number };
  data: EventReviewItem[];
}

export interface EventReviewActionResponse {
  data: EventReviewItem;
}

/* ---------- API ---------- */

export interface EventReviewListParams {
  page?: number;
  size?: number;
  status?: string;
  /** Canonical server-side search parameter. */
  q?: string;
  /** Legacy alias accepted for callers during the contract migration. */
  keyword?: string;
}

export async function getEventReviewList(params: EventReviewListParams = {}): Promise<EventReviewListResponse> {
  const { q, keyword, ...filters } = params;
  const normalizedQuery = (q ?? keyword)?.trim();
  const query = normalizedQuery ? { ...filters, q: normalizedQuery } : filters;
  return await request.get('/v1/event-review', { params: query });
}

export async function getEventReviewProgress(): Promise<EventReviewProgressResponse> {
  return await request.get('/v1/event-review/progress');
}

export async function getEventReviewDetail(eventId: number): Promise<EventReviewDetailResponse> {
  return await request.get(`/v1/event-review/${eventId}`);
}

export async function claimEvents(count: number = 10): Promise<EventReviewClaimResponse> {
  return await request.post('/v1/event-review/claim', { count });
}

export async function performEventAction(
  eventId: number,
  action: 'skip' | 'transfer' | 'clear',
  eventType?: string,
  remark?: string,
): Promise<EventReviewActionResponse> {
  return await request.post(`/v1/event-review/${eventId}/action`, {
    event_id: eventId,
    action,
    event_type: eventType,
    remark,
  });
}

export async function getEventReviewImageBlob(proxyUrl: string): Promise<Blob> {
  const apiUrl = proxyUrl.startsWith('/api') ? proxyUrl.slice(4) : proxyUrl;
  return await request.get(apiUrl, { responseType: 'blob' });
}

export async function setEventReviewToken(token: string): Promise<void> {
  await request.post('/v1/event-review/config', { token });
}
