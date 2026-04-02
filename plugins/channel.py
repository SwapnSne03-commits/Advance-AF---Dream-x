import re
import logging
import unicodedata
import asyncio
import pytz
from datetime import datetime
from collections import defaultdict
from plugins.Dreamxfutures.Imdbposter import get_movie_detailsx, fetch_image, get_movie_details
from database.users_chats_db import db
from pyrogram import Client, filters, enums
from info import CHANNELS, MOVIE_UPDATE_CHANNEL, LINK_PREVIEW, ABOVE_PREVIEW, BAD_WORDS, LANDSCAPE_POSTER, TMDB_POSTER
from Script import script
from database.ia_filterdb import save_file
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils import temp
from pymongo.errors import PyMongoError, DuplicateKeyError
from pyrogram.errors import MessageIdInvalid, MessageNotModified, FloodWait
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Precomputed sets for faster lookups
IGNORE_WORDS = {
    "rarbg", "dub", "sub", "sample", "mkv", "aac", "combined",
    "action", "adventure", "animation", "biography", "comedy", 
    "documentary", "drama", "fantasy", "film-noir", "history", 
    "horror", "music", "musical", "mystery", "romance", "sci-fi", "sport", 
    "thriller", "western", "hdcam", "hdtc", "camrip", "ts", "tc", 
    "telesync", "dvdscr", "dvdrip", "predvd", "webrip", "web-dl", "tvrip", 
    "hdtv", "web dl", "webdl", "bluray", "brrip", "bdrip", "360p", "480p", 
    "720p", "1080p", "2160p", "4k", "1440p", "540p", "240p", "140p", "hevc", 
    "hdrip", "hin", "hindi", "tam", "tamil", "kan", "kannada", "tel", "telugu", 
    "mal", "malayalam", "eng", "english", "pun", "punjabi", "ben", "bengali", 
    "mar", "marathi", "guj", "gujarati", "urd", "urdu", "kor", "korean", "jpn", 
    "japanese", "nf", "netflix", "sonyliv", "sony", "sliv", "amzn", "prime", 
    "primevideo", "hotstar", "zee5", "jio", "jhs", "aha", "hbo", "paramount", 
    "apple", "hoichoi", "sunnxt", "viki", "tg", "movies", "tgmovies", "x264", "h265", "h264", "x265", "H 264", "Hdwebmovies", 
}|BAD_WORDS

# Constants
CAPTION_LANGUAGES = {
    "hin": "Hindi", "hindi": "Hindi",
    "tam": "Tamil", "tamil": "Tamil",
    "kan": "Kannada", "kannada": "Kannada",
    "tel": "Telugu", "telugu": "Telugu",
    "mal": "Malayalam", "malayalam": "Malayalam",
    "eng": "English", "english": "English", "eng.": "English",
    "pun": "Punjabi", "punjabi": "Punjabi",
    "ben": "Bengali", "bengali": "Bengali", "bangla": "Bengali",
    "mar": "Marathi", "marathi": "Marathi",
    "guj": "Gujarati", "gujarati": "Gujarati",
    "urd": "Urdu", "urdu": "Urdu",
    "kor": "Korean", "korean": "Korean",
    "jpn": "Japanese", "japanese": "Japanese",
}

OTT_PLATFORMS = {
    "nf": "Netflix", "netflix": "Netflix",
    "sonyliv": "SonyLiv", "sony": "SonyLiv", "sliv": "SonyLiv",
    "amzn": "Amazon Prime Video", "prime": "Amazon Prime Video", "primevideo": "Amazon Prime Video",
    "hotstar": "Disney+ Hotstar", "zee5": "Zee5",
    "jio": "JioHotstar", "jhs": "JioHotstar", "dsnp": "Disney",
    "aha": "Aha", "hbo": "HBO Max", "paramount": "Paramount+",
    "apple": "Apple TV+", "hoichoi": "Hoichoi", "sunnxt": "Sun NXT", "viki": "Viki"
}

STANDARD_GENRES = {
    'Action', 'Adventure', 'Animation', 'Biography', 'Comedy', 'Crime', 'Documentary',
    'Drama', 'Family', 'Fantasy', 'Film-Noir', 'History', 'Horror', 'Music',
    'Musical', 'Mystery', 'Romance', 'Sci-Fi', 'Sport', 'Thriller', 'War', 'Western'
}

