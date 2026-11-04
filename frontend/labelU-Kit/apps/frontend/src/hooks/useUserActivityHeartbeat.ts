import { useEffect } from 'react';

import request from '@/api/request';

const SESSION_KEY = 'labelu:activity-session';
const HEARTBEAT_MS = 60_000;
const IDLE_MS = 2 * 60 * 1000;
const MAX_ACTIVE_SECONDS = 300;

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
 *
 * "Today online" must reflect actual interaction time, not the wall-clock span
 * from first to last heartbeat: a tab left open overnight must not count as
 * twenty hours online.  We therefore accrue one second per second only while
 * the tab is visible and the user interacted within the idle window, and send
 * that delta with the regular heartbeat.
 */
export function useUserActivityHeartbeat() {
  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      return;
    }

    let pendingSeconds = 0;
    let lastInteractionAt = Date.now();

    const markActive = () => {
      lastInteractionAt = Date.now();
    };

    const accrue = () => {
      if (
        document.visibilityState === 'visible' &&
        Date.now() - lastInteractionAt <= IDLE_MS
      ) {
        pendingSeconds += 1;
      }
    };

    const send = (activeSeconds = 0) => {
      const seconds = Math.min(
        MAX_ACTIVE_SECONDS,
        Math.max(0, Math.floor(activeSeconds)),
      );
      pendingSeconds = 0;
      void request
        .post('/v1/user-activity/heartbeat', {
          session_id: sessionId(),
          path: `${window.location.pathname}${window.location.search}`.slice(0, 512),
          active_seconds: seconds,
        })
        .catch(() => undefined);
    };

    const flush = () => {
      send(pendingSeconds);
    };

    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        lastInteractionAt = Date.now();
        return;
      }
      flush();
    };

    const onFocus = () => {
      lastInteractionAt = Date.now();
    };

    send(0);
    const accrueTimer = window.setInterval(accrue, 1_000);
    const heartbeatTimer = window.setInterval(flush, HEARTBEAT_MS);

    window.addEventListener('focus', onFocus);
    window.addEventListener('pointerdown', markActive, { passive: true });
    window.addEventListener('pointermove', markActive, { passive: true });
    window.addEventListener('keydown', markActive);
    window.addEventListener('wheel', markActive, { passive: true });
    window.addEventListener('touchstart', markActive, { passive: true });
    window.addEventListener('scroll', markActive, { passive: true });
    document.addEventListener('visibilitychange', onVisibilityChange);

    return () => {
      window.clearInterval(accrueTimer);
      window.clearInterval(heartbeatTimer);
      window.removeEventListener('focus', onFocus);
      window.removeEventListener('pointerdown', markActive);
      window.removeEventListener('pointermove', markActive);
      window.removeEventListener('keydown', markActive);
      window.removeEventListener('wheel', markActive);
      window.removeEventListener('touchstart', markActive);
      window.removeEventListener('scroll', markActive);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, []);
}
