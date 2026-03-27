"""
MLB Betting Model — Daily Picks Generator
Full feature set:
- Real 2025+2026 SP stats via player ID lookup
- Pitcher last 3 starts (recent form)
- Home/away splits for pitchers and teams
- Starting lineups from MLB Stats API
- Bullpen fatigue (last 3 days usage)
- Injury reports from MLB Stats API
- Home plate umpire with tendencies
- Dynamic park factors
- Stadium wind orientation (actual in/out calculation)
- Multiple bookmaker odds scanning
- Historical record tracker with CLV tracking
- Hard EV threshold enforcement (Python-level, not just prompt)
- Claude primary, Groq fallback
- Live scores every 30s in browser
"""

import os, json, datetime, math, requests
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

# ── Stadium data: coordinates + outfield facing direction (degrees from N) ────
# Wind blowing FROM this direction = blowing OUT (toward OF)
# Wind blowing TO this direction = blowing IN (from OF)
STADIUMS = {
    "New York Mets":         {"lat":40.7571,"lon":-73.8458,"of_facing":  5},
    "New York Yankees":      {"lat":40.8296,"lon":-73.9262,"of_facing":330},
    "Boston Red Sox":        {"lat":42.3467,"lon":-71.0972,"of_facing":100},
    "Tampa Bay Rays":        {"lat":27.7683,"lon":-82.6534,"of_facing":  0,"dome":True},
    "Baltimore Orioles":     {"lat":39.2838,"lon":-76.6218,"of_facing": 60},
    "Toronto Blue Jays":     {"lat":43.6414,"lon":-79.3894,"of_facing":  0,"dome":True},
    "Chicago White Sox":     {"lat":41.8300,"lon":-87.6338,"of_facing":  5},
    "Chicago Cubs":          {"lat":41.9484,"lon":-87.6553,"of_facing":350},
    "Milwaukee Brewers":     {"lat":43.0280,"lon":-87.9712,"of_facing":  0,"dome":True},
    "Minnesota Twins":       {"lat":44.9817,"lon":-93.2775,"of_facing":  0,"dome":True},
    "Cleveland Guardians":   {"lat":41.4962,"lon":-81.6852,"of_facing":345},
    "Detroit Tigers":        {"lat":42.3390,"lon":-83.0485,"of_facing":  5},
    "Kansas City Royals":    {"lat":39.0517,"lon":-94.4803,"of_facing": 10},
    "Houston Astros":        {"lat":29.7572,"lon":-95.3555,"of_facing":  0,"dome":True},
    "Texas Rangers":         {"lat":32.7513,"lon":-97.0832,"of_facing":  0,"dome":True},
    "Los Angeles Angels":    {"lat":33.8003,"lon":-117.8827,"of_facing":340},
    "Oakland Athletics":     {"lat":37.7516,"lon":-122.2005,"of_facing": 60},
    "Seattle Mariners":      {"lat":47.5914,"lon":-122.3325,"of_facing":  5},
    "Los Angeles Dodgers":   {"lat":34.0739,"lon":-118.2400,"of_facing":  0},
    "San Francisco Giants":  {"lat":37.7786,"lon":-122.3893,"of_facing":100},
    "San Diego Padres":      {"lat":32.7076,"lon":-117.1570,"of_facing":330},
    "Arizona Diamondbacks":  {"lat":33.4453,"lon":-112.0667,"of_facing":  0,"dome":True},
    "Colorado Rockies":      {"lat":39.7559,"lon":-104.9942,"of_facing":340},
    "Atlanta Braves":        {"lat":33.8908,"lon":-84.4678,"of_facing":  5},
    "Miami Marlins":         {"lat":25.7781,"lon":-80.2197,"of_facing":  0,"dome":True},
    "Philadelphia Phillies": {"lat":39.9061,"lon":-75.1665,"of_facing":  5},
    "Washington Nationals":  {"lat":38.8730,"lon":-77.0074,"of_facing":  5},
    "Pittsburgh Pirates":    {"lat":40.4469,"lon":-80.0058,"of_facing":315},
    "Cincinnati Reds":       {"lat":39.0979,"lon":-84.5082,"of_facing":  5},
    "St. Louis Cardinals":   {"lat":38.6226,"lon":-90.1928,"of_facing":  5},
}

TEAM_NAME_MAP = {
    "Los Angeles Angels of Anaheim": "Los Angeles Angels",
    "Athletics": "Oakland Athletics",
}

PARK_FACTORS = {
    "Coors Field":               {"runs":1.35,"hr":1.30,"note":"Extreme hitter park"},
    "Great American Ball Park":  {"runs":1.12,"hr":1.18,"note":"Hitter friendly"},
    "Globe Life Field":          {"runs":1.08,"hr":1.05,"note":"Slight hitter lean, dome"},
    "Truist Park":               {"runs":1.02,"hr":1.04,"note":"Neutral"},
    "Yankee Stadium":            {"runs":1.03,"hr":1.15,"note":"HR-friendly"},
    "Fenway Park":               {"runs":1.05,"hr":0.98,"note":"Neutral runs, quirky dims"},
    "Wrigley Field":             {"runs":1.04,"hr":1.02,"note":"Wind-dependent"},
    "Petco Park":                {"runs":0.88,"hr":0.82,"note":"Elite pitcher park"},
    "Oracle Park":               {"runs":0.90,"hr":0.78,"note":"Pitcher friendly, cold air"},
    "T-Mobile Park":             {"runs":0.93,"hr":0.91,"note":"Pitcher friendly"},
    "loanDepot park":            {"runs":0.96,"hr":0.92,"note":"Slight pitcher lean, dome"},
    "Daikin Park":               {"runs":0.98,"hr":1.02,"note":"Neutral"},
    "Rogers Centre":             {"runs":1.01,"hr":1.05,"note":"Dome, neutral"},
    "American Family Field":     {"runs":1.03,"hr":1.06,"note":"Slight hitter lean, dome"},
    "Target Field":              {"runs":0.97,"hr":0.94,"note":"Neutral to pitcher"},
    "Progressive Field":         {"runs":0.96,"hr":0.88,"note":"Pitcher friendly"},
    "Comerica Park":             {"runs":0.94,"hr":0.85,"note":"Pitcher friendly"},
    "Kauffman Stadium":          {"runs":0.97,"hr":0.90,"note":"Pitcher friendly"},
    "Minute Maid Park":          {"runs":1.01,"hr":1.03,"note":"Dome, neutral"},
    "Angel Stadium":             {"runs":0.96,"hr":0.93,"note":"Pitcher lean"},
    "Oakland Coliseum":          {"runs":0.93,"hr":0.86,"note":"Pitcher friendly"},
    "Dodger Stadium":            {"runs":0.97,"hr":0.95,"note":"Pitcher lean"},
    "Chase Field":               {"runs":1.02,"hr":1.04,"note":"Dome, neutral"},
    "Busch Stadium":             {"runs":0.97,"hr":0.92,"note":"Pitcher lean"},
    "PNC Park":                  {"runs":0.96,"hr":0.91,"note":"Pitcher friendly"},
    "Nationals Park":            {"runs":0.98,"hr":0.97,"note":"Neutral"},
    "Citizens Bank Park":        {"runs":1.06,"hr":1.12,"note":"Hitter friendly"},
    "Citi Field":                {"runs":0.95,"hr":0.89,"note":"Pitcher lean"},
    "Guaranteed Rate Field":     {"runs":1.00,"hr":1.08,"note":"Neutral"},
    "UNIQLO Field at Dodger Stadium": {"runs":0.97,"hr":0.95,"note":"Pitcher lean"},
}

BOOK_PRIORITY = ["draftkings","fanduel","betmgm","caesars","williamhill_us","betonlineag","bovada"]

