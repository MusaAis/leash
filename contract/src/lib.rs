//! LEASH vault: on-chain spending policy for AI agents.
//!
//! Funds are held by this contract. Agents can only request payments through
//! `spend`, and the owner's policy is enforced on-chain.

use near_sdk::json_types::{U128, U64};
use near_sdk::serde_json::{json, Value};
use near_sdk::store::LookupMap;
use near_sdk::{
    assert_one_yocto, env, near, require, AccountId, BorshStorageKey, Gas, NearToken,
    PanicOnDefault, Promise,
};

const DAY_NS: u64 = 86_400_000_000_000;
const MAX_ALLOWLIST: usize = 20;
const MAX_MEMO_BYTES: usize = 128;
const MAX_ROUTES: usize = 10;
const MAX_PENDING_APPROVALS: usize = 5;
const MAX_ROUTE_FIELD_BYTES: usize = 128;
/// An approval may be valid for at most 30 minutes.
const MAX_APPROVAL_NS: u64 = 30 * 60 * 1_000_000_000;
/// 0.1 NEAR kept back so the contract never drains below its storage cost.
const RESERVE_BUFFER: u128 = 10u128.pow(23);
/// wNEAR storage registration (min = max), read from `storage_balance_bounds` once.
/// If the token changes its bounds the swap batch fails closed. 0.00125 NEAR.
const STORAGE_DEPOSIT: u128 = 1_250_000_000_000_000_000_000;
/// Two storage deposits per swap (the vault and the deposit account). Counted in the
/// funds check, the daily check and the daily counter, so the daily cap bounds them.
const INTENT_OVERHEAD: u128 = 2 * STORAGE_DEPOSIT;
/// Gas numbers below are placeholders, to be measured in the rehearsal.
/// `spend_intent` panics early when less than this is attached (to be measured).
const MIN_INTENT_GAS_TGAS: u64 = 120;
/// Gas given to each of the four batch actions (to be measured).
const ACTION_GAS_TGAS: u64 = 10;
/// `unwrap` panics early when less than this is attached (to be measured).
const MIN_UNWRAP_GAS_TGAS: u64 = 30;
/// Gas given to `near_withdraw` (to be measured).
const UNWRAP_ACTION_GAS_TGAS: u64 = 20;

#[near(serializers = [borsh])]
#[derive(BorshStorageKey)]
enum StorageKey {
    Agents,
    Routes,
    Approvals,
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

/// A destination the owner has approved for an agent. Ids are indexes.
#[near(serializers = [borsh, json])]
#[derive(Clone)]
pub struct Route {
    pub dest_asset: String,
    pub recipient: String,
}

/// A single-use permission from the guardian for one swap deposit.
#[near(serializers = [borsh])]
#[derive(Clone)]
pub struct Approval {
    pub route_id: u8,
    pub deposit_account: AccountId,
    pub amount: u128,
    pub expires_ns: u64,
    /// The value of `intents_epoch` when the guardian approved. A later
    /// `set_intents_config` bumps the epoch, so this approval stops matching.
    pub epoch: u32,
    /// sha256 of the saved 1Click answer. A guardian assertion, not enforced here.
    pub quote_hash: String,
}

#[near(serializers = [json])]
pub struct ApprovalView {
    pub route_id: u8,
    pub deposit_account: AccountId,
    pub amount: U128,
    pub expires_ns: U64,
    pub quote_hash: String,
}

fn valid_route_field(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= MAX_ROUTE_FIELD_BYTES
        && value.chars().all(|c| ('!'..='~').contains(&c))
}

fn valid_quote_hash(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// All inputs of the `spend_intent` checks. Plain values, no environment reads,
/// so a view can run the same function later.
struct IntentCheck<'a> {
    configured: bool,
    all_paused: bool,
    agent_paused: bool,
    per_tx_cap: u128,
    daily_cap: u128,
    window_start_ns: u64,
    spent_in_window: u128,
    route_count: usize,
    approvals: &'a [Approval],
    epoch: u32,
    now: u64,
    /// Balance available to the vault (already net of storage and the reserve buffer).
    available: u128,
}

/// Runs the checks in the decided order. `Ok` carries the index of the approval
/// to consume (first match in list order); `Err` carries the denial reason.
fn check_intent(
    s: &IntentCheck,
    route_id: u8,
    deposit_account: &AccountId,
    amount: u128,
) -> Result<usize, &'static str> {
    if !s.configured {
        return Err("intents_not_configured");
    }
    if s.all_paused {
        return Err("all_paused");
    }
    if s.agent_paused {
        return Err("agent_paused");
    }
    if amount == 0 {
        return Err("zero_amount");
    }
    if (route_id as usize) >= s.route_count {
        return Err("unknown_route");
    }
    if amount > s.per_tx_cap {
        return Err("over_per_tx_cap");
    }
    let index = s
        .approvals
        .iter()
        .position(|a| {
            a.route_id == route_id
                && &a.deposit_account == deposit_account
                && a.amount == amount
                && a.expires_ns > s.now
                && a.epoch == s.epoch
        })
        .ok_or("no_approval")?;
    let spent = if s.now.saturating_sub(s.window_start_ns) >= DAY_NS {
        0
    } else {
        s.spent_in_window
    };
    match spent
        .checked_add(amount)
        .and_then(|t| t.checked_add(INTENT_OVERHEAD))
    {
        Some(total) if total <= s.daily_cap => {}
        _ => return Err("over_daily_cap"),
    }
    match amount.checked_add(INTENT_OVERHEAD) {
        Some(need) if need <= s.available => Ok(index),
        _ => Err("insufficient_funds"),
    }
}

/// All inputs of the `spend` checks. Plain values, no environment reads, so a
/// view runs the same function as the real method.
struct SpendCheck<'a> {
    all_paused: bool,
    memo_ok: bool,
    agent_paused: bool,
    per_tx_cap: u128,
    daily_cap: u128,
    window_start_ns: u64,
    spent_in_window: u128,
    allowlist: &'a [AccountId],
    now: u64,
    /// Balance available to the vault (net of storage and the reserve buffer).
    available: u128,
}

/// Runs the `spend` checks in the decided order. `Err` carries the denial reason.
fn check_spend(s: &SpendCheck, to: &AccountId, amount: u128) -> Result<(), &'static str> {
    if s.all_paused {
        return Err("all_paused");
    }
    if !s.memo_ok {
        return Err("memo_too_long");
    }
    if s.agent_paused {
        return Err("agent_paused");
    }
    if amount == 0 {
        return Err("zero_amount");
    }
    if amount > s.per_tx_cap {
        return Err("over_per_tx_cap");
    }
    if !s.allowlist.contains(to) {
        return Err("not_allowlisted");
    }
    let spent = if s.now.saturating_sub(s.window_start_ns) >= DAY_NS {
        0
    } else {
        s.spent_in_window
    };
    match spent.checked_add(amount) {
        Some(total) if total <= s.daily_cap => {}
        _ => return Err("over_daily_cap"),
    }
    if amount > s.available {
        return Err("insufficient_funds");
    }
    Ok(())
}

