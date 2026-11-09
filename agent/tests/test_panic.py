import unittest

from leash_agent import panic
from leash_agent.callpath import CallPathError
from leash_agent.chain import ChainError
from tests.test_callpath import make_config

NEAR = 10**24


def key(public, permission):
    return {"public_key": public, "access_key": {"nonce": 0, "permission": permission}}


def function_call(vault, methods, allowance=NEAR // 10):
    return {"FunctionCall": {"allowance": str(allowance), "receiver_id": vault, "method_names": list(methods)}}


def answer(*keys):
    return {"result": {"keys": list(keys)}}


class PanicKey(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config()
        self.out, self.calls = [], []

    def run_panic(self, keys_now, confirmed=False, network_cfg=None, env=None, runner=None):
        return panic.run(network_cfg or self.cfg, confirmed=confirmed, env=env, out=self.out.append,
                         runner=runner or (lambda argv: self.calls.append(argv) or "ok"), fetch=lambda account, network: keys_now)

    def pause(self, **kw):
        return key("ed25519:P", function_call(self.cfg.vault, panic.PANIC_METHODS, **kw))

    def test_the_key_allows_only_the_two_pause_methods_on_the_vault(self):
        self.assertEqual(panic.PANIC_METHODS, ("pause_agent", "pause_all"))
        self.assertNotIn("withdraw", panic.PANIC_METHODS)
        self.assertNotIn("resume_all", panic.PANIC_METHODS)

    def test_without_yes_nothing_is_sent_and_the_plan_is_printed(self):
        self.assertEqual(self.run_panic(answer(key("ed25519:O", "FullAccess"))), 0)
        self.assertEqual(self.calls, [])
        text = "\n".join(self.out)
        self.assertIn("plan only: nothing sent", text)
        self.assertIn("No pause key is on the account yet.", text)

    def test_with_yes_the_add_key_command_has_the_receiver_and_methods(self):
        self.run_panic(answer(key("ed25519:O", "FullAccess"), self.pause()), confirmed=True)
        argv = self.calls[0]
        self.assertEqual(argv[:4], ["near", "account", "add-key", self.cfg.owner])
        self.assertEqual(argv[argv.index("--contract-account-id") + 1], self.cfg.vault)
        self.assertEqual(argv[argv.index("--function-names") + 1], "pause_agent,pause_all")
        self.assertEqual(argv[argv.index("--allowance") + 1], "0.1 NEAR")
        self.assertIn("The pause key can call: pause_agent, pause_all.", "\n".join(self.out))

    def test_a_missing_key_after_adding_is_an_error(self):
        with self.assertRaisesRegex(CallPathError, "not found"):
            self.run_panic(answer(key("ed25519:O", "FullAccess")), confirmed=True)

    def test_a_key_with_more_methods_is_not_a_pause_key(self):
        wide = key("ed25519:W", function_call(self.cfg.vault, ("pause_all", "pause_agent", "withdraw")))
        self.assertFalse(panic.has_pause_key(answer(wide), self.cfg.vault))
        other = key("ed25519:V", function_call("other.testnet", panic.PANIC_METHODS))
        self.assertFalse(panic.has_pause_key(answer(other), self.cfg.vault))
        self.assertTrue(panic.has_pause_key(answer(wide, self.pause()), self.cfg.vault))

    def test_an_unreadable_key_list_is_an_error(self):
        with self.assertRaises(ChainError):
            panic.has_pause_key({"result": {}}, self.cfg.vault)
        with self.assertRaises(CallPathError):
            self.run_panic({"result": {}})

    def test_a_failed_add_is_reported_without_reading_on(self):
        def broken(argv):
            raise ChainError("near CLI failed")

        with self.assertRaisesRegex(CallPathError, "adding the pause key failed"):
            self.run_panic(answer(self.pause()), confirmed=True, runner=broken)

    def test_mainnet_never_sends_and_fails_on_an_unlisted_full_access_key(self):
        main = make_config("mainnet")
        env = {"LEASH_ALLOW_MAINNET": "1"}
        with self.assertRaisesRegex(CallPathError, "only verifies"):
            self.run_panic(answer(self.pause()), confirmed=True, network_cfg=main, env=env)
        self.assertEqual(self.calls, [])
        main_pause = key("ed25519:P", function_call(main.vault, panic.PANIC_METHODS))
        self.assertEqual(self.run_panic(answer(key("ed25519:O", "FullAccess"), main_pause), network_cfg=main, env=dict(env, LEASH_KNOWN_FULL_ACCESS_KEYS="ed25519:O")), 0)
        self.assertEqual(self.run_panic(answer(key("ed25519:X", "FullAccess"), main_pause), network_cfg=main, env=env), 1)
        self.assertEqual(self.calls, [])

    def test_a_pause_key_with_no_allowance_limit_is_flagged(self):
        unlimited = key("ed25519:P", {"FunctionCall": {"allowance": None, "receiver_id": self.cfg.vault, "method_names": list(panic.PANIC_METHODS)}})
        self.run_panic(answer(unlimited))
        self.assertIn("NOT AS PLANNED", "\n".join(self.out))


if __name__ == "__main__":
    unittest.main()
