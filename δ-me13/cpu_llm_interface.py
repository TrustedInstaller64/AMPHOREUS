# cpu_llm_interface.py

import os
from config import config

import logging 
logger = logging.getLogger("OmphalosLogger") 

import platform

class CpuLlmInterface:
    def __init__(self, model_folder="models"):
        """
        初始化并加载GGUF模型。
        """
        if not config['llm']['enable_llm']:
            logger.info("\033[93m大模型功能已禁用。请在配置文件或命令行中启用。\033[0m")
            self.llm = None
            return

        # 只有在LLM启用时才尝试导入 llama_cpp
        try:
            from llama_cpp import Llama
        except ImportError:
            logger.info("\n\033[91m错误：未安装 llama_cpp 库。请运行 'pip install llama-cpp-python' 或使用 --disable-llm 参数。\033[0m")
            self.llm = None
            return

        model_name = config['llm']['model_name']
        model_path = os.path.join(model_folder, model_name)

        # 检查是否启用 MLX 后端
        self.mlx_enabled = config.get('ane', {}).get('llm_mlx_backend', False)
        self._use_mlx_backend = False
        if self.mlx_enabled:
            self._init_mlx_backend(model_folder)
            if self._use_mlx_backend:
                return  # MLX 加载成功，跳过 llama.cpp

        if not os.path.exists(model_path):
            logger.info(f"\n\033[91m错误：找不到模型文件！\033[0m")
            logger.info(f"请确保你已经下载了 '{model_name}'")
            logger.info(f"并将其放置在项目的 '{model_folder}' 文件夹中。")
            return

        # 检测操作系统与处理器，为不同平台选择最优配置
        system = platform.system()
        processor = platform.processor()
        is_apple_silicon = (system == "Darwin" and processor == "arm")

        if is_apple_silicon:
            n_gpu_layers = 35
            n_threads = 8
            logger.info("\033[92m检测到Apple Silicon芯片，启用Metal加速...\033[0m")
        else:
            n_gpu_layers = config['llm']['gpu_max_count']
            n_threads = os.cpu_count()

        try:
            self.llm = Llama(
                model_path=model_path,
                n_ctx=4096,
                n_gpu_layers=n_gpu_layers,
                n_threads=n_threads,
                verbose=False,
                enable_thinking=config['llm']['enable_thinking']
            )
            logger.info("\033[92m模型加载成功！使用 %d GPU层和 %d CPU线程。翁法罗斯拥有了新的低语者。\033[0m", n_gpu_layers, n_threads)
        except Exception as e:
            logger.info("\033[93mGPU加速模型加载失败: %s\033[0m", e)
            logger.info("\033[93m尝试回退到纯CPU模式...\033[0m")
            try:
                self.llm = Llama(
                    model_path=model_path,
                    n_ctx=4096,
                    n_gpu_layers=0,
                    n_threads=os.cpu_count(),
                    verbose=False
                )
                logger.info("\033[93m已回退到纯CPU模式运行。\033[0m")
            except Exception as e2:
                logger.info("\033[91mCPU模式也加载失败: %s\033[0m", e2)
                self.llm = None

    def _init_mlx_backend(self, model_folder):
        """使用 Apple MLX 框架加载 Qwen3，利用 ANE 加速."""
        try:
            from mlx_lm import load, generate
        except ImportError:
            logger.info("\033[93mMLX 后端未安装。请运行: pip install mlx mlx-lm\033[0m")
            self.llm = None
            return

        mlx_model_name = config.get('ane', {}).get('mlx_model_name', 'mlx-community/Qwen3-0.6B-4bit')

        try:
            logger.info("\033[96m🧠 正在通过 MLX 加载模型 (可能使用 ANE)...\033[0m")
            self._mlx_model, self._mlx_tokenizer = load(mlx_model_name)
            self._use_mlx_backend = True
            self._mlx_generate = generate
            logger.info("\033[92mMLX 模型加载成功！Qwen3 将通过 ANE 加速推理。\033[0m")
        except Exception as e:
            logger.info("\033[93mMLX 模型加载失败: %s，回退到 llama.cpp\033[0m", e)
            self.llm = None
            self.mlx_enabled = False

    def generate_response(self, prompt, max_tokens=120, timeout=30):
        """
        使用加载的模型生成响应（30 秒超时，防止无限阻塞）。
        """
        import concurrent.futures
        if self.llm is None and not getattr(self, '_use_mlx_backend', False):
            return "错误：模型未能成功加载，无法生成响应。"

        def _do_generate():
            if getattr(self, '_use_mlx_backend', False):
                response = self._mlx_generate(
                    self._mlx_model, self._mlx_tokenizer,
                    prompt=prompt, max_tokens=max_tokens, temperature=0.7,
                )
                return response.strip()
            messages = [
                {"role": "system", "content": "You are a helpful assistant. /no_think"},
                {"role": "user", "content": prompt}
            ]
            output = self.llm.create_chat_completion(
                messages=messages, max_tokens=max_tokens, temperature=0.7,
            )
            return str(output["choices"][0]["message"]["content"].strip())

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(_do_generate)
                return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            return "(LLM 响应超时)"
        except Exception as e:
            return f"(LLM 错误: {e})"
