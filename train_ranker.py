"""Train and cross-check an experimental ranker on AI-reviewed clips."""

import argparse
import json
import math
import re
from pathlib import Path

from promo_finder import candidate_clips, classify, parse_script, read_scripts


VISUAL = re.compile(r"\b(?:flames?|burning|collaps\w*|blood\w*|tears?|weapon|sword|"
                    r"dagger|lunge\w*|thrust\w*|chained|strikes?|tackles?)\b", re.I)
STAKES = re.compile(r"\b(?:fire|burn\w*|poison\w*|die|death|kill\w*|execut\w*|"
                    r"arrest\w*|witchcraft|betray\w*|marry\w*|wedding|stole|stolen|impost[eo]r)\b", re.I)
FEATURES = ["rules_score", "hook_signal", "ending_signal", "opening_visual",
            "opening_stakes", "ending_stakes", "duration"]


def features(clip, cues):
    opening = [cue for cue in cues if cue.start < clip.start + 300 and cue.end > clip.start]
    ending = [cue for cue in cues if cue.end > clip.end - 300 and cue.start < clip.end]
    return [
        clip.score / 100, classify(clip.hook)[1] / 5, classify(clip.ending)[1] / 5,
        int(any(VISUAL.search(cue.text) for cue in opening if not cue.spoken)),
        int(any(STAKES.search(cue.text) for cue in opening if cue.spoken)),
        int(any(STAKES.search(cue.text) for cue in ending if cue.spoken)),
        (clip.end - clip.start) / 6000,
    ]


def probability(weights, values):
    score = sum(weight * value for weight, value in zip(weights, (1, *values)))
    return 1 / (1 + math.exp(-max(-40, min(40, score))))


def fit(rows):
    weights = [0.0] * (len(FEATURES) + 1)
    for _ in range(650):
        gradient = [0.0] * len(weights)
        for _, values, label in rows:
            error = probability(weights, values) - label
            for index, value in enumerate((1, *values)):
                gradient[index] += error * value
        for index in range(len(weights)):
            penalty = 0.03 * weights[index] if index else 0
            weights[index] -= 0.35 * (gradient[index] / len(rows) + penalty)
    return weights


def auroc(pairs):
    positives = [score for score, label in pairs if label]
    negatives = [score for score, label in pairs if not label]
    return sum((left > right) + 0.5 * (left == right)
               for left in positives for right in negatives) / (len(positives) * len(negatives))


def evaluate(rows, block_size=10):
    results = []
    for block in sorted({(episode - 1) // block_size for episode, _, _ in rows}):
        train = [row for row in rows if (row[0] - 1) // block_size != block]
        test = [row for row in rows if (row[0] - 1) // block_size == block]
        if {row[2] for row in train} != {0, 1} or {row[2] for row in test} != {0, 1}:
            continue
        weights = fit(train)
        model = auroc([(probability(weights, values), label) for _, values, label in test])
        rules = auroc([(values[0], label) for _, values, label in test])
        results.append({"held_out_episodes": sorted({row[0] for row in test}),
                        "learned_auroc": round(model, 4), "rules_auroc": round(rules, 4)})
    return {
        "folds": results,
        "mean_learned_auroc": round(sum(row["learned_auroc"] for row in results) / len(results), 4)
        if results else None,
        "mean_rules_auroc": round(sum(row["rules_auroc"] for row in results) / len(results), 4)
        if results else None,
    }


def train(script_path, label_path):
    labels = json.loads(label_path.read_text(encoding="utf-8-sig"))
    if not isinstance(labels, dict) or any(type(value) is not int or value not in (0, 1)
                                           for value in labels.values()):
        raise ValueError("Supply a JSON object mapping original clip IDs to 0 or 1.")
    rows = {}
    for name, script in read_scripts(script_path):
        episode, cues = parse_script(name, script)
        for clip in candidate_clips(episode, cues):
            if clip.id in labels:
                rows[clip.id] = (episode, features(clip, cues), labels[clip.id])
    if set(rows) != set(labels) or {row[2] for row in rows.values()} != {0, 1}:
        raise ValueError("Labels must match generated clip IDs and include both classes.")
    data = list(rows.values())
    return {
        "model": "regularized logistic regression",
        "label": "Codex judged that the clip scored at least 3/5 on each criterion",
        "clips": len(data),
        "positives": sum(row[2] for row in data),
        "features": FEATURES,
        "held_out_evaluation": evaluate(data),
        "intercept_and_weights": [round(weight, 6) for weight in fit(data)],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scripts", type=Path)
    parser.add_argument("labels", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    model = train(args.scripts, args.labels)
    args.output.write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(model["held_out_evaluation"], indent=2))


if __name__ == "__main__":
    main()
