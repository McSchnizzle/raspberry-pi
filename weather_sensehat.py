#!/usr/bin/env python3
"""
Sense HAT weather display: alternates between current weather
and tomorrow's forecast with readable text and weather animations.

Cycle:
  1. Scroll "Now 43F Cloudy" in temperature-colored text
  2. Show weather animation for 12 seconds
  3. Scroll "Sun 50/40 Overcast" in temperature-colored text
  4. Show tomorrow's weather animation for 12 seconds
  5. Repeat
"""

import time
import math
import requests
from sense_hat import SenseHat

# ─── Config ──────────────────────────────────────────────────────────────────
DIM = 0.15
ANIM_DURATION = 12        # seconds of animation between scrolls
REFRESH_MIN = 15          # re-fetch weather interval
FRAME_DELAY = 0.15        # animation speed
SCROLL_SPEED = 0.055      # text scroll speed (lower = faster)

WMO = {
    0: ("Clear", "clear"), 1: ("Clear", "clear"),
    2: ("Cloudy", "cloudy"), 3: ("Overcast", "overcast"),
    45: ("Fog", "fog"), 48: ("Fog", "fog"),
    51: ("Drizzle", "drizzle"), 53: ("Drizzle", "drizzle"), 55: ("Drizzle", "drizzle"),
    56: ("F.Rain", "rain"), 57: ("F.Rain", "rain"),
    61: ("Rain", "rain"), 63: ("Rain", "rain"), 65: ("Rain", "rain"),
    66: ("F.Rain", "rain"), 67: ("F.Rain", "rain"),
    71: ("Snow", "snow"), 73: ("Snow", "snow"), 75: ("Snow", "snow"), 77: ("Snow", "snow"),
    80: ("Showers", "rain"), 81: ("Showers", "rain"), 82: ("Showers", "rain"),
    85: ("Snow", "snow"), 86: ("Snow", "snow"),
    95: ("Storm", "storm"), 96: ("Storm", "storm"), 99: ("Storm", "storm"),
}


def d(r, g, b):
    return [max(0, min(255, int(r * DIM))),
            max(0, min(255, int(g * DIM))),
            max(0, min(255, int(b * DIM)))]


def temp_color(temp_f):
    """Get scroll text color based on temperature."""
    if temp_f < 32:
        return d(80, 130, 255)    # cold blue
    elif temp_f < 45:
        return d(100, 180, 255)   # cool blue
    elif temp_f < 60:
        return d(80, 220, 120)    # mild green
    elif temp_f < 75:
        return d(220, 200, 60)    # warm yellow
    else:
        return d(255, 120, 40)    # hot orange


def fetch_weather():
    """Fetch current + tomorrow weather. Returns dict."""
    try:
        loc = requests.get("http://ip-api.com/json/?fields=lat,lon", timeout=8).json()
        url = (f"https://api.open-meteo.com/v1/forecast?"
               f"latitude={loc['lat']}&longitude={loc['lon']}"
               f"&daily=weathercode,temperature_2m_max,temperature_2m_min,"
               f"precipitation_probability_max"
               f"&current_weather=true"
               f"&temperature_unit=fahrenheit&timezone=auto&forecast_days=3")
        data = requests.get(url, timeout=8).json()
        cw = data["current_weather"]
        daily = data["daily"]
        from datetime import datetime

        cur_code = cw["weathercode"]
        cur_name, cur_anim = WMO.get(cur_code, ("?", "cloudy"))
        cur_temp = round(cw["temperature"])

        tom_code = daily["weathercode"][1]
        tom_name, tom_anim = WMO.get(tom_code, ("?", "cloudy"))
        tom_hi = round(daily["temperature_2m_max"][1])
        tom_lo = round(daily["temperature_2m_min"][1])
        tom_day = datetime.strptime(daily["time"][1], "%Y-%m-%d").strftime("%a")
        tom_precip = daily.get("precipitation_probability_max", [0, 0])[1]

        result = {
            "cur_temp": cur_temp, "cur_name": cur_name, "cur_anim": cur_anim,
            "tom_hi": tom_hi, "tom_lo": tom_lo, "tom_name": tom_name,
            "tom_anim": tom_anim, "tom_day": tom_day, "tom_precip": tom_precip,
        }
        print(f"[fetch] now: {cur_temp}F {cur_name} | {tom_day}: {tom_hi}/{tom_lo}F {tom_name}")
        return result
    except Exception as e:
        print(f"[fetch] error: {e}")
        return {
            "cur_temp": 45, "cur_name": "?", "cur_anim": "cloudy",
            "tom_hi": 48, "tom_lo": 38, "tom_name": "?",
            "tom_anim": "cloudy", "tom_day": "?", "tom_precip": 0,
        }


# ─── Animations ──────────────────────────────────────────────────────────────

def frame_clear(tick):
    pixels = [d(3, 3, 10)] * 64
    cx, cy = 3.5, 3.5
    phase = tick * 0.08
    pulse = 0.85 + 0.15 * math.sin(phase)
    for y in range(8):
        for x in range(8):
            dx, dy = x - cx, y - cy
            dist = math.sqrt(dx*dx + dy*dy)
            if dist < 2.2 * pulse:
                b = max(0, 1.0 - dist / (2.2 * pulse))
                pixels[y*8+x] = d(255*b, 200*b, 50*b)
            elif dist < 4.0:
                ang = math.atan2(dy, dx)
                ray = math.sin(ang * 4 + phase * 1.5)
                if ray > 0.4:
                    b = ray * 0.45 * max(0, 1.0 - dist/4.5)
                    pixels[y*8+x] = d(240*b, 160*b, 30*b)
    return pixels


