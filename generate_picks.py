"""
MLB Betting Model — Daily Picks Generator
- Runs every 2 hours via GitHub Actions
- Real 2025+2026 stats from MLB Stats API (cached daily)
- Live scores update every 30s in browser
- Groq free tier as AI engine
"""

import os, json, datetime, requests
from pathlib import Path

ODDS_API_KEY    = os.environ.get("ODDS_API_KEY", "")
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY", "")
GROQ_KEY        = os.environ.get("GROQ_API_KEY", "")
OUTPUT_DIR      = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)
TODAY      = datetime.date.today().isoformat()
NOW_ET     = datetime.datetime.now().strftime("%I:%M %p ET")
STATS_CACHE = OUTPUT_DIR / "stats_cache.json"

STADIUMS = {
    "New York Mets":(40.7571,-73.8458),"New York Yankees":(40.8296,-73.9262),
    "Boston Red Sox":(42.3467,-71.0972),"Tampa Bay Rays":(27.7683,-82.6534),
    "Baltimore Orioles":(39.2838,-76.6218),"Toronto Blue Jays":(43.6414,-79.3894),
    "Chicago White Sox":(41.8300,-87.6338),"Chicago Cubs":(41.9484,-87.6553),
    "Milwaukee Brewers":(43.0280,-87.9712),"Minnesota Twins":(44.9817,-93.2775),
    "Cleveland Guardians":(41.4962,-81.6852),"Detroit Tigers":(42.3390,-83.0485),
    "Kansas City Royals":(39.0517,-94.4803),"Houston Astros":(29.7572,-95.3555),
    "Texas Rangers":(32.7513,-97.0832),"Los Angeles Angels":(33.8003,-117.8827),
    "Oakland Athletics":(37.7516,-122.2005),"Seattle Mariners":(47.5914,-122.3325),
    "Los Angeles Dodgers":(34.0739,-118.2400),"San Francisco Giants":(37.7786,-122.3893),
    "San Diego Padres":(32.7076,-117.1570),"Arizona Diamondbacks":(33.4453,-112.0667),
    "Colorado Rockies":(39.7559,-104.9942),"Atlanta Braves":(33.8908,-84.4678),
    "Miami Marlins":(25.7781,-80.2197),"Philadelphia Phillies":(39.9061,-75.1665),
    "Washington Nationals":(38.8730,-77.0074),"Pittsburgh Pirates":(40.4469,-80.0058),
    "Cincinnati Reds":(39.0979,-84.5082),"St. Louis Cardinals":(38.6226,-90.1928),
}

def safe_float(val, default=0.0):
    try:
        if val in (None,"","-","--","-.--","---"): return default
        return float(val)
    except: return default

def score_id(game_str):
    return "s_" + game_str.replace(" @ ","_AT_").replace(" ","_")

