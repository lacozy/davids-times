# The David Times — Personal Daily Kindle Newspaper

A zero-API-key, NYTimes-inspired daily newspaper built specifically for **6.8"–7" grayscale jailbroken Kindles running KOReader** (Paperwhite 11th/12th gen, Oasis).

Every morning, it automatically compiles:
- **Classic NYT Broadsheet Front Page** — Blackletter masthead, ear-box motto, live **San Francisco weather box** (with monochrome e-ink SVG icons via Open-Meteo), dateline folio bar, `"Inside This Issue"` navigation with real page numbers, full hero lead story with drop cap, `"Morning Briefing · In Brief"` extractive summary box, and a personal desk featured story.
- **Dedicated Curated Sections + KOReader TOC** — **Top News**, **San Francisco** (with Morning / Afternoon / Evening SF metro weather strip), **Gaming**, **Movies**, **Television**, **Software & CS** (Hacker News front page, Ars Technica, Lobste.rs, The Register), and **Markets** (live S&P 500 / Dow / Nasdaq snapshot, Biggest Gainers & Losers, Upcoming Notable Earnings calendar, plus CNBC/MarketWatch/BBC Business coverage), all typeset in crisp two-column editorial typography with e-ink–optimized grayscale photographs and nested PDF Table of Contents bookmarks.
- **News Summary Back Page** — Quick 2-column wire digest of additional stories so nothing slips past you.
- **Heavy-Hitting Front-Page Curation (Zero API Keys)** — Pulls live editorial front-page headlines directly from **CNN**, **BBC News**, **NPR**, **SFGate**, and **Rock Paper Shotgun** (plus **KQED**, **Mission Local**, **The SF Standard**, **VGC**, **IGN**, **Eurogamer**, **PC Gamer**, **Kotaku**, and **Gematsu**), and ranks stories by **homepage hero prominence + cross-outlet consensus** (benchmarked against NYTimes Front Page RSS) with local full-article extraction (`trafilatura`) and Open-Meteo weather.

---

## Quick Start (Run Locally)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Generate today's issue locally (uses Chrome/Edge if already installed, or run `playwright install chromium`)
python -m src.main --output output/latest.pdf --publish-dir dist
```

Open `output/latest.pdf` to preview your daily edition!

---

## Customizing Your Paper (`config.yaml`)

Everything is controlled from [`config.yaml`](config.yaml):
- **Masthead & Identity**: Edit `paper.title`, `paper.motto`, `paper.edition`, `paper.timezone`.
- **Interests & Filters**:
  - Add favorite games, SF neighborhoods, studios, or topics to `interests.boost_keywords` to float matching articles to the top.
  - Add noisy topics (e.g., promo deals, Wordle hints) to `interests.mute_keywords` to keep them out.
- **Sections & RSS Feeds**: Add, remove, or reorder feeds under `sections:`.
- **Kindle Screen Sizing**: Defaults to `108mm × 144mm` (exact 3:4 aspect ratio for 6.8"–7" Paperwhite/Oasis).

---

## Automated Daily Delivery (GitHub Actions + KOReader)

### Step 1 — Push to GitHub & Enable GitHub Pages (Free, Zero Secrets)
1. Push this repository to GitHub (e.g., `github.com/<your-username>/davids-times`).
2. Go to **Actions → Daily Kindle Newspaper → Run workflow** to generate your first issue.
3. Once finished, go to **Settings → Pages → Build and deployment**:
   - **Source**: `Deploy from a branch`
   - **Branch**: `gh-pages` / `/ (root)`
4. Your paper is now live every morning at 5:30 AM PT at:
   - Latest PDF: `https://<your-username>.github.io/davids-times/latest.pdf`
   - KOReader OPDS Catalog: `https://<your-username>.github.io/davids-times/opds.xml`
   - Rolling 14-day Web Archive: `https://<your-username>.github.io/davids-times/`

### Step 2 — Pull Automatically on Your Jailbroken Kindle (Choose Either Method!)

#### Option A: Built-in KOReader OPDS Catalog (Zero Plugins Needed)
1. Open **KOReader** on your Kindle.
2. Tap the top menu → **Search (magnifying glass)** → **OPDS catalog** → **+ (Add catalog)**.
3. Enter:
   - **Catalog name**: `The David Times`
   - **Catalog URL**: `https://<your-username>.github.io/davids-times/opds.xml`
4. Tap **The David Times** any morning to download today's edition (or any of the last 14 days)!

#### Option B: One-Tap & Auto-Download on Wi-Fi (`davidtimes.koplugin`)
1. Connect your Kindle via USB and copy the folder `koreader/davidtimes.koplugin` into:
   ```
   /mnt/us/koreader/plugins/davidtimes.koplugin
   ```
2. Restart KOReader.
3. Open the top menu → **Tools (wrench icon)** → **The David Times**:
   - Tap **Set newspaper URL…** and enter `https://<your-username>.github.io/davids-times`
   - Toggle **Auto-download when Wi-Fi connects** ON if you want today's paper pulled automatically the moment your Kindle wakes on Wi-Fi!
   - Tap **Download & open today's issue** anytime (or bind it to a KOReader gesture!).

---

## Recommended KOReader View Settings for This PDF
Because the PDF is natively typeset for a 6.8"–7" 3:4 screen:
- Set **Zoom mode** to **Fit Page** (bottom menu → page icon).
- Use KOReader's **Table of Contents** icon to jump directly between `Front Page`, `Top News`, `San Francisco`, `Gaming`, and individual articles!
