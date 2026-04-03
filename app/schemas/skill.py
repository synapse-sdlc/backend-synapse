from pydantic import BaseModel


class SkillUpdate(BaseModel):
    content: str


class SkillResponse(BaseModel):
    name: str
    content: str
    source: str  # "custom" or "builtin"
    description: str  # first non-empty line of content
