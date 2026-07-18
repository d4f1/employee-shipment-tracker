import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx
import jwt
from jinja2 import pass_context
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.templating import Jinja2Templates
from jwt import InvalidTokenError
from pwdlib import PasswordHash
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
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
NOMINATIM_SEARCH_URL = os.getenv(
    "NOMINATIM_SEARCH_URL", "https://nominatim.openstreetmap.org/search"
)
GEOCODER_COUNTRYCODES = os.getenv("GEOCODER_COUNTRYCODES", "id").strip()
GEOCODER_USER_AGENT = os.getenv(
    "GEOCODER_USER_AGENT", "employee-shipment-tracker-demo/1.0"
)
SHIPMENTS_PER_PAGE = 10

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
    document_type: Mapped[str] = mapped_column(String(100), default="Item")
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    sender_tags: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    courier: Mapped[str] = mapped_column(String(40))
    awb: Mapped[str] = mapped_column(String(100), index=True)
    external_awb: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    po_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
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
    created_by: Mapped[Optional[User]] = relationship()
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


class MonthlyBudget(Base):
    __tablename__ = "monthly_budgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    month: Mapped[str] = mapped_column(String(7), unique=True, index=True)
    amount: Mapped[float] = mapped_column(Float, default=0)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class PurchaseItem(Base):
    __tablename__ = "purchase_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    month: Mapped[str] = mapped_column(String(7), index=True)
    item_name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(100), index=True)
    amount: Mapped[float] = mapped_column(Float, default=0)
    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CompanyLocation(Base):
    __tablename__ = "company_locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_name: Mapped[str] = mapped_column(String(200), index=True)
    branch_name: Mapped[str] = mapped_column(String(200), index=True)
    address: Mapped[str] = mapped_column(String(500))
    road: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    state: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    postcode: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)


def ensure_optional_columns():
    shipment_columns = {column["name"] for column in inspect(engine).get_columns("shipments")}
    if "external_awb" not in shipment_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE shipments ADD COLUMN external_awb VARCHAR(100)"))
    if "po_number" not in shipment_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE shipments ADD COLUMN po_number VARCHAR(100)"))
    if "created_by_id" not in shipment_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE shipments ADD COLUMN created_by_id INTEGER"))
    if "sender_tags" not in shipment_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE shipments ADD COLUMN sender_tags VARCHAR(255)"))
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
        "GA Dashboard": "Dasbor GA",
        "Monitor company items and packages from dispatch until employee receipt.": "Pantau item dan paket perusahaan dari pengiriman sampai diterima karyawan.",
        "Create shipment": "Buat pengiriman",
        "Total shipments": "Total pengiriman",
        "Dashboard menu": "Menu dashboard",
        "Shipping": "Pengiriman",
        "Admin": "Admin",
        "Operators": "Operator",
        "Locations": "Lokasi",
        "Company locations": "Lokasi perusahaan",
        "Register company and branch addresses for manual shipment entry.": "Daftarkan alamat perusahaan dan cabang untuk input pengiriman manual.",
        "Add branch location": "Tambah lokasi cabang",
        "Branch name": "Nama cabang",
        "Full address": "Alamat lengkap",
        "Road": "Jalan",
        "City": "Kota",
        "Province": "Provinsi",
        "Postal code": "Kode pos",
        "Country": "Negara",
        "Save location": "Simpan lokasi",
        "Validate address": "Validasi alamat",
        "Address validation": "Validasi alamat",
        "Use this address": "Gunakan alamat ini",
        "Suggested address": "Alamat yang disarankan",
        "Preview this address in maps and apply the suggested details.": "Pratinjau alamat di peta dan gunakan detail yang disarankan.",
        "Open in Google Maps": "Buka di Google Maps",
        "Choose a suggested address or open Google Maps before saving.": "Pilih alamat yang disarankan atau buka Google Maps sebelum menyimpan.",
        "Choose a suggested address or validate it in the map preview before saving.": "Pilih alamat yang disarankan atau validasi di pratinjau peta sebelum menyimpan.",
        "Location saved successfully.": "Lokasi berhasil disimpan.",
        "No company locations yet.": "Belum ada lokasi perusahaan.",
        "Saved locations": "Lokasi tersimpan",
        "Active operators/vendors": "Operator/vendor aktif",
        "Monitor active senders and item totals.": "Pantau pengirim aktif dan total item.",
        "Total active operators": "Total operator aktif",
        "Items sent": "Item terkirim",
        "Delivered items": "Item terkirim selesai",
        "In-progress items": "Item dalam proses",
        "No active operators yet.": "Belum ada operator aktif.",
        "Last sent": "Terakhir kirim",
        "Never sent": "Belum pernah kirim",
        "Vendor sender": "Pengirim vendor",
        "Sender analytics": "Analitik pengirim",
        "In progress": "Dalam proses",
        "Delivered": "Terkirim",
        "Shipping cost": "Biaya kirim",
        "Recent shipments": "Pengiriman terbaru",
        "Sort by date": "Urutkan tanggal",
        "Newest first": "Terbaru dulu",
        "Oldest first": "Terlama dulu",
        "Click a reference number to view its complete delivery timeline.": "Klik nomor referensi untuk melihat linimasa pengiriman lengkap.",
        "Search reference, employee, AWB, ticket, PO number, or hashtag": "Cari referensi, karyawan, AWB, tiket, nomor PO, atau hashtag",
        "Reference": "Referensi",
        "Item": "Item",
        "Employee": "Karyawan",
        "Courier": "Kurir",
        "Ticket number": "Nomor tiket",
        "PO number": "Nomor PO",
        "Route": "Rute",
        "Cost": "Biaya",
        "Status": "Status",
        "Last location": "Lokasi terakhir",
        "No shipments found": "Tidak ada pengiriman",
        "New shipments will appear here.": "Pengiriman baru akan muncul di sini.",
        "No matching shipments": "Tidak ada pengiriman yang cocok",
        "Try another reference, employee name, courier, AWB, ticket, or PO number.": "Coba referensi, nama karyawan, kurir, AWB, tiket, atau nomor PO lain.",
        "Showing": "Menampilkan",
        "to": "sampai",
        "of": "dari",
        "rows": "baris",
        "Previous": "Sebelumnya",
        "Next": "Berikutnya",
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
        "Sender": "Pengirim",
        "Sender source": "Sumber pengirim",
        "Sender hashtags": "Hashtag pengirim",
        "Add optional tags separated by spaces.": "Tambahkan tag opsional dipisahkan spasi.",
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
        "Monthly budget": "Budget bulanan",
        "Track buying needs by category and remaining budget.": "Pantau kebutuhan pembelian per kategori dan sisa budget.",
        "Budget month": "Bulan budget",
        "Set budget": "Atur budget",
        "Admin monthly budget": "Budget bulanan admin",
        "Spent": "Terpakai",
        "Remaining": "Sisa",
        "Add purchase item": "Tambah item pembelian",
        "Item name": "Nama item",
        "Category": "Kategori",
        "Amount": "Jumlah",
        "Note": "Catatan",
        "Optional note": "Catatan opsional",
        "Save item": "Simpan item",
        "Category breakdown": "Rincian kategori",
        "Recent items": "Item terbaru",
        "No purchase items yet.": "Belum ada item pembelian.",
        "Budget saved successfully.": "Budget berhasil disimpan.",
        "Purchase item added successfully.": "Item pembelian berhasil ditambahkan.",
        "Budget analytics": "Analitik budget",
        "Largest category usage, item count, timeline, and spending curve.": "Penggunaan kategori terbesar, jumlah item, timeline, dan kurva pengeluaran.",
        "Budget usage": "Penggunaan budget",
        "Category usage": "Penggunaan kategori",
        "Items by category": "Item per kategori",
        "Daily spending timeline": "Timeline pengeluaran harian",
        "Cumulative spending curve": "Kurva pengeluaran kumulatif",
        "No chart data yet.": "Belum ada data grafik.",
        "New delivery": "Pengiriman baru",
        "Create a shipment": "Buat pengiriman",
        "Register the item, recipient, courier, cost, and expected delivery time.": "Daftarkan item, penerima, kurir, biaya, dan estimasi waktu pengiriman.",
        "Reference number": "Nomor referensi",
        "Auto generated after save": "Dibuat otomatis setelah disimpan",
        "Generated from shipment ID": "Dibuat dari ID pengiriman",
        "Item name": "Nama item",
        "Item type": "Jenis item",
        "Employee recipient": "Penerima karyawan",
        "AWB / waybill": "AWB / waybill",
        "Lookup": "Cari",
        "Lookup AWB to fill origin and destination.": "Cari AWB untuk mengisi origin dan destination.",
        "Lookup AWB to fill origin, destination, ETA, and shipping cost.": "Cari AWB untuk mengisi origin, destination, ETA, dan biaya kirim.",
        "Ticket number": "Nomor tiket",
        "Origin": "Origin",
        "Uses your detected current location when available.": "Menggunakan lokasi Anda saat ini jika tersedia.",
        "Destination": "Destination",
        "ETA days": "Estimasi hari",
        "Filled from AWB provider lookup.": "Diisi dari lookup provider AWB.",
        "Cancel": "Batal",
        "Creating...": "Membuat...",
        "Package map": "Peta paket",
        "Shipment location": "Lokasi pengiriman",
        "Current package location details.": "Detail lokasi paket saat ini.",
        "Ticket number": "Nomor tiket",
        "PO number": "Nomor PO",
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
        "Updates first": "Update dulu",
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


