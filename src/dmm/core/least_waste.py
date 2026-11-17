"""
Least-waste scheduling of rules on one path, ported from
sim/least_waste_cleaned_full_version_3.py (final_allocation).

The path is a time x bandwidth plane, capacity high and horizon wide. Others'
reservations are (start, end, bandwidth) blocks stacked from 0. Each rule is a
(size, priority) area; it gets a rectangle (t_start, t_end, bw_start, bw_end):
constant bandwidth from t_start until it is done. Rules that cannot be placed
within the horizon are dropped, smallest first.

Units are the caller's: bandwidth and size must agree (e.g. Mbps and Mbps*ticks).
Time is walked in whole ticks, as in the sim.
"""
from itertools import combinations
from math import inf

# find_best_over_and_best_under tries every subset of the rules.
MAX_RULES = 16


def get_next_slot(unavailable, total):
    """Compute available regions by subtracting unavailable from total."""
    if not total:
        return []
    t_x1, t_x2, t_h = next(iter(total))
    result_regions = []
    current_y = 0
    for u_x1, u_x2, u_h in sorted(unavailable, key=lambda u: (u[0], u[2])):
        if u_h <= current_y:
            continue
        if u_x1 > t_x1:
            result_regions.append((t_x1, u_x1, current_y, t_h))
        if u_h > current_y:
            result_regions.append((t_x1, u_x1, current_y, u_h))
        current_y = u_h
    if current_y < t_h:
        result_regions.append((t_x1, t_x2, current_y, t_h))
    return sorted(
        [(x1, x2, y1, y2) for x1, x2, y1, y2 in result_regions if x2 > x1 and y2 > y1],
        key=lambda r: (r[2], r[0], -r[3], r[1])
    )


def compute_slot_areas(slots):
    return [(x2 - x1) * (y2 - y1) for x1, x2, y1, y2 in slots]


def r_sorted_by_area(rules, reverse=False):
    """[(size, rule id)] sorted by size; rule ids count from 1."""
    return sorted([(size, idx + 1) for idx, (size, _) in enumerate(rules)], key=lambda x: x[0], reverse=reverse)


def can_fit_all_remaining_rule(r_remaining, current_slot_area):
    return sum(area for area, _ in r_remaining) <= current_slot_area


def find_best_over_and_best_under(r_remaining, current_slot_area):
    """Best-fit group: one slightly under the slot's area, one slightly over."""
    best_under, best_under_sum = (), 0
    best_over, best_over_sum = (), inf
    for n in range(1, len(r_remaining) + 1):
        for combo in combinations(r_remaining, n):
            s = sum(area for area, _ in combo)
            if s <= current_slot_area and s > best_under_sum:
                best_under, best_under_sum = combo, s
                if s == current_slot_area:
                    break
            elif s > current_slot_area and s < best_over_sum:
                best_over, best_over_sum = combo, s
        if best_under_sum == current_slot_area:
            break
    return best_under, best_under_sum, best_over, best_over_sum


def evaluate_blank2_area(best_over_sum, current_slot_area, current_slot_rect, next_slot_rect):
    x1, x2, _, _ = current_slot_rect
    overflow_h = (best_over_sum - current_slot_area) / (x2 - x1)
    return overflow_h * abs(next_slot_rect[1] - x2)


def apply_overflow_to_next_slots(slot_rects, slot_area_list, i, delta_h):
    for j in (i + 1, i + 2):
        if j >= len(slot_rects):
            break
        x1, x2, y1, y2 = slot_rects[j]
        new_y2 = y2 - delta_h
        if new_y2 <= y1:
            slot_rects.pop(j)
            slot_area_list.pop(j)
        else:
            slot_rects[j] = (x1, x2, y1, new_y2)
            slot_area_list[j] = (x2 - x1) * (new_y2 - y1)


def calculate_priority_bandwidth_ratio(original_prio, priority):
    unique = sorted(set(priority), reverse=True)
    return 1.0 if len(unique) == 1 else float(original_prio)


def has_bandwidth_conflict(x1, x2, y1, y2, unavailable_slots):
    for ux1, ux2, uh in unavailable_slots:
        if not (x2 <= ux1 or x1 >= ux2):
            if not (y1 >= uh or y2 <= 0):
                return True
    return False


