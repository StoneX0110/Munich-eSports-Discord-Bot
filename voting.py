"""
Department election voting cog for the Munich eSports Discord bot.

Provides slash commands for managing anonymous department elections with
session-based delegated votes, verified via easyVerein department membership.
"""

import asyncio
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands
from easyverein.models.custom_field import CustomField
from easyverein.models.custom_field_select_option import CustomFieldSelectOption
from easyverein.models.member import MemberFilter

from config import (
    ABTEILUNGEN_FIELD_ID,
    DEPARTMENT_HEAD_ROLE_ID,
    GUILD_ID,
    MEMBERSHIP_ROLE_ID,
    VOTES_FILE,
)

logger = logging.getLogger("munich_esports_bot.voting")

GUILD_OBJ = discord.Object(id=GUILD_ID)

# Concurrency guards
_data_lock = asyncio.Lock()
_active_voters: dict[str, set[str]] = {}  # vote_id → set of user_ids with open dialogs


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _load_data() -> dict:
    """Load the votes/sessions data from disk."""
    if not VOTES_FILE.exists():
        return {
            "next_session_id": 1,
            "next_vote_id": 1,
            "sessions": {},
            "votes": {},
        }
    try:
        return json.loads(VOTES_FILE.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to load %s – starting fresh.", VOTES_FILE)
        return {
            "next_session_id": 1,
            "next_vote_id": 1,
            "sessions": {},
            "votes": {},
        }


def _save_data(data: dict) -> None:
    """Persist the votes/sessions data to disk."""
    try:
        VOTES_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        logger.exception("Failed to save %s.", VOTES_FILE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_department_head(member: discord.Member) -> bool:
    """Check whether a Discord member has the department head role."""
    return any(r.id == DEPARTMENT_HEAD_ROLE_ID for r in member.roles)


def _now_iso() -> str:
    return datetime.now(ZoneInfo("Europe/Berlin")).isoformat()


def _max_votes_for_user(session: dict, user_id: str) -> int:
    """Return the total number of votes a user may cast (1 own + delegated)."""
    return 1 + session.get("delegated_votes", {}).get(user_id, 0)


def _votes_used_by_user(vote: dict, user_id: str) -> int:
    """Return how many votes the user has already cast in this vote."""
    return vote.get("votes_used", {}).get(user_id, 0)


def _remaining_votes(session: dict, vote: dict, user_id: str) -> int:
    return _max_votes_for_user(session, user_id) - _votes_used_by_user(vote, user_id)


def _build_result_bar(count: int, total: int, bar_length: int = 10) -> str:
    """Build a visual bar like ████████░░ 8 (40.0%)."""
    pct = (count / total * 100) if total > 0 else 0
    filled = round(bar_length * count / total) if total > 0 else 0
    bar = "█" * filled + "░" * (bar_length - filled)
    return f"{bar} {count} ({pct:.1f}%)"


async def _get_vote(
    vote_id: str | int,
    interaction: discord.Interaction,
) -> tuple[dict, dict, dict] | None:
    """Load data and return (data, vote, session) for an active vote.

    Sends an ephemeral error to *interaction* and returns None on failure.
    """
    async def _send_error(msg: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    data = _load_data()
    vote = data["votes"].get(str(vote_id))
    if not vote:
        await _send_error(f"❌ Abstimmung #{vote_id} nicht gefunden.")
        return None
    if not vote.get("active"):
        await _send_error("❌ Diese Abstimmung ist nicht mehr aktiv.")
        return None
    session = data["sessions"].get(str(vote["session_id"]), {})
    return data, vote, session


async def _get_active_session(
    session_id: int, interaction: discord.Interaction,
) -> tuple[dict, dict, str] | None:
    """Load data and return (data, session, sid) if the session exists and is active.

    Sends an ephemeral error to *interaction* and returns None otherwise.
    """
    data = _load_data()
    sid = str(session_id)
    session = data["sessions"].get(sid)
    if not session or not session.get("active"):
        await interaction.response.send_message(
            f"❌ Wahlsitzung #{session_id} nicht gefunden oder bereits beendet.",
            ephemeral=True,
        )
        return None
    return data, session, sid


# ---------------------------------------------------------------------------
# Vote recording helper
# ---------------------------------------------------------------------------

async def _record_votes(
    vote_id: str, option: str, count: int, interaction: discord.Interaction,
) -> bool:
    """Record *count* votes for *option* and send a confirmation message.

    Uses followup.send if the interaction response is already consumed,
    otherwise uses response.send_message.
    Returns True on success, False on failure (error already sent to user).
    """
    async with _data_lock:
        result = await _get_vote(vote_id, interaction)
        if not result:
            return False
        data, vote, session = result
        user_id = str(interaction.user.id)

        # Re-check remaining votes to prevent over-voting
        remaining = _remaining_votes(session, vote, user_id)
        if remaining <= 0:
            if interaction.response.is_done():
                await interaction.followup.send(
                    "❌ Du hast bereits alle deine Stimmen für diese Abstimmung abgegeben.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "❌ Du hast bereits alle deine Stimmen für diese Abstimmung abgegeben.",
                    ephemeral=True,
                )
            return False

        # Cap count at remaining votes
        count = min(count, remaining)

        # Update tallies
        vote["tallies"][option] = vote["tallies"].get(option, 0) + count

        # Update votes_used
        if "votes_used" not in vote:
            vote["votes_used"] = {}
        vote["votes_used"][user_id] = vote["votes_used"].get(user_id, 0) + count

        _save_data(data)
        new_remaining = remaining - count

    # Send confirmation (outside lock)
    if new_remaining > 0:
        msg = (
            f"✅ **{count}** Stimme(n) für **{option}** abgegeben! "
            f"Du hast noch **{new_remaining}** Stimme(n) übrig."
        )
    else:
        msg = (
            f"✅ **{count}** Stimme(n) für **{option}** abgegeben! "
            f"Du hast alle deine Stimmen abgegeben. Danke!"
        )

    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)
    return True


# ---------------------------------------------------------------------------
# Persistent vote button view
# ---------------------------------------------------------------------------

class VoteView(discord.ui.View):
    """Persistent view attached to vote embeds with the 'Abstimmen' button."""

    def __init__(self, vote_id: str):
        super().__init__(timeout=None)
        self.vote_id = vote_id

    @discord.ui.button(
        label="🗳️ Abstimmen",
        style=discord.ButtonStyle.primary,
        custom_id="vote_button",  # placeholder – overridden per instance
    )
    async def vote_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle the vote button press – check eligibility and show option select."""
        result = await _get_vote(self.vote_id, interaction)
        if not result:
            return
        data, vote, session = result
        user_id = str(interaction.user.id)

        # Prevent multiple open vote dialogs
        if user_id in _active_voters.get(self.vote_id, set()):
            await interaction.response.send_message(
                "❌ Du hast bereits ein Abstimmungsfenster offen. "
                "Bitte schließe es zuerst oder warte, bis es ausläuft.",
                ephemeral=True,
            )
            return

        # --- Eligibility: check club / department membership ---
        department = session.get("department")

        if not department:
            # No department restriction – any club member may vote
            if not any(r.id == MEMBERSHIP_ROLE_ID for r in interaction.user.roles):
                await interaction.response.send_message(
                    "❌ Du bist kein Vereinsmitglied und darfst daher nicht abstimmen.",
                    ephemeral=True,
                )
                return
        else:
            # Department-specific – verify via easyVerein
            ev_client = interaction.client.ev_client
            today = datetime.now(ZoneInfo("Europe/Berlin")).date()

            eligible = False
            try:
                query = "{id,resignationDate,customFields{customField{id},value,selectedOptions{id,value}}}"
                search = MemberFilter(
                    custom_field_name="Discord-ID",
                    custom_field_value=user_id,
                    isApplication=False,
                )
                members = await asyncio.to_thread(
                    ev_client.member.get_all, query=query, search=search,
                )

                # Filter to active members only
                members = [
                    m for m in members
                    if m.resignationDate is None or m.resignationDate >= today
                ]

                for m in members:
                    if not m.customFields:
                        continue
                    for mcf in m.customFields:
                        cf = mcf.customField
                        if isinstance(cf, CustomField) and cf.id == ABTEILUNGEN_FIELD_ID:
                            if mcf.selectedOptions:
                                for opt in mcf.selectedOptions:
                                    if isinstance(opt, CustomFieldSelectOption) and opt.value == department:
                                        eligible = True
                            break
            except Exception:
                logger.exception("Failed to verify department membership for user %s.", user_id)
                await interaction.response.send_message(
                    "❌ Fehler bei der Überprüfung deiner Abteilungszugehörigkeit. Bitte versuche es erneut.",
                    ephemeral=True,
                )
                return

            if not eligible:
                await interaction.response.send_message(
                    f"❌ Du bist kein Mitglied der Abteilung **{department}** und darfst daher nicht abstimmen.",
                    ephemeral=True,
                )
                return

        # --- Check remaining votes ---
        remaining = _remaining_votes(session, vote, user_id)
        if remaining <= 0:
            await interaction.response.send_message(
                "❌ Du hast bereits alle deine Stimmen für diese Abstimmung abgegeben.",
                ephemeral=True,
            )
            return

        # --- Show option select ---
        view = VoteSelectView(self.vote_id, remaining, user_id)
        await interaction.response.send_message(
            f"**Abstimmung #{self.vote_id}: {vote['title']}**\n"
            f"Du hast noch **{remaining}** Stimme(n) übrig.\n"
            f"Wähle eine Option und die Anzahl der Stimmen:",
            view=view,
            ephemeral=True,
        )
        _active_voters.setdefault(self.vote_id, set()).add(user_id)


class VoteSelectView(discord.ui.View):
    """Ephemeral view with option selector, then count selector."""

    def __init__(self, vote_id: str, remaining: int, user_id: str):
        super().__init__(timeout=120)
        self.vote_id = vote_id
        self.remaining = remaining
        self.user_id = user_id

        # Load vote options for select menu
        data = _load_data()
        vote = data["votes"].get(vote_id, {})
        options = vote.get("options", [])

        self.option_select = discord.ui.Select(
            placeholder="Wähle eine Option...",
            options=[
                discord.SelectOption(label=opt, value=str(i))
                for i, opt in enumerate(options)
            ],
            custom_id=f"vote_select_{vote_id}_{user_id}",
        )
        self.options_list = options
        self.option_select.callback = self.on_option_select
        self.add_item(self.option_select)
        self.selected_option = None

    def _cleanup(self):
        """Remove this user from the active voters tracking."""
        voters = _active_voters.get(self.vote_id)
        if voters:
            voters.discard(self.user_id)

    async def on_option_select(self, interaction: discord.Interaction):
        idx = int(interaction.data["values"][0])
        self.selected_option = self.options_list[idx]

        if self.remaining == 1:
            # Only one vote – cast directly and clean up
            await interaction.response.edit_message(
                content=f"⏳ Stimme wird abgegeben für **{self.selected_option}**...",
                view=None,
            )
            await _record_votes(self.vote_id, self.selected_option, 1, interaction)
            self._cleanup()
            await interaction.delete_original_response()
        else:
            # Show count select (1 to remaining, max 25)
            self.clear_items()
            max_count = min(self.remaining, 25)
            count_select = discord.ui.Select(
                placeholder=f"Anzahl Stimmen für {self.selected_option}...",
                options=[
                    discord.SelectOption(label=str(i), value=str(i))
                    for i in range(1, max_count + 1)
                ],
                custom_id=f"vote_count_{self.vote_id}_{self.user_id}",
            )
            count_select.callback = self.on_count_select
            self.add_item(count_select)
            await interaction.response.edit_message(
                content=(
                    f"**{self.selected_option}** ausgewählt.\n"
                    f"Wie viele Stimmen möchtest du abgeben? (max {self.remaining})"
                ),
                view=self,
            )

    async def on_count_select(self, interaction: discord.Interaction):
        count = int(interaction.data["values"][0])
        self.stop()
        await interaction.response.edit_message(
            content=f"⏳ **{count}** Stimme(n) für **{self.selected_option}** werden abgegeben...",
            view=None,
        )
        await _record_votes(self.vote_id, self.selected_option, count, interaction)
        self._cleanup()
        await interaction.delete_original_response()

    async def on_timeout(self):
        self._cleanup()


# ---------------------------------------------------------------------------
# The Cog
# ---------------------------------------------------------------------------

class VotingCog(commands.Cog):
    """Slash command group for department election voting."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.department_choices: list[app_commands.Choice[str]] = []

    async def cog_load(self):
        """Fetch department choices from easyVerein at startup and register persistent views."""
        # Fetch department options directly via the Abteilungen custom field
        try:
            ev_client = self.bot.ev_client
            sub = ev_client.custom_field.select_option(ABTEILUNGEN_FIELD_ID)
            options = await asyncio.to_thread(sub.get_all)
            for opt in options:
                self.department_choices.append(
                    app_commands.Choice(name=opt.value, value=opt.value)
                )
        except Exception:
            logger.exception("Failed to fetch department choices from easyVerein.")

        if not self.department_choices:
            logger.warning(
                "No department choices loaded – /vote session will have no autocomplete."
            )
        else:
            logger.info(
                "Loaded %d department choices: %s",
                len(self.department_choices),
                [c.name for c in self.department_choices],
            )

        # Re-register persistent views for all active votes
        data = _load_data()
        for vote_id, vote in data.get("votes", {}).items():
            if vote.get("active"):
                view = VoteView(vote_id)
                # Set unique custom_id per vote
                view.vote_button.custom_id = f"vote_button_{vote_id}"
                self.bot.add_view(view)

    # -----------------------------------------------------------------------
    # Command groups
    # -----------------------------------------------------------------------
    vote_group = app_commands.Group(
        name="vote",
        description="Abstimmungs-Befehle",
        guild_ids=[GUILD_ID],
    )

    session_group = app_commands.Group(
        name="session",
        description="Wahlsitzungs-Befehle",
        guild_ids=[GUILD_ID],
    )

    # -----------------------------------------------------------------------
    # /session start
    # -----------------------------------------------------------------------
    @session_group.command(name="start", description="Starte eine Wahlsitzung (optional für eine Abteilung)")
    @app_commands.describe(department="Die Abteilung, für die abgestimmt wird (leer = alle Mitglieder)")
    async def session_start(self, interaction: discord.Interaction, department: str = None):
        if not _is_department_head(interaction.user):
            await interaction.response.send_message(
                "❌ Nur Abteilungsleiter können eine Wahlsitzung starten.", ephemeral=True
            )
            return

        # Validate department against known values
        if department is not None:
            department = department.strip()
            valid_names = [c.name for c in self.department_choices]
            if department not in valid_names:
                await interaction.response.send_message(
                    f"❌ Ungültige Abteilung. Gültige Werte: {', '.join(valid_names)}",
                    ephemeral=True,
                )
                return

        async with _data_lock:
            data = _load_data()
            session_id = str(data["next_session_id"])
            data["next_session_id"] += 1

            data["sessions"][session_id] = {
                "department": department,
                "created_by": interaction.user.id,
                "delegated_votes": {},
                "active": True,
                "created_at": _now_iso(),
            }
            _save_data(data)

        scope = f"für **{department}**" if department else "für **alle Mitglieder**"
        await interaction.response.send_message(
            f"✅ **Wahlsitzung #{session_id}** {scope} erstellt!\n"
            f"Nutze `/session delegate` um Delegiertenstimmen festzulegen, "
            f"dann `/vote start` um Abstimmungen zu erstellen."
        )

    @session_start.autocomplete("department")
    async def department_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return [c for c in self.department_choices if current.lower() in c.name.lower()][:25]

    # -----------------------------------------------------------------------
    # /session delegate
    # -----------------------------------------------------------------------
    @session_group.command(
        name="delegate",
        description="Delegiertenstimmen für einen Nutzer in einer Sitzung festlegen",
    )
    @app_commands.describe(
        session_id="ID der Wahlsitzung",
        user="Discord-Nutzer, der Delegiertenstimmen erhält",
        count="Anzahl der Delegiertenstimmen",
    )
    async def session_delegate(
        self,
        interaction: discord.Interaction,
        session_id: int,
        user: discord.Member,
        count: int,
    ):
        if not _is_department_head(interaction.user):
            await interaction.response.send_message(
                "❌ Nur Abteilungsleiter können Delegiertenstimmen vergeben.", ephemeral=True
            )
            return

        if count < 0:
            await interaction.response.send_message(
                "❌ Anzahl muss 0 oder größer sein.", ephemeral=True
            )
            return

        async with _data_lock:
            result = await _get_active_session(session_id, interaction)
            if not result:
                return
            data, session, sid = result

            user_id = str(user.id)
            if count == 0:
                session["delegated_votes"].pop(user_id, None)
            else:
                session["delegated_votes"][user_id] = count
            _save_data(data)

        total = 1 + count
        await interaction.response.send_message(
            f"✅ {user.mention} hat jetzt **{count}** Delegiertenstimme(n) in Sitzung #{session_id}.\n"
            f"Insgesamt kann {user.mention} **{total}** Stimme(n) pro Abstimmung abgeben.",
            ephemeral=True,
        )

    # -----------------------------------------------------------------------
    # /vote start
    # -----------------------------------------------------------------------
    @vote_group.command(name="start", description="Starte eine Abstimmung innerhalb einer Sitzung")
    @app_commands.describe(
        session_id="ID der Wahlsitzung",
        title="Titel der Abstimmung",
        options="Optionen (kommagetrennt, z.B. 'Alice, Bob, Charlie')",
    )
    async def vote_start(
        self,
        interaction: discord.Interaction,
        session_id: int,
        title: str,
        options: str,
    ):
        if not _is_department_head(interaction.user):
            await interaction.response.send_message(
                "❌ Nur Abteilungsleiter können Abstimmungen erstellen.", ephemeral=True
            )
            return

        # Sanitize and parse options (before lock – no state mutation)
        title = title.strip()[:100]
        option_list = [o.strip()[:50] for o in options.split(",") if o.strip()]
        if len(option_list) < 2:
            await interaction.response.send_message(
                "❌ Bitte gib mindestens 2 Optionen an (kommagetrennt).", ephemeral=True
            )
            return

        if len(option_list) > 25:
            await interaction.response.send_message(
                "❌ Maximal 25 Optionen erlaubt.", ephemeral=True
            )
            return

        if len(option_list) != len(set(option_list)):
            await interaction.response.send_message(
                "❌ Optionen dürfen nicht doppelt vorkommen.", ephemeral=True
            )
            return

        async with _data_lock:
            result = await _get_active_session(session_id, interaction)
            if not result:
                return
            data, session, sid = result

            vote_id = str(data["next_vote_id"])
            data["next_vote_id"] += 1

            tallies = {opt: 0 for opt in option_list}

            data["votes"][vote_id] = {
                "session_id": sid,
                "title": title,
                "options": option_list,
                "channel_id": interaction.channel_id,
                "message_id": None,  # set after sending
                "tallies": tallies,
                "votes_used": {},
                "active": True,
                "created_at": _now_iso(),
            }
            _save_data(data)

        # Build embed
        embed = discord.Embed(
            title=f"📊 Abstimmung #{vote_id}: {title}",
            description=(
                f"**Abteilung:** {session['department']}\n"
                f"**Sitzung:** #{sid}\n"
                f"**Status:** 🟢 Offen\n\n"
                f"**Optionen:**\n"
                + "\n".join(f"• {opt}" for opt in option_list)
            ),
            color=discord.Color.blue(),
            timestamp=datetime.now(ZoneInfo("Europe/Berlin")),
        )
        embed.set_footer(text=f"Erstellt von {interaction.user.display_name}")

        # Create persistent view
        view = VoteView(vote_id)
        view.vote_button.custom_id = f"vote_button_{vote_id}"
        self.bot.add_view(view)

        await interaction.response.send_message(embed=embed, view=view)

        # Store message ID for later updates
        msg = await interaction.original_response()
        async with _data_lock:
            data = _load_data()
            if vote_id in data["votes"]:
                data["votes"][vote_id]["message_id"] = msg.id
                _save_data(data)

        logger.info(
            "Vote #%s created in session #%s by %s: %s",
            vote_id, sid, interaction.user, title,
        )

    # -----------------------------------------------------------------------
    # /vote close
    # -----------------------------------------------------------------------
    @vote_group.command(name="close", description="Beende eine Abstimmung und zeige das Ergebnis")
    @app_commands.describe(vote_id="ID der Abstimmung")
    async def vote_close(self, interaction: discord.Interaction, vote_id: int):
        if not _is_department_head(interaction.user):
            await interaction.response.send_message(
                "❌ Nur Abteilungsleiter können Abstimmungen beenden.", ephemeral=True
            )
            return

        async with _data_lock:
            result = await _get_vote(vote_id, interaction)
            if not result:
                return
            data, vote, session = result

            # Close the vote
            vote["active"] = False
            _save_data(data)

        # Build results embed
        tallies = vote["tallies"]
        total_votes = sum(tallies.values())
        total_voters = len(vote.get("votes_used", {}))

        results_lines = []
        for opt in vote["options"]:
            count = tallies.get(opt, 0)
            bar = _build_result_bar(count, total_votes)
            results_lines.append(f"**{opt}**\n{bar}")

        embed = discord.Embed(
            title=f"📊 Ergebnis – Abstimmung #{vote_id}: {vote['title']}",
            description=(
                f"**Abteilung:** {session.get('department', 'Unbekannt')}\n\n"
                + "\n".join(results_lines)
                + f"\n\n**Gesamt:** {total_votes} Stimmen von {total_voters} Wählern"
            ),
            color=discord.Color.green(),
            timestamp=datetime.now(ZoneInfo("Europe/Berlin")),
        )
        embed.set_footer(text=f"Geschlossen von {interaction.user.display_name}")

        await interaction.response.send_message(embed=embed)

        # Try to update the original vote message to show it's closed
        try:
            channel = self.bot.get_channel(vote["channel_id"])
            if channel:
                original_msg = await channel.fetch_message(vote["message_id"])
                closed_embed = original_msg.embeds[0] if original_msg.embeds else discord.Embed()
                closed_embed.description = closed_embed.description.replace(
                    "**Status:** 🟢 Offen", "**Status:** 🔴 Geschlossen"
                )
                closed_embed.color = discord.Color.red()
                # Remove the button by setting view to empty
                await original_msg.edit(embed=closed_embed, view=None)
        except Exception:
            logger.exception("Failed to update original vote message for vote #%s.", vote_id)

        logger.info("Vote #%s closed by %s.", vote_id, interaction.user)

    # -----------------------------------------------------------------------
    # /session end
    # -----------------------------------------------------------------------
    @session_group.command(name="end", description="Beende eine gesamte Wahlsitzung")
    @app_commands.describe(session_id="ID der Wahlsitzung")
    async def session_end(self, interaction: discord.Interaction, session_id: int):
        if not _is_department_head(interaction.user):
            await interaction.response.send_message(
                "❌ Nur Abteilungsleiter können Wahlsitzungen beenden.", ephemeral=True
            )
            return

        async with _data_lock:
            result = await _get_active_session(session_id, interaction)
            if not result:
                return
            data, session, sid = result

            # Check for open votes – refuse if any remain
            open_votes = [
                vid for vid, v in data["votes"].items()
                if str(v.get("session_id")) == sid and v.get("active")
            ]
            if open_votes:
                votes_str = ", ".join(f"#{v}" for v in open_votes)
                await interaction.response.send_message(
                    f"❌ Es gibt noch offene Abstimmungen: {votes_str}\n"
                    f"Bitte schließe diese zuerst mit `/vote close`.",
                    ephemeral=True,
                )
                return

            # Close the session
            session["active"] = False
            _save_data(data)

        await interaction.response.send_message(
            f"✅ Wahlsitzung #{session_id} beendet."
        )

        logger.info("Session #%s ended by %s.", sid, interaction.user)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot):
    await bot.add_cog(VotingCog(bot))
