#!/usr/bin/env sh
set -eu

mkdir -p /io /io/.hf_cache /io/mineru-output

# MindIE / CANN 运行环境（镜像内常见脚本；存在则 source）
for envf in \
  /usr/local/Ascend/ascend-toolkit/set_env.sh \
  /usr/local/Ascend/nnal/atb/set_env.sh \
  /usr/local/Ascend/mindie/set_env.sh \
  /usr/local/Ascend/llm_model/set_env.sh \
  /usr/local/Ascend/mindformers/set_env.sh; do
  if [ -f "$envf" ]; then
    # shellcheck disable=SC1090
    . "$envf"
  fi
done

# 构建阶段写入的厂商 / 系统 Python bin
if [ -f /etc/ascend-python-bindir ]; then
  _py_bin="$(cat /etc/ascend-python-bindir)"
  if [ -n "$_py_bin" ] && [ -d "$_py_bin" ]; then
    export PATH="${_py_bin}:${PATH}"
  fi
fi
if [ -f /etc/profile.d/ascend-python.sh ]; then
  # shellcheck disable=SC1091
  . /etc/profile.d/ascend-python.sh
fi
# 系统 Python + MindIE site-packages 场景
if [ -f /etc/mineru-extra-pythonpath ]; then
  _site="$(cat /etc/mineru-extra-pythonpath)"
  if [ -n "$_site" ]; then
    export PYTHONPATH="${_site}${PYTHONPATH:+:$PYTHONPATH}"
  fi
fi

# 兼容 Nvidia wheel 布局（MindIE 栈通常无这些目录；存在则追加）
_NVIDIA_LIB_DIRS=""
for _d in \
  /usr/local/lib/python3.10/dist-packages/nvidia/cudnn/lib \
  /usr/local/lib/python3.10/dist-packages/nvidia/cublas/lib \
  /usr/local/lib/python3.10/dist-packages/nvidia/cuda_runtime/lib; do
  if [ -d "$_d" ]; then
    if [ -n "$_NVIDIA_LIB_DIRS" ]; then
      _NVIDIA_LIB_DIRS="${_NVIDIA_LIB_DIRS}:$_d"
    else
      _NVIDIA_LIB_DIRS="$_d"
    fi
  fi
done
if [ -n "$_NVIDIA_LIB_DIRS" ]; then
  export LD_LIBRARY_PATH="${_NVIDIA_LIB_DIRS}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi

exec "$@"
