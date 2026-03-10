"""
小游戏救场服务
==============
在结束判定前尝试重新抢回有效小游戏检测。
"""

import time

from utils.logger import log


class RescueService:
    """封装丢检测后的 PD 救场逻辑。"""

    def __init__(self, grab_frame, tick_fps, detect_minigame_ready, show_overlay):
        self._grab = grab_frame
        self._tick_fps = tick_fps
        self._detect_ready = detect_minigame_ready
        self._show_overlay = show_overlay

    def try_rescue(self, reason: str, runtime, skip_success_check: bool,
                   attempts: int = 3, interval_s: float = 0.02) -> bool:
        """在结束判定前尝试重新抢回小游戏有效检测。"""
        for i in range(max(1, attempts)):
            try:
                rescue_screen = self._grab()
                self._tick_fps()
                ready, rescue_fish, rescue_bar, rescue_progress = (
                    self._detect_ready(rescue_screen)
                )
            except Exception:
                ready = False
                rescue_fish = rescue_bar = rescue_progress = None

            if ready:
                runtime.no_detect = 0
                runtime.fish_lost = 0
                runtime.fish_gone_since = None
                runtime.bar_gone_since = None
                runtime.obj_gone_count = 0
                runtime.had_good_detection = True
                runtime.game_active = True
                self._show_overlay(
                    rescue_screen, rescue_fish, rescue_bar,
                    progress=None if skip_success_check else rescue_progress,
                    status_text=f"⚠ {reason} 前抢回 PD"
                )
                log.warning(
                    f"[⚠ 抢回] {reason} 前重新检测到有效鱼+条，继续 PD 控制"
                )
                return True

            if i + 1 < attempts:
                time.sleep(interval_s)
        return False