UMP_DATA = {
    "Angel Hernandez":  {"rpg":9.2,"k_pct":0.21,"bb_pct":0.09,"note":"High run ump"},
    "CB Bucknor":       {"rpg":9.4,"k_pct":0.20,"bb_pct":0.10,"note":"High run ump"},
    "Doug Eddings":     {"rpg":8.8,"k_pct":0.23,"bb_pct":0.08,"note":"Neutral"},
    "Lance Barrett":    {"rpg":8.5,"k_pct":0.24,"bb_pct":0.08,"note":"Pitcher friendly"},
    "Will Little":      {"rpg":8.4,"k_pct":0.24,"bb_pct":0.07,"note":"Pitcher friendly"},
    "John Tumpane":     {"rpg":9.1,"k_pct":0.22,"bb_pct":0.09,"note":"Slight over lean"},
    "Chad Fairchild":   {"rpg":8.9,"k_pct":0.23,"bb_pct":0.08,"note":"Neutral"},
    "Marvin Hudson":    {"rpg":8.7,"k_pct":0.23,"bb_pct":0.08,"note":"Neutral"},
    "Jordan Baker":     {"rpg":9.0,"k_pct":0.22,"bb_pct":0.09,"note":"Neutral"},
    "Cory Blaser":      {"rpg":8.6,"k_pct":0.24,"bb_pct":0.07,"note":"Pitcher friendly"},
    "Stu Scheurwater":  {"rpg":9.3,"k_pct":0.21,"bb_pct":0.09,"note":"Over lean"},
    "Dan Bellino":      {"rpg":8.3,"k_pct":0.25,"bb_pct":0.07,"note":"Strong pitcher friendly"},
    "Vic Carapazza":    {"rpg":9.5,"k_pct":0.20,"bb_pct":0.10,"note":"Strong over lean"},
    "Pat Hoberg":       {"rpg":8.4,"k_pct":0.24,"bb_pct":0.07,"note":"Pitcher friendly"},
    "James Hoye":       {"rpg":9.0,"k_pct":0.22,"bb_pct":0.09,"note":"Neutral"},
    "Mark Carlson":     {"rpg":9.2,"k_pct":0.21,"bb_pct":0.09,"note":"Slight over lean"},
    "Mike Estabrook":   {"rpg":8.8,"k_pct":0.23,"bb_pct":0.08,"note":"Neutral"},
    "Jeremie Rehak":    {"rpg":8.5,"k_pct":0.24,"bb_pct":0.07,"note":"Pitcher friendly"},
    "Brian Knight":     {"rpg":8.9,"k_pct":0.22,"bb_pct":0.08,"note":"Neutral"},
    "Ryan Blakney":     {"rpg":9.1,"k_pct":0.22,"bb_pct":0.09,"note":"Slight over lean"},
    "Roberto Ortiz":    {"rpg":9.0,"k_pct":0.22,"bb_pct":0.09,"note":"Neutral"},
    "Todd Tichenor":    {"rpg":9.1,"k_pct":0.22,"bb_pct":0.08,"note":"Neutral"},
    "Gabe Morales":     {"rpg":8.6,"k_pct":0.24,"bb_pct":0.07,"note":"Pitcher friendly"},
    "D.J. Reyburn":     {"rpg":8.8,"k_pct":0.23,"bb_pct":0.08,"note":"Neutral"},
    "Adam Beck":        {"rpg":9.2,"k_pct":0.21,"bb_pct":0.09,"note":"Slight over lean"},
}

# ── Helpers ───────────────────────────────────────────────────────────────────

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

def wind_impact(team_name, wind_dir_str, wind_mph):
    """Calculate whether wind is blowing in or out at this specific stadium."""
    sd = STADIUMS.get(team_name, {})
    if sd.get("dome"):
        return "Dome — weather irrelevant"
    if wind_mph == "N/A" or wind_mph < 5:
        return "Wind minimal (<5 mph)"

    # Convert wind direction string to degrees (wind is FROM this direction)
    dir_map = {"N":0,"NNE":22,"NE":45,"ENE":67,"E":90,"ESE":112,"SE":135,"SSE":157,
               "S":180,"SSW":202,"SW":225,"WSW":247,"W":270,"WNW":292,"NW":315,"NNW":337}
    wind_from_deg = dir_map.get(wind_dir_str.upper(), -1)
    if wind_from_deg < 0:
        return str(wind_mph)+" mph "+wind_dir_str+" — direction unclear"

    of_facing = sd.get("of_facing", -1)
    if of_facing < 0:
        return str(wind_mph)+" mph "+wind_dir_str

    # Angle between wind direction (FROM) and outfield facing
    # Wind blowing FROM same direction as OF faces = blowing IN (toward home plate)
    # Wind blowing FROM opposite direction = blowing OUT (toward OF)
    angle_diff = abs(((wind_from_deg - of_facing + 180) % 360) - 180)

    if angle_diff < 45:
        direction = "blowing IN from CF"
        impact = "suppresses scoring" if wind_mph >= 12 else "slight scoring suppression"
        lean = "UNDER lean" if wind_mph >= 12 else ""
    elif angle_diff > 135:
        direction = "blowing OUT to CF"
        impact = "boosts HR/scoring" if wind_mph >= 12 else "slight scoring boost"
        lean = "OVER lean" if wind_mph >= 12 else ""
    else:
        direction = "crosswind"
        impact = "minimal scoring impact"
        lean = ""

    result = str(wind_mph)+" mph "+wind_dir_str+" — "+direction
    if lean:
        result += " ("+lean+")"
    return result

def get_park_factor(venue):
    if venue in PARK_FACTORS:
        return PARK_FACTORS[venue]
    for k,v in PARK_FACTORS.items():
        if k.lower() in venue.lower() or venue.lower() in k.lower():
            return v
    return {"runs":1.0,"hr":1.0,"note":"No park data — using neutral"}

def get_ump_stats(ump_name):
    if not ump_name:
        return {"name":"TBD","rpg":8.8,"note":"Unknown — using league average"}
    last = ump_name.split()[-1] if ump_name else ""
    for k,v in UMP_DATA.items():
        if last.lower() in k.lower():
            return dict(v, name=ump_name)
    return {"name":ump_name,"rpg":8.8,"k_pct":0.22,"note":"No data — using league average"}

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

def fetch_pitcher_recent_form(pid, season):
    """Fetch last 3 starts for a pitcher — most important for current form."""
    if not pid: return {}
    data = mlb_api("/people/"+str(pid)+"/stats", {
        "stats":"gameLog","season":str(season),"group":"pitching","sportId":"1",
    })
    stats_list = data.get("stats",[])
    if not stats_list: return {}
    splits = stats_list[0].get("splits",[])
    # Filter to starts only, take last 3
    starts = [s for s in splits if int(s.get("stat",{}).get("gamesStarted",0) or 0) > 0]
    last3 = starts[-3:] if len(starts) >= 3 else starts
    if not last3: return {}
    era_last3 = []
    total_ip = 0; total_er = 0; total_so = 0; total_bb = 0
    for s in last3:
        stat = s.get("stat",{})
        ip = safe_float(stat.get("inningsPitched","0"))
        er = int(stat.get("earnedRuns",0) or 0)
        so = int(stat.get("strikeOuts",0) or 0)
        bb = int(stat.get("baseOnBalls",0) or 0)
        total_ip += ip; total_er += er; total_so += so; total_bb += bb
        if ip > 0:
            era_last3.append(round(er/ip*9, 2))
    if total_ip == 0: return {}
    return {
        "starts": len(last3),
        "era_last3": round(total_er/total_ip*9, 2) if total_ip > 0 else 0,
        "k9_last3": round(total_so/total_ip*9, 2) if total_ip > 0 else 0,
        "bb9_last3": round(total_bb/total_ip*9, 2) if total_ip > 0 else 0,
        "ip_per_start": round(total_ip/len(last3), 1),
    }

def fetch_pitcher_splits(pid, season):
    """Fetch home/away and L/R splits for a pitcher."""
    if not pid: return {}
    splits_data = {}
    # Home/away
    data = mlb_api("/people/"+str(pid)+"/stats", {
        "stats":"statSplits","season":str(season),"group":"pitching",
        "sportId":"1","sitCodes":"h,a",
    })
    stats_list = data.get("stats",[])
    if not stats_list: return {}
    for split in stats_list[0].get("splits",[]):
        sit = split.get("split",{}).get("code","")
        stat = split.get("stat",{})
        ip = safe_float(stat.get("inningsPitched","0"))
        so = int(stat.get("strikeOuts",0) or 0)
        if sit == "h":
            splits_data["home_era"] = safe_float(stat.get("era"))
            splits_data["home_k9"] = round(so/ip*9,2) if ip > 0 else 0
        elif sit == "a":
            splits_data["away_era"] = safe_float(stat.get("era"))
            splits_data["away_k9"] = round(so/ip*9,2) if ip > 0 else 0
    return splits_data

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
    stats_list = data.get("stats",[])
    if not stats_list: return {}
    splits = stats_list[0].get("splits",[])
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

