from api_expansion import Expansion
from power_state import get_power_state

import atexit
import json
import math
import os
import signal
import socket
import time
import sys

_COLOR_LOW  = (0, 206, 209)   # water blue at low power
_COLOR_HIGH = (255, 0, 0)     # red at high power
_LOW_WATTS  = 500
_HIGH_WATTS = 5000

# app_config.json LED.mode values (matches app_ui_led.py radio button order)
_MODE_RAINBOW   = 0
_MODE_BREATHING = 1
_MODE_FOLLOW    = 2
_MODE_MANUAL    = 3  # power-based color (replaces static single color)
_MODE_CUSTOM    = 4  # same power-based logic (task_led.py custom mode)
_MODE_CLOSE     = 5

# Hardware set_led_mode() values from api_expansion comment:
# 0: close, 1: RGB, 2: Following, 3: Breathing, 4: Rainbow
_HW_CLOSE     = 0
_HW_RGB       = 1
_HW_FOLLOWING = 2
_HW_BREATHING = 3
_HW_RAINBOW   = 4

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app_config.json')


def _is_network_connected():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('10.255.255.255', 1))
            return s.getsockname()[0] != '0.0.0.0'
    except OSError:
        return False


def _read_config():
    try:
        with open(_CONFIG_PATH, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def _power_to_color(watts):
    if watts is None or watts <= _LOW_WATTS:
        return _COLOR_LOW
    if watts >= _HIGH_WATTS:
        return _COLOR_HIGH
    t = (math.log(watts) - math.log(_LOW_WATTS)) / (math.log(_HIGH_WATTS) - math.log(_LOW_WATTS))
    t = max(0.0, min(1.0, t))
    r = int(_COLOR_LOW[0] + t * (_COLOR_HIGH[0] - _COLOR_LOW[0]))
    g = int(_COLOR_LOW[1] + t * (_COLOR_HIGH[1] - _COLOR_LOW[1]))
    b = int(_COLOR_LOW[2] + t * (_COLOR_HIGH[2] - _COLOR_LOW[2]))
    return (r, g, b)


def _rainbow_step(pos):
    pos = pos % 255
    if pos < 85:
        return (255 - pos * 3, pos * 3, 0)
    if pos < 170:
        pos -= 85
        return (0, 255 - pos * 3, pos * 3)
    pos -= 170
    return (pos * 3, 0, 255 - pos * 3)


class LED_TASK:

    def __init__(self):
        self.expansion = None
        self.running = True
        self._cleaned = False

        try:
            self.expansion = Expansion()
        except Exception as e:
            print(f"[task_led] failed to initialize expansion board: {e}", flush=True)
            sys.exit(1)

        atexit.register(self.cleanup)
        signal.signal(signal.SIGTERM, self.handle_signal)
        signal.signal(signal.SIGINT, self.handle_signal)

    def handle_signal(self, signum=None, frame=None):
        # Only flag here: doing I2C cleanup inside the handler races with the
        # main loop, which lazily reopens the bus and can leave the LEDs lit.
        self.running = False

    def cleanup(self):
        if self._cleaned:
            return
        self._cleaned = True
        try:
            if self.expansion:
                self.expansion.set_led_mode(_HW_CLOSE)
                self.expansion.set_all_led_color(0, 0, 0)
                self.expansion.end()
        except Exception as e:
            print(f"[task_led] cleanup error: {e}", flush=True)

    def run_led_loop(self):
        """Main loop: reads LED.mode from app_config.json every 3 s and dispatches accordingly.

        mode 0 (Rainbow)  → software rainbow wheel (original show_wheel_color behavior)
        mode 1 (Breathing)→ hardware breathing with configured color
        mode 2 (Follow)   → hardware following with configured color
        mode 3 (Manual)   → power-based dynamic color (custom: water-blue→red by wattage)
        mode 4 (Custom)   → same power-based logic as mode 3
        mode 5 (Close)    → LEDs off

        Power-mode status indication:
        network down            → red blink
        Redis down / data stale → yellow blink
        """
        config = _read_config()
        last_config_read = time.monotonic()

        # power-mode state
        blink_on = False
        power_status = 'ok'   # 'ok' | 'offline' | 'stale'
        power_r, power_g, power_b = _COLOR_LOW
        last_power_check = 0.0

        # rainbow state
        rainbow_pos = 0

        # track hardware mode to avoid redundant set_led_mode calls
        hw_mode = None

        try:
            while self.running:
                now = time.monotonic()

                if now - last_config_read >= 3.0:
                    config = _read_config()
                    last_config_read = now

                led_cfg = config.get('LED', {})
                mode = led_cfg.get('mode', _MODE_MANUAL)

                if mode == _MODE_RAINBOW:
                    r, g, b = _rainbow_step(rainbow_pos)
                    if hw_mode != _HW_RGB:
                        self.expansion.set_led_mode(_HW_RGB)
                        hw_mode = _HW_RGB
                    self.expansion.set_all_led_color(r, g, b)
                    rainbow_pos = (rainbow_pos + 1) % 255
                    time.sleep(0.05)

                elif mode == _MODE_BREATHING:
                    r = led_cfg.get('red_value', 0)
                    g = led_cfg.get('green_value', 0)
                    b = led_cfg.get('blue_value', 255)
                    if hw_mode != _HW_BREATHING:
                        self.expansion.set_led_mode(_HW_BREATHING)
                        self.expansion.set_all_led_color(r, g, b)
                        hw_mode = _HW_BREATHING
                    time.sleep(0.5)

                elif mode == _MODE_FOLLOW:
                    r = led_cfg.get('red_value', 0)
                    g = led_cfg.get('green_value', 0)
                    b = led_cfg.get('blue_value', 255)
                    if hw_mode != _HW_FOLLOWING:
                        self.expansion.set_led_mode(_HW_FOLLOWING)
                        self.expansion.set_all_led_color(r, g, b)
                        hw_mode = _HW_FOLLOWING
                    time.sleep(0.5)

                elif mode in (_MODE_MANUAL, _MODE_CUSTOM):
                    if hw_mode != _HW_RGB:
                        self.expansion.set_led_mode(_HW_RGB)
                        hw_mode = _HW_RGB

                    if now - last_power_check >= 3.0:
                        last_power_check = now
                        watts, fresh = get_power_state()
                        if not _is_network_connected():
                            power_status = 'offline'
                        elif watts is None or not fresh:
                            power_status = 'stale'  # Redis down or reading older than 60 s
                        else:
                            power_status = 'ok'
                            power_r, power_g, power_b = _power_to_color(watts)

                    if power_status == 'ok':
                        self.expansion.set_all_led_color(power_r, power_g, power_b)
                    else:
                        blink_on = not blink_on
                        if not blink_on:
                            br, bg, bb = (0, 0, 0)
                        elif power_status == 'offline':
                            br, bg, bb = (255, 0, 0)
                        else:
                            br, bg, bb = (255, 255, 0)
                        self.expansion.set_all_led_color(br, bg, bb)

                    time.sleep(0.5)

                elif mode == _MODE_CLOSE:
                    if hw_mode != _HW_CLOSE:
                        self.expansion.set_led_mode(_HW_CLOSE)
                        hw_mode = _HW_CLOSE
                    time.sleep(0.5)

                else:
                    time.sleep(0.5)

        except KeyboardInterrupt:
            pass

        self.cleanup()


if __name__ == "__main__":
    led_task = None
    try:
        led_task = LED_TASK()
        led_task.run_led_loop()
    except KeyboardInterrupt:
        print("\nShutdown requested by user (Ctrl+C)")
    except Exception as e:
        print(f"Unexpected error: {e}")
