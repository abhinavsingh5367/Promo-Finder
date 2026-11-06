import json
import tempfile
import unittest
from pathlib import Path

from promo_finder import Cue, ai_priority, candidate_clips, classify, cut_boundaries, launch_priority, load_reviews


class PromoFinderTests(unittest.TestCase):
    def test_continued_line_keeps_both_parts_together(self):
        first = Cue("NELL", 0, 240, "If we lose the gate...")
        reaction = Cue("ACTION", 250, 380, "Ivo reaches for the lever.")
        continuation = Cue("NELL (Continues)", 400, 600, "...the village floods.")
        answer = Cue("IVO", 750, 900, "Then stay back.")
        turn = Cue("NELL", 2200, 2510, "Who opened the gate?")
        cues = [first, reaction, continuation, answer, turn]
        starts, ends = cut_boundaries(cues, [first, continuation, answer, turn])
        self.assertNotIn(reaction.start, starts)
        self.assertNotIn(continuation.start, starts)
        self.assertNotIn(first.end, ends)
        self.assertNotIn(reaction.end, ends)
        self.assertIn(continuation.end, ends)
        self.assertIn((0, 2510), {(clip.start, clip.end) for clip in candidate_clips(1, cues)})

    def test_cut_avoids_speech_already_in_progress(self):
        line = Cue("NELL", 0, 500, "I can hear them at the gate.")
        action = Cue("ACTION", 120, 300, "Ivo slams the door.")
        starts, ends = cut_boundaries([line, action], [line])
        self.assertEqual(starts, [0])
        self.assertEqual(ends, [500])

    def test_routine_question_scores_below_explicit_stakes(self):
        routine = Cue("NELL", 0, 90, "What is it?")
        high_stakes = Cue("NELL", 0, 200, "Will they execute her?")
        self.assertLess(classify(routine)[1], classify(high_stakes)[1])

    def test_launch_score_rewards_a_clip_that_sells_the_show(self):
        action = dict(hook_score=5, cliffhanger_score=5, standalone_score=4, premise_fit_score=2)
        premise = dict(hook_score=5, cliffhanger_score=4, standalone_score=5, premise_fit_score=5)
        self.assertGreater(ai_priority(action), ai_priority(premise))
        self.assertLess(launch_priority(action), launch_priority(premise))

    def test_visual_recut_can_use_shots_around_dialogue(self):
        cues = [
            Cue("CAMERA", 0, 280, "The river surges over the floodgate."),
            Cue("NELL", 320, 550, "The gate won't hold."),
            Cue("IVO", 750, 1000, "Half the village is asleep."),
            Cue("NELL", 1400, 1730, "We have to wake them."),
            Cue("CAMERA", 1910, 2120, "The floodgate buckles."),
        ]
        ordinary = {(clip.start, clip.end) for clip in candidate_clips(1, cues)}
        recuts = {(clip.start, clip.end) for clip in candidate_clips(1, cues, shot_boundaries=True)}
        self.assertNotIn((0, 2120), ordinary)
        self.assertIn((0, 2120), recuts)

    def test_review_rejects_unknown_ids_and_invalid_scores(self):
        review = dict(clip_id="E001-000000-002000", hook_score=4, cliffhanger_score=4,
                      standalone_score=4, hook="The gate jams", cliffhanger="The latch breaks",
                      why="The flood is still approaching")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "review.json"
            path.write_text(json.dumps([review]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unexpected or duplicate"):
                load_reviews(path, set())
            review["standalone_score"] = 6
            path.write_text(json.dumps([review]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "integer from 0 to 5"):
                load_reviews(path, {review["clip_id"]})
            review.update(standalone_score=4, premise_fit_score=True)
            path.write_text(json.dumps([review]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "premise_fit_score"):
                load_reviews(path, {review["clip_id"]})


if __name__ == "__main__":
    unittest.main()
