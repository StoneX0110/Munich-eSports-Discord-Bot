import json
from unittest.mock import MagicMock, patch

import pytest

import bot


def test_main_initializes_runtime_and_honors_dry_run():
    with (
        patch.object(bot, "DISCORD_TOKEN", "discord-token"),
        patch.object(bot, "EV_API_KEY", "ev-token"),
        patch.object(bot, "configure_logging") as configure_logging,
        patch.object(bot, "initialize_easyverein_client") as initialize_client,
        patch.object(bot.bot, "run") as run,
    ):
        bot.bot.dry_run = False
        bot.main(["--dry-run"])

    configure_logging.assert_called_once_with()
    initialize_client.assert_called_once_with()
    run.assert_called_once_with("discord-token", log_handler=None)
    assert bot.bot.dry_run is True


def test_corrupt_known_members_file_is_not_treated_as_first_run(tmp_path):
    path = tmp_path / "known_members.json"
    path.write_text("not json")
    with patch.object(bot, "KNOWN_MEMBERS_FILE", path):
        with pytest.raises(json.JSONDecodeError):
            bot._load_known_members()
    assert path.read_text() == "not json"


def test_dry_run_does_not_register_mutating_cogs_or_sync_commands():
    import asyncio
    from unittest.mock import AsyncMock
    with patch.object(bot.bot, 'dry_run', True, create=True), \
         patch.object(bot.bot, 'load_extension', new_callable=AsyncMock) as load, \
         patch.object(bot.bot.tree, 'sync', new_callable=AsyncMock) as sync:
        asyncio.run(bot._setup_hook())
    load.assert_not_awaited()
    sync.assert_not_awaited()
