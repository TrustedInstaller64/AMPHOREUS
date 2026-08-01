# device_utils.py -- 提供设备选择和性能优化功能
import torch
import platform
import logging
import os

logger = logging.getLogger("OmphalosLogger")


def get_optimal_device():
    """自动选择最优的计算设备"""
    if torch.backends.mps.is_available():
        device = torch.device("mps")
        logger.info("🔧 检测到Apple Silicon GPU，启用Metal加速")
        _configure_mps_optimizations()
    elif torch.cuda.is_available():
        device = torch.device("cuda")
        logger.info("🔧 检测到NVIDIA GPU，启用CUDA加速")
    else:
        device = torch.device("cpu")
        num_cores = os.cpu_count()
        if num_cores:
            torch.set_num_threads(num_cores)
            logger.info("🔧 使用CPU多核心优化: %d 逻辑核心", num_cores)
    return device


def _configure_mps_optimizations():
    """
    配置Apple Silicon MPS特定的优化.
    PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0 解除内存上限。
    """
    os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.0")
    if hasattr(torch.backends, "cudnn") and hasattr(torch.backends.cudnn, "enabled"):
        torch.backends.cudnn.enabled = False
    logger.info("🔧 Apple Silicon MPS优化已配置 (PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0)")


def setup_performance_optimizations():
    """设置全局性能优化（CPU和GPU通用）。线程数由 scheduler 管理。"""
    if hasattr(torch, "set_float32_matmul_precision"):
        torch.set_float32_matmul_precision("high")
    if torch.cuda.is_available() and torch.backends.cudnn.is_available():
        torch.backends.cudnn.benchmark = True
