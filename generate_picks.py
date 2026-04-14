"""
MLB Betting Model — Daily Picks Generator
See MODEL_CONTEXT.md for full documentation.
"""

import os, json, datetime, math, requests
from pathlib import Path
from data.constants import (
    STADIUMS, TEAM_NAME_MAP, PARK_FACTORS, BOOK_PRIORITY,
    UMP_DATA, SAVANT_TEAM_MAP, SAVANT_TEAM_MAP_REV
)

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

# ── HARD LOCK: Exit immediately if picks already generated today ───────────────
LOCK_FILE = OUTPUT_DIR / ("picks_locked_" + TODAY + ".txt")
INDEX_FILE = OUTPUT_DIR / "index.html"
REBUILD_ONLY = LOCK_FILE.exists() and not FORCE_REGEN and not INDEX_FILE.exists()
if LOCK_FILE.exists() and not FORCE_REGEN and INDEX_FILE.exists():
    print(f"[LOCK] picks_locked_{TODAY}.txt exists — picks already generated today. Exiting.")
    print("[LOCK] Use FORCE_REGENERATE=yes to override.")
    import sys; sys.exit(0)
# ──────────────────────────────────────────────────────────────────────────────


# ── Helpers ───────────────────────────────────────────────────────────────────

def american_to_implied(odds):
    """Convert American odds to implied probability percentage."""
    try:
        o = float(str(odds).replace("+",""))
        if o < 0:
            return round(abs(o) / (abs(o) + 100) * 100, 1)
        else:
            return round(100 / (o + 100) * 100, 1)
    except:
        return 0


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

# Baseball Savant uses team abbreviations — map to full MLB team names
SAVANT_TEAM_MAP = {
    "ARI":"Arizona Diamondbacks","ATL":"Atlanta Braves","BAL":"Baltimore Orioles",
    "BOS":"Boston Red Sox","CHC":"Chicago Cubs","CWS":"Chicago White Sox",
    "CIN":"Cincinnati Reds","CLE":"Cleveland Guardians","COL":"Colorado Rockies",
    "DET":"Detroit Tigers","HOU":"Houston Astros","KC":"Kansas City Royals",
    "LAA":"Los Angeles Angels","LAD":"Los Angeles Dodgers","MIA":"Miami Marlins",
    "MIL":"Milwaukee Brewers","MIN":"Minnesota Twins","NYM":"New York Mets",
    "NYY":"New York Yankees","ATH":"Athletics","PHI":"Philadelphia Phillies",
    "PIT":"Pittsburgh Pirates","SD":"San Diego Padres","SF":"San Francisco Giants",
    "SEA":"Seattle Mariners","STL":"St. Louis Cardinals","TB":"Tampa Bay Rays",
    "TEX":"Texas Rangers","TOR":"Toronto Blue Jays","WSH":"Washington Nationals",
    "OAK":"Athletics",
}
SAVANT_TEAM_MAP_REV = {v:k for k,v in SAVANT_TEAM_MAP.items()}

