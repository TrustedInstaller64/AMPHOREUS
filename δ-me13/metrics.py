# metrics.py — 跨平台系统性能采集
# macOS: psutil + powermetrics (CPU+GPU+ANE)
# Linux:  psutil + sysfs 每节点内存 + RAPL 功耗
# Windows: psutil + Win32 NUMA 内存

import os
import sys
import time
import glob
import threading
import subprocess
import logging

logger = logging.getLogger("OmphalosLogger")

_running = False
_data = {
    "cpu_pct": 0.0,
    "mem_pct": 0.0,
    "mem_used_gb": 0.0,
    "cpu_power_mw": 0,
    "gpu_power_mw": 0,
    "ane_power_mw": 0,
}
_lock = threading.Lock()
_powermetrics_proc = None
_rapl_last_joules: dict[str, float] = {}
_rapl_last_ts: float = 0.0


# ── 统一 CPU + 内存采集 ────────────────────────────────

def _psutil_loop():
    """后台线程：每 1s 采集 CPU + 内存。"""
    global _running
    try:
        import psutil
    except ImportError:
        logger.warning("\033[93mpsutil not installed, metrics unavailable\033[0m")
        return

    platform = sys.platform

    # 预热基线
    psutil.cpu_percent(interval=0.1)

    while _running:
        try:
            cpu = psutil.cpu_percent(interval=None)

            if platform == "linux":
                mem = _linux_system_mem()
            else:
                mem = psutil.virtual_memory()
                mem = {"pct": mem.percent, "used_gb": mem.used / (1024**3)}

            with _lock:
                _data["cpu_pct"] = round(cpu, 1)
                _data["mem_pct"] = round(mem["pct"], 1)
                _data["mem_used_gb"] = round(mem["used_gb"], 2)

        except Exception:
            pass
        time.sleep(1.0)


def _linux_system_mem() -> dict:
    """Linux: 聚合所有 NUMA 节点内存。"""
    try:
        total = free = 0
        for f in glob.glob("/sys/devices/system/node/node*/meminfo"):
            with open(f) as fh:
                for line in fh:
                    if "MemTotal" in line:
                        total += _parse_kb(line)
                    elif "MemFree" in line:
                        free += _parse_kb(line)
        used = total - free
        pct = (used / total * 100) if total else 0
        return {"pct": pct, "used_gb": used / (1024**2)}
    except Exception:
        try:
            import psutil
            m = psutil.virtual_memory()
            return {"pct": m.percent, "used_gb": m.used / (1024**3)}
        except Exception:
            return {"pct": 0, "used_gb": 0}


def _parse_kb(line: str) -> int:
    try:
        return int(line.split(":")[1].strip().split()[0])
    except Exception:
        return 0


# ── 功耗采集 ────────────────────────────────────────────

def _start_power_collection():
    """根据平台启动功耗采集。"""
    p = sys.platform
    if p == "darwin":
        _start_powermetrics()
    elif p == "linux":
        t = threading.Thread(target=_rapl_loop, daemon=True)
        t.start()


def _start_powermetrics():
    """macOS: 启动 powermetrics 后台进程。"""
    global _powermetrics_proc
    try:
        result = subprocess.run(
            ["sudo", "-n", "true"], capture_output=True, timeout=2)
        if result.returncode != 0:
            logger.info("\033[93msudo 免密不可用，功耗监测跳过\033[0m")
            return
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return

    try:
        _powermetrics_proc = subprocess.Popen(
            ["sudo", "-n", "powermetrics",
             "--samplers", "cpu_power,gpu_power,ane_power",
             "-i", "2000"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    except (FileNotFoundError, OSError):
        logger.info("\033[93mpowermetrics 启动失败\033[0m")
        return

    buffer = ""
    while _running:
        try:
            line = _powermetrics_proc.stdout.readline()
            if not line:
                break
            buffer += line
            parsed = {}
            for raw in buffer.splitlines():
                s = raw.strip()
                if s.startswith("CPU Power:"):
                    try:
                        parsed["cpu_power_mw"] = int(s.split(":")[1].strip().split()[0])
                    except Exception:
                        pass
                elif s.startswith("GPU Power:"):
                    try:
                        parsed["gpu_power_mw"] = int(s.split(":")[1].strip().split()[0])
                    except Exception:
                        pass
                elif s.startswith("ANE Power:"):
                    try:
                        parsed["ane_power_mw"] = int(s.split(":")[1].strip().split()[0])
                    except Exception:
                        pass
            if parsed:
                with _lock:
                    _data.update(parsed)
                buffer = ""
        except Exception:
            pass


def _rapl_loop():
    """Linux: 读取 RAPL 功耗（每 2 秒差值法）。"""
    global _rapl_last_joules, _rapl_last_ts
    rapl_dir = "/sys/class/powercap"
    if not os.path.isdir(rapl_dir):
        return

    while _running:
        try:
            now = time.time()
            cpu_total = 0.0
            for domain in sorted(glob.glob(f"{rapl_dir}/intel-rapl:*/energy_uj")):
                with open(domain) as f:
                    joules = int(f.read().strip()) / 1_000_000.0
                name_path = os.path.join(os.path.dirname(domain), "name")
                try:
                    with open(name_path) as f:
                        domain_name = f.read().strip()
                except Exception:
                    domain_name = os.path.basename(os.path.dirname(domain))

                key = f"rapl_{domain_name}"
                prev = _rapl_last_joules.get(key)
                if prev is not None and _rapl_last_ts > 0:
                    dt = now - _rapl_last_ts
                    if dt > 0:
                        watts = (joules - prev) / dt
                        if "package" in domain_name.lower():
                            cpu_total += watts
                _rapl_last_joules[key] = joules

            _rapl_last_ts = now

            if cpu_total > 0:
                with _lock:
                    _data["cpu_power_mw"] = int(cpu_total * 1000)

        except Exception:
            pass
        time.sleep(2.0)


# ── 公共 API ────────────────────────────────────────────

def start_collection(gui_power_enabled: bool = False):
    """启动后台采集。"""
    global _running
    if _running:
        return
    _running = True

    t = threading.Thread(target=_psutil_loop, daemon=True)
    t.start()

    if gui_power_enabled:
        _start_power_collection()
        logger.info("\033[96m📊 系统性能采集已启动（含功耗监测）\033[0m")
    else:
        logger.info("\033[96m📊 系统性能采集已启动（仅 CPU/内存）\033[0m")


def get_snapshot() -> dict:
    """线程安全的指标快照。"""
    with _lock:
        return dict(_data)


def shutdown():
    """停止采集。"""
    global _running, _powermetrics_proc
    _running = False
    if _powermetrics_proc:
        try:
            _powermetrics_proc.terminate()
            _powermetrics_proc.wait(timeout=3)
        except Exception:
            try:
                _powermetrics_proc.kill()
            except Exception:
                pass
        _powermetrics_proc = None
    logger.info("\033[96m📊 系统性能采集已停止\033[0m")