def out_of_range(remaining_r, original_r_list, current_slot_area, total_available_area):
    if not remaining_r:
        return [], []
    eff_area = total_available_area if len(remaining_r) == len(
        original_r_list) and total_available_area else current_slot_area

    best_under, best_under_sum, _, _ = find_best_over_and_best_under(remaining_r, eff_area)
    if not best_under or best_under_sum > eff_area:
        return [], remaining_r[:]

    best_group_sizes = [area for area, _ in best_under]
    candidates = {}
    for area, rid in remaining_r:
        if area in best_group_sizes:
            candidates.setdefault(area, []).append((area, rid))

    final_best = []
    used = set()
    for size in best_group_sizes:
        if size in candidates:
            best_cand = max(
                (c for c in candidates[size] if c not in used),
                key=lambda x: original_r_list[x[1] - 1][1], default=None
            )
            if best_cand:
                final_best.append(best_cand)
                used.add(best_cand)

    allocated_set = set(final_best)
    return final_best, [r for r in remaining_r if r not in allocated_set]


def calc_available_area(slot_area_list):
    even_sum = sum(slot_area_list[i] for i in range(1, len(slot_area_list) - 1, 2)) if len(slot_area_list) > 1 else 0
    return even_sum + (slot_area_list[-1] if slot_area_list else 0)


def check_slot_is_empty(slot_index, slot_rects, allocation):
    if slot_index >= len(slot_rects):
        return False
    sx1, sx2, sy1, sy2 = slot_rects[slot_index]
    for x1, x2, y1, y2, rid in allocation:
        if not (x2 <= sx1 or x1 >= sx2 or y2 <= sy1 or y1 >= sy2):
            return False
    return True


def merge_two_slots(slot1, slot2):
    """Merge two rectangles into an L-shaped polygon."""
    x1_r1, x2_r1, y1_r1, y2_r1 = slot1
    x1_r2, x2_r2, y1_r2, y2_r2 = slot2
    vertices = [(0, 0), (x2_r1, 0), (x2_r1, y2_r1), (x2_r2, y1_r2), (x2_r2, y2_r2), (0, y2_r2)]
    area = (x2_r1 - x1_r1) * (y2_r1 - y1_r1) + (x2_r2 - x1_r2) * (y2_r2 - y1_r2)
    return vertices, area


def merge_new_slot(new_merged_slot, slot):
    vertices = new_merged_slot['vertices']
    x1_r, x2_r, y1_r, y2_r = slot
    max_x, max_y = max(v[0] for v in vertices), max(v[1] for v in vertices)
    new_vertices = [v for v in vertices if not ((v[0] == max_x and v[1] == max_y) or (v[0] == 0 and v[1] == max_y))]
    new_vertices.extend([(max_x, y1_r), (x2_r, y1_r), (x2_r, y2_r), (0, y2_r)])
    new_area = new_merged_slot['area'] + (x2_r - x1_r) * (y2_r - y1_r)
    return new_vertices, new_area


def find_borrowable_areas(current_time_end, total_time, total_bandwidth, unavailable_slots):
    """The free area after the current slot, which an outer slot that fits everything may borrow."""
    if current_time_end >= total_time:
        return []
    future_total = [(current_time_end, total_time, total_bandwidth)]
    adjusted_unavailable = [
        (max(x1, current_time_end), x2, h)
        for x1, x2, h in unavailable_slots
        if x2 > current_time_end and x2 > max(x1, current_time_end)
    ]
    return get_next_slot(adjusted_unavailable, future_total)


def extend_merged_area_with_future(merged_info, total_time, total_bandwidth, unavailable_slots):
    component_rects = merged_info.get('component_rects', [])
    if not component_rects:
        return merged_info
    current_max_time = max(rect[1] for rect in component_rects)
    borrowable_areas = find_borrowable_areas(current_max_time, total_time, total_bandwidth, unavailable_slots)
    if not borrowable_areas:
        return merged_info
    borrowable_area = sum((x2 - x1) * (y2 - y1) for x1, x2, y1, y2 in borrowable_areas)
    extended_info = merged_info.copy()
    extended_info['borrowable_rects'] = borrowable_areas
    extended_info['extended_area'] = merged_info['area'] + borrowable_area
    extended_info['all_rects'] = component_rects + borrowable_areas
    return extended_info


