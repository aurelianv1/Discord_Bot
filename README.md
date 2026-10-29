# Discord Bot

Bot de Discord în Python, pentru un server personal: **XP și nivele**, **muzică de pe YouTube** și **comenzi utilitare**. Mesajele sunt în română.

Fiecare comandă merge în două feluri: cu **punct** (`.ping`, `.play numele melodiei`) sau ca **slash command** (`/ping`), cu meniu de sugestii. Scrie `.help` pentru lista completă.

Botul are nevoie de un singur secret, tokenul de Discord (gratuit). Nu folosește chei de API și niciun serviciu cu plată: merge doar cu discord.py, yt-dlp și FFmpeg.

## Comenzi

Toate comenzile de mai jos merg și cu `/` în loc de `.`. `<...>` înseamnă obligatoriu, `[...]` opțional.

| Categorie | Comandă | Descriere |
|---|---|---|
| Ajutor | `.help` | Lista tuturor comenzilor |
| XP | `.rank [membru]` | Nivel, loc în clasament, XP total și bară de progres |
| XP | `.leaderboard` (`.top`) | Top 10 pe server |
| XP | `.levelrole` · `.levelrole add <nivel> <rol>` · `.levelrole remove <rol>` | Roluri date automat la anumite nivele *(doar cu Manage Roles)* |
| Muzică | `.play <link sau nume>` (`.p`) | Caută pe YouTube și redă (sau adaugă în coadă) |
| Muzică | `.skip` (`.s`) · `.pause` · `.resume` · `.stop` | Controlul redării |
| Muzică | `.queue` (`.q`) · `.nowplaying` (`.np`) | Coada și melodia curentă |
| Muzică | `.volume [0–100]` · `.loop [oprit\|melodie\|coada]` · `.leave` | Volum, buclă, ieșire |
| Utilitare | `.setwelcome <canal>` · `.welcomeoff` | Mesaj de bun venit *(doar cu Manage Server)* |
| Utilitare | `.ping` · `.avatar` · `.userinfo` · `.serverinfo` | Informații |
| Utilitare | `.poll "întrebare" "opțiune1" "opțiune2" ["3"] ["4"]` | Sondaj cu reacții numerotate |
| Utilitare | `.8ball <întrebare>` · `.coinflip` · `.dice [fete] [numar]` | Distracție |
| Utilitare | `.remind <minute> <text>` | Reminder (maxim 7 zile, rezistă la repornirea botului) |

Exemple:

```
.play never gonna give you up
.remind 10 scoate pizza din cuptor
.poll "Ce mâncăm?" Pizza Paste "Supă de pui"
.levelrole add 5 @Activ
.loop melodie
```

La `.poll`, pune ghilimele în jurul textelor care conțin spații.

**Cum funcționează XP-ul:** fiecare mesaj aduce între 15 și 25 XP, cel mult o dată la 60 de secunde per utilizator. Pentru a trece de la nivelul *n* la *n+1* ai nevoie de `5·n² + 50·n + 100` XP. XP-ul se ține separat pe fiecare server.

**Muzica:** pentru comenzile de control trebuie să fii pe același canal de voce cu botul. Botul iese singur după 5 minute fără melodii în coadă și după 30 de secunde în care a rămas singur pe canal.

---

## Instalare, pas cu pas

### 1. Creează aplicația și obține tokenul (gratuit)

