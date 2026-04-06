import asyncio
import os
import logging
import aiofiles
import tempfile
import uuid
import requests
import pycountry

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from telegraph import Telegraph
from pymediainfo import MediaInfo

from database.ia_filterdb import get_file_details
from info import BIN_CHANNEL
from dreamxbotz.util.file_properties import get_name

logger = logging.getLogger(__name__)

# ======================================
# 🔥 LANGUAGE FORMATTER (NEW)
# ======================================

LOCAL_NAMES = {
    "Bengali": "বাংলা",
    "Bangla": "বাংলা",
    "Hindi": "हिन्दी",
    "Tamil": "தமிழ்",
    "Telugu": "తెలుగు",
    "Punjabi": "ਪੰਜਾਬੀ",
    "Malayalam": "മലയാളം",
    "Kannada": "ಕನ್ನಡ",
    "Urdu": "اردو",
    "Arabic": "العربية",
    "Chinese": "中文",
    "Japanese": "日本語",
    "Korean": "한국어",
    "Thai": "ไทย"
}

def fmt_lang(code):
    if not code:
        return "Unknown"

    code = str(code).lower()

    try:
        lang = (
            pycountry.languages.get(alpha_2=code)
            or pycountry.languages.get(alpha_3=code)
        )

        if not lang:
            return code.upper()

        name = lang.name.replace(" (macrolanguage)", "")
        local = LOCAL_NAMES.get(name)

        return f"{name} ({local})" if local else name

    except:
        return code.upper()


# ======================================
# Telegraph init (UNCHANGED)
# ======================================

TELEGRAPH_ACCESS_TOKEN = os.environ.get("TELEGRAPH_ACCESS_TOKEN") or "38a8ac190ac77ad863fa0c3fa98bdf0bb563fa200211b168062e5313b401"
if TELEGRAPH_ACCESS_TOKEN:
    telegraph = Telegraph(access_token=TELEGRAPH_ACCESS_TOKEN)
else:
    telegraph = Telegraph()
    try:
        telegraph.create_account(short_name="Graduate Movies")
    except Exception:
        logger.exception("Failed to create Telegraph account")


