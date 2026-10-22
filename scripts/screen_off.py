"""立刻关屏：屏幕黑掉，机器继续跑；动一下鼠标或键盘就会亮。"""

import ctypes

HWND_BROADCAST = 0xFFFF
WM_SYSCOMMAND = 0x0112
SC_MONITORPOWER = 0xF170
MONITOR_OFF = 2

ctypes.windll.user32.SendMessageTimeoutW(
    HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, MONITOR_OFF, 2, 1000, None
)
