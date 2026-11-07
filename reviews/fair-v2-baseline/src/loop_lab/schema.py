"""Transport and outcome contracts shared by all candidate architectures."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL = "codex-json-decision-v2"
MAX_BATCH = 16


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    value: str
    source: str


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["answered", "completed", "blocked", "needs_approval", "needs_clarification"]
    facts: list[Claim]
    receipt_ids: list[str]


class Call(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    arguments_json: str


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_calls: list[Call] = Field(max_length=MAX_BATCH)
    final: Answer | None
