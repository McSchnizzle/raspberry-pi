#!/usr/bin/env python3
"""
Light Controller — Runs on Touchscreen Pi (192.168.1.175)

Controls Lutron Caseta and Tuya/Smart Life lights via local APIs.
Called by the Flask app to handle light on/off/brightness/color commands.
"""

import asyncio
import colorsys
import json
import os
import time

# --- Lutron Caseta ---
from pylutron_caseta.smartbridge import Smartbridge

LUTRON_BRIDGE_IP = "192.168.1.129"
CERTS_DIR = "/home/dp/sports"

# --- Tuya / Smart Life ---
import tinytuya

# Light definitions: name -> config
LIGHTS = {
    "parlor": {
        "name": "Parlor Overhead",
        "system": "lutron",
        "device_id": "5",  # Lutron device ID (string key)
        "has_color": False,
    },
    "octagon": {
        "name": "Octagon Overhead",
        "system": "lutron",
        "device_id": "4",  # Lutron device ID (string key)
        "has_color": False,
    },
    "kitchen_left": {
        "name": "Left Kitchen Cabinet",
        "system": "tuya",
        "device_id": "REDACTED_TUYA_LEFT_ID",
        "local_key": "REDACTED_TUYA_LEFT_KEY",
        "ip": "192.168.1.101",
        "has_color": True,
    },
    "kitchen_right": {
        "name": "Right Kitchen Cabinet",
        "system": "tuya",
        "device_id": "REDACTED_TUYA_RIGHT_ID",
        "local_key": "REDACTED_TUYA_RIGHT_KEY",
        "ip": "192.168.1.110",
        "has_color": True,
    },
}

# Presets: name -> {light_id: {brightness, color (optional)}}
PRESETS = {
    "movie": {
        "name": "Movie Night",
        "parlor": {"brightness": 10},
        "octagon": {"brightness": 10},
        "kitchen_left": {"brightness": 15, "color": [255, 147, 41]},
        "kitchen_right": {"brightness": 15, "color": [255, 147, 41]},
    },
    "cooking": {
        "name": "Cooking",
        "parlor": {"brightness": 80},
        "octagon": {"brightness": 80},
        "kitchen_left": {"brightness": 100, "color": [255, 255, 255]},
        "kitchen_right": {"brightness": 100, "color": [255, 255, 255]},
    },
    "relaxed": {
        "name": "Relaxed",
        "parlor": {"brightness": 40},
        "octagon": {"brightness": 40},
        "kitchen_left": {"brightness": 30, "color": [255, 180, 80]},
        "kitchen_right": {"brightness": 30, "color": [255, 180, 80]},
    },
    "bright": {
        "name": "Full Bright",
        "parlor": {"brightness": 100},
        "octagon": {"brightness": 100},
        "kitchen_left": {"brightness": 100, "color": [255, 255, 255]},
        "kitchen_right": {"brightness": 100, "color": [255, 255, 255]},
    },
    "off": {
        "name": "All Off",
        "parlor": {"brightness": 0},
        "octagon": {"brightness": 0},
        "kitchen_left": {"brightness": 0},
        "kitchen_right": {"brightness": 0},
    },
}

# Status cache: light_id -> {data, timestamp}
_status_cache = {}
_CACHE_TTL = 5  # seconds


def _get_tuya_bulb(light_cfg):
    """Create a Tuya BulbDevice with socket timeout."""
    d = tinytuya.BulbDevice(
        dev_id=light_cfg["device_id"],
        address=light_cfg["ip"],
        local_key=light_cfg["local_key"],
        version=3.3,
    )
    d.set_socketPersistent(False)
    d.set_socketTimeout(3)
    return d


async def _lutron_command(device_id, brightness=None, status_only=False):
    """Connect to Lutron bridge, run command, disconnect."""
    bridge = Smartbridge.create_tls(
        LUTRON_BRIDGE_IP,
        keyfile=os.path.join(CERTS_DIR, "lutron-key.pem"),
        certfile=os.path.join(CERTS_DIR, "lutron-cert.pem"),
        ca_certs=os.path.join(CERTS_DIR, "lutron-bridge-cert.pem"),
    )
    await bridge.connect()
    try:
        if status_only:
            devices = bridge.get_devices()
            dev = devices.get(device_id, {})
            level = dev.get("current_state", 0)
            return {"on": level > 0, "brightness": level}
        else:
            if brightness == 0:
                await bridge.turn_off(device_id)
            else:
                await bridge.set_value(device_id, brightness)
            return {"ok": True}
    finally:
        await bridge.close()


def _tuya_set(light_cfg, brightness, color=None):
    """Set Tuya light brightness and optionally color."""
    if not light_cfg.get("device_id") or not light_cfg.get("local_key"):
        return {"error": "Tuya device not configured yet"}

    d = _get_tuya_bulb(light_cfg)

    if brightness == 0:
        d.turn_off()
    else:
        d.turn_on()
        if color and len(color) == 3:
            r, g, b = color
            # Convert RGB to HSV, use brightness as the V (value) component
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            # set_hsv: h=0-360, s=0-100, v=0-100
            d.set_hsv(h * 360, s * 100, max(1, brightness))
        else:
            # White mode — Tuya brightness range is 10-1000
            tuya_brightness = max(10, int(brightness * 10))
            d.set_brightness(tuya_brightness)

    return {"ok": True}


def _tuya_set_color_only(light_cfg, r, g, b):
    """Set Tuya light color without changing brightness."""
    if not light_cfg.get("device_id") or not light_cfg.get("local_key"):
        return {"error": "Tuya device not configured yet"}

    d = _get_tuya_bulb(light_cfg)
    d.turn_on()
    d.set_colour(r, g, b)
    return {"ok": True}


