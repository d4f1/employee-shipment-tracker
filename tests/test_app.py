import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
MODULE_NAME = "employee_shipment_tracker_test_app"


@pytest.fixture()
def tracker(monkeypatch, tmp_path):
    """Load the application against an isolated SQLite database."""
    database_path = tmp_path / "employee_shipments_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    monkeypatch.setenv("JWT_SECRET_KEY", "pytest-only-secret")
    monkeypatch.setenv("RAJAONGKIR_MOCK", "true")

    sys.modules.pop(MODULE_NAME, None)
    spec = importlib.util.spec_from_file_location(MODULE_NAME, APP_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)

    yield module

    module.engine.dispose()
    sys.modules.pop(MODULE_NAME, None)


def test_health_reports_mock_mode(tracker):
    response = TestClient(tracker.app).get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["rajaongkir_mode"] == "mock"


def test_manual_ncs_shipment_does_not_call_awb_provider(tracker):
    with tracker.SessionLocal() as db:
        recipient = tracker.Employee(
            employee_code="PYTEST-NCS-001",
            name="NCS Test Recipient",
            email="ncs-recipient@example.test",
            company_name="Pytest Company",
        )
        sender = tracker.User(
            username="pytest-ncs-sender",
            full_name="Pytest NCS Sender",
            company_name="Pytest Company",
            password_hash=tracker.password_hash.hash("PytestPassword123!"),
            role="admin",
            is_active=True,
        )
        db.add_all([recipient, sender])
        db.commit()
        db.refresh(recipient)
        db.refresh(sender)

        tracker.get_current_web_user = lambda request, database: sender

        async def provider_must_not_be_called(*args, **kwargs):
            raise AssertionError("Manual NCS shipment must not call the AWB provider")

        tracker.resolve_awb_shipment_fields = provider_must_not_be_called
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/shipments/create",
                "headers": [(b"accept", b"application/json"), (b"x-requested-with", b"fetch")],
            }
        )

        result = asyncio.run(
            tracker.create_shipment_form(
                request=request,
                title="Pytest manual NCS shipment",
                document_type="Document",
                employee_id=recipient.id,
                recipient_user_id=None,
                courier="ncs",
                awb="",
                external_awb="PYTEST-TICKET-1",
                po_number="PYTEST-PO-1",
                sender_tags="#HO",
                origin="Jakarta",
                destination="Bandung",
                shipping_cost=125000,
                eta_days=2,
                allow_manual_awb="1",
                db=db,
            )
        )

        assert result == {"redirect_url": "/?created=1"}
        shipment = db.query(tracker.Shipment).filter_by(title="Pytest manual NCS shipment").one()
        assert shipment.courier == "ncs"
        assert shipment.awb == ""
        assert shipment.shipping_cost == 125000
