import unittest

from leash_arena.limits import MAX_MESSAGE_CHARS, Limiter, MessageRejected, clean_message, visitor_key


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def reason_of(raw):
    try:
        clean_message(raw)
    except MessageRejected as exc:
        return exc.reason
    return None


class MessageTests(unittest.TestCase):
    def test_a_normal_message_passes(self):
        self.assertEqual(clean_message("  pay invoice 7  "), "pay invoice 7")

    def test_the_length_limit_is_exact(self):
        self.assertEqual(len(clean_message("a" * MAX_MESSAGE_CHARS)), MAX_MESSAGE_CHARS)
        self.assertEqual(reason_of("a" * (MAX_MESSAGE_CHARS + 1)), "too_long")

    def test_empty_and_blank_messages_are_refused(self):
        self.assertEqual(reason_of(""), "empty")
        self.assertEqual(reason_of(" \n\t "), "empty")
        self.assertEqual(reason_of("​\u0000"), "empty")

    def test_non_text_is_refused(self):
        for value in (None, 5, b"abc", ["a"], {"a": 1}):
            self.assertEqual(reason_of(value), "not_text")

    def test_control_and_direction_characters_are_dropped(self):
        self.assertEqual(clean_message("a\x00b‮c d\ud800e"), "abcde")

    def test_newline_and_tab_are_kept(self):
        self.assertEqual(clean_message("a\nb\tc"), "a\nb\tc")


class VisitorKeyTests(unittest.TestCase):
    def test_same_client_same_salt_same_key(self):
        salt = b"s" * 32
        self.assertEqual(visitor_key("203.0.113.9", salt), visitor_key("203.0.113.9", salt))

    def test_key_differs_by_client_and_by_salt(self):
        self.assertNotEqual(visitor_key("a", b"s" * 32), visitor_key("b", b"s" * 32))
        self.assertNotEqual(visitor_key("a", b"s" * 32), visitor_key("a", b"t" * 32))

    def test_the_key_does_not_contain_the_address(self):
        self.assertNotIn("203.0.113.9", visitor_key("203.0.113.9", b"s" * 32))

    def test_a_short_salt_is_refused(self):
        with self.assertRaises(ValueError):
            visitor_key("a", b"short")
        with self.assertRaises(ValueError):
            visitor_key("a", "x" * 32)


class LimiterTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()

    def limiter(self, **kw):
        return Limiter(self.clock, **kw)

    def attempt(self, lim, visitor):
        reason = lim.admit(visitor)
        if reason is None:
            lim.release(visitor)
        return reason

    def test_ten_per_hour_then_refused(self):
        lim = self.limiter()
        for _ in range(10):
            self.assertIsNone(self.attempt(lim, "v"))
            self.clock.now += 1
        self.assertEqual(self.attempt(lim, "v"), "visitor_hour")

    def test_the_hour_window_slides(self):
        lim = self.limiter()
        for _ in range(10):
            self.attempt(lim, "v")
        self.clock.now += 3601
        self.assertIsNone(self.attempt(lim, "v"))

    def test_thirty_per_day_then_refused(self):
        lim = self.limiter()
        for hour in range(3):
            for _ in range(10):
                self.assertIsNone(self.attempt(lim, "v"))
            self.clock.now += 3601
        self.assertEqual(self.attempt(lim, "v"), "visitor_day")
        self.clock.now += 86400
        self.assertIsNone(self.attempt(lim, "v"))

    def test_global_hour_limit(self):
        lim = self.limiter(per_hour=1000, per_day=1000, global_per_hour=300)
        for i in range(300):
            self.assertIsNone(self.attempt(lim, f"v{i}"))
        self.assertEqual(self.attempt(lim, "newcomer"), "global_hour")
        self.clock.now += 3601
        self.assertIsNone(self.attempt(lim, "newcomer"))

    def test_one_in_flight_per_visitor(self):
        lim = self.limiter()
        self.assertIsNone(lim.admit("v"))
        self.assertEqual(lim.admit("v"), "in_flight")
        lim.release("v")
        self.assertIsNone(lim.admit("v"))

    def test_the_queue_holds_ten(self):
        lim = self.limiter()
        for i in range(10):
            self.assertIsNone(lim.admit(f"v{i}"))
        self.assertEqual(lim.admit("v10"), "busy")
        lim.release("v3")
        self.assertIsNone(lim.admit("v10"))

    def test_a_refusal_uses_no_quota(self):
        lim = self.limiter(per_hour=2)
        self.assertIsNone(lim.admit("v"))
        for _ in range(5):
            self.assertEqual(lim.admit("v"), "in_flight")
        lim.release("v")
        self.assertIsNone(self.attempt(lim, "v"))
        self.assertEqual(self.attempt(lim, "v"), "visitor_hour")

    def test_release_of_an_unknown_visitor_changes_nothing(self):
        lim = self.limiter()
        lim.release("nobody")
        for i in range(10):
            self.assertIsNone(lim.admit(f"v{i}"))
        self.assertEqual(lim.admit("v10"), "busy")

    def test_the_order_of_reasons_is_pinned(self):
        # in_flight comes before busy, busy before the visitor limits, the visitor limits before the global limit.
        lim = self.limiter(per_hour=1, global_per_hour=1, max_active=1)
        self.assertIsNone(lim.admit("a"))
        self.assertEqual(lim.admit("a"), "in_flight")
        self.assertEqual(lim.admit("b"), "busy")
        lim.release("a")
        self.assertEqual(lim.admit("a"), "visitor_hour")
        self.assertEqual(lim.admit("b"), "global_hour")

    def test_the_visitor_table_is_bounded(self):
        lim = self.limiter(max_visitors=5, per_hour=100, per_day=100, global_per_hour=1000, max_active=100)
        for i in range(5):
            self.assertIsNone(self.attempt(lim, f"v{i}"))
        self.assertEqual(lim.admit("v5"), "busy")
        self.clock.now += 86401
        self.assertIsNone(self.attempt(lim, "v5"))


if __name__ == "__main__":
    unittest.main()
