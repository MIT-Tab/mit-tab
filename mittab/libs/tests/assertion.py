
def assert_nearly_equal(left, right, precision=7, message=None):
    """Asserts that left is equal to right up to precision digits"""
    condition = round(abs(left - right), precision) == 0
    if message is not None:
        assert condition, message
    else:
        assert condition
