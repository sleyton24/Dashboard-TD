"""
Dashboard Transformación Digital — Sanvest
Backend FastAPI + SQLite, single-file por ahora (ver CLAUDE.md).
"""
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "dev-token")
DATABASE_PATH = os.environ.get("DATABASE_PATH", "./data/dashboard.db")
SEED_DATA_PATH = os.environ.get("SEED_DATA_PATH", "./seed_data.json")

os.makedirs(os.path.dirname(DATABASE_PATH) or ".", exist_ok=True)

DATABASE_URL = f"sqlite:///{DATABASE_PATH}"
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}, echo=False
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


# ─────────────────────────────────────────────────────────────
# Modelos SQLAlchemy
# ─────────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


class Software(Base):
    __tablename__ = "software"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    vendor = Column(String, default="")
    unit = Column(String, default="")
    responsible = Column(String, default="")
    cost = Column(Integer, default=0)
    costPeriod = Column(String, default="mensual")
    contractDate = Column(String, default="")
    renewalDate = Column(String, default="")
    status = Column(String, default="activo")
    notes = Column(String, default="")


class Training(Base):
    __tablename__ = "trainings"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    type = Column(String, default="")
    facilitator = Column(String, default="")
    date = Column(String, default="")
    duration = Column(String, default="")
    attendees = Column(String, default="")
    attendance = Column(Integer, nullable=True)
    status = Column(String, default="programada")
    notes = Column(String, default="")


class GanttTask(Base):
    __tablename__ = "gantt"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    responsible = Column(String, default="")
    startDate = Column(String, nullable=False)
    endDate = Column(String, nullable=False)
    progress = Column(Integer, default=0)
    status = Column(String, default="pendiente")
    notes = Column(String, default="")


class Setting(Base):
    __tablename__ = "settings"
    key = Column(String, primary_key=True)
    value = Column(String, default="")


class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(String, nullable=False)
    actor = Column(String, default="admin")
    action = Column(String, nullable=False)            # CREATE | UPDATE | DELETE | IMPORT
    resource_type = Column(String, nullable=False)     # software | training | gantt | setting | bulk
    resource_id = Column(String, default="")
    before = Column(String, nullable=True)             # JSON string o None
    after = Column(String, nullable=True)


def log_audit(db: Session, action: str, resource_type: str, resource_id: str,
              before=None, after=None, actor: str = "admin"):
    db.add(AuditLog(
        timestamp=datetime.utcnow().isoformat(),
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id or "",
        before=json.dumps(before, ensure_ascii=False) if before is not None else None,
        after=json.dumps(after, ensure_ascii=False) if after is not None else None,
    ))


# ─────────────────────────────────────────────────────────────
# Schemas Pydantic
# ─────────────────────────────────────────────────────────────
class SoftwareSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    vendor: str = ""
    unit: str = ""
    responsible: str = ""
    cost: int = 0
    costPeriod: str = "mensual"
    contractDate: str = ""
    renewalDate: str = ""
    status: str = "activo"
    notes: str = ""


class TrainingSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    type: str = ""
    facilitator: str = ""
    date: str = ""
    duration: str = ""
    attendees: str = ""
    attendance: Optional[int] = None
    status: str = "programada"
    notes: str = ""


class GanttSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    responsible: str = ""
    startDate: str
    endDate: str
    progress: int = 0
    status: str = "pendiente"
    notes: str = ""


class SettingsSchema(BaseModel):
    planStart: Optional[str] = None


