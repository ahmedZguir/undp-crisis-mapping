#!/usr/bin/env bash
set -euo pipefail

export HOME=/export/home/azguir

source /export/home/azguir/anaconda3/etc/profile.d/conda.sh
conda activate vllm_new

export CUDA_VISIBLE_DEVICES=1


# No --task / transcription flag: vLLM auto-detects the /v1/audio/transcriptions
# endpoint from the model's SupportsTranscription interface.
exec vllm serve Qwen/Qwen3-ASR-1.7B \
    --served-model-name Qwen/Qwen3-ASR-1.7B \
    --tensor-parallel-size 1 \
    --dtype bfloat16 \
    --gpu-memory-utilization 0.45 \
    --host 0.0.0.0 \
    --port 8112
    # gpu-memory-utilization kept low: shares GPU 2 with the embedding model (~0.55)
