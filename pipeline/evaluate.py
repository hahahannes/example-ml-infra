#!/usr/bin/env python3
"""Evaluate a fine-tuned model on the test split: loss and perplexity.

If MLFLOW_TRACKING_URI is set and the model dir has training_complete.json (written
by train.py), the test metrics are also logged to the MLflow run that trained it.

Usage:
    python3 evaluate.py                          # bmw-llm on test.json
    python3 evaluate.py -m bmw-llm/checkpoint-50 -i test.json
    python3 evaluate.py -o metrics.json          # also write the metrics as JSON
"""
import argparse
import json
import math
import os

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from train import COMPLETE_FILE, evaluate, load_corpus, make_blocks


def log_to_mlflow(model_dir, results):
    marker = os.path.join(model_dir, COMPLETE_FILE)
    if not os.environ.get("MLFLOW_TRACKING_URI") or not os.path.exists(marker):
        return
    import mlflow

    with open(marker) as f:
        run_id = json.load(f)["mlflow_run_id"]
    with mlflow.start_run(run_id=run_id):
        mlflow.log_metrics(results)
    print(f"Logged test metrics to MLflow run {run_id}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-m", "--model", default="bmw-llm")
    ap.add_argument("-i", "--input", default="test.json")
    ap.add_argument("-o", "--output", help="write metrics to this JSON file")
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--block-size", type=int, default=256)
    args = ap.parse_args()

    device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForCausalLM.from_pretrained(args.model).to(device)

    blocks = make_blocks(load_corpus(args.input), tok, args.block_size)
    if len(blocks) == 0:
        raise SystemExit(f"{args.input} is shorter than one block of {args.block_size} tokens")

    loss = evaluate(model, blocks, args.batch_size, device)
    results = {"test_loss": loss, "test_perplexity": math.exp(loss), "test_blocks": len(blocks)}
    print(f"{args.model} on {args.input} ({len(blocks)} blocks): "
          f"loss {loss:.4f}  perplexity {results['test_perplexity']:.2f}")

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
    log_to_mlflow(args.model, results)


if __name__ == "__main__":
    main()
