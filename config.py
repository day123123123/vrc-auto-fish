"""
全局配置模块
============
所有可调参数集中管理。
"""

import os
import sys

# ═══════════════════════════════════════════════════════════
#  路径
# ═══════════════════════════════════════════════════════════
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else BASE_DIR
PATCH_DIR = os.path.join(_APP_DIR, "patch")
DEBUG_DIR = os.path.join(_APP_DIR, "debug")
SETTINGS_FILE = os.path.join(_APP_DIR, "settings.json")
LANGUAGE = "auto"


def _resource_candidates(relative_path: str):
    norm_rel = relative_path.replace("/", os.sep).replace("\\", os.sep)
    candidates = [
        os.path.join(PATCH_DIR, norm_rel),
        os.path.join(_APP_DIR, norm_rel),
        os.path.join(BASE_DIR, norm_rel),
    ]
    seen = set()
    for path in candidates:
        norm = os.path.normpath(path)
        if norm in seen:
            continue
        seen.add(norm)
        yield norm


def resolve_resource_path(relative_path: str, expect_dir: bool = False) -> str:
    exists = os.path.isdir if expect_dir else os.path.isfile
    for candidate in _resource_candidates(relative_path):
        if exists(candidate):
            return candidate
    if expect_dir:
        return os.path.join(BASE_DIR, relative_path)
    return os.path.join(BASE_DIR, relative_path)


IMG_DIR = resolve_resource_path("img", expect_dir=True)

# ═══════════════════════════════════════════════════════════
#  VRChat 窗口
# ═══════════════════════════════════════════════════════════
WINDOW_TITLE = "VRChat"

# ═══════════════════════════════════════════════════════════
#  鼠标输入方式
#  ★ U6 (Unity 6) 之后 VRChat 不再响应 PostMessage 投递的鼠标消息,
#    必须使用 SendInput 注入系统级真实鼠标事件。
# ═══════════════════════════════════════════════════════════
# "sendinput"   = 真实输入注入 (U6 及以后必需)
#                 需要 VRChat 处于前台窗口, 会占用真实光标 (自动停在窗口中心)
# "postmessage" = 旧版后台消息投递 (不抢焦点不占光标, U6 版本已失效)
INPUT_MODE           = "sendinput"
INPUT_FOCUS_DELAY    = 0.08       # 切前台后等待游戏激活的缓冲 (秒)
INPUT_CLICK_HOLD_S   = 0.06       # click() 的按下时长 (秒)
INPUT_RESTORE_CURSOR = True       # True=停止钓鱼后把光标还原到接管前的位置
# ★ True = 用 SetCursorPos 静默搬移光标 (不产生 raw input 事件)
#        VRChat 桌面模式的鼠标视角不会把静默搬移算成鼠标移动, 避免大幅甩视角
#   False = 旧行为: SendInput 绝对移动 (跨屏搬移会被算成约 180° 的视角转动)
INPUT_QUIET_CURSOR_MOVE = True

# ═══════════════════════════════════════════════════════════
#  快捷键 (VRChat 内也可用)
# ═══════════════════════════════════════════════════════════
HOTKEY_TOGGLE = "F9"
HOTKEY_STOP   = "F10"
HOTKEY_DEBUG  = "F11"

