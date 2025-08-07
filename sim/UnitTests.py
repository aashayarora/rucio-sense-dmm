import unittest
from unittest.mock import patch, MagicMock
import sys
import os

# Add the parent directory to the path so we can import the main module
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from find_least_waste import (
    get_current_max_bandwidth,
    update_bandwidth_usage,
    get_next_slot,
    compute_slot_areas,
    r_sorted_by_area,
    can_fit_all_remaining_requests,
    allocate_requests_in_slot,
    find_best_fit_groups,
    evaluate_blank2_area,
    apply_overflow_penalty_to_next_slots,
    calculate_priority_bandwidth_ratio,
    find_r_slot_with_allocation,
    current_bandwidth_usage
)


class TestSlotCalculations(unittest.TestCase):
    """Test slot calculation functions"""

    def test_get_next_slot_simple(self):
        """Test basic slot calculation"""
        unavailable = {(10, 15, 50)}
        total = {(0, 20, 100)}

        slots = get_next_slot(unavailable, total)

        self.assertTrue(len(slots) > 0)

        for slot in slots:
            self.assertEqual(len(slot), 4)
            x1, x2, y1, y2 = slot
            self.assertLessEqual(x1, x2)
            self.assertLessEqual(y1, y2)

    def test_get_next_slot_no_unavailable(self):
        """Test slot calculation with no unavailable slots"""
        unavailable = set()
        total = {(0, 10, 100)}

        slots = get_next_slot(unavailable, total)

        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0], (0, 10, 0, 100))

    def test_compute_slot_areas(self):
        """Test area computation for slots"""
        slots = [(0, 10, 0, 50), (10, 20, 25, 100)]
        areas = compute_slot_areas(slots)

        expected_areas = [
            (10 - 0) * (50 - 0),
            (20 - 10) * (100 - 25)
        ]
        self.assertEqual(areas, expected_areas)


class TestRequestSorting(unittest.TestCase):
    """Test request sorting and utility functions"""

    def test_r_sorted_by_area_ascending(self):
        """Test sorting requests by area in ascending order"""
        requests = [(300, 5), (100, 2), (500, 1)]
        sorted_requests = r_sorted_by_area(requests, reverse=False)

        expected = [(100, 2), (300, 1), (500, 3)]
        self.assertEqual(sorted_requests, expected)


class TestRequestFitting(unittest.TestCase):
    """Test request fitting logic"""

    def test_can_fit_all_remaining_requests_true(self):
        """Test when all requests can fit"""
        requests = [(100, 1), (200, 2)]
        slot_area = 350

        result = can_fit_all_remaining_requests(requests, slot_area)
        self.assertTrue(result)

    def test_can_fit_all_remaining_requests_false(self):
        """Test when requests cannot fit"""
        requests = [(100, 1), (200, 2)]
        slot_area = 250

        result = can_fit_all_remaining_requests(requests, slot_area)
        self.assertFalse(result)

    def test_can_fit_all_remaining_requests_exact(self):
        """Test when requests fit exactly"""
        requests = [(100, 1), (200, 2)]
        slot_area = 300

        result = can_fit_all_remaining_requests(requests, slot_area)
        self.assertTrue(result)


class TestAllocationFunctions(unittest.TestCase):
    """Test allocation functions"""

    def setUp(self):
        global current_bandwidth_usage
        from find_least_waste import current_bandwidth_usage
        current_bandwidth_usage.clear()

    def test_allocate_requests_in_slot_simple(self):
        """Test simple request allocation in slot"""
        slot_rect = (0, 10, 0, 100)
        requests = [(200, 1), (300, 2)]

        allocation, wasted = allocate_requests_in_slot(slot_rect, requests)

        self.assertEqual(len(allocation), 2)

        for alloc in allocation:
            self.assertEqual(len(alloc), 5)
            x1, x2, y1, y2, rid = alloc
            self.assertLessEqual(x1, x2)
            self.assertLessEqual(y1, y2)
            self.assertIn(rid, [1, 2])

    def test_allocate_requests_in_slot_empty(self):
        """Test allocation with no requests"""
        slot_rect = (0, 10, 0, 100)
        requests = []

        allocation, wasted = allocate_requests_in_slot(slot_rect, requests)

        self.assertEqual(len(allocation), 0)

    def test_find_best_fit_groups(self):
        """Test finding best fit groups"""
        requests = [(100, 1), (150, 2), (200, 3)]
        slot_area = 250

        best_under, best_under_sum, best_over, best_over_sum = find_best_fit_groups(requests, slot_area)

        self.assertLessEqual(best_under_sum, slot_area)
        if best_over:
            self.assertGreater(best_over_sum, slot_area)


