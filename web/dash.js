"use strict";
(function () {
  var tiers = document.getElementById("tiers");
  var status = document.getElementById("status");
  var panic = document.getElementById("panic");
  var tokenInput = document.getElementById("token");
  var pauseButton = document.getElementById("pause");
  var result = document.getElementById("panic-result");
  var token = ""; // kept in memory only; never stored

  function row(list, label, value, cls) {
    var dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = label;
    dd.textContent = value === null || value === undefined ? "not readable" : String(value);
    if (cls) dd.className = cls;
    list.appendChild(dt); list.appendChild(dd);
  }

  function render(data) {
    tiers.textContent = "";
    Object.keys(data.tiers).forEach(function (name) {
      var t = data.tiers[name], card = document.createElement("div"), head = document.createElement("div");
      var h = document.createElement("h2"), state = document.createElement("span");
      var list = document.createElement("dl"), p = t.policy;
      var stopped = !!(t.paused_all || (p && p.paused));
      card.className = "card"; head.className = "card-head";
      h.textContent = name + " (" + data.network + ")";
      state.className = "state" + (stopped ? " bad" : ""); state.textContent = stopped ? "Paused" : "Active";
      head.appendChild(h); head.appendChild(state); card.appendChild(head);
      var balance = document.createElement("p"), unit = document.createElement("span"), cap = document.createElement("p");
      balance.className = "balance";
      balance.textContent = t.available_near === null ? "not readable" : String(t.available_near);
      unit.className = "unit"; unit.textContent = t.available_near === null ? "" : " NEAR";
      balance.appendChild(unit);
      cap.className = "balance-label"; cap.textContent = "Available in the vault";
      card.appendChild(balance); card.appendChild(cap);
      row(list, "Vault", t.vault);
      row(list, "Allowed / denied", t.allowed === null ? null : t.allowed + " / " + t.denied);
      row(list, "Paused (all)", t.paused_all, t.paused_all ? "bad" : "ok");
      row(list, "Agent paused", p ? p.paused : null, p ? (p.paused ? "bad" : "ok") : "");
      row(list, "Per transaction", p ? p.per_tx_cap_near + " NEAR" : null);
      row(list, "Daily cap", p ? p.daily_cap_near + " NEAR" : null);
      row(list, "Spent in window", p ? p.spent_in_window_near + " NEAR" : null);
      row(list, "Allowlist", p ? p.allowlist.join(", ") : null);
      row(list, "Chain head", t.head_hash);
      card.appendChild(list); tiers.appendChild(card);
    });
    panic.hidden = !data.panic_enabled;
    status.textContent = "Read from the NEAR " + data.network + " RPC.";
  }

  function load() {
    fetch("/api/dashboard", { cache: "no-store" }).then(function (r) { return r.json(); })
      .then(render).catch(function () { status.textContent = "The dashboard could not be loaded."; });
  }

  pauseButton.addEventListener("click", function () {
    token = tokenInput.value.trim(); tokenInput.value = "";
    if (!token) { result.textContent = "Enter the token first."; return; }
    if (!window.confirm("Pause both vaults and stop new attempts?")) { token = ""; return; }
    pauseButton.disabled = true; result.textContent = "Working…";
    fetch("/api/panic", { method: "POST", headers: { "Authorization": "Bearer " + token } })
      .then(function (r) { return r.json().then(function (b) { return { code: r.status, body: b }; }); })
      .then(function (x) {
        if (x.code === 200) {
          result.textContent = "Stopped. " + Object.keys(x.body.tiers).map(function (n) { return n + ": " + x.body.tiers[n]; }).join(", ");
          load();
        } else if (x.code === 401) { result.textContent = "Wrong token."; }
        else if (x.code === 429) { result.textContent = "Locked for a minute after wrong tokens."; }
        else { result.textContent = "Not done (" + x.code + ")."; }
      })
      .catch(function () { result.textContent = "No answer arrived. The pause may still have run: check the status below before trying again."; load(); })
      .then(function () { token = ""; pauseButton.disabled = false; });
  });

  load(); setInterval(load, 20000);
})();
