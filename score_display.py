#!/usr/bin/env python3
"""
NCAAM Score Alert Display — Runs on Touchscreen Pi (192.168.1.175)

Flask app that receives score alerts from the Pi 400 tracker and serves
a full-screen display page with animated pop-up alert cards.

Endpoints:
  POST /score-update   — Receive alert from Pi 400
  GET  /               — Serve the display page
  GET  /updates        — Return recent alerts as JSON (polled by frontend)
  POST /test-alert     — Send a fake alert for testing
  GET  /api/sports     — ESPN scores proxy (cached 60s)
  POST /api/display    — Screen on/off via wlopm

Usage:
  python3 score_display.py
"""

from flask import Flask, request, jsonify, render_template, Response
from datetime import datetime
from collections import deque
import light_controller
import subprocess
import time
import urllib.request
import json

app = Flask(__name__)

# Store last 50 alerts in memory
alerts = deque(maxlen=50)

# Counter for unique alert IDs
alert_counter = 0

# Sports cache
_sports_cache = {"data": None, "ts": 0}
_SPORTS_CACHE_TTL = 60  # seconds

# Priority teams for Portland area
PRIORITY_TEAMS = {
    "trail blazers", "blazers", "portland trail blazers",
    "timbers", "portland timbers",
    "thorns", "portland thorns",
    "oregon ducks", "oregon",
    "portland fire",
    "oregon state beavers", "oregon state",
}

# ESPN league endpoints (public, no auth needed)
ESPN_LEAGUES = {
    "nfl": "football/nfl",
    "nba": "basketball/nba",
    "wnba": "basketball/wnba",
    "mls": "soccer/usa.1",
    "nwsl": "soccer/usa.nwsl",
    "ncaaf": "football/college-football",
    "ncaam": "basketball/mens-college-basketball",
    "ncaaw": "basketball/womens-college-basketball",
}


def _fetch_espn_league(sport_path):
    """Fetch scoreboard from ESPN public API for one league."""
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/scoreboard"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def _parse_games(league_key, data):
    """Parse ESPN scoreboard JSON into simplified game objects."""
    if not data or "events" not in data:
        return []
    games = []
    for event in data["events"]:
        comp = event.get("competitions", [{}])[0]
        competitors = comp.get("competitors", [])
        if len(competitors) < 2:
            continue

        home = away = None
        for c in competitors:
            if c.get("homeAway") == "home":
                home = c
            else:
                away = c
        if not home or not away:
            continue

        home_name = home.get("team", {}).get("displayName", "")
        away_name = away.get("team", {}).get("displayName", "")
        home_abbr = home.get("team", {}).get("abbreviation", "")
        away_abbr = away.get("team", {}).get("abbreviation", "")
        home_score = home.get("score", "0")
        away_score = away.get("score", "0")
        home_logo = home.get("team", {}).get("logo", "")
        away_logo = away.get("team", {}).get("logo", "")
        home_record = ""
        away_record = ""
        home_rank = 99
        away_rank = 99
        for rec in home.get("records", []):
            if rec.get("type") == "total":
                home_record = rec.get("summary", "")
                break
        for rec in away.get("records", []):
            if rec.get("type") == "total":
                away_record = rec.get("summary", "")
                break
        # Extract national ranking (curatedRank or rank field)
        hr = home.get("curatedRank", home.get("rank", {}))
        if isinstance(hr, dict):
            home_rank = hr.get("current", 99)
        elif isinstance(hr, (int, float)):
            home_rank = int(hr)
        ar = away.get("curatedRank", away.get("rank", {}))
        if isinstance(ar, dict):
            away_rank = ar.get("current", 99)
        elif isinstance(ar, (int, float)):
            away_rank = int(ar)

        status_obj = comp.get("status", event.get("status", {}))
        status_type = status_obj.get("type", {})
        state = status_type.get("state", "pre")  # pre, in, post
        # Use detail (has full date/time) instead of shortDetail ("Scheduled")
        detail = status_type.get("detail", status_type.get("shortDetail", ""))
        short_detail = status_type.get("shortDetail", detail)

        # Venue and broadcast info
        venue = comp.get("venue", {})
        venue_name = venue.get("fullName", "")
        venue_city = venue.get("address", {}).get("city", "")
        broadcasts = []
        for b in comp.get("broadcasts", []):
            broadcasts.extend(b.get("names", []))

        # Check if this is a priority team
        is_priority = (
            home_name.lower() in PRIORITY_TEAMS
            or away_name.lower() in PRIORITY_TEAMS
            or any(t in home_name.lower() for t in PRIORITY_TEAMS)
            or any(t in away_name.lower() for t in PRIORITY_TEAMS)
        )

        games.append({
            "league": league_key.upper(),
            "state": state,
            "detail": short_detail,
            "fullDetail": detail,
            "home": {"name": home_name, "abbr": home_abbr, "score": home_score, "logo": home_logo, "record": home_record, "rank": home_rank},
            "away": {"name": away_name, "abbr": away_abbr, "score": away_score, "logo": away_logo, "record": away_record, "rank": away_rank},
            "priority": is_priority,
            "date": event.get("date", ""),
            "venue": venue_name,
            "venueCity": venue_city,
            "broadcast": ", ".join(broadcasts) if broadcasts else "",
        })
    return games