def mlb_api(path, params=None):
    try:
        r = requests.get("https://statsapi.mlb.com/api/v1"+path, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("MLB API error: "+str(e))
        return {}

def fetch_sp_stats(season):
    data = mlb_api("/stats", {"stats":"season","playerPool":"All",
                               "sportId":"1","season":str(season),"group":"pitching","limit":"600"})
    result = {}
    for split in data.get("stats",[{}])[0].get("splits",[]):
        name = split.get("player",{}).get("fullName","")
        stat = split.get("stat",{})
        gs = int(stat.get("gamesStarted",0) or 0)
        if gs < 1: continue
        ip = safe_float(stat.get("inningsPitched","0"))
        so = int(stat.get("strikeOuts",0) or 0)
        bb = int(stat.get("baseOnBalls",0) or 0)
        result[name] = {
            "season":season,"gs":gs,"ip":round(ip,1),
            "era":safe_float(stat.get("era")),
            "whip":safe_float(stat.get("whip")),
            "k9":round(so/ip*9,2) if ip>0 else 0,
            "bb9":round(bb/ip*9,2) if ip>0 else 0,
        }
    return result

def fetch_team_pitching(season):
    data = mlb_api("/stats", {"stats":"season","group":"pitching","gameType":"R",
                               "season":str(season),"sportId":"1","playerPool":"All"})
    result = {}
    for split in data.get("stats",[{}])[0].get("splits",[]):
        team = split.get("team",{}).get("name","")
        stat = split.get("stat",{})
        if not team: continue
        ip = safe_float(stat.get("inningsPitched","0"))
        so = int(stat.get("strikeOuts",0) or 0)
        result[team] = {
            "season":season,
            "team_era":safe_float(stat.get("era")),
            "team_whip":safe_float(stat.get("whip")),
            "team_k9":round(so/ip*9,2) if ip>0 else 0,
        }
    return result

def fetch_team_batting(season):
    data = mlb_api("/stats", {"stats":"season","group":"hitting","gameType":"R",
                               "season":str(season),"sportId":"1","playerPool":"All"})
    result = {}
    for split in data.get("stats",[{}])[0].get("splits",[]):
        team = split.get("team",{}).get("name","")
        stat = split.get("stat",{})
        if not team: continue
        g = int(stat.get("gamesPlayed",1) or 1)
        runs = int(stat.get("runs",0) or 0)
        # Skip early season noise — need at least 10 games for meaningful batting stats
        if g < 10 and season == 2026:
            continue
        ops = safe_float(stat.get("ops"))
        # Sanity check — OPS above 1.2 in small sample is noise, skip it
        if ops > 1.2:
            continue
        result[team] = {
            "season":season,
            "games_played":g,
            "ops":ops,
            "avg":safe_float(stat.get("avg")),
            "obp":safe_float(stat.get("obp")),
            "slg":safe_float(stat.get("slg")),
            "runs_per_game":round(runs/g,2) if g>0 else 0,
        }
    return result

def fetch_and_cache_stats():
    if STATS_CACHE.exists():
        try:
            cached = json.loads(STATS_CACHE.read_text())
            if cached.get("date") == TODAY:
                print("Using cached stats")
                return cached
        except: pass
    print("Fetching fresh stats...")
    stats = {
        "date":TODAY,
        "sp_2025":fetch_sp_stats(2025),
        "sp_2026":fetch_sp_stats(2026),
        "team_pitching_2025":fetch_team_pitching(2025),
        "team_pitching_2026":fetch_team_pitching(2026),
        "team_batting_2025":fetch_team_batting(2025),
        "team_batting_2026":fetch_team_batting(2026),
    }
    print("SP stats: "+str(len(stats["sp_2025"]))+" in 2025, "+str(len(stats["sp_2026"]))+" in 2026")
    STATS_CACHE.write_text(json.dumps(stats))
    return stats

def get_pitcher_stats(name, stats):
    # Try exact match first, then fuzzy match on last name
    def find_in(pool, n):
        if n in pool:
            return pool[n]
        # Try matching by last name only as fallback
        last = n.split()[-1].lower() if n else ""
        for k, v in pool.items():
            if k.split()[-1].lower() == last and last:
                return v
        return {}
    s25 = find_in(stats["sp_2025"], name)
    s26 = find_in(stats["sp_2026"], name)
    if not s25 and not s26:
        return {"note":"No stats available — pitcher may be new or name mismatch"}
    if not s26 or s26.get("gs",0) == 0:
        s25["note"] = "2025 only (no 2026 starts yet)"
        return s25
    gs26 = s26.get("gs",0)
    if gs26 >= 10:
        s26["note"] = "2026 primary ("+str(gs26)+" starts)"
        return s26
    elif gs26 >= 5:
        blended = {}
        for key in ["era","whip","k9","bb9"]:
            v25 = s25.get(key,0); v26 = s26.get(key,0)
            blended[key] = round(v26*0.6+v25*0.4,2) if v25 and v26 else (v26 or v25)
        blended["gs_2026"] = gs26
        blended["gs_2025"] = s25.get("gs",0)
        blended["note"] = "Blended 60/40 ("+str(gs26)+" 2026 starts)"
        return blended
    else:
        s25["gs_2026"] = gs26
        s25["era_2026"] = s26.get("era")
        s25["note"] = "Primarily 2025 (only "+str(gs26)+" 2026 starts)"
        return s25

def get_team_stats(team, stats, stat_type):
    s26 = stats.get(stat_type+"_2026",{}).get(team,{})
    s25 = stats.get(stat_type+"_2025",{}).get(team,{})
    if s26: s26["note"]="2026 YTD"; return s26
    if s25: s25["note"]="2025 full season"; return s25
    return {}

def fetch_mlb_games():
    url = "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date="+TODAY+"&hydrate=probablePitcher,linescore,team"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        games = []
        for de in r.json().get("dates",[]):
            for g in de.get("games",[]):
                abstract = g.get("status",{}).get("abstractGameState","")
                detailed = g.get("status",{}).get("detailedState","")
                home = g["teams"]["home"]["team"]["name"]
                away = g["teams"]["away"]["team"]["name"]
                home_sp = g["teams"]["home"].get("probablePitcher",{}).get("fullName","TBD")
                away_sp = g["teams"]["away"].get("probablePitcher",{}).get("fullName","TBD")
                hs = g["teams"]["home"].get("score",None)
                as_ = g["teams"]["away"].get("score",None)
                live_score = away+" "+str(as_)+" - "+home+" "+str(hs) if hs is not None and as_ is not None else None
                games.append({
                    "home":home,"away":away,
                    "game_time":g.get("gameDate",""),
                    "home_sp":home_sp,"away_sp":away_sp,
                    "venue":g.get("venue",{}).get("name",""),
                    "status":detailed or abstract,
                    "live_score":live_score,
                })
        print("Fetched "+str(len(games))+" games")
        return games
    except Exception as e:
        print("Games error: "+str(e))
        return []

def fetch_odds():
    if not ODDS_API_KEY: return {}
    try:
        r = requests.get(
            "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/",
            params={"apiKey":ODDS_API_KEY,"regions":"us","markets":"h2h,totals",
                    "oddsFormat":"american","dateFormat":"iso"},
            timeout=10
        )
        r.raise_for_status()
        odds_map = {}
        for event in r.json():
            home = event.get("home_team",""); away = event.get("away_team","")
            ml = {}; total = {}
            for bm in event.get("bookmakers",[])[:1]:
                for market in bm.get("markets",[]):
                    if market["key"]=="h2h":
                        for o in market["outcomes"]: ml[o["name"]] = o["price"]
                    elif market["key"]=="totals":
                        for o in market["outcomes"]:
                            if o["name"]=="Over": total["line"]=o.get("point",""); total["over"]=o["price"]
                            elif o["name"]=="Under": total["under"]=o["price"]
            odds_map[away+"@"+home] = {"moneyline":ml,"total":total}
        return odds_map
    except Exception as e:
        print("Odds error: "+str(e))
        return {}

def fetch_weather(team_name):
    coords = STADIUMS.get(team_name)
    if not coords or not WEATHER_API_KEY:
        return {"temp_f":"N/A","wind_mph":"N/A","wind_dir":"N/A","precip_pct":"N/A"}
    try:
        r = requests.get("https://api.openweathermap.org/data/2.5/forecast",
            params={"lat":coords[0],"lon":coords[1],"appid":WEATHER_API_KEY,"units":"imperial","cnt":4},
            timeout=10)
        r.raise_for_status()
        e = r.json()["list"][0]
        deg = e["wind"].get("deg",0)
        dirs = ["N","NE","E","SE","S","SW","W","NW"]
        return {
            "temp_f":round(e["main"]["temp"]),
            "wind_mph":round(e["wind"]["speed"]*2.237,1),
            "wind_dir":dirs[round(deg/45)%8],
            "precip_pct":round(e.get("pop",0)*100),
        }
    except:
        return {"temp_f":"N/A","wind_mph":"N/A","wind_dir":"N/A","precip_pct":"N/A"}

SYSTEM_PROMPT = """You are a sharp MLB betting analyst. Find positive EV bets using the REAL stats provided.
Never use memory for stats — only use the numbers in the data given to you.

STAT WEIGHTING (apply automatically based on note field):
- "2026 primary": trust 2026 stats fully
- "Blended": use the pre-calculated blended values
- "Primarily 2025": use 2025, note small 2026 sample
- "2025 only": baseline from 2025

ANALYSIS ORDER (most to least important):
1. SP quality — cite ERA, K/9, BB/9, WHIP from stats provided
2. Bullpen — cite team ERA/K9. Elite: Dodgers/Rays/Braves/Phillies. Weak: Rockies/A's/Nationals/CWS
3. Lineup/offense — cite OPS, runs/game. Injuries or missing starters = lower run expectation
4. Park factors — Coors +1.5, GABP +0.7, Globe Life +0.4, Petco -0.7, Oracle -0.5, T-Mobile -0.4
5. Weather — tiebreaker only, never sole reason. Dome = irrelevant.
   Wind 12+ mph OUT = lean OVER. Wind 12+ mph IN = lean UNDER. >85F adds ~0.4r. <50F subtracts ~0.4r.

BETTING RULES:
- Min edge: 3% ML, 4% totals, 5% run line
- Never ML worse than -200. Never total juice worse than -130
- Tier A = 7%+ edge (1.5u). Tier B = 4-6% (1u). Tier C = 3% (0.5u). Max 5u/day
- Prefer F5 totals — isolates SP, removes bullpen variance
- SKIP if: SP is TBD, both starters unknown rookies, rain 50%+, or no clear edge

IMPORTANT — flag any of these as they affect picks:
- SP scratch or change from what was listed
- Lineup missing key players
- Weather significantly different from forecast

OUTPUT: Raw JSON array only. No markdown. No backticks. Every game must appear.

Each entry:
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
  "sp_analysis": "cite specific ERA/K9/WHIP numbers from data",
  "bullpen_note": "cite team ERA/K9 from data",
  "lineup_note": "lineup quality or known injuries",
  "park_note": "park factor impact",
  "weather_impact": "specific weather effect or Dome - N/A",
  "key_edge": "most important reason with specific stat cited",
  "rationale": "3 sentences: SP edge with stats. Supporting factors. Why line has value.",
  "avoid_reason": "if SKIP: specific reason. Empty string otherwise.",
  "flags": "any SP changes, lineup news, or weather updates worth noting. Empty string if none."
}"""

def call_groq(games_with_data):
    if not GROQ_KEY:
        print("No GROQ_API_KEY")
        return []
    n = len(games_with_data)
    user_msg = (
        "Today is "+TODAY+". Analyze these "+str(n)+" MLB games using the real stats provided.\n"
        "Return a JSON array with exactly "+str(n)+" entries. Raw JSON only.\n\n"
        "GAMES:\n"+json.dumps(games_with_data, indent=2)
    )
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization":"Bearer "+GROQ_KEY,"Content-Type":"application/json"},
            json={
                "model":"llama-3.3-70b-versatile",
                "messages":[
                    {"role":"system","content":SYSTEM_PROMPT},
                    {"role":"user","content":user_msg},
                ],
                "temperature":0.1,"max_tokens":8000,
            },
            timeout=90
        )
        if not r.ok:
            print("Groq error: "+r.text[:300])
            return []
        raw = r.json()["choices"][0]["message"]["content"].strip()
        print("Groq response: "+str(len(raw))+" chars")
        if "```" in raw:
            for part in raw.split("```"):
                part = part.strip()
                if part.startswith("json"): part = part[4:].strip()
                if part.startswith("["): raw = part; break
        start = raw.find("["); end = raw.rfind("]")+1
        if start >= 0 and end > start: raw = raw[start:end]
        picks = json.loads(raw.strip())
        print("Groq returned "+str(len(picks))+" picks")
        return picks
    except Exception as e:
        print("Groq failed: "+str(e))
        return []

