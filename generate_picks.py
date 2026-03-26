"""
MLB Betting Model — Daily Picks Generator
Runs every morning, outputs picks to output/picks.json and output/index.html

APIs used (all free):
  - MLB Stats API     : no key needed
  - The Odds API      : free tier (500 req/month) — set ODDS_API_KEY in env
  - OpenWeatherMap    : free tier — set WEATHER_API_KEY in env
  - Anthropic API     : set ANTHROPIC_API_KEY in env
"""

import os, json, datetime, requests
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
ODDS_API_KEY    = os.environ.get("ODDS_API_KEY", "")
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY", "")
ANTHROPIC_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
OUTPUT_DIR      = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

TODAY = datetime.date.today().isoformat()

# ── Stadium coordinates for weather lookup ────────────────────────────────────
STADIUMS = {
    "New York Mets":          (40.7571, -73.8458),
    "New York Yankees":       (40.8296, -73.9262),
    "Boston Red Sox":         (42.3467, -71.0972),
    "Tampa Bay Rays":         (27.7683, -82.6534),
    "Baltimore Orioles":      (39.2838, -76.6218),
    "Toronto Blue Jays":      (43.6414, -79.3894),
    "Chicago White Sox":      (41.8300, -87.6338),
    "Chicago Cubs":           (41.9484, -87.6553),
    "Milwaukee Brewers":      (43.0280, -87.9712),
    "Minnesota Twins":        (44.9817, -93.2775),
    "Cleveland Guardians":    (41.4962, -81.6852),
    "Detroit Tigers":         (42.3390, -83.0485),
    "Kansas City Royals":     (39.0517, -94.4803),
    "Houston Astros":         (29.7572, -95.3555),
    "Texas Rangers":          (32.7513, -97.0832),
    "Los Angeles Angels":     (33.8003, -117.8827),
    "Oakland Athletics":      (37.7516, -122.2005),
    "Seattle Mariners":       (47.5914, -122.3325),
    "Los Angeles Dodgers":    (34.0739, -118.2400),
    "San Francisco Giants":   (37.7786, -122.3893),
    "San Diego Padres":       (32.7076, -117.1570),
    "Arizona Diamondbacks":   (33.4453, -112.0667),
    "Colorado Rockies":       (39.7559, -104.9942),
    "Atlanta Braves":         (33.8908, -84.4678),
    "Miami Marlins":          (25.7781, -80.2197),
    "Philadelphia Phillies":  (39.9061, -75.1665),
    "Washington Nationals":   (38.8730, -77.0074),
    "Pittsburgh Pirates":     (40.4469, -80.0058),
    "Cincinnati Reds":        (39.0979, -84.5082),
    "St. Louis Cardinals":    (38.6226, -90.1928),
}

# ── Step 1: Fetch today's MLB games from official MLB Stats API ───────────────
def fetch_mlb_games():
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={TODAY}&hydrate=probablePitcher,linescore,team"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        games = []
        for date_entry in data.get("dates", []):
            for g in date_entry.get("games", []):
                # Only scheduled/pre-game games
                status = g.get("status", {}).get("abstractGameState", "")
                if status not in ("Preview", "Pre-Game", "Scheduled"):
                    continue
                home = g["teams"]["home"]["team"]["name"]
                away = g["teams"]["away"]["team"]["name"]
                game_time = g.get("gameDate", "")
                home_sp = g["teams"]["home"].get("probablePitcher", {}).get("fullName", "TBD")
                away_sp = g["teams"]["away"].get("probablePitcher", {}).get("fullName", "TBD")
                games.append({
                    "home": home,
                    "away": away,
                    "game_time": game_time,
                    "home_sp": home_sp,
                    "away_sp": away_sp,
                    "venue": g.get("venue", {}).get("name", ""),
                })
        return games
    except Exception as e:
        print(f"MLB API error: {e}")
        return []