def _get_sports_data():
    """Fetch all leagues, parse, sort by priority. Cached for 60s."""
    now = time.time()
    if _sports_cache["data"] is not None and (now - _sports_cache["ts"]) < _SPORTS_CACHE_TTL:
        return _sports_cache["data"]

    all_games = []
    for league_key, sport_path in ESPN_LEAGUES.items():
        raw = _fetch_espn_league(sport_path)
        if raw:
            all_games.extend(_parse_games(league_key, raw))

    # Sort: live games first, then by best team ranking (lower = better)
    # Priority Portland teams always float to top when live
    state_order = {"in": 0, "pre": 1, "post": 2}
    all_games.sort(key=lambda g: (
        state_order.get(g["state"], 3),
        0 if g["priority"] else 1,
        min(g["home"].get("rank", 99), g["away"].get("rank", 99)),
        g["date"],
    ))

    _sports_cache["data"] = all_games
    _sports_cache["ts"] = now
    return all_games


@app.route("/")
def display():
    """Serve the lights page."""
    return render_template("display.html")


@app.route("/weather")
def weather():
    """Serve the weather + lights dashboard."""
    return render_template("weather.html")


@app.route("/scores")
def scores():
    """Serve the scores page."""
    return render_template("scores.html")


@app.route("/score-update", methods=["POST"])
def score_update():
    """Receive a score alert from the Pi 400 tracker."""
    global alert_counter

    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    alert_counter += 1
    data["alert_id"] = alert_counter
    data["received_at"] = datetime.now().isoformat()

    alerts.appendleft(data)

    home = data.get("home_abbr", "???")
    away = data.get("away_abbr", "???")
    types = ", ".join(data.get("alert_types", []))
    print(f"[ALERT #{alert_counter}] {types}: {away} {data.get('away_score', '?')} @ {home} {data.get('home_score', '?')}")

    # Score-to-brightness sync: if exactly one live game, adjust parlor/octagon
    alert_types = data.get("alert_types", [])
    if "FINAL" not in alert_types:
        # Count live (non-final) games
        live_games = set()
        for a in alerts:
            if "FINAL" not in a.get("alert_types", []):
                live_games.add(a.get("game_id"))
        if len(live_games) == 1:
            home_score = data.get("home_score", 0)
            away_score = data.get("away_score", 0)
            result = light_controller.apply_score_brightness(home_score, away_score)
            print(f"  [LIGHTS] Score sync: parlor={result['parlor']}%, octagon={result['octagon']}% (delta={result['delta']})")

    return jsonify({"status": "ok", "alert_id": alert_counter})


@app.route("/updates")
def updates():
    """Return recent alerts as JSON. Frontend polls this."""
    since_id = request.args.get("since", 0, type=int)
    new_alerts = [a for a in alerts if a.get("alert_id", 0) > since_id]
    return jsonify({"alerts": new_alerts})


