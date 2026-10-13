"""
Interazioni vocali e testuali di Franchino con gli utenti del canale Discord:
- Manda un bacio (💋)
- Fai un saluto speciale (🎙️)
- Fai un brindisi / Cin cin (🥂)
- Manda un abbraccio (🫂)
- Carica & Hype / Complimenti (🔥)
- Batti il cinque / High five (✋)
- Tanti auguri di compleanno / Festa (🎂)
- Buonanotte & Sogni d'oro (🌙)
- Caldo benvenuto in console (✨)
- Presa in giro amichevole / Trolling (😜)
- Freccia di Cupido / Amore (💘)
"""

import asyncio
import difflib
import logging
import random
import re
import time
from typing import Dict, List, Optional, Tuple

import discord

from config import COLOR_FRANCHINO

logger = logging.getLogger("Franchino.Interactions")


INTERACTION_CONFIG: Dict[str, dict] = {
    "bacio": {
        "title": "Un Bacio Magico dalla Console!",
        "emoji": "💋",
        "color": 0xFF69B4,
        "sfx": "applause",
        "action_label": "ha mandato un bacio a",
        "self_quotes": [
            "Un bacio magico da tutta la console per te, grande {target}! Sentilo nell'aria della notte... Smack! Sei pura magia! 💋✨",
            "Smack! Un bacio stellare dritto al tuo cuore, {target}! Continua a volare alto e a brillare con noi! 💋🎧",
            "Per il nostro mitico {target}: un bacio caldo come la notte dell'Insomnia! Ti vogliamo un bene dell'anima! 💋❤️",
        ],
        "all_quotes": [
            "Un bacio immenso da Franchino per tutti quanti in console! Sentite l'energia della notte... Smack! Vi voglio bene ragazzi, siete fantastici! 💋✨",
            "Smack a tutta la sala! Un bacio collettivo che vola sopra le casse e abbraccia tutti i presenti! Continuate a ballare con il cuore! 💋🎉",
            "Ragazzi, un bacio magico per ognuno di voi! Senza di voi la notte non avrebbe questa magia! Smack! 💋🎧",
        ],
        "quotes": [
            "{user} manda un bacio magico a {target}! Sentilo volare sopra le casse... Smack! C'è pura magia nell'aria stasera! 💋✨",
            "Smack! Un bacio stellare da parte di {user} per il nostro mitico {target}! Apri il cuore e lasciati cullare dal ritmo della notte! 💋🎧",
            "Per il meraviglioso {target}, un bacio caldo e speciale dedicato da {user}! Continua a splendere, sei unico! 💋❤️",
            "Chiudi gli occhi {target}... Smack! Questo bacio da parte di {user} arriva con tutta l'energia dell'Insomnia! Sei speciale! 💋✨",
        ],
    },
    "saluto": {
        "title": "Saluto Speciale dalla Console!",
        "emoji": "🎙️",
        "color": 0x00D4FF,
        "sfx": "airhorn",
        "action_label": "ha salutato",
        "self_quotes": [
            "Un saluto speciale e grandissimo per te, mitico {target}! Benvenuto nella magia della notte, sei il re della console! 🎙️✨",
            "Eccoti qua {target}! La console dell'Insomnia si illumina per te, un caloroso saluto stellare! 🙌🎉",
        ],
        "all_quotes": [
            "Un saluto spaziale da Franchino per tutta la stanza e tutti i presenti! Alzate le mani al cielo, stasera siamo tutti una grande tribù! 🎙️✨",
            "Attenzione a tutta la console! Un salutone mitico per tutti voi ragazzi! Siete il cuore pulsante di questa serata! 🙌🎉",
            "Un saluto infinito per tutti gli amici del canale! Grazie di esserci, stasera si vola altissimo! 🚀🎧",
        ],
        "quotes": [
            "Un saluto stellare dalla console per il nostro grandissimo {target}, dedicato da {user}! Metti le cuffie, chiudi gli occhi e vola con noi! 🎙️✨",
            "Attenzione a tutti! {user} manda un super saluto a {target}! Sei il numero uno, alzati in piedi e fai casino con noi! 🙌🎉",
            "Un saluto leggendario per {target} da parte di {user}! La notte è giovane e noi siamo qui per vivere un sogno ad occhi aperti! 🌟🎧",
            "Dedicato a {target} da {user}: un saluto caldo, forte e carico di vibrazioni positive! Viva la musica! 🎶✨",
        ],
    },
    "brindisi": {
        "title": "Brindisi Stellare in Console!",
        "emoji": "🥂",
        "color": 0xFFD700,
        "sfx": "airhorn",
        "action_label": "ha fatto un brindisi per",
        "self_quotes": [
            "Calici in alto per il nostro leggendario {target}! Bevi alla salute della libertà, dell'amore e della magia! Cin cin! 🥂🔥",
            "Un brindisi speciale per te, {target}! Cento di queste notti meravigliose sotto la nostra console! Cin cin! 🍾✨",
        ],
        "all_quotes": [
            "Alzate tutti i calici ragazzi! Un brindisi clamoroso per tutta la sala! Alla vita, all'amicizia e a questa musica che non si ferma mai! Cin cin a tutti! 🥂✨",
            "Cin cin a tutta la compagnia! Brindiamo all'energia positiva, alle risate e alla notte più bella! Bevete con gioia! 🍾🎉",
        ],
        "quotes": [
            "Alzate i calici in console! {user} offre un brindisi stellare per {target}! Alla vostra amicizia, alla felicità e a questa serata indimenticabile! Cin cin! 🥂🍾",
            "Un brindisi speciale dal cuore dell'Insomnia per {target}, offerto da {user}! Che la festa non finisca mai! Cento di queste notti! 🥂✨",
            "Cin cin per {target}! Bevi alla salute della libertà, della fratellanza e della musica elettronica insieme a {user}! Evviva ragazzi! 🥂🔥",
        ],
    },
    "abbraccio": {
        "title": "Un Abbraccio Caloroso!",
        "emoji": "🫂",
        "color": 0xFF7043,
        "sfx": None,
        "action_label": "ha abbracciato",
        "self_quotes": [
            "Un abbraccio fortissimo e sincero per te, {target}! Non sei mai solo quando c'è la musica a tenerti per mano! 🫂✨",
            "Stringiti forte alla musica {target}! Tutta la nostra console ti avvolge in un abbraccio caldo e rassicurante! 🫂❤️",
        ],
        "all_quotes": [
            "Un abbraccio immenso e caloroso da Franchino per tutti i ragazzi della stanza! Siamo una sola grande famiglia sotto questo cielo! 🫂❤️",
            "Abbraccio collettivo per tutti quanti! Sentite il calore e l'unione che solo la vera amicizia sa regalare! 🫂✨",
        ],
        "quotes": [
            "{user} stringe forte in un grande abbraccio il mitico {target}! Sentite il calore e l'energia positiva che ci unisce tutti! 🫂❤️",
            "Un mega abbraccio dalla console per {target} con tanto affetto da parte di {user}! L'amicizia è la melodia più bella del mondo! 🫂🌟",
            "Per {target}, ecco un abbraccio speciale dedicato da {user}! Un gesto semplice ma carico di cuore e armonia! 🫂✨",
        ],
    },
    "carica": {
        "title": "Carica & Hype Totale!",
        "emoji": "🔥",
        "color": 0xFF3D00,
        "sfx": "airhorn",
        "action_label": "ha caricato al massimo",
        "self_quotes": [
            "Caricaaa {target}! Sei una forza della natura, stasera non ce n'è per nessuno! Fai tremare la console! 🔥🔊",
            "Sentiti invincibile {target}! Il ritmo batte nel tuo sangue, alza le braccia e domina la notte! ⚡🚀",
        ],
        "all_quotes": [
            "Caricaaa ragazzi! Svegliatevi tutti, stasera spacchiamo la console! Senti come batte la cassa a martello nel petto! Fuocooo! 🔥🔊",
            "Tutti in piedi! È il momento della massima carica! Niente paura, solo potenza e adrenalina pura! Andiamo ragazzi! ⚡🎉",
        ],
        "quotes": [
            "{target}, sei una forza della natura! Caricaaa! {user} crede in te, stasera sei tu a guidare la pista, facci vedere di cosa sei capace! 🔥🔊",
            "Guardatelo tutti! {user} hypa al massimo il nostro leggendario {target}! Sei imbattibile, spingi sull'acceleratore e prenditi questa notte! ⚡🚀",
            "{target}, per te solo ovazioni dalla console da parte di {user}! Sei il numero uno indiscusso, fagli vedere chi comanda! 👑💪",
        ],
    },
    "cinque": {
        "title": "Batti il Cinque!",
        "emoji": "✋",
        "color": 0x00E676,
        "sfx": "applause",
        "action_label": "ha dato il cinque a",
        "self_quotes": [
            "Batti quel cinque {target}! Splash! Sintonia cosmica e intesa perfetta in console! Grandissimo! ✋✨",
            "Dammi quel cinque {target}! Boom! Connessione stellare tra te e la musica della notte! ✋💥",
        ],
        "all_quotes": [
            "Cinque alto per tutta la squadra! Siete una forza pazzesca, battete tutti le mani a tempo di musica! ✋🎉",
        ],
        "quotes": [
            "Batti quel cinque {target}! Splash! {user} ti manda un cinque volante al sapore di vittoria e rispetto! ✋✨",
            "Dammi quel cinque {target}! Boom! Connessione stellare e sintonia pura tra te, {user} e la console! ✋💥",
            "Cinque alto tra {user} e {target}! Siete una coppia esplosiva, continuate a spaccare insieme! ✋🔥",
        ],
    },
    "auguri": {
        "title": "Tanti Augurissimi di Buon Compleanno!",
        "emoji": "🎂",
        "color": 0xAB47BC,
        "sfx": "victory",
        "action_label": "ha fatto gli auguri a",
        "self_quotes": [
            "Tanti augurissimiii di buon compleanno a te, leggendario {target}! Oggi sei tu il sovrano della console! Si festeggia fino all'alba! 🎂🎉🎁",
        ],
        "all_quotes": [
            "Tanti auguri e buona festa a tutti quanti! Oggi celebriamo la vita, l'amicizia e la bellezza di stare insieme! 🎂🥳✨",
        ],
        "quotes": [
            "Tanti augurissimiii di buon compleanno a {target} da parte di {user} e di tutta la console! Oggi sei tu la star assoluta! Si festeggia fino all'alba! 🎂🎉🎁",
            "Auguri di cuore al mitico {target} da parte di {user}! Che tutti i tuoi sogni più belli diventino realtà sulle note di questa serata indimenticabile! 🥳✨",
            "Oggi è il gran giorno di {target}! {user} ti dedica la festa più pazzesca dell'anno! Spegni le candeline e alza il volume al massimo! 🎂🔥🎶",
        ],
    },
    "buonanotte": {
        "title": "Buonanotte Magica & Sogni d'Oro!",
        "emoji": "🌙",
        "color": 0x5C6BC0,
        "sfx": None,
        "action_label": "ha dato la buonanotte a",
        "self_quotes": [
            "Buonanotte {target}... Spegni i pensieri, lasciati cullare dal respiro della notte e vola nei sogni più magici... Ci vediamo domani in console! 🌙✨",
        ],
        "all_quotes": [
            "Buonanotte a tutti ragazzi... Spegnete i pensieri, lasciatevi cullare dal respiro della notte e volate nei sogni più magici... Ci vediamo domani in console! 🌙✨",
            "Sogni d'oro a tutta la tribù! Riposate bene, guerrieri della notte... Domani si riparte con altra grande musica! 💤⭐",
        ],
        "quotes": [
            "Buonanotte {target}... {user} ti augura sogni d'oro. Chiudi gli occhi e riposa bene, domani ti aspetta un'altra grande favola... 🌙✨",
            "Sogni d'oro a {target} con tanto affetto da parte di {user}! Che le stelle ti guidino verso sogni sereni e pieni di armonia! 💤⭐",
        ],
    },
    "benvenuto": {
        "title": "Caldo Benvenuto in Console!",
        "emoji": "✨",
        "color": 0x26C6DA,
        "sfx": "applause",
        "action_label": "ha dato il benvenuto a",
        "self_quotes": [
            "Benvenuto nella nostra casa musicale, {target}! Mettiti comodo, allaccia le cinture e lasciati trasportare dal suono! 🎧🚀",
        ],
        "all_quotes": [
            "Un benvenuto colossale a tutti i nuovi arrivati! La porta della magia è sempre aperta per voi, siete tra amici! ✨🎉",
        ],
        "quotes": [
            "Benvenuto tra noi {target}! {user} ti accoglie nel tempio della magia e della buona musica! Mettiti comodo e goditi il viaggio! 🎧🚀",
            "Un caldissimo benvenuto in console a {target} da parte di {user}! Da adesso in poi fai parte della nostra tribù! Alza il volume e divertiti con noi! 🎉✨",
        ],
    },
    "troll": {
        "title": "Scherzo & Presa in Giro Amichevole!",
        "emoji": "😜",
        "color": 0xFFA000,
        "sfx": "fail",
        "action_label": "ha preso in giro bonariamente",
        "self_quotes": [
            "{target}, ma che stai combinando?! Ripigliati che la notte è appena cominciata! Meno chiacchiere e più cassa a martello! 😂🔊",
        ],
        "all_quotes": [
            "Ragazzi vi vedo un po' moscetti stasera! Bevete un bicchiere d'acqua fresca, scuotete la testa e tornate in pista prima che vi faccia fare cinquanta flessioni! 😜⚡",
        ],
        "quotes": [
            "{target}, ma che stai combinando?! {user} dice che ti stai addormentando in piedi! Svegliaaaa, la notte è giovane e la cassa non perdona chi batte la fiacca! 😂🔊",
            "Ehi {target}, {user} mi ha detto di darti una scossa! Smettila di fare il timido, prendi il ritmo e facci vedere che sei ancora vivo! 😜⚡",
            "{target}, mi dicono dalla regia che stai dormendo con gli occhi aperti! Meno scuse e più festa, ripigliati subito per il nostro amico {user}! 📢🤣",
        ],
    },
    "amore": {
        "title": "La Freccia di Cupido!",
        "emoji": "💘",
        "color": 0xE91E63,
        "sfx": None,
        "action_label": "ha dedicato un messaggio d'amore a",
        "self_quotes": [
            "L'amore per se stessi è la scintilla che accende l'universo intero, {target}! Continua a volerti bene e a brillare! 💖✨",
        ],
        "all_quotes": [
            "C'è tanto amore nell'aria stasera in questa stanza! Aprite i cuori ragazzi, perché quando c'è l'amore la notte diventa eterna! 💖🎶",
        ],
        "quotes": [
            "Sentite come batte forte il cuore di {target}... Cupido ha lanciato la sua freccia magica sulle note di questo disco per {user}! L'amore è il drop più bello! 💘✨",
            "Attenzione attenzione! C'è una dichiarazione speciale nell'aria per {target} da parte di {user}! L'amore fa battere il tempo della felicità! 💖🎶",
        ],
    },
}


