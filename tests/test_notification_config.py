import pytest

from app.config import Settings


VAPID_SETTINGS = {
    "web_push_vapid_public_key": "public-key",
    "web_push_vapid_private_key": "private-key",
    "web_push_vapid_subject": "mailto:notifications@example.com",
}


def test_web_push_is_disabled_without_vapid_configuration(monkeypatch):
    for env_name in (
        "WEB_PUSH_VAPID_PUBLIC_KEY",
        "WEB_PUSH_VAPID_PRIVATE_KEY",
        "WEB_PUSH_VAPID_SUBJECT",
    ):
        monkeypatch.delenv(env_name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.web_push_enabled is False


def test_web_push_is_enabled_with_complete_vapid_configuration():
    settings = Settings(_env_file=None, **VAPID_SETTINGS)

    assert settings.web_push_enabled is True


@pytest.mark.parametrize("missing_setting", VAPID_SETTINGS)
def test_web_push_requires_every_vapid_setting(missing_setting):
    values = VAPID_SETTINGS.copy()
    values[missing_setting] = ""

    settings = Settings(_env_file=None, **values)

    assert settings.web_push_enabled is False
