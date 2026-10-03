from scripts.apply_rdh_block_equivalency_results import allocate_integer, district_key


def test_district_key_normalizes_house_and_numeric_ids():
    assert district_key("HD-013") == "13"
    assert district_key("07") == "7"


def test_allocate_integer_conserves_total_and_uses_largest_remainder():
    result = allocate_integer(10, {"1": 1.0, "2": 1.0, "3": 1.0})
    assert sum(result.values()) == 10
    assert result == {"1": 4, "2": 3, "3": 3}
