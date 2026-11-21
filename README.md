# RxScan

[![Build & publish image](https://github.com/mstras/rxscan/actions/workflows/docker.yml/badge.svg)](https://github.com/mstras/rxscan/actions/workflows/docker.yml)

Take a photo of a prescription receipt (or Rx bag label). A vision model pulls each prescription into columns, you review and edit the values, and they are appended to any Google Sheet/tab you choose.

- Mobile-first: "Take photo" opens the rear camera; batch-pick from the library (HEIC ok)
- One receipt with several prescriptions → several rows
- Header-aware: an empty tab gets a header row; a tab that already has headers gets values placed by matching header names (other columns are left blank)
- Rx numbers, NDCs, phone numbers and transaction IDs keep leading zeros
- Recent sheets and the last-used tab are remembered per device

## Setup

### 1. Run it
You only need the compose file. The image is prebuilt for amd64 and arm64 at `ghcr.io/mstras/rxscan`.
```bash
mkdir -p /mnt/two/Containers/rxscan && cd /mnt/two/Containers/rxscan
curl -fsSLO https://raw.githubusercontent.com/mstras/rxscan/main/docker-compose.yml
docker compose up -d          # http://homeserv:8096 · sign in with admin / admin123
```
`.env` is optional; copy `.env.example` if you want to set things there instead of in the app. `./data` is created and its permissions fixed automatically. Set `PUID`/`PGID` in the compose file to change which user owns it.

**Update:** `docker compose pull && docker compose up -d`. Or let Dockhand/Watchtower do it, since `:latest` tracks the `main` branch. Pin a release with `image: ghcr.io/mstras/rxscan:1.0` after tagging `v1.0.0`.

**Build from source instead:** `docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build`

### 2. Add your vision API key
You can put the API key and model in `.env`, or leave them blank and enter them in the app: tap the ⚙︎ gear, paste the key, tap **Load list** to pull the models your key can use, then **Test connection** and **Save**. Values saved in the app are stored in `data/settings.json` (mode 600) and override `.env`. Fields you don't change keep following `.env`.

### 3. Connect Google Sheets (one time, ~2 min)
Tap **Connect Google Sheets** (or ⚙︎ → Google Sheets connection):
1. **Copy setup script.** It's a small Apps Script (`app/apps_script.gs`) with a secret unique to your server built in.
2. Open script.google.com → New project, paste over the code, and save.
3. **Deploy → New deployment → Web app**. Execute as **Me**, who has access **Anyone**. Deploy and authorize; on the "unverified app" warning, click Advanced → Go to project.
4. Paste the `/exec` URL into RxScan and tap **Connect**.

After that, the app lists your sheets in a dropdown and **＋ New sheet** creates one with the headers already set. Nothing needs sharing, because the script runs as you. "Anyone" access only means the URL works without a Google login; every call must carry the secret. If you regenerate the secret or edit the script, re-paste it and choose Deploy → Manage deployments → Edit → **New version**, which keeps the same URL.

The script asks for Drive access only so it can list your spreadsheets.

<details><summary>Alternative: service account</summary>

Create a service account in Google Cloud with the Sheets API enabled, download the JSON key to `config/service-account.json`, uncomment that volume in `docker-compose.yml`, and share each sheet with the account's `client_email` as an Editor. It can't list or create sheets.
</details>
To test the UI without an API key, set `MOCK_PARSER=true`.

### Exposing via Pangolin
Point a resource (e.g. `rxscan.strasserco.com`) at `192.168.50.229:8096`. These are medical records, so put it behind Pangolin SSO **and/or** set `APP_PASSWORD`. HTTPS is also what makes the app installable (see below).

## Install as an app (PWA)
RxScan is a full Progressive Web App:
- **Installs to your home screen** with its own icon, opens full-screen, and has a "Scan a receipt" shortcut (long-press the icon on Android).
- **Opens offline.** Photos taken without a connection are saved and parsed automatically once the server is reachable again: when the phone reconnects, when you reopen the app, or within 30 seconds.
- **Nothing is lost if the app is closed.** Photos and un-appended rows are kept on the device (IndexedDB) until you append or remove them. The icon badge shows how many are waiting, where the platform supports it.
- **Android share sheet:** share photos from Google Photos/Gallery straight to RxScan.
- **Updates:** after you rebuild the container, the app shows "A new version is ready → Reload".

**HTTPS is required.** Browsers only allow installing and offline mode on `https://` (or `localhost`), so use the Pangolin URL, not `http://homeserv:8096`.
- **iPhone:** open the URL in Safari → Share → **Add to Home Screen**.
- **Android/desktop Chrome:** tap the **Install** button in the app header (or the browser menu → Install app).

**Signing in from the installed app:** an installed iPhone app has its own cookie storage, separate from Safari, so you sign in once more inside the app (and to Pangolin too, if its login is on).

API responses are never cached by the service worker. Receipt data lives only on your device until appended, then in your sheet.

## Receipt copies
When you append, RxScan saves a copy of each receipt photo and adds a **Receipt** column to the sheet with a "View receipt" link. Copies are upright JPEGs up to 3000 px.
- **Google Drive** (default when the Apps Script connection is set up): saved to the folder you pick in **Settings → Receipt copies → Change**. Browse My Drive or "Shared with me", create a folder, or paste a folder link (works for shared drives). Default: `My Drive › RxScan Receipts`. Files are private to your Google account unless the folder is shared.
- **This server:** saved to `/data/receipts` by default. To use another folder on homeserv or a NAS, mount it in `docker-compose.yml` (for example `- /mnt/two/Receipts:/receipts`, writable by UID 1000) and set **Server folder** to `/receipts`. Links point at RxScan (sign-in required). Set `PUBLIC_URL` if the links come out with the wrong host.
- **Subfolder per year** (on by default): Drive `<folder>/2026/…`, server `<dir>/2026/11/…`. Turn it off to keep everything in one folder.
- **Off:** no copies; the Receipt column stays empty.

Photos are only saved when you append, and a failed append that you retry reuses the copies already saved. On a sheet that has its own headers, a Receipt column is added at the end automatically.

**Upgrading the bridge script** (needed for receipt copies and the folder picker): Settings shows "Your bridge script needs an update". Copy the setup script, paste it over the old code in Apps Script, then Deploy → Manage deployments → ✏️ → Version: **New version** → Deploy. The URL stays the same. The first upload asks Google for permission again, because the script now creates files.

## Sign-in
Sign-in is always on. The built-in login is **admin / admin123**; set `APP_USERNAME` and `APP_PASSWORD` in `.env` to override it. You get a sign-in page, and the session lasts `SESSION_DAYS` (default 90). It works inside the installed app.
- Everything is protected (the page, every `/api/*` call, settings). Only static files (icons, scripts, manifest, service worker) and `/healthz` are public; none of them contain data.
- Scripts can use HTTP Basic instead: `curl -u admin:admin123 https://rxscan.example.com/api/config`.
- After 8 failed attempts, an IP is locked out for 15 minutes. Failed sign-ins appear in `docker compose logs`.
- Sessions are signed with a random key in `data/session.key`. Change the password, or delete that file and restart, to sign everyone out. **Settings → Sign out** signs out this device.
- If your session expires mid-scan, your photos and rows are kept. You go to sign in and come back to them.

## Columns
Date Filled, Patient, Medication, Strength, Form, Quantity, Days Supply, Refills Left, Rx Number, NDC, Prescriber, Pharmacy, Pharmacy Address, Pharmacy Phone, Insurance, Retail Price, Insurance Paid, Savings, You Paid, Payment Method, Transaction ID, Notes, Source Image, Added At

To add, remove, rename or reorder columns, edit `app/fields.py`. That one list drives the model schema, the UI and the sheet headers. If you rename a header in an existing sheet, keep it matching the header text there, or match the field key (e.g. `patient_paid`).

## Vision options
| Provider | Notes |
|---|---|
| `gemini` (default) | Get a key at aistudio.google.com → API keys. `gemini-3.8-flash` is the recommended model. `gemini-3.5-flash-lite` is cheaper and faster but may slip on faded receipts. `gemini-4-argon` is overkill for this. On the free tier, Google may use your prompts to improve its products, so turn on billing if you don't want receipt images used that way. |
| `anthropic` | `claude-sonnet-5-5`. Very accurate on crumpled thermal receipts. Roughly a cent or two per image. |
| `ollama` | Fully local. Needs a vision model (`qwen2.5vl:7b`, `llama3.2-vision`, `gemma3`). On a CPU-only box expect about 1–3 minutes per image and weaker accuracy on small print. |

## Publishing (maintainers)
Pushing to `main` runs `.github/workflows/docker.yml`. It builds the image, starts it and checks it responds (health, sign-in, service worker, permission drop), then pushes `linux/amd64` and `linux/arm64` to GHCR as `:latest` and `:sha-xxxxxxx`. A `v1.2.3` tag also publishes `:1.2.3` and `:1.2`. Pull requests are built and tested but not pushed.

The first publish creates the package as **private**. To pull without logging in, go to GitHub → your profile → Packages → rxscan → Package settings → Change visibility → Public. Otherwise, on the server, run `echo <PAT with read:packages> | docker login ghcr.io -u mstras --password-stdin` once.

## API
- `GET /api/config`: field list, service account email, vision model
- `GET/PUT /api/settings`: provider, keys (masked on read), models, defaults
- `POST /api/settings/models`, `POST /api/settings/test`: list models / test a key
- `POST /api/parse`: multipart `file` → `{is_prescription_receipt, rows:[...]}`
- `POST /api/sheet`: `{sheet: url|id}` → title and tabs
- `POST /api/append`: `{sheet, tab, rows}` → append result
- `GET /healthz`

## Privacy notes
- Images are processed in memory and never stored on disk.
- The model is told never to output full card numbers (last 4 at most).
