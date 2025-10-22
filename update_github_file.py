import os
import re
import time as pytime
import requests
from datetime import datetime, date, time as dt_time, timedelta, timezone
from github import Github, GithubException

# ==========================
# KONFIGURASI UTAMA
# ==========================
GITHUB_TOKEN = os.getenv('GITHUB_PAT')  # Ambil dari environment variable

# Gunakan pola raw yang valid: https://raw.githubusercontent.com/<user>/<repo>/<branch>/<path>
SOURCE_URL   = "https://raw.githubusercontent.com/uppermoon77/dadangconelo/main/dadangconelo"

# REPO tujuan (Format: "username/repository")
DEST_REPO    = "uppermoon77/dadangconelo"
GIT_BRANCH   = "main"
COMMIT_MSG   = "Auto update: Sync playlist from source + footer update"
SLEEP_BETWEEN_COMMITS_SEC = 0.7

# Mode: Expiry berdasarkan NAMA REPOSITORY
USE_REPO_NAME_EXPIRY = True
EXPIRE_HOUR_LOCAL = 13    # 13:00 WIB
EXPIRE_MINUTE_LOCAL = 0

# Saat expired, kita tulis marker ini supaya run berikutnya tahu sinkron sudah dimatikan
SYNC_DISABLED_MARKER = ".SYNC_DISABLED"

# TARGET FILES (contoh November 2025). Kamu bisa ganti generator ini sesuai kebutuhan.
def generate_target_files() -> list[str]:
    month = "OKTOBER"
    year = "2025"
    prefix = "DC"
    # CD01OKTOBER2025 ... CD31OKTOBER2025
    return [f"{prefix}{day:02d}{month}{year}" for day in range(1, 31)]

# ==========================
# UTIL TANGGAL & WIB
# ==========================
JAKARTA_TZ = timezone(timedelta(hours=7))

def now_jakarta() -> datetime:
    return datetime.now(tz=JAKARTA_TZ)

def today_jakarta() -> date:
    return now_jakarta().date()

def expiry_cutoff(dt: date) -> datetime:
    """Expire pada Hari-H pukul EXPIRE_HOUR_LOCAL:EXPIRE_MINUTE_LOCAL WIB."""
    return datetime(dt.year, dt.month, dt.day, EXPIRE_HOUR_LOCAL, EXPIRE_MINUTE_LOCAL, tzinfo=JAKARTA_TZ)

# ==========================
# PARSER TANGGAL DARI NAMA REPO
# ==========================
# Dukungan nama bulan Indonesia
ID_MONTHS = {
    "JANUARI": 1, "FEBRUARI": 2, "MARET": 3, "APRIL": 4, "MEI": 5, "JUNI": 6,
    "JULI": 7, "AGUSTUS": 8, "SEPTEMBER": 9, "OKTOBER": 10, "NOVEMBER": 11, "DESEMBER": 12
}

def extract_repo_name(full_repo: str) -> str:
    """Ambil bagian <repo> dari 'user/repo'."""
    if "/" in full_repo:
        return full_repo.split("/", 1)[1]
    return full_repo

