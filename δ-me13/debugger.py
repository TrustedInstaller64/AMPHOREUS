import os
import sys
import threading
import time
import select

try:
    import msvcrt
except ImportError:
    import tty, termios, select

from constants import BAIE_STAGNATION_THRESHOLD, PATH_NAMES, TITAN_NAMES

import logging
logger = logging.getLogger("OmphalosLogger")

class Debugger:
    def __init__(self, simulation, gui_mode=False):
        self.sim = simulation
        self.paused = False
        self.last_command = ''
        self._gui_mode = gui_mode

        self._stdin_lock = threading.Event()
        self._stdin_lock.set()

        if gui_mode:
            self._start_gui_stdin_reader()
        else:
            self._setup_raw_terminal()
            self._keyboard_thread = threading.Thread(target=self._listen_for_keys, daemon=True)
            self._keyboard_thread.start()

    def _restore_terminal(self):
        """恢复终端设置"""
        if hasattr(self, 'old_termios'):
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self.old_termios)

    def _get_char_mac(self):
        """Mac专用的非阻塞字符读取"""
        import select
        if select.select([sys.stdin], [], [], 0) == ([sys.stdin], [], []):
            char = sys.stdin.read(1)
            return char.lower() if char else None
        return None

    def _get_char(self):
        """跨平台字符读取，针对Mac优化"""
        if sys.platform == "darwin":
            return self._get_char_mac()
        else:
            try:
                import msvcrt
                if msvcrt.kbhit():
                    return msvcrt.getch().decode('utf-8').lower()
            except ImportError:
                import termios, tty, select
                fd = sys.stdin.fileno()
                old_settings = termios.tcgetattr(fd)
                try:
                    tty.setraw(sys.stdin.fileno())
                    if select.select([sys.stdin], [], [], 0) == ([sys.stdin], [], []):
                        return sys.stdin.read(1).lower()
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return None

    def _listen_for_keys(self):
        """监听键盘输入的独立线程"""
        while True:
            if self._stdin_lock.is_set():
                char = self._get_char()
                if char == 'p':
                    self.paused = not self.paused
                    time.sleep(0.1)
            time.sleep(0.05)

    def __del__(self):
        if not self._gui_mode:
            self._restore_terminal()

    def _start_gui_stdin_reader(self):
        """GUI 模式：后台线程从 stdin 读取 JSON 命令。"""
        def _reader():
            import json
            while True:
                try:
                    line = sys.stdin.readline()
                    if not line:
                        break
                    cmd = json.loads(line.strip())
                    cmd_type = cmd.get("type", "")
                    if cmd_type == "pause":
                        self.paused = True
                    elif cmd_type == "resume":
                        self.paused = False
                    elif cmd_type == "cmd":
                        self._execute_gui_command(cmd.get("text", ""))
                    elif cmd_type == "set_param":
                        key = cmd.get("key")
                        val = cmd.get("val")
                        if key and val is not None and hasattr(self.sim, key):
                            try:
                                current = getattr(self.sim, key)
                                setattr(self.sim, key, type(current)(val))
                                logger.info(f"参数 '{key}' 已设为 {val}")
                            except (ValueError, TypeError):
                                logger.info(f"无法设置 '{key}' = {val}")
                except (json.JSONDecodeError, Exception):
                    pass
        t = threading.Thread(target=_reader, daemon=True)
        t.start()

    def _execute_gui_command(self, cmd_text: str):
        """GUI 模式：执行命令字符串，输出通过 logger 发送到 GUI stdout。"""
        if not cmd_text.strip():
            return
        parts = cmd_text.strip().split()
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in ('c', 'continue'):
            self.paused = False
        elif cmd in ('n', 'next'):
            self.last_command = 'next'
            self.paused = False
        elif cmd in ('p', 'print'):
            if not args:
                logger.info("用法: p <name|baie>")
                return
            entity_name = ' '.join(args)
            entity = None
            if entity_name.lower() == 'baie':
                entity = self.sim.reincarnator
            else:
                entity = self.sim.name_to_entity_map.get(entity_name)
            if entity:
                logger.info("--- 实体详情: %s ---", entity.name)
                logger.info(str(entity))
                logger.info("  泰坦亲和度:")
                for i, name in enumerate(TITAN_NAMES):
                    logger.info("    %s: %.2f", name, entity.titan_affinities[i])
                logger.info("  命途倾向:")
                for i, name in enumerate(PATH_NAMES):
                    logger.info("    %s: %.3f", name, entity.path_affinities[i])
                logger.info("---")
            else:
                logger.info("未找到实体: %s", entity_name)
        elif cmd == 'top':
            k = int(args[0]) if args and args[0].isdigit() else 5
            sorted_pop = sorted(self.sim.population, key=lambda p: p.score, reverse=True)
            logger.info("--- 评分 Top %d ---", k)
            for i, p in enumerate(sorted_pop[:k]):
                logger.info("%d. %s", i+1, str(p))
            logger.info("---")
        elif cmd == 'status':
            diversity = 0
            if self.sim.population:
                diversity = len(set(p.dominant_path_idx for p in self.sim.population)) / len(PATH_NAMES)
            logger.info("--- 翁法罗斯状态 ---")
            logger.info("  世代: %d/%d", self.sim.generation, self.sim.total_generations)
            logger.info("  种群: %d", len(self.sim.population))
            logger.info("  多样性: %.1f%%", diversity*100)
            logger.info("  突变率: %.4f", self.sim.stagnation_manager.mutation_rate)
            logger.info("---")
        elif cmd == 'zeitgeist':
            logger.info("--- 翁法罗斯思潮 ---")
            for name, weight in sorted(zip(PATH_NAMES, self.sim.cosmic_zeitgeist), key=lambda x: x[1], reverse=True):
                logger.info("  %s: %+.4f", name, weight)
            logger.info("---")
        elif cmd == 'blueprint':
            logger.info("--- 演化蓝图 ---")
            for name, aff in sorted(zip(TITAN_NAMES, self.sim.base_titan_affinities), key=lambda x: x[1], reverse=True):
                logger.info("  %s: %.4f", name, aff)
            logger.info("---")
        elif cmd == 'set':
            if len(args) != 2:
                logger.info("用法: set <参数> <值>")
                return
            param, value = args[0], args[1]
            if hasattr(self.sim, param):
                try:
                    current = getattr(self.sim, param)
                    setattr(self.sim, param, type(current)(value))
                    logger.info("参数 '%s' 已设为 %s", param, value)
                except (ValueError, TypeError):
                    logger.info("无法设置 '%s' = %s", param, value)
        elif cmd == 'help':
            logger.info("命令: c/n/p/top/status/zeitgeist/blueprint/set/save/load/help")
        else:
            logger.info("未知命令: %s", cmd)

    def handle_commands(self):
        """在模拟暂停时处理用户输入的命令（终端模式）。"""
        while self.paused:
            try:
                self._temp_restore_for_input()
                try:
                    command_line_str = input("\033[93m(翁法罗斯创世涡心) > \033[0m").strip()
                finally:
                    if sys.platform == "darwin":
                        self._setup_raw_terminal()

                if not command_line_str:
                    continue

                command_line = command_line_str.split()
                cmd = command_line[0].lower()
                args = command_line[1:]

                if cmd in ('c', 'continue'):
                    self.paused = False
                    break
                elif cmd in ('n', 'next'):
                    self.last_command = 'next'
                    self.paused = False
                    break
                elif cmd in ('p', 'print'):
                    if not args:
                        logger.info("错误: 请提供实体名称或 'baie'。用法: p <name|baie>")
                        continue
                    entity_name = ' '.join(args)
                    entity = None
                    if entity_name.lower() == 'baie':
                        entity = self.sim.reincarnator
                        if not entity:
                            logger.info("错误: 当前没有卡厄斯兰那实体。")
                            continue
                    else:
                        entity = self.sim.name_to_entity_map.get(entity_name)
                    if entity:
                        logger.info(f"\n--- 实体详情: {entity.name} ---")
                        logger.info(entity)
                        logger.info("  泰坦亲和度:")
                        for i, name in enumerate(TITAN_NAMES):
                            logger.info(f"    {name:<4}: {entity.titan_affinities[i]:.2f}")
                        logger.info("  命途倾向:")
                        for i, name in enumerate(PATH_NAMES):
                            logger.info(f"    {name:<4}: {entity.path_affinities[i]:.3f}")
                        logger.info("---")
                    else:
                        logger.info(f"错误: 未找到名为 '{entity_name}' 的实体。")
                elif cmd == 'top':
                    k = int(args[0]) if args and args[0].isdigit() else 5
                    sorted_pop = sorted(self.sim.population, key=lambda p: p.score, reverse=True)
                    top_k = sorted_pop[:k]
                    logger.info(f"\n--- 当前评分 Top {k} ---")
                    for i, p in enumerate(top_k):
                        logger.info(f"{i+1}. {p}")
                    logger.info("---")
                elif cmd == 'status':
                    diversity = 0
                    if self.sim.population:
                        diversity = len(set(p.dominant_path_idx for p in self.sim.population)) / len(PATH_NAMES)
                    logger.info("\n--- 翁法罗斯状态报告 ---")
                    logger.info(f"  世代: {self.sim.generation}/{self.sim.total_generations}")
                    logger.info(f"  种群数量: {len(self.sim.population)}")
                    logger.info(f"  生态多样性: {diversity:.2%}")
                    logger.info(f"  当前突变率: {self.sim.stagnation_manager.mutation_rate:.4f}")
                    if self.sim.stagnation_manager.long_term_stagnation_counter:
                        logger.info(f"  全局停滞计数: {self.sim.stagnation_manager.long_term_stagnation_counter} / 10 (触发唤醒)")
                    if self.sim.reincarnator:
                        logger.info(f"  白厄停滞计数: {self.sim.stagnation_manager.baie_stagnation_counter} / {BAIE_STAGNATION_THRESHOLD}")
                    logger.info("---")
                elif cmd == 'zeitgeist':
                    logger.info("\n--- 当前翁法罗斯思潮 ---")
                    zeitgeist_status = sorted(zip(PATH_NAMES, self.sim.cosmic_zeitgeist), key=lambda item: item[1], reverse=True)
                    for name, weight in zeitgeist_status:
                        logger.info(f"  {name:<4}: {weight:+.4f}")
                    logger.info("---")
                elif cmd == 'blueprint':
                    logger.info("\n--- 当前演化蓝图 ---")
                    blueprint_status = sorted(zip(TITAN_NAMES, self.sim.base_titan_affinities), key=lambda item: item[1], reverse=True)
                    for name, affinity in blueprint_status:
                        logger.info(f"  {name:<4}: {affinity:.4f}")
                    logger.info("---")
                elif cmd == 'set':
                    if len(args) != 2:
                        logger.info("错误: 用法: set <parameter_name> <value>")
                        continue
                    param, value = args[0], args[1]
                    if hasattr(self.sim, param):
                        try:
                            current_val = getattr(self.sim, param)
                            setattr(self.sim, param, type(current_val)(value))
                            logger.info(f"成功: 参数 '{param}' 已被设置为 {value}。")
                        except (ValueError, TypeError):
                            logger.info(f"错误: 无法将 '{value}' 转换为 '{param}' 所需的类型。")
                    else:
                        logger.info(f"错误: 模拟中不存在名为 '{param}' 的参数。")
                elif cmd == 'save':
                    if not args:
                        logger.info("错误: 请提供存档文件名。用法: save <filename.json>")
                        continue
                    filepath = args[0]
                    self.sim.save_simulation_state(filepath)
                elif cmd == 'load':
                    if not args:
                        logger.info("错误: 请提供要加载的存档文件名。用法: load <filename.json>")
                        continue
                    filepath = args[0]
                    self.sim.load_simulation_state(filepath)
                    logger.info("状态已加载。输入 'c' 或 'n' 继续。")
                elif cmd == 'threads':
                    try:
                        ratio = float(args[0]) if args else 0
                        ratio = max(0.0, min(1.0, ratio))
                        from scheduler import set_manual_thread_ratio
                        set_manual_thread_ratio(ratio)
                        if ratio == 0:
                            logger.info("线程模式: AUTO EMA")
                        else:
                            cores = int(os.cpu_count() * ratio)
                            logger.info("线程模式: 手动 %d 核 (ratio=%.2f)", cores, ratio)
                    except (ValueError, IndexError):
                        logger.info("用法: threads <0~1>  (0 = AUTO EMA, 如 0.5 = 50%% 核心)")
                elif cmd == 'autosave':
                    try:
                        secs = int(args[0]) if args else 180
                        from scheduler import autosave_interval
                        autosave_interval[0] = max(10, secs)
                        logger.info("自动存档间隔: %d 秒", autosave_interval[0])
                    except (ValueError, IndexError):
                        logger.info("用法: autosave <秒>  (如 autosave 300)")
                elif cmd == 'stop':
                    logger.info("\n停止演算并保存至 AMPHOREUS.json...")
                    save_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "AMPHOREUS.json")
                    self.sim.save_simulation_state(save_path)
                    logger.info("进度已保存。正在退出...")
                    self._stop_requested = True
                    self.paused = False
                    break
                elif cmd == 'help':
                    logger.info("\n--- 可用命令 ---")
                    logger.info("  c, continue         : 继续模拟")
                    logger.info("  n, next             : 执行下一世代并暂停")
                    logger.info("  stop                : 存档并安全退出")
                    logger.info("  autosave <秒>       : 调整自动存档间隔 (当前每 3 分钟)")
                    logger.info("  threads <0~1>       : 手动设置线程比例 (0=AUTO EMA)")
                    logger.info("  p, print <name|baie>: 打印指定实体或当前卡厄斯兰那的详细信息")
                    logger.info("  top [k]             : 显示评分最高的k个实体 (默认 k=5)")
                    logger.info("  status              : 显示当前的翁法罗斯宏观状态")
                    logger.info("  zeitgeist           : 查看当前的翁法罗斯思潮权重")
                    logger.info("  blueprint           : 查看当前的演化蓝图亲和度")
                    logger.info("  set <param> <value> : 动态设置一个模拟参数")
                    logger.info("  save <file.json>    : 将当前模拟状态保存到文件")
                    logger.info("  load <file.json>    : 从文件加载模拟状态")
                    logger.info("  help                : 显示此帮助信息")
                    logger.info("---")
                else:
                    logger.info(f"错误: 未知命令 '{cmd}'。")
            except (KeyboardInterrupt, EOFError):
                logger.info("\n强制恢复模拟...")
                self.paused = False
                break

    def _temp_restore_for_input(self):
        """临时将终端恢复到标准模式，供 input() 使用。"""
        if not hasattr(self, 'old_termios'):
            return
        import termios
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self.old_termios)

    def _setup_raw_terminal(self):
        """将终端设置为cbreak模式（保留信号处理+输出处理）。"""
        if sys.platform != "darwin":
            return
        try:
            import termios
            import tty
            if not hasattr(self, 'old_termios'):
                self.old_termios = termios.tcgetattr(sys.stdin.fileno())
            tty.setcbreak(sys.stdin.fileno())
        except (termios.error, OSError):
            pass
