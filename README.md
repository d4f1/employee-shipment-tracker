# Employee Shipment Tracker — JWT MVP

A FastAPI, SQLite and Bootstrap application for tracking company items and parcels delivered to employees.

## Authentication and roles

The application now uses signed JWT access tokens.

- The HTML dashboard stores the token in an `HttpOnly` cookie.
- REST API clients send the token through `Authorization: Bearer <token>`.
- The role is loaded from the database and signed into the token.
- Employees can only view shipments assigned to their linked employee record.
- Admins and operators can view, create and refresh all shipments.

### Demo users

| Username | Password | Role |
|---|---|---|
| `admin` | `Admin123!` | Admin |
| `operator` | `Operator123!` | Operator |
| `andriana` | `Employee123!` | Employee |
| `siti` | `Employee123!` | Employee |

Change or remove these accounts before production.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app:app --reload
```

Open:

- Login page: http://127.0.0.1:8000/login
- Dashboard: http://127.0.0.1:8000/
- Swagger: http://127.0.0.1:8000/docs

## Free deployment for client demo

Heroku no longer provides a true free dyno/database plan. Use Render's free Web Service instead. Do not use Blueprint for this demo.

1. Push this folder to a GitHub, GitLab, or Bitbucket repository.
2. In Render, click **New > Web Service**.
3. Connect the repository.
4. Select the **Free** instance type.
5. Use these settings:

| Field | Value |
|---|---|
| Runtime | Python |
| Build command | `pip install -r requirements.txt` |
| Start command | `uvicorn app:app --host 0.0.0.0 --port $PORT` |
| Health check path | `/healthz` |

Add these environment variables:

| Key | Value |
|---|---|
| `APP_NAME` | `Employee Shipment Tracker` |
| `DATABASE_URL` | `sqlite:///./employee_shipments.db` |
| `JWT_SECRET_KEY` | Generate a long random value |
| `JWT_ALGORITHM` | `HS256` |
| `JWT_ACCESS_TOKEN_MINUTES` | `60` |
| `JWT_COOKIE_SECURE` | `true` |
| `RAJAONGKIR_MOCK` | `true` |
| `RAJAONGKIR_API_KEY` | Leave empty for mock demo |
| `RAJAONGKIR_BASE_URL` | `https://rajaongkir.komerce.id/api/v1` |
| `NOMINATIM_REVERSE_URL` | `https://nominatim.openstreetmap.org/reverse` |
| `GEOCODER_USER_AGENT` | `employee-shipment-tracker-demo/1.0` |

After deploy finishes, share:

```text
https://<service-name>.onrender.com/login
```

Demo accounts:

| Username | Password |
|---|---|
| `admin` | `Admin123!` |
| `operator` | `Operator123!` |
| `andriana` | `Employee123!` |

The free service is enough for a behavior-flow demo, but it can sleep after inactivity and local SQLite data can reset after redeploy/restart. Use Postgres or a paid persistent disk before treating this as production.

## API login

```bash
curl -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'username=admin&password=Admin123!'
```

Copy `access_token`, then call a protected endpoint:

```bash
curl http://127.0.0.1:8000/api/auth/me \
  -H 'Authorization: Bearer YOUR_TOKEN'

curl http://127.0.0.1:8000/api/shipments \
  -H 'Authorization: Bearer YOUR_TOKEN'
```

## Role access

| Feature | Admin | Operator | Employee |
|---|---:|---:|---:|
| View all shipments | Yes | Yes | No |
| View own shipments | Yes | Yes | Yes |
| Create shipments | Yes | Yes | No |
| Refresh courier tracking | Yes | Yes | No |
| List employees | Yes | Yes | No |

## JWT configuration

```env
JWT_SECRET_KEY=replace-with-a-long-random-production-secret
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_MINUTES=60
JWT_COOKIE_SECURE=false
```

For HTTPS production deployment, set:

```env
JWT_COOKIE_SECURE=true
```

Generate a secret example:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

## RajaOngkir configuration

Mock mode is enabled by default:

```env
RAJAONGKIR_MOCK=true
```

For live tracking:

```env
RAJAONGKIR_MOCK=false
RAJAONGKIR_API_KEY=your-secret-key
RAJAONGKIR_BASE_URL=https://rajaongkir.komerce.id/api/v1
```

## Production improvements

1. PostgreSQL and Alembic migrations.
2. Refresh-token rotation and token revocation.
3. Company SSO/OIDC integration.
4. CSRF protection for browser forms.
5. Login throttling and account lockout.
6. Audit logs for shipment and role changes.
7. Admin user-management screen.
8. HTTPS-only secure cookies.

## Dashboard UI

The dashboard uses Bootstrap 5.3 and Bootstrap Icons from CDN.

UI features:

- Responsive navigation and KPI cards
- Modal-based shipment creation
- Smooth modal transitions
- Success toast after shipment creation
- Confirmation modal before tracking refresh
- Loading states to prevent duplicate form submission
- Client-side shipment search
- Responsive shipment table
- Improved login and shipment detail pages

Internet access is required for the Bootstrap CDN assets. For an internal
offline deployment, download the Bootstrap CSS, JavaScript, and icon assets and
serve them from a local `static` directory.
