#!/usr/bin/env python3
"""
Discover Hubspace devices — run once to find device IDs.
Outputs device names, IDs, capabilities. Saves full JSON for reference.
Uses a hard timeout to prevent hanging.
"""
import asyncio
import json
import signal
import sys
import os
from dotenv import dotenv_values

# Hard kill after 20 seconds no matter what
signal.signal(signal.SIGALRM, lambda *_: (print("\nTimeout — exiting."), os._exit(1)))
signal.alarm(20)

env = dotenv_values(os.path.join(os.path.dirname(__file__), ".env"))


async def discover():
    from aioafero import v1

    print(f"Authenticating as {env['HUBSPACE_EMAIL']}...", flush=True)
    bridge = v1.AferoBridgeV1(
        env["HUBSPACE_EMAIL"],
        env["HUBSPACE_PASSWORD"],
        polling_interval=9999,
    )

    try:
        await asyncio.wait_for(bridge.initialize(), timeout=15)
    except asyncio.TimeoutError:
        print("Initialize timed out — trying to read what we got...", flush=True)
    except Exception as e:
        print(f"Init error (may be ok): {e}", flush=True)

    print(f"\nDiscovered:", flush=True)
    print(f"  Lights:   {len(bridge.lights.items)}", flush=True)
    print(f"  Fans:     {len(bridge.fans.items)}", flush=True)
    print(f"  Switches: {len(bridge.switches.items)}", flush=True)

    all_devices = []

    for label, controller in [
        ("LIGHT", bridge.lights),
        ("FAN", bridge.fans),
        ("SWITCH", bridge.switches),
    ]:
        for dev in controller.items:
            print(f"\n  [{label}] {dev.name}", flush=True)
            print(f"    ID: {dev.id}", flush=True)
            info = {}
            for attr in ["on", "dimming", "color", "color_temperature", "color_mode", "effect", "model"]:
                val = getattr(dev, attr, None)
                if val is not None:
                    print(f"    {attr}: {val}", flush=True)
                    info[attr] = str(val)
            all_devices.append({
                "type": label,
                "name": dev.name,
                "id": dev.id,
                **info,
            })

    # Save for reference
    out_path = os.path.join(os.path.dirname(__file__), "hubspace_devices.json")
    with open(out_path, "w") as f:
        json.dump(all_devices, f, indent=2)
    print(f"\nSaved to {out_path}", flush=True)

    try:
        await asyncio.wait_for(bridge.close(), timeout=3)
    except Exception:
        pass


asyncio.run(discover())