@app.route("/test-alert", methods=["POST"])
def test_alert():
    """Send a fake alert for testing when no games are live."""
    global alert_counter
    alert_counter += 1

    fake = {
        "alert_id": alert_counter,
        "game_id": "test-001",
        "home_team": "Duke Blue Devils",
        "home_abbr": "DUKE",
        "home_score": 72,
        "home_rank": 7,
        "home_logo": "https://a.espncdn.com/i/teamlogos/ncaa/500/150.png",
        "away_team": "North Carolina Tar Heels",
        "away_abbr": "UNC",
        "away_score": 70,
        "away_rank": 12,
        "away_logo": "https://a.espncdn.com/i/teamlogos/ncaa/500/153.png",
        "alert_types": ["SCORE_UPDATE", "CLOSE_GAME"],
        "is_favorite": True,
        "status_detail": "5:32 - 2nd Half",
        "clock": "5:32",
        "period": 2,
        "timestamp": datetime.now().isoformat(),
        "received_at": datetime.now().isoformat(),
    }
    alerts.appendleft(fake)
    print(f"[TEST ALERT #{alert_counter}] UNC 70 @ DUKE 72")
    return jsonify({"status": "ok", "alert_id": alert_counter})


@app.route("/lights")
def lights_status():
    """Get status of all lights."""
    return jsonify(light_controller.get_all_status())


@app.route("/lights/<light_id>", methods=["POST"])
def lights_control(light_id):
    """Control a light. JSON body: {"brightness": 0-100}"""
    data = request.get_json()
    if not data or "brightness" not in data:
        return jsonify({"error": "Need brightness 0-100"}), 400

    brightness = max(0, min(100, int(data["brightness"])))
    light_controller.set_light(light_id, brightness)

    return jsonify({"status": "ok", "light": light_id, "brightness": brightness})


@app.route("/lights/<light_id>/color", methods=["POST"])
def lights_color(light_id):
    """Set a light's color. JSON body: {"r": 0-255, "g": 0-255, "b": 0-255}"""
    data = request.get_json()
    if not data or "r" not in data:
        return jsonify({"error": "Need r, g, b values 0-255"}), 400

    r = max(0, min(255, int(data["r"])))
    g = max(0, min(255, int(data["g"])))
    b = max(0, min(255, int(data["b"])))
    result = light_controller.set_color(light_id, r, g, b)
    return jsonify(result)


@app.route("/presets")
def presets_list():
    """Get available lighting presets."""
    return jsonify(light_controller.get_presets())


@app.route("/presets/<preset_id>", methods=["POST"])
def preset_apply(preset_id):
    """Apply a lighting preset."""
    result = light_controller.apply_preset(preset_id)
    return jsonify(result)


@app.route("/api/sports")
def api_sports():
    """ESPN scores proxy. Returns prioritized games list, cached 60s."""
    games = _get_sports_data()
    return jsonify({"games": games})