def request_prefers_json(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    requested_with = request.headers.get("x-requested-with", "")
    return "application/json" in accept or requested_with == "fetch"


def normalize_budget_month(value: Optional[str]) -> str:
    if value:
        try:
            return datetime.strptime(value, "%Y-%m").strftime("%Y-%m")
        except ValueError:
            pass
    return datetime.utcnow().strftime("%Y-%m")


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


def search_geocode(query: str, limit: int = 6) -> list[dict]:
    normalized_query = query.strip()
    if len(normalized_query) < 3:
        return []
    try:
        params = {
            "format": "jsonv2",
            "q": normalized_query,
            "addressdetails": 1,
            "limit": max(1, min(limit, 8)),
            "accept-language": "id,en",
        }
        if GEOCODER_COUNTRYCODES:
            params["countrycodes"] = GEOCODER_COUNTRYCODES
        response = httpx.get(
            NOMINATIM_SEARCH_URL,
            params=params,
            headers={"User-Agent": GEOCODER_USER_AGENT},
            timeout=4,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return []

    if not isinstance(payload, list):
        return []

    suggestions = []
    seen = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        address = item.get("address") if isinstance(item.get("address"), dict) else {}
        display_name = item.get("display_name")
        label = display_name or format_location_address(address)
        if not label:
            continue
        normalized_label = label.lower()
        if normalized_label in seen:
            continue
        seen.add(normalized_label)
        road = address.get("road") or address.get("pedestrian") or address.get("footway")
        area = address.get("neighbourhood") or address.get("suburb") or address.get("village")
        city = address.get("city") or address.get("town") or address.get("municipality") or address.get("county")
        state = address.get("state")
        postcode = address.get("postcode")
        suggestions.append({
            "label": label,
            "primary": road or area or city or label.split(",")[0],
            "secondary": format_provider_location(area, city, state, postcode, address.get("country")),
            "postcode": postcode,
            "road": road,
            "city": city,
            "state": state,
            "country": address.get("country"),
            "latitude": parse_optional_float(item.get("lat")),
            "longitude": parse_optional_float(item.get("lon")),
        })
    return suggestions


def search_saved_locations(db: Session, query: str, limit: int = 6) -> list[dict]:
    normalized_query = query.strip().lower()
    if len(normalized_query) < 3:
        return []

    suggestions = []
    seen = set()
    branch_locations = db.scalars(
        select(CompanyLocation)
        .where(CompanyLocation.is_active.is_(True))
        .order_by(CompanyLocation.company_name, CompanyLocation.branch_name)
    ).all()
    for location in branch_locations:
        label = format_provider_location(
            location.address,
            location.city,
            location.state,
            location.postcode,
            location.country,
        )
        haystack = " ".join(
            part for part in [
                location.company_name,
                location.branch_name,
                label,
                location.road,
                location.city,
                location.postcode,
            ] if part
        ).lower()
        label_key = (label or "").lower()
        if normalized_query not in haystack or not label or label_key in seen:
            continue
        seen.add(label_key)
        suggestions.append({
            "label": label,
            "primary": f"{location.branch_name} - {location.company_name}",
            "secondary": label,
            "postcode": location.postcode,
            "road": location.road,
            "city": location.city,
            "state": location.state,
            "country": location.country,
            "latitude": None,
            "longitude": None,
            "source": "company_location",
        })
        if len(suggestions) >= limit:
            return suggestions

    values = []
    shipments = db.scalars(select(Shipment).order_by(Shipment.created_at.desc())).all()
    for shipment in shipments:
        values.extend([shipment.origin, shipment.destination])

    ranked = []
    for value in values:
        clean_value = (value or "").strip()
        if not clean_value:
            continue
        lower_value = clean_value.lower()
        if normalized_query not in lower_value or lower_value in seen:
            continue
        seen.add(lower_value)
        rank = 0 if lower_value.startswith(normalized_query) else 1
        ranked.append((rank, clean_value))

    ranked.sort(key=lambda item: (item[0], item[1].lower()))
    for _, value in ranked[:limit]:
        if len(suggestions) >= limit:
            break
        parts = [part.strip() for part in value.split(",") if part.strip()]
        suggestions.append({
            "label": value,
            "primary": parts[0] if parts else value,
            "secondary": ", ".join(parts[1:]) if len(parts) > 1 else "Saved shipment address",
            "postcode": None,
            "road": None,
            "city": None,
            "state": None,
            "country": None,
            "latitude": None,
            "longitude": None,
            "source": "saved",
        })
    return suggestions


def is_postal_code_only(value: Optional[str]) -> bool:
    return bool(value and re.fullmatch(r"\d{4,6}", value.strip()))


def format_location_suggestion(suggestion: dict) -> Optional[str]:
    primary = suggestion.get("primary")
    secondary = suggestion.get("secondary")
    label = suggestion.get("label")
    return format_provider_location(primary, secondary) or label


def enrich_postal_code_location(value: Optional[str]) -> Optional[str]:
    if not is_postal_code_only(value):
        return value
    suggestions = search_geocode(value.strip(), limit=1)
    if not suggestions:
        return value
    enriched_location = format_location_suggestion(suggestions[0])
    return enriched_location or value


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


def parse_number_from_value(value: Optional[str]) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw_value = str(value).strip()
    if not raw_value:
        return None
    digits_only = re.sub(r"\D", "", raw_value)
    if not digits_only:
        return None
    separator_match = re.search(r"[.,](\d{3})$", raw_value)
    if (
        re.search(r"[A-Za-z$Rp]", raw_value)
        or raw_value.count(".") + raw_value.count(",") > 1
        or separator_match
    ):
        return float(digits_only)
    cleaned = "".join(char for char in raw_value if char.isdigit() or char in ".-")
    try:
        return float(cleaned)
    except ValueError:
        return float(digits_only)


def parse_integer_from_value(value: Optional[str]) -> Optional[int]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else None


def normalize_package_weight(value: Optional[str]) -> int:
    parsed_value = parse_number_from_value(value)
    if parsed_value is None or parsed_value <= 0:
        return 1000
    if parsed_value < 30:
        return max(int(parsed_value * 1000), 1)
    return max(int(parsed_value), 1)


def estimate_days_from_events(events: list[dict]) -> Optional[int]:
    event_times = []
    for event in events:
        event_time = event.get("event_time")
        if isinstance(event_time, datetime):
            event_times.append(event_time)
    if len(event_times) < 2:
        return None
    elapsed = max(event_times) - min(event_times)
    days = elapsed.days
    if elapsed.seconds or elapsed.microseconds:
        days += 1
    return max(days, 0)


def latest_meaningful_location(events: list[dict]) -> Optional[str]:
    meaningful_events = [
        event for event in events
        if event.get("location") and event.get("location") != "-"
    ]
    if not meaningful_events:
        return None
    return max(meaningful_events, key=lambda event: event.get("event_time") or datetime.min)["location"]


def format_provider_location(*parts: Optional[str]) -> Optional[str]:
    seen = set()
    formatted_parts = []
    for part in parts:
        if not part:
            continue
        clean_part = str(part).strip()
        if not clean_part or clean_part == "-":
            continue
        key = clean_part.lower()
        if key in seen:
            continue
        seen.add(key)
        formatted_parts.append(clean_part)
    return ", ".join(formatted_parts) or None


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
    explicit_origin_postcode = find_first_value(raw, (
        "origin_postal_code",
        "origin_postcode",
        "origin_zip_code",
        "shipper_postal_code",
        "sender_postal_code",
        "postal_code_origin",
        "zip_origin",
    ))
    explicit_origin_area = find_first_value(raw, (
        "origin_area",
        "origin_district",
        "origin_subdistrict",
        "shipper_area",
        "sender_area",
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
    explicit_destination_postcode = find_first_value(raw, (
        "destination_postal_code",
        "destination_postcode",
        "destination_zip_code",
        "receiver_postal_code",
        "recipient_postal_code",
        "consignee_postal_code",
        "postal_code_destination",
        "zip_destination",
    ))
    explicit_destination_area = find_first_value(raw, (
        "destination_area",
        "destination_district",
        "destination_subdistrict",
        "receiver_area",
        "recipient_area",
        "consignee_area",
    ))
    shipping_cost = parse_number_from_value(find_first_value(raw, (
        "shipping_cost",
        "shipment_cost",
        "cost",
        "ongkir",
        "price",
        "tariff",
        "fee",
        "total_cost",
        "shipping_fee",
    )))
    eta_days = parse_integer_from_value(find_first_value(raw, (
        "eta_days",
        "etd",
        "estimate_day",
        "estimated_day",
        "estimated_days",
        "duration",
        "sla",
        "lead_time",
    )))
    if eta_days is None:
        eta_days = estimate_days_from_events(events)
    timeline_locations = [
        event.get("location")
        for event in events
        if event.get("location") and event.get("location") != "-"
    ]
    origin = (
        format_provider_location(explicit_origin, explicit_origin_area, explicit_origin_postcode)
        or (timeline_locations[0] if timeline_locations else None)
    )
    destination = (
        format_provider_location(explicit_destination, explicit_destination_area, explicit_destination_postcode)
        or (timeline_locations[-1] if timeline_locations else None)
    )
    origin = enrich_postal_code_location(origin)
    destination = enrich_postal_code_location(destination)
    last_location = enrich_postal_code_location(
        latest_meaningful_location(events) or result.get("last_location") or destination or "-"
    )
    return {
        "origin": origin,
        "destination": destination,
        "shipping_cost": shipping_cost,
        "eta_days": eta_days,
        "origin_postal_code": explicit_origin_postcode,
        "destination_postal_code": explicit_destination_postcode,
        "last_location": last_location,
        "status": result.get("status") or "UNKNOWN",
        "events": events,
        "source": "courier_payload" if explicit_origin or explicit_destination or shipping_cost is not None or eta_days is not None else "tracking_timeline",
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


def sort_tracking_events_for_timeline(events: list[TrackingEvent]) -> list[TrackingEvent]:
    return sorted(
        events,
        key=lambda event: (
            1 if event.status.upper() == "CREATED" else 0,
            -event.event_time.timestamp(),
        ),
    )


def base_sender_tag_for_role(role: str) -> str:
    return "#HO" if role == "admin" else "#Vendor"


def normalize_sender_tags(value: Optional[str], role: str) -> str:
    base_tag = base_sender_tag_for_role(role)
    tags = [base_tag]
    reserved_tags = {"#ho", "#vendor"}
    for raw_tag in re.split(r"[\s,]+", value or ""):
        clean_tag = raw_tag.strip()
        if not clean_tag:
            continue
        clean_tag = clean_tag if clean_tag.startswith("#") else f"#{clean_tag}"
        clean_tag = re.sub(r"[^A-Za-z0-9_#-]", "", clean_tag)
        if clean_tag.lower() in reserved_tags and clean_tag.lower() != base_tag.lower():
            continue
        if len(clean_tag) > 1 and clean_tag.lower() not in {tag.lower() for tag in tags}:
            tags.append(clean_tag)
    return " ".join(tags[:8])


def display_sender_tags(shipment: Shipment) -> str:
    if shipment.sender_tags:
        return shipment.sender_tags
    if shipment.created_by:
        return normalize_sender_tags(None, shipment.created_by.role)
    return ""


def ensure_employee_for_user_account(account: User, db: Session) -> Employee:
    if account.employee:
        return account.employee
    employee = Employee(
        employee_code=f"USR-{account.id:04d}",
        name=account.full_name,
        email=f"user-{account.id}@employee-shipment.local",
        company_name=account.company_name,
        department=account.role.title(),
    )
    db.add(employee)
    db.flush()
    account.employee_id = employee.id
    return employee


def resolve_recipient_employee_id(
    db: Session,
    *,
    recipient_user_id: Optional[int],
    employee_id: Optional[int],
) -> int:
    if recipient_user_id:
        account = db.get(User, recipient_user_id)
        if not account or not account.is_active:
            raise HTTPException(status_code=404, detail="Recipient account not found")
        return ensure_employee_for_user_account(account, db).id
    if employee_id and db.get(Employee, employee_id):
        return employee_id
    raise HTTPException(status_code=404, detail="Employee not found")


def build_reference_number(shipment_id: int) -> str:
    return f"DOC-{datetime.utcnow().year}-{shipment_id:04d}"


def assign_unique_reference_number(shipment: Shipment, db: Session) -> None:
    base_reference = build_reference_number(shipment.id)
    reference_no = base_reference
    suffix = 2
    while db.scalar(
        select(Shipment.id).where(
            Shipment.reference_no == reference_no,
            Shipment.id != shipment.id,
        )
    ):
        reference_no = f"{base_reference}-{suffix}"
        suffix += 1
    shipment.reference_no = reference_no


def create_shipment_record(
    db: Session,
    *,
    title: str,
    document_type: str,
    employee_id: int,
    created_by_id: int,
    courier: str,
    awb: str,
    external_awb: Optional[str],
    po_number: Optional[str],
    sender_tags: Optional[str],
    origin: str,
    destination: str,
    shipping_cost: float,
    eta_days: int,
    last_location: Optional[str] = None,
) -> Shipment:
    if not db.get(Employee, employee_id):
        raise HTTPException(status_code=404, detail="Employee not found")
    shipment = Shipment(
        reference_no=f"PENDING-{uuid.uuid4().hex}",
        title=title,
        document_type=document_type,
        employee_id=employee_id,
        created_by_id=created_by_id,
        sender_tags=sender_tags,
        courier=courier.lower(),
        awb=awb,
        external_awb=external_awb or None,
        po_number=po_number or None,
        origin=origin,
        destination=destination,
        shipping_cost=shipping_cost,
        eta_days=eta_days,
        last_location=last_location or "-",
        expected_arrival=datetime.utcnow() + timedelta(days=eta_days) if eta_days else None,
    )
    db.add(shipment)
    db.flush()
    assign_unique_reference_number(shipment, db)
    db.add(TrackingEvent(shipment_id=shipment.id, status="CREATED", description="Shipment record created", location=origin))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Reference number already exists. Please try again.") from exc
    return shipment


async def resolve_awb_shipment_fields(
    courier: str,
    awb: str,
    origin: str,
    destination: str,
    fallback_shipping_cost: float = 0,
    fallback_eta_days: int = 0,
) -> dict:
    result = await provider.track(awb.strip(), courier.strip().lower())
    route = extract_awb_route(result)
    route = await enrich_awb_route_cost(
        route,
        result,
        courier=courier,
        fallback_origin=origin,
        fallback_destination=destination,
    )
    return {
        "origin": route.get("origin") or origin,
        "destination": route.get("destination") or destination,
        "shipping_cost": route.get("shipping_cost") if route.get("shipping_cost") is not None else fallback_shipping_cost,
        "eta_days": route.get("eta_days") if route.get("eta_days") is not None else fallback_eta_days,
        "last_location": route.get("last_location") or "-",
    }


class RajaOngkirProvider:
    def _normalize_manifest_items(self, value) -> list[dict]:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            for nested_value in value.values():
                if isinstance(nested_value, list):
                    return [item for item in nested_value if isinstance(item, dict)]
            return [value]
        return []

    def _normalize_payload_items(self, payload: dict) -> list[dict]:
        data = payload.get("data") or payload.get("rajaongkir") or payload.get("results") or payload
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("data", "results", "costs"):
                nested_value = data.get(key)
                if isinstance(nested_value, list):
                    return [item for item in nested_value if isinstance(item, dict)]
            return [data]
        return []

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        data: Optional[dict] = None,
    ) -> Optional[dict]:
        if not RAJAONGKIR_API_KEY:
            return None
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.request(
                    method,
                    f"{RAJAONGKIR_BASE_URL}{path}",
                    params=params,
                    data=data,
                    headers={"key": RAJAONGKIR_API_KEY},
                )
            if response.status_code >= 400:
                return None
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    async def find_domestic_destination_id(self, query: str, postcode: Optional[str] = None) -> Optional[str]:
        payload = await self._request_json(
            "GET",
            "/destination/domestic-destination",
            params={"search": query, "limit": 10, "offset": 0},
        )
        if not payload:
            return None
        items = self._normalize_payload_items(payload)
        if postcode:
            for item in items:
                item_postcode = str(item.get("zip_code") or item.get("postal_code") or item.get("postcode") or "")
                if item_postcode == postcode:
                    return str(item.get("id") or item.get("destination_id") or "")
        for item in items:
            destination_id = item.get("id") or item.get("destination_id")
            if destination_id:
                return str(destination_id)
        return None

    async def calculate_domestic_shipping_cost(
        self,
        *,
        courier: str,
        origin_postcode: Optional[str],
        destination_postcode: Optional[str],
        origin_label: Optional[str],
        destination_label: Optional[str],
        weight: int,
    ) -> dict:
        origin_query = origin_postcode or origin_label or ""
        destination_query = destination_postcode or destination_label or ""
        origin_id = await self.find_domestic_destination_id(origin_query, origin_postcode)
        destination_id = await self.find_domestic_destination_id(destination_query, destination_postcode)
        if not origin_id or not destination_id:
            return {}

        payload = await self._request_json(
            "POST",
            "/calculate/domestic-cost",
            data={
                "origin": origin_id,
                "destination": destination_id,
                "weight": weight,
                "courier": courier,
                "price": "lowest",
            },
        )
        if not payload:
            return {}
        cost_items = self._normalize_payload_items(payload)
        costs = [
            parse_number_from_value(find_first_value(item, ("shipping_cost", "cost", "value", "price", "tariff")))
            for item in cost_items
        ]
        valid_costs = [cost for cost in costs if cost is not None and cost > 0]
        eta_days = parse_integer_from_value(find_first_value(payload, (
            "etd",
            "eta_days",
            "estimate_day",
            "estimated_day",
            "estimated_days",
            "duration",
        )))
        return {
            "shipping_cost": min(valid_costs) if valid_costs else None,
            "eta_days": eta_days,
        }

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
                "raw": {
                    "mock": True,
                    "awb": awb,
                    "courier": courier,
                    "origin_city": "Jakarta",
                    "origin_area": "Distribution Center",
                    "origin_postal_code": "10110",
                    "destination_city": "Yogyakarta",
                    "destination_area": "Umbulharjo",
                    "destination_postal_code": "55161",
                    "shipping_cost": 28000,
                    "eta_days": 2,
                },
            }

        if not RAJAONGKIR_API_KEY:
            raise HTTPException(status_code=503, detail="RAJAONGKIR_API_KEY is not configured")

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{RAJAONGKIR_BASE_URL}/track/waybill",
                    params={"awb": awb, "courier": courier},
                    headers={"key": RAJAONGKIR_API_KEY},
                )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"RajaOngkir request failed: {exc}",
            ) from exc

        if response.status_code >= 400:
            raise HTTPException(
                status_code=404 if response.status_code == 404 else 502,
                detail=f"RajaOngkir returned HTTP {response.status_code}: {response.text[:300]}",
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"RajaOngkir returned invalid JSON: {response.text[:300]}",
            ) from exc

        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=502,
                detail="RajaOngkir returned an unexpected response format.",
            )

        meta = payload.get("meta")
        if isinstance(meta, dict) and meta.get("status") == "error":
            raise HTTPException(
                status_code=404 if meta.get("code") == 404 else 502,
                detail=json.dumps(payload),
            )

        data = payload.get("data") or payload.get("rajaongkir") or payload
        if isinstance(data, list):
            data = next((item for item in data if isinstance(item, dict)), {})
        if not isinstance(data, dict):
            raise HTTPException(
                status_code=502,
                detail="RajaOngkir returned an unexpected data format.",
            )

        summary = data.get("summary") or data.get("delivery_status") or {}
        if not isinstance(summary, dict):
            summary = {"status": summary}
        manifests = self._normalize_manifest_items(
            data.get("manifest") or data.get("history") or data.get("tracking") or data.get("details")
        )
        current_status = summary.get("status") or data.get("status") or "UNKNOWN"
        if isinstance(current_status, dict):
            current_status = current_status.get("status") or current_status.get("pod_status") or "UNKNOWN"

        events = []
        for item in manifests:
            event_time = datetime.utcnow()
            time_value = item.get("manifest_date") or item.get("date") or item.get("event_time")
            clock_value = item.get("manifest_time") or item.get("time")
            if time_value:
                try:
                    parsed_time_value = str(time_value).replace("Z", "+00:00")
                    if clock_value and "T" not in parsed_time_value and " " not in parsed_time_value:
                        parsed_time_value = f"{parsed_time_value}T{clock_value}"
                    event_time = datetime.fromisoformat(parsed_time_value)
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


