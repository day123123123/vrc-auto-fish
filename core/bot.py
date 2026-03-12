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
from core.minigame_runtime import DetectionContext, MinigameRuntime, PipelineContext
from core.pd_controller import PDController
from utils.logger import log

_yolo_detector = None
_yolo_device_used = None

def _get_yolo_detector(force_reload=False):
    """延迟加载 YOLO 检测器（避免未安装 ultralytics 时报错）"""
    global _yolo_detector, _yolo_device_used
    if force_reload:
        _yolo_detector = None
    if _yolo_detector is None or _yolo_device_used != config.YOLO_DEVICE:
        from core.yolo_detector import YoloDetector
        _yolo_detector = YoloDetector(config.YOLO_MODEL, conf=config.YOLO_CONF)
        _yolo_device_used = config.YOLO_DEVICE
    return _yolo_detector


class FishingBot:
    """VRChat 自动钓鱼机器人"""

    # 鱼模板 → 中文名 + 调试框颜色 (BGR)
    FISH_DISPLAY = {
        "fish_generic": ("通用鱼", (180, 180, 180)),
        "fish_black":   ("黑鱼",  (80, 80, 80)),
        "fish_white":   ("白鱼",  (255, 255, 255)),
        "fish_copper":  ("铜鱼",  (50, 127, 180)),
        "fish_green":   ("绿鱼",  (0, 255, 0)),
        "fish_blue":    ("蓝鱼",  (255, 150, 0)),
        "fish_purple":  ("紫鱼",  (200, 50, 200)),
        "fish_golden":  ("金鱼",  (0, 215, 255)),
        "fish_pink":    ("粉鱼",  (180, 105, 255)),
        "fish_red":     ("红鱼",  (0, 0, 255)),
        "fish_rainbow": ("彩鱼",  (0, 255, 255)),
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
                log.warning(f"[YOLO] 启动加载失败: {e}")

        # ── 共享状态（GUI 读取）──
        self.running    = False
        self.debug_mode = False
        self.fish_count = 0
        self.state      = "就绪"

        # ── PD 控制器 ──
        self.pd = PDController()
        self.minigame_detection = MinigameDetectionService(
            self.detector,
            self.pd,
            lambda: self.yolo,
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

    def _ensure_minigame_services(self):
        """惰性初始化小游戏编排相关服务，兼容测试里的 __new__ 假对象。"""
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
            log.warning(f"[⚠ 抢占] {reason}，立即切入 PD 控制")

    def _consume_minigame_preempt(self) -> bool:
        """读取并清空小游戏抢占标记。"""
        flag = self._force_minigame
        self._force_minigame = False
        return flag

    def _cast_rod(self):
        self.state = "抛竿中"
        if config.IL_RECORD:
            log.info("[🎣 抛竿] 录制模式 — 请手动抛竿 (点击鼠标)")
        else:
            self.input.click()
            if self._wait_with_minigame_preempt(0.15, "🎣 抛竿后摇杆等待"):
                return True
            mode = getattr(config, "ANTI_STUCK_MODE", "jump")
            if mode == "jump":
                log.info("[🎣 抛竿] 抛竿 → 跳跃防卡杆")
                self.input.jump_toggle()
            else:
                log.info("[🎣 抛竿] 抛竿 → 摇头防卡杆")
                self.input.shake_head()
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
            except Exception:
                pass

        bar = self.detector.find_multiscale(
            screen, "bar", config.THRESH_BAR,
            scales=config.BAR_SCALES, search_region=_roi)
        if bar:
            if return_bbox:
                bbox = (bar[0], bar[1], bar[0] + bar[2], bar[1] + bar[3])
                return True, bbox
            return True
        if return_bbox:
            return False, None
        return False

    def _wait_until_ui_gone(self, timeout=3.0, clear_frames=2):
        """收杆后等待上一轮小游戏 UI 消失，避免串到下一轮。"""
        self.state = "等待UI消失"
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
        if config.USE_YOLO and self.yolo is None:
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
        self.state = "提竿"
        if config.IL_RECORD:
            log.info("[🪝 提竿] 录制模式 — 请手动提竿 (点击鼠标)")
        else:
            log.info("[🪝 提竿] 点击鼠标提竿!")
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
                        log.info(f"[IL] 检测到UI元素 ({consecutive}/{required})...")
                        logged = True
                    if consecutive >= required:
                        log.info(
                            f"[IL] 小游戏确认! (连续{required}帧检测到白条+轨道)"
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
        log.info(f"[⏳ 等待] 等待 {wait_s:.0f}s 后提竿, 同时运行检测...")
        wait_t0 = time.time()
        while self.running:
            wait_elapsed = time.time() - wait_t0
            if wait_elapsed >= wait_s:
                log.info(f"[🪝 提竿] 等待 {wait_elapsed:.1f}s 完毕, 自动提竿")
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
                        self.state = "小游戏进行中"
                        log.warning(
                            f"[⚠ 提前进入] 设定提竿时间前 {wait_elapsed:.1f}s "
                            f"已检测到鱼/小游戏UI，判定已提前拉杆，"
                            f"直接切入小游戏控制阶段"
                        )
                        break
            except Exception:
                pass
            time.sleep(0.05)

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
        self.state = "小游戏进行中"
        if entered_early:
            log.info("[🐟 钓鱼] 检测到已提前进入小游戏，开始接管控制")
        else:
            log.info("[🐟 钓鱼] 小游戏开始")

        if config.IL_RECORD:
            self.il.start_recording()
            log.info("[IL] 录制模式: 请手动操作鼠标控制白条!")
        elif config.IL_USE_MODEL:
            if self.il.policy is None:
                self.il.load_policy()
            if self.il.policy is not None:
                log.info("[IL] ★ 本局使用行为克隆模型控制 ★")
            else:
                log.warning("[IL] 模型加载失败, 回退到 PD 控制器")
        else:
            log.info("[PD] 本局使用 PD 控制器")

        if use_yolo:
            log.info("[YOLO] 使用 YOLO 目标检测")

        self.detector.debug_report = True
        self.input.move_to_game_center()

    def _build_minigame_runtime(self, entered_minigame_early: bool) -> MinigameRuntime:
        """初始化一局小游戏运行时状态。"""
        return MinigameRuntime(game_active=entered_minigame_early)

    def _build_detection_context(self, use_yolo: bool, skip_success_check: bool):
        """初始化小游戏检测上下文。"""
        return DetectionContext(
            use_yolo=use_yolo,
            skip_success_check=skip_success_check,
            bar_x_half=config.REGION_X,
            fish_x_half=max(config.REGION_X * 2, 80),
        )

    def _initialize_minigame_context(self, ctx: DetectionContext):
        """初始化搜索区域、截图信息与首帧调试输出。"""
        self.pd.reset()
        self._bar_smooth_cy = None
        self._bar_locked_cx = None
        self._progress_debug_saved = False

        screen_orig = self._grab()
        self.screen.save_debug(screen_orig, "minigame_start")
        h_orig, w_orig = screen_orig.shape[:2]
        log.info(f"  截图尺寸: {w_orig}×{h_orig}")
        self._show_debug_overlay(screen_orig, status_text="🐟 小游戏初始化...")

        if self._need_rotation:
            log.info(
                f"  ► 轨道倾斜 {self._track_angle:.1f}°, "
                f"启用旋转补偿 (旋转 {-self._track_angle:.1f}°)"
            )
            screen = self._rotate_for_detection(screen_orig)
        else:
            screen = screen_orig

        ctx.height, ctx.width = screen.shape[:2]
        if ctx.use_yolo:
            ctx.search_region = None
            ctx.bar_search_region = None
            ctx.regions_locked = True
            if config.DETECT_ROI:
                log.info(
                    f"  [YOLO] 使用手动 ROI: "
                    f"X={config.DETECT_ROI[0]} Y={config.DETECT_ROI[1]} "
                    f"{config.DETECT_ROI[2]}x{config.DETECT_ROI[3]}"
                )
            elif self._auto_roi:
                log.info(
                    f"  [YOLO] 使用自动 ROI: "
                    f"X={self._auto_roi[0]} Y={self._auto_roi[1]} "
                    f"{self._auto_roi[2]}x{self._auto_roi[3]}"
                )
            else:
                log.info("  [YOLO] 全屏检测")
        else:
            ctx.search_region, track_cx, ctx.bar_search_region = self._init_search_region(screen)
            ctx.regions_locked = False
            if track_cx is not None:
                self._bar_locked_cx = track_cx
                log.info(f"  ★ 轨道X轴预锁定: X={track_cx}")
            if ctx.search_region:
                srx, sry, srw, srh = ctx.search_region
                log.info(f"  初始鱼搜索: X={srx}~{srx+srw} Y={sry}~{sry+srh}")
            if ctx.bar_search_region:
                bsx, bsy, bsw, bsh = ctx.bar_search_region
                log.info(
                    f"  初始白条搜索: X={bsx}~{bsx+bsw} "
                    f"Y={bsy}~{bsy+bsh} (下半屏)"
                )

        return screen_orig, screen

    def _start_pipeline(self, ctx: DetectionContext) -> PipelineContext:
        """根据当前模式启动同步/异步检测流水线。"""
        sync_pd_mode = (
            getattr(config, "SYNC_PD_MODE", True)
            and not config.IL_RECORD
            and not config.IL_USE_MODEL
        )
        pipe = PipelineContext(sync_pd_mode=sync_pd_mode)
        if pipe.sync_pd_mode:
            log.info("[模式] 小游戏使用旧版模式（使用旧版参数）")
            return pipe

        pipe.frame_q = queue.Queue(maxsize=1)
        pipe.result_q = queue.Queue(maxsize=1)
        pipe.stop_evt = threading.Event()
        pipe.shared_params = {
            "search_region": ctx.search_region,
            "bar_search_region": ctx.bar_search_region,
            "locked_fish_key": ctx.locked_fish_key,
            "locked_fish_scales": ctx.locked_fish_scales,
            "locked_bar_scales": ctx.locked_bar_scales,
            "frame": 0,
            "yolo_roi": config.DETECT_ROI or self._auto_roi,
            "skip_success": ctx.skip_success_check,
        }
        pipe.params_lock = threading.Lock()
        pipe.capture_thread = threading.Thread(
            target=self._capture_worker_fn,
            args=(pipe.frame_q, pipe.stop_evt),
            daemon=True, name="FishCapture"
        )
        pipe.detect_thread = threading.Thread(
            target=self._detect_worker_fn,
            args=(
                pipe.frame_q, pipe.result_q, pipe.stop_evt,
                pipe.shared_params, pipe.params_lock, ctx.use_yolo
            ),
            daemon=True, name="FishDetect"
        )
        pipe.capture_thread.start()
        pipe.detect_thread.start()
        log.info("[流水线] 截图线程 & 检测线程已启动 (最新结果模式, 队列=1)")
        return pipe

    def _stop_pipeline(self, pipe: PipelineContext):
        """停止异步检测流水线。"""
        if pipe.sync_pd_mode:
            return
        pipe.stop_evt.set()
        pipe.capture_thread.join(timeout=1.0)
        pipe.detect_thread.join(timeout=1.0)
        log.info("[流水线] 截图线程 & 检测线程已停止")

    def _get_next_detection_result(self, runtime: MinigameRuntime,
                                   ctx: DetectionContext,
                                   pipe: PipelineContext):
        """获取下一帧检测结果，兼容同步与异步模式。"""
        if pipe.sync_pd_mode:
            next_frame = runtime.frame + 1
            screen_raw = self._grab()
            screen = (self._rotate_for_detection(screen_raw)
                      if self._need_rotation else screen_raw)
            self._tick_fps()
            (pipe_fish, pipe_bar, pipe_progress, pipe_hook,
             pipe_mk, pipe_bs, pipe_track,
             ctx.sync_track_cache) = self._detect_frame_once(
                screen, ctx.use_yolo,
                ctx.search_region, ctx.bar_search_region,
                ctx.locked_fish_key, ctx.locked_fish_scales,
                ctx.locked_bar_scales, next_frame,
                config.DETECT_ROI or self._auto_roi,
                ctx.skip_success_check,
                track_cache=ctx.sync_track_cache,
            )
            runtime.frame = next_frame
            return (
                screen_raw, screen, pipe_fish, pipe_bar,
                pipe_progress, pipe_hook, pipe_mk, pipe_bs, pipe_track
            )

        try:
            pipe_data = pipe.result_q.get(timeout=0.5)
        except queue.Empty:
            return None
        while True:
            try:
                pipe_data = pipe.result_q.get_nowait()
            except queue.Empty:
                break
        runtime.frame += 1
        self._tick_fps()
        return pipe_data

    def _sync_pipeline_params(self, runtime: MinigameRuntime,
                              ctx: DetectionContext,
                              pipe: PipelineContext):
        """同步检测参数给异步检测线程。"""
        if pipe.sync_pd_mode:
            return
        with pipe.params_lock:
            pipe.shared_params["search_region"] = ctx.search_region
            pipe.shared_params["bar_search_region"] = ctx.bar_search_region
            pipe.shared_params["locked_fish_key"] = ctx.locked_fish_key
            pipe.shared_params["locked_fish_scales"] = ctx.locked_fish_scales
            pipe.shared_params["locked_bar_scales"] = ctx.locked_bar_scales
            pipe.shared_params["frame"] = runtime.frame

    def _postprocess_minigame_detection(self, screen, screen_raw,
                                        fish, bar, matched_key, bar_scale,
                                        yolo_progress, prog_hook,
                                        runtime: MinigameRuntime,
                                        ctx: DetectionContext):
        """处理一帧检测结果的模板锁定、轨道约束与调试显示。"""
        fish_detect_name = ""

        if ctx.use_yolo:
            if fish is not None:
                # 多分类 YOLO 模型直接输出 fish_name，只有旧模型才回退颜色识别。
                fish_detect_name = matched_key or ""
                if not fish_detect_name:
                    save_debug = not runtime.fish_id_saved
                    color_key = self.detector.identify_fish_type(
                        screen, fish, debug_save=save_debug
                    )
                    if save_debug:
                        runtime.fish_id_saved = True
                    matched_key = color_key
                    fish_detect_name = color_key

            if config.YOLO_COLLECT and runtime.frame % 10 == 0:
                collect_dir = os.path.join(
                    config.BASE_DIR, "yolo", "dataset", "images", "unlabeled"
                )
                os.makedirs(collect_dir, exist_ok=True)
                ts = time.strftime("%Y%m%d_%H%M%S")
                ms = int((time.time() % 1) * 1000)
                cv2.imwrite(os.path.join(collect_dir, f"{ts}_{ms:03d}.png"), screen)
        else:
            if ctx.locked_fish_key:
                if fish is not None:
                    fish_detect_name = ctx.locked_fish_key
                if (fish is None and runtime.fish_lost > 20
                        and runtime.fish_lost % 20 == 0):
                    ctx.locked_fish_key = None
                    ctx.locked_fish_scales = None
                    log.info("  ★ 解除鱼模板锁定, 重新搜索")
            elif fish is not None:
                fish_detect_name = matched_key or "?"
                if matched_key and matched_key != "fish_white":
                    ctx.locked_fish_key = matched_key
                    scale = self.detector._last_best_scale
                    ctx.locked_fish_scales = [
                        round(scale * 0.85, 2), scale, round(scale * 1.15, 2)
                    ]
                    log.info(
                        f"  ★ 锁定鱼模板: {ctx.locked_fish_key} @ scales="
                        f"{[f'{x:.2f}' for x in ctx.locked_fish_scales]}"
                    )

        if fish is not None:
            self._current_fish_name = fish_detect_name
        if not runtime.skip_fish and fish_detect_name:
            wl_key = fish_detect_name
            if not config.FISH_WHITELIST.get(wl_key, True):
                fname_cn = self.FISH_DISPLAY.get(wl_key, (wl_key,))[0]
                log.info(f"[白名单] {fname_cn} 不在白名单中, 放弃本次钓鱼")
                runtime.skip_fish = True

        if not ctx.use_yolo and bar is not None and not ctx.locked_bar_scales:
            ctx.locked_bar_scales = [
                round(max(0.2, bar_scale * 0.85), 2),
                bar_scale,
                round(bar_scale * 1.15, 2),
            ]
            log.info(
                f"  ★ 锁定白条 @ scales="
                f"{[f'{x:.2f}' for x in ctx.locked_bar_scales]}"
            )

        if bar is not None:
            raw_bcx = bar[0] + bar[2] // 2
            if self._bar_locked_cx is None:
                self._bar_locked_cx = raw_bcx
                log.info(f"  ★ 轨道X轴锁定(白条): X={raw_bcx}")
            elif abs(raw_bcx - self._bar_locked_cx) > ctx.bar_x_half:
                bar = None

            if bar is not None:
                raw_bar_cy = bar[1] + bar[3] // 2
                if self._bar_smooth_cy is None:
                    self._bar_smooth_cy = float(raw_bar_cy)
                else:
                    max_jump = max(12.0, bar[3] * 0.60)
                    delta = raw_bar_cy - self._bar_smooth_cy
                    if delta > max_jump:
                        raw_bar_cy = int(self._bar_smooth_cy + max_jump)
                    elif delta < -max_jump:
                        raw_bar_cy = int(self._bar_smooth_cy - max_jump)
                    self._bar_smooth_cy = (
                        0.45 * raw_bar_cy + 0.55 * self._bar_smooth_cy
                    )
                smooth_bar_cy = int(round(self._bar_smooth_cy))
                bar = (
                    self._bar_locked_cx - bar[2] // 2,
                    smooth_bar_cy - bar[3] // 2,
                    bar[2], bar[3], bar[4],
                )
            else:
                self._bar_smooth_cy = None

        if bar is not None and not ctx.regions_locked:
            bar_cy = bar[1] + bar[3] // 2
            tcx = self._bar_locked_cx or (bar[0] + bar[2] // 2)
            y_top = max(0, bar_cy - config.REGION_UP)
            y_bot = min(ctx.height, bar_cy + config.REGION_DOWN)
            roi = config.DETECT_ROI
            if roi:
                y_top = max(y_top, roi[1])
                y_bot = min(y_bot, roi[1] + roi[3])
            rh = y_bot - y_top

            fish_half = max(config.REGION_X * 2, 80)
            fsx = max(0, tcx - fish_half)
            fsw = min(fish_half * 2, ctx.width - fsx)
            if roi:
                fsx = max(fsx, roi[0])
                fsw = min(fsw, roi[0] + roi[2] - fsx)
            ctx.search_region = (fsx, y_top, fsw, rh)

            bar_half = config.REGION_X
            bsx = max(0, tcx - bar_half)
            bsw = min(bar_half * 2, ctx.width - bsx)
            if roi:
                bsx = max(bsx, roi[0])
                bsw = min(bsw, roi[0] + roi[2] - bsx)
            ctx.bar_search_region = (bsx, y_top, bsw, rh)
            ctx.regions_locked = True
            log.info(
                f"  ★ 搜索区域锁定(白条Y={bar_cy}): "
                f"Y={y_top}~{y_bot} "
                f"鱼X=±{fish_half} 条X=±{bar_half}"
                f"{' (ROI裁剪)' if roi else ''}"
            )

        if fish is not None:
            raw_fcx = fish[0] + fish[2] // 2
            if (self._bar_locked_cx is not None
                    and abs(raw_fcx - self._bar_locked_cx) > ctx.fish_x_half):
                fish = None
                self._current_fish_name = ""
            if fish is not None and self._bar_locked_cx is not None:
                fish = (
                    self._bar_locked_cx - fish[2] // 2,
                    fish[1], fish[2], fish[3], fish[4],
                )

        if fish is not None and bar is not None:
            fish_cy_check = fish[1] + fish[3] // 2
            bar_cy_check = bar[1] + bar[3] // 2
            dist_y = abs(fish_cy_check - bar_cy_check)
            if dist_y > config.MAX_FISH_BAR_DIST:
                if runtime.frame % 30 == 1:
                    log.warning(
                        f"[⚠ 误检] 鱼Y={fish_cy_check} 条Y={bar_cy_check} "
                        f"距离={dist_y}px > {config.MAX_FISH_BAR_DIST}px"
                    )
                fish = None
                bar = None

        display_sr = ctx.search_region or self._auto_roi
        if not self._need_rotation:
            self._show_debug_overlay(
                screen_raw, fish, bar, display_sr,
                bar_search_region=ctx.bar_search_region,
                progress=None if ctx.skip_success_check else yolo_progress,
                prog_hook=prog_hook,
                status_text=f"🐟 小游戏 F{runtime.frame:04d}"
            )
        else:
            self._show_debug_overlay(
                screen_raw,
                search_region=display_sr,
                bar_search_region=ctx.bar_search_region,
                progress=None if ctx.skip_success_check else yolo_progress,
                prog_hook=prog_hook,
                status_text=(
                    f"🐟 小游戏 F{runtime.frame:04d} "
                    f"(旋转{self._track_angle:.0f}°补偿中)"
                )
            )

        return fish, bar, yolo_progress

    def _compute_minigame_progress(self, screen, screen_raw,
                                   fish, bar, yolo_progress, prog_hook,
                                   runtime: MinigameRuntime,
                                   ctx: DetectionContext) -> float:
        """统计当前进度条绿色占比。"""
        green = 0.0
        if ctx.skip_success_check or runtime.frame <= runtime.progress_skip_frames:
            return green

        if ctx.use_yolo and yolo_progress is not None:
            px, py, pw, ph = yolo_progress[:4]
            green, hook_box, hook_source = self.detector.estimate_progress_in_box(
                screen, yolo_progress
            )
            if hook_box is not None:
                prog_hook = hook_box
                if runtime.frame % 10 == 0:
                    hx, hy, hw, hh, hconf = hook_box
                    log.info(
                        f"[Hook] F{runtime.frame:04d} progress=({px},{py},{pw},{ph}) "
                        f"{hook_source}=({hx},{hy},{hw},{hh}) conf={hconf:.2f} "
                        f"ratio={green:.0%}"
                    )
            else:
                pad_x = max(1, int(pw * 0.08))
                pad_y = max(1, int(ph * 0.05))
                sx = px + pad_x
                sy = py + pad_y
                sw = max(1, pw - pad_x * 2)
                sh = max(1, ph - pad_y * 2)
                green = self.detector.detect_green_ratio(screen, (sx, sy, sw, sh))
                if not self._progress_debug_saved and green > 0:
                    self._progress_debug_saved = True
                    pad = 20
                    dx = max(0, px - pad)
                    dw = min(pw + pad * 2, ctx.width - dx)
                    dbg = screen[py:py + ph, dx:dx + dw].copy()
                    cv2.rectangle(
                        dbg, (sx - dx, sy - py),
                        (sx - dx + sw, sy - py + sh), (0, 255, 0), 1
                    )
                    info = f"green={green:.0%} roi={sw}x{sh}"
                    cv2.putText(
                        dbg, info, (2, 16),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1
                    )
                    debug_dir = os.path.join(config.BASE_DIR, "debug")
                    os.makedirs(debug_dir, exist_ok=True)
                    cv2.imwrite(os.path.join(debug_dir, "progress_strip.png"), dbg)

            display_sr = ctx.search_region or self._auto_roi
            self._show_debug_overlay(
                screen_raw, fish, bar, display_sr,
                bar_search_region=ctx.bar_search_region,
                progress=yolo_progress,
                prog_hook=prog_hook,
                status_text=f"🐟 小游戏 F{runtime.frame:04d}"
            )
        else:
            progress_sr = ctx.search_region
            if bar is not None:
                bcx = bar[0] + bar[2] // 2
                bcy = bar[1] + bar[3] // 2
                pr_half_x = max(config.REGION_X * 2, 80)
                pr_x = max(0, bcx - pr_half_x)
                pr_y = max(0, bcy - config.REGION_UP)
                pr_w = min(pr_half_x * 2, ctx.width - pr_x)
                pr_h = min(config.REGION_UP + config.REGION_DOWN, ctx.height - pr_y)
                progress_sr = (pr_x, pr_y, pr_w, pr_h)
                runtime.last_progress_sr = progress_sr
            elif runtime.last_progress_sr is not None:
                progress_sr = runtime.last_progress_sr
            green = self._check_progress(screen, fish, progress_sr)

        if (green > 0 and runtime.prev_green > 0.01
                and (green - runtime.prev_green) > 0.30):
            capped_green = min(green, runtime.prev_green + 0.12)
            log.debug(
                f"  进度跳变过大 {runtime.prev_green:.0%}→{green:.0%}，"
                f"限幅到 {capped_green:.0%}"
            )
            green = capped_green

        if green > 0:
            runtime.prev_green = green
        if green > runtime.last_green:
            runtime.last_green = green
        return green

    def _maybe_activate_minigame(self, fish, bar, yolo_progress,
                                 runtime: MinigameRuntime,
                                 ctx: DetectionContext):
        """检查是否正式进入小游戏控制阶段。"""
        if runtime.game_active:
            return "ok"

        det_count = ((fish is not None)
                     + (bar is not None)
                     + (yolo_progress is not None))
        if det_count >= 2:
            runtime.game_active = True
            runtime.had_good_detection = True
            det_names = []
            if fish is not None:
                det_names.append("鱼")
            if bar is not None:
                det_names.append("条")
            if yolo_progress is not None:
                det_names.append("进度条")
            log.info(f"[🐟 开始] 检测到{'+'.join(det_names)}, 小游戏确认! PD控制启动")
            if not config.IL_RECORD:
                press_t = getattr(config, "INITIAL_PRESS_TIME", 0.2)
                self.input.mouse_down()
                time.sleep(press_t)
                self.input.mouse_up()
            return "ok"

        if time.time() - runtime.hook_time > ctx.hook_detect_timeout:
            log.warning(
                f"[⚠ 超时] 提竿后 {ctx.hook_detect_timeout}s 未检测到鱼,"
                f" 判定未进入小游戏, 返回主循环重新抛竿"
            )
            runtime.hook_timeout_retry = True
            return "break"

        return "continue"

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
        backend = getattr(self, "_active_control_backend", None) or self._build_control_backend()
        return backend.control(fish, bar, yolo_progress, runtime, ctx)

    def _log_minigame_frame(self, fish, bar, green,
                            runtime: MinigameRuntime,
                            skip_success_check: bool):
        """输出小游戏周期日志。"""
        if runtime.frame == 50:
            self.detector.debug_report = self.debug_mode
        if runtime.frame % 30 != 0:
            return
        fname = self._current_fish_name.replace("fish_", "") if self._current_fish_name else ""
        fi = f"鱼[{fname}]Y={fish[1]+fish[3]//2}" if fish else "鱼=无"
        bi = f"条Y={bar[1]+bar[3]//2}" if bar else "条=无"
        vel = f"v={self.pd.bar_velocity:+.0f}"
        if skip_success_check:
            log.info(
                f"[F{runtime.frame:04d}] {fi} | {bi} | {vel} | 按住:{runtime.hold_count}"
            )
            return
        log.info(
            f"[F{runtime.frame:04d}] {fi} | {bi} | {vel} | "
            f"按住:{runtime.hold_count} | 进度:{green:.0%}"
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
            fish_display=self.FISH_DISPLAY,
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
            log.info(f"  ► 仅检测到白条 @ X={bar_cx} conf={bar[4]:.2f}")
        elif track_cx is not None:
            chosen_cx = track_cx
            log.info(f"  ► 仅检测到轨道 @ X={track_cx} conf={track[4]:.2f}")

        # ── 有 ROI → 直接用 ROI 作为搜索区域 ──
        if roi:
            roi_t = tuple(roi)
            if chosen_cx is None:
                chosen_cx = roi[0] + roi[2] // 2
                log.info(f"  ► ROI内未找到轨道/白条, 使用ROI中心 X={chosen_cx}")
            log.info(
                f"  ★ 使用框选区域: X={roi[0]} Y={roi[1]} "
                f"{roi[2]}x{roi[3]}"
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
        log.info("  ► 未找到轨道和白条, 使用左侧下半区域")
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
            import os
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
        log.info("钓鱼线程已启动")

        while self.running:
            try:
                force_minigame = self._consume_minigame_preempt()
                if config.IL_RECORD:
                    # ★ 录制模式: 用户手动操作, 程序等待小游戏UI出现
                    self.state = "录制: 等待小游戏"
                    log.info("[IL] 请手动抛竿→等待→提竿, 程序在等待小游戏出现...")
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
                    self.state = "等待重抛"
                    self._wait_with_minigame_preempt(
                        config.POST_CATCH_DELAY, "⏳ 等待重抛")
                    log.info("─" * 40)
                    continue

                self.fish_count += 1
                tag = "成功 ✅" if result else "完成"
                log.info(f"[🎣 结果] 第 {self.fish_count} 次钓鱼 — {tag}")
                log.info("─" * 40)

                self.state = "等待下一轮"
                self._wait_with_minigame_preempt(
                    config.POST_CATCH_DELAY, "⏳ 等待下一轮")
            except Exception as e:
                log.error(f"运行异常: {e}")
                if not config.IL_RECORD:
                    self.input.safe_release()
                self._wait_with_minigame_preempt(2.0, "⚠ 异常恢复等待")

        if not config.IL_RECORD:
            self.input.safe_release()
        self.state = "已停止"
        log.info("钓鱼线程已停止")
        self.shutdown_debug_overlay()
