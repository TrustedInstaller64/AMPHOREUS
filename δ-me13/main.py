# main.py

import os
import sys
import traceback
import threading
import colorama
from simulation import AeonEvolution
import logging
from config import config
from datetime import datetime
import argparse 
from pyclog import ClogFileHandler, constants # 导入 pyclog
import re # 导入 re 模块
from channel import open_channel, send_metrics, read_command, is_gui_connected
from metrics import start_collection, shutdown as shutdown_metrics
from scheduler import start_scheduler, shutdown as shutdown_scheduler

colorama.init(autoreset=True) # 确保在sys.stdout重定向前初始化colorama

# CPU 拓扑感知初始化（NUMA / Apple Silicon P 核）
from numa_topology import detect_topology
_topo = detect_topology()

if "NUMA_NODE_ID" in os.environ:
    # 子进程模式：已在外部设置 OMP_NUM_THREADS / MKL_NUM_THREADS
    pass
else:
    # 主进程：根据拓扑设置线程数
    node = _topo.numa_nodes[0]
    if _topo.is_heterogeneous:
        cores = node.p_cores
    else:
        cores = node.phys_cores or os.cpu_count()
    os.environ['OMP_NUM_THREADS'] = str(cores)
    os.environ['MKL_NUM_THREADS'] = str(cores)

import numpy as np

# 创建一个 logger 实例
logger = logging.getLogger("OmphalosLogger")
logger.setLevel(logging.INFO)

# 终端输出使用 StreamHandler（clog handler 在 run_simple 中按 run_dir 创建）
class CustomConsoleFormatter(logging.Formatter):
    def format(self, record):
        # 获取完整的格式化日志行
        log_line = super().format(record)
        
        # 查找消息部分的起始位置
        # 格式字符串是 '[%(asctime)s][%(levelname)s] %(message)s'
        # 我们需要找到 ']' 之后的第一个空格，作为消息的起始
        match = re.match(r'\[.*?\]\[.*?\]\s*', log_line)
        if match:
            prefix_length = match.end()
        else:
            prefix_length = 0 # 如果没有匹配到，则不缩进

        # 将日志行按换行符分割
        lines = log_line.splitlines()
        
        # 对除第一行以外的每一行添加缩进
        if len(lines) > 1:
            indented_lines = [lines[0]]
            for line in lines[1:]:
                indented_lines.append(' ' * prefix_length + line)
            return '\n'.join(indented_lines)
        else:
            return log_line

# 终端输出仍然使用 StreamHandler
console_handler = logging.StreamHandler(sys.stdout)
console_formatter = CustomConsoleFormatter('[%(asctime)s][%(levelname)s] %(message)s')
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)

# 移除 LoggerWriter 和 sys.stdout/sys.stderr 重定向
# if config['log']['enable']:
#     sys.stdout = LoggerWriter(logger.info)
#     sys.stderr = LoggerWriter(logger.error)

