from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


def test_import_app_main_works() -> None:
    import app.main as main_module

    assert main_module.app is not None
    assert main_module.create_app is not None


def test_email_worker_entrypoint_imports_without_connecting_to_external_providers() -> None:
    module_name = "scripts.run_email_worker"
    sys.modules.pop(module_name, None)

    with (
        patch("pika.BlockingConnection", MagicMock()) as blocking_connection,
        patch("sqlalchemy.create_engine", MagicMock()) as create_engine,
    ):
        module = importlib.import_module(module_name)

    assert callable(module.main)
    blocking_connection.assert_not_called()
    create_engine.assert_not_called()


def test_no_real_provider_calls_happen_at_import_time() -> None:
    module_name = "scripts.run_email_worker"
    sys.modules.pop(module_name, None)

    with patch(
        "app.email.factory.create_email_provider_from_settings",
        side_effect=AssertionError("email provider must not be created at import time"),
    ):
        import app.main as main_module  # noqa: F401
        worker_module = importlib.import_module(module_name)

    assert main_module.app is not None
    assert callable(worker_module.main)


def test_docker_compose_config_still_validates() -> None:
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["docker", "compose", "config"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
