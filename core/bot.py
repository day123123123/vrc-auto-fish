"""
钓鱼机器人主逻辑
================
状态机: IDLE → CASTING → WAITING → HOOKING → FISHING → (循环)

设计为可在后台线程运行，通过共享属性与 GUI 通信。
"""

import time
import cv2
import os
import queue
import threading
from concurrent.futures import ThreadPoolExecutor

import config
from core.control_backends import build_control_backend
from core.control_executor import ControlExecutor
from core.window import WindowManager
from core.screen import ScreenCapture
from core.detector import ImageDetector
from core.debug_overlay import DebugOverlay
from core.input_ctrl import InputController
from core.il_adapter import ILAdapter
from core.minigame_end_judge import MinigameEndJudge
from core.minigame_detection import MinigameDetectionService
from core.minigame_reel_exit import ReelExitHandler
from core.minigame_rescue import RescueService
from core.minigame_runner import MinigameRunner
from core.minigame_session import MinigameSession
from core.minigame_runtime import DetectionContext, MinigameRuntime, PipelineContext
from core.pd_controller import PDController
from utils.i18n import fish_name, t
from utils.logger import log

_yolo_detector = None
_yolo_device_used = None

def _get_yolo_detector(force_reload=False):
    """延迟加载 YOLO 检测器（避免未安装 ultralytics 时报错）"""
    global _yolo_detector, _yolo_device_used
    current_device = config.normalize_yolo_device(config.YOLO_DEVICE)
    if force_reload:
        _yolo_detector = None
    if _yolo_detector is None or _yolo_device_used != current_device:
        from core.yolo_detector import YoloDetector
        _yolo_detector = YoloDetector(
            config.YOLO_MODEL,
            conf=config.YOLO_CONF,
            device=current_device,
        )
        _yolo_device_used = current_device
    return _yolo_detector


