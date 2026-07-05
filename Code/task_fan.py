from api_expansion import Expansion
from api_systemInfo import SystemInformation
from api_json import ConfigManager
import atexit
import signal
import time
import sys


def _log(message):
    print(f"[task_fan] {message}", flush=True)


class FAN_TASK:

    HW_FAN_MODE_CLOSE  = 0
    HW_FAN_MODE_MANUAL = 1

    LOOP_INTERVAL = 5.0
    SLEEP_SLICE   = 0.5

    def __init__(self):
        self.expansion = None
        self.system_info = None
        self.config_manager = None
        self.board_type = None
        self.running = True
        self._cleaned = False

        try:
            self.expansion = Expansion()
            self.board_type = self.expansion.get_board_type()
        except Exception as e:
            _log(f"failed to initialize expansion board: {e}")
            sys.exit(1)

        if self.board_type not in ("FNK0100", "FNK0107"):
            _log(f"unsupported board type: {self.board_type}")
            sys.exit(1)

        try:
            self.system_info = SystemInformation()
        except Exception as e:
            _log(f"failed to initialize system information: {e}")
            sys.exit(1)

        try:
            self.config_manager = ConfigManager()
        except Exception as e:
            _log(f"failed to load app_config.json: {e}")
            sys.exit(1)

        atexit.register(self.cleanup)
        signal.signal(signal.SIGTERM, self.handle_signal)
        signal.signal(signal.SIGINT, self.handle_signal)

    def handle_signal(self, signum=None, frame=None):
        # Only flag here: doing I2C cleanup inside the handler races with the
        # main loop, which lazily reopens the bus and can leave the fan running.
        self.running = False

    def cleanup(self):
        if self._cleaned:
            return
        self._cleaned = True
        try:
            if self.expansion:
                self.expansion.set_fan_mode(self.HW_FAN_MODE_CLOSE)
                self._set_fan_duty(0)
                self.expansion.end()
        except Exception as e:
            _log(f"cleanup error: {e}")

    def _load_fan_config(self):
        # get_section() re-reads app_config.json, so UI changes apply live
        fan_cfg = self.config_manager.get_section('Fan')
        return {
            'low_threshold':  fan_cfg.get('mode2_low_temp_threshold', 30),
            'high_threshold': fan_cfg.get('mode2_high_temp_threshold', 50),
            'schmitt':        fan_cfg.get('mode2_temp_schmitt', 3),
            'low_speed':      fan_cfg.get('mode2_low_speed', 75),
            'middle_speed':   fan_cfg.get('mode2_middle_speed', 125),
            'high_speed':     fan_cfg.get('mode2_high_speed', 175),
        }

    def _get_cpu_temp(self):
        try:
            return self.system_info.get_raspberry_pi_cpu_temperature()
        except Exception:
            return 0.0

    def _get_case_temp(self):
        try:
            return self.expansion.get_temp()
        except Exception:
            return 0.0

    def _set_fan_duty(self, duty):
        if self.board_type == "FNK0100":
            self.expansion.set_fan_duty(duty, duty)
        elif self.board_type == "FNK0107":
            self.expansion.set_fan_duty(duty, duty, duty)

    def _apply_manual_mode(self):
        self.expansion.set_fan_mode(self.HW_FAN_MODE_MANUAL)
        if self.board_type == "FNK0107":
            self.expansion.set_fan_frequency(50000)
            self.expansion.set_fan_power_switch(1)
        else:  # FNK0100 requires 50 Hz (50000 does not drive the fans)
            self.expansion.set_fan_frequency(50)

    def run_fan_loop(self):
        """Follow Case: software Schmitt-trigger control using max(cpu_temp, case_temp).

        Upward transitions fire at low/high_threshold, downward transitions at
        threshold - schmitt, so the fan does not chatter around a threshold.
        """
        self._apply_manual_mode()

        state = 'STOP'
        self._set_fan_duty(0)
        _log(f"started on {self.board_type}")

        while self.running:
            cfg = self._load_fan_config()
            low     = cfg['low_threshold']
            high    = cfg['high_threshold']
            schmitt = cfg['schmitt']
            speed_map = {
                'STOP': 0,
                'LOW':  cfg['low_speed'],
                'MID':  cfg['middle_speed'],
                'HIGH': cfg['high_speed'],
            }

            cpu_temp  = self._get_cpu_temp()
            case_temp = self._get_case_temp()
            temp = max(cpu_temp, case_temp)

            prev_state = state
            if temp <= 0.0:
                # Both sensors unreadable: fail safe at middle speed.
                state = 'MID'
                if prev_state != 'MID':
                    _log("temperature unavailable (cpu and case both 0); failsafe to MID")
            elif state == 'HIGH':
                if temp < high - schmitt:
                    state = 'MID'
            elif state == 'MID':
                if temp >= high:
                    state = 'HIGH'
                elif temp < low - schmitt:
                    state = 'LOW'
            elif state == 'LOW':
                if temp >= low:
                    state = 'MID'
                elif temp < low - schmitt:
                    state = 'STOP'
            else:  # STOP
                if temp >= low:
                    state = 'MID'  # skip LOW on heating

            if state != prev_state:
                _log(f"{prev_state} -> {state} "
                     f"(cpu={cpu_temp:.1f}C case={case_temp:.1f}C duty={speed_map[state]})")

            # task_manager (or firmware defaults) may switch the board to
            # hardware auto mode, which ignores duty writes; take control back.
            hw_mode = self.expansion.get_fan_mode()
            if hw_mode != self.HW_FAN_MODE_MANUAL:
                _log(f"fan mode was changed externally to {hw_mode}; re-applying manual mode")
                self._apply_manual_mode()

            self._set_fan_duty(speed_map[state])

            slept = 0.0
            while self.running and slept < self.LOOP_INTERVAL:
                time.sleep(self.SLEEP_SLICE)
                slept += self.SLEEP_SLICE

        self.cleanup()


if __name__ == "__main__":
    fan_task = FAN_TASK()
    try:
        fan_task.run_fan_loop()
    except Exception as e:
        _log(f"unexpected error: {e}")
        sys.exit(1)