# ═══════════════════════════════════════════════════════════
#  时间参数（秒）
# ═══════════════════════════════════════════════════════════
CAST_DELAY          = 1.5         # 抛竿后等待
BITE_FORCE_HOOK     = 18.0        # 提竿时间: N秒后提竿
HOOK_PRE_DELAY      = 0.1         # 提竿前延迟 (★ 0.2→0.1)
HOOK_POST_DELAY     = 0.4         # 提竿后等待 UI 出现 (★ 0.3→0.4)
VERIFY_FRAMES       = 5           # 主循环连续丢失达到 N 帧后直接判定结束
GAME_LOOP_INTERVAL  = 0.005       # 小游戏循环间隔 (60FPS游戏, 尽量快)
CAPTURE_FPS_LIMIT   = 0           # 截图线程最大帧率上限 (0=不限制, 更接近旧版同步手感)
FULL_RATE_WAIT_HOOK = False       # True=等待提竿阶段也不做 50ms 节流, 尽量满帧检测
SYNC_PD_MODE       = False        # True=旧版模式(使用旧版参数), False=异步流水线模式
SHOW_DEBUG             = True     # 是否显示debug窗口 (关闭可提升性能)
DEBUG_OVERLAY_INTERVAL = 0.033    # debug窗口最小刷新间隔(秒) ~30FPS
DEBUG_OVERLAY_MAX_W    = 1920  # debug窗口最大宽度(像素)
DEBUG_OVERLAY_MAX_H    = 1080      # debug窗口最大高度(像素)
FISH_LOST_LIMIT     = 120          # 连续N帧鱼消失 → 游戏可能结束
SINGLE_OBJ_TIMEOUT  = 5.0         # ★ 鱼或条单独消失超过N秒 → 判定失败收杆 (3→5)
OBJ_MIN_COUNT       = 1            # ★ 每帧至少检测到N个对象才继续 (2→1, 只要鱼或条任一即可)
OBJ_GONE_LIMIT      = 80           # ★ 连续N帧对象不足 → 游戏结束 (25→80)
POST_CATCH_DELAY    = 3.0         # 钓鱼结束/失败后等待(秒), 收杆→等待→摇头→抛竿
SHAKE_HEAD_TIME     = 0.02        # 摇头每段按住时长(秒)
INITIAL_PRESS_TIME  = 0.2         # 开局按压时长(秒)
SUCCESS_PROGRESS    = 0.55        # 进度条 > 此值判定钓鱼成功 (0~1)
SKIP_SUCCESS_CHECK  = True        # True=不检查成功/失败,总是点击收杆
ANTI_STUCK_MODE     = "jump"      # "shake"=摇头, "jump"=跳跃, "none"=关闭 (既不摇头也不跳跃)
MINIGAME_TIMEOUT    = 120.0       # 小游戏最长持续时间 (秒), 超过强制结束
UI_CHECK_FRAMES     = 30           # 每N帧检查一次轨道是否还在 (15→30, 降低检查频率)
UI_GONE_LIMIT       = 4            # 连续N次轨道检查失败 → 判定游戏结束 (2→4)

# ═══════════════════════════════════════════════════════════
#  模板匹配置信度阈值
#  ★ ROI 框选后搜索范围极小, 误匹配风险很低, 阈值可大幅放宽
#    真实鱼: 0.61~0.82    真实白条: 0.84~0.89    真实轨道: 0.51~0.57
# ═══════════════════════════════════════════════════════════
THRESH_FISH     = 0.35           # ★ 0.50→0.35 (ROI内极少误匹配, 大幅放宽)
THRESH_BAR      = 0.40           # ★ 0.62→0.40 (实测0.84+, 大幅放宽防漏检)
THRESH_TRACK    = 0.35           # ★ 0.48→0.35 (ROI内无干扰, 大幅放宽)

# ═══════════════════════════════════════════════════════════
#  多尺度匹配
# ═══════════════════════════════════════════════════════════
# 通用缩放 (轨道检测)
MATCH_SCALES = [0.7, 1.0, 1.5, 2.0, 3.0]
# 白条缩放
BAR_SCALES   = [0.7, 1.0, 1.5, 2.0, 3.0]
# ★ 游戏内鱼图标的大致像素大小 (用户可在GUI调节)
#   系统会根据 模板尺寸 / FISH_GAME_SIZE 自动计算最佳缩放比例
#   例: 模板38px, 游戏鱼20px → 最佳scale=1.9, 搜索范围 1.1~2.7
FISH_GAME_SIZE = 30

# ═══════════════════════════════════════════════════════════
#  小游戏控制
# ═══════════════════════════════════════════════════════════
# ── PD 控制器参数 (适配高惯性钓鱼) ──
DEAD_ZONE       = 15              # 固定死区(px), 备用 (动态死区优先)
DEAD_ZONE_RATIO = 0.35            # 动态死区: 白条高度 × 此比例 (鱼在白条中心此范围内=居中)
MAINTAIN_TAP_S  = 0.010           # 死区内维持性短按时长(秒), 抵消重力防坠底
HOLD_MIN_S      = 0.005           # 抗重力基准 (秒) — 越小下降越快
HOLD_MAX_S      = 0.100           # 单次最长按住 (秒)
HOLD_GAIN       = 0.040           # 位置增益: 误差×增益=额外按住时长
VELOCITY_SMOOTH = 0.5             # 速度低通滤波系数 (0~1, 越大越平滑)
PREDICT_AHEAD   = 0.5             # 前瞻时间 (秒) — 高惯性系统需要更远的预判
SPEED_DAMPING   = 0.00025         # 速度阻尼: 下坠快时加按住, 上升快时减按住
MAX_FISH_BAR_DIST = 300           # ★ 鱼和白条中心最大合理距离(px), 超过视为误检
REGION_UP         = 300           # 白条锁定后, 向上搜索像素数
REGION_DOWN       = 400           # 白条锁定后, 向下搜索像素数
REGION_X          = 100           # 白条锁定后, 左右搜索像素数 (中心±N)
USE_OSC           = True           # True=OSC输入(不占鼠标), False=PostMessage输入
DETECT_ROI        = None           # 玩家框选的检测区域 [x, y, w, h], None=全屏搜索

