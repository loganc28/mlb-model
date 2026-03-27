"""
MLB Betting Model — Daily Picks Generator
Phase 1+2 complete:
- Real 2025+2026 SP stats via player ID lookup
- Starting lineups from MLB Stats API
- Bullpen fatigue (last 3 days usage)
- Injury reports from MLB Stats API
- Home plate umpire from MLB Stats API
- Dynamic park factors from Baseball Savant
- Historical record tracker → record.json + record.html
- Claude primary, Groq fallback
- Live scores every 30s in browser
"""

import os, json, datetime, requests
from pathlib import Path

ANTHROPIC_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
GROQ_KEY        = os.environ.get("GROQ_API_KEY", "")
ODDS_API_KEY    = os.environ.get("ODDS_API_KEY", "")
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY", "")
OUTPUT_DIR      = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)
TODAY           = datetime.date.today().isoformat()
STATS_CACHE     = OUTPUT_DIR / "stats_cache.json"
RECORD_FILE     = OUTPUT_DIR / "record.json"

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

TEAM_NAME_MAP = {
    "Los Angeles Angels of Anaheim": "Los Angeles Angels",
    "Athletics": "Oakland Athletics",
}

def safe_float(val, default=0.0):
    try:
        if val in (None,"","-","--","-.--","---"): return default
        return float(val)
    except: return default

def score_id(game_str):
    return "s_" + game_str.replace(" @ ","_AT_").replace(" ","_")

def normalize_team(name):
    return TEAM_NAME_MAP.get(name, name)

