"""The roster snap: a misread jersey number lands on the roster player it really is, or is called unmatched.
Run:  python -m unittest discover tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import roster_snap  # noqa: E402

ROSTER = {"1", "2", "3", "4", "5", "10", "11", "13", "14", "15", "20", "23"}


class SnapTests(unittest.TestCase):
    def test_on_roster_stays(self):
        self.assertEqual(roster_snap.snap("4", {"4": 5}, ROSTER), ("4", "on roster"))

    def test_doubled_digit(self):
        self.assertEqual(roster_snap.snap("22", {"22": 3}, ROSTER), ("2", "snapped"))

    def test_zeros_are_a_ten(self):
        self.assertEqual(roster_snap.snap("00", {"00": 2}, ROSTER), ("10", "snapped"))

    def test_extra_digit_one_candidate(self):
        # 30 can only be the 3 (0 is nobody); 25 is a 2 or a 5: the vote decides
        self.assertEqual(roster_snap.snap("30", {"30": 2}, ROSTER), ("3", "snapped"))
        self.assertEqual(roster_snap.snap("25", {"25": 2, "5": 3}, ROSTER), ("5", "snapped"))

    def test_two_candidates_need_a_vote(self):
        self.assertEqual(roster_snap.snap("34", {"34": 4}, ROSTER), (None, "unmatched"))
        self.assertEqual(roster_snap.snap("34", {"34": 4, "4": 1}, ROSTER), ("4", "snapped"))
        self.assertEqual(roster_snap.snap("12", {"12": 3, "1": 2}, ROSTER), ("1", "snapped"))
        self.assertEqual(roster_snap.snap("21", {"21": 4, "1": 1}, ROSTER), ("1", "snapped"))

    def test_tied_votes_stay_unmatched(self):
        self.assertEqual(roster_snap.snap("12", {"1": 2, "2": 2}, ROSTER), (None, "unmatched"))

    def test_no_roster_number_near(self):
        self.assertEqual(roster_snap.snap("77", {"77": 3}, ROSTER), (None, "unmatched"))
        self.assertEqual(roster_snap.snap("", {}, ROSTER), (None, "unmatched"))

    def test_candidates_order(self):
        self.assertEqual(roster_snap.candidates("34", ROSTER), ["4", "3"])
        self.assertEqual(roster_snap.candidates("4", ROSTER), [])
        self.assertEqual(roster_snap.candidates("113", ROSTER), ["13", "11"])


if __name__ == "__main__":
    unittest.main()
