"""
调试叠加层
==========
负责 FPS 统计、调试图像绘制与独立窗口显示。
"""

import threading
import time

import cv2

import config
from utils.i18n import t


class DebugOverlay:
    """独立管理 debug overlay 的绘制与显示线程。"""

    def __init__(self):
        self._last_overlay_time = 0.0
        self._fps = 0.0
        self._frame_times = []
        self._debug_frame = None
        self._debug_lock = threading.Lock()
        self._debug_thread = None
        self._debug_close_requested = False
        self._running = False

    @property
    def fps(self) -> float:
        return self._fps

    def tick_fps(self):
        """更新调试窗口 FPS 统计。"""
        now_t = time.time()
        self._frame_times.append(now_t)
        if len(self._frame_times) > 20:
            self._frame_times = self._frame_times[-20:]
        if len(self._frame_times) >= 2:
            dt = self._frame_times[-1] - self._frame_times[0]
            if dt > 0:
                self._fps = (len(self._frame_times) - 1) / dt

    def show(self, screen, fish=None, bar=None, search_region=None,
             bar_search_region=None, progress=None, prog_hook=None, status_text="",
             *, state="", running=False, need_rotation=False,
             track_angle=0.0, current_fish_name="",
             fish_display=None, bar_velocity=0.0):
        """绘制并异步显示调试画面。"""
        if not config.SHOW_DEBUG:
            return

        fish_display = fish_display or {}
        self._running = running
        self._debug_close_requested = False

        now = time.time()
        if now - self._last_overlay_time < config.DEBUG_OVERLAY_INTERVAL:
            return
        self._last_overlay_time = now

        roi = config.DETECT_ROI
        ox, oy = 0, 0
        if roi:
            rx, ry, rw, rh = roi
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
        scale = min(max_w / w, max_h / h, 1.0)

        if scale < 1.0:
            debug = cv2.resize(
                screen, (int(w * scale), int(h * scale)),
                interpolation=cv2.INTER_NEAREST,
            )
        else:
            debug = screen.copy()
            scale = 1.0

        def sx(v):
            return int((v - ox) * scale)

        def sy(v):
            return int((v - oy) * scale)

        y_txt = 22
        font_scale = 0.55
        debug_w = debug.shape[1]
        fps_text = f"{self._fps:.1f} FPS"
        fps_color = (
            (0, 255, 0) if self._fps >= 10
            else (0, 255, 255) if self._fps >= 5
            else (0, 0, 255)
        )
        cv2.putText(
            debug, fps_text, (debug_w - 120, 22),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, fps_color, 2
        )

        if status_text:
            cv2.putText(
                debug, status_text, (8, y_txt),
                cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 255, 255), 1
            )
            y_txt += 22

        if need_rotation:
            cv2.putText(
                debug, t("debug.rotation", angle=-track_angle), (8, y_txt),
                cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 200, 255), 1
            )
            y_txt += 20

        if fish is not None and bar is not None:
            fish_cy = fish[1] + fish[3] // 2
            bar_cy = bar[1] + bar[3] // 2
            diff = bar_cy - fish_cy
            if diff > config.DEAD_ZONE:
                label = t("debug.barBelow", diff=diff)
                label_color = (0, 100, 255)
            elif diff < -config.DEAD_ZONE:
                label = t("debug.barAbove", diff=diff)
                label_color = (255, 200, 0)
            else:
                label = t("debug.deadZone", diff=diff)
                label_color = (0, 255, 0)
            cv2.putText(
                debug, label, (8, y_txt),
                cv2.FONT_HERSHEY_SIMPLEX, font_scale, label_color, 1
            )
            y_txt += 20
        elif fish is None and bar is None and state == "bot.state.minigame":
            cv2.putText(
                debug, t("debug.noFishBar"), (8, y_txt),
                cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 255), 1
            )
            y_txt += 20

        if abs(bar_velocity) > 0.5:
            cv2.putText(
                debug, f"v={bar_velocity:+.0f} px/s", (8, y_txt),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1
            )
            y_txt += 18

        if search_region:
            rx, ry, rw, rh = [int(v) for v in search_region]
            cv2.rectangle(
                debug, (sx(rx), sy(ry)), (sx(rx + rw), sy(ry + rh)),
                (128, 128, 128), 1
            )
        if bar_search_region:
            bx, by, bw, bh = [int(v) for v in bar_search_region]
            cv2.rectangle(
                debug, (sx(bx), sy(by)), (sx(bx + bw), sy(by + bh)),
                (128, 200, 200), 1
            )

        if fish is not None:
            fx, fy, fw, fh = fish[:4]
            fish_cy = fy + fh // 2
            fname, fcolor = fish_display.get(current_fish_name, ("?", (0, 255, 0)))
            cv2.rectangle(debug, (sx(fx), sy(fy)), (sx(fx + fw), sy(fy + fh)), fcolor, 2)
            cv2.putText(
                debug, f"{fname} Y={fish_cy}",
                (sx(fx + fw) + 4, sy(fish_cy)),
                cv2.FONT_HERSHEY_SIMPLEX, font_scale, fcolor, 1
            )
            cv2.line(debug, (sx(fx), sy(fish_cy)), (sx(fx + fw), sy(fish_cy)), fcolor, 1)

        if bar is not None:
            bx, by, bw, bh = bar[:4]
            bar_cy = by + bh // 2
            cv2.rectangle(debug, (sx(bx), sy(by)), (sx(bx + bw), sy(by + bh)), (255, 100, 0), 2)
            cv2.putText(
                debug, t("debug.barY", y=bar_cy), (max(0, sx(bx) - 90), sy(bar_cy)),
                cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 100, 0), 1
            )
            cv2.line(debug, (sx(bx), sy(bar_cy)), (sx(bx + bw), sy(bar_cy)), (255, 100, 0), 1)

        if progress is not None:
            px, py, pw, ph = progress[:4]
            cv2.rectangle(
                debug, (sx(px), sy(py)), (sx(px + pw), sy(py + ph)),
                (0, 220, 180), 2
            )
            cv2.putText(
                debug, t("debug.progress"), (sx(px), sy(py) - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 220, 180), 1
            )

        if prog_hook is not None:
            hx, hy, hw, hh = prog_hook[:4]
            hook_cx = hx + hw // 2
            hook_cy = hy + hh // 2
            draw_w = max(10, int(hw * scale))
            draw_h = max(10, int(hh * scale))
            left = sx(hook_cx) - draw_w // 2
            top = sy(hook_cy) - draw_h // 2
            right = left + draw_w
            bottom = top + draw_h
            color = (255, 0, 255)
            cv2.rectangle(debug, (left, top), (right, bottom), color, 2)
            cv2.drawMarker(
                debug,
                (sx(hook_cx), sy(hook_cy)),
                color,
                markerType=cv2.MARKER_CROSS,
                markerSize=12,
                thickness=2,
            )
            cv2.putText(
                debug, t("debug.hook"), (left, top - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1
            )

        if fish is not None and bar is not None:
            fish_cy = fish[1] + fish[3] // 2
            bar_cy = bar[1] + bar[3] // 2
            cx = (fish[0] + bar[0]) // 2
            diff = bar_cy - fish_cy
            color = (0, 0, 255) if abs(diff) > 50 else (0, 255, 255)
            cv2.arrowedLine(
                debug, (sx(cx), sy(bar_cy)), (sx(cx), sy(fish_cy)),
                color, 1, tipLength=0.15
            )
            cv2.putText(
                debug, f"d={diff:+d}", (sx(cx) + 6, sy((fish_cy + bar_cy) // 2)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1
            )

        with self._debug_lock:
            self._debug_frame = debug

        if self._debug_thread is None or not self._debug_thread.is_alive():
            self._debug_thread = threading.Thread(target=self._display_loop, daemon=True)
            self._debug_thread.start()

    def shutdown(self):
        """请求 debug 线程自行关闭窗口，避免阻塞 GUI 主线程。"""
        with self._debug_lock:
            self._debug_frame = None
        self._debug_close_requested = True
        self._running = False

    def _display_loop(self):
        while True:
            frame = None
            with self._debug_lock:
                if self._debug_frame is not None:
                    frame = self._debug_frame
                    self._debug_frame = None
                close_requested = self._debug_close_requested
                running = self._running

            if close_requested and frame is None:
                break
            if not running and frame is None:
                break
            if frame is not None:
                try:
                    cv2.imshow("Debug Overlay", frame)
                except Exception:
                    break
            key = cv2.waitKey(1)
            if key == 27:
                break
        try:
            cv2.destroyWindow("Debug Overlay")
        except Exception:
            pass