# ─────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────
def require_token(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    token = authorization[7:]
    if token != ADMIN_TOKEN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid token")
    return True


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────
# Seed inicial
# ─────────────────────────────────────────────────────────────
def seed_if_empty():
    """Carga seed_data.json si la base está vacía."""
    if not os.path.exists(SEED_DATA_PATH):
        print(f"[seed] No seed file at {SEED_DATA_PATH}, skipping")
        return
    with SessionLocal() as db:
        if db.query(Software).count() > 0:
            print("[seed] DB ya tiene datos, no seedeando")
            return
        print(f"[seed] Cargando datos desde {SEED_DATA_PATH}")
        with open(SEED_DATA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for s in data.get("software", []):
            db.add(Software(**s))
        for t in data.get("trainings", []):
            db.add(Training(**t))
        for g in data.get("gantt", []):
            db.add(GanttTask(**g))
        for k, v in data.get("settings", {}).items():
            db.add(Setting(key=k, value=v))
        db.commit()
        print(
            f"[seed] OK: {len(data.get('software', []))} software, "
            f"{len(data.get('gantt', []))} tareas, "
            f"{len(data.get('settings', {}))} settings"
        )


# ─────────────────────────────────────────────────────────────
# Lifespan: crea tablas + seed al startup
# ─────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    seed_if_empty()
    yield


# ─────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────
app = FastAPI(title="Sanvest TD Dashboard", lifespan=lifespan)

# CORS solo para dev local (Caddy en prod sirve frontend desde mismo origen)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True, "ts": datetime.utcnow().isoformat()}


# ─────────────────────────────────────────────────────────────
# Endpoints — Software
# ─────────────────────────────────────────────────────────────
@app.get("/api/software", response_model=list[SoftwareSchema])
def list_software(_=Depends(require_token), db: Session = Depends(get_db)):
    return db.query(Software).all()


@app.post("/api/software", response_model=SoftwareSchema)
def create_software(
    item: SoftwareSchema,
    _=Depends(require_token),
    db: Session = Depends(get_db),
):
    if db.get(Software, item.id):
        raise HTTPException(409, f"id {item.id} already exists")
    obj = Software(**item.model_dump())
    db.add(obj)
    log_audit(db, "CREATE", "software", item.id, after=item.model_dump())
    db.commit()
    db.refresh(obj)
    return obj


@app.put("/api/software/{item_id}", response_model=SoftwareSchema)
def update_software(
    item_id: str,
    item: SoftwareSchema,
    _=Depends(require_token),
    db: Session = Depends(get_db),
):
    obj = db.get(Software, item_id)
    if not obj:
        raise HTTPException(404, "Not found")
    before = SoftwareSchema.model_validate(obj).model_dump()
    for k, v in item.model_dump().items():
        setattr(obj, k, v)
    log_audit(db, "UPDATE", "software", item_id, before=before, after=item.model_dump())
    db.commit()
    db.refresh(obj)
    return obj


@app.delete("/api/software/{item_id}")
def delete_software(
    item_id: str, _=Depends(require_token), db: Session = Depends(get_db)
):
    obj = db.get(Software, item_id)
    if not obj:
        raise HTTPException(404, "Not found")
    before = SoftwareSchema.model_validate(obj).model_dump()
    db.delete(obj)
    log_audit(db, "DELETE", "software", item_id, before=before)
    db.commit()
    return {"deleted": item_id}


# ─────────────────────────────────────────────────────────────
# Endpoints — Trainings
# ─────────────────────────────────────────────────────────────
@app.get("/api/trainings", response_model=list[TrainingSchema])
def list_trainings(_=Depends(require_token), db: Session = Depends(get_db)):
    return db.query(Training).all()


@app.post("/api/trainings", response_model=TrainingSchema)
def create_training(
    item: TrainingSchema,
    _=Depends(require_token),
    db: Session = Depends(get_db),
):
    if db.get(Training, item.id):
        raise HTTPException(409, f"id {item.id} already exists")
    obj = Training(**item.model_dump())
    db.add(obj)
    log_audit(db, "CREATE", "training", item.id, after=item.model_dump())
    db.commit()
    db.refresh(obj)
    return obj


@app.put("/api/trainings/{item_id}", response_model=TrainingSchema)
def update_training(
    item_id: str,
    item: TrainingSchema,
    _=Depends(require_token),
    db: Session = Depends(get_db),
):
    obj = db.get(Training, item_id)
    if not obj:
        raise HTTPException(404, "Not found")
    before = TrainingSchema.model_validate(obj).model_dump()
    for k, v in item.model_dump().items():
        setattr(obj, k, v)
    log_audit(db, "UPDATE", "training", item_id, before=before, after=item.model_dump())
    db.commit()
    db.refresh(obj)
    return obj


@app.delete("/api/trainings/{item_id}")
def delete_training(
    item_id: str, _=Depends(require_token), db: Session = Depends(get_db)
):
    obj = db.get(Training, item_id)
    if not obj:
        raise HTTPException(404, "Not found")
    before = TrainingSchema.model_validate(obj).model_dump()
    db.delete(obj)
    log_audit(db, "DELETE", "training", item_id, before=before)
    db.commit()
    return {"deleted": item_id}


# ─────────────────────────────────────────────────────────────
# Endpoints — Gantt
# ─────────────────────────────────────────────────────────────
@app.get("/api/gantt", response_model=list[GanttSchema])
def list_gantt(_=Depends(require_token), db: Session = Depends(get_db)):
    return db.query(GanttTask).order_by(GanttTask.startDate).all()


@app.post("/api/gantt", response_model=GanttSchema)
def create_gantt(
    item: GanttSchema,
    _=Depends(require_token),
    db: Session = Depends(get_db),
):
    if db.get(GanttTask, item.id):
        raise HTTPException(409, f"id {item.id} already exists")
    obj = GanttTask(**item.model_dump())
    db.add(obj)
    log_audit(db, "CREATE", "gantt", item.id, after=item.model_dump())
    db.commit()
    db.refresh(obj)
    return obj


@app.put("/api/gantt/{item_id}", response_model=GanttSchema)
def update_gantt(
    item_id: str,
    item: GanttSchema,
    _=Depends(require_token),
    db: Session = Depends(get_db),
):
    obj = db.get(GanttTask, item_id)
    if not obj:
        raise HTTPException(404, "Not found")
    before = GanttSchema.model_validate(obj).model_dump()
    for k, v in item.model_dump().items():
        setattr(obj, k, v)
    log_audit(db, "UPDATE", "gantt", item_id, before=before, after=item.model_dump())
    db.commit()
    db.refresh(obj)
    return obj


@app.delete("/api/gantt/{item_id}")
def delete_gantt(
    item_id: str, _=Depends(require_token), db: Session = Depends(get_db)
):
    obj = db.get(GanttTask, item_id)
    if not obj:
        raise HTTPException(404, "Not found")
    before = GanttSchema.model_validate(obj).model_dump()
    db.delete(obj)
    log_audit(db, "DELETE", "gantt", item_id, before=before)
    db.commit()
    return {"deleted": item_id}


# ─────────────────────────────────────────────────────────────
# Endpoints — Settings
# ─────────────────────────────────────────────────────────────
@app.get("/api/settings", response_model=SettingsSchema)
def get_settings(_=Depends(require_token), db: Session = Depends(get_db)):
    rows = db.query(Setting).all()
    out = {r.key: r.value for r in rows}
    return SettingsSchema(planStart=out.get("planStart"))


@app.put("/api/settings", response_model=SettingsSchema)
def update_settings(
    s: SettingsSchema,
    _=Depends(require_token),
    db: Session = Depends(get_db),
):
    for k, v in s.model_dump().items():
        if v is None:
            continue
        row = db.get(Setting, k)
        if row:
            before = {k: row.value}
            row.value = v
            log_audit(db, "UPDATE", "setting", k, before=before, after={k: v})
        else:
            db.add(Setting(key=k, value=v))
            log_audit(db, "CREATE", "setting", k, after={k: v})
    db.commit()
    return get_settings(_, db)


# ─────────────────────────────────────────────────────────────
# Backup / Restore
# ─────────────────────────────────────────────────────────────
@app.get("/api/export")
def export_all(_=Depends(require_token), db: Session = Depends(get_db)):
    return {
        "exportedAt": datetime.utcnow().isoformat(),
        "version": "1.0",
        "software": [SoftwareSchema.model_validate(s).model_dump() for s in db.query(Software).all()],
        "trainings": [TrainingSchema.model_validate(t).model_dump() for t in db.query(Training).all()],
        "gantt": [GanttSchema.model_validate(g).model_dump() for g in db.query(GanttTask).all()],
        "settings": {r.key: r.value for r in db.query(Setting).all()},
    }


@app.post("/api/import")
def import_all(
    payload: dict,
    _=Depends(require_token),
    db: Session = Depends(get_db),
):
    """Reemplaza todos los datos. Usa con cuidado."""
    counts_before = {
        "software": db.query(Software).count(),
        "trainings": db.query(Training).count(),
        "gantt": db.query(GanttTask).count(),
        "settings": db.query(Setting).count(),
    }
    db.query(Software).delete()
    db.query(Training).delete()
    db.query(GanttTask).delete()
    db.query(Setting).delete()
    for s in payload.get("software", []):
        db.add(Software(**s))
    for t in payload.get("trainings", []):
        db.add(Training(**t))
    for g in payload.get("gantt", []):
        db.add(GanttTask(**g))
    for k, v in payload.get("settings", {}).items():
        db.add(Setting(key=k, value=v))
    counts_after = {
        "software": len(payload.get("software", [])),
        "trainings": len(payload.get("trainings", [])),
        "gantt": len(payload.get("gantt", [])),
        "settings": len(payload.get("settings", {})),
    }
    log_audit(db, "IMPORT", "bulk", "", before=counts_before, after=counts_after)
    db.commit()
    return {"ok": True}


# ─────────────────────────────────────────────────────────────
# Endpoints — Audit log
# ─────────────────────────────────────────────────────────────
@app.get("/api/audit")
def list_audit(
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = 100,
    _=Depends(require_token),
    db: Session = Depends(get_db),
):
    limit = max(1, min(limit, 1000))
    q = db.query(AuditLog).order_by(AuditLog.id.desc())
    if resource_type:
        q = q.filter(AuditLog.resource_type == resource_type)
    if resource_id:
        q = q.filter(AuditLog.resource_id == resource_id)
    if action:
        q = q.filter(AuditLog.action == action)
    return [
        {
            "id": r.id,
            "timestamp": r.timestamp,
            "actor": r.actor,
            "action": r.action,
            "resource_type": r.resource_type,
            "resource_id": r.resource_id,
            "before": json.loads(r.before) if r.before else None,
            "after": json.loads(r.after) if r.after else None,
        }
        for r in q.limit(limit).all()
    ]
