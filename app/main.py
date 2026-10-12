"""Expose the existing FastAPI application at ``app.main:app``."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


_ROOT_APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
_SPEC = spec_from_file_location("employee_shipment_tracker_application", _ROOT_APP_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"Could not load FastAPI application from {_ROOT_APP_PATH}")

_application_module = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_application_module)
app = _application_module.app

__all__ = ["app"]
