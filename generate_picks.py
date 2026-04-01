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
FORCE_REGEN     = os.environ.get("FORCE_REGENERATE", "no").lower() == "yes"
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
    # Additional umps
    "Sean Barber":      {"rpg":8.9,"k_pct":0.22,"bb_pct":0.08,"note":"Neutral"},
    "Nestor Ceja":      {"rpg":8.7,"k_pct":0.23,"bb_pct":0.08,"note":"Neutral"},
    "Ramon De Jesus":   {"rpg":8.8,"k_pct":0.22,"bb_pct":0.08,"note":"Neutral"},
    "Brennan Miller":   {"rpg":8.9,"k_pct":0.22,"bb_pct":0.08,"note":"Neutral"},
    "Carlos Torres":    {"rpg":9.1,"k_pct":0.21,"bb_pct":0.09,"note":"Slight over lean"},
    "Roberto Ortiz":    {"rpg":9.0,"k_pct":0.22,"bb_pct":0.09,"note":"Neutral"},
    "Chris Segal":      {"rpg":8.6,"k_pct":0.24,"bb_pct":0.07,"note":"Pitcher friendly"},
    "Ryan Blakney":     {"rpg":9.1,"k_pct":0.22,"bb_pct":0.09,"note":"Slight over lean"},
    "Gabe Morales":     {"rpg":8.6,"k_pct":0.24,"bb_pct":0.07,"note":"Pitcher friendly"},
    "Roberto Ortiz":    {"rpg":9.0,"k_pct":0.22,"bb_pct":0.09,"note":"Neutral"},
    "Phil Cuzzi":       {"rpg":9.1,"k_pct":0.22,"bb_pct":0.09,"note":"Slight over lean"},
    "Tripp Gibson":     {"rpg":8.7,"k_pct":0.23,"bb_pct":0.08,"note":"Neutral"},
    "Jim Reynolds":     {"rpg":9.0,"k_pct":0.22,"bb_pct":0.09,"note":"Neutral"},
    "Scott Barry":      {"rpg":8.8,"k_pct":0.23,"bb_pct":0.08,"note":"Neutral"},
    "Mike Muchlinski":  {"rpg":8.9,"k_pct":0.22,"bb_pct":0.08,"note":"Neutral"},
    "Erich Bacchus":    {"rpg":8.7,"k_pct":0.23,"bb_pct":0.08,"note":"Neutral"},
    "Chris Conroy":     {"rpg":9.2,"k_pct":0.21,"bb_pct":0.09,"note":"Slight over lean"},
    "David Rackley":    {"rpg":8.5,"k_pct":0.24,"bb_pct":0.07,"note":"Pitcher friendly"},
    "Edwin Moscoso":    {"rpg":8.8,"k_pct":0.22,"bb_pct":0.08,"note":"Neutral"},
    "Ben May":          {"rpg":9.0,"k_pct":0.22,"bb_pct":0.09,"note":"Neutral"},
    "Nate Tomlinson":   {"rpg":8.7,"k_pct":0.23,"bb_pct":0.08,"note":"Neutral"},
    "John Libka":       {"rpg":8.9,"k_pct":0.22,"bb_pct":0.08,"note":"Neutral"},
    "Manny Gonzalez":   {"rpg":9.1,"k_pct":0.21,"bb_pct":0.09,"note":"Slight over lean"},
    "Paul Emmel":       {"rpg":8.6,"k_pct":0.24,"bb_pct":0.07,"note":"Pitcher friendly"},
    "Alan Porter":      {"rpg":9.3,"k_pct":0.21,"bb_pct":0.09,"note":"Over lean"},
    "Bill Miller":      {"rpg":8.8,"k_pct":0.22,"bb_pct":0.08,"note":"Neutral"},
    "Larry Vanover":    {"rpg":9.0,"k_pct":0.22,"bb_pct":0.09,"note":"Neutral"},
    "Laz Diaz":         {"rpg":9.4,"k_pct":0.20,"bb_pct":0.10,"note":"High run ump"},
    "Ted Barrett":      {"rpg":8.7,"k_pct":0.23,"bb_pct":0.08,"note":"Neutral"},
    "Gary Cederstrom":  {"rpg":8.5,"k_pct":0.24,"bb_pct":0.07,"note":"Pitcher friendly"},
    "Mike Winters":     {"rpg":8.9,"k_pct":0.22,"bb_pct":0.08,"note":"Neutral"},
    "Ron Kulpa":        {"rpg":9.2,"k_pct":0.21,"bb_pct":0.09,"note":"Slight over lean"},
    "Tom Hallion":      {"rpg":8.6,"k_pct":0.24,"bb_pct":0.07,"note":"Pitcher friendly"},
    "Joe West":         {"rpg":9.5,"k_pct":0.20,"bb_pct":0.10,"note":"Strong over lean"},
    "Tim Timmons":      {"rpg":9.1,"k_pct":0.22,"bb_pct":0.09,"note":"Slight over lean"},
    "Alfonso Marquez":  {"rpg":8.8,"k_pct":0.23,"bb_pct":0.08,"note":"Neutral"},
    "Chris Guccione":   {"rpg":9.0,"k_pct":0.22,"bb_pct":0.09,"note":"Neutral"},
    "Sam Holbrook":     {"rpg":8.7,"k_pct":0.23,"bb_pct":0.08,"note":"Neutral"},
    "Hunter Wendelstedt": {"rpg":9.3,"k_pct":0.21,"bb_pct":0.09,"note":"Over lean"},
    "Jerry Layne":      {"rpg":8.5,"k_pct":0.24,"bb_pct":0.07,"note":"Pitcher friendly"},
    "Fieldin Culbreth": {"rpg":8.8,"k_pct":0.22,"bb_pct":0.08,"note":"Neutral"},
    "Mike DiMuro":      {"rpg":9.1,"k_pct":0.22,"bb_pct":0.09,"note":"Slight over lean"},
    "Dana DeMuth":      {"rpg":8.6,"k_pct":0.24,"bb_pct":0.07,"note":"Pitcher friendly"},
    "Bruce Dreckman":   {"rpg":8.9,"k_pct":0.22,"bb_pct":0.08,"note":"Neutral"},
    "Toby Basner":      {"rpg":8.8,"k_pct":0.22,"bb_pct":0.08,"note":"Neutral"},
    "Shaun Lampe":      {"rpg":8.7,"k_pct":0.23,"bb_pct":0.08,"note":"Neutral"},
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

    # Temperature modifier — cold kills wind OUT benefit for OVER
    temp_note = ""
    if lean == "OVER lean":
        # Below 50F: wind OUT loses all OVER value (cold = dead ball)
        # 50-60F: partial value
        # Above 60F: full value
        pass  # temp is passed via weather dict; note added in summarize_game

    result = str(wind_mph)+" mph "+wind_dir_str+" — "+direction
    if lean:
        result += " ("+lean+")"
    return result

def effective_wind_lean(wind_impact_str, temp_f):
    """
    Calculate wind's directional contribution to scoring environment.
    Returns a string describing the wind factor — NOT a pick recommendation.
    Wind is ONE factor among many. Claude weighs it alongside SP quality,
    bullpen fatigue, park factor, and lineup data.
    """
    if not wind_impact_str or "Dome" in wind_impact_str:
        return "Dome or no wind — weather not a factor"
    try:
        temp = float(temp_f) if temp_f not in ("N/A","Dome","") else 72.0
    except:
        temp = 72.0

    is_out = "blowing OUT" in wind_impact_str and "OVER lean" in wind_impact_str
    is_in  = "blowing IN" in wind_impact_str and "UNDER lean" in wind_impact_str
    is_cross = "crosswind" in wind_impact_str

    if is_cross or "minimal" in wind_impact_str:
        return "Crosswind or minimal — no directional scoring impact"

    if is_out:
        if temp < 50:
            return "Wind OUT but below 50F — cold air neutralizes carry, minimal scoring impact"
        elif temp < 60:
            return "Wind OUT with cool temps ("+str(int(temp))+"F) — partial OVER lean, weight other factors more"
        else:
            return "Wind OUT at "+str(int(temp))+"F — meaningful OVER lean, ball carries well"

    if is_in:
        if temp < 50:
            return "Wind IN at "+str(int(temp))+"F — strong UNDER lean, cold+wind IN suppresses offense"
        else:
            return "Wind IN at "+str(int(temp))+"F — moderate UNDER lean, factor alongside SP/bullpen data"

    return "Wind direction unclear — treat as neutral"

def get_park_factor(venue):
    if venue in PARK_FACTORS:
        return PARK_FACTORS[venue]
    for k,v in PARK_FACTORS.items():
        if k.lower() in venue.lower() or venue.lower() in k.lower():
            return v
    return {"runs":1.0,"hr":1.0,"note":"No park data — using neutral"}

def get_ump_stats(ump_name):
    if not ump_name or ump_name == "TBD":
        return {"name":"TBD","rpg":8.8,"k_pct":0.22,"note":"Unknown — using league average"}
    # Try full name match first
    for k,v in UMP_DATA.items():
        if ump_name.lower() == k.lower():
            return dict(v, name=ump_name)
    # Try last name match
    last = ump_name.split()[-1] if ump_name else ""
    for k,v in UMP_DATA.items():
        if last.lower() in k.lower() or k.lower().endswith(last.lower()):
            return dict(v, name=ump_name)
    # Try first name match
    first = ump_name.split()[0] if ump_name else ""
    for k,v in UMP_DATA.items():
        if first.lower() in k.lower():
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

_SPLITS_CACHE = {}

def fetch_team_home_away_splits(team_id, season):
    """Fetch home vs away batting splits for a team."""
    cache_key = str(team_id)+"-"+str(season)
    if cache_key in _SPLITS_CACHE:
        return _SPLITS_CACHE[cache_key]
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
    _SPLITS_CACHE[cache_key] = result
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
            num_starts = recent.get("starts", 0)
            if season_era > 0 and recent_era > 0:
                diff = recent_era - season_era
                # Require minimum 4 starts before form flags fire — early season noise suppression
                if num_starts >= 4:
                    if diff > 1.5:
                        primary["form_flag"] = "DECLINING — last 3 starts ERA "+str(recent_era)+" vs season "+str(season_era)
                    elif diff < -1.5:
                        primary["form_flag"] = "HOT — last 3 starts ERA "+str(recent_era)+" vs season "+str(season_era)
                else:
                    primary["form_flag"] = "SMALL SAMPLE ("+str(num_starts)+" starts) — recent form not reliable"

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
        # Get injured player names to filter from lineup
        injured_ids = set()
        for pid_str, pdata in players.items():
            status = pdata.get("gameStatus",{})
            if not status.get("isCurrentBatter") and not status.get("isCurrentPitcher"):
                il_status = pdata.get("status",{}).get("code","")
                if il_status in ("IL10","IL15","IL60","DL10","DL15","DL60","DTD","SCR"):
                    injured_ids.add(str(pid_str).replace("ID",""))

        for pid in batting_order[:9]:
            player = players.get("ID"+str(pid),{})
            name = player.get("person",{}).get("fullName","")
            pos = player.get("position",{}).get("abbreviation","")
            bats = player.get("batSide",{}).get("code","") # L or R
            s = player.get("seasonStats",{}).get("batting",{})
            avg = safe_float(s.get("avg","0"))
            ops = safe_float(s.get("ops","0"))
            # Skip if player is on IL/injured list
            if str(pid) in injured_ids:
                continue
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

# Module-level bullpen cache to avoid redundant API calls
_BULLPEN_CACHE = {}

def fetch_bullpen_fatigue(team_id):
    if team_id in _BULLPEN_CACHE:
        return _BULLPEN_CACHE[team_id]
    fatigued = []
    for days_ago in range(1, 3):  # Only check last 2 days — day 3 is rarely relevant
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
    result = {
        "recent_usage": fatigued[:10],
        "high_usage_count": len(high_usage),
        "fatigued_arms": [p["name"] for p in high_usage],
        "fatigue_level": "SEVERE" if len(high_usage) >= 2 else "MODERATE" if len(high_usage) == 1 else "FRESH",
    }
    _BULLPEN_CACHE[team_id] = result
    return result

# ── Injuries ──────────────────────────────────────────────────────────────────

_INJURY_CACHE = {}
_ESPN_INJURIES = {}  # Global cache for ESPN injury data

def fetch_espn_injuries():
    """
    Fetch MLB injury report from ESPN.
    Returns dict keyed by player name with injury status.
    Updated once per run and cached globally.
    """
    global _ESPN_INJURIES
    if _ESPN_INJURIES:
        return _ESPN_INJURIES

    try:
        r = requests.get(
            "https://www.espn.com/mlb/injuries",
            headers={"User-Agent": "Mozilla/5.0 (compatible; MLB-Model/1.0)"},
            timeout=15
        )
        if not r.ok:
            print("ESPN injury fetch failed: "+str(r.status_code))
            return {}

        # Parse injury table from ESPN HTML
        html = r.text
        injuries = {}

        # ESPN injury pages list players in format: Name | Pos | Status | Comment
        # Look for injury table rows
        import re
        # Find player names and statuses in ESPN's injury HTML
        # ESPN uses consistent patterns for injury data
        pattern = r'"displayName":"([^"]+)"[^}]*"injuryStatus":"([^"]+)"'
        matches = re.findall(pattern, html)

        for name, status in matches:
            if name and status and status != "Active":
                injuries[name] = {
                    "status": status,
                    "source": "ESPN"
                }

        # Also try parsing the injuries API endpoint ESPN uses
        # ESPN API returns team context — store by team name
        if not injuries:
            api_r = requests.get(
                "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/injuries",
                timeout=10
            )
            if api_r.ok:
                data = api_r.json()
                for team_data in data.get("injuries", []):
                    team_name = team_data.get("team", {}).get("displayName", "")
                    for player in team_data.get("injuries", []):
                        athlete = player.get("athlete", {})
                        name = athlete.get("displayName", "")
                        status = player.get("status", "")
                        detail = player.get("shortComment", "")
                        if name and status and "Active" not in status:
                            injuries[name] = {
                                "status": status,
                                "detail": detail,
                                "source": "ESPN API",
                                "team": team_name,
                            }

        print("ESPN injuries loaded: "+str(len(injuries))+" players")
        _ESPN_INJURIES = injuries
        return injuries

    except Exception as e:
        print("ESPN injury error: "+str(e))
        return {}

