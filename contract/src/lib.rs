//! LEASH vault: on-chain spending policy for AI agents.
//!
//! Funds are held by this contract. Agents can only request payments through
//! `spend`, and the owner's policy is enforced on-chain.

use near_sdk::json_types::{U128, U64};
use near_sdk::serde_json::{json, Value};
use near_sdk::store::LookupMap;
use near_sdk::{
    assert_one_yocto, env, near, require, AccountId, BorshStorageKey, NearToken, PanicOnDefault,
    Promise,
};

const DAY_NS: u64 = 86_400_000_000_000;
const MAX_ALLOWLIST: usize = 20;
const MAX_MEMO_BYTES: usize = 128;
/// 0.1 NEAR kept back so the contract never drains below its storage cost.
const RESERVE_BUFFER: u128 = 10u128.pow(23);

#[near(serializers = [borsh])]
#[derive(BorshStorageKey)]
enum StorageKey {
    Agents,
}

#[near(serializers = [borsh])]
pub struct Policy {
    pub per_tx_cap: u128,
    pub daily_cap: u128,
    pub spent_in_window: u128,
    pub window_start_ns: u64,
    pub allowlist: Vec<AccountId>,
    pub paused: bool,
}

#[near(serializers = [json])]
pub struct PolicyView {
    pub per_tx_cap: U128,
    pub daily_cap: U128,
    pub spent_in_window: U128,
    pub window_start_ns: U64,
    pub allowlist: Vec<AccountId>,
    pub paused: bool,
}

fn emit(event: &str, data: Value) {
    env::log_str(&format!(
        "EVENT_JSON:{}",
        json!({
            "standard": "leash",
            "version": env!("CARGO_PKG_VERSION"),
            "event": event,
            "data": [data]
        })
    ));
}

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct Leash {
    owner: AccountId,
    paused_all: bool,
    agents: LookupMap<AccountId, Policy>,
    allowed_total: u64,
    denied_total: u64,
}

#[near]
impl Leash {
    /// Initializes the vault. Only the contract account itself may call this,
    /// so deploy and init must happen in one batch.
    #[init]
    pub fn new(owner: AccountId) -> Self {
        require!(
            env::predecessor_account_id() == env::current_account_id(),
            "init must be called by the contract account"
        );
        require!(
            owner != env::current_account_id(),
            "owner must differ from the vault account"
        );
        Self {
            owner,
            paused_all: false,
            agents: LookupMap::new(StorageKey::Agents),
            allowed_total: 0,
            denied_total: 0,
        }
    }

    // ---------- owner: money in/out ----------

    /// Adds NEAR to the vault. Anyone may fund.
    #[payable]
    pub fn fund(&mut self) {
        let deposit = env::attached_deposit().as_yoctonear();
        require!(deposit > 0, "attach NEAR to fund");
        emit(
            "funded",
            json!({"from": env::predecessor_account_id(), "amount": deposit.to_string()}),
        );
    }

    /// Owner only. Requires 1 yoctoNEAR so function-call keys cannot call it.
    #[payable]
    pub fn withdraw(&mut self, amount: U128) -> Promise {
        self.assert_owner_strict();
        require!(
            amount.0 > 0 && amount.0 <= self.available_balance_yocto(),
            "invalid amount"
        );
        emit("withdrawn", json!({"amount": amount.0.to_string()}));
        Promise::new(self.owner.clone()).transfer(NearToken::from_yoctonear(amount.0))
    }

    // ---------- owner: policy ----------

