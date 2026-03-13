"""
小游戏收尾服务
==============
集中处理结算、收杆和 UI 退场等待。
"""

import config
from utils.logger import log


class ReelExitHandler:
    """统一处理小游戏结束后的收尾动作。"""

    def __init__(self, input_controller, wait_with_preempt,
                 wait_until_ui_gone, il_adapter):
        self.input = input_controller
        self._wait_with_preempt = wait_with_preempt
        self._wait_until_ui_gone = wait_until_ui_gone
        self.il = il_adapter

    def resolve_result(self, skip_fish: bool,
                       skip_success_check: bool,
                       last_green: float) -> bool:
        """根据本局结果解析成功/失败。"""
        if skip_fish:
            if skip_success_check:
                log.info_t("reel.log.skipNoCheck")
            else:
                log.info_t("reel.log.skipWithProgress", progress=last_green)
            return False
        if skip_success_check:
            log.info_t("reel.log.completeNoCheck")
            return True
        if last_green > config.SUCCESS_PROGRESS:
            log.info_t(
                "reel.log.success",
                progress=last_green,
                threshold=config.SUCCESS_PROGRESS,
            )
            return True
        log.info_t(
            "reel.log.fail",
            progress=last_green,
            threshold=config.SUCCESS_PROGRESS,
        )
        return False

    def perform_exit(self, success: bool) -> bool:
        """执行收杆/等待 UI 消失流程。"""
        self.input.safe_release()
        if self._wait_with_preempt(0.5, "⏳ 收杆准备", allow_preempt=False):
            return success
        if getattr(config, "SKIP_SUCCESS_CHECK", False):
            if self._wait_with_preempt(0.2, "⏳ 收杆点击前等待", allow_preempt=False):
                return success
            self.input.click()
            log.info_t("reel.log.clickSkipCheck")
            success = True
        elif success:
            if self._wait_with_preempt(0.2, "⏳ 收杆点击前等待", allow_preempt=False):
                return success
            self.input.click()
            log.info_t("reel.log.clickSuccess")
        else:
            log.info_t("reel.log.failAutoReel")

        ui_gone = self._wait_until_ui_gone(
            timeout=max(config.POST_CATCH_DELAY + 1.0, 3.0)
        )
        if not ui_gone:
            log.warning_t("reel.log.uiNotGone")
        return success

    def finalize(self, hook_timeout_retry: bool,
                 skip_fish: bool,
                 skip_success_check: bool,
                 last_green: float):
        """统一处理小游戏结束后的结算、收杆与返回值。"""
        if hook_timeout_retry:
            self.input.safe_release()
            if self._wait_with_preempt(0.3, "⏳ 收杆前等待", allow_preempt=False):
                return None
            self.input.click()
            self._wait_until_ui_gone(
                timeout=max(config.POST_CATCH_DELAY + 1.0, 3.0)
            )
            log.info_t("reel.log.retryReturnLoop")
            return None

        success = self.resolve_result(
            skip_fish, skip_success_check, last_green
        )
        if config.IL_RECORD:
            self.il.stop_recording()
            log.info_t("reel.log.recordManualExit")
            return success
        return self.perform_exit(success)
