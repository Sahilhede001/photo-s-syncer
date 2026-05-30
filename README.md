# Google Photos → Discord Sync

Automatically posts photos from your Google Photos library to a Discord channel every 24 hours.

## What it does

- Fetches photos added in the last 24 hours from Google Photos
- Uploads each one to Discord as an embed with the photo's taken-at timestamp
- Skips files over 8 MB
- Throttles to 25 uploads/minute to stay within Discord's rate limits
- Loops forever — runs once, sleeps 24 hours, repeats

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Get Google credentials

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a project and enable the **Google Photos Library API**
3. Go to **APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID**
4. Application type: **Desktop app**
5. Download the JSON and save it as `credentials.json` in the project root

### 4. Set your Discord webhook

Create a `.env` file in the project root:

```
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_ID/YOUR_TOKEN
```

To get a webhook URL: Discord server → **Settings → Integrations → Webhooks → New Webhook → Copy URL**

### 5. Run

```bash
python main.py
```

The first run opens a browser for Google OAuth login. After you approve it, `token.json` is saved and all future runs are fully automatic.

## Files

| File | Description |
|------|-------------|
| `main.py` | The script |
| `requirements.txt` | Python dependencies |
| `credentials.json` | Google OAuth credentials — **not committed, you provide this** |
| `token.json` | Saved OAuth token — **auto-generated on first run, not committed** |
| `.env` | Discord webhook URL — **not committed, you provide this** |

## Logs

Every run prints what was uploaded and what was skipped:

```
2025-01-15 09:00:00  INFO      === Sync started ===
2025-01-15 09:00:02  INFO      Found 3 photo(s) from the last 24 hours.
2025-01-15 09:00:04  INFO      OK    IMG_4821.jpg                             142.3 KB
2025-01-15 09:00:05  INFO      OK    IMG_4822.jpg                             98.7 KB
2025-01-15 09:00:06  INFO      SKIP  IMG_4823.jpg                             9.1 MB > 8 MB limit
2025-01-15 09:00:06  INFO      === Sync done — uploaded: 2  skipped: 1 ===
2025-01-15 09:00:06  INFO      Next sync in 24 hours.
```
