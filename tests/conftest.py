from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest

pytest_plugins = ["tests.test_retell_tool_adapter"]


@pytest.fixture(autouse=True)
def block_live_retell_http() -> Generator[None, None, None]:
    """CI must never reach api.retellai.com; tests inject stub clients instead."""

    def _reject_live_retell_http(*args: object, **kwargs: object) -> None:
        request = args[0] if args else kwargs.get("request")
        url = getattr(request, "full_url", None) or str(request)
        if "api.retellai.com" in url:
            raise AssertionError(
                "live Retell HTTP is blocked in tests; inject a stub RetellWebCallClient",
            )

    with patch(
        "app.integrations.retell.web_call_client.urlopen",
        side_effect=_reject_live_retell_http,
    ):
        yield
