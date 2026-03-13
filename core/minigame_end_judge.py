"""
小游戏结束判定
==============
封装丢检测、对象不足、单对象超时等结束条件。
"""

import time

import config
from utils.i18n import t
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
                log.warning_t("endJudge.log.noDetectWarning", count=runtime.no_detect)
                self.screen.save_debug(screen, "minigame_lost")
            if runtime.no_detect >= 10 and rescue(
                    "连续丢失中", attempts=4, interval_s=0.02):
                return "continue"
            if runtime.no_detect >= config.VERIFY_FRAMES:
                if rescue("连续丢失结束判定"):
                    return "continue"
                log.info_t(
                    "endJudge.log.noUiEnd",
                    count=runtime.no_detect,
                    limit=config.VERIFY_FRAMES,
                )
                return "break"
            time.sleep(config.GAME_LOOP_INTERVAL)
            return "continue"

        if runtime.no_detect > 5:
            log.info_t("endJudge.log.uiRecovered", count=runtime.no_detect)
        runtime.no_detect = 0

        if fish is None:
            runtime.fish_lost += 1
            if runtime.fish_gone_since is None:
                runtime.fish_gone_since = time.time()
            if runtime.fish_lost == 30:
                log.warning_t("endJudge.log.fishLostWarning", count=runtime.fish_lost)
            if runtime.had_good_detection and runtime.fish_lost > config.FISH_LOST_LIMIT:
                if rescue("鱼丢失结束判定"):
                    return "continue"
                log.info_t("endJudge.log.fishLostEnd", count=runtime.fish_lost)
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
            log.info_t("endJudge.log.fishTimeout", elapsed=elapsed)
            return "break"
        if (runtime.had_good_detection and runtime.bar_gone_since is not None
                and now_t - runtime.bar_gone_since > timeout):
            if rescue("白条消失超时判定"):
                return "continue"
            elapsed = now_t - runtime.bar_gone_since
            log.info_t("endJudge.log.barTimeout", elapsed=elapsed)
            return "break"

        if obj_count < config.OBJ_MIN_COUNT:
            runtime.obj_gone_count += 1
            if runtime.obj_gone_count == 1 or runtime.obj_gone_count % 10 == 0:
                has_f = "鱼✓" if fish is not None else "鱼✗"
                has_b = "条✓" if bar is not None else "条✗"
                log.warning(
                    t(
                        "endJudge.log.objInsufficient",
                        fish=has_f,
                        bar=has_b,
                        count=obj_count,
                        gone=runtime.obj_gone_count,
                        limit=config.OBJ_GONE_LIMIT,
                    )
                )
            if runtime.obj_gone_count >= config.OBJ_GONE_LIMIT:
                if rescue("对象不足结束判定"):
                    return "continue"
                log.info_t(
                    "endJudge.log.objInsufficientEnd",
                    count=runtime.obj_gone_count,
                )
                return "break"
        else:
            if runtime.obj_gone_count > 3:
                log.info_t(
                    "endJudge.log.objRecovered",
                    count=obj_count,
                    gone=runtime.obj_gone_count,
                )
            runtime.obj_gone_count = 0

        return "ok"
