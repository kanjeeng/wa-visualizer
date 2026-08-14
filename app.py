"""
WhatsApp Chat Visualizer - FULLSCREEN EDITION
================================================================
Fullscreen UI ala WhatsApp Web. Pesan lama dimuat lewat tombol manual
"🔼 Muat Pesan Sebelumnya" di atas daftar pesan (anti-jump: posisi baca
tidak melompat setelah pesan lama ditambahkan), auto-scroll ke bawah
hanya saat chat pertama kali dibuka, dan media (gambar/video/audio/
dokumen) dirender penuh langsung di dalam bubble.

CATATAN PERBAIKAN (dari versi Infinite-Scroll sebelumnya):
Mekanisme lama mendeteksi "user scroll ke atas" secara otomatis lewat
Promise + scroll-event-listener JS (TOP_DETECT_JS). Itu sumber bug:
race condition antar-render membuat trigger kadang dobel, kadang tidak
jalan sama sekali, dan auto-scroll-ke-bawah di render pertama ikut
kena imbas karena berbagi komponen JS yang sama. Mekanisme itu sudah
DIHAPUS dan diganti tombol manual (lihat HEIGHT_PROBE_JS & main()) yang
jauh lebih deterministik: tidak ada apa pun yang dijalankan/ditunggu
sampai user benar-benar menekan tombol.
"""

import base64
import hashlib
import os
import re
import tempfile
import zipfile
import shutil
from datetime import date, datetime, timedelta

import streamlit as st
from streamlit_javascript import st_javascript

# =====================================================================================
# 1. KONFIGURASI HALAMAN
# =====================================================================================
st.set_page_config(
    page_title="WhatsApp Chat Visualizer",
    page_icon="💬",
    layout="wide",
    initial_sidebar_state="expanded",
)

STATIC_DIR = os.path.join(os.getcwd(), "static", "wa_media")
os.makedirs(STATIC_DIR, exist_ok=True)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXTS = {".mp4"}
AUDIO_EXTS = {".opus", ".mp3", ".m4a"}
DOC_EXTS = {".pdf", ".xls", ".xlsx", ".ppt", ".pptx", ".doc", ".docx", ".csv", ".txt"}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS | DOC_EXTS

MIME_MAP = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".pdf": "application/pdf",
    ".opus": "audio/ogg", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
    ".xls": "application/vnd.ms-excel", 
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint", 
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".doc": "application/msword", 
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".csv": "text/csv", ".txt": "text/plain"
}

CHUNK = 300  # jumlah pesan lama yang ditambah setiap kali tombol "Muat Pesan Sebelumnya" diklik
CACHE_MAX_ENTRIES = 200

BULAN_ID = [
    "", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember",
]

AVATAR_PALETTE = [
    "#e542a3", "#5b9bd5", "#e67e22", "#16a085", "#8e44ad",
    "#c0392b", "#27ae60", "#2980b9", "#d35400", "#7f8c8d",
    "#c2185b", "#00897b",
]

URL_RE = re.compile(r"(https?://[^\s<>\"']+)")

# Regex utama pesan: [DD/MM/YY, HH.MM.SS] Nama: Pesan
LINE_RE = re.compile(
    r"^\u200e?"
    r"(?:\[)?(?P<date>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})"
    r"[,]?\s+"
    r"(?P<time>\d{1,2}[.:]\d{2}(?:[.:]\d{2})?(?:\s?[APap][Mm])?)(?:\])?\s*"
    r"(?:-\s+)?"
    r"(?P<sender>[^:]+):\s(?P<message>.*)$"
)

# search() (bukan match/fullmatch) agar lampiran tetap terdeteksi meski ada teks di sekitarnya
ATTACH_IOS_RE = re.compile(r"<attached:\s*([^>]+)>")
ATTACH_AND_RE = re.compile(r"\u200e?([^\n]+?\.\w{3,4})\s*\(file terlampir\)", re.IGNORECASE)


