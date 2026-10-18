"""Snap a jersey number the reader got slightly wrong onto the roster, or call it unmatched.

The number reader sees digits from behind, half hidden, at an angle: 00 for 10, 22 for 2, 34 for 3 or 4
(a 4 next to a 3 that is the next player's arm), 25 for 5. With the roster known, a read that is not a
roster number but is one small slip away from one is snapped to it; which roster number was read most
for that tracker breaks a tie. A read that no roster number explains is "unmatched" and kept out of
the stats (the Players page can show it in grey).

    candidates("34", {"3", "4", "10"})            -> ["3", "4"]
    snap("34", {"34": 4, "3": 1}, {"3", "4"})     -> ("3", "snapped")
    snap("77", {"77": 3}, {"3", "4"})             -> (None, "unmatched")
    snap("4", {"4": 5}, {"3", "4"})               -> ("4", "on roster")
Plain functions, no files: stats.py and library.py call them, and tests do.
"""


def candidates(read, roster):
    """The roster numbers a misread could really be, in the order to prefer them (none when it is on the roster)."""
    read, roster = str(read or "").strip(), {str(number) for number in roster}
    if not read or read in roster:
        return []
    found = []

    def add(number):
        if number in roster and number not in found:
            found.append(number)
    if len(read) == 2 and read[0] == read[1]:               # a doubled digit: 22 is a 2, 33 a 3
        add(read[0])
    if read.strip("0") == "" and len(read) >= 2:             # all zeros: the 1 of a 10 lost, 00 is a 10
        add("10")
    if len(read) == 2:                                       # a digit too many: 34 is a 3 or a 4, 25 a 5, 21 a 1
        add(read[1])                                         # the trailing digit first: 25 is a 5, 21 a 1
        add(read[0])                                         # then the leading one: 30 is a 3
    if len(read) == 3:                                       # one spurious digit on a two-digit number
        add(read[1:])
        add(read[:2])
        add(read[0] + read[2])
    return found


def snap(read, votes, roster):
    """(roster number or None, "on roster" | "snapped" | "unmatched") for one tracker's read.

    votes: {number read: how many looks said so} for that tracker; the candidate read most often wins. A single
    candidate wins without a vote; several candidates with no vote between them stay unmatched (34 could be the
    3 or the 4: only the reader's other looks at that player can tell)."""
    read = str(read or "").strip()
    roster = {str(number) for number in roster}
    if not read:
        return None, "unmatched"
    if read in roster:
        return read, "on roster"
    options = candidates(read, roster)
    if not options:
        return None, "unmatched"
    votes = {str(number): int(count or 0) for number, count in (votes or {}).items()}
    best = max(options, key=lambda number: votes.get(number, 0))
    if votes.get(best, 0) > 0:
        tied = [number for number in options if votes.get(number, 0) == votes.get(best, 0)]
        if len(tied) == 1:
            return best, "snapped"
    if len(options) == 1:
        return options[0], "snapped"
    return None, "unmatched"          # two roster numbers it could be and no vote to tell: safer hidden than wrong
