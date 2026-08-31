const tg = window.Telegram.WebApp;
tg.ready();
tg.expand();

// ⚠️ عدّل الرابط ده بعد ما ترفع الـ backend فعليًا
const API_BASE = "https://zorocoin-bot-production.up.railway.app";
// ⚠️ عدّل رابط المانيفست بعد النشر
const MANIFEST_URL = "https://oudjani4.github.io/zorocoin-bot/tonconnect-manifest.json";

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
    await apiPost("/api/link-wallet", { wallet_address: address });
    await refreshState();
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
    const err = await res.json().catch(() => ({ detail: "خطأ غير معروف" }));
    const detail = err.detail;
    if (detail && typeof detail === "object" && detail.error === "subscription_required") {
      const gateError = new Error(detail.message || "لازم تنضم للقنوات المطلوبة");
      gateError.subscriptionRequired = true;
      gateError.missingChannels = detail.missing_channels || [];
      throw gateError;
    }
    throw new Error(typeof detail === "string" ? detail : "خطأ في الطلب");
  }
  return res.json();
}

async function apiGet(path) {
  const res = await fetch(API_BASE + path, {
    headers: { "X-Telegram-Init-Data": initData },
  });
  if (!res.ok) throw new Error("خطأ في الطلب");
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
  // الحالة 1: المستخدم فتح رابط t.me/YourBot?startapp=CODE مباشرة
  const startParam = tg.initDataUnsafe?.start_param;
  if (startParam) return startParam;

  // الحالة 2: الكود اتحط في رابط الـ WebApp نفسه كـ ?ref=CODE (من زرار داخل شات البوت)
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

  document.getElementById("usernameLabel").textContent = data.username || "بدون اسم";
  document.getElementById("avatarInitial").textContent = (data.username || "?")[0].toUpperCase();
  document.getElementById("levelValue").textContent = data.level;

  document.getElementById("poolBalance").textContent = data.pool_balance.toFixed(4);
  document.getElementById("poolBalance2").textContent = `${data.pool_balance.toFixed(4)} ZORO`;
  document.getElementById("holdingBalance").textContent = `${data.holding_balance} ZORO`;

  document.getElementById("profileUsername").textContent = data.username || "-";
  document.getElementById("profileWallet").textContent = data.wallet_address || "غير مربوطة";
  document.getElementById("profilePool").textContent = data.pool_balance.toFixed(4);
  document.getElementById("profileHolding").textContent = data.holding_balance;
  document.getElementById("profileMinWithdrawal").textContent = `${data.min_withdrawal_zoro} ZORO`;
  document.getElementById("profileExchangeRate").textContent =
    `سعر الصرف عند التوزيع: ${data.zoro_to_ton_rate} ZORO = 1 TON`;

  const remaining = Math.max(0, data.min_withdrawal_zoro - data.pool_balance);
  document.getElementById("minWithdrawalHint").textContent =
    remaining > 0
      ? `محتاج ${remaining.toFixed(2)} ZORO كمان عشان توصل للحد الأدنى للسحب (${data.min_withdrawal_zoro})`
      : `✅ رصيدك وصل للحد الأدنى للسحب (${data.min_withdrawal_zoro} ZORO)`;

  // ---- زرار الفعل الرئيسي (START / CLAIM) ----
  const btn = document.getElementById("mainActionBtn");
  const coin = document.getElementById("coinVisual");
  const hint = document.getElementById("mineHint");

  clearInterval(pendingTimer);

  if (!data.wallet_address) {
    btn.textContent = "اربط محفظتك أولاً";
    btn.disabled = true;
    hint.textContent = "اضغط زرار Connect Wallet فوق";
    coin.classList.remove("mining");
  } else if (data.is_mining) {
    btn.textContent = data.session_full ? "CLAIM" : "TAP TO CLAIM EARLY";
    btn.disabled = false;
    btn.classList.add("claim");
    coin.classList.add("mining");
    hint.textContent = "التعدين شغال...";

    let pending = data.pending_mined;
    document.getElementById("pendingBadge").textContent = `+${pending.toFixed(4)}`;

    // عداد بصري تقريبي (العدد الحقيقي بيتأكد من السيرفر وقت الـ claim)
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
    hint.textContent = `معدل التعدين: ${data.mining_rate_per_hour} ZORO/ساعة`;
  }

  // ---- المهام ----
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
        <div class="task-reward">+${task.reward} Zoro • كل ${task.cooldown_hours} ساعة</div>
      </div>
      <div class="task-action"></div>
    `;
    tasksList.appendChild(div);
  });

  renderTaskButtons();
  clearInterval(taskCooldownTimer);
  taskCooldownTimer = setInterval(renderTaskButtons, 1000);

  // ---- Miner (المستويات) ----
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

  // ---- الإحالة ----
  const botUsername = "zorrocoin_bot"; // ⚠️ عدّله لاسم يوزر البوت بتاعك
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
      actionEl.innerHTML = `<button data-task-id="${div.dataset.taskId}" data-channel="${div.dataset.channel}">انضمام</button>`;
      const btn = actionEl.querySelector("button");
      btn.addEventListener("click", () => handleTaskJoinClick(btn, div.dataset.taskId, div.dataset.channel, div));
    } else {
      const remaining = (new Date(nextAt) - now) / 1000;
      const h = Math.floor(remaining / 3600);
      const m = Math.floor((remaining % 3600) / 60);
      const s = Math.floor(remaining % 60);
      actionEl.innerHTML = `<span class="task-cooldown">${h}س ${m}د ${s}ث</span>`;
    }
  });
}

// المرحلة 1: الضغط على "انضمام" بيفتح رابط القناة، وبيحول الزرار لـ "تحقق الآن".
function handleTaskJoinClick(btn, taskId, channel, itemEl) {
  const username = channel.replace("@", "").replace("https://t.me/", "");
  tg.openTelegramLink(`https://t.me/${username}`);

  btn.textContent = "تحقق الآن ✅";
  btn.classList.add("verify-mode");
  btn.onclick = () => handleTaskVerifyClick(taskId, itemEl);
}