# =====================================================================================
# 2. CSS FULLSCREEN WHATSAPP WEB (tanpa header, tanpa tombol navigasi)
# =====================================================================================
def inject_css():
    st.markdown(
        """
        <style>
        /* Sembunyikan Elemen Bawaan Streamlit */
        #MainMenu, footer, header[data-testid="stHeader"] {visibility: hidden; display: none !important;}

        /* FULL SCREEN SETUP */
        .block-container {
            padding: 0.5rem 0 0 0 !important;
            max-width: 100% !important;
        }

        /* BACKGROUND UTAMA ALA WHATSAPP */
        .stApp {
            background-color: #efeae2;
            background-image: url("data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMDAiIGhlaWdodD0iMTAwIiB2aWV3Qm94PSIwIDAgMTAwIDEwMCI+PGcgZmlsbD0iI2Q5ZDJjNyIgZmlsbC1vcGFjaXR5PSIwLjQiPjxjaXJjbGUgY3g9IjEwIiBjeT0iMTAiIHI9IjEuNCIvPjxjaXJjbGUgY3g9IjQwIiBjeT0iMjUiIHI9IjEuMiIvPjxjaXJjbGUgY3g9IjcwIiBjeT0iMTAiIHI9IjEuNiIvPjxjaXJjbGUgY3g9IjkwIiBjeT0iNDAiIHI9IjEuMiIvPjxjaXJjbGUgY3g9IjIwIiBjeT0iNTUiIHI9IjEuNCIvPjxjaXJjbGUgY3g9IjU1IiBjeT0iNjUiIHI9IjEuMiIvPjxjaXJjbGUgY3g9IjgwIiBjeT0iODAiIHI9IjEuNiIvPjxjaXJjbGUgY3g9IjE1IiBjeT0iODUiIHI9IjEuMiIvPjwvZz48L3N2Zz4=");
            background-repeat: repeat;
        }

        /* AVATAR (dipakai di layar upload / pilih POV, bukan di header chat lagi) */
        .wa-avatar-lg {
            width: 46px; height: 46px; border-radius: 50%;
            background: #b0b7bd; color: #fff;
            display: flex; align-items: center; justify-content: center;
            font-weight: bold; font-size: 18px; flex-shrink: 0; margin: 0 auto 10px auto;
        }

        /* BUBBLE CHAT */
        .wa-row { display: flex; margin: 4px 6%; padding-top: 2px;}
        .wa-row.other { justify-content: flex-start; }
        .wa-row.me { justify-content: flex-end; }

        .wa-bubble {
            position: relative; max-width: 70%; padding: 6px 9px 8px 9px;
            border-radius: 8px; box-shadow: 0 1px 0.5px rgba(11,20,26,0.13);
            font-size: 14.5px; line-height: 20px; color: #111b21; word-wrap: break-word;
        }
        .wa-bubble.other { background: #ffffff; border-top-left-radius: 0; }
        .wa-bubble.me { background: #d9fdd3; border-top-right-radius: 0; }

        .wa-sender { font-size: 13px; font-weight: 600; margin-bottom: 3px; }
        .wa-msg-text { white-space: pre-wrap; margin: 0; padding-right: 48px; }
        .wa-msg-text a { color: #027eb5; text-decoration: none; }
        .wa-time { float: right; font-size: 11px; color: #667781; margin-top: 4px; margin-left: 8px; position: relative; top: 4px;}

        /* MEDIA — dirender penuh, tanpa interaksi tambahan */
        .wa-img-wrap { margin: -2px -5px 4px -5px; line-height: 0; }
        .wa-chat-image { width: 100%; max-width: 320px; border-radius: 6px; display: block; }
        .wa-chat-video { width: 100%; max-width: 320px; border-radius: 6px; display: block; margin-bottom: 4px; background: #000; }
        .wa-doc-frame { width: 100%; max-width: 320px; height: 380px; border: none; border-radius: 6px; margin-bottom: 6px; background: #fff; }
        .wa-doc-link {
            display: flex; align-items: center; gap: 10px; padding: 10px 12px; background: rgba(0,0,0,0.04);
            border-radius: 6px; text-decoration: none !important; color: #111b21 !important;
            border: 1px solid rgba(0,0,0,0.06); margin-bottom: 6px; font-size: 13px;
        }
        .wa-doc-link:hover { background: rgba(0,0,0,0.08); }
        .wa-img-broken {
            background: #f7f7f7; border: 1px dashed #c7c7c7; border-radius: 6px;
            padding: 22px 10px; text-align: center; color: #8a8a8a; font-size: 12.5px;
        }

        /* DATE DIVIDER */
        .wa-date-wrap { display: flex; justify-content: center; margin: 18px 0; }
        .wa-date-pill {
            background: #ffffffcc; color: #54656f; font-size: 12.5px; font-weight: 500;
            padding: 6px 14px; border-radius: 10px; box-shadow: 0 1px 1px rgba(11,20,26,0.1);
        }

        /* Awal riwayat */
        .wa-history-start { text-align:center; color:#667781; font-size:12.5px; margin: 16px 0 6px 0; }

        /* Ruang kosong di bawah untuk kenyamanan scroll */
        .bottom-spacer { height: 40px; }
        </style>
        """,
        unsafe_allow_html=True,
    )