def run_simple(load_save_path=None):
    """ 以简单的控制台模式运行模拟 """

    # 创建输出目录
    run_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "runs", datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    )
    os.makedirs(run_dir, exist_ok=True)

    # 日志文件重定向到输出目录（先设置再创建 handler）
    log_path = os.path.join(run_dir, config["log"]["file_name"])
    config["log"]["file_name"] = log_path
    if config["log"]["enable"]:
        clog_handler = ClogFileHandler(log_path, mode="w", compression_code=constants.COMPRESSION_GZIP)
        clog_handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(clog_handler)

    sim = None
    try:
        sim = AeonEvolution(
            # --- LLM ---
            bard_frequency=config['simulation']['bard_frequency'],         
            laertes_frequency=config['simulation']['laertes_frequency'],      
            kaoselanna_llm_enabled=config['llm']['kaoselanna_llm_enabled'],

            fast_forward=args.fast_forward if 'args' in locals() and hasattr(args, 'fast_forward') else False,
            # --- 其他模拟参数 ---
            num_initial_entities=config['simulation']['num_initial_entities'], 
            golden_one_cap=config['simulation']['golden_one_cap'], 
            population_soft_cap=config['simulation']['population_soft_cap'],
            population_hard_cap=config['simulation']['population_hard_cap'], 
            growth_factor=config['simulation']['growth_factor'], 
            mutation_rate=config['simulation']['mutation_rate'],
            culling_strength=config['simulation']['culling_strength'], 
            encounter_similarity=config['simulation']['encounter_similarity'], 
            purity_factor=config['simulation']['purity_factor'],
            initial_rl_lr=config['simulation']['initial_rl_lr'], 
            golden_one_reversion_prob=config['simulation']['golden_one_reversion_prob'],
            elite_selection_percentile=config['simulation']['elite_selection_percentile'], 
            aeonic_event_prob=config['simulation']['aeonic_event_prob'],
            initial_max_affinity_norm=config['simulation']['initial_max_affinity_norm'], 
            target_avg_score=config['simulation']['target_avg_score'],
            norm_adjustment_strength=config['simulation']['norm_adjustment_strength'],
            gui_mode=args.gui if 'args' in locals() and hasattr(args, 'gui') else False
        )
        if load_save_path:
            sim.load_simulation_state(load_save_path)

        # 设置 policy_saver 输出目录
        sim.policy_saver.output_dir = run_dir

        # 打开 GUI 通信通道（fd 3 不可用时静默跳过）
        open_channel()

        # 启动性能调度器和系统数据采集
        gui_power = config.get("scheduler", {}).get("gui_power_enabled", False)
        start_collection(gui_power_enabled=gui_power)
        start_scheduler()

        # ── 自动存档线程（默认每 3 分钟，可运行时调整）──
        auto_save_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "AMPHOREUS.json")
        auto_save_stop = threading.Event()

        def _auto_save_loop():
            while not auto_save_stop.is_set():
                from scheduler import autosave_interval
                auto_save_stop.wait(autosave_interval[0])
                if auto_save_stop.is_set():
                    break
                try:
                    sim.save_simulation_state(auto_save_path)
                except Exception as e:
                    logger.warning("自动存档失败: %s", e)

        auto_save_thread = threading.Thread(target=_auto_save_loop, daemon=True)
        auto_save_thread.start()

        logger.info("=== 翁法罗斯 v10.4 (Dev) 启动 ===\nLoading Operating AMPHOREUS Core System v10.4_dev...")
        sim.start(
            num_generations=config['simulation_phases']['TOTAL_SIMULATION_END']
        )

    except KeyboardInterrupt:
        logger.info("\n\n模拟被用户中断。正在保存进度...")
        try:
            sim.save_simulation_state(auto_save_path)
            logger.info("进度已保存至 AMPHOREUS.json")
        except Exception as e:
            logger.warning("保存失败: %s", e)

    except Exception:
        traceback.print_exc()

    finally:
        auto_save_stop.set()
        if sim and hasattr(sim, 'policy_saver'):
            logger.info("\n正在尝试保存策略模型...")
            sim.policy_saver.save_policy_models()
            logger.info("\n正在尝试保存clog...")
            logging.shutdown()

        shutdown_scheduler()
        shutdown_metrics()

        print("\n模拟已结束。按任意键退出。")
        if sim and hasattr(sim, 'debugger') and not getattr(args, 'gui', False):
            sim.debugger._restore_terminal()
        try:
            if not getattr(args, 'gui', False):
                input()
        except EOFError:
            pass
        sys.exit(0) 


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="翁法罗斯")
    parser.add_argument('--disable-llm', action='store_true', 
                        help='彻底禁用LLM参与演算，无需安装llama_cpp。')
    parser.add_argument('--load-save', type=str, default=None,
                        help='从指定的存档文件加载并开始模拟。')
    parser.add_argument('--fast-forward', action='store_true',
                        help='快速演化模式，仅显示进度条和周期性报告。')
    parser.add_argument('--gui', action='store_true',
                        help='由 GUI 启动。禁用终端交互，使用管道通信。')
    parser.add_argument('--numa-node', type=int, default=None,
                        help='NUMA 节点 ID（子进程模式）。由调度器自动传入。')
    global args; args = parser.parse_args() # 定义为全局变量以便run_simple访问
 
    # 根据参数更配
    if args.disable_llm:
        config['llm']['enable_llm'] = False
        logger.info("\033[93m命令行参数 --disable-llm 已启用，LLM功能已彻底禁用。\033[0m")
    
    if args.fast_forward:
        # 找到控制台处理器并抑制其输出，以实现快速模式
        found_handler = False
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler) and handler.stream == sys.stdout:
                logger.info("\033[93m快速演化模式已启用。控制台输出将受到抑制。\033[0m")
                handler.setLevel(logging.CRITICAL + 1) # 将等级设为极高，使其忽略所有常规日志
                found_handler = True
        if not found_handler:
             logger.warning("警告：无法找到控制台日志处理器，--fast-forward可能无法完全生效。")

    run_simple()