def fetch_injuries(team_id):
    """
    Fetch injuries from TWO sources and merge:
    1. MLB Stats API — official IL placements (formal, accurate)
    2. ESPN — day-to-day and game-time decisions (more timely)
    Only returns players confirmed injured from these sources.
    """
    if team_id in _INJURY_CACHE:
        return _INJURY_CACHE[team_id]

    injured = []

    # Source 1: MLB Stats API official IL — only confirmed IL placements
    IL_STATUSES = {
        "10-Day IL", "15-Day IL", "60-Day IL",
        "10-Day Injured List", "15-Day Injured List", "60-Day Injured List",
        "Bereavement List", "Restricted List", "Suspended List",
    }
    try:
        data = mlb_api("/teams/"+str(team_id)+"/roster", {"rosterType":"injuries"})
        for p in data.get("roster",[]):
            name = p.get("person",{}).get("fullName","")
            status = p.get("status",{}).get("description","")
            pos = p.get("position",{}).get("abbreviation","")
            # Only include confirmed IL placements — not day-to-day or general roster moves
            if name and any(il in status for il in IL_STATUSES):
                injured.append({
                    "name": name,
                    "status": status,
                    "pos": pos,
                    "source": "MLB IL"
                })
    except Exception as e:
        print("MLB IL fetch error for team "+str(team_id)+": "+str(e))

    # Source 2: ESPN (game-time decisions, day-to-day)
    # Match ESPN injuries to this team by cross-referencing player names
    # We can't filter by team_id from ESPN directly so we load all and match later
    # This is handled in main() after all teams are known

    _INJURY_CACHE[team_id] = injured
    return injured

def get_team_injuries_with_espn(team_name, ml_injuries, espn_injuries):
    """
    Merge MLB IL injuries with ESPN injuries for a specific team.
    Only adds ESPN injuries that match this team — no cross-team contamination.
    """
    combined = list(ml_injuries)
    existing_names = {p["name"] for p in combined}

    # Normalize team name for matching
    team_lower = team_name.lower()

    for name, data in espn_injuries.items():
        if name in existing_names:
            continue
        # Only add if ESPN data has team context matching this team
        espn_team = data.get("team", "").lower()
        if not espn_team:
            continue  # No team context — skip to avoid cross-team contamination
        # Check if ESPN team name matches — handle common variations
        if (espn_team in team_lower or team_lower in espn_team or
            any(word in espn_team for word in team_lower.split() if len(word) > 3)):
            combined.append({
                "name": name,
                "status": data.get("status",""),
                "pos": "",
                "source": "ESPN",
                "detail": data.get("detail","")
            })

    return combined[:8]

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

def fetch_team_streak(team_id):
    """Fetch recent form — last 10 games W/L record for momentum."""
    try:
        data = mlb_api("/teams/"+str(team_id)+"/records", {
            "leagueId":"103,104","season":"2026"
        })
        records = data.get("records",[])
        if not records: return {}
        rec = records[0]
        streak = rec.get("streak",{})
        return {
            "wins": rec.get("wins",0),
            "losses": rec.get("losses",0),
            "streak_type": streak.get("streakType",""),
            "streak_number": streak.get("streakNumber",0),
            "last10_wins": rec.get("lastTen","").split("-")[0] if rec.get("lastTen") else "",
            "last10_losses": rec.get("lastTen","").split("-")[1] if rec.get("lastTen") and "-" in rec.get("lastTen","") else "",
        }
    except:
        return {}

# ── Odds ──────────────────────────────────────────────────────────────────────

def best_book_value(bookmakers, market_key):
    """Return outcomes with best price for each side across all books."""
    best_prices = {}  # outcome_name -> best price
    best_outcomes = {}  # outcome_name -> full outcome dict at best price

    for bm in bookmakers:
        for market in bm.get("markets",[]):
            if market["key"] != market_key:
                continue
            for o in market.get("outcomes",[]):
                name = o["name"]
                price = o.get("price", -999)
                # Higher price is always better for the bettor
                if name not in best_prices or price > best_prices[name]:
                    best_prices[name] = price
                    best_outcomes[name] = dict(o)
                    best_outcomes[name]["_book"] = bm.get("key","")

    return list(best_outcomes.values())