# =====================================================================================
# 3. PARSING DATA
# =====================================================================================
def parse_datetime(date_str: str, time_str: str):
    time_str = time_str.replace(".", ":").strip()
    has_ampm = bool(re.search(r"[APap][Mm]$", time_str))
    date_str = date_str.strip()
    dfmt_candidates = ["%d/%m/%y", "%d/%m/%Y"]
    tfmt_candidates = ["%I:%M:%S %p", "%I:%M %p"] if has_ampm else ["%H:%M:%S", "%H:%M"]

    for dfmt in dfmt_candidates:
        for tfmt in tfmt_candidates:
            try:
                return datetime.strptime(f"{date_str} {time_str}", f"{dfmt} {tfmt}")
            except ValueError:
                continue
    return None


def extract_zip(uploaded_file):
    """Ekstrak file ke folder static agar bisa diakses langsung via URL HTML."""
    signature = hashlib.md5(f"{uploaded_file.name}_{uploaded_file.size}".encode()).hexdigest()
    tmp_path = os.path.join(STATIC_DIR, signature)
    os.makedirs(tmp_path, exist_ok=True)

    txt_path = None
    media_index = {}

    with zipfile.ZipFile(uploaded_file) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            lower = name.lower()
            ext = os.path.splitext(lower)[1]
            base_name = os.path.basename(name)

            out_file = os.path.join(tmp_path, name)

            if ext == ".txt":
                if not os.path.exists(out_file):
                    zf.extract(info, tmp_path)
                txt_path = out_file
            elif ext in MEDIA_EXTS:
                if not os.path.exists(out_file):
                    zf.extract(info, tmp_path)
                # KUNCI PERUBAHAN: Simpan sebagai URL statis Streamlit!
                media_index[base_name.lower()] = f"app/static/wa_media/{signature}/{name}"

    return tmp_path, txt_path, media_index


