"""Sistem de XP și nivele, separat pe fiecare server."""

from __future__ import annotations

import logging
import random
import time
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from bot import DiscordBot

log = logging.getLogger(__name__)

XP_MIN = 15
XP_MAX = 25
XP_COOLDOWN_SECONDS = 60.0
MAX_LEVEL = 1000


# --- Logică pură (testată în tests/test_levels.py) ---------------------------------

def xp_to_next_level(level: int) -> int:
    """XP necesar pentru a trece de la nivelul `level` la `level + 1`."""
    return 5 * level**2 + 50 * level + 100


def total_xp_for_level(level: int) -> int:
    """XP total necesar pentru a ajunge la nivelul `level` pornind de la 0."""
    return sum(xp_to_next_level(n) for n in range(level))


def level_progress(total_xp: int) -> tuple[int, int, int]:
    """Întoarce (nivel, XP acumulat în nivelul curent, XP necesar pentru nivelul următor)."""
    level = 0
    remaining = max(total_xp, 0)
    while level < MAX_LEVEL and remaining >= xp_to_next_level(level):
        remaining -= xp_to_next_level(level)
        level += 1
    return level, remaining, xp_to_next_level(level)


def level_from_xp(total_xp: int) -> int:
    return level_progress(total_xp)[0]


def progress_bar(current: int, needed: int, length: int = 12) -> str:
    ratio = 0.0 if needed <= 0 else min(max(current / needed, 0.0), 1.0)
    filled = int(ratio * length)
    return "▰" * filled + "▱" * (length - filled)


# --- Cog ---------------------------------------------------------------------------