def check_allocation_validity(allocations, main_slot, request_r):
    """Whether every allocation is within the horizon and the capacity and has its rule's area."""
    bandwidth_limit, time_limit = main_slot
    for x1, x2, y1, y2, rid in allocations:
        size, _ = request_r[rid - 1]
        if x2 > time_limit or y2 > bandwidth_limit or abs((x2 - x1) * (y2 - y1) - size) > 0.1:
            return False
    return True


class _Planner:
    """One scheduling run: the sim's functions that share its bandwidth-usage map."""

    def __init__(self, unavailable_slots):
        self.usage = {}
        for x1, x2, h in unavailable_slots:
            for t in range(int(x1), int(x2)):
                self.usage[t] = h

    def current_max(self, x1, x2):
        return max((self.usage.get(t, 0) for t in range(x1, x2)), default=0)

    def update(self, x1, x2, y1, y2):
        for t in range(x1, x2):
            self.usage[t] = max(self.usage.get(t, 0), y2)

    def allocate_rules_unified(self, slot_rect, rule, rule_r, unavailable_slots=None, strategy='auto'):
        allocation = []
        x1, x2, y1, y2 = slot_rect
        slot_width = x2 - x1
        if not rule:
            return allocation

        effective_y1 = max(y1, self.current_max(int(x1), int(x2)))
        available_height = y2 - effective_y1
        if available_height <= 0:
            return allocation

        def commit_alloc(rx1, rx2, ry1, ry2, rid):
            allocation.append((rx1, rx2, ry1, ry2, rid))
            self.update(int(rx1), int(rx2), ry1, ry2)

        if strategy == 'auto':
            total_size = sum(area for area, _ in rule)
            strategy = 'fill' if len(rule) == 1 or total_size <= available_height * slot_width else 'simple'

        if strategy == 'simple':
            y_cursor = effective_y1
            for area, rid in rule:
                h = area / slot_width
                commit_alloc(x1, x2, y_cursor, y_cursor + h, rid)
                y_cursor += h
            return allocation

        if strategy == 'priority':
            total_priority = sum(rule_r[rid - 1][1] for area, rid in rule)
            y_cursor = effective_y1
            for r_area, rid in rule:
                priority = rule_r[rid - 1][1]
                height_ratio = priority / total_priority if total_priority > 0 else 1.0 / len(rule)
                allocated_height = available_height * height_ratio
                if allocated_height > 0:
                    actual_width = r_area / allocated_height
                else:
                    actual_width = slot_width
                    allocated_height = r_area / actual_width if actual_width > 0 else 0
                if actual_width > slot_width:
                    actual_width = slot_width
                    allocated_height = r_area / actual_width
                x_end, y_end = x1 + actual_width, y_cursor + allocated_height
                commit_alloc(x1, x_end, y_cursor, y_end, rid)
                y_cursor = y_end
            return allocation

        if strategy == 'fill':
            total_size = sum(area for area, _ in rule)
            if len(rule) == 1:
                area, rid = rule[0]
                height = available_height
                width = area / height if height > 0 else 0
                if width > slot_width:
                    width = slot_width
                    height = area / width if width > 0 else 0
                commit_alloc(x1, x1 + width, effective_y1, effective_y1 + height, rid)
                return allocation

            if total_size <= available_height * slot_width:
                all_priorities = [rule_r[rid - 1][1] for area, rid in rule]
                rule_data = []
                total_priority_sum = 0
                for area, rid in rule:
                    original_priority = rule_r[rid - 1][1]
                    ratio = calculate_priority_bandwidth_ratio(original_priority, all_priorities)
                    total_priority_sum += ratio
                    rule_data.append({'area': area, 'rid': rid, 'ratio': ratio})

                has_conflict = False
                y_cursor_check = effective_y1
                for req_data in rule_data:
                    height = available_height * (req_data['ratio'] / total_priority_sum)
                    width = req_data['area'] / height if height > 0 else 0
                    x_end = x1 + width
                    y_end = y_cursor_check + height
                    if ((unavailable_slots and has_bandwidth_conflict(x1, x_end, y_cursor_check, y_end, unavailable_slots))
                            or y_end > y2 or width > slot_width):
                        has_conflict = True
                        break
                    req_data['height'] = height
                    req_data['width'] = width
                    y_cursor_check = y_end

                if has_conflict:
                    return self._allocate_with_conflict_handling(slot_rect, rule, rule_r, effective_y1, slot_width)

                y_cursor = effective_y1
                for req_data in rule_data:
                    width, height = req_data['width'], req_data['height']
                    if width > slot_width:
                        width = slot_width
                        height = req_data['area'] / width if width > 0 else 0
                    commit_alloc(x1, x1 + width, y_cursor, y_cursor + height, req_data['rid'])
                    y_cursor += height
                return allocation
            return self.allocate_rules_unified(slot_rect, rule, rule_r, unavailable_slots, 'simple')

        return allocation

    def _allocate_with_conflict_handling(self, slot_rect, rule, rule_r, effective_y1, slot_width):
        allocation = []
        x1, x2, y1, y2 = slot_rect
        if not rule:
            return allocation

        initial_alloc = self.allocate_rules_unified(slot_rect, rule, rule_r, None, 'simple')
        max_y_used = max(alloc[3] for alloc in initial_alloc) if initial_alloc else effective_y1
        remaining_bw = y2 - max_y_used
        if remaining_bw <= 0:
            return initial_alloc

        p_sum = sum(rule_r[rid - 1][1] for _, rid in rule)
        priorities = [rule_r[rid - 1][1] for _, rid in rule] if p_sum > 0 else [1.0] * len(rule)
        p_sum = p_sum if p_sum > 0 else len(rule)

        y_cursor = effective_y1
        for i, (area, rid) in enumerate(rule):
            h_up = area / slot_width + remaining_bw * (priorities[i] / p_sum)
            w_up = area / h_up if h_up > 0 else slot_width
            if w_up > slot_width:
                w_up = slot_width
                h_up = area / w_up if w_up > 0 else 0
            allocation.append((x1, x1 + w_up, y_cursor, y_cursor + h_up, rid))
            self.update(int(x1), int(x1 + w_up), y_cursor, y_cursor + h_up)
            y_cursor += h_up
        return allocation

    def best_fit_allocation(self, r_remaining, current_slot_area, current_slot_rect, slot_rects, slot_area_list,
                            rule_r, i):
        allocation = []
        best_under, best_under_sum, best_over, best_over_sum = find_best_over_and_best_under(r_remaining, current_slot_area)
        blank_2 = (evaluate_blank2_area(best_over_sum, current_slot_area, current_slot_rect, slot_rects[i + 1])
                   if best_over and i + 1 < len(slot_rects) else inf)

        use_overflow = False
        if best_under and best_under_sum <= current_slot_area:
            best_fit_group = best_under
            allocation.extend(self.allocate_rules_unified(current_slot_rect, best_fit_group, rule_r, None, 'priority'))
        elif best_over and blank_2 < current_slot_area:
            best_fit_group, use_overflow = best_over, True
            allocation.extend(self.allocate_rules_unified(current_slot_rect, best_fit_group, rule_r, None, 'simple'))
        else:
            return False, allocation, r_remaining

        for val in best_fit_group:
            r_remaining.remove(val)
        if use_overflow and i + 1 < len(slot_rects):
            delta_h = (best_over_sum - current_slot_area) / (current_slot_rect[1] - current_slot_rect[0])
            apply_overflow_to_next_slots(slot_rects, slot_area_list, i, delta_h)
        return True, allocation, r_remaining

    def minimum_rule_check(self, r_remaining, current_slot_area, current_slot_rect, slot_rects, i):
        allocation = []
        min_rule_size = min(area for area, _ in r_remaining)
        if min_rule_size <= current_slot_area:
            return False, allocation, r_remaining

        next_slot_width = slot_rects[i + 1][1] - current_slot_rect[1]
        max_height_extend = current_slot_area / next_slot_width
        current_w = current_slot_rect[1] - current_slot_rect[0]
        current_h = current_slot_rect[3] - current_slot_rect[2]
        if ((min_rule_size / current_w) - current_h) > max_height_extend:
            return True, allocation, r_remaining

        min_rule = min(r_remaining, key=lambda x: x[0])
        allocation.extend(self.allocate_rules_unified(current_slot_rect, [min_rule], None, None, 'simple'))
        r_remaining.remove(min_rule)
        return True, allocation, r_remaining

    def allocate_rule_in_merged_slot(self, merged_info, rule, rule_r, unavailable_slots):
        allocation = []
        if not rule:
            return allocation
        component_rects = merged_info.get('component_rects', [])
        if not component_rects:
            return allocation

        all_rects = component_rects + merged_info.get('borrowable_rects', [])
        max_time = max(rect[1] for rect in all_rects)
        min_bandwidth = min(rect[2] for rect in all_rects)
        max_bandwidth = max(rect[3] for rect in all_rects)

        y_cursor = min_bandwidth
        for req_area, rid in rule:
            remaining_bandwidth = max_bandwidth - y_cursor
            if remaining_bandwidth <= 0:
                continue

            max_width = 0
            best_time_range = None
            for rx1, rx2, ry1, ry2 in all_rects:
                if ry2 <= y_cursor:
                    continue
                has_conflict = any(not (rx2 <= ux1 or rx1 >= ux2) and uh > y_cursor
                                   for ux1, ux2, uh in unavailable_slots)
                if not has_conflict and rx2 - rx1 > max_width:
                    max_width = rx2 - rx1
                    best_time_range = (rx1, rx2)

            if best_time_range is None:
                first_rect = all_rects[0]
                best_time_range = (first_rect[0], first_rect[1])
                max_width = first_rect[1] - first_rect[0]

            x_start, x_end = best_time_range
            height_needed = req_area / max_width
            if height_needed > remaining_bandwidth:
                actual_height = remaining_bandwidth
                actual_x_end = x_start + req_area / actual_height
                if actual_x_end > max_time:
                    actual_x_end = max_time
                    actual_height = req_area / (actual_x_end - x_start)
            else:
                actual_height = height_needed
                actual_x_end = x_end

            y_end = min(y_cursor + actual_height, max_bandwidth)
            actual_x_end = min(actual_x_end, max_time)
            allocation.append((x_start, actual_x_end, y_cursor, y_end, rid))
            self.update(int(x_start), int(actual_x_end), y_cursor, y_end)
            y_cursor = y_end
        return allocation

    def progressive_inner_outer_merge(self, start_outer_index, slot_rects, slot_area_list, allocation, r_remaining,
                                      rule_r, unavailable_slots, total_time=None, total_bandwidth=None):
        """
        Alternating inner/outer merge: an empty inner slot merges into the next outer
        one (with borrowable future area) until all remaining rules fit.
        """
        outer_index = start_outer_index
        if outer_index < 1:
            return False, {}, 0, []
        prev_inner_index = outer_index - 1
        if prev_inner_index % 2 == 0:
            return False, {}, 0, []
        if not check_slot_is_empty(prev_inner_index, slot_rects, allocation):
            return False, {}, 0, []

        prev_inner_slot = slot_rects[prev_inner_index]
        current_outer_slot = slot_rects[outer_index]

        vertices, _ = merge_two_slots(prev_inner_slot, current_outer_slot)
        merged_area = slot_area_list[prev_inner_index] + slot_area_list[outer_index]
        updated_outer = {
            'vertices': vertices,
            'area': merged_area,
            'bounding_box': (0, max(v[0] for v in vertices), 0, max(v[1] for v in vertices)),
            'component_rects': [prev_inner_slot, current_outer_slot],
        }
        if total_time is not None and total_bandwidth is not None:
            updated_outer = extend_merged_area_with_future(updated_outer, total_time, total_bandwidth, unavailable_slots)
            check_area = updated_outer.get('extended_area', merged_area)
        else:
            check_area = merged_area

        if can_fit_all_remaining_rule(r_remaining, check_area):
            alloc = self.allocate_rule_in_merged_slot(updated_outer, r_remaining, rule_r, unavailable_slots)
            return True, updated_outer, len(updated_outer['component_rects']), alloc

        updated_inner = {
            'vertices': [(0, 0), (prev_inner_slot[1], 0), (prev_inner_slot[1], prev_inner_slot[3]),
                         (0, prev_inner_slot[3])],
            'area': slot_area_list[prev_inner_index],
            'bounding_box': prev_inner_slot,
            'component_rects': [prev_inner_slot],
        }

        next_inner_index = outer_index + 1
        next_outer_index = outer_index + 2
        while next_outer_index < len(slot_rects):
            if next_inner_index >= len(slot_rects):
                break
            next_inner_slot = slot_rects[next_inner_index]
            vertices, _ = merge_new_slot(updated_inner, next_inner_slot)
            updated_inner = {
                'vertices': vertices,
                'area': updated_inner['area'] + slot_area_list[next_inner_index],
                'bounding_box': (0, max(v[0] for v in vertices), 0, max(v[1] for v in vertices)),
                'component_rects': updated_inner['component_rects'] + [next_inner_slot],
            }

            next_outer_slot = slot_rects[next_outer_index]
            vertices, _ = merge_new_slot(updated_inner, next_outer_slot)
            updated_outer = {
                'vertices': vertices,
                'area': updated_inner['area'] + slot_area_list[next_outer_index],
                'bounding_box': (0, max(v[0] for v in vertices), 0, max(v[1] for v in vertices)),
                'component_rects': updated_inner['component_rects'] + [next_outer_slot],
            }
            if total_time is not None and total_bandwidth is not None:
                updated_outer = extend_merged_area_with_future(updated_outer, total_time, total_bandwidth,
                                                               unavailable_slots)
                check_area = updated_outer.get('extended_area', updated_outer['area'])
            else:
                check_area = updated_outer['area']

            if can_fit_all_remaining_rule(r_remaining, check_area):
                alloc = self.allocate_rule_in_merged_slot(updated_outer, r_remaining, rule_r, unavailable_slots)
                return True, updated_outer, len(updated_outer['component_rects']), alloc

            next_inner_index = next_outer_index + 1
            next_outer_index = next_outer_index + 2

        return False, updated_outer, len(updated_outer.get('component_rects', [])), []

    def last_slot(self, r_remaining, current_slot_area, current_slot_rect, rule_r, unavailable_slots,
                  total_available_area, slot_rects, slot_area_list, allocation, current_slot_index):
        allocation_result = []
        effective_slot_area = current_slot_area
        if current_slot_index > 0:
            prev_idx = current_slot_index - 1
            if prev_idx % 2 == 1 and check_slot_is_empty(prev_idx, slot_rects, allocation):
                effective_slot_area += slot_area_list[prev_idx]
                chk = prev_idx - 2
                while chk >= 0 and chk % 2 == 1 and check_slot_is_empty(chk, slot_rects, allocation):
                    effective_slot_area += slot_area_list[chk]
                    chk -= 2

        if can_fit_all_remaining_rule(r_remaining, effective_slot_area):
            if effective_slot_area > current_slot_area:
                success, _, _, alloc = self.progressive_inner_outer_merge(
                    current_slot_index, slot_rects, slot_area_list, allocation, r_remaining, rule_r, unavailable_slots
                )
                if success:
                    allocation_result.extend(alloc)
                    return allocation_result
            else:
                allocation_result.extend(
                    self.allocate_rules_unified(current_slot_rect, r_remaining, rule_r, unavailable_slots, 'fill'))
            return allocation_result

        best_group, _ = out_of_range(r_remaining, rule_r, effective_slot_area, total_available_area)
        if best_group:
            allocation_result.extend(self.allocate_rules_unified(current_slot_rect, best_group, rule_r, None, 'priority'))
        return allocation_result

    def find_r_slot_with_allocation(self, r_list, slot_area_list, slot_rects, unavailable_slots, rule_r,
                                    total_time=None, total_bandwidth=None, enable_advanced_features=True):
        r_remaining = r_list[:]
        total_available_area = calc_available_area(slot_area_list)
        allocation = []
        i = 0
        compare_mode = True

        while r_remaining and i < len(slot_area_list):
            current_slot_area = slot_area_list[i]
            current_slot_rect = slot_rects[i]

            if i == len(slot_area_list) - 1:
                allocation.extend(self.last_slot(
                    r_remaining, current_slot_area, current_slot_rect, rule_r, unavailable_slots,
                    total_available_area, slot_rects, slot_area_list, allocation, i
                ))
                return allocation

            if compare_mode:
                if enable_advanced_features:
                    success, _, _, alloc = self.progressive_inner_outer_merge(
                        i, slot_rects, slot_area_list, allocation, r_remaining, rule_r, unavailable_slots,
                        total_time, total_bandwidth
                    )
                    if success:
                        allocation.extend(alloc)
                        return allocation
                if can_fit_all_remaining_rule(r_remaining, current_slot_area):
                    allocation.extend(
                        self.allocate_rules_unified(current_slot_rect, r_remaining, rule_r, unavailable_slots, 'fill'))
                    return allocation
                i += 1
                compare_mode = False
                continue

            if enable_advanced_features:
                should_continue, alloc, r_remaining = self.minimum_rule_check(
                    r_remaining, current_slot_area, current_slot_rect, slot_rects, i)
                if should_continue:
                    allocation.extend(alloc)
                    i += 1
                    compare_mode = True
                    continue

            if enable_advanced_features:
                _, alloc, r_remaining = self.best_fit_allocation(
                    r_remaining, current_slot_area, current_slot_rect, slot_rects, slot_area_list, rule_r, i)
                allocation.extend(alloc)
            else:
                best_under, best_under_sum, _, _ = find_best_over_and_best_under(r_remaining, current_slot_area)
                if best_under and best_under_sum <= current_slot_area:
                    allocation.extend(self.allocate_rules_unified(current_slot_rect, best_under, rule_r, None, 'priority'))
                    for val in best_under:
                        r_remaining.remove(val)

            i += 1
            compare_mode = True

        return allocation


