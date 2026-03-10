"""
小游戏检测服务
==============
封装同步/异步小游戏帧检测逻辑。
"""

from dataclasses import dataclass
import queue

import config


@dataclass
class DetectionResult:
    """统一模板匹配与 YOLO 的单帧检测输出。"""

    fish: tuple | None = None
    bar: tuple | None = None
    progress: tuple | None = None
    matched_key: str | None = None
    bar_scale: float = 1.0
    track_det: tuple | None = None
    track_cache: tuple | None = None

    def as_tuple(self):
        return (
            self.fish,
            self.bar,
            self.progress,
            self.matched_key,
            self.bar_scale,
            self.track_det,
            self.track_cache,
        )


class MinigameDetectionService:
    """统一小游戏检测线程与同步检测实现。"""

    def __init__(self, detector, pd_controller, yolo_getter, bar_locked_cx_getter):
        self.detector = detector
        self.pd = pd_controller
        self._get_yolo = yolo_getter
        self._get_bar_locked_cx = bar_locked_cx_getter

    def detect_worker_loop(self, frame_q: queue.Queue,
                           result_q: queue.Queue,
                           stop_evt,
                           shared_params: dict,
                           params_lock,
                           use_yolo: bool):
        """检测线程主循环。"""
        local_track = None
        local_frame = 0
        while not stop_evt.is_set():
            try:
                raw, scr = frame_q.get(timeout=0.1)
            except queue.Empty:
                continue

            local_frame += 1
            try:
                with params_lock:
                    params = dict(shared_params)
                frame_result = self.detect_frame(
                        scr, use_yolo,
                        params["search_region"],
                        params["bar_search_region"],
                        params["locked_fish_key"],
                        params["locked_fish_scales"],
                        params["locked_bar_scales"],
                        params["frame"],
                        params["yolo_roi"],
                        params["skip_success"],
                        track_cache=local_track,
                    )
                local_track = frame_result.track_cache
                det = (
                    raw, scr,
                    frame_result.fish, frame_result.bar, frame_result.progress,
                    frame_result.matched_key, frame_result.bar_scale,
                    frame_result.track_det,
                )
                try:
                    result_q.put_nowait(det)
                except queue.Full:
                    try:
                        result_q.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        result_q.put_nowait(det)
                    except queue.Full:
                        pass
            except Exception:
                pass

    def detect_frame(self, scr, use_yolo: bool,
                     search_region, bar_search_region,
                     locked_fish_key, locked_fish_scales,
                     locked_bar_scales, frame_no: int,
                     yolo_roi, skip_success: bool,
                     track_cache=None) -> DetectionResult:
        """单帧检测，返回统一结构化结果。"""
        fish = bar = progress = None
        matched_key = None
        bar_scale = 1.0
        track_det = None

        if use_yolo:
            yolo = self._get_yolo()
            if yolo is not None:
                det = yolo.detect(scr, roi=yolo_roi)
                fish = det.get("fish")
                bar = det.get("bar")
                track_det = det.get("track")
                if not skip_success:
                    progress = det.get("progress")
            return DetectionResult(
                fish=fish,
                bar=bar,
                progress=progress,
                matched_key=matched_key,
                bar_scale=bar_scale,
                track_det=track_det,
                track_cache=track_cache,
            )

        fish_sr = self._build_fish_search_region(search_region)
        fg, fox, foy = self.detector.prepare_gray(scr, fish_sr, upload_gpu=True)
        bg, box, boy = self.detector.prepare_gray(
            scr, bar_search_region, upload_gpu=True
        )
        has_cuda = self.detector._use_cuda

        if locked_fish_key:
            fish = self.detector.find_multiscale(
                scr, locked_fish_key, config.THRESH_FISH, fish_sr,
                scales=locked_fish_scales, pre_gray=fg, pre_offset=(fox, foy)
            )
            if fish is None and fish_sr is not search_region:
                fish = self.detector.find_multiscale(
                    scr, locked_fish_key, config.THRESH_FISH, search_region,
                    scales=locked_fish_scales
                )
            matched_key = locked_fish_key if fish else None
        else:
            if has_cuda:
                fish = self.detector.find_fish(
                    scr, config.THRESH_FISH, fish_sr,
                    pre_gray=fg, pre_offset=(fox, foy)
                )
            else:
                n_keys = len(config.FISH_KEYS)
                group_size = 2
                group_count = (n_keys + group_size - 1) // group_size
                group_idx = frame_no % group_count
                start_idx = group_idx * group_size
                keys = config.FISH_KEYS[start_idx:start_idx + group_size]
                fish = self.detector.find_fish(
                    scr, config.THRESH_FISH, fish_sr,
                    pre_gray=fg, pre_offset=(fox, foy), keys=keys
                )
            matched_key = self.detector._last_best_key if fish else None

        bar_scales = locked_bar_scales or config.BAR_SCALES
        bar = self.detector.find_multiscale(
            scr, "bar", config.THRESH_BAR, bar_search_region,
            scales=bar_scales, pre_gray=bg, pre_offset=(box, boy)
        )
        bar_scale = self.detector._last_scale

        track_interval = max(config.UI_CHECK_FRAMES // 2, 5)
        if frame_no % track_interval == 0:
            track_cache = self.detector.find_multiscale(scr, "track", 0.50)
        track_det = track_cache

        return DetectionResult(
            fish=fish,
            bar=bar,
            progress=progress,
            matched_key=matched_key,
            bar_scale=bar_scale,
            track_det=track_det,
            track_cache=track_cache,
        )

    def detect_once(self, scr, use_yolo: bool,
                    search_region, bar_search_region,
                    locked_fish_key, locked_fish_scales,
                    locked_bar_scales, frame_no: int,
                    yolo_roi, skip_success: bool,
                    track_cache=None):
        """兼容旧接口，返回元组结果。"""
        return self.detect_frame(
            scr, use_yolo,
            search_region, bar_search_region,
            locked_fish_key, locked_fish_scales,
            locked_bar_scales, frame_no,
            yolo_roi, skip_success,
            track_cache=track_cache,
        ).as_tuple()

    def _build_fish_search_region(self, search_region):
        """基于轨道与鱼平滑位置收窄鱼搜索范围。"""
        fish_x_half = max(config.REGION_X * 2, 80)
        fish_sr = search_region
        bar_locked_cx = self._get_bar_locked_cx()
        if search_region and bar_locked_cx is not None:
            sr_x, sr_y, sr_w, sr_h = search_region
            nx = max(sr_x, bar_locked_cx - fish_x_half)
            nx2 = min(sr_x + sr_w, bar_locked_cx + fish_x_half)
            if nx2 - nx > 10:
                fish_sr = (nx, sr_y, nx2 - nx, sr_h)

        if self.pd.fish_smooth_cy is not None and fish_sr:
            sx, sy, sw, sh = fish_sr
            ny = max(sy, int(self.pd.fish_smooth_cy) - 150)
            ny2 = min(sy + sh, int(self.pd.fish_smooth_cy) + 150)
            if ny2 - ny > 30:
                fish_sr = (sx, ny, sw, ny2 - ny)
        return fish_sr
