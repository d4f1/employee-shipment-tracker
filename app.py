import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import jwt
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.templating import Jinja2Templates
from jwt import InvalidTokenError
from pwdlib import PasswordHash
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

load_dotenv()

APP_NAME = os.getenv("APP_NAME", "Employee Shipment Tracker")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./employee_shipments.db")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change-this-development-secret")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_ACCESS_TOKEN_MINUTES = int(os.getenv("JWT_ACCESS_TOKEN_MINUTES", "60"))
JWT_COOKIE_SECURE = os.getenv("JWT_COOKIE_SECURE", "false").lower() == "true"
RAJAONGKIR_MOCK = os.getenv("RAJAONGKIR_MOCK", "true").lower() == "true"
RAJAONGKIR_API_KEY = os.getenv("RAJAONGKIR_API_KEY", "")
RAJAONGKIR_BASE_URL = os.getenv(
    "RAJAONGKIR_BASE_URL", "https://rajaongkir.komerce.id/api/v1"
).rstrip("/")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
password_hash = PasswordHash.recommended()
bearer_scheme = HTTPBearer(auto_error=False)


class Base(DeclarativeBase):
    pass


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150))
    email: Mapped[str] = mapped_column(String(150), unique=True)
    department: Mapped[str] = mapped_column(String(100), default="-")
    shipments: Mapped[list["Shipment"]] = relationship(back_populates="employee")
    user: Mapped[Optional["User"]] = relationship(back_populates="employee", uselist=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(150))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(30), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    employee_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("employees.id"), unique=True, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    employee: Mapped[Optional[Employee]] = relationship(back_populates="user")


class Shipment(Base):
    __tablename__ = "shipments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference_no: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    document_type: Mapped[str] = mapped_column(String(100), default="Document")
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    courier: Mapped[str] = mapped_column(String(40))
    awb: Mapped[str] = mapped_column(String(100), index=True)
    external_awb: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="CREATED")
    origin: Mapped[str] = mapped_column(String(150))
    destination: Mapped[str] = mapped_column(String(150))
    shipping_cost: Mapped[float] = mapped_column(Float, default=0)
    eta_days: Mapped[int] = mapped_column(Integer, default=0)
    expected_arrival: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_location: Mapped[str] = mapped_column(String(200), default="-")
    provider_raw: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    employee: Mapped[Employee] = relationship(back_populates="shipments")
    events: Mapped[list["TrackingEvent"]] = relationship(
        back_populates="shipment", cascade="all, delete-orphan"
    )


class TrackingEvent(Base):
    __tablename__ = "tracking_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"))
    status: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(500))
    location: Mapped[str] = mapped_column(String(200), default="-")
    event_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    shipment: Mapped[Shipment] = relationship(back_populates="events")


Base.metadata.create_all(engine)


def ensure_optional_columns():
    shipment_columns = {column["name"] for column in inspect(engine).get_columns("shipments")}
    if "external_awb" not in shipment_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE shipments ADD COLUMN external_awb VARCHAR(100)"))


ensure_optional_columns()

app = FastAPI(title=APP_NAME, version="0.2.0")
templates = Jinja2Templates(directory="templates")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_access_token(user: User) -> tuple[str, datetime]:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=JWT_ACCESS_TOKEN_MINUTES)
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "employee_id": user.employee_id,
        "iat": datetime.now(timezone.utc),
        "exp": expires_at,
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM), expires_at


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def user_from_token(token: str, db: Session) -> User:
    payload = decode_access_token(token)
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid token subject") from exc
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User is inactive or no longer exists")
    return user


def get_current_api_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Bearer token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_from_token(credentials.credentials, db)


def get_current_web_user(request: Request, db: Session) -> Optional[User]:
    token = request.cookies.get("access_token")
    if not token:
        return None
    try:
        return user_from_token(token, db)
    except HTTPException:
        return None


def require_roles(user: User, *allowed_roles: str) -> None:
    if user.role not in allowed_roles:
        raise HTTPException(
            status_code=403,
            detail=f"Required role: {', '.join(allowed_roles)}",
        )


def can_view_shipment(user: User, shipment: Shipment) -> bool:
    return user.role in {"admin", "operator"} or (
        user.role == "employee" and user.employee_id == shipment.employee_id
    )


