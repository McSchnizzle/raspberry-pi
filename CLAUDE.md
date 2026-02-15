# Raspberry Pi Network - Quick Reference

## Machines

### touchpi — Raspberry Pi 4 Model B Rev 1.5
- **IP Address**: 192.168.1.175
- **MAC Address**: e4:5f:01:ae:31:56
- **Hostname**: touchscreen-rp4b
- **SSH**: `ssh dp@192.168.1.175` (password: dp12)
- **OS**: Debian 13 (trixie), 64-bit
- **Display**: Official 7" DSI touchscreen, 800x480, rotated 180deg
- **Features**: Touchscreen display, controls SmartLife and Caseta lights, sports scores
- **Last updated**: 2026-02-14 (kernel 6.12.62, EEPROM Jan 2026)

### keypi — Raspberry Pi 400 Rev 1.0
- **IP Address**: 192.168.1.176
- **MAC Address**: e4:5f:01:19:23:51
- **Hostname**: DrewPi
- **SSH**: `ssh dp@192.168.1.176` (password: dp12)
- **OS**: Debian 13 (trixie), 64-bit
- **Display**: ASUS VK246 monitor on HDMI-A-1 (1920x1080@60Hz)
- **Audio**: HDMI audio via custom PipeWire sink config (see below)
- **Features**: Sense HAT (LED matrix, sensors, joystick), keyboard form factor, YouTube/browser
- **Desktop**: labwc (Wayland compositor) via LightDM
- **Last updated**: 2026-02-14 (kernel 6.12.62, EEPROM Jan 2026)

## Common Credentials
- **Username**: dp
- **Password**: dp12

## Network Discovery
Raspberry Pi MAC prefix: `E4:5F:01` (Raspberry Pi Trading Ltd)
```bash
# Ping sweep to find all devices, then filter by Pi MAC prefix
for i in $(seq 1 254); do ping -c 1 -t 1 192.168.1.$i &>/dev/null & done; wait
arp -a | grep "e4:5f:1"
```

---

## touchpi — Flask App & Light Controls

### Flask App (`~/sports/score_display.py`)
Serves the touchscreen UI. Start with:
```bash
cd ~/sports && python3 score_display.py
# Listens on http://0.0.0.0:5000
```

### Routes
| Route | Description |
|---|---|
| `/` | Light control panel (display.html) |
| `/weather` | Weather dashboard + light controls (weather.html) |
| `/scores` | Live NCAAM score alerts (scores.html) |
| `/lights` | GET: JSON status of all 4 lights |
| `/lights/<id>` | POST: `{"brightness": 0-100}` |
| `/lights/<id>/color` | POST: `{"r":0-255,"g":0-255,"b":0-255}` (Tuya only) |
| `/presets` | GET: available presets |
| `/presets/<id>` | POST: apply a preset |
| `/score-update` | POST: receive score alert from keypi |

### Light Controller (`~/sports/light_controller.py`)
Controls 4 lights via local APIs:

| Light ID | Name | System | Details |
|---|---|---|---|
| `parlor` | Parlor Overhead | Lutron Caseta | Device ID: 5, dimmer only |
| `octagon` | Octagon Overhead | Lutron Caseta | Device ID: 4, dimmer only |
| `kitchen_left` | Left Kitchen Cabinet | Tuya/SmartLife | IP: 192.168.1.101, has color |
| `kitchen_right` | Right Kitchen Cabinet | Tuya/SmartLife | IP: 192.168.1.110, has color |

### Lutron Caseta Bridge
- **IP**: 192.168.1.129
- **Certs**: `~/sports/lutron-key.pem`, `~/sports/lutron-cert.pem`, `~/sports/lutron-bridge-cert.pem`

### Tuya Device Keys
- Kitchen Left: ID `REDACTED_TUYA_LEFT_ID`, key `REDACTED_TUYA_LEFT_KEY`
- Kitchen Right: ID `REDACTED_TUYA_RIGHT_ID`, key `REDACTED_TUYA_RIGHT_KEY`

### Lighting Presets
| Preset ID | Name | Parlor | Octagon | Kitchen (color) |
|---|---|---|---|---|
| `movie` | Movie Night | 10% | 10% | 15% warm amber |
| `cooking` | Cooking | 80% | 80% | 100% white |
| `relaxed` | Relaxed | 40% | 40% | 30% warm |
| `bright` | Full Bright | 100% | 100% | 100% white |
| `off` | All Off | 0% | 0% | 0% |

### Launching Chromium Kiosk Mode
```bash
# From SSH, launch Chromium on the touchscreen:
export WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000
nohup chromium --kiosk --noerrdialogs --disable-infobars \
  --ozone-platform=wayland http://localhost:5000/weather > /dev/null 2>&1 &
```