def parse_date_from_repo_name(repo_name: str) -> date | None:
    """
    Coba berbagai pola tanggal di nama repository:
    1) DC21NOVEMBER2025 / 21NOVEMBER2025 / 021NOVEMBER2025 (intinya <dd><BULAN_ID><yyyy>)
    2) 21-11-2025 | 21_11_2025 | 21.11.2025 | 21/11/2025
    3) 2025-11-21 | 2025_11_21 | 20251121 | 21112025
    Tidak case sensitive untuk bulan.
    """
    name = repo_name.upper()

    # Pola 1: <optional prefix>DD<BULAN_ID>YYYY (contoh: CD21NOVEMBER2025 / 21NOVEMBER2025)
    m = re.search(r'(\d{1,2})(JANUARI|FEBRUARI|MARET|APRIL|MEI|JUNI|JULI|AGUSTUS|SEPTEMBER|OKTOBER|NOVEMBER|DESEMBER)(\d{4})', name, re.IGNORECASE)
    if m:
        dd = int(m.group(1))
        mm = ID_MONTHS[m.group(2).upper()]
        yyyy = int(m.group(3))
        try:
            return date(yyyy, mm, dd)
        except ValueError:
            pass

    # Pola 2: DD[-_./]MM[-_./]YYYY (21-11-2025, 21_11_2025, 21.11.2025, 21/11/2025)
    m = re.search(r'(\d{1,2})[-_./](\d{1,2})[-_./](\d{4})', name)
    if m:
        dd = int(m.group(1)); mm = int(m.group(2)); yyyy = int(m.group(3))
        try:
            return date(yyyy, mm, dd)
        except ValueError:
            pass

    # Pola 3a: YYYY[-_./]MM[-_./]DD
    m = re.search(r'(\d{4})[-_./](\d{1,2})[-_./](\d{1,2})', name)
    if m:
        yyyy = int(m.group(1)); mm = int(m.group(2)); dd = int(m.group(3))
        try:
            return date(yyyy, mm, dd)
        except ValueError:
            pass

    # Pola 3b: 8 digit rapat: YYYYMMDD atau DDMMYYYY
    m = re.search(r'(\d{8})', name)
    if m:
        digits = m.group(1)
        # Coba YYYYMMDD
        try:
            yyyy = int(digits[0:4]); mm = int(digits[4:6]); dd = int(digits[6:8])
            return date(yyyy, mm, dd)
        except ValueError:
            pass
        # Coba DDMMYYYY
        try:
            dd  = int(digits[0:2]); mm = int(digits[2:4]); yyyy = int(digits[4:8])
            return date(yyyy, mm, dd)
        except ValueError:
            pass

    return None

def is_expired_by_repo_name(full_repo: str) -> bool:
    """True jika sekarang (WIB) >= Hari-H 13:00, berdasar tanggal yang ditemukan di nama repo."""
    if not USE_REPO_NAME_EXPIRY:
        return False
    repo_name = extract_repo_name(full_repo)
    dt = parse_date_from_repo_name(repo_name)
    if not dt:
        print(f"⚠️  Tidak menemukan tanggal di nama repo '{repo_name}'. Lewati expiry berbasis repo.")
        return False
    cutoff = expiry_cutoff(dt)
    now_ = now_jakarta()
    print(f"ℹ️  Repo date = {dt.isoformat()} | Cutoff = {cutoff.isoformat()} | Now = {now_.isoformat()}")
    return now_ >= cutoff

# ==========================
# FOOTER & TEMPLATE
# ==========================
FOOTER_REGEX = r'#EXTM3U billed-msg="[^"]+"'

def generate_footer(dest_file_path: str, expired: bool) -> str:
    if expired:
        return '#EXTM3U billed-msg="MASA BERLAKU HABIS| lynk.id/magelife😎"'
    return f'#EXTM3U billed-msg="😎{dest_file_path}| lynk.id/magelife😎"'

def strip_footer(text: str) -> str:
    return re.sub(FOOTER_REGEX, '', text).strip()

def add_footer(text: str, dest_file_path: str, expired: bool) -> str:
    cleaned = strip_footer(text)
    return f"{cleaned}\n\n{generate_footer(dest_file_path, expired)}\n"

