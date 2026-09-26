"""Department-scoped team roles and channels."""

import asyncio
import json
import logging
import re
import unicodedata

import discord
from discord import app_commands
from discord.ext import commands

from config import GUILD_ID, TEAM_DEPARTMENTS, TEAMS_FILE
from utils.persistence import atomic_write_json

logger = logging.getLogger("munich_esports_bot.department.teams")
DEPARTMENT_CHOICES = [app_commands.Choice(name=key, value=key) for key in TEAM_DEPARTMENTS]


class TeamError(Exception):
    """An actionable validation error for the command caller."""


def team_names(department: str, name: str) -> tuple[str, str, list[str]]:
    """Return the normalized team name, role name, and ordered text-channel names."""
    name = " ".join(unicodedata.normalize("NFKC", name).split())
    if not name or any(unicodedata.category(c).startswith("C") for c in name):
        raise TeamError("Teamname darf nicht leer sein oder Steuerzeichen enthalten.")
    if name.casefold() in {"main", "academy"}:
        name = name.capitalize()
        role = f"{department} {name} Team"
    else:
        role = f"{department} Team {name}"
    slug = re.sub(r"[^\w]+", "-", name.casefold()).strip("-_")
    texts = [slug]
    if department == "VAL":
        texts = [f"{slug}-{suffix}" for suffix in ("termine", "chat", "vods", "strats")]
    if not slug or max(map(len, [role, name, *texts])) > 100:
        raise TeamError("Teamname ist leer oder zu lang (maximal 100 Zeichen einschließlich Präfix/Suffix).")
    return name, role, texts


