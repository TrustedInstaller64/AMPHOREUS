# scheduler.py — 跨平台 NUMA 感知 CPU 性能调度器
# 每 NUMA 节点独立 EMA 调控 + 线程亲和性
# 支持: macOS (Apple Silicon P 核), Linux (numactl), Windows (start /NODE)

import os
import sys
import time
import threading
import subprocess
import logging
from channel import send_metrics

logger = logging.getLogger("OmphalosLogger")

# ── 全局状态 ────────────────────────────────────────────

_running = False
_lock = threading.Lock()

# 每节点状态
_node_ema_idle: dict[int, float] = {}
_node_current_threads: dict[int, int] = {}
_cfg: dict = {}
_topo = None  # 惰性初始化


def _try_import_threadpoolctl():
    try:
        import threadpoolctl
        return threadpoolctl
    except ImportError:
        return None


# ── 平台工具 ────────────────────────────────────────────

def _apply_threads(threads: int):
    """进程级线程应用（单进程模式）。"""
    s = str(threads)
    os.environ["OMP_NUM_THREADS"] = s
    os.environ["MKL_NUM_THREADS"] = s
    os.environ["OMP_PROC_BIND"] = "close"
    os.environ["OMP_PLACES"] = "cores"
    # PyTorch
    try:
        import torch
        torch.set_num_threads(threads)
        if hasattr(torch, 'set_num_interop_threads'):
            torch.set_num_interop_threads(threads)
    except ImportError:
        pass
    # NumPy / threadpoolctl
    import numpy as np
    try:
        import threadpoolctl
        threadpoolctl.threadpool_limits(limits=threads, user_api='blas')
        threadpoolctl.threadpool_limits(limits=threads, user_api='openmp')
    except ImportError:
        pass
    # 直接改 NumPy 内部（兜底）
    try:
        np.show_config()
    except Exception:
        pass


def _bind_current_to_node(node) -> bool:
    """将当前进程绑定到指定 NUMA 节点。"""
    try:
        os.sched_setaffinity(0, node.cpus)
        return True
    except (OSError, AttributeError):
        return False


def _spawn_node_worker(node) -> subprocess.Popen | None:
    """在指定 NUMA 节点上启动独立子进程（多进程模式）。"""
    from numa_topology import Topology  # noqa: F811

    env = os.environ.copy()
    env["OMP_NUM_THREADS"] = str(node.phys_cores)
    env["MKL_NUM_THREADS"] = str(node.phys_cores)
    env["OMP_PROC_BIND"] = "close"
    env["OMP_PLACES"] = "cores"
    env["NUMA_NODE_ID"] = str(node.id)
    env["KMP_AFFINITY"] = "granularity=fine,compact,1,0"

    platform = sys.platform
    py = sys.executable
    args = [py, "main.py", "--gui", f"--numa-node={node.id}"]

    if platform == "linux":
        cmd = [
            "numactl",
            f"--cpunodebind={node.id}",
            f"--membind={node.id}",
        ] + args
    elif platform == "win32":
        hex_mask = format(node.affinity_mask, "X")
        cmd = [
            "cmd", "/c", "start", "/NODE", str(node.id),
            "/AFFINITY", hex_mask,
        ] + args
    else:
        return None

    try:
        proc = subprocess.Popen(cmd, env=env)
        logger.info("  节点 %d 子进程已启动 (PID %d)", node.id, proc.pid)
        return proc
    except Exception as e:
        logger.error("  节点 %d 子进程启动失败: %s", node.id, e)
        return None


# ── 调度循环 ────────────────────────────────────────────

def _ema_loop(node):
    """每 NUMA 节点的独立 EMA 调度循环。"""
    global _running

    try:
        import psutil
    except ImportError:
        logger.warning("⚠️  psutil 未安装，调度器不可用")
        return

    alpha = _cfg.get("alpha", 0.35)
    interval = _cfg.get("interval", 3)
    min_threads = _cfg.get("min_threads_per_node",
                           _cfg.get("min_threads", 2))

    phys = node.phys_cores
    total_cores = len(node.cpus) or phys
    proc = psutil.Process()

    with _lock:
        _node_ema_idle[node.id] = 100.0
        _node_current_threads[node.id] = phys

    logger.info("  节点 %d EMA 循环启动 (cores=%d, α=%.2f)", node.id, phys, alpha)

    while _running:
        try:
            # 系统总 CPU + 自身占用
            total_cpu = psutil.cpu_percent(interval=1.0)              # 0-100，含自身
            our_raw = proc.cpu_percent(interval=None)                 # 所有核的累计%（80核全开≈8000%）
            our_pct = our_raw / (total_cores or 1)                    # 归一化到 0-100

            # 别人的占用 = 总占用 - 自身占用
            other_cpu = max(0.0, total_cpu - our_pct)

            other_idle = 100.0 - other_cpu                            # "真正的"空闲率

            with _lock:
                prev = _node_ema_idle[node.id]
                _node_ema_idle[node.id] = alpha * other_idle + (1.0 - alpha) * prev
                ema = _node_ema_idle[node.id]

            # 手动模式：跳过 EMA，直接用固定比例
            if _manual_ratio > 0:
                target = max(1, int(phys * _manual_ratio))
            else:
                # 激进策略：别人不用时就抢满全核
                competition = 100.0 - ema  # 其他人占用的 CPU%
                if competition < 20:
                    ratio = 1.0
                else:
                    ratio = 1.0 - (competition - 20) / 60 * 0.5
                    ratio = max(0.5, ratio)
                target = max(min_threads, int(phys * ratio))

            with _lock:
                current = _node_current_threads[node.id]
                if target != current:
                    _node_current_threads[node.id] = target
                    # 单进程模式：应用线程
                    # 多进程模式：子进程自行管理
                    _apply_threads(target)

            # 发送指标到 GUI
            try:
                from metrics import get_snapshot
                snap = get_snapshot()
                send_metrics({
                    "type": "metrics",
                    "node_id": node.id,
                    "gen": 0, "pop": 0,
                    "cpu_pct": round(node_usage, 1),
                    "mem_pct": snap.get("mem_pct", 0),
                    "cpu_power_mw": snap.get("cpu_power_mw", 0),
                    "gpu_power_mw": snap.get("gpu_power_mw", 0),
                    "ane_power_mw": snap.get("ane_power_mw", 0),
                    "threads": target,
                    "ema_idle": round(ema, 1),
                })
            except Exception:
                pass

        except Exception:
            pass

        time.sleep(interval)