async def enrich_awb_route_cost(
    route: dict,
    result: dict,
    *,
    courier: str,
    fallback_origin: Optional[str] = None,
    fallback_destination: Optional[str] = None,
) -> dict:
    if route.get("shipping_cost") is not None:
        return route
    calculated_cost = await provider.calculate_domestic_shipping_cost(
        courier=courier.strip().lower(),
        origin_postcode=route.get("origin_postal_code"),
        destination_postcode=route.get("destination_postal_code"),
        origin_label=route.get("origin") or fallback_origin,
        destination_label=route.get("destination") or fallback_destination,
        weight=normalize_package_weight(find_first_value(result.get("raw") or {}, ("weight", "shipment_weight", "actual_weight"))),
    )
    if calculated_cost.get("shipping_cost") is not None:
        route["shipping_cost"] = calculated_cost["shipping_cost"]
    if route.get("eta_days") is None and calculated_cost.get("eta_days") is not None:
        route["eta_days"] = calculated_cost["eta_days"]
    return route


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
            db.flush()

        if not db.scalar(select(Shipment.id).limit(1)):
            sender = db.scalar(select(User).where(User.username == "operator"))
            shipment = Shipment(
                reference_no="DOC-2026-0001",
                title="Employment Contract",
                document_type="Contract",
                employee_id=employees[0].id,
                created_by_id=sender.id if sender else None,
                sender_tags=normalize_sender_tags(None, sender.role if sender else "operator"),
                courier="jne",
                awb="MOCK123456789",
                external_awb="TCK-MOCK-2026-0001",
                po_number="PO-MOCK-2026-0001",
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


@app.get("/api/location/search")
def search_location_for_form(
    request: Request,
    query: str = Query(..., min_length=3),
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Login is required")
    require_roles(user, "admin", "operator")
    saved_suggestions = search_saved_locations(db, query)
    remaining_limit = max(0, 6 - len(saved_suggestions))
    provider_suggestions = search_geocode(query, remaining_limit) if remaining_limit else []
    seen = {item["label"].strip().lower() for item in saved_suggestions}
    suggestions = saved_suggestions[:]
    for item in provider_suggestions:
        label_key = item["label"].strip().lower()
        if label_key in seen:
            continue
        seen.add(label_key)
        suggestions.append(item)
    return {
        "query": query.strip(),
        "suggestions": suggestions,
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
    route = await enrich_awb_route_cost(route, result, courier=courier)
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
        "sender": s.created_by.full_name if s.created_by else None,
        "sender_role": s.created_by.role if s.created_by else None,
        "sender_company": s.created_by.company_name if s.created_by else None,
        "sender_tags": display_sender_tags(s),
        "courier": s.courier,
        "awb": s.awb,
        "ticket_number": s.external_awb,
        "po_number": s.po_number,
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
        "sender": shipment.created_by.full_name if shipment.created_by else None,
        "sender_role": shipment.created_by.role if shipment.created_by else None,
        "sender_company": shipment.created_by.company_name if shipment.created_by else None,
        "sender_tags": display_sender_tags(shipment),
        "courier": shipment.courier,
        "awb": shipment.awb,
        "ticket_number": shipment.external_awb,
        "po_number": shipment.po_number,
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
        } for e in sort_tracking_events_for_timeline(shipment.events)],
    }


