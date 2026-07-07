from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Dict, Any, List

class ExtractionRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    prompt_id: str
    model_id: str
    model_name: str
    file_ids: Optional[list] = None  # If None, use all files
    constraints: Optional[List[str]] = None  # Extra constraint texts appended to prompt

class ExtractionTaskOut(BaseModel):
    model_config = {"from_attributes": True, "protected_namespaces": ()}

    id: str
    ontology_id: str
    prompt_id: Optional[str]
    model_id: Optional[str]
    status: str
    parameters: Dict[str, Any]
    progress: Dict[str, Any]
    error: Optional[str]
    validation_report: Optional[Dict[str, Any]]
    created_at: datetime
    updated_at: datetime
