"""
小游戏运行时状态
================
封装钓鱼小游戏过程中的临时状态与流水线资源。
"""

from dataclasses import dataclass, field
from typing import Any
import queue
import threading
import time


@dataclass
class MinigameRuntime:
    """一局小游戏运行中的可变状态。"""

    no_detect: int = 0
    fish_lost: int = 0
    frame: int = 0
    hold_count: int = 0
    skip_fish: bool = False
    fish_id_saved: bool = False
    had_good_detection: bool = False
    obj_gone_count: int = 0
    fish_gone_since: float | None = None
    bar_gone_since: float | None = None
    game_active: bool = False
    hook_time: float = field(default_factory=time.time)
    hook_timeout_retry: bool = False
    last_progress_sr: tuple[int, int, int, int] | None = None
    last_green: float = 0.0
    prev_green: float = 0.0
    minigame_start: float = field(default_factory=time.time)
    progress_skip_frames: int = 20
    fish_name_pending: str = ""
    fish_name_pending_frames: int = 0
    blocked_fish_pending: str = ""
    blocked_fish_pending_frames: int = 0


@dataclass
class DetectionContext:
    """检测与搜索区域相关的运行时上下文。"""

    use_yolo: bool
    skip_success_check: bool
    search_region: tuple[int, int, int, int] | None = None
    bar_search_region: tuple[int, int, int, int] | None = None
    regions_locked: bool = False
    locked_fish_key: str | None = None
    locked_fish_scales: list[float] | None = None
    locked_bar_scales: list[float] | None = None
    bar_x_half: int = 0
    fish_x_half: int = 0
    hook_detect_timeout: float = 1.5
    sync_track_cache: Any = None
    width: int = 0
    height: int = 0


@dataclass
class PipelineContext:
    """异步截图/检测流水线资源。"""

    sync_pd_mode: bool
    frame_q: queue.Queue | None = None
    result_q: queue.Queue | None = None
    stop_evt: Any = None
    shared_params: dict[str, Any] | None = None
    params_lock: Any = None
    capture_thread: threading.Thread | None = None
    detect_thread: threading.Thread | None = None
