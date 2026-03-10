"""
控制动作执行器
==============
把 PD/IL 等控制层输出的动作，统一转换成输入设备副作用。
"""

import time

from core.pd_controller import PDAction
from utils.logger import log


class ControlExecutor:
    """执行控制动作，并统一处理日志输出。"""

    def __init__(self, input_controller, sleep_fn=time.sleep, logger=log):
        self.input = input_controller
        self._sleep = sleep_fn
        self._log = logger

    def release(self, log_message: str = "") -> bool:
        self.input.mouse_up()
        if log_message:
            self._log.info(log_message)
        return False

    def execute(self, action: PDAction) -> bool:
        if action.should_press:
            self.input.mouse_down()
            self._sleep(action.hold_s)
            self.input.mouse_up()
            if action.log_message:
                self._log.info(action.log_message)
            return True
        return self.release(action.log_message)