# ── Step 2: Fetch odds from The Odds API ──────────────────────────────────────
def fetch_odds():
    if not ODDS_API_KEY:
        print("No ODDS_API_KEY — skipping odds fetch")
        return {}
    url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": "h2h,totals",
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        events = r.json()
        odds_map = {}
        for event in events:
            home = event.get("home_team", "")
            away = event.get("away_team", "")
            key = f"{away}@{home}"
            ml = {}
            total = {}
            for bookmaker in event.get("bookmakers", [])[:1]:  # take first book
                for market in bookmaker.get("markets", []):
                    if market["key"] == "h2h":
                        for outcome in market["outcomes"]:
                            ml[outcome["name"]] = outcome["price"]
                    elif market["key"] == "totals":
                        for outcome in market["outcomes"]:
                            if outcome["name"] == "Over":
                                total["line"] = outcome.get("point", "")
                                total["over"] = outcome["price"]
                            elif outcome["name"] == "Under":
                                total["under"] = outcome["price"]
            odds_map[key] = {"moneyline": ml, "total": total}
        return odds_map
    except Exception as e:
        print(f"Odds API error: {e}")
        return {}

# ── Step 3: Fetch weather for each home stadium ───────────────────────────────
def fetch_weather(team_name):
    coords = STADIUMS.get(team_name)
    if not coords or not WEATHER_API_KEY:
        return {"temp_f": "N/A", "wind_mph": "N/A", "wind_dir": "N/A", "precip_pct": "N/A"}
    lat, lon = coords
    url = "https://api.openweathermap.org/data/2.5/forecast"
    params = {"lat": lat, "lon": lon, "appid": WEATHER_API_KEY, "units": "imperial", "cnt": 8}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        # take the first forecast entry (closest to game time)
        entry = data["list"][0]
        wind_mph = round(entry["wind"]["speed"] * 2.237, 1)  # m/s to mph
        wind_deg = entry["wind"].get("deg", 0)
        dirs = ["N","NE","E","SE","S","SW","W","NW"]
        wind_dir = dirs[round(wind_deg / 45) % 8]
        temp_f = round(entry["main"]["temp"])
        precip_pct = round(entry.get("pop", 0) * 100)
        return {"temp_f": temp_f, "wind_mph": wind_mph, "wind_dir": wind_dir, "precip_pct": precip_pct}
    except Exception as e:
        print(f"Weather error for {team_name}: {e}")
        return {"temp_f": "N/A", "wind_mph": "N/A", "wind_dir": "N/A", "precip_pct": "N/A"}

