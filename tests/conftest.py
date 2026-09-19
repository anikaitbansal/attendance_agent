import pytest

from app.notification_service import wait_for_chat_deliveries


@pytest.fixture(autouse=True)
def no_real_integrations(monkeypatch: pytest.MonkeyPatch):
    """Keep tests away from anything configured in a developer's .env.

    app.agent calls load_dotenv() at import, so a real Google Chat webhook in
    .env would otherwise receive every notification the tests create. Tests
    that exercise the webhook set a fake URL themselves.
    """
    monkeypatch.delenv("GOOGLE_CHAT_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("REMINDERS_ENABLED", "false")
    yield
    # Background Chat posts must not outlive the test, or they would write to
    # the next test's database.
    wait_for_chat_deliveries()
