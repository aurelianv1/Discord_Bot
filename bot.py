"""Punctul de intrare al botului: configurare, baza de date, încărcarea cogs și sincronizarea comenzilor."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "bot.db"

EXTENSIONS: tuple[str, ...] = (
    "cogs.xp",
    "cogs.music",
    "cogs.utility",
)

log = logging.getLogger("bot")


class DiscordBot(commands.Bot):
    db: aiosqlite.Connection

    def __init__(self, guild_id: int | None = None, db_path: Path | str = DB_PATH) -> None:
        intents = discord.Intents.none()
        intents.guilds = True          # canale, roluri, informații despre server
        intents.guild_messages = True  # evenimentul on_message pentru XP (fără conținutul mesajelor)
        intents.voice_states = True    # cine e pe ce canal de voce (muzică)
        intents.members = True         # privilegiat: necesar pentru mesajul de bun venit

        super().__init__(
            command_prefix=commands.when_mentioned,  # botul folosește doar slash commands
            intents=intents,
            help_command=None,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
        )
        self.guild_id = guild_id
        self.db_path = db_path

    async def setup_hook(self) -> None:
        await self.load_everything()
        await self.sync_commands()

    async def load_everything(self) -> None:
        """Deschide baza de date și încarcă extensiile (fără rețea, util și în teste)."""
        self.db = await aiosqlite.connect(self.db_path)
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("PRAGMA journal_mode=WAL")
        self.tree.on_error = self.on_app_command_error
        for extension in EXTENSIONS:
            await self.load_extension(extension)
            log.info("Extensie încărcată: %s", extension)

    async def sync_commands(self) -> None:
        if self.guild_id:
            guild = discord.Object(id=self.guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("%d comenzi sincronizate pe serverul %s (instant).", len(synced), self.guild_id)
        else:
            synced = await self.tree.sync()
            log.info("%d comenzi sincronizate global (pot apărea cu întârziere).", len(synced))

    async def on_ready(self) -> None:
        assert self.user is not None
        log.info("Conectat ca %s (ID %s) pe %d servere.", self.user, self.user.id, len(self.guilds))

    async def close(self) -> None:
        await super().close()
        if hasattr(self, "db"):
            await self.db.close()

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.CommandOnCooldown):
            message = f"Încetinește puțin! Încearcă din nou peste {error.retry_after:.0f} secunde."
        elif isinstance(error, app_commands.MissingPermissions):
            message = "Nu ai permisiunile necesare pentru această comandă."
        elif isinstance(error, app_commands.BotMissingPermissions):
            missing = ", ".join(error.missing_permissions)
            message = f"Îmi lipsesc permisiunile necesare: `{missing}`."
        elif isinstance(error, app_commands.NoPrivateMessage):
            message = "Această comandă poate fi folosită doar pe un server."
        elif type(error) is app_commands.CheckFailure:
            # Verificările proprii (ex. cele de la muzică) au deja mesajul în română.
            message = str(error) or "Nu poți folosi această comandă acum."
        else:
            command = interaction.command.qualified_name if interaction.command else "?"
            log.exception("Eroare în comanda /%s", command, exc_info=error)
            message = "A apărut o eroare neașteptată. Încearcă din nou mai târziu."

        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass


def main() -> None:
    load_dotenv(BASE_DIR / ".env")
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        sys.exit("Lipsește DISCORD_TOKEN. Copiază .env.example în .env și completează tokenul.")

    raw_guild_id = os.getenv("GUILD_ID", "").strip()
    guild_id: int | None = None
    if raw_guild_id:
        if not raw_guild_id.isdigit():
            sys.exit("GUILD_ID trebuie să fie un număr (ID-ul serverului) sau gol.")
        guild_id = int(raw_guild_id)

    bot = DiscordBot(guild_id=guild_id)
    try:
        bot.run(token, root_logger=True)
    except discord.LoginFailure:
        sys.exit("Tokenul este invalid. Generează altul din Developer Portal (Bot → Reset Token).")
    except discord.PrivilegedIntentsRequired:
        sys.exit(
            "Activează „Server Members Intent” din Developer Portal (Bot → Privileged Gateway Intents)."
        )


if __name__ == "__main__":
    main()
