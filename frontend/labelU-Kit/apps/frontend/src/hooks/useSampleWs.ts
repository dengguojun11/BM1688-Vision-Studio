import { useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRevalidator } from 'react-router-dom';

import WebSocketClient from '@/classes/WebsocketClient';

import useMe from './useMe';

export interface TaskSampleUser {
  user_id: number;
  username: string;
  task_id: number;
  sample_id: number;
}

export const SAMPLE_LOCKING_ENABLED = import.meta.env.VITE_SAMPLE_LOCKING_ENABLED === '1';
const EMPTY_CONNECTIONS: TaskSampleUser[] = [];

export default function useSampleWs() {
  const routeParams = useParams();
  const revalidator = useRevalidator();
  const me = useMe();
  const [connections, setConnections] = useState<TaskSampleUser[]>([]);
  const host = window.location.host;
  const token = localStorage.getItem('token')?.split(' ')[1];
  const search = typeof window !== 'undefined' ? window.location.search : '';
  const compareAccessToken =
    typeof window !== 'undefined'
      ? new URLSearchParams(search).get('compare_access_token')
      : null;
  const wsRef = useRef<WebSocketClient | null>(null);

  useEffect(() => {
    if (!SAMPLE_LOCKING_ENABLED) {
      return;
    }

    if (!wsRef.current) {
      return;
    }

    const isMeCurrentEditor = connections[0]?.user_id === me.data?.id;
    const ws = wsRef.current;
    const handleSampleUpdate = (data: TaskSampleUser) => {
      if (!isMeCurrentEditor && data.user_id !== me.data?.id && data.sample_id === +routeParams.sampleId!) {
        revalidator.revalidate();
      }
    };

    ws.on('update', handleSampleUpdate);

    return () => {
      ws.off('update', handleSampleUpdate);
    };
  }, [connections, me.data?.id, revalidator, routeParams.sampleId]);

  useEffect(() => {
    if (!SAMPLE_LOCKING_ENABLED) {
      setConnections(EMPTY_CONNECTIONS);
      return;
    }
    if (compareAccessToken) {
      setConnections(EMPTY_CONNECTIONS);
      return;
    }

    wsRef.current = new WebSocketClient(
      `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${host}/ws/task/${routeParams.taskId}/${
        routeParams.sampleId
      }?token=${token}`,
    );

    const ws = wsRef.current;

    ws.on('peers', (data) => {
      setConnections(data);
    });

    return () => {
      ws.disconnect();
    };
  }, [compareAccessToken, host, routeParams.sampleId, routeParams.taskId, token]);

  const currentSampleUsers = useMemo(() => {
    return connections.filter((item) => item.sample_id === +routeParams.sampleId!);
  }, [connections, routeParams.sampleId]);

  return [currentSampleUsers, connections];
}
