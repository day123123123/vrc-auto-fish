"""
小游戏控制后端
==============
统一 PD、IL 录制、IL 推理三类控制入口。
"""

import config


class PDControlBackend:
    """使用 PD 控制器驱动输入。"""

    def __init__(self, input_controller, pd_control):
        self.input = input_controller
        self._pd_control = pd_control

    def control(self, fish, bar, yolo_progress, runtime, ctx) -> bool:
        frame_det = ((fish is not None)
                     + (bar is not None)
                     + (yolo_progress is not None))
        if runtime.skip_fish or frame_det < 2:
            self.input.mouse_up()
            return False
        return self._pd_control(fish, bar, ctx.search_region)


class ILRecordControlBackend:
    """录制模式下只记录用户操作，不主动控制鼠标。"""

    def __init__(self, input_controller, il_adapter):
        self.input = input_controller
        self.il = il_adapter

    def control(self, fish, bar, yolo_progress, runtime, ctx) -> bool:
        frame_det = ((fish is not None)
                     + (bar is not None)
                     + (yolo_progress is not None))
        if runtime.skip_fish or frame_det < 2:
            self.input.mouse_up()
            return False
        self.il.record_frame(runtime.frame, fish, bar)
        return False


class ILModelControlBackend:
    """使用行为克隆模型接管控制。"""

    def __init__(self, input_controller, il_adapter):
        self.input = input_controller
        self.il = il_adapter

    def control(self, fish, bar, yolo_progress, runtime, ctx) -> bool:
        frame_det = ((fish is not None)
                     + (bar is not None)
                     + (yolo_progress is not None))
        if runtime.skip_fish or frame_det < 2:
            self.input.mouse_up()
            return False
        return self.il.model_control(fish, bar)


def build_control_backend(bot):
    """根据当前配置为本局小游戏选择控制后端。"""
    if config.IL_RECORD:
        return ILRecordControlBackend(bot.input, bot.il)
    if config.IL_USE_MODEL and bot.il.policy is not None:
        return ILModelControlBackend(bot.input, bot.il)
    return PDControlBackend(bot.input, bot._control_mouse)
