"""Punctul de intrare al botului: configurare, baza de date, încărcarea cogs și sincronizarea comenzilor.

Comenzile sunt hibride: merg atât cu prefixul `.` (ex. `.ping`), cât și ca slash commands (`/ping`).
"""

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
PREFIX = "."

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
        intents.guilds = True
        intents.guild_messages = True
        intents.voice_states = True
        intents.members = True
        intents.message_content = True

        super().__init__(
            command_prefix=commands.when_mentioned_or(PREFIX),
            case_insensitive=True,
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

    async def on_command_error(self, ctx: commands.Context[DiscordBot], error: commands.CommandError) -> None:
        if isinstance(error, commands.HybridCommandError):
            original: Exception = error.original
            if isinstance(original, app_commands.CommandInvokeError):
                original = original.original
        elif isinstance(error, commands.CommandInvokeError):
            original = error.original
        else:
            original = error

        if isinstance(original, commands.CommandNotFound):
            return
        message = self._error_message(ctx, original)
        if message is None:
            command = ctx.command.qualified_name if ctx.command else "?"
            log.error("Eroare în comanda %s", command, exc_info=original)
            message = "A apărut o eroare neașteptată. Încearcă din nou mai târziu."

        try:
            await ctx.send(message, ephemeral=True)
        except discord.HTTPException:
            pass

    @staticmethod
    def _error_message(ctx: commands.Context[DiscordBot], error: BaseException) -> str | None:
        usage = ""
        if ctx.command:
            text = f"{PREFIX}{ctx.command.qualified_name} {ctx.command.signature}".strip()
            usage = f"`{text}`"
        if isinstance(error, (commands.CommandOnCooldown, app_commands.CommandOnCooldown)):
            return f"Încetinește puțin! Încearcă din nou peste {error.retry_after:.0f} secunde."
        if isinstance(error, (commands.MissingPermissions, app_commands.MissingPermissions)):
            return "Nu ai permisiunile necesare pentru această comandă."
        if isinstance(error, (commands.BotMissingPermissions, app_commands.BotMissingPermissions)):
            return f"Îmi lipsesc permisiunile necesare: `{', '.join(error.missing_permissions)}`."
        if isinstance(error, (commands.NoPrivateMessage, app_commands.NoPrivateMessage)):
            return "Această comandă poate fi folosită doar pe un server."
        if isinstance(error, commands.MissingRequiredArgument):
            return f"Lipsește `{error.param.name}`. Folosire: {usage}"
        if isinstance(error, commands.TooManyArguments):
            return f"Prea multe argumente. Folosire: {usage}"
        if isinstance(error, commands.MemberNotFound):
            return f"Nu am găsit membrul `{error.argument}`."
        if isinstance(error, commands.UserNotFound):
            return f"Nu am găsit utilizatorul `{error.argument}`."
        if isinstance(error, commands.RoleNotFound):
            return f"Nu am găsit rolul `{error.argument}`."
        if isinstance(error, commands.ChannelNotFound):
            return f"Nu am găsit canalul `{error.argument}`."
        if isinstance(error, commands.RangeError):
            unit = "caractere" if isinstance(error.value, str) else "valoare"
            return f"Valoare în afara limitelor ({unit}: între {error.minimum} și {error.maximum}). Folosire: {usage}"
        if isinstance(error, commands.BadLiteralArgument):
            options = ", ".join(f"`{literal}`" for literal in error.literals)
            return f"Valoare invalidă pentru `{error.param.name}`. Opțiuni: {options}."
        if isinstance(error, (commands.BadArgument, commands.ArgumentParsingError)):
            return f"Argument invalid. Folosire: {usage}"
        if type(error) in (commands.CheckFailure, app_commands.CheckFailure):
            return str(error) or "Nu poți folosi această comandă acum."
        return None


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
            "Activează „Server Members Intent” și „Message Content Intent” din Developer Portal "
            "(Bot → Privileged Gateway Intents)."
        )


if __name__ == "__main__":
    main()
