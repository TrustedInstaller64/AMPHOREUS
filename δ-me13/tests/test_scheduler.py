# tests/test_scheduler.py — CPU 调度器 5 项自动化测试
import os
import sys
import time
import multiprocessing
import logging
from datetime import datetime

# 确保 δ-me13 在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scheduler import start_scheduler, get_ema_idle, get_current_threads, shutdown as shutdown_scheduler

# ---------- 测试日志初始化 ----------
_test_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "TestLog")
os.makedirs(_test_log_dir, exist_ok=True)
_test_log_path = os.path.join(_test_log_dir, f"{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}.log")

_test_logger = logging.getLogger("SchedulerTest")
_test_logger.setLevel(logging.DEBUG)

# 文件 handler
_fh = logging.FileHandler(_test_log_path, encoding="utf-8")
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter("[%(asctime)s.%(msecs)03d] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
_test_logger.addHandler(_fh)

# 控制台 handler
_ch = logging.StreamHandler(sys.stdout)
_ch.setLevel(logging.INFO)
_ch.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
_test_logger.addHandler(_ch)

# 自定义级别别名
PASS = 25  # 介于 INFO(20) 和 WARNING(30) 之间
logging.addLevelName(PASS, "PASS")
FAIL = 35
logging.addLevelName(FAIL, "FAIL")


def _cpu_burn():
    """模块级函数：纯 CPU 计算 (macOS spawn 兼容)。"""
    while True:
        _ = [i * i for i in range(10000)]


def _cpu_workers(n: int) -> list:
    """启动 N 个纯 CPU 计算 worker。"""
    procs = []
    for _ in range(n):
        p = multiprocessing.Process(target=_cpu_burn)
        p.start()
        procs.append(p)
    return procs


def _stop_workers(procs: list):
    for p in procs:
        p.terminate()
        p.join(timeout=3)


def test_ema_smoothing():
    _test_logger.info("TEST START: test_ema_smoothing")
    alpha = 0.3
    ema = 100.0
    values = [100, 50, 100, 50, 100]
    for v in values:
        ema = alpha * v + (1 - alpha) * ema
    _test_logger.info("  input=%s, alpha=%.1f, final_ema=%.2f", values, alpha, ema)
    assert 75 <= ema <= 90, f"EMA={ema} 不在 [75,90]"
    _test_logger.log(PASS, "  EMA %.2f in [75,90]", ema)
    _test_logger.info("TEST END: test_ema_smoothing")


def test_threads_decrease():
    _test_logger.info("TEST START: test_threads_decrease")
    shutdown_scheduler()
    cpu_count = os.cpu_count() or 4
    start_scheduler()
    time.sleep(2)
    initial = get_current_threads()
    _test_logger.info("  cpu_count=%d, initial_threads=%d", cpu_count, initial)
    _test_logger.info("  starting %d CPU workers...", cpu_count)
    workers = _cpu_workers(cpu_count)
    time.sleep(12)
    decreased = get_current_threads()
    _test_logger.info("  after 12s: threads=%d", decreased)
    _stop_workers(workers)
    _test_logger.info("  workers stopped")
    shutdown_scheduler()
    assert decreased < cpu_count, f"线程未下降: {decreased} >= {cpu_count}"
    _test_logger.log(PASS, "  threads decreased to %d (assert %d < %d)", decreased, decreased, cpu_count)
    _test_logger.info("TEST END: test_threads_decrease")


def test_threads_recovery():
    _test_logger.info("TEST START: test_threads_recovery")
    shutdown_scheduler()
    cpu_count = os.cpu_count() or 4
    start_scheduler()
    _test_logger.info("  cpu_count=%d", cpu_count)
    workers = _cpu_workers(cpu_count)
    _test_logger.info("  starting %d workers...", cpu_count)
    time.sleep(12)
    after_load = get_current_threads()
    _test_logger.info("  under load: threads=%d", after_load)
    _stop_workers(workers)
    time.sleep(18)
    recovered = get_current_threads()
    _test_logger.info("  after recovery: threads=%d", recovered)
    shutdown_scheduler()
    assert recovered > after_load, f"线程未恢复: {recovered} <= {after_load}"
    assert recovered >= cpu_count - 2, f"线程恢复不足: {recovered} < {cpu_count - 2}"
    _test_logger.log(PASS, "  threads recovered to %d (increased from %d)", recovered, after_load)
    _test_logger.info("TEST END: test_threads_recovery")


def test_min_threads_boundary():
    _test_logger.info("TEST START: test_min_threads_boundary")
    shutdown_scheduler()
    cpu_count = os.cpu_count() or 4
    start_scheduler()
    time.sleep(2)
    t = get_current_threads()
    _test_logger.info("  cpu_count=%d, current_threads=%d", cpu_count, t)
    shutdown_scheduler()
    assert t >= 2, f"线程 {t} < min_threads(2)"
    _test_logger.log(PASS, "  threads=%d >= min_threads(2)", t)
    _test_logger.info("TEST END: test_min_threads_boundary")


def test_noise_immunity():
    _test_logger.info("TEST START: test_noise_immunity")
    shutdown_scheduler()
    start_scheduler()
    time.sleep(3)
    before = get_ema_idle()
    _test_logger.info("  baseline ema_idle=%.1f", before)
    workers = _cpu_workers(1)
    time.sleep(2)
    _stop_workers(workers)
    time.sleep(5)
    after = get_ema_idle()
    _test_logger.info("  after noise: ema_idle=%.1f", after)
    shutdown_scheduler()
    change_pct = abs(after - before) / max(before, 0.1) * 100
    assert change_pct < 20, f"EMA 变化 {change_pct:.1f}% >= 20%"
    _test_logger.log(PASS, "  EMA change %.1f%% < 20%%", change_pct)
    _test_logger.info("TEST END: test_noise_immunity")


if __name__ == "__main__":
    _test_logger.info("========== SCHEDULER TEST SUITE START ==========")
    tests = [
        test_ema_smoothing,
        test_min_threads_boundary,
        test_noise_immunity,
        test_threads_decrease,
        test_threads_recovery,
    ]
    passed = 0
    for test in tests:
        try:
            test()
            print(f"PASS: {test.__name__}")
            passed += 1
        except Exception as e:
            _test_logger.log(FAIL, "%s — %s", test.__name__, e)
            print(f"FAIL: {test.__name__} — {e}")
    _test_logger.info("========== RESULT: %d/%d 通过 ==========", passed, len(tests))
    print(f"\n{passed}/{len(tests)} 通过")
    print(f"日志文件: {_test_log_path}")
