#!/usr/bin/env bash
# Full pipeline: scrape BMW news -> train/val/test split -> fine-tune with MLflow logging.
# Meant to run inside the container; data files are written to the current directory.
#
# Env overrides: EPOCHS (3), SAVE_STEPS (0), MODEL, OUTPUT (bmw-llm),
#                MLFLOW_TRACKING_URI (default ./mlruns)
#
# Restart-safe: if a previous run was interrupted during training, rerunning this
# script skips scraping/splitting and train.py resumes from the latest checkpoint.
set -euo pipefail
SCRIPTS="$(cd "$(dirname "$0")" && pwd)"

EPOCHS=${EPOCHS:-3}
SAVE_STEPS=${SAVE_STEPS:-0}
MODEL=${MODEL:-HuggingFaceTB/SmolLM2-135M}
OUTPUT=${OUTPUT:-bmw-llm}

# An unfinished training run (checkpoints with trainer state, no completion marker)
# is resumed by train.py. Keep its data unchanged: don't re-scrape or re-split.
if [[ ! -f "$OUTPUT/training_complete.json" ]] \
   && compgen -G "$OUTPUT/checkpoint-*/trainer_state.pt" >/dev/null \
   && [[ -f train.json ]]; then
    echo "==> Unfinished training found in $OUTPUT; skipping scrape/split to resume on the same data"
else
    echo "==> 1/4 Scraping news"
    python3 "$SCRIPTS/bmw_news.py" -o news.json

    echo "==> 2/4 Splitting corpus"
    python3 "$SCRIPTS/split.py" -i news.json
fi

echo "==> 3/4 Training"
python3 "$SCRIPTS/train.py" -i train.json --val val.json -o "$OUTPUT" \
    --model "$MODEL" --epochs "$EPOCHS" --save-steps "$SAVE_STEPS"

echo "==> 4/4 Evaluating on test set"
python3 "$SCRIPTS/evaluate.py" -m "$OUTPUT" -i test.json

echo "==> Done. Model in $(pwd)/$OUTPUT"
