# ane_utils.py -- Apple Neural Engine 推理加速工具
import torch
import logging
import os
import platform

logger = logging.getLogger("OmphalosLogger")
_ane_enabled = False


def is_apple_silicon():
    """检测是否在 Apple Silicon 上运行."""
    return platform.system() == "Darwin" and platform.processor() == "arm"


def enable_ane_optimizations():
    """
    启用 ANE 推理优化.
    在 Apple Silicon + MPS 可用时，配置 autocast FP16 环境。
    受 config['ane']['enable_autocast'] 开关控制。
    """
    global _ane_enabled

    # 读取配置开关
    try:
        from config import config as _cfg
        if not _cfg.get('ane', {}).get('enable_autocast', True):
            logger.info("\033[93mANE 优化已在配置中禁用 (ane.enable_autocast=False)\033[0m")
            return False
    except ImportError:
        pass

    if not is_apple_silicon():
        logger.info("\033[93m非 Apple Silicon 平台，ANE 优化不可用\033[0m")
        return False
    if not torch.backends.mps.is_available():
        logger.info("\033[93mMPS 不可用，ANE 优化不可用\033[0m")
        return False

    # 验证 autocast 支持（使用真实 Linear forward，比零张量更可靠）
    try:
        test_linear = torch.nn.Linear(4, 4).to("mps")
        with torch.autocast(device_type="mps", dtype=torch.float16):
            _ = test_linear(torch.randn(1, 4, device="mps"))
    except Exception as e:
        logger.info("\033[93mMPS autocast 不支持: %s\033[0m", e)
        return False

    _ane_enabled = True
    logger.info("\033[96m🧠 Apple Neural Engine 推理优化已启用 (MPS autocast FP16)\033[0m")
    return True


def ane_autocast_context():
    """
    返回 autocast 上下文管理器（如果 ANE 可用）.
    非 Apple Silicon 时返回 nullcontext()，行为完全不变。
    """
    if _ane_enabled:
        return torch.autocast(device_type="mps", dtype=torch.float16)
    else:
        from contextlib import nullcontext
        return nullcontext()


def is_ane_enabled():
    """检查 ANE 优化是否已启用."""
    return _ane_enabled


def print_ane_verification_help():
    """打印 ANE 验证帮助信息."""
    logger.info("\033[96m--- ANE 使用验证方法 ---\033[0m")
    logger.info("方法 1 — powermetrics (实时):")
    logger.info("  sudo powermetrics --samplers cpu_power,gpu_power,ane_power -n 1 -i 1000")
    logger.info("  观察 ANE Power 列是否 > 0 mW")
    logger.info("方法 2 — Instruments (图形化):")
    logger.info("  打开 Xcode → Instruments → 选择 'ANE' 模板 → 附加到 Python 进程")
    logger.info("  运行: python main.py --disable-llm --fast-forward")
    logger.info("  观察 ANE 活动时间线")
    logger.info("--------------------------------\033[0m")
