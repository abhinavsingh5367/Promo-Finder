# Promo Finder

Promo Finder reads timestamped microdrama scripts and ranks 20–60 second clips. It accepts the original assignment ZIP, a folder of episode scripts, or one .txt screenplay in the same format. The output is a CSV you can open in Excel, plus a JSON summary of the run.

## Quick start

Unzip this prototype and open the folder containing `promo_finder.py`. On Windows, click the File Explorer address bar, type `powershell`, and press Enter to open PowerShell in that folder. Use Python 3.10 or newer. The tool uses Python's standard library and runs locally.

```powershell
py -3 promo_finder.py sample-episode.txt --output sample-results
py -3 promo_finder.py "C:\path\to\your-scripts.zip" --output results
```

If your Python runs with `python` on Windows or `python3` on macOS or Linux, use that command in place of `py -3`. The fictional sample should return one promo at 00:00.00–00:28.00. Run `py -3 -m unittest -v` from this folder to check the cut rules and AI review validation.

Open `results/ranked_promos.csv` and start at rank 1. Each row is one continuous clip. The review status distinguishes AI priority picks, clips awaiting editorial review, and clips the AI flagged for a weak hook, ending, or standalone clarity. `run_summary.json` shows coverage by episode, including episodes with zero valid picks. The default returns up to two non-overlapping clips per episode. Set `--per-episode 1` for a shorter list.

## Optional AI review

Create a private prompt for the highest-scoring candidate from up to 50 different episodes. Use `--review-count 12` for a smaller batch.

```powershell
py -3 promo_finder.py "C:\path\to\your-scripts.zip" --output baseline --review-prompt private-review.txt
```

Paste the instructions and clip blocks into the AI chat tool you are using for the assignment. For a long prompt, review in batches and combine the returned objects into a single JSON array. Save it without Markdown fences as `ai_review.json`. Keep the prompt outside the script folder and private because it contains screenplay cues. Rerun using the same input and shortlist settings.

```powershell
py -3 promo_finder.py "C:\path\to\your-scripts.zip" --output results --ai-review ai_review.json
```

The tool accepts AI scores and descriptions for known clip IDs. Reviewed clips scoring at least 3 out of 5 on all three criteria come first, then clips still awaiting review, then reviewed clips that miss a criterion. Within the reviewed groups, the AI score contributes 85% and the rules score 15%. AI scores weight hook, cliffhanger and standalone clarity 40%, 35% and 25%.

## Rank clips for a series launch

Add `--show-premise "A one-sentence description of the show"` when writing a review prompt. The prompt includes nearby cues for comparing possible recuts. Give every clip that clears the three core criteria a `premise_fit_score` from 0 to 5. The launch score weights hook, cliffhanger, standalone clarity and fit with the premise at 30%, 25%, 20% and 25%. Supply the reviewed JSON as usual to apply this ranking. The CSV shows both the general and launch scores.

## Compare and adjust a cut

The review can explicitly replace a shortlisted cut after a side-by-side comparison. Give the new `clip_id` and set `replaces` to the original ID. An ID takes the form `E018-002890-007350`, with start and end in hundredths of a second from the episode start. Both endpoints must be timed dialogue, action, or shot boundaries. The proposed cut must pass the same duration and dialogue checks and overlap at least half of the shorter cut. The tool then picks any alternate for the episode around the revised cut.

## Reproduce the training experiment

```powershell
py -3 train_ranker.py "C:\path\to\your-scripts.zip" training_labels.json trained_ranker.json
```

The labels are the earlier AI rubric judgments, one clip per episode. The trainer fits a regularized logistic regression and holds out ten consecutive episodes at a time. The saved model includes weights and comparison metrics. The trained ranker is experimental. The submission uses rules plus direct review because the learned gain was small and varied across test groups.

## How it works

1. Read timed dialogue and action. Use their boundaries to propose continuous windows between 20 and 60 seconds. Discard cuts inside a spoken cue and cuts between nearby pieces of a continued line marked with ellipses.
2. Score each window for the first three seconds, the final beat, context and length. Prefer questions with explicit stakes over routine questions. Dialogue turns and beat count stand in for context. The duration preference is 35 seconds.
3. Keep up to two non-overlapping windows per episode, rank them, and optionally use AI to review the strongest candidates. An editor makes the final cut.

The CSV uses minute, second and hundredth timestamps from the script. For clips awaiting review, the hook and ending are rule labels with short evidence excerpts. Share the prototype with its fictional sample. Keep the show's scripts and the private review prompt out of the prototype repository. Share the results separately with the reviewers.
