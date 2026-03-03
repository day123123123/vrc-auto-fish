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
IMG_DIR = os.path.join(BASE_DIR, "img")
_APP_DIR = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else BASE_DIR
DEBUG_DIR = os.path.join(_APP_DIR, "debug")
SETTINGS_FILE = os.path.join(_APP_DIR, "settings.json")

# ═══════════════════════════════════════════════════════════
#  VRChat 窗口
# ═══════════════════════════════════════════════════════════
WINDOW_TITLE = "VRChat"

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
BITE_TIMEOUT        = 60.0        # 最长等鱼时间 (绝对上限)
BITE_FORCE_HOOK     = 18.0        # N秒无咬钩 → 强制提竿进入小游戏 (防漏检)
BITE_CHECK_INTERVAL = 0.15        # 咬钩检测间隔
MIN_BITE_WAIT       = 3.0         # 最少等待N秒才开始检测咬钩（防止误检）
COLOR_BITE_WAIT     = 6.0         # N秒后才启用颜色检测（模板优先）
COLOR_BITE_PIXELS   = 500         # 颜色检测最少像素数（越高越严格）
HOOK_PRE_DELAY      = 0.1         # 提竿前延迟 (★ 0.2→0.1)
HOOK_POST_DELAY     = 0.4         # 提竿后等待 UI 出现 (★ 0.3→0.4)
VERIFY_TIMEOUT      = 3.0         # 提竿后验证小游戏出现的超时(秒)
VERIFY_CONSECUTIVE  = 1           # ★ 累计N帧检测到白条+轨道即确认
GAME_LOOP_INTERVAL  = 0.005       # 小游戏循环间隔 (60FPS游戏, 尽量快)
SHOW_DEBUG             = True     # 是否显示debug窗口 (关闭可提升性能)
DEBUG_OVERLAY_INTERVAL = 0.033    # debug窗口最小刷新间隔(秒) ~30FPS
DEBUG_OVERLAY_MAX_W    = 1920  # debug窗口最大宽度(像素)
DEBUG_OVERLAY_MAX_H    = 1080      # debug窗口最大高度(像素)
TRACK_LOST_LIMIT    = 60           # 连续N帧鱼+条都没了 → 游戏结束 (15→60, 约3-4秒容忍)
FISH_LOST_LIMIT     = 120          # 连续N帧鱼消失 → 游戏可能结束
SINGLE_OBJ_TIMEOUT  = 5.0         # ★ 鱼或条单独消失超过N秒 → 判定失败收杆 (3→5)
OBJ_MIN_COUNT       = 1            # ★ 每帧至少检测到N个对象才继续 (2→1, 只要鱼或条任一即可)
OBJ_GONE_LIMIT      = 80           # ★ 连续N帧对象不足 → 游戏结束 (25→80)
POST_CATCH_DELAY    = 3.0         # 钓鱼结束/失败后等待(秒), 收杆→等待→摇头→抛竿
SHAKE_HEAD_TIME     = 0.02        # 摇头每段按住时长(秒)
INITIAL_PRESS_TIME  = 0.2         # 开局按压时长(秒)
SUCCESS_PROGRESS    = 0.55        # 进度条 > 此值判定钓鱼成功 (0~1)
MINIGAME_TIMEOUT    = 120.0       # 小游戏最长持续时间 (秒), 超过强制结束
UI_CHECK_FRAMES     = 30           # 每N帧检查一次轨道是否还在 (15→30, 降低检查频率)
UI_GONE_LIMIT       = 4            # 连续N次轨道检查失败 → 判定游戏结束 (2→4)

# ═══════════════════════════════════════════════════════════
#  模板匹配置信度阈值
#  ★ ROI 框选后搜索范围极小, 误匹配风险很低, 阈值可大幅放宽
#    真实鱼: 0.61~0.82    真实白条: 0.84~0.89    真实轨道: 0.51~0.57
# ═══════════════════════════════════════════════════════════
THRESH_BITE     = 0.50
THRESH_FISH     = 0.35           # ★ 0.50→0.35 (ROI内极少误匹配, 大幅放宽)
THRESH_BAR      = 0.40           # ★ 0.62→0.40 (实测0.84+, 大幅放宽防漏检)
THRESH_HOOK     = 0.45
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
HOLD_MIN_S      = 0.025           # 抗重力基准 (秒) — 越小下降越快
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
YOLO_MODEL    = os.path.join(BASE_DIR, "yolo", "runs", "fish_detect", "weights", "best.pt")
YOLO_CONF     = 0.45              # YOLO 检测置信度阈值
YOLO_DEVICE   = "auto"            # "auto" 优先GPU / "cpu" 强制CPU / "gpu" 强制GPU
YOLO_COLLECT  = False             # True=钓鱼时自动保存截图用于训练
TRACK_MIN_ANGLE   = 3.0           # 轨道倾斜角度阈值(度), 超过此值启用旋转补偿
TRACK_MAX_ANGLE   = 45.0          # 轨道最大合理角度(度), 超过视为误检(如把海平线当轨道)

# ═══════════════════════════════════════════════════════════
#  行为克隆 (录制你的操作 → 训练模型 → 替代PD控制器)
# ═══════════════════════════════════════════════════════════
IL_RECORD       = False           # True=录制模式: 检测位置但不控制鼠标, 记录你的操作
IL_USE_MODEL    = False           # True=用训练好的模型控制, False=PD控制器
IL_MODEL_PATH   = os.path.join(BASE_DIR, "imitation", "policy.pt")
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
    "fish_copper":  "copperFish.png",
    "fish_blue":    "blueFish.png",
    "fish_purple":  "purpleFish.png",
    "fish_black":   "blackFish.png",
    "hook":         "gou.png",
    "prog_full":    "full.png",
    "prog_empty":   "null.png",
}

# 所有鱼模板 key 列表（find_fish 使用）
FISH_KEYS = [
    "fish_white", "fish_green", "fish_golden",
    "fish_copper", "fish_blue", "fish_purple", "fish_black",
    "fish_pink", "fish_red", "fish_rainbow",
]

# ═══════════════════════════════════════════════════════════
#  钓鱼白名单 (True=要钓, False=放弃)
# ═══════════════════════════════════════════════════════════
FISH_WHITELIST = {
    "fish_black":   True,   # 黑鱼
    "fish_white":   True,   # 白鱼
    "fish_copper":  True,   # 铜鱼
    "fish_green":   True,   # 绿鱼
    "fish_blue":    True,   # 蓝鱼
    "fish_purple":  True,   # 紫鱼
    "fish_pink":    True,   # 粉鱼
    "fish_red":     True,   # 红鱼
    "fish_rainbow": True,   # 彩鱼
}