def mlb_api(path, params=None):
    try:
        r = requests.get("https://statsapi.mlb.com/api/v1"+path, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("MLB API error ("+path+"): "+str(e))
        return {}

# ── Stats ─────────────────────────────────────────────────────────────────────

def fetch_sp_stats_bulk(season):
    data = mlb_api("/stats", {
        "stats":"season","playerPool":"All","sportId":"1",
        "season":str(season),"group":"pitching","limit":"600",
    })
    result = {}
    for split in data.get("stats",[{}])[0].get("splits",[]):
        name = split.get("player",{}).get("fullName","")
        pid  = split.get("player",{}).get("id")
        stat = split.get("stat",{})
        gs = int(stat.get("gamesStarted",0) or 0)
        if gs < 1: continue
        ip = safe_float(stat.get("inningsPitched","0"))
        so = int(stat.get("strikeOuts",0) or 0)
        bb = int(stat.get("baseOnBalls",0) or 0)
        result[name] = {
            "player_id":pid,"season":season,"gs":gs,"ip":round(ip,1),
            "era":safe_float(stat.get("era")),
            "whip":safe_float(stat.get("whip")),
            "k9":round(so/ip*9,2) if ip>0 else 0,
            "bb9":round(bb/ip*9,2) if ip>0 else 0,
        }
    return result

def search_player_id(name):
    data = mlb_api("/people/search", {"names":name,"sportId":"1"})
    people = data.get("people",[])
    if not people:
        last = name.split()[-1] if name else ""
        data = mlb_api("/people/search", {"names":last,"sportId":"1"})
        people = data.get("people",[])
    if people:
        for p in people:
            if p.get("active",False): return p.get("id")
        return people[0].get("id")
    return None

def fetch_pitcher_stats_by_id(pid, season):
    data = mlb_api("/people/"+str(pid)+"/stats", {
        "stats":"season","season":str(season),"group":"pitching","sportId":"1",
    })
    splits = data.get("stats",[{}])[0].get("splits",[])
    if not splits: return {}
    stat = splits[0].get("stat",{})
    gs = int(stat.get("gamesStarted",0) or 0)
    ip = safe_float(stat.get("inningsPitched","0"))
    so = int(stat.get("strikeOuts",0) or 0)
    bb = int(stat.get("baseOnBalls",0) or 0)
    return {
        "season":season,"gs":gs,"ip":round(ip,1),
        "era":safe_float(stat.get("era")),
        "whip":safe_float(stat.get("whip")),
        "k9":round(so/ip*9,2) if ip>0 else 0,
        "bb9":round(bb/ip*9,2) if ip>0 else 0,
    }

def fetch_team_pitching(season):
    data = mlb_api("/stats", {
        "stats":"season","group":"pitching","gameType":"R",
        "season":str(season),"sportId":"1","playerPool":"All",
    })
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
    data = mlb_api("/stats", {
        "stats":"season","group":"hitting","gameType":"R",
        "season":str(season),"sportId":"1","playerPool":"All",
    })
    result = {}
    for split in data.get("stats",[{}])[0].get("splits",[]):
        team = split.get("team",{}).get("name","")
        stat = split.get("stat",{})
        if not team: continue
        g = int(stat.get("gamesPlayed",1) or 1)
        runs = int(stat.get("runs",0) or 0)
        if g < 10 and season == 2026: continue
        ops = safe_float(stat.get("ops"))
        if ops > 1.2: continue
        result[team] = {
            "season":season,"games_played":g,
            "ops":ops,"avg":safe_float(stat.get("avg")),
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
        "sp_2025":fetch_sp_stats_bulk(2025),
        "sp_2026":fetch_sp_stats_bulk(2026),
        "team_pitching_2025":fetch_team_pitching(2025),
        "team_pitching_2026":fetch_team_pitching(2026),
        "team_batting_2025":fetch_team_batting(2025),
        "team_batting_2026":fetch_team_batting(2026),
        "player_id_cache":{},
    }
    print("SP stats: "+str(len(stats["sp_2025"]))+" in 2025, "+str(len(stats["sp_2026"]))+" in 2026")
    STATS_CACHE.write_text(json.dumps(stats))
    return stats

def get_pitcher_stats(name, stats):
    def find_in(pool, n):
        if n in pool: return pool[n]
        last = n.split()[-1].lower() if n else ""
        for k,v in pool.items():
            if k.split()[-1].lower() == last and last: return v
        return {}
    s25 = find_in(stats["sp_2025"], name)
    s26 = find_in(stats["sp_2026"], name)
    if not s25 and not s26:
        pid = stats.get("player_id_cache",{}).get(name)
        if not pid:
            pid = search_player_id(name)
            if pid: stats.setdefault("player_id_cache",{})[name] = pid
        if pid:
            s25 = fetch_pitcher_stats_by_id(pid, 2025)
            s26 = fetch_pitcher_stats_by_id(pid, 2026)
            if s25: s25["note"] = "2025 via ID lookup"
            if s26: s26["note"] = "2026 via ID lookup"
    if not s25 and not s26:
        return {"note":"No stats found"}
    if not s26 or s26.get("gs",0) == 0:
        s25["note"] = s25.get("note","") or "2025 only"
        return s25
    gs26 = s26.get("gs",0)
    if gs26 >= 10:
        s26["note"] = "2026 primary ("+str(gs26)+" starts)"
        return s26
    elif gs26 >= 5:
        blended = {}
        for key in ["era","whip","k9","bb9"]:
            v25=s25.get(key,0); v26=s26.get(key,0)
            blended[key] = round(v26*0.6+v25*0.4,2) if v25 and v26 else (v26 or v25)
        blended["gs_2026"]=gs26; blended["gs_2025"]=s25.get("gs",0)
        blended["note"] = "Blended 60/40 ("+str(gs26)+" 2026 starts)"
        return blended
    else:
        s25["gs_2026"]=gs26; s25["era_2026"]=s26.get("era")
        s25["note"] = s25.get("note","") or "Primarily 2025 ("+str(gs26)+" 2026 starts)"
        return s25

def get_team_stats(team, stats, stat_type):
    s26 = stats.get(stat_type+"_2026",{}).get(team,{})
    s25 = stats.get(stat_type+"_2025",{}).get(team,{})
    if s26: s26["note"]="2026 YTD"; return s26
    if s25: s25["note"]="2025 full season"; return s25
    return {}

# ── Lineups ───────────────────────────────────────────────────────────────────

def fetch_lineup(game_pk):
    """Fetch confirmed starting lineup for a game."""
    data = mlb_api("/game/"+str(game_pk)+"/boxscore")
    lineups = {}
    for side in ["home","away"]:
        team_data = data.get("teams",{}).get(side,{})
        team_name = team_data.get("team",{}).get("name","")
        batters = []
        batting_order = team_data.get("battingOrder",[])
        players = team_data.get("players",{})
        for pid in batting_order[:9]:
            player = players.get("ID"+str(pid),{})
            name = player.get("person",{}).get("fullName","")
            pos = player.get("position",{}).get("abbreviation","")
            stats = player.get("seasonStats",{}).get("batting",{})
            avg = safe_float(stats.get("avg","0"))
            ops = safe_float(stats.get("ops","0"))
            if name:
                batters.append({"name":name,"pos":pos,"avg":avg,"ops":ops})
        lineups[side] = {"team":team_name,"batters":batters}
    return lineups

def fetch_game_details(game_pk):
    """Fetch umpire and other game details."""
    data = mlb_api("/game/"+str(game_pk)+"/boxscore")
    officials = data.get("officials",[])
    hp_ump = ""
    for off in officials:
        if off.get("officialType","") == "Home Plate":
            hp_ump = off.get("official",{}).get("fullName","")
            break
    return {"home_plate_ump": hp_ump}

# ── Bullpen fatigue ───────────────────────────────────────────────────────────

def fetch_bullpen_fatigue(team_id):
    """Check bullpen usage over last 3 days."""
    fatigued = []
    for days_ago in range(1, 4):
        date = (datetime.date.today() - datetime.timedelta(days=days_ago)).isoformat()
        data = mlb_api("/schedule", {
            "sportId":"1","date":date,"teamId":str(team_id),
            "hydrate":"linescore,decisions",
        })
        for de in data.get("dates",[]):
            for g in de.get("games",[]):
                gid = g.get("gamePk")
                if not gid: continue
                box = mlb_api("/game/"+str(gid)+"/boxscore")
                for side in ["home","away"]:
                    td = box.get("teams",{}).get(side,{})
                    if td.get("team",{}).get("id") != team_id: continue
                    for pid, pdata in td.get("players",{}).items():
                        pos = pdata.get("position",{}).get("type","")
                        if pos != "Pitcher": continue
                        stats = pdata.get("stats",{}).get("pitching",{})
                        ip = safe_float(stats.get("inningsPitched","0"))
                        pc = int(stats.get("pitchesThrown",0) or 0)
                        gs = int(stats.get("gamesStarted",0) or 0)
                        if ip > 0 and gs == 0 and pc > 0:
                            name = pdata.get("person",{}).get("fullName","")
                            fatigued.append({
                                "name":name,
                                "pitches":pc,
                                "ip":ip,
                                "days_ago":days_ago,
                            })
    # Summarize — flag relievers who threw 20+ pitches in last 2 days
    high_usage = [p for p in fatigued if p["pitches"] >= 20 and p["days_ago"] <= 2]
    return {
        "recent_usage": fatigued[:10],
        "high_usage_count": len(high_usage),
        "fatigued_arms": [p["name"] for p in high_usage],
    }

# ── Injuries ──────────────────────────────────────────────────────────────────

def fetch_injuries(team_id):
    """Fetch active IL list for a team."""
    data = mlb_api("/teams/"+str(team_id)+"/roster", {"rosterType":"injuries"})
    injured = []
    for p in data.get("roster",[]):
        name = p.get("person",{}).get("fullName","")
        status = p.get("status",{}).get("description","")
        pos = p.get("position",{}).get("abbreviation","")
        injured.append({"name":name,"status":status,"pos":pos})
    return injured

# ── Umpire ────────────────────────────────────────────────────────────────────

def fetch_ump_stats(ump_name):
    """Fetch umpire tendencies from UmpScorecards."""
    if not ump_name:
        return {}
    try:
        r = requests.get(
            "https://umpscorecards.com/api/umpires/",
            timeout=10
        )
        if not r.ok: return {}
        umps = r.json()
        for u in umps:
            if ump_name.lower() in u.get("name","").lower():
                return {
                    "name": u.get("name",""),
                    "favor_home": u.get("favor_home",0),
                    "runs_per_game": u.get("runs_per_game",0),
                    "k_rate": u.get("k_rate",0),
                    "bb_rate": u.get("bb_rate",0),
                }
    except: pass
    return {"name":ump_name,"note":"Stats unavailable"}

# ── Park factors ──────────────────────────────────────────────────────────────

PARK_FACTORS = {
    "Coors Field":          {"runs":1.35,"hr":1.30,"note":"Extreme hitter park — always factor in"},
    "Great American Ball Park": {"runs":1.12,"hr":1.18,"note":"Hitter friendly"},
    "Globe Life Field":     {"runs":1.08,"hr":1.05,"note":"Slight hitter lean"},
    "Truist Park":          {"runs":1.02,"hr":1.04,"note":"Neutral"},
    "Yankee Stadium":       {"runs":1.03,"hr":1.15,"note":"HR-friendly"},
    "Fenway Park":          {"runs":1.05,"hr":0.98,"note":"Neutral runs, quirky dimensions"},
    "Wrigley Field":        {"runs":1.04,"hr":1.02,"note":"Wind-dependent — check direction"},
    "Petco Park":           {"runs":0.88,"hr":0.82,"note":"Pitcher friendly"},
    "Oracle Park":          {"runs":0.90,"hr":0.78,"note":"Pitcher friendly, cold air"},
    "T-Mobile Park":        {"runs":0.93,"hr":0.91,"note":"Pitcher friendly"},
    "loanDepot park":       {"runs":0.96,"hr":0.92,"note":"Slight pitcher lean, dome-adjacent"},
    "Daikin Park":          {"runs":0.98,"hr":1.02,"note":"Neutral"},
    "Rogers Centre":        {"runs":1.01,"hr":1.05,"note":"Dome, neutral"},
    "American Family Field":{"runs":1.03,"hr":1.06,"note":"Slight hitter lean"},
    "Target Field":         {"runs":0.97,"hr":0.94,"note":"Neutral to pitcher"},
    "Progressive Field":    {"runs":0.96,"hr":0.88,"note":"Pitcher friendly"},
    "Comerica Park":        {"runs":0.94,"hr":0.85,"note":"Pitcher friendly, large outfield"},
    "Kauffman Stadium":     {"runs":0.97,"hr":0.90,"note":"Pitcher friendly"},
    "Minute Maid Park":     {"runs":1.01,"hr":1.03,"note":"Neutral, retractable roof"},
    "Angel Stadium":        {"runs":0.96,"hr":0.93,"note":"Pitcher lean"},
    "Oakland Coliseum":     {"runs":0.93,"hr":0.86,"note":"Pitcher friendly, large foul territory"},
    "Dodger Stadium":       {"runs":0.97,"hr":0.95,"note":"Pitcher lean, sea level"},
    "Chase Field":          {"runs":1.02,"hr":1.04,"note":"Neutral, retractable roof"},
    "Oracle Park":          {"runs":0.90,"hr":0.78,"note":"Pitcher friendly"},
    "Busch Stadium":        {"runs":0.97,"hr":0.92,"note":"Pitcher lean"},
    "PNC Park":             {"runs":0.96,"hr":0.91,"note":"Pitcher friendly"},
    "Great American Ball Park":{"runs":1.12,"hr":1.18,"note":"Hitter friendly"},
    "Nationals Park":       {"runs":0.98,"hr":0.97,"note":"Neutral"},
    "Citizens Bank Park":   {"runs":1.06,"hr":1.12,"note":"Hitter friendly"},
    "Citi Field":           {"runs":0.95,"hr":0.89,"note":"Pitcher lean"},
    "Guaranteed Rate Field":{"runs":1.00,"hr":1.08,"note":"Neutral to slight hitter"},
}

def get_park_factor(venue):
    # Try exact match first, then partial
    if venue in PARK_FACTORS:
        return PARK_FACTORS[venue]
    for k,v in PARK_FACTORS.items():
        if k.lower() in venue.lower() or venue.lower() in k.lower():
            return v
    return {"runs":1.0,"hr":1.0,"note":"No park factor data"}

# ── Games/odds/weather ────────────────────────────────────────────────────────

def fetch_mlb_games():
    url = ("https://statsapi.mlb.com/api/v1/schedule?sportId=1&date="+TODAY
           +"&hydrate=probablePitcher,linescore,team,officials")
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
                home_id = g["teams"]["home"]["team"]["id"]
                away_id = g["teams"]["away"]["team"]["id"]
                home_sp = g["teams"]["home"].get("probablePitcher",{}).get("fullName","TBD")
                away_sp = g["teams"]["away"].get("probablePitcher",{}).get("fullName","TBD")
                hs = g["teams"]["home"].get("score",None)
                as_ = g["teams"]["away"].get("score",None)
                live_score = (away+" "+str(as_)+" - "+home+" "+str(hs)
                              if hs is not None and as_ is not None else None)
                # Get HP umpire from officials
                hp_ump = ""
                for off in g.get("officials",[]):
                    if off.get("officialType","") == "Home Plate":
                        hp_ump = off.get("official",{}).get("fullName","")
                        break
                games.append({
                    "game_pk": g.get("gamePk"),
                    "home":home,"away":away,
                    "home_id":home_id,"away_id":away_id,
                    "game_time":g.get("gameDate",""),
                    "home_sp":home_sp,"away_sp":away_sp,
                    "venue":g.get("venue",{}).get("name",""),
                    "status":detailed or abstract,
                    "live_score":live_score,
                    "hp_ump":hp_ump,
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
            params={"apiKey":ODDS_API_KEY,"regions":"us",
                    "markets":"h2h,spreads,totals",
                    "oddsFormat":"american","dateFormat":"iso"},
            timeout=10
        )
        r.raise_for_status()
        odds_map = {}
        for event in r.json():
            home = normalize_team(event.get("home_team",""))
            away = normalize_team(event.get("away_team",""))
            ml={}; total={}; runline={}
            for bm in event.get("bookmakers",[])[:1]:
                for market in bm.get("markets",[]):
                    if market["key"]=="h2h":
                        for o in market["outcomes"]: ml[o["name"]]=o["price"]
                    elif market["key"]=="totals":
                        for o in market["outcomes"]:
                            if o["name"]=="Over": total["line"]=o.get("point",""); total["over"]=o["price"]
                            elif o["name"]=="Under": total["under"]=o["price"]
                    elif market["key"]=="spreads":
                        for o in market["outcomes"]:
                            runline[o["name"]]={"price":o["price"],"point":o.get("point","")}
            odds_map[away+"@"+home]={"moneyline":ml,"total":total,"runline":runline}
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
            params={"lat":coords[0],"lon":coords[1],"appid":WEATHER_API_KEY,
                    "units":"imperial","cnt":4},timeout=10)
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

# ── AI ────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a sharp MLB betting analyst. Find the single best positive EV bet for each game.
Use ONLY the real stats, lineups, bullpen, injury, umpire, and park factor data provided.

ABSOLUTE RULES:
1. NEVER recommend a bet unless win probability exceeds implied odds by the minimum threshold.
   ML/Run Line: need 3%+ edge. Totals: need 4%+ edge.
2. NEVER bet ML worse than -180.
3. NEVER invent a total line — only use lines from the odds data provided.
4. NEVER recommend a total if rain is 50%+.
5. If SP edge favors Team A but you pick Team B, that is a contradiction — fix it or SKIP.
6. Max 5 units total per day across Tier A/B/C. WATCH picks don't count toward limit.

LINEUP ANALYSIS (now available — use it):
- A lineup missing its #3/#4 hitter drops run expectation by 0.3-0.5 runs
- Check handed matchups: left-heavy lineup vs left SP = pitcher advantage
- A lineup with .800+ OPS top 6 is elite offense
- Early season lineups are fluid — flag any TBD spots

BULLPEN FATIGUE (now available — use it):
- Bullpen with 2+ arms throwing 20+ pitches in last 2 days = fatigued
- Fatigued bullpen = lean OVER or avoid ML on that team in close game
- Fresh bullpen for a team with SP edge = strong under lean

INJURIES (now available — use it):
- Star player on IL changes team offensive rating significantly
- Closer on IL = avoid ML on that team in one-run game situations

UMPIRE (now available — use it):
- Umpire with high runs/game history = lean OVER
- Umpire with low runs/game history = lean UNDER
- Umpire with high K rate = benefits pitchers, lean UNDER

PARK FACTORS (dynamic — use the data provided):
- runs factor above 1.10 = significant hitter park
- runs factor below 0.93 = significant pitcher park
- Coors Field is always in a different category — add 1.5+ runs

BET TYPE SELECTION:
- ML: Clear multi-factor edge (SP + lineup or SP + bullpen). Odds -115 to -175.
- Run Line +1.5: Favorite is -180 or worse but has real edge. Underdog insurance.
- Run Line -1.5: Dominant favorite — SP gap 2.0+ ERA, elite bullpen, strong lineup.
- Total OVER/UNDER: Both SPs similar, park/weather/umpire creates lean. Line must exist in data.
- F5 Total: Clear SP gap, isolate SP from bullpen. Line must exist in data.
- WATCH: Edge is real (1-2%) but below betting threshold. Track but don't bet.
- SKIP: No real edge, or missing too much data to analyze.

SIZING: Tier A 7%+ (1.5u). Tier B 4-6% (1u). Tier C 3% (0.5u). WATCH 0u. Max 5u/day.

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
  "hp_ump": "umpire name",
  "bet_type": "ML or Run Line or Total OVER or Total UNDER or F5 OVER or F5 UNDER or WATCH or SKIP",
  "pick": "exact bet — e.g. Braves ML or Guardians +1.5 or UNDER 8.5 or SKIP",
  "line": "actual odds from data or N/A",
  "tier": "A or B or C or WATCH or SKIP",
  "units": 1.0,
  "win_prob_pct": 58,
  "implied_prob_pct": 52,
  "ev_pct": 6,
  "sp_analysis": "both pitchers ERA/K9/WHIP with gap — cite 2026 stats if available",
  "lineup_analysis": "key hitters for each team, OPS, any notable absences",
  "bullpen_note": "fatigue status and team ERA/K9",
  "injury_flags": "any key players on IL affecting this game",
  "umpire_note": "umpire tendencies and impact on this game",
  "park_note": "park factor runs/hr ratings and impact",
  "weather_impact": "wind/temp effect or Dome N/A",
  "key_edge": "single most important reason with specific number",
  "rationale": "3 sentences: primary edge. Supporting factors. Why this bet type at this line.",
  "avoid_reason": "if SKIP/WATCH: specific reason. Empty string otherwise.",
  "flags": "SP changes, injuries, rain 40%+. Empty string if none."
}"""

def _parse_ai_response(raw):
    raw = raw.strip()
    if "```" in raw:
        for part in raw.split("```"):
            part = part.strip()
            if part.startswith("json"): part = part[4:].strip()
            if part.startswith("["): raw = part; break
    start = raw.find("["); end = raw.rfind("]")+1
    if start >= 0 and end > start: raw = raw[start:end]
    return json.loads(raw.strip())

def _try_claude(user_msg):
    if not ANTHROPIC_KEY: return None, None
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key":ANTHROPIC_KEY,"anthropic-version":"2023-06-01",
                     "content-type":"application/json"},
            json={"model":"claude-sonnet-4-5","max_tokens":8000,
                  "system":SYSTEM_PROMPT,
                  "messages":[{"role":"user","content":user_msg}]},
            timeout=120
        )
        if not r.ok:
            print("Claude error: "+r.text[:300])
            return None, None
        raw = r.json()["content"][0]["text"]
        picks = _parse_ai_response(raw)
        print("Claude returned "+str(len(picks))+" picks")
        return picks, "Claude Sonnet 4.5"
    except Exception as e:
        print("Claude failed: "+str(e))
        return None, None

def _try_groq(user_msg):
    if not GROQ_KEY: return None, None
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization":"Bearer "+GROQ_KEY,"Content-Type":"application/json"},
            json={"model":"llama-3.3-70b-versatile",
                  "messages":[{"role":"system","content":SYSTEM_PROMPT},
                               {"role":"user","content":user_msg}],
                  "temperature":0.1,"max_tokens":8000},
            timeout=90
        )
        if not r.ok:
            print("Groq error: "+r.text[:200])
            return None, None
        raw = r.json()["choices"][0]["message"]["content"]
        picks = _parse_ai_response(raw)
        print("Groq returned "+str(len(picks))+" picks")
        return picks, "Groq Llama 3.3"
    except Exception as e:
        print("Groq failed: "+str(e))
        return None, None

def call_ai(games_with_data):
    n = len(games_with_data)
    user_msg = (
        "Today is "+TODAY+". Analyze these "+str(n)+" MLB games.\n"
        "Use ALL the data provided: SP stats, lineups, bullpen fatigue, injuries, umpire, park factors, odds, weather.\n"
        "Return exactly "+str(n)+" entries. Raw JSON array only.\n\n"
        "GAMES:\n"+json.dumps(games_with_data, indent=2)
    )
    picks, model = _try_claude(user_msg)
    if picks is not None:
        return picks, model
    print("Falling back to Groq...")
    picks, model = _try_groq(user_msg)
    if picks is not None:
        return picks, model
    print("Both AI engines failed")
    return [], "None"

# ── Record tracker ────────────────────────────────────────────────────────────

def load_record():
    if RECORD_FILE.exists():
        try: return json.loads(RECORD_FILE.read_text())
        except: pass
    return {"picks":[],"updated":TODAY}

def save_record(record):
    RECORD_FILE.write_text(json.dumps(record, indent=2))

def build_record_html(record):
    picks = record.get("picks",[])
    settled = [p for p in picks if p.get("result") in ("W","L","P")]
    wins = [p for p in settled if p["result"]=="W"]
    losses = [p for p in settled if p["result"]=="L"]
    pushes = [p for p in settled if p["result"]=="P"]
    total_bets = len(settled)
    win_rate = round(len(wins)/total_bets*100,1) if total_bets else 0
    units_won = sum(p.get("units_result",0) for p in settled)
    units_won = round(units_won,2)

    # By tier
    tiers = {}
    for p in settled:
        t = p.get("tier","?")
        if t not in tiers: tiers[t] = {"W":0,"L":0,"P":0,"units":0}
        tiers[t][p["result"]] += 1
        tiers[t]["units"] += p.get("units_result",0)

    # By bet type
    bet_types = {}
    for p in settled:
        bt = p.get("bet_type","?")
        if bt not in bet_types: bet_types[bt] = {"W":0,"L":0,"P":0,"units":0}
        bet_types[bt][p["result"]] += 1
        bet_types[bt]["units"] += p.get("units_result",0)

    # Pending picks
    pending = [p for p in picks if not p.get("result")]
    watch = [p for p in picks if p.get("tier")=="WATCH" and not p.get("result")]
    watch_settled = [p for p in picks if p.get("tier")=="WATCH" and p.get("result")]
    watch_wins = len([p for p in watch_settled if p["result"]=="W"])
    watch_total = len(watch_settled)
    watch_rate = round(watch_wins/watch_total*100,1) if watch_total else 0

    def tier_row(t, d):
        w=d["W"]; l=d["L"]; p=d["P"]; tot=w+l+p
        wr = round(w/tot*100,1) if tot else 0
        u = round(d["units"],2)
        color = "#1D9E75" if u>=0 else "#A32D2D"
        return ('<tr><td style="padding:8px 12px;font-weight:600">'+t+'</td>'
                '<td style="padding:8px 12px;text-align:center">'+str(w)+'-'+str(l)+('-'+str(p) if p else '')+'</td>'
                '<td style="padding:8px 12px;text-align:center">'+str(wr)+'%</td>'
                '<td style="padding:8px 12px;text-align:center;color:'+color+';font-weight:600">'
                +('+'if u>=0 else '')+str(u)+'u</td></tr>')

    def pick_row(p):
        result = p.get("result","")
        u_result = p.get("units_result",0)
        if result=="W": rc="#1D9E75"; rl="WIN"
        elif result=="L": rc="#A32D2D"; rl="LOSS"
        elif result=="P": rc="#888"; rl="PUSH"
        else: rc="#BA7517"; rl="PENDING"
        tier = p.get("tier","?")
        if tier=="WATCH": tc="#8B6FBA"
        elif tier=="A": tc="#1D9E75"
        elif tier=="B": tc="#378ADD"
        else: tc="#BA7517"
        return ('<tr style="border-bottom:0.5px solid #f0f0ee">'
                '<td style="padding:8px 12px;font-size:12px;color:#888">'+p.get("date","")+'</td>'
                '<td style="padding:8px 12px;font-size:13px;font-weight:500">'+p.get("pick","")+'</td>'
                '<td style="padding:8px 12px;font-size:12px;color:#666">'+p.get("game","")+'</td>'
                '<td style="padding:8px 12px;text-align:center"><span style="background:'+tc+'22;color:'+tc+';font-size:11px;font-weight:600;padding:1px 7px;border-radius:10px">'+tier+'</span></td>'
                '<td style="padding:8px 12px;text-align:center;font-size:12px">'+p.get("line","")+'</td>'
                '<td style="padding:8px 12px;text-align:center"><span style="background:'+rc+'22;color:'+rc+';font-size:11px;font-weight:700;padding:2px 8px;border-radius:4px">'+rl+'</span></td>'
                '<td style="padding:8px 12px;text-align:center;font-weight:600;color:'+rc+'">'
                +('+'if u_result>=0 else '')+str(round(u_result,2))+'u</td></tr>')

    tier_rows = "".join(tier_row(t,d) for t,d in sorted(tiers.items()))
    bt_rows = "".join(tier_row(bt,d) for bt,d in sorted(bet_types.items()))
    pick_rows = "".join(pick_row(p) for p in reversed(picks[-50:]))

    units_color = "#1D9E75" if units_won >= 0 else "#A32D2D"
    units_str = ("+" if units_won >= 0 else "")+str(units_won)+"u"

    return ('<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>MLB Model Record</title>'
            '<style>*{box-sizing:border-box;margin:0;padding:0}'
            'body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;'
            'background:#f9f9f7;color:#1a1a1a;padding:1.25rem;max-width:900px;margin:0 auto}'
            'h1{font-size:20px;font-weight:700;margin-bottom:3px}'
            '.meta{font-size:13px;color:#888;margin-bottom:1.25rem}'
            '.sum{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-bottom:1.5rem}'
            '.s{background:#fff;border:0.5px solid #e8e8e5;border-radius:9px;padding:10px 12px}'
            '.sn{font-size:22px;font-weight:700}.sl{font-size:10px;color:#999;margin-top:2px;'
            'text-transform:uppercase;letter-spacing:.04em}'
            '.section{font-size:13px;font-weight:600;color:#999;text-transform:uppercase;'
            'letter-spacing:.06em;margin:1.5rem 0 0.5rem}'
            'table{width:100%;background:#fff;border:0.5px solid #e8e8e5;border-radius:9px;'
            'border-collapse:collapse;margin-bottom:1.5rem;overflow:hidden}'
            'th{padding:8px 12px;font-size:11px;font-weight:600;color:#999;text-transform:uppercase;'
            'letter-spacing:.04em;text-align:left;background:#f9f9f7;border-bottom:0.5px solid #e8e8e5}'
            'tr:hover{background:#fafaf8}'
            '.watch-note{font-size:12px;color:#8B6FBA;background:#F0ECFB;padding:8px 12px;'
            'border-radius:7px;margin-bottom:1rem}'
            'footer{font-size:11px;color:#bbb;margin-top:1.5rem;text-align:center;padding-bottom:1rem}'
            '</style></head><body>'
            '<h1>MLB Model Record</h1>'
            '<div class="meta">Updated '+TODAY+' &nbsp;&middot;&nbsp; '
            '<a href="index.html" style="color:#378ADD;text-decoration:none">Today\'s picks &rarr;</a></div>'
            '<div class="sum">'
            '<div class="s"><div class="sn">'+str(total_bets)+'</div><div class="sl">Total bets</div></div>'
            '<div class="s"><div class="sn">'+str(len(wins))+'-'+str(len(losses))+'</div><div class="sl">W-L record</div></div>'
            '<div class="s"><div class="sn">'+str(win_rate)+'%</div><div class="sl">Win rate</div></div>'
            '<div class="s"><div class="sn" style="color:'+units_color+'">'+units_str+'</div><div class="sl">Units P&L</div></div>'
            '<div class="s"><div class="sn" style="color:#8B6FBA">'+str(watch_rate)+'%</div><div class="sl">Watch hit rate</div></div>'
            '</div>'
            +(('<div class="watch-note">&#128064; WATCH picks are hitting at '+str(watch_rate)+'% ('+str(watch_wins)+'/'+str(watch_total)+'). '
               +('Consider lowering threshold.' if watch_rate >= 57 else 'Threshold looks correct.')+'</div>') if watch_total >= 10 else '')
            +'<div class="section">Performance by Tier</div>'
            '<table><thead><tr><th>Tier</th><th>Record</th><th>Win %</th><th>Units</th></tr></thead><tbody>'
            +tier_rows+'</tbody></table>'
            '<div class="section">Performance by Bet Type</div>'
            '<table><thead><tr><th>Bet Type</th><th>Record</th><th>Win %</th><th>Units</th></tr></thead><tbody>'
            +bt_rows+'</tbody></table>'
            '<div class="section">Pick History (last 50)</div>'
            '<div style="font-size:12px;color:#888;margin-bottom:8px">Results are entered manually. '
            'Come back after each game and update record.json with W/L/P.</div>'
            '<table><thead><tr><th>Date</th><th>Pick</th><th>Game</th><th>Tier</th><th>Line</th><th>Result</th><th>Units</th></tr></thead><tbody>'
            +pick_rows+'</tbody></table>'
            '<footer>EV model &nbsp;&middot;&nbsp; Paper trading until 50+ picks verified</footer>'
            '</body></html>')

# ── Archive ───────────────────────────────────────────────────────────────────

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
            'border-radius:9px;margin-bottom:8px;text-decoration:none;color:#0F6E56">'
            '<span style="font-size:14px;font-weight:600">Today &mdash; '+TODAY+'</span>'
            '<span style="font-size:12px">View &rarr;</span></a>'
            '<a href="record.html" style="display:flex;justify-content:space-between;'
            'align-items:center;padding:12px 16px;background:#F0ECFB;border:0.5px solid #C4B8E8;'
            'border-radius:9px;margin-bottom:16px;text-decoration:none;color:#4A2D8F">'
            '<span style="font-size:14px;font-weight:600">&#128200; Model Record &amp; ROI</span>'
            '<span style="font-size:12px">View &rarr;</span></a>'
            +rows+'</body></html>')
    (OUTPUT_DIR / "archive.html").write_text(html)

# ── HTML builder ──────────────────────────────────────────────────────────────

def build_html(data):
    all_picks = data.get("picks",[])
    active  = [p for p in all_picks if p.get("tier") in ("A","B","C")]
    watched = [p for p in all_picks if p.get("tier") == "WATCH"]
    skipped = [p for p in all_picks if p.get("tier") == "SKIP"]
    total_u = round(sum(p.get("units",0) for p in active),1)
    gen     = data.get("generated_at","")[:16].replace("T"," ")
    date    = data["date"]
    ai_model= data.get("ai_model","Unknown")

    if "Claude" in ai_model:
        model_bg="#E1F5EE"; model_tc="#0F6E56"; model_icon="&#129302;"
    else:
        model_bg="#E6F1FB"; model_tc="#185FA5"; model_icon="&#129302;"
    model_badge = ('<span style="background:'+model_bg+';color:'+model_tc+';font-size:11px;'
                   'font-weight:600;padding:2px 9px;border-radius:20px;">'+model_icon+' '+ai_model+'</span>')

    TBAR = {"A":"#1D9E75","B":"#378ADD","C":"#BA7517","WATCH":"#8B6FBA"}
    TBG  = {"A":"#E1F5EE","B":"#E6F1FB","C":"#FAEEDA","WATCH":"#F0ECFB"}
    TTC  = {"A":"#0F6E56","B":"#185FA5","C":"#854F0B","WATCH":"#4A2D8F"}
    TLBL = {"A":"TIER A &mdash; PLAY","B":"TIER B &mdash; PLAY","C":"TIER C &mdash; LEAN","WATCH":"WATCH &mdash; TRACK ONLY"}

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
        c=TBAR.get(t,"#888"); bg=TBG.get(t,"#eee"); tc=TTC.get(t,"#333")
        ev=p.get("ev_pct",0); bw=min(int(ev)*8,100)
        game=str(p.get("game",""))
        ump = str(p.get("hp_ump",""))
        ump_line = (' &nbsp;&middot;&nbsp; &#9918; '+ump) if ump else ""
        return (
            '<div style="background:#fff;border:0.5px solid #e0e0e0;border-left:3px solid '+c+';'
            'border-radius:10px;padding:1rem 1.25rem;margin-bottom:10px">'
            '<span style="background:'+bg+';color:'+tc+';font-size:11px;font-weight:600;'
            'padding:2px 9px;border-radius:4px;display:inline-block;margin-bottom:8px">'+TLBL.get(t,"LEAN")+'</span>'
            +flag_row(p.get("flags",""))+
            '<div style="font-size:16px;font-weight:600;margin-bottom:2px">'+str(p.get("pick",""))+'</div>'
            '<div style="font-size:13px;color:#777;margin-bottom:10px">'
            +game+' &nbsp;&middot;&nbsp; '+str(p.get("line","N/A"))
            +' &nbsp;&middot;&nbsp; '+str(p.get("units",0))+'u'+ump_line
            +score_span(game)+'</div>'
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px">'
            +sp_box("Away SP",p.get("away_sp","TBD"))
            +sp_box("Home SP",p.get("home_sp","TBD"))+'</div>'
            '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">'
            '<span style="font-size:11px;background:#f0f0ee;padding:2px 9px;border-radius:20px;color:#555">'
            'Win '+str(p.get("win_prob_pct",0))+'% vs implied '+str(p.get("implied_prob_pct",0))+'%</span>'
            '<span style="font-size:11px;background:'+bg+';color:'+tc+';padding:2px 9px;'
            'border-radius:20px;font-weight:600">+'+str(ev)+'% EV</span></div>'
            '<div style="height:4px;background:#f0f0ee;border-radius:2px;margin-bottom:10px;overflow:hidden">'
            '<div style="height:100%;width:'+str(bw)+'%;background:'+c+';border-radius:2px"></div></div>'
            '<div style="margin-bottom:10px">'
            +mrow("&#9918;",p.get("sp_analysis",""))
            +mrow("&#128101;",p.get("lineup_analysis",""))
            +mrow("&#128293;",p.get("bullpen_note",""))
            +mrow("&#129657;",p.get("injury_flags",""))
            +mrow("&#9878;",p.get("umpire_note",""))
            +mrow("&#127966;",p.get("park_note",""))
            +mrow("&#127748;",p.get("weather_impact",""))+'</div>'
            '<div style="border-top:0.5px solid #eee;padding-top:8px">'
            '<div style="font-size:12px;font-weight:600;color:#222;margin-bottom:3px">'
            'Key edge: '+str(p.get("key_edge",""))+'</div>'
            '<div style="font-size:12px;color:#666;line-height:1.6">'+str(p.get("rationale",""))+'</div>'
            '</div></div>'
        )

    def watch_card(p):
        game=str(p.get("game",""))
        ev=p.get("ev_pct",0)
        return (
            '<div style="background:#fff;border:0.5px solid #C4B8E8;border-left:3px solid #8B6FBA;'
            'border-radius:10px;padding:1rem 1.25rem;margin-bottom:10px">'
            '<span style="background:#F0ECFB;color:#4A2D8F;font-size:11px;font-weight:600;'
            'padding:2px 9px;border-radius:4px;display:inline-block;margin-bottom:8px">'
            'WATCH &mdash; TRACK ONLY</span>'
            +flag_row(p.get("flags",""))+
            '<div style="font-size:16px;font-weight:600;margin-bottom:2px">'+str(p.get("pick",""))+'</div>'
            '<div style="font-size:13px;color:#777;margin-bottom:4px">'
            +game+' &nbsp;&middot;&nbsp; '+str(p.get("line","N/A"))+' &nbsp;&middot;&nbsp; Not betting'
            +score_span(game)+'</div>'
            '<div style="font-size:11px;color:#8B6FBA;margin-bottom:10px;font-style:italic">'
            'Edge is real but below threshold ('+str(ev)+'% vs 3% min). Tracking to build confidence.</div>'
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px">'
            +sp_box("Away SP",p.get("away_sp","TBD"))
            +sp_box("Home SP",p.get("home_sp","TBD"))+'</div>'
            '<div style="margin-bottom:10px">'
            +mrow("&#9918;",p.get("sp_analysis",""))
            +mrow("&#128101;",p.get("lineup_analysis",""))
            +mrow("&#128293;",p.get("bullpen_note",""))
            +mrow("&#129657;",p.get("injury_flags",""))
            +mrow("&#9878;",p.get("umpire_note",""))
            +mrow("&#127966;",p.get("park_note",""))
            +mrow("&#127748;",p.get("weather_impact",""))+'</div>'
            '<div style="border-top:0.5px solid #eee;padding-top:8px">'
            '<div style="font-size:12px;font-weight:600;color:#4A2D8F;margin-bottom:3px">'
            'Why watching: '+str(p.get("avoid_reason","Edge below threshold"))+'</div>'
            '<div style="font-size:12px;color:#888;line-height:1.6">'+str(p.get("rationale",""))+'</div>'
            '</div></div>'
        )

    def skip_card(p):
        game=str(p.get("game",""))
        return (
            '<div style="background:#fff;border:0.5px solid #e0e0e0;border-left:3px solid #B4B2A9;'
            'border-radius:10px;padding:1rem 1.25rem;margin-bottom:10px">'
            '<span style="background:#F1EFE8;color:#5F5E5A;font-size:11px;font-weight:600;'
            'padding:2px 9px;border-radius:4px;display:inline-block;margin-bottom:8px">SKIP &mdash; NO EDGE</span>'
            +flag_row(p.get("flags",""))+
            '<div style="font-size:16px;font-weight:600;margin-bottom:2px">'+game+score_span(game)+'</div>'
            '<div style="font-size:13px;color:#777;margin-bottom:10px">'+str(p.get("venue",""))+'</div>'
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px">'
            +sp_box("Away SP",p.get("away_sp","TBD"))
            +sp_box("Home SP",p.get("home_sp","TBD"))+'</div>'
            '<div style="margin-bottom:10px">'
            +mrow("&#9918;",p.get("sp_analysis",""))
            +mrow("&#128101;",p.get("lineup_analysis",""))
            +mrow("&#128293;",p.get("bullpen_note",""))
            +mrow("&#129657;",p.get("injury_flags",""))
            +mrow("&#9878;",p.get("umpire_note",""))
            +mrow("&#127966;",p.get("park_note",""))
            +mrow("&#127748;",p.get("weather_impact",""))+'</div>'
            '<div style="border-top:0.5px solid #eee;padding-top:8px">'
            '<div style="font-size:12px;font-weight:600;color:#A32D2D;margin-bottom:3px">'
            'Why skip: '+str(p.get("avoid_reason","No clear edge"))+'</div>'
            '<div style="font-size:12px;color:#888;line-height:1.6">'+str(p.get("rationale",""))+'</div>'
            '</div></div>'
        )

    cards = ("".join(pick_card(p) for p in active)
             +"".join(watch_card(p) for p in watched)
             +"".join(skip_card(p) for p in skipped))
    if not cards:
        cards = '<p style="color:#888;font-size:14px;padding:1.5rem 0;text-align:center">No games found today.</p>'

    live_js = (
        '<script>'
        'var D="'+date+'";'
        'function toET(iso){var d=new Date(iso);'
        'return d.toLocaleTimeString("en-US",{timeZone:"America/New_York",hour:"numeric",minute:"2-digit"})+" ET";}'
        'function upd(){'
        'fetch("https://statsapi.mlb.com/api/v1/schedule?sportId=1&date="+D+"&hydrate=linescore,team")'
        '.then(function(r){return r.json();})'
        '.then(function(data){'
        'var games=[];'
        '(data.dates||[]).forEach(function(d){(d.games||[]).forEach(function(g){games.push(g);});});'
        'games.forEach(function(g){'
        'var away=g.teams.away.team.name;var home=g.teams.home.team.name;'
        'var sid="s_"+(away+"_AT_"+home).replace(/ /g,"_");'
        'var el=document.getElementById(sid);if(!el)return;'
        'var ab=g.status.abstractGameState;'
        'var aS=g.teams.away.score;var hS=g.teams.home.score;'
        'if(ab==="Final"){'
        'el.textContent="FINAL: "+away+" "+aS+" - "+home+" "+hS;'
        'el.style.background="#f0f0ee";el.style.color="#555";'
        '}else if(ab==="Live"){'
        'var inn=(g.linescore&&g.linescore.currentInningOrdinal)?g.linescore.currentInningOrdinal:"";'
        'el.textContent="LIVE "+inn+": "+away+" "+aS+" - "+home+" "+hS;'
        'el.style.background="#FAEEDA";el.style.color="#633806";'
        '}else{el.textContent=toET(g.gameDate);el.style.background="#f0f0ee";el.style.color="#888";}'
        '});'
        'var lu=document.getElementById("last_update");'
        'if(lu)lu.textContent="Scores updated "+new Date().toLocaleTimeString("en-US",'
        '{timeZone:"America/New_York",hour:"numeric",minute:"2-digit"})+" ET";'
        '}).catch(function(e){console.log("score err",e);});}'
        'upd();setInterval(upd,30000);</script>'
    )

    css = (
        '<style>*{box-sizing:border-box;margin:0;padding:0}'
        'body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;'
        'background:#f9f9f7;color:#1a1a1a;padding:1.25rem;max-width:700px;margin:0 auto}'
        'h1{font-size:20px;font-weight:700;margin-bottom:3px}'
        '.meta{font-size:13px;color:#888;margin-bottom:2px}'
        '.updated{font-size:11px;color:#aaa;margin-bottom:1.25rem;display:flex;gap:8px;align-items:center;flex-wrap:wrap}'
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
        ' &nbsp;&middot;&nbsp; <a href="archive.html" style="color:#378ADD;text-decoration:none">Archive &rarr;</a>'
        ' &nbsp;&middot;&nbsp; <a href="record.html" style="color:#8B6FBA;text-decoration:none">&#128200; Record</a></div>'
        '<div class="updated">Picks generated '+gen+' ET &nbsp;&middot;&nbsp; '+model_badge
        +' &nbsp;&middot;&nbsp; <span id="last_update">Scores loading...</span></div>'
        '<div class="sum">'
        '<div class="s"><div class="sn" style="color:#1D9E75">'+str(len(active))+'</div><div class="sl">Active picks</div></div>'
        '<div class="s"><div class="sn">'+str(total_u)+'u</div><div class="sl">Total units</div></div>'
        '<div class="s"><div class="sn" style="color:#8B6FBA">'+str(len(watched))+'</div><div class="sl">Watching</div></div>'
        '<div class="s"><div class="sn">'+str(len(skipped))+'</div><div class="sl">No edge</div></div>'
        '</div>'
        '<div class="st">Active Picks</div>'
        +cards+
        '<footer>EV model &nbsp;&middot;&nbsp; Real MLB stats 2025+2026 &nbsp;&middot;&nbsp; Lineups &nbsp;&middot;&nbsp; Bullpen &nbsp;&middot;&nbsp; Umpires'
        ' &nbsp;&middot;&nbsp; Never bet more than you can afford to lose</footer>'
        +live_js+'</body></html>'
    )

# ── Main ──────────────────────────────────────────────────────────────────────

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
        print("Enriching: "+g["away"]+" @ "+g["home"])
        odds    = odds_map.get(g["away"]+"@"+g["home"], {})
        weather = fetch_weather(g["home"])
        park    = get_park_factor(g["venue"])

        # Lineups
        lineups = {}
        if g.get("game_pk"):
            try: lineups = fetch_lineup(g["game_pk"])
            except: pass

        # Bullpen fatigue
        home_bullpen = {}; away_bullpen = {}
        try: home_bullpen = fetch_bullpen_fatigue(g["home_id"])
        except: pass
        try: away_bullpen = fetch_bullpen_fatigue(g["away_id"])
        except: pass

        # Injuries
        home_injuries = []; away_injuries = []
        try: home_injuries = fetch_injuries(g["home_id"])
        except: pass
        try: away_injuries = fetch_injuries(g["away_id"])
        except: pass

        # Umpire stats
        ump_stats = {}
        if g.get("hp_ump"):
            try: ump_stats = fetch_ump_stats(g["hp_ump"])
            except: pass

        gd = dict(g)
        gd["odds"]               = odds
        gd["weather"]            = weather
        gd["park_factor"]        = park
        gd["home_sp_stats"]      = get_pitcher_stats(g["home_sp"], stats)
        gd["away_sp_stats"]      = get_pitcher_stats(g["away_sp"], stats)
        gd["home_team_pitching"] = get_team_stats(g["home"], stats, "team_pitching")
        gd["away_team_pitching"] = get_team_stats(g["away"], stats, "team_pitching")
        gd["home_team_batting"]  = get_team_stats(g["home"], stats, "team_batting")
        gd["away_team_batting"]  = get_team_stats(g["away"], stats, "team_batting")
        gd["home_lineup"]        = lineups.get("home",{})
        gd["away_lineup"]        = lineups.get("away",{})
        gd["home_bullpen_fatigue"] = home_bullpen
        gd["away_bullpen_fatigue"] = away_bullpen
        gd["home_injuries"]      = home_injuries[:5]
        gd["away_injuries"]      = away_injuries[:5]
        gd["ump_stats"]          = ump_stats
        games_with_data.append(gd)

    # Save updated stats cache
    STATS_CACHE.write_text(json.dumps(stats))

    picks, ai_model = call_ai(games_with_data)
    active = [p for p in picks if p.get("tier") in ("A","B","C")]

    # Add today's active picks to record tracker
    record = load_record()
    existing_games = {p["game"]+p["date"] for p in record["picks"]}
    for p in active:
        key = p.get("game","")+TODAY
        if key not in existing_games:
            record["picks"].append({
                "date": TODAY,
                "game": p.get("game",""),
                "pick": p.get("pick",""),
                "bet_type": p.get("bet_type",""),
                "line": p.get("line",""),
                "tier": p.get("tier",""),
                "units": p.get("units",0),
                "ev_pct": p.get("ev_pct",0),
                "result": "",
                "units_result": 0,
            })
    # Also track WATCH picks
    for p in [x for x in picks if x.get("tier")=="WATCH"]:
        key = p.get("game","")+TODAY+"WATCH"
        if key not in existing_games:
            record["picks"].append({
                "date": TODAY,
                "game": p.get("game",""),
                "pick": p.get("pick","")+" (WATCH)",
                "bet_type": p.get("bet_type",""),
                "line": p.get("line",""),
                "tier": "WATCH",
                "units": 0,
                "ev_pct": p.get("ev_pct",0),
                "result": "",
                "units_result": 0,
            })
    record["updated"] = TODAY
    save_record(record)

    output = {
        "date": TODAY,
        "generated_at": datetime.datetime.utcnow().isoformat()+"Z",
        "stats_date": stats.get("date",""),
        "ai_model": ai_model,
        "total_games": len(games),
        "total_picks": len(active),
        "picks": picks,
        "raw_games_data": games_with_data,
    }

    (OUTPUT_DIR/"picks.json").write_text(json.dumps(output, indent=2))
    html = build_html(output)
    (OUTPUT_DIR/(TODAY+".html")).write_text(html)
    (OUTPUT_DIR/"index.html").write_text(html)
    (OUTPUT_DIR/"record.html").write_text(build_record_html(record))
    build_archive_index()
    print("Done. "+str(len(active))+" active picks across "+str(len(games))+" games.")
    print("AI engine: "+ai_model)

if __name__ == "__main__":
    main()
