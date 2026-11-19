import math
import unittest

from shapely import box
import numpy as np

from contour_area import InvalidContour, area_cells, area_grid, difference, polygon, region


def square(left, bottom, size):
    return [(left, bottom), (left + size, bottom),
            (left + size, bottom + size), (left, bottom + size)]


class ContourAreaTests(unittest.TestCase):
    def test_nested_shifted_identical_and_disjoint(self):
        base = square(-5, -5, 10)
        for comp, expected in [(square(-3, -3, 6), (64, 0)),
                               (square(0, -5, 10), (50, 50)),
                               (base, (0, 0)), (square(10, -5, 10), (100, 100))]:
            with self.subTest(comp=comp):
                inner, outer = difference(base, comp)
                self.assertAlmostEqual(inner.area, expected[0])
                self.assertAlmostEqual(outer.area, expected[1])
                for shape in (inner, outer):
                    cells = area_cells(shape, 21, 64)
                    self.assertAlmostEqual(math.fsum(cells.values()), shape.area, places=8)
                    self.assertTrue(all(0 <= k < 64 ** 2 for k in cells))

    def test_fractional_cells_match_vector_clipping_with_hole_and_reversed_winding(self):
        base, comp = square(-1.7, -1.3, 3.1), square(-.5, -.8, 1.2)[::-1]
        shape, _ = difference(base, comp)
        cells = area_cells(shape, 2, 8)
        bp, _ = polygon(base)
        cp, _ = polygon(comp)
        path = bp.difference(cp)
        for row in range(8):
            for col in range(8):
                clip = box(-2 + col * .5, 2 - (row + 1) * .5, -2 + (col + 1) * .5, 2 - row * .5)
                expected = region(path.intersection(clip)).area
                self.assertAlmostEqual(cells.get(row * 8 + col, 0), expected, places=10)

    def test_nonconvex_and_subpixel_difference(self):
        base = [(-2, -2), (2, -2), (2, -1), (-1, -1), (-1, 2), (-2, 2)]
        for delta in (.2, .001):
            comp = [(x + delta, y - delta) for x, y in base]
            for shape in difference(base, comp):
                cells = area_cells(shape, 3, 64)
                self.assertAlmostEqual(sum(cells.values()), shape.area, places=8)

    def test_invalid_contours_are_not_repaired(self):
        for points in ([(0, 0), (1, 1)], [(0, 0), (1, 0), (2, 0)],
                       [(0, 0), (1, 1), (0, 1), (1, 0)],
                       [(0, 0), (math.nan, 1), (1, 0)]):
            with self.subTest(points=points), self.assertRaises(InvalidContour):
                difference(square(-1, -1, 2), points)

    def test_batch_accumulation_preserves_overlaps_holes_and_frame_boundaries(self):
        hole = box(-2, -2, 2, 2).difference(box(-1, -1, 1, 1))
        shapes = [hole] * 257 + [box(-2, -2, 0, 0), box(0, 0, 2, 2)]
        grid = area_grid(shapes, 2, 8)
        expected = np.zeros((8, 8))
        for row in range(8):
            for col in range(8):
                cell = box(-2 + col / 2, 2 - (row + 1) / 2, -2 + (col + 1) / 2, 2 - row / 2)
                expected[row, col] = sum(shape.intersection(cell).area for shape in shapes)
        np.testing.assert_allclose(grid, expected, rtol=1e-10, atol=1e-10)
        self.assertAlmostEqual(grid.sum(), sum(shape.area for shape in shapes))
        self.assertEqual(area_grid([], 2, 8).sum(), 0)
        with self.assertRaisesRegex(ValueError, "does not contain"):
            area_grid([box(-3, -1, 1, 1)], 2, 8)


if __name__ == "__main__":
    unittest.main()
