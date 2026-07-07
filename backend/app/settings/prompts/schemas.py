from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class PromptCreate(BaseModel):
    name: str
    domain: str
    content: str
    version: str = "v1.0"

class PromptUpdate(BaseModel):
    name: Optional[str] = None
    domain: Optional[str] = None
    content: Optional[str] = None
    version: Optional[str] = None

class PromptOut(BaseModel):
    id: str
    name: str
    domain: str
    content: str
    version: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}
