"""Teste pentru logica pură a nivelelor. Rulează cu: python -m unittest discover tests"""

import unittest

from cogs.xp import (
    level_from_xp,
    level_progress,
    progress_bar,
    total_xp_for_level,
    xp_to_next_level,
)


class LevelFormulaTests(unittest.TestCase):
    def test_xp_to_next_level(self) -> None:
        self.assertEqual(xp_to_next_level(0), 100)
        self.assertEqual(xp_to_next_level(1), 155)
        self.assertEqual(xp_to_next_level(2), 220)
        self.assertEqual(xp_to_next_level(10), 1100)

    def test_total_xp_for_level(self) -> None:
        self.assertEqual(total_xp_for_level(0), 0)
        self.assertEqual(total_xp_for_level(1), 100)
        self.assertEqual(total_xp_for_level(2), 255)
        self.assertEqual(total_xp_for_level(3), 475)

    def test_level_boundaries(self) -> None:
        self.assertEqual(level_from_xp(0), 0)
        self.assertEqual(level_from_xp(99), 0)
        self.assertEqual(level_from_xp(100), 1)
        self.assertEqual(level_from_xp(254), 1)
        self.assertEqual(level_from_xp(255), 2)
        self.assertEqual(level_from_xp(-50), 0)

    def test_progress_round_trip(self) -> None:
        for level in range(0, 60):
            start = total_xp_for_level(level)
            self.assertEqual(level_progress(start), (level, 0, xp_to_next_level(level)))
            end = start + xp_to_next_level(level) - 1
            self.assertEqual(
                level_progress(end), (level, xp_to_next_level(level) - 1, xp_to_next_level(level))
            )

    def test_progress_bar(self) -> None:
        self.assertEqual(progress_bar(0, 100, 10), "▱" * 10)
        self.assertEqual(progress_bar(50, 100, 10), "▰" * 5 + "▱" * 5)
        self.assertEqual(progress_bar(200, 100, 10), "▰" * 10)


if __name__ == "__main__":
    unittest.main()
