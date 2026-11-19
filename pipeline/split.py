#!/usr/bin/env python3
"""Split news.json into train/val/test JSON files.

Usage:
    python3 split.py                          # 80/10/10 -> train.json, val.json, test.json
    python3 split.py -i news.json --val 0.1 --test 0.1 --seed 42
"""
import argparse
import json
import random

ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ap.add_argument("-i", "--input", default="news.json")
ap.add_argument("--val", type=float, default=0.1)
ap.add_argument("--test", type=float, default=0.1)
ap.add_argument("--seed", type=int, default=42)
args = ap.parse_args()

with open(args.input, encoding="utf-8") as f:
    items = json.load(f)

random.Random(args.seed).shuffle(items)
n_test = max(1, round(len(items) * args.test))
n_val = max(1, round(len(items) * args.val))
splits = {
    "test": items[:n_test],
    "val": items[n_test:n_test + n_val],
    "train": items[n_test + n_val:],
}

for name, part in splits.items():
    with open(f"{name}.json", "w", encoding="utf-8") as f:
        json.dump(part, f, ensure_ascii=False, indent=2)
    print(f"{name}: {len(part)} -> {name}.json")
