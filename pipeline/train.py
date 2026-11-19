#!/usr/bin/env python3
"""Fine-tune a small Hugging Face causal LM on the BMW news corpus (news.json).

Checkpoints are written to <output>/checkpoint-<step>/ every --save-steps steps and
at the end of each epoch. Params and metrics are logged to MLflow (set
MLFLOW_TRACKING_URI to use a server; defaults to ./mlruns).

Resuming: each checkpoint also stores the optimizer state, step, epoch, position
within the epoch, RNG state and MLflow run id. If the script is restarted with the
same output dir, it continues from the latest checkpoint exactly where it stopped
(logging into the same MLflow run). Checkpoints made from different training data
or settings are never resumed: they are moved to <output>/stale-<time>/ and training
starts fresh. Use --no-resume to force a fresh start. When training finishes,
<output>/training_complete.json is written.

Usage:
    pip install torch transformers mlflow
    python3 train.py                                   # news.json -> ./bmw-llm
    python3 train.py -i train.json --val val.json --save-steps 50
    python3 train.py -i train.json --val val.json --log-model   # also upload final model to MLflow
"""
import argparse
import glob
import hashlib
import json
import math
import os
import shutil
import time

import mlflow
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

STATE_FILE = "trainer_state.pt"
COMPLETE_FILE = "training_complete.json"


def load_corpus(path):
    with open(path, encoding="utf-8") as f:
        items = json.load(f)
    return [f"{i['title']}\n\n{i.get('text') or i.get('summary', '')}" for i in items]


def make_blocks(texts, tok, block_size):
    ids = []
    for t in texts:
        ids += tok(t)["input_ids"] + [tok.eos_token_id]
    n = len(ids) // block_size * block_size
    return torch.tensor(ids[:n]).view(-1, block_size)


@torch.no_grad()
def evaluate(model, blocks, batch_size, device):
    model.eval()
    total = 0.0
    for i in range(0, len(blocks), batch_size):
        batch = blocks[i : i + batch_size].to(device)
        total += model(input_ids=batch, labels=batch).loss.item() * len(batch)
    model.train()
    return total / len(blocks)


def fingerprint(blocks, args):
    """Identifies the training data + settings a checkpoint belongs to."""
    h = hashlib.sha256(blocks.numpy().tobytes())
    h.update(f"{args.model}|{args.block_size}|{args.batch_size}".encode())
    return h.hexdigest()


def save_checkpoint(model, tok, path, state=None):
    """Write atomically (tmp dir + rename) so a crash never leaves a half-written checkpoint."""
    tmp = path + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    model.save_pretrained(tmp)
    tok.save_pretrained(tmp)
    if state is not None:
        torch.save(state, os.path.join(tmp, STATE_FILE))
    shutil.rmtree(path, ignore_errors=True)
    os.replace(tmp, path)
    print(f"  saved checkpoint {path}/")


def latest_checkpoint(output):
    """Highest-step checkpoint-<step>/ that contains resumable trainer state."""
    best = None
    for d in glob.glob(os.path.join(output, "checkpoint-*")):
        step = d.rsplit("-", 1)[-1]
        if step.isdigit() and os.path.isfile(os.path.join(d, STATE_FILE)):
            if best is None or int(step) > best[0]:
                best = (int(step), d)
    return best[1] if best else None


