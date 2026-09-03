window.addEventListener("unhandledrejection", function(e) { tg.showAlert("Promise error: " + e.reason); });
window.onerror = function(msg, url, line, col, error) { tg.showAlert("JS Error:\n" + msg + "\nLine: " + line); };
const tg = window.Telegram.WebApp;
tg.ready();
tg.expand();

// ⚠️ Update this URL after deploying the backend
const API_BASE = "https://zoro-backend-5jyv.onrender.com";
// ⚠️ Update the manifest URL after deployment
const MANIFEST_URL = "https://zoro-abel.onrender.com/tonconnect-manifest.json";

const initData = tg.initData;

// ---------- TonConnect ----------
const tonConnectUI = new TON_CONNECT_UI.TonConnectUI({
  manifestUrl: MANIFEST_URL,
  buttonRootId: "ton-connect-btn",
  actionsConfiguration: {
    twaReturnUrl: "https://t.me/zorrocoin_bot"
  },
});

tonConnectUI.onStatusChange(async (wallet) => {
  if (wallet) {
    const address = wallet.account.address;
    try {
      await apiPost("/api/link-wallet", { wallet_address: address });
      await refreshState();
    } catch (e) {
      tg.showAlert("Error linking wallet: " + e.message);
      console.error("link-wallet error:", e);
    }
  }
});

// ---------- Helpers ----------
async function apiPost(path, body) {
  const res = await fetch(API_BASE + path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Telegram-Init-Data": initData,
    },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Unknown error" }));
    const detail = err.detail;
    if (detail && typeof detail === "object" && detail.error === "subscription_required") {
      const gateError = new Error(detail.message || "You need to join the required channels");
      gateError.subscriptionRequired = true;
      gateError.missingChannels = detail.missing_channels || [];
      throw gateError;
    }
    throw new Error(typeof detail === "string" ? detail : "Request error");
  }
  return res.json();
}

async function apiGet(path) {
  const res = await fetch(API_BASE + path, {
    headers: { "X-Telegram-Init-Data": initData },
  });
  if (!res.ok) throw new Error("Request error");
  return res.json();
}

function showError(msg) {
  const el = document.getElementById("errorMsg");
  el.textContent = msg;
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 4000);
}

// ---------- Navigation ----------
const pages = ["mine", "tasks", "miner", "friends", "profile"];
document.querySelectorAll(".nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    pages.forEach((p) => document.getElementById(`page-${p}`).classList.add("hidden"));
    document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active"));
    document.getElementById(`page-${btn.dataset.page}`).classList.remove("hidden");
    btn.classList.add("active");
  });
});

// ---------- State rendering ----------
let currentState = null;
let pendingTimer = null;

function getReferralCodeFromStartParam() {
  // Case 1: user opened t.me/YourBot?startapp=CODE directly
  const startParam = tg.initDataUnsafe?.start_param;
  if (startParam) return startParam;

  // Case 2: the code was placed in the WebApp link itself as ?ref=CODE (from a button inside the bot chat)
  const urlParams = new URLSearchParams(window.location.search);
  return urlParams.get("ref");
}

async function refreshState() {
  const referral_code = getReferralCodeFromStartParam();
  const data = await apiPost("/api/me", { referral_code });
  currentState = data;
  render(data);
}