class RajaOngkirProvider:
    async def track(self, awb: str, courier: str) -> dict:
        if RAJAONGKIR_MOCK:
            now = datetime.utcnow()
            return {
                "status": "ON_PROCESS",
                "last_location": "Jakarta Distribution Center",
                "delivered_at": None,
                "events": [
                    {
                        "status": "PICKED_UP",
                        "description": "Shipment picked up by courier",
                        "location": "Jakarta",
                        "event_time": now - timedelta(days=1),
                    },
                    {
                        "status": "ON_PROCESS",
                        "description": "Shipment is being processed at distribution center",
                        "location": "Jakarta Distribution Center",
                        "event_time": now,
                    },
                ],
                "raw": {"mock": True, "awb": awb, "courier": courier},
            }

        if not RAJAONGKIR_API_KEY:
            raise HTTPException(status_code=503, detail="RAJAONGKIR_API_KEY is not configured")

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{RAJAONGKIR_BASE_URL}/track/waybill",
                params={"awb": awb, "courier": courier},
                headers={"key": RAJAONGKIR_API_KEY},
            )
        if response.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"RajaOngkir returned HTTP {response.status_code}: {response.text[:300]}",
            )

        payload = response.json()
        data = payload.get("data") or payload.get("rajaongkir") or payload
        summary = data.get("summary") or data.get("delivery_status") or {}
        manifests = data.get("manifest") or data.get("history") or []
        current_status = summary.get("status") or data.get("status") or "UNKNOWN"
        if isinstance(current_status, dict):
            current_status = current_status.get("status") or current_status.get("pod_status") or "UNKNOWN"

        events = []
        for item in manifests:
            event_time = datetime.utcnow()
            time_value = item.get("manifest_date") or item.get("date") or item.get("event_time")
            if time_value:
                try:
                    event_time = datetime.fromisoformat(str(time_value).replace("Z", "+00:00"))
                    if event_time.tzinfo:
                        event_time = event_time.astimezone(timezone.utc).replace(tzinfo=None)
                except ValueError:
                    pass
            events.append({
                "status": item.get("manifest_code") or item.get("status") or "UPDATE",
                "description": item.get("manifest_description") or item.get("description") or "Tracking update",
                "location": item.get("city_name") or item.get("location") or "-",
                "event_time": event_time,
            })

        normalized_status = str(current_status).upper()
        return {
            "status": normalized_status,
            "last_location": events[-1]["location"] if events else "-",
            "delivered_at": datetime.utcnow() if normalized_status in {"DELIVERED", "RECEIVED"} else None,
            "events": events,
            "raw": payload,
        }


provider = RajaOngkirProvider()


def seed_data():
    with SessionLocal() as db:
        employees = db.scalars(select(Employee).order_by(Employee.id)).all()
        if not employees:
            employees = [
                Employee(employee_code="EMP-001", name="Andriana Khadafi", email="andriana@example.com", department="Engineering"),
                Employee(employee_code="EMP-002", name="Siti Rahma", email="siti@example.com", department="Finance"),
                Employee(employee_code="EMP-003", name="Budi Santoso", email="budi@example.com", department="Operations"),
            ]
            db.add_all(employees)
            db.flush()

        if not db.scalar(select(User.id).limit(1)):
            db.add_all([
                User(username="admin", full_name="System Administrator", password_hash=password_hash.hash("Admin123!"), role="admin"),
                User(username="operator", full_name="Delivery Operator", password_hash=password_hash.hash("Operator123!"), role="operator"),
                User(username="andriana", full_name=employees[0].name, password_hash=password_hash.hash("Employee123!"), role="employee", employee_id=employees[0].id),
                User(username="siti", full_name=employees[1].name, password_hash=password_hash.hash("Employee123!"), role="employee", employee_id=employees[1].id),
            ])

        if not db.scalar(select(Shipment.id).limit(1)):
            shipment = Shipment(
                reference_no="DOC-2026-0001",
                title="Employment Contract",
                document_type="Contract",
                employee_id=employees[0].id,
                courier="jne",
                awb="MOCK123456789",
                external_awb="EXT-MOCK-2026-0001",
                status="IN_TRANSIT",
                origin="Jakarta",
                destination="Yogyakarta",
                shipping_cost=28000,
                eta_days=2,
                expected_arrival=datetime.utcnow() + timedelta(days=1),
                last_location="Jakarta Distribution Center",
            )
            db.add(shipment)
            db.flush()
            db.add_all([
                TrackingEvent(shipment_id=shipment.id, status="CREATED", description="Shipment record created", location="Jakarta", event_time=datetime.utcnow() - timedelta(days=2)),
                TrackingEvent(shipment_id=shipment.id, status="IN_TRANSIT", description="Package departed from origin hub", location="Jakarta Distribution Center", event_time=datetime.utcnow() - timedelta(days=1)),
            ])
        db.commit()


seed_data()


@app.get("/api/health")
def health():
    return {"status": "ok", "app": APP_NAME, "rajaongkir_mode": "mock" if RAJAONGKIR_MOCK else "live"}


