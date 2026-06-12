import datetime
from pydantic import BaseModel, ConfigDict
from typing import List, Optional

class ImageBase(BaseModel):
    slug: str
    mime_type: str
    file_size: int
    created_at: datetime.datetime
    views: int

class ImageResponse(ImageBase):
    id: str
    is_nsfw: bool
    
    model_config = ConfigDict(from_attributes=True)

class UploadSuccessResponse(BaseModel):
    message: str
    slug: str
    view_url: str
    raw_url: str
    delete_url: str

class StatsItem(BaseModel):
    total_uploads: int
    total_views: int
    total_storage_mb: float
    daily_uploads: int
    most_viewed: List[ImageResponse]
