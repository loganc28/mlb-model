"""
MLB Betting Model — Daily Picks Generator
APIs: MLB Stats (free), The Odds API (free), OpenWeatherMap (free), Groq (free)
"""

import os, json, datetime, requests
from pathlib import Path

ODDS_API_KEY    = os.environ.get("ODDS_API_KEY", "")
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY", "")
GROQ_KEY        = os.environ.get("GROQ_API_KEY", "")
OUTPUT_DIR      = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)
TODAY = datetime.date.today().isoformat()

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
                game_time = g.get("gameDate", "")
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
                    "game_time": game_time,
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

SYSTEM_PROMPT = """You are a sharp MLB betting analyst finding positive expected value (EV) bets.

ANALYSIS ORDER (most to least important):
1. Starting pitcher quality - FIP/xFIP over ERA, K/9, BB/9, handedness splits
2. Bullpen strength - elite: Dodgers/Rays/Braves/Phillies, weak: Rockies/Athletics/Nationals/White Sox
3. Lineup quality - injuries, platoon splits, hot/cold streaks
4. Park factors - Coors +1.5 runs, GABP +0.7, Petco -0.7, Oracle -0.5, T-Mobile -0.4
5. Weather - tiebreaker only, never the sole reason for a bet. Dome = irrelevant.
   Wind 12+ mph OUT = lean OVER. Wind 12+ mph IN = lean UNDER. >85F adds ~0.4 runs. <50F subtracts ~0.4.

BETTING RULES:
- Only bet when your win probability exceeds implied odds by 3%+ for ML, 4%+ for totals
- Never bet ML worse than -200. Never bet totals with juice worse than -130.
- Tier A = 7%+ edge (1.5u). Tier B = 4-6% edge (1.0u). Tier C = 3% edge (0.5u). Max 5u/day.
- Prefer F5 totals over full-game totals early in the season (no bullpen data yet)
- Skip if SP is TBD, both pitchers are unknown rookies, or rain 50%+

REQUIRED OUTPUT FORMAT - respond with ONLY a raw JSON array, nothing else, no markdown:
[
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
    "sp_analysis": "which pitcher has the edge and why",
    "bullpen_note": "bullpen situation for both teams",
    "lineup_note": "lineup quality or platoon splits",
    "park_note": "park factor impact",
    "weather_impact": "weather effect or Dome - N/A",
    "key_edge": "the single most important reason for this bet with a specific stat",
    "rationale": "SP edge. Supporting factors. Why the line has value.",
    "avoid_reason": "why no edge if SKIP, else empty string"
  }
]"""

