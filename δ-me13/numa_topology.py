# numa_topology.py — 跨平台 CPU 拓扑检测
# 支持: macOS (Apple Silicon / Intel), Linux (多路 NUMA), Windows (多路 NUMA)

import sys
import os
import re
import glob
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("OmphalosLogger")


@dataclass
class NUMANode:
    """一次 NUMA 节点（macOS 上只有一个）。"""
    id: int
    cpus: list[int]                              # 归属该节点的逻辑 CPU 索引
    phys_cores: int                               # 物理核心数
    p_cores: int = 0                              # P 核数（仅 Apple Silicon）
    e_cores: int = 0                              # E 核数（仅 Apple Silicon）
    mem_total_mb: int = 0                         # 节点本地内存总量（MiB）
    mem_free_mb: int = 0                          # 节点可用内存（MiB）
    affinity_mask: int = 0                        # CPU 亲和性掩码（Windows 专用）


@dataclass
class Topology:
    """完整 CPU 拓扑快照。"""
    platform: str                                 # "macos" / "linux" / "windows"
    sockets: int                                  # 物理 CPU 数量
    numa_nodes: list = field(default_factory=list)
    total_logical: int = 0                        # 总逻辑核心数
    node_count: int = 0                           # NUMA 节点数
    is_heterogeneous: bool = False                # P/E 混合架构


# ── 平台检测 ────────────────────────────────────────────

_TOPOLOGY_CACHE: Topology | None = None


def detect_topology() -> Topology:
    """自动检测当前平台的 CPU 拓扑（结果缓存）。"""
    global _TOPOLOGY_CACHE
    if _TOPOLOGY_CACHE is not None:
        return _TOPOLOGY_CACHE

    p = sys.platform
    if p == "darwin":
        topo = _macos_detect()
    elif p == "linux":
        topo = _linux_detect()
    elif p == "win32":
        topo = _windows_detect()
    else:
        logger.warning("⚠️  未知平台 %s，回退到 os.cpu_count()", p)
        topo = _fallback_detect(p)

    _TOPOLOGY_CACHE = topo
    return topo


# ── 平台 API ────────────────────────────────────────────

def get_node_for_cpu(cpu_id: int) -> int | None:
    """返回 cpu_id 归属的 NUMA 节点 ID。"""
    topo = detect_topology()
    for node in topo.numa_nodes:
        if cpu_id in node.cpus:
            return node.id
    return None


def bind_current_to_node(node: NUMANode) -> bool:
    """将当前进程绑定到指定 NUMA 节点的 CPU 集合。"""
    try:
        os.sched_setaffinity(0, node.cpus)
        return True
    except (OSError, AttributeError):
        return False


# ── macOS ────────────────────────────────────────────────

def _macos_detect() -> Topology:
    try:
        import psutil
        _has_psutil = True
    except ImportError:
        _has_psutil = False

    total_logical = os.cpu_count() or (_has_psutil and psutil.cpu_count(logical=True)) or 4
    total_phys = (_has_psutil and psutil.cpu_count(logical=False)) or total_logical

    # 检测 P/E 核（Apple Silicon）
    p_cores = _macos_sysctl_int("hw.perflevel0.logicalcpu", 0)
    e_cores = _macos_sysctl_int("hw.perflevel1.logicalcpu", 0)
    is_het = e_cores > 0

    if is_het:
        # P 核在前、E 核在后
        cpus = list(range(total_logical))
        phys = p_cores  # E 核物理数不参与仿真
    else:
        cpus = list(range(total_logical))
        phys = total_phys
        p_cores = total_phys

    # 统一内存：所有节点共享（macOS 无 NUMA）
    mem_total = mem_free = 0
    if _has_psutil:
        try:
            mem = psutil.virtual_memory()
            mem_total = mem.total // (1024 * 1024)
            mem_free = mem.available // (1024 * 1024)
        except Exception:
            pass

    node = NUMANode(
        id=0, cpus=cpus, phys_cores=phys,
        p_cores=p_cores, e_cores=e_cores,
        mem_total_mb=mem_total, mem_free_mb=mem_free,
    )

    sockets = 1  # Apple Silicon 统一封装

    return Topology(
        platform="macos", sockets=sockets,
        numa_nodes=[node], node_count=1,
        total_logical=total_logical,
        is_heterogeneous=is_het,
    )


