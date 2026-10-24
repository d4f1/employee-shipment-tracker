# Employee Shipment Tracker — JWT MVP

A FastAPI and Bootstrap application for tracking company items and parcels delivered to employees. Local development uses SQLite; production uses PostgreSQL on the production VM.

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

## Local and production databases

Local development uses the checked-in [`.env.example`](.env.example) and SQLite:

```env
DATABASE_URL=sqlite:///./employee_shipments.db
```

Production uses the root-owned file `/opt/employee-shipment-tracker/state/application.env` on the VM. Its non-secret shape is documented in [`.env.production.example`](.env.production.example):

```env
DATABASE_URL=postgresql+psycopg:///employee_shipments
JWT_COOKIE_SECURE=true
RAJAONGKIR_MOCK=false
```

PostgreSQL listens only on the VM loopback/socket. The application uses it for all production data, including company branch locations. SQLite is retained only as a migration recovery backup and is not the active production database.

## GitHub Actions deployment

Every push to `master` runs [`.github/workflows/deploy-production.yml`](.github/workflows/deploy-production.yml):

1. Install dependencies and smoke-test the application against temporary SQLite on the GitHub runner.
2. Wait for the protected GitHub `production` environment.
3. Verify the VM is configured for its local PostgreSQL database and live RajaOngkir.
4. Create a PostgreSQL backup, deploy the exact pushed commit, restart the service, and check application health and the company-location table.

The workflow never writes production secrets or `application.env`. It rolls back the application code if the restart or health check fails; PostgreSQL backups are saved to `/opt/employee-shipment-tracker/state/postgresql-backups/` before every deploy.

Create these secrets in **GitHub → Settings → Environments → production**:

| Secret | Purpose |
|---|---|
| `PRODUCTION_SSH_HOST` | VM public IP or hostname |
| `PRODUCTION_SSH_USER` | Deploy user with passwordless `sudo` for the commands in the workflow |
| `PRODUCTION_SSH_PRIVATE_KEY` | Private key for that deploy user |
| `PRODUCTION_SSH_KNOWN_HOSTS` | Pinned SSH host key line for the VM |

Use an environment approval rule before enabling automatic production deployment.

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

## Import branch locations

Admins can import up to 500 branch locations from the **Locations** panel using a UTF-8 CSV or XLSX file up to 5 MB. Enter the shared company name in the form, then upload a sheet with these headers:

| Required | Optional | Ignored |
|---|---|---|
| `Location`, `Address` | `Company`, `Category`, `Phone`, `Fax` | `No.` |

The import is atomic: any invalid row stops the whole import. Existing rows with the same company and location are updated; new rows are added. When `Category`, `Phone`, or `Fax` headers are omitted, those values on matching existing locations are preserved.

## Google sign-in and registration

The anonymous landing page opens authentication in a modal and supports both existing Google-account login and new employee registration.

1. In Google Cloud Console, create an OAuth 2.0 Client ID for a **Web application**.
2. Add this authorized redirect URI for local development:

```text
http://127.0.0.1:8000/auth/google/callback
```

3. Set these values in `.env` and restart the application:

```env
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
GOOGLE_REDIRECT_URI=http://127.0.0.1:8000/auth/google/callback
```

For deployment, replace `GOOGLE_REDIRECT_URI` with the HTTPS callback URL for the deployed domain and add the exact same URI in Google Cloud Console. New Google registrations are created with the `employee` role.

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

Live mode is enabled by default:

```env
RAJAONGKIR_MOCK=false
RAJAONGKIR_API_KEY=your-secret-key
```

Use mock mode only for local demos:

```env
RAJAONGKIR_MOCK=true
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