# ═══════════════════════════════════════════════════════════
#  YOLO 目标检测 (替代模板匹配, 需训练后使用)
# ═══════════════════════════════════════════════════════════
USE_YOLO      = True
YOLO_MODEL    = resolve_resource_path("yolo/runs/fish_detect/weights/best.pt")


def resolve_ncnn_model_path(model_path: str | None = None) -> str:
    source = model_path or YOLO_MODEL
    if os.path.isdir(source):
        return source
    root, ext = os.path.splitext(source)
    if ext.lower() == ".pt":
        return f"{root}_ncnn_model"
    return f"{source}_ncnn_model"


YOLO_MODEL_NCNN = resolve_ncnn_model_path(YOLO_MODEL)
YOLO_CONF     = 0.30              # YOLO 检测置信度阈值
YOLO_RAW_DEBUG = True             # True=打印排查用 YOLO 原始检测日志（会刷屏）
YOLO_FISH_STABLE_FRAMES = 3       # 鱼类别需连续命中N帧才切换，抑制颜色抖动
YOLO_WHITELIST_CONFIRM_FRAMES = 6 # 非白名单鱼需连续命中N帧才放弃，避免单帧误判
YOLO_DEVICE   = "auto"            # "auto" 自动选择 / "cpu" 强制 CPU / "cuda" 强制 CUDA / "ncnn" 强制 NCNN
YOLO_COLLECT  = False             # True=钓鱼时自动保存截图用于训练
TRACK_MIN_ANGLE   = 3.0           # 轨道倾斜角度阈值(度), 超过此值启用旋转补偿
TRACK_MAX_ANGLE   = 45.0          # 轨道最大合理角度(度), 超过视为误检(如把海平线当轨道)

# ═══════════════════════════════════════════════════════════
#  行为克隆 (录制你的操作 → 训练模型 → 替代PD控制器)
# ═══════════════════════════════════════════════════════════
IL_RECORD       = False           # True=录制模式: 检测位置但不控制鼠标, 记录你的操作
IL_USE_MODEL    = False           # True=用训练好的模型控制, False=PD控制器
IL_MODEL_PATH   = resolve_resource_path("imitation/policy.pt")
IL_DATA_DIR     = os.path.join(BASE_DIR, "imitation", "data")
IL_HISTORY_LEN  = 10              # 输入历史帧数 (捕捉鱼的运动模式)
IL_PRESS_THRESH = 0.50            # 按住阈值: 模型概率 > 此值才按住 (默认0.5, 按太久就调高)

# ═══════════════════════════════════════════════════════════
#  模板文件映射
# ═══════════════════════════════════════════════════════════
TEMPLATE_FILES = {
    "track":        "finshblock.png",
    "bar":          "block.png",
    "fish_white":   "wFish.png",
    "fish_green":   "greenFish.png",
    "fish_golden":  "goldenFish.png",
    "fish_relic":   "copperFish.png",
    "fish_blue":    "blueFish.png",
    "fish_purple":  "purpleFish.png",
    "fish_black":   "blackFish.png",
    "prog_full":    "full.png",
    "prog_empty":   "null.png",
}

# 所有鱼模板 key 列表（find_fish 使用）
FISH_KEYS = [
    "fish_white", "fish_green", "fish_golden",
    "fish_relic", "fish_blue", "fish_purple", "fish_black",
    "fish_pink", "fish_red", "fish_rainbow",
]

# ═══════════════════════════════════════════════════════════
#  钓鱼白名单 (True=要钓, False=放弃)
# ═══════════════════════════════════════════════════════════
FISH_WHITELIST = {
    "fish_black":   True,   # 黑鱼
    "fish_white":   True,   # 白鱼
    "fish_relic":   True,   # 遗物
    "fish_green":   True,   # 绿鱼
    "fish_clover":  True,   # 四叶草
    "fish_question": True,  # 问号鱼
    "fish_blue":    True,   # 蓝鱼
    "fish_purple":  True,   # 紫鱼
    "fish_pink":    True,   # 粉鱼
    "fish_red":     True,   # 红鱼
    "fish_rainbow": True,   # 彩鱼
}

# 兼容旧设置和旧模型里仍然使用的鱼类 key。
LEGACY_FISH_KEY_ALIASES = {
    "fish_generic": "fish_black",
    "fish_copper": "fish_relic",
    "fish_teal": "fish_clover",
}

YOLO_DEVICE_ALIASES = {
    "gpu": "cuda",
    "ncnn-cpu": "ncnn",
    "vulkan": "ncnn",
}


def normalize_yolo_device(device: str | None) -> str:
    if not isinstance(device, str):
        return "auto"
    normalized = YOLO_DEVICE_ALIASES.get(device.lower(), device.lower())
    if normalized in ("auto", "cpu", "cuda", "ncnn"):
        return normalized
    return "auto"