@app.post("/api/auth/login")
def api_login(
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.username == username))
    if not user or not user.is_active or not password_hash.verify(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    token, expires_at = create_access_token(user)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires_at,
        "user": {
            "id": user.id,
            "username": user.username,
            "full_name": user.full_name,
            "role": user.role,
            "employee_id": user.employee_id,
        },
    }


@app.get("/api/auth/me")
def api_me(current_user: User = Depends(get_current_api_user)):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "full_name": current_user.full_name,
        "role": current_user.role,
        "employee_id": current_user.employee_id,
    }


@app.get("/api/employees")
def list_employees(
    current_user: User = Depends(get_current_api_user),
    db: Session = Depends(get_db),
):
    require_roles(current_user, "admin", "operator")
    return db.scalars(select(Employee).order_by(Employee.name)).all()


@app.get("/healthz")
def health_check():
    return {"status": "ok"}


@app.get("/api/shipments")
def list_shipments(
    current_user: User = Depends(get_current_api_user),
    db: Session = Depends(get_db),
):
    stmt = select(Shipment).order_by(Shipment.created_at.desc())
    if current_user.role == "employee":
        if not current_user.employee_id:
            return []
        stmt = stmt.where(Shipment.employee_id == current_user.employee_id)
    shipments = db.scalars(stmt).all()
    return [{
        "id": s.id,
        "reference_no": s.reference_no,
        "title": s.title,
        "employee": s.employee.name,
        "courier": s.courier,
        "awb": s.awb,
        "external_awb": s.external_awb,
        "status": s.status,
        "origin": s.origin,
        "destination": s.destination,
        "shipping_cost": s.shipping_cost,
        "eta_days": s.eta_days,
        "expected_arrival": s.expected_arrival,
        "last_location": s.last_location,
    } for s in shipments]


@app.get("/api/shipments/{shipment_id}")
def shipment_detail(
    shipment_id: int,
    current_user: User = Depends(get_current_api_user),
    db: Session = Depends(get_db),
):
    shipment = db.get(Shipment, shipment_id)
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")
    if not can_view_shipment(current_user, shipment):
        raise HTTPException(status_code=403, detail="You cannot view this shipment")
    return {
        "id": shipment.id,
        "reference_no": shipment.reference_no,
        "title": shipment.title,
        "document_type": shipment.document_type,
        "employee": shipment.employee.name,
        "courier": shipment.courier,
        "awb": shipment.awb,
        "external_awb": shipment.external_awb,
        "status": shipment.status,
        "origin": shipment.origin,
        "destination": shipment.destination,
        "shipping_cost": shipment.shipping_cost,
        "eta_days": shipment.eta_days,
        "expected_arrival": shipment.expected_arrival,
        "delivered_at": shipment.delivered_at,
        "last_location": shipment.last_location,
        "events": [{
            "status": e.status,
            "description": e.description,
            "location": e.location,
            "event_time": e.event_time,
        } for e in sorted(shipment.events, key=lambda item: item.event_time, reverse=True)],
    }


@app.post("/api/shipments")
def create_shipment(
    reference_no: str = Form(...),
    title: str = Form(...),
    document_type: str = Form("Document"),
    employee_id: int = Form(...),
    courier: str = Form(...),
    awb: str = Form(...),
    external_awb: Optional[str] = Form(None),
    origin: str = Form(...),
    destination: str = Form(...),
    shipping_cost: float = Form(0),
    eta_days: int = Form(0),
    current_user: User = Depends(get_current_api_user),
    db: Session = Depends(get_db),
):
    require_roles(current_user, "admin", "operator")
    if db.scalar(select(Shipment.id).where(Shipment.reference_no == reference_no)):
        raise HTTPException(status_code=409, detail="Reference number already exists")
    if not db.get(Employee, employee_id):
        raise HTTPException(status_code=404, detail="Employee not found")
    shipment = Shipment(
        reference_no=reference_no,
        title=title,
        document_type=document_type,
        employee_id=employee_id,
        courier=courier.lower(),
        awb=awb,
        external_awb=external_awb or None,
        origin=origin,
        destination=destination,
        shipping_cost=shipping_cost,
        eta_days=eta_days,
        expected_arrival=datetime.utcnow() + timedelta(days=eta_days) if eta_days else None,
    )
    db.add(shipment)
    db.flush()
    db.add(TrackingEvent(shipment_id=shipment.id, status="CREATED", description="Shipment record created", location=origin))
    db.commit()
    return {"id": shipment.id, "reference_no": shipment.reference_no}


@app.post("/api/shipments/{shipment_id}/refresh")
async def refresh_tracking(
    shipment_id: int,
    current_user: User = Depends(get_current_api_user),
    db: Session = Depends(get_db),
):
    require_roles(current_user, "admin", "operator")
    shipment = db.get(Shipment, shipment_id)
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")
    await apply_tracking_refresh(shipment, db)
    return {"message": "Tracking refreshed", "status": shipment.status}


