#!/usr/bin/env bash
set -eo pipefail

# Launch external vLLM OpenAI-compatible server for V3 remote-client mode.
#
# Terminal 1:
#   bash UNDP_disasterVL_v3/start_vllm_server.sh
#
# Terminal 2:
#   python -m UNDP_disasterVL_v3.run_sample --backend remote /path/to/image.jpg


set -u

export CUDA_VISIBLE_DEVICES="1"
export VLLM_PORT=8500

BASE_MODEL="${UNDP_V3_REMOTE_BASE_MODEL:-${UNDP_V3_BASE_MODEL:-Qwen/Qwen3.5-9B}}"
DISASTER_MODEL="${UNDP_V3_REMOTE_DISASTER_MODEL:-${UNDP_V3_ADAPTER_NAME:-disastervl}}"
ADAPTER_PATH="${UNDP_V3_ADAPTER_PATH:-/data-sas/undp/models/model/best_checkpoint}"
HOST="${VLLM_HOST:-0.0.0.0}"
PORT="${VLLM_PORT:-8000}"
MAX_LORA_RANK="${UNDP_V3_MAX_LORA_RANK:-32}"
GPU_MEM="${UNDP_V3_GPU_MEMORY_UTILIZATION:-0.6}"
# Multi-GPU: set UNDP_V3_TENSOR_PARALLEL_SIZE=2 (or 4) and expose the GPUs via
# CUDA_VISIBLE_DEVICES="2,3".  TP=2 roughly halves per-request latency for Qwen3.5-9B.
TP="${UNDP_V3_TENSOR_PARALLEL_SIZE:-1}"
MAX_MODEL_LEN="${UNDP_V3_MAX_MODEL_LEN:-4096}"
# vLLM 0.18.0 crashes during CUDA graph compilation with the installed torch version
# (AttributeError: standalone_compile does not have attribute 'FakeTensorMode').
# Eager mode is required until this is resolved by a vLLM upgrade.
# Once fixed, set UNDP_V3_ENFORCE_EAGER=0 to recover the ~15-30% latency gain.
ENFORCE_EAGER="${UNDP_V3_ENFORCE_EAGER:-1}"

CMD=(
  vllm serve "$BASE_MODEL"
  --host "$HOST" \
  --port "$PORT" \
  --enable-lora \
  --lora-modules "${DISASTER_MODEL}=${ADAPTER_PATH}" \
  --max-lora-rank "$MAX_LORA_RANK" \
  --tensor-parallel-size "$TP" \
  --gpu-memory-utilization "$GPU_MEM" \
  --max-model-len "$MAX_MODEL_LEN" \
  --limit-mm-per-prompt '{"image":1}'
)

if [[ "$ENFORCE_EAGER" == "1" || "$ENFORCE_EAGER" == "true" || "$ENFORCE_EAGER" == "yes" ]]; then
  CMD+=(--enforce-eager)
fi

echo "Launching vLLM server"
echo "  base model:        ${BASE_MODEL}"
echo "  DisasterVL model:  ${DISASTER_MODEL}"
echo "  adapter path:      ${ADAPTER_PATH}"
echo "  API endpoint:      http://${HOST}:${PORT}/v1"
echo "  max model length:  ${MAX_MODEL_LEN}"
echo "  tensor parallelism:${TP}"
echo "  enforce eager:     ${ENFORCE_EAGER}"

"${CMD[@]}"