function render(data) {
  document.getElementById("loadingMsg").classList.add("hidden");

  document.getElementById("usernameLabel").textContent = data.username || "No name";
  document.getElementById("avatarInitial").textContent = (data.username || "?")[0].toUpperCase();
  document.getElementById("levelValue").textContent = data.level;

  document.getElementById("poolBalance").textContent = data.pool_balance.toFixed(4);
  document.getElementById("poolBalance2").textContent = `${data.pool_balance.toFixed(4)} ZORO`;
  document.getElementById("holdingBalance").textContent = `${data.holding_balance} ZORO`;

  document.getElementById("profileUsername").textContent = data.username || "-";
  document.getElementById("profileWallet").textContent = data.wallet_address || "Not linked";
  document.getElementById("profilePool").textContent = data.pool_balance.toFixed(4);
  document.getElementById("profileHolding").textContent = data.holding_balance;
  document.getElementById("profileMinWithdrawal").textContent = `${data.min_withdrawal_zoro} ZORO`;
  document.getElementById("profileExchangeRate").textContent =
    `Exchange rate at distribution: ${data.zoro_to_ton_rate} ZORO = 1 TON`;

  const remaining = Math.max(0, data.min_withdrawal_zoro - data.pool_balance);
  document.getElementById("minWithdrawalHint").textContent =
    remaining > 0
      ? `You need ${remaining.toFixed(2)} more ZORO to reach the minimum withdrawal (${data.min_withdrawal_zoro})`
      : `✅ Your balance has reached the minimum withdrawal (${data.min_withdrawal_zoro} ZORO)`;

  // ---- Main action button (START / CLAIM) ----
  const btn = document.getElementById("mainActionBtn");
  const coin = document.getElementById("coinVisual");
  const hint = document.getElementById("mineHint");

  clearInterval(pendingTimer);

  if (!data.wallet_address) {
    btn.textContent = "Link your wallet first";
    btn.disabled = true;
    hint.textContent = "Tap the Connect Wallet button above";
    coin.classList.remove("mining");
  } else if (data.is_mining) {
    btn.textContent = data.session_full ? "CLAIM" : "TAP TO CLAIM EARLY";
    btn.disabled = false;
    btn.classList.add("claim");
    coin.classList.add("mining");
    hint.textContent = "Mining in progress...";

    let pending = data.pending_mined;
    document.getElementById("pendingBadge").textContent = `+${pending.toFixed(4)}`;

    // Approximate visual counter (the real amount is confirmed by the server at claim time)
    const ratePerSecond = data.mining_rate_per_hour / 3600;
    if (!data.session_full) {
      pendingTimer = setInterval(() => {
        pending += ratePerSecond;
        document.getElementById("pendingBadge").textContent = `+${pending.toFixed(4)}`;
      }, 1000);
    }
  } else {
    btn.textContent = "START";
    btn.disabled = false;
    btn.classList.remove("claim");
    coin.classList.remove("mining");
    document.getElementById("pendingBadge").textContent = "+0.0000";
    hint.textContent = `Mining rate: ${data.mining_rate_per_hour} ZORO/hour`;
  }

  // ---- Tasks ----
  const tasksList = document.getElementById("tasksList");
  tasksList.innerHTML = "";
  data.tasks.forEach((task) => {
    const div = document.createElement("div");
    div.className = "task-item";
    div.dataset.taskId = task.id;
    div.dataset.channel = task.channel;
    div.dataset.nextAvailableAt = task.next_available_at || "";
    div.innerHTML = `
      <div>
        <div class="task-title">${task.title}</div>
        <div class="task-reward">+${task.reward} Zoro • every ${task.cooldown_hours}h</div>
      </div>
      <div class="task-action"></div>
    `;
    tasksList.appendChild(div);
  });

  renderTaskButtons();
  clearInterval(taskCooldownTimer);
  taskCooldownTimer = setInterval(renderTaskButtons, 1000);

  loadSpecialTask();

  // ---- Miner (levels) ----
  document.getElementById("minerCurrentLevel").textContent = data.level;
  document.getElementById("minerMaxLevel").textContent = data.max_level;
  document.getElementById("minerCurrentRate").textContent = data.mining_rate_per_hour;

  const nextBox = document.getElementById("minerNextBox");
  const maxedMsg = document.getElementById("minerMaxedMsg");
  if (data.level >= data.max_level) {
    nextBox.classList.add("hidden");
    maxedMsg.classList.remove("hidden");
  } else {
    nextBox.classList.remove("hidden");
    maxedMsg.classList.add("hidden");
    document.getElementById("minerNextLevel").textContent = data.level + 1;
    document.getElementById("minerNextRate").textContent = data.next_level_mining_rate;
    document.getElementById("minerNextPrice").textContent = data.next_level_price_ton;
  }

  if (levelsListLoaded) renderLevelsList(data.level);

  // ---- Referral ----
  const botUsername = "zorrocoin_bot"; // ⚠️ Change this to your bot's username
  const refLink = `https://t.me/${botUsername}?startapp=${data.referral_code}`;
  document.getElementById("refLinkText").textContent = refLink;
  window._refLink = refLink;
}

let taskCooldownTimer = null;

function renderTaskButtons() {
  document.querySelectorAll(".task-item").forEach((div) => {
    const actionEl = div.querySelector(".task-action");
    const nextAt = div.dataset.nextAvailableAt;
    const now = new Date();

    if (!nextAt || now >= new Date(nextAt)) {
      actionEl.innerHTML = `<button data-task-id="${div.dataset.taskId}" data-channel="${div.dataset.channel}">Join</button>`;
      const btn = actionEl.querySelector("button");
      btn.addEventListener("click", () => handleTaskJoinClick(btn, div.dataset.taskId, div.dataset.channel, div));
    } else {
      const remaining = (new Date(nextAt) - now) / 1000;
      const h = Math.floor(remaining / 3600);
      const m = Math.floor((remaining % 3600) / 60);
      const s = Math.floor(remaining % 60);
      actionEl.innerHTML = `<span class="task-cooldown">${h}h ${m}m ${s}s</span>`;
    }
  });
}