class XP(commands.Cog):
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        self._cooldowns: dict[tuple[int, int], float] = {}

    async def cog_load(self) -> None:
        await self.bot.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS xp (
                guild_id INTEGER NOT NULL,
                user_id  INTEGER NOT NULL,
                xp       INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_xp_guild_xp ON xp (guild_id, xp DESC);

            CREATE TABLE IF NOT EXISTS level_roles (
                guild_id INTEGER NOT NULL,
                role_id  INTEGER NOT NULL,
                level    INTEGER NOT NULL,
                PRIMARY KEY (guild_id, role_id)
            );
            """
        )
        await self.bot.db.commit()

    # --- Acordarea XP --------------------------------------------------------------

    def _on_cooldown(self, guild_id: int, user_id: int) -> bool:
        now = time.monotonic()
        key = (guild_id, user_id)
        last = self._cooldowns.get(key)
        if last is not None and now - last < XP_COOLDOWN_SECONDS:
            return True
        self._cooldowns[key] = now
        if len(self._cooldowns) > 10_000:
            self._cooldowns = {
                k: t for k, t in self._cooldowns.items() if now - t < XP_COOLDOWN_SECONDS
            }
        return False

    async def _add_xp(self, guild_id: int, user_id: int, amount: int) -> int:
        db = self.bot.db
        await db.execute(
            """
            INSERT INTO xp (guild_id, user_id, xp) VALUES (?, ?, ?)
            ON CONFLICT (guild_id, user_id) DO UPDATE SET xp = xp + excluded.xp
            """,
            (guild_id, user_id, amount),
        )
        await db.commit()
        async with db.execute(
            "SELECT xp FROM xp WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["xp"]) if row else amount

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or message.webhook_id is not None:
            return
        if not isinstance(message.author, discord.Member):
            return
        if self._on_cooldown(message.guild.id, message.author.id):
            return

        gained = random.randint(XP_MIN, XP_MAX)
        new_xp = await self._add_xp(message.guild.id, message.author.id, gained)
        old_level = level_from_xp(new_xp - gained)
        new_level = level_from_xp(new_xp)
        if new_level <= old_level:
            return

        try:
            await message.channel.send(
                f"🎉 Felicitări, {message.author.mention}! Ai ajuns la nivelul **{new_level}**!"
            )
        except discord.HTTPException:
            log.warning("Nu pot trimite mesajul de level up în #%s", message.channel)
        await self._apply_level_roles(message.author, new_level)

    async def _apply_level_roles(self, member: discord.Member, level: int) -> None:
        async with self.bot.db.execute(
            "SELECT role_id FROM level_roles WHERE guild_id = ? AND level <= ?",
            (member.guild.id, level),
        ) as cursor:
            rows = await cursor.fetchall()

        roles = [member.guild.get_role(row["role_id"]) for row in rows]
        to_add = [role for role in roles if role is not None and role not in member.roles]
        if not to_add:
            return
        try:
            await member.add_roles(*to_add, reason=f"Rol de nivel (nivelul {level})")
        except discord.HTTPException as exc:
            log.warning("Nu pot acorda roluri de nivel lui %s: %s", member, exc)

    # --- Comenzi -------------------------------------------------------------------

    @commands.hybrid_command(name="rank", description="Arată nivelul și XP-ul unui membru")
    @app_commands.describe(membru="Membrul pentru care vrei să vezi rangul (implicit tu)")
    @commands.guild_only()
    async def rank(self, ctx: commands.Context[DiscordBot], membru: discord.Member | None = None) -> None:
        assert ctx.guild is not None
        target = membru or ctx.author
        if target.bot:
            await ctx.send("Boții nu primesc XP. 🤖", ephemeral=True)
            return

        db = self.bot.db
        async with db.execute(
            "SELECT xp FROM xp WHERE guild_id = ? AND user_id = ?", (ctx.guild.id, target.id)
        ) as cursor:
            row = await cursor.fetchone()
        total_xp = int(row["xp"]) if row else 0

        position_text = "—"
        if row:
            async with db.execute(
                "SELECT COUNT(*) + 1 AS pos FROM xp WHERE guild_id = ? AND xp > ?",
                (ctx.guild.id, total_xp),
            ) as cursor:
                pos_row = await cursor.fetchone()
            position_text = f"#{pos_row['pos']}" if pos_row else "—"

        level, current, needed = level_progress(total_xp)
        percent = int(current / needed * 100) if needed else 100

        embed = discord.Embed(title=f"Rangul lui {target.display_name}", color=discord.Color.blurple())
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Nivel", value=str(level))
        embed.add_field(name="Loc în clasament", value=position_text)
        embed.add_field(name="XP total", value=f"{total_xp:,}".replace(",", "."))
        embed.add_field(
            name=f"Progres spre nivelul {level + 1}",
            value=f"{progress_bar(current, needed)} {percent}%\n{current} / {needed} XP",
            inline=False,
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="leaderboard", aliases=["top"], description="Top 10 membri după XP pe acest server")
    @commands.guild_only()
    async def leaderboard(self, ctx: commands.Context[DiscordBot]) -> None:
        assert ctx.guild is not None
        async with self.bot.db.execute(
            "SELECT user_id, xp FROM xp WHERE guild_id = ? ORDER BY xp DESC LIMIT 10", (ctx.guild.id,)
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            await ctx.send("Nimeni nu are încă XP pe acest server. Scrieți câteva mesaje! 💬")
            return

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for index, row in enumerate(rows, start=1):
            prefix = medals.get(index, f"`#{index}`")
            lines.append(
                f"{prefix} <@{row['user_id']}> — nivel **{level_from_xp(row['xp'])}** ({row['xp']} XP)"
            )

        embed = discord.Embed(
            title=f"🏆 Clasament — {ctx.guild.name}",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        await ctx.send(embed=embed)

    # `.levelrole` singur (sau `/levelrole list`) afișează lista.
    @commands.hybrid_group(
        name="levelrole",
        fallback="list",
        invoke_without_command=True,
        description="Arată rolurile acordate automat pe nivele",
    )
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @app_commands.default_permissions(manage_roles=True)
    async def levelrole(self, ctx: commands.Context[DiscordBot]) -> None:
        assert ctx.guild is not None
        async with self.bot.db.execute(
            "SELECT role_id, level FROM level_roles WHERE guild_id = ? ORDER BY level, role_id",
            (ctx.guild.id,),
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            await ctx.send("Nu există roluri de nivel. Adaugă unul cu `.levelrole add <nivel> <rol>`.", ephemeral=True)
            return

        lines = []
        for row in rows:
            role = ctx.guild.get_role(row["role_id"])
            role_text = role.mention if role else f"*rol șters* (`{row['role_id']}`)"
            lines.append(f"Nivel **{row['level']}** → {role_text}")
        embed = discord.Embed(title="Roluri de nivel", description="\n".join(lines), color=discord.Color.green())
        await ctx.send(embed=embed, ephemeral=True)

    @levelrole.command(name="add", description="Acordă automat un rol la atingerea unui nivel")
    @app_commands.describe(nivel="Nivelul la care se acordă rolul", rol="Rolul acordat")
    @commands.has_permissions(manage_roles=True)
    async def levelrole_add(
        self, ctx: commands.Context[DiscordBot], nivel: commands.Range[int, 1, MAX_LEVEL], rol: discord.Role
    ) -> None:
        assert ctx.guild is not None
        me = ctx.guild.me
        if rol.is_default() or rol.managed:
            await ctx.send(
                "Acest rol nu poate fi acordat (e @everyone sau e gestionat de o integrare).", ephemeral=True
            )
            return
        if not me.guild_permissions.manage_roles:
            await ctx.send("Am nevoie de permisiunea **Manage Roles** ca să pot acorda roluri.", ephemeral=True)
            return
        if rol >= me.top_role:
            await ctx.send(
                f"Nu pot acorda {rol.mention}, pentru că este deasupra (sau la nivelul) celui mai "
                "înalt rol al meu. Mută rolul botului mai sus în setările serverului.",
                ephemeral=True,
            )
            return

        await self.bot.db.execute(
            """
            INSERT INTO level_roles (guild_id, role_id, level) VALUES (?, ?, ?)
            ON CONFLICT (guild_id, role_id) DO UPDATE SET level = excluded.level
            """,
            (ctx.guild.id, rol.id, nivel),
        )
        await self.bot.db.commit()
        await ctx.send(
            f"✅ {rol.mention} va fi acordat la nivelul **{nivel}**. Membrii care au deja acest "
            "nivel îl vor primi la următorul lor level up.",
            ephemeral=True,
        )

    @levelrole.command(name="remove", description="Elimină un rol din lista rolurilor de nivel")
    @app_commands.describe(rol="Rolul care nu va mai fi acordat automat")
    @commands.has_permissions(manage_roles=True)
    async def levelrole_remove(self, ctx: commands.Context[DiscordBot], rol: discord.Role) -> None:
        assert ctx.guild is not None
        cursor = await self.bot.db.execute(
            "DELETE FROM level_roles WHERE guild_id = ? AND role_id = ?", (ctx.guild.id, rol.id)
        )
        await self.bot.db.commit()
        if cursor.rowcount:
            message = f"🗑️ {rol.mention} nu mai este rol de nivel. (Membrii care îl au deja îl păstrează.)"
        else:
            message = f"{rol.mention} nu era configurat ca rol de nivel."
        await ctx.send(message, ephemeral=True)


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(XP(bot))