def _macos_sysctl_int(key: str, default: int) -> int:
    try:
        import subprocess
        out = subprocess.check_output(["sysctl", "-n", key], text=True).strip()
        return int(out)
    except Exception:
        return default


# ── Linux ────────────────────────────────────────────────

def _linux_detect() -> Topology:
    total_logical = os.cpu_count()
    if not total_logical:
        try:
            import psutil
            total_logical = psutil.cpu_count(logical=True) or 4
        except ImportError:
            total_logical = 4
    nodes = []

    # 检测 NUMA 节点
    node_dirs = sorted(glob.glob("/sys/devices/system/node/node[0-9]*"))
    if not node_dirs:
        return _fallback_detect("linux")

    socket_set: set[int] = set()

    for ndir in node_dirs:
        m = re.search(r"node(\d+)", ndir)
        if not m:
            continue
        node_id = int(m.group(1))

        # CPU 列表
        cpulist_path = os.path.join(ndir, "cpulist")
        cpus = _parse_linux_cpulist(cpulist_path)

        # 物理核心数 = 去重 core_id
        phys = _linux_phys_cores(cpus)

        # 内存
        meminfo_path = os.path.join(ndir, "meminfo")
        mem_total, mem_free = _linux_node_mem(meminfo_path)

        # 套接字检测
        for cpu in cpus:
            pkg_path = f"/sys/devices/system/cpu/cpu{cpu}/topology/physical_package_id"
            try:
                with open(pkg_path) as f:
                    socket_set.add(int(f.read().strip()))
            except Exception:
                pass

        nodes.append(NUMANode(
            id=node_id, cpus=cpus,
            phys_cores=phys,
            p_cores=phys, e_cores=0,
            mem_total_mb=mem_total,
            mem_free_mb=mem_free,
        ))

    sockets = len(socket_set) if socket_set else max(1, len(nodes) // 2)

    return Topology(
        platform="linux", sockets=sockets,
        numa_nodes=sorted(nodes, key=lambda n: n.id),
        node_count=len(nodes),
        total_logical=total_logical,
        is_heterogeneous=False,
    )


def _parse_linux_cpulist(path: str) -> list[int]:
    """解析 Linux cpulist 格式：0-3,8-11 → [0,1,2,3,8,9,10,11]"""
    try:
        with open(path) as f:
            raw = f.read().strip()
    except Exception:
        return []
    cpus = []
    for part in raw.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            cpus.extend(range(int(lo), int(hi) + 1))
        elif part:
            cpus.append(int(part))
    return cpus


def _linux_phys_cores(cpus: list[int]) -> int:
    """返回节点物理核心数（去重 core_id）。"""
    cores: set[int] = set()
    for cpu in cpus:
        path = f"/sys/devices/system/cpu/cpu{cpu}/topology/core_id"
        try:
            with open(path) as f:
                cores.add(int(f.read().strip()))
        except Exception:
            pass
    return len(cores) if cores else len(cpus)


def _linux_node_mem(path: str) -> tuple[int, int]:
    """解析 Linux 节点 meminfo，返回 (total_mb, free_mb)。"""
    total = free = 0
    try:
        with open(path) as f:
            for line in f:
                if "MemTotal" in line:
                    total = _parse_kb(line)
                elif "MemFree" in line:
                    free = _parse_kb(line)
    except Exception:
        pass
    return total // 1024, free // 1024


def _parse_kb(line: str) -> int:
    try:
        return int(line.split(":")[1].strip().split()[0])
    except Exception:
        return 0


# ── Windows ──────────────────────────────────────────────

def _windows_detect() -> Topology:
    try:
        import ctypes
        import ctypes.wintypes
    except ImportError:
        return _fallback_detect("windows")

    kernel32 = ctypes.windll.kernel32

    total_logical = os.cpu_count() or kernel32.GetActiveProcessorCount(0xFFFF)

    # 使用 PROCESSOR_RELATIONSHIP 获取物理核心布局
    # Win32 GetNumaNodeProcessorMask 用 64-bit mask，>64 core 会溢出
    # 换用 GetNumaNodeProcessorMaskEx + GROUP_AFFINITY 支持多处理器组

    class GROUP_AFFINITY(ctypes.Structure):
        _fields_ = [("Mask", ctypes.c_ulonglong), ("Group", ctypes.c_ushort)]

    def _mask_to_cpus(mask_value, group):
        """将 64-bit mask + group → 逻辑 CPU 列表。每 group 最多 64 核。"""
        cpus = []
        base = group * 64
        for i in range(64):
            if mask_value & (1 << i):
                cpus.append(base + i)
        return cpus

    nodes = []
    node_id = 0
    while True:
        ga = GROUP_AFFINITY(0, 0)
        if not kernel32.GetNumaNodeProcessorMaskEx(node_id, ctypes.byref(ga)):
            break

        cpus = _mask_to_cpus(ga.Mask, ga.Group)
        # 物理核心数：逻辑÷2（HT 下），保守估计
        phys = max(1, len(cpus) // 2) if len(cpus) >= 2 else len(cpus)

        mem_kb = ctypes.c_ulonglong(0)
        try:
            kernel32.GetNumaAvailableMemoryNode(node_id, ctypes.byref(mem_kb))
        except Exception:
            mem_kb.value = 0

        nodes.append(NUMANode(
            id=node_id, cpus=cpus,
            phys_cores=phys,
            p_cores=phys, e_cores=0,
            mem_free_mb=mem_kb.value // 1024,
            affinity_mask=ga.Mask,
        ))
        node_id += 1

    if not nodes:
        return _fallback_detect("windows")

    sockets = max(1, len(nodes))

    return Topology(
        platform="windows", sockets=sockets,
        numa_nodes=nodes, node_count=len(nodes),
        total_logical=total_logical,
        is_heterogeneous=False,
    )


# ── 回退 ─────────────────────────────────────────────────

def _fallback_detect(platform: str) -> Topology:
    """当无法检测拓扑时的安全回退：单节点、全核心。"""
    total_logical = os.cpu_count() or 4
    phys = total_logical
    try:
        import psutil
        phys = psutil.cpu_count(logical=False) or total_logical
    except ImportError:
        pass

    node = NUMANode(
        id=0,
        cpus=list(range(total_logical)),
        phys_cores=phys,
        p_cores=phys, e_cores=0,
    )
    return Topology(
        platform=platform, sockets=1,
        numa_nodes=[node], node_count=1,
        total_logical=total_logical,
    )


# ── 便捷输出 ──────────────────────────────────────────────

def log_topology(topo: Topology):
    """将拓扑信息打印到日志。"""
    logger.info("=" * 50)
    logger.info("CPU 拓扑检测完成 — 平台: %s", topo.platform)
    logger.info("  套接字: %d  |  NUMA 节点: %d  |  总逻辑核: %d",
                topo.sockets, topo.node_count, topo.total_logical)
    if topo.is_heterogeneous:
        n = topo.numa_nodes[0]
        logger.info("  异构架构: P 核=%d  E 核=%d", n.p_cores, n.e_cores)
    for node in topo.numa_nodes:
        logger.info("  节点 %d: CPU %s  |  物理核 %d  |  内存 %d / %d MB",
                    node.id,
                    _fmt_cpu_range(node.cpus),
                    node.phys_cores,
                    node.mem_free_mb, node.mem_total_mb)


def _fmt_cpu_range(cpus: list[int]) -> str:
    if not cpus:
        return "[]"
    if len(cpus) <= 4:
        return str(cpus)
    return f"[{cpus[0]}..{cpus[-1]}] ({len(cpus)} cores)"
