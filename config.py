import os
import shutil
from dotenv import load_dotenv

load_dotenv()

# Discord Bot Token
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

# Prefisso comandi testuali
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!").strip() or "!"

# Spotify API credentials (opzionali)
SPOTIPY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID", "").strip() or None
SPOTIPY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET", "").strip() or None

# Volume iniziale (0.0 - 1.0)
try:
    _raw_vol = float(os.getenv("DEFAULT_VOLUME", "70"))
    DEFAULT_VOLUME = max(0.01, min(1.0, _raw_vol / 100.0))
except ValueError:
    DEFAULT_VOLUME = 0.70

# Timeout di inattività in secondi prima di disconnettersi
try:
    INACTIVITY_TIMEOUT = int(os.getenv("INACTIVITY_TIMEOUT", "180"))
except ValueError:
    INACTIVITY_TIMEOUT = 180

# Percorso per Node.js (usato da yt-dlp per risolvere le sfide JS di YouTube)
NODE_PATH = shutil.which("node") or shutil.which("nodejs")

# Colori degli Embed Discord di Franchino
COLOR_FRANCHINO = 0xFF6F00   # Arancione console / DJ vibe
COLOR_SUCCESS = 0x2ECC71     # Verde successo
COLOR_ERROR = 0xE74C3C       # Rosso errore
COLOR_INFO = 0x3498DB        # Blu info
COLOR_SPOTIFY = 0x1DB954     # Verde Spotify
COLOR_YOUTUBE = 0xFF0000     # Rosso YouTube

# Frasi tipiche e messaggi di Franchino
FRANCHINO_QUOTES = [
    "Vivere per vivere! 🎶",
    "Sentite come batte la cassa! 💥",
    "Questa è musica per veri intenditori! 🎧",
    "Andiamo a decollare! 🚀",
    "Magia pura in console! ✨",
    "Alza il volume e lasciati trasportare! 🔊",
]

FRANCHINO_LIVENESS_QUOTES = [
    "Magia pura! Sono qui in console con voi ragazzi! Il microfono della notte è sempre acceso... Chiudi gli occhi e vola!",
    "Sempre vivo e sempre presente in console! Siamo tutti figli della musica! Magia!",
    "Vi sento forte e chiaro ragazzi! Il volume è alto, la cassa batte e siamo pronti a decollare!",
    "Vedo la luce all'orizzonte! Ci sono, ci sono! In diretta dalla console per farvi viaggiare nel tempo e nello spazio! Magia!",
    "Eccomi qua ragazzi! Sono sempre sveglio, sempre sul pezzo! Chiedetemi una traccia e vi porto sulle nuvole!",
    "Ci sono ragazzi! Questo è il popolo della notte, alzate le mani al cielo e lasciatevi trasportare! Magia!",
]

# Frasi di ingresso e accoglienza vocale di Franchino da Vocalist
FRANCHINO_ENTRANCE_QUOTES = [
    "Magia ragazzi! Franchino è in console con voi! Chiudi gli occhi e vola!",
    "Buonasera popolo della notte! Benvenuti nel tempio della musica! Franchino al microfono, si accende la console!",
    "Eccomi qua ragazzi! Alziamo le mani al cielo! Si accende la console della notte!",
    "Sempre presente in console! Vivere per vivere, ragazzi! Cosa vi serve stasera?",
    "Magia pura! Sento l'energia nella stanza! Franchino è con voi in console!",
    "Vedo la luce all'orizzonte! Tutti a bordo, accendiamo i motori e andiamo a decollare!",
]

