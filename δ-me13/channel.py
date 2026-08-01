# channel.py -- GUI 管道通信
import os
import sys
import json
import select
import logging

logger = logging.getLogger("OmphalosLogger")

_fd3 = None
_connected = False


def open_channel() -> bool:
    """打开 fd 3 作为 GUI 输出通道。不可用时静默失败。"""
    global _fd3, _connected
    try:
        # 用 os.fstat 轻量检查 fd 3 有效性，不创建多余的缓冲文件对象
        _fd3 = 3
        os.fstat(_fd3)
        _connected = True
        logger.info("\033[96m📡 GUI 通道已打开 (fd 3)\033[0m")
        return True
    except (OSError, IOError):
        _fd3 = None
        _connected = False
        return False


def send_metrics(data: dict):
    """向 GUI 发送 metrics JSON 行。fd 3 不可用时静默跳过。"""
    if not _connected or _fd3 is None:
        return
    try:
        line = json.dumps(data, ensure_ascii=False) + "\n"
        os.write(_fd3, line.encode("utf-8"))
    except (OSError, IOError, BrokenPipeError):
        pass


def read_command(timeout: float = 0) -> dict | None:
    """从 stdin 非阻塞读取一行 JSON 命令。超时返回 None。"""
    try:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if ready:
            line = sys.stdin.readline()
            if line:
                return json.loads(line.strip())
    except (json.JSONDecodeError, OSError):
        pass
    return None


def is_gui_connected() -> bool:
    """检查 GUI 是否已连接。"""
    return _connected