@Client.on_callback_query(filters.regex(r"^extract_data"), group=2)
async def extract_data_handler(client: Client, query: CallbackQuery):
    try:
        await query.answer("Fetching Details...", show_alert=False)
    except Exception:
        pass

    _, file_id = query.data.split(":")

    current_markup = query.message.reply_markup
    wait_keyboard = []

    if current_markup and getattr(current_markup, "inline_keyboard", None):
        for row in current_markup.inline_keyboard:
            new_row = []
            for btn in row:
                if btn.callback_data == query.data:
                    new_row.append(
                        InlineKeyboardButton("ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ... ⏳", callback_data="wait_data")
                    )
                else:
                    new_row.append(btn)
            wait_keyboard.append(new_row)

    try:
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(wait_keyboard))
    except Exception:
        pass

    temp_path = os.path.join(
        tempfile.gettempdir(),
        f"acc_{query.from_user.id}_{query.message.id}_{uuid.uuid4().hex}.tmp"
    )

    try:
        files_ = await get_file_details(file_id)
        if not files_:
            await query.message.reply_text("❌ File not found in DB.", quote=True)
            return

        log_msg = await client.send_cached_media(
            chat_id=BIN_CHANNEL,
            file_id=file_id
        )

        file_name = get_name(log_msg)
        safe_title = (
            file_name.replace(".", " ")
            .replace("_", " ")
            .replace("-", " ")
            .replace("[", "")
            .replace("]", "")
            .replace("(", "")
            .replace(")", "")
            .replace("mkv", "")
            .replace("mp4", "")
        )

        media = getattr(log_msg, log_msg.media.value) if log_msg.media else None
        file_size = getattr(media, "file_size", 0) or 0
        chunk_limit = 5 if file_size > 200 * 1024 * 1024 else 4

        async with aiofiles.open(temp_path, "wb") as f:
            async for chunk in client.stream_media(log_msg, limit=chunk_limit):
                await f.write(chunk)

        lib_path = os.path.abspath("MediaInfo.dll") if os.path.exists("MediaInfo.dll") else None

        media_info = await asyncio.wait_for(
            asyncio.to_thread(MediaInfo.parse, temp_path, library_file=lib_path),
            timeout=6
        )

        audio_tracks = []
        subtitle_tracks = []
        video_info = []

        seen_audio = set()
        seen_subs = set()

        for track in media_info.tracks:
            ttype = (track.track_type or "").lower()

            # ================= VIDEO =================
            if ttype == "video":
                width = track.width or 0
                height = track.height or 0

                # Resolution
                resolution = f"{width}x{height}" if width and height else "Unknown"

                # Quality detect
                quality = "Unknown"
                try:
                    h = int(height)
                    if h >= 2160:
                        quality = "2160p"
                    elif h >= 1440:
                        quality = "1440p"
                    elif h >= 1080:
                        quality = "1080p"
                    elif h >= 720:
                        quality = "720p"
                    elif h >= 480:
                        quality = "480p"
                    else:
                        quality = f"{h}p"
                except:
                    pass

                # Codec
                codec = track.format or track.codec_id or "Unknown"

                # Bitrate
                bitrate = "Unknown"
                if track.bit_rate:
                    try:
                        bitrate = f"{int(track.bit_rate)//1000}kbps"
                    except:
                        pass

                # 🔥 Final structured block (IMPORTANT formatting)
                label = (
                    f"Quality   : {quality}\n"
                    f"Resolution: {resolution}\n"
                    f"Codec     : {codec}"
                )

                if bitrate != "Unknown":
                    label += f"\nBitrate   : {bitrate}"

                video_info.append(label)

            # ================= AUDIO =================
            elif ttype == "audio":
                lang = (
                    track.other_language[0]
                    if getattr(track, "other_language", None)
                    else track.language or "und"
                )

                lang = fmt_lang(lang)

                codec = (track.format or "").replace("E-AC-3", "DDP").replace("AC-3", "DD")
                channels = track.channel_s or track.channels or ""
                bitrate = ""

                if channels:
                    try:
                        ch = float(channels)
                        if ch == 6:
                            channels = "5.1"
                        elif ch == 2:
                            channels = "2.0"
                    except:
                        pass

                if track.bit_rate:
                    try:
                        bitrate = f"{int(track.bit_rate)//1000}kbps"
                    except:
                        pass

                details = []
                if codec:
                    details.append(f"{codec}{channels}" if channels else codec)
                if bitrate:
                    details.append(bitrate)

                label = f"{lang} ~ {' - '.join(details)}" if details else lang

                if label not in seen_audio:
                    seen_audio.add(label)
                    audio_tracks.append(label)

            # ================= SUBTITLE =================
            elif ttype in ("text", "subtitle"):
                lang = (
                    track.other_language[0]
                    if getattr(track, "other_language", None)
                    else track.language or "und"
                )

                lang = fmt_lang(lang)

                if lang not in seen_subs:
                    seen_subs.add(lang)
                    subtitle_tracks.append(lang)

        # ======================================
        # 🔥 TELEGRAPH UI (BOT A STYLE)
        # ======================================

        html = "🧾 <b>All Tracks Details</b><hr><br>"

        if video_info:
            html += "🎬 <u><b>Video Track</b></u>"
            for v in video_info:
                html += f"<blockquote>• <code>{v}</code></blockquote>"

        if audio_tracks:
            html += f"<br>🔊 <u><b>Audio Tracks ({len(audio_tracks)})</b></u>"
            for a in audio_tracks:
                html += f"<blockquote>• <code>{a}</code></blockquote>"

        if subtitle_tracks:
            html += f"<br>💬 <u><b>Subtitle Tracks ({len(subtitle_tracks)})</b></u>"
            for s in subtitle_tracks:
                html += f"<blockquote>• <code>{s}</code></blockquote>"

        html += """
        <br>
        <i>
        🔺 Provided By
        <b><a href="https://t.me/DreamxBotz">Graduate Movies</a></b> 🔺
        </i>
        """

        try:
            response = await asyncio.to_thread(
                telegraph.create_page,
                title=safe_title[:200],
                html_content=html,
                author_name="Graduate Movies"
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout):
            await query.message.reply_text("⚠️ Telegraph is busy. Try again later.", quote=True)
            return

        telegraph_url = response["url"]

        success_keyboard = []
        if current_markup and getattr(current_markup, "inline_keyboard", None):
            for row in current_markup.inline_keyboard:
                new_row = []
                for btn in row:
                    if btn.callback_data == query.data:
                        new_row.append(
                            InlineKeyboardButton("📝 ᴠɪᴇᴡ ᴛʀᴀᴄᴋꜱ", url=telegraph_url)
                        )
                    else:
                        new_row.append(btn)
                success_keyboard.append(new_row)

        await query.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup(success_keyboard)
        )

    except Exception as e:
        logger.exception(e)
        await query.message.reply_text(f"Error: {e}", quote=True)

    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
