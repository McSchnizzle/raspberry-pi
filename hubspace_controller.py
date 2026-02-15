#!/usr/bin/env python3
"""
Hubspace Controller — Controls Hubspace/Afero smart lights via cloud API.

Manages a persistent aioafero bridge in a background thread so Flask
can call sync functions like set_light() and get_status().

Usage:
    import hubspace_controller
    hubspace_controller.start()  # call once at Flask startup
    hubspace_controller.set_brightness("device-id", 50)
    hubspace_controller.set_color("device-id", 255, 0, 128)
    status = hubspace_controller.get_all_status()
"""
import asyncio
import json
import os
import threading
import time
from dotenv import dotenv_values

# Try to import aioafero — if not installed, module degrades gracefully
try:
    from aioafero import v1 as afero_v1
    HAS_AIOAFERO = True
except ImportError:
    HAS_AIOAFERO = False

_env = dotenv_values(os.path.join(os.path.dirname(__file__), ".env"))

# Bridge singleton + event loop in background thread
_bridge = None
_loop = None
_thread = None
_ready = threading.Event()
_devices = {}  # id -> {name, type, ...}
_device_names = {}  # friendly_name_lower -> id
_status_cache = {}  # id -> {data, ts}
_CACHE_TTL = 10  # seconds


def _run_loop():
    """Background thread: run asyncio event loop with the aioafero bridge."""
    global _bridge, _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _loop.run_until_complete(_init_bridge())


async def _init_bridge():
    """Authenticate, discover devices, then keep loop alive for commands."""
    global _bridge

    email = _env.get("HUBSPACE_EMAIL", "")
    pw = _env.get("HUBSPACE_PASSWORD", "")
    if not email or not pw:
        print("[Hubspace] No credentials in .env — skipping")
        _ready.set()
        return

    if not HAS_AIOAFERO:
        print("[Hubspace] aioafero not installed — skipping")
        _ready.set()
        return

    print(f"[Hubspace] Connecting as {email}...")
    _bridge = afero_v1.AferoBridgeV1(email, pw, polling_interval=300)

    try:
        await asyncio.wait_for(_bridge.initialize(), timeout=20)
        print("[Hubspace] Connected!")
    except asyncio.TimeoutError:
        print("[Hubspace] Init timed out — using partial data")
    except Exception as e:
        print(f"[Hubspace] Init error: {e}")

    # Catalog discovered devices via aioafero
    for label, controller in [("light", _bridge.lights), ("fan", _bridge.fans), ("switch", _bridge.switches)]:
        for dev in controller.items:
            _devices[dev.id] = {
                "name": dev.name,
                "type": label,
                "id": dev.id,
            }
            _device_names[dev.name.lower()] = dev.id
            print(f"[Hubspace]   {label}: {dev.name} (id={dev.id})")

    # Fallback: if aioafero found 0 devices, query the API directly
    if not _devices:
        print("[Hubspace] aioafero found 0 devices — trying direct API...")
        try:
            import aiohttp
            token = await _bridge._auth.token()
            account_id = _bridge._account_id
            async with aiohttp.ClientSession() as sess:
                url = f"https://semantics2.afero.net/v1/accounts/{account_id}/metadevices"
                headers = {"Authorization": f"Bearer {token}"}
                async with sess.get(url, headers=headers, params={"expansions": "state"}) as resp:
                    if resp.status == 200:
                        raw = await resp.json()
                        for dev in raw:
                            dev_id = dev.get("id", "")
                            name = dev.get("friendlyName", "unnamed")
                            values = dev.get("state", {}).get("values", [])
                            funcs = [v.get("functionClass") for v in values if v.get("functionClass")]
                            has_power = "power" in funcs
                            if has_power:
                                _devices[dev_id] = {"name": name, "type": "light", "id": dev_id}
                                _device_names[name.lower()] = dev_id
                                print(f"[Hubspace]   light: {name} (id={dev_id})")
        except Exception as e:
            print(f"[Hubspace] Direct API fallback failed: {e}")

    _ready.set()
    print(f"[Hubspace] {len(_devices)} devices ready")

    # Keep the event loop alive for future commands
    try:
        while True:
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        pass
    finally:
        try:
            await asyncio.wait_for(_bridge.close(), timeout=5)
        except Exception:
            pass


