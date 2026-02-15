#!/usr/bin/env python3
"""
Tomorrow's weather forecast on Sense HAT 8x8 LED matrix.
Clean, calm animations. Fetches from Open-Meteo API.
"""

import time
import math
import random
import requests
from sense_hat import SenseHat

# ─── Config ──────────────────────────────────────────────────────────────────
DIM = 0.12                        # brightness scale (low)
REFRESH_MIN = 15                  # re-fetch weather every 15 min
SCROLL_INTERVAL = 90              # scroll temp text every 90s
FRAME_DELAY = 0.15                # slower = calmer animation

WMO = {
    0: "clear", 1: "clear", 2: "cloudy", 3: "overcast",
    45: "fog", 48: "fog",
    51: "drizzle", 53: "drizzle", 55: "drizzle",
    56: "drizzle", 57: "drizzle",
    61: "rain", 63: "rain", 65: "rain",
    66: "rain", 67: "rain",
    71: "snow", 73: "snow", 75: "snow", 77: "snow",
    80: "rain", 81: "rain", 82: "rain",
    85: "snow", 86: "snow",
    95: "storm", 96: "storm", 99: "storm",
}


def d(r, g, b):
    """Dim an RGB value."""
    return [max(0, min(255, int(r * DIM))),
            max(0, min(255, int(g * DIM))),
            max(0, min(255, int(b * DIM)))]


def fetch_tomorrow():
    """Fetch tomorrow's forecast. Returns (condition, hi_f, lo_f, day_name, precip_pct)."""
    try:
        loc = requests.get("http://ip-api.com/json/?fields=lat,lon", timeout=8).json()
        url = (f"https://api.open-meteo.com/v1/forecast?"
               f"latitude={loc['lat']}&longitude={loc['lon']}"
               f"&daily=weathercode,temperature_2m_max,temperature_2m_min,"
               f"precipitation_probability_max&temperature_unit=fahrenheit"
               f"&timezone=auto&forecast_days=3")
        data = requests.get(url, timeout=8).json()
        daily = data["daily"]
        # Index 1 = tomorrow
        code = daily["weathercode"][1]
        hi = round(daily["temperature_2m_max"][1])
        lo = round(daily["temperature_2m_min"][1])
        precip = daily.get("precipitation_probability_max", [0, 0])[1]
        from datetime import datetime
        day_name = datetime.strptime(daily["time"][1], "%Y-%m-%d").strftime("%a")
        cond = WMO.get(code, "clear")
        print(f"[fetch] tomorrow: {day_name} {cond} {hi}/{lo}F {precip}% rain")
        return cond, hi, lo, day_name, precip
    except Exception as e:
        print(f"[fetch] error: {e}")
        return "cloudy", 45, 35, "?", 0


# ─── Animation frames ───────────────────────────────────────────────────────

def frame_clear(tick):
    """Gentle sun with slow pulsing rays."""
    pixels = [d(5, 5, 15)] * 64
    cx, cy = 3.5, 3.5
    phase = tick * 0.08  # slow
    for y in range(8):
        for x in range(8):
            dx, dy = x - cx, y - cy
            dist = math.sqrt(dx*dx + dy*dy)
            ang = math.atan2(dy, dx)
            pulse = 0.85 + 0.15 * math.sin(phase)
            if dist < 2.2 * pulse:
                b = max(0, 1.0 - dist / (2.2 * pulse))
                pixels[y*8+x] = d(255*b, 200*b, 50*b)
            elif dist < 4.0:
                ray = math.sin(ang * 4 + phase * 1.5)
                if ray > 0.4:
                    b = ray * 0.5 * max(0, 1.0 - dist/4.5)
                    pixels[y*8+x] = d(240*b, 170*b, 30*b)
    return pixels


def frame_cloudy(tick):
    """Slow drifting gray clouds."""
    pixels = [d(10, 12, 20)] * 64
    drift = (tick * 0.1) % 16 - 4
    clouds = [(drift, 2, 3.5), (drift + 6, 5, 2.8), (drift - 2, 7, 2.0)]
    for y in range(8):
        for x in range(8):
            br = 0
            for ccx, ccy, rad in clouds:
                wx = x - ccx
                wy = (y - ccy) * 1.6
                dd = math.sqrt(wx*wx + wy*wy)
                if dd < rad:
                    br = max(br, 1.0 - dd/rad)
            if br > 0.05:
                v = int(160 * br)
                pixels[y*8+x] = d(v, v, v+15)
    return pixels