@app.route("/api/display", methods=["POST"])
def api_display():
    """Control the DSI-1 display via wlopm. Body: {"action": "on"|"off"}"""
    data = request.get_json()
    if not data or "action" not in data:
        return jsonify({"error": "Need action: on or off"}), 400

    action = data["action"]
    if action == "off":
        try:
            subprocess.run(["wlopm", "--off", "DSI-1"], timeout=5)
            return jsonify({"status": "ok", "display": "off"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    elif action == "on":
        try:
            subprocess.run(["wlopm", "--on", "DSI-1"], timeout=5)
            return jsonify({"status": "ok", "display": "on"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    else:
        return jsonify({"error": "action must be 'on' or 'off'"}), 400


# Webcam image cache: source -> {data, ts}
_webcam_cache = {}
_WEBCAM_CACHE_TTL = 30  # seconds

_WEBCAM_SOURCES = {
    "spirit": "https://portlandweather.com/assets/images/cameras/PortlandSpiritLiveCam.jpeg",
    "youtube": "https://img.youtube.com/vi/IFfE7Ex3NgA/maxresdefault_live.jpg",
}


@app.route("/api/webcam")
def api_webcam():
    """Proxy webcam images to avoid CORS issues. Cached 30s."""
    source = request.args.get("source", "spirit")
    url = _WEBCAM_SOURCES.get(source, _WEBCAM_SOURCES["spirit"])

    now = time.time()
    cached = _webcam_cache.get(source)
    if cached and (now - cached["ts"]) < _WEBCAM_CACHE_TTL:
        return Response(cached["data"], mimetype="image/jpeg",
                        headers={"Cache-Control": "public, max-age=30"})

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
            _webcam_cache[source] = {"data": data, "ts": now}
            return Response(data, mimetype="image/jpeg",
                            headers={"Cache-Control": "public, max-age=30"})
    except Exception as e:
        # Try fallback
        for alt_source, alt_url in _WEBCAM_SOURCES.items():
            if alt_source != source:
                try:
                    req = urllib.request.Request(alt_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        data = resp.read()
                        return Response(data, mimetype="image/jpeg")
                except Exception:
                    pass
        return Response("", status=404)


# Ambient Weather API (cloud) — fetches WS-2902 data
_AW_API_KEY = "REDACTED_AW_API_KEY"
_AW_APP_KEY = "REDACTED_AW_APP_KEY"
_AW_MAC = "C8:C9:A3:16:AA:F9"
_station_cache = {"data": None, "ts": 0}
_STATION_CACHE_TTL = 60  # seconds


def _fetch_ambient_weather():
    """Fetch latest data from Ambient Weather REST API."""
    if not _AW_APP_KEY:
        return None
    url = (
        f"https://rt.ambientweather.net/v1/devices/{_AW_MAC}"
        f"?applicationKey={_AW_APP_KEY}&apiKey={_AW_API_KEY}&limit=60"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = json.loads(resp.read())
        if not raw or not isinstance(raw, list):
            return None
        # raw is a list of readings, newest first
        latest = raw[0]
        return {
            "latest": {
                "received_at": datetime.now().isoformat(),
                "dateutc": latest.get("dateutc", ""),
                "temp_f": _safe_float(latest.get("tempf")),
                "feels_like_f": _safe_float(latest.get("feelsLike")),
                "dew_point_f": _safe_float(latest.get("dewPoint")),
                "humidity": _safe_int(latest.get("humidity")),
                "temp_in_f": _safe_float(latest.get("tempinf")),
                "humidity_in": _safe_int(latest.get("humidityin")),
                "pressure_rel": _safe_float(latest.get("baromrelin")),
                "pressure_abs": _safe_float(latest.get("baromabsin")),
                "wind_speed": _safe_float(latest.get("windspeedmph")),
                "wind_gust": _safe_float(latest.get("windgustmph")),
                "wind_dir": _safe_int(latest.get("winddir")),
                "max_daily_gust": _safe_float(latest.get("maxdailygust")),
                "hourly_rain": _safe_float(latest.get("hourlyrainin")),
                "daily_rain": _safe_float(latest.get("dailyrainin")),
                "weekly_rain": _safe_float(latest.get("weeklyrainin")),
                "monthly_rain": _safe_float(latest.get("monthlyrainin")),
                "yearly_rain": _safe_float(latest.get("yearlyrainin")),
                "solar_radiation": _safe_float(latest.get("solarradiation")),
                "uv": _safe_int(latest.get("uv")),
                "battery": _safe_int(latest.get("battout")),
            },
            "history": [
                {
                    "dateutc": r.get("dateutc", ""),
                    "temp_f": _safe_float(r.get("tempf")),
                    "humidity": _safe_int(r.get("humidity")),
                    "pressure_rel": _safe_float(r.get("baromrelin")),
                    "wind_speed": _safe_float(r.get("windspeedmph")),
                    "wind_dir": _safe_int(r.get("winddir")),
                }
                for r in raw[:60]
            ],
            "count": len(raw),
        }
    except Exception as e:
        print(f"Ambient Weather API error: {e}")
        return None


@app.route("/api/station")
def api_station():
    """Serve latest WS-2902 data from Ambient Weather cloud API. Cached 60s."""
    now = time.time()
    if _station_cache["data"] is not None and (now - _station_cache["ts"]) < _STATION_CACHE_TTL:
        return jsonify(_station_cache["data"])

    data = _fetch_ambient_weather()
    if data:
        _station_cache["data"] = data
        _station_cache["ts"] = now
        return jsonify(data)

    # Return cached even if stale, or empty
    if _station_cache["data"]:
        return jsonify(_station_cache["data"])
    return jsonify({"latest": None, "history": [], "count": 0, "error": "No data yet — check API keys"})


def _safe_float(v):
    try:
        return float(v) if v is not None else None
    except (ValueError, TypeError):
        return None


def _safe_int(v):
    try:
        return int(float(v)) if v is not None else None
    except (ValueError, TypeError):
        return None


if __name__ == "__main__":
    print("=" * 50)
    print("  Score Alert Display + Weather Dashboard")
    print("  Listening on http://0.0.0.0:5000")
    print("  GET /api/station    — WS-2902 data (Ambient Weather API)")
    if not _AW_APP_KEY:
        print("  ⚠ Set _AW_APP_KEY in score_display.py for Ambient Weather!")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5000, debug=False)