def frame_cloudy(tick):
    pixels = [d(8, 10, 18)] * 64
    drift = (tick * 0.08) % 16 - 4
    clouds = [(drift, 2, 3.5), (drift + 6, 5, 2.8), (drift - 2, 7, 2.0)]
    for y in range(8):
        for x in range(8):
            br = 0
            for ccx, ccy, rad in clouds:
                dd = math.sqrt((x-ccx)**2 + ((y-ccy)*1.6)**2)
                if dd < rad:
                    br = max(br, 1.0 - dd/rad)
            if br > 0.05:
                v = int(150 * br)
                pixels[y*8+x] = d(v, v, v+12)
    return pixels


def frame_overcast(tick):
    pixels = []
    phase = tick * 0.04
    for y in range(8):
        for x in range(8):
            v = 65 + int(18 * math.sin(x*0.5 + y*0.3 + phase))
            pixels.append(d(v, v, v+5))
    return pixels


def frame_rain(tick):
    pixels = [d(15, 18, 30)] * 64
    for col in range(8):
        drop_y = (tick + col * 3) % 10
        if drop_y < 8:
            pixels[drop_y*8 + col] = d(60, 110, 255)
            if drop_y > 0:
                pixels[(drop_y-1)*8 + col] = d(30, 55, 130)
    return pixels


def frame_drizzle(tick):
    pixels = [d(20, 22, 35)] * 64
    for col in [1, 3, 5, 7]:
        drop_y = (tick + col * 4) % 12
        if drop_y < 8:
            pixels[drop_y*8 + col] = d(50, 90, 190)
    return pixels


def frame_snow(tick):
    pixels = [d(12, 15, 25)] * 64
    flakes = [(0, 7), (1, 3), (2, 11), (3, 5), (4, 9), (5, 2), (6, 8), (7, 6)]
    for col, offset in flakes:
        y = (tick // 2 + offset) % 10
        if y < 8:
            x = (col + int(math.sin(tick * 0.1 + offset) * 0.8)) % 8
            pixels[y*8 + x] = d(190, 200, 255)
    return pixels


def frame_fog(tick):
    pixels = []
    phase = tick * 0.05
    for y in range(8):
        for x in range(8):
            w = math.sin(x*0.6 + y*0.4 + phase) * 0.5 + 0.5
            v = int(35 + 45 * w)
            pixels.append(d(v, v, v+6))
    return pixels


def frame_storm(tick):
    if tick % 35 < 2:
        return [d(180, 180, 160)] * 64
    return frame_rain(tick)


FRAMES = {
    "clear": frame_clear, "cloudy": frame_cloudy, "overcast": frame_overcast,
    "rain": frame_rain, "drizzle": frame_drizzle, "snow": frame_snow,
    "fog": frame_fog, "storm": frame_storm,
}


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    sense = SenseHat()
    sense.low_light = True
    sense.clear()

    wx = fetch_weather()
    last_fetch = time.time()
    showing_current = True  # start with current weather

    print("[main] starting alternating display")

    try:
        while True:
            # Re-fetch weather periodically
            if time.time() - last_fetch > REFRESH_MIN * 60:
                wx = fetch_weather()
                last_fetch = time.time()

            if showing_current:
                # --- CURRENT WEATHER ---
                msg = f"Now {wx['cur_temp']}F {wx['cur_name']}"
                tc = temp_color(wx["cur_temp"])
                print(f"[show] {msg}")
                sense.show_message(msg, scroll_speed=SCROLL_SPEED,
                                   text_colour=tc, back_colour=[0, 0, 0])
                sense.low_light = True

                # Animate current weather for ANIM_DURATION seconds
                frame_fn = FRAMES.get(wx["cur_anim"], frame_cloudy)
                start = time.time()
                tick = 0
                while time.time() - start < ANIM_DURATION:
                    sense.set_pixels(frame_fn(tick))
                    tick += 1
                    time.sleep(FRAME_DELAY)

            else:
                # --- TOMORROW'S FORECAST ---
                msg = f"{wx['tom_day']} {wx['tom_hi']}/{wx['tom_lo']}F {wx['tom_name']}"
                if wx["tom_precip"] > 20:
                    msg += f" {wx['tom_precip']}%"
                tc = temp_color(wx["tom_hi"])
                print(f"[show] {msg}")
                sense.show_message(msg, scroll_speed=SCROLL_SPEED,
                                   text_colour=tc, back_colour=[0, 0, 0])
                sense.low_light = True

                # Animate tomorrow's weather for ANIM_DURATION seconds
                frame_fn = FRAMES.get(wx["tom_anim"], frame_cloudy)
                start = time.time()
                tick = 0
                while time.time() - start < ANIM_DURATION:
                    sense.set_pixels(frame_fn(tick))
                    tick += 1
                    time.sleep(FRAME_DELAY)

            # Toggle between current and tomorrow
            showing_current = not showing_current

    except KeyboardInterrupt:
        sense.clear()
        print("[main] done")


if __name__ == "__main__":
    main()