# ── Step 4: Build prompt and call Claude ─────────────────────────────────────
SYSTEM_PROMPT = """You are a sharp MLB betting analyst. Your only job is to find positive expected value (EV) bets.
You think like a professional handicapper, not a fan. You are ruthless about skipping games with no edge.

═══ CORE PHILOSOPHY ═══
- EV = (win probability × potential profit) - (loss probability × stake)
- Only recommend bets where your estimated win probability beats the implied odds by at least 3%
- It is ALWAYS better to have 0 picks than bad picks. Passing is a valid and often correct decision.
- Never chase action. Never recommend a bet just to have something on the slate.

═══ WHAT TO EVALUATE FOR EVERY GAME ═══

1. STARTING PITCHER QUALITY (highest weight)
   - Use FIP and xFIP over ERA — ERA is luck-influenced, FIP is skill
   - K/9 and BB/9 matter more than wins/losses
   - Pitcher handedness vs. opposing lineup's L/R splits
   - Pitcher's historical performance at this specific ballpark
   - Days of rest (pitchers on extra rest outperform, short rest underperform)
   - Spring training ERA is noisy — discount it heavily unless the gap is extreme (4.00+ difference)

2. WEATHER (second highest weight for totals)
   - Wind 12+ mph BLOWING OUT toward CF/LCF/RCF = strong OVER lean on totals
   - Wind 12+ mph BLOWING IN from CF = strong UNDER lean on totals
   - Wind blowing across the diamond (L-R or R-L) = mild effect, slight over lean
   - Temp above 80°F = ball carries further, adds ~0.3-0.5 runs to total
   - Temp below 50°F = ball dies, subtracts ~0.3-0.5 runs from total
   - Dome stadiums: weather is IRRELEVANT, do not factor it in
   - Rain 40%+ chance = postponement risk, flag the game

3. PARK FACTORS
   - Extreme overs parks: Coors Field (Colorado), Great American Ball Park (Cincinnati), Globe Life (Texas)
   - Extreme unders parks: Petco Park (San Diego), Oracle Park (SF), T-Mobile Park (Seattle)  
   - Neutral parks: most others, small adjustments only

4. LINE VALUE
   - Always calculate implied probability from the American odds
   - Positive odds: implied% = 100 / (odds + 100)
   - Negative odds: implied% = |odds| / (|odds| + 100)
   - Your edge = your win prob% - implied prob%
   - Minimum edge to recommend: 3% for ML bets, 4% for totals
   - Never recommend ML bets worse than -200 (requires 67%+ win rate to be profitable)
   - Heavy favorites (-180 or worse) are almost always overpriced — skip unless edge is clear

5. SITUATIONS TO AUTOMATICALLY SKIP
   - SP listed as TBD
   - Game already started or in progress
   - Dome stadium with no other edge identified
   - Both pitchers are unknown rookies with no MLB sample
   - Line is -200 or worse with no clear analytical edge
   - Back-to-back games where bullpen usage is unknown

═══ BET TYPE PRIORITY ═══
1. Game totals (OVER/UNDER) — most reliable, weather edge is clear
2. F5 totals (first 5 innings) — isolates SP quality, removes bullpen variance
3. Run line (+1.5 or -1.5) — better value than ML in most cases
4. Moneyline — only when edge is very clear and juice is reasonable
5. Team totals — useful when one SP is clearly dominant vs the other

═══ BANKROLL RULES ═══
- Tier A (edge 7%+): 1.5 units — strong, well-supported edge
- Tier B (edge 4-6%): 1.0 unit — solid edge
- Tier C (edge 3%): 0.5 units — lean, small exposure only
- SKIP: edge under 3%, bad data, or situational red flag
- Maximum 5 units total per day regardless of slate size
- If total units across all picks exceeds 5u, downgrade the weakest picks to SKIP

═══ OUTPUT FORMAT ═══
Respond ONLY with a valid JSON array. No preamble, no markdown fences, no text outside the JSON.
Every game on the slate must appear in the output — either as a pick or a SKIP with a reason.

Each entry must have ALL of these fields:
{
  "game": "AWAY TEAM @ HOME TEAM",
  "venue": "stadium name",
  "game_time": "time string from input data",
  "away_sp": "pitcher name",
  "home_sp": "pitcher name",
  "bet_type": "Total OVER | Total UNDER | F5 OVER | F5 UNDER | ML | Run Line | SKIP",
  "pick": "exact plain-English bet e.g. OVER 8.5 or Cubs ML or SKIP",
  "line": "American odds e.g. -110 or N/A if skip",
  "tier": "A | B | C | SKIP",
  "units": 1.0,
  "win_prob_pct": 56,
  "implied_prob_pct": 52,
  "ev_pct": 4,
  "weather_impact": "brief note on how weather affects this game e.g. 14mph out, adds ~0.4 runs or Dome - N/A",
  "sp_edge": "one line on which pitcher has the edge and why",
  "park_note": "one line on park factor e.g. Petco suppresses offense or neutral park",
  "key_edge": "single most important reason to bet this — be specific",
  "rationale": "2-3 sentences of sharp analysis. Reference specific stats, weather numbers, or situational factors. No vague language.",
  "avoid_reason": "if SKIP: one sentence on why there is no edge here"
}"""

def call_claude(games_with_data):
    if not ANTHROPIC_KEY:
        print("No ANTHROPIC_API_KEY — skipping Claude call")
        return []
    
    user_content = f"""Today is {TODAY}. Analyze these MLB games and return your picks as JSON.

GAMES DATA:
{json.dumps(games_with_data, indent=2)}

Return ONLY the JSON array of picks. Tier A = strong edge (1.5u max), Tier B = moderate (1u), Tier C = lean (0.5u), SKIP = no value or bad data."""

    headers = {
        "x-api-key": ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 4000,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_content}],
    }
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=body, timeout=60)
        r.raise_for_status()
        raw = r.json()["content"][0]["text"].strip()
        # Strip accidental markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        print(f"Claude API error: {e}")
        return []

