
def assert_nearly_equal(left, right, precision=7):
    """Asserts that left is equal to right up to precision digits"""
    assert round(abs(left - right), precision) == 0