# Precompiled regex patterns
CLEAN_PATTERN = re.compile(r'@[^ \n\r\t\.,:;!?()\[\]{}<>\\/"\'=_%]+|\bwww\.[^\s\]\)]+|\([\@^]+\)|\[[\@^]+\]')
NORMALIZE_PATTERN = re.compile(r"[._]+|[()\[\]{}:;–!,.?_]")
QUALITY_PATTERN = re.compile(
    r"\b(?:360p|480p|720p|1080p|2160p|4K|1440p|540p|240p|140p)\b",
    re.IGNORECASE
)
FORMAT_PATTERN = re.compile(
    r"\b(?:WEB-DL|WEBRip|BluRay|HDRip|DVDRip|HDTV|CAM|HDCAM|HDTS)\b",
    re.IGNORECASE
)
YEAR_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?:19|20)\d{2}(?![A-Za-z0-9])")
RANGE_REGEX = re.compile(r'\bS(\d{1,2})[^\w\n\r]*E(?:p(?:isode)?)?0*(\d{1,2})\s*(?:to|-)\s*(?:E(?:p(?:isode)?)?)?0*(\d{1,2})',re.IGNORECASE)
SINGLE_REGEX = re.compile(r'\bS(\d{1,2})[^\w\n\r]*E(?:p(?:isode)?)?0*(\d{1,3})', re.IGNORECASE)
NAMED_REGEX = re.compile(r'Season\s*0*(\d{1,2})[\s\-,:]*Ep(?:isode)?\s*0*(\d{1,3})', re.IGNORECASE)
EP_ONLY_RANGE = re.compile(r'\b(?:EP|Episode)0*(\d{1,3})\s*-\s*0*(\d{1,3})\b',re.IGNORECASE)


MEDIA_FILTER = filters.document | filters.video | filters.audio
locks = defaultdict(asyncio.Lock)
pending_updates = {}
error_tmdb = False

def detect_languages(text):
    found = set()
    text = text.lower()

    for key, value in CAPTION_LANGUAGES.items():
        pattern = r'\b' + re.escape(key) + r'\b'
        if re.search(pattern, text):
            found.add(value)

    return ", ".join(sorted(found)) if found else "N/A"

def clean_title_advanced(name: str) -> str:
    if not name:
        return name

    # 🔥 remove season/episode
    name = re.sub(r'\bS\d{1,2}E\d{1,3}\b', ' ', name, flags=re.I)
    name = re.sub(r'\bS\d{1,2}E\d{1,2}\b', ' ', name, flags=re.I)
    name = re.sub(r'\bS\d{1,2}\b', ' ', name, flags=re.I)
    name = re.sub(r'\bE\d{1,3}\b', ' ', name, flags=re.I)
    name = re.sub(r'\bEp(?:isode)?\s*\d+\b', ' ', name, flags=re.I)
    name = re.sub(r'\bS\d{1,2}E\d{1,2}\b', '', name, flags=re.IGNORECASE)

    # 🔥 remove quality/format
    name = QUALITY_PATTERN.sub(" ", name)
    name = FORMAT_PATTERN.sub(" ", name)

    # 🔥 remove codecs
    name = re.sub(r'\b(x264|x265|h264|h265|hevc)\b', ' ', name, flags=re.I)

    # remove x2 
    name = re.sub(r'\bx\d{1,3}\b', ' ', name, flags=re.I)

    # 🔥 remove audio tags
    name = re.sub(r'\b(ddp?\d+(\.\d+)?)\b', ' ', name, flags=re.I)

    # 🔥 remove extra numbers (but keep year)
    name = re.sub(r'\b(?!19\d{2}|20\d{2})\d{1,3}\b', ' ', name)

    # 🔥 remove leftover junk words
    name = re.sub(r'\b(merged|dual|audio|esub|proper|hq)\b', ' ', name, flags=re.I)

    # clean spaces
    name = re.sub(r'\s+', ' ', name).strip()

    return name