# ── Step 5: Assemble everything and write output ──────────────────────────────
def main():
    print(f"Running MLB picks generator for {TODAY}...")

    games = fetch_mlb_games()
    if not games:
        print("No games found today — exiting")
        return

    odds_map = fetch_odds()

    # Enrich each game with odds + weather
    games_with_data = []
    for g in games:
        key = f"{g['away']}@{g['home']}"
        odds = odds_map.get(key, {})
        weather = fetch_weather(g["home"])
        games_with_data.append({**g, "odds": odds, "weather": weather})

    print(f"Found {len(games_with_data)} games — calling Claude...")
    picks = call_claude(games_with_data)

    # Filter out SKIP tiers
    active_picks = [p for p in picks if p.get("tier") != "SKIP"]

    output = {
        "date": TODAY,
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "total_games": len(games),
        "total_picks": len(active_picks),
        "picks": active_picks,
        "raw_games_data": games_with_data,
    }

    # Write JSON
    json_path = OUTPUT_DIR / "picks.json"
    json_path.write_text(json.dumps(output, indent=2))
    print(f"Wrote {json_path}")

    # Write HTML dashboard
    html = build_html(output)
    html_path = OUTPUT_DIR / "index.html"
    html_path.write_text(html)
    print(f"Wrote {html_path}")
    print(f"Done. {len(active_picks)} picks generated.")