INTERACTION_PATTERNS: List[Tuple[str, str]] = [
    # 1. Bacio
    (
        "bacio",
        r"^(?:manda(?:mi)?\s+(?:un\s+)?bacio|dai(?:mi)?\s+(?:un\s+)?bacio|fai\s+(?:un\s+)?bacio|(?:un\s+)?bacio|baciami|bacia|smack|limone)\b(?:\s+(?:a|ad|per|con))?\s*(.*)$"
    ),
    # 2. Saluto
    (
        "saluto",
        r"^(?:fai\s+(?:un\s+)?saluto|(?:un\s+)?saluto|manda(?:mi)?\s+(?:un\s+)?saluto|saluta(?:mi)?)\b(?:\s+(?:a|ad|per))?\s*(.*)$"
    ),
    # 3. Brindisi
    (
        "brindisi",
        r"^(?:fai\s+(?:un\s+)?brindisi|(?:un\s+)?brindisi|brinda|brindiamo|cin\s+cin|bevi\s+con\s+noi)\b(?:\s+(?:a|ad|per|con))?\s*(.*)$"
    ),
    # 4. Abbraccio
    (
        "abbraccio",
        r"^(?:manda(?:mi)?\s+(?:un\s+)?abbraccio|fai\s+(?:un\s+)?abbraccio|(?:un\s+)?abbraccio|abbraccia(?:mi)?|stringi(?:mi)?)\b(?:\s+(?:a|ad|per|con))?\s*(.*)$"
    ),
    # 5. Carica / Hype / Complimenti
    (
        "carica",
        r"^(?:fai\s+i\s+complimenti|complimenti|carica|motiva|pompa|hypa|esalta|incoraggia)\b(?:\s+(?:a|ad|per))?\s*(.*)$"
    ),
    # 6. Batti il cinque
    (
        "cinque",
        r"^(?:batti\s+(?:il\s+)?cinque|dai\s+(?:il\s+)?cinque|dammi\s+(?:il\s+)?cinque|(?:il\s+)?cinque|high\s+five)\b(?:\s+(?:a|ad|per|con))?\s*(.*)$"
    ),
    # 7. Auguri / Compleanno
    (
        "auguri",
        r"^(?:fai\s+(?:gli\s+)?auguri|tanti\s+auguri|buon\s+compleanno|auguri)\b(?:\s+(?:a|ad|per))?\s*(.*)$"
    ),
    # 8. Buonanotte
    (
        "buonanotte",
        r"^(?:dai\s+(?:la\s+)?buonanotte|buonanotte|sogni\s+d\'oro)\b(?:\s+(?:a|ad|per))?\s*(.*)$"
    ),
    # 9. Benvenuto
    (
        "benvenuto",
        r"^(?:dai\s+(?:il\s+)?benvenuto|benvenuto|accogli)\b(?:\s+(?:a|ad|per))?\s*(.*)$"
    ),
    # 10. Trolling / Prendi in giro / Insulta
    (
        "troll",
        r"^(?:prendi\s+in\s+giro|scherza\s+con|sfotti|rimprovera|bacchetta|insulta|sveglia)\b(?:\s+(?:a|ad|per|con))?\s*(.*)$"
    ),
    # 11. Amore / Cupido
    (
        "amore",
        r"^(?:dichiara\s+(?:il\s+tuo\s+)?amore|fai\s+innamorare|cupido|ti\s+amo|ti\s+voglio\s+bene)\b(?:\s+(?:a|ad|per|con))?\s*(.*)$"
    ),
]