def extract_title_upto_year_or_season(text: str) -> str:
    if not text:
        return text

    # 🔥 S02E01 strongest priority
    match = re.search(r'\bS\d{1,2}E\d{1,3}\b', text, re.I)
    if match:
        return text[:match.start()]

    # 🔥 Season fallback
    match = re.search(r'\bS(?:eason)?\s*\d{1,2}\b', text, re.I)
    if match:
        return text[:match.start()]

    # 🔥 Year fallback
    match = re.search(r'\b(19|20)\d{2}\b', text)
    if match:
        return text[:match.end()]

    return text

def clean_mentions_links(text: str) -> str:
    return CLEAN_PATTERN.sub("", text or "").strip()

def normalize(s: str) -> str:
    if not s:
        return ""

    # 🔥 Unicode normalize (𝐓𝐆 → TG)
    s = unicodedata.normalize("NFKD", s)

    # existing logic
    s = NORMALIZE_PATTERN.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()

def remove_ignored_words(text: str) -> str:
    words = text.split()

    cleaned_words = []
    for word in words:
        if word.lower() not in IGNORE_WORDS:
            cleaned_words.append(word)

    return " ".join(cleaned_words)

def get_qualities(text: str) -> str:
    qualities = QUALITY_PATTERN.findall(text)
    return ", ".join(qualities) if qualities else "N/A"

def get_format(text: str) -> str:
    match = FORMAT_PATTERN.findall(text)
    return match[0].upper() if match else "N/A"

def smart_title(text):
    return " ".join(word.capitalize() for word in text.split())

def extract_ott_platform(text: str) -> str:
    text = text.lower()
    platforms = {plat for key, plat in OTT_PLATFORMS.items() if key in text}
    return " | ".join(platforms) if platforms else "N/A"

def extract_season_episode(filename: str):
    # 🔥 Season detect (fallback)
    season_match = re.search(r'\bS(?:eason)?\s*0*(\d{1,2})\b', filename, re.IGNORECASE)
    season = int(season_match.group(1)) if season_match else None

    # 🔥 FIX: E(06-07) support (highest priority)
    if m := EP_BRACKET_RANGE.search(filename):
        return int(m.group(1)), f"{int(m.group(2))}-{int(m.group(3))}"

    # 🔥 Normal EP range (EP 01-05)
    if m := EP_ONLY_RANGE.search(filename):
        return season or 1, f"{int(m.group(1))}-{int(m.group(2))}"

    # 🔥 Single episode
    if m := re.search(r'\bS(\d{1,2})E(\d{1,3})\b', filename, re.IGNORECASE):
        return int(m.group(1)), str(int(m.group(2)))

    # 🔥 fallback
    return season, None

def schedule_update(bot, base_name, delay=5):
    if handle := pending_updates.get(base_name):
        if not handle.cancelled():
            handle.cancel()
    
    loop = asyncio.get_event_loop()
    pending_updates[base_name] = loop.call_later(
        delay,
        lambda: asyncio.create_task(update_movie_message(bot, base_name))
    )
