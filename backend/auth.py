"""
التحقق من صحة initData اللي بيبعتها تطبيق تليجرام المصغّر (Mini App).
ده أهم خطوة أمان: من غيرها أي حد يقدر يدّعي إنه أي مستخدم ويسرق نقاطه.
مرجع طريقة التحقق الرسمية:
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# أقصى عمر مسموح به لبيانات initData (بالثواني) قبل ما تعتبر منتهية الصلاحية
MAX_INIT_DATA_AGE = 3600 * 12  # 12 ساعة


def verify_telegram_init_data(init_data: str) -> dict:
    """
    يتحقق من التوقيع ويرجع بيانات المستخدم (dict) لو صحيحة.
    يرمي استثناء ValueError لو البيانات مزورة أو منتهية.
    """
    if not init_data:
        raise ValueError("initData مفقودة")

    parsed = dict(parse_qsl(init_data, strict_parsing=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise ValueError("hash مفقود")

    # بناء data_check_string حسب توثيق تليجرام
    data_check_arr = sorted(f"{k}={v}" for k, v in parsed.items())
    data_check_string = "\n".join(data_check_arr)

    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise ValueError("توقيع البيانات غير صحيح - محاولة تزوير محتملة")

    auth_date = int(parsed.get("auth_date", 0))
    if time.time() - auth_date > MAX_INIT_DATA_AGE:
        raise ValueError("بيانات الجلسة منتهية الصلاحية، افتح التطبيق من جديد")

    user_raw = parsed.get("user")
    if not user_raw:
        raise ValueError("بيانات المستخدم مفقودة")

    return json.loads(user_raw)


async def get_current_telegram_user(x_telegram_init_data: str = Header(...)) -> dict:
    """Dependency لـ FastAPI: يُستخدم في أي endpoint محتاج التأكد من هوية المستخدم."""
    try:
        return verify_telegram_init_data(x_telegram_init_data)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