def build_archive_index():
    dated_files = sorted([f for f in OUTPUT_DIR.glob("????-??-??.html")], reverse=True)
    if not dated_files: return
    rows = ""
    for f in dated_files:
        d = f.stem
        rows += ('<a href="'+d+'.html" style="display:flex;justify-content:space-between;'
                 'align-items:center;padding:12px 16px;background:#fff;border:0.5px solid #e8e8e5;'
                 'border-radius:9px;margin-bottom:8px;text-decoration:none;color:#1a1a1a">'
                 '<span style="font-size:14px;font-weight:500">'+d+'</span>'
                 '<span style="font-size:12px;color:#999">View &rarr;</span></a>\n')
    html = ('<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>MLB Archive</title>'
            '<style>*{box-sizing:border-box;margin:0;padding:0}'
            'body{font-family:-apple-system,sans-serif;background:#f9f9f7;color:#1a1a1a;'
            'padding:1.25rem;max-width:700px;margin:0 auto}'
            'h1{font-size:20px;font-weight:700;margin-bottom:4px}'
            '.meta{font-size:13px;color:#888;margin-bottom:1.5rem}</style></head><body>'
            '<h1>MLB Picks Archive</h1>'
            '<div class="meta">Click any date to review picks</div>'
            '<a href="index.html" style="display:flex;justify-content:space-between;'
            'align-items:center;padding:12px 16px;background:#E1F5EE;border:0.5px solid #5DCAA5;'
            'border-radius:9px;margin-bottom:16px;text-decoration:none;color:#0F6E56">'
            '<span style="font-size:14px;font-weight:600">Today &mdash; '+TODAY+'</span>'
            '<span style="font-size:12px">View &rarr;</span></a>'+rows+'</body></html>')
    (OUTPUT_DIR / "archive.html").write_text(html)

