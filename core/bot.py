"""
钓鱼机器人主逻辑
================
状态机: IDLE → CASTING → WAITING → HOOKING → FISHING → (循环)

设计为可在后台线程运行，通过共享属性与 GUI 通信。
"""

import time
import cv2
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import config
from core.window import WindowManager
from core.screen import ScreenCapture
from core.detector import ImageDetector
from core.input_ctrl import InputController
from utils.logger import log

import ctypes
import csv
from collections import deque

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

        # ── PD 控制器状态 ──
        self._bar_prev_cy   = None       # 上一帧白条中心 Y
        self._bar_prev_time = None       # 上一帧时间戳
        self._bar_velocity  = 0.0        # 白条速度估算 (px/s, 正=下, 负=上)
        self._last_hold     = None       # 上一帧 hold 时长 (后备用)
        self._last_fish_cy  = None       # 上一次鱼的中心 Y (后备用)

        # ── Debug overlay (独立线程, 不阻塞钓鱼逻辑) ──
        self._last_overlay_time = 0
        self._fps = 0.0
        self._frame_times = []
        self._debug_frame = None         # 最新待显示的帧
        self._debug_lock = threading.Lock()
        self._debug_thread = None

        # ── 旋转补偿状态 ──
        self._track_angle   = 0.0        # 轨道偏转角度 (度)
        self._need_rotation = False      # 是否需要旋转补偿

        # ── 自动 ROI (未手动框选时, 从验证阶段自动推断) ──
        self._auto_roi = None

        # ── 鱼/白条位置平滑 (减少检测抖动) ──
        self._fish_smooth_cy = None      # 平滑后的鱼中心 Y
        self._current_fish_name = ""     # 当前检测到的鱼模板名 (如 "fish_blue")
        self._bar_locked_cx  = None      # ★ 轨道X轴锁定 (白条+鱼共用)
        self._pool = ThreadPoolExecutor(max_workers=2)

        # ── 行为克隆 ──
        self._il_history = deque(maxlen=config.IL_HISTORY_LEN)
        self._il_writer = None       # CSV writer (录制模式)
        self._il_file = None         # CSV file handle
        self._il_prev_fish_cy = None # 上一帧鱼Y (计算鱼位移)
        self._il_mouse_prev = 0      # 上一帧鼠标状态
        self._il_log_counter = 0     # 日志节流计数
        self._il_policy = None       # 训练好的模型
        self._il_device = "cpu"
        self._il_norm_mean = None    # 特征归一化均值
        self._il_norm_std = None     # 特征归一化标准差
        if config.IL_USE_MODEL:
            self._load_il_policy()

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

    def _cast_rod(self):
        self.state = "抛竿中"
        if config.IL_RECORD:
            log.info("[🎣 抛竿] 录制模式 — 请手动抛竿 (点击鼠标)")
        else:
            self.input.click()
            time.sleep(0.15)
            mode = getattr(config, "ANTI_STUCK_MODE", "shake")
            if mode == "jump":
                log.info("[🎣 抛竿] 抛竿 → 跳跃防卡杆")
                self.input.jump_toggle()
            else:
                log.info("[🎣 抛竿] 抛竿 → 摇头防卡杆")
                self.input.shake_head()
        # ★ 从抛竿开始就显示 debug 窗口
        try:
            screen = self._grab()
            self._show_debug_overlay(screen, status_text="🎣 抛竿中...")
        except Exception:
            pass
        time.sleep(config.CAST_DELAY)

    # ══════════════════════════════════════════════════════
    #  第2步: 等待咬钩
    # ══════════════════════════════════════════════════════

    def _detect_ui_once(self, screen, return_bbox=False):
        """单帧检测: 白条+轨道是否同时出现（YOLO优先，模板兜底）。
        return_bbox=True 时返回 (found, (min_x, min_y, max_x, max_y))"""
        _roi = config.DETECT_ROI
        _use_yolo = config.USE_YOLO and self.yolo is not None
        bbox = None

        if _use_yolo:
            try:
                det = self.yolo.detect(screen, _roi)
                if det.get("bar") and det.get("track"):
                    yb, yt = det["bar"], det["track"]
                    if abs((yb[0]+yb[2]//2) - (yt[0]+yt[2]//2)) < 150:
                        if return_bbox:
                            bbox = (min(yb[0], yt[0]), min(yb[1], yt[1]),
                                    max(yb[0]+yb[2], yt[0]+yt[2]),
                                    max(yb[1]+yb[3], yt[1]+yt[3]))
                            return True, bbox
                        return True
            except Exception:
                pass

        bar = self.detector.find_multiscale(
            screen, "bar", config.THRESH_BAR,
            scales=config.BAR_SCALES, search_region=_roi)
        track = self.detector.find_multiscale(
            screen, "track", config.THRESH_TRACK, search_region=_roi)
        if bar and track:
            bar_cx = bar[0] + bar[2] // 2
            track_cx = track[0] + track[2] // 2
            if abs(bar_cx - track_cx) < 150:
                if return_bbox:
                    bbox = (min(bar[0], track[0]), min(bar[1], track[1]),
                            max(bar[0]+bar[2], track[0]+track[2]),
                            max(bar[1]+bar[3], track[1]+track[3]))
                    return True, bbox
                return True
        if return_bbox:
            return False, None
        return False

    def _verify_game_alive(self) -> bool:
        """快速检查小游戏UI是否仍然存在 (5帧, 无延迟)。
        用于游戏结束条件触发后的二次确认。"""
        for i in range(5):
            if not self.running:
                return False
            screen = self._grab()
            self._show_debug_overlay(
                screen,
                status_text=f"🔍 验证小游戏 ({i+1}/5)")
            if self._detect_ui_once(screen):
                return True
            time.sleep(0.03)
        return False

    def _hook_fish(self):
        self.state = "提竿"
        if config.IL_RECORD:
            log.info("[🪝 提竿] 录制模式 — 请手动提竿 (点击鼠标)")
        else:
            log.info("[🪝 提竿] 点击鼠标提竿!")
            time.sleep(config.HOOK_PRE_DELAY)
            self.input.click()
        # ★ 提竿后短暂等待, 持续刷新 debug 窗口
        t0 = time.time()
        while time.time() - t0 < config.HOOK_POST_DELAY:
            try:
                screen = self._grab()
                self._show_debug_overlay(
                    screen, status_text="🪝 提竿! 等待小游戏UI..."
                )
            except Exception:
                pass
            time.sleep(0.05)

    def _verify_minigame(self) -> bool:
        """
        提竿后验证钓鱼小游戏 UI 是否真的出现了。

        延迟 0.5s 等待 UI 渲染，然后用十多帧(~15帧)快速确认。
        累计 N 帧检测到即确认，优先 YOLO，模板兜底。
        确认后自动计算动态 ROI (仅在未手动框选时生效)。
        """
        self.state = "验证小游戏"

        time.sleep(0.5)
        log.info("[🔍 验证] 延迟0.5s后开始检测小游戏UI...")

        t0 = time.time()
        hit_count = 0
        required = config.VERIFY_CONSECUTIVE
        total_frames = 0
        max_frames = 15

        self._track_angle = 0.0
        self._need_rotation = False
        self._auto_roi = None
        last_bbox = None

        while self.running and total_frames < max_frames and (time.time() - t0 < config.VERIFY_TIMEOUT):
            screen = self._grab()
            total_frames += 1

            self._show_debug_overlay(
                screen,
                status_text=f"🔍 验证UI ({hit_count}/{required}) 帧{total_frames}/{max_frames}"
            )

            found, bbox = self._detect_ui_once(screen, return_bbox=True)
            if bbox is not None:
                last_bbox = bbox

            if found:
                hit_count += 1
                if hit_count >= required:
                    self._track_angle = 0.0

                    if last_bbox is not None and config.DETECT_ROI is None:
                        min_x, min_y, max_x, max_y = last_bbox
                        h_scr, w_scr = screen.shape[:2]
                        pad_x, pad_y = 120, 150
                        rx = max(0, min_x - pad_x)
                        ry = max(0, min_y - pad_y)
                        rw = min(w_scr - rx, (max_x - min_x) + pad_x * 2)
                        rh = min(h_scr - ry, (max_y - min_y) + pad_y * 2)
                        self._auto_roi = (rx, ry, rw, rh)
                        log.info(
                            f"[✓ 确认] 检测到UI! "
                            f"(耗时 {time.time()-t0:.1f}s, {total_frames}帧) "
                            f"自动ROI: X={rx} Y={ry} {rw}x{rh}")
                    else:
                        log.info(
                            f"[✓ 确认] 检测到UI! "
                            f"(耗时 {time.time()-t0:.1f}s, {total_frames}帧)")
                    return True

            time.sleep(0.03)

        log.warning(
            f"[✗ 误触] {total_frames}帧内未确认小游戏UI "
            f"(累计命中: {hit_count}/{required})，将重新抛竿")
        return False

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

            time.sleep(0.1)

        return False

    # ══════════════════════════════════════════════════════
    #  第4步: 钓鱼小游戏
    # ══════════════════════════════════════════════════════

    def _fishing_minigame(self) -> bool:
        """返回 True=成功, False=失败/停止, None=验证失败需重试"""

        # ── 行为克隆: 每局重置状态 ──
        self._il_history.clear()
        self._il_prev_fish_cy = None
        self._il_mouse_prev = 0
        self._il_press_streak = 0
        self._il_prev_velocity = 0.0
        self._il_log_counter = 0

        # ★ YOLO 模式 (延迟加载: 首次使用时加载)
        if config.USE_YOLO and self.yolo is None:
            try:
                self.yolo = _get_yolo_detector()
            except Exception as e:
                log.warning(f"[YOLO] 加载失败: {e}，回退到模板匹配")
        _use_yolo = config.USE_YOLO and self.yolo is not None

        # ═══════ Phase 1: 等待期间运行 YOLO 检测 (不控制) ═══════
        if not config.IL_RECORD:
            wait_s = config.BITE_FORCE_HOOK
            log.info(f"[⏳ 等待] 等待 {wait_s:.0f}s 后提竿, 同时运行检测...")
            _wait_t0 = time.time()
            while self.running:
                _wait_elapsed = time.time() - _wait_t0
                if _wait_elapsed >= wait_s:
                    log.info(f"[🪝 提竿] 等待 {_wait_elapsed:.1f}s 完毕, 自动提竿")
                    break
                try:
                    _wait_screen = self._grab()
                    _pre_fish, _pre_bar = None, None
                    if _use_yolo:
                        _yd = self.yolo.detect(
                            _wait_screen,
                            roi=config.DETECT_ROI or self._auto_roi)
                        _pre_fish = _yd.get("fish")
                        _pre_bar = _yd.get("bar")
                    self._show_debug_overlay(
                        _wait_screen, _pre_fish, _pre_bar,
                        status_text=f"⏳ 等待提竿 ({_wait_elapsed:.0f}/{wait_s:.0f}s)")
                except Exception:
                    pass
                time.sleep(0.2)

            if not self.running:
                return False

            self._hook_fish()
            if not self.running:
                return False

        # ═══════ Phase 2: 小游戏正式开始 ═══════
        self.state = "小游戏进行中"
        log.info("[🐟 钓鱼] 小游戏开始")

        if config.IL_RECORD:
            self._il_start_recording()
            log.info("[IL] 录制模式: 请手动操作鼠标控制白条!")
        elif config.IL_USE_MODEL:
            if self._il_policy is None:
                self._load_il_policy()
            if self._il_policy is not None:
                log.info("[IL] ★ 本局使用行为克隆模型控制 ★")
            else:
                log.warning("[IL] 模型加载失败, 回退到 PD 控制器")
        else:
            log.info("[PD] 本局使用 PD 控制器")

        if _use_yolo:
            log.info("[YOLO] 使用 YOLO 目标检测")

        # ★ 前几秒开启调试报告（便于排查检测问题）
        self.detector.debug_report = True

        # ★ PostMessage 模式不需要前台聚焦, 只更新点击坐标
        self.input.move_to_game_center()

        no_detect = 0
        fish_lost = 0          # ★ 连续鱼消失帧数
        frame = 0
        hold_count = 0         # 按住次数
        success = False
        _skip_fish = False     # ★ 白名单跳过标志: 非目标鱼→放弃控制
        _fish_id_saved = False # ★ 鱼种识别截图只保存一次
        self._progress_debug_saved = False  # ★ 进度条截图只保存一次
        minigame_start = time.time()   # ★ 计时: 超时强制结束
        ui_gone_count = 0              # ★ UI消失计数器
        had_good_detection = False     # ★ 是否曾经成功检测到鱼+条
        track_alive = True             # ★ 轨道是否存活 (定期更新)
        obj_gone_count = 0             # ★ 连续对象不足帧数
        fish_gone_since = None         # ★ 鱼消失开始时间
        bar_gone_since  = None         # ★ 白条消失开始时间

        # ── 重置 PD 控制器 ──
        self._bar_prev_cy   = None
        self._bar_prev_time = None
        self._bar_velocity  = 0.0
        self._last_hold     = None
        self._last_fish_cy  = None
        self._fish_smooth_cy = None
        self._bar_locked_cx  = None

        # ── 模板锁定变量（加速后续帧检测） ──
        locked_fish_key = None       # 如 "fish_blue"
        locked_fish_scales = None    # 如 [0.4, 0.5, 0.6]
        locked_bar_scales = None     # 如 [0.4, 0.5, 0.6]
        _BAR_X_HALF = config.REGION_X
        _FISH_X_HALF = max(config.REGION_X * 2, 80)

        # 初始化搜索区域
        screen_orig = self._grab()

        # ★ 始终保存小游戏首帧截图 (原始未旋转)
        self.screen.save_debug(screen_orig, "minigame_start")
        h_orig, w_orig = screen_orig.shape[:2]
        log.info(f"  截图尺寸: {w_orig}×{h_orig}")

        # ★ 初始化阶段也刷新 debug 窗口
        self._show_debug_overlay(
            screen_orig, status_text="🐟 小游戏初始化..."
        )

        if self._need_rotation:
            log.info(
                f"  ► 轨道倾斜 {self._track_angle:.1f}°, "
                f"启用旋转补偿 (旋转 {-self._track_angle:.1f}°)"
            )
            screen = self._rotate_for_detection(screen_orig)
        else:
            screen = screen_orig

        h_scr, w_scr = screen.shape[:2]

        if _use_yolo:
            search_region = None
            bar_search_region = None
            _regions_locked = True
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
            search_region, track_cx, bar_search_region = \
                self._init_search_region(screen)
            _regions_locked = False

            if track_cx is not None:
                self._bar_locked_cx = track_cx
                log.info(f"  ★ 轨道X轴预锁定: X={track_cx}")

            if search_region:
                srx, sry, srw, srh = search_region
                log.info(
                    f"  初始鱼搜索: X={srx}~{srx+srw} Y={sry}~{sry+srh}"
                )
            if bar_search_region:
                bsx, bsy, bsw, bsh = bar_search_region
                log.info(
                    f"  初始白条搜索: X={bsx}~{bsx+bsw} "
                    f"Y={bsy}~{bsy+bsh} (下半屏)"
                )

        _game_active = False
        _hook_time = time.time()
        _HOOK_DETECT_TIMEOUT = 1.5
        _hook_timeout_retry = False

        _last_progress_sr = None
        _last_track_w = None
        _last_green = 0.0
        _PROGRESS_SKIP_FRAMES = 20
        _prev_green = 0.0
        try:
            while self.running:
                frame += 1
                # ★ FPS 计算
                now_t = time.time()
                self._frame_times.append(now_t)
                if len(self._frame_times) > 20:
                    self._frame_times = self._frame_times[-20:]
                if len(self._frame_times) >= 2:
                    dt = self._frame_times[-1] - self._frame_times[0]
                    if dt > 0:
                        self._fps = (len(self._frame_times) - 1) / dt

                screen_raw = self._grab()
                screen = self._rotate_for_detection(screen_raw) \
                    if self._need_rotation else screen_raw

                # ════════════ 超时检测 ════════════
                elapsed = time.time() - minigame_start
                if elapsed > config.MINIGAME_TIMEOUT:
                    log.info(
                        f"[⏱ 超时] 小游戏已进行 {elapsed:.0f}s，"
                        f"超过 {config.MINIGAME_TIMEOUT:.0f}s 限制，强制结束"
                    )
                    break

                # ════════════ 定期检查 UI 是否还存在 ════════════
                if frame % config.UI_CHECK_FRAMES == 0 and frame > 10:
                    if _use_yolo:
                        _tc = self.yolo.detect(screen, config.DETECT_ROI or self._auto_roi)
                        track_check = _tc["track"]
                    else:
                        track_check = self.detector.find_multiscale(
                            screen, "track", 0.50
                        )
                    if track_check is None:
                        ui_gone_count += 1
                        track_alive = False
                        log.info(
                            f"[⚠ UI检查] 轨道未检测到 "
                            f"({ui_gone_count}/{config.UI_GONE_LIMIT})"
                        )
                        if ui_gone_count >= config.UI_GONE_LIMIT:
                            log.info("[📋 结束] 小游戏UI已消失，游戏结束!")
                            break
                    else:
                        ui_gone_count = 0
                        track_alive = True

                # ★ 每60帧确保鼠标在游戏窗口内
                if frame % 60 == 0:
                    self.input.ensure_cursor_in_game()

                # ════════════ ★ 连续丢失时跳过昂贵的全量搜索 ════════════
                if no_detect > 3 and not _use_yolo:
                    bar_quick = self.detector.find_multiscale(
                        screen, "bar", config.THRESH_BAR,
                        bar_search_region,
                        scales=locked_bar_scales or config.BAR_SCALES,
                    )
                    if bar_quick is not None:
                        # UI可能恢复了, 重置计数让下一帧做完整检测
                        log.info(f"[✓ 恢复] 丢失{no_detect}帧后重新检测到白条")
                        no_detect = 0
                    else:
                        no_detect += 1
                        if no_detect > 5:
                            self.input.mouse_up()
                        if no_detect > config.TRACK_LOST_LIMIT:
                            if _game_active and self._verify_game_alive():
                                log.info(f"[✓ 恢复] 连续{no_detect}帧丢失, 但验证UI仍在")
                                no_detect = 0
                                fish_lost = 0
                                fish_gone_since = None
                                bar_gone_since = None
                                obj_gone_count = 0
                            else:
                                log.info(
                                    f"[📋 结束] 连续{no_detect}帧未检测到"
                                    f"有效UI，验证确认游戏已结束")
                                break
                        # ★ debug 窗口仍然刷新
                        self._show_debug_overlay(
                            screen_raw,
                            status_text=f"⚠ 丢失中 {no_detect}/{config.TRACK_LOST_LIMIT}"
                        )
                        time.sleep(config.GAME_LOOP_INTERVAL)
                        continue

                # ════════════ 检测鱼 + 白条 ════════════
                fish = None
                bar = None
                fish_detect_name = ""
                _matched_key = None
                _bar_scale = 1.0

                _yolo_progress = None
                if _use_yolo:
                    # ──── YOLO: 一次推理检测全部 ────
                    _yolo_roi = config.DETECT_ROI or self._auto_roi
                    _ydet = self.yolo.detect(screen, roi=_yolo_roi)
                    fish = _ydet["fish"]
                    bar = _ydet["bar"]
                    _yolo_progress = _ydet.get("progress")
                    if fish is not None:
                        _save = not _fish_id_saved
                        _color_key = self.detector.identify_fish_type(
                            screen, fish, debug_save=_save)
                        if _save:
                            _fish_id_saved = True
                        _matched_key = _color_key
                        fish_detect_name = _color_key
                    else:
                        _matched_key = None
                        fish_detect_name = ""

                    # YOLO 数据采集: 保存完整窗口画面（不裁剪ROI）
                    if config.YOLO_COLLECT and frame % 10 == 0:
                        _cdir = os.path.join(
                            config.BASE_DIR, "yolo", "dataset",
                            "images", "unlabeled")
                        os.makedirs(_cdir, exist_ok=True)
                        _ts = time.strftime("%Y%m%d_%H%M%S")
                        _ms = int((time.time() % 1) * 1000)
                        cv2.imwrite(
                            os.path.join(_cdir, f"{_ts}_{_ms:03d}.png"),
                            screen)

                else:
                    # ──── 模板匹配: 原有逻辑 ────
                    _fish_sr = search_region
                    if search_region:
                        _sr_x, _sr_y, _sr_w, _sr_h = search_region
                        _new_x, _new_w = _sr_x, _sr_w
                        _new_y, _new_h = _sr_y, _sr_h
                        if self._bar_locked_cx is not None:
                            _nx = max(_sr_x,
                                      self._bar_locked_cx - _FISH_X_HALF)
                            _nx2 = min(_sr_x + _sr_w,
                                       self._bar_locked_cx + _FISH_X_HALF)
                            if _nx2 - _nx > 10:
                                _new_x, _new_w = _nx, _nx2 - _nx
                        if self._fish_smooth_cy is not None:
                            _ny = max(_sr_y,
                                      int(self._fish_smooth_cy) - 150)
                            _ny2 = min(_sr_y + _sr_h,
                                       int(self._fish_smooth_cy) + 150)
                            if _ny2 - _ny > 30:
                                _new_y, _new_h = _ny, _ny2 - _ny
                        _fish_sr = (_new_x, _new_y, _new_w, _new_h)

                    _fg, _fox, _foy = self.detector.prepare_gray(
                        screen, _fish_sr, upload_gpu=True
                    )
                    _bg, _box, _boy = self.detector.prepare_gray(
                        screen, bar_search_region, upload_gpu=True
                    )

                    _has_cuda = self.detector._use_cuda

                    def _detect_fish():
                        if locked_fish_key:
                            r = self.detector.find_multiscale(
                                screen, locked_fish_key, config.THRESH_FISH,
                                _fish_sr, scales=locked_fish_scales,
                                pre_gray=_fg, pre_offset=(_fox, _foy),
                            )
                            if r is None and _fish_sr is not search_region:
                                r = self.detector.find_multiscale(
                                    screen, locked_fish_key,
                                    config.THRESH_FISH,
                                    search_region, scales=locked_fish_scales
                                )
                            return r, locked_fish_key if r else None
                        else:
                            if _has_cuda:
                                r = self.detector.find_fish(
                                    screen, config.THRESH_FISH, _fish_sr,
                                    pre_gray=_fg, pre_offset=(_fox, _foy),
                                )
                            else:
                                _n = len(config.FISH_KEYS)
                                _grp_size = 2
                                _grp_count = ((_n + _grp_size - 1)
                                              // _grp_size)
                                _grp_idx = frame % _grp_count
                                _start = _grp_idx * _grp_size
                                _keys = config.FISH_KEYS[
                                    _start:_start + _grp_size]
                                r = self.detector.find_fish(
                                    screen, config.THRESH_FISH, _fish_sr,
                                    pre_gray=_fg, pre_offset=(_fox, _foy),
                                    keys=_keys,
                                )
                            return (r, self.detector._last_best_key
                                    if r else None)

                    def _detect_bar():
                        _scales = locked_bar_scales or config.BAR_SCALES
                        r = self.detector.find_multiscale(
                            screen, "bar", config.THRESH_BAR,
                            bar_search_region, scales=_scales,
                            pre_gray=_bg, pre_offset=(_box, _boy),
                        )
                        return r, self.detector._last_scale

                    fut_fish = self._pool.submit(_detect_fish)
                    fut_bar = self._pool.submit(_detect_bar)
                    fish_result = fut_fish.result()
                    bar_result = fut_bar.result()

                    fish, _matched_key = fish_result
                    bar, _bar_scale = bar_result
                if not _use_yolo:
                    fish_detect_name = ""
                    if locked_fish_key:
                        if fish is not None:
                            fish_detect_name = locked_fish_key
                        if (fish is None and fish_lost > 20
                                and fish_lost % 20 == 0):
                            locked_fish_key = None
                            locked_fish_scales = None
                            log.info("  ★ 解除鱼模板锁定, 重新搜索")
                    else:
                        if fish is not None:
                            fish_detect_name = _matched_key or "?"
                            if (_matched_key
                                    and _matched_key != "fish_white"):
                                locked_fish_key = _matched_key
                                s = self.detector._last_best_scale
                                locked_fish_scales = [
                                    round(s * 0.85, 2), s,
                                    round(s * 1.15, 2)
                                ]
                                log.info(
                                    f"  ★ 锁定鱼模板: "
                                    f"{locked_fish_key} @ scales="
                                    f"{[f'{x:.2f}' for x in locked_fish_scales]}"
                                )

                if fish is not None:
                    self._current_fish_name = fish_detect_name
                    if not _skip_fish and fish_detect_name:
                        wl_key = fish_detect_name
                        if not config.FISH_WHITELIST.get(wl_key, True):
                            fname_cn = self.FISH_DISPLAY.get(
                                wl_key, (wl_key,))[0]
                            log.info(
                                f"[白名单] {fname_cn} 不在白名单中, 放弃本次钓鱼")
                            _skip_fish = True

                if not _use_yolo and bar is not None and not locked_bar_scales:
                    locked_bar_scales = [
                        round(max(0.2, _bar_scale * 0.85), 2),
                        _bar_scale,
                        round(_bar_scale * 1.15, 2),
                    ]
                    log.info(
                        f"  ★ 锁定白条 "
                        f"@ scales={[f'{x:.2f}' for x in locked_bar_scales]}"
                    )

                # ════════════ ★ X轴验证 (鱼和白条共用轨道X) ════════════
                if bar is not None:
                    raw_bcx = bar[0] + bar[2] // 2
                    if self._bar_locked_cx is None:
                        self._bar_locked_cx = raw_bcx
                        log.info(f"  ★ 轨道X轴锁定(白条): X={raw_bcx}")
                    elif abs(raw_bcx - self._bar_locked_cx) > _BAR_X_HALF:
                        bar = None
                    if bar is not None:
                        bar = (self._bar_locked_cx - bar[2] // 2,
                               bar[1], bar[2], bar[3], bar[4])

                # ════════════ ★ 首次检测到白条 → 锁定Y轴搜索范围 ════════════
                if bar is not None and not _regions_locked:
                    bar_cy = bar[1] + bar[3] // 2
                    tcx = self._bar_locked_cx or (bar[0] + bar[2] // 2)
                    y_top = max(0, bar_cy - config.REGION_UP)
                    y_bot = min(h_scr, bar_cy + config.REGION_DOWN)
                    _roi = config.DETECT_ROI
                    if _roi:
                        y_top = max(y_top, _roi[1])
                        y_bot = min(y_bot, _roi[1] + _roi[3])
                    rh = y_bot - y_top
                    # 鱼: 比白条稍宽的搜索区域
                    fish_half = max(config.REGION_X * 2, 80)
                    fsx = max(0, tcx - fish_half)
                    fsw = min(fish_half * 2, w_scr - fsx)
                    if _roi:
                        fsx = max(fsx, _roi[0])
                        fsw = min(fsw, _roi[0] + _roi[2] - fsx)
                    search_region = (fsx, y_top, fsw, rh)
                    # 白条: 紧搜索区域 (用户控制)
                    bar_half = config.REGION_X
                    bsx = max(0, tcx - bar_half)
                    bsw = min(bar_half * 2, w_scr - bsx)
                    if _roi:
                        bsx = max(bsx, _roi[0])
                        bsw = min(bsw, _roi[0] + _roi[2] - bsx)
                    bar_search_region = (bsx, y_top, bsw, rh)
                    _regions_locked = True
                    log.info(
                        f"  ★ 搜索区域锁定(白条Y={bar_cy}): "
                        f"Y={y_top}~{y_bot} "
                        f"鱼X=±{fish_half} 条X=±{bar_half}"
                        f"{' (ROI裁剪)' if _roi else ''}"
                    )

                # 鱼: 用同一个轨道X验证, 偏离过大则丢弃
                if fish is not None:
                    raw_fcx = fish[0] + fish[2] // 2
                    if self._bar_locked_cx is not None:
                        if abs(raw_fcx - self._bar_locked_cx) > _FISH_X_HALF:
                            fish = None
                            self._current_fish_name = ""
                    if fish is not None and self._bar_locked_cx is not None:
                        fish = (self._bar_locked_cx - fish[2] // 2,
                                fish[1], fish[2], fish[3], fish[4])

                # ════════════ ★ 空间合理性验证 (仅Y轴) ════════════
                if fish is not None and bar is not None:
                    fish_cy_check = fish[1] + fish[3] // 2
                    bar_cy_check  = bar[1]  + bar[3]  // 2
                    dist_y = abs(fish_cy_check - bar_cy_check)

                    if dist_y > config.MAX_FISH_BAR_DIST:
                        if frame % 30 == 1:
                            log.warning(
                                f"[⚠ 误检] 鱼Y={fish_cy_check} 条Y="
                                f"{bar_cy_check} 距离={dist_y}px > "
                                f"{config.MAX_FISH_BAR_DIST}px"
                            )
                        fish = None
                        bar = None

                # ════════════ ★ 可视化调试 (每帧都画, 内置节流) ════════════
                # ★ 用原始画面展示 (不旋转), 更直观
                # (旋转时坐标略有偏差, 但远好过看旋转画面)
                _display_sr = search_region or self._auto_roi
                if not self._need_rotation:
                    self._show_debug_overlay(
                        screen_raw, fish, bar, _display_sr,
                        bar_search_region=bar_search_region,
                        progress=_yolo_progress,
                        status_text=f"🐟 小游戏 F{frame:04d}"
                    )
                else:
                    self._show_debug_overlay(
                        screen_raw,
                        search_region=_display_sr,
                        bar_search_region=bar_search_region,
                        progress=_yolo_progress,
                        status_text=f"🐟 小游戏 F{frame:04d} (旋转{self._track_angle:.0f}°补偿中)"
                    )

                # ════════════ ★ 首次检测到鱼 → 激活PD控制 ════════════
                if not _game_active:
                    if fish is not None:
                        _game_active = True
                        had_good_detection = True
                        log.info("[🐟 开始] 检测到鱼, 小游戏确认! PD控制启动")
                        if not config.IL_RECORD:
                            press_t = getattr(config, 'INITIAL_PRESS_TIME', 0.2)
                            self.input.mouse_down()
                            time.sleep(press_t)
                            self.input.mouse_up()
                    elif time.time() - _hook_time > _HOOK_DETECT_TIMEOUT:
                        log.warning(
                            f"[⚠ 超时] 提竿后 {_HOOK_DETECT_TIMEOUT}s 未检测到鱼,"
                            f" 判定未进入小游戏, 返回主循环重新抛竿")
                        _hook_timeout_retry = True
                        break
                    else:
                        time.sleep(config.GAME_LOOP_INTERVAL)
                        continue

                # ════════════ 进度条 (记录进度, 不直接判定结束) ════════════
                green = 0.0
                if frame <= _PROGRESS_SKIP_FRAMES:
                    pass
                elif _use_yolo and _yolo_progress is not None:
                    px, py, pw, ph = _yolo_progress[:4]
                    pcx = px + pw // 2
                    strip_w = 5
                    sx = max(0, pcx - strip_w // 2)
                    green = self.detector.detect_green_ratio(
                        screen, (sx, py, strip_w, ph))
                    if not self._progress_debug_saved and green > 0:
                        self._progress_debug_saved = True
                        _pad = 20
                        _dx = max(0, px - _pad)
                        _dw = min(pw + _pad * 2, w_scr - _dx)
                        _dbg = screen[py:py + ph, _dx:_dx + _dw].copy()
                        cv2.rectangle(_dbg, (sx - _dx, 0),
                                      (sx - _dx + strip_w, ph),
                                      (0, 255, 0), 1)
                        _info = f"green={green:.0%} w={strip_w}"
                        cv2.putText(_dbg, _info, (2, 16),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                                    (0, 255, 255), 1)
                        _ddir = os.path.join(config.BASE_DIR, "debug")
                        os.makedirs(_ddir, exist_ok=True)
                        cv2.imwrite(
                            os.path.join(_ddir, "progress_strip.png"), _dbg)
                else:
                    _sr_for_progress = search_region
                    if bar is not None:
                        bcx = bar[0] + bar[2] // 2
                        bcy = bar[1] + bar[3] // 2
                        _pr_half_x = max(config.REGION_X * 2, 80)
                        _pr_x = max(0, bcx - _pr_half_x)
                        _pr_y = max(0, bcy - config.REGION_UP)
                        _pr_w = min(_pr_half_x * 2, w_scr - _pr_x)
                        _pr_h = min(config.REGION_UP + config.REGION_DOWN,
                                    h_scr - _pr_y)
                        _sr_for_progress = (_pr_x, _pr_y, _pr_w, _pr_h)
                        _last_progress_sr = _sr_for_progress
                    elif _last_progress_sr is not None:
                        _sr_for_progress = _last_progress_sr
                    green = self._check_progress(
                        screen, fish, _sr_for_progress)

                if green > 0 and _prev_green > 0.01 and (green - _prev_green) > 0.30:
                    log.debug(f"  进度跳变过大 {_prev_green:.0%}→{green:.0%}，忽略")
                    green = _prev_green

                if green > 0:
                    _prev_green = green
                if green > _last_green:
                    _last_green = green

                # ════════════ 游戏结束检测 ════════════
                # ★ 统计本帧检测到的对象数量 (鱼/白条/轨道)
                obj_count = ((fish is not None) + (bar is not None)
                             + (1 if track_alive else 0))

                # 1) 鱼+条都没检测到 → 计数
                if fish is None and bar is None:
                    no_detect += 1
                    if no_detect > 5 and not config.IL_RECORD:
                        self.input.mouse_up()

                    if no_detect == 10:
                        log.warning(
                            f"[⚠ 丢失] 连续{no_detect}帧鱼+条均未检测到"
                        )
                        self.screen.save_debug(screen, "minigame_lost")

                    if no_detect > config.TRACK_LOST_LIMIT:
                        if self._verify_game_alive():
                            log.info(f"[✓ 恢复] 连续{no_detect}帧丢失, 但验证UI仍在")
                            no_detect = 0
                            fish_lost = 0
                            fish_gone_since = None
                            bar_gone_since = None
                            obj_gone_count = 0
                        else:
                            log.info(f"[📋 结束] 连续{no_detect}帧未检测到有效UI，验证确认结束")
                            break

                    time.sleep(config.GAME_LOOP_INTERVAL)
                    continue
                else:
                    if no_detect > 5:
                        log.info(f"[✓ 恢复] 重新检测到有效UI (之前丢失{no_detect}帧)")
                    no_detect = 0

                # 2) 单独追踪鱼的消失 (条可能误匹配)
                if fish is None:
                    fish_lost += 1
                    if fish_gone_since is None:
                        fish_gone_since = time.time()
                    if fish_lost == 30:
                        log.warning(f"[⚠ 鱼丢失] 连续{fish_lost}帧未检测到鱼")
                    if had_good_detection and fish_lost > config.FISH_LOST_LIMIT:
                        if self._verify_game_alive():
                            log.info(f"[✓ 恢复] 鱼消失{fish_lost}帧, 但验证UI仍在")
                            fish_lost = 0
                            fish_gone_since = None
                            no_detect = 0
                        else:
                            log.info(f"[📋 结束] 鱼已消失{fish_lost}帧，验证确认结束")
                            break
                else:
                    fish_lost = 0
                    fish_gone_since = None
                    had_good_detection = True

                if bar is None:
                    if bar_gone_since is None:
                        bar_gone_since = time.time()
                else:
                    bar_gone_since = None

                # ★ 单项超时: 鱼或条任一消失超过 N 秒 → 失败收杆
                _timeout = config.SINGLE_OBJ_TIMEOUT
                now_t = time.time()
                if (had_good_detection and fish_gone_since is not None
                        and now_t - fish_gone_since > _timeout):
                    elapsed = now_t - fish_gone_since
                    if self._verify_game_alive():
                        log.info(f"[✓ 恢复] 鱼消失{elapsed:.1f}s, 但验证UI仍在")
                        fish_gone_since = None
                        fish_lost = 0
                        no_detect = 0
                    else:
                        log.info(
                            f"[📋 失败] 鱼连续消失 {elapsed:.1f}s, 验证确认结束")
                        break
                if (had_good_detection and bar_gone_since is not None
                        and now_t - bar_gone_since > _timeout):
                    elapsed = now_t - bar_gone_since
                    if self._verify_game_alive():
                        log.info(f"[✓ 恢复] 白条消失{elapsed:.1f}s, 但验证UI仍在")
                        bar_gone_since = None
                        no_detect = 0
                    else:
                        log.info(
                            f"[📋 失败] 白条连续消失 {elapsed:.1f}s, 验证确认结束")
                        break

                # 3) ★ 对象不足检测: 鱼/条/轨道 至少2个才继续
                if obj_count < config.OBJ_MIN_COUNT:
                    obj_gone_count += 1
                    if obj_gone_count == 1 or obj_gone_count % 10 == 0:
                        has_f = "鱼✓" if fish is not None else "鱼✗"
                        has_b = "条✓" if bar is not None else "条✗"
                        has_t = "轨道✓" if track_alive else "轨道✗"
                        log.warning(
                            f"[⚠ 对象不足] {has_f} {has_b} {has_t} "
                            f"= {obj_count}个 "
                            f"({obj_gone_count}/{config.OBJ_GONE_LIMIT})"
                        )
                    if obj_gone_count >= config.OBJ_GONE_LIMIT:
                        if self._verify_game_alive():
                            log.info(f"[✓ 恢复] 对象不足{obj_gone_count}帧, 但验证UI仍在")
                            obj_gone_count = 0
                            no_detect = 0
                            fish_lost = 0
                            fish_gone_since = None
                            bar_gone_since = None
                        else:
                            log.info(
                                f"[📋 结束] 连续{obj_gone_count}帧对象不足,"
                                f"验证确认结束")
                            break
                else:
                    if obj_gone_count > 3:
                        log.info(
                            f"[✓ 恢复] 对象数恢复为{obj_count}"
                            f" (之前不足{obj_gone_count}帧)"
                        )
                    obj_gone_count = 0

                # ════════════ ★ 控制 (录制 / 模型 / PD) ════════════
                if _skip_fish:
                    self.input.mouse_up()
                    held = False
                elif config.IL_RECORD:
                    self._il_record_frame(frame, fish, bar)
                    held = False
                elif config.IL_USE_MODEL and self._il_policy is not None:
                    held = self._il_model_control(fish, bar)
                else:
                    held = self._control_mouse(fish, bar, search_region)
                if held:
                    hold_count += 1

                # 5秒后切回用户设置的调试模式
                if frame == 50:
                    self.detector.debug_report = self.debug_mode

                # ── 日志 (每30帧输出) ──
                if frame % 30 == 0:
                    fname = self._current_fish_name.replace(
                        "fish_", ""
                    ) if self._current_fish_name else ""
                    fi = (f"鱼[{fname}]Y={fish[1]+fish[3]//2}"
                          if fish else "鱼=无")
                    bi = f"条Y={bar[1]+bar[3]//2}" if bar else "条=无"
                    vel = f"v={self._bar_velocity:+.0f}"
                    log.info(
                        f"[F{frame:04d}] {fi} | {bi} | {vel} | "
                        f"按住:{hold_count} | 进度:{green:.0%}"
                    )

                time.sleep(config.GAME_LOOP_INTERVAL)

        finally:
            if _hook_timeout_retry:
                self.input.safe_release()
                time.sleep(0.3)
                self.input.click()
                log.info("[🎣 收杆] 点击收杆后返回主循环重新抛竿")
                return None

            if _skip_fish:
                success = False
                log.info(
                    f"[⏭ 跳过] 非目标鱼, 已放弃 (进度 {_last_green:.0%} 不计)"
                )
            elif _last_green > config.SUCCESS_PROGRESS:
                success = True
                log.info(
                    f"[✅ 成功] 最终进度 {_last_green:.0%} > "
                    f"{config.SUCCESS_PROGRESS:.0%}，判定成功"
                )
            else:
                log.info(
                    f"[❌ 失败] 最终进度 {_last_green:.0%} <= "
                    f"{config.SUCCESS_PROGRESS:.0%}，判定失败"
                )

            if config.IL_RECORD:
                self._il_stop_recording()
                log.info("[🎣 收杆] 录制模式 — 请手动收杆")
            else:
                self.input.safe_release()
                time.sleep(0.5)

                if getattr(config, "SKIP_SUCCESS_CHECK", False):
                    time.sleep(0.2)
                    self.input.click()
                    log.info("[🎣 收杆] 跳过成功检查, 点击收杆")
                    success = True
                elif success:
                    time.sleep(0.2)
                    self.input.click()
                    log.info("[🎣 收杆] 钓鱼成功, 点击收杆")
                else:
                    log.info("[🎣 失败] 鱼竿已自动收回, 跳过收杆")

        return success

    # ══════════════════════════════════════════════════════
    #  可视化调试
    # ══════════════════════════════════════════════════════

    def _show_debug_overlay(self, screen, fish=None, bar=None,
                            search_region=None, bar_search_region=None,
                            progress=None, status_text=""):
        """
        统一调试窗口 — 所有阶段可用。
        ★ 先缩小到小图再绘制叠加层，大幅降低 CPU / 内存开销。
        """
        if not config.SHOW_DEBUG:
            return
        now = time.time()
        if now - self._last_overlay_time < config.DEBUG_OVERLAY_INTERVAL:
            return
        self._last_overlay_time = now

        # ── ROI 裁剪: 只显示框选区域 ──
        _roi = config.DETECT_ROI
        ox, oy = 0, 0
        if _roi:
            rx, ry, rw, rh = _roi
            sh, sw = screen.shape[:2]
            rx = max(0, min(rx, sw - 1))
            ry = max(0, min(ry, sh - 1))
            rw = min(rw, sw - rx)
            rh = min(rh, sh - ry)
            if rw > 20 and rh > 20:
                screen = screen[ry:ry + rh, rx:rx + rw].copy()
                ox, oy = rx, ry

        h, w = screen.shape[:2]
        max_w = config.DEBUG_OVERLAY_MAX_W
        max_h = config.DEBUG_OVERLAY_MAX_H
        s = min(max_w / w, max_h / h, 1.0)

        if s < 1.0:
            debug = cv2.resize(screen, (int(w * s), int(h * s)),
                               interpolation=cv2.INTER_NEAREST)
        else:
            debug = screen.copy()
            s = 1.0

        # ── 坐标缩放辅助 (减去 ROI 偏移后再缩放) ──
        def sx(v):
            return int((v - ox) * s)

        def sy(v):
            return int((v - oy) * s)

        # ── 顶部状态文字 ──
        y_txt = 22
        fs = 0.55
        dw = debug.shape[1]
        # ★ FPS 显示 (右上角)
        fps_text = f"{self._fps:.1f} FPS"
        fps_color = (0, 255, 0) if self._fps >= 10 else (0, 255, 255) if self._fps >= 5 else (0, 0, 255)
        cv2.putText(debug, fps_text, (dw - 120, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, fps_color, 2)

        if status_text:
            cv2.putText(debug, status_text, (8, y_txt),
                        cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 255, 255), 1)
            y_txt += 22

        if self._need_rotation:
            cv2.putText(debug, f"Rotation: {-self._track_angle:.1f} deg",
                        (8, y_txt), cv2.FONT_HERSHEY_SIMPLEX, fs,
                        (0, 200, 255), 1)
            y_txt += 20

        # ★ 控制状态 + 速度标注
        if fish is not None and bar is not None:
            fish_cy = fish[1] + fish[3] // 2
            bar_cy  = bar[1]  + bar[3]  // 2
            diff = bar_cy - fish_cy
            if diff > config.DEAD_ZONE:
                label = f"v BAR below (d={diff}px)"
                lcolor = (0, 100, 255)
            elif diff < -config.DEAD_ZONE:
                label = f"^ BAR above (d={diff}px)"
                lcolor = (255, 200, 0)
            else:
                label = f"= dead zone (d={diff}px)"
                lcolor = (0, 255, 0)
            cv2.putText(debug, label, (8, y_txt),
                        cv2.FONT_HERSHEY_SIMPLEX, fs, lcolor, 1)
            y_txt += 20
        elif fish is None and bar is None and self.state == "小游戏进行中":
            cv2.putText(debug, "X no fish+bar", (8, y_txt),
                        cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 255), 1)
            y_txt += 20

        if abs(self._bar_velocity) > 0.5:
            cv2.putText(debug, f"v={self._bar_velocity:+.0f} px/s",
                        (8, y_txt), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                        (200, 200, 200), 1)
            y_txt += 18

        # ── 绘制搜索区域 (灰色=鱼, 浅青=白条) ──
        if search_region:
            rx, ry, rw, rh = [int(v) for v in search_region]
            cv2.rectangle(debug, (sx(rx), sy(ry)),
                          (sx(rx + rw), sy(ry + rh)), (128, 128, 128), 1)
        if bar_search_region:
            bx, by, bw, bh = [int(v) for v in bar_search_region]
            cv2.rectangle(debug, (sx(bx), sy(by)),
                          (sx(bx + bw), sy(by + bh)), (128, 200, 200), 1)

        # ── 绘制鱼 + 显示鱼颜色名 ──
        if fish is not None:
            fx, fy, fw, fh = fish[:4]
            fish_cy = fy + fh // 2
            fname, fcolor = self.FISH_DISPLAY.get(
                self._current_fish_name, ("?", (0, 255, 0))
            )
            cv2.rectangle(debug, (sx(fx), sy(fy)),
                          (sx(fx + fw), sy(fy + fh)), fcolor, 2)
            cv2.putText(debug, f"{fname} Y={fish_cy}",
                        (sx(fx + fw) + 4, sy(fish_cy)),
                        cv2.FONT_HERSHEY_SIMPLEX, fs, fcolor, 1)
            cv2.line(debug, (sx(fx), sy(fish_cy)),
                     (sx(fx + fw), sy(fish_cy)), fcolor, 1)

        # ── 绘制白条（蓝色）──
        if bar is not None:
            bx, by, bw, bh = bar[:4]
            bar_cy = by + bh // 2
            cv2.rectangle(debug, (sx(bx), sy(by)),
                          (sx(bx + bw), sy(by + bh)), (255, 100, 0), 2)
            cv2.putText(debug, f"Bar Y={bar_cy}",
                        (max(0, sx(bx) - 90), sy(bar_cy)),
                        cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 100, 0), 1)
            cv2.line(debug, (sx(bx), sy(bar_cy)),
                     (sx(bx + bw), sy(bar_cy)), (255, 100, 0), 1)

        # ── 绘制进度条 (黄绿色) ──
        if progress is not None:
            px, py, pw, ph = progress[:4]
            cv2.rectangle(debug, (sx(px), sy(py)),
                          (sx(px + pw), sy(py + ph)), (0, 220, 180), 2)
            cv2.putText(debug, "Progress",
                        (sx(px), sy(py) - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 220, 180), 1)

        # ── 鱼和白条之间的连线 ──
        if fish is not None and bar is not None:
            fish_cy = fish[1] + fish[3] // 2
            bar_cy  = bar[1]  + bar[3]  // 2
            cx = (fish[0] + bar[0]) // 2
            diff = bar_cy - fish_cy
            color = (0, 0, 255) if abs(diff) > 50 else (0, 255, 255)
            cv2.arrowedLine(debug, (sx(cx), sy(bar_cy)),
                            (sx(cx), sy(fish_cy)), color, 1, tipLength=0.15)
            cv2.putText(debug, f"d={diff:+d}",
                        (sx(cx) + 6, sy((fish_cy + bar_cy) // 2)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)

        with self._debug_lock:
            self._debug_frame = debug

        if self._debug_thread is None or not self._debug_thread.is_alive():
            self._debug_thread = threading.Thread(
                target=self._debug_display_loop, daemon=True
            )
            self._debug_thread.start()

    def _debug_display_loop(self):
        """独立线程: 循环显示 debug 帧, cv2.waitKey 阻塞不影响钓鱼线程"""
        while self.running or self._debug_frame is not None:
            frame = None
            with self._debug_lock:
                if self._debug_frame is not None:
                    frame = self._debug_frame
                    self._debug_frame = None
            if frame is not None:
                try:
                    cv2.imshow("Debug Overlay", frame)
                except Exception:
                    break
            key = cv2.waitKey(1)
            if key == 27:  # ESC
                break
        try:
            cv2.destroyWindow("Debug Overlay")
        except Exception:
            pass

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
        以白条中心X左侧 5 像素宽窄条检测绿色占比, 避免背景干扰。
        """
        if sr is None:
            return 0.0

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
        """加载训练好的行为克隆模型 (含归一化参数)"""
        try:
            import torch
            from imitation.model import FishPolicy
            checkpoint = torch.load(config.IL_MODEL_PATH, map_location="cpu",
                                    weights_only=True)

            # 兼容旧格式 (纯 state_dict) 和新格式 (含归一化)
            if "model_state" in checkpoint:
                state = checkpoint["model_state"]
                self._il_norm_mean = checkpoint["norm_mean"].numpy()
                self._il_norm_std = checkpoint["norm_std"].numpy()
                hist_len = checkpoint.get("history_len", config.IL_HISTORY_LEN)
            else:
                state = checkpoint
                self._il_norm_mean = None
                self._il_norm_std = None
                hist_len = config.IL_HISTORY_LEN

            model = FishPolicy(history_len=hist_len)
            model.load_state_dict(state)
            model.eval()
            if torch.cuda.is_available():
                model = model.cuda()
                self._il_device = "cuda"
            self._il_policy = model
            norm_info = "含归一化" if self._il_norm_mean is not None else "无归一化"
            log.info(f"[IL] 模型已加载 ({self._il_device}, {norm_info})")
        except Exception as e:
            log.warning(f"[IL] 模型加载失败: {e}")
            self._il_policy = None

    def _il_start_recording(self):
        """开始录制一局小游戏的数据"""
        os.makedirs(config.IL_DATA_DIR, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(config.IL_DATA_DIR, f"session_{ts}.csv")
        self._il_file = open(path, "w", newline="", encoding="utf-8")
        self._il_writer = csv.writer(self._il_file)
        self._il_writer.writerow([
            "frame", "timestamp",
            "fish_cy", "bar_cy", "bar_h",
            "error", "velocity", "fish_delta", "dist_ratio",
            "mouse_pressed",
            "fish_in_bar", "press_streak",
            "predicted", "bar_accel",
        ])
        self._il_prev_fish_cy = None
        self._il_mouse_prev = 0
        self._il_history.clear()
        log.info(f"[IL] 录制开始 → {path}")

    def _il_stop_recording(self):
        """结束录制"""
        if self._il_file:
            self._il_file.close()
            self._il_file = None
            self._il_writer = None
            log.info("[IL] 录制结束")

    @staticmethod
    def _is_mouse_pressed() -> bool:
        """检测用户是否按住鼠标左键"""
        return ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000 != 0

    def _il_build_features(self, fish, bar):
        """从检测结果构建一帧特征 [10维]"""
        fish_cy = fish[1] + fish[3] // 2
        bar_cy = bar[1] + bar[3] // 2
        bar_h = bar[3]
        bar_top = bar[1]
        error = bar_cy - fish_cy
        velocity = self._bar_velocity
        fish_delta = 0.0
        if self._il_prev_fish_cy is not None:
            fish_delta = fish_cy - self._il_prev_fish_cy
        self._il_prev_fish_cy = fish_cy
        dist_ratio = error / max(bar_h, 1)

        fish_in_bar = (fish_cy - bar_top) / max(bar_h, 1)

        if self._il_mouse_prev == 1:
            self._il_press_streak = max(1, getattr(self, '_il_press_streak', 0) + 1)
        else:
            self._il_press_streak = min(-1, getattr(self, '_il_press_streak', 0) - 1)
        press_streak = self._il_press_streak / 10.0

        # 惯性预测: 150ms 后白条相对鱼的位置
        predicted = error + velocity * 0.15

        # 加速度: 速度变化量
        bar_accel = 0.0
        if hasattr(self, '_il_prev_velocity'):
            bar_accel = velocity - self._il_prev_velocity
        self._il_prev_velocity = velocity

        return [error, velocity, bar_h, fish_delta, dist_ratio,
                self._il_mouse_prev, fish_in_bar, press_streak,
                predicted, bar_accel]

    def _il_record_frame(self, frame_idx, fish, bar):
        """录制一帧: 读取用户鼠标状态并写入CSV"""
        if fish is None or bar is None or self._il_writer is None:
            return

        mouse = 1 if self._is_mouse_pressed() else 0
        feats = self._il_build_features(fish, bar)
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

        self._il_writer.writerow([
            frame_idx, f"{time.time():.4f}",
            fish_cy, bar_cy, bar_h,
            f"{error:.1f}", f"{velocity:.1f}", f"{fish_delta:.1f}",
            f"{dist_ratio:.3f}",
            mouse,
            f"{fish_in_bar:.3f}", f"{press_streak:.2f}",
            f"{predicted:.1f}", f"{bar_accel:.1f}",
        ])
        self._il_mouse_prev = mouse

    def _il_model_control(self, fish, bar) -> bool:
        """
        用训练好的模型决定按/松 — 状态式控制 (不是脉冲)
        模型输出 = "此刻鼠标应该处于按住还是松开", 与录制时一致
        """
        import torch

        if self._il_policy is None:
            return False

        if fish is not None and bar is not None:
            feats = self._il_build_features(fish, bar)
            self._il_history.append(feats)
        elif fish is None and bar is None:
            self.input.mouse_up()
            self._il_mouse_prev = 0
            return False

        if len(self._il_history) < config.IL_HISTORY_LEN:
            self.input.mouse_down()
            self._il_mouse_prev = 1
            return True

        import numpy as np
        flat = []
        for f in self._il_history:
            flat.extend(f)
        flat_np = np.array(flat, dtype=np.float32)
        if self._il_norm_mean is not None:
            flat_np = (flat_np - self._il_norm_mean) / self._il_norm_std
        x = torch.from_numpy(flat_np).unsqueeze(0).to(self._il_device)
        prob = self._il_policy.predict(x)

        fish_cy = fish[1] + fish[3] // 2 if fish else -1
        bar_cy = bar[1] + bar[3] // 2 if bar else -1

        thresh = config.IL_PRESS_THRESH
        if prob > thresh:
            self.input.mouse_down()
            self._il_mouse_prev = 1
            if fish is not None and bar is not None and self._il_log_counter % 10 == 0:
                log.info(
                    f"  [IL] 鱼Y={fish_cy} 条Y={bar_cy} "
                    f"p={prob:.2f}>{thresh:.2f} → 按住"
                )
            self._il_log_counter += 1
            return True
        else:
            self.input.mouse_up()
            self._il_mouse_prev = 0
            if fish is not None and bar is not None and self._il_log_counter % 10 == 0:
                log.info(
                    f"  [IL] 鱼Y={fish_cy} 条Y={bar_cy} "
                    f"p={prob:.2f}<={thresh:.2f} → 释放"
                )
            self._il_log_counter += 1
            return False

    def _control_mouse(self, fish, bar, sr) -> bool:
        """
        PD 物理控制器（星露谷钓鱼）:

        物理模型:
        - 按住鼠标 → 白条获得向上加速度，按住越久速度越快
        - 松开鼠标 → 重力让白条减速→停→加速下落
        - 白条有惯性: 即使松开也会继续按原方向运动一段时间

        控制策略:
        - 计算「误差」= 白条中心 - 鱼中心 (正=白条在下方)
        - 估算「速度」= 白条的运动速度 (正=向下, 负=向上)
        - 用速度预测未来位置, 提前松开避免惯性过冲
        - 按住时长 ∝ 预测误差 (远=长按, 近=短按)

        返回: 是否执行了按住操作
        """
        now = time.time()

        # ═══════════ ★ 速度估算: 只要检测到白条就更新 ═══════════
        if bar is not None:
            bar_cy_raw = bar[1] + bar[3] // 2
            if (self._bar_prev_cy is not None
                    and self._bar_prev_time is not None):
                dt = now - self._bar_prev_time
                if dt > 0.003:
                    raw_vel = (bar_cy_raw - self._bar_prev_cy) / dt
                    α = min(config.VELOCITY_SMOOTH, 0.95)
                    self._bar_velocity = (
                        α * self._bar_velocity + (1 - α) * raw_vel
                    )
            self._bar_prev_cy = bar_cy_raw
            self._bar_prev_time = now

        vel = self._bar_velocity

        # ═══════════ ★ 连续 PD 控制器 (读取 GUI 参数) ═══════════
        TARGET_FIB = 0.5
        KP         = getattr(config, 'HOLD_GAIN', 0.040)
        KD         = getattr(config, 'SPEED_DAMPING', 0.00025)
        BASE_HOLD  = getattr(config, 'HOLD_MIN_S', 0.025)
        MAX_HOLD   = getattr(config, 'HOLD_MAX_S', 0.100)
        MIN_HOLD   = 0.004

        if fish is not None and bar is not None:
            raw_fish_cy = fish[1] + fish[3] // 2
            bar_cy      = bar[1]  + bar[3]  // 2

            # ── 鱼位置平滑 (EMA) ──
            if self._fish_smooth_cy is None:
                self._fish_smooth_cy = float(raw_fish_cy)
            else:
                self._fish_smooth_cy = (
                    0.4 * raw_fish_cy + 0.6 * self._fish_smooth_cy
                )
            fish_cy = int(self._fish_smooth_cy)

            bar_h   = max(bar[3], 1)
            bar_top = bar[1]
            fish_in_bar = (fish_cy - bar_top) / bar_h

            # PD 计算
            error = TARGET_FIB - fish_in_bar  # >0 需要上升, <0 需要下降
            error_clamp = max(-2.0, min(2.0, error))

            # hold = 基准 + 位置修正 + 速度阻尼
            # vel>0(下坠)→加hold减速; vel<0(上升)→减hold防过冲
            hold = BASE_HOLD + error_clamp * KP + vel * KD
            hold = max(MIN_HOLD, min(hold, MAX_HOLD))

            # 记录上次状态供后备使用
            self._last_hold = hold
            self._last_fish_cy = fish_cy

            fname = (self._current_fish_name.replace("fish_", "")
                     if self._current_fish_name else "?")

            if hold >= MIN_HOLD + 0.001:
                self.input.mouse_down()
                time.sleep(hold)
                self.input.mouse_up()
                log.info(
                    f"  ● [{fname}] fib={fish_in_bar:.2f} "
                    f"v={vel:+.0f} → 按 {hold*1000:.0f}ms"
                )
                return True
            else:
                self.input.mouse_up()
                log.info(
                    f"  ○ [{fname}] fib={fish_in_bar:.2f} "
                    f"v={vel:+.0f} → 释放"
                )
                return False

        # ── 后备: 仅鱼或仅条 → 使用上次 hold 衰减至基准 ──
        fallback = self._last_hold
        if fallback is None:
            fallback = BASE_HOLD

        # 衰减: 没有完整检测时, 逐帧趋向基准悬停
        fallback = 0.6 * fallback + 0.4 * BASE_HOLD
        self._last_hold = fallback

        if fish is not None:
            fish_cy = fish[1] + fish[3] // 2
            self._last_fish_cy = fish_cy
            # 鱼在上方(需要按)或下方(需要松)
            if sr is not None:
                mid_y = sr[1] + sr[3] // 2
            elif config.DETECT_ROI:
                mid_y = config.DETECT_ROI[1] + config.DETECT_ROI[3] // 2
            else:
                mid_y = fish_cy
            if fish_cy < mid_y:
                h = min(fallback * 1.5, MAX_HOLD)
                self.input.mouse_down()
                time.sleep(h)
                self.input.mouse_up()
                log.info(
                    f"  (仅鱼) Y={fish_cy} v={vel:+.0f}"
                    f" → 按 {h*1000:.0f}ms"
                )
                return True
            else:
                self.input.mouse_up()
                return False

        elif bar is not None:
            bar_cy = bar[1] + bar[3] // 2
            # 用上次鱼位置估算 fish_in_bar
            if self._last_fish_cy is not None:
                est_fib = (self._last_fish_cy - bar[1]) / max(bar[3], 1)
                error = TARGET_FIB - est_fib
                error_clamp = max(-2.0, min(2.0, error))
                hold = BASE_HOLD + error_clamp * KP + vel * KD
                hold = max(MIN_HOLD, min(hold, MAX_HOLD))
            else:
                hold = fallback
            self.input.mouse_down()
            time.sleep(hold)
            self.input.mouse_up()
            log.info(
                f"  (仅条) Y={bar_cy} v={vel:+.0f}"
                f" → 按 {hold*1000:.0f}ms"
            )
            return True

        return False

    # ══════════════════════════════════════════════════════
    #  主循环 (在后台线程中运行)
    # ══════════════════════════════════════════════════════

    def run(self):
        """主钓鱼循环 — 由 GUI 在后台线程启动"""
        log.info("钓鱼线程已启动")

        while self.running:
            try:
                if config.IL_RECORD:
                    # ★ 录制模式: 用户手动操作, 程序等待小游戏UI出现
                    self.state = "录制: 等待小游戏"
                    log.info("[IL] 请手动抛竿→等待→提竿, 程序在等待小游戏出现...")
                    if not self._wait_for_minigame_ui():
                        break
                else:
                    self._cast_rod()
                    if not self.running:
                        break

                if not self.running:
                    break

                result = self._fishing_minigame()

                if result is None:
                    time.sleep(config.POST_CATCH_DELAY)
                    log.info("─" * 40)
                    continue

                self.fish_count += 1
                tag = "成功 ✅" if result else "完成"
                log.info(f"[🎣 结果] 第 {self.fish_count} 次钓鱼 — {tag}")
                log.info("─" * 40)

                self.state = "等待下一轮"
                time.sleep(config.POST_CATCH_DELAY)

            except Exception as e:
                log.error(f"运行异常: {e}")
                if not config.IL_RECORD:
                    self.input.safe_release()
                time.sleep(2)

        if not config.IL_RECORD:
            self.input.safe_release()
        self.state = "已停止"
        log.info("钓鱼线程已停止")
        try:
            cv2.destroyWindow("Debug Overlay")
        except Exception:
            pass