    /// Owner only. Creates or updates an agent policy. The running daily
    /// counter is preserved, so editing a policy never resets the window.
    #[payable]
    pub fn set_policy(
        &mut self,
        agent: AccountId,
        per_tx_cap: U128,
        daily_cap: U128,
        allowlist: Vec<AccountId>,
    ) {
        self.assert_owner_strict();
        require!(allowlist.len() <= MAX_ALLOWLIST, "allowlist too long");
        require!(
            per_tx_cap.0 <= daily_cap.0,
            "per_tx_cap must be <= daily_cap"
        );
        require!(agent != self.owner, "owner cannot be an agent");
        let (spent, start, paused) = match self.agents.get(&agent) {
            Some(p) => (p.spent_in_window, p.window_start_ns, p.paused),
            None => (0, 0, false),
        };
        self.agents.insert(
            agent.clone(),
            Policy {
                per_tx_cap: per_tx_cap.0,
                daily_cap: daily_cap.0,
                spent_in_window: spent,
                window_start_ns: start,
                allowlist,
                paused,
            },
        );
        emit(
            "policy_set",
            json!({
                "agent": agent,
                "per_tx_cap": per_tx_cap.0.to_string(),
                "daily_cap": daily_cap.0.to_string()
            }),
        );
    }

    /// Owner only. Removes an agent and its policy.
    #[payable]
    pub fn remove_agent(&mut self, agent: AccountId) {
        self.assert_owner_strict();
        self.agents.remove(&agent);
        emit("agent_removed", json!({"agent": agent}));
    }

    /// Owner only. Blocks one agent. Safe to call with a function-call key.
    pub fn pause_agent(&mut self, agent: AccountId) {
        self.assert_owner();
        let p = self
            .agents
            .get_mut(&agent)
            .unwrap_or_else(|| env::panic_str("unknown agent"));
        p.paused = true;
        emit("agent_paused", json!({"agent": agent}));
    }

    /// Owner only. Requires 1 yoctoNEAR.
    #[payable]
    pub fn resume_agent(&mut self, agent: AccountId) {
        self.assert_owner_strict();
        let p = self
            .agents
            .get_mut(&agent)
            .unwrap_or_else(|| env::panic_str("unknown agent"));
        p.paused = false;
        emit("agent_resumed", json!({"agent": agent}));
    }

    /// Owner only. Global kill switch. Safe to call with a function-call key.
    pub fn pause_all(&mut self) {
        self.assert_owner();
        self.paused_all = true;
        emit("paused_all", json!({}));
    }

    /// Owner only. Requires 1 yoctoNEAR.
    #[payable]
    pub fn resume_all(&mut self) {
        self.assert_owner_strict();
        self.paused_all = false;
        emit("resumed_all", json!({}));
    }

    // ---------- agent: the only way money leaves ----------

    /// Requests a payment. Returns `true` if the transfer was scheduled and
    /// `false` if the policy denied it. Denials emit `spend_denied` with a
    /// reason. Callers without a policy are reverted and leave no trace.
    pub fn spend(&mut self, to: AccountId, amount: U128, memo: Option<String>) -> bool {
        let agent = env::predecessor_account_id();
        if !self.agents.contains_key(&agent) {
            env::panic_str("no policy for caller");
        }
        let amount = amount.0;
        let now = env::block_timestamp();
        let memo = memo.unwrap_or_default();
        let memo_ok = memo.len() <= MAX_MEMO_BYTES;
        let logged_memo = if memo_ok { memo } else { String::new() };

        let available = self.available_balance_yocto();
        let reason: Option<&'static str> = if self.paused_all {
            Some("all_paused")
        } else if !memo_ok {
            Some("memo_too_long")
        } else {
            match self.agents.get_mut(&agent) {
                None => env::panic_str("no policy for caller"),
                Some(p) => {
                    if p.paused {
                        Some("agent_paused")
                    } else if amount == 0 {
                        Some("zero_amount")
                    } else if amount > p.per_tx_cap {
                        Some("over_per_tx_cap")
                    } else if !p.allowlist.contains(&to) {
                        Some("not_allowlisted")
                    } else {
                        if now.saturating_sub(p.window_start_ns) >= DAY_NS {
                            p.window_start_ns = now;
                            p.spent_in_window = 0;
                        }
                        match p.spent_in_window.checked_add(amount) {
                            Some(total) if total <= p.daily_cap => {
                                if amount > available {
                                    Some("insufficient_funds")
                                } else {
                                    p.spent_in_window = total;
                                    None
                                }
                            }
                            _ => Some("over_daily_cap"),
                        }
                    }
                }
            }
        };