def build_html(data):
    all_picks = data.get("picks",[])
    active  = [p for p in all_picks if p.get("tier") != "SKIP"]
    skipped = [p for p in all_picks if p.get("tier") == "SKIP"]
    total_u = round(sum(p.get("units",0) for p in active),1)
    gen     = data.get("generated_at","")[:16].replace("T"," ")
    date    = data["date"]

    TBAR = {"A":"#1D9E75","B":"#378ADD","C":"#BA7517"}
    TBG  = {"A":"#E1F5EE","B":"#E6F1FB","C":"#FAEEDA"}
    TTC  = {"A":"#0F6E56","B":"#185FA5","C":"#854F0B"}
    TLBL = {"A":"TIER A &mdash; PLAY","B":"TIER B &mdash; PLAY","C":"TIER C &mdash; LEAN"}

    def sp_box(label, name):
        return ('<div style="background:#f7f7f5;border-radius:7px;padding:8px 10px">'
                '<div style="font-size:10px;color:#999;margin-bottom:3px;text-transform:uppercase;'
                'letter-spacing:.05em">'+label+'</div>'
                '<div style="font-size:13px;font-weight:500">'+str(name)+'</div></div>')

    def mrow(icon, text):
        t = str(text)
        if not t or t in ("N/A","null","None",""): return ""
        return '<div style="font-size:12px;color:#666;margin-bottom:3px">'+icon+' '+t+'</div>'

    def flag_row(text):
        t = str(text)
        if not t or t in ("","null","None"): return ""
        return ('<div style="font-size:12px;background:#FAEEDA;color:#633806;padding:4px 8px;'
                'border-radius:4px;margin-bottom:6px">&#9888; '+t+'</div>')

    def score_span(game):
        sid = score_id(game)
        return ('<span id="'+sid+'" style="font-size:11px;background:#f0f0ee;color:#888;'
                'padding:2px 8px;border-radius:4px;margin-left:6px">--</span>')

    def pick_card(p):
        t = p.get("tier","C")
        c = TBAR.get(t,"#888"); bg = TBG.get(t,"#eee"); tc = TTC.get(t,"#333")
        ev = p.get("ev_pct",0); bw = min(int(ev)*8,100)
        game = str(p.get("game",""))
        return (
            '<div style="background:#fff;border:0.5px solid #e0e0e0;border-left:3px solid '+c+';'
            'border-radius:10px;padding:1rem 1.25rem;margin-bottom:10px">'
            '<span style="background:'+bg+';color:'+tc+';font-size:11px;font-weight:600;'
            'padding:2px 9px;border-radius:4px;display:inline-block;margin-bottom:8px">'+TLBL.get(t,"LEAN")+'</span>'
            +flag_row(p.get("flags",""))+
            '<div style="font-size:16px;font-weight:600;margin-bottom:2px">'+str(p.get("pick",""))+'</div>'
            '<div style="font-size:13px;color:#777;margin-bottom:10px">'
            +game+' &nbsp;&middot;&nbsp; '+str(p.get("line","N/A"))+' &nbsp;&middot;&nbsp; '+str(p.get("units",0))+'u'
            +score_span(game)+'</div>'
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px">'
            +sp_box("Away SP",p.get("away_sp","TBD"))+sp_box("Home SP",p.get("home_sp","TBD"))+'</div>'
            '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">'
            '<span style="font-size:11px;background:#f0f0ee;padding:2px 9px;border-radius:20px;color:#555">'
            'Win '+str(p.get("win_prob_pct",0))+'% vs implied '+str(p.get("implied_prob_pct",0))+'%</span>'
            '<span style="font-size:11px;background:'+bg+';color:'+tc+';padding:2px 9px;'
            'border-radius:20px;font-weight:600">+'+str(ev)+'% EV</span></div>'
            '<div style="height:4px;background:#f0f0ee;border-radius:2px;margin-bottom:10px;overflow:hidden">'
            '<div style="height:100%;width:'+str(bw)+'%;background:'+c+';border-radius:2px"></div></div>'
            '<div style="margin-bottom:10px">'
            +mrow("&#9918;",p.get("sp_analysis",""))
            +mrow("&#128101;",p.get("bullpen_note",""))
            +mrow("&#128200;",p.get("lineup_note",""))
            +mrow("&#127966;",p.get("park_note",""))
            +mrow("&#127748;",p.get("weather_impact",""))+'</div>'
            '<div style="border-top:0.5px solid #eee;padding-top:8px">'
            '<div style="font-size:12px;font-weight:600;color:#222;margin-bottom:3px">'
            'Key edge: '+str(p.get("key_edge",""))+'</div>'
            '<div style="font-size:12px;color:#666;line-height:1.6">'+str(p.get("rationale",""))+'</div>'
            '</div></div>'
        )

    def skip_card(p):
        game = str(p.get("game",""))
        return (
            '<div style="background:#fff;border:0.5px solid #e0e0e0;border-left:3px solid #B4B2A9;'
            'border-radius:10px;padding:1rem 1.25rem;margin-bottom:10px">'
            '<span style="background:#F1EFE8;color:#5F5E5A;font-size:11px;font-weight:600;'
            'padding:2px 9px;border-radius:4px;display:inline-block;margin-bottom:8px">SKIP &mdash; NO EDGE</span>'
            +flag_row(p.get("flags",""))+
            '<div style="font-size:16px;font-weight:600;margin-bottom:2px">'
            +game+score_span(game)+'</div>'
            '<div style="font-size:13px;color:#777;margin-bottom:10px">'+str(p.get("venue",""))+'</div>'
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px">'
            +sp_box("Away SP",p.get("away_sp","TBD"))+sp_box("Home SP",p.get("home_sp","TBD"))+'</div>'
            '<div style="margin-bottom:10px">'
            +mrow("&#9918;",p.get("sp_analysis",""))
            +mrow("&#128101;",p.get("bullpen_note",""))
            +mrow("&#128200;",p.get("lineup_note",""))
            +mrow("&#127966;",p.get("park_note",""))
            +mrow("&#127748;",p.get("weather_impact",""))+'</div>'
            '<div style="border-top:0.5px solid #eee;padding-top:8px">'
            '<div style="font-size:12px;font-weight:600;color:#A32D2D;margin-bottom:3px">'
            'Why skip: '+str(p.get("avoid_reason","No edge"))+'</div>'
            '<div style="font-size:12px;color:#888;line-height:1.6">'+str(p.get("rationale",""))+'</div>'
            '</div></div>'
        )

    cards = "".join(pick_card(p) for p in active) + "".join(skip_card(p) for p in skipped)
    if not cards:
        cards = '<p style="color:#888;font-size:14px;padding:1.5rem 0;text-align:center">No games found today.</p>'

    # Live score JS — runs in browser, updates every 30s
    live_js = (
        '<script>'
        'var D="'+date+'";'
        'function toET(iso){'
        'var d=new Date(iso);'
        'return d.toLocaleTimeString("en-US",{timeZone:"America/New_York",hour:"numeric",minute:"2-digit"})+" ET";'
        '}'
        'function upd(){'
        'fetch("https://statsapi.mlb.com/api/v1/schedule?sportId=1&date="+D+"&hydrate=linescore,team")'
        '.then(function(r){return r.json();})'
        '.then(function(data){'
        'var games=[];'
        '(data.dates||[]).forEach(function(d){(d.games||[]).forEach(function(g){games.push(g);});});'
        'games.forEach(function(g){'
        'var away=g.teams.away.team.name;'
        'var home=g.teams.home.team.name;'
        'var sid="s_"+(away+"_AT_"+home).replace(/ /g,"_");'
        'var el=document.getElementById(sid);'
        'if(!el)return;'
        'var ab=g.status.abstractGameState;'
        'var aS=g.teams.away.score;'
        'var hS=g.teams.home.score;'
        'if(ab==="Final"){'
        'el.textContent="FINAL: "+away+" "+aS+" - "+home+" "+hS;'
        'el.style.background="#f0f0ee";el.style.color="#555";'
        '}else if(ab==="Live"){'
        'var inn=(g.linescore&&g.linescore.currentInningOrdinal)?g.linescore.currentInningOrdinal:"";'
        'el.textContent="LIVE "+inn+": "+away+" "+aS+" - "+home+" "+hS;'
        'el.style.background="#FAEEDA";el.style.color="#633806";'
        '}else{'
        'el.textContent=toET(g.gameDate);'
        'el.style.background="#f0f0ee";el.style.color="#888";'
        '}'
        '});'
        'var lu=document.getElementById("last_update");'
        'if(lu)lu.textContent="Scores updated "+new Date().toLocaleTimeString("en-US",{timeZone:"America/New_York",hour:"numeric",minute:"2-digit"})+" ET";'
        '}).catch(function(e){console.log("score err",e);});'
        '}'
        'upd();setInterval(upd,30000);'
        '</script>'
    )

    css = (
        '<style>*{box-sizing:border-box;margin:0;padding:0}'
        'body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;'
        'background:#f9f9f7;color:#1a1a1a;padding:1.25rem;max-width:700px;margin:0 auto}'
        'h1{font-size:20px;font-weight:700;margin-bottom:3px}'
        '.meta{font-size:13px;color:#888;margin-bottom:2px}'
        '.updated{font-size:11px;color:#aaa;margin-bottom:1.25rem}'
        '.sum{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:1.25rem}'
        '.s{background:#fff;border:0.5px solid #e8e8e5;border-radius:9px;padding:10px 12px}'
        '.sn{font-size:22px;font-weight:700}'
        '.sl{font-size:10px;color:#999;margin-top:2px;text-transform:uppercase;letter-spacing:.04em}'
        '.st{font-size:13px;font-weight:600;color:#999;text-transform:uppercase;'
        'letter-spacing:.06em;margin:1.25rem 0 0.5rem}'
        'footer{font-size:11px;color:#bbb;margin-top:1.5rem;text-align:center;padding-bottom:1rem}'
        '</style>'
    )

    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>MLB Picks - '+date+'</title>'+css+'</head><body>'
        '<h1>MLB Betting Model</h1>'
        '<div class="meta">'+date+' &nbsp;&middot;&nbsp; '+str(data["total_games"])+' games'
        ' &nbsp;&middot;&nbsp; <a href="archive.html" style="color:#378ADD;text-decoration:none">Archive &rarr;</a></div>'
        '<div class="updated">Picks generated '+gen+' ET &nbsp;&middot;&nbsp; Updates every 2hrs'
        ' &nbsp;&middot;&nbsp; <span id="last_update">Scores loading...</span></div>'
        '<div class="sum">'
        '<div class="s"><div class="sn" style="color:#1D9E75">'+str(len(active))+'</div><div class="sl">Active picks</div></div>'
        '<div class="s"><div class="sn">'+str(total_u)+'u</div><div class="sl">Total units</div></div>'
        '<div class="s"><div class="sn">'+str(len(skipped))+'</div><div class="sl">No edge</div></div>'
        '<div class="s"><div class="sn">5u</div><div class="sl">Daily max</div></div>'
        '</div>'
        '<div class="st">Full Slate &mdash; '+date+'</div>'
        +cards+
        '<footer>EV model &nbsp;&middot;&nbsp; Real MLB stats 2025+2026 &nbsp;&middot;&nbsp; Never bet more than you can afford to lose</footer>'
        +live_js+
        '</body></html>'
    )

