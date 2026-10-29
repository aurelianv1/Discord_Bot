"""Comenzi utilitare: bun venit, informații, sondaje, distracție și remindere."""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

if TYPE_CHECKING:
    from bot import DiscordBot

log = logging.getLogger(__name__)

PREFIX = "."
POLL_EMOJIS = ("1️⃣", "2️⃣", "3️⃣", "4️⃣")
HELP_SECTIONS = (("XP", "⭐ XP și nivele"), ("Music", "🎵 Muzică"), ("Utility", "🛠️ Utilitare"))
MAX_REMIND_MINUTES = 7 * 24 * 60

EIGHT_BALL_ANSWERS = (
    "Sigur că da.",
    "Fără îndoială.",
    "Da, categoric.",
    "Poți conta pe asta.",
    "Așa cum văd eu, da.",
    "Cel mai probabil.",
    "Perspectivele sunt bune.",
    "Da.",
    "Semnele spun că da.",
    "Neclar, mai încearcă.",
    "Întreabă-mă mai târziu.",
    "Mai bine să nu-ți spun acum.",
    "Nu pot prezice asta acum.",
    "Concentrează-te și întreabă din nou.",
    "Nu conta pe asta.",
    "Răspunsul meu este nu.",
    "Sursele mele spun că nu.",
    "Perspectivele nu sunt prea bune.",
    "Foarte îndoielnic.",
)


def _date_with_relative(moment: datetime) -> str:
    return f"{discord.utils.format_dt(moment, 'D')} ({discord.utils.format_dt(moment, 'R')})"


def _chunk_lines(lines: list[str], limit: int) -> list[str]:
    """Grupează liniile în bucăți de cel mult `limit` caractere (limita unui câmp de embed)."""
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