def load_teams() -> dict:
    try:
        data = json.loads(TEAMS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("invalid teams store")
        return data
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise TeamError("Teamdatei kann nicht sicher gelesen werden. Bitte Administration kontaktieren.") from exc


def save_teams(data: dict) -> None:
    atomic_write_json(TEAMS_FILE, data)


def separator_name(name: str) -> str:
    # Use the same normalization as the team's text channels.
    _, _, texts = team_names("CS", name)
    return f"━━━━{texts[0]}━━━━"


class TeamCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.lock = asyncio.Lock()

    team_group = app_commands.Group(name="team", description="Teams verwalten", guild_ids=[GUILD_ID])

    def _resolve_department(self, interaction: discord.Interaction, selected: str | None) -> str:
        if interaction.guild_id != GUILD_ID:
            raise TeamError("Dieser Befehl ist nur auf dem Vereinsserver verfügbar.")
        role_ids = {role.id for role in interaction.user.roles}
        allowed = [
            department for department, config in TEAM_DEPARTMENTS.items()
            if config["lead_role_id"] in role_ids
        ]
        if selected is None and len(allowed) == 1:
            return allowed[0]
        if selected in allowed:
            return selected
        if len(allowed) > 1 and selected is None:
            raise TeamError("Bitte wähle deine Abteilung über die Option department.")
        raise TeamError("Nur die zuständige Abteilungsleitung darf dieses Team verwalten.")

    async def _fetch_context(self, interaction: discord.Interaction, department: str):
        """Fetch current Discord resources and validate the configured department."""
        guild = interaction.guild
        cfg = TEAM_DEPARTMENTS[department]
        roles = await guild.fetch_roles()
        channels = await guild.fetch_channels()
        by_id = {r.id: r for r in roles}
        category = next((c for c in channels if c.id == cfg["category_id"]), None)
        lead, member = by_id.get(cfg["lead_role_id"]), by_id.get(cfg["member_role_id"])
        if not isinstance(category, discord.CategoryChannel) or lead is None or member is None:
            raise TeamError("Kategorie oder Abteilungsrollen fehlen in der Konfiguration bzw. auf Discord.")
        if member.id in {guild.id, lead.id}:
            raise TeamError("Mitgliederrolle muss eine eigene Abteilungsrolle sein.")
        me = guild.me
        if not me or not me.guild_permissions.manage_roles or not me.guild_permissions.manage_channels:
            raise TeamError("Dem Bot fehlen Rollen verwalten / Kanäle verwalten.")
        if not category.permissions_for(me).manage_channels:
            raise TeamError("Der Bot darf in der Teamkategorie keine Kanäle verwalten.")
        return guild, roles, channels, category, lead, member

    def _creation_resources(self, guild, department, roles, channels):
        """Check the anchor and shared channels before creating any resources."""
        config = TEAM_DEPARTMENTS[department]
        anchor = next((role for role in roles if role.id == config["role_anchor_id"]), None)
        bot_role_ids = {role.id for role in guild.me.roles}
        bot_position = max(role.position for role in roles if role.id in bot_role_ids)
        if anchor is None or not 0 < anchor.position < bot_position:
            raise TeamError("Die Ankerrolle fehlt oder liegt nicht unter der höchsten Botrolle.")
        by_id = {channel.id: channel for channel in channels}
        shared = []
        for channel_id in config["shared_channel_ids"]:
            channel = by_id.get(channel_id)
            if not isinstance(channel, discord.TextChannel):
                raise TeamError(f"Gemeinsamer Textkanal {channel_id} fehlt oder hat einen falschen Typ.")
            if not channel.permissions_for(guild.me).manage_roles:
                raise TeamError(f"Dem Bot fehlt Berechtigungen verwalten im Kanal {channel.name}.")
            shared.append(channel)
        return anchor, shared

    def _resolve_team(self, data, department, name, roles, channels, category):
        """Find a stored team or match an existing team without modifying it."""
        name, role_name, text_names = team_names(department, name)
        records = [
            (key, record) for key, record in data.items()
            if record["department"] == department and record["name"].casefold() == name.casefold()
        ]
        if len(records) > 1:
            raise TeamError("Mehrdeutige gespeicherte Teams. Bitte Administration kontaktieren.")
        if records:
            return records[0]

        def unique(objects, expected, kind):
            found = [o for o in objects if isinstance(o, kind) and o.name.casefold() == expected.casefold()]
            if len(found) != 1:
                raise TeamError(f"Bestehendes Team nicht eindeutig/vollständig: {expected}. Bitte manuell prüfen.")
            return found[0].id

        scoped = [c for c in channels if getattr(c, "category_id", None) == category.id]
        record = {
            "department": department,
            "name": name,
            "role": unique(roles, role_name, discord.Role),
            "texts": [unique(scoped, text_name, discord.TextChannel) for text_name in text_names],
            "voice": unique(scoped, name, discord.VoiceChannel),
        }
        if department == "VAL":
            record["separator"] = unique(scoped, separator_name(name), discord.TextChannel)
        used = {
            resource_id for team in data.values()
            for resource_id in [team["role"], team["voice"], team.get("separator"), *team["texts"]]
            if resource_id is not None
        }
        if used.intersection([record["role"], record["voice"], record.get("separator"), *record["texts"]]):
            raise TeamError("Diese Ressourcen gehören bereits zu einem gespeicherten Team.")
        return str(record["role"]), record

    def _team_resources(self, record, roles, channels, category, guild, *, allow_missing=False):
        """Validate the exact targets; deletion may resume after a partial failure."""
        role = next((r for r in roles if r.id == record["role"]), None)
        protected = {guild.id}
        for config in TEAM_DEPARTMENTS.values():
            protected.update((config["lead_role_id"], config["member_role_id"], config["role_anchor_id"]))
        if role and (role.id in protected or role.managed or role.permissions.value
                     or role.position >= guild.me.top_role.position):
            raise TeamError("Die Teamrolle hat Rechte, ist geschützt oder steht oberhalb der Botrolle.")
        result = []
        expected_channels = [
            (channel_id, discord.TextChannel) for channel_id in record["texts"]
        ]
        expected_channels.append((record["voice"], discord.VoiceChannel))
        if record["department"] == "VAL":
            expected_channels.append((record["separator"], discord.TextChannel))
        channels_by_id = {channel.id: channel for channel in channels}
        for channel_id, kind in expected_channels:
            channel = channels_by_id.get(channel_id)
            if channel is None and allow_missing:
                continue
            if not isinstance(channel, kind) or channel.category_id != category.id:
                raise TeamError("Teamkanal fehlt, wurde verschoben oder hat einen falschen Typ. Bitte manuell prüfen.")
            if not channel.permissions_for(guild.me).manage_channels:
                raise TeamError("Dem Bot fehlt Kanäle verwalten in einem Teamkanal.")
            result.append(channel)
        text_count = 4 if record["department"] == "VAL" else 1
        if not allow_missing and (role is None or len(record["texts"]) != text_count):
            raise TeamError("Das Team ist unvollständig. Bitte Administration kontaktieren oder entfernen.")
        return role, result

    def _check_collisions(self, data, department, name, roles, channels, category, excluded=()):
        name, role_name, text_names = team_names(department, name)
        if any(r["department"] == department and r["name"].casefold() == name.casefold()
               and r["role"] not in excluded for r in data.values()):
            raise TeamError("Ein Team mit diesem Namen ist bereits gespeichert.")
        if any(r.id not in excluded and r.name.casefold() == role_name.casefold() for r in roles):
            raise TeamError("Eine gleichnamige Rolle existiert bereits.")
        channel_names = {channel_name.casefold() for channel_name in [name, *text_names]}
        if department == "VAL":
            channel_names.add(separator_name(name).casefold())
        if any(c.id not in excluded and getattr(c, "category_id", None) == category.id
               and c.name.casefold() in channel_names for c in channels):
            raise TeamError("Ein gleichnamiger Kanal existiert bereits in der Teamkategorie.")

    async def _create_team(self, data, department, name, guild, category, lead, member, shared, reason):
        name, role_name, text_names = team_names(department, name)
        # Persist intent before Discord mutations, then save each returned ID so
        # a partial team can be removed after an API failure or restart.
        key = f"{department}:{name.casefold()}"
        record = {"department": department, "name": name, "role": None, "texts": [], "voice": None}
        if department == "VAL":
            record["separator"] = None
        data[key] = record
        save_teams(data)
        role = await guild.create_role(
            name=role_name,
            colour=discord.Colour(0x8ABEFF),
            permissions=discord.Permissions.none(),
            mentionable=True,
            reason=reason,
        )
        record["role"] = role.id
        # Use a stable key: renaming must not reserve or overwrite the old name.
        data[str(role.id)] = data.pop(key)
        save_teams(data)
        # Creating a bottom role shifts the anchor's position. Fetch it again;
        # use the bulk endpoint directly so placement doesn't depend on cache events.
        anchor, _ = self._creation_resources(guild, department, await guild.fetch_roles(), shared)
        await guild.edit_role_positions(positions={role: anchor.position - 1}, reason=reason)
        for channel in shared:
            overwrite = channel.overwrites_for(role)
            overwrite.update(view_channel=True, read_message_history=True, send_messages=True)
            await channel.set_permissions(role, overwrite=overwrite, reason=reason)
        text_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            lead: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True),
        }
        if department == "VAL":
            separator_overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                lead: discord.PermissionOverwrite(view_channel=True, send_messages=False),
                role: discord.PermissionOverwrite(view_channel=True, send_messages=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True),
            }
            separator = await guild.create_text_channel(
                separator_name(name), category=category, overwrites=separator_overwrites, reason=reason
            )
            record["separator"] = separator.id
            save_teams(data)
        created_texts = []
        for text_name in text_names:
            channel = await guild.create_text_channel(
                text_name, category=category, overwrites=text_overwrites, reason=reason
            )
            record["texts"].append(channel.id)
            save_teams(data)
            created_texts.append(channel)
        if department == "VAL":
            await separator.move(before=created_texts[0], reason=reason)
        voice_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
            lead: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
            role: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
            member: discord.PermissionOverwrite(view_channel=True, connect=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True, connect=True),
        }
        channel = await guild.create_voice_channel(
            name, category=category, overwrites=voice_overwrites, reason=reason
        )
        record["voice"] = channel.id
        save_teams(data)
        return record

    async def _rename_team(self, data, record, role, channels, new_name, reason):
        name, role_name, text_names = team_names(record["department"], new_name)
        await role.edit(name=role_name, reason=reason)
        targets = [*text_names, name]
        if record["department"] == "VAL":
            targets.append(separator_name(name))
        for channel, target in zip(channels, targets):
            await channel.edit(name=target, reason=reason)
        # Keep the old lookup name until every edit succeeds, allowing retries.
        record["name"] = name
        save_teams(data)

    async def execute(self, interaction, action, name, department=None, new_name=None, expected=None):
        await interaction.response.defer(ephemeral=True)
        try:
            async with self.lock:
                department = self._resolve_department(interaction, department)
                guild, roles, channels, category, lead, member = await self._fetch_context(interaction, department)
                data = load_teams()
                audit_action = "delete" if action == "confirm-delete" else action
                reason = f"/team {audit_action}: {interaction.user.id}, {department}, {name}"
                if action == "create":
                    self._check_collisions(data, department, name, roles, channels, category)
                    _, shared = self._creation_resources(guild, department, roles, channels)
                    record = await self._create_team(
                        data, department, name, guild, category, lead, member, shared, reason
                    )
                else:
                    key, record = self._resolve_team(data, department, name, roles, channels, category)
                    if expected is not None and record != expected:
                        raise TeamError("Das Team wurde inzwischen geändert. Bitte Befehl erneut ausführen.")
                    role, team_channels = self._team_resources(
                        record, roles, channels, category, guild,
                        allow_missing=action in {"delete", "confirm-delete"},
                    )
                    if action == "delete":
                        view = DeleteTeamView(self, interaction.user.id, department, record.copy())
                        targets = [f"Rolle: {role.name} ({role.id})"] if role else []
                        targets += [f"Kanal: {c.name} ({c.id})" for c in team_channels]
                        await interaction.followup.send(
                            "Diese Ressourcen endgültig löschen? Kanalverläufe gehen verloren.\n"
                            + "\n".join(targets),
                            view=view,
                            ephemeral=True,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                        return
                    if action == "rename":
                        self._check_collisions(
                            data, department, new_name, roles, channels, category,
                            excluded=[record["role"], record["voice"], record.get("separator"), *record["texts"]],
                        )
                    data[key] = record
                    save_teams(data)  # Adopt exact IDs before editing/deleting anything.
                    if action == "rename":
                        await self._rename_team(data, record, role, team_channels, new_name, reason)
                    elif action == "confirm-delete":
                        for channel in team_channels:
                            await channel.delete(reason=reason)
                        if role:
                            await role.delete(reason=reason)
                        del data[key]
                        save_teams(data)
                logger.info(
                    "Team action=%s department=%s name=%s actor=%s record=%s",
                    action,
                    department,
                    name,
                    interaction.user.id,
                    record,
                )
            success = {
                "create": "erstellt",
                "rename": "umbenannt",
                "confirm-delete": "entfernt",
            }[action]
            await interaction.followup.send(f"✅ Team wurde {success}.", ephemeral=True)
        except TeamError as exc:
            await interaction.followup.send(
                f"❌ {exc}",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.HTTPException, OSError):
            logger.exception(
                "Team operation failed: action=%s department=%s name=%s actor=%s",
                action,
                department,
                name,
                interaction.user.id,
            )
            await interaction.followup.send(
                "❌ Vorgang nicht vollständig abgeschlossen. Bereits erfolgte Änderungen "
                "bleiben bestehen. Umbenennen/Entfernen mit dem bisherigen Teamnamen "
                "wiederholen; ein unvollständiges Erstellen zuerst mit `/team delete` "
                "aufräumen.",
                ephemeral=True,
            )

    @team_group.command(name="create", description="Teamrolle und Teamkanäle erstellen")
    @app_commands.describe(name="Teamname, z. B. Fire oder Main", department="Nur nötig bei mehreren Leitungsrollen")
    @app_commands.choices(department=DEPARTMENT_CHOICES)
    async def create(self, interaction: discord.Interaction, name: str, department: str | None = None):
        await self.execute(interaction, "create", name, department)

    @team_group.command(name="rename", description="Teamrolle und Teamkanäle umbenennen")
    @app_commands.describe(
        team="Bisheriger Teamname",
        new_name="Neuer Teamname",
        department="Nur nötig bei mehreren Leitungsrollen",
    )
    @app_commands.choices(department=DEPARTMENT_CHOICES)
    async def rename(
        self,
        interaction: discord.Interaction,
        team: str,
        new_name: str,
        department: str | None = None,
    ):
        await self.execute(interaction, "rename", team, department, new_name)

    @team_group.command(name="delete", description="Teamrolle und Teamkanäle endgültig löschen")
    @app_commands.describe(team="Zu löschender Teamname", department="Nur nötig bei mehreren Leitungsrollen")
    @app_commands.choices(department=DEPARTMENT_CHOICES)
    async def delete(self, interaction: discord.Interaction, team: str, department: str | None = None):
        await self.execute(interaction, "delete", team, department)


class DeleteTeamView(discord.ui.View):
    def __init__(self, cog, actor_id, department, record):
        super().__init__(timeout=60)
        self.cog = cog
        self.actor_id = actor_id
        self.department = department
        self.record = record
        self.used = False

    @discord.ui.button(label="Endgültig löschen", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.actor_id or self.used:
            await interaction.response.send_message(
                "Diese Bestätigung ist nicht mehr gültig oder gehört jemand anderem.",
                ephemeral=True,
            )
            return
        self.used = True
        self.stop()
        await self.cog.execute(
            interaction,
            "confirm-delete",
            self.record["name"],
            self.department,
            expected=self.record,
        )
        await interaction.edit_original_response(view=None)


async def setup(bot):
    await bot.add_cog(TeamCog(bot))
