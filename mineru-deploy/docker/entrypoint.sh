#!/usr/bin/env sh
set -eu

mkdir -p /io /io/.hf_cache /io/mineru-output

# 优先使用 PyTorch wheel 自带的 nvidia/cudnn 等库，避免与基础镜像 cuDNN 混用导致 CUDNN_STATUS_NOT_INITIALIZED
_NVIDIA_LIB_DIRS=""
for _d in \
  /usr/local/lib/python3.10/dist-packages/nvidia/cudnn/lib \
  /usr/local/lib/python3.10/dist-packages/nvidia/cublas/lib \
  /usr/local/lib/python3.10/dist-packages/nvidia/cuda_runtime/lib \
  /usr/local/lib/python3.10/dist-packages/nvidia/cusparse/lib \
  /usr/local/lib/python3.10/dist-packages/nvidia/cusolver/lib \
  /usr/local/lib/python3.10/dist-packages/nvidia/cufft/lib \
  /usr/local/lib/python3.10/dist-packages/nvidia/curand/lib \
  /usr/local/lib/python3.10/dist-packages/nvidia/nvjitlink/lib; do
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