def _run_async(coro, timeout=10):
    """Submit a coroutine to the background loop and wait for result."""
    if not _loop or not _bridge:
        return {"error": "Hubspace not connected"}
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    try:
        return future.result(timeout=timeout)
    except Exception as e:
        return {"error": str(e)}


def start():
    """Start the Hubspace bridge in a background thread. Non-blocking."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_run_loop, daemon=True, name="hubspace")
    _thread.start()
    # Wait up to 25s for init (auth + discovery)
    _ready.wait(timeout=25)


def get_devices():
    """Return dict of all discovered devices."""
    return dict(_devices)


def get_device_id(name_or_id):
    """Resolve a device name or ID to the actual ID."""
    if name_or_id in _devices:
        return name_or_id
    return _device_names.get(name_or_id.lower())


# --- Direct Afero REST API helpers ---

async def _api_set_state(device_id, function_class, value):
    """Set a device state value via direct Afero REST API."""
    import aiohttp
    token = await _bridge._auth.token()
    account_id = _bridge._account_id
    url = f"https://semantics2.afero.net/v1/accounts/{account_id}/metadevices/{device_id}/state"
    payload = {
        "metadeviceId": device_id,
        "values": [{
            "functionClass": function_class,
            "functionInstance": None,
            "value": value,
            "lastUpdateTime": int(time.time() * 1000),
        }],
    }
    async with aiohttp.ClientSession() as sess:
        async with sess.put(url, json=payload, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
            "Host": "semantics2.afero.net",
        }) as resp:
            return resp.status in (200, 202, 204)


async def _api_get_state(device_id):
    """Get a device's current state via direct Afero REST API."""
    import aiohttp
    token = await _bridge._auth.token()
    account_id = _bridge._account_id
    url = f"https://semantics2.afero.net/v1/accounts/{account_id}/metadevices/{device_id}"
    async with aiohttp.ClientSession() as sess:
        async with sess.get(url, headers={
            "Authorization": f"Bearer {token}",
        }, params={"expansions": "state"}) as resp:
            if resp.status == 200:
                return await resp.json()
    return None


# --- Control functions (sync wrappers) ---

def turn_on(device_id):
    """Turn a Hubspace light on."""
    resolved = get_device_id(device_id)
    if not resolved:
        return {"error": f"Unknown device: {device_id}"}
    _status_cache.pop(resolved, None)

    async def _do():
        # Try aioafero first
        try:
            light = _bridge.lights.get_device(resolved)
            if light:
                await _bridge.lights.turn_on(resolved)
                return {"ok": True}
        except Exception:
            pass
        # Fallback to direct API
        ok = await _api_set_state(resolved, "power", "on")
        return {"ok": True} if ok else {"error": "API call failed"}

    return _run_async(_do())


def turn_off(device_id):
    """Turn a Hubspace light off."""
    resolved = get_device_id(device_id)
    if not resolved:
        return {"error": f"Unknown device: {device_id}"}
    _status_cache.pop(resolved, None)

    async def _do():
        try:
            light = _bridge.lights.get_device(resolved)
            if light:
                await _bridge.lights.turn_off(resolved)
                return {"ok": True}
        except Exception:
            pass
        ok = await _api_set_state(resolved, "power", "off")
        return {"ok": True} if ok else {"error": "API call failed"}

    return _run_async(_do())


def set_brightness(device_id, brightness):
    """Set brightness (0-100). 0 turns off."""
    resolved = get_device_id(device_id)
    if not resolved:
        return {"error": f"Unknown device: {device_id}"}
    _status_cache.pop(resolved, None)

    async def _do():
        if brightness == 0:
            ok = await _api_set_state(resolved, "power", "off")
        else:
            await _api_set_state(resolved, "power", "on")
            ok = await _api_set_state(resolved, "brightness", brightness)
        return {"ok": True} if ok else {"error": "API call failed"}

    return _run_async(_do())


