from fastapi import APIRouter, HTTPException

from labelu.internal.application.response.base import OkResp
from labelu.internal.common.local_dev_session import read_dev_session_payload

router = APIRouter(prefix="/local-dev", tags=["local_dev"])


@router.get("/session", response_model=OkResp[dict])
async def get_local_dev_session():
    payload = read_dev_session_payload()
    if payload is None:
        raise HTTPException(status_code=404, detail="local dev session is not active")
    return OkResp[dict](data=payload)