def fetch_savant_pitcher_data(season):
    """
    Fetch pitcher Statcast data from Baseball Savant expected statistics leaderboard.
    Returns xERA, xwOBA against, barrel_pct, hard_hit_pct, whiff_pct, avg_fastball_velo
    keyed by player name. Falls back gracefully if unavailable.
    """
    try:
        import csv, io
        url = "https://baseballsavant.mlb.com/leaderboard/expected_statistics"
        params = {
            "type": "pitcher",
            "year": str(season),
            "position": "",
            "team": "",
            "min": "1",
            "csv": "true"
        }
        r = requests.get(url, params=params,
                        headers={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
                        timeout=20)
        if not r.ok:
            print(f"Savant pitcher HTTP {r.status_code}")
            return {}
        if len(r.text) < 100:
            print(f"Savant pitcher returned empty ({len(r.text)} chars)")
            return {}
        text = r.text.lstrip('\ufeff').replace('\r\n','\n')
        reader = csv.DictReader(io.StringIO(text))
        fieldnames = [f.strip().strip('"') for f in (reader.fieldnames or [])]
        print(f"Savant pitcher columns ({season}): {fieldnames[:12]}")
        result = {}
        for row in reader:
            clean_row = {k.strip().strip('"'): v for k,v in row.items()}
            last  = (clean_row.get("last_name","") or "").strip().strip('"').split(",")[0].strip()
            first = (clean_row.get("first_name","") or "").strip().strip('"')
            if not first and not last:
                combined = clean_row.get("last_name, first_name","") or clean_row.get("player_name","")
                if combined and "," in combined:
                    parts = combined.split(",")
                    last = parts[0].strip().strip('"')
                    first = parts[1].strip().strip('"') if len(parts) > 1 else ""
            if not last: continue
            full_name = (first+" "+last).strip() if first else last
            def sv(k, _c=clean_row):
                v = (_c.get(k,"") or "").strip().strip('"')
                try: return round(float(v),3) if v and v not in ("","null","NA","--","None") else None
                except: return None
            result[full_name] = {
                "player_id_savant": (clean_row.get("player_id","") or "").strip(),
                "xera": sv("xera") or sv("est_era") or sv("xERA"),
                "xwoba_against": sv("est_woba") or sv("xwoba") or sv("xwOBA"),
                "xba": sv("est_ba") or sv("xba"),
            }

        # Statcast leaderboard — barrel, hard hit, whiff, AND velocity
        url2 = "https://baseballsavant.mlb.com/leaderboard/statcast"
        params2 = {
            "type": "pitcher",
            "year": str(season),
            "position": "",
            "team": "",
            "min": "q",
            "csv": "true"
        }
        r2 = requests.get(url2, params=params2,
                         headers={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
                         timeout=20)
        if r2.ok and len(r2.text) > 100:
            text2 = r2.text.lstrip('\ufeff').replace('\r\n','\n')
            reader2 = csv.DictReader(io.StringIO(text2))
            fields2 = [f.strip().strip('"') for f in (reader2.fieldnames or [])]
            print(f"Savant statcast pitcher columns ({season}): {fields2[:15]}")
            for row in reader2:
                clean_row2 = {k.strip().strip('"'): v for k,v in row.items()}
                last  = (clean_row2.get("last_name","") or "").strip().strip('"')
                first = (clean_row2.get("first_name","") or "").strip().strip('"')
                if not last: continue
                full_name = (first+" "+last).strip() if first else last
                def sv2(k, _c=clean_row2):
                    v = (_c.get(k,"") or "").strip().strip('"')
                    try: return round(float(v),2) if v and v not in ("","null","NA","--","None") else None
                    except: return None
                entry = result.setdefault(full_name, {})
                entry["barrel_pct"]    = sv2("barrel_batted_rate") or sv2("brl_percent") or sv2("barrel_percent")
                entry["hard_hit_pct"]  = sv2("hard_hit_percent") or sv2("hard_hit_rate")
                entry["whiff_pct"]     = sv2("whiff_percent") or sv2("whiff_rate")
                # Fastball velocity — key for detecting declining SP
                entry["avg_fastball_velo"] = sv2("fastball_avg_speed") or sv2("avg_best_speed") or sv2("release_speed")

        print(f"Baseball Savant: loaded {len(result)} pitcher records for {season}")
        return result
    except Exception as e:
        print(f"Baseball Savant pitcher fetch failed: {str(e)}")
        return {}


def fetch_savant_batter_data(season):
    """
    Fetch team batting Statcast data from Baseball Savant.
    Returns wOBA, xwOBA, barrel_pct, hard_hit_pct, sprint_speed keyed by team name.
    Falls back gracefully if unavailable.
    """
    try:
        import csv, io
        url = "https://baseballsavant.mlb.com/leaderboard/custom"
        params = {
            "year": str(season),
            "type": "batter",
            "filter": "",
            "min": "1",
            "selections": "player_id,last_name,first_name,team_name_alt,woba,xwoba,barrel_batted_rate,hard_hit_percent,exit_velocity_avg",
            "statcast": "true",
            "csv": "true"
        }
        r = requests.get(url, params=params,
                        headers={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
                        timeout=15)
        if not r.ok or len(r.text) < 100:
            return {}
        reader = csv.DictReader(io.StringIO(r.text))
        # Aggregate by team
        team_data = {}
        team_counts = {}
        for row in reader:
            team = (row.get("team_name_alt","") or "").strip()
            if not team: continue
            def sv(k, _r=row):
                v = _r.get(k,"")
                try: return float(v) if v and v not in ("","null","NA","--") else None
                except: return None
            woba = sv("woba"); xwoba = sv("xwoba")
            barrel = sv("barrel_batted_rate"); hh = sv("hard_hit_percent")
            ev = sv("exit_velocity_avg")
            if team not in team_data:
                team_data[team] = {"woba_sum":0,"xwoba_sum":0,"barrel_sum":0,"hh_sum":0,"ev_sum":0}
                team_counts[team] = 0
            if woba: team_data[team]["woba_sum"] += woba
            if xwoba: team_data[team]["xwoba_sum"] += xwoba
            if barrel: team_data[team]["barrel_sum"] += barrel
            if hh: team_data[team]["hh_sum"] += hh
            if ev: team_data[team]["ev_sum"] += ev
            team_counts[team] += 1
        result = {}
        for team_abbrev, d in team_data.items():
            n = team_counts[team_abbrev]
            if n == 0: continue
            # Store by both abbreviation AND full name for flexible lookup
            full_name = SAVANT_TEAM_MAP.get(team_abbrev, team_abbrev)
            entry = {
                "woba_savant": round(d["woba_sum"]/n,3) if d["woba_sum"] else None,
                "xwoba": round(d["xwoba_sum"]/n,3) if d["xwoba_sum"] else None,
                "barrel_pct": round(d["barrel_sum"]/n,1) if d["barrel_sum"] else None,
                "hard_hit_pct": round(d["hh_sum"]/n,1) if d["hh_sum"] else None,
                "exit_velo": round(d["ev_sum"]/n,1) if d["ev_sum"] else None,
            }
            result[full_name] = entry
            result[team_abbrev] = entry  # also store by abbrev for fallback
        print(f"Baseball Savant: loaded {len(result)//2} team batting records for {season}")
        return result
    except Exception as e:
        print(f"Baseball Savant batter fetch failed: {str(e)}")
        return {}


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
        hr = int(stat.get("homeRuns",0) or 0)
        hbp = int(stat.get("hitBatsmen",0) or 0)
        fip = round((13*hr + 3*(bb+hbp) - 2*so) / ip + 3.10, 2) if ip > 0 else None
        result[name] = {
            "player_id":pid,"season":season,"gs":gs,"ip":round(ip,1),
            "era":safe_float(stat.get("era")),
            "fip":fip,
            "whip":safe_float(stat.get("whip")),
            "k9":round(so/ip*9,2) if ip>0 else 0,
            "bb9":round(bb/ip*9,2) if ip>0 else 0,
            "hr9":round(hr/ip*9,2) if ip>0 else 0,
        }
    return result

def fetch_reliever_stats_bulk(season):
    """Fetch season stats for all relievers — used for bullpen quality scoring."""
    data = mlb_api("/stats", {
        "stats":"season","playerPool":"All","sportId":"1",
        "season":str(season),"group":"pitching","limit":"800",
    })
    result = {}
    for split in data.get("stats",[{}])[0].get("splits",[]):
        name = split.get("player",{}).get("fullName","")
        pid  = split.get("player",{}).get("id")
        stat = split.get("stat",{})
        gs = int(stat.get("gamesStarted",0) or 0)
        g  = int(stat.get("gamesPitched",0) or 0)
        if gs >= 1: continue  # skip starters
        if g < 3: continue    # need meaningful sample
        ip = safe_float(stat.get("inningsPitched","0"))
        if ip < 3: continue
        so = int(stat.get("strikeOuts",0) or 0)
        bb = int(stat.get("baseOnBalls",0) or 0)
        hr = int(stat.get("homeRuns",0) or 0)
        hbp = int(stat.get("hitBatsmen",0) or 0)
        sv = int(stat.get("saves",0) or 0)
        holds = int(stat.get("holds",0) or 0)
        fip = round((13*hr + 3*(bb+hbp) - 2*so) / ip + 3.10, 2) if ip > 0 else None
        result[name] = {
            "player_id": pid, "season": season,
            "era": safe_float(stat.get("era")),
            "fip": fip,
            "whip": safe_float(stat.get("whip")),
            "k9": round(so/ip*9,2) if ip>0 else 0,
            "ip": round(ip,1),
            "saves": sv,
            "holds": holds,
            "role": "closer" if sv >= 3 else ("setup" if holds >= 3 else "middle"),
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

_VELO_CACHE = {}  # savant_id → velo trend data

def fetch_pitcher_splits(pid, season):
    """Fetch home/away splits for a pitcher."""
    if not pid: return {}
    splits_data = {}
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

def fetch_pitcher_velo_trend(savant_id, season_velo):
    """
    Fetch per-game fastball velocity for last 3 starts from Savant.
    Compares to season average to detect declining velocity.
    A drop of 1.5+ mph is a significant red flag — often precedes ERA spike.
    Cached by savant_id.
    """
    if not savant_id: return {}
    if savant_id in _VELO_CACHE: return _VELO_CACHE[savant_id]

    try:
        import csv, io, datetime
        # Savant game log by pitcher
        url = "https://baseballsavant.mlb.com/statcast_search/csv"
        today = datetime.date.today()
        start_date = (today - datetime.timedelta(days=30)).isoformat()
        params = {
            "hfGT": "R|",
            "hfSea": f"{today.year}|",
            "player_type": "pitcher",
            "pitcherId": str(savant_id),
            "group_by": "name_event",
            "sort_col": "game_date",
            "sort_order": "desc",
            "min_pitches": "0",
            "min_results": "0",
            "min_abs": "0",
            "type": "details",
            "game_date_gt": start_date,
        }
        r = requests.get(url, params=params,
                        headers={"User-Agent": "Mozilla/5.0"},
                        timeout=15)
        if not r.ok or len(r.text) < 100:
            _VELO_CACHE[savant_id] = {}
            return {}

        text = r.text.lstrip('\ufeff').replace('\r\n', '\n')
        reader = csv.DictReader(io.StringIO(text))

        # Group pitches by game date, collect fastball velocities
        from collections import defaultdict
        game_velos = defaultdict(list)
        for row in reader:
            pitch_type = (row.get("pitch_type","") or "").strip().upper()
            # Fastball types: FF=4-seam, SI=sinker, FC=cutter
            if pitch_type not in ("FF","SI","FC"): continue
            velo_str = (row.get("release_speed","") or "").strip()
            game_date = (row.get("game_date","") or "").strip()
            try:
                velo = float(velo_str)
                if velo > 70:  # sanity check
                    game_velos[game_date].append(velo)
            except: continue

        if not game_velos:
            _VELO_CACHE[savant_id] = {}
            return {}

        # Average velo per game, take last 3
        game_avgs = {d: round(sum(v)/len(v),1) for d,v in game_velos.items() if v}
        sorted_games = sorted(game_avgs.items(), reverse=True)[:3]
        if not sorted_games:
            _VELO_CACHE[savant_id] = {}
            return {}

        recent_avg = round(sum(v for _,v in sorted_games) / len(sorted_games), 1)
        velo_drop = round((season_velo or 0) - recent_avg, 1) if season_velo else None

        result = {
            "recent_avg_velo": recent_avg,
            "season_avg_velo": season_velo,
            "velo_drop": velo_drop,
            "starts_measured": len(sorted_games),
        }

        # Flag significant velocity drops
        if velo_drop is not None:
            if velo_drop >= 2.0:
                result["velo_trend"] = f"DECLINING — down {velo_drop}mph last {len(sorted_games)} starts (now {recent_avg}mph)"
                result["velo_flag"] = "DECLINING"
            elif velo_drop >= 1.0:
                result["velo_trend"] = f"SOFT DECLINE — down {velo_drop}mph last {len(sorted_games)} starts (now {recent_avg}mph)"
                result["velo_flag"] = "SOFT_DECLINE"
            elif velo_drop <= -1.0:
                result["velo_trend"] = f"GAINING — up {abs(velo_drop)}mph last {len(sorted_games)} starts (now {recent_avg}mph)"
                result["velo_flag"] = "GAINING"
            else:
                result["velo_trend"] = f"STABLE — {recent_avg}mph last {len(sorted_games)} starts"
                result["velo_flag"] = "STABLE"

        _VELO_CACHE[savant_id] = result
        return result
    except Exception as e:
        _VELO_CACHE[savant_id] = {}
        return {}
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
        team = normalize_team(split.get("team",{}).get("name",""))
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
        team_raw = split.get("team",{}).get("name","")
        team = normalize_team(team_raw)  # normalize at fetch time
        stat = split.get("stat",{})
        if not team: continue
        g = int(stat.get("gamesPlayed",1) or 1)
        runs = int(stat.get("runs",0) or 0)
        if g < 3 and season == 2026: continue
        ops = safe_float(stat.get("ops"))
        if ops > 1.2: continue
        if ops <= 0 and season == 2026: continue
        bb  = int(stat.get("baseOnBalls",0) or 0)
        hbp = int(stat.get("hitByPitch",0) or 0)
        h   = int(stat.get("hits",0) or 0)
        d   = int(stat.get("doubles",0) or 0)
        t   = int(stat.get("triples",0) or 0)
        hr  = int(stat.get("homeRuns",0) or 0)
        ab  = int(stat.get("atBats",0) or 0)
        sf  = int(stat.get("sacFlies",0) or 0)
        singles = h - d - t - hr
        pa = ab + bb + hbp + sf
        woba = round((0.69*bb + 0.72*hbp + 0.89*singles + 1.27*d + 1.62*t + 2.10*hr) / pa, 3) if pa > 0 else None
        result[team] = {
            "season":season,"games_played":g,
            "ops":ops,"avg":safe_float(stat.get("avg")),
            "obp":safe_float(stat.get("obp")),
            "slg":safe_float(stat.get("slg")),
            "woba":woba,
            "runs_per_game":round(runs/g,2) if g>0 else 0,
        }
    return result

_SPLITS_CACHE = {}
_THROWS_CACHE = {}  # pitcher_id → "L" or "R"

def fetch_pitcher_throws(pid):
    """Fetch pitcher throwing arm — cached to avoid repeated calls."""
    if not pid: return ""
    if pid in _THROWS_CACHE: return _THROWS_CACHE[pid]
    try:
        data = mlb_api("/people/"+str(pid), {"hydrate":"currentTeam"})
        people = data.get("people",[])
        if people:
            throws = people[0].get("pitchHand",{}).get("code","")
            _THROWS_CACHE[pid] = throws
            return throws
    except: pass
    return ""

def fetch_team_home_away_splits(team_id, season):
    """Fetch home vs away batting splits + W/L record for a team."""
    cache_key = str(team_id)+"-"+str(season)+"-splits"
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
            if g < 3: continue
            runs = int(stat.get("runs",0) or 0)
            ops = safe_float(stat.get("ops"))
            if ops > 1.2: continue
            if ops <= 0: continue
            bb  = int(stat.get("baseOnBalls",0) or 0)
            hbp = int(stat.get("hitByPitch",0) or 0)
            h   = int(stat.get("hits",0) or 0)
            d   = int(stat.get("doubles",0) or 0)
            t   = int(stat.get("triples",0) or 0)
            hr  = int(stat.get("homeRuns",0) or 0)
            ab  = int(stat.get("atBats",0) or 0)
            sf  = int(stat.get("sacFlies",0) or 0)
            singles = h - d - t - hr
            pa = ab + bb + hbp + sf
            woba = round((0.69*bb + 0.72*hbp + 0.89*singles + 1.27*d + 1.62*t + 2.10*hr) / pa, 3) if pa > 0 else None
            wins = int(stat.get("wins",0) or 0)
            losses_s = int(stat.get("losses",0) or 0)
            win_pct = round(wins/(wins+losses_s),3) if wins+losses_s > 0 else None
            result[label] = {
                "ops": ops,
                "woba": woba,
                "runs_per_game": round(runs/g,2) if g>0 else 0,
                "games": g,
                "wins": wins,
                "losses": losses_s,
                "win_pct": win_pct,
            }
    _SPLITS_CACHE[cache_key] = result
    return result

STATS_CACHE_VERSION = "v9"  # bump when fetch logic changes to force cache refresh

def fetch_pitch_arsenal(season):
    """
    Fetch pitch mix data for all pitchers from Savant pitch arsenal leaderboard.
    Returns fastball%, slider%, changeup%, curveball% per pitcher.
    Combined with platoon data: LHP who throws 70% sliders vs right-heavy lineup = elite edge.
    One bulk CSV fetch — no per-pitcher calls needed.
    """
    try:
        import csv, io
        url = "https://baseballsavant.mlb.com/leaderboard/pitch-arsenal-stats"
        params = {
            "type": "pitcher",
            "pitchType": "",
            "year": str(season),
            "team": "",
            "min": "10",
            "csv": "true",
        }
        r = requests.get(url, params=params,
                        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
                        timeout=20)
        if not r.ok or len(r.text) < 100:
            print(f"Pitch arsenal fetch failed: HTTP {r.status_code if r else 'no response'}")
            return {}

        text = r.text.lstrip('\ufeff').replace('\r\n', '\n')
        reader = csv.DictReader(io.StringIO(text))
        fields = [f.strip().strip('"') for f in (reader.fieldnames or [])]
        print(f"Pitch arsenal columns: {fields[:15]}")

        # Aggregate pitch usage % per pitcher
        pitcher_pitches = {}  # name → {pitch_type: {pct, whiff_pct, ...}}
        for row in reader:
            clean = {k.strip().strip('"'): (v or "").strip().strip('"') for k,v in row.items()}

            # Handle combined "last_name, first_name" column (Savant format)
            combined = clean.get("last_name, first_name","") or clean.get("player_name","")
            if combined and "," in combined:
                parts = combined.split(",", 1)
                last  = parts[0].strip()
                first = parts[1].strip() if len(parts) > 1 else ""
            else:
                last  = clean.get("last_name","").strip()
                first = clean.get("first_name","").strip()

            if not last: continue
            name = (first+" "+last).strip() if first else last

            pitch_type = clean.get("pitch_type","").strip().upper()
            if not pitch_type: continue

            # Use default arg to capture current 'clean' value — avoids loop closure bug
            def pf(k, _c=clean):
                v = _c.get(k,"")
                try: return round(float(v), 4) if v and v not in ("","null","NA","--") else None
                except: return None

            pitch_data = {
                "pct":       pf("pitch_usage") or pf("pitch_percent") or pf("pct"),
                "whiff_pct": pf("whiff_percent") or pf("whiff_pct"),
                "avg_speed": pf("avg_speed") or pf("release_speed"),
            }
            pitcher_pitches.setdefault(name, {})[pitch_type] = pitch_data

        # Summarize into clean pitch mix per pitcher
        result = {}
        for name, pitches in pitcher_pitches.items():
            # Categorize pitches
            fastball_types = {"FF","SI","FC"}
            breaking_types = {"SL","CU","KC","SV","CS"}
            offspeed_types = {"CH","FS","FO","SC"}

            fb_pct  = sum((p.get("pct") or 0) for t,p in pitches.items() if t in fastball_types)
            brk_pct = sum((p.get("pct") or 0) for t,p in pitches.items() if t in breaking_types)
            off_pct = sum((p.get("pct") or 0) for t,p in pitches.items() if t in offspeed_types)

            # Primary pitch type (highest usage)
            primary = max(pitches.items(), key=lambda x: x[1].get("pct") or 0, default=(None,{}))
            primary_type = primary[0]
            primary_pct  = primary[1].get("pct")
            primary_speed = primary[1].get("avg_speed")

            # Slider specifically — most platoon-relevant pitch
            slider = pitches.get("SL",{})
            slider_pct = slider.get("pct")
            slider_whiff = slider.get("whiff_pct")

            # Overall whiff rate from arsenal
            whiff_vals = [p.get("whiff_pct") for p in pitches.values() if p.get("whiff_pct")]
            avg_whiff = round(sum(whiff_vals)/len(whiff_vals),1) if whiff_vals else None

            if fb_pct or brk_pct or off_pct:
                result[name] = {
                    "fastball_pct": round(fb_pct,1) if fb_pct else None,
                    "breaking_pct": round(brk_pct,1) if brk_pct else None,
                    "offspeed_pct": round(off_pct,1) if off_pct else None,
                    "slider_pct":   round(slider_pct,1) if slider_pct else None,
                    "slider_whiff": slider_whiff,
                    "primary_pitch": primary_type,
                    "primary_pct":   primary_pct,
                    "avg_speed":     primary_speed,
                    "avg_whiff_pct": avg_whiff,
                    "pitch_count":   len(pitches),
                }

        print(f"Pitch arsenal: loaded {len(result)} pitcher profiles for {season}")
        return result
    except Exception as e:
        print(f"Pitch arsenal fetch failed: {e}")
        return {}


def fetch_and_cache_stats():
    if STATS_CACHE.exists():
        try:
            cached = json.loads(STATS_CACHE.read_text())
            if cached.get("date") == TODAY and cached.get("version") == STATS_CACHE_VERSION:
                print("Using cached stats")
                return cached
        except: pass
    print("Fetching fresh stats...")
    import time as _t, concurrent.futures

    # Run all API calls in parallel — cuts fetch time from ~3min to ~30s
    _t0 = _t.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        f_sp25      = ex.submit(fetch_sp_stats_bulk, 2025)
        f_sp26      = ex.submit(fetch_sp_stats_bulk, 2026)
        f_rp25      = ex.submit(fetch_reliever_stats_bulk, 2025)
        f_rp26      = ex.submit(fetch_reliever_stats_bulk, 2026)
        f_tp25      = ex.submit(fetch_team_pitching, 2025)
        f_tp26      = ex.submit(fetch_team_pitching, 2026)
        f_tb25      = ex.submit(fetch_team_batting,  2025)
        f_tb26      = ex.submit(fetch_team_batting,  2026)
        f_sv_p26    = ex.submit(fetch_savant_pitcher_data, 2026)
        f_sv_p25    = ex.submit(fetch_savant_pitcher_data, 2025)
        f_sv_b26    = ex.submit(fetch_savant_batter_data,  2026)
        f_sv_b25    = ex.submit(fetch_savant_batter_data,  2025)
        f_arsenal   = ex.submit(fetch_pitch_arsenal, 2026)

    stats = {
        "date": TODAY,
        "version": STATS_CACHE_VERSION,
        "sp_2025":             f_sp25.result(),
        "sp_2026":             f_sp26.result(),
        "rp_2025":             f_rp25.result(),
        "rp_2026":             f_rp26.result(),
        "team_pitching_2025":  f_tp25.result(),
        "team_pitching_2026":  f_tp26.result(),
        "team_batting_2025":   f_tb25.result(),
        "team_batting_2026":   f_tb26.result(),
        "savant_pitchers_2026":f_sv_p26.result(),
        "savant_pitchers_2025":f_sv_p25.result(),
        "savant_batting_2026": f_sv_b26.result(),
        "savant_batting_2025": f_sv_b25.result(),
        "pitch_arsenal_2026":  f_arsenal.result() if f_arsenal.exception() is None else {},
        "player_id_cache": {},
    }
    print(f"Stats fetch: {round(_t.time()-_t0,1)}s total (parallel)")
    print(f"SP stats: {len(stats['sp_2025'])} in 2025, {len(stats['sp_2026'])} in 2026")
    print(f"RP stats: {len(stats['rp_2025'])} in 2025, {len(stats['rp_2026'])} in 2026")
    print(f"Pitch arsenal: {len(stats['pitch_arsenal_2026'])} pitchers")
    sv_p = len(stats['savant_pitchers_2026']) + len(stats['savant_pitchers_2025'])
    sv_b = len(stats['savant_batting_2026']) + len(stats['savant_batting_2025'])
    print(f"Savant: {sv_p} pitcher records, {sv_b} batting records")
    STATS_CACHE.write_text(json.dumps(stats))
    return stats


def sp_reliability_score(sp_stats):
    """
    Calculate how reliable a pitcher's stats are based on sample size.
    Returns a score 0.0-1.0 and a confidence label.
    
    This is the core fix for the early-season overconfidence problem.
    xERA from 1 start = noise. ERA from 150 IP = signal.
    """
    gs_2026 = sp_stats.get("gs_2026", 0) or sp_stats.get("gs", 0) or 0
    ip_2026 = sp_stats.get("ip", 0) or 0
    note = sp_stats.get("note", "")
    
    # If using 2025 stats only, moderate reliability
    if "2025 only" in note or gs_2026 == 0:
        return 0.65, "2025_ONLY"
    
    # 2026 sample size scoring
    if ip_2026 >= 60:
        return 1.0, "RELIABLE"
    elif ip_2026 >= 40:
        return 0.90, "RELIABLE"
    elif ip_2026 >= 25:
        return 0.75, "MODERATE"
    elif ip_2026 >= 15:
        return 0.55, "SMALL_SAMPLE"
    elif ip_2026 >= 5:
        return 0.35, "VERY_SMALL"
    else:
        return 0.20, "UNRELIABLE"


def fetch_team_rest_days(team_id, game_date_str):
    """
    Fetch days of rest for a team — how many days since their last game.
    Returns dict with rest_days, back_to_back, road_trip_length.
    """
    try:
        # Look back 4 days to find last game
        target = datetime.date.fromisoformat(game_date_str) if game_date_str else datetime.date.today()
        for days_back in range(1, 5):
            check_date = (target - datetime.timedelta(days=days_back)).isoformat()
            data = mlb_api("/schedule", {
                "sportId":"1","date":check_date,"teamId":str(team_id),
                "hydrate":"team","gameType":"R",
            })
            games = []
            for de in data.get("dates",[]):
                for g in de.get("games",[]):
                    status = g.get("status",{}).get("abstractGameState","")
                    if status in ("Final","Live","In Progress"):
                        games.append(g)
            if games:
                # Found last game
                rest_days = days_back - 1  # 0 = back-to-back
                # Check if it was a road game
                last_game = games[0]
                was_away = last_game.get("teams",{}).get("away",{}).get("team",{}).get("id") == team_id
                return {
                    "rest_days": rest_days,
                    "back_to_back": rest_days == 0,
                    "was_away_last": was_away,
                }
        return {"rest_days": 3, "back_to_back": False, "was_away_last": False}
    except:
        return {"rest_days": 2, "back_to_back": False, "was_away_last": False}


def fetch_line_movement(away_team, home_team):
    """
    Fetch current odds and compare against historical opening lines
    to detect sharp money movement.
    Uses the Odds API historical endpoint if available.
    Returns movement data: direction, magnitude, sharp_signal.
    """
    # We detect movement by comparing DraftKings vs market consensus
    # If DK moved significantly vs other books, sharp action happened
    if not ODDS_API_KEY:
        return {}
    try:
        r = requests.get(
            "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/",
            params={
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": "h2h,totals",
                "oddsFormat": "american",
                "bookmakers": "draftkings,fanduel,betmgm,caesars,pinnacle",
            },
            timeout=10
        )
        if not r.ok:
            return {}
        
        away_norm = normalize_team(away_team)
        home_norm = normalize_team(home_team)
        
        for event in r.json():
            ev_home = normalize_team(event.get("home_team",""))
            ev_away = normalize_team(event.get("away_team",""))
            if ev_home != home_norm or ev_away != away_norm:
                continue
            
            # Collect prices per book
            book_prices = {}
            for bm in event.get("bookmakers",[]):
                for market in bm.get("markets",[]):
                    if market["key"] == "h2h":
                        for o in market.get("outcomes",[]):
                            book = bm["key"]
                            if book not in book_prices:
                                book_prices[book] = {}
                            book_prices[book][o["name"]] = o["price"]
            
            if len(book_prices) < 2:
                return {}
            
            # Calculate consensus (average) vs Pinnacle (sharpest book)
            all_home_prices = [v.get(ev_home,0) for v in book_prices.values() if v.get(ev_home)]
            all_away_prices = [v.get(ev_away,0) for v in book_prices.values() if v.get(ev_away)]
            
            if not all_home_prices or not all_away_prices:
                return {}
            
            consensus_home = round(sum(all_home_prices)/len(all_home_prices))
            consensus_away = round(sum(all_away_prices)/len(all_away_prices))
            pinnacle_home = book_prices.get("pinnacle",{}).get(ev_home)
            pinnacle_away = book_prices.get("pinnacle",{}).get(ev_away)
            
            result = {
                "consensus_home": consensus_home,
                "consensus_away": consensus_away,
                "book_count": len(book_prices),
            }
            
            # Sharp signal: Pinnacle significantly different from consensus
            if pinnacle_home and abs(pinnacle_home - consensus_home) >= 10:
                if pinnacle_home > consensus_home:
                    result["sharp_signal"] = f"Sharp money on {ev_home} — Pinnacle {pinnacle_home} vs consensus {consensus_home}"
                    result["sharp_side"] = ev_home
                else:
                    result["sharp_signal"] = f"Sharp money on {ev_away} — Pinnacle {pinnacle_away} vs consensus {consensus_away}"
                    result["sharp_side"] = ev_away
            
            return result
        return {}
    except:
        return {}


def get_pitcher_stats(name, stats, is_home=False):
    """Get full pitcher profile: season stats + recent form + splits."""
    def find_in(pool, n):
        if n in pool: return pool[n]
        last = n.split()[-1].lower() if n else ""
        for k,v in pool.items():
            if k.split()[-1].lower() == last and last: return v
        return {}

    s25 = find_in(stats.get("sp_2025",{}), name)
    s26 = find_in(stats.get("sp_2026",{}), name)

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
            if is_home and "home_era" in splits:
                primary["relevant_split"] = "Home ERA: "+str(splits["home_era"])+" K/9: "+str(splits.get("home_k9",""))
            elif not is_home and "away_era" in splits:
                primary["relevant_split"] = "Away ERA: "+str(splits["away_era"])+" K/9: "+str(splits.get("away_k9",""))

    # Merge Baseball Savant Statcast data
    def find_savant(savant_pool, name):
        if name in savant_pool: return savant_pool[name]
        last = name.split()[-1].lower() if name else ""
        for k,v in savant_pool.items():
            if k.split()[-1].lower() == last and last: return v
        return {}

    sv26 = find_savant(stats.get("savant_pitchers_2026",{}), name)
    sv25 = find_savant(stats.get("savant_pitchers_2025",{}), name)
    sv = sv26 if sv26 else sv25
    if sv:
        if sv.get("xfip"): primary["xfip"] = sv["xfip"]
        if sv.get("xera"): primary["xera"] = sv["xera"]
        if sv.get("barrel_pct"): primary["barrel_pct"] = sv["barrel_pct"]
        if sv.get("hard_hit_pct"): primary["hard_hit_pct"] = sv["hard_hit_pct"]
        if sv.get("whiff_pct"): primary["whiff_pct"] = sv["whiff_pct"]
        if sv.get("avg_fastball_velo"): primary["avg_fastball_velo"] = sv["avg_fastball_velo"]
        # Fetch per-start velo trend using Savant player ID
        savant_id = sv.get("player_id_savant") or sv.get("player_id")
        season_velo = sv.get("avg_fastball_velo")
        if savant_id and season_velo:
            velo_data = fetch_pitcher_velo_trend(savant_id, season_velo)
            if velo_data:
                primary["velo_trend"] = velo_data.get("velo_trend","")
                primary["velo_flag"] = velo_data.get("velo_flag","")
                primary["recent_avg_velo"] = velo_data.get("recent_avg_velo")
                primary["velo_drop"] = velo_data.get("velo_drop")

    # Add pitch arsenal data
    arsenal = stats.get("pitch_arsenal_2026",{})
    if not arsenal:
        arsenal = stats.get("pitch_arsenal_2025",{})
    def find_arsenal(pool, n):
        if n in pool: return pool[n]
        last = n.split()[-1].lower() if n else ""
        for k,v in pool.items():
            if k.split()[-1].lower() == last and last: return v
        return {}
    arsen = find_arsenal(arsenal, name)
    if arsen:
        primary["fastball_pct"]  = arsen.get("fastball_pct")
        primary["breaking_pct"]  = arsen.get("breaking_pct")
        primary["slider_pct"]    = arsen.get("slider_pct")
        primary["slider_whiff"]  = arsen.get("slider_whiff")
        primary["primary_pitch"] = arsen.get("primary_pitch")
        primary["primary_pct"]   = arsen.get("primary_pct")

    # Add reliability score — core fix for early-season overconfidence
    reliability, reliability_label = sp_reliability_score(primary)
    primary["reliability"] = reliability
    primary["reliability_label"] = reliability_label

    # Add throws (L/R) — needed for platoon split calculation
    pid_for_throws = primary.get("player_id") or s25.get("player_id") or s26.get("player_id")
    if pid_for_throws and not primary.get("throws"):
        primary["throws"] = fetch_pitcher_throws(pid_for_throws)

    return primary

def get_team_stats(team, stats, stat_type):
    s26 = stats.get(stat_type+"_2026",{}).get(team,{})
    s25 = stats.get(stat_type+"_2025",{}).get(team,{})

    # Validate 2026 data — must have real OPS/ERA, not zero
    s26_valid = bool(s26) and (
        (stat_type == "team_batting" and safe_float(s26.get("ops",0)) > 0.400) or
        (stat_type == "team_pitching" and safe_float(s26.get("era",0)) > 0)
    )
    s25_valid = bool(s25)

    result = {}

    if s26_valid and s25_valid:
        g26 = s26.get("games_played", 0) or 0
        # Blend based on 2026 sample size
        # Under 20 games: 40% 2026 / 60% 2025 (still early)
        # 20-40 games: 60% 2026 / 40% 2025
        # 40+ games: 80% 2026 / 20% 2025
        if g26 >= 40:
            w26, w25 = 0.80, 0.20
        elif g26 >= 20:
            w26, w25 = 0.60, 0.40
        else:
            w26, w25 = 0.40, 0.60

        result = dict(s26)
        # Blend key offensive metrics
        if stat_type == "team_batting":
            for key in ["ops","avg","obp","slg","woba","runs_per_game"]:
                v26 = safe_float(s26.get(key, 0))
                v25 = safe_float(s25.get(key, 0))
                if v26 > 0 and v25 > 0:
                    result[key] = round(v26 * w26 + v25 * w25, 3)
        if stat_type == "team_pitching":
            for key in ["era","whip","k9","bb9"]:
                v26 = safe_float(s26.get(key, 0))
                v25 = safe_float(s25.get(key, 0))
                if v26 > 0 and v25 > 0:
                    result[key] = round(v26 * w26 + v25 * w25, 3)
        result["note"] = f"Blended {int(w26*100)}/{int(w25*100)} 2026/2025 ({g26} games)"

    elif s26_valid:
        result = dict(s26)
        result["note"] = "2026 YTD"
    elif s25_valid:
        result = dict(s25)
        result["note"] = "2025 full season (2026 data unavailable)"
    else:
        return {}

    # Merge Savant batting data for hitting stats
    if stat_type == "team_batting":
        sv26 = stats.get("savant_batting_2026",{})
        sv25 = stats.get("savant_batting_2025",{})
        abbrev = SAVANT_TEAM_MAP_REV.get(team,"")
        sv = sv26.get(team) or sv25.get(team) or sv26.get(abbrev) or sv25.get(abbrev)
        if not sv:
            for k,v in {**sv26,**sv25}.items():
                full = SAVANT_TEAM_MAP.get(k,k)
                if full == team or k in team or team.split()[-1] in k:
                    sv = v; break
        if sv:
            if sv.get("xwoba"): result["xwoba"] = sv["xwoba"]
            if sv.get("barrel_pct"): result["barrel_pct"] = sv["barrel_pct"]
            if sv.get("hard_hit_pct"): result["hard_hit_pct"] = sv["hard_hit_pct"]
            if sv.get("exit_velo"): result["exit_velo"] = sv["exit_velo"]

    return result

# ── Lineups ───────────────────────────────────────────────────────────────────

_BATTER_SPLITS_CACHE = {}  # pid → {vs_lhp_ops, vs_rhp_ops, vs_lhp_woba, vs_rhp_woba}
_MATCHUP_CACHE = {}  # (batter_pid, pitcher_pid) → matchup stats

def fetch_batter_splits(pid, season=2026):
    """
    Fetch individual batter vs LHP and vs RHP splits.
    This is the data most models miss — season OPS hides massive platoon variance.
    A .780 OPS hitter might be .920 vs RHP but .580 vs LHP.
    Cached per player — fetched once, reused all day.
    """
    if not pid: return {}
    cache_key = f"{pid}_{season}"
    if cache_key in _BATTER_SPLITS_CACHE:
        return _BATTER_SPLITS_CACHE[cache_key]

    try:
        data = mlb_api(f"/people/{pid}/stats", {
            "stats": "statSplits",
            "season": str(season),
            "group": "hitting",
            "sportId": "1",
            "sitCodes": "vl,vr",
        })
        result = {}
        stats_list = data.get("stats", [])
        if not stats_list:
            # Try 2025 as fallback
            if season == 2026:
                _BATTER_SPLITS_CACHE[cache_key] = {}
                return fetch_batter_splits(pid, 2025)
            _BATTER_SPLITS_CACHE[cache_key] = {}
            return {}

        for split in stats_list[0].get("splits", []):
            sit = split.get("split", {}).get("code", "")
            stat = split.get("stat", {})
            ab = int(stat.get("atBats", 0) or 0)
            if ab < 10: continue  # need meaningful sample
            ops = safe_float(stat.get("ops"))
            avg = safe_float(stat.get("avg"))
            # Calculate wOBA
            bb  = int(stat.get("baseOnBalls", 0) or 0)
            hbp = int(stat.get("hitByPitch", 0) or 0)
            h   = int(stat.get("hits", 0) or 0)
            d   = int(stat.get("doubles", 0) or 0)
            t   = int(stat.get("triples", 0) or 0)
            hr  = int(stat.get("homeRuns", 0) or 0)
            sf  = int(stat.get("sacFlies", 0) or 0)
            singles = h - d - t - hr
            pa = ab + bb + hbp + sf
            woba = round((0.69*bb + 0.72*hbp + 0.89*singles + 1.27*d + 1.62*t + 2.10*hr) / pa, 3) if pa > 0 else None

            if sit == "vl":  # vs LHP
                result["vs_lhp_ops"] = ops
                result["vs_lhp_avg"] = avg
                result["vs_lhp_woba"] = woba
                result["vs_lhp_ab"] = ab
            elif sit == "vr":  # vs RHP
                result["vs_rhp_ops"] = ops
                result["vs_rhp_avg"] = avg
                result["vs_rhp_woba"] = woba
                result["vs_rhp_ab"] = ab

        _BATTER_SPLITS_CACHE[cache_key] = result
        return result
    except Exception:
        _BATTER_SPLITS_CACHE[cache_key] = {}
        return {}


def fetch_batter_vs_pitcher(batter_pid, pitcher_pid):
    """
    Fetch historical matchup stats between a specific batter and pitcher.
    This is elite-level signal — career 2-for-20 against Kirby is real.
    Only use when AB >= 10 to ensure meaningful sample size.
    Cached by (batter_pid, pitcher_pid) pair.
    """
    if not batter_pid or not pitcher_pid: return {}
    cache_key = f"{batter_pid}_{pitcher_pid}"
    if cache_key in _MATCHUP_CACHE: return _MATCHUP_CACHE[cache_key]

    try:
        data = mlb_api(f"/people/{batter_pid}/stats", {
            "stats": "vsPlayer",
            "opposingPlayerId": str(pitcher_pid),
            "group": "hitting",
            "sportId": "1",
        })
        stats_list = data.get("stats", [])
        if not stats_list:
            _MATCHUP_CACHE[cache_key] = {}
            return {}

        splits = stats_list[0].get("splits", [])
        if not splits:
            _MATCHUP_CACHE[cache_key] = {}
            return {}

        stat = splits[0].get("stat", {})
        ab  = int(stat.get("atBats", 0) or 0)

        # Minimum 10 AB for meaningful sample
        if ab < 10:
            _MATCHUP_CACHE[cache_key] = {}
            return {}

        h   = int(stat.get("hits", 0) or 0)
        hr  = int(stat.get("homeRuns", 0) or 0)
        bb  = int(stat.get("baseOnBalls", 0) or 0)
        so  = int(stat.get("strikeOuts", 0) or 0)
        hbp = int(stat.get("hitByPitch", 0) or 0)
        d   = int(stat.get("doubles", 0) or 0)
        t   = int(stat.get("triples", 0) or 0)
        sf  = int(stat.get("sacFlies", 0) or 0)
        singles = h - d - t - hr
        pa = ab + bb + hbp + sf

        avg  = round(h/ab, 3) if ab > 0 else 0
        obp  = round((h+bb+hbp)/(ab+bb+hbp+sf), 3) if (ab+bb+hbp+sf) > 0 else 0
        slg  = round((singles + 2*d + 3*t + 4*hr)/ab, 3) if ab > 0 else 0
        ops  = round(obp + slg, 3)
        woba = round((0.69*bb + 0.72*hbp + 0.89*singles + 1.27*d + 1.62*t + 2.10*hr)/pa, 3) if pa > 0 else 0
        k_pct = round(so/ab*100, 1) if ab > 0 else 0

        result = {
            "ab": ab, "h": h, "hr": hr, "bb": bb, "so": so,
            "avg": avg, "ops": ops, "woba": woba, "k_pct": k_pct,
        }
        _MATCHUP_CACHE[cache_key] = result
        return result
    except Exception:
        _MATCHUP_CACHE[cache_key] = {}
        return {}


def analyze_lineup_vs_sp(batters, pitcher_pid):
    """
    Aggregate historical matchup data for entire lineup vs a specific SP.
    Returns summary: how many batters have history, avg OPS vs this SP,
    and lists of batters who struggle or dominate vs this pitcher.
    Only counts batters with 10+ AB.
    """
    if not batters or not pitcher_pid: return {}

    import concurrent.futures as _cf
    matchups = {}
    batter_pids = [(b.get("name",""), b.get("pid")) for b in batters if b.get("pid")]

    if not batter_pids: return {}

    with _cf.ThreadPoolExecutor(max_workers=min(len(batter_pids), 18)) as ex:
        futures = {ex.submit(fetch_batter_vs_pitcher, pid, pitcher_pid): name
                   for name, pid in batter_pids}
        for fut in _cf.as_completed(futures, timeout=8):
            name = futures[fut]
            try:
                result = fut.result()
                if result:
                    matchups[name] = result
            except Exception:
                pass

    if not matchups: return {}

    ops_list = [m["ops"] for m in matchups.values() if m.get("ops")]
    avg_ops = round(sum(ops_list)/len(ops_list), 3) if ops_list else None

    # Categorize batters
    struggles = [n for n,m in matchups.items() if m.get("ops",1) < 0.550 and m.get("ab",0) >= 10]
    dominates = [n for n,m in matchups.items() if m.get("ops",0) > 0.900 and m.get("ab",0) >= 10]

    return {
        "batters_with_history": len(matchups),
        "avg_ops_vs_sp": avg_ops,
        "struggles_vs_sp": struggles[:3],   # batters who are 0-fer vs this guy
        "dominates_sp": dominates[:3],      # batters who own this pitcher
        "matchup_note": (
            f"{len(struggles)} batters historically struggle vs this SP" if struggles else
            f"{len(dominates)} batters have owned this SP" if dominates else
            f"{len(matchups)} batters have history vs this SP (avg OPS {avg_ops})"
        ) if matchups else "No significant matchup history",
    }


def fetch_lineup(game_pk):
    data = mlb_api("/game/"+str(game_pk)+"/boxscore")
    lineups = {}
    for side in ["home","away"]:
        team_data = data.get("teams",{}).get(side,{})
        team_name = team_data.get("team",{}).get("name","")
        batters = []
        batting_order = team_data.get("battingOrder",[])
        players = team_data.get("players",{})
        injured_ids = set()
        for pid_str, pdata in players.items():
            status = pdata.get("gameStatus",{})
            if not status.get("isCurrentBatter") and not status.get("isCurrentPitcher"):
                il_status = pdata.get("status",{}).get("code","")
                if il_status in ("IL10","IL15","IL60","DL10","DL15","DL60","DTD","SCR"):
                    injured_ids.add(str(pid_str).replace("ID",""))

        # Collect batters first
        raw_batters = []
        for pid in batting_order[:9]:
            player = players.get("ID"+str(pid),{})
            name = player.get("person",{}).get("fullName","")
            pos = player.get("position",{}).get("abbreviation","")
            bats = player.get("batSide",{}).get("code","")
            s = player.get("seasonStats",{}).get("batting",{})
            avg = safe_float(s.get("avg","0"))
            ops = safe_float(s.get("ops","0"))
            if str(pid) in injured_ids: continue
            if name:
                raw_batters.append({
                    "pid": pid, "name": name, "pos": pos,
                    "bats": bats, "avg": avg, "ops": ops,
                })

        # Fetch batter splits in parallel — zero sequential overhead
        import concurrent.futures as _cf
        split_results = {}
        if raw_batters:
            pids = [b["pid"] for b in raw_batters]
            with _cf.ThreadPoolExecutor(max_workers=min(len(pids), 20)) as ex:
                futures = {ex.submit(fetch_batter_splits, pid): pid for pid in pids}
                for fut in _cf.as_completed(futures, timeout=8):
                    pid = futures[fut]
                    try:
                        split_results[pid] = fut.result()
                    except Exception:
                        split_results[pid] = {}

        # Attach splits to each batter
        for b in raw_batters:
            splits = split_results.get(b["pid"], {})
            batter = {
                "name": b["name"], "pos": b["pos"],
                "bats": b["bats"], "avg": b["avg"], "ops": b["ops"],
                "pid": b["pid"],  # keep pid for matchup analysis
            }
            if splits.get("vs_lhp_ops"): batter["vs_lhp_ops"] = splits["vs_lhp_ops"]
            if splits.get("vs_rhp_ops"): batter["vs_rhp_ops"] = splits["vs_rhp_ops"]
            if splits.get("vs_lhp_woba"): batter["vs_lhp_woba"] = splits["vs_lhp_woba"]
            if splits.get("vs_rhp_woba"): batter["vs_rhp_woba"] = splits["vs_rhp_woba"]
            batters.append(batter)

        lineups[side] = {"team": team_name, "batters": batters}
    return lineups

def analyze_pitch_mix_vs_lineup(sp_stats, platoon_data):
    """
    Cross-reference SP pitch mix with lineup handedness.
    A LHP throwing 70% sliders against 7 righties = elite platoon edge.
    Sliders break away from righties vs LHP = very hard to hit.
    Returns a pitch_mix_edge string for Claude to use.
    """
    if not sp_stats or not platoon_data: return {}

    throws      = (sp_stats.get("throws","") or "").upper()
    slider_pct  = sp_stats.get("slider_pct") or 0
    breaking_pct = sp_stats.get("breaking_pct") or 0
    fastball_pct = sp_stats.get("fastball_pct") or 0
    primary     = sp_stats.get("primary_pitch","")
    slider_whiff = sp_stats.get("slider_whiff") or 0

    disadvantaged_pct = platoon_data.get("disadvantaged_pct", 0)
    edge_score        = platoon_data.get("edge_score", 0)

    insights = []

    # LHP with heavy slider usage vs right-heavy lineup
    if throws == "L" and slider_pct >= 30 and disadvantaged_pct >= 60:
        if slider_whiff and slider_whiff >= 35:
            insights.append(
                f"ELITE: LHP throws {slider_pct}% sliders (back-door to RHBs, {slider_whiff}% whiff) "
                f"vs {disadvantaged_pct}% right-handed lineup"
            )
        else:
            insights.append(
                f"STRONG: LHP {slider_pct}% sliders vs {disadvantaged_pct}% RHB lineup"
            )

    # RHP with heavy slider usage vs left-heavy lineup
    elif throws == "R" and slider_pct >= 30 and disadvantaged_pct >= 60:
        insights.append(
            f"STRONG: RHP {slider_pct}% sliders vs {disadvantaged_pct}% LHB lineup"
        )

    # Heavy fastball pitcher facing lineup with platoon advantage (lineup can sit on FB)
    elif fastball_pct >= 60 and edge_score <= -1:
        insights.append(
            f"LINEUP EDGE: {fastball_pct}% fastball pitcher facing lineup with platoon advantage — hitters can sit on FB"
        )

    # Breaking ball dominant pitcher regardless of platoon
    elif breaking_pct >= 55:
        insights.append(
            f"Breaking ball heavy ({breaking_pct}%) — generates weak contact regardless of lineup hand"
        )

    return {
        "pitch_mix_edge": insights[0] if insights else "",
        "has_pitch_mix_edge": len(insights) > 0,
    }


def analyze_lineup_handedness(batters, sp_throws):
    """
    Calculate platoon advantage using ACTUAL vs-LHP/RHP split OPS where available.
    This is elite-level analysis — uses real split data not season averages.
    Same hand = platoon disadvantage for hitters (e.g. RHB vs RHP).
    Opposite hand = platoon advantage for hitters (e.g. LHB vs RHP).
    """
    if not batters or not sp_throws:
        return {}

    sp_throws = sp_throws.upper()
    same_hand = []   # disadvantaged
    opp_hand  = []   # advantaged
    unknown   = []

    for b in batters:
        bats = (b.get("bats") or "").upper()
        if not bats or bats == "S":
            opp_hand.append(b)   # switch hitters always have advantage
        elif bats == sp_throws:
            same_hand.append(b)  # disadvantage
        else:
            opp_hand.append(b)   # advantage

    total = len(batters)
    if total == 0:
        return {}

    disadvantaged_pct = round(len(same_hand) / total * 100)
    advantaged_pct    = round(len(opp_hand)  / total * 100)

    # Use SPLIT OPS (vs LHP or vs RHP) where available — fall back to season OPS
    # This is the key improvement: .780 season OPS hitter might be .920 vs RHP or .580 vs LHP
    split_key_same = "vs_lhp_ops" if sp_throws == "L" else "vs_rhp_ops"
    split_key_opp  = "vs_rhp_ops" if sp_throws == "L" else "vs_lhp_ops"
    split_woba_same = "vs_lhp_woba" if sp_throws == "L" else "vs_rhp_woba"
    split_woba_opp  = "vs_rhp_woba" if sp_throws == "L" else "vs_lhp_woba"

    def best_ops(batter, split_key):
        """Use split OPS if available and meaningful, else fall back to season OPS."""
        split = batter.get(split_key)
        if split and split > 0.300:  # sanity check — split OPS should be real
            return split
        return batter.get("ops", 0) or 0

    def best_woba(batter, woba_key):
        return batter.get(woba_key) or None

    same_ops_list  = [best_ops(b, split_key_same) for b in same_hand if best_ops(b, split_key_same) > 0.300]
    opp_ops_list   = [best_ops(b, split_key_opp)  for b in opp_hand  if best_ops(b, split_key_opp)  > 0.300]
    avg_same_ops   = round(sum(same_ops_list)/len(same_ops_list), 3) if same_ops_list else None
    avg_opp_ops    = round(sum(opp_ops_list)/len(opp_ops_list),   3) if opp_ops_list  else None

    # How many batters have real split data vs season average fallback
    has_split_data = sum(1 for b in batters if b.get(split_key_same) or b.get(split_key_opp))

    # Overall platoon edge assessment
    if disadvantaged_pct >= 70:
        platoon_edge = f"STRONG — SP throws {sp_throws}, {disadvantaged_pct}% of lineup at platoon disadvantage"
        edge_score = 2
    elif disadvantaged_pct >= 55:
        platoon_edge = f"MODERATE — {disadvantaged_pct}% of lineup same-handed as SP ({sp_throws})"
        edge_score = 1
    elif advantaged_pct >= 70:
        platoon_edge = f"LINEUP ADVANTAGE — {advantaged_pct}% of lineup has platoon edge vs {sp_throws}HP"
        edge_score = -1
    else:
        platoon_edge = f"NEUTRAL — balanced lineup vs {sp_throws}HP"
        edge_score = 0

    return {
        "sp_throws": sp_throws,
        "disadvantaged_pct": disadvantaged_pct,
        "advantaged_pct": advantaged_pct,
        "disadvantaged_batters": [b["name"] for b in same_hand[:4]],
        "advantaged_batters": [b["name"] for b in opp_hand[:4]],
        "avg_ops_vs_this_sp_type": avg_same_ops,   # actual split OPS for disadvantaged group
        "avg_ops_with_platoon_edge": avg_opp_ops,  # actual split OPS for advantaged group
        "batters_with_split_data": has_split_data,
        "platoon_edge": platoon_edge,
        "edge_score": edge_score,
        "data_note": f"{has_split_data}/{total} batters have real split data" if has_split_data < total else "All batters have split data",
    }

# ── Bullpen fatigue ───────────────────────────────────────────────────────────

# Module-level bullpen cache to avoid redundant API calls
_BULLPEN_CACHE = {}

def score_bullpen_quality(fatigued_arms, available_arms, rp_stats_2026, rp_stats_2025):
    """
    Score bullpen quality based on ERA/FIP of fatigued and available arms.
    Returns a quality score and context string.
    SEVERE + elite bullpen = still dangerous
    SEVERE + poor bullpen = massive OVER edge
    FRESH + elite bullpen = strong UNDER lean
    """
    def get_era(name):
        for pool in [rp_stats_2026, rp_stats_2025]:
            if name in pool:
                return pool[name].get("era", 4.50)
            # Partial name match
            last = name.split()[-1].lower() if name else ""
            for k,v in pool.items():
                if k.split()[-1].lower() == last and last:
                    return v.get("era", 4.50)
        return 4.50  # league average if unknown

    all_arms = list(set(fatigued_arms + available_arms))
    if not all_arms:
        return {"quality": "UNKNOWN", "avg_era": 4.50, "quality_note": "No reliever data"}

    eras = [get_era(arm) for arm in all_arms if arm]
    if not eras:
        return {"quality": "UNKNOWN", "avg_era": 4.50, "quality_note": "No ERA data"}

    avg_era = round(sum(eras) / len(eras), 2)
    fatigued_eras = [get_era(arm) for arm in fatigued_arms if arm]
    avg_fatigued_era = round(sum(fatigued_eras)/len(fatigued_eras), 2) if fatigued_eras else avg_era

    if avg_era <= 3.50:
        quality = "ELITE"
    elif avg_era <= 4.00:
        quality = "GOOD"
    elif avg_era <= 4.50:
        quality = "AVERAGE"
    elif avg_era <= 5.00:
        quality = "BELOW_AVERAGE"
    else:
        quality = "POOR"

    note = f"Pen avg ERA {avg_era}"
    if fatigued_eras:
        note += f" (fatigued arms avg ERA {avg_fatigued_era})"

    return {
        "quality": quality,
        "avg_era": avg_era,
        "fatigued_avg_era": avg_fatigued_era,
        "quality_note": note,
    }


def fetch_bullpen_fatigue(team_id, rp_stats_2026=None, rp_stats_2025=None):
    cache_key = f"{team_id}_fatigue"
    if cache_key in _BULLPEN_CACHE:
        return _BULLPEN_CACHE[cache_key]
    fatigued = []
    available = []
    for days_ago in range(1, 3):
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
                        name = pdata.get("person",{}).get("fullName","")
                        if ip > 0 and gs == 0 and pc > 0:
                            fatigued.append({"name":name,"pitches":pc,"ip":ip,"days_ago":days_ago})
                        elif gs == 0 and pc == 0 and ip == 0 and name:
                            available.append(name)

    high_usage = [p for p in fatigued if p["pitches"] >= 20 and p["days_ago"] <= 2]
    fatigue_level = "SEVERE" if len(high_usage) >= 2 else "MODERATE" if len(high_usage) == 1 else "FRESH"

    # Score bullpen quality if reliever stats available
    quality_data = {}
    if rp_stats_2026 is not None:
        fatigued_names = [p["name"] for p in high_usage]
        quality_data = score_bullpen_quality(
            fatigued_names, available[:5],
            rp_stats_2026 or {}, rp_stats_2025 or {}
        )

    result = {
        "recent_usage": fatigued[:10],
        "high_usage_count": len(high_usage),
        "fatigued_arms": [p["name"] for p in high_usage],
        "fatigue_level": fatigue_level,
        **quality_data,
    }
    _BULLPEN_CACHE[cache_key] = result
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
                home     = normalize_team(g["teams"]["home"]["team"]["name"])
                away     = normalize_team(g["teams"]["away"]["team"]["name"])
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
                game_num = g.get("gameNumber", 1)
                games.append({
                    "game_pk": g.get("gamePk"),
                    "game_num": game_num,
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

_STANDINGS_CACHE = {}

def fetch_team_streak(team_id):
    """Fetch recent form — current W/L record and streak from standings. Cached."""
    global _STANDINGS_CACHE
    # Load standings once per run
    if not _STANDINGS_CACHE:
        try:
            data = mlb_api("/standings", {
                "leagueId":"103,104","season":"2026",
                "standingsTypes":"regularSeason","hydrate":"team,record,streak",
            })
            for div in data.get("records",[]):
                for tr in div.get("teamRecords",[]):
                    tid = tr.get("team",{}).get("id")
                    if not tid: continue
                    streak = tr.get("streak",{})
                    last10 = tr.get("lastTen","")
                    parts = last10.split("-") if last10 and "-" in last10 else ["",""]
                    _STANDINGS_CACHE[tid] = {
                        "wins": tr.get("wins",0),
                        "losses": tr.get("losses",0),
                        "streak_type": streak.get("streakType",""),
                        "streak_number": streak.get("streakNumber",0),
                        "last10_wins": parts[0],
                        "last10_losses": parts[1] if len(parts)>1 else "",
                    }
        except Exception as e:
            print("Standings fetch error: "+str(e))
    return _STANDINGS_CACHE.get(team_id, {})

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

def fetch_odds_espn_fallback():
    """
    Fallback odds source using ESPN's public API.
    No API key required. Returns ML and totals only (no run line).
    Used when The Odds API is unavailable or quota exceeded.
    """
    try:
        import datetime as _dt
        today = _dt.date.today().isoformat()
        url = f"https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard?dates={today.replace('-','')}"
        r = requests.get(url,
                        headers={"User-Agent": "Mozilla/5.0"},
                        timeout=15)
        if not r.ok:
            print(f"ESPN odds fallback HTTP {r.status_code}")
            return {}, {}

        data = r.json()
        odds_map = {}
        event_ids = {}

        for event in data.get("events", []):
            competition = event.get("competitions", [{}])[0]
            competitors = competition.get("competitors", [])
            if len(competitors) < 2: continue

            home = away = ""
            for c in competitors:
                name = normalize_team(c.get("team", {}).get("displayName", ""))
                if c.get("homeAway") == "home":
                    home = name
                else:
                    away = name

            if not home or not away: continue
            key = away + "@" + home
            event_ids[key] = str(event.get("id", ""))

            # ESPN odds
            odds_data = competition.get("odds", [{}])[0] if competition.get("odds") else {}
            ml = {}
            total = {}

            # Moneyline
            home_odds = odds_data.get("homeTeamOdds", {})
            away_odds = odds_data.get("awayTeamOdds", {})
            if home_odds.get("moneyLine"):
                ml[home] = int(home_odds["moneyLine"])
            if away_odds.get("moneyLine"):
                ml[away] = int(away_odds["moneyLine"])

            # Total
            over_under = odds_data.get("overUnder")
            if over_under:
                total["line"] = str(over_under)
                total["over"] = odds_data.get("overOdds", -110)
                total["under"] = odds_data.get("underOdds", -110)

            if ml or total:
                odds_map[key] = {"moneyline": ml, "total": total, "runline": {}}

        if odds_map:
            print(f"ESPN fallback: fetched odds for {len(odds_map)} games")
        else:
            print("ESPN fallback: no odds data returned")
        return odds_map, event_ids
    except Exception as e:
        print(f"ESPN odds fallback error: {str(e)}")
        return {}, {}


def fetch_odds():
    if not ODDS_API_KEY:
        print("No ODDS_API_KEY — trying ESPN fallback...")
        return fetch_odds_espn_fallback()
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
        if r.status_code == 401:
            print("Odds API 401 — quota exceeded or key invalid. Trying ESPN fallback...")
            return fetch_odds_espn_fallback()
        r.raise_for_status()
        print("Odds API remaining: "+str(r.headers.get("x-requests-remaining","?")))
        odds_map = {}
        event_ids = {}  # track event IDs for NRFI fetch
        for event in r.json():
            home = normalize_team(event.get("home_team",""))
            away = normalize_team(event.get("away_team",""))
            bms  = event.get("bookmakers",[])
            ml   = {}; total = {}; runline = {}

            # Store event ID for NRFI lookup
            event_ids[away+"@"+home] = event.get("id","")

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
        return odds_map, event_ids
    except Exception as e:
        print("Odds error: "+str(e))
        return {}, {}

def fetch_nrfi_odds(event_ids):
    """
    Fetch real NRFI/YRFI book lines for each game in parallel.
    Uses totals_1st_1_innings market (over/under 0.5 runs in 1st inning).
    All games fetched simultaneously — max 10 seconds total wall time.
    """
    if not ODDS_API_KEY or not event_ids:
        return {}

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def fetch_one(game_key, event_id):
        if not event_id:
            return game_key, None
        try:
            r = requests.get(
                f"https://api.the-odds-api.com/v4/sports/baseball_mlb/events/{event_id}/odds",
                params={
                    "apiKey": ODDS_API_KEY,
                    "regions": "us",
                    "markets": "totals_1st_1_innings,h2h_1st_5_innings,totals_1st_5_innings",
                    "oddsFormat": "american",
                    "bookmakers": "draftkings,fanduel,betmgm,caesars,betonlineag",
                },
                timeout=6
            )
            if not r.ok:
                return game_key, None
            best_nrfi = None; best_yrfi = None
            f5_ml_away = None; f5_ml_home = None
            f5_total_line = None; f5_over = None; f5_under = None
            # Parse event teams for F5 ML matching
            event_data = r.json()
            home_team = normalize_team(event_data.get("home_team",""))
            away_team = normalize_team(event_data.get("away_team",""))

            for bm in event_data.get("bookmakers", []):
                for market in bm.get("markets", []):
                    # NRFI/YRFI
                    if market["key"] == "totals_1st_1_innings":
                        for o in market.get("outcomes", []):
                            price = o.get("price")
                            name = o.get("name","").upper()
                            if name == "UNDER":
                                if best_nrfi is None or price > best_nrfi:
                                    best_nrfi = price
                            elif name == "OVER":
                                if best_yrfi is None or price > best_yrfi:
                                    best_yrfi = price
                    # F5 ML
                    elif market["key"] == "h2h_1st_5_innings":
                        for o in market.get("outcomes", []):
                            price = o.get("price")
                            nm = normalize_team(o.get("name",""))
                            if nm == away_team:
                                if f5_ml_away is None or price > f5_ml_away:
                                    f5_ml_away = price
                            elif nm == home_team:
                                if f5_ml_home is None or price > f5_ml_home:
                                    f5_ml_home = price
                    # F5 Total
                    elif market["key"] == "totals_1st_5_innings":
                        for o in market.get("outcomes", []):
                            price = o.get("price")
                            name = o.get("name","").upper()
                            if not f5_total_line:
                                f5_total_line = str(o.get("point",""))
                            if name == "OVER":
                                if f5_over is None or price > f5_over:
                                    f5_over = price
                            elif name == "UNDER":
                                if f5_under is None or price > f5_under:
                                    f5_under = price

            result = {}
            if best_nrfi or best_yrfi:
                result["nrfi_price"] = best_nrfi
                result["yrfi_price"] = best_yrfi
                result["source"] = "book"
            if f5_ml_away or f5_ml_home:
                result["f5_ml_away"] = f5_ml_away
                result["f5_ml_home"] = f5_ml_home
                result["f5_away_team"] = away_team
                result["f5_home_team"] = home_team
            if f5_total_line:
                result["f5_total_line"] = f5_total_line
                result["f5_over"] = f5_over
                result["f5_under"] = f5_under
            if result:
                return game_key, result
            return game_key, None
        except Exception:
            return game_key, None

    nrfi_map = {}
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(fetch_one, k, v): k for k, v in event_ids.items()}
            for future in as_completed(futures, timeout=10):
                try:
                    game_key, result = future.result()
                    if result:
                        nrfi_map[game_key] = result
                except Exception:
                    pass
    except Exception as e:
        print(f"NRFI parallel fetch error: {str(e)}")

    if nrfi_map:
        print(f"Fetched real NRFI lines for {len(nrfi_map)} games")
    return nrfi_map

# ── AI ────────────────────────────────────────────────────────────────────────

# System prompt loaded from file — edit prompts/system_prompt.md to change AI behavior
def _load_system_prompt():
    p = Path(__file__).parent / "prompts" / "system_prompt.md"
    if p.exists():
        return p.read_text()
    raise FileNotFoundError("prompts/system_prompt.md not found")
SYSTEM_PROMPT = _load_system_prompt()

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

def _try_claude(user_msg, retries=3):
    if not ANTHROPIC_KEY:
        print("Claude: no API key")
        return None, None
    import time
    for attempt in range(retries):
        try:
            # Claude 4.x requires streaming to avoid HTTP timeouts on large max_tokens
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key":ANTHROPIC_KEY,"anthropic-version":"2023-06-01",
                         "content-type":"application/json"},
                json={"model":"claude-sonnet-4-5","max_tokens":4000,
                      "temperature":0,
                      "stream":True,
                      "system":SYSTEM_PROMPT,
                      "messages":[{"role":"user","content":user_msg}]},
                timeout=120,
                stream=True
            )
            print(f"Claude HTTP {r.status_code}")
            if r.status_code in (529, 503):
                wait = 30 * (attempt + 1)
                print(f"Claude unavailable ({r.status_code}) — waiting {wait}s")
                time.sleep(wait)
                continue
            if not r.ok:
                body_preview = ""
                try: body_preview = r.text[:300]
                except: pass
                print(f"Claude error {r.status_code}: {body_preview}")
                if attempt < retries - 1:
                    time.sleep(20)
                    continue
                return None, None
            # Collect streamed SSE response
            full_text = ""
            for line in r.iter_lines():
                if not line: continue
                line = line.decode("utf-8") if isinstance(line, bytes) else line
                if line.startswith("data: "):
                    data = line[6:]
                    if data.strip() == "[DONE]": break
                    try:
                        chunk = json.loads(data)
                        if chunk.get("type") == "content_block_delta":
                            full_text += chunk.get("delta",{}).get("text","")
                        elif chunk.get("type") == "error":
                            print(f"Claude stream error: {chunk}")
                            break
                    except: pass
            if not full_text.strip():
                print(f"Claude streamed empty response")
                if attempt < retries - 1:
                    time.sleep(20)
                    continue
                return None, None
            picks = _parse_ai_response(full_text)
            print(f"Claude returned {len(picks)} picks")
            return picks, "Claude Sonnet 4.5"
        except Exception as e:
            print(f"Claude exception (attempt {attempt+1}/{retries}): {type(e).__name__}: {str(e)}")
            if attempt < retries - 1:
                time.sleep(20)
            else:
                return None, None
    return None, None

def _try_groq(user_msg):
    if not GROQ_KEY: return None, None
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization":"Bearer "+GROQ_KEY,"Content-Type":"application/json"},
            json={"model":"llama-3.1-8b-instant",
                  "messages":[{"role":"system","content":SYSTEM_PROMPT},
                               {"role":"user","content":user_msg}],
                  "temperature":0,"max_tokens":4000},
            timeout=60
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
    MIN_EV = {"ML":5,"Run Line":5,"Total OVER":5,"Total UNDER":5,"NRFI":7,"YRFI":5,"F5 OVER":5,"F5 UNDER":5}
    MAX_ML_ODDS = -150  # tightened — negative ML favorites have been losing, juice is brutal
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

        # Road underdog gate — model keeps losing on road dogs vs elite home teams
        # Rangers at LAD +162, Rockies at SD +166 — the plus money is a trap
        if tier in ("MAX","A","B","C") and p.get("bet_type") == "ML":
            try:
                line = float(str(p.get("line","")).replace("+",""))
                if line >= 120:  # road underdog at +120 or higher
                    sp_analysis = (p.get("sp_analysis","") or "").lower()
                    # Check if home SP is an ace (xFIP < 3.20 is elite)
                    import re as _re
                    xfip_matches = _re.findall(r'xfip[:\s]+([0-9]\.[0-9]+)', sp_analysis)
                    home_xfip = float(xfip_matches[0]) if xfip_matches else 4.5
                    ev = float(p.get("ev_pct",0) or 0)
                    # Block road underdogs facing ace home SPs unless massive EV
                    if home_xfip <= 3.20 and ev < 10:
                        game = p.get("game","")
                        print(f"ROAD DOG BLOCK: {game} — road underdog +{int(line)} vs ace home SP (xFIP {home_xfip}), converting to SKIP")
                        p["tier"] = "SKIP"; p["bet_type"] = "SKIP"; p["units"] = 0
                        p["avoid_reason"] = f"Road underdog vs elite home SP (xFIP {home_xfip}) — no edge at plus money"
            except: pass
        lineup = (p.get("lineup_analysis","") or "").lower()
        bet_type = p.get("bet_type","")
        if tier in ("MAX","A","B","C") and bet_type in ("ML","Run Line"):
            if "ops 0.000" in lineup or "ops: 0.000" in lineup or "0.000 ops" in lineup:
                game = p.get("game","")
                print(f"OPS DATA BLOCK: {game} — team OPS 0.000, cannot evaluate lineup, downgrading to WATCH")
                p["tier"] = "WATCH"; p["units"] = 0
                p["avoid_reason"] = "Team OPS data unavailable (0.000) — cannot evaluate lineup strength"
                enforced.append(p)
                continue

        # Hard wind contradiction check — don't rely on Claude to self-flag this
        pick_str_upper = p.get("pick","").upper()
        is_over = "OVER" in pick_str_upper and "Total" in p.get("bet_type","")
        is_under = "UNDER" in pick_str_upper and "Total" in p.get("bet_type","")
        weather_impact = (p.get("weather_impact","") or "").lower()

        if is_over and ("blowing in" in weather_impact or "wind in" in weather_impact):
            # Check wind speed — only matters if meaningful
            import re
            mph_nums = re.findall(r'(\d+\.?\d*)\s*mph', weather_impact)
            wind_speed = max([float(m) for m in mph_nums], default=0) if mph_nums else 0
            if wind_speed >= 12:
                print(f"WIND CONTRADICTION: {p.get('game','')} — OVER pick but wind IN {wind_speed}mph, converting to SKIP")
                p["tier"] = "SKIP"; p["bet_type"] = "SKIP"; p["units"] = 0
                p["avoid_reason"] = f"Wind IN {wind_speed}mph directly contradicts OVER pick"

        if is_under and ("blowing out" in weather_impact or "wind out" in weather_impact):
            import re
            mph_nums = re.findall(r'(\d+\.?\d*)\s*mph', weather_impact)
            wind_speed = max([float(m) for m in mph_nums], default=0) if mph_nums else 0
            if wind_speed >= 12:
                print(f"WIND CONTRADICTION: {p.get('game','')} — UNDER pick but wind OUT {wind_speed}mph, converting to SKIP")
                p["tier"] = "SKIP"; p["bet_type"] = "SKIP"; p["units"] = 0
                p["avoid_reason"] = f"Wind OUT {wind_speed}mph directly contradicts UNDER pick"

        # Text-based contradiction check as additional safety net
        if tier in ("B","C"):
            flags_lower = (p.get("flags","") + " " + p.get("rationale","")).lower()
            if is_under and "contradicts under" in flags_lower:
                print("CONTRADICTION: "+p.get("game","")+" — contradicting factors, converting to SKIP")
                p["tier"] = "SKIP"; p["bet_type"] = "SKIP"; p["units"] = 0
            if is_over and "contradicts over" in flags_lower:
                print("CONTRADICTION: "+p.get("game","")+" — contradicting factors, converting to SKIP")
                p["tier"] = "SKIP"; p["bet_type"] = "SKIP"; p["units"] = 0

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

        # Auto-calculate implied prob from the actual line if Claude got it wrong or left it 0
        line_str = str(p.get("line","")).replace("+","")
        calc_implied = american_to_implied(line_str)
        if calc_implied > 0 and (implied == 0 or abs(calc_implied - implied) > 3):
            implied = calc_implied
            p["implied_prob_pct"] = implied

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
        min_ev = MIN_EV.get(bet_type, 5)
        effective_ev = min(ev, calc_ev) if win_prob > 0 and implied > 0 else ev
        if effective_ev < min_ev and tier in ("A","B","C"):
            print("ENFORCING: "+p.get("game","")+" — EV "+str(effective_ev)+"% below "+str(min_ev)+"% threshold for "+bet_type+", converting to SKIP")
            p["tier"] = "SKIP"
            p["bet_type"] = "SKIP"
            p["pick"] = "SKIP"
            p["units"] = 0
            p["ev_pct"] = effective_ev
            p["avoid_reason"] = "EV "+str(effective_ev)+"% below minimum "+str(min_ev)+"% threshold for "+bet_type

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
            p["tier"] = "B" if ev_val >= 5 else "SKIP"
            if p["tier"] == "SKIP":
                p["bet_type"] = "SKIP"; p["units"] = 0
                p["avoid_reason"] = f"EV {ev_val}% insufficient for Tier A — below 5% minimum"

        # ── SP Reliability Gate — core fix for early-season overconfidence ────
        # When SP data is unreliable (small sample), cap confidence accordingly
        sp_analysis = p.get("sp_analysis","").lower()
        flags_lower = p.get("flags","").lower()
        small_sample_count = sp_analysis.count("small_sample") + sp_analysis.count("small sample") + \
                             flags_lower.count("small_sample") + flags_lower.count("small sample")

        # Both SPs unreliable → cap at Tier B, cap EV at 7%
        if small_sample_count >= 2:
            if p["tier"] in ("MAX","A"):
                print(f"RELIABILITY GATE: {p.get('game','')} — both SPs SMALL SAMPLE, capping at Tier B")
                p["tier"] = "B"
                if ev_val > 7:
                    p["ev_pct"] = 7.0
                    ev_val = 7.0
        # One SP unreliable → cap at Tier A max, cap EV at 9%
        elif small_sample_count == 1:
            if p["tier"] == "MAX":
                print(f"RELIABILITY GATE: {p.get('game','')} — one SP SMALL SAMPLE, capping at Tier A")
                p["tier"] = "A"
            if ev_val > 9:
                p["ev_pct"] = 9.0
                ev_val = 9.0

        # Always enforce correct unit size regardless of what Claude said
        if p["tier"] == "MAX": p["units"] = 3.0
        elif p["tier"] == "A": p["units"] = 1.5
        elif p["tier"] == "B": p["units"] = 1.0
        elif p["tier"] == "C": p["units"] = 0.5
        elif p["tier"] in ("WATCH","SKIP"): p["units"] = 0

    # Velocity decline gate — DECLINING SP velo is a red flag Claude may miss
    # A 2mph+ drop over last 3 starts often precedes an ERA spike
    for p in enforced:
        if p.get("tier") not in ("MAX","A","B","C"): continue
        flags = (p.get("flags","") + " " + p.get("sp_analysis","") + " " + p.get("rationale","")).lower()
        if "declining" not in flags and "velo_flag" not in flags: continue
        # Only penalize if the pick is riding a specific SP's edge (UNDER or F5 UNDER)
        bet_type = p.get("bet_type","")
        if "UNDER" not in p.get("pick","").upper() and bet_type not in ("ML","F5 UNDER"): continue
        if "declining" in flags:
            game = p.get("game","")
            if p["tier"] == "MAX":
                print(f"VELO DECLINE: {game} — SP velocity declining, downgrading MAX→A")
                p["tier"] = "A"; p["units"] = 1.5
            elif p["tier"] == "A":
                print(f"VELO DECLINE: {game} — SP velocity declining, downgrading A→B")
                p["tier"] = "B"; p["units"] = 1.0

        # Validate run line price matches actual odds data
        if bet_type == "Run Line" and p.get("game"):
            pick_str = p.get("pick","").upper()
            stated_line = str(p.get("line","")).replace("+","")
            try:
                stated_price = float(stated_line)
                # Underdog +1.5 should never be worse than -200 juice
                if "+1.5" in pick_str and stated_price < -200:
                    print("LINE ERROR: "+p.get("game","")+" — "+pick_str+" showing "+str(p.get("line",""))+" which is impossible for underdog +1.5. Downgrading to WATCH.")
                    p["tier"] = "WATCH"
                    p["units"] = 0
                    p["avoid_reason"] = "Line validation failed — price inconsistent with run line direction"
                # Weak offense should never be -1.5 favorite
                lineup = p.get("lineup_analysis","").lower()
                if "-1.5" in pick_str:
                    # Extract OPS from lineup analysis
                    import re
                    ops_nums = re.findall(r'ops\s*([\d.]+)', lineup)
                    for ops_str in ops_nums:
                        try:
                            ops_val = float(ops_str)
                            if ops_val > 0 and ops_val < 0.720:
                                print(f"WEAK OFFENSE -1.5: {p.get('game','')} — OPS {ops_val} too weak to cover -1.5, converting to SKIP")
                                p["tier"] = "SKIP"; p["bet_type"] = "SKIP"
                                p["units"] = 0
                                p["avoid_reason"] = f"Run line -1.5 requires strong offense — OPS {ops_val} below 0.720 threshold"
                                break
                        except: pass
            except: pass

        enforced.append(p)

    # No daily unit cap — EV and scoring rubric are the only filters

    # ── Post-enforcement caps ─────────────────────────────────────────────────

    # 1. Rain auto-skip — 80%+ precip on an active pick is a postponement risk
    for p in enforced:
        if p.get("tier") not in ("MAX","A","B","C"): continue
        flags = (p.get("flags","") + " " + p.get("weather_impact","")).lower()
        if "100% rain" in flags or "postponement" in flags:
            precip_val = 0
            for word in flags.split():
                try:
                    v = int(word.replace("%",""))
                    if v >= 80: precip_val = v; break
                except: pass
            if precip_val >= 80 or "100% rain" in flags:
                print(f"RAIN SKIP: {p.get('game','')} — {precip_val}%+ precip, postponement risk")
                p["tier"] = "WATCH"
                p["units"] = 0
                p["avoid_reason"] = f"Rain {precip_val}%+ — postponement risk, bet voided if postponed"

    # 2. Doubleheader dedup — only keep the higher EV pick from same matchup
    seen_matchups = {}
    for p in enforced:
        if p.get("tier") not in ("MAX","A","B","C"): continue
        # Strip " (Game N)" suffix to get base matchup
        game = p.get("game","")
        base = game.split(" (Game")[0].strip()
        ev = float(p.get("ev_pct",0) or 0)
        if base in seen_matchups:
            # Keep higher EV pick, downgrade the other
            prev = seen_matchups[base]
            prev_ev = float(prev.get("ev_pct",0) or 0)
            if ev > prev_ev:
                prev["tier"] = "WATCH"
                prev["units"] = 0
                prev["avoid_reason"] = f"Doubleheader — {game} has higher EV ({ev}%)"
                seen_matchups[base] = p
                print(f"DH DEDUP: Keeping {game} ({ev}% EV), downgrading {prev.get('game','')} ({prev_ev}% EV)")
            else:
                p["tier"] = "WATCH"
                p["units"] = 0
                p["avoid_reason"] = f"Doubleheader — {seen_matchups[base].get('game','')} has higher EV ({prev_ev}%)"
                print(f"DH DEDUP: Keeping {seen_matchups[base].get('game','')} ({prev_ev}% EV), downgrading {game} ({ev}% EV)")
        else:
            seen_matchups[base] = p

    # 3. Tier A pick limit — max 3 Tier A picks per day until 50+ picks validated
    # 4. Back-to-back penalty — teams on no rest perform measurably worse
    for p in enforced:
        if p.get("tier") not in ("MAX","A","B","C"): continue
        flags = (p.get("flags","") or "").lower()
        bullpen = (p.get("bullpen_note","") or "").lower()
        pick_str = p.get("pick","").upper()
        game = p.get("game","")
        # Hard block: run line -1.5 on back-to-back teams → SKIP
        if "-1.5" in pick_str and p.get("bet_type") == "Run Line":
            if "back-to-back" in flags or "back to back" in flags:
                print(f"B2B RUN LINE BLOCK: {game} — cannot take -1.5 on back-to-back team, converting to SKIP")
                p["tier"] = "SKIP"; p["bet_type"] = "SKIP"; p["units"] = 0
                p["avoid_reason"] = "Run line -1.5 blocked — back-to-back team cannot reliably win by 2+"
                continue
        # If we're betting on a team that's on a back-to-back, downgrade confidence
        if "back-to-back" in flags or "back to back" in flags:
            if p["tier"] == "MAX":
                print(f"B2B PENALTY: {game} — back-to-back, downgrading MAX→A")
                p["tier"] = "A"; p["units"] = 1.5
            elif p["tier"] == "A":
                print(f"B2B PENALTY: {game} — back-to-back, downgrading A→B")
                p["tier"] = "B"; p["units"] = 1.0

    # 5. Closing Line Value filter — if sharp money moved the line against our pick
    # by 15+ cents, that's a real signal we're on the wrong side
    for p in enforced:
        if p.get("tier") not in ("MAX","A","B","C"): continue
        game = p.get("game","")
        bet_type = p.get("bet_type","")
        try:
            open_l = p.get("open_line","")
            close_l = p.get("close_line","")
            if not open_l or not close_l or str(open_l) in ("","N/A","null","None") or str(close_l) in ("","N/A","null","None"):
                continue
            ol = float(str(open_l).replace("+",""))
            cl = float(str(close_l).replace("+",""))
            # Calculate line movement against our pick
            # For negative lines: line getting MORE negative = market moving against us
            # For positive lines: line getting LESS positive = market moving against us
            if ol < 0 and cl < 0:
                movement = cl - ol  # e.g. -110 → -130 = movement of -20 (bad)
            elif ol > 0 and cl > 0:
                movement = ol - cl  # e.g. +130 → +110 = movement of +20 (market moved against dog)
            else:
                movement = 0  # line crossed zero, skip

            if movement <= -20:  # market moved 20+ cents against our pick
                ev = float(p.get("ev_pct",0) or 0)
                print(f"CLV ALERT: {game} — line moved {int(movement)} cents against pick (open {open_l} → close {close_l})")
                if movement <= -30:  # sharp money strongly against — downgrade one tier
                    if p["tier"] == "MAX": p["tier"] = "A"; p["units"] = 1.5
                    elif p["tier"] == "A": p["tier"] = "B"; p["units"] = 1.0
                    elif p["tier"] == "B": p["tier"] = "C"; p["units"] = 0.5
                    print(f"  → Downgraded one tier due to strong line movement against pick")
        except: pass

    # Run line cap — 4-7 record (36%), losing bet type. MAX 1 per slate, require 8%+ EV.
    for p in enforced:
        if p.get("tier") not in ("MAX","A","B","C"): continue
        if p.get("bet_type") != "Run Line": continue
        ev = float(p.get("ev_pct",0) or 0)
        if ev < 8:
            print(f"RUN LINE EV GATE: {p.get('game','')} — run line needs 8%+ EV (has {ev}%), converting to SKIP")
            p["tier"] = "SKIP"; p["bet_type"] = "SKIP"; p["units"] = 0
            p["avoid_reason"] = f"Run line requires 8%+ EV — only {ev}% (run lines are 4-7 historically)"

    MAX_RUN_LINES = 1  # max 1 run line per slate
    rl_picks = [p for p in enforced if p.get("bet_type") == "Run Line" and p.get("tier") in ("MAX","A","B","C")]
    if len(rl_picks) > MAX_RUN_LINES:
        rl_sorted = sorted(rl_picks, key=lambda x: float(x.get("ev_pct",0) or 0), reverse=True)
        for p in rl_sorted[MAX_RUN_LINES:]:
            print(f"RUN LINE CAP: {p.get('game','')} — max {MAX_RUN_LINES} run line pick, converting to SKIP")
            p["tier"] = "SKIP"; p["bet_type"] = "SKIP"; p["units"] = 0
            p["avoid_reason"] = "Run line cap — maximum 1 run line pick per slate"

    # OVER cap — audit showed 5-5 coin flip. Require stronger signal.
    # Hard requirement: park factor 1.10+ OR temp above 65F to qualify as active pick
    for p in enforced:
        if p.get("tier") not in ("MAX","A","B","C"): continue
        if p.get("bet_type") != "Total OVER": continue
        park = p.get("park_note","").lower()
        weather = p.get("weather_impact","").lower()
        # Extract park factor from park note
        import re
        pf_match = re.search(r'runs?\s*(?:factor\s*)?([0-9]\.[0-9]+)', park)
        park_factor = float(pf_match.group(1)) if pf_match else 1.0
        # Extract temp
        temp_match = re.search(r'(\d+)f', weather)
        temp = int(temp_match.group(1)) if temp_match else 72
        # Check bullpen condition
        bullpen = (p.get("bullpen_note","") or "").lower()
        both_severe = bullpen.count("severe") >= 2
        # OVER needs: Coors-level park OR warm + wind out OR both bullpens SEVERE + hitter park
        has_park_edge = park_factor >= 1.10
        has_weather_edge = temp >= 65 and "blowing out" in weather
        has_bullpen_edge = both_severe and park_factor >= 1.05
        if not (has_park_edge or has_weather_edge or has_bullpen_edge):
            print(f"OVER SIGNAL GATE: {p.get('game','')} — insufficient OVER signal (park {park_factor}, temp {temp}F), downgrading to WATCH")
            p["tier"] = "SKIP"; p["bet_type"] = "SKIP"; p["units"] = 0
            p["avoid_reason"] = f"OVER requires park factor 1.10+ or warm+wind out or both SEVERE pens + hitter park. Park: {park_factor}"

    # OVER slate cap — max 2 active OVERs per slate
    MAX_OVERS = 2
    over_picks = [p for p in enforced if p.get("bet_type") == "Total OVER" and p.get("tier") in ("MAX","A","B","C")]
    if len(over_picks) > MAX_OVERS:
        over_sorted = sorted(over_picks, key=lambda x: float(x.get("ev_pct",0) or 0), reverse=True)
        for p in over_sorted[MAX_OVERS:]:
            print(f"OVER CAP: {p.get('game','')} — max {MAX_OVERS} OVER picks, converting to SKIP")
            p["tier"] = "SKIP"; p["bet_type"] = "SKIP"; p["units"] = 0
            p["avoid_reason"] = "OVER cap — maximum 2 OVER picks per slate"

    # NRFI cap — max 2 per slate, and require EV 7%+ given brutal juice
    MAX_NRFI = 2
    nrfi_picks = [p for p in enforced if p.get("bet_type") == "NRFI" and p.get("tier") in ("MAX","A","B","C")]
    if len(nrfi_picks) > MAX_NRFI:
        nrfi_sorted = sorted(nrfi_picks, key=lambda x: float(x.get("ev_pct",0) or 0), reverse=True)
        for p in nrfi_sorted[MAX_NRFI:]:
            print(f"NRFI CAP: {p.get('game','')} — max {MAX_NRFI} NRFI picks, converting to SKIP")
            p["tier"] = "SKIP"; p["bet_type"] = "SKIP"; p["units"] = 0
            p["avoid_reason"] = "NRFI cap — maximum 2 NRFI picks per slate"

    # Plus money ML gate — 4-6 record because model was backing random underdogs
    # The fix is NOT banning plus money — it's requiring genuine structural edge
    # A real plus money edge means: the line is wrong, not just that they're the underdog
    for p in enforced:
        if p.get("tier") not in ("MAX","A","B","C"): continue
        if p.get("bet_type") != "ML": continue
        try:
            line = float(str(p.get("line","")).replace("+",""))
            ev = float(p.get("ev_pct",0) or 0)
            if line > 0:  # plus money underdog
                sp = (p.get("sp_analysis","") or "").lower()
                lineup = (p.get("lineup_analysis","") or "").lower()
                # Real plus money edge requires SP and lineup evidence
                # Not just "they're an underdog" or "the odds are juicy"
                has_sp_edge = any(x in sp for x in ["favors","gap","advantage","vs","better era","lower xfip"])
                has_lineup_edge = "ops" in lineup and ("gap" in lineup or "advantage" in lineup or "stronger" in lineup)
                if ev < 6:
                    print(f"PLUS ML EV: {p.get('game','')} — underdog needs 6%+ EV (has {ev}%), converting to SKIP")
                    p["tier"] = "SKIP"; p["bet_type"] = "SKIP"; p["units"] = 0
                    p["avoid_reason"] = f"Plus money ML needs 6%+ EV — only {ev}%. Plus money requires real structural edge, not just underdog status."
                elif not has_sp_edge and not has_lineup_edge:
                    print(f"PLUS ML STRUCTURE: {p.get('game','')} — no structural SP or lineup edge found in analysis, converting to SKIP")
                    p["tier"] = "SKIP"; p["bet_type"] = "SKIP"; p["units"] = 0
                    p["avoid_reason"] = "Plus money ML requires structural SP or lineup edge — cannot back underdog without specific data advantage"
        except: pass

    # Negative ML conviction gate
    for p in enforced:
        if p.get("tier") not in ("MAX","A","B","C"): continue
        if p.get("bet_type") != "ML": continue
        try:
            line = float(str(p.get("line","")).replace("+",""))
            ev = float(p.get("ev_pct",0) or 0)
            if line < 0:
                if ev < 7:
                    print(f"NEG ML GATE: {p.get('game','')} — favorite ML at {int(line)} needs 7%+ EV, only {ev}%, converting to SKIP")
                    p["tier"] = "SKIP"; p["bet_type"] = "SKIP"; p["units"] = 0
                    p["avoid_reason"] = f"Negative ML requires 7%+ EV. Only {ev}% — no edge at this juice."
        except: pass

    # KELLY SIZING SUSPENDED — cold stretch April 8-11, 6-15 overall
    # Flat 1.0u on all picks until we string together 3 winning days
    # Do NOT apply 1.25u sizing during losing streaks
    for p in enforced:
        if p.get("units") == 1.25:
            p["units"] = 1.0
            print(f"KELLY SUSPENDED: {p.get('game','')} — reverting to flat 1.0u during cold stretch")

    # Hard daily pick cap — 5 active picks max
    # April 11 had 8 picks and went 2-6. Volume is the enemy right now.
    MAX_DAILY_PICKS = 5
    active = [p for p in enforced if p.get("tier") in ("MAX","A","B","C")]
    if len(active) > MAX_DAILY_PICKS:
        active_sorted = sorted(active, key=lambda x: float(x.get("ev_pct",0) or 0), reverse=True)
        for p in active_sorted[MAX_DAILY_PICKS:]:
            print(f"DAILY CAP: {p.get('game','')} — max {MAX_DAILY_PICKS} active picks, converting to SKIP")
            p["tier"] = "SKIP"; p["bet_type"] = "SKIP"; p["units"] = 0
            p["avoid_reason"] = f"Daily pick cap — maximum {MAX_DAILY_PICKS} active picks per slate"

    # After April 10 audit — Tier A cap maintained until reliability data confirms
    MAX_TIER_A = 3
    tier_a_picks = [p for p in enforced if p.get("tier") == "A"]
    if len(tier_a_picks) > MAX_TIER_A:
        # Keep highest EV Tier A picks, downgrade the rest to B
        tier_a_sorted = sorted(tier_a_picks, key=lambda x: float(x.get("ev_pct",0) or 0), reverse=True)
        for p in tier_a_sorted[MAX_TIER_A:]:
            print(f"TIER A CAP: {p.get('game','')} downgraded A→B — max {MAX_TIER_A} Tier A picks per day")
            p["tier"] = "B"
            p["units"] = 1.0
            p["avoid_reason"] = ""

    # Clean up stale cap messages and empty avoid_reason
    for p in enforced:
        ar = p.get("avoid_reason","")
        if "[Daily 5u cap reached]" in str(ar):
            p["avoid_reason"] = str(ar).replace(" [Daily 5u cap reached]","").strip()
        # Fill empty avoid_reason on WATCH/SKIP picks
        if p.get("tier") in ("WATCH","SKIP") and not p.get("avoid_reason","").strip():
            if p.get("tier") == "WATCH":
                p["avoid_reason"] = "Operational hold — waiting on data or weather"
            else:
                p["avoid_reason"] = "No clear edge identified"
        # Clean up pick name for WATCH picks — remove (WATCH) suffix and WATCH prefix
        if p.get("tier") == "WATCH":
            raw = str(p.get("pick",""))
            cleaned = raw.replace("(WATCH)","").replace("(watch)","").strip()
            if cleaned.upper().startswith("WATCH "):
                cleaned = cleaned[6:].strip()
            # If pick is bare "WATCH" or empty, use bet type + key info
            if not cleaned or cleaned.upper() == "WATCH":
                bet = p.get("bet_type","")
                pick_str = p.get("pick","")
                # Try to extract a meaningful label from bet type
                if "OVER" in bet.upper() or "UNDER" in bet.upper():
                    # Keep the total line if present
                    import re as _re
                    nums = _re.findall(r'[0-9]+\.?[0-9]*', pick_str)
                    if nums:
                        cleaned = bet.split()[-1] + " " + nums[-1]
                    else:
                        cleaned = bet
                elif bet:
                    cleaned = bet
                else:
                    cleaned = ""  # let watch_card show game only
            p["pick"] = cleaned

    return enforced

def estimate_win_prob(home_sp_era, away_sp_era, home_ops, away_ops,
                      park_runs, home_recent_era=None, away_recent_era=None,
                      home_bullpen_era=None, away_bullpen_era=None,
                      home_sp_fip=None, away_sp_fip=None,
                      home_woba=None, away_woba=None):
    """
    Estimate home team win probability using Pythagorean run expectation.
    Uses FIP over ERA when available. Uses wOBA over OPS when available.
    Clamps output to 38-62% — extreme values indicate bad data not real edge.
    Claude adjusts this baseline by max ±5%.
    
    NOTE: Bullpen ERA is intentionally excluded from baseline.
    Bullpen quality is a separate signal Claude evaluates directly.
    Including it here caused 35% baselines on teams with poor pens,
    making the cap enforcement block every pick.
    """
    lg_era = 4.20; lg_ops = 0.720; lg_woba = 0.320; lg_runs_pg = 4.5

    # Prefer xFIP > FIP > ERA (not recent ERA — too noisy early season)
    h_era = home_sp_fip if home_sp_fip and home_sp_fip > 0 else home_sp_era
    a_era = away_sp_fip if away_sp_fip and away_sp_fip > 0 else away_sp_era

    # Blend in recent ERA only if meaningful sample (avoid 1-start noise)
    if home_recent_era and home_recent_era > 0 and home_recent_era < 9.0:
        h_era = h_era * 0.65 + home_recent_era * 0.35
    if away_recent_era and away_recent_era > 0 and away_recent_era < 9.0:
        a_era = a_era * 0.65 + away_recent_era * 0.35

    h_era = min(max(h_era, 1.5), 7.5)
    a_era = min(max(a_era, 1.5), 7.5)

    # Use wOBA when available — fall back to league average if OPS is zero
    if home_woba and home_woba > 0.200:
        h_off = min(max(home_woba / lg_woba, 0.70), 1.40)
    elif home_ops and home_ops > 0.400:
        h_ops = min(max(home_ops, 0.580), 0.980)
        h_off = h_ops / lg_ops
    else:
        h_off = 1.0

    if away_woba and away_woba > 0.200:
        a_off = min(max(away_woba / lg_woba, 0.70), 1.40)
    elif away_ops and away_ops > 0.400:
        a_ops = min(max(away_ops, 0.580), 0.980)
        a_off = a_ops / lg_ops
    else:
        a_off = 1.0

    pf = min(max(park_runs, 0.80), 1.35)

    # Expected runs per game — SP quality vs opposing offense
    home_runs = lg_runs_pg * (a_era / lg_era) * h_off * pf * 1.03  # home advantage
    away_runs = lg_runs_pg * (h_era / lg_era) * a_off * pf

    if home_runs <= 0 or away_runs <= 0:
        return 52.0

    # Pythagorean expectation
    exp = 1.83
    home_win_pct = home_runs**exp / (home_runs**exp + away_runs**exp)

    # Tighter clamp — 38-62% is realistic range from SP/lineup data alone
    result = round(min(max(home_win_pct * 100, 38.0), 62.0), 1)
    return result

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

    # Clamp to realistic range — NRFI historically 57% league avg, max ~70% even for elite matchups
    nrfi_prob = min(max(nrfi_prob, 0.35), 0.70)
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
    home_streak = g.get("home_streak",{})
    away_streak = g.get("away_streak",{})
    home_rest = g.get("home_rest",{})
    away_rest = g.get("away_rest",{})
    line_movement = g.get("line_movement",{})

    home_splits = g.get("home_team_splits",{})
    away_splits = g.get("away_team_splits",{})

    # Use home/away specific OPS for win probability if available — more accurate than season OPS
    home_ops_context = (home_splits.get("home",{}).get("ops") or
                        g.get("home_team_batting",{}).get("ops") or 0.720)
    away_ops_context = (away_splits.get("away",{}).get("ops") or
                        g.get("away_team_batting",{}).get("ops") or 0.720)
    home_woba_context = (g.get("home_team_batting",{}).get("xwoba") or
                         g.get("home_team_batting",{}).get("woba") or None)
    away_woba_context = (g.get("away_team_batting",{}).get("xwoba") or
                         g.get("away_team_batting",{}).get("woba") or None)

    game_num = g.get("game_num", 1)
    game_label = g["away"]+" @ "+g["home"]
    if game_num > 1:
        game_label += " (Game "+str(game_num)+")"
    return {
        "game": game_label,
        "venue": g.get("venue",""),
        "game_time": g.get("game_time",""),
        "status": g.get("status",""),
        "live_score": g.get("live_score"),
        "hp_ump": g.get("hp_ump",""),
        "away_sp": g["away_sp"],
        "home_sp": g["home_sp"],
        "away_sp_stats": {
            "era": away_sp.get("era",0),
            "fip": away_sp.get("fip"),
            "xfip": away_sp.get("xfip"),
            "xera": away_sp.get("xera"),
            "k9": away_sp.get("k9",0),
            "bb9": away_sp.get("bb9",0),
            "hr9": away_sp.get("hr9",0),
            "whip": away_sp.get("whip",0),
            "barrel_pct": away_sp.get("barrel_pct"),
            "hard_hit_pct": away_sp.get("hard_hit_pct"),
            "whiff_pct": away_sp.get("whiff_pct"),
            "note": away_sp.get("note",""),
            "form_flag": away_sp.get("form_flag",""),
            "relevant_split": away_sp.get("relevant_split",""),
            "recent_era": away_rec.get("era_last3",0),
            "recent_starts": away_rec.get("starts",0),
            "ip_per_start": away_rec.get("ip_per_start",0),
            "reliability": away_sp.get("reliability",0.5),
            "reliability_label": away_sp.get("reliability_label","UNKNOWN"),
            "throws": away_sp.get("throws",""),
            "avg_fastball_velo": away_sp.get("avg_fastball_velo"),
            "velo_trend": away_sp.get("velo_trend",""),
            "velo_flag": away_sp.get("velo_flag",""),
            "recent_avg_velo": away_sp.get("recent_avg_velo"),
            "velo_drop": away_sp.get("velo_drop"),
            "fastball_pct": away_sp.get("fastball_pct"),
            "breaking_pct": away_sp.get("breaking_pct"),
            "slider_pct": away_sp.get("slider_pct"),
            "slider_whiff": away_sp.get("slider_whiff"),
            "primary_pitch": away_sp.get("primary_pitch"),
        },
        "home_sp_stats": {
            "era": home_sp.get("era",0),
            "fip": home_sp.get("fip"),
            "xfip": home_sp.get("xfip"),
            "xera": home_sp.get("xera"),
            "k9": home_sp.get("k9",0),
            "bb9": home_sp.get("bb9",0),
            "hr9": home_sp.get("hr9",0),
            "whip": home_sp.get("whip",0),
            "barrel_pct": home_sp.get("barrel_pct"),
            "hard_hit_pct": home_sp.get("hard_hit_pct"),
            "whiff_pct": home_sp.get("whiff_pct"),
            "note": home_sp.get("note",""),
            "form_flag": home_sp.get("form_flag",""),
            "relevant_split": home_sp.get("relevant_split",""),
            "recent_era": home_rec.get("era_last3",0),
            "recent_starts": home_rec.get("starts",0),
            "ip_per_start": home_rec.get("ip_per_start",0),
            "reliability": home_sp.get("reliability",0.5),
            "reliability_label": home_sp.get("reliability_label","UNKNOWN"),
            "throws": home_sp.get("throws",""),
            "avg_fastball_velo": home_sp.get("avg_fastball_velo"),
            "velo_trend": home_sp.get("velo_trend",""),
            "velo_flag": home_sp.get("velo_flag",""),
            "recent_avg_velo": home_sp.get("recent_avg_velo"),
            "velo_drop": home_sp.get("velo_drop"),
            "fastball_pct": home_sp.get("fastball_pct"),
            "breaking_pct": home_sp.get("breaking_pct"),
            "slider_pct": home_sp.get("slider_pct"),
            "slider_whiff": home_sp.get("slider_whiff"),
            "primary_pitch": home_sp.get("primary_pitch"),
        },
        "away_team": {
            "ops": away_bat.get("ops",0),
            "woba": away_bat.get("woba"),
            "xwoba": away_bat.get("xwoba"),
            "barrel_pct": away_bat.get("barrel_pct"),
            "hard_hit_pct": away_bat.get("hard_hit_pct"),
            "exit_velo": away_bat.get("exit_velo"),
            "runs_per_game": away_bat.get("runs_per_game",0),
            "games_played": away_bat.get("games_played",0),
            "data_note": away_bat.get("note",""),
            # Road-specific performance — critical for evaluating away teams
            "away_ops": away_splits.get("away",{}).get("ops"),
            "away_woba": away_splits.get("away",{}).get("woba"),
            "away_rpg": away_splits.get("away",{}).get("runs_per_game"),
            "away_games": away_splits.get("away",{}).get("games"),
            "away_win_pct": away_splits.get("away",{}).get("win_pct"),
            "bullpen_fatigue": away_bp.get("fatigue_level","UNKNOWN"),
            "bullpen_quality": away_bp.get("quality","UNKNOWN"),
            "bullpen_avg_era": away_bp.get("avg_era"),
            "bullpen_quality_note": away_bp.get("quality_note",""),
            "fatigued_arms": away_bp.get("fatigued_arms",[])[:3],
            "platoon_vs_home_sp": g.get("away_platoon",{}),
            "matchups_vs_home_sp": g.get("away_matchups_vs_home_sp",{}),
            "pitch_mix_edge": g.get("away_pitch_mix",{}).get("pitch_mix_edge",""),
            "injuries": [i["name"] for i in g.get("away_injuries",[])[:2]],
            "streak": str(away_streak.get("streak_type",""))+str(away_streak.get("streak_number","")) if away_streak else "",
            "rest_days": away_rest.get("rest_days", 2),
            "back_to_back": away_rest.get("back_to_back", False),
        },
        "home_team": {
            "ops": home_bat.get("ops",0),
            "woba": home_bat.get("woba"),
            "xwoba": home_bat.get("xwoba"),
            "barrel_pct": home_bat.get("barrel_pct"),
            "hard_hit_pct": home_bat.get("hard_hit_pct"),
            "exit_velo": home_bat.get("exit_velo"),
            "runs_per_game": home_bat.get("runs_per_game",0),
            "games_played": home_bat.get("games_played",0),
            "data_note": home_bat.get("note",""),
            # Home-specific performance — home teams play differently at home
            "home_ops": home_splits.get("home",{}).get("ops"),
            "home_woba": home_splits.get("home",{}).get("woba"),
            "home_rpg": home_splits.get("home",{}).get("runs_per_game"),
            "home_games": home_splits.get("home",{}).get("games"),
            "home_win_pct": home_splits.get("home",{}).get("win_pct"),
            "bullpen_fatigue": home_bp.get("fatigue_level","UNKNOWN"),
            "bullpen_quality": home_bp.get("quality","UNKNOWN"),
            "bullpen_avg_era": home_bp.get("avg_era"),
            "bullpen_quality_note": home_bp.get("quality_note",""),
            "fatigued_arms": home_bp.get("fatigued_arms",[])[:3],
            "platoon_vs_away_sp": g.get("home_platoon",{}),
            "matchups_vs_away_sp": g.get("home_matchups_vs_away_sp",{}),
            "pitch_mix_edge": g.get("home_pitch_mix",{}).get("pitch_mix_edge",""),
            "injuries": [i["name"] for i in g.get("home_injuries",[])[:2]],
            "streak": str(home_streak.get("streak_type",""))+str(home_streak.get("streak_number","")) if home_streak else "",
            "rest_days": home_rest.get("rest_days", 2),
            "back_to_back": home_rest.get("back_to_back", False),
        },
        "sharp_money": line_movement,
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
        "nrfi_data": {
            **estimate_nrfi_odds(
                g.get("away_sp_stats",{}),
                g.get("home_sp_stats",{}),
                g.get("park_factor",{}).get("runs", 1.0),
                safe_float(odds.get("total",{}).get("line", 0)),
            ),
            # Override with real book prices if available
            **({"nrfi_book_price": odds.get("nrfi",{}).get("nrfi_price"),
                "yrfi_book_price": odds.get("nrfi",{}).get("yrfi_price"),
                "nrfi_source": "book"} if odds.get("nrfi",{}).get("nrfi_price") else {"nrfi_source": "model_estimate"}),
        },
        "f5_odds": {
            "ml_away": odds.get("f5",{}).get("ml_away"),
            "ml_home": odds.get("f5",{}).get("ml_home"),
            "total_line": odds.get("f5",{}).get("total_line"),
            "over": odds.get("f5",{}).get("over"),
            "under": odds.get("f5",{}).get("under"),
            "available": bool(odds.get("f5")),
        } if odds.get("f5") else {"available": False},
        "baseline_home_win_prob": estimate_win_prob(
            home_sp.get("era", 4.20) or 4.20,
            away_sp.get("era", 4.20) or 4.20,
            home_ops_context,   # home team's home OPS if available, else season OPS
            away_ops_context,   # away team's road OPS if available, else season OPS
            g.get("park_factor",{}).get("runs", 1.0) or 1.0,
            home_rec.get("era_last3", 0) or 0,
            away_rec.get("era_last3", 0) or 0,
            home_pit.get("team_era") or None,
            away_pit.get("team_era") or None,
            home_sp.get("xfip") or home_sp.get("fip") or None,
            away_sp.get("xfip") or away_sp.get("fip") or None,
            home_woba_context,
            away_woba_context,
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

    def strip_nulls(obj):
        """Remove None values recursively to reduce token count."""
        if isinstance(obj, dict):
            return {k: strip_nulls(v) for k, v in obj.items()
                    if v is not None and v != "" and v != [] and v != {}}
        if isinstance(obj, list):
            return [strip_nulls(i) for i in obj if i is not None]
        return obj

    summarized = [strip_nulls(g) for g in summarized]

    # Split into batches of 4 to stay within token limits
    BATCH_SIZE = 4
    all_picks = []
    model_used = "None"

    for i in range(0, n, BATCH_SIZE):
        batch = summarized[i:i+BATCH_SIZE]
        b_n = len(batch)
        print("Processing batch "+str(i//BATCH_SIZE+1)+"/"+str((n+BATCH_SIZE-1)//BATCH_SIZE)+" ("+str(b_n)+" games)...")

        batch_json = json.dumps(batch, indent=2)
        approx_tokens = len(batch_json) // 4
        print(f"Batch {i//BATCH_SIZE+1}: {b_n} games, ~{approx_tokens} tokens in game data")
        user_msg = (
            "Today is "+TODAY+". Analyze these "+str(b_n)+" MLB games.\n"
            "Use ALL provided data: SP stats (xFIP/FIP/ERA hierarchy), team offense (xwOBA/wOBA/OPS hierarchy), "
            "bullpen fatigue, injuries, umpire, park, weather, odds.\n"
            "Return exactly "+str(b_n)+" entries. Raw JSON array only.\n\n"
            "GAMES:\n"+batch_json
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
            "avoid_reason": "No odds data available — cannot calculate EV",
            "flags": "",
            "no_display": False,  # show as SKIP so game is visible
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
    """Fetch all final scores for a given date from MLB Stats API.
    Also detects postponed/cancelled games so picks can be voided."""
    data = mlb_api("/schedule", {
        "sportId":"1","date":date_str,
        "hydrate":"linescore,team","gameType":"R",
    })
    scores = {}
    postponed = set()  # set of game keys that were postponed/cancelled
    for de in data.get("dates",[]):
        for g in de.get("games",[]):
            home = g["teams"]["home"]["team"]["name"]
            away = g["teams"]["away"]["team"]["name"]
            key = away+"@"+home
            status = g.get("status",{}).get("abstractGameState","")
            detailed = g.get("status",{}).get("detailedState","")
            code = g.get("status",{}).get("statusCode","")
            # Detect postponed/cancelled/suspended games
            if any(x in detailed for x in ["Postponed","Cancelled","Suspended","Canceled"]) or code in ["DR","DI","DC"]:
                postponed.add(key)
                continue
            if status != "Final":
                continue
            # Sanity check — don't settle games that started less than 2 hours ago
            game_date_str = g.get("gameDate","")
            if game_date_str:
                try:
                    import datetime as _sd
                    game_utc = _sd.datetime.strptime(game_date_str[:19], "%Y-%m-%dT%H:%M:%S")
                    now_utc = _sd.datetime.utcnow()
                    if (now_utc - game_utc).total_seconds() < 7200:  # less than 2 hours since start
                        continue  # too early — skip settlement
                except: pass
            home_score = g["teams"]["home"].get("score",0) or 0
            away_score = g["teams"]["away"].get("score",0) or 0
            linescore = g.get("linescore",{})
            innings = linescore.get("innings",[])
            home_f5 = sum(int(inn.get("home",{}).get("runs",0) or 0) for inn in innings[:5])
            away_f5 = sum(int(inn.get("away",{}).get("runs",0) or 0) for inn in innings[:5])
            total_runs = home_score + away_score
            f5_total = home_f5 + away_f5
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
    return scores, postponed

def settle_pick(pick, scores, pick_date=None):
    """
    Determine W/L/P for a pick based on final scores.
    Returns updated pick dict or None if game not found/not final.
    """
    game_str = pick.get("game","")
    key = game_str.replace(" @ ","@")
    # Use date-scoped key to prevent cross-date collisions (same teams play multiple days)
    if pick_date:
        score = scores.get(pick_date+"_"+key)
    else:
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
    Postponed/cancelled games are voided (removed from record entirely).
    Runs every time the workflow fires.
    """
    unsettled = [p for p in record["picks"] if not p.get("result") and p.get("tier") != "SKIP"]
    if not unsettled:
        return record, 0

    # Get unique dates we need scores for
    dates_needed = set(p.get("date","") for p in unsettled if p.get("date"))
    all_scores = {}
    all_postponed = set()
    for d in dates_needed:
        try:
            day_scores, day_postponed = fetch_final_scores(d)
            # Store scores with date prefix to prevent cross-date collisions
            for k, v in day_scores.items():
                all_scores[d+"_"+k] = v
            all_postponed.update(day_postponed)
            print("Fetched "+str(len(day_scores))+" final scores for "+d+
                  (", "+str(len(day_postponed))+" postponed" if day_postponed else ""))
        except Exception as e:
            print("Score fetch error for "+d+": "+str(e))

    settled_count = 0
    closing_lines = fetch_closing_lines()

    # Void postponed picks first — remove them from record entirely
    postponed_picks = []
    for pick in record["picks"]:
        if pick.get("result") or pick.get("tier") == "SKIP":
            continue
        game = pick.get("game","")
        # Build key both ways
        parts = game.split(" @ ")
        if len(parts) == 2:
            key1 = parts[0]+"@"+parts[1]
            key2 = parts[1]+"@"+parts[0]
            if key1 in all_postponed or key2 in all_postponed:
                postponed_picks.append(pick)
                print("Postponed — removing from record: "+pick.get("pick","")+" ("+game+")")

    if postponed_picks:
        record["picks"] = [p for p in record["picks"] if p not in postponed_picks]

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

        pick_date = pick.get("date", TODAY)
        updated = settle_pick(pick, all_scores, pick_date)
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

def _load_record_live_js():
    p = Path(__file__).parent / "templates" / "record_live.js"
    if p.exists():
        return "<script>" + p.read_text() + "</script>"
    return ""
RECORD_LIVE_JS = _load_record_live_js()
def build_record_html(record):
    picks = record.get("picks",[])
    # Real picks only (exclude WATCH) for headline W-L, win rate, units
    settled     = [p for p in picks if p.get("result") in ("W","L","P") and p.get("tier") != "WATCH"]
    wins        = [p for p in settled if p["result"]=="W"]
    losses      = [p for p in settled if p["result"]=="L"]
    total_bets  = len(settled)
    win_rate    = round(len(wins)/total_bets*100,1) if total_bets else 0
    units_won   = round(sum(p.get("units_result",0) for p in settled),2)

    # CLV analysis
    clv_picks = [p for p in settled if p.get("open_line") and p.get("close_line")]
    avg_clv = 0
    if clv_picks:
        clvs = []
        for p in clv_picks:
            try:
                ol = float(str(p["open_line"]).replace("+",""))
                cl = float(str(p["close_line"]).replace("+",""))
                # Positive CLV = we got a better number than closing line
                # Negative odds: -110 open vs -120 close = +10 CLV (line moved against us, we got better)
                # Positive odds: +130 open vs +120 close = +10 CLV (line moved against us, we got better)
                if ol < 0:
                    clv = cl - ol  # e.g. -120 - (-110) = -10 (bad), -110 - (-120) = +10 (good)
                else:
                    clv = ol - cl  # e.g. +130 - +120 = +10 (good), +120 - +130 = -10 (bad)
                clvs.append(clv)
            except: pass
        avg_clv = round(sum(clvs)/len(clvs),1) if clvs else 0

    # By tier — real picks only (WATCH tracked separately below)
    tiers = {}
    for p in settled:
        t = p.get("tier","?")
        if t not in tiers: tiers[t] = {"W":0,"L":0,"P":0,"units":0.0}
        tiers[t][p["result"]] += 1
        tiers[t]["units"] += p.get("units_result",0)
    # Add WATCH to tier table separately
    watch_settled = [p for p in picks if p.get("tier")=="WATCH" and p.get("result") in ("W","L","P")]
    watch_wins  = len([p for p in watch_settled if p.get("result")=="W"])
    watch_losses = len([p for p in watch_settled if p.get("result")=="L"])
    watch_total = len(watch_settled)
    watch_rate  = round(watch_wins/watch_total*100,1) if watch_total else 0
    if watch_settled:
        tiers["WATCH"] = {"W":watch_wins,"L":watch_losses,"P":0,"units":0.0}

    # By bet type — real picks only
    bet_types = {}
    for p in settled:
        bt = p.get("bet_type","?")
        if bt not in bet_types: bet_types[bt] = {"W":0,"L":0,"P":0,"units":0.0}
        bet_types[bt][p["result"]] += 1
        bet_types[bt]["units"] += p.get("units_result",0)

    pending = [p for p in picks if not p.get("result") and p.get("tier") not in ("WATCH","SKIP")]

    # Loss reason breakdown
    REASON_LABELS = {
        "SP_OUTPERFORMED": "SP Outperformed",
        "BULLPEN_HELD":    "Bullpen Held",
        "LINEUP_DIFF":     "Lineup Diff",
        "WEATHER_WRONG":   "Weather Wrong",
        "PURE_VARIANCE":   "Variance",
        "BAD_DATA":        "Bad Data",
    }
    loss_reasons = {}
    for p in settled:
        if p.get("result") == "L" and p.get("loss_reason"):
            r = p["loss_reason"]
            loss_reasons[r] = loss_reasons.get(r, 0) + 1

    def stat_row(label, d):
        w=d["W"]; l=d["L"]; p=d.get("P",0); tot=w+l+p
        wr = round(w/tot*100,1) if tot else 0
        u = round(d["units"],2)
        uc = "var(--green)" if u>=0 else "var(--red)"
        dot = ('<span class="tier-dot '+label+'"></span>') if label in ("MAX","A","B","C","WATCH") else ""
        display_label = "On Hold" if label == "WATCH" else label
        return ('<tr>'
                '<td style="font-weight:600">'+dot+display_label+'</td>'
                '<td style="text-align:center;font-family:\'JetBrains Mono\',monospace">'+str(w)+'-'+str(l)+(('-'+str(p)) if p else '')+'</td>'
                '<td style="text-align:center">'+str(wr)+'%</td>'
                '<td style="text-align:right;font-family:\'JetBrains Mono\',monospace;font-weight:600;color:'+uc+'">'
                +('+'if u>=0 else '')+str(u)+'u</td></tr>')

    # Group picks by date for collapsible history
    from collections import defaultdict
    sorted_picks = sorted(picks, key=lambda p: p.get("date",""), reverse=True)
    picks_by_date = defaultdict(list)
    for p in sorted_picks:
        picks_by_date[p.get("date","")].append(p)

    def pick_card_html(p):
        res = p.get("result","")
        ur = p.get("units_result",0)
        t = p.get("tier","?")
        if t == "WATCH": ur = 0
        if res=="W": rl,rc="WIN","var(--green)"
        elif res=="L": rl,rc="LOSS","var(--red)"
        elif res=="P": rl,rc="PUSH","var(--muted)"
        else: rl,rc="PENDING","var(--gold)"
        open_l = p.get("open_line","")
        close_l = p.get("close_line","")
        clv_str = ""
        if open_l and close_l:
            try:
                ol = float(str(open_l).replace("+",""))
                cl2 = float(str(close_l).replace("+",""))
                clv = round(ol-cl2 if ol<0 else cl2-ol, 0)
                clv_str = ("+" if clv>0 else "")+str(int(clv))
            except: pass
        clv_color = "var(--green)" if clv_str.startswith('+') else "var(--red)" if clv_str.startswith('-') else "var(--muted)"
        loss_reason = p.get("loss_reason","")
        reason_badge = ""
        if res == "L" and loss_reason:
            label2 = REASON_LABELS.get(loss_reason, loss_reason)
            reason_badge = ('<span style="font-size:9px;background:#E8414B10;color:#E8414B80;'
                           'padding:1px 7px;border-radius:10px;border:1px solid #E8414B20;margin-left:4px">'+label2+'</span>')
        score = p.get("final_score","—")
        dot = '<span class="tier-dot '+t+'"></span>' if t in ("MAX","A","B","C","WATCH") else ""
        ur_color = "var(--green)" if ur > 0 else "var(--red)" if ur < 0 else "var(--muted)"
        watch_dim = 'opacity:.5;' if t == "WATCH" else ''
        return (
            '<div class="pick-row">'
            '<div class="pick-row-left">'
            '<div style="display:flex;align-items:center;gap:4px;margin-bottom:2px">'
            '<span class="pick-name-sm">'+p.get("pick","")+'</span>'
            +reason_badge+
            '</div>'
            '<div class="pick-game">'+p.get("game","")+'</div>'
            '<div class="pick-meta">'
            +dot+'<span style="color:var(--muted)">'+t+'</span>'
            '<span style="color:var(--border2)">·</span>'
            '<span style="font-family:\'JetBrains Mono\',monospace;color:var(--text)">'+str(open_l)+'</span>'
            +(('<span style="color:var(--border2)">→</span>'
               '<span style="font-family:\'JetBrains Mono\',monospace;color:var(--muted)">'+str(close_l)+'</span>'
               +(('<span style="color:var(--border2)">·</span>'
                  '<span style="font-family:\'JetBrains Mono\',monospace;color:'+clv_color+'">CLV '+clv_str+'</span>') if clv_str else '')
               ) if close_l else '')+
            '<span style="color:var(--border2)">·</span>'
            '<span style="color:var(--muted)">'+str(score)+'</span>'
            '</div>'
            '</div>'
            '<div class="pick-row-right">'
            '<div class="result-badge '+res+('' if res else 'pending')+'">'+rl+'</div>'
            '<div class="units-result" style="color:'+(
                'var(--green)' if ur>0 else 'var(--red)' if ur<0 else 'var(--muted)'
            )+'">'
            +('+' if ur>0 else '')+str(round(ur,2))+'u</div>'
            '</div>'
            '</div>'
        )

    def date_group_html(date, picks_list):
        real = [p for p in picks_list if p.get("tier") not in ("WATCH","SKIP")]
        w = len([p for p in real if p.get("result")=="W"])
        l = len([p for p in real if p.get("result")=="L"])
        u = round(sum(p.get("units_result",0) for p in real),2)
        pending_count = len([p for p in real if not p.get("result")])
        if w+l == 0 and pending_count == 0:
            summary = '<span style="color:var(--muted);font-size:11px">No active picks</span>'
        elif pending_count > 0:
            summary = '<span style="color:var(--gold);font-size:11px;font-family:\'JetBrains Mono\',monospace">'+str(pending_count)+' pending</span>'
        else:
            u_col = "var(--green)" if u>=0 else "var(--red)"
            wl_col = "var(--green)" if w>l else "var(--red)" if l>w else "var(--muted)"
            summary = ('<span style="font-family:\'JetBrains Mono\',monospace;font-size:11px;color:'+wl_col+';font-weight:600">'+str(w)+'-'+str(l)+'</span>'
                      +' <span style="color:var(--faint)">·</span> '
                      +'<span style="font-family:\'JetBrains Mono\',monospace;font-size:11px;color:'+u_col+';font-weight:600">'+('+'if u>=0 else '')+str(u)+'u</span>')
        uid = "dg_"+date.replace("-","")
        cards = "".join(pick_card_html(p) for p in picks_list)
        return (
            '<div class="dg" id="'+uid+'">'
            '<div class="dg-hdr" onclick="toggleDG(\''+uid+'\')">'
            '<div style="display:flex;align-items:center;gap:10px">'
            '<span style="font-size:13px;font-weight:700;font-family:\'JetBrains Mono\',monospace">'+date+'</span>'
            +summary+
            '</div>'
            '<span class="dg-arr">▾</span>'
            '</div>'
            '<div class="dg-body">'+cards+'</div>'
            '</div>'
        )

    tier_rows = "".join(stat_row(t,d) for t,d in sorted(tiers.items()))
    bt_rows   = "".join(stat_row(bt,d) for bt,d in sorted(bet_types.items()))

    date_groups_html = ""
    for date in sorted(picks_by_date.keys(), reverse=True):
        date_groups_html += date_group_html(date, picks_by_date[date])

    u_color = "var(--green)" if units_won>=0 else "var(--red)"
    u_str   = ("+" if units_won>=0 else "")+str(units_won)+"u"
    clv_color = "var(--green)" if avg_clv>0 else "var(--red)" if avg_clv<0 else "var(--muted)"

    _rec_css_path = Path(__file__).parent / "templates" / "record.css"
    rec_css = _rec_css_path.read_text() if _rec_css_path.exists() else ""

    toggle_js = (
        '<script>'
        'function toggleDG(id){'
        'var el=document.getElementById(id);'
        'if(el)el.classList.toggle("open");'
        '}'
        'document.addEventListener("DOMContentLoaded",function(){'
        'var first=document.querySelector(".dg");'
        'if(first)first.classList.add("open");'
        '});'
        '</script>'
    )

    loss_breakdown = ""
    if loss_reasons and losses:
        loss_breakdown = (
            '<div class="section-label">Loss Breakdown</div>'
            '<table><thead><tr><th>Reason</th><th>Count</th><th>% of Losses</th></tr></thead><tbody>'
            +"".join(
                '<tr><td style="font-weight:600">'+REASON_LABELS.get(r,r)+'</td>'
                '<td style="text-align:center;font-family:\'JetBrains Mono\',monospace">'+str(c)+'</td>'
                '<td style="text-align:center;color:var(--muted)">'+str(round(c/len(losses)*100,1))+'%</td></tr>'
                for r,c in sorted(loss_reasons.items(), key=lambda x: -x[1])
            )
            +'</tbody></table>'
        )

    return ('<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>MLB Record</title>'
            '<style>'+rec_css+'</style></head><body>'
            '<div class="page-header">'
            '<div class="brand">MLB Betting Model</div>'
            '<div class="page-title">Record</div>'
            '<div class="page-subtitle">'
            'Updated '+TODAY
            +' <span class="divider">&middot;</span> '
            '<a href="index.html">Today\'s picks</a>'
            +' <span class="divider">&middot;</span> '
            '<a href="archive.html">Archive</a>'
            +' <span class="divider">&middot;</span> '
            '<a href="scores.html">Scores</a>'
            +'</div></div>'
            '<div class="stats-bar">'
            '<div class="stat-card"><div class="stat-val">'+str(len(wins))+'-'+str(len(losses))+'</div><div class="stat-lbl">Record</div></div>'
            '<div class="stat-card"><div class="stat-val">'+str(win_rate)+'%</div><div class="stat-lbl">Win Rate</div></div>'
            '<div class="stat-card"><div class="stat-val" style="color:'+u_color+'">'+u_str+'</div><div class="stat-lbl">Units P&L</div></div>'
            '<div class="stat-card"><div class="stat-val" style="color:'+clv_color+'">'+('+'if avg_clv>=0 else '')+str(avg_clv)+'</div><div class="stat-lbl">Avg CLV</div></div>'
            '<div class="stat-card"><div class="stat-val" style="color:var(--muted)">'+str(watch_rate)+'%</div><div class="stat-lbl">Hold Hit %</div></div>'
            '</div>'
            '<div class="section-label">By Tier</div>'
            '<div class="table-wrap"><table><thead><tr><th>Tier</th><th>Record</th><th>Win %</th><th>Units</th></tr></thead><tbody>'+tier_rows+'</tbody></table></div>'
            '<div class="section-label">By Bet Type</div>'
            '<div class="table-wrap"><table><thead><tr><th>Type</th><th>Record</th><th>Win %</th><th>Units</th></tr></thead><tbody>'+bt_rows+'</tbody></table></div>'
            +loss_breakdown+
            '<div class="section-label">Pick History</div>'
            +date_groups_html+
            '<footer>EV model &middot; Track CLV for long-term edge &middot; Paper trading until 50+ picks verified</footer>'
            + RECORD_LIVE_JS
            + toggle_js
            + '</body></html>')

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
        mb_bg="#23C97A12"; mb_tc="#23C97A"
    else:
        mb_bg="#4A9CF010"; mb_tc="#4A9CF0"
    model_badge = ('<span style="background:'+mb_bg+';color:'+mb_tc+';font-size:10px;'
                   'font-weight:600;padding:2px 10px;border-radius:20px;font-family:\'JetBrains Mono\',monospace;'
                   'border:1px solid '+mb_tc+'25">'+ai_model+'</span>')

    TBAR={"MAX":"#0A0A0A","A":"#1D9E75","B":"#378ADD","C":"#BA7517","WATCH":"#8B6FBA"}
    TBG ={"MAX":"#1a1a1a","A":"#E1F5EE","B":"#E6F1FB","C":"#FAEEDA","WATCH":"#F0ECFB"}
    TTC ={"MAX":"#FFD700","A":"#0F6E56","B":"#185FA5","C":"#854F0B","WATCH":"#4A2D8F"}
    TLBL={"MAX":"&#9733; MAX BET &mdash; HIGHEST CONFIDENCE","A":"TIER A &mdash; PLAY","B":"TIER B &mdash; PLAY","C":"TIER C &mdash; LEAN","WATCH":"ON HOLD &mdash; OPERATIONAL BLOCK"}

    def sp_box(label, name):
        return ('<div class="sp-box">'
                '<div class="sp-lbl">'+label+'</div>'
                '<div class="sp-name">'+str(name)+'</div></div>')

    def flag_row(text):
        t=str(text)
        if not t or t in ('','null','None'): return ''
        return '<div class="flag">'+t+'</div>'

    def score_span(game):
        return '<span id="'+score_id(game)+'" class="score-pill"></span>'

    def detail_row(label, value):
        v = str(value)
        if not v or v in ('N/A','null','None',''): return ''
        return ('<div class="detail-row">'
                '<span class="detail-lbl">'+label+'</span>'
                '<span class="detail-val">'+v+'</span>'
                '</div>')

    def pick_card(p):
        t = p.get("tier","C")
        ev = p.get("ev_pct",0)
        game = str(p.get("game",""))
        ump = str(p.get("hp_ump",""))
        ump_display = ump if ump and ump not in ("TBD","") else "TBD"
        win_pct = p.get("win_prob_pct",0)
        impl_pct = p.get("implied_prob_pct",0)
        tier_labels = {"MAX":"★ MAX BET","A":"TIER A — PLAY","B":"TIER B — PLAY","C":"TIER C — LEAN"}
        lbl = tier_labels.get(t, t)
        away_sp = str(p.get("away_sp","TBD"))
        home_sp = str(p.get("home_sp","TBD"))
        sp_edge = p.get("sp_analysis","")
        lineup = p.get("lineup_analysis","")
        bullpen = p.get("bullpen_note","")
        weather = p.get("weather_impact","")
        park = p.get("park_note","")
        key_edge = str(p.get("key_edge",""))

        # Convert UTC game time to ET for display
        game_time_raw = str(p.get("game_time",""))
        game_time_display = game_time_raw
        if game_time_raw and "T" in game_time_raw:
            try:
                import datetime as _gdt
                utc_dt = _gdt.datetime.strptime(game_time_raw[:19], "%Y-%m-%dT%H:%M:%S")
                et_dt = utc_dt - _gdt.timedelta(hours=4)  # EDT
                game_time_display = et_dt.strftime("%-I:%M %p ET")
            except: pass

        # Compact detail rows — only show populated ones
        details = ""
        if lineup:  details += detail_row("Lineup", lineup)
        if bullpen: details += detail_row("Bullpen", bullpen)
        if weather or park:
            env = " · ".join(x for x in [weather, park] if x)
            if env: details += detail_row("Conditions", env)
        if ump_display != "TBD":
            details += detail_row("Umpire", ump_display)

        return (
            '<div class="pick-card tier-'+t+'">'
            '<div class="card-inner">'

            '<div class="card-top">'
            '<div class="tier-badge '+t+'">'+lbl+'</div>'
            '<div class="card-top-right">'
            '<span class="units-badge">'+str(p.get("units",0))+'u</span>'
            '<span class="odds-badge">'+str(p.get("line",""))+'</span>'
            '</div>'
            '</div>'

            '<div class="pick-name">'+str(p.get("pick",""))+'</div>'
            +flag_row(p.get("flags",""))+
            '<div class="pick-sub">'
            '<span class="game-label">'+game+'</span>'
            +(('<span class="game-time">'+game_time_display+'</span>') if game_time_display and game_time_display not in game else '')
            +score_span(game)
            +'</div>'
            '<div class="sp-grid">'
            +sp_box("Away SP", away_sp)+sp_box("Home SP", home_sp)
            +'</div>'

            '<div class="ev-strip">'
            '<div class="ev-nums">'
            '<span class="win-pct">'+str(win_pct)+'% win</span>'
            '<span class="ev-sep">vs</span>'
            '<span class="impl-pct">'+str(impl_pct)+'% implied</span>'
            '</div>'
            '<span class="ev-badge '+t+'">+'+str(ev)+'% EV</span>'
            '</div>'
            '<div class="ev-bar"><div class="ev-fill '+t+'" style="width:'+str(min(int(float(ev or 0))*8,100))+'%"></div></div>'
            +(('<div class="key-edge">'+key_edge+'</div>') if key_edge else '')
            +(('<div class="details">'+details+'</div>') if details else '')
            +'</div></div>'
        )

    def watch_card(p):
        game = str(p.get("game",""))
        avoid = str(p.get("avoid_reason",""))
        # Fix: clean up pick name — remove "(WATCH)" suffix, never show bare "WATCH"
        raw_pick = str(p.get("pick", game))
        pick_display = raw_pick.replace("(WATCH)","").replace("(watch)","").strip()
        # Strip WATCH prefix if Claude prepended it
        if pick_display.upper().startswith("WATCH "):
            pick_display = pick_display[6:].strip()
        if not pick_display or pick_display.upper() == "WATCH":
            pick_display = ""  # will show game only, no duplicate
        # Build card — only show pick name if it adds info beyond the game string
        show_pick_name = pick_display and pick_display != game
        line = str(p.get("line",""))
        line_display = line if line and line not in ("N/A","null","None","") else "—"
        return (
            '<div class="pick-card tier-WATCH">'
            '<div class="card-inner">'
            '<div class="card-top">'
            '<div class="tier-badge WATCH">ON HOLD</div>'
            '<span class="odds-badge" style="opacity:.5">'+line_display+'</span>'
            '</div>'
            +(('<div class="pick-name" style="font-size:17px;color:var(--subtle)">'+pick_display+'</div>') if show_pick_name else '')+
            '<div class="pick-sub"><span class="game-label">'+game+'</span>'+score_span(game)+'</div>'
            +(('<div class="watch-reason">'+avoid+'</div>') if avoid else '')+
            '</div></div>'
        )

    def skip_card(p):
        game = str(p.get("game",""))
        away_sp = str(p.get("away_sp","TBD"))
        home_sp = str(p.get("home_sp","TBD"))
        avoid = str(p.get("avoid_reason","No clear edge identified"))
        return (
            '<div class="pick-card tier-SKIP">'
            '<div class="card-inner">'
            '<div class="tier-badge SKIP">SKIP — NO EDGE</div>'
            '<div class="pick-name" style="font-size:15px;color:var(--muted);margin-top:6px">'+game+' '+score_span(game)+'</div>'
            '<div class="sp-grid" style="margin-top:8px">'+sp_box("Away SP",away_sp)+sp_box("Home SP",home_sp)+'</div>'
            '<div class="skip-reason">'+avoid+'</div>'
            '</div></div>'
        )


    active_cards = "".join(pick_card(p) for p in active)
    watch_cards = "".join(watch_card(p) for p in watched)
    skip_cards = "".join(skip_card(p) for p in skipped)

    cards = active_cards
    if watch_cards:
        cards += '<div class="section-label" style="margin-top:1rem">On Hold</div>' + watch_cards
    if skip_cards:
        cards += '<div class="section-label" style="margin-top:1rem">No Edge</div>' + skip_cards
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

    _css_path = Path(__file__).parent / "templates" / "picks.css"
    _css_content = _css_path.read_text() if _css_path.exists() else ""
    css = "<style>" + _css_content + "</style>" 

    has_max = any(p.get('tier')=='MAX' for p in active)
    active_color = 'var(--gold)' if has_max else 'var(--green)'
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>MLB Picks — '+date+'</title>'+css+'</head><body>'
        '<div class="page-header">'
        '<div class="brand">MLB Betting Model</div>'
        '<div class="page-title">Today\'s Picks</div>'
        '<div class="page-subtitle">'
        '<span>'+date+'</span><span class="divider">&middot;</span>'
        '<span>'+str(data['total_games'])+' games</span><span class="divider">&middot;</span>'
        '<a href="archive.html">Archive</a><span class="divider">&middot;</span>'
        '<a href="record.html">Record</a><span class="divider">&middot;</span>'
        '<a href="scores.html">Live Scores</a><span class="divider">&middot;</span>'
        '<span>'+model_badge+'</span><span class="divider">&middot;</span>'
        '<span id="last_update" style="color:var(--muted)">Loading...</span>'
        '</div>'
        '</div>'
        '<div class="stats-bar">'
        '<div class="stat-card"><div class="stat-val" style="color:'+active_color+'">'+str(len(active))+'</div>'
        '<div class="stat-lbl">Active picks</div></div>'
        '<div class="stat-card"><div class="stat-val" style="color:var(--gold)">'+str(total_u)+'u</div>'
        '<div class="stat-lbl">Total units</div></div>'
        '<div class="stat-card"><div class="stat-val" style="color:var(--muted)">'+str(len(watched))+'</div>'
        '<div class="stat-lbl">On Hold</div></div>'
        '<div class="stat-card"><div class="stat-val" style="color:var(--muted)">'+str(len(skipped))+'</div>'
        '<div class="stat-lbl">No edge</div></div>'
        '</div>'
        '<div class="section-label">Active Picks</div>'
        '<div class="picks-list">'
        +cards+
        '</div>'
        '<footer>EV model &middot; 2025+2026 stats &middot; Recent form &middot; Splits &middot; Lineups &middot; Bullpen &middot; Umpires<br>Never bet more than you can afford to lose</footer>'
        +live_js+'</body></html>'
    )

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("Running MLB picks generator for "+TODAY+"...")

    # Fast rebuild — index.html missing but picks locked, rebuild pages without enrichment
    if REBUILD_ONLY:
        print("[LOCK] index.html missing — rebuilding pages from existing picks.")
        record = json.loads(RECORD_FILE.read_text()) if RECORD_FILE.exists() else {"picks":[],"updated":TODAY}
        picks_json_path = OUTPUT_DIR/"picks.json"
        if picks_json_path.exists():
            output = json.loads(picks_json_path.read_text())
        else:
            today_picks = [p for p in record.get("picks",[]) if p.get("date")==TODAY]
            output = {"date":TODAY,"generated_at":datetime.datetime.utcnow().isoformat()+"Z",
                     "ai_model":record.get("ai_model","Claude Sonnet 4.6"),
                     "total_games":0,"picks":today_picks}
        html = build_html(output)
        (OUTPUT_DIR/(TODAY+".html")).write_text(html)
        (OUTPUT_DIR/"index.html").write_text(html)
        (OUTPUT_DIR/"record.html").write_text(build_record_html(record))
        scores_src = Path("scores.html")
        if scores_src.exists(): (OUTPUT_DIR/"scores.html").write_text(scores_src.read_text())
        build_archive_index()
        print("[LOCK] Pages rebuilt. Exiting.")
        return

    # Generation window check — only generate NEW picks during 7AM-10AM ET or with FORCE_REGEN
    import datetime as _dt
    _now_utc = _dt.datetime.utcnow()
    _now_et_hour = (_now_utc.hour - 4) % 24  # EDT offset
    _in_window = 6 <= _now_et_hour < 10
    _can_generate = _in_window or FORCE_REGEN
    if not _can_generate:
        print("Outside generation window ("+str(_now_et_hour).zfill(2)+":xx ET). Rebuilding pages only — no new picks.")

    stats = fetch_and_cache_stats()
    games = fetch_mlb_games()
    if not games:
        print("No games found -- exiting")
        return

    odds_map, event_ids = fetch_odds()

    # Fetch NRFI + F5 odds in parallel using same per-game calls — no extra API credits
    if event_ids:
        nrfi_f5_map = fetch_nrfi_odds(event_ids)
        for game_key, data in nrfi_f5_map.items():
            if game_key in odds_map:
                odds_map[game_key]["nrfi"] = data
                # Add F5 odds as separate key for clean access
                if data.get("f5_ml_away") or data.get("f5_total_line"):
                    odds_map[game_key]["f5"] = {
                        "ml_away": data.get("f5_ml_away"),
                        "ml_home": data.get("f5_ml_home"),
                        "away_team": data.get("f5_away_team"),
                        "home_team": data.get("f5_home_team"),
                        "total_line": data.get("f5_total_line"),
                        "over": data.get("f5_over"),
                        "under": data.get("f5_under"),
                    }
        f5_count = sum(1 for v in odds_map.values() if v.get("f5"))
        nrfi_count = sum(1 for v in odds_map.values() if v.get("nrfi"))
        print(f"NRFI lines: {nrfi_count} games, F5 lines: {f5_count} games")

    # Fetch ESPN injuries once for all teams
    espn_injuries = fetch_espn_injuries()

    # Exit early if no games have odds — nothing to analyze
    games_with_odds = [g for g in games if odds_map.get(g["away"]+"@"+g["home"])]
    if not games_with_odds or not _can_generate:
        if not games_with_odds:
            print("No games with odds found — rebuilding pages only.")
        record = json.loads(RECORD_FILE.read_text()) if RECORD_FILE.exists() else {"picks":[],"updated":TODAY}
        record, settled_count = auto_settle_record(record)
        if settled_count:
            print("Auto-settled "+str(settled_count)+" picks")
        RECORD_FILE.write_text(json.dumps(record, indent=2))
        # Preserve existing picks.json — never overwrite with empty during rebuild
        picks_json_path = OUTPUT_DIR/"picks.json"
        if picks_json_path.exists():
            try:
                picks_out = json.loads(picks_json_path.read_text())
                # Update total_games count but keep existing picks
                picks_out["total_games"] = len(games)
            except:
                picks_out = {"date":TODAY,"total_games":len(games),"picks":[],"generated_at":datetime.datetime.utcnow().isoformat(),"ai_model":"—"}
        else:
            # No picks.json yet — build from today's record picks
            today_picks = [p for p in record.get("picks",[]) if p.get("date")==TODAY]
            picks_out = {"date":TODAY,"total_games":len(games),"picks":today_picks,"generated_at":datetime.datetime.utcnow().isoformat(),"ai_model":record.get("ai_model","—")}
        picks_json_path.write_text(json.dumps(picks_out, indent=2))
        html = build_html(picks_out)
        (OUTPUT_DIR/(TODAY+".html")).write_text(html)
        (OUTPUT_DIR/"index.html").write_text(html)
        (OUTPUT_DIR/"record.html").write_text(build_record_html(record))
        scores_src = Path("scores.html")
        scores_dst = OUTPUT_DIR/"scores.html"
        if scores_src.exists():
            scores_dst.write_text(scores_src.read_text())
        build_archive_index()
        print("Done — pages rebuilt, picks preserved.")
        return

    games_with_data = []
    seen_pks = set()

    def enrich_game(g):
        """Enrich a single game with all data — runs in parallel."""
        pk = g.get("game_pk")
        base_key = g["away"]+"@"+g["home"]
        game_num = g.get("game_num", 1)
        odds = odds_map.get(base_key, {})
        weather = fetch_weather(g["home"])
        park    = get_park_factor(g["venue"])
        ump     = get_ump_stats(g.get("hp_ump",""))

        # Lineups
        lineups = {}
        status = g.get("status","")
        if pk and status not in ("In Progress","Live","Final","Game Over","Completed","Pre-Game"):
            try: lineups = fetch_lineup(pk)
            except: pass

        # SP stats
        home_sp_stats = get_pitcher_stats(g["home_sp"], stats, is_home=True)
        away_sp_stats = get_pitcher_stats(g["away_sp"], stats, is_home=False)

        # Platoon + historical matchup + pitch mix analysis
        home_platoon = {}; away_platoon = {}
        away_matchups = {}; home_matchups = {}
        home_pitch_mix = {}; away_pitch_mix = {}
        if lineups:
            home_throws = home_sp_stats.get("throws","")
            away_throws = away_sp_stats.get("throws","")
            if lineups.get("away",{}).get("batters") and away_throws:
                home_platoon = analyze_lineup_handedness(lineups["away"]["batters"], away_throws)
                home_pitch_mix = analyze_pitch_mix_vs_lineup(home_sp_stats, home_platoon)
            if lineups.get("home",{}).get("batters") and home_throws:
                away_platoon = analyze_lineup_handedness(lineups["home"]["batters"], home_throws)
                away_pitch_mix = analyze_pitch_mix_vs_lineup(away_sp_stats, away_platoon)

            # Historical batter vs SP matchups — run in parallel
            home_sp_pid = home_sp_stats.get("player_id")
            away_sp_pid = away_sp_stats.get("player_id")
            if home_sp_pid and lineups.get("away",{}).get("batters"):
                away_matchups = analyze_lineup_vs_sp(lineups["away"]["batters"], home_sp_pid)
            if away_sp_pid and lineups.get("home",{}).get("batters"):
                home_matchups = analyze_lineup_vs_sp(lineups["home"]["batters"], away_sp_pid)

        # Bullpen, injuries, splits, rest, line movement — run in parallel per game
        import concurrent.futures as _cf
        game_date = TODAY
        with _cf.ThreadPoolExecutor(max_workers=10) as ex:
            f_hbp   = ex.submit(fetch_bullpen_fatigue, g["home_id"], stats.get("rp_2026",{}), stats.get("rp_2025",{}))
            f_abp   = ex.submit(fetch_bullpen_fatigue, g["away_id"], stats.get("rp_2026",{}), stats.get("rp_2025",{}))
            f_hinj  = ex.submit(fetch_injuries, g["home_id"])
            f_ainj  = ex.submit(fetch_injuries, g["away_id"])
            f_hspl  = ex.submit(fetch_team_home_away_splits, g["home_id"], 2026)
            f_aspl  = ex.submit(fetch_team_home_away_splits, g["away_id"], 2026)
            f_hrest = ex.submit(fetch_team_rest_days, g["home_id"], game_date)
            f_arest = ex.submit(fetch_team_rest_days, g["away_id"], game_date)
            f_lines = ex.submit(fetch_line_movement, g["away"], g["home"])

        home_bullpen  = f_hbp.result() if f_hbp.exception() is None else {}
        away_bullpen  = f_abp.result() if f_abp.exception() is None else {}
        home_injuries = f_hinj.result() if f_hinj.exception() is None else []
        away_injuries = f_ainj.result() if f_ainj.exception() is None else []
        home_splits   = f_hspl.result() if f_hspl.exception() is None else {}
        away_splits   = f_aspl.result() if f_aspl.exception() is None else {}
        home_rest     = f_hrest.result() if f_hrest.exception() is None else {}
        away_rest     = f_arest.result() if f_arest.exception() is None else {}
        line_movement = f_lines.result() if f_lines.exception() is None else {}

        # Fall back to 2025 splits if 2026 unavailable
        if not home_splits:
            try: home_splits = fetch_team_home_away_splits(g["home_id"], 2025)
            except: pass
        if not away_splits:
            try: away_splits = fetch_team_home_away_splits(g["away_id"], 2025)
            except: pass

        home_injuries = get_team_injuries_with_espn(g["home"], home_injuries, espn_injuries)
        away_injuries = get_team_injuries_with_espn(g["away"], away_injuries, espn_injuries)

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
        gd["away_matchups_vs_home_sp"] = away_matchups  # away lineup history vs home SP
        gd["home_matchups_vs_away_sp"] = home_matchups  # home lineup history vs away SP
        gd["home_pitch_mix"]           = home_pitch_mix  # home SP pitch mix vs away lineup
        gd["away_pitch_mix"]           = away_pitch_mix  # away SP pitch mix vs home lineup
        gd["home_bullpen_fatigue"]  = home_bullpen
        gd["away_bullpen_fatigue"]  = away_bullpen
        gd["home_injuries"]         = home_injuries[:5]
        gd["away_injuries"]         = away_injuries[:5]
        gd["home_team_splits"]      = home_splits
        gd["away_team_splits"]      = away_splits
        gd["home_rest"]             = home_rest
        gd["away_rest"]             = away_rest
        gd["line_movement"]         = line_movement
        return gd

    # Filter duplicates first
    unique_games = []
    for g in games:
        pk = g.get("game_pk")
        if pk and pk in seen_pks:
            print(f"Skipping duplicate game_pk {pk}: {g['away']} @ {g['home']}")
            continue
        if pk: seen_pks.add(pk)
        unique_games.append(g)

    # Enrich all games in parallel
    import concurrent.futures as _cf2
    import time as _et
    _enrich_t = _et.time()
    print(f"Enriching {len(unique_games)} games in parallel...")
    with _cf2.ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(enrich_game, g): g for g in unique_games}
        for future in _cf2.as_completed(futures):
            g = futures[future]
            try:
                gd = future.result()
                # Add streaks (fast, cached after first call)
                gd["home_streak"] = fetch_team_streak(g["home_id"])
                gd["away_streak"] = fetch_team_streak(g["away_id"])
                games_with_data.append(gd)
                print(f"  ✓ {g['away']} @ {g['home']}")
            except Exception as e:
                import traceback
                print(f"  ✗ {g['away']} @ {g['home']}: {str(e)}")
                print(f"    {traceback.format_exc().splitlines()[-2]}")
    print(f"Enrichment: {round(_et.time()-_enrich_t,1)}s for {len(games_with_data)} games")

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
    # - Trigger conditions: SP scratch, rain 80%+, line moved 40+ cents
    # - Otherwise: keep locked picks, just update scores

    # Only lock picks that were actually generated today (have home_sp/away_sp fields)
    # Seeded picks from record.json manual entry don't have these fields
    # Check lock file first — most reliable way to prevent regeneration
    LOCK_FILE = OUTPUT_DIR / ("picks_locked_"+TODAY+".txt")
    lock_file_exists = LOCK_FILE.exists()
    
    today_picks = [p for p in record.get("picks",[])
                   if p.get("date")==TODAY 
                   and p.get("tier") in ("MAX","A","B","C","WATCH")]
    picks_locked = len(today_picks) > 0 or lock_file_exists
    if picks_locked:
        print("Picks locked for "+TODAY+" (lock_file="+str(lock_file_exists)+", record_picks="+str(len(today_picks))+"). Skipping generation.")

    # Check trigger conditions for regeneration
    def should_regenerate(locked_picks, new_game_data, old_odds):
        triggers = []
        # Only consider today's unsettled active picks
        active_today = [lp for lp in locked_picks
                        if lp.get("date") == TODAY
                        and not lp.get("result")
                        and lp.get("tier") not in ("WATCH","SKIP")]

        for gd in new_game_data:
            game_label = gd["away"]+" @ "+gd["home"]
            game_status = gd.get("status","")
            if game_status in ("In Progress","Live","Final","Game Over","Completed"):
                continue
            # SP scratch is the ONLY valid regen trigger after picks are locked
            for lp in active_today:
                if game_label == lp.get("game",""):
                    old_home_sp = lp.get("home_sp","")
                    old_away_sp = lp.get("away_sp","")
                    if old_home_sp and old_home_sp != gd["home_sp"] and gd["home_sp"] != "TBD":
                        triggers.append("SP scratch: "+old_home_sp+" → "+gd["home_sp"])
                    if old_away_sp and old_away_sp != gd["away_sp"] and gd["away_sp"] != "TBD":
                        triggers.append("SP scratch: "+old_away_sp+" → "+gd["away_sp"])
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
                # Write notification file for workflow to send push
                notif = "🔄 MLB Model Regenerating\n" + "\n".join("• "+r for r in regen_reasons)
                (OUTPUT_DIR/"notify.txt").write_text(notif)
            else:
                print("Picks locked for "+TODAY+" ("+str(len(today_picks))+" picks). No triggers. Keeping locked picks.")
                # Write clean notification so workflow knows to send locked confirmation
                (OUTPUT_DIR/"notify.txt").write_text("LOCKED")

    if not picks_locked or force_regen or FORCE_REGEN:
        if force_regen:
            # Only remove picks for games affected by triggers
            affected_games = set()
            for reason in regen_reasons:
                if "SP scratch:" in reason:
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

        elif FORCE_REGEN:
            # Manual FORCE_REGENERATE=yes — preserve existing valid picks,
            # only wipe picks for games with no odds or that are already final
            existing_today = [p for p in record.get("picks",[])
                              if p.get("date")==TODAY and not p.get("result")
                              and p.get("tier") in ("MAX","A","B","C")]
            if existing_today:
                print(f"FORCE_REGEN: Preserving {len(existing_today)} existing picks, adding new ones only")
                # Remove only games that have no existing active pick
                existing_games = {p.get("game","") for p in existing_today}
                games_to_regen = [g for g in games_with_data
                                  if (g["away"]+" @ "+g["home"]) not in existing_games]
                games_with_data = games_to_regen if games_to_regen else games_with_data

        picks, ai_model = call_ai(games_with_data)
        # Merge new picks with any preserved existing picks
        if FORCE_REGEN and not force_regen:
            preserved = [p for p in record.get("picks",[])
                        if p.get("date")==TODAY and not p.get("result")
                        and p.get("tier") in ("MAX","A","B","C")]
            new_games = {p.get("game","") for p in picks}
            # Only keep preserved picks for games not covered by new run
            preserved = [p for p in preserved if p.get("game","") not in new_games]
            picks = preserved + picks
        active = [p for p in picks if p.get("tier") in ("MAX","A","B","C")]
        record["ai_model"] = ai_model
        if regen_reasons:
            record["regen_reasons"] = record.get("regen_reasons",[]) + regen_reasons
    else:
        ai_model = record.get("ai_model", "Claude Sonnet 4.5")
        picks = [p for p in record.get("picks",[]) if p.get("date")==TODAY]
        active = [p for p in picks if p.get("tier") in ("MAX","A","B","C")]

        # 3PM ump patch — inject real ump assignments into today's picks
        _now_et = (_now_utc.hour - 4) % 24
        if _now_et >= 12:  # After noon ET — umps are posted
            ump_updated = 0
            clv_updated = 0
            for g in games_with_data:
                hp_ump = g.get("hp_ump","")
                game_key = g["away"]+" @ "+g["home"]
                current_odds = g.get("odds",{})
                for p in record["picks"]:
                    if p.get("game","") != game_key or p.get("date") != TODAY or p.get("result"):
                        continue
                    # Inject real ump
                    if hp_ump and hp_ump != "TBD" and p.get("hp_ump","") != hp_ump:
                        p["hp_ump"] = hp_ump
                        ump_updated += 1
                    # Capture closing line if not already set
                    if not p.get("close_line") and current_odds:
                        bet_type = p.get("bet_type","")
                        pick_str = p.get("pick","").upper()
                        cl = ""
                        try:
                            if "ML" in bet_type:
                                for team, price in current_odds.get("moneyline",{}).items():
                                    if team.upper() in pick_str or pick_str in team.upper():
                                        cl = str(price); break
                            elif "OVER" in bet_type:
                                cl = str(current_odds.get("total",{}).get("over",""))
                            elif "UNDER" in bet_type:
                                cl = str(current_odds.get("total",{}).get("under",""))
                            elif "Run Line" in bet_type:
                                for team, rl in current_odds.get("runline",{}).items():
                                    if team.upper() in pick_str or pick_str in team.upper():
                                        cl = str(rl.get("price","")); break
                        except: pass
                        if cl and cl not in ("","None","0"):
                            p["close_line"] = cl
                            clv_updated += 1
            if ump_updated or clv_updated:
                print(f"3PM patch: {ump_updated} ump updates, {clv_updated} closing lines captured")
                save_record(record)
                picks = [p for p in record.get("picks",[]) if p.get("date")==TODAY]

    # Save new picks to record (only when fresh or regenerated)
    existing_keys = {p["game"]+p.get("date","") for p in record["picks"]}
    for p in active:
        key = p.get("game","")+TODAY
        if key not in existing_keys:
            record["picks"].append({
                "date":        TODAY,
                "game":        p.get("game",""),
                "pick":        p.get("pick",""),
                "bet_type":    p.get("bet_type",""),
                "home_sp":     p.get("home_sp",""),
                "away_sp":     p.get("away_sp",""),
                "line":        p.get("line",""),
                "open_line":   p.get("line",""),
                "close_line":  "",
                "tier":        p.get("tier",""),
                "units":       p.get("units",0),
                "ev_pct":      p.get("ev_pct",0),
                "result":      "",
                "units_result": 0,
                "loss_reason": "",
            })
    for p in [x for x in picks if x.get("tier")=="WATCH"]:
        key = p.get("game","")+TODAY+"W"
        if key not in existing_keys:
            record["picks"].append({
                "date":        TODAY,
                "game":        p.get("game",""),
                "pick":        p.get("pick","")+" (WATCH)",
                "bet_type":    p.get("bet_type",""),
                "home_sp":     p.get("home_sp",""),
                "away_sp":     p.get("away_sp",""),
                "line":        p.get("line",""),
                "open_line":   p.get("line",""),
                "close_line":  "",
                "tier":        "WATCH",
                "units":       0,
                "ev_pct":      p.get("ev_pct",0),
                "result":      "",
                "units_result": 0,
                "loss_reason": "",
            })
    record["updated"] = TODAY
    save_record(record)
    
    # Write lock file to prevent duplicate generation today
    LOCK_FILE.write_text("Picks generated "+TODAY+" at "+datetime.datetime.utcnow().isoformat())
    print("Lock file written: "+str(LOCK_FILE))

    output = {
        "date":         TODAY,
        "generated_at": datetime.datetime.utcnow().isoformat()+"Z",  # stored UTC, displayed as ET
        "stats_date":   stats.get("date",""),
        "ai_model":     ai_model,
        "total_games":  len(games),
        "total_picks":  len(active),
        "picks":        picks,
    }

    (OUTPUT_DIR/"picks.json").write_text(json.dumps(output, indent=2))
    html = build_html(output)
    (OUTPUT_DIR/(TODAY+".html")).write_text(html)
    (OUTPUT_DIR/"index.html").write_text(html)
    (OUTPUT_DIR/"record.html").write_text(build_record_html(record))
    # Scores page — copy static file if not already present
    scores_src = Path("scores.html")
    scores_dst = OUTPUT_DIR/"scores.html"
    if scores_src.exists() and not scores_dst.exists():
        scores_dst.write_text(scores_src.read_text())
    build_archive_index()

    print("Done. "+str(len(active))+" active picks across "+str(len(games))+" games.")
    print("AI engine: "+ai_model)

if __name__ == "__main__":
    main()
    _rec_css_path = Path(__file__).parent / "templates" / "record.css"
    rec_css = _rec_css_path.read_text() if _rec_css_path.exists() else ""
