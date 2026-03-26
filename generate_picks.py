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
SYSTEM_PROMPT = """You are an elite MLB betting analyst. Your job is to identify positive expected value (EV) bets.

CORE RULES:
- Only recommend bets where your estimated win probability exceeds the implied odds probability by at least 3%
- Never recommend negative EV bets regardless of narrative
- Prioritize: moneylines, game totals, F5 (first 5 innings) totals
- Account for: pitcher quality (FIP > ERA), weather (wind direction is critical for totals), park factors, lineup matchups
- Flag if a game should be skipped (bad data, key injuries unknown, dome stadium irrelevant weather, etc.)

WIND RULES (hard-coded edges):
- Wind 10+ mph BLOWING OUT + warm temp (75°F+) = strong lean OVER on totals
- Wind 10+ mph BLOWING IN from center = strong lean UNDER on totals  
- Wrigley Field wind in from CF = one of the most reliable unders in baseball

OUTPUT: Respond ONLY with a valid JSON array. No preamble, no markdown, no explanation outside the JSON.
Each pick must have these fields:
{
  "game": "AWAY @ HOME",
  "bet_type": "ML | Total OVER | Total UNDER | F5 Total OVER | F5 Total UNDER | Run Line | Prop",
  "pick": "exact bet description",
  "line": "odds as American moneyline e.g. -110",
  "tier": "A | B | C | SKIP",
  "units": 0.5,
  "win_prob_pct": 58,
  "implied_prob_pct": 52,
  "ev_pct": 6,
  "rationale": "2-3 sentence sharp justification referencing the data provided",
  "key_edge": "one-line summary of the primary edge"
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
    picks_html = ""
    tier_colors = {"A": "#1D9E75", "B": "#378ADD", "C": "#BA7517"}
    tier_bg = {"A": "#E1F5EE", "B": "#E6F1FB", "C": "#FAEEDA"}
    tier_text = {"A": "#0F6E56", "B": "#185FA5", "C": "#854F0B"}

    for p in data["picks"]:
        tier = p.get("tier", "C")
        color = tier_colors.get(tier, "#888")
        bg = tier_bg.get(tier, "#f5f5f5")
        tc = tier_text.get(tier, "#333")
        ev = p.get("ev_pct", 0)
        ev_width = min(ev * 8, 100)

        picks_html += f"""
        <div style="background:#fff;border:0.5px solid #e0e0e0;border-left:3px solid {color};
                    border-radius:10px;padding:1rem 1.25rem;margin-bottom:0.75rem">
          <span style="background:{bg};color:{tc};font-size:11px;font-weight:600;
                       padding:2px 8px;border-radius:4px;display:inline-block;margin-bottom:6px">
            TIER {tier}
          </span>
          <div style="font-size:15px;font-weight:600;margin-bottom:2px">{p.get('pick','')}</div>
          <div style="font-size:13px;color:#666;margin-bottom:8px">{p.get('game','')} · {p.get('bet_type','')} · {p.get('line','')} · {p.get('units',0)}u</div>
          <div style="font-size:11px;color:#888;margin-bottom:3px">
            Win prob: {p.get('win_prob_pct',0)}% &nbsp;|&nbsp; 
            Implied: {p.get('implied_prob_pct',0)}% &nbsp;|&nbsp; 
            EV edge: +{ev}%
          </div>
          <div style="height:5px;background:#f0f0f0;border-radius:3px;margin-bottom:10px;overflow:hidden">
            <div style="height:100%;width:{ev_width}%;background:{color};border-radius:3px"></div>
          </div>
          <div style="font-size:12px;color:#555;line-height:1.6;padding-top:8px;border-top:0.5px solid #eee">
            <strong>Key edge:</strong> {p.get('key_edge','')}<br>
            {p.get('rationale','')}
          </div>
        </div>"""

    if not picks_html:
        picks_html = '<div style="color:#888;font-size:14px;padding:1rem 0">No picks with positive EV found today. Good discipline — pass the day.</div>'

    total_units = sum(p.get("units", 0) for p in data["picks"])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MLB Picks — {data['date']}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #f9f9f7; color: #1a1a1a; padding: 1.5rem; max-width: 680px; margin: 0 auto; }}
    h1 {{ font-size: 20px; font-weight: 600; margin-bottom: 4px; }}
    .meta {{ font-size: 13px; color: #888; margin-bottom: 1.25rem; }}
    .stats {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 1.25rem; }}
    .stat {{ background: #f0f0ee; border-radius: 8px; padding: 0.75rem 1rem; }}
    .stat-label {{ font-size: 11px; color: #888; margin-bottom: 3px; }}
    .stat-val {{ font-size: 22px; font-weight: 600; }}
    footer {{ font-size: 11px; color: #aaa; margin-top: 1.5rem; text-align: center; }}
  </style>
</head>
<body>
  <h1>MLB Betting Model</h1>
  <div class="meta">Generated {data['date']} · {data['total_games']} games analyzed</div>
  <div class="stats">
    <div class="stat"><div class="stat-label">Active Picks</div><div class="stat-val">{data['total_picks']}</div></div>
    <div class="stat"><div class="stat-label">Total Units</div><div class="stat-val">{total_units:.1f}u</div></div>
    <div class="stat"><div class="stat-label">Max Daily Exp.</div><div class="stat-val">5u</div></div>
  </div>
  {picks_html}
  <footer>EV-based model. Never bet more than you can afford to lose. Generated automatically.</footer>
</body>
</html>"""

if __name__ == "__main__":
    main()
