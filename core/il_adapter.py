"""
行为克隆适配层
==============
封装录制、模型加载与推理控制逻辑，减少 FishingBot 中的 IL 噪音。
"""

import csv
import ctypes
import os
import time
from collections import deque

import config
from utils.logger import log


class ILAdapter:
    """负责行为克隆录制与推理。"""

    def __init__(self, input_controller, pd_controller):
        self.input = input_controller
        self.pd = pd_controller
        self.history = deque(maxlen=config.IL_HISTORY_LEN)
        self.writer = None
        self.file = None
        self.prev_fish_cy = None
        self.mouse_prev = 0
        self.press_streak = 0
        self.prev_velocity = 0.0
        self.log_counter = 0
        self.policy = None
        self.device = "cpu"
        self.norm_mean = None
        self.norm_std = None

    def reset_round(self):
        """为新一局小游戏重置运行时状态。"""
        self.history.clear()
        self.prev_fish_cy = None
        self.mouse_prev = 0
        self.press_streak = 0
        self.prev_velocity = 0.0
        self.log_counter = 0

    def load_policy(self):
        """加载训练好的行为克隆模型。"""
        try:
            import torch
            from imitation.model import FishPolicy

            checkpoint = torch.load(
                config.IL_MODEL_PATH, map_location="cpu", weights_only=True
            )
            if "model_state" in checkpoint:
                state = checkpoint["model_state"]
                self.norm_mean = checkpoint["norm_mean"].numpy()
                self.norm_std = checkpoint["norm_std"].numpy()
                hist_len = checkpoint.get("history_len", config.IL_HISTORY_LEN)
            else:
                state = checkpoint
                self.norm_mean = None
                self.norm_std = None
                hist_len = config.IL_HISTORY_LEN

            model = FishPolicy(history_len=hist_len)
            model.load_state_dict(state)
            model.eval()
            self.device = "cpu"
            if torch.cuda.is_available():
                model = model.cuda()
                self.device = "cuda"
            self.policy = model
            norm_info = "含归一化" if self.norm_mean is not None else "无归一化"
            log.info_t("il.log.modelLoaded", device=self.device, norm_info=norm_info)
        except Exception as e:
            log.warning_t("il.log.modelLoadFailed", error=e)
            self.policy = None

    def start_recording(self):
        """开始录制一局小游戏的数据。"""
        os.makedirs(config.IL_DATA_DIR, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(config.IL_DATA_DIR, f"session_{ts}.csv")
        self.file = open(path, "w", newline="", encoding="utf-8")
        self.writer = csv.writer(self.file)
        self.writer.writerow([
            "frame", "timestamp",
            "fish_cy", "bar_cy", "bar_h",
            "error", "velocity", "fish_delta", "dist_ratio",
            "mouse_pressed",
            "fish_in_bar", "press_streak",
            "predicted", "bar_accel",
        ])
        self.prev_fish_cy = None
        self.mouse_prev = 0
        self.history.clear()
        log.info_t("il.log.recordStarted", path=path)

    def stop_recording(self):
        """结束录制。"""
        if self.file:
            self.file.close()
            self.file = None
            self.writer = None
            log.info_t("il.log.recordStopped")

    @staticmethod
    def is_mouse_pressed() -> bool:
        """检测用户是否按住鼠标左键。"""
        return ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000 != 0

    def build_features(self, fish, bar):
        """从检测结果构建一帧特征。"""
        fish_cy = fish[1] + fish[3] // 2
        bar_cy = bar[1] + bar[3] // 2
        bar_h = bar[3]
        bar_top = bar[1]
        error = bar_cy - fish_cy
        velocity = self.pd.bar_velocity
        fish_delta = 0.0
        if self.prev_fish_cy is not None:
            fish_delta = fish_cy - self.prev_fish_cy
        self.prev_fish_cy = fish_cy
        dist_ratio = error / max(bar_h, 1)
        fish_in_bar = (fish_cy - bar_top) / max(bar_h, 1)

        if self.mouse_prev == 1:
            self.press_streak = max(1, self.press_streak + 1)
        else:
            self.press_streak = min(-1, self.press_streak - 1)
        press_streak = self.press_streak / 10.0
        predicted = error + velocity * 0.15
        bar_accel = velocity - self.prev_velocity
        self.prev_velocity = velocity

        return [
            error, velocity, bar_h, fish_delta, dist_ratio,
            self.mouse_prev, fish_in_bar, press_streak,
            predicted, bar_accel,
        ]

    def record_frame(self, frame_idx, fish, bar):
        """录制一帧。"""
        if fish is None or bar is None or self.writer is None:
            return

        mouse = 1 if self.is_mouse_pressed() else 0
        feats = self.build_features(fish, bar)
        fish_cy = fish[1] + fish[3] // 2
        bar_cy = bar[1] + bar[3] // 2
        bar_h = bar[3]
        error = feats[0]
        velocity = feats[1]
        fish_delta = feats[3]
        dist_ratio = feats[4]
        fish_in_bar = feats[6]
        press_streak = feats[7]
        predicted = feats[8]
        bar_accel = feats[9]

        self.writer.writerow([
            frame_idx, f"{time.time():.4f}",
            fish_cy, bar_cy, bar_h,
            f"{error:.1f}", f"{velocity:.1f}", f"{fish_delta:.1f}",
            f"{dist_ratio:.3f}",
            mouse,
            f"{fish_in_bar:.3f}", f"{press_streak:.2f}",
            f"{predicted:.1f}", f"{bar_accel:.1f}",
        ])
        self.mouse_prev = mouse

    def model_control(self, fish, bar) -> bool:
        """用训练好的模型决定按/松。"""
        import numpy as np
        import torch

        if self.policy is None:
            return False

        if fish is not None and bar is not None:
            feats = self.build_features(fish, bar)
            self.history.append(feats)
        elif fish is None and bar is None:
            self.input.mouse_up()
            self.mouse_prev = 0
            return False

        if len(self.history) < config.IL_HISTORY_LEN:
            self.input.mouse_down()
            self.mouse_prev = 1
            return True

        flat = []
        for frame_features in self.history:
            flat.extend(frame_features)
        flat_np = np.array(flat, dtype=np.float32)
        if self.norm_mean is not None:
            flat_np = (flat_np - self.norm_mean) / self.norm_std
        x = torch.from_numpy(flat_np).unsqueeze(0).to(self.device)
        prob = self.policy.predict(x)

        fish_cy = fish[1] + fish[3] // 2 if fish else -1
        bar_cy = bar[1] + bar[3] // 2 if bar else -1
        thresh = config.IL_PRESS_THRESH
        should_press = prob > thresh
        if should_press:
            self.input.mouse_down()
            self.mouse_prev = 1
            if fish is not None and bar is not None and self.log_counter % 10 == 0:
                log.info(
                    f"  [IL] 鱼Y={fish_cy} 条Y={bar_cy} "
                    f"p={prob:.2f}>{thresh:.2f} → 按住"
                )
            self.log_counter += 1
            return True

        self.input.mouse_up()
        self.mouse_prev = 0
        if fish is not None and bar is not None and self.log_counter % 10 == 0:
            log.info(
                f"  [IL] 鱼Y={fish_cy} 条Y={bar_cy} "
                f"p={prob:.2f}<={thresh:.2f} → 释放"
            )
        self.log_counter += 1
        return False
