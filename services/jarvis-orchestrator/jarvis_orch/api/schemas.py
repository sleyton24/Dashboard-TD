"""Pydantic schemas — contratos de la API HTTP.

Separados de los modelos SQLAlchemy a propósito: la API no debe filtrar
implementación interna de la DB.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Agentes
# ---------------------------------------------------------------------------
class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    codename: str
    name: str
    area: str
    role: str
    parent_id: UUID | None
    description: str | None
    capabilities: dict[str, Any]
    default_model: str
    enabled: bool


# ---------------------------------------------------------------------------
# Invocación
# ---------------------------------------------------------------------------
class InvokeRequest(BaseModel):
    request: str = Field(..., min_length=1, max_length=10_000)
    source: Literal["dashboard", "mcp", "cron", "test"] = "dashboard"
    context: dict[str, Any] | None = None


class JobAttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    original_name: str
    mime: str
    size_bytes: int
    created_at: datetime


class InvokeResponse(BaseModel):
    job_id: UUID
    status: str
    stream_url: str
    attachments: list[JobAttachmentOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
JobStatus = Literal["queued", "running", "needs_approval", "done", "failed", "cancelled"]


class JobStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    seq: int
    agent_codename: str
    kind: str
    model_used: str | None
    payload: dict[str, Any]
    duration_ms: int | None
    created_at: datetime


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    agent_id: UUID
    source: str
    request: str
    status: JobStatus
    response: str | None
    tokens_local: int
    tokens_claude: int
    cost_usd: Decimal
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    created_at: datetime


class JobDetail(JobOut):
    steps: list[JobStepOut] = Field(default_factory=list)
    attachments: list[JobAttachmentOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Aprobaciones
# ---------------------------------------------------------------------------
ApprovalDecision = Literal["approved", "rejected"]


class ApprovalDecisionRequest(BaseModel):
    decision: ApprovalDecision
    reason: str | None = None


class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_id: UUID
    action: str
    payload: dict[str, Any]
    requested_at: datetime
    decided_at: datetime | None
    decision: str | None
    reason: str | None