def call_groq(games_with_data):
    if not GROQ_KEY:
        print("No GROQ_API_KEY -- skipping AI call")
        return []

    n = len(games_with_data)
    user_msg = (
        "Today is " + TODAY + ". Here are " + str(n) + " MLB games. "
        "Return a JSON array with exactly " + str(n) + " entries, one per game. "
        "Output raw JSON only - no markdown, no backticks, no explanation.\n\n"
        "GAMES:\n" + json.dumps(games_with_data, indent=2)
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
        print("Groq response length: " + str(len(raw)) + " chars")

        # Strip any markdown fences
        if "```" in raw:
            parts = raw.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("["):
                    raw = part
                    break

        raw = raw.strip()

        # Find the JSON array
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start >= 0 and end > start:
            raw = raw[start:end]

        picks = json.loads(raw)
        print("Groq returned " + str(len(picks)) + " picks")
        return picks

    except Exception as e:
        print("Groq call failed: " + str(e))
        return []

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
            '<h1>MLB Picks Archive</h1><div class="meta">Click any date to review that day\'s picks</div>'
            '<a href="index.html" style="display:flex;justify-content:space-between;align-items:center;padding:12px 16px;background:#E1F5EE;border:0.5px solid #5DCAA5;border-radius:9px;margin-bottom:16px;text-decoration:none;color:#0F6E56">'
            '<span style="font-size:14px;font-weight:600">Today &mdash; ' + TODAY + '</span><span style="font-size:12px">View &rarr;</span></a>' + rows + '</body></html>')
    (OUTPUT_DIR / "archive.html").write_text(html)
    print("Wrote archive.html")

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

    def sp_row(label, name):
        return ('<div style="background:#f7f7f5;border-radius:7px;padding:8px 10px">'
                '<div style="font-size:10px;color:#999;margin-bottom:3px;text-transform:uppercase;letter-spacing:.05em">' + label + '</div>'
                '<div style="font-size:13px;font-weight:500">' + str(name) + '</div></div>')

    def meta_row(icon, text):
        return '<div style="font-size:12px;color:#666;margin-bottom:3px">' + icon + ' ' + str(text) + '</div>'

    def pick_card(p):
        t = p.get("tier","C")
        c = TBAR.get(t,"#888"); bg = TBG.get(t,"#eee"); tc = TTC.get(t,"#333")
        ev = p.get("ev_pct",0); bw = min(int(ev)*8,100)
        return (
            '<div style="background:#fff;border:0.5px solid #e0e0e0;border-left:3px solid ' + c + ';border-radius:10px;padding:1rem 1.25rem;margin-bottom:10px">'
            '<span style="background:' + bg + ';color:' + tc + ';font-size:11px;font-weight:600;padding:2px 9px;border-radius:4px;display:inline-block;margin-bottom:8px">' + TLBL.get(t,"LEAN") + '</span>'
            '<div style="font-size:16px;font-weight:600;margin-bottom:2px">' + str(p.get("pick","")) + '</div>'
            '<div style="font-size:13px;color:#777;margin-bottom:10px">' + str(p.get("game","")) + ' &nbsp;&middot;&nbsp; ' + str(p.get("line","N/A")) + ' &nbsp;&middot;&nbsp; ' + str(p.get("units",0)) + 'u' + badge(p) + '</div>'
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px">' + sp_row("Away SP", p.get("away_sp","TBD")) + sp_row("Home SP", p.get("home_sp","TBD")) + '</div>'
            '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">'
            '<span style="font-size:11px;background:#f0f0ee;padding:2px 9px;border-radius:20px;color:#555">Win ' + str(p.get("win_prob_pct",0)) + '% vs implied ' + str(p.get("implied_prob_pct",0)) + '%</span>'
            '<span style="font-size:11px;background:' + bg + ';color:' + tc + ';padding:2px 9px;border-radius:20px;font-weight:600">+' + str(ev) + '% EV</span>'
            '</div>'
            '<div style="height:4px;background:#f0f0ee;border-radius:2px;margin-bottom:10px;overflow:hidden"><div style="height:100%;width:' + str(bw) + '%;background:' + c + ';border-radius:2px"></div></div>'
            '<div style="margin-bottom:10px">'
            + meta_row("&#9918;", p.get("sp_analysis","N/A"))
            + meta_row("&#128101;", p.get("bullpen_note","N/A"))
            + meta_row("&#128200;", p.get("lineup_note","N/A"))
            + meta_row("&#127966;", p.get("park_note","N/A"))
            + meta_row("&#127748;", p.get("weather_impact","N/A")) +
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
            '<div style="font-size:16px;font-weight:600;margin-bottom:2px">' + str(p.get("game","")) + badge(p) + '</div>'
            '<div style="font-size:13px;color:#777;margin-bottom:10px">' + str(p.get("venue","")) + '</div>'
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px">' + sp_row("Away SP", p.get("away_sp","TBD")) + sp_row("Home SP", p.get("home_sp","TBD")) + '</div>'
            '<div style="margin-bottom:10px">'
            + meta_row("&#9918;", p.get("sp_analysis","N/A"))
            + meta_row("&#128101;", p.get("bullpen_note","N/A"))
            + meta_row("&#128200;", p.get("lineup_note","N/A"))
            + meta_row("&#127966;", p.get("park_note","N/A"))
            + meta_row("&#127748;", p.get("weather_impact","N/A")) +
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
            '<div class="meta">' + data["date"] + ' &nbsp;&middot;&nbsp; ' + str(data["total_games"]) + ' games &nbsp;&middot;&nbsp; Updated ' + gen + ' UTC &nbsp;&middot;&nbsp; <a href="archive.html" style="color:#378ADD;text-decoration:none">Archive &rarr;</a></div>'
            '<div class="sum">'
            '<div class="s"><div class="sn" style="color:#1D9E75">' + str(len(active)) + '</div><div class="sl">Active picks</div></div>'
            '<div class="s"><div class="sn">' + str(total_u) + 'u</div><div class="sl">Total units</div></div>'
            '<div class="s"><div class="sn">' + str(len(skipped)) + '</div><div class="sl">No edge</div></div>'
            '<div class="s"><div class="sn">5u</div><div class="sl">Daily max</div></div>'
            '</div>'
            '<div class="st">Full Slate &mdash; ' + data["date"] + '</div>'
            + cards +
            '<footer>EV-based model &nbsp;&middot;&nbsp; Never bet more than you can afford to lose</footer>'
            '</body></html>')

def main():
    print("Running MLB picks generator for " + TODAY + "...")
    games = fetch_mlb_games()
    if not games:
        print("No games found -- exiting")
        return

    odds_map = fetch_odds()
    games_with_data = []
    for g in games:
        odds = odds_map.get(g["away"] + "@" + g["home"], {})
        weather = fetch_weather(g["home"])
        gd = dict(g)
        gd["odds"] = odds
        gd["weather"] = weather
        games_with_data.append(gd)

    picks = call_groq(games_with_data)
    active = [p for p in picks if p.get("tier") != "SKIP"]

    output = {
        "date": TODAY,
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
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