class TestPriorityRatio(unittest.TestCase):
    def test_calculate_priority_bandwidth_ratio_same_priority(self):
        """Test priority ratio when all priorities are the same"""
        result = calculate_priority_bandwidth_ratio(5, [5, 5, 5])
        self.assertEqual(result, 1.0)

    def test_calculate_priority_bandwidth_ratio_different_priority(self):
        """Test priority ratio with different priorities"""
        result = calculate_priority_bandwidth_ratio(3, [1, 2, 3])
        self.assertEqual(result, 3.0)


class TestOverflowHandling(unittest.TestCase):
    """Test overflow and penalty functions"""

    def test_evaluate_blank2_area(self):
        """Test blank area evaluation for overflow"""
        best_over_sum = 300
        current_slot_area = 250
        current_slot_rect = (0, 10, 0, 25)
        next_slot_rect = (10, 15, 0, 25)

        result = evaluate_blank2_area(best_over_sum, current_slot_area,
                                      current_slot_rect, next_slot_rect)
        self.assertIsInstance(result, (int, float))
        self.assertGreater(result, 0)

    def test_evaluate_blank2_area_no_next_slot(self):
        """Test blank area evaluation when no next slot exists"""
        result = evaluate_blank2_area(300, 250, (0, 10, 0, 25), None)
        self.assertEqual(result, float('inf'))

    def test_apply_overflow_penalty_to_next_slots(self):
        """Test applying overflow penalty to subsequent slots"""
        slot_rects = [(0, 10, 0, 100), (10, 20, 0, 100), (20, 30, 0, 100)]
        slot_area_list = [1000, 1000, 1000]
        delta_h = 25

        apply_overflow_penalty_to_next_slots(slot_rects, slot_area_list, 0, delta_h)

        self.assertTrue(len(slot_rects) >= 1)


class TestFirstAreaFits(unittest.TestCase):
    """Testing of all requests fit in first area (perfectly fit/ fit in)"""

    def setUp(self):
        global current_bandwidth_usage
        from find_least_waste import current_bandwidth_usage
        current_bandwidth_usage.clear()
        self.test_unavailable = {(10, 13, 50), (15, 30, 75)}
        self.test_total = {(0, 30, 120)}
        # available = {(0, 10, 0, 120), (0, 10, 0, 50), (0, 15, 50, 120), (0, 15, 50, 25), (0, 30, 75, 120)}

    def test_requests_can_fit_in_slot1(self):
        """Test case 1: requests can fit in slot 1"""
        requests = [(200, 1), (150, 2)]

        slots = get_next_slot(self.test_unavailable, self.test_total)
        slot_areas = compute_slot_areas(slots)
        request_areas = r_sorted_by_area(requests)

        result_texts, allocations, waste_rects, total_available, total_required = find_r_slot_with_allocation(
            request_areas, slot_areas, slots)

        self.assertGreater(len(allocations), 0)
        self.assertLessEqual(total_required, total_available)

        first_slot_allocations = [alloc for alloc in allocations if alloc[0] == slots[0][0]]
        self.assertGreater(len(first_slot_allocations), 0)

        print(f"Test 1 - Can fit in slot 1:")
        print(f"  Total required: {total_required}, Total available: {total_available}")
        print(f"  Allocations: {len(allocations)}")
        for alloc in allocations:
            print(f"    R{alloc[4]}: time [{alloc[0]:.1f}, {alloc[1]:.1f}], bandwidth [{alloc[2]:.1f}, {alloc[3]:.1f}]")

    def test_requests_perfectly_fit_in_slot1(self):
        """Test case 2: requests perfectly fit in slot 1"""
        slots = get_next_slot(self.test_unavailable, self.test_total)
        slot_areas = compute_slot_areas(slots)

        if len(slot_areas) > 0:
            first_slot_area = slot_areas[0]
            requests = [(first_slot_area // 2, 1), (first_slot_area - first_slot_area // 2, 2)]
            request_areas = r_sorted_by_area(requests)

            result_texts, allocations, waste_rects, total_available, total_required = find_r_slot_with_allocation(
                request_areas, slot_areas, slots)

            total_allocated_area = sum(area for area, _ in request_areas)
            self.assertEqual(total_allocated_area, first_slot_area)

            print(f"Test 2 - Perfect fit in slot 1:")
            print(f"  First slot area: {first_slot_area}")
            print(f"  Total request area: {total_allocated_area}")
            print(f"  Perfect fit: {total_allocated_area == first_slot_area}")



def run_all_tests():
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    return runner.run(suite)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Run bandwidth allocation tests')
    parser.add_argument('--class', dest='test_class', help='Run specific test class')
    parser.add_argument('--method', dest='test_method', help='Run specific test method')

    args = parser.parse_args()

    if args.test_method:
        suite = unittest.TestLoader().loadTestsFromName(args.test_method)
        runner = unittest.TextTestRunner(verbosity=2)
        runner.run(suite)
        run_all_tests()