def extract_media_info(filename: str, caption: str):
    filename = normalize(clean_mentions_links(filename))
    caption_clean = clean_mentions_links(caption).lower() if caption else ""
    unified = f"{caption_clean} {filename.lower()}".strip()

    season = episode = year = None
    tag = "#MOVIE"
    processed_raw = base_raw = filename
    # 🔥 caption priority (MAIN FIX)
    source_text = caption_clean if caption_clean else filename
    source_text = normalize(clean_mentions_links(source_text))

    # 🔥 cut after year/season
    source_text = extract_title_upto_year_or_season(source_text)

    # 🔥 override base_raw
    base_raw = source_text
    quality = get_qualities(caption_clean) or get_qualities(filename.lower()) or "N/A"
    format_type = get_format(caption_clean)
    if format_type == "N/A":
        format_type = get_format(filename.lower())
    ott_platform = extract_ott_platform(f"{filename} {caption_clean}")

    language = detect_languages(f"{caption_clean} {filename}")

    season, episode = extract_season_episode(caption_clean or filename)
    is_combined = False

    combined_keywords = [
    "combined", "full series", "complete series",
    "all episodes", "season complete", "full season",
    "complete", "full", "batch"
    ]

    import unicodedata

    def clean_text(s):
        return unicodedata.normalize("NFKD", s).lower()

    text_check = clean_text(caption_clean or filename)

    if re.search(r'\b(combined|complete|full\s+series|season\s+complete|batch|pack)\b', text_check):
        is_combined = True
    if season is not None:
        tag = "#SERIES"

    else:
        if year_match := YEAR_PATTERN.search(unified):
            year = year_match.group(0)
            year_idx = filename.lower().find(year.lower())
            if year_idx != -1:
                processed_raw = filename[:year_idx + 4]
                base_raw = processed_raw
        else:
            if qual_match := QUALITY_PATTERN.search(unified):
                qual_str = qual_match.group(0)
                qual_idx = filename.lower().find(qual_str.lower())
                if qual_idx != -1:
                    processed_raw = filename[:qual_idx]
                    base_raw = processed_raw

    base_name = normalize(remove_ignored_words(normalize(base_raw)))
    base_name = clean_title_advanced(base_name)
    base_name = re.sub(r'\bS\d{1,2}E\d{1,3}\b', '', base_name, flags=re.I)
    base_name = re.sub(r'\bS\d{1,2}\b', '', base_name, flags=re.I)
    base_name = re.sub(r'\bE\d{1,3}\b', '', base_name, flags=re.I)
    base_name = re.sub(r'\b\d+bit\b', '', base_name, flags=re.IGNORECASE)
    base_name = re.sub(r'\bx26[45]\b', '', base_name, flags=re.IGNORECASE)
    base_name = re.sub(r'\bx\d+\b', '', base_name, flags=re.IGNORECASE)
    base_name = re.sub(r'\s+', ' ', base_name).strip()
    # 🔥 FIX: same series merge (ignore year)
    if tag == "#SERIES":
        base_name = re.sub(r'\b(19|20)\d{2}\b', '', base_name).strip()
    base_name = base_name.strip(" .-_")

    if year and year not in base_name:
        base_name += f" {year}"

    if base_name.endswith(")"):
        base_name = re.sub(r"\s+\(\d{4}\)$", "", base_name)
        if year:
            base_name += f" {year}"
    print("DEBUG:", text_check, is_combined, season, episode)
    # -------------------------
    # NEW: strip season/episode tokens from final base_name
    # -------------------------
    def _strip_season_episode_tokens(name: str) -> str:
        """
        Remove common season/episode markers from a title while preserving a trailing year.
        Examples removed: S01, s01e02, 1x02, season 1, ep 02, episode 2, part 1
        """
        if not name:
            return name

        # Preserve trailing year (e.g. "Title (2020)" or "Title 2020")
        year_match = re.search(r'\(?\b(19|20)\d{2}\b\)?\s*$', name)
        year_part = ""
        if year_match:
            year_part = year_match.group(0)
            name = name[:year_match.start()].strip()

        # Common patterns to remove
        patterns = [
            r'\bS\d{1,2}E\d{1,2}\b',     # S01E02
            r'\bS\d{1,2}\b',             # S01
            r'\bE\d{1,2}\b',             # E02
            r'\b\d{1,2}x\d{1,2}\b',      # 1x02
            r'\bSeason\s*\d{1,2}\b',     # Season 1
            r'\bEp(?:isode)?\.?\s*\d{1,3}\b',  # Ep02, Episode 2
            r'\bEpisode\s*\d{1,3}\b',
            r'\bPart\s*\d{1,2}\b'
        ]

        for p in patterns:
            name = re.sub(p, ' ', name, flags=re.IGNORECASE)

        # Remove leftover separators and extra whitespace
        name = re.sub(r'[_\.\-]+', ' ', name)     # underscores/dots/hyphens
        name = re.sub(r'\s+', ' ', name).strip()

        # Reattach year in canonical form if we removed it earlier
        if year_part:
            y = re.search(r'(19|20)\d{2}', year_part)
            if y:
                name = f"{name} {y.group(0)}"

        return name.strip()

    base_name = _strip_season_episode_tokens(base_name)
    base_name = clean_title_advanced(base_name)

    # 🔥 better fallback condition
    if not base_name or len(base_name.split()) <= 1:
        base_name = normalize(remove_ignored_words(normalize(processed_raw))) or filename

    base_name = smart_title(base_name)

    return {
        "processed": normalize(processed_raw),
        "base_name": base_name,
        "tag": tag,
        "season": season,
        "episode": episode,
        "year": year,
        "quality": quality,
        "ott_platform": ott_platform,
        "format": format_type,
        "is_combined": is_combined,
        "language": language
    }