def set_color(device_id, r, g, b):
    """Set RGB color (0-255 each)."""
    resolved = get_device_id(device_id)
    if not resolved:
        return {"error": f"Unknown device: {device_id}"}
    _status_cache.pop(resolved, None)

    async def _do():
        await _api_set_state(resolved, "power", "on")
        ok = await _api_set_state(resolved, "color-rgb", {"color-rgb": {"r": r, "g": g, "b": b}})
        return {"ok": True} if ok else {"error": "API call failed"}

    return _run_async(_do())


def set_effect(device_id, effect_name):
    """Set a light effect/scene by name."""
    resolved = get_device_id(device_id)
    if not resolved:
        return {"error": f"Unknown device: {device_id}"}
    _status_cache.pop(resolved, None)

    async def _do():
        await _api_set_state(resolved, "power", "on")
        ok = await _api_set_state(resolved, "color-sequence", effect_name)
        return {"ok": True} if ok else {"error": "API call failed"}

    return _run_async(_do())


def set_color_temp(device_id, kelvin):
    """Set color temperature in Kelvin (e.g., 2700-6500)."""
    resolved = get_device_id(device_id)
    if not resolved:
        return {"error": f"Unknown device: {device_id}"}
    _status_cache.pop(resolved, None)

    async def _do():
        await _api_set_state(resolved, "power", "on")
        ok = await _api_set_state(resolved, "color-temperature", kelvin)
        return {"ok": True} if ok else {"error": "API call failed"}

    return _run_async(_do())


def get_status(device_id):
    """Get current status of a Hubspace light."""
    resolved = get_device_id(device_id)
    if not resolved:
        return {"error": f"Unknown device: {device_id}"}

    # Check cache
    cached = _status_cache.get(resolved)
    if cached and (time.time() - cached["ts"]) < _CACHE_TTL:
        return cached["data"]

    if not _bridge:
        return {"on": False, "brightness": 0, "error": "Not connected"}

    # Try aioafero in-memory state first
    try:
        light = _bridge.lights.get_device(resolved)
        if light:
            result = {
                "on": light.on.on if light.on else False,
                "brightness": light.dimming.brightness if light.dimming else 0,
            }
            if light.color:
                result["color"] = [light.color.r, light.color.g, light.color.b]
            if light.color_mode:
                result["mode"] = str(light.color_mode)
            _status_cache[resolved] = {"data": result, "ts": time.time()}
            return result
    except Exception:
        pass

    # Fallback: direct API query
    async def _fetch():
        raw = await _api_get_state(resolved)
        if not raw:
            return {"on": False, "brightness": 0, "error": "API fetch failed"}
        values = raw.get("state", {}).get("values", [])
        funcs = {v.get("functionClass"): v.get("value") for v in values}
        result = {
            "on": funcs.get("power") == "on",
            "brightness": funcs.get("brightness", 0) or 0,
        }
        color_rgb = funcs.get("color-rgb")
        if isinstance(color_rgb, dict):
            rgb = color_rgb.get("color-rgb", color_rgb)
            result["color"] = [rgb.get("r", 0), rgb.get("g", 0), rgb.get("b", 0)]
        return result

    result = _run_async(_fetch())
    if isinstance(result, dict) and "error" not in result:
        _status_cache[resolved] = {"data": result, "ts": time.time()}
    return result


def get_all_status():
    """Get status of all Hubspace lights."""
    result = {}
    for dev_id, dev_info in _devices.items():
        if dev_info["type"] == "light":
            try:
                status = get_status(dev_id)
            except Exception as e:
                status = {"on": False, "brightness": 0, "error": str(e)}
            result[dev_id] = {**dev_info, **status}
    return result
