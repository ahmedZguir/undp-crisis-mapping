#!/usr/bin/env bash
set -euo pipefail

export HOME=/export/home/azguir

source /export/home/azguir/anaconda3/etc/profile.d/conda.sh
conda activate vllm


export CUDA_VISIBLE_DEVICES=0


exec vllm serve Qwen/Qwen3-Embedding-4B \
    --served-model-name Qwen3-Embedding-4B \
    --task embed \
    --dtype bfloat16 \
    --max-model-len 8192 \
    --gpu-memory-utilization 0.35 \
    --host 0.0.0.0 \
    --port 8998