def build_expired_playlist_block() -> str:
    """
    Blok 'MASA BERLAKU HABIS' (sesuai contohmu).
    """
    return (
        '#EXTINF:-1 group-logo="https://i.imgur.com/aVBedkE.jpeg",🔰 MAGELIFE OFFICIAL\n\n'
        '#EXTINF:-1 tvg-id="Iheart80s" tvg-name="Iheart80s" tvg-logo="https://i.imgur.com/CctbVah.jpeg" group-title="🔰 MAGELIFE OFFICIAL", MASA BERLAKU HABIS\n'
        'https://iheart-iheart80s-1-us.roku.wurl.tv/playlist.m3u8\n\n'
        '#EXTINF:-1 group-logo="https://i.imgur.com/XXQ2pQ3.jpeg", ❌ MASA BERLAKU HABIS\n\n'
        '#EXTINF:-1 tvg-id="Iheart80s" tvg-name="Iheart80s" tvg-logo="https://i.imgur.com/XXQ2pQ3.jpeg" group-title="❌ MASA BERLAKU HABIS", MASA BERLAKU HABIS\n'
        'https://iheart-iheart80s-1-us.roku.wurl.tv/playlist.m3u8\n\n'
        '#EXTINF:-1 group-logo="https://i.imgur.com/XXQ2pQ3.jpeg", ❌ MASA BERLAKU HABIS OM\n\n'
        '#EXTINF:-1 tvg-id="Iheart80s" tvg-name="Iheart80s" tvg-logo="https://i.imgur.com/XXQ2pQ3.jpeg" group-title="❌ MASA BERLAKU HABIS OM", MASA BERLAKU HABIS\n'
        'https://iheart-iheart80s-1-us.roku.wurl.tv/playlist.m3u8\n\n'
        '#EXTINF:-1 group-logo="https://i.imgur.com/XXQ2pQ3.jpeg", ❌ MASA BERLAKU HABIS TANTE\n\n'
        '#EXTINF:-1 tvg-id="Iheart80s" tvg-name="Iheart80s" tvg-logo="https://i.imgur.com/XXQ2pQ3.jpeg" group-title="❌ MASA BERLAKU HABIS TANTE", MASA BERLAKU HABIS\n'
        'https://iheart-iheart80s-1-us.roku.wurl.tv/playlist.m3u8\n\n'
        '#EXTINF:-1 group-logo="https://i.imgur.com/bjfYe6g.jpegg", ✅ SILAHKAN RE ORDER\n\n'
        '#EXTINF:-1 tvg-id="Iheart80s" tvg-name="Iheart80s" tvg-logo="https://i.imgur.com/bjfYe6g.jpeg" group-title="✅ SILAHKAN RE ORDER", SILAHKAN RE ORDER\n'
        'https://iheart-iheart80s-1-us.roku.wurl.tv/playlist.m3u8\n\n'
        '#EXTINF:-1 group-logo="https://i.imgur.com/bjfYe6g.jpegg", ✅SILAHKAN RE ORDER OM\n\n'
        '#EXTINF:-1 tvg-id="Iheart80s" tvg-name="Iheart80s" tvg-logo="https://i.imgur.com/bjfYe6g.jpeg" group-title="✅ SILAHKAN RE ORDER OM", SILAHKAN RE ORDER\n'
        'https://iheart-iheart80s-1-us.roku.wurl.tv/playlist.m3u8\n\n'
        '#EXTINF:-1 group-logo="https://i.imgur.com/bjfYe6g.jpegg", ✅SILAHKAN RE ORDER TANTE\n\n'
        '#EXTINF:-1 tvg-id="Iheart80s" tvg-name="Iheart80s" tvg-logo="https://i.imgur.com/bjfYe6g.jpeg" group-title="✅ SILAHKAN RE ORDER TANTE", SILAHKAN RE ORDER\n'
        'https://iheart-iheart80s-1-us.roku.wurl.tv/playlist.m3u8\n\n'
        '#EXTINF:-1 group-logo="https://i.imgur.com/bjfYe6g.jpegg", 📲 Wa 082219213334\n\n'
        '#EXTINF:-1 tvg-id="Iheart80s" tvg-name="Iheart80s" tvg-logo="https://i.imgur.com/bjfYe6g.jpeg" group-title="📲 Wa 082219213334", SILAHKAN RE ORDER\n'
        'https://iheart-iheart80s-1-us.roku.wurl.tv/playlist.m3u8\n\n'
        '#EXTINF:-1 group-logo="https://i.imgur.com/bjfYe6g.jpegg", 📲 Wa 082219213334 order\n\n'
        '#EXTINF:-1 tvg-id="Iheart80s" tvg-name="Iheart80s" tvg-logo="https://i.imgur.com/bjfYe6g.jpeg" group-title="📲 Wa 082219213334 order", SILAHKAN RE ORDER\n'
        'https://iheart-iheart80s-1-us.roku.wurl.tv/playlist.m3u8\n\n'
        '#EXTINF:-1 group-logo="https://i.imgur.com/PJ9tRpK.jpeg",✅ ORDER LYNK\n\n'
        '#EXTINF:-1 tvg-id="Iheart80s" tvg-name="Iheart80s" tvg-logo="https://i.imgur.com/PJ9tRpK.jpeg" group-title="✅ ORDER LYNK", ORDER LYNK\n'
        'https://iheart-iheart80s-1-us.roku.wurl.tv/playlist.m3u8\n\n'
        '#EXTINF:-1 group-logo="https://i.imgur.com/PJ9tRpK.jpeg",✅ https://lynk.id/magelife\n\n'
        '#EXTINF:-1 tvg-id="Iheart80s" tvg-name="Iheart80s" tvg-logo="https://i.imgur.com/PJ9tRpK.jpeg" group-title="✅ https://lynk.id/magelife", ORDER SHOPEE\n'
        'https://iheart-iheart80s-1-us.roku.wurl.tv/playlist.m3u8\n\n'
        '#EXTINF:-1 group-logo="https://i.imgur.com/PJ9tRpK.jpeg", ✅ORDER SHOPEE \n\n'
        '#EXTINF:-1 tvg-id="Iheart80s" tvg-name="Iheart80s" tvg-logo="https://i.imgur.com/EWttwBZ.jpeg" group-title="✅ ORDER SHOPEE", ORDER LYNK\n'
        'https://iheart-iheart80s-1-us.roku.wurl.tv/playlist.m3u8\n\n'
        '#EXTINF:-1 group-logo="https://i.imgur.com/PJ9tRpK.jpeg", ✅ https://shorturl.at/1r9BB\n\n'
        '#EXTINF:-1 tvg-id="Iheart80s" tvg-name="Iheart80s" tvg-logo="https://i.imgur.com/EWttwBZ.jpeg" group-title="✅ https://shorturl.at/1r9BB", ORDER LYNK\n'
        'https://iheart-iheart80s-1-us.roku.wurl.tv/playlist.m3u8\n'
    )

