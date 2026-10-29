import asyncio
from types import SimpleNamespace

from labelu.internal.adapter.ws.sample import task_ws_endpoint
from labelu.internal.clients.ws import sampleConnectionManager


class DummyWebSocket:
    def __init__(self):
        self.closed = []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def close(self, code=None, reason=None):
        self.closed.append((code, reason))

    async def receive_text(self):
        await asyncio.sleep(0)
        return '{"type":"pong","data":null}'


def test_task_sample_ws_locking_is_disabled_by_default():
    websocket = DummyWebSocket()
    sampleConnectionManager.active_connections.clear()

    asyncio.run(
        task_ws_endpoint(
            websocket,
            task_id=1,
            sample_id=2,
            user=SimpleNamespace(id=3, username="pytest"),
        )
    )

    assert websocket.closed == [(1000, "Sample locking disabled")]
    assert websocket.accepted is False
    assert sampleConnectionManager.active_connections == {}
