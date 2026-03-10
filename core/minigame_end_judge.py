"""
小游戏结束判定
==============
封装丢检测、对象不足、单对象超时等结束条件。
"""

import time

import config
from utils.logger import log


class MinigameEndJudge:
    """统一处理小游戏结束前的状态推进和判定。"""

    def __init__(self, input_controller, screen_capture, rescue_service):
        self.input = input_controller
        self.screen = screen_capture
        self.rescue = rescue_service

    def evaluate(self, screen, fish, bar, runtime,
                 skip_success_check: bool, rescue_fn=None):
        """处理小游戏结束判定。返回 ok/continue/break。"""
        rescue = rescue_fn or (
            lambda reason, attempts=3, interval_s=0.02: self.rescue.try_rescue(
                reason, runtime, skip_success_check, attempts, interval_s
            )
        )
        obj_count = ((fish is not None) + (bar is not None))

        if fish is None and bar is None:
            runtime.no_detect += 1
            if runtime.no_detect > 5 and not config.IL_RECORD:
                self.input.mouse_up()
            if runtime.no_detect == 10:
                log.warning(f"[⚠ 丢失] 连续{runtime.no_detect}帧鱼+条均未检测到")
                self.screen.save_debug(screen, "minigame_lost")
            if runtime.no_detect >= 10 and rescue(
                    "连续丢失中", attempts=4, interval_s=0.02):
                return "continue"
            if runtime.no_detect >= config.VERIFY_FRAMES:
                if rescue("连续丢失结束判定"):
                    return "continue"
                log.info(
                    f"[📋 结束] 连续{runtime.no_detect}帧未检测到有效UI，"
                    f"达到结束帧数 {config.VERIFY_FRAMES}"
                )
                return "break"
            time.sleep(config.GAME_LOOP_INTERVAL)
            return "continue"

        if runtime.no_detect > 5:
            log.info(
                f"[✓ 恢复] 重新检测到有效UI "
                f"(之前丢失{runtime.no_detect}帧)"
            )
        runtime.no_detect = 0

        if fish is None:
            runtime.fish_lost += 1
            if runtime.fish_gone_since is None:
                runtime.fish_gone_since = time.time()
            if runtime.fish_lost == 30:
                log.warning(f"[⚠ 鱼丢失] 连续{runtime.fish_lost}帧未检测到鱼")
            if runtime.had_good_detection and runtime.fish_lost > config.FISH_LOST_LIMIT:
                if rescue("鱼丢失结束判定"):
                    return "continue"
                log.info(f"[📋 结束] 鱼已消失{runtime.fish_lost}帧，直接判定结束")
                return "break"
        else:
            runtime.fish_lost = 0
            runtime.fish_gone_since = None
            runtime.had_good_detection = True

        if bar is None:
            if runtime.bar_gone_since is None:
                runtime.bar_gone_since = time.time()
        else:
            runtime.bar_gone_since = None

        timeout = config.SINGLE_OBJ_TIMEOUT
        now_t = time.time()
        if (runtime.had_good_detection and runtime.fish_gone_since is not None
                and now_t - runtime.fish_gone_since > timeout):
            if rescue("鱼消失超时判定"):
                return "continue"
            elapsed = now_t - runtime.fish_gone_since
            log.info(f"[📋 失败] 鱼连续消失 {elapsed:.1f}s, 直接判定结束")
            return "break"
        if (runtime.had_good_detection and runtime.bar_gone_since is not None
                and now_t - runtime.bar_gone_since > timeout):
            if rescue("白条消失超时判定"):
                return "continue"
            elapsed = now_t - runtime.bar_gone_since
            log.info(f"[📋 失败] 白条连续消失 {elapsed:.1f}s, 直接判定结束")
            return "break"

        if obj_count < config.OBJ_MIN_COUNT:
            runtime.obj_gone_count += 1
            if runtime.obj_gone_count == 1 or runtime.obj_gone_count % 10 == 0:
                has_f = "鱼✓" if fish is not None else "鱼✗"
                has_b = "条✓" if bar is not None else "条✗"
                log.warning(
                    f"[⚠ 对象不足] {has_f} {has_b} = {obj_count}个 "
                    f"({runtime.obj_gone_count}/{config.OBJ_GONE_LIMIT})"
                )
            if runtime.obj_gone_count >= config.OBJ_GONE_LIMIT:
                if rescue("对象不足结束判定"):
                    return "continue"
                log.info(
                    f"[📋 结束] 连续{runtime.obj_gone_count}帧对象不足,直接判定结束"
                )
                return "break"
        else:
            if runtime.obj_gone_count > 3:
                log.info(
                    f"[✓ 恢复] 对象数恢复为{obj_count}"
                    f" (之前不足{runtime.obj_gone_count}帧)"
                )
            runtime.obj_gone_count = 0

        return "ok"