# ── 公共 API ────────────────────────────────────────────

def start_scheduler():
    """启动跨平台调度器。单节点→单进程 EMA；多节点→每节点子进程 + EMA。"""
    global _running, _topo
    try:
        from config import config as raw_cfg
    except ImportError:
        raw_cfg = {}
    _cfg.update(raw_cfg.get("scheduler", {}))

    if not _cfg.get("enabled", True):
        logger.info("\033[93m调度器已在配置中禁用\033[0m")
        return
    if _running:
        return

    from numa_topology import detect_topology, log_topology
    topo = detect_topology()
    _topo = topo
    log_topology(topo)

    _running = True
    numa_aware = _cfg.get("numa_aware", True)

    # 子进程模式：NUMA_NODE_ID 已由外部设置，直接单节点运行
    is_worker = "NUMA_NODE_ID" in os.environ

    # 单进程模式：所有核心参与同一仿真（不 fork 子进程）
    # 多 NUMA 节点用 OMP_PROC_BIND=spread 让 OpenMP 自行分布

    # 计算全节点物理核心总数
    total_phys = sum(n.phys_cores for n in topo.numa_nodes)
    if total_phys == 0:
        total_phys = topo.total_logical

    # 绑定到所有 NUMA 节点的所有核心
    all_cpus = []
    for n in topo.numa_nodes:
        all_cpus.extend(n.cpus)
    if all_cpus:
        try:
            os.sched_setaffinity(0, all_cpus)
        except Exception:
            pass

    os.environ["OMP_NUM_THREADS"] = str(total_phys)
    os.environ["MKL_NUM_THREADS"] = str(total_phys)
    os.environ["OMP_PROC_BIND"] = "spread"
    os.environ["OMP_PLACES"] = "cores"

    try:
        import torch
        torch.set_num_threads(total_phys)
    except ImportError:
        pass

    # 使用节点 0 做 EMA（后续可改为全节点平均）
    node0 = topo.numa_nodes[0]
    node0.phys_cores = total_phys  # EMA 用全物理核
    t = threading.Thread(target=_ema_loop, args=(node0,), daemon=True)
    t.start()

    logger.info("\033[96m⚙️  CPU 性能调度器已启动 (%d 物理核, %d 套接字, α=%.2f)\033[0m",
                total_phys, topo.sockets, _cfg.get("alpha", 0.35))
    return None


def get_ema_idle(node_id: int = 0) -> float:
    with _lock:
        return _node_ema_idle.get(node_id, 0.0)


_manual_ratio: float = 0.0  # 0 = AUTO, >0 = 手动线程比例
autosave_interval: list = [180]  # 自动存档间隔（秒），列表用于跨线程共享


def set_manual_thread_ratio(ratio: float):
    """手动设置线程比例（0~1）。0 = 恢复 AUTO EMA。"""
    global _manual_ratio
    _manual_ratio = max(0.0, min(1.0, ratio))
    if ratio == 0:
        return  # AUTO 模式，EMA 自行管理
    # 手动模式：立即应用
    total_phys = int(os.cpu_count() * ratio) or 1
    _apply_threads(total_phys)
    with _lock:
        for nid in _node_current_threads:
            _node_current_threads[nid] = total_phys


def get_current_threads(node_id: int = 0) -> int:
    with _lock:
        return _node_current_threads.get(node_id, 0)


def shutdown():
    """停止调度器，恢复全线程。"""
    global _running
    if not _running:
        return
    _running = False

    topo = _topo
    if topo is None:
        from numa_topology import detect_topology
        topo = detect_topology()

    total = topo.numa_nodes[0].phys_cores if topo.numa_nodes else os.cpu_count() or 4
    _apply_threads(total)

    logger.info("\033[96m⚙️  CPU 性能调度器已停止（恢复 %d 线程）\033[0m", total)