// المرحلة 2: الضغط على "تحقق الآن" بيتحقق فعليًا من العضوية عبر السيرفر
// (Telegram Bot API) قبل ما يمنح المكافأة.
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

// ---------- Miner (نظام المستويات) ----------
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
      <span class="level-rate">${lvl.mining_rate_per_hour} ZORO/س</span>
      <span class="level-price">${lvl.unlocked ? "" : priceLabel}</span>
      <span class="level-check">${lvl.unlocked ? "✅" : ""}</span>
    `;
    container.appendChild(row);
  });
}

// بيبني الـ payload (تعليق المعاملة) كـ base64 BOC، لازم يتحط بالظبط في المعاملة
// عشان السيرفر يقدر يلاقيها ويتأكد إنها هي فعلاً معاملة الترقية دي.
function buildCommentPayload(comment) {
  const cell = new TonWeb.boc.Cell();
  cell.bits.writeUint(0, 32); // op = 0 يعني "تعليق نصي بسيط"
  cell.bits.writeBytes(new TextEncoder().encode(comment));
  return TonWeb.utils.bytesToBase64(cell.toBoc());
}

async function pollVerifyUpgrade(nonce, statusEl, attempts = 12, delayMs = 5000) {
  for (let i = 0; i < attempts; i++) {
    try {
      const result = await apiPost("/api/levels/upgrade/verify", { nonce });
      return result; // نجحت
    } catch (e) {
      statusEl.textContent = `بستنى تأكيد المعاملة على الشبكة... (${i + 1}/${attempts})`;
      await new Promise((r) => setTimeout(r, delayMs));
    }
  }
  throw new Error("مقدرتش أتأكد من وصول الدفع لحد دلوقتي. جرّب زرار 'تحقق من الدفع' تاني بعد شوية.");
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
      statusEl.textContent = "جاري تجهيز طلب الترقية...";
      const req = await apiPost("/api/levels/upgrade/start", {});
      treasury = req.treasury_address;
      amountNanoton = req.amount_nanoton;
      comment = req.comment;
      nonce = comment.split(":").pop();
      pendingUpgradeNonce = nonce;

      statusEl.textContent = "أكّد المعاملة في محفظتك...";
      await tonConnectUI.sendTransaction({
        validUntil: Math.floor(Date.now() / 1000) + 600,
        messages: [
          {
            address: treasury,
            amount: String(amountNanoton),
            payload: buildCommentPayload(comment),
          },
        ],
      });
    }

    statusEl.textContent = "بنتأكد من وصول الدفع على الشبكة...";
    const result = await pollVerifyUpgrade(nonce, statusEl);

    pendingUpgradeNonce = null;
    statusEl.textContent = `🎉 اتّرقيت للمستوى ${result.new_level}!`;
    tg.HapticFeedback?.notificationOccurred("success");
    await refreshState();
    await fetchLevelsList();
  } catch (e) {
    statusEl.textContent = e.message || "حصل خطأ، حاول تاني";
    showError(e.message || "فشلت الترقية");
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
  } catch (e) {
    document.getElementById("loadingMsg").classList.add("hidden");
    if (e.subscriptionRequired) {
      showGateScreen(e.missingChannels);
    } else {
      showError("تعذر الاتصال بالسيرفر: " + e.message);
    }
  }
})();

// ---------- عملات ذهبية تطير من مكان المهمة إلى الرصيد ----------
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


// ---- عملات ذهبية متطايرة من العملة ----
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

// ---------- شاشة الاشتراك الإجباري ----------
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
    a.textContent = `➕ انضم لـ @${username}`;
    list.appendChild(a);
  });
}

document.getElementById("gateRecheckBtn")?.addEventListener("click", async () => {
  const statusEl = document.getElementById("gateStatus");
  statusEl.classList.remove("hidden");
  statusEl.textContent = "جاري التحقق...";
  try {
    await refreshState();
    document.getElementById("page-gate").classList.add("hidden");
    document.querySelector(".bottom-nav")?.classList.remove("hidden");
    document.querySelector(".topbar")?.classList.remove("hidden");
    statusEl.classList.add("hidden");
  } catch (e) {
    if (e.subscriptionRequired) {
      showGateScreen(e.missingChannels);
      statusEl.textContent = "لسه ناقصك قنوات ⚠️";
    } else {
      statusEl.textContent = "تعذر التحقق: " + e.message;
    }
  }
});

// ---------- نظام صوت النقود (تفعيل/تعطيل + تشغيل) ----------
const coinAudio = new Audio("assets/cha-ching.mp3");
let soundEnabled = localStorage.getItem("zoro_sound_enabled");
soundEnabled = soundEnabled === null ? true : soundEnabled === "true";

function playCoinSound() {
  if (!soundEnabled) return;
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