// Step 1: tapping "Join" opens the channel link and switches the button to "Verify now".
function handleTaskJoinClick(btn, taskId, channel, itemEl) {
  const username = channel.replace("@", "").replace("https://t.me/", "");
  tg.openTelegramLink(`https://t.me/${username}`);

  btn.textContent = "Verify now ✅";
  btn.classList.add("verify-mode");
  btn.onclick = () => handleTaskVerifyClick(taskId, itemEl);
}

// Step 2: tapping "Verify now" actually checks membership via the server
// (Telegram Bot API) before granting the reward.
async function handleTaskVerifyClick(taskId, itemEl) {
  try {
    const result = await apiPost(`/api/claim-task/${taskId}`, {});
    tg.HapticFeedback?.notificationOccurred("success");
    flyCoinsToBalance(itemEl);
    await refreshState();
  } catch (e) {
    tg.HapticFeedback?.notificationOccurred("error");
    showError(e.message);
  }
}

// ---------- Main action button ----------
document.getElementById("mainActionBtn").addEventListener("click", async () => {
  try {
    if (!currentState.is_mining) {
      await apiPost("/api/mine/start", {});
    } else {
      const result = await apiPost("/api/mine/claim", {});
      playCoinSound();
      tg.HapticFeedback?.notificationOccurred("success");
    }
    await refreshState();
  } catch (e) {
    showError(e.message);
  }
});

// ---------- Referral copy ----------
document.getElementById("copyRefBtn").addEventListener("click", () => {
  navigator.clipboard.writeText(window._refLink || "");
  tg.HapticFeedback?.impactOccurred("light");
});

// ---------- Miner (levels system) ----------
let allLevels = [];
let levelsListLoaded = false;
let pendingUpgradeNonce = null;

async function fetchLevelsList() {
  const data = await apiGet("/api/levels");
  allLevels = data.levels;
  levelsListLoaded = true;
  renderLevelsList(data.current_level);
}

function renderLevelsList(currentLevel) {
  const container = document.getElementById("levelsList");
  container.innerHTML = "";
  allLevels.forEach((lvl) => {
    const row = document.createElement("div");
    row.className = "level-row" + (lvl.unlocked ? " unlocked" : "") + (lvl.level === currentLevel ? " current" : "");
    const priceLabel = lvl.upgrade_price_ton != null ? `${lvl.upgrade_price_ton} TON` : "—";
    row.innerHTML = `
      <span class="level-num">Lvl ${lvl.level}</span>
      <span class="level-rate">${lvl.mining_rate_per_hour} ZORO/h</span>
      <span class="level-price">${lvl.unlocked ? "" : priceLabel}</span>
      <span class="level-check">${lvl.unlocked ? "✅" : ""}</span>
    `;
    container.appendChild(row);
  });
}

// Builds the payload (transaction comment) as base64 BOC, which must be placed
// exactly in the transaction so the server can find it and confirm it's this upgrade.
async function buildCommentPayload(comment) {
  const cell = new TonWeb.boc.Cell();
  cell.bits.writeUint(0, 32); // op = 0 means "simple text comment"
  cell.bits.writeBytes(new TextEncoder().encode(comment));
  return TonWeb.utils.bytesToBase64(await cell.toBoc({idx: false, crc32: true}));
}

async function pollVerifyUpgrade(nonce, statusEl, attempts = 12, delayMs = 5000) {
  for (let i = 0; i < attempts; i++) {
    try {
      const result = await apiPost("/api/levels/upgrade/verify", { nonce });
      return result; // succeeded
    } catch (e) {
      statusEl.textContent = `Waiting for transaction confirmation on the network... (${i + 1}/${attempts})`;
      await new Promise((r) => setTimeout(r, delayMs));
    }
  }
  throw new Error("Couldn't confirm the payment yet. Try the 'Check payment' button again in a bit.");
}

