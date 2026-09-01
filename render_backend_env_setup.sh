#!/data/data/com.termux/files/usr/bin/bash
# ============================================================
# سكريبت رفع Environment Variables إلى Render
# بوت Zoro Coin - zorocoin-bot
# ============================================================

# 1) حط الـ API Key بتاعك هنا (من Render > Account Settings > API Keys)
API_KEY="rnd_d1poApZduF6xCNQioUg8HSYu3FGJ"

# 2) Service ID (متعرف بالفعل)
SERVICE_ID="srv-dab32r710e5c739nbvr0"

# ============================================================
# 3) املأ القيم هنا. سيب أي متغير مش محتاجه فاضي "" أو امسح سطره
# ============================================================

BOT_TOKEN="8765278903:AAE1ahesSOL8kg9IyAXMPappcMSF1aNkhbc"
TREASURY_WALLET_ADDRESS="UQBufh6lLHE5H1NDJXQwRIVCX-t4iKHyyoXD0Spm8N9navPx"
WEBAPP_URL="https://zoro-abel.onrender.com"
TONCENTER_BASE_URL="https://toncenter.com/api/v2"
TONCONNECT_MANIFEST_URL="https://zoro-abel.onrender.com/tonconnect-manifest.json"
ZORO_TO_TON_RATE="500"
UPGRADE_REQUEST_TTL_MINUTES="30"
REFERRAL_BONUS="25"

ADMIN_SECRET=""
API_HOST="0.0.0.0"
API_PORT="8000"
DATABASE_URL="postgresql://zoro_db_jjrr_user:X8AYIXQNRwIK0nIijYgMoaAKQGFr0ogt@dpg-dab1l82d0e5s73d3fjf0-a/zoro_db_jjrr"
DISTRIBUTION_MAX_RETRIES="3"
DISTRIBUTION_TESTNET="false"
DISTRIBUTION_TX_DELAY_SECONDS="4"
DISTRIBUTION_WALLET_MNEMONIC="warfare army client bracket zone island tenant arrow street zero ahead health release asset vintage ribbon broken exclude tray security flash crucial dog entire"
JETTON_DECIMALS="9"
LEVEL_BASE_PRICE_TON="1.0"
LEVEL_MINING_RATE_INCREMENT="10"
LEVEL_PRICE_INCREMENT_TON="0.5"
MAX_LEVEL="100"
MAX_SESSION_HOURS="3"
MIN_WITHDRAWAL_TON="0.5"
MINING_RATE_PER_HOUR="10"
REQUIRED_CHANNELS="@zorocoinchat,@SmartEarnAr"

# ============================================================
# مفيش داعي تعدل تحت الخط ده
# ============================================================

if [ "$API_KEY" = "rnd_ضع_المفتاح_هنا" ]; then
  echo "❌ لازم تحط الـ API Key الحقيقي في السكريبت الأول."
  exit 1
fi

# بناء الـ JSON تلقائياً من المتغيرات المكتوبة فوق
JSON="["
FIRST=true
add_var() {
  local key="$1"
  local val="$2"
  if [ -n "$val" ]; then
    if [ "$FIRST" = true ]; then
      FIRST=false
    else
      JSON="$JSON,"
    fi
    # escape للـ quotes لو موجودة
    val_escaped=$(echo "$val" | sed 's/"/\\"/g')
    JSON="$JSON{\"key\":\"$key\",\"value\":\"$val_escaped\"}"
  fi
}

add_var "BOT_TOKEN" "$BOT_TOKEN"
add_var "TREASURY_WALLET_ADDRESS" "$TREASURY_WALLET_ADDRESS"
add_var "WEBAPP_URL" "$WEBAPP_URL"
add_var "TONCENTER_BASE_URL" "$TONCENTER_BASE_URL"
add_var "TONCONNECT_MANIFEST_URL" "$TONCONNECT_MANIFEST_URL"
add_var "ZORO_TO_TON_RATE" "$ZORO_TO_TON_RATE"
add_var "UPGRADE_REQUEST_TTL_MINUTES" "$UPGRADE_REQUEST_TTL_MINUTES"
add_var "REFERRAL_BONUS" "$REFERRAL_BONUS"
add_var "ADMIN_SECRET" "$ADMIN_SECRET"
add_var "API_HOST" "$API_HOST"
add_var "API_PORT" "$API_PORT"
add_var "DATABASE_URL" "$DATABASE_URL"
add_var "DISTRIBUTION_MAX_RETRIES" "$DISTRIBUTION_MAX_RETRIES"
add_var "DISTRIBUTION_TESTNET" "$DISTRIBUTION_TESTNET"
add_var "DISTRIBUTION_TX_DELAY_SECONDS" "$DISTRIBUTION_TX_DELAY_SECONDS"
add_var "DISTRIBUTION_WALLET_MNEMONIC" "$DISTRIBUTION_WALLET_MNEMONIC"
add_var "JETTON_DECIMALS" "$JETTON_DECIMALS"
add_var "LEVEL_BASE_PRICE_TON" "$LEVEL_BASE_PRICE_TON"
add_var "LEVEL_MINING_RATE_INCREMENT" "$LEVEL_MINING_RATE_INCREMENT"
add_var "LEVEL_PRICE_INCREMENT_TON" "$LEVEL_PRICE_INCREMENT_TON"
add_var "MAX_LEVEL" "$MAX_LEVEL"
add_var "MAX_SESSION_HOURS" "$MAX_SESSION_HOURS"
add_var "MIN_WITHDRAWAL_TON" "$MIN_WITHDRAWAL_TON"
add_var "MINING_RATE_PER_HOUR" "$MINING_RATE_PER_HOUR"
add_var "REQUIRED_CHANNELS" "$REQUIRED_CHANNELS"

JSON="$JSON]"

echo "🔄 جاري رفع المتغيرات إلى Render..."

RESPONSE=$(curl -s -w "\n%{http_code}" -X PUT \
  "https://api.render.com/v1/services/$SERVICE_ID/env-vars" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d "$JSON")

HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "200" ]; then
  echo "✅ تم رفع المتغيرات بنجاح. الخدمة هتعمل redeploy تلقائي."
else
  echo "❌ حصل خطأ (HTTP $HTTP_CODE):"
  echo "$BODY"
fi