def parse_chat(txt_path: str, media_index: dict):
    messages = []
    current = None

    with open(txt_path, encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            line = raw_line.rstrip("\r\n")
            if line == "":
                continue

            m = LINE_RE.match(line)
            if m:
                if current is not None:
                    messages.append(current)

                dt = parse_datetime(m.group("date"), m.group("time"))
                sender = m.group("sender").strip()
                text = m.group("message").lstrip("\u200e").strip()

                # Percantik pesan sistem yang dihapus/tidak disertakan medianya
                if text in ["<Media tidak disertakan>", "Pesan ini dihapus", "Anda menghapus pesan ini"]:
                    current = {
                        "id": len(messages),
                        "datetime": dt, "date": dt.date() if dt else None,
                        "sender": sender, "text": f"🚫 _{text}_",
                        "is_attachment": False, "is_image": False, "is_video": False, 
                        "is_pdf": False, "is_audio": False, "media_path": None, 
                        "media_filename": None, "media_ext": None
                    }
                    continue

                # Deteksi lampiran dari kedua format OS
                att_ios = ATTACH_IOS_RE.search(text)
                att_and = ATTACH_AND_RE.search(text)
                att_match = att_ios or att_and
                
                is_image = is_video = is_pdf = is_audio = False
                media_path, media_filename, media_ext = None, None, None

                if att_match:
                    media_filename = att_match.group(1).strip()
                    fname_lower = media_filename.lower()
                    media_ext = os.path.splitext(fname_lower)[1]

                    if media_ext in IMAGE_EXTS:
                        is_image = True
                    elif media_ext in VIDEO_EXTS:
                        is_video = True
                    elif media_ext in DOC_EXTS:
                        is_pdf = True  # Flag is_pdf ini sekarang kita pakai untuk mewakili SEMUA tipe dokumen
                    elif media_ext in AUDIO_EXTS:
                        is_audio = True

                    if media_ext in MEDIA_EXTS:
                        media_path = media_index.get(fname_lower)

                    # Hapus caption/tag lampiran asli dari pesan
                    if att_ios:
                        text = ATTACH_IOS_RE.sub("", text).strip()
                    elif att_and:
                        text = ATTACH_AND_RE.sub("", text).strip()

                current = {
                    "id": len(messages),
                    "datetime": dt,
                    "date": dt.date() if dt else None,
                    "sender": sender,
                    "text": text,
                    "is_attachment": att_match is not None,
                    "is_image": is_image,
                    "is_video": is_video,
                    "is_pdf": is_pdf,
                    "is_audio": is_audio,
                    "media_path": media_path,
                    "media_filename": media_filename,
                    "media_ext": media_ext,
                }
            else:
                if current is not None:
                    current["text"] += "\n" + line.strip()

        if current is not None:
            messages.append(current)

    messages.sort(key=lambda x: x["datetime"] or datetime.min)
    image_messages = [m for m in messages if m["is_image"] and m["media_path"]]

    return messages, image_messages


# =====================================================================================
# 4. RENDER HTML & BUBBLE
# =====================================================================================
def sender_color(name: str) -> str:
    h = int(hashlib.md5(name.encode("utf-8")).hexdigest(), 16)
    return AVATAR_PALETTE[h % len(AVATAR_PALETTE)]


def format_date_divider(d: date) -> str:
    if d is None:
        return "Tanggal tidak diketahui"
    today = date.today()
    if d == today:
        return "Hari Ini"
    if d == today - timedelta(days=1):
        return "Kemarin"
    return f"{d.day} {BULAN_ID[d.month]} {d.year}"


def linkify_and_escape(text: str) -> str:
    import html as _html

    escaped = _html.escape(text)
    return URL_RE.sub(r'<a href="\1" target="_blank" rel="noopener">\1</a>', escaped)


@st.cache_data(show_spinner=False, max_entries=CACHE_MAX_ENTRIES)
def get_base64_media(path: str, is_image=False, max_w=400) -> str:
    try:
        if not os.path.exists(path):
            return "MISSING"
            
        file_size_mb = os.path.getsize(path) / (1024 * 1024)
        if file_size_mb > 15.0:  # Batas aman 15MB untuk Streamlit Cloud
            return "TOO_LARGE"

        if is_image:
            try:
                with Image.open(path) as im:
                    # Amankan gambar stiker transparan (RGBA)
                    if im.mode in ("RGBA", "P"):
                        im = im.convert("RGB")
                    if im.width > max_w:
                        ratio = max_w / im.width
                        im = im.resize((max_w, int(im.height * ratio)))
                    buf = io.BytesIO()
                    im.save(buf, format="JPEG", quality=60, optimize=True)
                    return base64.b64encode(buf.getvalue()).decode("ascii")
            except Exception:
                # Jika PIL gagal (misal file .webp stiker rusak), baca mentah (raw bytes)
                with open(path, "rb") as f:
                    return base64.b64encode(f.read()).decode("ascii")
        else:
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode("ascii")
    except Exception:
        return ""


def build_bubble_html(msg: dict, is_me: bool, is_group: bool) -> str:
    side = "me" if is_me else "other"
    time_str = msg["datetime"].strftime("%H:%M") if msg["datetime"] else ""

    sender_html = ""
    if not is_me and is_group:
        color = sender_color(msg["sender"])
        sender_html = f'<div class="wa-sender" style="color:{color};">{msg["sender"]}</div>'

    media_html = ""
    if msg["is_attachment"]:
        # url sekarang berisi string link "app/static/wa_media/..."
        url = msg["media_path"] 
        mime = MIME_MAP.get(msg["media_ext"], "application/octet-stream")

        if url:
            if msg["is_image"]:
                # Pemuatan instan karena browser tinggal download URL
                media_html = f'<div class="wa-img-wrap"><img class="wa-chat-image" src="{url}" loading="lazy" /></div>'
            elif msg["is_video"]:
                media_html = f'<video controls preload="none" class="wa-chat-video"><source src="{url}" type="{mime}"></video>'
            elif msg["is_audio"]:
                media_html = f'<audio controls preload="none" style="width:250px; height:40px; margin-bottom:6px;"><source src="{url}" type="{mime}"></audio>'
            elif msg["is_pdf"]:
                media_html = f'<a class="wa-doc-link" href="{url}" target="_blank">📄 <div><b style="color:#027eb5;">⬇️ Buka/Unduh Dokumen</b><br><small style="color:#667781;">{msg["media_filename"]}</small></div></a>'
        else:
            media_html = f'<div class="wa-img-broken">⚠️ File "{msg["media_filename"] or "media"}" tidak ditemukan di arsip</div>'

    text_html = ""
    if msg["text"]:
        text_html = f'<p class="wa-msg-text">{linkify_and_escape(msg["text"])}<span class="wa-time">{time_str}</span></p>'
    else:
        text_html = f'<p class="wa-msg-text" style="padding-right:0;"><span class="wa-time">{time_str}</span></p>'

    return (
        f'<div class="wa-row {side}">'
        f'  <div class="wa-bubble {side}">'
        f"    {sender_html}"
        f"    {media_html}"
        f"    {text_html}"
        f"  </div>"
        f"</div>"
    )


# =====================================================================================
# 5. JAVASCRIPT — INFINITE SCROLL (ANTI-JUMP) & AUTO-SCROLL AWAL
# =====================================================================================
# Helper JS dipakai berulang di beberapa skrip untuk menemukan elemen yang benar-benar
# menjadi kontainer scroll aktif dari halaman Streamlit (bisa berubah antar versi).
_JS_FIND_SCROLL_PARENT = """
function waGetScrollParent() {
    const doc = window.parent.document;
    const candidates = [
        doc.querySelector('[data-testid="stAppViewContainer"] section.main'),
        doc.querySelector('section[data-testid="stMain"]'),
        doc.querySelector('[data-testid="stAppViewContainer"]'),
        doc.scrollingElement,
        doc.body
    ];
    for (const el of candidates) {
        if (el && el.scrollHeight > el.clientHeight) return el;
    }
    return candidates.find(Boolean) || doc.body;
}
"""

# GANTI (fix bug): sebelumnya di sini ada TOP_DETECT_JS yang memakai Promise +
# scroll-event-listener untuk MENDETEKSI OTOMATIS saat user scroll ke atas, lalu
# memicu Python untuk memuat 300 pesan lama berikutnya tanpa tombol apapun.
#
# Kenapa itu jadi sumber bug ("muat pesan lama gagal", "auto-scroll kacau"):
#  - Listener scroll + Promise itu balapan (race condition) dengan komponen
#    _restore_scroll_component/_scroll_to_bottom_component dari render
#    sebelumnya yang mungkin belum selesai jalan.
#  - Tidak ada kepastian TIMING kapan promise itu resolve relatif terhadap
#    rerun Streamlit berikutnya, jadi kadang trigger dobel, kadang tidak
#    trigger sama sekali.
#
# FIX: sesuai request, mekanisme auto-scroll-ke-atas DIHAPUS dan diganti
# tombol manual "🔼 Muat Pesan Sebelumnya" di atas daftar pesan (lihat main()).
# JS di bawah ini HANYA membaca angka scrollHeight & scrollTop SAAT INI secara
# langsung (bukan menunggu event scroll) — jauh lebih sederhana & deterministik,
# dipakai untuk tahu "tinggi konten SEBELUM tombol diklik" agar posisi baca
# pengguna tidak melompat (anti-jump) setelah pesan lama ditambahkan di atas.
HEIGHT_PROBE_JS = f"""
(function() {{
    {_JS_FIND_SCROLL_PARENT}
    const el = waGetScrollParent();
    if (!el) return null;
    return {{ h: el.scrollHeight, t: el.scrollTop }};
}})()
"""


def _scroll_to_bottom_component():
    st.components.v1.html(
        f"""
        <script>
        {_JS_FIND_SCROLL_PARENT}
        function waScrollBottom() {{
            const el = waGetScrollParent();
            if (el) {{ el.scrollTop = el.scrollHeight; }}
        }}
        waScrollBottom();
        setTimeout(waScrollBottom, 120);
        setTimeout(waScrollBottom, 350);
        </script>
        """,
        height=0,
    )


def _restore_scroll_component(old_height: int, old_top: int = 0):
    """Anti-jump: taruh kembali posisi baca user setelah pesan lama ditambahkan
    di ATAS. old_height/old_top adalah scrollHeight & scrollTop tepat sebelum
    tombol "Muat Pesan Sebelumnya" diklik (diambil dari HEIGHT_PROBE_JS)."""
    st.components.v1.html(
        f"""
        <script>
        {_JS_FIND_SCROLL_PARENT}
        function waRestoreScroll() {{
            const el = waGetScrollParent();
            if (!el) return;
            const oldHeight = {old_height};
            const oldTop = {old_top};
            const newHeight = el.scrollHeight;
            const delta = newHeight - oldHeight;
            if (delta > 0) {{ el.scrollTop = oldTop + delta; }}
        }}
        waRestoreScroll();
        setTimeout(waRestoreScroll, 60);
        setTimeout(waRestoreScroll, 220);
        </script>
        """,
        height=0,
    )


# =====================================================================================
# 6. STATE MANAGEMENT
# =====================================================================================
def ensure_loaded(uploaded_file):
    signature = (uploaded_file.name, uploaded_file.size)
    if st.session_state.get("file_signature") == signature:
        return

    old_tmpdir = st.session_state.get("tmpdir_obj")
    if old_tmpdir and os.path.exists(old_tmpdir):
        try:
            shutil.rmtree(old_tmpdir)
        except Exception:
            pass

    with st.spinner("📦 Membaca file ZIP dan memproses media..."):
        tmp_path, txt_path, media_index = extract_zip(uploaded_file)
        if not txt_path:
            st.error("Tidak ditemukan riwayat _chat.txt di dalam zip.")
            st.stop()

        messages, image_messages = parse_chat(txt_path, media_index)
        if not messages:
            st.error("Gagal membaca isi chat. Format file mungkin tidak dikenali.")
            st.stop()

        senders = sorted({m["sender"] for m in messages})
        contact_name = re.sub(
            r"^WhatsApp Chat( with| -)?\s*", "",
            os.path.splitext(uploaded_file.name)[0], flags=re.I,
        ).strip() or "Chat"

    st.session_state.tmpdir_obj = tmp_path
    st.session_state.messages = messages
    st.session_state.image_messages = image_messages
    st.session_state.senders = senders
    st.session_state.contact_name = contact_name
    st.session_state.file_signature = signature
    st.session_state.limit_chat = CHUNK
    st.session_state.me_sender = senders[-1] if senders else None
    st.session_state.pov_confirmed = False
    st.session_state.initial_scrolled = False
    st.session_state.pending_restore_height = None
    st.session_state.pending_restore_top = 0
    st.session_state.last_scroll_h = None
    st.session_state.last_scroll_t = 0


def reset_state():
    tmpdir_obj = st.session_state.get("tmpdir_obj")
    if tmpdir_obj and os.path.exists(tmpdir_obj):
        try:
            shutil.rmtree(tmpdir_obj)
        except Exception:
            pass
    for key in list(st.session_state.keys()):
        del st.session_state[key]


# =====================================================================================
# 7. MAIN APP LOGIC
# =====================================================================================
def main():
    inject_css()

    # ---------------- LAYAR 1: UPLOAD ----------------
    if "messages" not in st.session_state:
        st.markdown("<br><br>", unsafe_allow_html=True)
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.title("💬 WhatsApp Visualizer")
            st.caption("Aplikasi ringan, cepat, dan aman. File diekstrak ke memori sementara (stateless).")
            st.info("Pilih file **.zip** hasil export chat WhatsApp (sertakan media).")
            uploaded_file = st.file_uploader("Upload file zip", type=["zip"], label_visibility="collapsed")

            if uploaded_file is not None:
                ensure_loaded(uploaded_file)
                st.rerun()
        st.stop()

    # ---------------- LAYAR 2: PILIH SUDUT PANDANG (POV) ----------------
    if not st.session_state.get("pov_confirmed"):
        senders = st.session_state.senders
        st.markdown("<br><br>", unsafe_allow_html=True)
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            initial = st.session_state.contact_name.strip()[:1].upper() or "?"
            st.markdown(f'<div class="wa-avatar-lg">{initial}</div>', unsafe_allow_html=True)
            st.markdown(
                f"<h3 style='text-align:center;'>{st.session_state.contact_name}</h3>",
                unsafe_allow_html=True,
            )
            st.markdown(
                "<p style='text-align:center; color:#667781;'>Chat ini akan ditampilkan dari sudut pandang siapa? "
                "Pesan milik orang yang dipilih akan tampil di sisi kanan (hijau).</p>",
                unsafe_allow_html=True,
            )
            pov = st.selectbox(
                "Tampilkan sebagai sudut pandang:",
                options=senders,
                index=senders.index(st.session_state.me_sender) if st.session_state.me_sender in senders else 0,
            )
            if st.button("Mulai Chat 💬", type="primary", use_container_width=True):
                st.session_state.me_sender = pov
                st.session_state.pov_confirmed = True
                st.rerun()
        st.stop()

    # ---------------- LAYAR 3: CHAT FULLSCREEN ----------------
    messages = st.session_state.messages
    image_messages = st.session_state.image_messages
    senders = st.session_state.senders
    is_group = len(senders) > 2
    total = len(messages)

    # SIDEBAR: pengaturan (tidak termasuk "tombol navigasi chat" yang diminta dihapus)
    with st.sidebar:
        st.markdown("#### ⚙️ Pengaturan Tampilan")
        new_pov = st.selectbox(
            "Sudut pandang (bubble kanan):",
            options=senders,
            index=senders.index(st.session_state.me_sender) if st.session_state.me_sender in senders else 0,
            key="sidebar_pov_select",
        )
        if new_pov != st.session_state.me_sender:
            st.session_state.me_sender = new_pov

        st.divider()
        st.markdown("#### 📊 Statistik")
        st.write(f"Total Pesan: **{total:,}**")
        st.write(f"Total Gambar: **{len(image_messages):,}**")
        st.write(f"Pesan Termuat: **{min(st.session_state.limit_chat, total):,}**")

        st.divider()
        if st.button("⬅️ Tutup Chat & Upload Ulang", type="primary", use_container_width=True):
            reset_state()
            st.rerun()

    # ---------------- PROBE TINGGI SCROLL (pasif, bukan event listener) ----------------
    # Hanya membaca angka scrollHeight/scrollTop SAAT INI, dipakai sebagai acuan
    # "sebelum" ketika tombol "Muat Pesan Sebelumnya" nanti diklik. Tidak ada
    # penantian event scroll -> tidak ada race condition seperti mekanisme lama.
    if st.session_state.get("initial_scrolled") and st.session_state.limit_chat < total:
        probe = st_javascript(HEIGHT_PROBE_JS, key="wa_height_probe")
        if isinstance(probe, dict) and probe.get("h"):
            st.session_state.last_scroll_h = probe.get("h")
            st.session_state.last_scroll_t = probe.get("t", 0)

    # ---------------- TOMBOL MUAT PESAN SEBELUMNYA (manual, di atas daftar pesan) ----------------
    if st.session_state.limit_chat < total:
        col1, col2, col3 = st.columns([1, 1.4, 1])
        with col2:
            if st.button(
                "🔼 Muat Pesan Sebelumnya",
                use_container_width=True,
                key="load_older_btn",
            ):
                # Simpan tinggi & posisi scroll TEPAT SEBELUM pesan lama ditambahkan
                # di atas, supaya setelah rerun kita bisa mengembalikan posisi baca
                # user (anti-jump) alih-alih dia terlempar ke atas/bawah halaman.
                st.session_state.pending_restore_height = st.session_state.last_scroll_h
                st.session_state.pending_restore_top = st.session_state.last_scroll_t
                st.session_state.limit_chat = min(st.session_state.limit_chat + CHUNK, total)
    else:
        st.markdown(
            '<div class="wa-history-start">🔒 Ini adalah awal dari riwayat percakapan.</div>',
            unsafe_allow_html=True,
        )

    visible_messages = messages[-st.session_state.limit_chat:]
    last_date = None

    for msg in visible_messages:
        if msg["date"] != last_date:
            st.markdown(
                f'<div class="wa-date-wrap"><div class="wa-date-pill">{format_date_divider(msg["date"])}</div></div>',
                unsafe_allow_html=True,
            )
            last_date = msg["date"]

        is_me = msg["sender"] == st.session_state.me_sender
        st.markdown(build_bubble_html(msg, is_me, is_group), unsafe_allow_html=True)

    st.markdown('<div class="bottom-spacer"></div>', unsafe_allow_html=True)

    # ---------------- SCROLL HANDLING (SETELAH RENDER) ----------------
    if not st.session_state.get("initial_scrolled"):
        # Pertama kali chat dibuka -> paksa scroll ke pesan terbaru (paling bawah)
        _scroll_to_bottom_component()
        st.session_state.initial_scrolled = True
    elif st.session_state.get("pending_restore_height") is not None:
        # Baru saja memuat pesan lama lewat tombol "Muat Pesan Sebelumnya" ->
        # kembalikan posisi baca pengguna agar tidak melompat (anti-jump)
        old_h = st.session_state.pending_restore_height
        old_t = st.session_state.get("pending_restore_top", 0)
        st.session_state.pending_restore_height = None
        st.session_state.pending_restore_top = 0
        _restore_scroll_component(old_h, old_t)
    # else: rerun biasa (mis. ganti POV di sidebar) -> tidak memaksa scroll sama sekali


if __name__ == "__main__":
    main()