def frame_overcast(tick):
    """Flat gray with subtle shifting."""
    pixels = []
    phase = tick * 0.04
    for y in range(8):
        for x in range(8):
            v = 70 + int(20 * math.sin(x*0.5 + y*0.3 + phase))
            pixels.append(d(v, v, v+5))
    return pixels


def frame_rain(tick):
    """Gentle rain falling."""
    pixels = [d(20, 22, 35)] * 64
    for col in range(8):
        drop_y = (tick + col * 3) % 10
        if drop_y < 8:
            pixels[drop_y*8 + col] = d(70, 120, 255)
            if drop_y > 0:
                pixels[(drop_y-1)*8 + col] = d(35, 60, 140)
    return pixels


def frame_drizzle(tick):
    """Light sparse drizzle."""
    pixels = [d(25, 28, 40)] * 64
    for col in [1, 3, 5, 7]:
        drop_y = (tick + col * 4) % 12
        if drop_y < 8:
            pixels[drop_y*8 + col] = d(60, 100, 200)
    return pixels


def frame_snow(tick):
    """Gentle snowflakes drifting down."""
    pixels = [d(15, 18, 30)] * 64
    flakes = [(0, 7), (1, 3), (2, 11), (3, 5), (4, 9), (5, 2), (6, 8), (7, 6)]
    for col, offset in flakes:
        y = (tick // 2 + offset) % 10
        if y < 8:
            x = (col + int(math.sin(tick * 0.1 + offset) * 0.8)) % 8
            pixels[y*8 + x] = d(200, 210, 255)
    return pixels


def frame_fog(tick):
    """Slow misty waves."""
    pixels = []
    phase = tick * 0.05
    for y in range(8):
        for x in range(8):
            w = math.sin(x*0.6 + y*0.4 + phase) * 0.5 + 0.5
            v = int(40 + 50 * w)
            pixels.append(d(v, v, v+8))
    return pixels


def frame_storm(tick):
    """Rain with occasional soft flash."""
    pixels = frame_rain(tick)
    # Brief flash every ~40 frames
    if tick % 40 < 2:
        pixels = [d(200, 200, 180)] * 64
    return pixels


FRAMES = {
    "clear": frame_clear,
    "cloudy": frame_cloudy,
    "overcast": frame_overcast,
    "rain": frame_rain,
    "drizzle": frame_drizzle,
    "snow": frame_snow,
    "fog": frame_fog,
    "storm": frame_storm,
}


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    sense = SenseHat()
    sense.low_light = True
    sense.clear()

    cond, hi, lo, day_name, precip = fetch_tomorrow()
    last_fetch = time.time()
    last_scroll = time.time() - 60  # scroll soon after start
    tick = 0
    frame_fn = FRAMES.get(cond, frame_cloudy)

    print(f"[main] showing: {day_name} {cond} {hi}/{lo}F")

    try:
        while True:
            now = time.time()

            # Re-fetch weather
            if now - last_fetch > REFRESH_MIN * 60:
                cond, hi, lo, day_name, precip = fetch_tomorrow()
                frame_fn = FRAMES.get(cond, frame_cloudy)
                last_fetch = now

            # Scroll forecast text periodically
            if now - last_scroll > SCROLL_INTERVAL:
                # Color based on high temp
                if hi < 40:
                    tc = d(100, 150, 255)
                elif hi < 65:
                    tc = d(100, 220, 130)
                else:
                    tc = d(255, 170, 50)
                msg = f"{day_name} {hi}/{lo}F"
                if precip > 20:
                    msg += f" {precip}%"
                sense.show_message(msg, scroll_speed=0.06,
                                   text_colour=tc,
                                   back_colour=[0, 0, 0])
                sense.low_light = True
                last_scroll = time.time()
                tick = 0
                continue

            # Animate
            pixels = frame_fn(tick)
            sense.set_pixels(pixels)
            tick += 1
            time.sleep(FRAME_DELAY)

    except KeyboardInterrupt:
        sense.clear()
        print("[main] done")


if __name__ == "__main__":
    main()
