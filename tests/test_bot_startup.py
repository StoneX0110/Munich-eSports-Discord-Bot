import asyncio
from unittest.mock import AsyncMock, patch

import pytest
import bot


@pytest.mark.parametrize('dry_run', [False, True])
def test_startup_initializes_runtime_and_only_registers_cogs_when_live(dry_run):
    with (
        patch.object(bot, 'DISCORD_TOKEN', 'discord-token'),
        patch.object(bot, 'EV_API_KEY', 'ev-token'),
        patch.object(bot, 'configure_logging') as logging,
        patch.object(bot, 'initialize_easyverein_client') as client,
        patch.object(bot.bot, 'dry_run', False, create=True),
        patch.object(bot.bot, 'load_extension', new_callable=AsyncMock) as load,
        patch.object(bot.bot.tree, 'sync', new_callable=AsyncMock) as sync,
        patch.object(bot.bot, 'run', side_effect=lambda *a, **k: asyncio.run(bot._setup_hook())),
    ):
        bot.main(['--dry-run'] if dry_run else [])
    logging.assert_called_once()
    client.assert_called_once()
    assert load.await_count == (0 if dry_run else 5)
    assert sync.await_count == (0 if dry_run else 1)
