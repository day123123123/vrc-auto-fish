"""
PD 控制器
=========
封装钓鱼小游戏中的 PD 控制状态与按压决策逻辑。
"""

from dataclasses import dataclass
import time

import config


@dataclass
class PDAction:
    """PD 控制器输出的动作决策。"""

    should_press: bool
    hold_s: float = 0.0
    log_message: str = ""


@dataclass(frozen=True)
class PDParams:
    """当前一局 PD 决策所使用的参数快照。"""

    target_fib: float
    hold_gain: float
    speed_damping: float
    base_hold_s: float
    max_hold_s: float
    min_hold_s: float
    velocity_smooth: float

    @classmethod
    def from_config(cls):
        return cls(
            target_fib=0.5,
            hold_gain=getattr(config, "HOLD_GAIN", 0.040),
            speed_damping=getattr(config, "SPEED_DAMPING", 0.00025),
            base_hold_s=getattr(config, "HOLD_MIN_S", 0.025),
            max_hold_s=getattr(config, "HOLD_MAX_S", 0.100),
            min_hold_s=max(0.0, getattr(config, "MIN_HOLD_S", 0.0)),
            velocity_smooth=min(getattr(config, "VELOCITY_SMOOTH", 0.5), 0.95),
        )


class PDController:
    """管理 PD 控制状态，并输出纯动作决策。"""

    def __init__(self):
        self.reset()

    def reset(self):
        """为新一局小游戏重置控制器内部状态。"""
        self.bar_prev_cy = None
        self.bar_prev_time = None
        self.bar_velocity = 0.0
        self.last_hold = None
        self.last_fish_cy = None
        self.fish_smooth_cy = None

    def decide(self, fish, bar, search_region, current_fish_name, detect_roi) -> PDAction:
        """根据鱼/白条检测结果输出动作决策。"""
        now = time.time()
        params = PDParams.from_config()

        if bar is not None:
            bar_cy_raw = bar[1] + bar[3] // 2
            if self.bar_prev_cy is not None and self.bar_prev_time is not None:
                dt = now - self.bar_prev_time
                if dt > 0.003:
                    raw_vel = (bar_cy_raw - self.bar_prev_cy) / dt
                    alpha = params.velocity_smooth
                    self.bar_velocity = (
                        alpha * self.bar_velocity + (1 - alpha) * raw_vel
                    )
            self.bar_prev_cy = bar_cy_raw
            self.bar_prev_time = now

        vel = self.bar_velocity

        if fish is not None and bar is not None:
            raw_fish_cy = fish[1] + fish[3] // 2
            bar_cy = bar[1] + bar[3] // 2

            if self.fish_smooth_cy is None:
                self.fish_smooth_cy = float(raw_fish_cy)
            else:
                self.fish_smooth_cy = (
                    0.4 * raw_fish_cy + 0.6 * self.fish_smooth_cy
                )
            fish_cy = int(self.fish_smooth_cy)

            bar_h = max(bar[3], 1)
            bar_top = bar[1]
            fish_in_bar = (fish_cy - bar_top) / bar_h

            error = params.target_fib - fish_in_bar
            error_clamp = max(-2.0, min(2.0, error))
            hold = (
                params.base_hold_s
                + error_clamp * params.hold_gain
                + vel * params.speed_damping
            )
            hold = max(params.min_hold_s, min(hold, params.max_hold_s))

            self.last_hold = hold
            self.last_fish_cy = fish_cy

            fname = current_fish_name.replace("fish_", "") if current_fish_name else "?"
            if hold >= params.min_hold_s + 0.001:
                return PDAction(
                    should_press=True,
                    hold_s=hold,
                    log_message=(
                        f"  ● [{fname}] 鱼Y={fish_cy} 条Y={bar_cy} "
                        f"fib={fish_in_bar:.2f} "
                        f"v={vel:+.0f} → 按 {hold*1000:.0f}ms"
                    ),
                )

            return PDAction(
                should_press=False,
                log_message=(
                    f"  ○ [{fname}] 鱼Y={fish_cy} 条Y={bar_cy} "
                    f"fib={fish_in_bar:.2f} "
                    f"v={vel:+.0f} → 释放"
                ),
            )

        fallback = self.last_hold if self.last_hold is not None else params.base_hold_s
        fallback = 0.6 * fallback + 0.4 * params.base_hold_s
        self.last_hold = fallback

        if fish is not None:
            fish_cy = fish[1] + fish[3] // 2
            self.last_fish_cy = fish_cy
            if search_region is not None:
                mid_y = search_region[1] + search_region[3] // 2
            elif detect_roi:
                mid_y = detect_roi[1] + detect_roi[3] // 2
            else:
                mid_y = fish_cy
            if fish_cy < mid_y:
                hold = min(fallback * 1.5, params.max_hold_s)
                return PDAction(
                    should_press=True,
                    hold_s=hold,
                    log_message=(
                        f"  (仅鱼) Y={fish_cy} v={vel:+.0f}"
                        f" → 按 {hold*1000:.0f}ms"
                    ),
                )
            return PDAction(should_press=False, log_message=f"  (仅鱼) Y={fish_cy} v={vel:+.0f} → 释放")

        if bar is not None:
            bar_cy = bar[1] + bar[3] // 2
            if self.last_fish_cy is not None:
                est_fib = (self.last_fish_cy - bar[1]) / max(bar[3], 1)
                error = params.target_fib - est_fib
                error_clamp = max(-2.0, min(2.0, error))
                hold = (
                    params.base_hold_s
                    + error_clamp * params.hold_gain
                    + vel * params.speed_damping
                )
                hold = max(params.min_hold_s, min(hold, params.max_hold_s))
            else:
                # 没有鱼历史时，不要强制托条，允许自然下落。
                return PDAction(
                    should_press=False,
                    log_message=f"  (仅条) 条Y={bar_cy} v={vel:+.0f} → 释放"
                )
            if hold >= params.min_hold_s + 0.001:
                return PDAction(
                    should_press=True,
                    hold_s=hold,
                    log_message=(
                        f"  (仅条) 条Y={bar_cy} v={vel:+.0f}"
                        f" → 按 {hold*1000:.0f}ms"
                    ),
                )
            return PDAction(
                should_press=False,
                log_message=f"  (仅条) 条Y={bar_cy} v={vel:+.0f} → 释放"
            )

        return PDAction(should_press=False)

    def control(self, fish, bar, search_region, current_fish_name,
                input_controller, detect_roi) -> bool:
        """
        兼容旧接口: 根据鱼/白条检测结果直接执行按压动作。
        返回: 是否执行了按住操作。
        """
        from core.control_executor import ControlExecutor

        action = self.decide(
            fish, bar, search_region, current_fish_name, detect_roi
        )
        executor = ControlExecutor(input_controller)
        return executor.execute(action)