class FishingBot:
    """VRChat 自动钓鱼机器人"""

    # 鱼模板 → 调试框颜色 (BGR)
    FISH_COLORS = {
        "fish_generic": (80, 80, 80),
        "fish_black":   (80, 80, 80),
        "fish_white":   (255, 255, 255),
        "fish_relic":   (50, 127, 180),
        "fish_green":   (0, 255, 0),
        "fish_clover":  (200, 220, 0),
        "fish_question": (80, 255, 255),
        "fish_blue":    (255, 150, 0),
        "fish_purple":  (200, 50, 200),
        "fish_golden":  (0, 215, 255),
        "fish_pink":    (180, 105, 255),
        "fish_red":     (0, 0, 255),
        "fish_rainbow": (0, 255, 255),
    }

    def __init__(self):
        self.window   = WindowManager(config.WINDOW_TITLE)
        self.screen   = ScreenCapture()
        self.detector = ImageDetector(config.IMG_DIR, config.TEMPLATE_FILES)
        self.input    = InputController(self.window)

        self.yolo = None
        if config.USE_YOLO:
            try:
                self.yolo = _get_yolo_detector()
            except Exception as e:
                log.warning_t("bot.log.yoloStartupFailed", error=e)

        # ── 共享状态（GUI 读取）──
        self.running    = False
        self.debug_mode = False
        self.fish_count = 0
        self.fish_stats = {}          # {fish_key: count} 色別釣果
        self.state      = "bot.state.ready"

        # ── PD 控制器 ──
        self.pd = PDController()
        self.minigame_detection = MinigameDetectionService(
            self.detector,
            self.pd,
            lambda: _get_yolo_detector() if config.USE_YOLO else None,
            lambda: self._bar_locked_cx,
        )

        # ── Debug overlay (独立线程, 不阻塞钓鱼逻辑) ──
        self.debug_overlay = DebugOverlay()

        # ── 旋转补偿状态 ──
        self._track_angle   = 0.0        # 轨道偏转角度 (度)
        self._need_rotation = False      # 是否需要旋转补偿

        # ── 自动 ROI (未手动框选时, 从验证阶段自动推断) ──
        self._auto_roi = None

        # ── 鱼/白条位置平滑 (减少检测抖动) ──
        self._bar_smooth_cy = None       # 平滑后的白条中心 Y
        self._current_fish_name = ""     # 当前检测到的鱼模板名 (如 "fish_blue")
        self._bar_locked_cx  = None      # ★ 轨道X轴锁定 (白条+鱼共用)
        self._pool = ThreadPoolExecutor(max_workers=2)

        # ── 行为克隆 ──
        self.il = ILAdapter(self.input, self.pd)
        if config.IL_USE_MODEL:
            self.il.load_policy()

        # ── 全局抢占小游戏 ──
        self._force_minigame = False
        self._active_control_backend = None
        self._ensure_minigame_services()

    def _get_minigame_session(self) -> MinigameSession:
        """惰性获取小游戏会话对象，兼容测试里的 __new__ 假对象。"""
        session = getattr(self, "minigame_session", None)
        if session is None:
            session = MinigameSession(self)
            self.minigame_session = session
        return session

    def _ensure_minigame_services(self):
        """惰性初始化小游戏编排相关服务，兼容测试里的 __new__ 假对象。"""
        self._get_minigame_session()
        if not hasattr(self, "control_executor") or self.control_executor is None:
            self.control_executor = ControlExecutor(self.input)
        if not hasattr(self, "minigame_rescue") or self.minigame_rescue is None:
            self.minigame_rescue = RescueService(
                self._grab,
                self._tick_fps,
                self._detect_minigame_ready_now,
                self._show_debug_overlay,
            )
        if not hasattr(self, "minigame_end_judge") or self.minigame_end_judge is None:
            self.minigame_end_judge = MinigameEndJudge(
                self.input,
                self.screen,
                self.minigame_rescue,
            )
        if not hasattr(self, "minigame_reel_exit") or self.minigame_reel_exit is None:
            self.minigame_reel_exit = ReelExitHandler(
                self.input,
                self._wait_with_minigame_preempt,
                self._wait_until_ui_gone,
                self.il,
            )
        if not hasattr(self, "minigame_runner") or self.minigame_runner is None:
            self.minigame_runner = MinigameRunner(self)

    def _build_control_backend(self):
        """为当前一局小游戏构建控制后端。"""
        self._ensure_minigame_services()
        self.control_executor = ControlExecutor(self.input)
        return build_control_backend(self)

    def _tick_fps(self):
        """在任意阶段更新调试窗口 FPS 统计。"""
        self.debug_overlay.tick_fps()

    # ══════════════════════════════════════════════════════
    #  截取游戏画面
    # ══════════════════════════════════════════════════════

    def _grab(self):
        """截取 VRChat 窗口客户区，保证返回非空 BGR 图像"""
        try:
            img, _ = self.screen.grab_window(self.window)
            if img is not None and img.size > 0:
                return img
        except Exception:
            pass
        import numpy as np
        return np.zeros((100, 100, 3), dtype=np.uint8)

    def _grab_rotated(self):
        """截取窗口客户区，如果轨道有倾斜角则旋转使轨道变垂直"""
        img = self._grab()
        if self._need_rotation:
            return self._rotate_for_detection(img)
        return img

    def _rotate_for_detection(self, screen):
        """
        旋转图像使倾斜的钓鱼轨道变为垂直方向。

        原理: 轨道偏转 θ° → 旋转图像 -θ° → 轨道变垂直
        旋转后现有的所有模板匹配代码都能正常工作。
        """
        import numpy as np
        h, w = screen.shape[:2]
        center = (w / 2.0, h / 2.0)

        # getRotationMatrix2D: 正角度在图像坐标系中为顺时针旋转
        # 轨道向右偏 θ° → 需要逆时针旋转 θ° → 参数传 -θ
        M = cv2.getRotationMatrix2D(center, -self._track_angle, 1.0)

        # 扩大画布避免旋转后内容被裁切
        cos_a = abs(M[0, 0])
        sin_a = abs(M[0, 1])
        new_w = int(h * sin_a + w * cos_a)
        new_h = int(h * cos_a + w * sin_a)
        M[0, 2] += (new_w - w) / 2
        M[1, 2] += (new_h - h) / 2

        return cv2.warpAffine(
            screen, M, (new_w, new_h), borderValue=(0, 0, 0)
        )

    # ══════════════════════════════════════════════════════
    #  第1步: 抛竿
    # ══════════════════════════════════════════════════════

    def _set_minigame_preempt(self, reason: str):
        """设置全局小游戏抢占标记。"""
        if not self._force_minigame:
            self._force_minigame = True
            log.warning_t("bot.log.preemptSwitch", reason=reason)

    def _consume_minigame_preempt(self) -> bool:
        """读取并清空小游戏抢占标记。"""
        flag = self._force_minigame
        self._force_minigame = False
        return flag

    def _cast_rod(self):
        self.state = "bot.state.casting"
        if config.IL_RECORD:
            log.info_t("bot.log.castRecord")
        else:
            self.input.click()
            if self._wait_with_minigame_preempt(0.15, "🎣 抛竿后摇杆等待"):
                return True
            mode = str(getattr(config, "ANTI_STUCK_MODE", "jump")).strip().lower()
            if mode in ("none", "off"):
                # 防卡杆关闭: 既不跳跃也不摇头
                log.info_t("bot.log.castNoAntiStuck")
            elif mode == "shake":
                log.info_t("bot.log.castShake")
                self.input.shake_head()
            else:
                log.info_t("bot.log.castJump")
                self.input.jump_toggle()
        # ★ 从抛竿开始就显示 debug 窗口
        try:
            screen = self._grab()
            self._tick_fps()
            self._show_debug_overlay(screen, status_text="🎣 抛竿中...")
        except Exception:
            pass
        return self._wait_with_minigame_preempt(config.CAST_DELAY, "🎣 抛竿冷却")

    # ══════════════════════════════════════════════════════
    #  第2步: 等待咬钩
    # ══════════════════════════════════════════════════════

    def _detect_ui_once(self, screen, return_bbox=False):
        """单帧检测: 白条是否仍在（YOLO优先，模板兜底）。
        return_bbox=True 时返回 (found, (min_x, min_y, max_x, max_y))"""
        _roi = config.DETECT_ROI
        _use_yolo = config.USE_YOLO and self.yolo is not None
        bbox = None

        if _use_yolo:
            try:
                det = self.yolo.detect(screen, _roi)
                if det.get("bar"):
                    yb = det["bar"]
                    if return_bbox:
                        bbox = (yb[0], yb[1], yb[0] + yb[2], yb[1] + yb[3])
                        return True, bbox
                    return True
                if getattr(config, "YOLO_RAW_DEBUG", False):
                    fish_name = det.get("fish_name") or "-"
                    fish_conf = f"{det['fish'][4]:.2f}" if det.get("fish") is not None else "-"
                    log.info(
                        f"[YOLO RAW] UI检查: YOLO未检出bar，准备回退模板 "
                        f"(fish={fish_name}@{fish_conf}, THRESH_BAR={config.THRESH_BAR:.2f})"
                    )
            except Exception:
                pass

        bar = self.detector.find_multiscale(
            screen, "bar", config.THRESH_BAR,
            scales=config.BAR_SCALES, search_region=_roi)
        if bar:
            if getattr(config, "YOLO_RAW_DEBUG", False):
                log.info(
                    f"[YOLO RAW] UI检查: 模板bar兜底命中 conf={bar[4]:.3f} "
                    f"(THRESH_BAR={config.THRESH_BAR:.2f})"
                )
            if return_bbox:
                bbox = (bar[0], bar[1], bar[0] + bar[2], bar[1] + bar[3])
                return True, bbox
            return True
        if _use_yolo and getattr(config, "YOLO_RAW_DEBUG", False):
            log.info(
                f"[YOLO RAW] UI检查: 模板bar兜底也未命中 (THRESH_BAR={config.THRESH_BAR:.2f})"
            )
        if return_bbox:
            return False, None
        return False

    def _wait_until_ui_gone(self, timeout=3.0, clear_frames=2):
        """收杆后等待上一轮小游戏 UI 消失，避免串到下一轮。"""
        self.state = "bot.state.waitUiGone"
        # 清掉上一阶段遗留的抢占标记，避免收尾阶段把本局残留 UI 串成下一局。
        self._force_minigame = False
        t0 = time.time()
        clear_count = 0

        while self.running and time.time() - t0 < timeout:
            screen = self._grab()
            self._tick_fps()
            try:
                ready, fish, bar, progress = self._detect_minigame_ready_now(screen)
            except Exception:
                ready, fish, bar, progress = False, None, None, None

            if ready:
                clear_count = 0
                self._show_debug_overlay(
                    screen, fish, bar, progress=progress,
                    status_text="⏳ 本局小游戏UI仍存在，等待退场..."
                )
                time.sleep(0.05)
                continue

            ui_found = self._detect_ui_once(screen)
            if ui_found:
                clear_count = 0
                self._show_debug_overlay(
                    screen,
                    status_text="⏳ 等待上一轮小游戏UI消失..."
                )
            else:
                clear_count += 1
                self._show_debug_overlay(
                    screen,
                    status_text=f"✅ UI退场确认 {clear_count}/{clear_frames}"
                )
                if clear_count >= clear_frames:
                    return True
            time.sleep(0.05)

        return False

    def _detect_minigame_ready_now(self, screen):
        """任意阶段检查是否已经满足进入小游戏控制的条件。"""
        skip_success = getattr(config, "SKIP_SUCCESS_CHECK", False)
        if config.USE_YOLO:
            try:
                self.yolo = _get_yolo_detector()
            except Exception:
                pass

        if config.USE_YOLO and self.yolo is not None:
            det = self.yolo.detect(screen, roi=config.DETECT_ROI or self._auto_roi)
            fish = det.get("fish")
            bar = det.get("bar")
            progress = None if skip_success else det.get("progress")
            ready = ((fish is not None)
                     + (bar is not None)
                     + (progress is not None)) >= 2
            return ready, fish, bar, progress

        search_region = config.DETECT_ROI or self._auto_roi
        fish = self.detector.find_fish(
            screen, config.THRESH_FISH, search_region=search_region)
        bar = self.detector.find_multiscale(
            screen, "bar", config.THRESH_BAR,
            search_region=search_region, scales=config.BAR_SCALES)
        ready = (fish is not None) and (bar is not None)
        return ready, fish, bar, None

    def _wait_with_minigame_preempt(self, duration, status_text, allow_preempt=True):
        """等待期间持续检测，若满足小游戏条件则立即抢占进入控制。"""
        if allow_preempt and self._force_minigame:
            return True
        t0 = time.time()
        while self.running and time.time() - t0 < duration:
            screen = self._grab()
            self._tick_fps()
            try:
                ready, fish, bar, progress = self._detect_minigame_ready_now(screen)
            except Exception:
                ready, fish, bar, progress = False, None, None, None

            remain = max(0.0, duration - (time.time() - t0))
            self._show_debug_overlay(
                screen, fish, bar, progress=progress,
                status_text=f"{status_text} ({remain:.1f}s)"
            )

            if allow_preempt and ready:
                self._set_minigame_preempt(f"{self.state} 阶段已满足小游戏条件")
                return True

            time.sleep(0.05)

        return False

    def _hook_fish(self):
        self.state = "bot.state.hooking"
        if config.IL_RECORD:
            log.info_t("bot.log.manualHookRecord")
        else:
            log.info_t("bot.log.manualHookClick")
            if self._wait_with_minigame_preempt(config.HOOK_PRE_DELAY, "🪝 提竿前等待"):
                return True
            self.input.click()
        # ★ 提竿后短暂等待, 持续刷新 debug 窗口
        return self._wait_with_minigame_preempt(
            config.HOOK_POST_DELAY, "🪝 提竿后等待小游戏UI")

    def _wait_for_minigame_ui(self) -> bool:
        """
        录制模式专用: 持续等待小游戏UI出现。
        要求白条和轨道同时检测到, 且连续 3 帧确认, 防止误触发。
        """
        consecutive = 0
        required = 3
        _roi = config.DETECT_ROI
        logged = False

        while self.running:
            screen = self._grab()
            self._tick_fps()
            self._show_debug_overlay(
                screen,
                status_text=f"[IL] 等待小游戏... ({consecutive}/{required})"
            )

            bar = self.detector.find_multiscale(
                screen, "bar", config.THRESH_BAR,
                scales=config.BAR_SCALES, search_region=_roi,
            )
            track = self.detector.find_multiscale(
                screen, "track", config.THRESH_TRACK,
                search_region=_roi,
            )

            if bar is not None and track is not None:
                bar_cx = bar[0] + bar[2] // 2
                track_cx = track[0] + track[2] // 2
                if abs(bar_cx - track_cx) < 150:
                    consecutive += 1
                    if not logged and consecutive >= 1:
                        log.info_t(
                            "bot.log.ilUiDetected",
                            current=consecutive,
                            required=required,
                        )
                        logged = True
                    if consecutive >= required:
                        log.info_t(
                            "bot.log.ilMinigameConfirmed",
                            required=required,
                        )
                        return True
                else:
                    consecutive = 0
                    logged = False
            else:
                consecutive = 0
                logged = False

            time.sleep(0.05)

        return False

    # ══════════════════════════════════════════════════════
    #  双缓冲流水线：截图线程 & 检测线程
    # ══════════════════════════════════════════════════════

    def _capture_worker_fn(self, frame_q: queue.Queue,
                           stop_evt: threading.Event):
        """截图线程：持续截取屏幕并放入帧缓冲区（只保留最新帧）。"""
        _fps_limit = getattr(config, 'CAPTURE_FPS_LIMIT', 0)
        _min_interval = (1.0 / _fps_limit) if _fps_limit > 0 else 0.0
        _last_cap = 0.0
        while not stop_evt.is_set():
            if _min_interval > 0:
                _now = time.monotonic()
                _elapsed = _now - _last_cap
                if _elapsed < _min_interval:
                    time.sleep(_min_interval - _elapsed)
                _last_cap = time.monotonic()
            try:
                raw = self._grab()
                scr = (self._rotate_for_detection(raw)
                       if self._need_rotation else raw)
                try:
                    frame_q.put_nowait((raw, scr))
                except queue.Full:
                    try:
                        frame_q.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        frame_q.put_nowait((raw, scr))
                    except queue.Full:
                        pass
            except Exception:
                pass

    @staticmethod
    def _wait_hook_sleep_interval() -> float:
        """等待提竿阶段的节流间隔。"""
        return 0.0 if getattr(config, "FULL_RATE_WAIT_HOOK", False) else 0.05

    def _detect_worker_fn(self, frame_q: queue.Queue,
                          result_q: queue.Queue,
                          stop_evt: threading.Event,
                          shared_params: dict,
                          params_lock: threading.Lock,
                          use_yolo: bool):
        """检测线程：委托给独立检测服务。"""
        self.minigame_detection.detect_worker_loop(
            frame_q, result_q, stop_evt, shared_params, params_lock, use_yolo
        )

    def _detect_frame_once(self, scr, use_yolo: bool,
                           search_region, bar_search_region,
                           locked_fish_key, locked_fish_scales,
                           locked_bar_scales, frame_no: int,
                           yolo_roi, skip_success: bool,
                           track_cache=None):
        """同步模式单帧检测，委托给独立检测服务。"""
        return self.minigame_detection.detect_once(
            scr, use_yolo,
            search_region, bar_search_region,
            locked_fish_key, locked_fish_scales,
            locked_bar_scales, frame_no,
            yolo_roi, skip_success,
            track_cache=track_cache,
        )

    def _wait_for_minigame_entry(self, start_in_minigame: bool,
                                 use_yolo: bool):
        """等待提竿或提前进入小游戏。"""
        entered_early = start_in_minigame
        if config.IL_RECORD or start_in_minigame:
            return self.running, entered_early

        wait_s = config.BITE_FORCE_HOOK
        log.info_t("bot.log.waitHook", seconds=wait_s)
        wait_t0 = time.time()
        wait_sleep = self._wait_hook_sleep_interval()
        while self.running:
            wait_elapsed = time.time() - wait_t0
            if wait_elapsed >= wait_s:
                log.info_t("bot.log.autoHook", elapsed=wait_elapsed)
                break
            try:
                wait_screen = self._grab()
                self._tick_fps()
                pre_fish, pre_bar = None, None
                pre_progress = None
                if use_yolo and self.yolo is not None:
                    ydet = self.yolo.detect(
                        wait_screen, roi=config.DETECT_ROI or self._auto_roi
                    )
                    pre_fish = ydet.get("fish")
                    pre_bar = ydet.get("bar")
                    pre_progress = ydet.get("progress")
                self._show_debug_overlay(
                    wait_screen, pre_fish, pre_bar,
                    progress=pre_progress if use_yolo else None,
                    status_text=f"⏳ 等待提竿 ({wait_elapsed:.0f}/{wait_s:.0f}s)"
                )

                if pre_fish is not None:
                    ui_found = bool(pre_bar is not None or pre_progress is not None)
                    if not ui_found:
                        try:
                            ui_found = self._detect_ui_once(wait_screen)
                        except Exception:
                            ui_found = False
                    if ui_found:
                        entered_early = True
                        self.state = "bot.state.minigame"
                        log.warning_t(
                            "bot.log.earlyMinigame",
                            elapsed=wait_elapsed,
                        )
                        break
            except Exception:
                pass
            if wait_sleep > 0:
                time.sleep(wait_sleep)

        if not self.running:
            return False, entered_early

        if not entered_early:
            if self._hook_fish():
                entered_early = True
            if not self.running:
                return False, entered_early

        return True, entered_early

    def _announce_minigame_start(self, entered_early: bool, use_yolo: bool):
        """统一输出小游戏开始阶段的日志，并准备控制模式。"""
        self.state = "bot.state.minigame"
        if entered_early:
            log.info_t("bot.log.enteredEarly")
        else:
            log.info_t("bot.log.minigameStarted")

        if config.IL_RECORD:
            self.il.start_recording()
            log.info_t("bot.log.ilManualControl")
        elif config.IL_USE_MODEL:
            if self.il.policy is None:
                self.il.load_policy()
            if self.il.policy is not None:
                log.info_t("bot.log.ilUseModel")
            else:
                log.warning_t("bot.log.ilFallbackPd")
        else:
            log.info_t("bot.log.usePd")

        if use_yolo:
            log.info_t("bot.log.useYolo")

        self.detector.debug_report = True
        self.input.move_to_game_center()

    def _build_minigame_runtime(self, entered_minigame_early: bool) -> MinigameRuntime:
        """初始化一局小游戏运行时状态。"""
        return self._get_minigame_session().build_runtime(entered_minigame_early)

    def _build_detection_context(self, use_yolo: bool, skip_success_check: bool):
        """初始化小游戏检测上下文。"""
        return self._get_minigame_session().build_detection_context(
            use_yolo,
            skip_success_check,
        )

    def _initialize_minigame_context(self, ctx: DetectionContext):
        """初始化搜索区域、截图信息与首帧调试输出。"""
        return self._get_minigame_session().initialize_context(ctx)

    def _start_pipeline(self, ctx: DetectionContext) -> PipelineContext:
        """根据当前模式启动同步/异步检测流水线。"""
        return self._get_minigame_session().start_pipeline(ctx)

    def _stop_pipeline(self, pipe: PipelineContext):
        """停止异步检测流水线。"""
        self._get_minigame_session().stop_pipeline(pipe)

    def _get_next_detection_result(self, runtime: MinigameRuntime,
                                   ctx: DetectionContext,
                                   pipe: PipelineContext):
        """获取下一帧检测结果，兼容同步与异步模式。"""
        return self._get_minigame_session().get_next_detection_result(runtime, ctx, pipe)

    def _sync_pipeline_params(self, runtime: MinigameRuntime,
                              ctx: DetectionContext,
                              pipe: PipelineContext):
        """同步检测参数给异步检测线程。"""
        self._get_minigame_session().sync_pipeline_params(runtime, ctx, pipe)

    def _get_fish_display(self):
        return self._get_minigame_session().get_fish_display()

    def _reset_fish_name_state(self, runtime: MinigameRuntime):
        """清理鱼类别稳定器与白名单确认状态。"""
        self._get_minigame_session().reset_fish_name_state(runtime)

    def _stabilize_fish_name(self, detected_name: str,
                             runtime: MinigameRuntime) -> str:
        return self._get_minigame_session().stabilize_fish_name(detected_name, runtime)

    def _should_skip_fish_by_whitelist(self, fish_name: str,
                                       runtime: MinigameRuntime) -> bool:
        """非白名单鱼需要连续命中多帧才真正触发放弃。"""
        return self._get_minigame_session().should_skip_fish_by_whitelist(
            fish_name,
            runtime,
        )

    def _postprocess_minigame_detection(self, screen, screen_raw,
                                        fish, bar, matched_key, bar_scale,
                                        yolo_progress, prog_hook,
                                        runtime: MinigameRuntime,
                                        ctx: DetectionContext):
        """处理一帧检测结果的模板锁定、轨道约束与调试显示。"""
        return self._get_minigame_session().postprocess_detection(
            screen,
            screen_raw,
            fish,
            bar,
            matched_key,
            bar_scale,
            yolo_progress,
            prog_hook,
            runtime,
            ctx,
        )

    def _compute_minigame_progress(self, screen, screen_raw,
                                   fish, bar, yolo_progress, prog_hook,
                                   runtime: MinigameRuntime,
                                   ctx: DetectionContext) -> float:
        """统计当前进度条绿色占比。"""
        return self._get_minigame_session().compute_progress(
            screen,
            screen_raw,
            fish,
            bar,
            yolo_progress,
            prog_hook,
            runtime,
            ctx,
        )

    def _maybe_activate_minigame(self, fish, bar, yolo_progress,
                                 runtime: MinigameRuntime,
                                 ctx: DetectionContext):
        """检查是否正式进入小游戏控制阶段。"""
        return self._get_minigame_session().maybe_activate(
            fish,
            bar,
            yolo_progress,
            runtime,
            ctx,
        )

    def _evaluate_minigame_end_state(self, screen, fish, bar,
                                     runtime: MinigameRuntime,
                                     try_rescue_pd):
        """处理小游戏结束判定。返回 ok/continue/break。"""
        self._ensure_minigame_services()
        return self.minigame_end_judge.evaluate(
            screen, fish, bar, runtime,
            getattr(config, "SKIP_SUCCESS_CHECK", False),
            rescue_fn=try_rescue_pd,
        )

    def _run_minigame_control(self, fish, bar, yolo_progress,
                              runtime: MinigameRuntime,
                              ctx: DetectionContext) -> bool:
        """执行当前帧控制逻辑。"""
        return self._get_minigame_session().run_control(
            fish,
            bar,
            yolo_progress,
            runtime,
            ctx,
        )

    def _log_minigame_frame(self, fish, bar, green,
                            runtime: MinigameRuntime,
                            skip_success_check: bool):
        """输出小游戏周期日志。"""
        self._get_minigame_session().log_frame(
            fish,
            bar,
            green,
            runtime,
            skip_success_check,
        )

    def _try_rescue_pd(self, reason: str, runtime: MinigameRuntime,
                       skip_success_check: bool,
                       attempts: int = 3,
                       interval_s: float = 0.02) -> bool:
        """在结束判定前尝试重新抢回小游戏有效检测。"""
        self._ensure_minigame_services()
        return self.minigame_rescue.try_rescue(
            reason, runtime, skip_success_check, attempts, interval_s
        )

    def _resolve_minigame_result(self, skip_fish: bool,
                                 skip_success_check: bool,
                                 last_green: float) -> bool:
        """根据本局结果解析成功/失败。"""
        self._ensure_minigame_services()
        return self.minigame_reel_exit.resolve_result(
            skip_fish, skip_success_check, last_green
        )

    def _perform_minigame_reel_exit(self, success: bool) -> bool:
        """执行收杆/等待 UI 消失流程。"""
        self._ensure_minigame_services()
        return self.minigame_reel_exit.perform_exit(success)

    def _finalize_minigame(self, hook_timeout_retry: bool,
                           skip_fish: bool,
                           skip_success_check: bool,
                           last_green: float):
        """统一处理小游戏结束后的结算、收杆与返回值。"""
        self._ensure_minigame_services()
        return self.minigame_reel_exit.finalize(
            hook_timeout_retry,
            skip_fish,
            skip_success_check,
            last_green,
        )

    # ══════════════════════════════════════════════════════
    #  第4步: 钓鱼小游戏
    # ══════════════════════════════════════════════════════

    def _fishing_minigame(self, start_in_minigame=False) -> bool:
        """委托给小游戏编排器执行一局小游戏。"""
        self._ensure_minigame_services()
        return self.minigame_runner.run(start_in_minigame)

    # ══════════════════════════════════════════════════════
    #  可视化调试
    # ══════════════════════════════════════════════════════

    def _show_debug_overlay(self, screen, fish=None, bar=None,
                            search_region=None, bar_search_region=None,
                            progress=None, prog_hook=None, status_text=""):
        """转发给独立 debug overlay 管理器。"""
        self.debug_overlay.show(
            screen,
            fish=fish,
            bar=bar,
            search_region=search_region,
            bar_search_region=bar_search_region,
            progress=progress,
            prog_hook=prog_hook,
            status_text=status_text,
            state=self.state,
            running=self.running,
            need_rotation=self._need_rotation,
            track_angle=self._track_angle,
            current_fish_name=self._current_fish_name,
            fish_display=self._get_fish_display(),
            bar_velocity=self.pd.bar_velocity,
        )

    def shutdown_debug_overlay(self):
        """请求 debug 线程自行关闭窗口，避免阻塞 GUI 主线程。"""
        self.debug_overlay.shutdown()

    # ══════════════════════════════════════════════════════
    #  小游戏辅助
    # ══════════════════════════════════════════════════════

    def _init_search_region(self, screen):
        """
        初始化搜索区域，返回 (region, track_center_x, bar_region)。

        ★ 如果玩家设置了 DETECT_ROI (框选区域):
          - 只在 ROI 内搜索轨道/白条
          - ROI 本身作为初始搜索区域
        ★ 无 ROI 时: 交叉验证 (白条+轨道) 定位
        """
        h, w = screen.shape[:2]
        roi = config.DETECT_ROI

        # 验证 ROI 有效性
        if roi:
            rx, ry, rw, rh = roi
            if rx + rw > w or ry + rh > h or rw < 20 or rh < 20:
                log.warning(
                    f"  ► ROI ({rx},{ry},{rw},{rh}) 超出屏幕 "
                    f"({w}x{h}) 或太小, 已忽略"
                )
                roi = None

        # 在 ROI (或全屏) 内搜索白条和轨道
        bar = self.detector.find_multiscale(
            screen, "bar", config.THRESH_BAR,
            scales=config.BAR_SCALES,
            search_region=roi,
        )
        track = self.detector.find_multiscale(
            screen, "track", config.THRESH_TRACK,
            search_region=roi,
        )

        bar_cx = (bar[0] + bar[2] // 2) if bar else None
        track_cx = (track[0] + track[2] // 2) if track else None

        chosen_cx = None

        if bar_cx is not None and track_cx is not None:
            if abs(bar_cx - track_cx) < 150:
                chosen_cx = bar_cx
                log.info(
                    f"  ► 轨道+白条一致: 轨道X={track_cx}(conf={track[4]:.2f}) "
                    f"白条X={bar_cx}(conf={bar[4]:.2f}) → 采用白条X"
                )
            else:
                chosen_cx = bar_cx
                log.warning(
                    f"  ► 轨道X={track_cx}(conf={track[4]:.2f}) "
                    f"白条X={bar_cx}(conf={bar[4]:.2f}) 不一致, "
                    f"以白条为准"
                )
        elif bar_cx is not None:
            chosen_cx = bar_cx
            log.info_t("bot.log.onlyBarDetected", x=bar_cx, conf=bar[4])
        elif track_cx is not None:
            chosen_cx = track_cx
            log.info_t("bot.log.onlyTrackDetected", x=track_cx, conf=track[4])

        # ── 有 ROI → 直接用 ROI 作为搜索区域 ──
        if roi:
            roi_t = tuple(roi)
            if chosen_cx is None:
                chosen_cx = roi[0] + roi[2] // 2
                log.info_t("bot.log.useRoiCenter", x=chosen_cx)
            log.info_t(
                "bot.log.useSelectedRoi",
                x=roi[0],
                y=roi[1],
                w=roi[2],
                h=roi[3],
            )
            return roi_t, chosen_cx, roi_t

        # ── 无 ROI → 基于检测结果构建区域 ──
        if chosen_cx is not None:
            y_start = h // 3
            bar_half = max(config.REGION_X, 60)
            bsx = max(0, chosen_cx - bar_half)
            bsw = min(bar_half * 2, w - bsx)
            bar_region = (bsx, y_start, bsw, h - y_start)
            fish_half = max(config.REGION_X * 2, 120)
            fsx = max(0, chosen_cx - fish_half)
            fsw = min(fish_half * 2, w - fsx)
            fish_region = (fsx, y_start, fsw, h - y_start)
            return fish_region, chosen_cx, bar_region

        sw = int(w * 0.6)
        y_start = h // 2
        log.info_t("bot.log.useFallbackRegion")
        fallback = (0, y_start, sw, h - y_start)
        return fallback, None, fallback

    _progress_debug_saved = False

    def _check_progress(self, screen, fish, sr):
        """
        检测进度条（绿色部分）。
        优先使用鱼钩模板估算进度，失败时回退到绿色窄条检测。
        """
        if sr is None:
            return 0.0

        hook_ratio, hook_box = self.detector.estimate_progress_by_hook(screen, sr)
        if hook_box is not None:
            if not self._progress_debug_saved and hook_ratio > 0:
                self._progress_debug_saved = True
                pad = 24
                hx, hy, hw, hh = hook_box[:4]
                dx = max(0, hx - pad)
                dy = max(0, sr[1] - pad)
                dw = min(max(hw + pad * 2, sr[2] + pad * 2), screen.shape[1] - dx)
                dh = min(sr[3] + pad * 2, screen.shape[0] - dy)
                dbg = screen[dy:dy + dh, dx:dx + dw].copy()
                cv2.rectangle(
                    dbg,
                    (hx - dx, hy - dy),
                    (hx - dx + hw, hy - dy + hh),
                    (255, 255, 255),
                    1,
                )
                cv2.putText(
                    dbg,
                    f"hook={hook_ratio:.0%}",
                    (2, 16),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.4,
                    (0, 255, 255),
                    1,
                )
                debug_dir = os.path.join(config.BASE_DIR, "debug")
                os.makedirs(debug_dir, exist_ok=True)
                cv2.imwrite(os.path.join(debug_dir, "progress_hook.png"), dbg)
            return hook_ratio

        bar_cx = self._bar_locked_cx
        if bar_cx is None:
            if fish is not None:
                bar_cx = fish[0]
            else:
                bar_cx = sr[0] + sr[2] // 3

        strip_w = 5
        sx = max(0, bar_cx - strip_w - 8)
        sy = sr[1]
        sw = strip_w
        sh = sr[3]
        if sx + sw > screen.shape[1]:
            sw = screen.shape[1] - sx
        if sy + sh > screen.shape[0]:
            sh = screen.shape[0] - sy
        if sw <= 0 or sh <= 0:
            return 0.0

        ratio = self.detector.detect_green_ratio(
            screen, (sx, sy, sw, sh))

        if not self._progress_debug_saved and ratio > 0:
            self._progress_debug_saved = True
            pad = 30
            dx = max(0, sx - pad)
            dw = min(sw + pad * 2, screen.shape[1] - dx)
            dbg = screen[sy:sy + sh, dx:dx + dw].copy()
            cv2.rectangle(dbg, (sx - dx, 0), (sx - dx + sw, sh),
                          (0, 255, 0), 1)
            info = f"green={ratio:.0%} w={strip_w}"
            cv2.putText(dbg, info, (2, 16),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)
            debug_dir = os.path.join(config.BASE_DIR, "debug")
            os.makedirs(debug_dir, exist_ok=True)
            cv2.imwrite(
                os.path.join(debug_dir, "progress_strip.png"), dbg)

        return ratio

    # ══════════════════════════════════════════════════════
    #  行为克隆: 录制 / 推理
    # ══════════════════════════════════════════════════════

    def _load_il_policy(self):
        """兼容旧调用，委托给 IL 适配器。"""
        self.il.load_policy()

    def _il_start_recording(self):
        """兼容旧调用，委托给 IL 适配器。"""
        self.il.start_recording()

    def _il_stop_recording(self):
        """兼容旧调用，委托给 IL 适配器。"""
        self.il.stop_recording()

    @staticmethod
    def _is_mouse_pressed() -> bool:
        """兼容旧调用。"""
        return ILAdapter.is_mouse_pressed()

    def _il_build_features(self, fish, bar):
        """兼容旧调用，委托给 IL 适配器。"""
        return self.il.build_features(fish, bar)

    def _il_record_frame(self, frame_idx, fish, bar):
        """兼容旧调用，委托给 IL 适配器。"""
        self.il.record_frame(frame_idx, fish, bar)

    def _il_model_control(self, fish, bar) -> bool:
        """兼容旧调用，委托给 IL 适配器。"""
        return self.il.model_control(fish, bar)

    def _control_mouse(self, fish, bar, sr) -> bool:
        """委托给 PD 控制器计算动作，再由 bot 执行输入副作用。"""
        self._ensure_minigame_services()
        action = self.pd.decide(
            fish, bar, sr, self._current_fish_name, config.DETECT_ROI
        )
        return self.control_executor.execute(action)

    # ══════════════════════════════════════════════════════
    #  主循环 (在后台线程中运行)
    # ══════════════════════════════════════════════════════

    def run(self):
        """主钓鱼循环 — 由 GUI 在后台线程启动"""
        log.info_t("bot.log.threadStarted")

        while self.running:
            try:
                force_minigame = self._consume_minigame_preempt()
                if config.IL_RECORD:
                    # ★ 录制模式: 用户手动操作, 程序等待小游戏UI出现
                    self.state = "bot.state.recordWaitMinigame"
                    log.info_t("bot.log.recordWait")
                    if not self._wait_for_minigame_ui():
                        break
                elif not force_minigame:
                    force_minigame = self._cast_rod() or self._consume_minigame_preempt()
                    if not self.running:
                        break

                if not self.running:
                    break

                result = self._fishing_minigame(start_in_minigame=force_minigame)

                if result is None:
                    self.state = "bot.state.waitRecast"
                    self._wait_with_minigame_preempt(
                        config.POST_CATCH_DELAY, "⏳ 等待重抛")
                    log.info_t("bot.log.separator")
                    continue

                self.fish_count += 1
                fish_key = self._current_fish_name or "fish_generic"
                entry = self.fish_stats.setdefault(fish_key, {"success": 0, "fail": 0})
                entry["success" if result else "fail"] += 1
                tag = t("bot.result.success") if result else t("bot.result.complete")
                log.info_t("bot.log.result", count=self.fish_count, tag=tag)
                log.info_t("bot.log.separator")

                self.state = "bot.state.waitNextRound"
                self._wait_with_minigame_preempt(
                    config.POST_CATCH_DELAY, "⏳ 等待下一轮")
            except Exception as e:
                log.error_t("bot.log.runException", error=e)
                if not config.IL_RECORD:
                    self.input.safe_release()
                self._wait_with_minigame_preempt(2.0, "⚠ 异常恢复等待")

        if not config.IL_RECORD:
            self.input.safe_release()
            # SendInput 模式下光标被脚本停在游戏窗口中心, 退出时还原
            self.input.restore_cursor()
        self.state = "bot.state.stopped"
        log.info_t("bot.log.threadStopped")
        self.shutdown_debug_overlay()
