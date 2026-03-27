"""
MLB Betting Model — Daily Picks Generator
APIs: MLB Stats (free), The Odds API (free), OpenWeatherMap (free), Groq (free)
Stats: MLB Stats API pulls SP/bullpen/offense for 2025 + 2026, cached daily
"""

import os, json, datetime, requests
from pathlib import Path

ODDS_API_KEY    = os.environ.get("ODDS_API_KEY", "")
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY", "")
GROQ_KEY        = os.environ.get("GROQ_API_KEY", "")
OUTPUT_DIR      = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)
TODAY           = datetime.date.today().isoformat()
STATS_CACHE     = OUTPUT_DIR / "stats_cache.json"

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

# ── Stats fetching ────────────────────────────────────────────────────────────

def mlb_api(path, params=None):
    base = "https://statsapi.mlb.com/api/v1"
    try:
        r = requests.get(base + path, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("MLB API error (" + path + "): " + str(e))
        return {}

def fetch_sp_stats(season):
    """Fetch qualified SP stats for a given season."""
    data = mlb_api("/stats", {
        "stats": "season",
        "playerPool": "All",
        "position": "1",
        "sportId": "1",
        "season": str(season),
        "group": "pitching",
        "limit": "300",
        "fields": "stats,splits,stat,era,whip,strikeOuts,baseOnBalls,inningsPitched,gamesStarted,player,fullName"
    })
    result = {}
    for split in data.get("stats", [{}])[0].get("splits", []):
        player = split.get("player", {})
        name = player.get("fullName", "")
        stat = split.get("stat", {})
        gs = stat.get("gamesStarted", 0)
        if gs < 1:
            continue
        ip = float(stat.get("inningsPitched", "0") or 0)
        so = int(stat.get("strikeOuts", 0) or 0)
        bb = int(stat.get("baseOnBalls", 0) or 0)
        era_raw = stat.get("era", "0") or "0"
        era = float(era_raw) if era_raw not in ("-.--", "", "-", "---") else 0.0
        whip_raw = stat.get("whip", "0") or "0"
        whip = float(whip_raw) if whip_raw not in ("-.--", "", "-", "---") else 0.0
        k9 = round((so / ip * 9), 2) if ip > 0 else 0
        bb9 = round((bb / ip * 9), 2) if ip > 0 else 0
        result[name] = {
            "season": season,
            "gs": gs,
            "ip": round(ip, 1),
            "era": era,
            "whip": whip,
            "k9": k9,
            "bb9": bb9,
            "so": so,
            "bb": bb,
        }
    return result

def fetch_team_pitching(season):
    """Fetch team bullpen/pitching stats."""
    data = mlb_api("/stats", {
        "stats": "season",
        "group": "pitching",
        "gameType": "R",
        "season": str(season),
        "sportId": "1",
        "playerPool": "All",
        "sitCodes": "r",
        "fields": "stats,splits,stat,era,whip,strikeOuts,inningsPitched,team,name"
    })
    result = {}
    for split in data.get("stats", [{}])[0].get("splits", []):
        team = split.get("team", {}).get("name", "")
        stat = split.get("stat", {})
        if team:
            ip = float(stat.get("inningsPitched", "0") or 0)
            so = int(stat.get("strikeOuts", 0) or 0)
            era_raw = stat.get("era", "0") or "0"
            era = float(era_raw) if era_raw not in ("-.--", "", "-", "---") else 0.0
            whip_raw = stat.get("whip", "0") or "0"
            whip = float(whip_raw) if whip_raw not in ("-.--", "", "-", "---") else 0.0
            k9 = round((so / ip * 9), 2) if ip > 0 else 0
            result[team] = {"season": season, "team_era": era, "team_whip": whip, "team_k9": k9}
    return result

def fetch_team_batting(season):
    """Fetch team offense stats."""
    data = mlb_api("/stats", {
        "stats": "season",
        "group": "hitting",
        "gameType": "R",
        "season": str(season),
        "sportId": "1",
        "playerPool": "All",
        "fields": "stats,splits,stat,avg,obp,slg,ops,runs,team,name,atBats,hits"
    })
    result = {}
    for split in data.get("stats", [{}])[0].get("splits", []):
        team = split.get("team", {}).get("name", "")
        stat = split.get("stat", {})
        if team:
            g = int(stat.get("gamesPlayed", stat.get("games", 1)) or 1)
            runs = int(stat.get("runs", 0) or 0)
            result[team] = {
                "season": season,
                "avg": float(stat.get("avg", "0") or 0),
                "obp": float(stat.get("obp", "0") or 0),
                "slg": float(stat.get("slg", "0") or 0),
                "ops": float(stat.get("ops", "0") or 0),
                "runs_per_game": round(runs / g, 2) if g > 0 else 0,
            }
    return result

def fetch_and_cache_stats():
    """Fetch all stats once per day and cache to disk."""
    # Check cache
    if STATS_CACHE.exists():
        try:
            cached = json.loads(STATS_CACHE.read_text())
            if cached.get("date") == TODAY:
                print("Using cached stats from today")
                return cached
        except:
            pass

    print("Fetching fresh stats from MLB API...")
    stats = {
        "date": TODAY,
        "sp_2025": fetch_sp_stats(2025),
        "sp_2026": fetch_sp_stats(2026),
        "team_pitching_2025": fetch_team_pitching(2025),
        "team_pitching_2026": fetch_team_pitching(2026),
        "team_batting_2025": fetch_team_batting(2025),
        "team_batting_2026": fetch_team_batting(2026),
    }

    sp25 = len(stats["sp_2025"])
    sp26 = len(stats["sp_2026"])
    print("SP stats fetched: " + str(sp25) + " in 2025, " + str(sp26) + " in 2026")

    STATS_CACHE.write_text(json.dumps(stats))
    return stats

def get_pitcher_stats(name, stats):
    """Get merged pitcher stats, weighting 2025 vs 2026 by sample size."""
    s25 = stats["sp_2025"].get(name, {})
    s26 = stats["sp_2026"].get(name, {})

    if not s25 and not s26:
        return {"note": "No stats available — treat as unknown"}

    if not s26 or s26.get("gs", 0) == 0:
        s25["note"] = "2025 stats only (no 2026 starts yet)"
        return s25

    gs26 = s26.get("gs", 0)

    if gs26 >= 10:
        s26["note"] = "2026 stats primary (10+ starts)"
        return s26
    elif gs26 >= 5:
        # Blend — weight 2026 at 60%, 2025 at 40%
        blended = {}
        for key in ["era","whip","k9","bb9"]:
            v25 = s25.get(key, 0)
            v26 = s26.get(key, 0)
            if v25 and v26:
                blended[key] = round(v26 * 0.6 + v25 * 0.4, 2)
            else:
                blended[key] = v26 or v25
        blended["gs_2026"] = gs26
        blended["gs_2025"] = s25.get("gs", 0)
        blended["note"] = "Blended: 60% 2026 (" + str(gs26) + " starts) + 40% 2025"
        return blended
    else:
        # 1-4 starts in 2026 — mostly trust 2025, flag small sample
        s25["gs_2026"] = gs26
        s25["era_2026"] = s26.get("era")
        s25["note"] = "Primarily 2025 stats — 2026 sample too small (" + str(gs26) + " starts). 2026 ERA shown for reference."
        return s25

def get_team_stats(team_name, stats, stat_type):
    """Get merged team stats with same 2025/2026 weighting logic."""
    key25 = stat_type + "_2025"
    key26 = stat_type + "_2026"
    s25 = stats.get(key25, {}).get(team_name, {})
    s26 = stats.get(key26, {}).get(team_name, {})
    if s26 and s26.get("runs_per_game", s26.get("team_era", 0)):
        s26["note"] = "2026 YTD"
        return s26
    if s25:
        s25["note"] = "2025 full season (no 2026 data yet)"
        return s25
    return {}

# ── Game/odds/weather fetching ────────────────────────────────────────────────

def fetch_mlb_games():
    url = "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=" + TODAY + "&hydrate=probablePitcher,linescore,team"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        games = []
        for date_entry in data.get("dates", []):
            for g in date_entry.get("games", []):
                abstract = g.get("status", {}).get("abstractGameState", "")
                detailed = g.get("status", {}).get("detailedState", "")
                home_name = g["teams"]["home"]["team"]["name"]
                away_name = g["teams"]["away"]["team"]["name"]
                home_sp = g["teams"]["home"].get("probablePitcher", {}).get("fullName", "TBD")
                away_sp = g["teams"]["away"].get("probablePitcher", {}).get("fullName", "TBD")
                home_score = g["teams"]["home"].get("score", None)
                away_score = g["teams"]["away"].get("score", None)
                live_score = None
                if home_score is not None and away_score is not None:
                    live_score = away_name + " " + str(away_score) + " - " + home_name + " " + str(home_score)
                games.append({
                    "home": home_name,
                    "away": away_name,
                    "game_time": g.get("gameDate", ""),
                    "home_sp": home_sp,
                    "away_sp": away_sp,
                    "venue": g.get("venue", {}).get("name", ""),
                    "status": detailed or abstract,
                    "live_score": live_score,
                })
        print("Fetched " + str(len(games)) + " games from MLB API")
        return games
    except Exception as e:
        print("MLB API error: " + str(e))
        return []

def fetch_odds():
    if not ODDS_API_KEY:
        return {}
    try:
        r = requests.get(
            "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/",
            params={"apiKey": ODDS_API_KEY, "regions": "us", "markets": "h2h,totals", "oddsFormat": "american", "dateFormat": "iso"},
            timeout=10
        )
        r.raise_for_status()
        odds_map = {}
        for event in r.json():
            home = event.get("home_team", "")
            away = event.get("away_team", "")
            ml = {}
            total = {}
            for bookmaker in event.get("bookmakers", [])[:1]:
                for market in bookmaker.get("markets", []):
                    if market["key"] == "h2h":
                        for o in market["outcomes"]:
                            ml[o["name"]] = o["price"]
                    elif market["key"] == "totals":
                        for o in market["outcomes"]:
                            if o["name"] == "Over":
                                total["line"] = o.get("point", "")
                                total["over"] = o["price"]
                            elif o["name"] == "Under":
                                total["under"] = o["price"]
            odds_map[away + "@" + home] = {"moneyline": ml, "total": total}
        return odds_map
    except Exception as e:
        print("Odds API error: " + str(e))
        return {}

def fetch_weather(team_name):
    coords = STADIUMS.get(team_name)
    if not coords or not WEATHER_API_KEY:
        return {"temp_f": "N/A", "wind_mph": "N/A", "wind_dir": "N/A", "precip_pct": "N/A"}
    try:
        r = requests.get(
            "https://api.openweathermap.org/data/2.5/forecast",
            params={"lat": coords[0], "lon": coords[1], "appid": WEATHER_API_KEY, "units": "imperial", "cnt": 4},
            timeout=10
        )
        r.raise_for_status()
        entry = r.json()["list"][0]
        deg = entry["wind"].get("deg", 0)
        dirs = ["N","NE","E","SE","S","SW","W","NW"]
        return {
            "temp_f": round(entry["main"]["temp"]),
            "wind_mph": round(entry["wind"]["speed"] * 2.237, 1),
            "wind_dir": dirs[round(deg / 45) % 8],
            "precip_pct": round(entry.get("pop", 0) * 100),
        }
    except Exception as e:
        print("Weather error for " + team_name + ": " + str(e))
        return {"temp_f": "N/A", "wind_mph": "N/A", "wind_dir": "N/A", "precip_pct": "N/A"}

# ── Groq call ────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a sharp MLB betting analyst finding positive expected value (EV) bets.
You are given REAL statistical data from the MLB Stats API for each game. Use these stats — do not rely on memory.

STAT WEIGHTING RULES (critical):
- If a pitcher has 10+ starts in 2026: use 2026 stats as primary
- If a pitcher has 5-9 starts in 2026: use blended stats provided (already calculated for you)
- If a pitcher has 1-4 starts in 2026: primarily use 2025, note small 2026 sample
- If no 2026 stats exist yet: use 2025 as the baseline
- As the season progresses past June, weight 2026 stats increasingly heavily
- A pitcher with 3.00 ERA in 2025 but 6.00 ERA through 6 starts in 2026 is a regression — trust the 2026 data

ANALYSIS ORDER (most to least important):
1. Starting pitcher quality — use the ERA, FIP proxy (use K/9 and BB/9 as indicators), WHIP from the stats provided
2. Bullpen strength — use team pitching stats provided. Elite: Dodgers/Rays/Braves/Phillies. Weak: Rockies/Athletics/Nationals/White Sox
3. Lineup/offense quality — use team batting stats (OPS, runs/game) provided
4. Park factors — Coors +1.5 runs, GABP +0.7, Petco -0.7, Oracle -0.5, T-Mobile -0.4, Wrigley weather-reactive
5. Weather — tiebreaker only, never sole reason. Dome = irrelevant. Wind 12+ mph OUT = lean OVER. Wind 12+ mph IN = lean UNDER

BETTING RULES:
- Only bet when your win probability exceeds implied odds by 3%+ (ML) or 4%+ (totals)
- Never bet ML worse than -200. Never bet total juice worse than -130
- Tier A = 7%+ edge (1.5u). Tier B = 4-6% (1.0u). Tier C = 3% (0.5u). Max 5u/day
- Prefer F5 totals — isolates SP, removes bullpen variance
- Skip if SP is TBD, both pitchers unknown rookies, or rain 50%+

OUTPUT: Respond with ONLY a raw JSON array, nothing else, no markdown, no backticks.
Every game must appear. """ + str(datetime.date.today().year) + """ season context: weight 2026 stats as primary when sufficient sample exists.

Each entry must have these exact fields:
{
  "game": "AWAY @ HOME",
  "venue": "stadium",
  "game_time": "from input",
  "status": "Scheduled or In Progress or Final",
  "live_score": "score or null",
  "away_sp": "name",
  "home_sp": "name",
  "bet_type": "F5 OVER or F5 UNDER or Total OVER or Total UNDER or ML or Run Line or SKIP",
  "pick": "e.g. OVER 8.5 or Cubs ML or SKIP",
  "line": "e.g. -110 or N/A",
  "tier": "A or B or C or SKIP",
  "units": 1.0,
  "win_prob_pct": 56,
  "implied_prob_pct": 52,
  "ev_pct": 4,
  "sp_analysis": "cite specific stats from the data provided e.g. Yamamoto 2.45 ERA 10.2 K/9 in 2026",
  "bullpen_note": "cite team ERA/K9 from data provided",
  "lineup_note": "cite team OPS or runs/game from data provided",
  "park_note": "park factor impact",
  "weather_impact": "weather effect or Dome - N/A",
  "key_edge": "cite a specific number from the stats provided",
  "rationale": "3 sentences citing actual stats. SP comparison. Supporting factors. Line value.",
  "avoid_reason": "if SKIP: specific reason. Empty string otherwise."
}"""

def call_groq(games_with_data):
    if not GROQ_KEY:
        print("No GROQ_API_KEY -- skipping AI call")
        return []

    n = len(games_with_data)
    user_msg = (
        "Today is " + TODAY + ". Analyze these " + str(n) + " MLB games using the real stats provided. "
        "Return a JSON array with exactly " + str(n) + " entries. "
        "Output raw JSON only — no markdown, no backticks, no explanation.\n\n"
        "GAMES WITH REAL STATS:\n" + json.dumps(games_with_data, indent=2)
    )

    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": "Bearer " + GROQ_KEY, "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                "temperature": 0.1,
                "max_tokens": 8000,
            },
            timeout=90
        )
        if not r.ok:
            print("Groq error: " + r.text[:300])
            return []

        raw = r.json()["choices"][0]["message"]["content"].strip()
        print("Groq response: " + str(len(raw)) + " chars")

        if "```" in raw:
            parts = raw.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("["):
                    raw = part
                    break

        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start >= 0 and end > start:
            raw = raw[start:end]

        picks = json.loads(raw.strip())
        print("Groq returned " + str(len(picks)) + " picks")
        return picks

    except Exception as e:
        print("Groq call failed: " + str(e))
        return []

# ── Archive + HTML ────────────────────────────────────────────────────────────

def build_archive_index():
    dated_files = sorted([f for f in OUTPUT_DIR.glob("????-??-??.html")], reverse=True)
    if not dated_files:
        return
    rows = ""
    for f in dated_files:
        d = f.stem
        rows += '<a href="' + d + '.html" style="display:flex;justify-content:space-between;align-items:center;padding:12px 16px;background:#fff;border:0.5px solid #e8e8e5;border-radius:9px;margin-bottom:8px;text-decoration:none;color:#1a1a1a"><span style="font-size:14px;font-weight:500">' + d + '</span><span style="font-size:12px;color:#999">View &rarr;</span></a>\n'
    html = ('<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MLB Archive</title>'
            '<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:-apple-system,sans-serif;background:#f9f9f7;color:#1a1a1a;padding:1.25rem;max-width:700px;margin:0 auto}'
            'h1{font-size:20px;font-weight:700;margin-bottom:4px}.meta{font-size:13px;color:#888;margin-bottom:1.5rem}</style></head><body>'
            '<h1>MLB Picks Archive</h1><div class="meta">Click any date to review picks</div>'
            '<a href="index.html" style="display:flex;justify-content:space-between;align-items:center;padding:12px 16px;background:#E1F5EE;border:0.5px solid #5DCAA5;border-radius:9px;margin-bottom:16px;text-decoration:none;color:#0F6E56">'
            '<span style="font-size:14px;font-weight:600">Today &mdash; ' + TODAY + '</span><span style="font-size:12px">View &rarr;</span></a>' + rows + '</body></html>')
    (OUTPUT_DIR / "archive.html").write_text(html)

def build_html(data):
    all_picks = data.get("picks", [])
    active  = [p for p in all_picks if p.get("tier") != "SKIP"]
    skipped = [p for p in all_picks if p.get("tier") == "SKIP"]
    total_u = round(sum(p.get("units", 0) for p in active), 1)

    TBAR = {"A":"#1D9E75","B":"#378ADD","C":"#BA7517"}
    TBG  = {"A":"#E1F5EE","B":"#E6F1FB","C":"#FAEEDA"}
    TTC  = {"A":"#0F6E56","B":"#185FA5","C":"#854F0B"}
    TLBL = {"A":"TIER A &mdash; PLAY","B":"TIER B &mdash; PLAY","C":"TIER C &mdash; LEAN"}

    def badge(p):
        live = p.get("live_score") or ""
        s = p.get("status","")
        if live and "Final" in s:
            return '<span style="font-size:11px;background:#f0f0ee;color:#555;padding:2px 8px;border-radius:4px;margin-left:6px">FINAL: ' + live + '</span>'
        if live:
            return '<span style="font-size:11px;background:#FAEEDA;color:#633806;padding:2px 8px;border-radius:4px;margin-left:6px">LIVE: ' + live + '</span>'
        return ""

    def sp_box(label, name):
        return ('<div style="background:#f7f7f5;border-radius:7px;padding:8px 10px">'
                '<div style="font-size:10px;color:#999;margin-bottom:3px;text-transform:uppercase;letter-spacing:.05em">' + label + '</div>'
                '<div style="font-size:13px;font-weight:500">' + str(name) + '</div></div>')

    def mrow(icon, text):
        return '<div style="font-size:12px;color:#666;margin-bottom:3px">' + icon + ' ' + str(text) + '</div>'

    def pick_card(p):
        t = p.get("tier","C")
        c = TBAR.get(t,"#888"); bg = TBG.get(t,"#eee"); tc = TTC.get(t,"#333")
        ev = p.get("ev_pct",0); bw = min(int(ev)*8,100)
        return (
            '<div style="background:#fff;border:0.5px solid #e0e0e0;border-left:3px solid ' + c + ';border-radius:10px;padding:1rem 1.25rem;margin-bottom:10px">'
            '<span style="background:' + bg + ';color:' + tc + ';font-size:11px;font-weight:600;padding:2px 9px;border-radius:4px;display:inline-block;margin-bottom:8px">' + TLBL.get(t,"LEAN") + '</span>'
            '<div style="font-size:16px;font-weight:600;margin-bottom:2px">' + str(p.get("pick","")) + '</div>'
            '<div style="font-size:13px;color:#777;margin-bottom:10px">' + str(p.get("game","")) + ' &nbsp;&middot;&nbsp; ' + str(p.get("line","N/A")) + ' &nbsp;&middot;&nbsp; ' + str(p.get("units",0)) + 'u &nbsp;<span id="score_' + str(p.get("game","")).replace(" ","_") + '" style="font-size:11px;background:#f0f0ee;color:#555;padding:2px 8px;border-radius:4px;">loading...</span></div>'
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px">' + sp_box("Away SP", p.get("away_sp","TBD")) + sp_box("Home SP", p.get("home_sp","TBD")) + '</div>'
            '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">'
            '<span style="font-size:11px;background:#f0f0ee;padding:2px 9px;border-radius:20px;color:#555">Win ' + str(p.get("win_prob_pct",0)) + '% vs implied ' + str(p.get("implied_prob_pct",0)) + '%</span>'
            '<span style="font-size:11px;background:' + bg + ';color:' + tc + ';padding:2px 9px;border-radius:20px;font-weight:600">+' + str(ev) + '% EV</span>'
            '</div>'
            '<div style="height:4px;background:#f0f0ee;border-radius:2px;margin-bottom:10px;overflow:hidden"><div style="height:100%;width:' + str(bw) + '%;background:' + c + ';border-radius:2px"></div></div>'
            '<div style="margin-bottom:10px">'
            + mrow("&#9918;", p.get("sp_analysis","N/A"))
            + mrow("&#128101;", p.get("bullpen_note","N/A"))
            + mrow("&#128200;", p.get("lineup_note","N/A"))
            + mrow("&#127966;", p.get("park_note","N/A"))
            + mrow("&#127748;", p.get("weather_impact","N/A")) +
            '</div>'
            '<div style="border-top:0.5px solid #eee;padding-top:8px">'
            '<div style="font-size:12px;font-weight:600;color:#222;margin-bottom:3px">Key edge: ' + str(p.get("key_edge","")) + '</div>'
            '<div style="font-size:12px;color:#666;line-height:1.6">' + str(p.get("rationale","")) + '</div>'
            '</div></div>'
        )

    def skip_card(p):
        return (
            '<div style="background:#fff;border:0.5px solid #e0e0e0;border-left:3px solid #B4B2A9;border-radius:10px;padding:1rem 1.25rem;margin-bottom:10px">'
            '<span style="background:#F1EFE8;color:#5F5E5A;font-size:11px;font-weight:600;padding:2px 9px;border-radius:4px;display:inline-block;margin-bottom:8px">SKIP &mdash; NO EDGE</span>'
            '<div style="font-size:16px;font-weight:600;margin-bottom:2px">' + str(p.get("game","")) + ' &nbsp;<span id="score_' + (str(p.get("game","")).replace(" ","_").replace("@","@")) + '" style="font-size:11px;background:#f0f0ee;color:#555;padding:2px 8px;border-radius:4px;">loading...</span></div>'
            '<div style="font-size:13px;color:#777;margin-bottom:10px">' + str(p.get("venue","")) + '</div>'
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px">' + sp_box("Away SP", p.get("away_sp","TBD")) + sp_box("Home SP", p.get("home_sp","TBD")) + '</div>'
            '<div style="margin-bottom:10px">'
            + mrow("&#9918;", p.get("sp_analysis","N/A"))
            + mrow("&#128101;", p.get("bullpen_note","N/A"))
            + mrow("&#128200;", p.get("lineup_note","N/A"))
            + mrow("&#127966;", p.get("park_note","N/A"))
            + mrow("&#127748;", p.get("weather_impact","N/A")) +
            '</div>'
            '<div style="border-top:0.5px solid #eee;padding-top:8px">'
            '<div style="font-size:12px;font-weight:600;color:#A32D2D;margin-bottom:3px">Why skip: ' + str(p.get("avoid_reason","No edge")) + '</div>'
            '<div style="font-size:12px;color:#888;line-height:1.6">' + str(p.get("rationale","")) + '</div>'
            '</div></div>'
        )

    cards = "".join(pick_card(p) for p in active) + "".join(skip_card(p) for p in skipped)
    if not cards:
        cards = '<p style="color:#888;font-size:14px;padding:1.5rem 0;text-align:center">No games found today.</p>'

    gen = data.get("generated_at","")[:16].replace("T"," ")
    stats_note = data.get("stats_date","")
    return ('<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>MLB Picks - ' + data["date"] + '</title>'
            '<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f9f9f7;color:#1a1a1a;padding:1.25rem;max-width:700px;margin:0 auto}'
            'h1{font-size:20px;font-weight:700;margin-bottom:3px}.meta{font-size:13px;color:#888;margin-bottom:1.25rem}'
            '.sum{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:1.25rem}'
            '.s{background:#fff;border:0.5px solid #e8e8e5;border-radius:9px;padding:10px 12px}'
            '.sn{font-size:22px;font-weight:700}.sl{font-size:10px;color:#999;margin-top:2px;text-transform:uppercase;letter-spacing:.04em}'
            '.st{font-size:13px;font-weight:600;color:#999;text-transform:uppercase;letter-spacing:.06em;margin:1.25rem 0 0.5rem}'
            'footer{font-size:11px;color:#bbb;margin-top:1.5rem;text-align:center;padding-bottom:1rem}</style></head><body>'
            '<h1>MLB Betting Model</h1>'
            '<div class="meta">' + data["date"] + ' &nbsp;&middot;&nbsp; ' + str(data["total_games"]) + ' games &nbsp;&middot;&nbsp; Updated ' + gen + ' UTC &nbsp;&middot;&nbsp; Stats: 2025+2026 &nbsp;&middot;&nbsp; <a href="archive.html" style="color:#378ADD;text-decoration:none">Archive &rarr;</a></div><div style="font-size:11px;color:#aaa;margin-bottom:1rem" id="last_update">Scores loading...</div>'
            '<div class="sum">'
            '<div class="s"><div class="sn" style="color:#1D9E75">' + str(len(active)) + '</div><div class="sl">Active picks</div></div>'
            '<div class="s"><div class="sn">' + str(total_u) + 'u</div><div class="sl">Total units</div></div>'
            '<div class="s"><div class="sn">' + str(len(skipped)) + '</div><div class="sl">No edge</div></div>'
            '<div class="s"><div class="sn">5u</div><div class="sl">Daily max</div></div>'
            '</div>'
            '<div class="st">Full Slate &mdash; ' + data["date"] + '</div>'
            + cards +
            '<footer>EV-based model &nbsp;&middot;&nbsp; Real MLB stats 2025+2026 &nbsp;&middot;&nbsp; Never bet more than you can afford to lose</footer>'
            '<script>'
            'var MLB_DATE="' + data["date"] + '";'
            'function toET(iso){var d=new Date(iso);return d.toLocaleTimeString("en-US",{timeZone:"America/New_York",hour:"numeric",minute:"2-digit"});}'
            'function updateScores(){'
            'fetch("https://statsapi.mlb.com/api/v1/schedule?sportId=1&date="+MLB_DATE+"&hydrate=linescore,team")'
            '.then(function(r){return r.json();})'
            '.then(function(data){'
            'var games=[];'
            '(data.dates||[]).forEach(function(d){(d.games||[]).forEach(function(g){games.push(g);});});'
            'games.forEach(function(g){'
            'var away=g.teams.away.team.name;'
            'var home=g.teams.home.team.name;'
            'var away_u=away.replace(/ /g,"_");'
            'var home_u=home.replace(/ /g,"_");'
            'var key=away_u+"@"+home_u;'
            'var abstract=g.status.abstractGameState;'
            'var awayS=g.teams.away.score;'
            'var homeS=g.teams.home.score;'
            'var el=document.getElementById("score_"+key);'
            'if(!el)return;'
            'if(abstract==="Final"){'
            'el.innerHTML="FINAL: "+away+" "+awayS+" - "+home+" "+homeS;'
            'el.style.background="#f0f0ee";el.style.color="#555";'
            '}else if(abstract==="Live"){'
            'var inn=g.linescore?g.linescore.currentInningOrdinal:"";'
            'el.innerHTML="LIVE "+inn+": "+away+" "+awayS+" - "+home+" "+homeS;'
            'el.style.background="#FAEEDA";el.style.color="#633806";'
            '}else{'
            'el.innerHTML=toET(g.gameDate)+" ET";'
            'el.style.background="#f0f0ee";el.style.color="#555";'
            '}});'
            'document.getElementById("last_update").textContent="Scores updated: "+new Date().toLocaleTimeString("en-US",{timeZone:"America/New_York",hour:"numeric",minute:"2-digit"})+" ET";'
            '}).catch(function(e){console.log("Score update failed:",e);});'
            '}'
            'updateScores();'
            'setInterval(updateScores,30000);'
            '</script>'
            '</body></html>')

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("Running MLB picks generator for " + TODAY + "...")

    # Fetch stats (cached daily)
    stats = fetch_and_cache_stats()

    # Fetch games
    games = fetch_mlb_games()
    if not games:
        print("No games found -- exiting")
        return

    # Fetch odds and weather
    odds_map = fetch_odds()

    # Build enriched game data
    games_with_data = []
    for g in games:
        odds = odds_map.get(g["away"] + "@" + g["home"], {})
        weather = fetch_weather(g["home"])

        # Enrich with real pitcher stats
        home_sp_stats = get_pitcher_stats(g["home_sp"], stats)
        away_sp_stats = get_pitcher_stats(g["away_sp"], stats)

        # Enrich with team stats
        home_team_pitching = get_team_stats(g["home"], stats, "team_pitching")
        away_team_pitching = get_team_stats(g["away"], stats, "team_pitching")
        home_team_batting  = get_team_stats(g["home"], stats, "team_batting")
        away_team_batting  = get_team_stats(g["away"], stats, "team_batting")

        gd = dict(g)
        gd["odds"]               = odds
        gd["weather"]            = weather
        gd["home_sp_stats"]      = home_sp_stats
        gd["away_sp_stats"]      = away_sp_stats
        gd["home_team_pitching"] = home_team_pitching
        gd["away_team_pitching"] = away_team_pitching
        gd["home_team_batting"]  = home_team_batting
        gd["away_team_batting"]  = away_team_batting
        games_with_data.append(gd)

    # Call Groq
    picks = call_groq(games_with_data)
    active = [p for p in picks if p.get("tier") != "SKIP"]

    output = {
        "date": TODAY,
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "stats_date": stats.get("date",""),
        "total_games": len(games),
        "total_picks": len(active),
        "picks": picks,
        "raw_games_data": games_with_data,
    }

    (OUTPUT_DIR / "picks.json").write_text(json.dumps(output, indent=2))
    html = build_html(output)
    (OUTPUT_DIR / (TODAY + ".html")).write_text(html)
    (OUTPUT_DIR / "index.html").write_text(html)
    build_archive_index()
    print("Done. " + str(len(active)) + " active picks across " + str(len(games)) + " games.")

if __name__ == "__main__":
    main()