# ── HTML builder ──────────────────────────────────────────────────────────────
def build_html(data):
    all_picks = data.get("picks", [])
    active = [p for p in all_picks if p.get("tier") != "SKIP"]
    skipped = [p for p in all_picks if p.get("tier") == "SKIP"]
    total_units = sum(p.get("units", 0) for p in active)

    tier_bar   = {"A": "#1D9E75", "B": "#378ADD", "C": "#BA7517"}
    tier_bg    = {"A": "#E1F5EE", "B": "#E6F1FB", "C": "#FAEEDA"}
    tier_text  = {"A": "#0F6E56", "B": "#185FA5", "C": "#854F0B"}
    tier_label = {"A": "TIER A — PLAY", "B": "TIER B — PLAY", "C": "TIER C — LEAN"}

    def card(p):
        tier  = p.get("tier", "C")
        color = tier_bar.get(tier, "#888")
        bg    = tier_bg.get(tier, "#f5f5f5")
        tc    = tier_text.get(tier, "#333")
        lbl   = tier_label.get(tier, "LEAN")
        ev    = p.get("ev_pct", 0)
        bar_w = min(int(ev) * 8, 100)
        return f"""
<div style="background:#fff;border:0.5px solid #e0e0e0;border-left:3px solid {color};
            border-radius:10px;padding:1rem 1.25rem;margin-bottom:10px">
  <span style="background:{bg};color:{tc};font-size:11px;font-weight:600;
               padding:2px 9px;border-radius:4px;display:inline-block;margin-bottom:8px">{lbl}</span>
  <div style="font-size:16px;font-weight:600;margin-bottom:2px">{p.get('pick','')}</div>
  <div style="font-size:13px;color:#666;margin-bottom:10px">
    {p.get('game','')} &nbsp;·&nbsp; {p.get('line','N/A')} &nbsp;·&nbsp; {p.get('units',0)}u
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px">
    <div style="background:#f7f7f5;border-radius:7px;padding:8px 10px">
      <div style="font-size:10px;color:#999;margin-bottom:3px;text-transform:uppercase;letter-spacing:.05em">Away SP</div>
      <div style="font-size:13px;font-weight:600">{p.get('away_sp','TBD')}</div>
    </div>
    <div style="background:#f7f7f5;border-radius:7px;padding:8px 10px">
      <div style="font-size:10px;color:#999;margin-bottom:3px;text-transform:uppercase;letter-spacing:.05em">Home SP</div>
      <div style="font-size:13px;font-weight:600">{p.get('home_sp','TBD')}</div>
    </div>
  </div>
  <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">
    <span style="font-size:11px;background:#f0f0ee;padding:2px 9px;border-radius:20px;color:#555">
      Win {p.get('win_prob_pct',0)}% vs implied {p.get('implied_prob_pct',0)}%
    </span>
    <span style="font-size:11px;background:{bg};color:{tc};padding:2px 9px;border-radius:20px;font-weight:600">
      +{ev}% EV edge
    </span>
  </div>
  <div style="height:4px;background:#f0f0ee;border-radius:2px;margin-bottom:10px;overflow:hidden">
    <div style="height:100%;width:{bar_w}%;background:{color};border-radius:2px"></div>
  </div>
  <div style="display:flex;flex-direction:column;gap:4px;margin-bottom:10px">
    <div style="font-size:12px;color:#555">🌤 {p.get('weather_impact','N/A')}</div>
    <div style="font-size:12px;color:#555">⚾ {p.get('sp_edge','N/A')}</div>
    <div style="font-size:12px;color:#555">🏟 {p.get('park_note','N/A')}</div>
  </div>
  <div style="border-top:0.5px solid #eee;padding-top:8px">
    <div style="font-size:12px;font-weight:600;color:#333;margin-bottom:3px">Key edge: {p.get('key_edge','')}</div>
    <div style="font-size:12px;color:#666;line-height:1.6">{p.get('rationale','')}</div>
  </div>
</div>"""

    def skip_row(p):
        return f"""
<div style="background:#fff;border:0.5px solid #e0e0e0;border-left:3px solid #ccc;
            border-radius:10px;padding:0.75rem 1.25rem;margin-bottom:8px;
            display:flex;justify-content:space-between;align-items:center;gap:12px">
  <div>
    <div style="font-size:13px;font-weight:600;color:#333">{p.get('game','')}</div>
    <div style="font-size:12px;color:#999;margin-top:2px">{p.get('away_sp','TBD')} vs {p.get('home_sp','TBD')}</div>
  </div>
  <div style="text-align:right;flex-shrink:0">
    <span style="font-size:11px;background:#f5f5f5;color:#999;padding:2px 9px;
                 border-radius:4px;display:block;margin-bottom:3px">SKIP</span>
    <div style="font-size:11px;color:#bbb">{p.get('avoid_reason','No edge identified')}</div>
  </div>
</div>"""

    picks_html = "".join(card(p) for p in active) if active else \
        '<div style="color:#888;font-size:14px;padding:1.5rem 0;text-align:center">No picks with positive EV today. Passing is the right call.</div>'

    skips_html = "".join(skip_row(p) for p in skipped)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MLB Picks — {data['date']}</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
         background:#f9f9f7;color:#1a1a1a;padding:1.25rem;max-width:700px;margin:0 auto}}
    h1{{font-size:20px;font-weight:700;margin-bottom:3px}}
    .meta{{font-size:13px;color:#888;margin-bottom:1.25rem}}
    .summary{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:1.25rem}}
    .s{{background:#fff;border:0.5px solid #e8e8e5;border-radius:9px;padding:10px 12px}}
    .s-n{{font-size:22px;font-weight:700}}
    .s-l{{font-size:10px;color:#999;margin-top:2px;text-transform:uppercase;letter-spacing:.04em}}
    .section-title{{font-size:13px;font-weight:600;color:#999;text-transform:uppercase;
                   letter-spacing:.06em;margin:1.25rem 0 0.6rem}}
    footer{{font-size:11px;color:#bbb;margin-top:1.5rem;text-align:center;padding-bottom:1rem}}
  </style>
</head>
<body>
  <h1>MLB Betting Model</h1>
  <div class="meta">{data['date']} &nbsp;·&nbsp; {data['total_games']} games analyzed &nbsp;·&nbsp; Generated {data.get('generated_at','')[:16].replace('T',' ')} UTC</div>
  <div class="summary">
    <div class="s"><div class="s-n" style="color:#1D9E75">{len(active)}</div><div class="s-l">Active picks</div></div>
    <div class="s"><div class="s-n">{total_units:.1f}u</div><div class="s-l">Total units</div></div>
    <div class="s"><div class="s-n">{len(skipped)}</div><div class="s-l">Games skipped</div></div>
    <div class="s"><div class="s-n">5u</div><div class="s-l">Daily max</div></div>
  </div>
  <div class="section-title">Today's Picks</div>
  {picks_html}
  <div class="section-title">Skipped Games</div>
  {skips_html if skips_html else '<div style="color:#bbb;font-size:13px">—</div>'}
  <footer>EV-based model · Never bet more than you can afford to lose · Refreshes daily at 10 AM ET</footer>
</body>
</html>"""

if __name__ == "__main__":
    main()
