#!/usr/bin/env bash
set -euo pipefail

export HOME=/export/home/azguir

source /export/home/azguir/anaconda3/etc/profile.d/conda.sh
conda activate vllm

export CUDA_VISIBLE_DEVICES=1

exec vllm serve google/gemma-3-4b-it \
    --served-model-name google/gemma-3-4b-it \
    --tensor-parallel-size 1 \
    --dtype bfloat16 \
    --max-model-len 8192 \
    --limit-mm-per-prompt '{"image": 4}' \
    --gpu-memory-utilization 0.85 \
    --host 0.0.0.0 \
    --port 8111