### Templates (in `~/sports/templates/`)
- `display.html` — Full light control panel with sliders, toggles, color swatches, presets
- `weather.html` — Weather dashboard v2 + compact light controls
  - Canvas-based animated weather particles (rain, snow, sun rays)
  - Portland cityscape SVG silhouette with time-of-day sky gradients
  - Wind compass with directional arrow
  - Barometric pressure gauge with trend indicator (rising/falling/steady)
  - Humidity with dew point calculation
  - UV index with severity label
  - Visibility in miles
  - 6-day forecast with precipitation probability
  - Compact 2x2 light control grid with sliders, toggles, and presets
  - APIs: Open-Meteo (weather), ip-api.com (geolocation)
  - Auto-refreshes every 15 minutes
  - Source kept at local dev machine: `weather-lights-v2.html`
- `scores.html` — NCAAM basketball score alerts

---

## keypi — HDMI & Audio Setup

### HDMI Fix (Sense HAT compatibility)
The Sense HAT's EEPROM overlay can break HDMI with the KMS driver. Fixes applied to `/boot/firmware/config.txt` and `/boot/firmware/cmdline.txt`:

**config.txt changes:**
- Commented out `disable_fw_kms_setup=1` (let firmware help init HDMI)
- Added `hdmi_drive=2` and `hdmi_drive:1=2` (force HDMI mode, not DVI)
- Legacy `hdmi_force_hotplug=1` lines kept but they don't work with KMS

**cmdline.txt addition:**
```
video=HDMI-A-1:1920x1080@60D video=HDMI-A-2:1920x1080@60D
```
The `D` suffix forces output regardless of hotplug detection. This is the KMS equivalent of `hdmi_force_hotplug`.

### HDMI Audio Fix
The ASUS VK246 monitor's EDID contains audio descriptors (LPCM 2ch, 48kHz) but the vc4-hdmi driver doesn't populate ELD files. PipeWire sees no audio profile and falls back to Dummy Output.

**Fix**: Manual PipeWire sink config at `~/.config/pipewire/pipewire.conf.d/hdmi-audio.conf`:
```
context.objects = [
    {   factory = adapter
        args = {
            factory.name     = api.alsa.pcm.sink
            node.name        = "alsa-hdmi-output"
            node.description = "HDMI Audio Output"
            media.class      = "Audio/Sink"
            api.alsa.path    = "hdmi:0"
            audio.format     = "S16LE"
            audio.rate       = 48000
            audio.channels   = 2
            audio.position   = [ FL FR ]
        }
    }
]
```
This creates an "HDMI Audio Output" sink that routes through `plughw:0` (which auto-converts PCM to IEC958 format).

**Volume control**: `wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.5` (for 50%)

### Sense HAT
- Python library: `sense-hat 2.6.0`
- I2C devices: 0x1c (accel/mag), 0x46 (LED/MCU), 0x5c (pressure), 0x5f (humidity), 0x6a (gyro)
- Low brightness mode: `sense.low_light = True`
- The Sense HAT creates `/dev/fb1` (8x8 LED framebuffer); main display is `/dev/fb0`

### Sense HAT Weather Visualization (`~/weather_sensehat.py`)
Animated weather display on the 8x8 LED matrix. Shows current conditions and tomorrow's forecast.

```bash
# Launch (persists after SSH disconnect):
cd ~ && nohup python3 weather_sensehat.py > /tmp/sensehat_weather.log 2>&1 &

# Check status:
cat /tmp/sensehat_weather.log
```

Features:
- Animated patterns per condition: clear (sun rays), cloudy (drifting gray), rain (falling drops), snow (floating flakes), thunderstorm (lightning flashes), fog (shifting mist)
- Temperature-tinted colors (blue=cold, green=mild, orange=warm, red=hot)
- Alternates between current weather and tomorrow's forecast every 30 seconds
- Scrolls temperature text every 2 minutes
- Low brightness mode (`sense.low_light = True`, RGB scale 0.15)
- Fetches weather from Open-Meteo API via ip-api.com geolocation
- Refreshes weather data every 15 minutes

---

## Useful Commands

### SSH shortcuts
```bash
# Over SSH, set these env vars for Wayland commands:
export WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000
```

### System checks
```bash
sudo rpi-eeprom-update              # Check firmware/EEPROM version
wlr-randr                           # HDMI display status (Wayland)
wpctl status                        # PipeWire audio status
wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.5   # Set volume to 50%
cat /proc/asound/card0/eld*         # HDMI audio ELD data
cat /sys/firmware/devicetree/base/model     # Pi model identification
```

### Updates
```bash
sudo apt update && sudo apt upgrade -y    # Update packages
sudo rpi-eeprom-update -a && sudo reboot  # Update bootloader EEPROM
```

### Sense HAT quick test
```bash
python3 -c "from sense_hat import SenseHat; s = SenseHat(); s.low_light = True; s.show_message('Hi')"
```

### Light control from command line
```bash
# On touchpi, or from any machine on the network:
curl -X POST http://192.168.1.175:5000/lights/parlor -H 'Content-Type: application/json' -d '{"brightness":50}'
curl -X POST http://192.168.1.175:5000/presets/relaxed -X POST
```
