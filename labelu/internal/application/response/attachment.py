from typing import Union
from pydantic import BaseModel, Field


class AttachmentResponse(BaseModel):
    id: Union[int, None] = Field(
        default=None, description="description: upload file id"
    )
    url: Union[str, None] = Field(
        default=None, description="description: upload file url"
    )
    thumbnail_url: Union[str, None] = Field(
        default=None, description="description: upload file thumbnail url"
    )
    stream_url: Union[str, None] = Field(
        default=None, description="description: upload file stream url"
    )
    annotation_display_url: Union[str, None] = Field(
        default=None, description="description: downscaled image url for annotation page only"
    )
    original_width: Union[int, None] = Field(
        default=None, description="description: original image width in pixels"
    )
    original_height: Union[int, None] = Field(
        default=None, description="description: original image height in pixels"
    )
    storage_backend: Union[str, None] = Field(
        default=None, description="description: upload file storage backend"
    )
    filename: Union[str, None] = Field(
        default=None, description="description: upload file name"
    )
    purpose: Union[str, None] = Field(
        default=None, description="description: attachment upload purpose"
    )
    sha256: Union[str, None] = Field(default=None, description="content SHA-256")
    size_bytes: Union[int, None] = Field(default=None, description="content size")
    