# ==========================
# AMBIL KONTEN (HANYA SAAT BELUM EXPIRED)
# ==========================
def get_source_content() -> str | None:
    try:
        print(f"Mengambil konten dari: {SOURCE_URL} ...")
        headers = {"User-Agent": "MagelifeSync/1.0 (+https://lynk.id/magelife)"}
        r = requests.get(SOURCE_URL, timeout=30, headers=headers)
        r.raise_for_status()
        print("✅ Konten berhasil diambil.")
        return r.text
    except requests.exceptions.RequestException as e:
        print(f"❌ Gagal mengambil konten sumber: {e}")
        return None

# ==========================
# GITHUB HELPER
# ==========================
def ensure_marker(repo, expired_now: bool):
    """
    Jika expired, pastikan marker .SYNC_DISABLED ada (menandakan sinkron dimatikan).
    """
    if not expired_now:
        return
    try:
        repo.get_contents(SYNC_DISABLED_MARKER, ref=GIT_BRANCH)
        print(f"ℹ️  Marker {SYNC_DISABLED_MARKER} sudah ada.")
    except GithubException as e:
        if getattr(e, "status", None) == 404:
            print(f"📝 Membuat marker {SYNC_DISABLED_MARKER} ...")
            repo.create_file(
                path=SYNC_DISABLED_MARKER,
                message="Mark: sync disabled due to expiry",
                content=f"Expired at {now_jakarta().isoformat()} WIB\n",
                branch=GIT_BRANCH
            )
            print("✅ Marker dibuat.")
        else:
            print(f"⚠️  Tidak bisa cek/buat marker: {e}")

