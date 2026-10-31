use near_sdk::json_types::{U128, U64};
use near_sdk::serde_json::{json, Value};
use near_sdk::store::LookupMap;
use near_sdk::{
    env, near, require, AccountId, BorshStorageKey, NearToken, PanicOnDefault, Promise,
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
        json!({"standard": "leash", "version": "0.1.0", "event": event, "data": [data]})
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
    #[init]
    pub fn new(owner: AccountId) -> Self {
        Self {
            owner,
            paused_all: false,
            agents: LookupMap::new(StorageKey::Agents),
            allowed_total: 0,
            denied_total: 0,
        }
    }

    // ---------- owner: money in/out ----------

    #[payable]
    pub fn fund(&mut self) {
        let deposit = env::attached_deposit().as_yoctonear();
        require!(deposit > 0, "attach NEAR to fund");
        emit(
            "funded",
            json!({"from": env::predecessor_account_id(), "amount": deposit.to_string()}),
        );
    }

    pub fn withdraw(&mut self, amount: U128) -> Promise {
        self.assert_owner();
        require!(
            amount.0 > 0 && amount.0 <= self.available_balance_yocto(),
            "invalid amount"
        );
        emit("withdrawn", json!({"amount": amount.0.to_string()}));
        Promise::new(self.owner.clone()).transfer(NearToken::from_yoctonear(amount.0))
    }

    // ---------- owner: policy ----------

    pub fn set_policy(
        &mut self,
        agent: AccountId,
        per_tx_cap: U128,
        daily_cap: U128,
        allowlist: Vec<AccountId>,
    ) {
        self.assert_owner();
        require!(allowlist.len() <= MAX_ALLOWLIST, "allowlist too long");
        require!(per_tx_cap.0 <= daily_cap.0, "per_tx_cap must be <= daily_cap");
        require!(agent != self.owner, "owner cannot be an agent");
        // Keep the running window so editing a policy never resets the daily counter.
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
            json!({"agent": agent, "per_tx_cap": per_tx_cap.0.to_string(), "daily_cap": daily_cap.0.to_string()}),
        );
    }

    pub fn remove_agent(&mut self, agent: AccountId) {
        self.assert_owner();
        self.agents.remove(&agent);
        emit("agent_removed", json!({"agent": agent}));
    }

    pub fn pause_agent(&mut self, agent: AccountId) {
        self.assert_owner();
        let p = self
            .agents
            .get_mut(&agent)
            .unwrap_or_else(|| env::panic_str("unknown agent"));
        p.paused = true;
        emit("agent_paused", json!({"agent": agent}));
    }

    pub fn resume_agent(&mut self, agent: AccountId) {
        self.assert_owner();
        let p = self
            .agents
            .get_mut(&agent)
            .unwrap_or_else(|| env::panic_str("unknown agent"));
        p.paused = false;
        emit("agent_resumed", json!({"agent": agent}));
    }

    /// Global kill switch: nothing can be spent until resume_all.
    pub fn pause_all(&mut self) {
        self.assert_owner();
        self.paused_all = true;
        emit("paused_all", json!({}));
    }

    pub fn resume_all(&mut self) {
        self.assert_owner();
        self.paused_all = false;
        emit("resumed_all", json!({}));
    }

    // ---------- agent: the only way money leaves ----------

    /// Returns true if the transfer was scheduled, false if the policy denied it.
    /// Denials do not panic, so the denial is recorded and counted on-chain.
    pub fn spend(&mut self, to: AccountId, amount: U128, memo: Option<String>) -> bool {
        let agent = env::predecessor_account_id();
        let amount = amount.0;
        let now = env::block_timestamp();
        let memo = memo.unwrap_or_default();
        require!(memo.len() <= MAX_MEMO_BYTES, "memo too long");

        let available = self.available_balance_yocto();
        let reason: Option<&'static str> = if self.paused_all {
            Some("all_paused")
        } else {
            match self.agents.get_mut(&agent) {
                None => Some("no_policy"),
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
                    json!({"agent": agent, "to": to, "amount": amount.to_string(), "reason": r, "memo": memo}),
                );
                false
            }
            None => {
                self.allowed_total += 1;
                emit(
                    "spend_allowed",
                    json!({"agent": agent, "to": to, "amount": amount.to_string(), "memo": memo}),
                );
                Promise::new(to).transfer(NearToken::from_yoctonear(amount)).detach();
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

    pub fn get_stats(&self) -> (U64, U64) {
        (U64(self.allowed_total), U64(self.denied_total))
    }

    pub fn get_available_balance(&self) -> U128 {
        U128(self.available_balance_yocto())
    }

    pub fn get_policy(&self, agent: AccountId) -> Option<PolicyView> {
        self.agents.get(&agent).map(|p| PolicyView {
            per_tx_cap: U128(p.per_tx_cap),
            daily_cap: U128(p.daily_cap),
            spent_in_window: U128(p.spent_in_window),
            window_start_ns: U64(p.window_start_ns),
            allowlist: p.allowlist.clone(),
            paused: p.paused,
        })
    }

    // ---------- internal ----------

    fn assert_owner(&self) {
        require!(env::predecessor_account_id() == self.owner, "owner only");
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
    use near_sdk::test_utils::{accounts, VMContextBuilder};
    use near_sdk::testing_env;

    const NEAR: u128 = 10u128.pow(24);
    const T0: u64 = 1_000 * DAY_NS;

    fn set_ctx(caller: &AccountId, ts: u64) {
        let mut b = VMContextBuilder::new();
        b.predecessor_account_id(caller.clone())
            .block_timestamp(ts)
            .account_balance(NearToken::from_near(100));
        testing_env!(b.build());
    }

    /// owner = accounts(0), agent = accounts(1), target = accounts(2)
    fn setup() -> (Leash, AccountId, AccountId, AccountId) {
        let owner = accounts(0);
        let agent = accounts(1);
        let target = accounts(2);
        set_ctx(&owner, T0);
        let mut c = Leash::new(owner.clone());
        c.set_policy(agent.clone(), U128(2 * NEAR), U128(5 * NEAR), vec![target.clone()]);
        (c, owner, agent, target)
    }

    #[test]
    fn allowed_within_limits() {
        let (mut c, _o, agent, target) = setup();
        set_ctx(&agent, T0);
        assert!(c.spend(target, U128(NEAR), Some("ok".to_string())));
        let (a, d) = c.get_stats();
        assert_eq!((a.0, d.0), (1, 0));
    }

    #[test]
    fn denied_over_per_tx_cap() {
        let (mut c, _o, agent, target) = setup();
        set_ctx(&agent, T0);
        assert!(!c.spend(target, U128(3 * NEAR), None));
        assert_eq!(c.get_stats().1 .0, 1);
    }

    #[test]
    fn daily_cap_enforced_across_calls() {
        let (mut c, _o, agent, target) = setup();
        set_ctx(&agent, T0);
        assert!(c.spend(target.clone(), U128(2 * NEAR), None));
        assert!(c.spend(target.clone(), U128(2 * NEAR), None));
        assert!(!c.spend(target.clone(), U128(2 * NEAR), None)); // 6 > 5
        assert!(c.spend(target, U128(NEAR), None)); // exactly 5
        let (a, d) = c.get_stats();
        assert_eq!((a.0, d.0), (3, 1));
    }

    #[test]
    fn denied_not_allowlisted() {
        let (mut c, _o, agent, _t) = setup();
        set_ctx(&agent, T0);
        assert!(!c.spend(accounts(3), U128(NEAR), None));
    }

    #[test]
    fn denied_without_policy() {
        let (mut c, _o, _a, target) = setup();
        set_ctx(&accounts(4), T0);
        assert!(!c.spend(target, U128(NEAR), None));
    }

    #[test]
    fn kill_switch_blocks_everything() {
        let (mut c, owner, agent, target) = setup();
        set_ctx(&owner, T0);
        c.pause_all();
        set_ctx(&agent, T0);
        assert!(!c.spend(target.clone(), U128(NEAR), None));
        set_ctx(&owner, T0);
        c.resume_all();
        set_ctx(&agent, T0);
        assert!(c.spend(target, U128(NEAR), None));
    }

    #[test]
    fn agent_pause_blocks_only_that_agent() {
        let (mut c, owner, agent, target) = setup();
        set_ctx(&owner, T0);
        c.pause_agent(agent.clone());
        set_ctx(&agent, T0);
        assert!(!c.spend(target, U128(NEAR), None));
    }

    #[test]
    fn window_resets_after_24h() {
        let (mut c, _o, agent, target) = setup();
        set_ctx(&agent, T0);
        assert!(c.spend(target.clone(), U128(2 * NEAR), None));
        assert!(c.spend(target.clone(), U128(2 * NEAR), None));
        assert!(c.spend(target.clone(), U128(NEAR), None));
        assert!(!c.spend(target.clone(), U128(NEAR), None)); // cap reached
        set_ctx(&agent, T0 + DAY_NS + 1);
        assert!(c.spend(target, U128(2 * NEAR), None)); // new day
    }

    #[test]
    #[should_panic(expected = "owner only")]
    fn non_owner_cannot_set_policy() {
        let (mut c, _o, agent, target) = setup();
        set_ctx(&agent, T0);
        c.set_policy(agent.clone(), U128(NEAR), U128(NEAR), vec![target]);
    }
}