document.getElementById("minerUpgradeBtn").addEventListener("click", async () => {
  const btn = document.getElementById("minerUpgradeBtn");
  const statusEl = document.getElementById("minerUpgradeStatus");
  statusEl.classList.remove("hidden");
  btn.disabled = true;

  try {
    let nonce = pendingUpgradeNonce;
    let treasury, amountNanoton, comment;

    if (!nonce) {
      statusEl.textContent = "Preparing upgrade request...";
      const req = await apiPost("/api/levels/upgrade/start", {});
      treasury = req.treasury_address;
      amountNanoton = req.amount_nanoton;
      comment = req.comment;
      nonce = comment.split(":").pop();
      pendingUpgradeNonce = nonce;

      statusEl.textContent = "Confirm the transaction in your wallet...";
      await tonConnectUI.sendTransaction({
        validUntil: Math.floor(Date.now() / 1000) + 600,
        messages: [
          {
            address: treasury,
            amount: String(amountNanoton),
          },
        ],
      });
    }

    statusEl.textContent = "Activating upgrade...";
    const result = await apiPost("/api/levels/upgrade/verify", { nonce });

    pendingUpgradeNonce = null;
    statusEl.textContent = `🎉 You've been upgraded to level ${result.new_level}!`;
    tg.HapticFeedback?.notificationOccurred("success");
    await refreshState();
    await fetchLevelsList();
  } catch (e) {
    statusEl.textContent = e.message || "An error occurred, try again";
    showError(e.message || "Upgrade failed");
  } finally {
    btn.disabled = false;
  }
});

// ---------- Init ----------
(async function init() {
  try {
    await refreshState();
    await fetchLevelsList();
    const refStats = await apiGet("/api/referral-stats");
    document.getElementById("refCount").textContent = refStats.referred_count;
    document.getElementById("refBonusLabel").textContent = refStats.bonus_per_referral;
    const referralsListEl = document.getElementById("referralsList");
    if (refStats.referrals && refStats.referrals.length > 0) {
      referralsListEl.innerHTML = refStats.referrals.map((r) => {
        const name = r.username ? "@" + r.username : "(no username)";
        return `<div class="stat-line" style="border-bottom:1px solid rgba(255,255,255,0.08);padding:8px 0;">
          <b>${name}</b><br>
          <span style="font-size:12px;opacity:0.7;">ID: ${r.telegram_id} | Level: ${r.level}</span>
        </div>`;
      }).join("");
    }
  } catch (e) {
    document.getElementById("loadingMsg").classList.add("hidden");
    if (e.subscriptionRequired) {
      showGateScreen(e.missingChannels);
    } else {
      showError("Could not connect to the server: " + e.message);
    }
  }
})();

// ---------- Gold coins flying from the task to the balance ----------
function flyCoinsToBalance(startEl, coinCount = 6) {
  const targetEl = document.getElementById("poolBalance");
  if (!startEl || !targetEl) return;

  const startRect = startEl.getBoundingClientRect();
  const targetRect = targetEl.getBoundingClientRect();

  for (let i = 0; i < coinCount; i++) {
    const coin = document.createElement("div");
    coin.className = "flying-coin";
    coin.textContent = "🪙";

    const jitterX = (Math.random() - 0.5) * 40;
    const jitterY = (Math.random() - 0.5) * 20;
    const startX = startRect.left + startRect.width / 2 + jitterX;
    const startY = startRect.top + startRect.height / 2 + jitterY;

    coin.style.left = `${startX}px`;
    coin.style.top = `${startY}px`;

    document.body.appendChild(coin);

    requestAnimationFrame(() => {
      setTimeout(() => {
        const dx = targetRect.left + targetRect.width / 2 - startX;
        const dy = targetRect.top + targetRect.height / 2 - startY;
        coin.style.transform = `translate(${dx}px, ${dy}px) scale(0.3)`;
        coin.classList.add("fly");
      }, i * 60);
    });

    setTimeout(() => coin.remove(), 1000 + i * 60);
  }

  setTimeout(() => {
    targetEl.classList.add("bump");
    setTimeout(() => targetEl.classList.remove("bump"), 400);
  }, 900 + coinCount * 60);
}


// ---- Gold coin particles flying from the coin ----
function spawnZoroParticles() {
  const wrap = document.querySelector(".coin-wrap");
  if (!wrap) return;
  for (let i = 0; i < 3; i++) {
    const p = document.createElement("div");
    p.className = "zoro-coin-particle";
    p.textContent = "\ud83e\ude99";
    const angle = (Math.random() * 140 - 70) * (Math.PI / 180);
    const distance = 60 + Math.random() * 50;
    const x = Math.sin(angle) * distance;
    const y = -Math.cos(angle) * distance - 40;
    p.style.setProperty("--fly-transform", `translate(${x}px, ${y}px) scale(1.1) rotate(${Math.random()*360}deg)`);
    p.style.left = "50%";
    p.style.top = "35%";
    wrap.appendChild(p);
    setTimeout(() => p.remove(), 1700);
  }
}
setInterval(() => {
  if (document.getElementById("coinVisual")?.classList.contains("mining")) {
    spawnZoroParticles();
  }
}, 900);