def main():
    print("Running MLB picks generator for "+TODAY+"...")
    stats = fetch_and_cache_stats()
    games = fetch_mlb_games()
    if not games:
        print("No games found -- exiting")
        return
    odds_map = fetch_odds()
    games_with_data = []
    for g in games:
        odds = odds_map.get(g["away"]+"@"+g["home"],{})
        weather = fetch_weather(g["home"])
        gd = dict(g)
        gd["odds"]               = odds
        gd["weather"]            = weather
        gd["home_sp_stats"]      = get_pitcher_stats(g["home_sp"], stats)
        gd["away_sp_stats"]      = get_pitcher_stats(g["away_sp"], stats)
        gd["home_team_pitching"] = get_team_stats(g["home"], stats, "team_pitching")
        gd["away_team_pitching"] = get_team_stats(g["away"], stats, "team_pitching")
        gd["home_team_batting"]  = get_team_stats(g["home"], stats, "team_batting")
        gd["away_team_batting"]  = get_team_stats(g["away"], stats, "team_batting")
        games_with_data.append(gd)
    picks = call_groq(games_with_data)
    active = [p for p in picks if p.get("tier") != "SKIP"]
    output = {
        "date": TODAY,
        "generated_at": datetime.datetime.utcnow().isoformat()+"Z",
        "stats_date": stats.get("date",""),
        "total_games": len(games),
        "total_picks": len(active),
        "picks": picks,
        "raw_games_data": games_with_data,
    }
    (OUTPUT_DIR/"picks.json").write_text(json.dumps(output, indent=2))
    html = build_html(output)
    (OUTPUT_DIR/(TODAY+".html")).write_text(html)
    (OUTPUT_DIR/"index.html").write_text(html)
    build_archive_index()
    print("Done. "+str(len(active))+" active picks across "+str(len(games))+" games.")

if __name__ == "__main__":
    main()