@Client.on_message(filters.chat(CHANNELS) & MEDIA_FILTER)
async def media_handler(bot, message):
    media = next(
        (getattr(message, ft) for ft in ("document", "video", "audio")
         if getattr(message, ft, None)),
        None
    )
    if not media:
        return

    media.file_type = next(ft for ft in ("document", "video", "audio") if hasattr(message, ft))
    media.caption = message.caption or ""
    success, info = await save_file(media)
    if not success:
        return

    try:
        if await db.movie_update_status(bot.me.id):
            await process_and_send_update(bot, media.file_name, media.caption)
    except Exception:
        logger.exception("Error processing media")

async def process_and_send_update(bot, filename, caption):
    try:
        media_info = extract_media_info(filename, caption)
        base_name = media_info["base_name"]
        processed = media_info["processed"]

        lock = locks[base_name]
        async with lock:
            await _process_with_lock(bot, filename, caption, media_info, base_name, processed)
    except PyMongoError as e:
        logger.error(f"Database error in process_and_send_update: {e}")
    except Exception as e:
        logger.exception(f"Processing failed in process_and_send_update: {e}")

async def _process_with_lock(bot, filename, caption, media_info, base_name, processed):
    if not hasattr(db, 'movie_updates'):
        db.movie_updates = db.db.movie_updates

    movie_doc = await db.movie_updates.find_one({"_id": base_name})
    error_tmdb=False
    file_data = {
        "filename": filename,
        "processed": processed,
        "quality": media_info["quality"],
        "language": media_info["language"],
        "format": media_info.get("format"),           # ✅ ADD THIS
        "is_combined": media_info.get("is_combined"), # ✅ ADD THIS
        "ott_platform": media_info["ott_platform"],
        "timestamp": datetime.now(),
        "tag": media_info["tag"],
        "season": media_info["season"],
        "episode": media_info["episode"]
    }

    if not movie_doc:
        if TMDB_POSTER:
            details = await get_movie_detailsx(base_name)
            if not details or details.get("error") or (not details.get("poster_url") and not details.get("backdrop_url")):
                error_tmdb=True
                logger.info("TMDB error switching to IMDB")
                details = await get_movie_details(base_name) or {}
        else:
            details = await get_movie_details(base_name) or {}

        raw_genres = details.get("genres", "N/A")
        if isinstance(raw_genres, str):
            genre_list = [g.strip() for g in raw_genres.split(",")]
            genres = ", ".join(g for g in genre_list if g in STANDARD_GENRES) or "N/A"
        else:
            genres = ", ".join(g for g in raw_genres if g in STANDARD_GENRES) or "N/A"
        movie_doc = {
            "_id": base_name,
            "files": [file_data],
            "poster_url": details.get("backdrop_url") if LANDSCAPE_POSTER and TMDB_POSTER and details.get("backdrop_url") and not error_tmdb else details.get("poster_url"),
            "genres": genres,
            "rating": details.get("rating", "N/A"),
            "imdb_url": details.get("url", "")if not TMDB_POSTER or error_tmdb else details.get("tmdb_url"),
            "year": media_info["year"] or details.get("year"),
            "tag": media_info["tag"],
            "ott_platform": media_info["ott_platform"],
            "message_id": None,
            "is_photo": False,
            "error_tmdb": error_tmdb,
            "is_backdrop": details.get("backdrop_url")
        }
        try:
            await db.movie_updates.insert_one(movie_doc)
            await send_movie_update(bot, base_name)
            movie_doc = await db.movie_updates.find_one({"_id": base_name})
        except DuplicateKeyError:
            movie_doc = await db.movie_updates.find_one({"_id": base_name})
            if movie_doc:
                if any(f["filename"] == filename for f in movie_doc["files"]):
                    return
                await db.movie_updates.update_one(
                    {"_id": base_name},
                    {"$push": {"files": file_data}}
                )
                movie_doc["files"].append(file_data)
                schedule_update(bot, base_name)
    else:
        if any(f["filename"] == filename for f in movie_doc["files"]):
            return
        await db.movie_updates.update_one(
            {"_id": base_name},
            {"$push": {"files": file_data}}
        )
        movie_doc["files"].append(file_data)
        schedule_update(bot, base_name)