class Utility(commands.Cog):
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        await self.bot.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS welcome_channels (
                guild_id   INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reminders (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                due_at     REAL    NOT NULL,
                text       TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders (due_at);
            """
        )
        await self.bot.db.commit()
        self.deliver_reminders.start()

    async def cog_unload(self) -> None:
        self.deliver_reminders.cancel()

    # --- Bun venit -----------------------------------------------------------------

    @commands.hybrid_command(name="setwelcome", description="Setează canalul pentru mesajele de bun venit")
    @app_commands.describe(canal="Canalul în care salut membrii noi")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    async def setwelcome(self, ctx: commands.Context[DiscordBot], canal: discord.TextChannel) -> None:
        assert ctx.guild is not None
        permissions = canal.permissions_for(ctx.guild.me)
        if not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
            await ctx.send(
                f"Nu pot trimite mesaje (cu embed-uri) în {canal.mention}. Verifică permisiunile mele.",
                ephemeral=True,
            )
            return
        await self.bot.db.execute(
            """
            INSERT INTO welcome_channels (guild_id, channel_id) VALUES (?, ?)
            ON CONFLICT (guild_id) DO UPDATE SET channel_id = excluded.channel_id
            """,
            (ctx.guild.id, canal.id),
        )
        await self.bot.db.commit()
        await ctx.send(f"👋 Membrii noi vor fi salutați în {canal.mention}.", ephemeral=True)

    @commands.hybrid_command(name="welcomeoff", description="Dezactivează mesajele de bun venit")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    async def welcomeoff(self, ctx: commands.Context[DiscordBot]) -> None:
        assert ctx.guild is not None
        cursor = await self.bot.db.execute("DELETE FROM welcome_channels WHERE guild_id = ?", (ctx.guild.id,))
        await self.bot.db.commit()
        message = (
            "🔕 Mesajele de bun venit au fost dezactivate."
            if cursor.rowcount
            else "Mesajele de bun venit nu erau activate."
        )
        await ctx.send(message, ephemeral=True)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        async with self.bot.db.execute(
            "SELECT channel_id FROM welcome_channels WHERE guild_id = ?", (member.guild.id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return
        channel = member.guild.get_channel(row["channel_id"])
        if not isinstance(channel, discord.TextChannel):
            return

        embed = discord.Embed(
            title=f"Bun venit pe {member.guild.name}! 🎉",
            description=f"Ne bucurăm că ești aici, {member.mention}!\n"
            f"Ești membrul cu numărul **{member.guild.member_count}**.",
            color=discord.Color.green(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            await channel.send(content=member.mention, embed=embed)
        except discord.HTTPException as exc:
            log.warning("Nu pot trimite mesajul de bun venit pe %s: %s", member.guild, exc)

    # --- Ajutor --------------------------------------------------------------------

    @commands.hybrid_command(name="help", aliases=["ajutor", "comenzi"], description="Arată lista comenzilor")
    async def help(self, ctx: commands.Context[DiscordBot]) -> None:
        embed = discord.Embed(
            title="📖 Comenzile botului",
            description=f"Le poți folosi cu `{PREFIX}` sau cu `/` — de exemplu `{PREFIX}ping` sau `/ping`.\n"
            f"`<...>` = obligatoriu, `[...]` = opțional.",
            color=discord.Color.blurple(),
        )
        for cog_name, label in HELP_SECTIONS:
            cog = self.bot.get_cog(cog_name)
            if cog is None:
                continue
            lines = []
            for command in cog.walk_commands():
                if command.hidden:
                    continue
                usage = f"{PREFIX}{command.qualified_name} {command.signature}".strip()
                lines.append(f"`{usage}` — {command.description}")
            for index, chunk in enumerate(_chunk_lines(lines, 1024)):
                embed.add_field(name=label if index == 0 else "\u200b", value=chunk, inline=False)
        await ctx.send(embed=embed, ephemeral=True)

    # --- Informații ----------------------------------------------------------------

    @commands.hybrid_command(name="ping", description="Verifică latența botului")
    async def ping(self, ctx: commands.Context[DiscordBot]) -> None:
        await ctx.send(f"🏓 Pong! Latență: **{self.bot.latency * 1000:.0f} ms**")

    @commands.hybrid_command(name="avatar", aliases=["av"], description="Arată avatarul unui utilizator")
    @app_commands.describe(membru="Utilizatorul (implicit tu)")
    async def avatar(self, ctx: commands.Context[DiscordBot], membru: discord.User | None = None) -> None:
        target = membru or ctx.author
        asset = target.display_avatar
        links = " • ".join(
            f"[{fmt.upper()}]({asset.replace(format=fmt, size=1024).url})" for fmt in ("png", "jpg", "webp")
        )
        embed = discord.Embed(
            title=f"Avatarul lui {target.display_name}", description=links, color=discord.Color.blurple()
        )
        embed.set_image(url=asset.replace(size=1024).url)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="userinfo", aliases=["ui"], description="Informații despre un membru")
    @app_commands.describe(membru="Membrul (implicit tu)")
    @commands.guild_only()
    async def userinfo(self, ctx: commands.Context[DiscordBot], membru: discord.Member | None = None) -> None:
        target = membru or ctx.author
        assert isinstance(target, discord.Member)

        roles = [role.mention for role in reversed(target.roles) if not role.is_default()]
        roles_text = ", ".join(roles[:15]) if roles else "niciunul"
        if len(roles) > 15:
            roles_text += f" și încă {len(roles) - 15}"

        embed = discord.Embed(
            title=str(target),
            color=target.color if target.color.value else discord.Color.blurple(),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Nume pe server", value=target.display_name)
        embed.add_field(name="ID", value=str(target.id))
        embed.add_field(name="Tip", value="🤖 Bot" if target.bot else "👤 Utilizator")
        embed.add_field(name="Cont creat", value=_date_with_relative(target.created_at))
        if target.joined_at:
            embed.add_field(name="A intrat pe server", value=_date_with_relative(target.joined_at))
        embed.add_field(name=f"Roluri ({len(roles)})", value=roles_text[:1024], inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="serverinfo", aliases=["si"], description="Informații despre acest server")
    @commands.guild_only()
    async def serverinfo(self, ctx: commands.Context[DiscordBot]) -> None:
        guild = ctx.guild
        assert guild is not None

        embed = discord.Embed(title=guild.name, description=guild.description, color=discord.Color.blurple())
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Proprietar", value=f"<@{guild.owner_id}>")
        embed.add_field(name="ID", value=str(guild.id))
        embed.add_field(name="Creat", value=_date_with_relative(guild.created_at))
        embed.add_field(name="Membri", value=str(guild.member_count or len(guild.members)))
        embed.add_field(
            name="Canale", value=f"💬 {len(guild.text_channels)} text • 🔊 {len(guild.voice_channels)} voce"
        )
        embed.add_field(name="Roluri", value=str(len(guild.roles) - 1))
        embed.add_field(
            name="Boost-uri", value=f"Nivel {guild.premium_tier} ({guild.premium_subscription_count or 0} boost-uri)"
        )
        await ctx.send(embed=embed)

    # --- Sondaje -------------------------------------------------------------------

    @commands.hybrid_command(
        name="poll",
        description="Creează un sondaj cu 2–4 opțiuni",
        usage='"<întrebare>" "<opțiune1>" "<opțiune2>" ["opțiune3"] ["opțiune4"]',
    )
    @app_commands.describe(
        intrebare="Întrebarea sondajului",
        optiune1="Prima opțiune",
        optiune2="A doua opțiune",
        optiune3="A treia opțiune (opțional)",
        optiune4="A patra opțiune (opțional)",
    )
    @commands.guild_only()
    async def poll(
        self,
        ctx: commands.Context[DiscordBot],
        intrebare: commands.Range[str, 1, 256],
        optiune1: commands.Range[str, 1, 100],
        optiune2: commands.Range[str, 1, 100],
        optiune3: commands.Range[str, 1, 100] | None = None,
        optiune4: commands.Range[str, 1, 100] | None = None,
    ) -> None:
        options = [opt for opt in (optiune1, optiune2, optiune3, optiune4) if opt]
        description = "\n\n".join(f"{POLL_EMOJIS[i]} {opt}" for i, opt in enumerate(options))
        embed = discord.Embed(title=f"📊 {intrebare}", description=description, color=discord.Color.orange())
        embed.set_footer(text=f"Sondaj creat de {ctx.author.display_name} • votează cu reacțiile de mai jos")
        message = await ctx.send(embed=embed)
        try:
            for emoji in POLL_EMOJIS[: len(options)]:
                await message.add_reaction(emoji)
        except discord.HTTPException:
            await ctx.send(
                "Nu am putut adăuga reacțiile. Am nevoie de permisiunile **Add Reactions** și "
                "**Read Message History** în acest canal.",
                ephemeral=True,
            )

    # --- Distracție ----------------------------------------------------------------

    @commands.hybrid_command(name="8ball", description="Pune o întrebare globului magic")
    @app_commands.describe(intrebare="Întrebarea ta")
    async def eight_ball(self, ctx: commands.Context[DiscordBot], *, intrebare: commands.Range[str, 1, 256]) -> None:
        await ctx.send(f"❓ **{intrebare}**\n🎱 {random.choice(EIGHT_BALL_ANSWERS)}")

    @commands.hybrid_command(name="coinflip", description="Dă cu banul")
    async def coinflip(self, ctx: commands.Context[DiscordBot]) -> None:
        result = random.choice(("Cap", "Pajură"))
        await ctx.send(f"🪙 A ieșit **{result}**!")

    @commands.hybrid_command(name="dice", aliases=["zar"], description="Dă cu zarul")
    @app_commands.describe(fete="Numărul de fețe ale zarului (implicit 6)", numar="Câte zaruri (implicit 1)")
    async def dice(
        self,
        ctx: commands.Context[DiscordBot],
        fete: commands.Range[int, 2, 1000] = 6,
        numar: commands.Range[int, 1, 10] = 1,
    ) -> None:
        rolls = [random.randint(1, fete) for _ in range(numar)]
        if numar == 1:
            text = f"🎲 Ai dat **{rolls[0]}** (zar cu {fete} fețe)."
        else:
            text = f"🎲 {numar} zaruri cu {fete} fețe: {', '.join(map(str, rolls))} — total **{sum(rolls)}**."
        await ctx.send(text)

    # --- Remindere -----------------------------------------------------------------

    @commands.hybrid_command(name="remind", aliases=["reminder"], description="Îți amintesc ceva peste un număr de minute")
    @app_commands.describe(minute="Peste câte minute (maxim 7 zile)", text="Ce să-ți amintesc")
    async def remind(
        self,
        ctx: commands.Context[DiscordBot],
        minute: commands.Range[int, 1, MAX_REMIND_MINUTES],
        *,
        text: commands.Range[str, 1, 500],
    ) -> None:
        due_at = time.time() + minute * 60
        await self.bot.db.execute(
            "INSERT INTO reminders (user_id, channel_id, due_at, text) VALUES (?, ?, ?, ?)",
            (ctx.author.id, ctx.channel.id, due_at, text),
        )
        await self.bot.db.commit()
        when = discord.utils.format_dt(datetime.fromtimestamp(due_at, tz=timezone.utc), "R")
        await ctx.send(f"⏰ Gata! Îți amintesc {when}: {text}", ephemeral=True)

    @tasks.loop(seconds=10)
    async def deliver_reminders(self) -> None:
        # O excepție neprinsă ar opri bucla definitiv, așa că o prindem și reîncercăm la următorul tur.
        try:
            async with self.bot.db.execute(
                "SELECT id, user_id, channel_id, text FROM reminders WHERE due_at <= ? ORDER BY due_at",
                (time.time(),),
            ) as cursor:
                rows = await cursor.fetchall()

            for row in rows:
                await self._send_reminder(row["user_id"], row["channel_id"], row["text"])
                await self.bot.db.execute("DELETE FROM reminders WHERE id = ?", (row["id"],))
                await self.bot.db.commit()
        except Exception:
            log.exception("Eroare în bucla de remindere")

    @deliver_reminders.before_loop
    async def _before_reminders(self) -> None:
        await self.bot.wait_until_ready()

    async def _send_reminder(self, user_id: int, channel_id: int, text: str) -> None:
        content = f"⏰ <@{user_id}>, îți amintesc: {text}"
        # Pingăm doar persoana care a cerut reminder-ul, chiar dacă textul conține alte mențiuni.
        mentions = discord.AllowedMentions(everyone=False, roles=False, users=[discord.Object(user_id)])
        try:
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            if isinstance(channel, discord.abc.Messageable):
                await channel.send(content, allowed_mentions=mentions)
                return
        except discord.HTTPException:
            pass
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            await user.send(f"⏰ Îți amintesc: {text}")
        except discord.HTTPException:
            log.warning("Nu am putut livra reminder-ul pentru utilizatorul %s", user_id)


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Utility(bot))
