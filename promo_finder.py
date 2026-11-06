"""Find continuous promo clips in timestamped screenplays."""

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from zipfile import BadZipFile, ZipFile


TIME = r"\d+:[0-5]\d\.\d{2}"
HEADER = re.compile(
    rf"^\*\*(?P<label>[^\n]+?)\*\*[ \t]*\n"
    rf"[ \t]*Start:[ \t]*\((?P<start>{TIME})\)[ \t]*\|[ \t]*"
    rf"End:[ \t]*\((?P<end>{TIME})\)[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)
TIMED_LINE = re.compile(r"^[ \t]*Start:", re.MULTILINE | re.IGNORECASE)
EPISODE = re.compile(r"^EPISODE NUMBER:\s*(?:Episode\s+)?(\d+)", re.MULTILINE | re.IGNORECASE)
TECHNICAL = {"CAMERA", "SOUND FX", "SFX", "MUSIC", "LIGHTING", "TRANSITION"}
DANGER = re.compile(
    r"\b(?:slaps?|stabs?|gunfire|tackles?|attacks?|"
    r"shoots? (?:at|him|her|them)|chokes? (?:him|her|them)|"
    r"lunges? (?:at|towards?)|charges? (?:straight )?(?:at|towards?)|"
    r"strikes? (?:her|him|the \w+ (?:across|in))|"
    r"grabs? (?:her|his|the) (?:throat|knife|sword)|fires? (?:a|the) (?:gun|shot))\b",
    re.IGNORECASE,
)
SIGNALS = (
    ("Direct threat", 5, r"\b(?:i(?:'ll| will) (?:kill|destroy|ruin)|you(?:'ll| will) (?:die|pay)|"
     r"i swear (?:i|you)|kill (?:him|her|them)|execute (?:him|her|them)|off with)\b"),
    ("Active emergency", 4, r"(?:^fire[!.]?$|\b(?:ceiling (?:is )?(?:coming down|collaps\w*)|"
     r"building (?:is )?(?:on fire|collaps\w*)|can(?:not|'t) breathe)\b)"),
    ("Serious stakes", 4, r"\b(?:capital charge|death sentence|sentenced to|"
     r"faces? execution|(?:will|shall|to) be executed|arrest (?:him|her|them)|"
     r"(?:wants?|want) (?:me|him|her|us|them) dead|(?:was|were|been|being) poisoned)\b"),
    ("Reveal or betrayal", 4, r"\b(?:betray\w*|poison\w*|murder\w*|forg\w+ (?:letter|signature)|"
     r"secret|impostor|marrying|confess\w*|lied|father of|real (?:heir|duke)|proof)\b"),
    ("Urgent demand", 4, r"^(?:stop|wait|run|get out|let (?:me|him|her) go|"
     r"stay back|help me|stand back|don't (?:move|touch))\b"),
    ("Visible distress", 3, r"\b(?:collaps\w*|bleed\w*|scream\w*|sobs?|gasps?|"
     r"bursts? into tears|trembl\w*)\b"),
    ("Confrontation", 3, r"\b(?:how dare|you knew|you promised|you swore|"
     r"i saw you|what have you done|what did you do|never forgive|you stole)\b"),
    ("Decision or promise", 2, r"\b(?:i have to|i must|i will|we must|you must|"
     r"we have to|i won't|i promise|i refuse)\b"),
)
SIGNALS = tuple((name, points, re.compile(pattern, re.IGNORECASE))
                for name, points, pattern in SIGNALS)
QUESTION_STAKES = re.compile(
    r"\b(?:kill\w*|dead|dying|execut\w*|arrest\w*|witchcraft|"
    r"poison\w*|betray\w*|chained|gallows|burning|blackmail\w*|forgery)\b",
    re.IGNORECASE,
)
ELLIPSIS = ("...", "…")
FIELDS = [
    "rank", "clip_id", "episode", "start", "end", "duration_seconds",
    "hook", "cliffhanger", "why_it_works", "review_status",
    "rules_score", "ai_hook", "ai_cliffhanger", "ai_standalone", "ai_priority",
    "premise_fit", "launch_priority",
]


@dataclass(frozen=True)
class Cue:
    label: str
    start: int
    end: int
    text: str

    @property
    def spoken(self):
        return self.label.upper() not in TECHNICAL | {"ACTION"}

    @property
    def editorial(self):
        return self.label.upper() not in TECHNICAL

    @property
    def speaker(self):
        return self.label.split("(")[0].strip().casefold()


@dataclass(frozen=True)
class Clip:
    episode: int
    start: int
    end: int
    hook: Cue
    ending: Cue
    hook_kind: str
    end_kind: str
    score: float

    @property
    def id(self):
        return f"E{self.episode:03}-{self.start:06}-{self.end:06}"


def read_scripts(path):
    if path.is_dir():
        for source in sorted(path.rglob("*.txt")):
            yield source.name, source.read_text(encoding="utf-8-sig")
    elif path.suffix.lower() == ".zip":
        with ZipFile(path) as archive:
            for name in sorted(archive.namelist()):
                if name.lower().endswith(".txt") and not name.startswith("__MACOSX/"):
                    yield Path(name).name, archive.read(name).decode("utf-8-sig")
    elif path.suffix.lower() == ".txt":
        yield path.name, path.read_text(encoding="utf-8-sig")
    else:
        raise ValueError("Choose a ZIP, a folder of scripts, or one .txt script.")


def ticks(value):
    minute, second = value.split(":")
    whole, hundredths = second.split(".")
    return int(minute) * 6000 + int(whole) * 100 + int(hundredths)


def stamp(value):
    minute, rest = divmod(value, 6000)
    second, hundredths = divmod(rest, 100)
    return f"{minute:02}:{second:02}.{hundredths:02}"


def parse_script(name, script):
    script = script.replace("\r\n", "\n").replace("\r", "\n")
    number = EPISODE.search(script) or re.search(r"episode[-_ ](\d+)", name, re.IGNORECASE)
    headers = list(HEADER.finditer(script))
    if not number or not headers or len(headers) != len(TIMED_LINE.findall(script)):
        raise ValueError(f"{name} needs an episode number and supported timestamp lines.")
    cues = []
    for index, header in enumerate(headers):
        stop = headers[index + 1].start() if index + 1 < len(headers) else len(script)
        lines = script[header.end():stop].strip().splitlines()
        body = []
        for line in lines:
            if not line.strip() or line.lstrip().startswith(("SCENE ", "---")):
                break
            body.append(line.strip())
        label = header["label"].rstrip(":").strip()
        start, end = ticks(header["start"]), ticks(header["end"])
        if end < start:
            raise ValueError(f"{name} has a timestamp that ends before it starts.")
        cues.append(Cue(label, start, end, " ".join(body).strip('"“”')))
    return int(number[1]), sorted(cues, key=lambda cue: (cue.start, cue.end))


def classify(cue):
    text = cue.text.replace("’", "'")
    if cue.label.upper() == "ACTION" and DANGER.search(text):
        return "Immediate danger", 5
    for name, points, pattern in SIGNALS:
        if pattern.search(text):
            return name, points
    if cue.spoken and "?" in text:
        question = text.split("?", 1)[0]
        if QUESTION_STAKES.search(question):
            return "High-stakes question", 3
        if len(question.split()) >= 5:
            return "Direct question", 2
    return ("Dialogue beat" if cue.spoken else "Action beat"), 1


def cut_boundaries(beats, speech):
    continuation_starts = {cue.start for cue in speech if cue.text.startswith(ELLIPSIS)}
    # Screenplays sometimes divide one line into two timed cues.
    gaps = [
        (previous.end, current.start)
        for previous, current in zip(speech, speech[1:])
        if previous.speaker == current.speaker and 0 <= current.start - previous.end <= 200
        and (current.start in continuation_starts
             or (previous.text.endswith(ELLIPSIS) and "continu" in current.label.casefold()))
    ]

    def can_cut(point):
        return (not any(cue.start < point < cue.end for cue in speech)
                and not any(left <= point <= right for left, right in gaps))

    starts = [point for point in sorted({cue.start for cue in beats})
              if point not in continuation_starts and can_cut(point)]
    ends = [point for point in sorted({cue.end for cue in beats}) if can_cut(point)]
    return starts, ends


def candidate_clips(episode, cues, *, shot_boundaries=False):
    beats = [cue for cue in cues if cue.editorial and cue.text]
    speech = [cue for cue in cues if cue.spoken and cue.text]
    shots = [cue for cue in cues if shot_boundaries and cue.label.upper() == "CAMERA" and cue.text]
    starts, ends = cut_boundaries(beats + shots, speech)
    ratings = {cue: classify(cue) for cue in beats + shots}
    for start in starts:
        for end in ends:
            duration = end - start
            if not 2000 <= duration <= 6000:
                continue
            inside = [cue for cue in beats if start <= cue.start and cue.end <= end]
            if len(inside) < 3:
                continue
            opening = [cue for cue in inside if cue.start < start + 300]
            closing = [cue for cue in inside if cue.end > end - 300]
            opening.extend(cue for cue in shots if start <= cue.start < start + 300)
            closing.extend(cue for cue in shots if end - 300 < cue.end <= end)
            if not opening or not closing:
                continue
            first_line = next((cue for cue in opening if cue.spoken), None)
            if first_line and first_line.text.startswith(ELLIPSIS):
                continue
            hook = max(opening, key=lambda cue: (ratings[cue][1], -cue.start))
            ending = max(closing, key=lambda cue: (ratings[cue][1], cue.end))
            hook_kind, hook_score = ratings[hook]
            end_kind, end_score = ratings[ending]
            speakers = {cue.speaker for cue in inside if cue.spoken}
            context = 0.65 * min(len(speakers), 2) / 2 + 0.35 * min(len(inside), 7) / 7
            pace = max(0, 1 - abs(duration - 3500) / 2500)
            score = 8 * hook_score + 7.6 * end_score + 14 * context + 8 * pace
            yield Clip(episode, start, end, hook, ending, hook_kind, end_kind, round(score, 2))


def choose_clips(clips, limit):
    if limit == 0:
        return []
    selected = []
    for clip in sorted(clips, key=lambda item: (-item.score, item.start, item.end)):
        if all(clip.end <= other.start or clip.start >= other.end for other in selected):
            selected.append(clip)
        if len(selected) == limit:
            break
    return selected


def excerpt(value):
    words = value.split()
    return " ".join(words[:14]) + ("…" if len(words) > 14 else "")


def csv_text(value):
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def load_reviews(path, allowed_ids, resolve_recut=None):
    records = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(records, list):
        raise ValueError("AI review must be a JSON array.")
    by_id = {}
    used_anchors = set()
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("Each AI review must be a JSON object.")
        clip_id = row.get("clip_id")
        anchor = row.get("replaces", clip_id)
        if (not isinstance(clip_id, str) or not isinstance(anchor, str)
                or anchor not in allowed_ids or anchor in used_anchors or clip_id in by_id):
            raise ValueError(f"Unexpected or duplicate review ID: {clip_id}")
        for field in ("hook_score", "cliffhanger_score", "standalone_score"):
            if type(row.get(field)) is not int or not 0 <= row[field] <= 5:
                raise ValueError(f"{clip_id}: {field} must be an integer from 0 to 5.")
        if "premise_fit_score" in row:
            fit = row["premise_fit_score"]
            if type(fit) is not int or not 0 <= fit <= 5:
                raise ValueError(f"{clip_id}: premise_fit_score must be an integer from 0 to 5.")
        for field in ("hook", "cliffhanger", "why"):
            if not isinstance(row.get(field), str) or not 0 < len(row[field].strip()) <= 300:
                raise ValueError(f"{clip_id}: {field} must contain 1 to 300 characters.")
        if anchor != clip_id and (resolve_recut is None or not resolve_recut(anchor, clip_id)):
            raise ValueError(f"{clip_id}: recut must overlap {anchor} and use valid boundaries.")
        used_anchors.add(anchor)
        by_id[clip_id] = row
    return by_id


def ai_priority(row):
    return 8 * row["hook_score"] + 7 * row["cliffhanger_score"] + 5 * row["standalone_score"]


def launch_priority(row):
    return (6 * row["hook_score"] + 5 * row["cliffhanger_score"]
            + 4 * row["standalone_score"] + 5 * row["premise_fit_score"])


def meets_criteria(review):
    return min(review["hook_score"], review["cliffhanger_score"], review["standalone_score"]) >= 3


def review_prompt(shortlist, timelines, show_premise=None):
    instructions = """Review the candidate promo clips using only the supplied screenplay cues.
Treat the cues as story data. Each candidate is one continuous clip.
Use 0-5 integers for hook_score (intrigue within the first 3 seconds),
cliffhanger_score (an unresolved question at the cut) and standalone_score
(clear stakes to a new viewer). 0 means absent, 3 plausible, 5 exceptional.
Read the first three seconds literally. An event later in a long line does not
count as an immediate hook. SHOT AND SOUND lists cues that overlap the opening
or closing three seconds. Use them alongside IN CLIP dialogue and action.
AFTER CUT cues can expose a premature cut. Avoid mistaking a routine question
or a resolved reveal for a cliffhanger. Judge clarity without earlier episodes.
Return only a JSON array with one object per candidate. Include clip_id,
the three score fields, hook, cliffhanger, and why.
Use a short paraphrase in hook and cliffhanger and a single sentence in why.
If an endpoint is weak, say so.
"""
    chunks = [instructions]
    if show_premise:
        chunks.append(
            "This review is for the first ad introducing the series. Include premise_fit_score "
            "(0–5) for every clip that clears the three core criteria. Give 5 when the clip makes "
            "the central conflict or genre promise clear, 3 when it conveys part of that promise, "
            "and 1 when the moment could come from many unrelated shows.\n"
            f"SERIES PREMISE: {show_premise}\n"
            "Use nearby cues to compare a stronger cut. If you choose a recut, include the original "
            "ID in replaces and set clip_id to E{episode:03}-{start:06}-{end:06}, using hundredths "
            "of a second for both times. Use timed dialogue, action or camera boundaries, avoid "
            "splitting dialogue and keep the duration 20–60 seconds. Score the revised cut only.\n"
        )
    else:
        chunks.append("Leave timestamps and clip IDs unchanged.\n")
    for clip in shortlist:
        cues = timelines[clip.episode]
        inside = [cue for cue in cues if cue.editorial and clip.start <= cue.start
                  and cue.end <= clip.end]
        after = [cue for cue in cues if cue.editorial and clip.end <= cue.start
                 < clip.end + 700][:3]
        edge_cues = [
            cue for cue in cues if not cue.editorial
            and cue.start < clip.end and cue.end > clip.start
            and (cue.start < clip.start + 300 or cue.end > clip.end - 300)
        ]
        chunks.append(f"\n{clip.id} | Episode {clip.episode} | {stamp(clip.start)} to {stamp(clip.end)}")
        sections = [("IN CLIP", inside), ("SHOT AND SOUND", edge_cues), ("AFTER CUT", after)]
        if show_premise:
            nearby = [cue for cue in cues if cue.start < clip.end + 2000 and cue.end > clip.start - 2000
                      and (cue.start < clip.start or cue.end > clip.end)]
            sections.append(("NEARBY CUES FOR COMPARISON", nearby))
        for label, section in sections:
            chunks.append(label)
            for cue in section:
                start = max(cue.start, clip.start) if label == "SHOT AND SOUND" else cue.start
                end = min(cue.end, clip.end) if label == "SHOT AND SOUND" else cue.end
                chunks.append(f"{stamp(start)} to {stamp(end)} | {cue.label} | {cue.text}")
    return "\n".join(chunks) + "\n"


def output_row(rank, clip, review):
    status = "Pending editor review"
    if review:
        status = "AI priority" if meets_criteria(review) else "AI flagged"
    return [
        rank, clip.id, clip.episode, stamp(clip.start), stamp(clip.end),
        f"{(clip.end - clip.start) / 100:.2f}",
        csv_text(review["hook"]) if review else f"{clip.hook_kind}: {excerpt(clip.hook.text)}",
        csv_text(review["cliffhanger"]) if review else f"{clip.end_kind}: {excerpt(clip.ending.text)}",
        csv_text(review["why"]) if review else
        f"Rules flagged {clip.hook_kind.lower()} near the opening and "
        f"{clip.end_kind.lower()} near the cut.",
        status,
        f"{clip.score:.2f}", review["hook_score"] if review else "",
        review["cliffhanger_score"] if review else "", review["standalone_score"] if review else "",
        ai_priority(review) if review else "",
        review.get("premise_fit_score", "") if review else "",
        launch_priority(review) if review and "premise_fit_score" in review else "",
    ]


def run(args):
    if args.review_prompt:
        reserved = [args.input, args.output / "ranked_promos.csv", args.output / "run_summary.json"]
        if args.ai_review:
            reserved.append(args.ai_review)
        prompt_path = args.review_prompt.resolve()
        if prompt_path in {path.resolve() for path in reserved}:
            raise ValueError("Use a separate file for the private AI review prompt.")
        if args.input.is_dir() and args.input.resolve() in prompt_path.parents:
            raise ValueError("Keep the private AI review prompt outside the input folder.")
    timelines = {}
    windows = {}
    clips = []
    episode_results = []
    for name, script in read_scripts(args.input):
        episode, cues = parse_script(name, script)
        if episode in timelines:
            raise ValueError(f"Episode {episode} appears in more than one script.")
        timelines[episode] = cues
        candidates = list(candidate_clips(episode, cues))
        windows[episode] = candidates
        selected = choose_clips(candidates, args.per_episode)
        clips.extend(selected)
        episode_results.append({"episode": episode, "timed_cues": len(cues),
                                "candidate_windows": len(candidates), "promos": len(selected)})
    if not timelines:
        raise ValueError("No .txt screenplay files found.")
    clips.sort(key=lambda clip: (-clip.score, clip.episode, clip.start, clip.end))
    best_per_episode = {}
    for clip in clips:
        best_per_episode.setdefault(clip.episode, clip)
    shortlist = list(best_per_episode.values())[:args.review_count]
    anchors = {clip.id: clip for clip in shortlist}
    recuts = {}

    def resolve_recut(anchor_id, clip_id):
        original = anchors[anchor_id]
        for alt in candidate_clips(original.episode, timelines[original.episode], shot_boundaries=True):
            if alt.id == clip_id:
                overlap = min(original.end, alt.end) - max(original.start, alt.start)
                if 2 * overlap >= min(original.end - original.start, alt.end - alt.start):
                    recuts[original.episode] = alt
                    return True
        return False

    reviews = load_reviews(args.ai_review, anchors, resolve_recut) if args.ai_review else {}
    viable = [row for row in reviews.values() if meets_criteria(row)]
    launch_mode = any("premise_fit_score" in row for row in viable)
    if launch_mode and not all("premise_fit_score" in row for row in viable):
        raise ValueError("Score premise fit for every AI priority clip to use launch ranking.")
    if recuts:
        clips = [clip for clip in clips if clip.episode not in recuts]
        for result in episode_results:
            episode = result["episode"]
            if episode not in recuts:
                continue
            recut = recuts[episode]
            remaining = [clip for clip in windows[episode]
                         if clip.end <= recut.start or clip.start >= recut.end]
            revised = [recut, *choose_clips(remaining, args.per_episode - 1)]
            clips.extend(revised)
            result["promos"] = len(revised)
        clips.sort(key=lambda clip: (-clip.score, clip.episode, clip.start, clip.end))
    # Reviewed clips with a viable score on all three criteria lead the queue.
    def ranking(clip):
        review = reviews.get(clip.id)
        if not review:
            return 1, -clip.score
        priority_pick = meets_criteria(review)
        priority = launch_priority(review) if priority_pick and launch_mode else ai_priority(review)
        return (0 if priority_pick else 2), -(0.85 * priority + 0.15 * clip.score)

    clips.sort(key=ranking)
    if args.review_prompt:
        args.review_prompt.parent.mkdir(parents=True, exist_ok=True)
        args.review_prompt.write_text(review_prompt(shortlist, timelines, args.show_premise), encoding="utf-8")
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / "ranked_promos.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(FIELDS)
        writer.writerows(output_row(rank, clip, reviews.get(clip.id))
                         for rank, clip in enumerate(clips, 1))
    summary = {
        "episodes_processed": len(timelines),
        "timed_cues": sum(len(cues) for cues in timelines.values()),
        "candidate_windows": sum(row["candidate_windows"] for row in episode_results),
        "promos_returned": len(clips),
        "ai_reviews_applied": len(reviews),
        "validated_recuts": len(recuts),
        "ai_priority_picks": sum(meets_criteria(review) for review in reviews.values()),
        "ranking_goal": "series launch" if launch_mode else "general promo",
        "episodes_without_promos": sorted(row["episode"] for row in episode_results if not row["promos"]),
        "per_episode": sorted(episode_results, key=lambda row: row["episode"]),
    }
    (args.output / "run_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Scanned {len(timelines)} episode(s). Ranked {len(clips)} promo(s). "
          f"Applied {len(reviews)} AI review(s).\nResults: {args.output.resolve()}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="ZIP, folder, or single .txt screenplay")
    parser.add_argument("--output", type=Path, default=Path("results"), help="output folder")
    parser.add_argument("--per-episode", type=int, choices=range(1, 6), default=2)
    parser.add_argument("--review-count", type=int, choices=range(1, 51), default=50,
                        metavar="1-50", help="maximum number of distinct episodes to send for AI review")
    parser.add_argument("--review-prompt", type=Path, help="write a private AI review prompt")
    parser.add_argument("--show-premise", help="include a series premise in the private review prompt")
    parser.add_argument("--ai-review", type=Path, help="rerank using a JSON response to the review prompt")
    try:
        run(parser.parse_args())
    except (ValueError, OSError, BadZipFile) as error:
        print(f"Promo finder: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
