"""
GUI 设置存储
============
集中处理 settings.json 的读写与 GUI 同步。
"""

import json
import os

import config
from utils.i18n import set_language


class AppSettingsStore:
    """处理 GUI 设置持久化与同步。"""

    CURRENT_KEY = "current"
    PRESETS_KEY = "presets"
    ACTIVE_PRESET_KEY = "active_preset"

    def __init__(self, app):
        self.app = app
        self._active_preset_name = ""

    def refresh_param_widgets(self):
        for attr, (var, vtype) in self.app._param_vars.items():
            var.set(self.config_to_display(attr, vtype))

    def config_to_display(self, attr: str, vtype: str) -> str:
        """将 config 值转换为 GUI 显示值。"""
        val = getattr(config, attr)
        if vtype == "ms":
            return str(round(val * 1000))
        if vtype == "pct":
            return str(round(val * 100))
        if vtype == "int":
            return str(int(val))
        if vtype == "float":
            if val == 0:
                return "0"
            if abs(val) < 0.001:
                return f"{val:.5f}"
            if abs(val) < 0.1:
                return f"{val:.4f}"
            if abs(val) < 10:
                return f"{val:.3f}"
            return f"{val:.1f}"
        return str(val)

    def display_to_config(self, text: str, vtype: str):
        """将 GUI 显示值转换为 config 值。"""
        text = text.strip()
        if not text:
            return None
        try:
            if vtype == "ms":
                return float(text) / 1000.0
            if vtype == "pct":
                return float(text) / 100.0
            if vtype == "int":
                return int(float(text))
            if vtype == "float":
                return float(text)
        except ValueError:
            return None
        return None

    def apply_params(self):
        """读取所有参数输入框，应用到 config 并保存。"""
        changed = []
        for attr, (var, vtype) in self.app._param_vars.items():
            new_val = self.display_to_config(var.get(), vtype)
            if new_val is None:
                continue

            old_val = getattr(config, attr)
            if vtype == "ms":
                is_same = abs(old_val - new_val) < 0.0001
            elif vtype == "float":
                is_same = abs(old_val - new_val) < 1e-7
            else:
                is_same = old_val == new_val

            if not is_same:
                setattr(config, attr, new_val)
                changed.append(f"{attr}: {old_val} → {new_val}")

        if changed:
            self.save()
            self.app._log_t("log.paramsSaved", changes=", ".join(changed))

    def reset_params(self):
        """恢复当前参数为默认值，但保留已保存的预设。"""
        for attr, default_val in self.app.PARAM_DEFAULTS.items():
            setattr(config, attr, default_val)
        self.refresh_param_widgets()
        self.reset_extra_settings()
        self.save()
        self.app._log_t("log.paramsReset")

    def reset_extra_settings(self):
        for attr, value in self.app.SETTINGS_DEFAULTS.items():
            setattr(config, attr, value)

        if hasattr(self.app, "var_skip_success"):
            self.app.var_skip_success.set(config.SKIP_SUCCESS_CHECK)
        if hasattr(self.app, "var_sync_pd_mode"):
            self.app.var_sync_pd_mode.set(config.SYNC_PD_MODE)
        if hasattr(self.app, "var_anti_mode"):
            self.app.var_anti_mode.set(config.ANTI_STUCK_MODE)
        if hasattr(self.app, "var_shake_time"):
            self.app.var_shake_time.set(f"{config.SHAKE_HEAD_TIME:.3f}")
        self.app._update_success_threshold_state()

    def get_active_preset_name(self) -> str:
        return self._active_preset_name

    def get_preset_names(self):
        data = self._read_settings_file()
        presets = data.get(self.PRESETS_KEY, {})
        return sorted(presets.keys())

    def collect_settings_data(self):
        data = {
            attr: getattr(config, attr)
            for attr, _ in self.app._param_vars.items()
        }
        for attr in self.app.PERSISTED_CONFIG_ATTRS:
            data[attr] = getattr(config, attr)
        data["GROUPED_PARAMS_UI"] = self.app.var_grouped_params.get()
        return data

    def _read_settings_file(self):
        if not os.path.exists(config.SETTINGS_FILE):
            return {}
        with open(config.SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}

    def _write_settings_file(self, data: dict):
        with open(config.SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _normalize_storage(self, data: dict):
        if self.CURRENT_KEY in data or self.PRESETS_KEY in data:
            data.setdefault(self.CURRENT_KEY, {})
            data.setdefault(self.PRESETS_KEY, {})
            data.setdefault(self.ACTIVE_PRESET_KEY, "")
            return data

        current = dict(data)
        return {
            self.CURRENT_KEY: current,
            self.PRESETS_KEY: {},
            self.ACTIVE_PRESET_KEY: "",
        }

    def normalize_loaded_settings(self, data: dict):
        if data.get("HOLD_GAIN", 1) < 0.02:
            data["HOLD_GAIN"] = self.app.PARAM_DEFAULTS["HOLD_GAIN"]
        if data.get("SPEED_DAMPING", 0) > 0.001:
            data["SPEED_DAMPING"] = self.app.PARAM_DEFAULTS["SPEED_DAMPING"]
        if data.get("HOLD_MAX_S", 1) <= 0:
            data["HOLD_MAX_S"] = self.app.PARAM_DEFAULTS["HOLD_MAX_S"]
        if data.get("HOLD_MIN_S", 1) <= 0:
            data["HOLD_MIN_S"] = self.app.PARAM_DEFAULTS["HOLD_MIN_S"]
        if data.get("ANTI_STUCK_MODE") == "crouch":
            data["ANTI_STUCK_MODE"] = "jump"

    def apply_loaded_setting(self, attr: str, val) -> bool:
        if attr == "DETECT_ROI":
            if val and isinstance(val, list) and len(val) == 4:
                config.DETECT_ROI = val
                if hasattr(self.app, "var_roi"):
                    x, y, w, h = val
                    self.app.var_roi.set(f"X={x} Y={y} {w}x{h}")
                    self.app.lbl_roi.config(foreground="green")
            else:
                config.DETECT_ROI = None
            return True

        if attr == "LANGUAGE":
            config.LANGUAGE = set_language(str(val))
            if hasattr(self.app, "_update_window_title"):
                self.app._update_window_title()
            if hasattr(self.app, "_refresh_language_choices"):
                self.app._refresh_language_choices()
            if hasattr(self.app, "_rebuild_ui_for_language"):
                self.app._rebuild_ui_for_language()
            return True

        if attr == "YOLO_COLLECT":
            config.YOLO_COLLECT = bool(val)
            if hasattr(self.app, "var_yolo_collect"):
                self.app.var_yolo_collect.set(config.YOLO_COLLECT)
            return True

        if attr == "YOLO_DEVICE":
            if val in ("auto", "cpu", "gpu"):
                config.YOLO_DEVICE = val
                if hasattr(self.app, "var_yolo_device"):
                    self.app.var_yolo_device.set(val)
            return True

        if attr == "SHOW_DEBUG":
            config.SHOW_DEBUG = bool(val)
            if hasattr(self.app, "var_show_debug"):
                self.app.var_show_debug.set(config.SHOW_DEBUG)
            return True

        if attr == "FISH_WHITELIST":
            if isinstance(val, dict):
                config.FISH_WHITELIST.update(val)
            return True

        if attr == "SKIP_SUCCESS_CHECK":
            config.SKIP_SUCCESS_CHECK = bool(val)
            if hasattr(self.app, "var_skip_success"):
                self.app.var_skip_success.set(config.SKIP_SUCCESS_CHECK)
            self.app._update_success_threshold_state()
            return True

        if attr == "SYNC_PD_MODE":
            config.SYNC_PD_MODE = bool(val)
            if hasattr(self.app, "var_sync_pd_mode"):
                self.app.var_sync_pd_mode.set(config.SYNC_PD_MODE)
            return True

        if attr == "ANTI_STUCK_MODE":
            if val in ("shake", "jump"):
                config.ANTI_STUCK_MODE = val
                if hasattr(self.app, "var_anti_mode"):
                    self.app.var_anti_mode.set(val)
            return True

        if attr == "SHAKE_HEAD_TIME":
            config.SHAKE_HEAD_TIME = float(val)
            if hasattr(self.app, "var_shake_time"):
                self.app.var_shake_time.set(f"{config.SHAKE_HEAD_TIME:.3f}")
            return True

        if attr == "GROUPED_PARAMS_UI":
            if hasattr(self.app, "var_grouped_params"):
                self.app.var_grouped_params.set(bool(val))
                self.app._render_params_panel()
            return True

        if attr in self.app._param_vars:
            setattr(config, attr, val)
            var, vtype = self.app._param_vars[attr]
            var.set(self.config_to_display(attr, vtype))
            return True

        return False

    def _apply_settings_blob(self, data: dict):
        self.normalize_loaded_settings(data)
        for attr, val in data.items():
            self.apply_loaded_setting(attr, val)

    def save_preset(self, name: str):
        name = name.strip()
        if not name:
            return
        raw = self._read_settings_file()
        data = self._normalize_storage(raw)
        data[self.PRESETS_KEY][name] = self.collect_settings_data()
        data[self.ACTIVE_PRESET_KEY] = name
        self._active_preset_name = name
        self._write_settings_file(data)

    def load_preset(self, name: str) -> bool:
        raw = self._read_settings_file()
        data = self._normalize_storage(raw)
        preset = data.get(self.PRESETS_KEY, {}).get(name)
        if not isinstance(preset, dict):
            return False
        self._apply_settings_blob(dict(preset))
        data[self.CURRENT_KEY] = self.collect_settings_data()
        data[self.ACTIVE_PRESET_KEY] = name
        self._active_preset_name = name
        self._write_settings_file(data)
        return True

    def delete_preset(self, name: str) -> bool:
        raw = self._read_settings_file()
        data = self._normalize_storage(raw)
        presets = data.get(self.PRESETS_KEY, {})
        if name not in presets:
            return False
        del presets[name]
        if data.get(self.ACTIVE_PRESET_KEY) == name:
            data[self.ACTIVE_PRESET_KEY] = ""
            self._active_preset_name = ""
            if hasattr(self.app, "var_preset_name"):
                self.app.var_preset_name.set("")
        self._write_settings_file(data)
        return True

    def save(self):
        raw = self._read_settings_file()
        data = self._normalize_storage(raw)
        data[self.CURRENT_KEY] = self.collect_settings_data()
        typed_name = (
            self.app.var_preset_name.get().strip()
            if hasattr(self.app, "var_preset_name")
            else ""
        )
        existing_presets = data.get(self.PRESETS_KEY, {})
        if typed_name in existing_presets:
            data[self.ACTIVE_PRESET_KEY] = typed_name
        self._active_preset_name = data[self.ACTIVE_PRESET_KEY]
        self._write_settings_file(data)

    def load(self):
        raw = self._read_settings_file()
        if not raw:
            return
        data = self._normalize_storage(raw)
        current = data.get(self.CURRENT_KEY, {})
        self._apply_settings_blob(dict(current))
        self._active_preset_name = data.get(self.ACTIVE_PRESET_KEY, "")
        if hasattr(self.app, "var_preset_name"):
            self.app.var_preset_name.set(self._active_preset_name)
        self._write_settings_file(data)
