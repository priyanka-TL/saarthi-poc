from typing import Optional
from pydantic import BaseModel, ConfigDict

class UserContext(BaseModel):
    """
    Core identity derived from a single source (e.g. the JWT token).
    """
    model_config = ConfigDict(frozen=True)

    tenant_code: str
    user_id: str
    organization_id: Optional[str] = None
    locale: str = "en"

class MemorySpec(BaseModel):
    """
    Specifies how much history an agent should receive.
    """
    model_config = ConfigDict(frozen=True)
    
    max_messages: int