async def send_movie_update(bot, base_name):
    max_retries = 3
    base_delay = 5
    for attempt in range(max_retries):
        try:
            movie_doc = await db.movie_updates.find_one({"_id": base_name})
            if not movie_doc:
                return None

            text = generate_movie_message(movie_doc, base_name)
            buttons = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    'ɢᴇᴛ ғɪʟᴇs',
                    url=f"https://t.me/{temp.U_NAME}?start=getfile-{base_name.replace(' ', '-')}"
                )
            ]])
            size=(2560, 1440) if LANDSCAPE_POSTER and TMDB_POSTER and movie_doc.get("is_backdrop") and not movie_doc.get("error_tmdb") else (853, 1280)
            if movie_doc.get("poster_url") and not LINK_PREVIEW:
                resized_poster = await fetch_image(movie_doc["poster_url"], size)
                msg = await bot.send_photo(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    photo=resized_poster,
                    caption=text,
                    reply_markup=buttons,
                    parse_mode=enums.ParseMode.HTML
                )
                is_photo = True
            else:
                send_params = {
                    "chat_id": MOVIE_UPDATE_CHANNEL,
                    "text": text,
                    "reply_markup": buttons,
                    "parse_mode": enums.ParseMode.HTML
                }
                if movie_doc.get("poster_url") and LINK_PREVIEW:
                    send_params["invert_media"] = ABOVE_PREVIEW
                msg = await bot.send_message(**send_params)
                is_photo = False

            await db.movie_updates.update_one(
                {"_id": base_name},
                {"$set": {"message_id": msg.id, "is_photo": is_photo}}
            )
            return msg
        except FloodWait as e:
            wait_time = e.value + 2
            await asyncio.sleep(wait_time)
        except Exception as e:
            logger.error(f"Failed to send movie update: {e}")
            break
    return None

async def update_movie_message(bot, base_name):
    try:
        movie_doc = await db.movie_updates.find_one({"_id": base_name})
        if not movie_doc:
            return

        text = generate_movie_message(movie_doc, base_name)
        buttons = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                'ɢᴇᴛ ғɪʟᴇs',
                url=f"https://t.me/{temp.U_NAME}?start=getfile-{base_name.replace(' ', '-')}"
            )
        ]])

        message_id = movie_doc.get("message_id")
        is_photo = movie_doc.get("is_photo", False)

        if not message_id:
            await send_movie_update(bot, base_name)
            return

        try:
            if is_photo:
                await bot.edit_message_caption(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    message_id=message_id,
                    caption=text,
                    reply_markup=buttons,
                    parse_mode=enums.ParseMode.HTML
                )
            else:
                await bot.edit_message_text(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    message_id=message_id,
                    text=text,
                    reply_markup=buttons,
                    parse_mode=enums.ParseMode.HTML,
                    invert_media=ABOVE_PREVIEW,
                    disable_web_page_preview=not LINK_PREVIEW
                )
            return
        except (MessageIdInvalid, MessageNotModified) as e:
            logger.warning(f"Message update skipped due to error: {e}")
            pass
        except Exception:
            try:
                await bot.delete_messages(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    message_ids=message_id
                )
                await db.movie_updates.update_one(
                    {"_id": base_name},
                    {"$set": {"message_id": None, "is_photo": False}}
                )
            except Exception as e:
                logger.error(f"Error during message deletion/update in recovery: {e}")
                pass
            await send_movie_update(bot, base_name)
    except Exception as e:
        logger.error(f"Failed to update movie message for {base_name}: {e}")