def parse_interaction_command(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Riconosce se il comando vocale o testuale è un'interazione con gli utenti del canale Discord.
    Ritorna: (interaction_type, raw_target_string) oppure (None, None).
    """
    if not text:
        return None, None
    clean = text.lower().strip(" ,.!?:;-")
    for itype, pat in INTERACTION_PATTERNS:
        m = re.match(pat, clean, re.IGNORECASE)
        if m:
            target = m.group(1).strip(" ,.!?:;-") if m.groups() else ""
            target = re.sub(r"^(?:a|ad|per|con|il|lo|la|i|gli|le)\s+", "", target).strip(" ,.!?:;-")
            return itype, target
    return None, None


def resolve_target_info(
    player,
    requester: discord.Member,
    raw_target: str,
) -> Tuple[str, Optional[discord.Member], bool, bool]:
    """
    Risolve il target dell'interazione (nome mostrato, eventuale oggetto Member, is_self, is_all).
    Cerca prioritariamente tra i partecipanti nella stanza vocale, poi tra tutti i membri del server,
    con fallback intelligente sul nome pronunciato.
    """
    clean_target = raw_target.lower().strip(" ,.!?:;-") if raw_target else ""

    # Caso 1: se stesso (es. "mandami un bacio", "salutami", "abbracciami")
    if not clean_target or clean_target in ("me", "a me", "me stesso", "me stessa", "io", "mio"):
        return requester.display_name, requester, True, False

    # Caso 2: tutti (es. "saluta tutti", "un brindisi a tutti", "buonanotte a tutti")
    if clean_target in (
        "tutti", "tutti quanti", "noi", "la stanza", "tutta la stanza",
        "la sala", "tutti i presenti", "tutti i ragazzi", "tutti gli amici",
        "la gang", "la ciurma", "il canale", "tutti quanti in vocale"
    ):
        return "tutti quanti in console", None, False, True

    # Caso 3: ricerca mirata tra i membri della stanza vocale
    vc = player.voice_client or getattr(player.guild, "voice_client", None)
    if vc and getattr(vc, "channel", None):
        channel_members = [
            m for m in vc.channel.members
            if not getattr(m, "bot", False) and getattr(m, "id", 0) != requester.id
        ]
        # Match esatto o per prefisso nei display_name della stanza
        for m in channel_members:
            dn = m.display_name.lower()
            un = m.name.lower()
            if dn == clean_target or un == clean_target:
                return m.display_name, m, False, False
            if dn.startswith(clean_target) or clean_target.startswith(dn):
                return m.display_name, m, False, False
            if clean_target in dn or clean_target in un:
                return m.display_name, m, False, False

        # Match fuzzy SequenceMatcher tra i presenti in stanza
        best_m = None
        best_ratio = 0.0
        for m in channel_members:
            ratio = max(
                difflib.SequenceMatcher(None, clean_target, m.display_name.lower()).ratio(),
                difflib.SequenceMatcher(None, clean_target, m.name.lower()).ratio(),
            )
            if ratio > best_ratio:
                best_ratio = ratio
                best_m = m
        if best_m and best_ratio >= 0.58:
            return best_m.display_name, best_m, False, False

    # Caso 4: ricerca tra tutti i membri del server (se la persona è mutata, sorda o in chat testuale)
    if hasattr(player.guild, "members") and player.guild.members:
        for m in player.guild.members:
            if getattr(m, "bot", False) or getattr(m, "id", 0) == requester.id:
                continue
            dn = m.display_name.lower()
            un = m.name.lower()
            if dn == clean_target or un == clean_target:
                return m.display_name, m, False, False
            if dn.startswith(clean_target) or clean_target.startswith(dn):
                return m.display_name, m, False, False

    # Caso 5: fallback sul nome pronunciato
    return raw_target.strip().title(), None, False, False


def get_interaction_quote(
    itype: str,
    user_name: str,
    target_name: str,
    is_self: bool = False,
    is_all: bool = False,
) -> str:
    """Restituisce una battuta da vocalist autentico in stile Franchino per l'interazione."""
    cfg = INTERACTION_CONFIG.get(itype, INTERACTION_CONFIG["saluto"])
    if is_self and cfg.get("self_quotes"):
        template = random.choice(cfg["self_quotes"])
    elif is_all and cfg.get("all_quotes"):
        template = random.choice(cfg["all_quotes"])
    else:
        template = random.choice(cfg["quotes"])

    return template.format(user=user_name, target=target_name)


async def execute_interaction(
    player,
    user: discord.Member,
    itype: str,
    raw_target: str,
    notify_channel_func,
) -> bool:
    """
    Esegue l'interazione:
    1. Risolve il target nella stanza o nel server
    2. Seleziona la frase da vocalist di Franchino
    3. Invia l'embed con menzione/avatar nel canale Discord
    4. Riproduce un effetto sonoro DJ dedicato (sfx)
    5. Fa parlare Franchino in stanza vocale con timbro caldo ed eco da console
    6. Archivia l'interazione nell'Hub centrale
    """
    if itype not in INTERACTION_CONFIG:
        return False

    cfg = INTERACTION_CONFIG[itype]
    target_name, target_member, is_self, is_all = resolve_target_info(player, user, raw_target)
    quote = get_interaction_quote(itype, user.display_name, target_name, is_self=is_self, is_all=is_all)

    # Costruzione dell'embed Discord
    embed = discord.Embed(
        title=f"{cfg['emoji']} {cfg['title']}",
        description=f"> *\"{quote}\"*",
        color=cfg["color"],
    )

    # Dettagli mittente e destinatario
    if is_self:
        embed.add_field(name="🎙️ Dedicato a", value=f"**{user.display_name}** *(Auto-dedica)*", inline=True)
    elif is_all:
        embed.add_field(name="👤 Da parte di", value=f"**{user.display_name}**", inline=True)
        embed.add_field(name="👥 Destinatari", value="**Tutti i presenti in console!**", inline=True)
    else:
        target_display = target_member.mention if target_member else f"**{target_name}**"
        embed.add_field(name="👤 Da parte di", value=f"**{user.display_name}**", inline=True)
        embed.add_field(name="🎯 Per", value=target_display, inline=True)

    embed.set_footer(text="Franchino Vocalist • Insomnia Console Club ✨")

    if target_member and getattr(target_member, "display_avatar", None):
        embed.set_thumbnail(url=target_member.display_avatar.url)
    elif getattr(user, "display_avatar", None):
        embed.set_thumbnail(url=user.display_avatar.url)

    # 1. Pubblica subito nel canale del server
    chat_msg = f"{cfg['emoji']} **{user.display_name}** {cfg['action_label']} **{target_name}**!"
    await notify_channel_func(message=chat_msg, embed=embed)

    # 2. Riproduce l'effetto sonoro DJ associato (es. Airhorn, Applausi, Fail trombone)
    sfx = cfg.get("sfx")
    if sfx and hasattr(player, "play_sound_effect"):
        try:
            await asyncio.wait_for(player.play_sound_effect(sfx), timeout=2.5)
        except Exception as e:
            logger.debug(f"SFX {sfx} non completato o timeout: {e}")

    # 3. Voce di Franchino in console con modalità vocalist (calore + eco/delay notturno)
    if hasattr(player, "speak_tts"):
        asyncio.create_task(
            player.speak_tts(
                quote,
                wait=False,
                interrupt_music=True,
                dramatic_fable=True,
            )
        )

    # 4. Archiviazione nell'Hub Centrale
    try:
        from central_hub import hub
        asyncio.create_task(
            hub.archive_interaction(
                user_id=str(getattr(user, "id", 0)),
                user_name=user.display_name,
                raw_query=f"interazione_{itype} {raw_target}",
                intent=f"interaction_{itype}",
                ai_response=f"{cfg['title']} -> {target_name}",
                voice_summary=quote,
                channel_name=player.text_channel.name if player.text_channel else None,
                metadata={"type": itype, "target": target_name},
            )
        )
    except Exception:
        pass

    logger.info(f"✨ Interazione '{itype}' eseguita con successo: {user.display_name} -> {target_name}")
    return True


async def handle_voice_interaction(player, user: discord.Member, command_part: str, notify_channel_func) -> bool:
    """
    Riconosce e gestisce le interazioni vocali con gli utenti del canale Discord:
    es. "Franchino manda un bacio a Marco", "Franchino fai un brindisi per Giulia", "Franchino saluta tutti"
    """
    itype, raw_target = parse_interaction_command(command_part)
    if not itype:
        return False

    return await execute_interaction(player, user, itype, raw_target, notify_channel_func)
