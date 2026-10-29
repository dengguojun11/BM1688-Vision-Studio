from enum import Enum
import time
from typing import Any, Dict
import uuid
from fastapi import WebSocket
from loguru import logger
import asyncio
from dataclasses import dataclass

from pydantic import BaseModel

HEARTBEAT_INTERVAL = 30
HEARTBEAT_TIMEOUT = 60
WEBSOCKET_SEND_TIMEOUT = 3


class MessageType(str, Enum):
    PEERS = "peers"
    PING = "ping"
    PONG = "pong"
    UPDATE = "update"


class Message(BaseModel):
    type: MessageType
    data: Any = None


@dataclass
class ConnectionData:
    id: str = None
    ws: WebSocket = None
    data: Any = None
    heartbeat_task: asyncio.Task = None
    last_heartbeat: float = time.time()

    def update_heartbeat(self):
        self.last_heartbeat = time.time()


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Dict[str, ConnectionData]] = {}

    def _get_connection(self, client_id: str, connection_id: str) -> ConnectionData | None:
        if client_id in self.active_connections:
            return self.active_connections[client_id].get(connection_id)

        return None

    async def _remove_connection(self, client_id: str, connection_id: str, *, close: bool = False):
        connection = self._get_connection(client_id, connection_id)
        if not connection:
            return

        if close:
            try:
                await connection.ws.close()
            except Exception:
                pass

        current_task = asyncio.current_task()
        heartbeat_task = connection.heartbeat_task
        if heartbeat_task and heartbeat_task is not current_task and not heartbeat_task.done():
            heartbeat_task.cancel()

        if client_id in self.active_connections:
            self.active_connections[client_id].pop(connection_id, None)
            if not self.active_connections[client_id]:
                del self.active_connections[client_id]

        logger.info(f"Client {client_id} disconnected")

    async def _heartbeat(self, client_id: str, connection_id: str):
        while True:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL)

                connection = self._get_connection(client_id, connection_id)
                if not connection:
                    break

                await self.send_message(client_id, connection_id, Message(type=MessageType.PING))

                connection = self._get_connection(client_id, connection_id)
                if connection and time.time() - connection.last_heartbeat > HEARTBEAT_TIMEOUT:
                    logger.error(f"Heartbeat timeout for client {connection.data.username}")
                    await self._remove_connection(client_id, connection_id, close=True)
                    break

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in heartbeat for client {client_id}: {e}")
                await self._remove_connection(client_id, connection_id, close=True)
                break

    async def connect(self, client_id: str, websocket: WebSocket, data: Any = None) -> ConnectionData:
        await websocket.accept()

        if client_id not in self.active_connections:
            self.active_connections[client_id] = {}

        logger.info(f"Client {client_id} connected")
        connection_id = str(uuid.uuid4())
        connection_data = ConnectionData(
            id=connection_id,
            ws=websocket,
            data=data,
            last_heartbeat=time.time(),
            heartbeat_task=asyncio.create_task(self._heartbeat(client_id, connection_id)),
        )

        self.active_connections[client_id][connection_id] = connection_data

        return connection_data

    async def disconnect(self, client_id: str, websocket: WebSocket):
        if client_id not in self.active_connections:
            return

        for connection_id, connection in list(self.active_connections[client_id].items()):
            if connection.ws == websocket:
                await self._remove_connection(client_id, connection_id)
                break

    async def _safe_send_message(self, client_id: str, connection_id: str, message: Message) -> bool:
        connection = self._get_connection(client_id, connection_id)
        if not connection:
            return False

        try:
            await asyncio.wait_for(connection.ws.send_json(message.dict()), timeout=WEBSOCKET_SEND_TIMEOUT)
            return False
        except Exception as e:
            logger.warning(f"Remove stale websocket connection for client {client_id}: {e}")
            await self._remove_connection(client_id, connection_id)
            return True

    async def send_message(self, client_id: str, conn_id: str | None = None, message: Message = None) -> int:
        if not message:
            return 0

        if conn_id:
            return int(await self._safe_send_message(client_id, conn_id, message))

        if client_id not in self.active_connections:
            return 0

        tasks = [
            self._safe_send_message(client_id, connection_id, message)
            for connection_id in list(self.active_connections[client_id].keys())
        ]
        if not tasks:
            return 0

        results = await asyncio.gather(*tasks, return_exceptions=True)
        return sum(1 for result in results if result is True)