async def apply_tracking_refresh(shipment: Shipment, db: Session) -> None:
    result = await provider.track(shipment.awb, shipment.courier)
    shipment.status = result["status"]
    shipment.last_location = result["last_location"]
    shipment.delivered_at = result["delivered_at"]
    shipment.provider_raw = json.dumps(result["raw"], default=str)
    shipment.updated_at = datetime.utcnow()
    existing_keys = {
        (e.status, e.description, e.location, e.event_time.replace(microsecond=0))
        for e in shipment.events
    }
    for event in result["events"]:
        key = (event["status"], event["description"], event["location"], event["event_time"].replace(microsecond=0))
        if key not in existing_keys:
            db.add(TrackingEvent(shipment_id=shipment.id, **event))
    db.commit()


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: Optional[str] = None):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"app_name": APP_NAME, "error": error},
    )


@app.post("/login")
def login_form(
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.username == username))
    if not user or not user.is_active or not password_hash.verify(password, user.password_hash):
        return RedirectResponse(url="/login?error=Incorrect+username+or+password", status_code=303)
    token, _ = create_access_token(user)
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=JWT_COOKIE_SECURE,
        samesite="lax",
        max_age=JWT_ACCESS_TOKEN_MINUTES * 60,
    )
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("access_token")
    return response


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    created: Optional[int] = None,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    employees = db.scalars(select(Employee).order_by(Employee.name)).all() if user.role in {"admin", "operator"} else []
    stmt = select(Shipment).order_by(Shipment.created_at.desc())
    if user.role == "employee":
        stmt = stmt.where(Shipment.employee_id == user.employee_id)
    shipments = db.scalars(stmt).all()
    counts = {
        "total": len(shipments),
        "in_progress": sum(s.status not in {"DELIVERED", "RECEIVED"} for s in shipments),
        "delivered": sum(s.status in {"DELIVERED", "RECEIVED"} for s in shipments),
        "cost": sum(s.shipping_cost for s in shipments),
    }
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "app_name": APP_NAME,
            "current_user": user,
            "employees": employees,
            "shipments": shipments,
            "counts": counts,
            "mock_mode": RAJAONGKIR_MOCK,
            "created": bool(created),
        },
    )


@app.get("/shipments/{shipment_id}", response_class=HTMLResponse)
def shipment_page(
    shipment_id: int,
    request: Request,
    refreshed: Optional[int] = None,
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    shipment = db.get(Shipment, shipment_id)
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")
    if not can_view_shipment(user, shipment):
        raise HTTPException(status_code=403, detail="You cannot view this shipment")
    return templates.TemplateResponse(
        request=request,
        name="shipment.html",
        context={
            "app_name": APP_NAME,
            "shipment": shipment,
            "events": sorted(shipment.events, key=lambda item: item.event_time, reverse=True),
            "current_user": user,
            "mock_mode": RAJAONGKIR_MOCK,
            "refreshed": bool(refreshed),
        },
    )


@app.post("/shipments/create")
def create_shipment_form(
    request: Request,
    reference_no: str = Form(...),
    title: str = Form(...),
    document_type: str = Form("Document"),
    employee_id: int = Form(...),
    courier: str = Form(...),
    awb: str = Form(...),
    external_awb: Optional[str] = Form(None),
    origin: str = Form(...),
    destination: str = Form(...),
    shipping_cost: float = Form(0),
    eta_days: int = Form(0),
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    require_roles(user, "admin", "operator")
    if db.scalar(select(Shipment.id).where(Shipment.reference_no == reference_no)):
        raise HTTPException(status_code=409, detail="Reference number already exists")
    shipment = Shipment(
        reference_no=reference_no,
        title=title,
        document_type=document_type,
        employee_id=employee_id,
        courier=courier.lower(),
        awb=awb,
        external_awb=external_awb or None,
        origin=origin,
        destination=destination,
        shipping_cost=shipping_cost,
        eta_days=eta_days,
        expected_arrival=datetime.utcnow() + timedelta(days=eta_days) if eta_days else None,
    )
    db.add(shipment)
    db.flush()
    db.add(TrackingEvent(shipment_id=shipment.id, status="CREATED", description="Shipment record created", location=origin))
    db.commit()
    return RedirectResponse(url="/?created=1", status_code=303)


@app.post("/shipments/{shipment_id}/refresh")
async def refresh_tracking_form(shipment_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    require_roles(user, "admin", "operator")
    shipment = db.get(Shipment, shipment_id)
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")
    await apply_tracking_refresh(shipment, db)
    return RedirectResponse(url=f"/shipments/{shipment_id}?refreshed=1", status_code=303)
