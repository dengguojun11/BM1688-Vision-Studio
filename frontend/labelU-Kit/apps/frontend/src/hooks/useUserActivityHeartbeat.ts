import { useEffect } from 'react';

import request from '@/api/request';

const SESSION_KEY = 'labelu:activity-session';
const HEARTBEAT_MS = 60_000;

function sessionId() {
  let value = sessionStorage.getItem(SESSION_KEY);
  if (!value) {
    value = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    sessionStorage.setItem(SESSION_KEY, value);
  }
  return value;
}

/**
 * Keep a lightweight presence session for personnel statistics.
 * This is intentionally a heartbeat, not a click recorder: it answers
 * "who is in the platform now", while meaningful work comes from business logs.
 */
export function useUserActivityHeartbeat() {
  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      return;
    }

    const send = () => {
      void request
        .post('/v1/user-activity/heartbeat', {
          session_id: sessionId(),
          path: `${window.location.pathname}${window.location.search}`.slice(0, 512),
        })
        .catch(() => undefined);
    };

    send();
    const timer = window.setInterval(send, HEARTBEAT_MS);
    const onVisible = () => {
      if (document.visibilityState === 'visible') send();
    };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', send);

    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', send);
    };
  }, []);
}
