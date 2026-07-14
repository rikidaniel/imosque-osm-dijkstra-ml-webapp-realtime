from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.domain.models.schemas import UserSettingsRequest
from app.domain.repositories import user_settings_repo


def test_user_settings_rejects_unknown_algorithm():
    with pytest.raises(ValidationError):
        UserSettingsRequest(
            user_id="device_test",
            search_settings={"algorithm": "unknown"},
        )


def test_partial_prayer_update_preserves_search_settings(monkeypatch):
    existing = {
        "_key": "123",
        "user_id": "device_test",
        "search_settings": {"algorithm": "astar", "bufferKm": "25"},
        "prayer_settings": {"hijriDate": "old"},
    }
    collection = MagicMock()
    collection.find.return_value = [existing]
    collection.get.return_value = {**existing, "prayer_settings": {"hijriDate": "new"}}
    database = MagicMock()
    database.collection.return_value = collection
    monkeypatch.setattr(user_settings_repo, "get_db", lambda: database)

    user_settings_repo.save_user_settings(
        "device_test",
        {"prayer_settings": {"hijriDate": "new"}},
    )

    update_document = collection.update.call_args.args[0]
    assert collection.update.call_args.kwargs == {"merge": True}
    assert update_document["search_settings"] == existing["search_settings"]
    assert update_document["prayer_settings"]["hijriDate"] == "new"