def _tuya_get_status(light_cfg):
    """Get current status of a Tuya device."""
    if not light_cfg.get("device_id") or not light_cfg.get("local_key"):
        return {"on": False, "brightness": 0, "error": "Not configured"}

    try:
        d = _get_tuya_bulb(light_cfg)
        status = d.status()
        dps = status.get("dps", {})
        is_on = dps.get("1", False) or dps.get("20", False)
        mode = dps.get("2", "white") or dps.get("21", "white")

        color = None
        if mode == "colour":
            # In colour mode, brightness is the V in HSV stored in DPS 5/24
            colour_data = dps.get("5", "") or dps.get("24", "")
            try:
                # tinytuya colour format ends with HHHSSSVVV (each 0-1000 as hex)
                # V is the last 4 hex chars
                v_raw = int(colour_data[-4:], 16) if len(colour_data) >= 4 else 0
                brightness = max(0, min(100, int(v_raw / 10)))
            except (ValueError, TypeError):
                brightness = 50
            # Extract RGB: format A starts with RRGGBB hex
            if colour_data and len(colour_data) >= 6:
                try:
                    r = int(colour_data[0:2], 16)
                    g = int(colour_data[2:4], 16)
                    b = int(colour_data[4:6], 16)
                    color = [r, g, b]
                except (ValueError, TypeError):
                    pass
        else:
            # White mode — DPS 3 or 22 is brightness (10-1000)
            raw_brightness = dps.get("3", 0) or dps.get("22", 0)
            brightness = max(0, min(100, int(raw_brightness / 10)))

        result = {"on": is_on, "brightness": brightness if is_on else 0, "mode": mode}
        if color:
            result["color"] = color
        return result
    except Exception as e:
        return {"on": False, "brightness": 0, "error": str(e)}


def set_light(light_id, brightness, color=None):
    """Set a light's brightness. brightness: 0=off, 1-100. color: [r,g,b] for Tuya."""
    light = LIGHTS.get(light_id)
    if not light:
        return {"error": f"Unknown light: {light_id}"}

    # Invalidate cache on set
    _status_cache.pop(light_id, None)

    if light["system"] == "lutron":
        return asyncio.run(_lutron_command(light["device_id"], brightness=brightness))
    elif light["system"] == "tuya":
        return _tuya_set(light, brightness, color=color)

    return {"error": f"Unknown system: {light['system']}"}


def set_color(light_id, r, g, b):
    """Set a Tuya light's color."""
    light = LIGHTS.get(light_id)
    if not light or light["system"] != "tuya":
        return {"error": "Color only supported on Tuya lights"}
    _status_cache.pop(light_id, None)
    return _tuya_set_color_only(light, r, g, b)


def get_light_status(light_id):
    """Get a light's current status."""
    light = LIGHTS.get(light_id)
    if not light:
        return {"error": f"Unknown light: {light_id}"}

    # Check cache
    cached = _status_cache.get(light_id)
    if cached and (time.time() - cached["ts"]) < _CACHE_TTL:
        return cached["data"]

    if light["system"] == "lutron":
        result = asyncio.run(_lutron_command(light["device_id"], status_only=True))
    elif light["system"] == "tuya":
        result = _tuya_get_status(light)
    else:
        result = {"error": f"Unknown system: {light['system']}"}

    _status_cache[light_id] = {"data": result, "ts": time.time()}
    return result


def get_all_status():
    """Get status of all lights. Each device is queried independently so one failure doesn't block others."""
    result = {}
    for light_id, light in LIGHTS.items():
        try:
            status = get_light_status(light_id)
        except Exception as e:
            status = {"on": False, "brightness": 0, "error": str(e)}
        result[light_id] = {
            "name": light["name"],
            "system": light["system"],
            "has_color": light.get("has_color", False),
            **status,
        }
    return result


def apply_preset(preset_id):
    """Apply a lighting preset."""
    preset = PRESETS.get(preset_id)
    if not preset:
        return {"error": f"Unknown preset: {preset_id}"}

    for light_id in ["parlor", "octagon", "kitchen_left", "kitchen_right"]:
        cfg = preset.get(light_id, {})
        brightness = cfg.get("brightness", 0)
        color = cfg.get("color", None)
        set_light(light_id, brightness, color=color)

    return {"status": "ok", "preset": preset_id}


def apply_score_brightness(home_score, away_score):
    """Set parlor/octagon brightness based on score delta.
    Home winning → parlor brighter. Away winning → octagon brighter.
    """
    delta = home_score - away_score  # positive = home winning

    if delta > 0:
        # Home winning — parlor bright, octagon dim
        parlor_pct = min(100, 20 + delta * 4)
        octagon_pct = max(5, 50 - delta * 4)
    elif delta < 0:
        # Away winning — octagon bright, parlor dim
        octagon_pct = min(100, 20 + abs(delta) * 4)
        parlor_pct = max(5, 50 - abs(delta) * 4)
    else:
        # Tied
        parlor_pct = 50
        octagon_pct = 50

    set_light("parlor", parlor_pct)
    set_light("octagon", octagon_pct)

    return {
        "parlor": parlor_pct,
        "octagon": octagon_pct,
        "delta": delta,
    }


def get_presets():
    """Return available presets with full light values."""
    result = {}
    for k, v in PRESETS.items():
        preset = {"name": v["name"], "lights": {}}
        for light_id in ["parlor", "octagon", "kitchen_left", "kitchen_right"]:
            if light_id in v:
                preset["lights"][light_id] = v[light_id]
        result[k] = preset
    return result