        match reason {
            Some(r) => {
                self.denied_total += 1;
                emit(
                    "spend_denied",
                    json!({
                        "agent": agent,
                        "to": to,
                        "amount": amount.to_string(),
                        "reason": r,
                        "memo": logged_memo
                    }),
                );
                false
            }
            None => {
                self.allowed_total += 1;
                emit(
                    "spend_allowed",
                    json!({
                        "agent": agent,
                        "to": to,
                        "amount": amount.to_string(),
                        "memo": logged_memo
                    }),
                );
                Promise::new(to)
                    .transfer(NearToken::from_yoctonear(amount))
                    .detach();
                true
            }
        }
    }

    // ---------- views ----------

    pub fn get_owner(&self) -> AccountId {
        self.owner.clone()
    }

    pub fn is_paused_all(&self) -> bool {
        self.paused_all
    }

    /// Returns (allowed, denied) counters for agents with a policy.
    pub fn get_stats(&self) -> (U64, U64) {
        (U64(self.allowed_total), U64(self.denied_total))
    }

    pub fn get_available_balance(&self) -> U128 {
        U128(self.available_balance_yocto())
    }

    /// Returns the policy with the effective spent amount for the current window.
    pub fn get_policy(&self, agent: AccountId) -> Option<PolicyView> {
        let now = env::block_timestamp();
        self.agents.get(&agent).map(|p| {
            let expired = now.saturating_sub(p.window_start_ns) >= DAY_NS;
            PolicyView {
                per_tx_cap: U128(p.per_tx_cap),
                daily_cap: U128(p.daily_cap),
                spent_in_window: U128(if expired { 0 } else { p.spent_in_window }),
                window_start_ns: U64(p.window_start_ns),
                allowlist: p.allowlist.clone(),
                paused: p.paused,
            }
        })
    }

    // ---------- internal ----------

    fn assert_owner(&self) {
        require!(env::predecessor_account_id() == self.owner, "owner only");
    }

    /// Owner check plus exactly 1 yoctoNEAR (impossible with function-call keys).
    fn assert_owner_strict(&self) {
        self.assert_owner();
        assert_one_yocto();
    }

    fn available_balance_yocto(&self) -> u128 {
        let reserve = env::storage_byte_cost()
            .as_yoctonear()
            .saturating_mul(env::storage_usage() as u128);
        env::account_balance()
            .as_yoctonear()
            .saturating_sub(reserve)
            .saturating_sub(RESERVE_BUFFER)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use near_sdk::test_utils::{accounts, get_logs, VMContextBuilder};
    use near_sdk::testing_env;

    const NEAR: u128 = 10u128.pow(24);
    const T0: u64 = 1_000 * DAY_NS;

    fn vault() -> AccountId {
        "leash.testnet".parse().unwrap()
    }

    fn ctx(caller: &AccountId, ts: u64, deposit_yocto: u128) -> VMContextBuilder {
        let mut b = VMContextBuilder::new();
        b.current_account_id(vault())
            .signer_account_id(caller.clone())
            .predecessor_account_id(caller.clone())
            .block_timestamp(ts)
            .account_balance(NearToken::from_near(100))
            .attached_deposit(NearToken::from_yoctonear(deposit_yocto));
        b
    }

    fn set_ctx(caller: &AccountId, ts: u64) {
        testing_env!(ctx(caller, ts, 0).build());
    }

    fn owner_ctx(owner: &AccountId, ts: u64) {
        testing_env!(ctx(owner, ts, 1).build());
    }

    fn denied_with(reason: &str) -> bool {
        get_logs().iter().any(|l| {
            l.contains("\"event\":\"spend_denied\"")
                && l.contains(&format!("\"reason\":\"{reason}\""))
        })
    }

    /// owner = accounts(0), agent = accounts(1), target = accounts(2)
    fn setup() -> (Leash, AccountId, AccountId, AccountId) {
        let owner = accounts(0);
        let agent = accounts(1);
        let target = accounts(2);
        set_ctx(&vault(), T0);
        let mut c = Leash::new(owner.clone());
        owner_ctx(&owner, T0);
        c.set_policy(
            agent.clone(),
            U128(2 * NEAR),
            U128(5 * NEAR),
            vec![target.clone()],
        );
        (c, owner, agent, target)
    }

    // ----- spending rules -----

    #[test]
    fn allowed_within_limits() {
        let (mut c, _o, agent, target) = setup();
        set_ctx(&agent, T0);
        assert!(c.spend(target, U128(NEAR), Some("ok".to_string())));
        let (a, d) = c.get_stats();
        assert_eq!((a.0, d.0), (1, 0));
        assert!(get_logs()
            .iter()
            .any(|l| l.contains("\"event\":\"spend_allowed\"")));
    }

    #[test]
    fn denied_over_per_tx_cap() {
        let (mut c, _o, agent, target) = setup();
        set_ctx(&agent, T0);
        assert!(!c.spend(target, U128(3 * NEAR), None));
        assert!(denied_with("over_per_tx_cap"));
        assert_eq!(c.get_stats().1 .0, 1);
    }

    #[test]
    fn denied_zero_amount() {
        let (mut c, _o, agent, target) = setup();
        set_ctx(&agent, T0);
        assert!(!c.spend(target, U128(0), None));
        assert!(denied_with("zero_amount"));
    }

    #[test]
    fn denied_not_allowlisted() {
        let (mut c, _o, agent, _t) = setup();
        set_ctx(&agent, T0);
        assert!(!c.spend(accounts(3), U128(NEAR), None));
        assert!(denied_with("not_allowlisted"));
    }

    #[test]
    fn denied_lookalike_address() {
        let (mut c, _o, agent, _t) = setup();
        set_ctx(&agent, T0);
        let lookalike: AccountId = "charlie.near.x".parse().unwrap();
        assert!(!c.spend(lookalike, U128(NEAR), None));
        assert!(denied_with("not_allowlisted"));
    }

    #[test]
    fn denied_insufficient_funds() {
        let (mut c, owner, agent, target) = setup();
        owner_ctx(&owner, T0);
        c.set_policy(
            agent.clone(),
            U128(1_000 * NEAR),
            U128(2_000 * NEAR),
            vec![target.clone()],
        );
        set_ctx(&agent, T0);
        assert!(!c.spend(target, U128(1_000 * NEAR), None));
        assert!(denied_with("insufficient_funds"));
    }

    #[test]
    fn denied_memo_too_long_and_not_logged() {
        let (mut c, _o, agent, target) = setup();
        set_ctx(&agent, T0);
        assert!(!c.spend(target, U128(NEAR), Some("x".repeat(MAX_MEMO_BYTES + 1))));
        assert!(denied_with("memo_too_long"));
        assert!(!get_logs().iter().any(|l| l.contains("xxxxxxxxxx")));
        assert_eq!(c.get_stats().1 .0, 1);
    }

    #[test]
    fn caller_without_policy_reverts() {
        let (mut c, _o, _a, target) = setup();
        set_ctx(&accounts(4), T0);
        let res = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            c.spend(target, U128(NEAR), None)
        }));
        assert!(res.is_err());
        // counters untouched
        let (a, d) = c.get_stats();
        assert_eq!((a.0, d.0), (0, 0));
    }

    #[test]
    fn removed_agent_cannot_spend() {
        let (mut c, owner, agent, target) = setup();
        owner_ctx(&owner, T0);
        c.remove_agent(agent.clone());
        set_ctx(&agent, T0);
        let res = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            c.spend(target, U128(NEAR), None)
        }));
        assert!(res.is_err());
    }

    #[test]
    fn daily_cap_enforced_across_calls() {
        let (mut c, _o, agent, target) = setup();
        set_ctx(&agent, T0);
        assert!(c.spend(target.clone(), U128(2 * NEAR), None));
        assert!(c.spend(target.clone(), U128(2 * NEAR), None));
        assert!(!c.spend(target.clone(), U128(2 * NEAR), None)); // 6 > 5
        assert!(denied_with("over_daily_cap"));
        assert!(c.spend(target, U128(NEAR), None)); // exactly 5
        let (a, d) = c.get_stats();
        assert_eq!((a.0, d.0), (3, 1));
    }

    #[test]
    fn window_boundary_is_exact() {
        let (mut c, _o, agent, target) = setup();
        set_ctx(&agent, T0);
        assert!(c.spend(target.clone(), U128(2 * NEAR), None));
        assert!(c.spend(target.clone(), U128(2 * NEAR), None));
        assert!(c.spend(target.clone(), U128(NEAR), None)); // 5 used
        set_ctx(&agent, T0 + DAY_NS - 1);
        assert!(!c.spend(target.clone(), U128(NEAR), None)); // still same window
        set_ctx(&agent, T0 + DAY_NS);
        assert!(c.spend(target, U128(NEAR), None)); // new window
    }

    #[test]
    fn caps_at_u128_max_do_not_overflow() {
        let (mut c, owner, agent, target) = setup();
        owner_ctx(&owner, T0);
        c.set_policy(
            agent.clone(),
            U128(u128::MAX),
            U128(u128::MAX),
            vec![target.clone()],
        );
        set_ctx(&agent, T0);
        assert!(c.spend(target.clone(), U128(NEAR), None));
        assert!(!c.spend(target, U128(u128::MAX), None));
        assert!(denied_with("over_daily_cap"));
    }

    // ----- pause and kill switch -----

    #[test]
    fn kill_switch_blocks_everything() {
        let (mut c, owner, agent, target) = setup();
        set_ctx(&owner, T0);
        c.pause_all();
        assert!(get_logs()
            .iter()
            .any(|l| l.contains("\"event\":\"paused_all\"")));
        set_ctx(&agent, T0);
        assert!(!c.spend(target.clone(), U128(NEAR), None));
        assert!(denied_with("all_paused"));
        owner_ctx(&owner, T0);
        c.resume_all();
        set_ctx(&agent, T0);
        assert!(c.spend(target, U128(NEAR), None));
    }

    #[test]
    fn agent_pause_isolated_and_resumable() {
        let (mut c, owner, agent, target) = setup();
        let other = accounts(3);
        owner_ctx(&owner, T0);
        c.set_policy(
            other.clone(),
            U128(2 * NEAR),
            U128(5 * NEAR),
            vec![target.clone()],
        );
        set_ctx(&owner, T0);
        c.pause_agent(agent.clone());
        assert!(get_logs()
            .iter()
            .any(|l| l.contains("\"event\":\"agent_paused\"")));
        set_ctx(&agent, T0);
        assert!(!c.spend(target.clone(), U128(NEAR), None));
        assert!(denied_with("agent_paused"));
        set_ctx(&other, T0);
        assert!(c.spend(target.clone(), U128(NEAR), None)); // other agent unaffected
        owner_ctx(&owner, T0);
        c.resume_agent(agent.clone());
        set_ctx(&agent, T0);
        assert!(c.spend(target, U128(NEAR), None));
    }

    // ----- policy management -----

    #[test]
    fn set_policy_emits_event_and_keeps_counter() {
        let (mut c, owner, agent, target) = setup();
        assert!(get_logs()
            .iter()
            .any(|l| l.contains("\"event\":\"policy_set\"")));
        set_ctx(&agent, T0);
        assert!(c.spend(target.clone(), U128(2 * NEAR), None));
        owner_ctx(&owner, T0);
        c.set_policy(agent.clone(), U128(2 * NEAR), U128(5 * NEAR), vec![target]);
        let p = c.get_policy(agent).unwrap();
        assert_eq!(p.spent_in_window.0, 2 * NEAR);
    }

    #[test]
    fn get_policy_shows_effective_spent() {
        let (mut c, _o, agent, target) = setup();
        set_ctx(&agent, T0);
        assert!(c.spend(target, U128(2 * NEAR), None));
        set_ctx(&agent, T0 + 1);
        assert_eq!(
            c.get_policy(agent.clone()).unwrap().spent_in_window.0,
            2 * NEAR
        );
        set_ctx(&agent, T0 + DAY_NS);
        assert_eq!(c.get_policy(agent).unwrap().spent_in_window.0, 0);
    }

    #[test]
    #[should_panic(expected = "per_tx_cap must be <= daily_cap")]
    fn set_policy_rejects_per_tx_above_daily() {
        let (mut c, owner, agent, target) = setup();
        owner_ctx(&owner, T0);
        c.set_policy(agent, U128(6 * NEAR), U128(5 * NEAR), vec![target]);
    }

    #[test]
    #[should_panic(expected = "allowlist too long")]
    fn set_policy_rejects_long_allowlist() {
        let (mut c, owner, agent, _t) = setup();
        owner_ctx(&owner, T0);
        let list: Vec<AccountId> = (0..=MAX_ALLOWLIST)
            .map(|i| format!("t{i}.near").parse().unwrap())
            .collect();
        c.set_policy(agent, U128(NEAR), U128(NEAR), list);
    }

    #[test]
    #[should_panic(expected = "owner cannot be an agent")]
    fn set_policy_rejects_owner_as_agent() {
        let (mut c, owner, _a, target) = setup();
        owner_ctx(&owner, T0);
        c.set_policy(owner.clone(), U128(NEAR), U128(NEAR), vec![target]);
    }

    // ----- access control -----

    #[test]
    #[should_panic(expected = "owner only")]
    fn non_owner_cannot_set_policy() {
        let (mut c, _o, agent, target) = setup();
        owner_ctx(&agent, T0); // attacker even attaches 1 yocto
        c.set_policy(agent.clone(), U128(NEAR), U128(NEAR), vec![target]);
    }

    #[test]
    #[should_panic(expected = "owner only")]
    fn non_owner_cannot_withdraw() {
        let (mut c, _o, agent, _t) = setup();
        owner_ctx(&agent, T0);
        let _ = c.withdraw(U128(1));
    }

    #[test]
    #[should_panic(expected = "owner only")]
    fn non_owner_cannot_pause_all() {
        let (mut c, _o, agent, _t) = setup();
        set_ctx(&agent, T0);
        c.pause_all();
    }

    #[test]
    #[should_panic(expected = "owner only")]
    fn non_owner_cannot_pause_agent() {
        let (mut c, _o, agent, _t) = setup();
        set_ctx(&agent, T0);
        c.pause_agent(agent.clone());
    }

    #[test]
    #[should_panic(expected = "owner only")]
    fn non_owner_cannot_remove_agent() {
        let (mut c, _o, agent, _t) = setup();
        owner_ctx(&agent, T0);
        c.remove_agent(agent.clone());
    }

    #[test]
    #[should_panic(expected = "owner only")]
    fn signer_is_not_used_for_auth() {
        let (mut c, owner, agent, _t) = setup();
        let mut b = ctx(&agent, T0, 1);
        b.signer_account_id(owner);
        testing_env!(b.build());
        c.pause_all();
    }

    #[test]
    #[should_panic(expected = "Requires attached deposit")]
    fn owner_methods_need_one_yocto() {
        let (mut c, owner, agent, target) = setup();
        set_ctx(&owner, T0); // no deposit, like a function-call key
        c.set_policy(agent, U128(NEAR), U128(NEAR), vec![target]);
    }

    #[test]
    #[should_panic(expected = "init must be called by the contract account")]
    fn init_rejects_other_callers() {
        set_ctx(&accounts(3), T0);
        let _ = Leash::new(accounts(0));
    }

    #[test]
    #[should_panic(expected = "owner must differ from the vault account")]
    fn init_rejects_vault_as_owner() {
        set_ctx(&vault(), T0);
        let _ = Leash::new(vault());
    }

    // ----- withdraw -----

    #[test]
    #[should_panic(expected = "invalid amount")]
    fn withdraw_rejects_zero() {
        let (mut c, owner, _a, _t) = setup();
        owner_ctx(&owner, T0);
        let _ = c.withdraw(U128(0));
    }

    #[test]
    #[should_panic(expected = "invalid amount")]
    fn withdraw_rejects_more_than_available() {
        let (mut c, owner, _a, _t) = setup();
        owner_ctx(&owner, T0);
        let _ = c.withdraw(U128(1_000 * NEAR));
    }

    // ----- one-yocto rule on every fund-moving owner method -----

    #[test]
    #[should_panic(expected = "Requires attached deposit")]
    fn withdraw_needs_one_yocto() {
        let (mut c, owner, _a, _t) = setup();
        set_ctx(&owner, T0);
        let _ = c.withdraw(U128(1));
    }

    #[test]
    #[should_panic(expected = "Requires attached deposit")]
    fn remove_agent_needs_one_yocto() {
        let (mut c, owner, agent, _t) = setup();
        set_ctx(&owner, T0);
        c.remove_agent(agent);
    }

    #[test]
    #[should_panic(expected = "Requires attached deposit")]
    fn resume_agent_needs_one_yocto() {
        let (mut c, owner, agent, _t) = setup();
        set_ctx(&owner, T0);
        c.resume_agent(agent);
    }

    #[test]
    #[should_panic(expected = "Requires attached deposit")]
    fn resume_all_needs_one_yocto() {
        let (mut c, owner, _a, _t) = setup();
        set_ctx(&owner, T0);
        c.resume_all();
    }

    #[test]
    #[should_panic(expected = "Requires attached deposit")]
    fn two_yocto_is_rejected() {
        let (mut c, owner, agent, target) = setup();
        testing_env!(ctx(&owner, T0, 2).build());
        c.set_policy(agent, U128(NEAR), U128(NEAR), vec![target]);
    }

    #[test]
    fn pause_methods_work_without_deposit() {
        let (mut c, owner, agent, _t) = setup();
        set_ctx(&owner, T0);
        c.pause_agent(agent.clone());
        c.pause_all();
        assert!(c.is_paused_all());
        assert!(c.get_policy(agent).unwrap().paused);
    }

    // ----- money in and out -----

    #[test]
    #[should_panic(expected = "attach NEAR to fund")]
    fn fund_rejects_zero_deposit() {
        let (mut c, _o, _a, _t) = setup();
        set_ctx(&accounts(3), T0);
        c.fund();
    }

    #[test]
    fn fund_emits_event() {
        let (mut c, _o, _a, _t) = setup();
        testing_env!(ctx(&accounts(3), T0, 5 * NEAR).build());
        c.fund();
        assert!(get_logs().iter().any(|l| {
            l.contains("\"event\":\"funded\"") && l.contains("5000000000000000000000000")
        }));
    }

    #[test]
    fn withdraw_up_to_available_succeeds_and_emits_event() {
        let (mut c, owner, _a, _t) = setup();
        owner_ctx(&owner, T0);
        let available = c.get_available_balance().0;
        assert!(available > NEAR);
        assert!(available <= 100 * NEAR - RESERVE_BUFFER);
        let _ = c.withdraw(U128(available));
        assert!(get_logs()
            .iter()
            .any(|l| l.contains("\"event\":\"withdrawn\"")));
    }

    #[test]
    #[should_panic(expected = "invalid amount")]
    fn withdraw_rejects_one_above_available() {
        let (mut c, owner, _a, _t) = setup();
        owner_ctx(&owner, T0);
        let available = c.get_available_balance().0;
        let _ = c.withdraw(U128(available + 1));
    }
}