def fetch_team_home_away_splits(team_id, season):
    """Fetch home vs away batting splits for a team."""
    result = {}
    for sit_code, label in [("h","home"),("a","away")]:
        data = mlb_api("/teams/"+str(team_id)+"/stats", {
            "stats":"statSplits","season":str(season),"group":"hitting",
            "sportId":"1","sitCodes":sit_code,
        })
        for split in data.get("stats",[{}])[0].get("splits",[]):
            stat = split.get("stat",{})
            g = int(stat.get("gamesPlayed",1) or 1)
            if g < 5: continue
            runs = int(stat.get("runs",0) or 0)
            ops = safe_float(stat.get("ops"))
            if ops > 1.2: continue
            result[label] = {
                "ops":ops,
                "runs_per_game":round(runs/g,2) if g>0 else 0,
                "games":g,
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

def get_pitcher_stats(name, stats, is_home=False):
    """Get full pitcher profile: season stats + recent form + splits."""
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

    # Determine primary stats
    if not s26 or s26.get("gs",0) == 0:
        primary = dict(s25)
        primary["note"] = primary.get("note","") or "2025 only (no 2026 starts yet)"
    else:
        gs26 = s26.get("gs",0)
        if gs26 >= 10:
            primary = dict(s26)
            primary["note"] = "2026 primary ("+str(gs26)+" starts)"
        elif gs26 >= 5:
            primary = {}
            for key in ["era","whip","k9","bb9"]:
                v25=s25.get(key,0); v26=s26.get(key,0)
                primary[key] = round(v26*0.6+v25*0.4,2) if v25 and v26 else (v26 or v25)
            primary["gs_2026"]=gs26; primary["gs_2025"]=s25.get("gs",0)
            primary["player_id"] = s25.get("player_id") or s26.get("player_id")
            primary["note"] = "Blended 60/40 ("+str(gs26)+" 2026 starts)"
        else:
            primary = dict(s25)
            primary["gs_2026"]=gs26; primary["era_2026"]=s26.get("era")
            primary["player_id"] = s25.get("player_id") or s26.get("player_id")
            primary["note"] = primary.get("note","") or "Primarily 2025 ("+str(gs26)+" 2026 starts)"

    # Add recent form (last 3 starts) — most predictive of current performance
    pid = primary.get("player_id") or s25.get("player_id") or s26.get("player_id")
    if pid:
        recent = fetch_pitcher_recent_form(pid, 2026)
        if not recent:
            recent = fetch_pitcher_recent_form(pid, 2025)
        if recent:
            primary["recent_form"] = recent
            # Flag if recent ERA diverges significantly from season ERA
            season_era = primary.get("era", 0)
            recent_era = recent.get("era_last3", 0)
            if season_era > 0 and recent_era > 0:
                diff = recent_era - season_era
                if diff > 1.5:
                    primary["form_flag"] = "DECLINING — last 3 starts ERA "+str(recent_era)+" vs season "+str(season_era)
                elif diff < -1.5:
                    primary["form_flag"] = "HOT — last 3 starts ERA "+str(recent_era)+" vs season "+str(season_era)

        # Add home/away splits
        splits = fetch_pitcher_splits(pid, 2026)
        if not splits:
            splits = fetch_pitcher_splits(pid, 2025)
        if splits:
            primary["splits"] = splits
            # Apply relevant split based on whether pitching at home or away
            if is_home and "home_era" in splits:
                primary["relevant_split"] = "Home ERA: "+str(splits["home_era"])+" K/9: "+str(splits.get("home_k9",""))
            elif not is_home and "away_era" in splits:
                primary["relevant_split"] = "Away ERA: "+str(splits["away_era"])+" K/9: "+str(splits.get("away_k9",""))

    return primary

def get_team_stats(team, stats, stat_type):
    s26 = stats.get(stat_type+"_2026",{}).get(team,{})
    s25 = stats.get(stat_type+"_2025",{}).get(team,{})
    if s26: s26["note"]="2026 YTD"; return s26
    if s25: s25["note"]="2025 full season"; return s25
    return {}

# ── Lineups ───────────────────────────────────────────────────────────────────

def fetch_lineup(game_pk):
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
            bats = player.get("batSide",{}).get("code","") # L or R
            s = player.get("seasonStats",{}).get("batting",{})
            avg = safe_float(s.get("avg","0"))
            ops = safe_float(s.get("ops","0"))
            if name:
                batters.append({"name":name,"pos":pos,"bats":bats,"avg":avg,"ops":ops})
        lineups[side] = {"team":team_name,"batters":batters}
    return lineups

def analyze_lineup_handedness(batters, sp_throws):
    """Calculate platoon advantage — what % of lineup is at disadvantage vs SP hand."""
    if not batters or not sp_throws:
        return {}
    same_hand = sum(1 for b in batters if b.get("bats") == sp_throws and b.get("bats"))
    total_known = sum(1 for b in batters if b.get("bats"))
    if total_known == 0:
        return {}
    pct = round(same_hand/total_known*100)
    # Same hand = platoon disadvantage for hitters
    return {
        "pct_same_hand_as_pitcher": pct,
        "platoon_note": ("Strong platoon advantage for SP — "+str(pct)+"% of lineup same-handed"
                        if pct >= 60 else
                        "Balanced lineup vs SP handedness"),
    }

# ── Bullpen fatigue ───────────────────────────────────────────────────────────

def fetch_bullpen_fatigue(team_id):
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
                        s = pdata.get("stats",{}).get("pitching",{})
                        ip = safe_float(s.get("inningsPitched","0"))
                        pc = int(s.get("pitchesThrown",0) or 0)
                        gs = int(s.get("gamesStarted",0) or 0)
                        if ip > 0 and gs == 0 and pc > 0:
                            name = pdata.get("person",{}).get("fullName","")
                            fatigued.append({"name":name,"pitches":pc,"ip":ip,"days_ago":days_ago})
    high_usage = [p for p in fatigued if p["pitches"] >= 20 and p["days_ago"] <= 2]
    return {
        "recent_usage": fatigued[:10],
        "high_usage_count": len(high_usage),
        "fatigued_arms": [p["name"] for p in high_usage],
        "fatigue_level": "SEVERE" if len(high_usage) >= 2 else "MODERATE" if len(high_usage) == 1 else "FRESH",
    }

# ── Injuries ──────────────────────────────────────────────────────────────────

def fetch_injuries(team_id):
    data = mlb_api("/teams/"+str(team_id)+"/roster", {"rosterType":"injuries"})
    injured = []
    for p in data.get("roster",[]):
        name = p.get("person",{}).get("fullName","")
        status = p.get("status",{}).get("description","")
        pos = p.get("position",{}).get("abbreviation","")
        injured.append({"name":name,"status":status,"pos":pos})
    return injured

# ── Weather ───────────────────────────────────────────────────────────────────

def fetch_weather(team_name):
    sd = STADIUMS.get(team_name, {})
    if sd.get("dome"):
        return {"temp_f":"Dome","wind_mph":0,"wind_dir":"N/A","precip_pct":0,"wind_impact":"Dome — weather irrelevant"}
    lat = sd.get("lat"); lon = sd.get("lon")
    if not lat or not WEATHER_API_KEY:
        return {"temp_f":"N/A","wind_mph":"N/A","wind_dir":"N/A","precip_pct":"N/A","wind_impact":"N/A"}
    try:
        r = requests.get("https://api.openweathermap.org/data/2.5/forecast",
            params={"lat":lat,"lon":lon,"appid":WEATHER_API_KEY,"units":"imperial","cnt":4},timeout=10)
        r.raise_for_status()
        e = r.json()["list"][0]
        deg = e["wind"].get("deg",0)
        dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"]
        wind_dir = dirs[round(deg/22.5)%16]
        wind_mph = round(e["wind"]["speed"]*2.237,1)
        temp_f = round(e["main"]["temp"])
        precip_pct = round(e.get("pop",0)*100)
        impact = wind_impact(team_name, wind_dir, wind_mph)
        return {
            "temp_f":temp_f,"wind_mph":wind_mph,"wind_dir":wind_dir,
            "precip_pct":precip_pct,"wind_impact":impact,
        }
    except:
        return {"temp_f":"N/A","wind_mph":"N/A","wind_dir":"N/A","precip_pct":"N/A","wind_impact":"N/A"}

# ── Games ────────────────────────────────────────────────────────────────────

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
                home     = g["teams"]["home"]["team"]["name"]
                away     = g["teams"]["away"]["team"]["name"]
                home_id  = g["teams"]["home"]["team"]["id"]
                away_id  = g["teams"]["away"]["team"]["id"]
                home_sp  = g["teams"]["home"].get("probablePitcher",{}).get("fullName","TBD")
                away_sp  = g["teams"]["away"].get("probablePitcher",{}).get("fullName","TBD")
                hs  = g["teams"]["home"].get("score",None)
                as_ = g["teams"]["away"].get("score",None)
                live_score = (away+" "+str(as_)+" - "+home+" "+str(hs)
                              if hs is not None and as_ is not None else None)
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

# ── Odds ──────────────────────────────────────────────────────────────────────

def best_book_value(bookmakers, market_key):
    book_map = {bm["key"]: bm for bm in bookmakers}
    ordered = [book_map[b] for b in BOOK_PRIORITY if b in book_map]
    for bm in bookmakers:
        if bm not in ordered: ordered.append(bm)
    for bm in ordered:
        for market in bm.get("markets",[]):
            if market["key"] == market_key and market.get("outcomes"):
                return market["outcomes"]
    return []

def fetch_odds():
    if not ODDS_API_KEY: return {}
    try:
        r = requests.get(
            "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/",
            params={
                "apiKey":ODDS_API_KEY,"regions":"us",
                "markets":"h2h,spreads,totals","oddsFormat":"american","dateFormat":"iso",
                "bookmakers":"draftkings,fanduel,betmgm,caesars,williamhill_us,betonlineag,bovada",
            },
            timeout=10
        )
        r.raise_for_status()
        print("Odds API remaining: "+str(r.headers.get("x-requests-remaining","?")))
        odds_map = {}
        for event in r.json():
            home = normalize_team(event.get("home_team",""))
            away = normalize_team(event.get("away_team",""))
            bms  = event.get("bookmakers",[])
            ml   = {}; total = {}; runline = {}

            for o in best_book_value(bms,"h2h"):
                ml[o["name"]] = o["price"]
            for o in best_book_value(bms,"totals"):
                if o["name"]=="Over": total["line"]=o.get("point",""); total["over"]=o["price"]
                elif o["name"]=="Under": total["under"]=o["price"]

            # Run line — scan ALL books, take best price per side
            for bm in bms:
                for market in bm.get("markets",[]):
                    if market["key"]=="spreads":
                        for o in market["outcomes"]:
                            nm=o["name"]; pr=o["price"]; pt=o.get("point","")
                            if nm not in runline or pr > runline[nm]["price"]:
                                runline[nm] = {"price":pr,"point":pt}

            # Estimate run line from ML if unavailable
            if not runline and ml:
                teams = list(ml.keys())
                if len(teams)==2:
                    for team in teams:
                        mlp = ml[team]
                        if mlp < 0:
                            runline[team] = {"price":max(mlp+80,-200),"point":"-1.5","estimated":True}
                        else:
                            runline[team] = {"price":min(mlp-60,200),"point":"+1.5","estimated":True}

            odds_map[away+"@"+home] = {"moneyline":ml,"total":total,"runline":runline}
        print("Fetched odds for "+str(len(odds_map))+" games")
        return odds_map
    except Exception as e:
        print("Odds error: "+str(e))
        return {}

# ── AI ────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a sharp MLB betting analyst. Find the single best positive EV bet for each game.
Use ONLY the real data provided. Never use memory for stats.

ABSOLUTE RULES — violating these means the pick is wrong:
1. win_prob_pct MINUS implied_prob_pct = ev_pct. If ev_pct < threshold, tier MUST be WATCH or SKIP.
   Thresholds: ML = 3%, Run Line = 3%, Totals = 4%. NO EXCEPTIONS.
2. Tier assignment: A = 7%+, B = 4-6%, C = exactly 3%, WATCH = 1-2%, SKIP = below 1% or no edge.
3. NEVER bet ML worse than -180. This is automatic SKIP regardless of edge.
4. NEVER use a total line you invented. Only use actual lines from the odds data.
5. Max 5 units per day across Tier A/B/C only. WATCH = 0 units always.
6. If SP edge favors Team A but you pick Team B ML, that is a contradiction. Fix it.
7. SKIP any game with status In Progress, Live, or Final.

USING RECENT FORM (critical — often more predictive than season ERA):
- "recent_form" shows last 3 starts ERA/K9. This is the pitcher's true current level.
- If recent ERA is 2+ runs higher than season ERA: pitcher is DECLINING — adjust win prob down.
- If recent ERA is 2+ runs lower than season ERA: pitcher is HOT — adjust win prob up.
- "form_flag" field summarizes this automatically — always read it.
- "relevant_split" shows home or away ERA specifically for this game context — use it.

USING HOME/AWAY SPLITS:
- A pitcher with 2.50 home ERA but 4.80 away ERA is a completely different pitcher on the road.
- Always check relevant_split for the pitcher's ERA in this game context (home or away).
- Team batting home/away splits tell you if an offense performs better at home or on the road.

USING PLATOON DATA:
- "platoon_note" in lineup shows % of lineup batting same-handed as the SP.
- 60%+ same-handed = significant platoon advantage for the pitcher (lean UNDER or pitcher's team ML).
- Switch hitters count as neutral.

USING WIND IMPACT (stadium-specific calculation now provided):
- "wind_impact" field already tells you if wind is blowing IN or OUT at this specific park.
- "OVER lean" or "UNDER lean" is already calculated based on stadium orientation.
- Trust this field — it accounts for each park's outfield facing direction.

USING UMPIRE DATA:
- rpg (runs per game) above 9.2 = meaningful OVER lean.
- rpg below 8.5 = meaningful UNDER lean.
- High k_pct = benefits pitchers = UNDER lean.

BULLPEN FATIGUE LEVELS:
- SEVERE (2+ arms 20+ pitches last 2 days): Expect 1+ extra runs late. Lean OVER or avoid that team's ML.
- MODERATE (1 arm fatigued): Note it but don't overweight.
- FRESH: Supports UNDER or ML for that team.

BET TYPE DECISION:
- ML: Multi-factor edge (SP + lineup or SP + bullpen). Odds range -115 to -175.
- Run Line -1.5: Dominant team — ERA gap 2.0+, elite bullpen, strong lineup. Comfortable favorite.
- Run Line +1.5: Overpriced favorite (-180+) with real edge. Underdog insurance.
- Total OVER: Fatigued bullpens, hitter park, over-leaning umpire, warm/out-blowing wind.
- Total UNDER: Elite dual SPs, pitcher park, fresh pens, under-leaning umpire, cold/in-blowing wind.
- F5 Total: Use when SP quality gap is the primary edge but bullpen data is unclear.
- If run line is unavailable, use ML. Never skip solely because run line is missing.
- WATCH: Real edge of 1-2% — track but do not bet.
- SKIP: No edge, insufficient data, or game already started.

OUTPUT: Raw JSON array only. No markdown. No backticks. Every game must appear.
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
  "pick": "exact bet e.g. Braves ML or Guardians +1.5 or UNDER 8.5 or SKIP",
  "line": "actual odds from data or N/A",
  "tier": "A or B or C or WATCH or SKIP",
  "units": 1.0,
  "win_prob_pct": 58,
  "implied_prob_pct": 52,
  "ev_pct": 6,
  "sp_analysis": "season ERA/K9 + recent form ERA + relevant split (home or away ERA)",
  "lineup_analysis": "platoon note + key hitters OPS + any absences",
  "bullpen_note": "fatigue level + team ERA/K9 for both teams",
  "injury_flags": "key IL players affecting this game or None",
  "umpire_note": "rpg + k_pct + lean direction",
  "park_note": "runs factor + HR factor + note",
  "weather_impact": "wind_impact field value + temp effect",
  "key_edge": "single most important reason with specific number",
  "rationale": "3 sentences: primary edge with stats. Supporting factors. Why this bet type at this line has positive EV.",
  "avoid_reason": "if SKIP/WATCH: specific reason. Empty string otherwise.",
  "flags": "SP changes, rain 40%+, key injuries. Empty string if none."
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

def enforce_ev_rules(picks):
    """
    Hard Python-level EV enforcement.
    Claude sometimes miscalculates or ignores thresholds — this catches it.
    """
    MIN_EV = {"ML":3,"Run Line":3,"Total OVER":4,"Total UNDER":4,"F5 OVER":4,"F5 UNDER":4}
    MAX_ML_ODDS = -180
    enforced = []
    for p in picks:
        tier = p.get("tier","SKIP")
        if tier in ("SKIP","WATCH"):
            enforced.append(p)
            continue

        bet_type = p.get("bet_type","")
        ev = p.get("ev_pct",0)
        try: ev = float(ev)
        except: ev = 0

        win_prob = p.get("win_prob_pct",0)
        implied  = p.get("implied_prob_pct",0)
        try:
            win_prob = float(win_prob); implied = float(implied)
        except:
            win_prob = 0; implied = 0

        # Recalculate EV from win/implied prob for accuracy
        calc_ev = ev  # default to stated ev
        if win_prob > 0 and implied > 0:
            calc_ev = round(win_prob - implied, 1)
            if abs(calc_ev - ev) > 2:
                print("EV mismatch for "+p.get("game","")+" — Claude said "+str(ev)+"%, calc: "+str(calc_ev)+"%. Using calculated.")
                p["ev_pct"] = calc_ev
                ev = calc_ev

        # Check ML odds cap
        line_str = str(p.get("line",""))
        try:
            line_num = float(line_str.replace("+",""))
            if "Run Line" not in bet_type and "Total" not in bet_type and "F5" not in bet_type:
                if line_num < MAX_ML_ODDS:
                    print("ENFORCING: "+p.get("game","")+" — ML odds "+str(line_num)+" worse than -180, downgrading to SKIP")
                    p["tier"] = "SKIP"
                    p["bet_type"] = "SKIP"
                    p["pick"] = "SKIP"
                    p["units"] = 0
                    p["avoid_reason"] = "ML odds "+str(line_num)+" exceed -180 cap — negative EV at this juice"
                    enforced.append(p)
                    continue
        except: pass

        # Check EV threshold — enforce on BOTH stated ev_pct AND calculated ev
        min_ev = MIN_EV.get(bet_type, 3)
        # Use the lower of stated vs calculated ev to be conservative
        effective_ev = min(ev, calc_ev) if win_prob > 0 and implied > 0 else ev
        if effective_ev < min_ev and tier in ("A","B","C"):
            if effective_ev >= 1:
                print("ENFORCING: "+p.get("game","")+" — EV "+str(effective_ev)+"% below "+str(min_ev)+"% threshold for "+bet_type+", downgrading to WATCH")
                p["tier"] = "WATCH"
                p["units"] = 0
                p["ev_pct"] = effective_ev
                p["avoid_reason"] = "EV "+str(effective_ev)+"% below minimum "+str(min_ev)+"% threshold for "+bet_type
            else:
                print("ENFORCING: "+p.get("game","")+" — EV "+str(effective_ev)+"% below threshold, downgrading to SKIP")
                p["tier"] = "SKIP"
                p["bet_type"] = "SKIP"
                p["pick"] = "SKIP"
                p["units"] = 0
                p["avoid_reason"] = "EV "+str(effective_ev)+"% below minimum threshold"

        # Fix tier/units alignment
        ev_val = p.get("ev_pct",0)
        try: ev_val = float(ev_val)
        except: ev_val = 0
        if p["tier"] == "A" and ev_val < 7:
            p["tier"] = "B" if ev_val >= 4 else ("C" if ev_val >= 3 else "WATCH")
        if p["tier"] in ("A","B","C") and p.get("units",0) == 0:
            p["units"] = 1.5 if p["tier"]=="A" else (1.0 if p["tier"]=="B" else 0.5)

        enforced.append(p)

    # Enforce 5u daily max — downgrade lowest EV picks if over
    active = [p for p in enforced if p.get("tier") in ("A","B","C")]
    total_u = sum(p.get("units",0) for p in active)
    if total_u > 5:
        active.sort(key=lambda x: x.get("ev_pct",0))
        while total_u > 5 and active:
            p = active.pop(0)
            print("ENFORCING daily cap: downgrading "+p.get("pick","")+" to WATCH")
            p["tier"] = "WATCH"
            p["units"] = 0
            p["avoid_reason"] = p.get("avoid_reason","") + " [Daily 5u cap reached]"
            total_u = sum(x.get("units",0) for x in active)

    return enforced

def call_ai(games_with_data):
    n = len(games_with_data)
    user_msg = (
        "Today is "+TODAY+". Analyze these "+str(n)+" MLB games.\n"
        "Use ALL provided data: SP season stats, recent form (last 3 starts), home/away splits, "
        "platoon matchups, bullpen fatigue, injuries, umpire tendencies, park factors, wind impact, odds.\n"
        "Return exactly "+str(n)+" entries. Raw JSON array only.\n\n"
        "GAMES:\n"+json.dumps(games_with_data, indent=2)
    )
    picks, model = _try_claude(user_msg)
    if picks is not None:
        picks = enforce_ev_rules(picks)
        return picks, model
    print("Falling back to Groq...")
    picks, model = _try_groq(user_msg)
    if picks is not None:
        picks = enforce_ev_rules(picks)
        return picks, model
    print("Both AI engines failed")
    return [], "None"

# ── Record tracker with CLV ───────────────────────────────────────────────────


def american_odds_to_payout(odds_str, units):
    """Calculate units won/lost from American odds and stake."""
    try:
        odds = float(str(odds_str).replace("+","").replace(" ",""))
        if odds < 0:
            return round(units * 100 / abs(odds), 3)
        else:
            return round(units * odds / 100, 3)
    except:
        return round(units * 0.909, 3)  # default -110 payout

def fetch_final_scores(date_str):
    """Fetch all final scores for a given date from MLB Stats API."""
    data = mlb_api("/schedule", {
        "sportId":"1","date":date_str,
        "hydrate":"linescore,team","gameType":"R",
    })
    scores = {}
    for de in data.get("dates",[]):
        for g in de.get("games",[]):
            status = g.get("status",{}).get("abstractGameState","")
            if status != "Final":
                continue
            home = g["teams"]["home"]["team"]["name"]
            away = g["teams"]["away"]["team"]["name"]
            home_score = g["teams"]["home"].get("score",0) or 0
            away_score = g["teams"]["away"].get("score",0) or 0
            # F5 score from linescore
            linescore = g.get("linescore",{})
            innings = linescore.get("innings",[])
            home_f5 = sum(int(inn.get("home",{}).get("runs",0) or 0) for inn in innings[:5])
            away_f5 = sum(int(inn.get("away",{}).get("runs",0) or 0) for inn in innings[:5])
            total_runs = home_score + away_score
            f5_total = home_f5 + away_f5
            key = away+"@"+home
            scores[key] = {
                "home": home,
                "away": away,
                "home_score": home_score,
                "away_score": away_score,
                "total_runs": total_runs,
                "f5_home": home_f5,
                "f5_away": away_f5,
                "f5_total": f5_total,
                "winner": home if home_score > away_score else away,
                "run_diff": abs(home_score - away_score),
            }
    return scores

def settle_pick(pick, scores):
    """
    Determine W/L/P for a pick based on final scores.
    Returns updated pick dict or None if game not found/not final.
    """
    game_str = pick.get("game","")
    # Convert "AWAY @ HOME" to "AWAY@HOME" key
    key = game_str.replace(" @ ","@")
    score = scores.get(key)
    if not score:
        return None  # game not found or not final

    bet_type = pick.get("bet_type","")
    pick_str = pick.get("pick","").upper()
    line_str = str(pick.get("line",""))
    units    = float(pick.get("units",0) or 0)
    result   = None

    try:
        # Parse total line from pick string e.g. "UNDER 8.5" or "OVER 7.0"
        def parse_total(s):
            parts = s.split()
            for p in parts:
                try: return float(p)
                except: pass
            return None

        if bet_type in ("Total OVER","Total UNDER","F5 OVER","F5 UNDER"):
            if "F5" in bet_type:
                actual = score["f5_total"]
            else:
                actual = score["total_runs"]
            line = parse_total(pick_str)
            if line is None:
                return None
            if actual > line:
                result = "W" if "OVER" in bet_type else "L"
            elif actual < line:
                result = "W" if "UNDER" in bet_type else "L"
            else:
                result = "P"  # push

        elif bet_type == "ML":
            # Determine which team we bet on
            pick_team = None
            for team in [score["home"], score["away"]]:
                if team.upper() in pick_str or any(w in pick_str for w in team.upper().split()):
                    pick_team = team
                    break
            if not pick_team:
                return None
            result = "W" if score["winner"] == pick_team else "L"

        elif bet_type == "Run Line":
            # e.g. "Dodgers -1.5" or "Guardians +1.5"
            pick_team = None; spread = None
            for team in [score["home"], score["away"]]:
                if any(w in pick_str for w in team.upper().split()):
                    pick_team = team
                    break
            if "-1.5" in pick_str: spread = -1.5
            elif "+1.5" in pick_str: spread = 1.5
            if not pick_team or spread is None:
                return None
            if pick_team == score["home"]:
                adjusted = score["home_score"] - score["away_score"] + spread
            else:
                adjusted = score["away_score"] - score["home_score"] + spread
            if adjusted > 0: result = "W"
            elif adjusted < 0: result = "L"
            else: result = "P"

        elif bet_type in ("WATCH","SKIP") or not bet_type:
            # For WATCH picks — still track if they would have won
            if "OVER" in pick_str or "UNDER" in pick_str:
                line = parse_total(pick_str)
                actual = score["total_runs"]
                if line:
                    if actual > line: result = "W" if "OVER" in pick_str else "L"
                    elif actual < line: result = "W" if "UNDER" in pick_str else "L"
                    else: result = "P"
            elif "ML" in pick_str or any(t.upper() in pick_str for t in [score["home"],score["away"]]):
                for team in [score["home"],score["away"]]:
                    if any(w in pick_str for w in team.upper().split()):
                        result = "W" if score["winner"]==team else "L"
                        break

    except Exception as e:
        print("Settlement error for "+game_str+": "+str(e))
        return None

    if result is None:
        return None

    # Calculate units won/lost
    if result == "W":
        units_result = american_odds_to_payout(line_str, units)
    elif result == "L":
        units_result = -units
    else:  # Push
        units_result = 0

    pick = dict(pick)
    pick["result"] = result
    pick["units_result"] = round(units_result, 3)
    pick["final_score"] = score["away"]+" "+str(score["away_score"])+" - "+score["home"]+" "+str(score["home_score"])
    return pick

def auto_settle_record(record):
    """
    Check all unsettled picks against final scores and auto-update results.
    Runs every time the workflow fires.
    """
    unsettled = [p for p in record["picks"] if not p.get("result") and p.get("tier") != "SKIP"]
    if not unsettled:
        return record, 0

    # Get unique dates we need scores for
    dates_needed = set(p.get("date","") for p in unsettled if p.get("date"))
    all_scores = {}
    for d in dates_needed:
        try:
            day_scores = fetch_final_scores(d)
            all_scores.update(day_scores)
            print("Fetched "+str(len(day_scores))+" final scores for "+d)
        except Exception as e:
            print("Score fetch error for "+d+": "+str(e))

    settled_count = 0
    for i, pick in enumerate(record["picks"]):
        if pick.get("result") or pick.get("tier") == "SKIP":
            continue
        updated = settle_pick(pick, all_scores)
        if updated:
            record["picks"][i] = updated
            settled_count += 1
            print("Auto-settled: "+updated.get("pick","")+" → "+updated["result"]
                  +" ("+str(updated["units_result"])+"u) | "+updated.get("final_score",""))

    return record, settled_count


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
    wins    = [p for p in settled if p["result"]=="W"]
    losses  = [p for p in settled if p["result"]=="L"]
    total_bets = len(settled)
    win_rate = round(len(wins)/total_bets*100,1) if total_bets else 0
    units_won = round(sum(p.get("units_result",0) for p in settled),2)

    # CLV analysis
    clv_picks = [p for p in settled if p.get("open_line") and p.get("close_line")]
    avg_clv = 0
    if clv_picks:
        clvs = []
        for p in clv_picks:
            try:
                ol = float(str(p["open_line"]).replace("+",""))
                cl = float(str(p["close_line"]).replace("+",""))
                # Positive CLV = we got better number than closing line
                clv = ol - cl if ol < 0 else cl - ol
                clvs.append(clv)
            except: pass
        avg_clv = round(sum(clvs)/len(clvs),1) if clvs else 0

    # By tier
    tiers = {}
    for p in settled:
        t = p.get("tier","?")
        if t not in tiers: tiers[t] = {"W":0,"L":0,"P":0,"units":0.0}
        tiers[t][p["result"]] += 1
        tiers[t]["units"] += p.get("units_result",0)

    # By bet type
    bet_types = {}
    for p in settled:
        bt = p.get("bet_type","?")
        if bt not in bet_types: bet_types[bt] = {"W":0,"L":0,"P":0,"units":0.0}
        bet_types[bt][p["result"]] += 1
        bet_types[bt]["units"] += p.get("units_result",0)

    # WATCH tracking
    watch_settled = [p for p in picks if p.get("tier")=="WATCH" and p.get("result")]
    watch_wins = len([p for p in watch_settled if p.get("result")=="W"])
    watch_total = len(watch_settled)
    watch_rate = round(watch_wins/watch_total*100,1) if watch_total else 0

    pending = [p for p in picks if not p.get("result") and p.get("tier") != "WATCH"]

    def stat_row(label, d):
        w=d["W"]; l=d["L"]; p=d.get("P",0); tot=w+l+p
        wr = round(w/tot*100,1) if tot else 0
        u = round(d["units"],2)
        color = "#1D9E75" if u>=0 else "#A32D2D"
        return ('<tr><td style="padding:8px 12px;font-weight:600">'+label+'</td>'
                '<td style="padding:8px 12px;text-align:center">'+str(w)+'-'+str(l)+(('-'+str(p)) if p else '')+'</td>'
                '<td style="padding:8px 12px;text-align:center">'+str(wr)+'%</td>'
                '<td style="padding:8px 12px;text-align:center;color:'+color+';font-weight:600">'
                +('+'if u>=0 else '')+str(u)+'u</td></tr>')

    def pick_row(p):
        res = p.get("result","")
        ur  = p.get("units_result",0)
        if res=="W": rc="#1D9E75"; rl="WIN"
        elif res=="L": rc="#A32D2D"; rl="LOSS"
        elif res=="P": rc="#888"; rl="PUSH"
        else: rc="#BA7517"; rl="PENDING"
        t = p.get("tier","?")
        tc = {"A":"#1D9E75","B":"#378ADD","C":"#BA7517","WATCH":"#8B6FBA"}.get(t,"#888")
        open_l  = p.get("open_line","")
        close_l = p.get("close_line","")
        clv_str = ""
        if open_l and close_l:
            try:
                ol = float(str(open_l).replace("+",""))
                cl = float(str(close_l).replace("+",""))
                clv = round(ol-cl if ol<0 else cl-ol, 0)
                clv_str = ("+" if clv>0 else "")+str(int(clv))
            except: pass
        return ('<tr style="border-bottom:0.5px solid #f0f0ee">'
                '<td style="padding:8px 12px;font-size:12px;color:#888">'+p.get("date","")+'</td>'
                '<td style="padding:8px 12px;font-size:13px;font-weight:500">'+p.get("pick","")+'</td>'
                '<td style="padding:8px 12px;font-size:12px;color:#666">'+p.get("game","")+'</td>'
                '<td style="padding:8px 12px;text-align:center"><span style="background:'+tc+'22;color:'+tc+';font-size:11px;font-weight:600;padding:1px 7px;border-radius:10px">'+t+'</span></td>'
                '<td style="padding:8px 12px;text-align:center;font-size:12px">'+str(open_l)+'</td>'
                '<td style="padding:8px 12px;text-align:center;font-size:11px;color:#888">'+str(close_l)+'</td>'
                '<td style="padding:8px 12px;text-align:center;font-size:11px;'
                +('color:#1D9E75' if clv_str.startswith('+') else 'color:#A32D2D' if clv_str.startswith('-') else '')
                +'">'+clv_str+'</td>'
                '<td style="padding:8px 12px;text-align:center"><span style="background:'+rc+'22;color:'+rc+';font-size:11px;font-weight:700;padding:2px 8px;border-radius:4px">'+rl+'</span></td>'
                '<td style="padding:8px 12px;text-align:center;font-weight:600;color:'+rc+'">'
                +('+'if ur>=0 else '')+str(round(ur,2))+'u</td></tr>')

    tier_rows = "".join(stat_row(t,d) for t,d in sorted(tiers.items()))
    bt_rows   = "".join(stat_row(bt,d) for bt,d in sorted(bet_types.items()))
    pick_rows = "".join(pick_row(p) for p in reversed(picks[-60:]))

    u_color = "#1D9E75" if units_won>=0 else "#A32D2D"
    u_str   = ("+" if units_won>=0 else "")+str(units_won)+"u"
    clv_color = "#1D9E75" if avg_clv>0 else "#A32D2D" if avg_clv<0 else "#888"

    return ('<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>MLB Model Record</title>'
            '<style>*{box-sizing:border-box;margin:0;padding:0}'
            'body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;'
            'background:#f9f9f7;color:#1a1a1a;padding:1.25rem;max-width:950px;margin:0 auto}'
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
            '.clv-note{font-size:12px;color:#378ADD;background:#E6F1FB;padding:8px 12px;'
            'border-radius:7px;margin-bottom:1rem}'
            '.watch-note{font-size:12px;color:#8B6FBA;background:#F0ECFB;padding:8px 12px;'
            'border-radius:7px;margin-bottom:1rem}'
            'footer{font-size:11px;color:#bbb;margin-top:1.5rem;text-align:center;padding-bottom:1rem}'
            '</style></head><body>'
            '<h1>MLB Model Record</h1>'
            '<div class="meta">Updated '+TODAY+' &nbsp;&middot;&nbsp; '
            '<a href="index.html" style="color:#378ADD;text-decoration:none">Today\'s picks &rarr;</a>'
            ' &nbsp;&middot;&nbsp; <a href="archive.html" style="color:#378ADD;text-decoration:none">Archive &rarr;</a></div>'
            '<div class="sum">'
            '<div class="s"><div class="sn">'+str(len(wins))+'-'+str(len(losses))+'</div><div class="sl">W-L Record</div></div>'
            '<div class="s"><div class="sn">'+str(win_rate)+'%</div><div class="sl">Win rate</div></div>'
            '<div class="s"><div class="sn" style="color:'+u_color+'">'+u_str+'</div><div class="sl">Units P&L</div></div>'
            '<div class="s"><div class="sn" style="color:'+clv_color+'">'+('+'if avg_clv>=0 else '')+str(avg_clv)+'</div><div class="sl">Avg CLV</div></div>'
            '<div class="s"><div class="sn" style="color:#8B6FBA">'+str(watch_rate)+'%</div><div class="sl">Watch hit %</div></div>'
            '</div>'
            +(('<div class="clv-note">&#128200; Closing Line Value: Avg CLV of '+('+'if avg_clv>=0 else '')+str(avg_clv)+' points. '
               +('Positive CLV means the model consistently finds value before the market moves. This is the strongest indicator of long-term profitability.' if avg_clv>0 else 'Negative CLV suggests lines are moving against picks. Review model edge calculations.')
               +'</div>') if clv_picks else '')
            +(('<div class="watch-note">&#128064; WATCH picks hitting at '+str(watch_rate)+'% ('+str(watch_wins)+'/'+str(watch_total)+'). '
               +('57%+ suggests lowering the betting threshold.' if watch_rate>=57 else 'Threshold looks correct.')+'</div>') if watch_total>=10 else '')
            +'<div class="section">Performance by Tier</div>'
            '<table><thead><tr><th>Tier</th><th>Record</th><th>Win %</th><th>Units</th></tr></thead><tbody>'+tier_rows+'</tbody></table>'
            '<div class="section">Performance by Bet Type</div>'
            '<table><thead><tr><th>Type</th><th>Record</th><th>Win %</th><th>Units</th></tr></thead><tbody>'+bt_rows+'</tbody></table>'
            '<div class="section">Pick History</div>'
            '<div style="font-size:12px;color:#888;margin-bottom:8px">Update result and close_line in record.json after each game settles. CLV = opening line vs closing line.</div>'
            '<table style="font-size:12px"><thead><tr>'
            '<th>Date</th><th>Pick</th><th>Game</th><th>Tier</th><th>Open</th><th>Close</th><th>CLV</th><th>Result</th><th>Units</th>'
            '</tr></thead><tbody>'+pick_rows+'</tbody></table>'
            '<footer>EV model &nbsp;&middot;&nbsp; Track CLV to measure long-term edge &nbsp;&middot;&nbsp; Paper trading until 50+ picks verified</footer>'
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
            '<h1>MLB Picks Archive</h1><div class="meta">Click any date to review picks</div>'
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
    (OUTPUT_DIR/"archive.html").write_text(html)

# ── HTML builder ──────────────────────────────────────────────────────────────

def build_html(data):
    all_picks = data.get("picks",[])
    active  = [p for p in all_picks if p.get("tier") in ("A","B","C")]
    watched = [p for p in all_picks if p.get("tier") == "WATCH"]
    skipped = [p for p in all_picks if p.get("tier") == "SKIP"]
    total_u = round(sum(p.get("units",0) for p in active),1)
    gen      = data.get("generated_at","")[:16].replace("T"," ")
    date     = data["date"]
    ai_model = data.get("ai_model","Unknown")

    if "Claude" in ai_model:
        mb_bg="#E1F5EE"; mb_tc="#0F6E56"
    else:
        mb_bg="#E6F1FB"; mb_tc="#185FA5"
    model_badge = ('<span style="background:'+mb_bg+';color:'+mb_tc+';font-size:11px;'
                   'font-weight:600;padding:2px 9px;border-radius:20px;">&#129302; '+ai_model+'</span>')

    TBAR={"A":"#1D9E75","B":"#378ADD","C":"#BA7517","WATCH":"#8B6FBA"}
    TBG ={"A":"#E1F5EE","B":"#E6F1FB","C":"#FAEEDA","WATCH":"#F0ECFB"}
    TTC ={"A":"#0F6E56","B":"#185FA5","C":"#854F0B","WATCH":"#4A2D8F"}
    TLBL={"A":"TIER A &mdash; PLAY","B":"TIER B &mdash; PLAY","C":"TIER C &mdash; LEAN","WATCH":"WATCH &mdash; TRACK ONLY"}

    def sp_box(label, name):
        return ('<div style="background:#f7f7f5;border-radius:7px;padding:8px 10px">'
                '<div style="font-size:10px;color:#999;margin-bottom:3px;text-transform:uppercase;letter-spacing:.05em">'+label+'</div>'
                '<div style="font-size:13px;font-weight:500">'+str(name)+'</div></div>')

    def mrow(icon, text):
        t=str(text)
        if not t or t in ("N/A","null","None",""): return ""
        return '<div style="font-size:12px;color:#666;margin-bottom:3px">'+icon+' '+t+'</div>'

    def flag_row(text):
        t=str(text)
        if not t or t in ("","null","None"): return ""
        return ('<div style="font-size:12px;background:#FAEEDA;color:#633806;padding:4px 8px;'
                'border-radius:4px;margin-bottom:6px">&#9888; '+t+'</div>')

    def score_span(game):
        return ('<span id="'+score_id(game)+'" style="font-size:11px;background:#f0f0ee;'
                'color:#888;padding:2px 8px;border-radius:4px;margin-left:6px">--</span>')

    def pick_card(p):
        t=p.get("tier","C")
        c=TBAR.get(t,"#888"); bg=TBG.get(t,"#eee"); tc=TTC.get(t,"#333")
        ev=p.get("ev_pct",0); bw=min(int(float(ev or 0))*8,100)
        game=str(p.get("game",""))
        ump=str(p.get("hp_ump",""))
        ump_txt=(' &nbsp;&middot;&nbsp; &#9878; '+ump) if ump else ""
        return (
            '<div style="background:#fff;border:0.5px solid #e0e0e0;border-left:3px solid '+c+';'
            'border-radius:10px;padding:1rem 1.25rem;margin-bottom:10px">'
            '<span style="background:'+bg+';color:'+tc+';font-size:11px;font-weight:600;'
            'padding:2px 9px;border-radius:4px;display:inline-block;margin-bottom:8px">'+TLBL.get(t,"LEAN")+'</span>'
            +flag_row(p.get("flags",""))+
            '<div style="font-size:16px;font-weight:600;margin-bottom:2px">'+str(p.get("pick",""))+'</div>'
            '<div style="font-size:13px;color:#777;margin-bottom:10px">'
            +game+' &nbsp;&middot;&nbsp; '+str(p.get("line","N/A"))
            +' &nbsp;&middot;&nbsp; '+str(p.get("units",0))+'u'+ump_txt+score_span(game)+'</div>'
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
            +mrow("&#128101;",p.get("lineup_analysis",""))
            +mrow("&#128293;",p.get("bullpen_note",""))
            +mrow("&#129657;",p.get("injury_flags",""))
            +mrow("&#9878;",p.get("umpire_note",""))
            +mrow("&#127966;",p.get("park_note",""))
            +mrow("&#127748;",p.get("weather_impact",""))+'</div>'
            '<div style="border-top:0.5px solid #eee;padding-top:8px">'
            '<div style="font-size:12px;font-weight:600;color:#222;margin-bottom:3px">Key edge: '+str(p.get("key_edge",""))+'</div>'
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
            'padding:2px 9px;border-radius:4px;display:inline-block;margin-bottom:8px">WATCH &mdash; TRACK ONLY</span>'
            +flag_row(p.get("flags",""))+
            '<div style="font-size:16px;font-weight:600;margin-bottom:2px">'+str(p.get("pick",""))+'</div>'
            '<div style="font-size:13px;color:#777;margin-bottom:4px">'
            +game+' &nbsp;&middot;&nbsp; '+str(p.get("line","N/A"))+' &nbsp;&middot;&nbsp; Not betting'
            +score_span(game)+'</div>'
            '<div style="font-size:11px;color:#8B6FBA;margin-bottom:10px;font-style:italic">'
            'Edge is real but below threshold ('+str(ev)+'% vs 3% min). Tracking to build confidence.</div>'
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px">'
            +sp_box("Away SP",p.get("away_sp","TBD"))+sp_box("Home SP",p.get("home_sp","TBD"))+'</div>'
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
            +sp_box("Away SP",p.get("away_sp","TBD"))+sp_box("Home SP",p.get("home_sp","TBD"))+'</div>'
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
        'el.textContent="F: "+away+" "+aS+" - "+home+" "+hS;'
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
        '}).catch(function(e){console.log("score err",e);}); }'
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
        '.sn{font-size:22px;font-weight:700}.sl{font-size:10px;color:#999;margin-top:2px;text-transform:uppercase;letter-spacing:.04em}'
        '.st{font-size:13px;font-weight:600;color:#999;text-transform:uppercase;letter-spacing:.06em;margin:1.25rem 0 0.5rem}'
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
        '<footer>EV model &nbsp;&middot;&nbsp; 2025+2026 stats &nbsp;&middot;&nbsp; Recent form &nbsp;&middot;&nbsp; Splits &nbsp;&middot;&nbsp; Lineups &nbsp;&middot;&nbsp; Bullpen &nbsp;&middot;&nbsp; Umpires &nbsp;&middot;&nbsp; Never bet more than you can afford to lose</footer>'
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
        ump     = get_ump_stats(g.get("hp_ump",""))

        # Lineups — only for games not yet started
        lineups = {}
        status = g.get("status","")
        if g.get("game_pk") and status not in ("In Progress","Live","Final","Game Over","Completed","Pre-Game"):
            try: lineups = fetch_lineup(g["game_pk"])
            except: pass

        # SP stats with recent form + splits
        home_sp_stats = get_pitcher_stats(g["home_sp"], stats, is_home=True)
        away_sp_stats = get_pitcher_stats(g["away_sp"], stats, is_home=False)

        # Platoon analysis
        home_platoon = {}; away_platoon = {}
        if lineups:
            # Determine pitcher handedness from stats if available
            home_throws = home_sp_stats.get("throws","")
            away_throws = away_sp_stats.get("throws","")
            if lineups.get("away",{}).get("batters") and away_throws:
                home_platoon = analyze_lineup_handedness(lineups["away"]["batters"], away_throws)
            if lineups.get("home",{}).get("batters") and home_throws:
                away_platoon = analyze_lineup_handedness(lineups["home"]["batters"], home_throws)

        # Bullpen fatigue
        home_bullpen={}; away_bullpen={}
        try: home_bullpen = fetch_bullpen_fatigue(g["home_id"])
        except: pass
        try: away_bullpen = fetch_bullpen_fatigue(g["away_id"])
        except: pass

        # Injuries
        home_injuries=[]; away_injuries=[]
        try: home_injuries = fetch_injuries(g["home_id"])
        except: pass
        try: away_injuries = fetch_injuries(g["away_id"])
        except: pass

        # Team home/away splits (2026 if available, else 2025)
        home_splits={}; away_splits={}
        try: home_splits = fetch_team_home_away_splits(g["home_id"], 2026)
        except: pass
        if not home_splits:
            try: home_splits = fetch_team_home_away_splits(g["home_id"], 2025)
            except: pass
        try: away_splits = fetch_team_home_away_splits(g["away_id"], 2026)
        except: pass
        if not away_splits:
            try: away_splits = fetch_team_home_away_splits(g["away_id"], 2025)
            except: pass

        gd = dict(g)
        gd["odds"]                  = odds
        gd["weather"]               = weather
        gd["park_factor"]           = park
        gd["ump_stats"]             = ump
        gd["home_sp_stats"]         = home_sp_stats
        gd["away_sp_stats"]         = away_sp_stats
        gd["home_team_pitching"]    = get_team_stats(g["home"], stats, "team_pitching")
        gd["away_team_pitching"]    = get_team_stats(g["away"], stats, "team_pitching")
        gd["home_team_batting"]     = get_team_stats(g["home"], stats, "team_batting")
        gd["away_team_batting"]     = get_team_stats(g["away"], stats, "team_batting")
        gd["home_lineup"]           = lineups.get("home",{})
        gd["away_lineup"]           = lineups.get("away",{})
        gd["home_platoon"]          = home_platoon
        gd["away_platoon"]          = away_platoon
        gd["home_bullpen_fatigue"]  = home_bullpen
        gd["away_bullpen_fatigue"]  = away_bullpen
        gd["home_injuries"]         = home_injuries[:5]
        gd["away_injuries"]         = away_injuries[:5]
        gd["home_team_splits"]      = home_splits
        gd["away_team_splits"]      = away_splits
        games_with_data.append(gd)

    # Save updated stats cache (may have new player ID lookups)
    STATS_CACHE.write_text(json.dumps(stats))

    picks, ai_model = call_ai(games_with_data)
    active = [p for p in picks if p.get("tier") in ("A","B","C")]

    # Auto-settle any previous picks that have final scores
    record = load_record()
    record, settled = auto_settle_record(record)
    if settled:
        print("Auto-settled "+str(settled)+" picks")
        save_record(record)
    existing_keys = {p["game"]+p.get("date","") for p in record["picks"]}
    for p in active:
        key = p.get("game","")+TODAY
        if key not in existing_keys:
            record["picks"].append({
                "date":     TODAY,
                "game":     p.get("game",""),
                "pick":     p.get("pick",""),
                "bet_type": p.get("bet_type",""),
                "line":     p.get("line",""),
                "open_line":p.get("line",""),   # store opening line for CLV
                "close_line":"",                 # filled in manually or via future script
                "tier":     p.get("tier",""),
                "units":    p.get("units",0),
                "ev_pct":   p.get("ev_pct",0),
                "result":   "",
                "units_result": 0,
            })
    for p in [x for x in picks if x.get("tier")=="WATCH"]:
        key = p.get("game","")+TODAY+"W"
        if key not in existing_keys:
            record["picks"].append({
                "date":     TODAY,
                "game":     p.get("game",""),
                "pick":     p.get("pick","")+" (WATCH)",
                "bet_type": p.get("bet_type",""),
                "line":     p.get("line",""),
                "open_line":p.get("line",""),
                "close_line":"",
                "tier":     "WATCH",
                "units":    0,
                "ev_pct":   p.get("ev_pct",0),
                "result":   "",
                "units_result": 0,
            })
    record["updated"] = TODAY
    save_record(record)

    output = {
        "date":         TODAY,
        "generated_at": datetime.datetime.utcnow().isoformat()+"Z",
        "stats_date":   stats.get("date",""),
        "ai_model":     ai_model,
        "total_games":  len(games),
        "total_picks":  len(active),
        "picks":        picks,
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
