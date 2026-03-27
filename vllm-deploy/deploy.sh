#!/bin/bash
# 一键部署脚本 - 支持任意位置运行

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"

echo "=========================================="
echo "vLLM 企业级部署脚本"
echo "项目目录: $PROJECT_ROOT"
echo "=========================================="

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "错误: 未找到 python3"
    exit 1
fi

# 检查配置文件
if [ ! -f "$PROJECT_ROOT/config/vllm.yaml" ]; then
    echo "错误: 配置文件不存在"
    echo "请先配置 $PROJECT_ROOT/config/vllm.yaml"
    exit 1
fi

# 创建日志目录
mkdir -p "$PROJECT_ROOT/logs"

# 安装依赖
echo "安装 Python 依赖..."
pip3 install -q vllm pyyaml requests psutil 2>/dev/null || {
    echo "警告: 依赖安装失败，请手动安装"
}

# 设置执行权限
chmod +x "$PROJECT_ROOT/scripts/"*.py

# 启动服务
echo "启动 vLLM 服务..."
cd "$PROJECT_ROOT"
python3 scripts/start.py start

echo "=========================================="
echo "部署完成！"
echo "服务地址: http://localhost:8000"
echo "健康检查: http://localhost:8000/health"
echo "日志目录: $PROJECT_ROOT/logs"
echo "=========================================="