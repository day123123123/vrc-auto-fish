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
                log.info("[⏭ 跳过] 非目标鱼, 已放弃")
            else:
                log.info(
                    f"[⏭ 跳过] 非目标鱼, 已放弃 (进度 {last_green:.0%} 不计)"
                )
            return False
        if skip_success_check:
            log.info("[✅ 完成] 已跳过成功检查，不再判定最终进度")
            return True
        if last_green > config.SUCCESS_PROGRESS:
            log.info(
                f"[✅ 成功] 最终进度 {last_green:.0%} > "
                f"{config.SUCCESS_PROGRESS:.0%}，判定成功"
            )
            return True
        log.info(
            f"[❌ 失败] 最终进度 {last_green:.0%} <= "
            f"{config.SUCCESS_PROGRESS:.0%}，判定失败"
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
            log.info("[🎣 收杆] 跳过成功检查, 点击收杆")
            success = True
        elif success:
            if self._wait_with_preempt(0.2, "⏳ 收杆点击前等待", allow_preempt=False):
                return success
            self.input.click()
            log.info("[🎣 收杆] 钓鱼成功, 点击收杆")
        else:
            log.info("[🎣 失败] 鱼竿已自动收回, 跳过收杆")

        ui_gone = self._wait_until_ui_gone(
            timeout=max(config.POST_CATCH_DELAY + 1.0, 3.0)
        )
        if not ui_gone:
            log.warning("[⚠ UI] 收杆后上一轮小游戏UI未及时消失，已继续流程")
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
            log.info("[🎣 收杆] 点击收杆后返回主循环重新抛竿")
            return None

        success = self.resolve_result(
            skip_fish, skip_success_check, last_green
        )
        if config.IL_RECORD:
            self.il.stop_recording()
            log.info("[🎣 收杆] 录制模式 — 请手动收杆")
            return success
        return self.perform_exit(success)
