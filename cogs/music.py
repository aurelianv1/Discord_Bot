"""Muzică de pe YouTube cu yt-dlp + FFmpeg, cu o coadă separată pe fiecare server."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from bot import DiscordBot

log = logging.getLogger(__name__)

IDLE_TIMEOUT = 300          # secunde fără nimic în coadă până la ieșirea de pe canal
ALONE_TIMEOUT = 30          # secunde de așteptare când botul rămâne singur pe canal
INFO_MAX_AGE = 1800         # linkurile de stream YouTube expiră; după 30 min extragem din nou
MAX_QUEUE_SIZE = 100

# YouTube cere un runtime JavaScript; yt-dlp îl folosește pe primul găsit dintre acestea.
JS_RUNTIMES = ("deno", "node", "bun")

YTDL_OPTIONS: dict[str, Any] = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "default_search": "ytsearch",
    "js_runtimes": {runtime: {} for runtime in JS_RUNTIMES},
}
# Pentru redare, yt-dlp descarcă el însuși streamul (cu headerele și reîncercările de care
# YouTube are nevoie) și îl trimite prin pipe către FFmpeg. Dacă FFmpeg accesează direct
# linkul de stream, YouTube răspunde des cu 403 Forbidden.
YTDL_STREAM_ARGS: tuple[str, ...] = (
    "--quiet",
    "--no-warnings",
    "--no-playlist",
    "--format", "bestaudio/best",
    *(arg for runtime in JS_RUNTIMES for arg in ("--js-runtimes", runtime)),
    "--output", "-",
)
# Câmpuri mari de care nu avem nevoie la redare (păstrăm coada mică în memorie).
_UNUSED_INFO_KEYS = ("automatic_captions", "subtitles", "heatmap", "thumbnails", "chapters")

# Referințe la task-urile lansate „în fundal”, ca să nu fie colectate de garbage collector.
_background_tasks: set[asyncio.Task[None]] = set()


class NoResultsError(Exception):
    pass


def _extract_sync(query: str) -> dict[str, Any]:
    with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:
        info = ydl.extract_info(query, download=False)
        if info and "entries" in info:
            entries = [entry for entry in info["entries"] or [] if entry]
            if not entries:
                raise NoResultsError(query)
            info = entries[0]
        if not info or not info.get("url"):
            raise NoResultsError(query)
        info = ydl.sanitize_info(info)
    for key in _UNUSED_INFO_KEYS:
        info.pop(key, None)
    return info


async def extract_info(query: str) -> dict[str, Any]:
    """Rulează yt-dlp într-un thread separat, ca să nu blocheze event loop-ul."""
    if not query.startswith(("http://", "https://")):
        query = f"ytsearch1:{query}"
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _extract_sync, query)


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "🔴 LIVE"
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"


@dataclass
class Track:
    title: str
    webpage_url: str
    info_json: str
    duration: int | None
    thumbnail: str | None
    uploader: str | None
    requester: discord.abc.User
    extracted_at: float = field(default_factory=time.monotonic)
    announced: bool = False

    @classmethod
    def from_info(cls, info: dict[str, Any], requester: discord.abc.User) -> Track:
        duration = info.get("duration")
        return cls(
            title=info.get("title") or "Titlu necunoscut",
            webpage_url=info.get("webpage_url") or info.get("original_url") or info["url"],
            info_json=json.dumps(info),
            duration=None if info.get("is_live") or duration is None else int(duration),
            thumbnail=info.get("thumbnail"),
            uploader=info.get("uploader") or info.get("channel"),
            requester=requester,
        )

    def embed(self, heading: str) -> discord.Embed:
        embed = discord.Embed(
            title=self.title, url=self.webpage_url, description=heading, color=discord.Color.red()
        )
        embed.add_field(name="Durată", value=format_duration(self.duration))
        embed.add_field(name="Cerută de", value=self.requester.mention)
        if self.uploader:
            embed.add_field(name="Canal", value=self.uploader)
        if self.thumbnail:
            embed.set_thumbnail(url=self.thumbnail)
        return embed


class YTDLAudio(discord.FFmpegPCMAudio):
    """Sursă audio: stdout-ul lui yt-dlp devine direct stdin-ul lui FFmpeg."""

    def __init__(self, track: Track) -> None:
        self._info_path: str | None = None
        self._cleaned = False
        self._exit_code: int | None = None  # codul yt-dlp văzut înainte să oprim noi ceva
        args = [sys.executable, "-m", "yt_dlp", *YTDL_STREAM_ARGS]
        if time.monotonic() - track.extracted_at < INFO_MAX_AGE:
            # Refolosim informațiile de la /play, ca redarea să pornească mai repede.
            fd, self._info_path = tempfile.mkstemp(prefix="discord-bot-", suffix=".info.json")
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(track.info_json)
            args += ["--load-info-json", self._info_path]
        else:
            args += ["--", track.webpage_url]

        self._downloader = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
        try:
            super().__init__("pipe:0", options="-vn")
        except Exception:
            self._stop_downloader()
            raise
        finally:
            # FFmpeg are propria copie a pipe-ului; o închidem pe a noastră, ca yt-dlp
            # să se oprească singur dacă FFmpeg se închide.
            assert self._downloader.stdout is not None
            self._downloader.stdout.close()

    def _spawn_process(self, args: Any, **subprocess_kwargs: Any) -> subprocess.Popen[bytes]:
        subprocess_kwargs["stdin"] = self._downloader.stdout
        return super()._spawn_process(args, **subprocess_kwargs)

    @property
    def download_failed(self) -> bool:
        code = self._exit_code if self._cleaned else self._downloader.poll()
        return code not in (None, 0)

    def _stop_downloader(self) -> None:
        if self._downloader.poll() is None:
            self._downloader.kill()
        try:
            self._downloader.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.warning("Procesul yt-dlp %s nu s-a oprit la timp", self._downloader.pid)
        if self._info_path:
            try:
                os.unlink(self._info_path)
            except OSError:
                pass
            self._info_path = None

    def cleanup(self) -> None:
        if not self._cleaned:
            self._exit_code = self._downloader.poll()
            self._cleaned = True
        super().cleanup()
        self._stop_downloader()


class LoopMode(Enum):
    OFF = "oprit"
    TRACK = "melodia curentă"
    QUEUE = "toată coada"


class GuildPlayer:
    """Coada și bucla de redare pentru un singur server."""

    def __init__(self, cog: Music, guild: discord.Guild, text_channel: discord.abc.Messageable) -> None:
        self.cog = cog
        self.guild = guild
        self.text_channel = text_channel
        self.queue: deque[Track] = deque()
        self.current: Track | None = None
        self.loop_mode = LoopMode.OFF
        self.volume = 0.5
        self._skip_requested = False
        self._discard_current = False
        self._destroyed = False
        self._loop = asyncio.get_running_loop()
        self._queue_event = asyncio.Event()
        self._track_done = asyncio.Event()
        self._task = asyncio.create_task(self._player_loop(), name=f"music-{guild.id}")

    @property
    def voice_client(self) -> discord.VoiceClient | None:
        vc = self.guild.voice_client
        return vc if isinstance(vc, discord.VoiceClient) and vc.is_connected() else None

    @property
    def is_active(self) -> bool:
        return self.current is not None or bool(self.queue)

    def add(self, track: Track) -> None:
        self.queue.append(track)
        self._queue_event.set()

    def skip(self) -> None:
        self._skip_requested = True
        if self.voice_client:
            self.voice_client.stop()

    def stop(self) -> None:
        self.queue.clear()
        self._discard_current = True
        if self.voice_client:
            self.voice_client.stop()

    def set_volume(self, volume: float) -> None:
        self.volume = volume
        vc = self.voice_client
        if vc and isinstance(vc.source, discord.PCMVolumeTransformer):
            vc.source.volume = volume

    async def notify(self, content: str | None = None, *, embed: discord.Embed | None = None) -> None:
        try:
            await self.text_channel.send(content, embed=embed)
        except discord.HTTPException:
            log.warning("Nu pot trimite mesaje în canalul de muzică pe %s", self.guild)

    def _make_source(self, track: Track) -> discord.PCMVolumeTransformer[YTDLAudio]:
        return discord.PCMVolumeTransformer(YTDLAudio(track), volume=self.volume)

    def _after_playback(self, error: Exception | None) -> None:
        # Rulează în thread-ul audio al discord.py, nu în event loop.
        if error:
            log.error("Eroare la redare pe %s: %s", self.guild, error)
        self._loop.call_soon_threadsafe(self._track_done.set)

    async def _player_loop(self) -> None:
        try:
            while True:
                if not self.queue:
                    self._queue_event.clear()
                    try:
                        await asyncio.wait_for(self._queue_event.wait(), timeout=IDLE_TIMEOUT)
                    except asyncio.TimeoutError:
                        await self.notify(
                            "💤 Nu am mai avut nimic de redat de 5 minute, așa că am ieșit de pe canal."
                        )
                        return
                    continue

                vc = self.voice_client
                if vc is None:
                    return

                track = self.queue.popleft()
                self.current = track
                self._skip_requested = False
                self._discard_current = False
                try:
                    source = self._make_source(track)
                except discord.ClientException as exc:
                    self.current = None
                    log.error("Nu pot porni FFmpeg: %s", exc)
                    await self.notify("⚠️ Nu pot porni FFmpeg. Verifică dacă este instalat și în PATH.")
                    return

                self._track_done.clear()
                try:
                    vc.play(source, after=self._after_playback)
                except discord.ClientException as exc:
                    source.cleanup()
                    self.current = None
                    log.error("Nu pot reda pe %s: %s", self.guild, exc)
                    return
                if not track.announced:
                    track.announced = True
                    await self.notify(embed=track.embed("🎶 **Acum se redă**"))

                await self._track_done.wait()
                self.current = None

                if self._discard_current:
                    continue
                if not self._skip_requested and source.original.download_failed:
                    await self.notify(f"⚠️ Nu am putut reda **{track.title}**, trec mai departe.")
                    continue
                if self.loop_mode is LoopMode.TRACK and not self._skip_requested:
                    self.queue.appendleft(track)
                elif self.loop_mode is LoopMode.QUEUE:
                    track.announced = False
                    self.queue.append(track)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Eroare neașteptată în bucla de muzică pe %s", self.guild)
        finally:
            if not self._destroyed:
                task = asyncio.create_task(self.destroy())
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)

    async def destroy(self) -> None:
        """Oprește redarea, golește coada și iese de pe canal. Poate fi apelată de mai multe ori."""
        if self._destroyed:
            return
        self._destroyed = True
        self.queue.clear()
        self.current = None
        if self._task is not asyncio.current_task() and not self._task.done():
            self._task.cancel()
        self.cog.players.pop(self.guild.id, None)
        vc = self.guild.voice_client
        if vc is not None:
            try:
                await vc.disconnect(force=True)
            except Exception:
                log.exception("Eroare la deconectarea de pe canalul de voce pe %s", self.guild)


# --- Verificări pentru comenzi ------------------------------------------------------

def _member_and_bot_channel(interaction: discord.Interaction) -> tuple[discord.Member, discord.VoiceClient]:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        raise app_commands.NoPrivateMessage()
    vc = interaction.guild.voice_client
    if not isinstance(vc, discord.VoiceClient) or vc.channel is None:
        raise app_commands.CheckFailure("Nu sunt conectat pe niciun canal de voce.")
    return interaction.user, vc


def same_voice_channel() -> Any:
    """Utilizatorul trebuie să fie pe același canal de voce cu botul."""

    async def predicate(interaction: discord.Interaction) -> bool:
        member, vc = _member_and_bot_channel(interaction)
        if member.voice is None or member.voice.channel != vc.channel:
            raise app_commands.CheckFailure(
                f"Trebuie să fii pe același canal de voce cu mine ({vc.channel.mention})."
            )
        return True

    return app_commands.check(predicate)


# --- Cog ---------------------------------------------------------------------------

class Music(commands.Cog):
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        self.players: dict[int, GuildPlayer] = {}
        self._connect_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._alone_timers: dict[int, asyncio.Task[None]] = {}

    async def cog_unload(self) -> None:
        for timer in self._alone_timers.values():
            timer.cancel()
        for player in list(self.players.values()):
            await player.destroy()

    def _player_for(self, guild: discord.Guild) -> GuildPlayer | None:
        return self.players.get(guild.id)

    def _require_player(self, interaction: discord.Interaction) -> GuildPlayer:
        assert interaction.guild is not None
        player = self._player_for(interaction.guild)
        if player is None:
            raise app_commands.CheckFailure("Nu se redă nimic acum.")
        return player

    # --- Ieșirea automată când botul rămâne singur ---------------------------------

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        guild = member.guild
        assert self.bot.user is not None
        if member.id == self.bot.user.id and after.channel is None:
            # Botul a fost deconectat (de noi sau de cineva de pe server).
            self._cancel_alone_timer(guild.id)
            player = self._player_for(guild)
            if player:
                await player.destroy()
            return

        vc = guild.voice_client
        if not isinstance(vc, discord.VoiceClient) or vc.channel is None:
            return
        if vc.channel not in (before.channel, after.channel) and member.id != self.bot.user.id:
            return

        if any(not m.bot for m in vc.channel.members):
            self._cancel_alone_timer(guild.id)
        elif guild.id not in self._alone_timers:
            self._alone_timers[guild.id] = asyncio.create_task(self._leave_if_alone(guild))

    def _cancel_alone_timer(self, guild_id: int) -> None:
        timer = self._alone_timers.pop(guild_id, None)
        if timer:
            timer.cancel()

    async def _leave_if_alone(self, guild: discord.Guild) -> None:
        await asyncio.sleep(ALONE_TIMEOUT)
        # Ne scoatem din evidență înainte de deconectare, ca evenimentul de voce
        # declanșat de deconectare să nu anuleze acest task la jumătate.
        if self._alone_timers.get(guild.id) is asyncio.current_task():
            del self._alone_timers[guild.id]
        vc = guild.voice_client
        if not isinstance(vc, discord.VoiceClient) or vc.channel is None:
            return
        if any(not m.bot for m in vc.channel.members):
            return
        player = self._player_for(guild)
        if player:
            await player.notify("👋 Am rămas singur pe canal, așa că am ieșit.")
            await player.destroy()
        else:
            await vc.disconnect(force=True)

    # --- Comenzi -------------------------------------------------------------------

    @app_commands.command(name="play", description="Redă o melodie de pe YouTube (link sau nume)")
    @app_commands.describe(cautare="Un link YouTube sau numele melodiei")
    @app_commands.guild_only()
    async def play(self, interaction: discord.Interaction, cautare: app_commands.Range[str, 1, 300]) -> None:
        guild = interaction.guild
        member = interaction.user
        assert guild is not None and isinstance(member, discord.Member)

        if member.voice is None or member.voice.channel is None:
            await interaction.response.send_message(
                "Intră mai întâi pe un canal de voce. 🎧", ephemeral=True
            )
            return
        channel = member.voice.channel
        player = self._player_for(guild)
        vc = guild.voice_client
        if vc is not None and vc.channel != channel and player is not None and player.is_active:
            await interaction.response.send_message(
                f"Sunt deja folosit pe {vc.channel.mention}. Vino acolo sau așteaptă să termin.",
                ephemeral=True,
            )
            return
        permissions = channel.permissions_for(guild.me)
        if not (permissions.connect and permissions.speak):
            await interaction.response.send_message(
                f"Nu am permisiunea să intru sau să vorbesc pe {channel.mention}.", ephemeral=True
            )
            return
        if player is not None and len(player.queue) >= MAX_QUEUE_SIZE:
            await interaction.response.send_message(
                f"Coada este plină (maxim {MAX_QUEUE_SIZE} melodii).", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)
        try:
            info = await extract_info(cautare)
        except NoResultsError:
            await interaction.followup.send(f"Nu am găsit nimic pentru **{cautare}**. 🔍")
            return
        except yt_dlp.utils.DownloadError as exc:
            log.warning("yt-dlp a eșuat pentru %r: %s", cautare, exc)
            await interaction.followup.send(
                "Nu am putut încărca melodia. Poate fi privată, restricționată sau indisponibilă. "
                "Dacă problema persistă, actualizează yt-dlp (`pip install -U yt-dlp`)."
            )
            return

        track = Track.from_info(info, member)

        async with self._connect_locks[guild.id]:
            vc = guild.voice_client
            try:
                if vc is None:
                    await channel.connect(self_deaf=True, timeout=20)
                elif vc.channel != channel and isinstance(vc, discord.VoiceClient):
                    await vc.move_to(channel)
            except (asyncio.TimeoutError, discord.ClientException) as exc:
                log.warning("Nu mă pot conecta la %s: %s", channel, exc)
                await interaction.followup.send("Nu m-am putut conecta la canalul de voce. Încearcă din nou.")
                return

            player = self._player_for(guild)
            text_channel = interaction.channel
            if not isinstance(text_channel, discord.abc.Messageable):
                await interaction.followup.send("Nu pot trimite mesaje în acest canal.")
                return
            if player is None:
                player = GuildPlayer(self, guild, text_channel)
                self.players[guild.id] = player
            player.text_channel = text_channel

            if player.is_active:
                player.add(track)
                await interaction.followup.send(
                    embed=track.embed(f"➕ **Adăugată în coadă** pe poziția {len(player.queue)}")
                )
            else:
                track.announced = True
                player.add(track)
                await interaction.followup.send(embed=track.embed("🎶 **Acum se redă**"))

    @app_commands.command(name="skip", description="Sari peste melodia curentă")
    @app_commands.guild_only()
    @same_voice_channel()
    async def skip(self, interaction: discord.Interaction) -> None:
        player = self._require_player(interaction)
        if player.current is None:
            await interaction.response.send_message("Nu se redă nimic acum.", ephemeral=True)
            return
        title = player.current.title
        player.skip()
        await interaction.response.send_message(f"⏭️ Am sărit peste **{title}**.")

    @app_commands.command(name="pause", description="Pune melodia pe pauză")
    @app_commands.guild_only()
    @same_voice_channel()
    async def pause(self, interaction: discord.Interaction) -> None:
        _, vc = _member_and_bot_channel(interaction)
        if vc.is_paused():
            await interaction.response.send_message("Melodia este deja pe pauză.", ephemeral=True)
        elif not vc.is_playing():
            await interaction.response.send_message("Nu se redă nimic acum.", ephemeral=True)
        else:
            vc.pause()
            await interaction.response.send_message("⏸️ Pauză. Folosește `/resume` ca să continui.")

    @app_commands.command(name="resume", description="Continuă melodia pusă pe pauză")
    @app_commands.guild_only()
    @same_voice_channel()
    async def resume(self, interaction: discord.Interaction) -> None:
        _, vc = _member_and_bot_channel(interaction)
        if not vc.is_paused():
            await interaction.response.send_message("Melodia nu este pe pauză.", ephemeral=True)
        else:
            vc.resume()
            await interaction.response.send_message("▶️ Continuăm!")

    @app_commands.command(name="stop", description="Oprește muzica și golește coada")
    @app_commands.guild_only()
    @same_voice_channel()
    async def stop(self, interaction: discord.Interaction) -> None:
        player = self._require_player(interaction)
        player.loop_mode = LoopMode.OFF
        player.stop()
        await interaction.response.send_message("⏹️ Am oprit muzica și am golit coada.")

    @app_commands.command(name="queue", description="Arată melodiile din coadă")
    @app_commands.guild_only()
    async def queue(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        player = self._player_for(interaction.guild)
        if player is None or not player.is_active:
            await interaction.response.send_message("Coada este goală. Adaugă ceva cu `/play`.", ephemeral=True)
            return

        embed = discord.Embed(title="🎵 Coada de redare", color=discord.Color.red())
        if player.current:
            embed.add_field(
                name="Acum se redă",
                value=f"[{player.current.title}]({player.current.webpage_url}) "
                f"`{format_duration(player.current.duration)}`",
                inline=False,
            )
        shown = list(player.queue)[:10]
        if shown:
            lines = [
                f"`{i}.` [{t.title}]({t.webpage_url}) `{format_duration(t.duration)}` — {t.requester.mention}"
                for i, t in enumerate(shown, start=1)
            ]
            if len(player.queue) > len(shown):
                lines.append(f"… și încă {len(player.queue) - len(shown)} melodii")
            embed.add_field(name="Urmează", value="\n".join(lines)[:1024], inline=False)
        total = sum(t.duration or 0 for t in player.queue)
        embed.set_footer(
            text=f"{len(player.queue)} în coadă • durată {format_duration(total)} • "
            f"buclă: {player.loop_mode.value}"
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="nowplaying", description="Arată melodia care se redă acum")
    @app_commands.guild_only()
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        player = self._player_for(interaction.guild)
        if player is None or player.current is None:
            await interaction.response.send_message("Nu se redă nimic acum.", ephemeral=True)
            return
        vc = player.voice_client
        status = "⏸️ **Pe pauză**" if vc and vc.is_paused() else "🎶 **Acum se redă**"
        embed = player.current.embed(status)
        embed.set_footer(
            text=f"Volum: {round(player.volume * 100)}% • buclă: {player.loop_mode.value} • "
            f"{len(player.queue)} în coadă"
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="volume", description="Setează volumul (0–100)")
    @app_commands.describe(nivel="Volumul dorit, între 0 și 100 (gol = arată volumul curent)")
    @app_commands.guild_only()
    @same_voice_channel()
    async def volume(
        self, interaction: discord.Interaction, nivel: app_commands.Range[int, 0, 100] | None = None
    ) -> None:
        player = self._require_player(interaction)
        if nivel is None:
            await interaction.response.send_message(f"🔊 Volumul curent este **{round(player.volume * 100)}%**.")
            return
        player.set_volume(nivel / 100)
        icon = "🔇" if nivel == 0 else "🔉" if nivel < 50 else "🔊"
        await interaction.response.send_message(f"{icon} Volumul este acum **{nivel}%**.")

    @app_commands.command(name="loop", description="Repetă melodia curentă sau toată coada")
    @app_commands.describe(mod="Ce să repet (gol = trece la următorul mod)")
    @app_commands.choices(
        mod=[
            app_commands.Choice(name="oprit", value="off"),
            app_commands.Choice(name="melodia curentă", value="track"),
            app_commands.Choice(name="toată coada", value="queue"),
        ]
    )
    @app_commands.guild_only()
    @same_voice_channel()
    async def loop(self, interaction: discord.Interaction, mod: app_commands.Choice[str] | None = None) -> None:
        player = self._require_player(interaction)
        if mod is None:
            order = [LoopMode.OFF, LoopMode.TRACK, LoopMode.QUEUE]
            player.loop_mode = order[(order.index(player.loop_mode) + 1) % len(order)]
        else:
            player.loop_mode = {"off": LoopMode.OFF, "track": LoopMode.TRACK, "queue": LoopMode.QUEUE}[mod.value]
        icon = "➡️" if player.loop_mode is LoopMode.OFF else "🔂" if player.loop_mode is LoopMode.TRACK else "🔁"
        await interaction.response.send_message(f"{icon} Buclă: **{player.loop_mode.value}**.")

    @app_commands.command(name="leave", description="Scoate botul de pe canalul de voce")
    @app_commands.guild_only()
    @same_voice_channel()
    async def leave(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        player = self._player_for(interaction.guild)
        await interaction.response.send_message("👋 Am ieșit de pe canal. Pe curând!")
        if player:
            await player.destroy()
        elif interaction.guild.voice_client:
            await interaction.guild.voice_client.disconnect(force=True)


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Music(bot))