def archive_checkpoints(output):
    dirs = [d for d in glob.glob(os.path.join(output, "checkpoint-*")) if os.path.isdir(d)]
    if not dirs:
        return
    dest = os.path.join(output, time.strftime("stale-%Y%m%d-%H%M%S"))
    os.makedirs(dest)
    for d in dirs:
        shutil.move(d, dest)
    print(f"Moved {len(dirs)} old checkpoint(s) to {dest}/")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-i", "--input", default="news.json")
    ap.add_argument("--val", help="validation JSON (e.g. val.json from split.py)")
    ap.add_argument("-o", "--output", default="bmw-llm")
    ap.add_argument("--model", default="HuggingFaceTB/SmolLM2-135M")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--block-size", type=int, default=256)
    ap.add_argument("--lr", type=float, default=5e-5)
    ap.add_argument("--save-steps", type=int, default=0, help="checkpoint every N steps (0 = only per epoch)")
    ap.add_argument("--log-steps", type=int, default=10, help="log train loss every N steps")
    ap.add_argument("--experiment", default="bmw-llm", help="MLflow experiment name")
    ap.add_argument("--log-model", action="store_true", help="upload final model to MLflow as artifacts")
    ap.add_argument("--no-resume", action="store_true",
                    help="ignore existing checkpoints (they are moved to <output>/stale-*/) and start fresh")
    args = ap.parse_args()

    device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(args.model)
    blocks = make_blocks(load_corpus(args.input), tok, args.block_size)
    val_blocks = make_blocks(load_corpus(args.val), tok, args.block_size) if args.val else None
    if val_blocks is not None and len(val_blocks) == 0:
        print("warning: validation set is shorter than one block, skipping validation")
        val_blocks = None
    fp = fingerprint(blocks, args)

    # --- find a checkpoint to resume from ---
    state = None
    ckpt = None if args.no_resume else latest_checkpoint(args.output)
    if ckpt:
        state = torch.load(os.path.join(ckpt, STATE_FILE), map_location="cpu")
        if state.get("fingerprint") != fp:
            print(f"Checkpoint {ckpt} was trained on different data/settings; not resuming.")
            state, ckpt = None, None
    if ckpt is None:
        archive_checkpoints(args.output)

    model = AutoModelForCausalLM.from_pretrained(ckpt or args.model).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr)
    step, start_epoch, start_i, run_id = 0, 1, 0, None
    epoch_total, epoch_batches, perm = 0.0, 0, None
    if state:
        opt.load_state_dict(state["optimizer"])
        for g in opt.param_groups:
            g["lr"] = args.lr
        torch.set_rng_state(state["rng"])
        step, start_epoch, start_i = state["step"], state["epoch"], state["batch_start"]
        epoch_total, epoch_batches, perm = state["epoch_total"], state["epoch_batches"], state["perm"]
        run_id = state.get("mlflow_run_id")
        print(f"Resuming from {ckpt} (step {step}, epoch {start_epoch}, batch offset {start_i})")
    print(f"{len(blocks)} train blocks of {args.block_size} tokens, training on {device}")

    complete_marker = os.path.join(args.output, COMPLETE_FILE)
    if os.path.exists(complete_marker) and start_epoch <= args.epochs:
        os.remove(complete_marker)  # (more) training to do

    mlflow.set_experiment(args.experiment)
    if run_id:
        try:
            mlflow.get_run(run_id)
        except Exception:
            print(f"warning: MLflow run {run_id} not found, logging to a new run")
            run_id = None
    with mlflow.start_run(run_id=run_id) as run:
        if run_id is None:
            mlflow.log_params({**vars(args), "device": device, "train_blocks": len(blocks),
                               "val_blocks": len(val_blocks) if val_blocks is not None else 0})

        def trainer_state(epoch, batch_start, perm, total, n_batches):
            return {"fingerprint": fp, "step": step, "epoch": epoch, "batch_start": batch_start,
                    "perm": perm, "epoch_total": total, "epoch_batches": n_batches,
                    "optimizer": opt.state_dict(), "rng": torch.get_rng_state(),
                    "mlflow_run_id": run.info.run_id}

        model.train()
        for epoch in range(start_epoch, args.epochs + 1):
            if epoch == start_epoch and perm is not None:
                total, n_batches, first = epoch_total, epoch_batches, start_i  # resume mid-epoch
            else:
                perm, total, n_batches, first = torch.randperm(len(blocks)), 0.0, 0, 0
            for i in range(first, len(blocks), args.batch_size):
                batch = blocks[perm[i : i + args.batch_size]].to(device)
                loss = model(input_ids=batch, labels=batch).loss
                loss.backward()
                opt.step()
                opt.zero_grad()
                step += 1
                total += loss.item()
                n_batches += 1
                if step % args.log_steps == 0:
                    mlflow.log_metric("train_loss", loss.item(), step=step)
                if args.save_steps and step % args.save_steps == 0:
                    save_checkpoint(model, tok, f"{args.output}/checkpoint-{step}",
                                    trainer_state(epoch, i + args.batch_size, perm, total, n_batches))

            metrics = {"epoch_train_loss": total / max(n_batches, 1), "epoch": epoch}
            msg = f"epoch {epoch}/{args.epochs}  loss {metrics['epoch_train_loss']:.4f}"
            if val_blocks is not None:
                val_loss = evaluate(model, val_blocks, args.batch_size, device)
                metrics |= {"val_loss": val_loss, "val_perplexity": math.exp(val_loss)}
                msg += f"  val_loss {val_loss:.4f}  val_ppl {metrics['val_perplexity']:.2f}"
            mlflow.log_metrics(metrics, step=step)
            print(msg)
            save_checkpoint(model, tok, f"{args.output}/checkpoint-{step}",
                            trainer_state(epoch + 1, 0, None, 0.0, 0))

        if start_epoch > args.epochs:
            print(f"Training already completed ({args.epochs} epochs); finalizing.")
        model.save_pretrained(args.output)  # not save_checkpoint: that would replace the dir holding the checkpoints
        tok.save_pretrained(args.output)
        if args.log_model:
            for name in os.listdir(args.output):  # top-level model files only, not checkpoint-* dirs
                path = os.path.join(args.output, name)
                if os.path.isfile(path) and name != COMPLETE_FILE:
                    mlflow.log_artifact(path, artifact_path="model")

        model.eval()
        prompt = tok("The new BMW", return_tensors="pt").to(device)
        out = model.generate(**prompt, max_new_tokens=50, do_sample=True, top_p=0.9, pad_token_id=tok.eos_token_id)
        sample = tok.decode(out[0], skip_special_tokens=True)
        mlflow.log_text(sample, "sample.txt")
        print("Sample:", sample)

        with open(complete_marker, "w") as f:
            json.dump({"step": step, "epochs": args.epochs, "fingerprint": fp,
                       "mlflow_run_id": run.info.run_id}, f, indent=2)


if __name__ == "__main__":
    main()
