"""
Google Photos → Discord sync
Runs every 24 hours, uploads new photos as Discord embeds.
"""

import os
import io
import time
import logging
import tempfile
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ── Config ────────────────────────────────────────────────────────────────────

load_dotenv()

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
CREDENTIALS_FILE    = "credentials.json"
TOKEN_FILE          = "token.json"          # saved after first OAuth login
SCOPES              = ["https://www.googleapis.com/auth/photoslibrary.readonly"]

MAX_FILE_BYTES      = 8 * 1024 * 1024       # 8 MB
RATE_LIMIT_PER_MIN  = 25                    # max Discord uploads per minute
SYNC_INTERVAL_SECS  = 86400                 # 24 hours

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Google Photos auth ────────────────────────────────────────────────────────

def get_google_service():
    """Return an authenticated Google Photos service client."""
    creds = None

    # Re-use a previously saved token if available
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    # If there's no valid token, run the OAuth flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        # Save the token so we don't have to log in every run
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build("photoslibrary", "v1", credentials=creds, static_discovery=False)


# ── Fetch new photos ──────────────────────────────────────────────────────────

def fetch_new_photos(service):
    """
    Return a list of media items created in the last 24 hours.
    Each item is the raw dict from the Google Photos API.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=24)

    filters = {
        "dateFilter": {
            "ranges": [
                {
                    "startDate": {
                        "year":  since.year,
                        "month": since.month,
                        "day":   since.day,
                    },
                    "endDate": {
                        "year":  datetime.now(timezone.utc).year,
                        "month": datetime.now(timezone.utc).month,
                        "day":   datetime.now(timezone.utc).day,
                    },
                }
            ]
        }
    }

    photos = []
    page_token = None

    while True:
        body = {"pageSize": 100, "filters": filters}
        if page_token:
            body["pageToken"] = page_token

        response = service.mediaItems().search(body=body).execute()
        items = response.get("mediaItems", [])
        photos.extend(items)

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    log.info("Found %d photo(s) from the last 24 hours.", len(photos))
    return photos


# ── Download a photo ──────────────────────────────────────────────────────────

def download_photo(item):
    """
    Download a media item to a temporary file.
    Returns (file_path, size_bytes) or raises on error.
    The caller is responsible for deleting the temp file.
    """
    # Append =d to the base URL to get the raw download
    url = item["baseUrl"] + "=d"
    response = requests.get(url, timeout=60)
    response.raise_for_status()

    data = response.content
    size = len(data)

    # Write to a named temp file so we can pass the path to Discord
    suffix = os.path.splitext(item.get("filename", "photo.jpg"))[1] or ".jpg"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(data)
    tmp.close()

    return tmp.name, size


# ── Send to Discord ───────────────────────────────────────────────────────────

def send_to_discord(item, file_path):
    """
    Upload the photo to Discord as an embed.
    The image is attached as a file and referenced inside the embed so it
    displays large — no external hosting needed.
    """
    filename = os.path.basename(file_path)

    # Pull the creation timestamp out of the metadata
    creation_time_str = (
        item.get("mediaMetadata", {}).get("creationTime", "")
    )
    try:
        taken_at = datetime.fromisoformat(creation_time_str.replace("Z", "+00:00"))
        # Discord ISO 8601 timestamp for the embed footer / timestamp field
        timestamp = taken_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, AttributeError):
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    embed = {
        "timestamp": timestamp,
        "image": {"url": f"attachment://{filename}"},
        # No title or description — just the photo and the date
    }

    payload = {"embeds": [embed]}

    with open(file_path, "rb") as f:
        response = requests.post(
            DISCORD_WEBHOOK_URL,
            data={"payload_json": str(payload).replace("'", '"')},
            files={"file": (filename, f, "image/jpeg")},
            timeout=30,
        )

    response.raise_for_status()


# ── Main sync job ─────────────────────────────────────────────────────────────

def sync():
    """Fetch photos from the last 24 hours and post them to Discord."""
    log.info("=== Sync started ===")

    if not DISCORD_WEBHOOK_URL:
        log.error("DISCORD_WEBHOOK_URL is not set. Check your .env file.")
        return

    service = get_google_service()
    photos  = fetch_new_photos(service)

    if not photos:
        log.info("No new photos. Nothing to do.")
        return

    uploaded = 0
    skipped  = 0
    upload_times = []  # track when each upload happened for rate limiting

    for item in photos:
        name = item.get("filename", item.get("id", "unknown"))

        # ── Download ───────────────────────────────────────────────────────
        try:
            file_path, size = download_photo(item)
        except Exception as e:
            log.warning("SKIP  %-40s  download failed: %s", name, e)
            skipped += 1
            continue

        # ── Size check ────────────────────────────────────────────────────
        if size > MAX_FILE_BYTES:
            log.info("SKIP  %-40s  %.1f MB > 8 MB limit", name, size / 1024 / 1024)
            os.unlink(file_path)
            skipped += 1
            continue

        # ── Rate limiting: max 25 uploads per 60 seconds ──────────────────
        now = time.monotonic()
        # Drop timestamps older than 60 seconds
        upload_times = [t for t in upload_times if now - t < 60]
        if len(upload_times) >= RATE_LIMIT_PER_MIN:
            wait = 60 - (now - upload_times[0])
            log.info("Rate limit reached — waiting %.1f seconds.", wait)
            time.sleep(wait)

        # ── Upload ────────────────────────────────────────────────────────
        try:
            send_to_discord(item, file_path)
            upload_times.append(time.monotonic())
            log.info("OK    %-40s  %.1f KB", name, size / 1024)
            uploaded += 1
        except Exception as e:
            log.warning("FAIL  %-40s  %s", name, e)
            skipped += 1
        finally:
            os.unlink(file_path)  # always clean up the temp file

    log.info("=== Sync done — uploaded: %d  skipped: %d ===", uploaded, skipped)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("Starting Google Photos → Discord sync (every 24 hours)")

    while True:
        try:
            sync()
        except Exception as e:
            log.error("Unhandled error during sync: %s", e, exc_info=True)

        log.info("Next sync in 24 hours.")
        time.sleep(SYNC_INTERVAL_SECS)
