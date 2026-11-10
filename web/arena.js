(function () {
  "use strict";

  var EXPLORER_TX = "https://testnet.nearblocks.io/txns/";
  var TX_ID = /^[1-9A-HJ-NP-Za-km-z]{43,44}$/;
  var REFRESH_MS = 15000;
  var ATTEMPT_TIMEOUT_MS = 180000;

  var OUTCOMES = {
    refused: "The agent refused.",
    paid_payee: "The agent paid the payee.",
    blocked: "The agent tried to pay. The vault blocked it.",
    paid_other: "The vault paid the wrong account. Funds lost.",
    stopped: "The Arena was stopped before the payment.",
    transfer_failed: "The vault allowed it, but the money did not arrive. No funds lost.",
    chain_error: "The vault call failed.",
    model_error: "The model call failed."
  };

  function $(id) { return document.getElementById(id); }

  function make(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined) { node.textContent = String(text); }
    return node;
  }

  function clear(node) {
    while (node.firstChild) { node.removeChild(node.firstChild); }
  }

  function tierName(tier) { return tier === "loose" ? "Loose leash" : "Fortress"; }

  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }

  function when(iso) {
    var date = new Date(iso);
    return isNaN(date.getTime()) ? "" : date.toLocaleString();
  }

  function txLink(txId) {
    if (typeof txId !== "string" || !TX_ID.test(txId)) { return null; }
    var link = make("a", "", "View transaction");
    link.href = EXPLORER_TX + txId;
    link.rel = "noopener noreferrer";
    link.target = "_blank";
    return link;
  }

  // The page may be served from another address than the API. The address is read from one meta tag
  // and must be a plain https origin; anything else means "the same address as the page".
  var API_ORIGIN = /^https:\/\/[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[0-9]{1,5})?$/;
  var API_BASE = (function () {
    var tag = document.querySelector("meta[name=\"leash-api\"]");
    var value = tag ? tag.getAttribute("content") : "";
    return API_ORIGIN.test(value || "") ? value : "";
  }());

  function request(path, options, timeoutMs) {
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, timeoutMs || 20000);
    options = options || {};
    options.signal = controller.signal;
    options.credentials = "omit";
    return fetch(API_BASE + path, options).then(function (response) {
      return response.json().then(function (data) {
        return { ok: response.ok, status: response.status, data: data };
      }, function () {
        return { ok: false, status: response.status, data: {} };
      });
    }).finally(function () { clearTimeout(timer); });
  }

  // ---- scoreboard -------------------------------------------------------------
  function showStats(stats) {
    var total = stats.total;
    $("fooled-value").textContent = String(total.fooled);
    $("fooled-sub").textContent = "of " + plural(total.attempts, "attempt", "attempts") + " · NEAR testnet";
    $("lost-value").textContent = stats.total.funds_lost_near + " NEAR";
    $("lost-sub").textContent = plural(total.funds_lost_count, "payment", "payments") +
      " · of " + plural(total.attempts, "attempt", "attempts") + " · NEAR testnet";

    var rows = $("tier-rows");
    clear(rows);
    ["fortress", "loose"].forEach(function (name) {
      var tier = stats.tiers[name];
      if (!tier) { return; }
      var row = make("tr");
      row.appendChild(make("th", "", tierName(name))).setAttribute("scope", "row");
      row.appendChild(make("td", "num", tier.attempts));
      row.appendChild(make("td", "num", tier.fooled));
      row.appendChild(make("td", "num", tier.funds_lost_near + " NEAR"));
      rows.appendChild(row);

      var meta = $(name + "-meta");
      if (meta) {
        var text = "vault " + tier.vault + " · payee " + tier.payee;
        if (tier.bait) { text += " · bait " + tier.bait; }
        meta.textContent = text;
      }
    });

    var banner = $("banner");
    var note = "";
    if (stats.mode === "demo") {
      note = "Demo: a scripted agent and a fake vault. Nothing here is real.";
    } else if (stats.paused) {
      note = "The Arena is paused. The recorded attempts are still shown.";
    } else if (stats.replay) {
      note = "The model budget is used up. The recorded attempts are still shown.";
    }
    banner.textContent = note;
    banner.hidden = note === "";
    $("send").disabled = busy || (stats.mode !== "demo" && (stats.paused || stats.replay));
  }

  // ---- feed -------------------------------------------------------------------
  function feedItem(record) {
    var item = make("li", "item");
    var head = make("div", "item-head");
    head.appendChild(make("span", "badge", tierName(record.tier)));
    head.appendChild(make("span", "", OUTCOMES[record.outcome] || "Recorded."));
    if (record.fooled) { head.appendChild(make("span", "badge fooled", "agent fooled")); }
    if (record.funds_lost) { head.appendChild(make("span", "badge lost", "funds lost")); }
    var time = when(record.ts);
    if (time) { head.appendChild(make("span", "", time)); }
    var link = txLink(record.tx_id);
    if (link) { head.appendChild(link); }
    item.appendChild(head);
    item.appendChild(make("p", "item-msg", record.message || ""));
    return item;
  }

  function showFeed(records) {
    var list = $("feed");
    clear(list);
    records.forEach(function (record) { list.appendChild(feedItem(record)); });
    $("feed-empty").hidden = records.length > 0;
  }

  // ---- result of one attempt --------------------------------------------------
  function showResult(kind, title, lines, link) {
    var box = $("result");
    clear(box);
    box.className = "result " + kind;
    box.appendChild(make("h3", "", title));
    lines.forEach(function (line) {
      box.appendChild(make("p", line.detail ? "detail" : "", line.text));
    });
    if (link) { box.appendChild(link); }
    box.hidden = false;
    box.focus();
  }

  function showAttempt(data) {
    var lines = [];
    if (data.to) {
      lines.push({ text: "Decision: pay " + data.amount_near + " NEAR to " + data.to, detail: true });
    } else if (data.reason) {
      lines.push({ text: "Reason: " + data.reason, detail: true });
    }
    if (data.denial_reason) { lines.push({ text: "Vault reason: " + data.denial_reason, detail: true }); }
    if (data.fooled) { lines.push({ text: "The agent was fooled." }); }
    var kind = data.funds_lost ? "bad" : (data.fooled ? "bad" : "good");
    showResult(kind, OUTCOMES[data.outcome] || "Recorded.", lines, txLink(data.tx_id));
  }

  // ---- actions ----------------------------------------------------------------
  var busy = false;

  function refresh() {
    return Promise.all([request("/api/stats"), request("/api/feed?limit=20")]).then(function (answers) {
      if (answers[0].ok) { showStats(answers[0].data); }
      if (answers[1].ok) { showFeed(answers[1].data.records || []); }
    }).catch(function () { /* the next refresh tries again */ });
  }

  function submit(event) {
    event.preventDefault();
    if (busy) { return; }
    var tier = document.querySelector("input[name=tier]:checked").value;
    var message = $("message").value;
    busy = true;
    $("send").disabled = true;
    showResult("", "Waiting for the agent", [{ text: "This can take up to a minute." }]);
    request("/api/attempt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tier: tier, message: message })
    }, ATTEMPT_TIMEOUT_MS).then(function (answer) {
      if (answer.ok && answer.data.status === "done") {
        showAttempt(answer.data);
      } else {
        showResult("bad", "Not sent", [{ text: answer.data.message || "Something went wrong. Try again." }]);
      }
    }, function () {
      showResult("bad", "Not sent", [{ text: "The Arena did not answer. Try again." }]);
    }).then(function () {
      busy = false;
      $("send").disabled = false;
      return refresh();
    });
  }

  function countCharacters() {
    $("counter").textContent = $("message").value.length + " / 500";
  }

  $("form").addEventListener("submit", submit);
  $("message").addEventListener("input", countCharacters);
  countCharacters();
  refresh();
  setInterval(function () {
    if (document.visibilityState === "visible" && !busy) { refresh(); }
  }, REFRESH_MS);
})();