def fetch_odds():
    if not ODDS_API_KEY: return {}
    try:
        r = requests.get(
            "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/",
            params={
                "apiKey":ODDS_API_KEY,"regions":"us",
                "markets":"h2h,spreads,totals","oddsFormat":"american","dateFormat":"iso",
                "bookmakers":"draftkings,fanduel,betmgm,caesars,williamhill_us,betonlineag,bovada,betrivers,unibet,pointsbetus",
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

            # ML — best price across ALL books for each team
            for bm in bms:
                for market in bm.get("markets",[]):
                    if market["key"] == "h2h":
                        for o in market.get("outcomes",[]):
                            nm = o["name"]; pr = o["price"]
                            if nm not in ml or pr > ml[nm]:
                                ml[nm] = pr

            # Totals — best over AND under price across all books
            best_over = None; best_under = None; total_line = ""
            for bm in bms:
                for market in bm.get("markets",[]):
                    if market["key"] == "totals":
                        for o in market.get("outcomes",[]):
                            if o["name"] == "Over":
                                if not total_line: total_line = str(o.get("point",""))
                                if best_over is None or o["price"] > best_over:
                                    best_over = o["price"]
                            elif o["name"] == "Under":
                                if best_under is None or o["price"] > best_under:
                                    best_under = o["price"]
            if best_over: total["over"] = best_over
            if best_under: total["under"] = best_under
            if total_line: total["line"] = total_line

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
Use ONLY the real data provided. Never use memory for stats, injuries, or lineups.

ABSOLUTE RULES — violating these means the pick is wrong:
1. win_prob_pct MINUS implied_prob_pct = ev_pct. If ev_pct < threshold, tier MUST be WATCH or SKIP.
   Thresholds: ML = 3%, Run Line = 3%, Totals = 4%. NO EXCEPTIONS.
2. Tier assignment: MAX = 10%+, A = 7%+, B = 4-6%, C = exactly 3%, WATCH = 1-2%, SKIP = below 1%.
   BASELINE WIN PROBABILITY: Each game includes baseline_home_win_prob calculated from a
   Pythagorean run estimator using SP ERA, team OPS, and park factor. Use this as your starting
   point for win_prob_pct. Adjust UP or DOWN by maximum 7% based on:
   - Recent form (HOT/DECLINING flags): ±3-5%
   - Bullpen fatigue differential: ±2-3%
   - Confirmed injuries to key players: ±1-2%
   Do NOT invent win_prob from scratch. Start from baseline_home_win_prob and adjust.
   For away team win prob: 100 - baseline_home_win_prob (then adjust).
3. NEVER bet ML worse than -180. Automatic SKIP regardless of edge.
4. NEVER use a total line you invented. Only use actual lines from the odds data provided.
5. No daily unit cap. EV threshold and scoring rubric are the only filters.
6. If SP edge favors Team A but you pick Team B ML, that is a contradiction. Fix it.
7. SKIP any game with status In Progress, Live, or Final.
8. NEVER recommend ML on a team with OPS below 0.700 — weak offenses cannot support ML bets.
9. Wind blowing IN never supports an OVER pick. Wind blowing OUT never supports an UNDER pick.
   If wind direction contradicts your pick direction, remove it as a supporting factor entirely.
10. Bullpen fatigue alone is NOT sufficient for a Tier A or MAX pick. It must combine with SP edge or park.

INJURIES — ZERO TOLERANCE FOR HALLUCINATION:
- injury_flags field MUST only contain names from home_team.injuries or away_team.injuries arrays.
- If those arrays are empty, write "None". Period. No exceptions.
- NEVER mention ANY player as out, injured, missing, or absent from memory or training data.
- Do not reference past injuries you know about. Only use what is in the provided injury arrays.
- The lineup_analysis field must also never mention injuries not in the provided arrays.
- flags field must never mention player injuries not in the provided arrays.
- Violating this creates false analysis that causes direct financial harm.

USING RECENT FORM (most predictive factor):
- recent_era shows last 3 starts ERA — this is the pitcher's TRUE current level.
- If recent ERA is 2+ runs higher than season ERA: DECLINING — reduce win prob significantly.
- If recent ERA is 2+ runs lower than season ERA: HOT — increase win prob.
- Always cite the specific recent ERA number, not just "declining" or "hot".
- SMALL SAMPLE flag means fewer than 4 starts — do NOT use recent form as a confirming factor.
  Early season (first 3 weeks), most pitchers will have SMALL SAMPLE. Do not award +2 points for
  recent form confirmation when the flag is present. Season ERA and splits are more reliable.

USING HOME/AWAY SPLITS:
- relevant_split shows the pitcher's ERA specifically for home or away — always use this.
- A pitcher with 2.50 home ERA but 4.80 away ERA is a completely different pitcher on the road.
- Do not use season ERA when a relevant split is available.

USING WIND IMPACT:
- wind_impact field is pre-calculated for each stadium's outfield orientation.
- "blowing OUT" = OVER lean only if wind_mph >= 12.
- "blowing IN" = UNDER lean only if wind_mph >= 12.
- Cold weather below 50F suppresses scoring regardless of wind direction.
- Crosswind = no meaningful impact on totals.

USING TEAM MOMENTUM (new):
- home_streak and away_streak show current W/L record and winning/losing streak.
- A team on a 5+ game winning streak is playing with confidence — slight edge to their ML.
- A team on a 5+ game losing streak has momentum against them — factor into ML picks.
- Early season (under 10 games): streaks are too small to be meaningful, ignore.
- Last 10 games record is more useful than overall record early in season.

USING BULLPEN FATIGUE:
- SEVERE (2+ arms 20+ pitches last 2 days): +2 points toward OVER or away from that team's ML.
- MODERATE (1 arm): +0 points — note it but do not factor into tier.
- FRESH: Supports UNDER or ML for that team.
- Both teams SEVERE fatigue: leans OVER but requires SP support to reach Tier A.

USING UMPIRE DATA:
- rpg above 9.2 = meaningful OVER lean (+1 point).
- rpg below 8.5 = meaningful UNDER lean (+1 point).
- League average is 8.8 — do not assign points for neutral umpires.

ML BET RULES (stricter than totals):
- Only recommend ML when: SP ERA gap 2.0+, AND team OPS above 0.750, AND odds -115 to -175.
- Never recommend ML solely based on opposing pitcher being bad — your team must have real edge.
- Run Line +1.5 is almost always better than ML when favorite is -180 or worse.

CONFIDENCE SCORING RUBRIC:
+3 pts: SP ERA gap 2.0+ clearly favoring one side (use relevant split, not season ERA)
+2 pts: Recent form confirms AND differs from season ERA by 1.0+ runs
+2 pts: Home/away split confirms by 0.75+ ERA gap
+2 pts: Opposing bullpen SEVERE fatigue (2+ arms)
+1 pt:  Park runs factor below 0.92 for UNDER or above 1.08 for OVER
+1 pt:  Umpire RPG below 8.5 for UNDER or above 9.2 for OVER
+1 pt:  Wind is a meaningful OVER factor: wind OUT 12+ mph AND temp above 60F
+1 pt:  Wind is a meaningful UNDER factor: wind IN 12+ mph AND temp below 55F
        Use effective_wind_lean field for context but apply judgment:
        - Wind is ONE factor. It does not override SP quality or bullpen data.
        - Wind OUT + cold (below 50F) = largely neutralized, give 0 points.
        - Wind IN + warm temps = weaker UNDER factor, give 0 points.
        - Always combine wind with at least one other confirming factor before counting it.
+1 pt:  Lineup OPS gap 0.100+ aligned with pick direction
+1 pt:  Plus money odds or better than -108

TIER ASSIGNMENT:
- MAX (3.0u): 10+ points AND EV 10%+ AND SP gap confirmed by both recent form AND split,
  SEVERE opposing bullpen, park+weather+umpire all aligned, odds no worse than -170.
  Expect 0-1 per WEEK. If even one condition missing, drop to Tier A.
- Tier A (1.5u): 8-9 points AND EV 7%+. Expect 0-1 per slate.
  TIER A REQUIRES: SP edge must be confirmed by AT LEAST TWO of: season ERA gap, recent form, home/away split.
  If only one SP factor supports the pick, maximum tier is B regardless of point total.
  If either SP has SMALL SAMPLE flag on recent form, do not count recent form as a confirming factor.
- Tier B (1.0u): 6-7 points AND EV 4%+. Expect 1-3 per slate.
- Tier C (0.5u): 4-5 points AND EV 3%+. Expect 1-3 per slate.
- WATCH (0u): 2-3 points OR EV 1-2%. Track only.
- SKIP: 1 point or less, contradictory factors, missing critical data, or game started.

Most games on any slate should be SKIP or WATCH. If you have more than 6 active picks
on a 12-game slate, your standards are too low.
TOTALS CAP: Maximum 3 total (OVER/UNDER) active picks per slate. If you have more, keep only
the 3 highest EV ones. Spread your picks across ML, Run Line, and Totals — do not default
entirely to game totals just because they are easier to justify.

BET TYPE:
- ML: SP gap 2.0+ AND team OPS 0.750+ AND odds -115 to -175.
- Run Line -1.5: Dominant favorite — SP gap 2.0+, fresh bullpen, strong lineup.
- Run Line +1.5: Overpriced favorite (-180+) with real underlying edge.
  WIN PROB FOR RUN LINES: +1.5 win prob should be 55-68% maximum. Do not assign 75%+ to
  any +1.5 pick — that implies near-certainty which doesn't exist in baseball. The underdog
  covers +1.5 roughly 60-65% of the time even in strong matchups. Be conservative.
  -1.5 win prob should be 45-60% maximum — winning by 2+ is much harder than winning outright.
  CRITICAL — RUN LINE PRICING: The game data contains a "run_line" dict keyed by TEAM NAME.
  Always read the price for the SPECIFIC TEAM you are betting. The underdog team +1.5 will
  always have a NEGATIVE price (e.g. -120 to -160). The favorite team +1.5 does NOT exist —
  only the underdog gets +1.5. Never assign a positive price to an underdog +1.5 pick or a
  negative price greater than -200 to a +1.5 pick. If the run_line data shows a team at
  +1.5 with a price worse than -200, that is a data error — skip the run line and use ML instead.
- Total OVER: Fatigued bullpens + hitter park OR out-blowing wind + warm temp.
- Total UNDER: Elite dual SPs + pitcher park + fresh pens. Wind IN adds to edge.
- F5 Total OVER/UNDER: Use when SP quality gap is strong but bullpen fatigue on one side creates uncertainty for full game. F5 isolates the SP edge.
- NRFI (No Run First Inning): Use when nrfi_data shows nrfi_prob above 65%. Both SPs must have K/9 above 8.5 AND BB/9 below 3.0. Park factor below 1.05. Pitcher-friendly ump. The nrfi_fair_price in the data is your baseline — if the book offers NRFI at a price that implies lower probability than our model, that is positive EV.
- YRFI (Yes Run First Inning): Use when nrfi_data shows yrfi_prob above 50% AND at least one SP has ERA above 5.0 OR BB/9 above 4.0. Hitter-friendly park adds edge. The yrfi_fair_price is your baseline.
- For NRFI/YRFI: use nrfi_fair_price as the "line" field and estimate EV by comparing to typical book price (-130 NRFI / -110 YRFI for average games). Only recommend when your model price shows 5%+ EV vs typical book pricing.
- NRFI/YRFI resolves in about 15 minutes — highest confidence picks only.
- Never skip solely because run line unavailable — use ML instead.
- F5 and NRFI/YRFI should be considered on EVERY game — they are often higher EV than full game bets.

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
  "tier": "MAX or A or B or C or WATCH or SKIP",
  "units": 1.0,
  "win_prob_pct": 58,
  "implied_prob_pct": 52,
  "ev_pct": 6,
  "sp_analysis": "season ERA/K9 + recent form ERA + relevant split ERA — cite specific numbers",
  "lineup_analysis": "team OPS values + platoon note. NO injury mentions unless in injury arrays.",
  "bullpen_note": "fatigue level for each team with arm count and pitch counts",
  "injury_flags": "ONLY names from home_team.injuries or away_team.injuries arrays. If empty: None",
  "umpire_note": "rpg + k_pct + lean direction or Neutral if near 8.8 rpg",
  "park_note": "runs factor + HR factor + note",
  "weather_impact": "exact wind_impact field value + temp",
  "nrfi_analysis": "nrfi_prob% vs yrfi_prob% — cite specific K/9, BB/9, park factor. Only include if recommending NRFI/YRFI.",
  "key_edge": "single most important reason with specific numbers",
  "rationale": "3 sentences: primary edge with stats. Supporting factors. Why this bet type.",
  "avoid_reason": "if SKIP/WATCH: specific reason. Empty string otherwise.",
  "flags": "SP changes or rain 40%+. No injury flags unless confirmed in injury arrays."
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
            timeout=180
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

        # Auto-SKIP if either SP has no stats
        sp_analysis = p.get("sp_analysis","").lower()
        if tier in ("MAX","A","B","C") and (
            "no stats" in sp_analysis or
            "0.00 era placeholder" in sp_analysis or
            "no stats found" in sp_analysis
        ):
            print("AUTO-SKIP: "+p.get("game","")+" — SP has no stats, cannot make valid pick")
            p["tier"] = "SKIP"; p["bet_type"] = "SKIP"; p["pick"] = "SKIP"; p["units"] = 0
            p["avoid_reason"] = "SP has no statistical data — cannot make valid pick"
            enforced.append(p)
            continue

        # Auto-downgrade if flags explicitly say factors contradict pick direction
        if tier in ("B","C"):
            flags_lower = (p.get("flags","") + " " + p.get("rationale","")).lower()
            pick_str = p.get("pick","").upper()
            is_under = "UNDER" in pick_str
            is_over = "OVER" in pick_str
            contradictions = 0
            if is_under and "contradicts under" in flags_lower: contradictions += 1
            if is_over and "contradicts over" in flags_lower: contradictions += 1
            if contradictions >= 1:
                print("CONTRADICTION: "+p.get("game","")+" — contradicting factors, downgrading to WATCH")
                p["tier"] = "WATCH"; p["units"] = 0

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
        # Sanity cap — caps vary by bet type
        # Run lines have tighter cap because win_prob for +1.5 is easier to inflate
        max_ev = 10.0 if "Run Line" in bet_type else 15.0
        if ev > max_ev:
            print("EV sanity cap: "+p.get("game","")+" claimed "+str(ev)+"% EV — capping at "+str(max_ev)+"%")
            ev = max_ev
            p["ev_pct"] = max_ev

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

        # Fix tier/units alignment — enforce standard unit sizes
        ev_val = p.get("ev_pct",0)
        try: ev_val = float(ev_val)
        except: ev_val = 0
        # Validate MAX tier — strict data quality requirements
        if p["tier"] == "MAX":
            reasons = []
            if ev_val < 10:
                reasons.append("EV "+str(ev_val)+"% below 10%")
            sp = p.get("sp_analysis","").lower()
            if "no stats" in sp or "0.00 era placeholder" in sp:
                reasons.append("SP missing stats")
            lineup = p.get("lineup_analysis","").lower()
            if "0.000" in lineup or "missing data" in lineup or "unavailable" in lineup:
                reasons.append("OPS data missing")
            if sp.count("small sample") >= 2:
                reasons.append("both SPs SMALL SAMPLE")
            if reasons:
                print("MAX downgrade ("+p.get("game","")+") — "+", ".join(reasons))
                p["tier"] = "A"
        if p["tier"] == "A" and ev_val < 7:
            p["tier"] = "B" if ev_val >= 4 else ("C" if ev_val >= 3 else "WATCH")
        # Always enforce correct unit size regardless of what Claude said
        if p["tier"] == "MAX": p["units"] = 3.0
        elif p["tier"] == "A": p["units"] = 1.5
        elif p["tier"] == "B": p["units"] = 1.0
        elif p["tier"] == "C": p["units"] = 0.5
        elif p["tier"] in ("WATCH","SKIP"): p["units"] = 0

        # Validate run line price matches actual odds data
        if bet_type == "Run Line" and p.get("game"):
            game_key = p.get("game","").replace(" @ ","@")
            # Find the game in the original summarized data
            pick_str = p.get("pick","").upper()
            stated_line = str(p.get("line","")).replace("+","")
            try:
                stated_price = float(stated_line)
                # If price is more negative than -300 on +1.5, that's almost certainly wrong
                # Underdog +1.5 should never be more than -200
                if "+1.5" in pick_str and stated_price < -200:
                    print("LINE ERROR: "+p.get("game","")+" — "+pick_str+" showing "+str(p.get("line",""))+" which is impossible for underdog +1.5. Downgrading to WATCH.")
                    p["tier"] = "WATCH"
                    p["units"] = 0
                    p["avoid_reason"] = "Line validation failed — price inconsistent with run line direction"
                # Favorite -1.5 should never be plus money
                if "-1.5" in pick_str and stated_price > 0:
                    print("LINE ERROR: "+p.get("game","")+" — "+pick_str+" showing +"+str(p.get("line",""))+" which is impossible for favorite -1.5. Downgrading to WATCH.")
                    p["tier"] = "WATCH"
                    p["units"] = 0
                    p["avoid_reason"] = "Line validation failed — price inconsistent with run line direction"
            except: pass

        enforced.append(p)

    # No daily unit cap — EV and scoring rubric are the only filters

    # Clean up stale cap messages from avoid_reason
    for p in enforced:
        ar = p.get("avoid_reason","")
        if "[Daily 5u cap reached]" in str(ar):
            p["avoid_reason"] = str(ar).replace(" [Daily 5u cap reached]","").strip()
        if p.get("avoid_reason","") == "":
            p["avoid_reason"] = ""

    return enforced

def estimate_win_prob(home_sp_era, away_sp_era, home_ops, away_ops,
                      park_runs, home_recent_era=None, away_recent_era=None):
    """
    Estimate home team win probability using Pythagorean run expectation.
    Uses recent ERA when available as it's more predictive than season ERA.
    Claude adjusts this baseline by max ±7% based on qualitative factors.
    """
    lg_era = 4.20; lg_ops = 0.720; lg_runs_pg = 4.5

    # Use recent ERA if available and meaningful (not zero)
    h_era = home_recent_era if home_recent_era and home_recent_era > 0 else home_sp_era
    a_era = away_recent_era if away_recent_era and away_recent_era > 0 else away_sp_era

    # Cap ERAs to prevent extreme distortion from tiny samples
    h_era = min(max(h_era, 1.0), 9.0)
    a_era = min(max(a_era, 1.0), 9.0)
    # Use league average when OPS is 0 (missing data) — never use 0 directly
    h_ops = min(max(home_ops, 0.550), 1.000) if home_ops and home_ops > 0.100 else lg_ops
    a_ops = min(max(away_ops, 0.550), 1.000) if away_ops and away_ops > 0.100 else lg_ops
    pf = min(max(park_runs, 0.80), 1.30)

    # Expected runs per game for each team
    home_runs = lg_runs_pg * (a_era / lg_era) * (h_ops / lg_ops) * pf * 1.03  # home advantage
    away_runs = lg_runs_pg * (h_era / lg_era) * (a_ops / lg_ops) * pf

    # Pythagorean expectation (Davenport exponent 1.83)
    exp = 1.83
    if home_runs <= 0 or away_runs <= 0:
        return 54.0  # fallback to slight home advantage
    home_win_pct = home_runs**exp / (home_runs**exp + away_runs**exp)
    return round(home_win_pct * 100, 1)

def estimate_nrfi_odds(away_sp_stats, home_sp_stats, park_factor, game_total):
    """
    Estimate fair NRFI/YRFI odds from SP stats and game total.
    
    Key factors:
    - Both SP K/9 (higher = fewer baserunners = more NRFI lean)
    - Both SP BB/9 (higher = more baserunners = YRFI lean)  
    - Both SP ERA (lower = fewer runs = NRFI lean)
    - Park factor (above 1.05 = hitter friendly = YRFI lean)
    - Game total (higher total = more runs expected = YRFI lean)
    
    Returns dict with nrfi_prob, yrfi_prob, nrfi_price, yrfi_price, edge
    """
    # League averages for calibration
    lg_k9 = 8.8; lg_bb9 = 3.2; lg_era = 4.20
    lg_nrfi_pct = 0.57  # ~57% of innings are scoreless first innings historically

    # SP quality scores (higher = better for NRFI)
    def sp_nrfi_score(stats):
        era = stats.get("era", lg_era) or lg_era
        k9  = stats.get("k9", lg_k9) or lg_k9
        bb9 = stats.get("bb9", lg_bb9) or lg_bb9
        era = min(max(era, 1.0), 9.0)
        # Score: low ERA + high K/9 + low BB/9 = good NRFI pitcher
        era_factor = (lg_era / era) ** 0.4
        k9_factor  = (k9 / lg_k9) ** 0.3
        bb9_factor = (lg_bb9 / max(bb9, 0.5)) ** 0.3
        return era_factor * k9_factor * bb9_factor

    away_score = sp_nrfi_score(away_sp_stats) if away_sp_stats.get("era",0) > 0 else 1.0
    home_score = sp_nrfi_score(home_sp_stats) if home_sp_stats.get("era",0) > 0 else 1.0

    # Combined SP quality (geometric mean)
    combined_sp = (away_score * home_score) ** 0.5

    # Park adjustment
    pf = park_factor if park_factor else 1.0
    park_adj = 1.0 / pf  # hitter park = lower NRFI prob

    # Game total adjustment (higher total = lower NRFI prob)
    total = game_total if game_total and game_total > 0 else 8.5
    total_adj = 8.5 / total  # normalized to average total

    # Base NRFI probability
    nrfi_prob = lg_nrfi_pct * combined_sp * park_adj * total_adj

    # Clamp to reasonable range
    nrfi_prob = min(max(nrfi_prob, 0.30), 0.80)
    yrfi_prob = 1.0 - nrfi_prob

    # Convert to American odds
    def prob_to_american(p):
        if p >= 0.5:
            return round(-(p / (1 - p)) * 100)
        else:
            return round(((1 - p) / p) * 100)

    nrfi_fair = prob_to_american(nrfi_prob)
    yrfi_fair = prob_to_american(yrfi_prob)

    return {
        "nrfi_prob": round(nrfi_prob * 100, 1),
        "yrfi_prob": round(yrfi_prob * 100, 1),
        "nrfi_fair_price": nrfi_fair,
        "yrfi_fair_price": yrfi_fair,
        "away_sp_nrfi_score": round(away_score, 3),
        "home_sp_nrfi_score": round(home_score, 3),
    }

def summarize_game(g):
    """Compress game data to key numbers only — keeps prompt size manageable."""
    home_sp = g.get("home_sp_stats",{})
    away_sp = g.get("away_sp_stats",{})
    home_bp = g.get("home_bullpen_fatigue",{})
    away_bp = g.get("away_bullpen_fatigue",{})
    home_bat = g.get("home_team_batting",{})
    away_bat = g.get("away_team_batting",{})
    home_pit = g.get("home_team_pitching",{})
    away_pit = g.get("away_team_pitching",{})
    ump = g.get("ump_stats",{})
    park = g.get("park_factor",{})
    weather = g.get("weather",{})
    odds = g.get("odds",{})
    home_rec = home_sp.get("recent_form",{})
    away_rec = away_sp.get("recent_form",{})

    return {
        "game": g["away"]+" @ "+g["home"],
        "venue": g.get("venue",""),
        "game_time": g.get("game_time",""),
        "status": g.get("status",""),
        "live_score": g.get("live_score"),
        "hp_ump": g.get("hp_ump",""),
        "away_sp": g["away_sp"],
        "home_sp": g["home_sp"],
        "away_sp_stats": {
            "era": away_sp.get("era",0),
            "k9": away_sp.get("k9",0),
            "bb9": away_sp.get("bb9",0),
            "whip": away_sp.get("whip",0),
            "note": away_sp.get("note",""),
            "form_flag": away_sp.get("form_flag",""),
            "relevant_split": away_sp.get("relevant_split",""),
            "recent_era": away_rec.get("era_last3",0),
            "recent_k9": away_rec.get("k9_last3",0),
            "recent_starts": away_rec.get("starts",0),
        },
        "home_sp_stats": {
            "era": home_sp.get("era",0),
            "k9": home_sp.get("k9",0),
            "bb9": home_sp.get("bb9",0),
            "whip": home_sp.get("whip",0),
            "note": home_sp.get("note",""),
            "form_flag": home_sp.get("form_flag",""),
            "relevant_split": home_sp.get("relevant_split",""),
            "recent_era": home_rec.get("era_last3",0),
            "recent_k9": home_rec.get("k9_last3",0),
            "recent_starts": home_rec.get("starts",0),
        },
        "away_team": {
            "ops": away_bat.get("ops",0),
            "runs_pg": away_bat.get("runs_per_game",0),
            "bullpen_era": away_pit.get("team_era",0),
            "bullpen_fatigue": away_bp.get("fatigue_level","UNKNOWN"),
            "fatigued_arms": away_bp.get("fatigued_arms",[]),
            "injuries": [i["name"]+" ("+i["pos"]+")" for i in g.get("away_injuries",[])[:3]],
        },
        "home_team": {
            "ops": home_bat.get("ops",0),
            "runs_pg": home_bat.get("runs_per_game",0),
            "bullpen_era": home_pit.get("team_era",0),
            "bullpen_fatigue": home_bp.get("fatigue_level","UNKNOWN"),
            "fatigued_arms": home_bp.get("fatigued_arms",[]),
            "injuries": [i["name"]+" ("+i["pos"]+")" for i in g.get("home_injuries",[])[:3]],
        },
        "umpire": {
            "name": ump.get("name",""),
            "rpg": ump.get("rpg",8.8),
            "k_pct": ump.get("k_pct",0.22),
            "note": ump.get("note",""),
        },
        "park": {
            "runs": park.get("runs",1.0),
            "hr": park.get("hr",1.0),
            "note": park.get("note",""),
        },
        "weather": {
            "temp_f": weather.get("temp_f",""),
            "wind_mph": weather.get("wind_mph",""),
            "wind_dir": weather.get("wind_dir",""),
            "precip_pct": weather.get("precip_pct",0),
            "wind_impact": weather.get("wind_impact",""),
            "effective_wind_lean": effective_wind_lean(
                weather.get("wind_impact",""),
                weather.get("temp_f","72")
            ),
        },
        "odds": {
            "ml_away": odds.get("moneyline",{}).get(g["away"],""),
            "ml_home": odds.get("moneyline",{}).get(g["home"],""),
            "total_line": odds.get("total",{}).get("line",""),
            "total_over": odds.get("total",{}).get("over",""),
            "total_under": odds.get("total",{}).get("under",""),
            "f5_line": odds.get("f5_total",{}).get("line",""),
            "f5_over": odds.get("f5_total",{}).get("over",""),
            "f5_under": odds.get("f5_total",{}).get("under",""),
            "run_line": {
                g["away"]: {
                    "price": odds.get("runline",{}).get(g["away"],{}).get("price",""),
                    "point": odds.get("runline",{}).get(g["away"],{}).get("point",""),
                },
                g["home"]: {
                    "price": odds.get("runline",{}).get(g["home"],{}).get("price",""),
                    "point": odds.get("runline",{}).get(g["home"],{}).get("point",""),
                },
            },
            "has_odds": bool(odds.get("moneyline") or odds.get("total")),
        },
        "platoon": {
            "home": g.get("home_platoon",{}).get("platoon_note",""),
            "away": g.get("away_platoon",{}).get("platoon_note",""),
        },
        "home_streak": g.get("home_streak",{}),
        "away_streak": g.get("away_streak",{}),
        "nrfi_data": estimate_nrfi_odds(
            g.get("away_sp_stats",{}),
            g.get("home_sp_stats",{}),
            g.get("park_factor",{}).get("runs", 1.0),
            safe_float(odds.get("total",{}).get("line", 0)),
        ),
        "baseline_home_win_prob": estimate_win_prob(
            home_sp.get("era", 4.20) or 4.20,
            away_sp.get("era", 4.20) or 4.20,
            g.get("home_team_batting",{}).get("ops") or 0.720,
            g.get("away_team_batting",{}).get("ops") or 0.720,
            g.get("park_factor",{}).get("runs", 1.0) or 1.0,
            home_rec.get("era_last3", 0) or 0,
            away_rec.get("era_last3", 0) or 0,
        ),
    }

def call_ai(games_with_data):
    # Filter out games with no odds — nothing to bet on
    bettable = [g for g in games_with_data if g.get("odds",{}).get("moneyline") or g.get("odds",{}).get("total")]
    no_odds = [g for g in games_with_data if g not in bettable]
    if no_odds:
        print("Skipping "+str(len(no_odds))+" games with no odds: "+", ".join(g["away"]+" @ "+g["home"] for g in no_odds))
    n = len(bettable)
    if n == 0:
        return [], "None"
    summarized = [summarize_game(g) for g in bettable]

    # Split into batches of 8 to stay within token limits
    BATCH_SIZE = 8
    all_picks = []
    model_used = "None"

    for i in range(0, n, BATCH_SIZE):
        batch = summarized[i:i+BATCH_SIZE]
        b_n = len(batch)
        print("Processing batch "+str(i//BATCH_SIZE+1)+"/"+str((n+BATCH_SIZE-1)//BATCH_SIZE)+" ("+str(b_n)+" games)...")

        user_msg = (
            "Today is "+TODAY+". Analyze these "+str(b_n)+" MLB games.\n"
            "Use ALL provided data: SP season stats, recent form, home/away splits, "
            "platoon matchups, bullpen fatigue, injuries, umpire tendencies, park factors, wind impact, odds.\n"
            "Return exactly "+str(b_n)+" entries. Raw JSON array only.\n\n"
            "GAMES:\n"+json.dumps(batch, indent=2)
        )

        picks, model = _try_claude(user_msg)
        if picks is None:
            print("Claude failed batch, trying Groq...")
            picks, model = _try_groq(user_msg)
        if picks is None:
            print("Both failed for batch "+str(i//BATCH_SIZE+1))
            picks = []
        
        model_used = model or model_used
        all_picks.extend(picks)

    if all_picks:
        all_picks = enforce_ev_rules(all_picks)

    # Hard cap: no more than 3 total (OVER/UNDER) active picks per slate
    # Prevents model from defaulting entirely to totals
    total_picks = [p for p in all_picks if p.get("tier") in ("MAX","A","B","C")
                   and "OVER" in p.get("bet_type","").upper() or "UNDER" in p.get("bet_type","").upper()]
    if len([p for p in all_picks if p.get("tier") in ("MAX","A","B","C")
            and ("OVER" in p.get("bet_type","").upper() or "UNDER" in p.get("bet_type","").upper())]) > 3:
        # Keep highest EV totals up to 3, downgrade rest to WATCH
        active_totals = sorted(
            [p for p in all_picks if p.get("tier") in ("MAX","A","B","C")
             and ("OVER" in p.get("bet_type","").upper() or "UNDER" in p.get("bet_type","").upper())],
            key=lambda x: x.get("ev_pct",0), reverse=True
        )
        keep = {id(p) for p in active_totals[:3]}
        for p in all_picks:
            if (p.get("tier") in ("MAX","A","B","C")
                and ("OVER" in p.get("bet_type","").upper() or "UNDER" in p.get("bet_type","").upper())
                and id(p) not in keep):
                p["tier"] = "WATCH"
                p["units"] = 0
                p["avoid_reason"] = "Downgraded: total pick cap (max 3 totals per slate)"
                print("CAPPED: "+p.get("game","")+" — downgraded to WATCH (total pick cap)")

    # Add silent SKIPs for no-odds games (don't show on page)
    for g in no_odds:
        all_picks.append({
            "game": g["away"]+" @ "+g["home"],
            "venue": g.get("venue",""),
            "game_time": g.get("game_time",""),
            "status": g.get("status",""),
            "live_score": g.get("live_score"),
            "away_sp": g.get("away_sp",""),
            "home_sp": g.get("home_sp",""),
            "hp_ump": g.get("hp_ump",""),
            "bet_type": "SKIP",
            "pick": "SKIP",
            "line": "N/A",
            "tier": "SKIP",
            "units": 0,
            "win_prob_pct": 0,
            "implied_prob_pct": 0,
            "ev_pct": 0,
            "sp_analysis": "",
            "lineup_analysis": "",
            "bullpen_note": "",
            "injury_flags": "None",
            "umpire_note": "",
            "park_note": "",
            "weather_impact": "",
            "key_edge": "",
            "rationale": "",
            "avoid_reason": "No odds data available",
            "flags": "",
            "no_display": True,  # flag to hide from page
        })

    return all_picks, model_used

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
            # First inning scores for NRFI/YRFI settlement
            inn1_home = int(innings[0].get("home",{}).get("runs",0) or 0) if innings else 0
            inn1_away = int(innings[0].get("away",{}).get("runs",0) or 0) if innings else 0
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
                "inn1_home": inn1_home,
                "inn1_away": inn1_away,
                "inn1_total": inn1_home + inn1_away,
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

        elif bet_type == "NRFI":
            inn1 = score.get("inn1_total", None)
            if inn1 is None: return None
            result = "W" if inn1 == 0 else "L"

        elif bet_type == "YRFI":
            inn1 = score.get("inn1_total", None)
            if inn1 is None: return None
            result = "W" if inn1 > 0 else "L"

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

def fetch_closing_lines():
    """Fetch current odds to use as closing lines for settled games."""
    if not ODDS_API_KEY: return {}
    try:
        r = requests.get(
            "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/",
            params={
                "apiKey":ODDS_API_KEY,"regions":"us",
                "markets":"h2h,totals","oddsFormat":"american","dateFormat":"iso",
                "bookmakers":"draftkings,fanduel",
            },
            timeout=10
        )
        if not r.ok: return {}
        lines = {}
        for event in r.json():
            home = normalize_team(event.get("home_team",""))
            away = normalize_team(event.get("away_team",""))
            key = away+" @ "+home
            bms = event.get("bookmakers",[])
            for bm in bms:
                for market in bm.get("markets",[]):
                    if market["key"] == "h2h":
                        for o in market.get("outcomes",[]):
                            lines[key+"_ML_"+o["name"]] = o["price"]
                    elif market["key"] == "totals":
                        for o in market.get("outcomes",[]):
                            lines[key+"_"+o["name"]+"_"+str(o.get("point",""))] = o["price"]
        return lines
    except:
        return {}

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
    # Fetch current odds as closing lines before settling
    closing_lines = fetch_closing_lines()

    for i, pick in enumerate(record["picks"]):
        if pick.get("result") or pick.get("tier") == "SKIP":
            continue

        # Auto-fill closing line if not already set
        if not pick.get("close_line") and closing_lines:
            game = pick.get("game","")
            bet_type = pick.get("bet_type","")
            pick_str = pick.get("pick","").upper()
            cl = ""
            if "OVER" in bet_type or "UNDER" in bet_type:
                direction = "Over" if "OVER" in bet_type else "Under"
                # Find total line from pick string
                import re
                nums = re.findall(r"[0-9]+\.?[0-9]*", pick_str)
                if nums:
                    cl_key = game+"_"+direction+"_"+nums[-1]
                    if cl_key in closing_lines:
                        cl = str(closing_lines[cl_key])
            elif "ML" in bet_type:
                for team in game.split(" @ "):
                    cl_key = game+"_ML_"+team
                    if cl_key in closing_lines:
                        cl = str(closing_lines[cl_key])
                        break
            if cl:
                record["picks"][i]["close_line"] = cl

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

RECORD_LIVE_JS = """
<script>
// Live record settlement — updates W/L display when games go Final
// Reads pick data embedded in the page and checks MLB API for final scores

var PICK_DATA = [];

function parsePicksFromPage() {
    // Extract pick data from table rows
    var rows = document.querySelectorAll("tbody tr");
    rows.forEach(function(row, idx) {
        var cells = row.querySelectorAll("td");
        if (cells.length < 9) return;
        var pick = cells[1] ? cells[1].textContent.trim() : "";
        var game = cells[2] ? cells[2].textContent.trim() : "";
        var tier = cells[3] ? cells[3].textContent.trim() : "";
        var line = cells[4] ? cells[4].textContent.trim() : "";
        var resultCell = cells[8];
        var unitsCell = cells[9];
        var resultSpan = resultCell ? resultCell.querySelector("span") : null;
        if (resultSpan && resultSpan.textContent.trim() === "PENDING") {
            PICK_DATA.push({
                idx: idx,
                pick: pick,
                game: game,
                tier: tier,
                line: line,
                resultSpan: resultSpan,
                unitsCell: unitsCell,
                row: row
            });
        }
    });
}

function americanToDecimal(odds) {
    odds = parseFloat(odds);
    if (isNaN(odds)) return 1.909; // default -110
    if (odds < 0) return 1 + (100 / Math.abs(odds));
    return 1 + (odds / 100);
}

function calcUnitsResult(result, line, units) {
    units = parseFloat(units) || 1.0;
    if (result === "W") {
        var dec = americanToDecimal(line);
        return Math.round((units * (dec - 1)) * 100) / 100;
    } else if (result === "L") {
        return -units;
    }
    return 0;
}

function settlePick(pick, game, line, scores) {
    // Parse game string "AWAY @ HOME"
    var parts = game.split(" @ ");
    if (parts.length !== 2) return null;
    var away = parts[0].trim();
    var home = parts[1].trim();
    
    // Find score
    var score = null;
    for (var key in scores) {
        if (key.indexOf(away) >= 0 && key.indexOf(home) >= 0) {
            score = scores[key];
            break;
        }
    }
    if (!score) return null;
    
    var awayScore = score.away_score;
    var homeScore = score.home_score;
    var total = awayScore + homeScore;
    var pickUp = pick.toUpperCase();
    
    // Total OVER/UNDER
    if (pickUp.indexOf("OVER") >= 0 || pickUp.indexOf("UNDER") >= 0) {
        var lineMatch = pick.match(/[0-9.]+/);
        if (!lineMatch) return null;
        var lineNum = parseFloat(lineMatch[0]);
        if (total > lineNum) return pickUp.indexOf("OVER") >= 0 ? "W" : "L";
        if (total < lineNum) return pickUp.indexOf("UNDER") >= 0 ? "W" : "L";
        return "P";
    }
    
    // ML
    if (pickUp.indexOf("ML") >= 0) {
        var winner = homeScore > awayScore ? home : away;
        for (var team of [away, home]) {
            if (pickUp.indexOf(team.toUpperCase().split(" ").pop()) >= 0) {
                return winner === team ? "W" : "L";
            }
        }
    }
    
    // Run Line +1.5 / -1.5
    if (pickUp.indexOf("+1.5") >= 0 || pickUp.indexOf("-1.5") >= 0) {
        var spread = pickUp.indexOf("+1.5") >= 0 ? 1.5 : -1.5;
        for (var team of [away, home]) {
            if (pickUp.indexOf(team.toUpperCase().split(" ").pop()) >= 0) {
                var teamScore = team === home ? homeScore : awayScore;
                var oppScore = team === home ? awayScore : homeScore;
                var adjusted = teamScore - oppScore + spread;
                if (adjusted > 0) return "W";
                if (adjusted < 0) return "L";
                return "P";
            }
        }
    }

    // NRFI / YRFI — uses inning1 from linescore hydration
    if (pickUp === "NRFI" || pickUp === "YRFI") {
        if (!score.inning1) return null;
        var inn1away = score.inning1.away || 0;
        var inn1home = score.inning1.home || 0;
        var firstInningRuns = inn1away + inn1home;
        if (pickUp === "NRFI") return firstInningRuns === 0 ? "W" : "L";
        if (pickUp === "YRFI") return firstInningRuns > 0 ? "W" : "L";
    }

    return null;
}

function updateRecordDisplay(scores) {
    var wins = 0, losses = 0, pushes = 0, totalUnits = 0;

    // First count already-settled picks from server (non-WATCH, non-PENDING)
    document.querySelectorAll("tbody tr").forEach(function(row) {
        var cells = row.querySelectorAll("td");
        if (cells.length < 10) return;
        var tier = cells[3] ? cells[3].textContent.trim() : "";
        var resultSpan = cells[8] ? cells[8].querySelector("span") : null;
        var unitsCell = cells[9];
        if (!resultSpan) return;
        var existingResult = resultSpan.textContent.trim();
        if (existingResult !== "WIN" && existingResult !== "LOSS" && existingResult !== "PUSH") return;
        if (tier === "WATCH") return; // WATCH picks don't count
        var unitsText = unitsCell ? unitsCell.textContent.trim() : "0u";
        var unitsVal = parseFloat(unitsText.replace("u","")) || 0;
        if (existingResult === "WIN") { wins++; totalUnits += unitsVal; }
        else if (existingResult === "LOSS") { losses++; totalUnits += unitsVal; }
        else pushes++;
    });

    PICK_DATA.forEach(function(pd) {
        var result = settlePick(pd.pick, pd.game, pd.line, scores);
        if (!result) return;

        var isWatch = pd.tier === "WATCH";
        var units = isWatch ? 0 : parseFloat(pd.tier === "A" ? 1.5 : pd.tier === "MAX" ? 3.0 : pd.tier === "C" ? 0.5 : 1.0);
        var unitsResult = isWatch ? 0 : calcUnitsResult(result, pd.line, units);

        // Update result cell
        var color = result === "W" ? "#1D9E75" : result === "L" ? "#A32D2D" : "#888";
        pd.resultSpan.textContent = result === "W" ? "WIN" : result === "L" ? "LOSS" : "PUSH";
        pd.resultSpan.style.background = color + "22";
        pd.resultSpan.style.color = color;

        // Update units cell
        if (pd.unitsCell) {
            pd.unitsCell.textContent = (unitsResult >= 0 ? "+" : "") + unitsResult + "u";
            pd.unitsCell.style.color = color;
        }

        // Update score cell if available
        var scoreCell = pd.row.querySelector("td:nth-child(8)");
        if (scoreCell && scores[pd.game]) {
            var s = scores[pd.game];
            var parts = pd.game.split(" @ ");
            scoreCell.textContent = parts[0] + " " + s.away_score + " - " + parts[1] + " " + s.home_score;
        }

        // Count (non-WATCH only, not already counted from server)
        if (!isWatch) {
            if (result === "W") { wins++; totalUnits += unitsResult; }
            else if (result === "L") { losses++; totalUnits += unitsResult; }
            else pushes++;
        }

        // Highlight row
        pd.row.style.background = result === "W" ? "#f0fff8" : result === "L" ? "#fff0f0" : "";
    });

    // Update summary stats
    var total = wins + losses + pushes;
    if (total > 0) {
        var wrEl = document.querySelector(".sn[data-stat=winrate]");
        var wlEl = document.querySelector(".sn[data-stat=wl]");
        var uEl  = document.querySelector(".sn[data-stat=units]");
        if (wrEl) wrEl.textContent = Math.round(wins/total*100) + "%";
        if (wlEl) wlEl.textContent = wins + "-" + losses;
        if (uEl) {
            uEl.textContent = (totalUnits >= 0 ? "+" : "") + Math.round(totalUnits*100)/100 + "u";
            uEl.style.color = totalUnits >= 0 ? "#1D9E75" : "#A32D2D";
        }
    }
    
    // Update last refreshed
    var lu = document.getElementById("live_update");
    if (lu) lu.textContent = "Live results updated " + new Date().toLocaleTimeString("en-US", {timeZone:"America/New_York",hour:"numeric",minute:"2-digit"}) + " ET";
}

function fetchScoresForRecord() {
    // Get unique dates from picks
    var dates = new Set();
    PICK_DATA.forEach(function(pd) {
        // Extract date from row
        var dateCell = pd.row.querySelector("td:first-child");
        if (dateCell) dates.add(dateCell.textContent.trim());
    });
    
    // Also always check today
    dates.add(new Date().toLocaleDateString("en-CA", {timeZone:"America/New_York"}));
    
    var allScores = {};
    var promises = [];
    
    dates.forEach(function(date) {
        if (!date || date.length < 8) return;
        var p = fetch("https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=" + date + "&hydrate=linescore,team")
            .then(function(r) { return r.json(); })
            .then(function(data) {
                (data.dates || []).forEach(function(d) {
                    (d.games || []).forEach(function(g) {
                        if (g.status.abstractGameState !== "Final") return;
                        var away = g.teams.away.team.name;
                        var home = g.teams.home.team.name;
                        // Parse first inning from linescore
                        var inning1 = null;
                        var innings = (g.linescore && g.linescore.innings) ? g.linescore.innings : [];
                        if (innings.length > 0) {
                            inning1 = {
                                away: innings[0].away ? (innings[0].away.runs || 0) : 0,
                                home: innings[0].home ? (innings[0].home.runs || 0) : 0
                            };
                        }
                        allScores[away + " @ " + home] = {
                            away_score: g.teams.away.score || 0,
                            home_score: g.teams.home.score || 0,
                            inning1: inning1
                        };
                    });
                });
            }).catch(function() {});
        promises.push(p);
    });
    
    Promise.all(promises).then(function() {
        if (Object.keys(allScores).length > 0) {
            updateRecordDisplay(allScores);
        }
    });
}

// Initialize on page load
document.addEventListener("DOMContentLoaded", function() {
    parsePicksFromPage();
    if (PICK_DATA.length > 0) {
        fetchScoresForRecord();
        // Refresh every 2 minutes
        setInterval(fetchScoresForRecord, 120000);
    }
});
</script>
"""

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
        uc = "var(--green)" if u>=0 else "var(--red)"
        tier_colors = {"MAX":"var(--gold)","A":"var(--green)","B":"var(--blue)","C":"var(--purple)","WATCH":"var(--muted)"}
        dot = ('<span class="tier-dot '+label+'"></span>') if label in tier_colors else ""
        return ('<tr>'
                '<td style="font-weight:600">'+dot+label+'</td>'
                '<td style="text-align:center;font-family:\'DM Mono\',monospace">'+str(w)+'-'+str(l)+(('-'+str(p)) if p else '')+'</td>'
                '<td style="text-align:center">'+str(wr)+'%</td>'
                '<td style="text-align:center;font-family:\'DM Mono\',monospace;font-weight:600;color:'+uc+'">'
                +('+'if u>=0 else '')+str(u)+'u</td></tr>')

    def pick_row(p):
        res = p.get("result","")
        ur  = p.get("units_result",0)
        if res=="W": rl="WIN"
        elif res=="L": rl="LOSS"
        elif res=="P": rl="PUSH"
        else: rl="PENDING"
        t = p.get("tier","?")
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
        final_score = p.get("final_score","")
        # WATCH picks always show 0u
        if t == "WATCH":
            ur = 0
        ur_color = "var(--green)" if ur>=0 else "var(--red)"
        clv_color = "var(--green)" if clv_str.startswith('+') else "var(--red)" if clv_str.startswith('-') else "var(--muted)"
        tier_dot = '<span class="tier-dot '+t+'"></span>' if t in ("MAX","A","B","C","WATCH") else ""
        return ('<tr>'
                '<td style="color:var(--muted);font-family:\'DM Mono\',monospace;font-size:11px">'+p.get("date","")+'</td>'
                '<td style="font-weight:600">'+p.get("pick","")+'</td>'
                '<td style="color:var(--muted);font-size:11px">'+p.get("game","")+'</td>'
                '<td>'+tier_dot+t+'</td>'
                '<td style="font-family:\'DM Mono\',monospace;font-size:11px">'+str(open_l)+'</td>'
                '<td style="font-family:\'DM Mono\',monospace;font-size:11px;color:var(--muted)">'+str(close_l)+'</td>'
                '<td style="font-family:\'DM Mono\',monospace;font-size:11px;color:'+clv_color+'">'+clv_str+'</td>'
                '<td style="font-size:11px;color:var(--muted)">'+str(final_score)+'</td>'
                '<td><span class="badge '+rl+'">'+rl+'</span></td>'
                '<td style="font-family:\'DM Mono\',monospace;font-weight:600;color:'+ur_color+'">'
                +('+'if ur>=0 else '')+str(round(ur,2))+'u</td></tr>')

    tier_rows = "".join(stat_row(t,d) for t,d in sorted(tiers.items()))
    bt_rows   = "".join(stat_row(bt,d) for bt,d in sorted(bet_types.items()))
    pick_rows = "".join(pick_row(p) for p in reversed(picks[-60:]))

    u_color = "#1D9E75" if units_won>=0 else "#A32D2D"
    u_str   = ("+" if units_won>=0 else "")+str(units_won)+"u"
    clv_color = "#1D9E75" if avg_clv>0 else "#A32D2D" if avg_clv<0 else "#888"

    rec_css = (
        '@import url("https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Syne:wght@700;800&family=DM+Sans:wght@400;500;600&display=swap");'
        '*{box-sizing:border-box;margin:0;padding:0}'
        ':root{--bg:#0D0F11;--surface:#151719;--surface2:#1C1F22;--border:#272B2F;'
        '--text:#F0F2F4;--muted:#8A9099;--faint:#3A3F45;'
        '--gold:#F5C842;--green:#22C87A;--red:#F04B4B;--blue:#4B9CF5;--purple:#9B72F5;}'
        'body{font-family:"DM Sans",sans-serif;background:var(--bg);color:var(--text);'
        'padding:1.5rem 1rem;max-width:980px;margin:0 auto}'
        '.brand{font-family:"Syne",sans-serif;font-size:13px;font-weight:700;'
        'letter-spacing:.12em;text-transform:uppercase;color:var(--gold);margin-bottom:2px}'
        '.page-title{font-family:"Syne",sans-serif;font-size:28px;font-weight:800;'
        'letter-spacing:-.02em;color:var(--text);margin-bottom:4px;line-height:1}'
        '.meta{font-size:12px;color:var(--muted);margin-bottom:1.5rem;display:flex;'
        'gap:12px;align-items:center;flex-wrap:wrap}'
        '.meta a{color:var(--blue);text-decoration:none;font-weight:500}'
        '.stats-bar{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-bottom:2rem}'
        '.stat-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:12px 14px}'
        '.stat-val{font-family:"Syne",sans-serif;font-size:22px;font-weight:800;line-height:1}'
        '.stat-lbl{font-size:10px;color:var(--muted);margin-top:4px;text-transform:uppercase;letter-spacing:.08em;font-weight:500}'
        '.section-label{font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;'
        'color:var(--muted);margin:1.75rem 0 .75rem;display:flex;align-items:center;gap:8px}'
        '.section-label::after{content:"";flex:1;height:1px;background:var(--border)}'
        'table{width:100%;border-collapse:collapse;background:var(--surface);'
        'border:1px solid var(--border);border-radius:12px;overflow:hidden;margin-bottom:1.5rem}'
        'th{padding:10px 14px;font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;'
        'letter-spacing:.08em;text-align:left;background:var(--surface2);border-bottom:1px solid var(--border)}'
        'td{padding:10px 14px;font-size:12px;border-bottom:1px solid var(--border);color:var(--text)}'
        'tr:last-child td{border-bottom:none}'
        'tr:hover td{background:var(--surface2)}'
        '.badge{display:inline-block;font-size:10px;font-weight:700;padding:2px 8px;'
        'border-radius:5px;font-family:"DM Mono",monospace}'
        '.badge.WIN{background:#22C87A18;color:var(--green);border:1px solid #22C87A30}'
        '.badge.LOSS{background:#F04B4B18;color:var(--red);border:1px solid #F04B4B30}'
        '.badge.PENDING{background:#F5C84215;color:var(--gold);border:1px solid #F5C84230}'
        '.badge.PUSH{background:#8A909918;color:var(--muted);border:1px solid #8A909930}'
        '.tier-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;vertical-align:middle}'
        '.tier-dot.MAX{background:var(--gold)}'
        '.tier-dot.A{background:var(--green)}'
        '.tier-dot.B{background:var(--blue)}'
        '.tier-dot.C{background:var(--purple)}'
        '.tier-dot.WATCH{background:var(--muted)}'
        '.mono{font-family:"DM Mono",monospace}'
        'footer{font-size:11px;color:var(--faint);margin-top:2rem;text-align:center;padding-bottom:2rem;line-height:1.8}'
        '@media(max-width:600px){.stats-bar{grid-template-columns:repeat(3,1fr)}th,td{padding:8px 10px;font-size:11px}}'
    )

    return ('<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>MLB Record</title>'
            '<style>'+rec_css+'</style></head><body>'
            '<div class="brand">MLB Betting Model</div>'
            '<div class="page-title">Record</div>'
            '<div class="meta">'
            'Updated '+TODAY+' &nbsp;&middot;&nbsp; '
            '<span id="live_update" style="color:var(--green)">&#9679; Live</span>'
            ' &nbsp;&middot;&nbsp; <a href="index.html">Today\'s picks</a>'
            ' &nbsp;&middot;&nbsp; <a href="archive.html">Archive</a>'
            '</div>'
            '<div class="stats-bar">'
            '<div class="stat-card"><div class="stat-val" data-stat="wl">'+str(len(wins))+'-'+str(len(losses))+'</div><div class="stat-lbl">W-L Record</div></div>'
            '<div class="stat-card"><div class="stat-val" data-stat="winrate">'+str(win_rate)+'%</div><div class="stat-lbl">Win Rate</div></div>'
            '<div class="stat-card"><div class="stat-val" data-stat="units" style="color:'+u_color+'">'+u_str+'</div><div class="stat-lbl">Units P&L</div></div>'
            '<div class="stat-card"><div class="stat-val" style="color:'+clv_color+'">'+('+'if avg_clv>=0 else '')+str(avg_clv)+'</div><div class="stat-lbl">Avg CLV</div></div>'
            '<div class="stat-card"><div class="stat-val" style="color:var(--purple)">'+str(watch_rate)+'%</div><div class="stat-lbl">Watch Hit %</div></div>'
            '</div>'
            '<div class="section-label">Performance by Tier</div>'
            '<table><thead><tr><th>Tier</th><th>Record</th><th>Win %</th><th>Units</th></tr></thead><tbody>'+tier_rows+'</tbody></table>'
            '<div class="section-label">Performance by Bet Type</div>'
            '<table><thead><tr><th>Type</th><th>Record</th><th>Win %</th><th>Units</th></tr></thead><tbody>'+bt_rows+'</tbody></table>'
            '<div class="section-label">Pick History</div>'
            '<table><thead><tr>'
            '<th>Date</th><th>Pick</th><th>Game</th><th>Tier</th><th>Open</th><th>Close</th><th>CLV</th><th>Score</th><th>Result</th><th>Units</th>'
            '</tr></thead><tbody>'+pick_rows+'</tbody></table>'
            '<footer>EV model &middot; Track CLV to measure long-term edge &middot; Paper trading until 50+ picks verified</footer>'
            + RECORD_LIVE_JS
            + '</body></html>')

# ── Archive ───────────────────────────────────────────────────────────────────


def fetch_all_teams_data():
    """Fetch standings, stats, and schedule for all 30 MLB teams."""
    teams_data = {}

    # Fetch standings for both leagues in one call
    data = mlb_api("/standings", {
        "leagueId": "103,104",
        "season": "2026",
        "standingsTypes": "regularSeason",
        "hydrate": "team,record,streak,records",
    })
    print("Standings records: "+str(len(data.get("records",[]))))
    for rec in data.get("records", []):
        division = rec.get("division", {}).get("name", "")
        for tr in rec.get("teamRecords", []):
            team = tr.get("team", {})
            tid = team.get("id")
            name = team.get("name", "")
            streak = tr.get("streak", {})
            split_records = tr.get("records", {}).get("splitRecords", [])
            l10_rec = next((s for s in split_records if s.get("type") == "lastTen"), {})
            gp = tr.get("gamesPlayed", 0) or (tr.get("wins",0) + tr.get("losses",0))
            teams_data[tid] = {
                "id": tid,
                "name": name,
                "division": division,
                "wins": tr.get("wins", 0),
                "losses": tr.get("losses", 0),
                "pct": tr.get("winningPercentage", ".000"),
                "gb": tr.get("gamesBack", "-"),
                "streak_type": streak.get("streakType", ""),
                "streak_number": streak.get("streakNumber", 0),
                "last10_w": l10_rec.get("wins", 0),
                "last10_l": l10_rec.get("losses", 0),
                "runs_scored": tr.get("runsScored", 0),
                "runs_allowed": tr.get("runsAllowed", 0),
                "games_played": gp,
            }
    print("Teams loaded from standings: "+str(len(teams_data)))

    # Fetch team batting stats
    bat_data = mlb_api("/stats", {
        "stats": "season", "group": "hitting", "gameType": "R",
        "season": "2026", "sportId": "1", "playerPool": "All",
    })
    for split in bat_data.get("stats", [{}])[0].get("splits", []):
        tid = split.get("team", {}).get("id")
        stat = split.get("stat", {})
        if tid and tid in teams_data:
            teams_data[tid]["ops"] = safe_float(stat.get("ops"))
            teams_data[tid]["avg"] = safe_float(stat.get("avg"))
            teams_data[tid]["obp"] = safe_float(stat.get("obp"))
            teams_data[tid]["slg"] = safe_float(stat.get("slg"))

    # Fetch team pitching stats
    pit_data = mlb_api("/stats", {
        "stats": "season", "group": "pitching", "gameType": "R",
        "season": "2026", "sportId": "1", "playerPool": "All",
    })
    for split in pit_data.get("stats", [{}])[0].get("splits", []):
        tid = split.get("team", {}).get("id")
        stat = split.get("stat", {})
        if tid and tid in teams_data:
            ip = safe_float(stat.get("inningsPitched", "0"))
            so = int(stat.get("strikeOuts", 0) or 0)
            teams_data[tid]["team_era"] = safe_float(stat.get("era"))
            teams_data[tid]["team_whip"] = safe_float(stat.get("whip"))
            teams_data[tid]["team_k9"] = round(so / ip * 9, 2) if ip > 0 else 0.0

    # Fetch next 3 games for each team
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    in3days  = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
    sched = mlb_api("/schedule", {
        "sportId": "1", "startDate": TODAY, "endDate": in3days,
        "hydrate": "probablePitcher,team",
    })
    # Build upcoming games per team
    upcoming = {}
    for de in sched.get("dates", []):
        for g in de.get("games", []):
            home_id = g["teams"]["home"]["team"]["id"]
            away_id = g["teams"]["away"]["team"]["id"]
            home_name = g["teams"]["home"]["team"]["name"]
            away_name = g["teams"]["away"]["team"]["name"]
            home_sp = g["teams"]["home"].get("probablePitcher", {}).get("fullName", "TBD")
            away_sp = g["teams"]["away"].get("probablePitcher", {}).get("fullName", "TBD")
            game_date = de.get("date", "")
            game_time = g.get("gameDate", "")
            entry = {
                "date": game_date,
                "home": home_name,
                "away": away_name,
                "home_sp": home_sp,
                "away_sp": away_sp,
            }
            for tid in [home_id, away_id]:
                if tid not in upcoming:
                    upcoming[tid] = []
                if len(upcoming[tid]) < 3:
                    upcoming[tid].append(entry)

    for tid, td in teams_data.items():
        td["upcoming"] = upcoming.get(tid, [])

    return teams_data


def build_teams_html(teams_data):
    """Build the team overview page sorted alphabetically."""
    if not teams_data:
        return ""

    # Sort alphabetically by team name
    sorted_teams = sorted(teams_data.values(), key=lambda x: x["name"])

    def streak_badge(stype, snum):
        if not stype or not snum: return ""
        color = "#1D9E75" if stype == "W" else "#A32D2D"
        label = stype + str(snum)
        return ('<span style="background:'+color+'22;color:'+color+';font-size:11px;'
                'font-weight:700;padding:2px 8px;border-radius:4px">'+label+'</span>')

    def ops_color(ops):
        if ops >= 0.800: return "#1D9E75"
        if ops >= 0.720: return "#BA7517"
        return "#A32D2D"

    def era_color(era):
        if era <= 3.50: return "#1D9E75"
        if era <= 4.50: return "#BA7517"
        return "#A32D2D"

    def team_card(t):
        gp = t.get("games_played", 0)
        w = t.get("wins", 0)
        l = t.get("losses", 0)
        ops = t.get("ops", 0)
        era = t.get("team_era", 0)
        whip = t.get("team_whip", 0)
        k9  = t.get("team_k9", 0)
        rsg = round(t.get("runs_scored", 0) / gp, 2) if gp > 0 else 0
        rag = round(t.get("runs_allowed", 0) / gp, 2) if gp > 0 else 0
        l10w = t.get("last10_w", 0)
        l10l = t.get("last10_l", 0)
        streak = streak_badge(t.get("streak_type",""), t.get("streak_number",0))
        upcoming = t.get("upcoming", [])

        upcoming_html = ""
        for g in upcoming:
            is_home = g["home"] == t["name"]
            opp = g["away"] if is_home else g["home"]
            sp  = g["home_sp"] if is_home else g["away_sp"]
            loc = "vs" if is_home else "@"
            upcoming_html += ('<div style="font-size:11px;color:#666;padding:3px 0;border-bottom:0.5px solid #f5f5f3">'
                              +g["date"]+" "+loc+" "+opp+" — SP: "+sp+'</div>')

        return (
            '<div style="background:#fff;border:0.5px solid #e8e8e5;border-radius:10px;'
            'padding:1rem 1.25rem;margin-bottom:10px">'
            '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">'
            '<div>'
            '<div style="font-size:16px;font-weight:700">'+t["name"]+'</div>'
            '<div style="font-size:12px;color:#888;margin-top:2px">'+t.get("division","")+'</div>'
            '</div>'
            '<div style="text-align:right">'
            '<div style="font-size:18px;font-weight:700">'+str(w)+'-'+str(l)+'</div>'
            '<div style="font-size:11px;color:#888;margin-top:2px">'+str(l10w)+'-'+str(l10l)+' L10 &nbsp; '+streak+'</div>'
            '</div>'
            '</div>'
            '<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:6px;margin-bottom:10px">'
            '<div style="background:#f9f9f7;border-radius:7px;padding:6px 8px;text-align:center">'
            '<div style="font-size:13px;font-weight:700;color:'+ops_color(ops)+'">'+str(ops)+'</div>'
            '<div style="font-size:9px;color:#999;text-transform:uppercase;letter-spacing:.04em">OPS</div></div>'
            '<div style="background:#f9f9f7;border-radius:7px;padding:6px 8px;text-align:center">'
            '<div style="font-size:13px;font-weight:700">'+str(rsg)+'</div>'
            '<div style="font-size:9px;color:#999;text-transform:uppercase;letter-spacing:.04em">R/G</div></div>'
            '<div style="background:#f9f9f7;border-radius:7px;padding:6px 8px;text-align:center">'
            '<div style="font-size:13px;font-weight:700;color:'+era_color(era)+'">'+str(era)+'</div>'
            '<div style="font-size:9px;color:#999;text-transform:uppercase;letter-spacing:.04em">ERA</div></div>'
            '<div style="background:#f9f9f7;border-radius:7px;padding:6px 8px;text-align:center">'
            '<div style="font-size:13px;font-weight:700">'+str(whip)+'</div>'
            '<div style="font-size:9px;color:#999;text-transform:uppercase;letter-spacing:.04em">WHIP</div></div>'
            '<div style="background:#f9f9f7;border-radius:7px;padding:6px 8px;text-align:center">'
            '<div style="font-size:13px;font-weight:700">'+str(k9)+'</div>'
            '<div style="font-size:9px;color:#999;text-transform:uppercase;letter-spacing:.04em">K/9</div></div>'
            '<div style="background:#f9f9f7;border-radius:7px;padding:6px 8px;text-align:center">'
            '<div style="font-size:13px;font-weight:700">'+str(rag)+'</div>'
            '<div style="font-size:9px;color:#999;text-transform:uppercase;letter-spacing:.04em">RA/G</div></div>'
            '</div>'
            +(('<div style="border-top:0.5px solid #f0f0ee;padding-top:8px">'
               '<div style="font-size:10px;color:#999;text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px">Upcoming</div>'
               +upcoming_html+'</div>') if upcoming else '')
            +'</div>'
        )

    cards = "".join(team_card(t) for t in sorted_teams)

    css = (
        '<style>*{box-sizing:border-box;margin:0;padding:0}'
        'body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;'
        'background:#f9f9f7;color:#1a1a1a;padding:1.25rem;max-width:700px;margin:0 auto}'
        'h1{font-size:20px;font-weight:700;margin-bottom:3px}'
        '.meta{font-size:13px;color:#888;margin-bottom:1.5rem}'
        'footer{font-size:11px;color:#bbb;margin-top:1.5rem;text-align:center;padding-bottom:1rem}'
        '</style>'
    )

    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>MLB Team Overview</title>'+css+'</head><body>'
        '<h1>MLB Team Overview</h1>'
        '<div class="meta">Updated '+TODAY+' &nbsp;&middot;&nbsp; '
        '<a href="index.html" style="color:#378ADD;text-decoration:none">Today&#39;s picks &rarr;</a>'
        ' &nbsp;&middot;&nbsp; <a href="record.html" style="color:#8B6FBA;text-decoration:none">&#128200; Record &rarr;</a>'
        ' &nbsp;&middot;&nbsp; <a href="archive.html" style="color:#378ADD;text-decoration:none">Archive &rarr;</a>'
        ' &nbsp;&middot;&nbsp; <span style="font-size:11px;color:#1D9E75">&#9679; Live data</span></div>'
        '<div style="font-size:12px;color:#888;margin-bottom:1rem">'
        'Green OPS = .800+ &nbsp; Yellow = .720-.799 &nbsp; Red = below .720 &nbsp;&middot;&nbsp; '
        'ERA: Green = 3.50 or below &nbsp; Yellow = 3.51-4.50 &nbsp; Red = above 4.50</div>'
        +cards+
        '<footer>2026 season stats &nbsp;&middot;&nbsp; Updates every workflow run</footer>'
        '</body></html>'
    )


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
    active  = sorted([p for p in all_picks if p.get("tier") in ("MAX","A","B","C")],
                    key=lambda x: {"MAX":0,"A":1,"B":2,"C":3}.get(x.get("tier","C"),3))
    watched = [p for p in all_picks if p.get("tier") == "WATCH"]
    skipped = [p for p in all_picks if p.get("tier") == "SKIP" and not p.get("no_display")]
    total_u = round(sum(p.get("units",0) for p in active),1)
    gen_utc  = data.get("generated_at","")
    try:
        import datetime as _dt
        utc_dt = _dt.datetime.strptime(gen_utc[:19], "%Y-%m-%dT%H:%M:%S")
        et_offset = -4  # EDT
        et_dt = utc_dt + _dt.timedelta(hours=et_offset)
        gen = et_dt.strftime("%-I:%M %p")
    except:
        gen = gen_utc[:16].replace("T"," ")
    date     = data["date"]
    ai_model = data.get("ai_model","Unknown")

    if "Claude" in ai_model:
        mb_bg="#E1F5EE"; mb_tc="#0F6E56"
    else:
        mb_bg="#E6F1FB"; mb_tc="#185FA5"
    model_badge = ('<span style="background:'+mb_bg+';color:'+mb_tc+';font-size:11px;'
                   'font-weight:600;padding:2px 9px;border-radius:20px;">&#129302; '+ai_model+'</span>')

    TBAR={"MAX":"#0A0A0A","A":"#1D9E75","B":"#378ADD","C":"#BA7517","WATCH":"#8B6FBA"}
    TBG ={"MAX":"#1a1a1a","A":"#E1F5EE","B":"#E6F1FB","C":"#FAEEDA","WATCH":"#F0ECFB"}
    TTC ={"MAX":"#FFD700","A":"#0F6E56","B":"#185FA5","C":"#854F0B","WATCH":"#4A2D8F"}
    TLBL={"MAX":"&#9733; MAX BET &mdash; HIGHEST CONFIDENCE","A":"TIER A &mdash; PLAY","B":"TIER B &mdash; PLAY","C":"TIER C &mdash; LEAN","WATCH":"WATCH &mdash; TRACK ONLY"}

    def sp_box(label, name):
        return ('<div class="sp-box">'
                '<div class="sp-lbl">'+label+'</div>'
                '<div class="sp-name">'+str(name)+'</div></div>')

    def mrow(icon, text):
        t=str(text)
        if not t or t in ('N/A','null','None',''): return ''
        return '<div class="data-row">'+icon+' '+t+'</div>'

    def flag_row(text):
        t=str(text)
        if not t or t in ('','null','None'): return ''
        return '<div class="flag">⚠️ '+t+'</div>'

    def score_span(game):
        return '<span id="'+score_id(game)+'" class="score-pill">--</span>'

    def pick_card(p):
        t = p.get("tier","C")
        ev = p.get("ev_pct",0)
        bw = min(int(float(ev or 0))*8, 100)
        game = str(p.get("game",""))
        ump  = str(p.get("hp_ump",""))
        ump_txt = (' &middot; &#9878; '+ump) if ump and ump != "TBD" else ""
        tier_labels = {"MAX":"&#9733; MAX BET","A":"TIER A &mdash; PLAY","B":"TIER B &mdash; PLAY","C":"TIER C &mdash; LEAN","WATCH":"WATCH"}
        lbl = tier_labels.get(t, t)
        return (
            '<div class="pick-card tier-'+t+'">'
            '<div class="card-inner">'
            '<div class="tier-badge '+t+'">'+lbl+'</div>'
            +flag_row(p.get("flags",""))+
            '<div class="pick-name">'+str(p.get("pick",""))+'</div>'
            '<div class="pick-meta">'
            '<span class="game">'+game+'</span>'
            '<span class="odds">'+str(p.get("line","N/A"))+'</span>'
            '<span class="units">'+str(p.get("units",0))+'u</span>'
            +ump_txt+' '+score_span(game)+
            '</div>'
            '<div class="sp-grid">'
            +sp_box("Away SP",p.get("away_sp","TBD"))+sp_box("Home SP",p.get("home_sp","TBD"))+
            '</div>'
            '<div class="ev-row">'
            '<span class="win-prob">Win '+str(p.get("win_prob_pct",0))+'% vs implied '+str(p.get("implied_prob_pct",0))+'%</span>'
            '<span class="ev-badge" style="background:var(--surface2);color:var(--green)">+'+str(ev)+'% EV</span>'
            '</div>'
            '<div class="ev-bar"><div class="ev-fill '+t+'" style="width:'+str(bw)+'%"></div></div>'
            '<div>'
            +mrow("&#9918;",p.get("sp_analysis",""))
            +mrow("&#128101;",p.get("lineup_analysis",""))
            +mrow("&#128293;",p.get("bullpen_note",""))
            +mrow("&#129657;",p.get("injury_flags",""))
            +mrow("&#9878;",p.get("umpire_note",""))
            +mrow("&#127966;",p.get("park_note",""))
            +mrow("&#127748;",p.get("weather_impact",""))+
            '</div>'
            +(('<div class="key-edge">'
               '<div class="key-edge-lbl">Key Edge</div>'
               '<div class="key-edge-text">'+str(p.get("key_edge",""))+'</div>'
               '</div>') if p.get("key_edge") else '')+
            '<div class="rationale">'+str(p.get("rationale",""))+'</div>'
            '</div></div>'
        )

    def watch_card(p):
        t = "WATCH"
        game = str(p.get("game",""))
        ev = p.get("ev_pct",0)
        return (
            '<div class="pick-card tier-WATCH">'
            '<div class="card-inner">'
            '<div class="tier-badge WATCH">WATCH &mdash; TRACK ONLY</div>'
            +flag_row(p.get("flags",""))+
            '<div class="pick-name" style="font-size:17px">'+str(p.get("pick",game))+'</div>'
            '<div class="pick-meta">'
            '<span class="game">'+game+'</span>'
            '<span class="odds">'+str(p.get("line","N/A"))+'</span>'
            '<span style="font-size:11px;color:var(--muted)">Not betting</span>'
            ' '+score_span(game)+
            '</div>'
            '<div class="sp-grid">'
            +sp_box("Away SP",p.get("away_sp","TBD"))+sp_box("Home SP",p.get("home_sp","TBD"))+
            '</div>'
            '<div>'
            +mrow("&#9918;",p.get("sp_analysis",""))
            +mrow("&#128101;",p.get("lineup_analysis",""))
            +mrow("&#128293;",p.get("bullpen_note",""))
            +mrow("&#127966;",p.get("park_note",""))
            +mrow("&#127748;",p.get("weather_impact",""))+
            '</div>'
            '<div style="border-top:1px solid var(--border);padding-top:8px;margin-top:8px">'
            '<div class="watch-why">&#128065; '+str(p.get("avoid_reason",""))+'</div>'
            '<div class="rationale">'+str(p.get("rationale",""))+'</div>'
            '</div>'
            '</div></div>'
        )

    def skip_card(p):
        game = str(p.get("game",""))
        return (
            '<div class="pick-card tier-SKIP">'
            '<div class="card-inner">'
            '<div class="tier-badge SKIP">SKIP &mdash; NO EDGE</div>'
            +flag_row(p.get("flags",""))+
            '<div class="pick-name" style="font-size:16px;color:var(--muted)">'+game+' '+score_span(game)+'</div>'
            '<div class="pick-meta"><span style="color:var(--muted)">'+str(p.get("venue",""))+'</span></div>'
            '<div class="sp-grid">'
            +sp_box("Away SP",p.get("away_sp","TBD"))+sp_box("Home SP",p.get("home_sp","TBD"))+
            '</div>'
            '<div>'
            +mrow("&#9918;",p.get("sp_analysis",""))
            +mrow("&#128101;",p.get("lineup_analysis",""))
            +mrow("&#128293;",p.get("bullpen_note",""))
            +mrow("&#129657;",p.get("injury_flags",""))
            +mrow("&#9878;",p.get("umpire_note",""))
            +mrow("&#127966;",p.get("park_note",""))
            +mrow("&#127748;",p.get("weather_impact",""))+
            '</div>'
            '<div style="border-top:1px solid var(--border);padding-top:8px;margin-top:8px">'
            '<div class="skip-reason">Why skip: '+str(p.get("avoid_reason","No clear edge"))+'</div>'
            '<div class="rationale">'+str(p.get("rationale",""))+'</div>'
            '</div>'
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
        '<style>'
        '@import url(\"https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Syne:wght@700;800&family=DM+Sans:wght@400;500;600&display=swap\");'
        '*{box-sizing:border-box;margin:0;padding:0}'
        ':root{'
        '--bg:#0D0F11;--surface:#151719;--surface2:#1C1F22;--border:#272B2F;'
        '--text:#F0F2F4;--muted:#8A9099;--faint:#3A3F45;'
        '--gold:#F5C842;--green:#22C87A;--red:#F04B4B;--blue:#4B9CF5;--purple:#9B72F5;'
        '}'
        'body{font-family:"DM Sans",sans-serif;background:var(--bg);color:var(--text);'
        'padding:1.5rem 1rem;max-width:720px;margin:0 auto;min-height:100vh}'
        '.brand{font-family:"Syne",sans-serif;font-size:13px;font-weight:700;'
        'letter-spacing:.12em;text-transform:uppercase;color:var(--gold);margin-bottom:2px}'
        '.page-title{font-family:"Syne",sans-serif;font-size:28px;font-weight:800;'
        'letter-spacing:-.02em;color:var(--text);margin-bottom:4px;line-height:1}'
        '.meta{font-size:12px;color:var(--muted);margin-bottom:1.5rem;display:flex;'
        'gap:12px;align-items:center;flex-wrap:wrap}'
        '.meta a{color:var(--blue);text-decoration:none;font-weight:500}'
        '.meta a:hover{color:var(--text)}'
        '.divider{color:var(--faint)}'
        '.stats-bar{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:2rem}'
        '.stat-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;'
        'padding:12px 14px}'
        '.stat-val{font-family:"Syne",sans-serif;font-size:24px;font-weight:800;line-height:1}'
        '.stat-lbl{font-size:10px;color:var(--muted);margin-top:4px;text-transform:uppercase;'
        'letter-spacing:.08em;font-weight:500}'
        '.section-label{font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;'
        'color:var(--muted);margin:1.75rem 0 .75rem;display:flex;align-items:center;gap:8px}'
        '.section-label::after{content:"";flex:1;height:1px;background:var(--border)}'
        '.pick-card{background:var(--surface);border:1px solid var(--border);border-radius:14px;'
        'margin-bottom:12px;overflow:hidden;position:relative}'
        '.pick-card::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px}'
        '.pick-card.tier-MAX::before{background:var(--gold)}'
        '.pick-card.tier-A::before{background:var(--green)}'
        '.pick-card.tier-B::before{background:var(--blue)}'
        '.pick-card.tier-C::before{background:var(--purple)}'
        '.pick-card.tier-WATCH::before{background:var(--muted)}'
        '.pick-card.tier-SKIP::before{background:var(--faint)}'
        '.card-inner{padding:1rem 1rem 1rem 1.25rem}'
        '.tier-badge{display:inline-flex;align-items:center;gap:5px;font-size:10px;font-weight:700;'
        'letter-spacing:.1em;text-transform:uppercase;padding:3px 10px;border-radius:6px;margin-bottom:10px}'
        '.tier-badge.MAX{background:#F5C84220;color:var(--gold);border:1px solid #F5C84240}'
        '.tier-badge.A{background:#22C87A18;color:var(--green);border:1px solid #22C87A30}'
        '.tier-badge.B{background:#4B9CF518;color:var(--blue);border:1px solid #4B9CF530}'
        '.tier-badge.C{background:#9B72F518;color:var(--purple);border:1px solid #9B72F530}'
        '.tier-badge.WATCH{background:#8A909918;color:var(--muted);border:1px solid #8A909930}'
        '.tier-badge.SKIP{background:#3A3F4518;color:var(--faint);border:1px solid #3A3F4530}'
        '.pick-name{font-family:"Syne",sans-serif;font-size:20px;font-weight:800;'
        'letter-spacing:-.01em;margin-bottom:3px;line-height:1.1}'
        '.pick-meta{font-size:12px;color:var(--muted);margin-bottom:12px;display:flex;'
        'gap:8px;align-items:center;flex-wrap:wrap}'
        '.pick-meta .game{color:var(--text);opacity:.7}'
        '.pick-meta .odds{font-family:"DM Mono",monospace;font-weight:500;color:var(--text)}'
        '.pick-meta .units{font-family:"DM Mono",monospace;font-weight:500;'
        'color:var(--gold);background:#F5C84215;padding:1px 7px;border-radius:4px}'
        '.score-pill{font-family:"DM Mono",monospace;font-size:10px;background:var(--surface2);'
        'color:var(--muted);padding:2px 8px;border-radius:4px}'
        '.score-pill.live{background:#F5C84220;color:var(--gold)}'
        '.score-pill.final{background:#22C87A15;color:var(--green)}'
        '.sp-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px}'
        '.sp-box{background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:8px 10px}'
        '.sp-lbl{font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-bottom:3px}'
        '.sp-name{font-size:13px;font-weight:600;color:var(--text)}'
        '.ev-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}'
        '.win-prob{font-size:11px;background:var(--surface2);color:var(--muted);'
        'padding:3px 10px;border-radius:20px;font-family:"DM Mono",monospace}'
        '.ev-badge{font-size:11px;font-weight:700;padding:3px 10px;border-radius:20px;'
        'font-family:"DM Mono",monospace}'
        '.ev-bar{height:3px;background:var(--surface2);border-radius:2px;margin-bottom:12px}'
        '.ev-fill{height:100%;border-radius:2px;background:var(--green)}'
        '.ev-fill.MAX{background:var(--gold)}'
        '.ev-fill.A{background:var(--green)}'
        '.ev-fill.B{background:var(--blue)}'
        '.ev-fill.C{background:var(--purple)}'
        '.data-row{font-size:12px;color:var(--muted);margin-bottom:4px;line-height:1.5}'
        '.flag{font-size:11px;background:#F5C84210;color:#F5C842AA;padding:4px 10px;'
        'border-radius:6px;margin-bottom:8px;border:1px solid #F5C84220}'
        '.key-edge{background:var(--surface2);border-left:2px solid var(--gold);'
        'padding:8px 12px;border-radius:0 8px 8px 0;margin-bottom:10px}'
        '.key-edge-lbl{font-size:9px;text-transform:uppercase;letter-spacing:.1em;'
        'color:var(--gold);font-weight:700;margin-bottom:3px}'
        '.key-edge-text{font-size:12px;color:var(--text);font-weight:500;line-height:1.4}'
        '.rationale{font-size:12px;color:var(--muted);line-height:1.6;margin-top:8px;'
        'border-top:1px solid var(--border);padding-top:8px}'
        '.skip-reason{font-size:11px;color:var(--red);font-weight:600;margin-bottom:4px}'
        '.watch-why{font-size:11px;color:var(--purple);font-style:italic;margin-bottom:4px}'
        'footer{font-size:11px;color:var(--faint);margin-top:2rem;text-align:center;'
        'padding-bottom:2rem;line-height:1.8}'
        '@media(max-width:480px){'
        '.stats-bar{grid-template-columns:repeat(2,1fr)}'
        '.sp-grid{grid-template-columns:1fr}'
        '.page-title{font-size:22px}'
        '}'
        '</style>'
    )

    has_max = any(p.get('tier')=='MAX' for p in active)
    active_color = 'var(--gold)' if has_max else 'var(--green)'
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>MLB Picks — '+date+'</title>'+css+'</head><body>'
        '<div class="brand">MLB Betting Model</div>'
        '<div class="page-title">Today\'s Picks</div>'
        '<div class="meta">'
        '<span>'+date+'</span><span class="divider">&middot;</span>'
        '<span>'+str(data['total_games'])+' games</span><span class="divider">&middot;</span>'
        '<a href="archive.html">Archive</a><span class="divider">&middot;</span>'
        '<a href="record.html">Record</a><span class="divider">&middot;</span>'
        '<span>'+model_badge+'</span><span class="divider">&middot;</span>'
        '<span id="last_update" style="color:var(--muted)">Loading scores...</span>'
        '</div>'
        '<div class="stats-bar">'
        '<div class="stat-card"><div class="stat-val" style="color:'+active_color+'">'+str(len(active))+'</div>'
        '<div class="stat-lbl">Active picks</div></div>'
        '<div class="stat-card"><div class="stat-val" style="color:var(--gold)">'+str(total_u)+'u</div>'
        '<div class="stat-lbl">Total units</div></div>'
        '<div class="stat-card"><div class="stat-val" style="color:var(--purple)">'+str(len(watched))+'</div>'
        '<div class="stat-lbl">Watching</div></div>'
        '<div class="stat-card"><div class="stat-val" style="color:var(--muted)">'+str(len(skipped))+'</div>'
        '<div class="stat-lbl">No edge</div></div>'
        '</div>'
        '<div class="section-label">Active Picks</div>'
        +cards+
        '<footer>EV model &middot; 2025+2026 stats &middot; Recent form &middot; Splits &middot; Lineups &middot; Bullpen &middot; Umpires<br>Never bet more than you can afford to lose</footer>'
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

    # Fetch ESPN injuries once for all teams
    espn_injuries = fetch_espn_injuries()

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

        # Injuries — MLB IL + ESPN combined
        home_injuries=[]; away_injuries=[]
        try: home_injuries = fetch_injuries(g["home_id"])
        except: pass
        try: away_injuries = fetch_injuries(g["away_id"])
        except: pass
        # Merge with ESPN data
        home_injuries = get_team_injuries_with_espn(g["home"], home_injuries, espn_injuries)
        away_injuries = get_team_injuries_with_espn(g["away"], away_injuries, espn_injuries)

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

        # Team momentum/streak (fetched via standings, not per-team endpoint)
        gd["home_streak"] = {}
        gd["away_streak"] = {}

        games_with_data.append(gd)

    # Save updated stats cache (may have new player ID lookups)
    STATS_CACHE.write_text(json.dumps(stats))

    # Auto-settle any previous picks that have final scores first
    record = load_record()
    record, settled = auto_settle_record(record)
    if settled:
        print("Auto-settled "+str(settled)+" picks")
        save_record(record)

    # Smart regeneration logic:
    # - First run of the day: always generate fresh picks
    # - Subsequent runs: only regenerate if a trigger condition is met
    # - Trigger conditions: SP scratch, rain 50%+, line moved 15+ cents
    # - Otherwise: keep locked picks, just update scores

    # Only lock picks that were actually generated today (have home_sp/away_sp fields)
    # Seeded picks from record.json manual entry don't have these fields
    today_picks = [p for p in record.get("picks",[])
                   if p.get("date")==TODAY 
                   and p.get("tier") in ("MAX","A","B","C","WATCH")]
    picks_locked = len(today_picks) > 0
    if picks_locked:
        print("Picks already in record.json for "+TODAY+" ("+str(len(today_picks))+" picks). Locking.")

    # Check trigger conditions for regeneration
    def should_regenerate(locked_picks, new_game_data, old_odds):
        triggers = []
        for gd in new_game_data:
            game_key = gd["away"]+"@"+gd["home"]
            # Check rain 50%+
            precip = gd.get("weather",{}).get("precip_pct",0)
            if precip and int(precip) >= 50:
                triggers.append("Rain 50%+ at "+gd["home"])
            # Check SP scratch — SP changed from what was in locked picks
            for lp in locked_picks:
                if gd["away"]+" @ "+gd["home"] == lp.get("game",""):
                    old_home_sp = lp.get("home_sp","")
                    old_away_sp = lp.get("away_sp","")
                    if old_home_sp and old_home_sp != gd["home_sp"] and gd["home_sp"] != "TBD":
                        triggers.append("SP scratch: "+old_home_sp+" → "+gd["home_sp"])
                    if old_away_sp and old_away_sp != gd["away_sp"] and gd["away_sp"] != "TBD":
                        triggers.append("SP scratch: "+old_away_sp+" → "+gd["away_sp"])
            # Check line movement 15+ cents
            new_odds = gd.get("odds",{})
            for lp in locked_picks:
                if gd["away"]+" @ "+gd["home"] == lp.get("game",""):
                    try:
                        old_line = float(str(lp.get("open_line","0")).replace("+",""))
                        new_ml = new_odds.get("moneyline",{})
                        for team, price in new_ml.items():
                            if abs(float(price) - old_line) >= 15:
                                triggers.append("Line moved 15+ cents on "+team)
                    except: pass
        return triggers

    force_regen = False
    regen_reasons = []
    if picks_locked:
        if FORCE_REGEN:
            print("FORCE_REGENERATE flag set — regenerating picks for "+TODAY)
            force_regen = True
            regen_reasons = ["Manual force regeneration"]
        else:
            regen_reasons = should_regenerate(today_picks, games_with_data, odds_map)
            force_regen = len(regen_reasons) > 0
            if force_regen:
                print("REGENERATING picks — triggers: "+", ".join(regen_reasons))
            else:
                print("Picks locked for "+TODAY+" ("+str(len(today_picks))+" picks). No triggers. Keeping locked picks.")

    if not picks_locked or force_regen or FORCE_REGEN:
        if force_regen:
            # Only remove picks for games affected by triggers
            affected_games = set()
            for reason in regen_reasons:
                if "SP SCRATCH:" in reason:
                    # Extract game from trigger — format "SP SCRATCH: OldSP → NewSP (Team)"
                    for gd in games_with_data:
                        team_name = reason.split("(")[-1].replace(")","").strip()
                        if team_name in [gd["home"], gd["away"]]:
                            affected_games.add(gd["away"]+" @ "+gd["home"])
                else:
                    # For rain/line movement — regenerate all
                    affected_games = {gd["away"]+" @ "+gd["home"] for gd in games_with_data}
                    break

            if affected_games:
                print("Removing picks for affected games: "+", ".join(affected_games))
                record["picks"] = [p for p in record["picks"]
                                   if not (p.get("date")==TODAY
                                           and not p.get("result")
                                           and p.get("game","") in affected_games)]
            else:
                # Fallback — remove all unsettled today picks
                record["picks"] = [p for p in record["picks"]
                                   if not (p.get("date")==TODAY and not p.get("result"))]

        picks, ai_model = call_ai(games_with_data)
        active = [p for p in picks if p.get("tier") in ("MAX","A","B","C")]
        record["ai_model"] = ai_model
        if regen_reasons:
            record["regen_reasons"] = record.get("regen_reasons",[]) + regen_reasons
    else:
        ai_model = record.get("ai_model", "Claude Sonnet 4.5")
        picks = [p for p in record.get("picks",[]) if p.get("date")==TODAY]
        active = [p for p in picks if p.get("tier") in ("MAX","A","B","C")]

    # Save new picks to record (only when fresh or regenerated)
    existing_keys = {p["game"]+p.get("date","") for p in record["picks"]}
    for p in active:
        key = p.get("game","")+TODAY
        if key not in existing_keys:
            record["picks"].append({
                "date":     TODAY,
                "game":     p.get("game",""),
                "pick":     p.get("pick",""),
                "bet_type": p.get("bet_type",""),
                "home_sp":  p.get("home_sp",""),
                "away_sp":  p.get("away_sp",""),
                "line":     p.get("line",""),
                "open_line":p.get("line",""),
                "close_line":"",
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
                "home_sp":  p.get("home_sp",""),
                "away_sp":  p.get("away_sp",""),
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
        "generated_at": datetime.datetime.utcnow().isoformat()+"Z",  # stored UTC, displayed as ET
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