def repo_has_marker(repo) -> bool:
    try:
        repo.get_contents(SYNC_DISABLED_MARKER, ref=GIT_BRANCH)
        return True
    except GithubException as e:
        return False

# ==========================
# UPDATE FILE PER ITEM
# ==========================
def update_single_file(g: Github, dest_file_path: str, base_content_no_footer: str, expired_now: bool) -> None:
    """
    expired_now: jika True, paksa tulis konten expired & JANGAN ambil/bandingkan ke sumber normal.
    """
    repo = g.get_repo(DEST_REPO)
    content_body = build_expired_playlist_block() if expired_now else base_content_no_footer
    new_content_with_footer = add_footer(content_body, dest_file_path, expired_now)

    print(f"\n🟦 Memproses file: {dest_file_path} (expired={expired_now})")

    try:
        contents = repo.get_contents(dest_file_path, ref=GIT_BRANCH)
        sha = contents.sha
        old_text = contents.decoded_content.decode("utf-8")
        old_no_footer = strip_footer(old_text)

        # Cek perubahan (tanpa footer)
        if old_no_footer.strip() == content_body.strip():
            print("➡️  Tidak ada perubahan, skip.")
            return

        # Update file jika ada perubahan
        print("✏️  Ada perubahan, memperbarui file...")
        repo.update_file(
            path=contents.path,
            message=COMMIT_MSG,
            content=new_content_with_footer,
            sha=sha,
            branch=GIT_BRANCH
        )
        print("✅ File berhasil di-update!")

    except GithubException as e:
        if getattr(e, "status", None) == 404:
            print("🆕 File belum ada, membuat baru...")
            repo.create_file(
                path=dest_file_path,
                message=COMMIT_MSG,
                content=new_content_with_footer,
                branch=GIT_BRANCH
            )
            print("✅ File baru berhasil dibuat.")
        else:
            print(f"❌ Error API GitHub: {e}")
    except Exception as e:
        print(f"❌ Error tak terduga: {e}")

# ==========================
# MAIN
# ==========================
def main():
    if not GITHUB_TOKEN:
        print("❌ Error: environment variable GITHUB_PAT belum diatur.")
        return

    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(DEST_REPO)

    # 1) Tentukan apakah SUDAH EXPIRED berdasar nama repository + cutoff 13:00 WIB
    expired_now = is_expired_by_repo_name(DEST_REPO)

    # 2) Jika expired, jangan sinkron dari sumber (matikan auto sync) + buat marker
    if expired_now:
        print("⛔ Repo sudah EXPIRED per nama repository (mode 13:00 WIB). Auto sync dimatikan.")
        ensure_marker(repo, expired_now=True)
        base_no_footer = ""  # tidak dipakai saat expired
    else:
        # Kalau belum expired namun marker sudah ada, hormati marker = jangan sync lagi (opsional).
        # Jika kamu ingin mengabaikan marker saat belum expired, set honor_marker = False
        honor_marker = True
        if honor_marker and repo_has_marker(repo):
            print(f"⛔ Ditemukan marker {SYNC_DISABLED_MARKER}. Auto sync tetap dimatikan walau belum lewat tanggal. (Hormat marker)")
            expired_now = True
            base_no_footer = ""
        else:
            # 3) Ambil konten sumber NORMAL (hanya jika belum expired dan tidak ada marker)
            src = get_source_content()
            if src is None:
                print("❌ Gagal ambil sumber dan belum expired. Stop.")
                return
            base_no_footer = strip_footer(src)

    # 4) Proses semua file target
    target_files = generate_target_files()
    print(f"\n📁 Daftar file target ({len(target_files)}):")
    print(target_files)

    for idx, dest_file_path in enumerate(target_files, start=1):
        print(f"\n({idx}/{len(target_files)}) Mulai update {dest_file_path}...")
        update_single_file(g, dest_file_path, base_no_footer, expired_now)
        pytime.sleep(SLEEP_BETWEEN_COMMITS_SEC)

    print("\n🎯 Semua file selesai diproses!")

if __name__ == "__main__":
    main()