// ---------- Mandatory subscription screen ----------
function showGateScreen(missingChannels) {
  document.getElementById("loadingMsg").classList.add("hidden");
  document.querySelectorAll(".page").forEach((p) => p.classList.add("hidden"));
  document.querySelector(".bottom-nav")?.classList.add("hidden");
  document.querySelector(".topbar")?.classList.add("hidden");

  const gatePage = document.getElementById("page-gate");
  gatePage.classList.remove("hidden");

  const list = document.getElementById("gateChannelsList");
  list.innerHTML = "";
  missingChannels.forEach((ch) => {
    const username = ch.replace(/^@/, "").replace(/^https?:\/\/t\.me\//, "");
    const a = document.createElement("a");
    a.href = `https://t.me/${username}`;
    a.target = "_blank";
    a.className = "gate-channel-btn";
    a.textContent = `➕ Join @${username}`;
    list.appendChild(a);
  });
}

document.getElementById("gateRecheckBtn")?.addEventListener("click", async () => {
  const statusEl = document.getElementById("gateStatus");
  statusEl.classList.remove("hidden");
  statusEl.textContent = "Verifying...";
  try {
    await refreshState();
    document.getElementById("page-gate").classList.add("hidden");
    document.querySelector(".bottom-nav")?.classList.remove("hidden");
    document.querySelector(".topbar")?.classList.remove("hidden");
    statusEl.classList.add("hidden");
  } catch (e) {
    if (e.subscriptionRequired) {
      showGateScreen(e.missingChannels);
      statusEl.textContent = "You still need to join some channels ⚠️";
    } else {
      statusEl.textContent = "Verification failed: " + e.message;
    }
  }
});

// ---------- Coin sound system (enable/disable + play) ----------
const coinAudio = new Audio("assets/cha-ching.mp3");
let audioUnlocked = false;
document.addEventListener("click", function unlockAudio() {
  if (audioUnlocked) return;
  coinAudio.play().then(() => {
    coinAudio.pause();
    coinAudio.currentTime = 0;
    audioUnlocked = true;
  }).catch(() => {});
});

let soundEnabled = localStorage.getItem("zoro_sound_enabled");
soundEnabled = soundEnabled === null ? true : soundEnabled === "true";

function playCoinSound() {
  if (!soundEnabled) { return; }
  try {
    coinAudio.currentTime = 0;
    coinAudio.play().catch(() => {});
  } catch (e) {}
}

document.addEventListener("DOMContentLoaded", () => {
  const toggle = document.getElementById("soundToggle");
  if (toggle) {
    toggle.checked = soundEnabled;
    toggle.addEventListener("change", () => {
      soundEnabled = toggle.checked;
      localStorage.setItem("zoro_sound_enabled", soundEnabled);
    });
  }
});

// ---------- Special Task: video submission ----------
async function loadSpecialTask() {
  const actionEl = document.getElementById("specialTaskAction");
  if (!actionEl) return;
  try {
    const res = await apiGet("/api/my-video-task");
    renderSpecialTask(res.status, res.youtube_url);
  } catch (e) {
    renderSpecialTask(null);
  }
}

function renderSpecialTask(status, youtubeUrl) {
  const actionEl = document.getElementById("specialTaskAction");
  if (!actionEl) return;

  if (status === "pending") {
    actionEl.innerHTML = `<span class="task-cooldown">⏳ Under review</span>`;
  } else if (status === "approved") {
    actionEl.innerHTML = `<span class="task-cooldown">✅ Approved</span>`;
  } else if (status === "rejected") {
    actionEl.innerHTML = `
      <input type="text" id="specialTaskInput" placeholder="YouTube link" style="width:100%;margin-bottom:6px;">
      <button id="specialTaskSubmitBtn">Submit again</button>`;
    bindSpecialTaskSubmit();
  } else {
    actionEl.innerHTML = `
      <input type="text" id="specialTaskInput" placeholder="YouTube link" style="width:100%;margin-bottom:6px;">
      <button id="specialTaskSubmitBtn">Submit</button>`;
    bindSpecialTaskSubmit();
  }
}

function bindSpecialTaskSubmit() {
  const btn = document.getElementById("specialTaskSubmitBtn");
  if (!btn) return;
  btn.onclick = async () => {
    const input = document.getElementById("specialTaskInput");
    const url = input.value.trim();
    if (!url) return;
    btn.disabled = true;
    btn.textContent = "Submitting...";
    try {
      await apiPost("/api/submit-video-task", { youtube_url: url });
      renderSpecialTask("pending");
    } catch (e) {
      alert(e.message || "Failed to submit");
      btn.disabled = false;
      btn.textContent = "Submit";
    }
  };
}