/// True when `spend` got as far as the window and cap stage, which is where the
/// expired window is restarted (also for a call denied at that stage).
fn reached_window_stage(verdict: &Result<(), &'static str>) -> bool {
    matches!(
        verdict,
        Ok(()) | Err("over_daily_cap") | Err("insufficient_funds")
    )
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
    guardian: Option<AccountId>,
    wnear: Option<AccountId>,
    intents_epoch: u32,
    routes: LookupMap<AccountId, Vec<Route>>,
    approvals: LookupMap<AccountId, Vec<Approval>>,
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
            guardian: None,
            wnear: None,
            intents_epoch: 0,
            routes: LookupMap::new(StorageKey::Routes),
            approvals: LookupMap::new(StorageKey::Approvals),
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
        require!(
            self.guardian.as_ref() != Some(&agent),
            "guardian cannot be an agent"
        );
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
        self.routes.remove(&agent);
        self.approvals.remove(&agent);
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

    // ---------- owner: intents configuration ----------

    /// Owner only. Requires 1 yoctoNEAR. Sets the wNEAR token account and the
    /// guardian. The guardian must differ from the owner, the vault and any agent.
    #[payable]
    pub fn set_intents_config(&mut self, wnear: AccountId, guardian: AccountId) {
        self.assert_owner_strict();
        require!(
            guardian != self.owner && guardian != env::current_account_id(),
            "guardian must differ from the owner and the vault"
        );
        require!(
            !self.agents.contains_key(&guardian),
            "guardian cannot be an agent"
        );
        self.wnear = Some(wnear.clone());
        self.guardian = Some(guardian.clone());
        // Every call revokes all pending approvals, also with unchanged values.
        self.intents_epoch = self.intents_epoch.saturating_add(1);
        emit(
            "intents_configured",
            json!({"wnear": wnear, "guardian": guardian, "epoch": self.intents_epoch}),
        );
    }

    /// Owner only. Requires 1 yoctoNEAR. Replaces the agent's routes and
    /// clears its pending approvals.
    #[payable]
    pub fn set_routes(&mut self, agent: AccountId, routes: Vec<Route>) {
        self.assert_owner_strict();
        require!(self.agents.contains_key(&agent), "unknown agent");
        require!(routes.len() <= MAX_ROUTES, "too many routes");
        require!(
            routes
                .iter()
                .all(|r| valid_route_field(&r.dest_asset) && valid_route_field(&r.recipient)),
            "invalid route"
        );
        let count = routes.len();
        self.routes.insert(agent.clone(), routes);
        self.approvals.remove(&agent);
        emit("routes_set", json!({"agent": agent, "count": count}));
    }

    // ---------- guardian ----------

    /// Guardian only. Records a single-use approval for one swap deposit.
    /// Every rejection panics: the guardian is trusted infrastructure and a
    /// panic leaves no trace and no counters.
    pub fn approve_intent(
        &mut self,
        agent: AccountId,
        route_id: u8,
        deposit_account: AccountId,
        amount: U128,
        expires_ns: U64,
        quote_hash: String,
    ) {
        let guardian = self
            .guardian
            .clone()
            .unwrap_or_else(|| env::panic_str("intents not configured"));
        require!(env::predecessor_account_id() == guardian, "guardian only");
        let per_tx_cap = match self.agents.get(&agent) {
            Some(p) => p.per_tx_cap,
            None => env::panic_str("unknown agent"),
        };
        let route_count = self.routes.get(&agent).map_or(0, |r| r.len());
        require!((route_id as usize) < route_count, "unknown route");
        require!(
            amount.0 > 0 && amount.0 <= per_tx_cap,
            "amount must be above 0 and within per_tx_cap"
        );
        let now = env::block_timestamp();
        require!(expires_ns.0 > now, "expires_ns is in the past");
        require!(
            expires_ns.0 <= now.saturating_add(MAX_APPROVAL_NS),
            "expires_ns is more than 30 minutes ahead"
        );
        require!(
            deposit_account != env::current_account_id()
                && deposit_account != self.owner
                && deposit_account != guardian
                && deposit_account != agent
                && self.wnear.as_ref() != Some(&deposit_account)
                && !self.agents.contains_key(&deposit_account),
            "invalid deposit account"
        );
        require!(
            valid_quote_hash(&quote_hash),
            "quote_hash must be 64 lowercase hex characters"
        );
        let mut pending = self.approvals.get(&agent).cloned().unwrap_or_default();
        let epoch = self.intents_epoch;
        pending.retain(|a| a.expires_ns > now && a.epoch == epoch);
        require!(
            pending.len() < MAX_PENDING_APPROVALS,
            "too many pending approvals"
        );
        pending.push(Approval {
            route_id,
            deposit_account: deposit_account.clone(),
            amount: amount.0,
            expires_ns: expires_ns.0,
            epoch,
            quote_hash: quote_hash.clone(),
        });
        self.approvals.insert(agent.clone(), pending);
        emit(
            "intent_approved",
            json!({
                "agent": agent,
                "route_id": route_id,
                "deposit_account": deposit_account,
                "amount": amount.0.to_string(),
                "expires_ns": expires_ns.0.to_string(),
                "quote_hash": quote_hash
            }),
        );
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
        let verdict = self.spend_verdict(&agent, &to, amount, memo_ok, now, available);
        if reached_window_stage(&verdict) {
            match self.agents.get_mut(&agent) {
                None => env::panic_str("no policy for caller"),
                Some(p) => {
                    if now.saturating_sub(p.window_start_ns) >= DAY_NS {
                        p.window_start_ns = now;
                        p.spent_in_window = 0;
                    }
                    if verdict.is_ok() {
                        // The check already proved this sum does not overflow.
                        p.spent_in_window = p.spent_in_window.saturating_add(amount);
                    }
                }
            }
        }
        let reason = verdict.err();

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

    /// Agent only. Sends `amount` of NEAR to a swap deposit address through
    /// wNEAR, if the guardian approved exactly this route, deposit account and
    /// amount. Returns `true` if the batch was scheduled, `false` if denied.
    /// The daily cap is counted before sending and is not rolled back.
    /// Panics early (nothing counted) if too little gas is attached.
    pub fn spend_intent(&mut self, route_id: u8, deposit_account: AccountId, amount: U128) -> bool {
        require!(
            env::prepaid_gas() >= Gas::from_tgas(MIN_INTENT_GAS_TGAS),
            "not enough gas attached for the swap batch"
        );
        let agent = env::predecessor_account_id();
        if !self.agents.contains_key(&agent) {
            env::panic_str("no policy for caller");
        }
        let amount = amount.0;
        let now = env::block_timestamp();
        let available = self.available_balance_yocto();
        let approvals = self.approvals.get(&agent).cloned().unwrap_or_default();
        let verdict = self.intent_verdict(
            &agent,
            &approvals,
            route_id,
            &deposit_account,
            amount,
            now,
            available,
        );
        let index = match verdict {
            Err(reason) => {
                self.denied_total += 1;
                emit(
                    "intent_denied",
                    json!({
                        "agent": agent,
                        "route_id": route_id,
                        "deposit_account": deposit_account,
                        "amount": amount.to_string(),
                        "reason": reason
                    }),
                );
                return false;
            }
            Ok(index) => index,
        };
        let wnear = self
            .wnear
            .clone()
            .unwrap_or_else(|| env::panic_str("intents not configured"));
        let mut approvals = approvals;
        let consumed = approvals.remove(index);
        if approvals.is_empty() {
            self.approvals.remove(&agent);
        } else {
            self.approvals.insert(agent.clone(), approvals);
        }
        match self.agents.get_mut(&agent) {
            None => env::panic_str("no policy for caller"),
            Some(p) => {
                if now.saturating_sub(p.window_start_ns) >= DAY_NS {
                    p.window_start_ns = now;
                    p.spent_in_window = 0;
                }
                // The check already proved this sum does not overflow.
                p.spent_in_window = p
                    .spent_in_window
                    .saturating_add(amount.saturating_add(INTENT_OVERHEAD));
            }
        }
        self.allowed_total += 1;
        emit(
            "intent_sent",
            json!({
                "agent": agent,
                "route_id": route_id,
                "deposit_account": deposit_account,
                "amount": amount.to_string(),
                "quote_hash": consumed.quote_hash
            }),
        );
        let action_gas = Gas::from_tgas(ACTION_GAS_TGAS);
        let storage = NearToken::from_yoctonear(STORAGE_DEPOSIT);
        // One receipt to wNEAR: the four actions succeed or fail together.
        Promise::new(wnear)
            .function_call(
                "storage_deposit",
                json!({"account_id": env::current_account_id(), "registration_only": true})
                    .to_string()
                    .into_bytes(),
                storage,
                action_gas,
            )
            .function_call(
                "storage_deposit",
                json!({"account_id": deposit_account, "registration_only": true})
                    .to_string()
                    .into_bytes(),
                storage,
                action_gas,
            )
            .function_call(
                "near_deposit",
                b"{}".to_vec(),
                NearToken::from_yoctonear(amount),
                action_gas,
            )
            .function_call(
                "ft_transfer",
                json!({"receiver_id": deposit_account, "amount": amount.to_string()})
                    .to_string()
                    .into_bytes(),
                NearToken::from_yoctonear(1),
                action_gas,
            )
            .detach();
        true
    }

    /// Owner only. Requires 1 yoctoNEAR. Turns `amount` wNEAR back into NEAR in
    /// this vault. Touches no cap and works while the vault or an agent is
    /// paused. No balance check: wNEAR fails the call if the amount is too high.
    #[payable]
    pub fn unwrap(&mut self, amount: U128) -> Promise {
        self.assert_owner_strict();
        require!(amount.0 > 0, "amount must be above 0");
        let wnear = self
            .wnear
            .clone()
            .unwrap_or_else(|| env::panic_str("intents not configured"));
        require!(
            env::prepaid_gas() >= Gas::from_tgas(MIN_UNWRAP_GAS_TGAS),
            "not enough gas attached for unwrap"
        );
        emit("unwrapped", json!({"amount": amount.0.to_string()}));
        Promise::new(wnear).function_call(
            "near_withdraw",
            json!({"amount": amount.0.to_string()})
                .to_string()
                .into_bytes(),
            NearToken::from_yoctonear(1),
            Gas::from_tgas(UNWRAP_ACTION_GAS_TGAS),
        )
    }

    // ---------- views ----------

    /// What `spend` would answer right now for this agent: `allowed` or the
    /// denial reason. Runs the same check function as `spend`. It cannot see
    /// the caller. `no_policy` is view-only: the real method reverts then.
    pub fn would_spend(
        &self,
        agent: AccountId,
        to: AccountId,
        amount: U128,
        memo: Option<String>,
    ) -> String {
        if !self.agents.contains_key(&agent) {
            return "no_policy".to_string();
        }
        let memo_ok = memo.unwrap_or_default().len() <= MAX_MEMO_BYTES;
        let now = env::block_timestamp();
        let available = self.available_balance_yocto();
        match self.spend_verdict(&agent, &to, amount.0, memo_ok, now, available) {
            Ok(()) => "allowed".to_string(),
            Err(reason) => reason.to_string(),
        }
    }

    /// What `spend_intent` would answer right now for this agent: `allowed` or
    /// the denial reason. Runs the same check function as `spend_intent`. It
    /// cannot see the caller or the attached gas. `no_policy` is view-only.
    pub fn would_spend_intent(
        &self,
        agent: AccountId,
        route_id: u8,
        deposit_account: AccountId,
        amount: U128,
    ) -> String {
        if !self.agents.contains_key(&agent) {
            return "no_policy".to_string();
        }
        let approvals = self.approvals.get(&agent).cloned().unwrap_or_default();
        let now = env::block_timestamp();
        let available = self.available_balance_yocto();
        match self.intent_verdict(
            &agent,
            &approvals,
            route_id,
            &deposit_account,
            amount.0,
            now,
            available,
        ) {
            Ok(_) => "allowed".to_string(),
            Err(reason) => reason.to_string(),
        }
    }

    pub fn get_owner(&self) -> AccountId {
        self.owner.clone()
    }

    pub fn get_guardian(&self) -> Option<AccountId> {
        self.guardian.clone()
    }

    pub fn get_wnear(&self) -> Option<AccountId> {
        self.wnear.clone()
    }

    pub fn get_routes(&self, agent: AccountId) -> Vec<Route> {
        self.routes.get(&agent).cloned().unwrap_or_default()
    }

    /// Returns the approvals that have not expired and belong to the current epoch.
    pub fn get_approvals(&self, agent: AccountId) -> Vec<ApprovalView> {
        let now = env::block_timestamp();
        let epoch = self.intents_epoch;
        self.approvals
            .get(&agent)
            .map(|list| {
                list.iter()
                    .filter(|a| a.expires_ns > now && a.epoch == epoch)
                    .map(|a| ApprovalView {
                        route_id: a.route_id,
                        deposit_account: a.deposit_account.clone(),
                        amount: U128(a.amount),
                        expires_ns: U64(a.expires_ns),
                        quote_hash: a.quote_hash.clone(),
                    })
                    .collect()
            })
            .unwrap_or_default()
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

    /// Runs `check_spend` on the agent's stored policy. Reverts without a policy.
    fn spend_verdict(
        &self,
        agent: &AccountId,
        to: &AccountId,
        amount: u128,
        memo_ok: bool,
        now: u64,
        available: u128,
    ) -> Result<(), &'static str> {
        let p = self
            .agents
            .get(agent)
            .unwrap_or_else(|| env::panic_str("no policy for caller"));
        check_spend(
            &SpendCheck {
                all_paused: self.paused_all,
                memo_ok,
                agent_paused: p.paused,
                per_tx_cap: p.per_tx_cap,
                daily_cap: p.daily_cap,
                window_start_ns: p.window_start_ns,
                spent_in_window: p.spent_in_window,
                allowlist: &p.allowlist,
                now,
                available,
            },
            to,
            amount,
        )
    }

    /// Runs `check_intent` on the agent's stored policy. Reverts without a policy.
    #[allow(clippy::too_many_arguments)]
    fn intent_verdict(
        &self,
        agent: &AccountId,
        approvals: &[Approval],
        route_id: u8,
        deposit_account: &AccountId,
        amount: u128,
        now: u64,
        available: u128,
    ) -> Result<usize, &'static str> {
        let p = self
            .agents
            .get(agent)
            .unwrap_or_else(|| env::panic_str("no policy for caller"));
        check_intent(
            &IntentCheck {
                configured: self.guardian.is_some() && self.wnear.is_some(),
                all_paused: self.paused_all,
                agent_paused: p.paused,
                per_tx_cap: p.per_tx_cap,
                daily_cap: p.daily_cap,
                window_start_ns: p.window_start_ns,
                spent_in_window: p.spent_in_window,
                route_count: self.routes.get(agent).map_or(0, |r| r.len()),
                approvals,
                epoch: self.intents_epoch,
                now,
                available,
            },
            route_id,
            deposit_account,
            amount,
        )
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
    use near_sdk::mock::MockAction;
    use near_sdk::test_utils::{accounts, get_created_receipts, get_logs, VMContextBuilder};
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

    // ----- intents: configuration, routes, approvals (P4b step 1) -----

    const MIN_NS: u64 = 60_000_000_000;

    fn guardian() -> AccountId {
        accounts(3)
    }

    fn wnear() -> AccountId {
        accounts(4)
    }

    fn deposit_acct() -> AccountId {
        accounts(5)
    }

    fn hash() -> String {
        "a".repeat(64)
    }

    fn route() -> Route {
        Route {
            dest_asset: "nep245:base:usdc".to_string(),
            recipient: "0xRecipient".to_string(),
        }
    }

    /// setup() plus intents config (guardian = accounts(3), wnear = accounts(4)) and one route.
    fn intents_setup() -> (Leash, AccountId, AccountId) {
        let (mut c, owner, agent, _t) = setup();
        owner_ctx(&owner, T0);
        c.set_intents_config(wnear(), guardian());
        c.set_routes(agent.clone(), vec![route()]);
        (c, owner, agent)
    }

    fn approve(c: &mut Leash, agent: &AccountId, amount: u128, expires: u64) {
        set_ctx(&guardian(), T0);
        c.approve_intent(
            agent.clone(),
            0,
            deposit_acct(),
            U128(amount),
            U64(expires),
            hash(),
        );
    }

    #[test]
    fn new_vault_has_no_intents_state() {
        let (c, _o, agent, _t) = setup();
        assert!(c.get_guardian().is_none());
        assert!(c.get_wnear().is_none());
        assert!(c.get_routes(agent.clone()).is_empty());
        assert!(c.get_approvals(agent).is_empty());
    }

    #[test]
    fn owner_configures_intents_and_routes() {
        let (c, _o, agent) = intents_setup();
        assert_eq!(c.get_guardian(), Some(guardian()));
        assert_eq!(c.get_wnear(), Some(wnear()));
        assert_eq!(c.get_routes(agent).len(), 1);
        let logs = get_logs();
        assert!(logs.iter().any(|l| l.contains("\"event\":\"routes_set\"")));
    }

    #[test]
    #[should_panic(expected = "owner only")]
    fn guardian_cannot_set_config() {
        let (mut c, _o, _a) = intents_setup();
        testing_env!(ctx(&guardian(), T0, 1).build());
        c.set_intents_config(wnear(), guardian());
    }

    #[test]
    #[should_panic(expected = "Requires attached deposit")]
    fn set_config_needs_one_yocto() {
        let (mut c, owner, _a, _t) = setup();
        set_ctx(&owner, T0);
        c.set_intents_config(wnear(), guardian());
    }

    #[test]
    #[should_panic(expected = "guardian must differ")]
    fn guardian_cannot_be_the_owner() {
        let (mut c, owner, _a, _t) = setup();
        owner_ctx(&owner, T0);
        c.set_intents_config(wnear(), owner.clone());
    }

    #[test]
    #[should_panic(expected = "guardian must differ")]
    fn guardian_cannot_be_the_vault() {
        let (mut c, owner, _a, _t) = setup();
        owner_ctx(&owner, T0);
        c.set_intents_config(wnear(), vault());
    }

    #[test]
    #[should_panic(expected = "guardian cannot be an agent")]
    fn guardian_cannot_be_an_existing_agent() {
        let (mut c, owner, agent, _t) = setup();
        owner_ctx(&owner, T0);
        c.set_intents_config(wnear(), agent);
    }

    #[test]
    #[should_panic(expected = "guardian cannot be an agent")]
    fn a_policy_cannot_be_set_for_the_guardian() {
        let (mut c, owner, _a) = intents_setup();
        owner_ctx(&owner, T0);
        c.set_policy(guardian(), U128(NEAR), U128(NEAR), vec![]);
    }

    #[test]
    #[should_panic(expected = "unknown agent")]
    fn routes_need_a_policy() {
        let (mut c, owner, _a) = intents_setup();
        owner_ctx(&owner, T0);
        c.set_routes(accounts(5), vec![route()]);
    }

    #[test]
    #[should_panic(expected = "too many routes")]
    fn eleven_routes_are_rejected() {
        let (mut c, owner, agent) = intents_setup();
        owner_ctx(&owner, T0);
        c.set_routes(agent, vec![route(); 11]);
    }

    #[test]
    fn ten_routes_are_accepted() {
        let (mut c, owner, agent) = intents_setup();
        owner_ctx(&owner, T0);
        c.set_routes(agent.clone(), vec![route(); 10]);
        assert_eq!(c.get_routes(agent).len(), 10);
    }

    #[test]
    fn bad_route_fields_are_rejected() {
        let long = "x".repeat(129);
        let bad = [
            ("", "r"),
            ("a", ""),
            (long.as_str(), "r"),
            ("a", long.as_str()),
            ("a\nb", "r"),
            ("a", "r\u{0}"),
        ];
        for (dest, rec) in bad {
            let (mut c, owner, agent) = intents_setup();
            owner_ctx(&owner, T0);
            let r = Route {
                dest_asset: dest.to_string(),
                recipient: rec.to_string(),
            };
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                c.set_routes(agent, vec![r]);
            }));
            assert!(result.is_err(), "accepted {dest:?} / {rec:?}");
        }
    }

    #[test]
    fn guardian_approves_and_the_event_carries_the_quote_hash() {
        let (mut c, _o, agent) = intents_setup();
        approve(&mut c, &agent, NEAR, T0 + 10 * MIN_NS);
        let list = c.get_approvals(agent);
        assert_eq!(list.len(), 1);
        assert_eq!(list[0].amount.0, NEAR);
        assert_eq!(list[0].deposit_account, deposit_acct());
        assert_eq!(list[0].quote_hash, hash());
        assert!(get_logs()
            .iter()
            .any(|l| { l.contains("\"event\":\"intent_approved\"") && l.contains(&hash()) }));
        let (a, d) = c.get_stats();
        assert_eq!((a.0, d.0), (0, 0), "approving is not a spend");
    }

    #[test]
    #[should_panic(expected = "guardian only")]
    fn agent_cannot_approve() {
        let (mut c, _o, agent) = intents_setup();
        set_ctx(&agent, T0);
        c.approve_intent(
            agent.clone(),
            0,
            deposit_acct(),
            U128(NEAR),
            U64(T0 + MIN_NS),
            hash(),
        );
    }

    #[test]
    #[should_panic(expected = "guardian only")]
    fn owner_cannot_approve() {
        let (mut c, owner, agent) = intents_setup();
        set_ctx(&owner, T0);
        c.approve_intent(
            agent,
            0,
            deposit_acct(),
            U128(NEAR),
            U64(T0 + MIN_NS),
            hash(),
        );
    }

    #[test]
    #[should_panic(expected = "intents not configured")]
    fn approve_needs_a_configured_vault() {
        let (mut c, _o, agent, _t) = setup();
        set_ctx(&guardian(), T0);
        c.approve_intent(
            agent,
            0,
            deposit_acct(),
            U128(NEAR),
            U64(T0 + MIN_NS),
            hash(),
        );
    }

    #[test]
    #[should_panic(expected = "no policy for caller")]
    fn guardian_cannot_spend() {
        let (mut c, _o, _a) = intents_setup();
        set_ctx(&guardian(), T0);
        c.spend(accounts(2), U128(NEAR), None);
    }

    #[test]
    #[should_panic(expected = "owner only")]
    fn guardian_cannot_withdraw() {
        let (mut c, _o, _a) = intents_setup();
        testing_env!(ctx(&guardian(), T0, 1).build());
        let _ = c.withdraw(U128(NEAR));
    }

    #[test]
    #[should_panic(expected = "unknown agent")]
    fn approve_rejects_an_unknown_agent() {
        let (mut c, _o, _a) = intents_setup();
        set_ctx(&guardian(), T0);
        c.approve_intent(
            accounts(2),
            0,
            deposit_acct(),
            U128(NEAR),
            U64(T0 + MIN_NS),
            hash(),
        );
    }

    #[test]
    #[should_panic(expected = "unknown route")]
    fn approve_rejects_an_unknown_route() {
        let (mut c, _o, agent) = intents_setup();
        set_ctx(&guardian(), T0);
        c.approve_intent(
            agent,
            1,
            deposit_acct(),
            U128(NEAR),
            U64(T0 + MIN_NS),
            hash(),
        );
    }

    #[test]
    #[should_panic(expected = "within per_tx_cap")]
    fn approve_rejects_zero() {
        let (mut c, _o, agent) = intents_setup();
        approve(&mut c, &agent, 0, T0 + MIN_NS);
    }

    #[test]
    #[should_panic(expected = "within per_tx_cap")]
    fn approve_rejects_above_the_per_tx_cap() {
        let (mut c, _o, agent) = intents_setup();
        approve(&mut c, &agent, 2 * NEAR + 1, T0 + MIN_NS);
    }

    #[test]
    #[should_panic(expected = "in the past")]
    fn approve_rejects_an_expiry_in_the_past() {
        let (mut c, _o, agent) = intents_setup();
        approve(&mut c, &agent, NEAR, T0);
    }

    #[test]
    #[should_panic(expected = "more than 30 minutes")]
    fn approve_rejects_an_expiry_too_far_ahead() {
        let (mut c, _o, agent) = intents_setup();
        approve(&mut c, &agent, NEAR, T0 + 30 * MIN_NS + 1);
    }

    #[test]
    fn approve_accepts_exactly_30_minutes() {
        let (mut c, _o, agent) = intents_setup();
        approve(&mut c, &agent, NEAR, T0 + 30 * MIN_NS);
        assert_eq!(c.get_approvals(agent).len(), 1);
    }

    #[test]
    fn approve_rejects_protected_deposit_accounts() {
        let owner = accounts(0);
        for bad in [vault(), owner, guardian()] {
            let (mut c, _o, agent) = intents_setup();
            set_ctx(&guardian(), T0);
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                c.approve_intent(
                    agent.clone(),
                    0,
                    bad.clone(),
                    U128(NEAR),
                    U64(T0 + MIN_NS),
                    hash(),
                );
            }));
            assert!(result.is_err(), "accepted {bad}");
        }
        let (mut c, _o, agent) = intents_setup();
        set_ctx(&guardian(), T0);
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            c.approve_intent(
                agent.clone(),
                0,
                agent.clone(),
                U128(NEAR),
                U64(T0 + MIN_NS),
                hash(),
            );
        }));
        assert!(result.is_err(), "accepted the agent as deposit account");
    }

    #[test]
    fn approve_rejects_bad_quote_hashes() {
        let bad = [
            "a".repeat(63),
            "a".repeat(65),
            "A".repeat(64),
            "g".repeat(64),
            String::new(),
            format!("{} ", "a".repeat(63)),
        ];
        for h in bad {
            let (mut c, _o, agent) = intents_setup();
            set_ctx(&guardian(), T0);
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                c.approve_intent(
                    agent.clone(),
                    0,
                    deposit_acct(),
                    U128(NEAR),
                    U64(T0 + MIN_NS),
                    h.clone(),
                );
            }));
            assert!(result.is_err(), "accepted {h:?}");
        }
    }

    #[test]
    #[should_panic(expected = "too many pending approvals")]
    fn the_sixth_pending_approval_is_rejected() {
        let (mut c, _o, agent) = intents_setup();
        for _ in 0..6 {
            approve(&mut c, &agent, NEAR, T0 + 10 * MIN_NS);
        }
    }

    #[test]
    fn expired_approvals_are_pruned_and_free_a_slot() {
        let (mut c, _o, agent) = intents_setup();
        for _ in 0..5 {
            approve(&mut c, &agent, NEAR, T0 + MIN_NS);
        }
        set_ctx(&guardian(), T0 + 2 * MIN_NS);
        assert!(c.get_approvals(agent.clone()).is_empty());
        c.approve_intent(
            agent.clone(),
            0,
            deposit_acct(),
            U128(NEAR),
            U64(T0 + 10 * MIN_NS),
            hash(),
        );
        assert_eq!(c.get_approvals(agent).len(), 1);
    }

    #[test]
    fn set_routes_clears_pending_approvals() {
        let (mut c, owner, agent) = intents_setup();
        approve(&mut c, &agent, NEAR, T0 + 10 * MIN_NS);
        owner_ctx(&owner, T0);
        c.set_routes(agent.clone(), vec![route()]);
        assert!(c.get_approvals(agent).is_empty());
    }

    #[test]
    fn remove_agent_clears_routes_and_approvals() {
        let (mut c, owner, agent) = intents_setup();
        approve(&mut c, &agent, NEAR, T0 + 10 * MIN_NS);
        owner_ctx(&owner, T0);
        c.remove_agent(agent.clone());
        assert!(c.get_routes(agent.clone()).is_empty());
        assert!(c.get_approvals(agent).is_empty());
    }

    // ----- intents: epoch, route characters, deposit account rules -----

    #[test]
    fn rotating_the_guardian_invalidates_old_approvals() {
        let (mut c, owner, agent) = intents_setup();
        approve(&mut c, &agent, NEAR, T0 + 10 * MIN_NS);
        assert_eq!(c.get_approvals(agent.clone()).len(), 1);
        owner_ctx(&owner, T0);
        c.set_intents_config(wnear(), accounts(5));
        assert!(c.get_approvals(agent).is_empty());
    }

    #[test]
    fn calling_config_with_the_same_values_also_revokes_approvals() {
        let (mut c, owner, agent) = intents_setup();
        approve(&mut c, &agent, NEAR, T0 + 10 * MIN_NS);
        owner_ctx(&owner, T0);
        c.set_intents_config(wnear(), guardian());
        assert!(c.get_approvals(agent).is_empty());
        assert!(get_logs()
            .iter()
            .any(|l| l.contains("\"event\":\"intents_configured\"") && l.contains("\"epoch\":2")));
    }

    #[test]
    fn old_epoch_approvals_do_not_count_toward_the_limit() {
        let (mut c, owner, agent) = intents_setup();
        for _ in 0..5 {
            approve(&mut c, &agent, NEAR, T0 + 10 * MIN_NS);
        }
        owner_ctx(&owner, T0);
        c.set_intents_config(wnear(), guardian());
        approve(&mut c, &agent, NEAR, T0 + 10 * MIN_NS);
        assert_eq!(c.get_approvals(agent.clone()).len(), 1);
        assert_eq!(c.approvals.get(&agent).unwrap().len(), 1);
    }

    #[test]
    fn route_fields_must_be_printable_ascii() {
        let bad = ["a\u{202e}b", "a\u{200b}b", "a b", "caf\u{e9}", "a\tb"];
        for value in bad {
            for in_recipient in [false, true] {
                let (mut c, owner, agent) = intents_setup();
                owner_ctx(&owner, T0);
                let r = if in_recipient {
                    Route {
                        dest_asset: "usdc".to_string(),
                        recipient: value.to_string(),
                    }
                } else {
                    Route {
                        dest_asset: value.to_string(),
                        recipient: "0xabc".to_string(),
                    }
                };
                let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    c.set_routes(agent, vec![r]);
                }));
                assert!(result.is_err(), "accepted {value:?}");
            }
        }
    }

    #[test]
    #[should_panic(expected = "invalid deposit account")]
    fn approve_rejects_the_wnear_contract_as_deposit_account() {
        let (mut c, _o, agent) = intents_setup();
        set_ctx(&guardian(), T0);
        c.approve_intent(agent, 0, wnear(), U128(NEAR), U64(T0 + MIN_NS), hash());
    }

    #[test]
    #[should_panic(expected = "invalid deposit account")]
    fn approve_rejects_another_agent_as_deposit_account() {
        let (mut c, owner, agent) = intents_setup();
        owner_ctx(&owner, T0);
        let other = accounts(5);
        c.set_policy(other.clone(), U128(NEAR), U128(NEAR), vec![]);
        set_ctx(&guardian(), T0);
        c.approve_intent(agent, 0, other, U128(NEAR), U64(T0 + MIN_NS), hash());
    }

    #[test]
    #[should_panic(expected = "guardian only")]
    fn signer_is_not_used_for_approve_auth() {
        let (mut c, _o, agent) = intents_setup();
        let mut b = ctx(&agent, T0, 0);
        b.signer_account_id(guardian());
        testing_env!(b.build());
        c.approve_intent(
            agent.clone(),
            0,
            deposit_acct(),
            U128(NEAR),
            U64(T0 + MIN_NS),
            hash(),
        );
    }

    #[test]
    #[should_panic(expected = "owner only")]
    fn non_owner_cannot_set_routes() {
        let (mut c, _o, agent) = intents_setup();
        testing_env!(ctx(&agent, T0, 1).build());
        c.set_routes(agent.clone(), vec![route()]);
    }

    #[test]
    #[should_panic(expected = "Requires attached deposit")]
    fn set_routes_needs_one_yocto() {
        let (mut c, owner, agent) = intents_setup();
        set_ctx(&owner, T0);
        c.set_routes(agent, vec![route()]);
    }

    #[test]
    #[should_panic(expected = "unknown route")]
    fn approve_rejects_route_id_255() {
        let (mut c, _o, agent) = intents_setup();
        set_ctx(&guardian(), T0);
        c.approve_intent(
            agent,
            255,
            deposit_acct(),
            U128(NEAR),
            U64(T0 + MIN_NS),
            hash(),
        );
    }

    #[test]
    fn expired_approvals_are_hidden_and_removed_on_the_next_approval() {
        let (mut c, _o, agent) = intents_setup();
        approve(&mut c, &agent, NEAR, T0 + MIN_NS);
        set_ctx(&guardian(), T0 + 2 * MIN_NS);
        assert!(c.get_approvals(agent.clone()).is_empty());
        assert_eq!(c.approvals.get(&agent).unwrap().len(), 1, "still stored");
        c.approve_intent(
            agent.clone(),
            0,
            deposit_acct(),
            U128(NEAR),
            U64(T0 + 10 * MIN_NS),
            hash(),
        );
        assert_eq!(
            c.approvals.get(&agent).unwrap().len(),
            1,
            "expired one removed"
        );
    }

    #[test]
    fn the_events_carry_all_their_fields() {
        let (mut c, _o, agent) = intents_setup();
        assert!(get_logs().iter().any(|l| {
            l.contains("\"event\":\"intents_configured\"")
                && l.contains(&format!("\"wnear\":\"{}\"", wnear()))
                && l.contains(&format!("\"guardian\":\"{}\"", guardian()))
        }));
        approve(&mut c, &agent, NEAR, T0 + 10 * MIN_NS);
        let logs = get_logs();
        let line = logs
            .iter()
            .find(|l| l.contains("\"event\":\"intent_approved\""))
            .expect("intent_approved");
        for part in [
            format!("\"agent\":\"{agent}\""),
            "\"route_id\":0".to_string(),
            format!("\"deposit_account\":\"{}\"", deposit_acct()),
            format!("\"amount\":\"{NEAR}\""),
            format!("\"expires_ns\":\"{}\"", T0 + 10 * MIN_NS),
            format!("\"quote_hash\":\"{}\"", hash()),
        ] {
            assert!(line.contains(&part), "missing {part}");
        }
    }

    // ----- spend_intent and unwrap -----

    fn gas_ctx(caller: &AccountId, ts: u64, deposit: u128, tgas: u64) {
        testing_env!(ctx(caller, ts, deposit)
            .prepaid_gas(Gas::from_tgas(tgas))
            .build());
    }

    fn intent(c: &mut Leash, agent: &AccountId, amount: u128, ts: u64) -> bool {
        gas_ctx(agent, ts, 0, 200);
        c.spend_intent(0, deposit_acct(), U128(amount))
    }

    fn intent_denied_with(reason: &str) -> bool {
        get_logs().iter().any(|l| {
            l.contains("\"event\":\"intent_denied\"")
                && l.contains(&format!("\"reason\":\"{reason}\""))
        })
    }

    fn approve_h(c: &mut Leash, agent: &AccountId, amount: u128, expires: u64, h: &str) {
        set_ctx(&guardian(), T0);
        c.approve_intent(
            agent.clone(),
            0,
            deposit_acct(),
            U128(amount),
            U64(expires),
            h.to_string(),
        );
    }

    fn batch_calls() -> (AccountId, Vec<(String, Value, u128, u64)>) {
        let receipts = get_created_receipts();
        assert_eq!(receipts.len(), 1, "exactly one receipt");
        let calls = receipts[0]
            .actions
            .iter()
            .filter_map(|a| match a {
                MockAction::FunctionCallWeight {
                    method_name,
                    args,
                    attached_deposit,
                    prepaid_gas,
                    ..
                } => Some((
                    String::from_utf8(method_name.clone()).unwrap(),
                    near_sdk::serde_json::from_slice::<Value>(args).unwrap(),
                    attached_deposit.as_yoctonear(),
                    prepaid_gas.as_tgas(),
                )),
                _ => None,
            })
            .collect();
        (receipts[0].receiver_id.clone(), calls)
    }

    fn appr(amount: u128, expires: u64, epoch: u32, h: &str) -> Approval {
        Approval {
            route_id: 0,
            deposit_account: deposit_acct(),
            amount,
            expires_ns: expires,
            epoch,
            quote_hash: h.to_string(),
        }
    }

    fn base(a: &[Approval]) -> IntentCheck<'_> {
        IntentCheck {
            configured: true,
            all_paused: false,
            agent_paused: false,
            per_tx_cap: 2 * NEAR,
            daily_cap: 5 * NEAR,
            window_start_ns: T0,
            spent_in_window: 0,
            route_count: 1,
            approvals: a,
            epoch: 1,
            now: T0,
            available: 100 * NEAR,
        }
    }

    #[test]
    fn check_order_follows_the_decided_sequence() {
        let none: Vec<Approval> = vec![];
        let d = deposit_acct();
        let mut s = base(&none);
        s.configured = false;
        s.all_paused = true;
        s.agent_paused = true;
        s.available = 0;
        s.spent_in_window = 5 * NEAR;
        assert_eq!(check_intent(&s, 9, &d, 0), Err("intents_not_configured"));
        s.configured = true;
        assert_eq!(check_intent(&s, 9, &d, 0), Err("all_paused"));
        s.all_paused = false;
        assert_eq!(check_intent(&s, 9, &d, 0), Err("agent_paused"));
        s.agent_paused = false;
        assert_eq!(check_intent(&s, 9, &d, 0), Err("zero_amount"));
        assert_eq!(check_intent(&s, 9, &d, 3 * NEAR), Err("unknown_route"));
        assert_eq!(check_intent(&s, 0, &d, 3 * NEAR), Err("over_per_tx_cap"));
        assert_eq!(check_intent(&s, 0, &d, NEAR), Err("no_approval"));
        let one = vec![appr(NEAR, T0 + MIN_NS, 1, &hash())];
        let mut s2 = base(&one);
        s2.available = 0;
        s2.spent_in_window = 5 * NEAR;
        assert_eq!(check_intent(&s2, 0, &d, NEAR), Err("over_daily_cap"));
        s2.spent_in_window = 0;
        assert_eq!(check_intent(&s2, 0, &d, NEAR), Err("insufficient_funds"));
        s2.available = NEAR + INTENT_OVERHEAD;
        assert_eq!(check_intent(&s2, 0, &d, NEAR), Ok(0));
        s2.available -= 1;
        assert_eq!(check_intent(&s2, 0, &d, NEAR), Err("insufficient_funds"));
    }

    #[test]
    fn check_matching_rules() {
        let d = deposit_acct();
        let other: AccountId = "other.testnet".parse().unwrap();
        let list = vec![appr(NEAR, T0 + MIN_NS, 1, &hash())];
        let s = base(&list);
        assert_eq!(check_intent(&s, 0, &d, NEAR), Ok(0));
        assert_eq!(check_intent(&s, 0, &d, NEAR + 1), Err("no_approval"));
        assert_eq!(check_intent(&s, 0, &other, NEAR), Err("no_approval"));
        let mut old_epoch = base(&list);
        old_epoch.epoch = 2;
        assert_eq!(check_intent(&old_epoch, 0, &d, NEAR), Err("no_approval"));
        let mut at_expiry = base(&list);
        at_expiry.now = T0 + MIN_NS;
        assert_eq!(check_intent(&at_expiry, 0, &d, NEAR), Err("no_approval"));
        let twins = vec![
            appr(NEAR, T0 + MIN_NS, 1, &"1".repeat(64)),
            appr(NEAR, T0 + 5 * MIN_NS, 1, &"2".repeat(64)),
        ];
        assert_eq!(check_intent(&base(&twins), 0, &d, NEAR), Ok(0));
        let mut later = base(&twins);
        later.now = T0 + 2 * MIN_NS;
        assert_eq!(check_intent(&later, 0, &d, NEAR), Ok(1));
        let mut stale_window = base(&list);
        stale_window.spent_in_window = 5 * NEAR;
        stale_window.now = T0 + MIN_NS - 1;
        assert_eq!(
            check_intent(&stale_window, 0, &d, NEAR),
            Err("over_daily_cap")
        );
        let mut new_window = base(&list);
        new_window.spent_in_window = 5 * NEAR;
        new_window.window_start_ns = 0;
        assert_eq!(check_intent(&new_window, 0, &d, NEAR), Ok(0));
    }

    #[test]
    #[should_panic(expected = "guardian only")]
    fn agent_cannot_approve_intent() {
        let (mut c, _o, agent) = intents_setup();
        set_ctx(&agent, T0);
        c.approve_intent(
            agent.clone(),
            0,
            deposit_acct(),
            U128(NEAR),
            U64(T0 + MIN_NS),
            hash(),
        );
    }

    #[test]
    #[should_panic(expected = "no policy for caller")]
    fn guardian_cannot_spend_intent() {
        let (mut c, _o, _a) = intents_setup();
        gas_ctx(&guardian(), T0, 0, 200);
        c.spend_intent(0, deposit_acct(), U128(NEAR));
    }

    #[test]
    #[should_panic(expected = "no policy for caller")]
    fn guardian_cannot_call_spend() {
        let (mut c, _o, _a) = intents_setup();
        set_ctx(&guardian(), T0);
        c.spend(accounts(2), U128(NEAR), None);
    }

    #[test]
    #[should_panic(expected = "owner only")]
    fn guardian_cannot_unwrap() {
        let (mut c, _o, _a) = intents_setup();
        gas_ctx(&guardian(), T0, 1, 100);
        let _ = c.unwrap(U128(NEAR));
    }

    #[test]
    fn caller_without_policy_reverts_and_leaves_no_trace() {
        let (mut c, _o, _a) = intents_setup();
        gas_ctx(&accounts(2), T0, 0, 200);
        let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            c.spend_intent(0, deposit_acct(), U128(NEAR))
        }));
        assert!(r.is_err());
        assert_eq!(c.get_stats(), (U64(0), U64(0)));
        assert!(get_logs().is_empty());
    }

    #[test]
    fn denied_without_config_counts_and_emits() {
        let (mut c, _o, agent, _t) = setup();
        assert!(!intent(&mut c, &agent, NEAR, T0));
        assert!(intent_denied_with("intents_not_configured"));
        assert_eq!(c.get_stats(), (U64(0), U64(1)));
        let log = get_logs().join("");
        assert!(
            log.contains("\"route_id\":0")
                && log.contains("\"amount\":\"1000000000000000000000000\"")
        );
        assert!(log.contains(&format!("\"agent\":\"{agent}\"")));
    }

    #[test]
    fn denied_when_paused_or_unknown_route_or_over_cap() {
        let (mut c, owner, agent) = intents_setup();
        approve(&mut c, &agent, NEAR, T0 + 10 * MIN_NS);
        owner_ctx(&owner, T0);
        c.pause_all();
        assert!(!intent(&mut c, &agent, NEAR, T0));
        assert!(intent_denied_with("all_paused"));
        owner_ctx(&owner, T0);
        c.resume_all();
        owner_ctx(&owner, T0);
        c.pause_agent(agent.clone());
        assert!(!intent(&mut c, &agent, NEAR, T0));
        assert!(intent_denied_with("agent_paused"));
        // a denied intent consumes no approval and no cap
        set_ctx(&agent, T0);
        assert_eq!(c.get_approvals(agent.clone()).len(), 1);
        assert_eq!(c.get_policy(agent.clone()).unwrap().spent_in_window.0, 0);
        owner_ctx(&owner, T0);
        c.resume_agent(agent.clone());
        gas_ctx(&agent, T0, 0, 200);
        assert!(!c.spend_intent(0, deposit_acct(), U128(0)));
        assert!(intent_denied_with("zero_amount"));
        gas_ctx(&agent, T0, 0, 200);
        assert!(!c.spend_intent(1, deposit_acct(), U128(NEAR)));
        assert!(intent_denied_with("unknown_route"));
        gas_ctx(&agent, T0, 0, 200);
        assert!(!c.spend_intent(0, deposit_acct(), U128(3 * NEAR)));
        assert!(intent_denied_with("over_per_tx_cap"));
        assert!(intent(&mut c, &agent, NEAR, T0));
    }

    #[test]
    fn approval_is_single_use() {
        let (mut c, _o, agent) = intents_setup();
        approve(&mut c, &agent, NEAR, T0 + 10 * MIN_NS);
        assert!(intent(&mut c, &agent, NEAR, T0));
        assert!(!intent(&mut c, &agent, NEAR, T0));
        assert!(intent_denied_with("no_approval"));
        assert_eq!(c.get_stats(), (U64(1), U64(1)));
    }

    #[test]
    fn wrong_amount_route_deposit_or_agent_is_denied() {
        let (mut c, owner, agent) = intents_setup();
        owner_ctx(&owner, T0);
        c.set_routes(agent.clone(), vec![route(), route()]);
        let agent2: AccountId = "agent2.testnet".parse().unwrap();
        owner_ctx(&owner, T0);
        c.set_policy(agent2.clone(), U128(2 * NEAR), U128(5 * NEAR), vec![]);
        owner_ctx(&owner, T0);
        c.set_routes(agent2.clone(), vec![route()]);
        approve(&mut c, &agent, NEAR, T0 + 10 * MIN_NS);
        assert!(!intent(&mut c, &agent, NEAR + 1, T0));
        assert!(intent_denied_with("no_approval"));
        gas_ctx(&agent, T0, 0, 200);
        let other: AccountId = "other.testnet".parse().unwrap();
        assert!(!c.spend_intent(0, other, U128(NEAR)));
        assert!(intent_denied_with("no_approval"));
        gas_ctx(&agent, T0, 0, 200);
        assert!(!c.spend_intent(1, deposit_acct(), U128(NEAR)));
        assert!(intent_denied_with("no_approval"));
        assert!(!intent(&mut c, &agent2, NEAR, T0));
        assert!(intent_denied_with("no_approval"));
        // the right call still works: nothing above consumed the approval
        assert!(intent(&mut c, &agent, NEAR, T0));
    }

    #[test]
    fn expired_approval_is_never_consumed() {
        let (mut c, _o, agent) = intents_setup();
        approve(&mut c, &agent, NEAR, T0 + MIN_NS);
        assert!(!intent(&mut c, &agent, NEAR, T0 + MIN_NS));
        assert!(!intent(&mut c, &agent, NEAR, T0 + 2 * MIN_NS));
        assert!(intent_denied_with("no_approval"));
        // an expired twin is skipped; the live one is used
        approve_h(&mut c, &agent, NEAR, T0 + MIN_NS, &"1".repeat(64));
        approve_h(&mut c, &agent, NEAR, T0 + 20 * MIN_NS, &"2".repeat(64));
        assert!(intent(&mut c, &agent, NEAR, T0 + 5 * MIN_NS));
        assert!(get_logs().join("").contains(&"2".repeat(64)));
        assert!(!intent(&mut c, &agent, NEAR, T0 + 5 * MIN_NS));
    }

    #[test]
    fn set_routes_clears_approvals() {
        let (mut c, owner, agent) = intents_setup();
        approve(&mut c, &agent, NEAR, T0 + 10 * MIN_NS);
        owner_ctx(&owner, T0);
        c.set_routes(agent.clone(), vec![route()]);
        assert!(!intent(&mut c, &agent, NEAR, T0));
        assert!(intent_denied_with("no_approval"));
    }

    #[test]
    fn config_change_revokes_approvals_and_new_ones_work() {
        let (mut c, owner, agent) = intents_setup();
        approve(&mut c, &agent, NEAR, T0 + 10 * MIN_NS);
        owner_ctx(&owner, T0);
        c.set_intents_config(wnear(), guardian());
        assert!(!intent(&mut c, &agent, NEAR, T0));
        assert!(intent_denied_with("no_approval"));
        approve(&mut c, &agent, NEAR, T0 + 10 * MIN_NS);
        assert!(intent(&mut c, &agent, NEAR, T0));
    }

    #[test]
    fn identical_approvals_are_used_one_per_swap_in_list_order() {
        let (mut c, _o, agent) = intents_setup();
        approve_h(&mut c, &agent, NEAR, T0 + 10 * MIN_NS, &"1".repeat(64));
        approve_h(&mut c, &agent, NEAR, T0 + 10 * MIN_NS, &"2".repeat(64));
        assert!(intent(&mut c, &agent, NEAR, T0));
        assert!(get_logs().join("").contains(&"1".repeat(64)));
        assert!(intent(&mut c, &agent, NEAR, T0));
        assert!(get_logs().join("").contains(&"2".repeat(64)));
        assert!(!intent(&mut c, &agent, NEAR, T0));
        assert!(intent_denied_with("no_approval"));
    }

    #[test]
    fn daily_cap_is_shared_between_spend_and_spend_intent() {
        let (mut c, _o, agent) = intents_setup();
        set_ctx(&agent, T0);
        assert!(c.spend(accounts(2), U128(2 * NEAR), None));
        assert!(c.spend(accounts(2), U128(2 * NEAR), None));
        approve(&mut c, &agent, 2 * NEAR, T0 + 10 * MIN_NS);
        assert!(!intent(&mut c, &agent, 2 * NEAR, T0));
        assert!(intent_denied_with("over_daily_cap"));
        // the other direction, in a fresh vault
        let (mut c, _o, agent) = intents_setup();
        approve(&mut c, &agent, 2 * NEAR, T0 + 10 * MIN_NS);
        approve(&mut c, &agent, 2 * NEAR, T0 + 10 * MIN_NS);
        assert!(intent(&mut c, &agent, 2 * NEAR, T0));
        assert!(intent(&mut c, &agent, 2 * NEAR, T0));
        set_ctx(&agent, T0);
        assert!(!c.spend(accounts(2), U128(2 * NEAR), None));
        assert!(denied_with("over_daily_cap"));
    }

    #[test]
    fn funds_boundary_is_amount_plus_two_storage_deposits() {
        let (mut c, _o, agent) = intents_setup();
        approve(&mut c, &agent, NEAR, T0 + 10 * MIN_NS);
        gas_ctx(&agent, T0, 0, 200);
        let reserve = 100 * NEAR - c.get_available_balance().0;
        let need = NEAR + INTENT_OVERHEAD;
        testing_env!(ctx(&agent, T0, 0)
            .prepaid_gas(Gas::from_tgas(200))
            .account_balance(NearToken::from_yoctonear(need + reserve - 1))
            .build());
        assert!(!c.spend_intent(0, deposit_acct(), U128(NEAR)));
        assert!(intent_denied_with("insufficient_funds"));
        assert_eq!(c.get_approvals(agent.clone()).len(), 1);
        testing_env!(ctx(&agent, T0, 0)
            .prepaid_gas(Gas::from_tgas(200))
            .account_balance(NearToken::from_yoctonear(need + reserve))
            .build());
        assert!(c.spend_intent(0, deposit_acct(), U128(NEAR)));
    }

    #[test]
    fn batch_has_four_actions_in_order_to_wnear() {
        let (mut c, _o, agent) = intents_setup();
        approve(&mut c, &agent, NEAR, T0 + 10 * MIN_NS);
        assert!(intent(&mut c, &agent, NEAR, T0));
        let (receiver, calls) = batch_calls();
        assert_eq!(receiver, wnear());
        assert_eq!(calls.len(), 4);
        assert_eq!(calls[0].0, "storage_deposit");
        assert_eq!(
            calls[0].1,
            json!({"account_id": vault(), "registration_only": true})
        );
        assert_eq!(calls[0].2, STORAGE_DEPOSIT);
        assert_eq!(calls[1].0, "storage_deposit");
        assert_eq!(
            calls[1].1,
            json!({"account_id": deposit_acct(), "registration_only": true})
        );
        assert_eq!(calls[1].2, STORAGE_DEPOSIT);
        assert_eq!(calls[2].0, "near_deposit");
        assert_eq!(calls[2].2, NEAR);
        assert_eq!(calls[3].0, "ft_transfer");
        assert_eq!(
            calls[3].1,
            json!({"receiver_id": deposit_acct(), "amount": NEAR.to_string()})
        );
        assert_eq!(calls[3].2, 1);
        assert!(calls.iter().all(|x| x.3 == ACTION_GAS_TGAS));
    }

    #[test]
    fn storage_amounts_are_counted_in_the_daily_counter() {
        let (mut c, _o, agent) = intents_setup();
        approve(&mut c, &agent, NEAR, T0 + 10 * MIN_NS);
        assert!(intent(&mut c, &agent, NEAR, T0));
        assert_eq!(
            c.get_policy(agent).unwrap().spent_in_window.0,
            NEAR + INTENT_OVERHEAD
        );
    }

    #[test]
    fn a_one_yocto_intent_consumes_one_plus_the_overhead() {
        let (mut c, _o, agent) = intents_setup();
        approve(&mut c, &agent, 1, T0 + 10 * MIN_NS);
        assert!(intent(&mut c, &agent, 1, T0));
        assert_eq!(
            c.get_policy(agent).unwrap().spent_in_window.0,
            1 + INTENT_OVERHEAD
        );
    }

    #[test]
    fn daily_boundary_counts_the_overhead() {
        let list = vec![appr(NEAR, T0 + MIN_NS, 1, &hash())];
        let d = deposit_acct();
        let mut s = base(&list);
        s.spent_in_window = s.daily_cap - NEAR - INTENT_OVERHEAD;
        assert_eq!(check_intent(&s, 0, &d, NEAR), Ok(0));
        s.spent_in_window += 1;
        assert_eq!(check_intent(&s, 0, &d, NEAR), Err("over_daily_cap"));
        let mut big = base(&list);
        big.spent_in_window = u128::MAX;
        assert_eq!(check_intent(&big, 0, &d, NEAR), Err("over_daily_cap"));
        let mut huge = base(&list);
        huge.per_tx_cap = u128::MAX;
        huge.daily_cap = u128::MAX;
        let max_list = vec![appr(u128::MAX, T0 + MIN_NS, 1, &hash())];
        huge.approvals = &max_list;
        assert_eq!(check_intent(&huge, 0, &d, u128::MAX), Err("over_daily_cap"));
    }

    #[test]
    fn a_denied_intent_counts_nothing_in_the_window() {
        let (mut c, _o, agent) = intents_setup();
        approve(&mut c, &agent, 2 * NEAR, T0 + 10 * MIN_NS);
        set_ctx(&agent, T0);
        assert!(c.spend(accounts(2), U128(2 * NEAR), None));
        assert!(c.spend(accounts(2), U128(NEAR + NEAR / 2 + NEAR / 4), None));
        let before = c.get_policy(agent.clone()).unwrap().spent_in_window.0;
        assert!(!intent(&mut c, &agent, 2 * NEAR, T0));
        assert!(intent_denied_with("over_daily_cap"));
        set_ctx(&agent, T0);
        assert_eq!(c.get_policy(agent).unwrap().spent_in_window.0, before);
    }

    #[test]
    fn allowed_event_carries_the_consumed_quote_hash() {
        let (mut c, _o, agent) = intents_setup();
        approve_h(&mut c, &agent, NEAR, T0 + 10 * MIN_NS, &"b".repeat(64));
        assert!(intent(&mut c, &agent, NEAR, T0));
        let log = get_logs().join("");
        assert!(log.contains("\"event\":\"intent_sent\""));
        assert!(log.contains(&format!("\"quote_hash\":\"{}\"", "b".repeat(64))));
        assert!(log.contains(&format!("\"deposit_account\":\"{}\"", deposit_acct())));
    }

    #[test]
    fn early_gas_panic_counts_nothing_and_120_passes() {
        let (mut c, _o, agent) = intents_setup();
        approve(&mut c, &agent, NEAR, T0 + 10 * MIN_NS);
        gas_ctx(&agent, T0, 0, MIN_INTENT_GAS_TGAS - 1);
        let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            c.spend_intent(0, deposit_acct(), U128(NEAR))
        }));
        assert!(r.is_err());
        set_ctx(&agent, T0);
        assert_eq!(c.get_stats(), (U64(0), U64(0)));
        assert_eq!(c.get_approvals(agent.clone()).len(), 1);
        assert_eq!(c.get_policy(agent.clone()).unwrap().spent_in_window.0, 0);
        gas_ctx(&agent, T0, 0, MIN_INTENT_GAS_TGAS);
        assert!(c.spend_intent(0, deposit_acct(), U128(NEAR)));
    }

    #[test]
    fn gas_numbers_fit_the_transaction_limit() {
        const {
            assert!(MIN_INTENT_GAS_TGAS + 100 <= 300);
            assert!(4 * ACTION_GAS_TGAS <= MIN_INTENT_GAS_TGAS);
            assert!(UNWRAP_ACTION_GAS_TGAS <= MIN_UNWRAP_GAS_TGAS);
        }
    }

    #[test]
    fn unwrap_calls_near_withdraw_with_one_yocto() {
        let (mut c, owner, _a) = intents_setup();
        gas_ctx(&owner, T0, 1, MIN_UNWRAP_GAS_TGAS);
        let _ = c.unwrap(U128(NEAR));
        let log = get_logs().join("");
        assert!(log.contains("\"event\":\"unwrapped\""));
        assert!(log.contains("\"amount\":\"1000000000000000000000000\""));
        let (receiver, calls) = batch_calls();
        assert_eq!(receiver, wnear());
        assert_eq!(calls.len(), 1);
        assert_eq!(calls[0].0, "near_withdraw");
        assert_eq!(calls[0].1, json!({"amount": NEAR.to_string()}));
        assert_eq!(calls[0].2, 1);
    }

    #[test]
    fn unwrap_works_while_paused() {
        let (mut c, owner, agent) = intents_setup();
        owner_ctx(&owner, T0);
        c.pause_all();
        owner_ctx(&owner, T0);
        c.pause_agent(agent);
        gas_ctx(&owner, T0, 1, MIN_UNWRAP_GAS_TGAS);
        let _ = c.unwrap(U128(NEAR));
        assert!(get_logs().join("").contains("\"event\":\"unwrapped\""));
    }

    #[test]
    #[should_panic(expected = "owner only")]
    fn unwrap_needs_owner() {
        let (mut c, _o, agent) = intents_setup();
        gas_ctx(&agent, T0, 1, 100);
        let _ = c.unwrap(U128(NEAR));
    }

    #[test]
    #[should_panic(expected = "Requires attached deposit")]
    fn unwrap_needs_one_yocto() {
        let (mut c, owner, _a) = intents_setup();
        gas_ctx(&owner, T0, 0, 100);
        let _ = c.unwrap(U128(NEAR));
    }

    #[test]
    #[should_panic(expected = "amount must be above 0")]
    fn unwrap_rejects_zero() {
        let (mut c, owner, _a) = intents_setup();
        gas_ctx(&owner, T0, 1, 100);
        let _ = c.unwrap(U128(0));
    }

    #[test]
    #[should_panic(expected = "intents not configured")]
    fn unwrap_needs_wnear_configured() {
        let (mut c, owner, _a, _t) = setup();
        gas_ctx(&owner, T0, 1, 100);
        let _ = c.unwrap(U128(NEAR));
    }

    #[test]
    #[should_panic(expected = "not enough gas")]
    fn unwrap_needs_30_tgas() {
        let (mut c, owner, _a) = intents_setup();
        gas_ctx(&owner, T0, 1, MIN_UNWRAP_GAS_TGAS - 1);
        let _ = c.unwrap(U128(NEAR));
    }

    #[test]
    fn policy_change_after_approval_is_enforced_at_spend_time() {
        let (mut c, owner, agent) = intents_setup();
        approve(&mut c, &agent, 2 * NEAR, T0 + 10 * MIN_NS);
        owner_ctx(&owner, T0);
        c.set_policy(agent.clone(), U128(NEAR), U128(5 * NEAR), vec![accounts(2)]);
        assert!(!intent(&mut c, &agent, 2 * NEAR, T0));
        assert!(intent_denied_with("over_per_tx_cap"));
        set_ctx(&agent, T0);
        assert_eq!(c.get_approvals(agent.clone()).len(), 1);
        // lower daily cap below spent + amount
        owner_ctx(&owner, T0);
        c.set_policy(
            agent.clone(),
            U128(2 * NEAR),
            U128(5 * NEAR),
            vec![accounts(2)],
        );
        set_ctx(&agent, T0);
        assert!(c.spend(accounts(2), U128(2 * NEAR), None));
        owner_ctx(&owner, T0);
        c.set_policy(
            agent.clone(),
            U128(2 * NEAR),
            U128(3 * NEAR),
            vec![accounts(2)],
        );
        assert!(!intent(&mut c, &agent, 2 * NEAR, T0));
        assert!(intent_denied_with("over_daily_cap"));
        set_ctx(&agent, T0);
        assert_eq!(c.get_approvals(agent.clone()).len(), 1);
    }

    #[test]
    fn allowlist_does_not_apply_to_intents() {
        let (mut c, owner, agent) = intents_setup();
        owner_ctx(&owner, T0);
        c.set_policy(agent.clone(), U128(2 * NEAR), U128(5 * NEAR), vec![]);
        approve(&mut c, &agent, NEAR, T0 + 10 * MIN_NS);
        assert!(intent(&mut c, &agent, NEAR, T0));
    }

    // ----- views: same check functions as the real methods -----

    fn last_reason(event: &str) -> String {
        get_logs()
            .iter()
            .rev()
            .find(|l| l.contains(&format!("\"event\":\"{event}\"")))
            .and_then(|l| {
                let v: Value =
                    near_sdk::serde_json::from_str(l.trim_start_matches("EVENT_JSON:")).ok()?;
                v["data"][0]["reason"].as_str().map(String::from)
            })
            .expect("a denial event with a reason")
    }

    /// The mock keeps storage between `testing_env!` calls; each case starts empty.
    fn fresh_storage() {
        near_sdk::mock::with_mocked_blockchain(|b| {
            let _ = b.take_storage();
        });
    }

    fn spend_view_matches(
        prepare: impl Fn(&mut Leash, &AccountId, &AccountId),
        to: AccountId,
        amount: u128,
        memo: Option<String>,
        expected: &str,
    ) {
        fresh_storage();
        let (mut c, owner, agent, _t) = setup();
        prepare(&mut c, &owner, &agent);
        set_ctx(&agent, T0);
        let seen = c.would_spend(agent.clone(), to.clone(), U128(amount), memo.clone());
        assert_eq!(seen, expected, "view");
        let real = if c.spend(to, U128(amount), memo) {
            "allowed".to_string()
        } else {
            last_reason("spend_denied")
        };
        assert_eq!(real, expected, "real method");
    }

    fn no_prep(_c: &mut Leash, _o: &AccountId, _a: &AccountId) {}

    #[test]
    fn would_spend_equals_spend_for_every_reason() {
        let t = accounts(2);
        spend_view_matches(no_prep, t.clone(), NEAR, None, "allowed");
        spend_view_matches(
            |c, o, _a| {
                owner_ctx(o, T0);
                c.pause_all();
            },
            t.clone(),
            NEAR,
            None,
            "all_paused",
        );
        spend_view_matches(
            no_prep,
            t.clone(),
            NEAR,
            Some("x".repeat(MAX_MEMO_BYTES + 1)),
            "memo_too_long",
        );
        spend_view_matches(
            |c, o, a| {
                owner_ctx(o, T0);
                c.pause_agent(a.clone());
            },
            t.clone(),
            NEAR,
            None,
            "agent_paused",
        );
        spend_view_matches(no_prep, t.clone(), 0, None, "zero_amount");
        spend_view_matches(no_prep, t.clone(), 3 * NEAR, None, "over_per_tx_cap");
        spend_view_matches(no_prep, accounts(3), NEAR, None, "not_allowlisted");
        spend_view_matches(
            |c, _o, a| {
                for _ in 0..2 {
                    set_ctx(a, T0);
                    assert!(c.spend(accounts(2), U128(2 * NEAR), None));
                }
            },
            t.clone(),
            2 * NEAR,
            None,
            "over_daily_cap",
        );
        spend_view_matches(
            |c, o, a| {
                owner_ctx(o, T0);
                c.set_policy(
                    a.clone(),
                    U128(1_000 * NEAR),
                    U128(2_000 * NEAR),
                    vec![accounts(2)],
                );
            },
            t,
            1_000 * NEAR,
            None,
            "insufficient_funds",
        );
    }

    #[test]
    fn would_spend_without_a_policy_is_a_view_only_value() {
        let (c, _o, _a, t) = setup();
        set_ctx(&accounts(4), T0);
        assert_eq!(c.would_spend(accounts(4), t, U128(NEAR), None), "no_policy");
    }

    #[test]
    #[should_panic(expected = "no policy for caller")]
    fn spend_without_a_policy_still_reverts() {
        let (mut c, _o, _a, t) = setup();
        set_ctx(&accounts(4), T0);
        c.spend(t, U128(NEAR), None);
    }

    fn intent_view_matches(
        prepare: impl Fn(&mut Leash, &AccountId, &AccountId),
        route_id: u8,
        amount: u128,
        expected: &str,
    ) {
        fresh_storage();
        let (mut c, owner, agent) = intents_setup();
        prepare(&mut c, &owner, &agent);
        set_ctx(&agent, T0);
        let seen = c.would_spend_intent(agent.clone(), route_id, deposit_acct(), U128(amount));
        assert_eq!(seen, expected, "view");
        gas_ctx(&agent, T0, 0, 200);
        let real = if c.spend_intent(route_id, deposit_acct(), U128(amount)) {
            "allowed".to_string()
        } else {
            last_reason("intent_denied")
        };
        assert_eq!(real, expected, "real method");
    }

    #[test]
    fn would_spend_intent_equals_spend_intent_for_every_reason() {
        let appr1 =
            |c: &mut Leash, _o: &AccountId, a: &AccountId| approve(c, a, NEAR, T0 + 10 * MIN_NS);
        intent_view_matches(appr1, 0, NEAR, "allowed");
        intent_view_matches(no_prep, 0, NEAR, "no_approval");
        intent_view_matches(appr1, 5, NEAR, "unknown_route");
        intent_view_matches(no_prep, 0, 0, "zero_amount");
        intent_view_matches(
            |c, o, a| {
                approve(c, a, NEAR, T0 + 10 * MIN_NS);
                owner_ctx(o, T0);
                c.pause_all();
            },
            0,
            NEAR,
            "all_paused",
        );
        intent_view_matches(
            |c, o, a| {
                approve(c, a, NEAR, T0 + 10 * MIN_NS);
                owner_ctx(o, T0);
                c.pause_agent(a.clone());
            },
            0,
            NEAR,
            "agent_paused",
        );
        intent_view_matches(
            |c, o, a| {
                approve(c, a, 2 * NEAR, T0 + 10 * MIN_NS);
                owner_ctx(o, T0);
                c.set_policy(a.clone(), U128(NEAR), U128(5 * NEAR), vec![accounts(2)]);
            },
            0,
            2 * NEAR,
            "over_per_tx_cap",
        );
        intent_view_matches(
            |c, _o, a| {
                for _ in 0..2 {
                    set_ctx(a, T0);
                    assert!(c.spend(accounts(2), U128(2 * NEAR), None));
                }
                approve(c, a, NEAR, T0 + 10 * MIN_NS);
            },
            0,
            NEAR,
            "over_daily_cap",
        );
        intent_view_matches(
            |c, o, a| {
                owner_ctx(o, T0);
                c.set_policy(
                    a.clone(),
                    U128(1_000 * NEAR),
                    U128(2_000 * NEAR),
                    vec![accounts(2)],
                );
                approve(c, a, 1_000 * NEAR, T0 + 10 * MIN_NS);
            },
            0,
            1_000 * NEAR,
            "insufficient_funds",
        );
    }

    #[test]
    fn would_spend_intent_before_configuration_matches_the_real_denial() {
        let (mut c, _o, agent, _t) = setup();
        set_ctx(&agent, T0);
        assert_eq!(
            c.would_spend_intent(agent.clone(), 0, deposit_acct(), U128(NEAR)),
            "intents_not_configured"
        );
        gas_ctx(&agent, T0, 0, 200);
        assert!(!c.spend_intent(0, deposit_acct(), U128(NEAR)));
        assert_eq!(last_reason("intent_denied"), "intents_not_configured");
    }

    #[test]
    fn would_spend_intent_without_a_policy_is_a_view_only_value() {
        let (c, _o, _a) = intents_setup();
        set_ctx(&accounts(4), T0);
        assert_eq!(
            c.would_spend_intent(accounts(4), 0, deposit_acct(), U128(NEAR)),
            "no_policy"
        );
    }

    #[test]
    fn views_change_no_state() {
        let (c, _o, agent) = intents_setup();
        set_ctx(&agent, T0 + 3 * DAY_NS);
        let stats = |c: &Leash| {
            let (a, d) = c.get_stats();
            (a.0, d.0)
        };
        let before = (
            stats(&c),
            c.get_policy(agent.clone()).map(|p| p.window_start_ns.0),
        );
        let _ = c.would_spend(agent.clone(), accounts(2), U128(NEAR), None);
        let _ = c.would_spend_intent(agent.clone(), 0, deposit_acct(), U128(NEAR));
        let after = (stats(&c), c.get_policy(agent).map(|p| p.window_start_ns.0));
        assert_eq!(before, after);
        assert!(get_logs().is_empty());
    }

    fn window_of(c: &Leash, agent: &AccountId) -> (u64, u128) {
        let p = c.agents.get(agent).expect("policy");
        (p.window_start_ns, p.spent_in_window)
    }

    #[test]
    fn spend_check_order_follows_the_decided_sequence() {
        let target = accounts(2);
        let other = accounts(3);
        let list = vec![target.clone()];
        let mut s = SpendCheck {
            all_paused: true,
            memo_ok: false,
            agent_paused: true,
            per_tx_cap: NEAR,
            daily_cap: 2 * NEAR,
            window_start_ns: T0,
            spent_in_window: 2 * NEAR,
            allowlist: &list,
            now: T0,
            available: 0,
        };
        assert_eq!(check_spend(&s, &other, 0), Err("all_paused"));
        s.all_paused = false;
        assert_eq!(check_spend(&s, &other, 0), Err("memo_too_long"));
        s.memo_ok = true;
        assert_eq!(check_spend(&s, &other, 0), Err("agent_paused"));
        s.agent_paused = false;
        assert_eq!(check_spend(&s, &other, 0), Err("zero_amount"));
        assert_eq!(check_spend(&s, &other, 2 * NEAR), Err("over_per_tx_cap"));
        assert_eq!(check_spend(&s, &other, NEAR), Err("not_allowlisted"));
        assert_eq!(check_spend(&s, &target, NEAR), Err("over_daily_cap"));
        s.spent_in_window = 0;
        assert_eq!(check_spend(&s, &target, NEAR), Err("insufficient_funds"));
        s.available = NEAR;
        assert_eq!(check_spend(&s, &target, NEAR), Ok(()));
        s.available = NEAR - 1;
        assert_eq!(check_spend(&s, &target, NEAR), Err("insufficient_funds"));
    }

    #[test]
    #[should_panic(expected = "no policy for caller")]
    fn spend_without_a_policy_reverts_while_all_paused() {
        let (mut c, owner, _a, t) = setup();
        owner_ctx(&owner, T0);
        c.pause_all();
        set_ctx(&accounts(4), T0);
        c.spend(t, U128(NEAR), None);
    }

    #[test]
    #[should_panic(expected = "no policy for caller")]
    fn spend_without_a_policy_reverts_with_an_oversize_memo() {
        let (mut c, _o, _a, t) = setup();
        set_ctx(&accounts(4), T0);
        c.spend(t, U128(NEAR), Some("x".repeat(MAX_MEMO_BYTES + 1)));
    }

    #[test]
    fn spend_without_a_policy_leaves_the_counters_alone() {
        let (mut c, owner, _a, t) = setup();
        owner_ctx(&owner, T0);
        c.pause_all();
        let before = c.get_stats();
        set_ctx(&accounts(4), T0);
        let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            c.spend(t.clone(), U128(NEAR), None)
        }));
        assert!(r.is_err());
        let after = c.get_stats();
        assert_eq!((before.0 .0, before.1 .0), (after.0 .0, after.1 .0));
    }

    #[test]
    fn views_return_no_policy_for_an_outsider() {
        fresh_storage();
        let (mut c, owner, _a) = intents_setup();
        owner_ctx(&owner, T0);
        c.pause_all();
        set_ctx(&accounts(4), T0);
        let big = Some("x".repeat(MAX_MEMO_BYTES + 1));
        assert_eq!(c.would_spend(accounts(4), accounts(2), U128(NEAR), big), "no_policy");
        assert_eq!(c.would_spend(accounts(4), accounts(2), U128(NEAR), None), "no_policy");
        assert_eq!(
            c.would_spend_intent(accounts(4), 0, deposit_acct(), U128(NEAR)),
            "no_policy"
        );
    }

    #[test]
    fn memo_byte_limit_is_the_same_for_the_view_and_the_method() {
        let t = accounts(2);
        spend_view_matches(no_prep, t.clone(), NEAR, Some("x".repeat(MAX_MEMO_BYTES)), "allowed");
        spend_view_matches(
            no_prep,
            t.clone(),
            NEAR,
            Some("é".repeat(MAX_MEMO_BYTES / 2)),
            "allowed",
        );
        spend_view_matches(
            no_prep,
            t.clone(),
            NEAR,
            Some("é".repeat(MAX_MEMO_BYTES / 2 + 1)),
            "memo_too_long",
        );
        spend_view_matches(no_prep, t, NEAR, Some(String::new()), "allowed");
    }

    #[test]
    fn rollover_then_insufficient_funds_restarts_the_window_but_the_view_does_not() {
        let (mut c, owner, agent, target) = setup();
        owner_ctx(&owner, T0);
        c.set_policy(agent.clone(), U128(1_000 * NEAR), U128(2_000 * NEAR), vec![target.clone()]);
        set_ctx(&agent, T0);
        assert!(c.spend(target.clone(), U128(NEAR), None));
        let before = window_of(&c, &agent);
        assert_eq!(before.1, NEAR);
        let later = T0 + DAY_NS + 5;
        set_ctx(&agent, later);
        let seen = c.would_spend(agent.clone(), target.clone(), U128(1_000 * NEAR), None);
        assert_eq!(seen, "insufficient_funds");
        assert_eq!(window_of(&c, &agent), before, "the view must not touch the window");
        assert!(!c.spend(target, U128(1_000 * NEAR), None));
        assert!(denied_with("insufficient_funds"));
        assert_eq!(window_of(&c, &agent), (later, 0), "the real call restarts the window");
    }

    #[test]
    fn rollover_then_allowed_sums_from_zero() {
        let (mut c, _o, agent, target) = setup();
        set_ctx(&agent, T0);
        assert!(c.spend(target.clone(), U128(2 * NEAR), None));
        let later = T0 + DAY_NS;
        set_ctx(&agent, later);
        assert!(c.spend(target, U128(NEAR), None));
        assert_eq!(window_of(&c, &agent), (later, NEAR));
    }

    #[test]
    fn a_pause_denial_does_not_restart_an_expired_window() {
        let (mut c, owner, agent, target) = setup();
        set_ctx(&agent, T0);
        assert!(c.spend(target.clone(), U128(NEAR), None));
        let before = window_of(&c, &agent);
        owner_ctx(&owner, T0);
        c.pause_agent(agent.clone());
        set_ctx(&agent, T0 + 3 * DAY_NS);
        assert!(!c.spend(target, U128(NEAR), None));
        assert!(denied_with("agent_paused"));
        assert_eq!(window_of(&c, &agent), before);
    }

    fn intent_outcome(c: &mut Leash, agent: &AccountId, at: u64, amount: u128) -> (String, String) {
        set_ctx(agent, at);
        let seen = c.would_spend_intent(agent.clone(), 0, deposit_acct(), U128(amount));
        gas_ctx(agent, at, 0, 200);
        let real = if c.spend_intent(0, deposit_acct(), U128(amount)) {
            "allowed".to_string()
        } else {
            last_reason("intent_denied")
        };
        (seen, real)
    }

    #[test]
    fn would_spend_intent_matches_for_expired_old_epoch_and_wrong_amount_approvals() {
        fresh_storage();
        let (mut c, _o, agent) = intents_setup();
        approve(&mut c, &agent, NEAR, T0 + 10 * MIN_NS);
        let (seen, real) = intent_outcome(&mut c, &agent, T0 + 11 * MIN_NS, NEAR);
        assert_eq!((seen.as_str(), real.as_str()), ("no_approval", "no_approval"));

        fresh_storage();
        let (mut c, _o, agent) = intents_setup();
        approve(&mut c, &agent, NEAR, T0 + 10 * MIN_NS);
        let (seen, real) = intent_outcome(&mut c, &agent, T0 + 10 * MIN_NS, NEAR);
        assert_eq!(seen, real, "at the instant of expiry");
        assert_eq!(seen, "no_approval");

        fresh_storage();
        let (mut c, owner, agent) = intents_setup();
        approve(&mut c, &agent, NEAR, T0 + 10 * MIN_NS);
        owner_ctx(&owner, T0);
        c.set_intents_config(wnear(), guardian());
        let (seen, real) = intent_outcome(&mut c, &agent, T0, NEAR);
        assert_eq!((seen.as_str(), real.as_str()), ("no_approval", "no_approval"));

        fresh_storage();
        let (mut c, _o, agent) = intents_setup();
        approve(&mut c, &agent, NEAR, T0 + 10 * MIN_NS);
        let (seen, real) = intent_outcome(&mut c, &agent, T0, NEAR + 1);
        assert_eq!((seen.as_str(), real.as_str()), ("no_approval", "no_approval"));
    }
}
