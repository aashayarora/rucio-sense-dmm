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

# Here are the test for the data sets, ALL of the out of range tests should FAIL (hasn't finish the code yet)

def convert_to_simplified(allocations):
    simplified = []
    for x1, x2, y1, y2, rid in allocations:
        height = y2 - y1
        simplified.append((x1, x2, height, rid))
    return simplified

def run_functions(unavailable_slots, total_slots, request_r):
    slot_rects = get_next_slot(unavailable_slots, total_slots)
    slot_areas = compute_slot_areas(slot_rects)
    request_areas = r_sorted_by_area(request_r)

    result_texts, allocations, waste_rects, total_available_area, total_r_area = find_r_slot_with_allocation(
        request_areas, slot_areas, slot_rects, unavailable_slots, request_r
    )
    return result_texts, allocations, waste_rects, total_available_area, total_r_area

class TestWithData(unittest.TestCase):
    def test_no_reservations_simplified(self):
        unavailable_slots = set()
        total_slots = {(0, 10, 60)}
        request_r = [(100, 1), (150, 2)]

        result_texts, allocations, waste_rects, total_available_area, total_r_area = run_functions(
            unavailable_slots, total_slots, request_r
        )
        # (x1, x2, height, r_id)
        expected_allocations = [
            (0, 5, 20, 1),
            (0, 3.75, 40, 2),
        ]

        actual_simplified = convert_to_simplified(allocations)
        actual_sorted = sorted(actual_simplified, key=lambda x: x[3])
        expected_sorted = sorted(expected_allocations, key=lambda x: x[3])
        self.assertEqual(len(actual_sorted), len(expected_sorted))

        for actual, expected in zip(actual_sorted, expected_sorted):
            self.assertAlmostEqual(actual[0], expected[0], places=2)  # x1
            self.assertAlmostEqual(actual[1], expected[1], places=2)  # x2
            self.assertAlmostEqual(actual[2], expected[2], places=2)  # height
            self.assertEqual(actual[3], expected[3])  # r_id

    def test_small_requests_simplified(self):
        """测试小请求场景 - 简化版"""
        unavailable_slots = {(10, 15, 30)}
        total_slots = {(0, 20, 60)}
        request_r = [(50, 1), (40, 2), (30, 3)]

        result_texts, allocations, waste_rects, total_available_area, total_r_area = run_functions(
            unavailable_slots, total_slots, request_r
        )
        # (x1, x2, height, r_id)
        # allocation: r1:(0,5,10,1) r2:(0,2,20,2) r3:(0,1,30,3)
        expected_allocations = [
            (0, 5, 10, 1),
            (0, 2, 20, 2),
            (0, 1, 30, 3),
        ]

        actual_simplified = convert_to_simplified(allocations)

        # sort by r_id
        actual_sorted = sorted(actual_simplified, key=lambda x: x[3])
        expected_sorted = sorted(expected_allocations, key=lambda x: x[3])

        self.assertEqual(total_r_area, 120)  # 50+40+30
        self.assertEqual(len(actual_sorted), len(expected_sorted))

        for actual, expected in zip(actual_sorted, expected_sorted):
            self.assertAlmostEqual(actual[0], expected[0], places=2)  # x1
            self.assertAlmostEqual(actual[1], expected[1], places=2)  # x2
            self.assertAlmostEqual(actual[2], expected[2], places=2)  # height
            self.assertEqual(actual[3], expected[3])  # r_id

    """
        Overflow need to test: 
        # Will fail at this moment
        1. Considering in best fit (less than or equal to)
        2. When there are requests have the same size, fit in the higher priority one
    """
    def test_requests_overflow_with_best_fit_1(self): # Will fail at this moment
        unavailable_slots = {(5, 10, 20)}
        total_slots = {(0, 15, 40)}
        request_r = [(100, 1), (120, 2), (50, 3), (70, 5), (50, 2), (90, 1)]  # Requests (size, priority)

        result_texts, allocations, waste_rects, total_available_area, total_r_area = run_functions(
            unavailable_slots, total_slots, request_r
        )

        # Check area calculation
        self.assertEqual(total_r_area, 650)  # 200+300+150
        self.assertLess(total_available_area, total_r_area, "OVERFLOW")

        expected_allocations = [
            (0, 5, 20, 1),
            (0, 15, 8, 2),
            (0, 15, 3.33, 3),
            (0, 15, 4.06, 4),
            (0, 15, 3.33, 5)
        ]
        actual_simplified = convert_to_simplified(allocations)
        actual_sorted = sorted(actual_simplified, key=lambda x: x[3])
        expected_sorted = sorted(expected_allocations, key=lambda x: x[3])

        self.assertEqual(len(actual_sorted), 2)
        for actual, expected in zip(actual_sorted, expected_sorted):
            self.assertAlmostEqual(actual[0], expected[0], places=2)  # x1
            self.assertAlmostEqual(actual[1], expected[1], places=2)  # x2
            self.assertAlmostEqual(actual[2], expected[2], places=2)  # height
            self.assertEqual(actual[3], expected[3])

        # Check if the code print out "R6 is out of range"
        r6_rejected = any("R6" in text and "out of range" in text.lower()
                          for text in result_texts)
        self.assertTrue(r6_rejected, "R6 should be reported as out of range")

    def test_requests_overflow_requests_same_size(self): # Will fail at this moment
        unavailable_slots = {(5, 10, 20)}
        total_slots = {(0, 15, 40)}
        request_r = [(200, 1), (120, 2), (200, 3)]  # Requests (size, priority)

        result_texts, allocations, waste_rects, total_available_area, total_r_area = run_functions(
            unavailable_slots, total_slots, request_r
        )

        # Check area calculation
        self.assertEqual(total_r_area, 650)  # 200+300+150
        self.assertLess(total_available_area, total_r_area, "OVERFLOW")
        # Check R2 successfully fitted in (x1, x2, height, r_id)(0, 5, 24, 2)
        # Check R3 successfully fitted in (0, 15, 13.3, 3)
        expected_allocations = [
            (0, 5, 24, 2),
            (0, 15, 13.33, 3)
        ]
        actual_simplified = convert_to_simplified(allocations)
        actual_sorted = sorted(actual_simplified, key=lambda x: x[3])
        expected_sorted = sorted(expected_allocations, key=lambda x: x[3])

        self.assertEqual(len(actual_sorted), 2)
        for actual, expected in zip(actual_sorted, expected_sorted):
            self.assertAlmostEqual(actual[0], expected[0], places=2)  # x1
            self.assertAlmostEqual(actual[1], expected[1], places=2)  # x2
            self.assertAlmostEqual(actual[2], expected[2], places=2)  # height
            self.assertEqual(actual[3], expected[3])

        # Check if the code print out "R1 is out of range"
        r1_rejected = any("R1" in text and "out of range" in text.lower()
                          for text in result_texts)
        self.assertTrue(r1_rejected, "R1 should be reported as out of range")

    def test_requests_overflow_multiple_requests_same_size(self): # Will fail at this moment
        unavailable_slots = {(5, 10, 20)}
        total_slots = {(0, 15, 40)}
        request_r = [(200, 1), (120, 2), (200, 3), (200, 9)]  # Requests (size, priority)

        result_texts, allocations, waste_rects, total_available_area, total_r_area = run_functions(
            unavailable_slots, total_slots, request_r
        )

        # Check area calculation
        self.assertEqual(total_r_area, 650)  # 200+300+150
        self.assertLess(total_available_area, total_r_area, "OVERFLOW")
        # Check R2 successfully fitted in (x1, x2, height, r_id)(0, 5, 24, 2)
        # Check R3 successfully fitted in (0, 15, 13.3, 3)
        expected_allocations = [
            (0, 5, 24, 2),
            (0, 15, 13.33, 4)
        ]
        actual_simplified = convert_to_simplified(allocations)
        actual_sorted = sorted(actual_simplified, key=lambda x: x[3])
        expected_sorted = sorted(expected_allocations, key=lambda x: x[3])

        self.assertEqual(len(actual_sorted), 2)
        for actual, expected in zip(actual_sorted, expected_sorted):
            self.assertAlmostEqual(actual[0], expected[0], places=2)  # x1
            self.assertAlmostEqual(actual[1], expected[1], places=2)  # x2
            self.assertAlmostEqual(actual[2], expected[2], places=2)  # height
            self.assertEqual(actual[3], expected[3])

        # Check if the code print out "R1 is out of range"
        r1_rejected = any("R1" in text and "out of range" in text.lower()
                          for text in result_texts)
        self.assertTrue(r1_rejected, "R1 should be reported as out of range")
        r3_rejected = any("R3" in text and "out of range" in text.lower()
                          for text in result_texts)
        self.assertTrue(r3_rejected, f"R3 should be reported as out of range. Result texts: {result_texts}")


    # Todo: finish the following tests
    # With exact area of available slots

    # Fits in Slot 1

    # sum of requests area just over slot 1

    # sum of requests area over slot 1

    # best_under for slot 2

    # best_over for slot 2

    # other requests fits in slot 3

    # rest of the requests can fit in last slot

    # rest of the requests cannot fit in last slot



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
    unittest.main(verbosity=2, stream=sys.stdout, buffer=False)
    args = parser.parse_args()

    if args.test_method:
        suite = unittest.TestLoader().loadTestsFromName(args.test_method)
        runner = unittest.TextTestRunner(verbosity=2)
        runner.run(suite)
        run_all_tests()