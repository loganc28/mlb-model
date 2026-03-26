# MLB Betting Model

EV-based daily picks generator. Runs automatically every morning via GitHub Actions.

## Setup (20 minutes)

### 1. Get your free API keys

| Service | Where to sign up | Free tier |
|---|---|---|
| The Odds API | https://the-odds-api.com | 500 req/month |
| OpenWeatherMap | https://openweathermap.org/api | 1000 calls/day |
| Anthropic | https://console.anthropic.com | $5 free credit |

### 2. Create a GitHub repo

1. Go to github.com → New repository
2. Name it `mlb-model` (or anything)
3. Upload all files from this folder

### 3. Add your API keys as GitHub Secrets

In your repo: **Settings → Secrets and variables → Actions → New repository secret**

Add these three secrets:
- `ODDS_API_KEY` — from The Odds API dashboard
- `WEATHER_API_KEY` — from OpenWeatherMap
- `ANTHROPIC_API_KEY` — from Anthropic console

### 4. Enable GitHub Actions

Go to the **Actions** tab in your repo and enable workflows if prompted.

### 5. Deploy to Vercel (free hosting)

1. Go to vercel.com → New Project → Import your GitHub repo
2. Set **Output Directory** to `output`
3. Deploy

Vercel will auto-redeploy every time GitHub Actions commits new picks.

### 6. Test it manually

In your repo → **Actions** tab → **Daily MLB Picks** → **Run workflow**

This triggers an immediate run so you can see it working without waiting for the cron.

---

## How it works

```
10:00 AM ET daily (GitHub Actions cron)
        ↓
fetch_mlb_games()     — MLB Stats API (official, free, no key)
        ↓
fetch_odds()          — The Odds API (moneylines + totals)
        ↓
fetch_weather()       — OpenWeatherMap (per stadium coordinates)
        ↓
call_claude()         — Anthropic API analyzes all data, returns EV picks as JSON
        ↓
output/picks.json     — machine-readable picks
output/index.html     — visual dashboard (hosted on Vercel)
        ↓
git commit + push     — triggers Vercel redeploy
```

---

## Adjusting the model

The betting logic lives entirely in `SYSTEM_PROMPT` inside `generate_picks.py`.

To tune the model:
- Change the EV threshold (default: 3% edge minimum)
- Add/remove bet types it considers
- Tighten or loosen the wind rules
- Add pitcher-specific instructions (e.g. "weight FIP over ERA for all SP analysis")

After 30+ days of results, add a `past_performance` section to the prompt with your actual ROI by bet type. The model will factor it in.

---

## Bankroll rules (built into the model)

| Tier | Units |
|---|---|
| A (strong edge) | 1.5u max |
| B (moderate edge) | 1.0u |
| C (lean) | 0.5u |
| Props | 0.5u max |
| Daily max | 5u |

---

## Costs at scale

At free tiers this runs at $0/month for ~20 betting days.
Anthropic cost: ~$0.10–0.15 per day once free credit runs out.
