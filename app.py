import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx
import jwt
from jinja2 import pass_context
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
USER_ROLES = ("admin", "operator", "employee")
DEFAULT_LANGUAGE = "id"
SUPPORTED_LANGUAGES = {"id": "Bahasa", "en": "English"}
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
NOMINATIM_REVERSE_URL = os.getenv(
    "NOMINATIM_REVERSE_URL", "https://nominatim.openstreetmap.org/reverse"
)
GEOCODER_USER_AGENT = os.getenv(
    "GEOCODER_USER_AGENT", "employee-shipment-tracker-demo/1.0"
)

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
    company_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    department: Mapped[str] = mapped_column(String(100), default="-")
    shipments: Mapped[list["Shipment"]] = relationship(back_populates="employee")
    user: Mapped[Optional["User"]] = relationship(back_populates="employee", uselist=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(150))
    company_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(30), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    employee_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("employees.id"), unique=True, nullable=True
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_login_latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_login_longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_login_accuracy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_login_address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    last_login_road: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    last_login_neighbourhood: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    last_login_city: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    last_login_state: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    last_login_country: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    last_login_postcode: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
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
    origin: Mapped[str] = mapped_column(String(500))
    destination: Mapped[str] = mapped_column(String(500))
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
    user_columns = {column["name"] for column in inspect(engine).get_columns("users")}
    missing_user_columns = {
        "last_login_at": "DATETIME",
        "last_login_latitude": "FLOAT",
        "last_login_longitude": "FLOAT",
        "last_login_accuracy": "FLOAT",
        "last_login_address": "VARCHAR(500)",
        "last_login_road": "VARCHAR(200)",
        "last_login_neighbourhood": "VARCHAR(200)",
        "last_login_city": "VARCHAR(200)",
        "last_login_state": "VARCHAR(200)",
        "last_login_country": "VARCHAR(120)",
        "last_login_postcode": "VARCHAR(40)",
        "company_name": "VARCHAR(200)",
        "avatar_url": "VARCHAR(500)",
    }
    with engine.begin() as connection:
        for column_name, column_type in missing_user_columns.items():
            if column_name not in user_columns:
                connection.execute(text(f"ALTER TABLE users ADD COLUMN {column_name} {column_type}"))
    employee_columns = {column["name"] for column in inspect(engine).get_columns("employees")}
    if "company_name" not in employee_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE employees ADD COLUMN company_name VARCHAR(200)"))


ensure_optional_columns()

app = FastAPI(title=APP_NAME, version="0.2.0")
templates = Jinja2Templates(directory="templates")