@app.post("/api/shipments")
async def create_shipment(
    title: str = Form(...),
    document_type: str = Form("Item"),
    employee_id: Optional[int] = Form(None),
    recipient_user_id: Optional[int] = Form(None),
    courier: str = Form(...),
    awb: str = Form(...),
    external_awb: Optional[str] = Form(None),
    po_number: Optional[str] = Form(None),
    sender_tags: Optional[str] = Form(None),
    origin: str = Form(...),
    destination: str = Form(...),
    shipping_cost: float = Form(0),
    eta_days: int = Form(0),
    current_user: User = Depends(get_current_api_user),
    db: Session = Depends(get_db),
):
    require_roles(current_user, "admin", "operator")
    recipient_employee_id = resolve_recipient_employee_id(
        db,
        recipient_user_id=recipient_user_id,
        employee_id=employee_id,
    )
    provider_fields = await resolve_awb_shipment_fields(courier, awb, origin, destination, shipping_cost, eta_days)
    shipment = create_shipment_record(
        db,
        title=title,
        document_type=document_type,
        employee_id=recipient_employee_id,
        created_by_id=current_user.id,
        courier=courier,
        awb=awb,
        external_awb=external_awb,
        po_number=po_number,
        sender_tags=normalize_sender_tags(sender_tags, current_user.role),
        origin=provider_fields["origin"],
        destination=provider_fields["destination"],
        shipping_cost=provider_fields["shipping_cost"],
        eta_days=provider_fields["eta_days"],
        last_location=provider_fields["last_location"],
    )
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
    route = extract_awb_route(result)
    route = await enrich_awb_route_cost(
        route,
        result,
        courier=shipment.courier,
        fallback_origin=shipment.origin,
        fallback_destination=shipment.destination,
    )
    shipment.status = result["status"]
    shipment.last_location = route["last_location"]
    if route.get("shipping_cost") is not None:
        shipment.shipping_cost = route["shipping_cost"]
    if route.get("eta_days") is not None:
        shipment.eta_days = route["eta_days"]
        shipment.expected_arrival = shipment.created_at + timedelta(days=route["eta_days"]) if route["eta_days"] else None
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
    budget_updated: Optional[int] = None,
    budget_item_created: Optional[int] = None,
    location_created: Optional[int] = None,
    budget_error: Optional[str] = None,
    budget_month: Optional[str] = None,
    sort: str = Query("date_desc"),
    shipping_page: int = Query(1),
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    employees = db.scalars(select(Employee).order_by(Employee.name)).all() if user.role in {"admin", "operator"} else []
    recipient_accounts = db.scalars(
        select(User).where(User.is_active.is_(True)).order_by(User.full_name)
    ).all() if user.role in {"admin", "operator"} else []
    users = db.scalars(select(User).order_by(User.created_at.desc())).all() if user.role == "admin" else []
    assigned_employee_ids = {account.employee_id for account in users if account.employee_id}
    sort = sort if sort in {"date_asc", "date_desc"} else "date_desc"
    sort_expression = Shipment.created_at.asc() if sort == "date_asc" else Shipment.created_at.desc()
    stmt = select(Shipment).order_by(sort_expression)
    if user.role == "employee":
        stmt = stmt.where(Shipment.employee_id == user.employee_id)
    all_shipments = db.scalars(stmt).all()
    total_shipments = len(all_shipments)
    shipping_total_pages = max((total_shipments + SHIPMENTS_PER_PAGE - 1) // SHIPMENTS_PER_PAGE, 1)
    shipping_page = min(max(shipping_page, 1), shipping_total_pages)
    shipping_page_start = (shipping_page - 1) * SHIPMENTS_PER_PAGE
    shipments = all_shipments[shipping_page_start:shipping_page_start + SHIPMENTS_PER_PAGE]
    counts = {
        "total": total_shipments,
        "in_progress": sum(s.status not in {"DELIVERED", "RECEIVED"} for s in all_shipments),
        "delivered": sum(s.status in {"DELIVERED", "RECEIVED"} for s in all_shipments),
        "cost": sum(s.shipping_cost for s in all_shipments),
    }
    operator_analytics = []
    operator_counts = {
        "active": 0,
        "items": 0,
        "delivered": 0,
        "in_progress": 0,
    }
    company_locations = []
    budget_month = normalize_budget_month(budget_month)
    monthly_budget = None
    purchase_items = []
    budget_amount = 0.0
    budget_spent = 0.0
    budget_remaining = 0.0
    budget_category_totals = []
    budget_chart_data = {
        "categoryLabels": [],
        "categoryAmounts": [],
        "categoryCounts": [],
        "timelineLabels": [],
        "timelineAmounts": [],
        "cumulativeAmounts": [],
        "usageLabels": ["Spent", "Remaining"],
        "usageAmounts": [0, 0],
    }
    if user.role == "admin":
        company_locations = db.scalars(
            select(CompanyLocation).order_by(CompanyLocation.company_name, CompanyLocation.branch_name)
        ).all()
        active_operators = [
            account for account in users
            if account.role == "operator" and account.is_active
        ]
        for account in active_operators:
            sent_shipments = [shipment for shipment in all_shipments if shipment.created_by_id == account.id]
            delivered_count = sum(shipment.status in {"DELIVERED", "RECEIVED"} for shipment in sent_shipments)
            in_progress_count = len(sent_shipments) - delivered_count
            last_sent = max((shipment.created_at for shipment in sent_shipments), default=None)
            operator_analytics.append({
                "account": account,
                "items_sent": len(sent_shipments),
                "delivered": delivered_count,
                "in_progress": in_progress_count,
                "shipping_cost": sum(shipment.shipping_cost for shipment in sent_shipments),
                "last_sent": last_sent,
            })
        operator_analytics.sort(key=lambda row: (-row["items_sent"], row["account"].full_name.lower()))
        operator_counts = {
            "active": len(active_operators),
            "items": sum(row["items_sent"] for row in operator_analytics),
            "delivered": sum(row["delivered"] for row in operator_analytics),
            "in_progress": sum(row["in_progress"] for row in operator_analytics),
        }
        monthly_budget = db.scalar(select(MonthlyBudget).where(MonthlyBudget.month == budget_month))
        purchase_items = db.scalars(
            select(PurchaseItem)
            .where(PurchaseItem.month == budget_month)
            .order_by(PurchaseItem.created_at.desc())
        ).all()
        budget_amount = monthly_budget.amount if monthly_budget else 0.0
        budget_spent = sum(item.amount for item in purchase_items)
        budget_remaining = budget_amount - budget_spent
        category_totals: dict[str, float] = {}
        category_counts: dict[str, int] = {}
        daily_totals: dict[str, float] = {}
        for item in purchase_items:
            category_totals[item.category] = category_totals.get(item.category, 0.0) + item.amount
            category_counts[item.category] = category_counts.get(item.category, 0) + 1
            day_key = item.created_at.strftime("%d %b")
            daily_totals[day_key] = daily_totals.get(day_key, 0.0) + item.amount
        budget_category_totals = [
            {"category": category, "amount": amount}
            for category, amount in sorted(category_totals.items(), key=lambda entry: entry[0].lower())
        ]
        category_entries = sorted(category_totals.items(), key=lambda entry: entry[1], reverse=True)
        timeline_entries = sorted(
            daily_totals.items(),
            key=lambda entry: datetime.strptime(entry[0], "%d %b").replace(year=datetime.utcnow().year),
        )
        cumulative_amounts = []
        running_total = 0.0
        for _, amount in timeline_entries:
            running_total += amount
            cumulative_amounts.append(running_total)
        budget_chart_data = {
            "categoryLabels": [category for category, _ in category_entries],
            "categoryAmounts": [amount for _, amount in category_entries],
            "categoryCounts": [category_counts.get(category, 0) for category, _ in category_entries],
            "timelineLabels": [label for label, _ in timeline_entries],
            "timelineAmounts": [amount for _, amount in timeline_entries],
            "cumulativeAmounts": cumulative_amounts,
            "usageLabels": ["Spent", "Remaining"],
            "usageAmounts": [budget_spent, max(budget_remaining, 0)],
        }
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=localized_context(
            request,
            current_user=user,
            employees=employees,
            recipient_accounts=recipient_accounts,
            users=users,
            assigned_employee_ids=assigned_employee_ids,
            user_roles=USER_ROLES,
            shipments=shipments,
            shipment_pagination={
                "page": shipping_page,
                "per_page": SHIPMENTS_PER_PAGE,
                "total": total_shipments,
                "total_pages": shipping_total_pages,
                "start": shipping_page_start + 1 if total_shipments else 0,
                "end": min(shipping_page_start + SHIPMENTS_PER_PAGE, total_shipments),
                "has_previous": shipping_page > 1,
                "has_next": shipping_page < shipping_total_pages,
                "previous_page": shipping_page - 1 if shipping_page > 1 else 1,
                "next_page": shipping_page + 1 if shipping_page < shipping_total_pages else shipping_total_pages,
            },
            counts=counts,
            mock_mode=RAJAONGKIR_MOCK,
            created=bool(created),
            user_created=bool(user_created),
            user_updated=bool(user_updated),
            user_error=user_error,
            budget_month=budget_month,
            monthly_budget=monthly_budget,
            purchase_items=purchase_items,
            budget_amount=budget_amount,
            budget_spent=budget_spent,
            budget_remaining=budget_remaining,
            budget_category_totals=budget_category_totals,
            budget_chart_data=budget_chart_data,
            operator_analytics=operator_analytics,
            operator_counts=operator_counts,
            budget_updated=bool(budget_updated),
            budget_item_created=bool(budget_item_created),
            location_created=bool(location_created),
            budget_error=budget_error,
            sort=sort,
            company_locations=company_locations,
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


@app.post("/budgets/set")
def set_monthly_budget_form(
    request: Request,
    month: str = Form(...),
    amount: float = Form(...),
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    require_roles(user, "admin")

    month = normalize_budget_month(month)
    if amount < 0:
        return dashboard_redirect(budget_error="Budget amount cannot be negative.", budget_month=month)

    monthly_budget = db.scalar(select(MonthlyBudget).where(MonthlyBudget.month == month))
    if not monthly_budget:
        monthly_budget = MonthlyBudget(month=month, created_by_id=user.id)
        db.add(monthly_budget)
    monthly_budget.amount = amount
    monthly_budget.updated_at = datetime.utcnow()
    db.commit()
    return dashboard_redirect(budget_updated=1, budget_month=month)


@app.post("/budget-items/create")
def create_purchase_item_form(
    request: Request,
    month: str = Form(...),
    item_name: str = Form(...),
    category: str = Form(...),
    amount: float = Form(...),
    note: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    require_roles(user, "admin")

    month = normalize_budget_month(month)
    item_name = item_name.strip()
    category = category.strip()
    note = note.strip() if note else None

    if not item_name:
        return dashboard_redirect(budget_error="Item name is required.", budget_month=month)
    if not category:
        return dashboard_redirect(budget_error="Category is required.", budget_month=month)
    if amount <= 0:
        return dashboard_redirect(budget_error="Item amount must be greater than 0.", budget_month=month)

    db.add(PurchaseItem(
        month=month,
        item_name=item_name,
        category=category,
        amount=amount,
        note=note,
        created_by_id=user.id,
    ))
    db.commit()
    return dashboard_redirect(budget_item_created=1, budget_month=month)


@app.post("/locations/create")
def create_company_location_form(
    request: Request,
    company_name: str = Form(...),
    branch_name: str = Form(...),
    address: str = Form(...),
    road: Optional[str] = Form(None),
    city: Optional[str] = Form(None),
    state: Optional[str] = Form(None),
    postcode: Optional[str] = Form(None),
    country: Optional[str] = Form("Indonesia"),
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    require_roles(user, "admin")

    company_name = company_name.strip()
    branch_name = branch_name.strip()
    address = address.strip()
    if not company_name or not branch_name or not address:
        return dashboard_redirect(user_error="Company, branch, and address are required.")

    db.add(CompanyLocation(
        company_name=company_name,
        branch_name=branch_name,
        address=address,
        road=road.strip() if road else None,
        city=city.strip() if city else None,
        state=state.strip() if state else None,
        postcode=postcode.strip() if postcode else None,
        country=country.strip() if country else None,
        created_by_id=user.id,
    ))
    db.commit()
    return dashboard_redirect(location_created=1)


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
            events=sort_tracking_events_for_timeline(shipment.events),
            current_user=user,
            mock_mode=RAJAONGKIR_MOCK,
            refreshed=bool(refreshed),
            refresh_error=refresh_error,
        ),
    )


@app.post("/shipments/create")
async def create_shipment_form(
    request: Request,
    title: str = Form(...),
    document_type: str = Form("Item"),
    employee_id: Optional[int] = Form(None),
    recipient_user_id: Optional[int] = Form(None),
    courier: str = Form(...),
    awb: str = Form(...),
    external_awb: Optional[str] = Form(None),
    po_number: Optional[str] = Form(None),
    sender_tags: Optional[str] = Form(None),
    origin: str = Form(...),
    destination: str = Form(...),
    shipping_cost: float = Form(0),
    eta_days: int = Form(0),
    allow_manual_awb: str = Form("0"),
    db: Session = Depends(get_db),
):
    user = get_current_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    require_roles(user, "admin", "operator")
    try:
        recipient_employee_id = resolve_recipient_employee_id(
            db,
            recipient_user_id=recipient_user_id,
            employee_id=employee_id,
        )
        try:
            provider_fields = await resolve_awb_shipment_fields(courier, awb, origin, destination, shipping_cost, eta_days)
        except HTTPException as exc:
            if exc.status_code == 404 and allow_manual_awb == "1":
                provider_fields = {
                    "origin": origin,
                    "destination": destination,
                    "shipping_cost": shipping_cost,
                    "eta_days": eta_days,
                    "last_location": destination,
                }
            else:
                raise
        create_shipment_record(
            db,
            title=title,
            document_type=document_type,
            employee_id=recipient_employee_id,
            created_by_id=user.id,
            courier=courier,
            awb=awb,
            external_awb=external_awb,
            po_number=po_number,
            sender_tags=normalize_sender_tags(sender_tags, user.role),
            origin=provider_fields["origin"],
            destination=provider_fields["destination"],
            shipping_cost=provider_fields["shipping_cost"],
            eta_days=provider_fields["eta_days"],
            last_location=provider_fields["last_location"],
        )
    except HTTPException as exc:
        if exc.status_code in {404, 409, 502, 503}:
            if request_prefers_json(request):
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": exc.detail},
                )
            return dashboard_redirect(user_error=str(exc.detail))
        raise
    if request_prefers_json(request):
        return {"redirect_url": "/?created=1"}
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
