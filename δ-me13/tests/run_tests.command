#!/bin/bash
cd "$(dirname "$0")/.."
python3 tests/test_scheduler.py
echo ""
echo "日志已保存至: tests/TestLog/"
echo "按任意键继续..."
read -n 1