def _run_v2(unavailable_slots, main_slot, request_r, keep=(), max_iterations=10):
    """
    Allocate without the advanced features, dropping the smallest rule each time
    the result is invalid. Rules in keep (0-based) are dropped last.
    """
    bandwidth, time = main_slot
    total_slots = [(0, time, bandwidth)]
    remaining = list(range(len(request_r)))
    allocations = []
    for _ in range(max_iterations):
        if not remaining:
            return [], list(range(len(request_r)))
        slot_rects = get_next_slot(unavailable_slots, total_slots)
        slot_areas = compute_slot_areas(slot_rects)
        request_areas = sorted(((request_r[i][0], i + 1) for i in remaining), key=lambda x: x[0])
        allocations = _Planner(unavailable_slots).find_r_slot_with_allocation(
            request_areas, slot_areas, slot_rects, unavailable_slots, request_r, enable_advanced_features=False)
        placed = {rid for *_, rid in allocations}
        unplaced = [i for i in range(len(request_r)) if i + 1 not in placed]
        candidates = [i for i in remaining if i not in keep]
        if check_allocation_validity(allocations, main_slot, request_r):
            if not (set(unplaced) & keep and candidates):
                return allocations, unplaced
        candidates = candidates or remaining
        smallest = min(candidates, key=lambda i: request_r[i][0])
        remaining.remove(smallest)
    placed = {rid for *_, rid in allocations}
    return allocations, [i for i in range(len(request_r)) if i + 1 not in placed]


def final_allocation(unavailable_slots, main_slot, request_r, keep=()):
    """
    Schedule request_r = [(size, priority)] on a path main_slot = (bandwidth, time)
    around unavailable_slots = [(start, end, bandwidth)].

    Returns (allocations, dropped): allocations are (t_start, t_end, bw_start,
    bw_end, rule id counting from 1); dropped are 0-based indices into request_r.
    Rules in keep are dropped only once no other rule is left to drop.
    """
    bandwidth, time = main_slot
    total_slots = [(0, time, bandwidth)]
    slot_rects = get_next_slot(unavailable_slots, total_slots)
    slot_areas = compute_slot_areas(slot_rects)
    allocations = _Planner(unavailable_slots).find_r_slot_with_allocation(
        r_sorted_by_area(request_r), slot_areas, slot_rects, unavailable_slots, request_r, time, bandwidth)
    if check_allocation_validity(allocations, main_slot, request_r):
        placed = {rid for *_, rid in allocations}
        dropped = [i for i in range(len(request_r)) if i + 1 not in placed]
        if not set(dropped) & set(keep):
            return allocations, dropped
    return _run_v2(unavailable_slots, main_slot, request_r, keep=set(keep))