1. Intră pe <https://discord.com/developers/applications> și autentifică-te cu contul de Discord.
2. Apasă **New Application**, dă-i un nume (de exemplu „Botul meu”), acceptă termenii și apasă **Create**.
3. În meniul din stânga, intră la **Bot**.
4. La secțiunea **Token**, apasă **Reset Token**, confirmă, apoi **Copy**. Tokenul e afișat o singură dată; dacă îl pierzi, generezi altul tot cu Reset Token.
5. Pune tokenul în fișierul `.env` (vezi [pasul 5](#5-configurează-fișierul-env)), pe linia `DISCORD_TOKEN=`.

> ⚠️ **Tokenul este parola botului.** Nu-l trimite nimănui și nu-l pune pe GitHub. Fișierul `.env` este deja în `.gitignore`. Dacă tokenul ajunge public, apasă imediat **Reset Token**.

### 2. Activează intents

Tot în pagina **Bot**, la secțiunea **Privileged Gateway Intents**:

- ✅ **Server Members Intent**: **activează-l**, e necesar pentru mesajul de bun venit.
- ✅ **Message Content Intent**: **activează-l**, e necesar pentru comenzile cu punct (`.ping`). Fără el, botul nu vede textul mesajelor.
- ⬜ **Presence Intent**: nu e necesar.

Apasă **Save Changes**.

### 3. Invită botul pe server

1. În meniul din stânga, intră la **OAuth2** → **URL Generator**.
2. La **Scopes**, bifează **`bot`** și **`applications.commands`**.
3. La **Bot Permissions**, bifează:
   - View Channels, Send Messages, Embed Links, Read Message History, Add Reactions
   - Manage Roles *(pentru rolurile de nivel)*
   - Connect, Speak *(pentru muzică)*
4. Copiază linkul generat jos, deschide-l în browser, alege serverul și apasă **Authorize**.

Alternativ, folosește direct linkul de mai jos, după ce înlocuiești `CLIENT_ID` cu **Application ID** din pagina **General Information**:

```
https://discord.com/oauth2/authorize?client_id=CLIENT_ID&permissions=271666240&scope=bot+applications.commands
```

> **Rolurile de nivel:** botul poate da doar roluri aflate *sub* rolul lui. Din **Server Settings → Roles**, trage rolul botului deasupra rolurilor pe care vrei să le dea automat.

### 4. Instalează programele necesare

**Python 3.11 sau mai nou:** <https://www.python.org/downloads/>. Pe Windows, bifează „Add Python to PATH” la instalare.

**FFmpeg** decodează audio pentru muzică:

| Sistem | Comandă |
|---|---|
| Windows | `winget install Gyan.FFmpeg` (apoi redeschide terminalul) |
| macOS | `brew install ffmpeg` |
| Ubuntu / Debian | `sudo apt install ffmpeg libopus0` |
| Fedora | `sudo dnf install ffmpeg-free opus` (sau `ffmpeg` din RPM Fusion) |

Verifică instalarea cu `ffmpeg -version`. Pe Linux e nevoie și de biblioteca **libopus** (inclusă mai sus); pe Windows și macOS vine odată cu discord.py.

**Un runtime JavaScript (Deno recomandat):** YouTube cere ca yt-dlp să ruleze puțin JavaScript ca să obțină linkurile audio. Fără runtime, multe melodii nu mai pot fi redate. Instalează **Deno**:

| Sistem | Comandă |
|---|---|
| Windows | `winget install DenoLand.Deno` |
| macOS | `brew install deno` |
| Linux | `curl -fsSL https://deno.land/install.sh \| sh` |

Dacă ai deja **Node.js** (o versiune recentă) sau **Bun** instalat, merge și acela; botul îl folosește automat pe primul găsit.

### 5. Descarcă proiectul și configurează fișierul `.env`

```bash
git clone git@github.com:aurelianv1/Discord_Bot.git
cd Discord_Bot

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Copiază fișierul de exemplu și completează-l:

```bash
cp .env.example .env               # Windows: copy .env.example .env
```

```ini
DISCORD_TOKEN=tokenul_copiat_la_pasul_1
GUILD_ID=123456789012345678
```

- **`DISCORD_TOKEN`** (obligatoriu): tokenul de la pasul 1.
- **`GUILD_ID`** (opțional, dar recomandat): ID-ul serverului tău. Când e completat, comenzile apar **instant** pe server. Dacă îl lași gol, comenzile se înregistrează global și pot apărea cu întârziere. Ca să afli ID-ul: în Discord, **User Settings → Advanced → Developer Mode** (activează), apoi click dreapta pe numele serverului → **Copy Server ID**.

### 6. Pornește botul

```bash
python bot.py
```

Când pornește corect, în consolă apar mesaje de tipul:

```
Extensie încărcată: cogs.xp
Extensie încărcată: cogs.music
Extensie încărcată: cogs.utility
25 comenzi sincronizate pe serverul 123456789012345678 (instant).
Conectat ca Botul meu#1234 ...
```

Scrie `.help` pe server (sau `/` ca să vezi meniul de comenzi). Datele (XP, roluri de nivel, canalul de bun venit, remindere) sunt salvate în `bot.db`, lângă `bot.py`. Oprești botul cu `Ctrl+C`.

### 7. Rulează testele (opțional)

```bash
python -m unittest discover tests
```

Testele verifică formula de nivele și nu au nevoie de token.

---

## Actualizează yt-dlp periodic

YouTube își schimbă des site-ul, iar yt-dlp primește actualizări foarte des ca să țină pasul. Dacă `/play` începe brusc să dea erori sau melodiile nu mai pornesc, prima soluție este:

```bash
pip install -U "yt-dlp[default]"
```

Fă asta periodic, de exemplu o dată pe săptămână, și repornește botul după actualizare.

## Probleme frecvente

| Problemă | Soluție |
|---|---|
| Nu apar comenzile `/` | Completează `GUILD_ID` în `.env` și repornește botul. Verifică dacă ai bifat `applications.commands` la invitare. Repornește Discord cu `Ctrl+R`. |
| `Lipsește DISCORD_TOKEN` | Fișierul `.env` lipsește sau nu e lângă `bot.py`. |
| Botul cere să activezi intents la pornire | Activează **Server Members Intent** și **Message Content Intent** (pasul 2). |
| `.ping` nu răspunde, dar `/ping` merge | **Message Content Intent** nu e activat (pasul 2), sau botul nu are acces la canal. |
| `/play` spune că nu poate porni FFmpeg | FFmpeg nu e instalat sau nu e în PATH (pasul 4). |
| `/play` găsește melodia, dar nu se aude nimic | Actualizează yt-dlp și verifică dacă ai instalat Deno (pasul 4). |
| Nu se acordă rolurile de nivel | Mută rolul botului deasupra rolurilor de nivel și verifică permisiunea Manage Roles. |
| Nu apare mesajul de bun venit | Folosește `/setwelcome` și verifică dacă botul poate scrie în acel canal. |

## Structura proiectului

```
bot.py             # pornire, intents, baza de date, încărcarea cogs, sincronizarea comenzilor
cogs/xp.py         # XP, nivele, /rank, /leaderboard, /levelrole
cogs/music.py      # muzică: yt-dlp + FFmpeg, coadă separată pe fiecare server
cogs/utility.py    # bun venit, informații, sondaje, distracție, remindere
tests/             # teste pentru formula de nivele
requirements.txt
.env.example
```