TRANSLATIONS = {
    "id": {
        "Welcome back": "Selamat datang kembali",
        "Sign in to {app_name} using your assigned account.": "Masuk ke {app_name} menggunakan akun yang diberikan.",
        "Username": "Nama pengguna",
        "Password": "Kata sandi",
        "Current location detection": "Deteksi lokasi saat ini",
        "Location will be detected automatically when you sign in.": "Lokasi akan dideteksi otomatis saat Anda masuk.",
        "Sign in": "Masuk",
        "Signing in...": "Sedang masuk...",
        "Detecting location...": "Mendeteksi lokasi...",
        "Demo accounts": "Akun demo",
        "Location is not supported by this browser. Signing in without location.": "Lokasi tidak didukung browser ini. Masuk tanpa lokasi.",
        "Location detected. Signing in...": "Lokasi terdeteksi. Sedang masuk...",
        "Location permission was skipped. Signing in without location.": "Izin lokasi dilewati. Masuk tanpa lokasi.",
        "Employee delivery operations": "Operasional pengiriman karyawan",
        "Shipping operations overview": "Ringkasan operasional pengiriman",
        "Shipping business portal": "Portal bisnis pengiriman",
        "Track every employee package from origin to arrival.": "Pantau setiap paket karyawan dari asal hingga tiba.",
        "Monitor AWB status, origin, destination, courier movement, and employee handoff from one operational dashboard.": "Pantau status AWB, asal, tujuan, pergerakan kurir, dan serah terima karyawan dari satu dashboard operasional.",
        "Origin hub": "Hub asal",
        "Checked in": "Tercatat",
        "In transit": "Dalam perjalanan",
        "Live AWB": "AWB live",
        "Destination": "Tujuan",
        "ETA 2 days": "ETA 2 hari",
        "Tracking visibility": "Visibilitas pelacakan",
        "Courier lookup": "Pencarian kurir",
        "Login location": "Lokasi masuk",
        "Secure shipment access": "Akses pengiriman aman",
        "Mock API": "API Mock",
        "Live API": "API Live",
        "Logout": "Keluar",
        "access": "akses",
        "Shipment Dashboard": "Dasbor Pengiriman",
        "Monitor company documents and packages from dispatch until employee receipt.": "Pantau dokumen dan paket perusahaan dari pengiriman sampai diterima karyawan.",
        "Create shipment": "Buat pengiriman",
        "Total shipments": "Total pengiriman",
        "In progress": "Dalam proses",
        "Delivered": "Terkirim",
        "Shipping cost": "Biaya kirim",
        "Recent shipments": "Pengiriman terbaru",
        "Sort by date": "Urutkan tanggal",
        "Newest first": "Terbaru dulu",
        "Oldest first": "Terlama dulu",
        "Click a reference number to view its complete delivery timeline.": "Klik nomor referensi untuk melihat linimasa pengiriman lengkap.",
        "Search reference, employee, AWB, or external waybill": "Cari referensi, karyawan, AWB, atau waybill eksternal",
        "Reference": "Referensi",
        "Document": "Dokumen",
        "Employee": "Karyawan",
        "Courier": "Kurir",
        "External waybill": "Waybill eksternal",
        "Route": "Rute",
        "Cost": "Biaya",
        "Status": "Status",
        "Last location": "Lokasi terakhir",
        "No shipments found": "Tidak ada pengiriman",
        "New shipments will appear here.": "Pengiriman baru akan muncul di sini.",
        "No matching shipments": "Tidak ada pengiriman yang cocok",
        "Try another reference, employee name, courier, AWB, or external waybill.": "Coba referensi, nama karyawan, kurir, AWB, atau waybill eksternal lain.",
        "Users": "Pengguna",
        "Create accounts with existing roles.": "Buat akun dengan role yang tersedia.",
        "Full name": "Nama lengkap",
        "Role": "Role",
        "Linked employee": "Karyawan terkait",
        "Choose employee": "Pilih karyawan",
        "Temporary password": "Kata sandi sementara",
        "Profile details": "Detail profil",
        "Company name": "Nama perusahaan",
        "Avatar URL": "URL avatar",
        "Company": "Perusahaan",
        "Active account": "Akun aktif",
        "Add account": "Tambah akun",
        "Adding...": "Menambahkan...",
        "Edit account": "Ubah akun",
        "Update account": "Perbarui akun",
        "Updating...": "Memperbarui...",
        "New password": "Kata sandi baru",
        "Leave blank to keep current password.": "Kosongkan untuk mempertahankan kata sandi saat ini.",
        "Active": "Aktif",
        "Inactive": "Tidak aktif",
        "No users yet.": "Belum ada pengguna.",
        "User account updated successfully.": "Akun pengguna berhasil diperbarui.",
        "New delivery": "Pengiriman baru",
        "Create a shipment": "Buat pengiriman",
        "Register the document, recipient, courier, cost, and expected delivery time.": "Daftarkan dokumen, penerima, kurir, biaya, dan estimasi waktu pengiriman.",
        "Reference number": "Nomor referensi",
        "Document title": "Judul dokumen",
        "Document type": "Jenis dokumen",
        "Employee recipient": "Penerima karyawan",
        "AWB / waybill": "AWB / waybill",
        "Lookup": "Cari",
        "Lookup AWB to fill origin and destination.": "Cari AWB untuk mengisi origin dan destination.",
        "External AWB / waybill": "AWB / waybill eksternal",
        "Origin": "Origin",
        "Uses your detected current location when available.": "Menggunakan lokasi Anda saat ini jika tersedia.",
        "Destination": "Destination",
        "ETA days": "Estimasi hari",
        "Cancel": "Batal",
        "Creating...": "Membuat...",
        "Package map": "Peta paket",
        "Shipment location": "Lokasi pengiriman",
        "Current package location details.": "Detail lokasi paket saat ini.",
        "External AWB": "AWB eksternal",
        "Last updated": "Terakhir diperbarui",
        "Open in Google Maps": "Buka di Google Maps",
        "Invalid AWB code": "Kode AWB tidak valid",
        "AWB lookup failed": "Pencarian AWB gagal",
        "The courier provider could not find this AWB.": "Penyedia kurir tidak menemukan AWB ini.",
        "The courier provider could not process this AWB.": "Penyedia kurir tidak dapat memproses AWB ini.",
        "The courier provider reported this AWB as invalid.": "Penyedia kurir melaporkan AWB ini tidak valid.",
        "Provider detail": "Detail provider",
        "Review AWB": "Periksa AWB",
        "Unknown lookup error.": "Kesalahan pencarian tidak diketahui.",
        "Shipment created successfully.": "Pengiriman berhasil dibuat.",
        "User account created successfully.": "Akun pengguna berhasil dibuat.",
        "Choose courier and enter AWB first.": "Pilih kurir dan isi AWB terlebih dahulu.",
        "Looking up route from courier tracking...": "Mencari rute dari pelacakan kurir...",
        "Route filled from {source}. Last status: {status}.": "Rute diisi dari {source}. Status terakhir: {status}.",
        "courier data": "data kurir",
        "tracking timeline": "linimasa pelacakan",
        "Could not lookup this AWB. Fill origin and destination manually.": "Tidak dapat mencari AWB ini. Isi origin dan destination secara manual.",
        "Browser location is unavailable. Origin uses your last detected address.": "Lokasi browser tidak tersedia. Origin memakai alamat terakhir yang terdeteksi.",
        "Detecting current origin location...": "Mendeteksi lokasi origin saat ini...",
        "Origin filled from your current detected location.": "Origin diisi dari lokasi Anda saat ini.",
        "Could not read address details. Origin uses your last detected address.": "Tidak dapat membaca detail alamat. Origin memakai alamat terakhir yang terdeteksi.",
        "Location permission was skipped. Origin uses your last detected address.": "Izin lokasi dilewati. Origin memakai alamat terakhir yang terdeteksi.",
        "Dashboard": "Dasbor",
        "Shipment reference": "Referensi pengiriman",
        "Current location": "Lokasi saat ini",
        "Shipment information": "Informasi pengiriman",
        "Estimated duration": "Estimasi durasi",
        "Expected arrival": "Estimasi tiba",
        "Delivered at": "Diterima pada",
        "Refresh tracking": "Refresh pelacakan",
        "Provider mode": "Mode provider",
        "Tracking timeline": "Linimasa pelacakan",
        "recorded event(s)": "event tercatat",
        "Latest first": "Terbaru dulu",
        "Refresh tracking status?": "Refresh status pelacakan?",
        "Refresh tracking failed": "Refresh pelacakan gagal",
        "The courier provider could not refresh this AWB.": "Penyedia kurir tidak dapat memperbarui AWB ini.",
        "Refresh now": "Refresh sekarang",
        "Refreshing...": "Sedang refresh...",
        "Tracking refreshed successfully.": "Pelacakan berhasil diperbarui.",
    },
    "en": {},
}


