#!/usr/bin/env python3
"""Kubeflow Pipelines (v2) definition of the BMW news LLM pipeline.

Same steps as pipeline.sh, each one a pipeline task running in the project image:

    scrape (bmw_news.py) -> split (split.py) -> train (train.py) -> evaluate (test set)

Data and the model are passed between steps as KFP artifacts (stored in the KFP
object store). Training and test metrics go to the in-cluster MLflow server; the
test metrics are also kept as a JSON artifact (`metrics`) of the evaluate step.

Usage:
    pip install "kfp>=2.13,<3"
    python3 kfp_pipeline.py                         # compile -> bmw_llm_pipeline.yaml
    python3 kfp_pipeline.py --host http://localhost:8081 --epochs 1   # compile + start a run

Build-time settings (env vars, read when compiling):
    BMW_LLM_IMAGE        image built from ./Dockerfile (default: ghcr.io/hahahannes/bmw-llm:latest)
    MLFLOW_TRACKING_URI  MLflow server seen from the pipeline pods
                         (default: http://mlflow.mlflow.svc.cluster.local:5000)
"""
import argparse
import os

from kfp import compiler, dsl
from kfp.dsl import Artifact, Dataset, Input, Model, Output

IMAGE = os.environ.get("BMW_LLM_IMAGE", "ghcr.io/hahahannes/bmw-llm:latest")
MLFLOW_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://mlflow.mlflow.svc.cluster.local:5000")


@dsl.container_component
def scrape(news: Output[Dataset]):
    """Fetch the BMW Group Newsroom RSS feed into a JSON corpus."""
    return dsl.ContainerSpec(
        image=IMAGE,
        command=["python3", "/app/bmw_news.py"],
        args=["--fresh", "-o", news.path],
    )


@dsl.container_component
def split(
    news: Input[Dataset],
    train: Output[Dataset],
    val: Output[Dataset],
    test: Output[Dataset],
    val_frac: float,
    test_frac: float,
    seed: int,
):
    """Shuffle and split the corpus. split.py writes into the CWD, so run it in a temp dir."""
    return dsl.ContainerSpec(
        image=IMAGE,
        command=["sh", "-c"],
        args=[
            'set -e; cd "$(mktemp -d)"\n'
            'python3 /app/split.py -i "$0" --val "$1" --test "$2" --seed "$3"\n'
            'for f in "$4" "$5" "$6"; do mkdir -p "$(dirname "$f")"; done\n'
            'cp train.json "$4"; cp val.json "$5"; cp test.json "$6"',
            news.path, val_frac, test_frac, seed, train.path, val.path, test.path,
        ],
    )


@dsl.container_component
def train(
    train_data: Input[Dataset],
    val_data: Input[Dataset],
    model: Output[Model],
    base_model: str,
    epochs: int,
    batch_size: int,
    block_size: int,
    lr: float,
    experiment: str,
):
    """Fine-tune and export only the final model (not the per-epoch checkpoints)."""
    return dsl.ContainerSpec(
        image=IMAGE,
        command=["sh", "-c"],
        args=[
            'set -e; work="$(mktemp -d)"\n'
            'python3 /app/train.py -i "$0" --val "$1" -o "$work/out" --model "$3" '
            '--epochs "$4" --batch-size "$5" --block-size "$6" --lr "$7" --experiment "$8"\n'
            'mkdir -p "$2"\n'
            'find "$work/out" -maxdepth 1 -type f -exec cp {} "$2"/ \\;',
            train_data.path, val_data.path, model.path,
            base_model, epochs, batch_size, block_size, lr, experiment,
        ],
    )


@dsl.container_component
def evaluate(
    model: Input[Model],
    test_data: Input[Dataset],
    metrics: Output[Artifact],
    batch_size: int,
    block_size: int,
):
    """Test-set loss/perplexity as a JSON artifact; also logged to the training's MLflow run."""
    return dsl.ContainerSpec(
        image=IMAGE,
        command=["python3", "/app/evaluate.py"],
        args=[
            "-m", model.path, "-i", test_data.path, "-o", metrics.path,
            "--batch-size", batch_size, "--block-size", block_size,
        ],
    )


@dsl.pipeline(
    name="bmw-llm",
    description="Scrape BMW Group news, split, fine-tune a small causal LM and evaluate it on the test set.",
)
def bmw_llm_pipeline(
    base_model: str = "EleutherAI/pythia-70m",
    epochs: int = 3,
    batch_size: int = 4,
    block_size: int = 256,
    lr: float = 5e-5,
    val_frac: float = 0.1,
    test_frac: float = 0.1,
    seed: int = 42,
    experiment: str = "bmw-llm",
):
    scrape_task = scrape()
    scrape_task.set_caching_options(False)  # the feed changes; always fetch fresh news

    split_task = split(
        news=scrape_task.outputs["news"], val_frac=val_frac, test_frac=test_frac, seed=seed
    )

    train_task = train(
        train_data=split_task.outputs["train"],
        val_data=split_task.outputs["val"],
        base_model=base_model,
        epochs=epochs,
        batch_size=batch_size,
        block_size=block_size,
        lr=lr,
        experiment=experiment,
    )
    train_task.set_env_variable("MLFLOW_TRACKING_URI", MLFLOW_URI)
    train_task.set_cpu_request("2").set_memory_request("4G").set_memory_limit("8G")

    eval_task = evaluate(
        model=train_task.outputs["model"],
        test_data=split_task.outputs["test"],
        batch_size=batch_size,
        block_size=block_size,
    )
    eval_task.set_env_variable("MLFLOW_TRACKING_URI", MLFLOW_URI)
    eval_task.set_cpu_request("1").set_memory_request("2G").set_memory_limit("4G")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Compile (and optionally run) the BMW LLM pipeline")
    ap.add_argument("-o", "--output", default="bmw_llm_pipeline.yaml")
    ap.add_argument("--host", help="KFP API endpoint; if given, also start a run")
    ap.add_argument("--epochs", type=int, help="override epochs for the started run")
    args = ap.parse_args()

    compiler.Compiler().compile(bmw_llm_pipeline, args.output)
    print(f"Compiled to {args.output} (image {IMAGE})")

    if args.host:
        import kfp

        params = {"epochs": args.epochs} if args.epochs is not None else {}
        run = kfp.Client(host=args.host).create_run_from_pipeline_package(
            args.output, arguments=params, experiment_name="bmw-llm"
        )
        print(f"Started run {run.run_id}")