def generate_movie_message(movie_doc, base_name):
    all_formats = set()
    # 🔥 date & time (Kolkata)
    now = datetime.now(pytz.timezone("Asia/Kolkata"))
    date_str = now.strftime("%d %b %Y")   # 01 Apr 2026
    time_str = now.strftime("%H.%M.%S")   # 10.00.01
    all_qualities = set()
    all_languages = set()
    all_ott_platforms = set()
    all_tags = set()

    # 🔥 Season ভিত্তিক data
    season_data = {}

    for file in movie_doc["files"]:
        # Quality
        if file["quality"] != "N/A":
            all_qualities.update(q.strip() for q in file["quality"].split(",") if q.strip())

        # Language
        if file["language"] != "N/A":
            all_languages.update(l.strip() for l in file["language"].split(",") if l.strip())

        # OTT
        if file["ott_platform"] != "N/A":
            platforms = [p.strip() for p in file["ott_platform"].split("|") if p.strip()]
            all_ott_platforms.update(platforms)

        # Tag
        if file["tag"]:
            all_tags.add(file["tag"])

        # Format
        if file.get("format") and file["format"] != "N/A":
            all_formats.add(file["format"])

        # 🔥 Season-wise grouping
        season = file.get("season")
        if season is not None:
            if season not in season_data:
                season_data[season] = {
                    "episodes": set(),
                    "combined": False
                }

            # Episode
            if file.get("episode"):
                ep = file["episode"]
                if "-" in ep:
                    try:
                        start, end = map(int, ep.split("-"))
                        season_data[season]["episodes"].update(range(start, end + 1))
                    except:
                        pass
                else:
                    try:
                        season_data[season]["episodes"].add(int(ep))
                    except:
                        pass

            #Combined
            if file.get("is_combined") is True:
                season_data[season]["combined"] = True

    # Tag
    primary_tag = "#SERIES" if "#SERIES" in all_tags else "#MOVIE"

    # Format/Quality/Language
    format_str = ", ".join(sorted(all_formats)) if all_formats else "N/A"
    quality_str = ", ".join(sorted(all_qualities)) if all_qualities else "N/A"
    language_str = ", ".join(sorted(all_languages)) if all_languages else "N/A"
    ott_str = ", ".join(sorted(all_ott_platforms)) if all_ott_platforms else "N/A"

    # 🔥 MULTI-SEASON LOGIC
    epi_block = ""

    if primary_tag == "#SERIES" and season_data:
        sorted_seasons = sorted(season_data.keys(), key=int)
        # Season range
        if len(sorted_seasons) == 1:
            season_str = f"{sorted_seasons[0]:02}"
        else:
            season_str = f"{sorted_seasons[0]:02}-{sorted_seasons[-1]:02}"

        # Episode lines
        episode_lines = []

        for s in sorted_seasons:
            data = season_data[s]
            eps = data["episodes"]
            combined = data["combined"]

            if eps:
                sorted_eps = sorted(set(eps))  # 🔥 duplicate safety

                if len(sorted_eps) == 1:
                    ep_str = f"{sorted_eps[0]}"
                else:
                    start, end = sorted_eps[0], sorted_eps[-1]
                    ep_str = f"{start}-{end}"
            else:
                ep_str = ""

            if combined:
                if ep_str:
                    ep_str = f"{ep_str}, COMBINED"
                else:
                    ep_str = "COMBINED"
            episode_lines.append(f"S{s:02} : {ep_str if ep_str else 'COMBINED'}")

        epi_block = ""

        if primary_tag == "#SERIES" and season_data:
            epi_block = f"🔅 Sᴇᴀsᴏɴ : {season_str}\n🔹 Eᴘɪsᴏᴅᴇs :\n{chr(10).join(episode_lines)}"

    # Genres
    genres = movie_doc.get("genres", "N/A")

    text = script.MOVIE_UPDATE_NOTIFY_TXT.format(
        poster_url=movie_doc.get("poster_url", ""),
        imdb_url=movie_doc.get("imdb_url", ""),
        filename=base_name,
        tag=primary_tag,
        genres=genres,
        ott=ott_str,
        format=format_str,
        quality=quality_str,
        language=language_str,
        episodes=epi_block,
        rating=movie_doc.get("rating", "N/A"),
        search_link=temp.B_LINK
    )
    text += f"\n\n<b>ᴜᴘʟᴏᴀᴅᴇᴅ ʙʏ - <a href='https://t.me/Graduate_Movies'>Graduate Movies</a></b>\n<b>📅 {date_str}  ⏱️{time_str}</b>"
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text