def get_locale(request: Request) -> str:
    lang = request.query_params.get("lang") or request.cookies.get("lang") or DEFAULT_LANGUAGE
    return lang if lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def translate_text(lang: str, text: str, **kwargs) -> str:
    translated = TRANSLATIONS.get(lang, {}).get(text, text)
    return translated.format(**kwargs) if kwargs else translated


@pass_context
def tr_filter(context, value: str) -> str:
    return translate_text(context.get("lang", DEFAULT_LANGUAGE), str(value))


templates.env.filters["tr"] = tr_filter


def localized_context(request: Request, **context):
    lang = get_locale(request)
    return {
        "lang": lang,
        "languages": SUPPORTED_LANGUAGES,
        "app_name": APP_NAME,
        **context,
    }


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


def dashboard_redirect(**params):
    clean_params = {key: value for key, value in params.items() if value}
    query = f"?{urlencode(clean_params)}" if clean_params else ""
    return RedirectResponse(url=f"/{query}", status_code=303)


def parse_optional_float(value: Optional[str]) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def reverse_geocode(latitude: Optional[float], longitude: Optional[float]) -> dict:
    if latitude is None or longitude is None:
        return {}
    try:
        response = httpx.get(
            NOMINATIM_REVERSE_URL,
            params={
                "format": "jsonv2",
                "lat": latitude,
                "lon": longitude,
                "addressdetails": 1,
                "zoom": 18,
            },
            headers={"User-Agent": GEOCODER_USER_AGENT},
            timeout=3,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return {}

    address = payload.get("address") or {}
    return {
        "display_name": payload.get("display_name"),
        "road": address.get("road") or address.get("pedestrian") or address.get("footway"),
        "neighbourhood": address.get("neighbourhood") or address.get("suburb") or address.get("village"),
        "city": address.get("city") or address.get("town") or address.get("municipality") or address.get("county"),
        "state": address.get("state"),
        "country": address.get("country"),
        "postcode": address.get("postcode"),
    }


def format_location_address(details: dict) -> Optional[str]:
    if details.get("display_name"):
        return details["display_name"]
    parts = [
        details.get("road"),
        details.get("neighbourhood"),
        details.get("city"),
        details.get("state"),
        details.get("postcode"),
        details.get("country"),
    ]
    return ", ".join(part for part in parts if part) or None


def find_first_value(payload, keys: tuple[str, ...]) -> Optional[str]:
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized_key = key.lower()
            if normalized_key in keys and value not in (None, ""):
                return str(value)
        for value in payload.values():
            match = find_first_value(value, keys)
            if match:
                return match
    if isinstance(payload, list):
        for item in payload:
            match = find_first_value(item, keys)
            if match:
                return match
    return None


def extract_awb_route(result: dict) -> dict:
    raw = result.get("raw") or {}
    events = result.get("events") or []
    explicit_origin = find_first_value(raw, (
        "origin",
        "origin_city",
        "shipper_city",
        "sender_city",
        "city_origin",
        "origin_name",
    ))
    explicit_destination = find_first_value(raw, (
        "destination",
        "destination_city",
        "receiver_city",
        "recipient_city",
        "consignee_city",
        "city_destination",
        "destination_name",
    ))
    timeline_locations = [
        event.get("location")
        for event in events
        if event.get("location") and event.get("location") != "-"
    ]
    origin = explicit_origin or (timeline_locations[0] if timeline_locations else None)
    destination = explicit_destination or (timeline_locations[-1] if timeline_locations else None)
    return {
        "origin": origin,
        "destination": destination,
        "last_location": result.get("last_location") or destination or "-",
        "status": result.get("status") or "UNKNOWN",
        "events": events,
        "source": "courier_payload" if explicit_origin or explicit_destination else "tracking_timeline",
    }


def record_login_location(
    user: User,
    db: Session,
    latitude: Optional[str] = None,
    longitude: Optional[str] = None,
    accuracy: Optional[str] = None,
) -> None:
    parsed_latitude = parse_optional_float(latitude)
    parsed_longitude = parse_optional_float(longitude)
    address_details = reverse_geocode(parsed_latitude, parsed_longitude)
    user.last_login_at = datetime.utcnow()
    user.last_login_latitude = parsed_latitude
    user.last_login_longitude = parsed_longitude
    user.last_login_accuracy = parse_optional_float(accuracy)
    user.last_login_address = address_details.get("display_name")
    user.last_login_road = address_details.get("road")
    user.last_login_neighbourhood = address_details.get("neighbourhood")
    user.last_login_city = address_details.get("city")
    user.last_login_state = address_details.get("state")
    user.last_login_country = address_details.get("country")
    user.last_login_postcode = address_details.get("postcode")
    db.commit()


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
        sample_employee_companies = {
            "EMP-001": "Acme Logistics",
            "EMP-002": "Nusantara Finance",
            "EMP-003": "Operations Hub",
        }
        if not employees:
            employees = [
                Employee(employee_code="EMP-001", name="Andriana Khadafi", email="andriana@example.com", company_name=sample_employee_companies["EMP-001"], department="Engineering"),
                Employee(employee_code="EMP-002", name="Siti Rahma", email="siti@example.com", company_name=sample_employee_companies["EMP-002"], department="Finance"),
                Employee(employee_code="EMP-003", name="Budi Santoso", email="budi@example.com", company_name=sample_employee_companies["EMP-003"], department="Operations"),
            ]
            db.add_all(employees)
            db.flush()
        else:
            for employee in employees:
                if not employee.company_name and employee.employee_code in sample_employee_companies:
                    employee.company_name = sample_employee_companies[employee.employee_code]

        if not db.scalar(select(User.id).limit(1)):
            db.add_all([
                User(username="admin", full_name="System Administrator", company_name="Employee Shipment Tracker", password_hash=password_hash.hash("Admin123!"), role="admin"),
                User(username="operator", full_name="Delivery Operator", company_name="Employee Shipment Tracker", password_hash=password_hash.hash("Operator123!"), role="operator"),
                User(username="andriana", full_name=employees[0].name, company_name="Employee Shipment Tracker", password_hash=password_hash.hash("Employee123!"), role="employee", employee_id=employees[0].id),
                User(username="siti", full_name=employees[1].name, company_name="Employee Shipment Tracker", password_hash=password_hash.hash("Employee123!"), role="employee", employee_id=employees[1].id),
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
    latitude: Optional[str] = Form(None),
    longitude: Optional[str] = Form(None),
    accuracy: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.username == username))
    if not user or not user.is_active or not password_hash.verify(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    record_login_location(user, db, latitude, longitude, accuracy)
    token, expires_at = create_access_token(user)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires_at,
        "user": {
            "id": user.id,
            "username": user.username,
            "full_name": user.full_name,
            "company_name": user.company_name,
            "avatar_url": user.avatar_url,
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
        "company_name": current_user.company_name,
        "avatar_url": current_user.avatar_url,
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


@app.get("/api/location/reverse")
def reverse_location_for_form(
    request: Request,
    latitude: str = Query(...),
    longitude: str = Query(...),
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Login is required")
    require_roles(user, "admin", "operator")
    parsed_latitude = parse_optional_float(latitude)
    parsed_longitude = parse_optional_float(longitude)
    if parsed_latitude is None or parsed_longitude is None:
        raise HTTPException(status_code=422, detail="Valid latitude and longitude are required")

    details = reverse_geocode(parsed_latitude, parsed_longitude)
    origin = format_location_address(details) or f"{parsed_latitude:.6f}, {parsed_longitude:.6f}"
    return {
        "origin": origin,
        "latitude": parsed_latitude,
        "longitude": parsed_longitude,
        "address": details,
    }


@app.get("/api/awb/lookup")
async def lookup_awb_route(
    request: Request,
    courier: str = Query(...),
    awb: str = Query(...),
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Login is required")
    require_roles(user, "admin", "operator")

    courier = courier.strip().lower()
    awb = awb.strip()
    if not courier or not awb:
        raise HTTPException(status_code=422, detail="Courier and AWB are required")

    result = await provider.track(awb, courier)
    route = extract_awb_route(result)
    return {
        "courier": courier,
        "awb": awb,
        **route,
    }


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
        "employee_company": s.employee.company_name,
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
        "employee_company": shipment.employee.company_name,
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
        context=localized_context(request, error=error),
    )


@app.get("/language")
def set_language(request: Request, lang: str = Query(DEFAULT_LANGUAGE), next: str = Query("/")):
    if lang not in SUPPORTED_LANGUAGES:
        lang = DEFAULT_LANGUAGE
    if not next.startswith("/"):
        next = "/"
    response = RedirectResponse(url=next, status_code=303)
    response.set_cookie("lang", lang, httponly=False, samesite="lax", max_age=60 * 60 * 24 * 365)
    return response


@app.post("/login")
def login_form(
    username: str = Form(...),
    password: str = Form(...),
    latitude: Optional[str] = Form(None),
    longitude: Optional[str] = Form(None),
    accuracy: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.username == username))
    if not user or not user.is_active or not password_hash.verify(password, user.password_hash):
        return RedirectResponse(url="/login?error=Incorrect+username+or+password", status_code=303)
    record_login_location(user, db, latitude, longitude, accuracy)
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
    user_created: Optional[int] = None,
    user_updated: Optional[int] = None,
    user_error: Optional[str] = None,
    sort: str = Query("date_desc"),
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    employees = db.scalars(select(Employee).order_by(Employee.name)).all() if user.role in {"admin", "operator"} else []
    users = db.scalars(select(User).order_by(User.created_at.desc())).all() if user.role == "admin" else []
    assigned_employee_ids = {account.employee_id for account in users if account.employee_id}
    sort = sort if sort in {"date_asc", "date_desc"} else "date_desc"
    sort_expression = Shipment.created_at.asc() if sort == "date_asc" else Shipment.created_at.desc()
    stmt = select(Shipment).order_by(sort_expression)
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
        context=localized_context(
            request,
            current_user=user,
            employees=employees,
            users=users,
            assigned_employee_ids=assigned_employee_ids,
            user_roles=USER_ROLES,
            shipments=shipments,
            counts=counts,
            mock_mode=RAJAONGKIR_MOCK,
            created=bool(created),
            user_created=bool(user_created),
            user_updated=bool(user_updated),
            user_error=user_error,
            sort=sort,
        ),
    )


@app.post("/users/create")
def create_user_form(
    request: Request,
    username: str = Form(...),
    full_name: str = Form(...),
    company_name: Optional[str] = Form(None),
    avatar_url: Optional[str] = Form(None),
    role: str = Form(...),
    password: str = Form(...),
    employee_id: Optional[str] = Form(None),
    is_active: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    require_roles(user, "admin")

    username = username.strip()
    full_name = full_name.strip()
    company_name = company_name.strip() if company_name else None
    avatar_url = avatar_url.strip() if avatar_url else None
    role = role.strip().lower()
    password = password.strip()

    if role not in USER_ROLES:
        return dashboard_redirect(user_error="Choose an existing role.")
    if len(password) < 8:
        return dashboard_redirect(user_error="Password must be at least 8 characters.")
    if db.scalar(select(User.id).where(User.username == username)):
        return dashboard_redirect(user_error="Username already exists.")

    selected_employee_id = int(employee_id) if employee_id and employee_id.isdigit() else None
    if role == "employee":
        if not selected_employee_id:
            return dashboard_redirect(user_error="Employee accounts must be linked to an employee.")
        if not db.get(Employee, selected_employee_id):
            return dashboard_redirect(user_error="Selected employee was not found.")
        if db.scalar(select(User.id).where(User.employee_id == selected_employee_id)):
            return dashboard_redirect(user_error="Selected employee already has an account.")
    else:
        selected_employee_id = None

    db.add(User(
        username=username,
        full_name=full_name,
        company_name=company_name,
        avatar_url=avatar_url,
        role=role,
        password_hash=password_hash.hash(password),
        employee_id=selected_employee_id,
        is_active=is_active == "on",
    ))
    db.commit()
    return dashboard_redirect(user_created=1)


@app.post("/users/{account_id}/update")
def update_user_form(
    account_id: int,
    request: Request,
    username: str = Form(...),
    full_name: str = Form(...),
    company_name: Optional[str] = Form(None),
    avatar_url: Optional[str] = Form(None),
    role: str = Form(...),
    password: Optional[str] = Form(None),
    employee_id: Optional[str] = Form(None),
    is_active: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    require_roles(user, "admin")

    account = db.get(User, account_id)
    if not account:
        return dashboard_redirect(user_error="User account was not found.")

    username = username.strip()
    full_name = full_name.strip()
    company_name = company_name.strip() if company_name else None
    avatar_url = avatar_url.strip() if avatar_url else None
    role = role.strip().lower()
    password = password.strip() if password else ""
    active = is_active == "on"

    if role not in USER_ROLES:
        return dashboard_redirect(user_error="Choose an existing role.")
    if not username or not full_name:
        return dashboard_redirect(user_error="Username and full name are required.")
    existing_username = db.scalar(select(User.id).where(User.username == username, User.id != account.id))
    if existing_username:
        return dashboard_redirect(user_error="Username already exists.")
    if password and len(password) < 8:
        return dashboard_redirect(user_error="Password must be at least 8 characters.")
    if account.id == user.id and (role != "admin" or not active):
        return dashboard_redirect(user_error="You cannot remove admin access from your own account.")

    selected_employee_id = int(employee_id) if employee_id and employee_id.isdigit() else None
    if role == "employee":
        if not selected_employee_id:
            return dashboard_redirect(user_error="Employee accounts must be linked to an employee.")
        if not db.get(Employee, selected_employee_id):
            return dashboard_redirect(user_error="Selected employee was not found.")
        linked_user_id = db.scalar(select(User.id).where(User.employee_id == selected_employee_id, User.id != account.id))
        if linked_user_id:
            return dashboard_redirect(user_error="Selected employee already has an account.")
    else:
        selected_employee_id = None

    account.username = username
    account.full_name = full_name
    account.company_name = company_name
    account.avatar_url = avatar_url
    account.role = role
    account.employee_id = selected_employee_id
    account.is_active = active
    if password:
        account.password_hash = password_hash.hash(password)

    db.commit()
    return dashboard_redirect(user_updated=1)


@app.get("/shipments/{shipment_id}", response_class=HTMLResponse)
def shipment_page(
    shipment_id: int,
    request: Request,
    refreshed: Optional[int] = None,
    refresh_error: Optional[str] = None,
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
        context=localized_context(
            request,
            shipment=shipment,
            events=sorted(shipment.events, key=lambda item: item.event_time, reverse=True),
            current_user=user,
            mock_mode=RAJAONGKIR_MOCK,
            refreshed=bool(refreshed),
            refresh_error=refresh_error,
        ),
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
    try:
        await apply_tracking_refresh(shipment, db)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, default=str)
        query = urlencode({"refresh_error": detail})
        return RedirectResponse(url=f"/shipments/{shipment_id}?{query}", status_code=303)
    return RedirectResponse(url=f"/shipments/{shipment_id}?refreshed=1", status_code=303)
