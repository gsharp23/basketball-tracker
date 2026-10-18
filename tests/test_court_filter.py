"""The court filter: a box whose feet are outside the court (plus the margin) is moved out of the way,
one on the court or on the line stays, the ball is never touched, cut-off feet are left alone.
Run:  python -m unittest discover tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import court_filter  # noqa: E402

# A picture -> court matrix that is simply "one pixel = one foot" (an identity homography).
IDENTITY = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
COURT = (84.0, 50.0)


def box(track_id, foot_x, foot_y, height=10):
    return {"id": track_id, "bbox": [foot_x - 2, foot_y - height, foot_x + 2, foot_y], "conf": 0.9}


class InsideTests(unittest.TestCase):
    def test_inside_and_on_the_line(self):
        self.assertTrue(court_filter.inside(42, 25, 84, 50))
        self.assertTrue(court_filter.inside(0, 0, 84, 50))
        self.assertTrue(court_filter.inside(84, 50, 84, 50))

    def test_margin_keeps_a_sideline_player(self):
        self.assertTrue(court_filter.inside(42, -1.0, 84, 50))            # 1 ft outside the sideline: within 3% of 50 ft
        self.assertTrue(court_filter.inside(-2.0, 25, 84, 50))            # 2 ft behind the baseline: within 3% of 84 ft

    def test_bench_is_outside(self):
        self.assertFalse(court_filter.inside(42, -4.0, 84, 50))
        self.assertFalse(court_filter.inside(90, 25, 84, 50))


class JudgeTests(unittest.TestCase):
    def test_moves_bench_boxes_and_keeps_players(self):
        record = {"frame": 0, "players": [box(1, 40, 25), box(2, 40, -6)], "refs": [box(3, 42, 0.5), box(4, 42, -5)],
                  "ball": None, "off_court": []}
        kept, dropped = court_filter.judge_frame(record, IDENTITY, COURT, picture_height=1000)
        self.assertEqual((kept, dropped), (2, 2))
        self.assertEqual([b["id"] for b in record["players"]], [1])
        self.assertEqual([b["id"] for b in record["refs"]], [3])
        self.assertEqual(sorted((b["id"], b["cls"]) for b in record["off_court"]), [(2, "Player"), (4, "Referee")])

    def test_cut_off_feet_are_left_alone(self):
        record = {"frame": 0, "players": [box(5, 40, 999)], "refs": [], "off_court": []}     # bottom edge: feet unknown
        kept, dropped = court_filter.judge_frame(record, IDENTITY, COURT, picture_height=1000)
        self.assertEqual((kept, dropped), (1, 0))

    def test_restore_then_judge_again_is_the_same(self):
        record = {"frame": 0, "players": [box(1, 40, 25), box(2, 40, -6)], "refs": [], "off_court": []}
        court_filter.judge_frame(record, IDENTITY, COURT, 1000)
        court_filter.restore(record)
        self.assertEqual(sorted(b["id"] for b in record["players"]), [1, 2])
        court_filter.judge_frame(record, IDENTITY, COURT, 1000)
        self.assertEqual([b["id"] for b in record["players"]], [1])


if __name__ == "__main__":
    unittest.main()
