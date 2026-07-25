# 🛡️ Kimi Cloudflare Worker Proxy

## چرا این پروکسی لازمه؟

سرور Kimi (`inference.dahl.global`) پشت **Cloudflare Bot Fight Mode** قرار داره. Cloudflare محدوده‌ی IP های GitHub Actions رو مشکوک می‌بینه و به جای پاسخ API، صفحه‌ی **"Just a moment..."** با **HTTP 403** برمی‌گردونه.

این محدودیت **IP-based** هست، نه TLS/UA-based:
- ❌ تغییر User-Agent فایده نداره
- ❌ حتی TLS fingerprint یه مرورگر واقعی (curl_cffi + Chrome impersonation) هم دور نمی‌زنه
- ✅ راه واقعی: درخواست‌ها از یه IP سالم (که خودش داخل شبکه Cloudflare هست) بره

## راه‌حل: Cloudflare Worker

Worker خودش داخل شبکه‌ی Cloudflare اجرا میشه. درخواست‌های خروجیش از IP های edge خود Cloudflare خارج میشن و در نتیجه با هیچ challenge ای مواجه نمی‌شن.

---

## ⚙️ نصب — روش ۱ (ساده‌ترین راه، از توی داشبورد)

**زمان کل: حدود ۲ دقیقه**

### گام ۱: ساخت اکانت Cloudflare

اگه اکانت نداری، رایگان بساز: <https://dash.cloudflare.com/sign-up>

### گام ۲: ساخت Worker

1. برو به: <https://dash.cloudflare.com/>
2. از منوی سمت چپ: **Workers & Pages** → **Create** → **Create Worker**
3. یه اسم بذار (مثلاً `kimi-proxy`) → **Deploy**
4. بعد از deploy، روی **Edit code** کلیک کن
5. کل محتوای فایل [`worker.js`](./worker.js) رو کپی کن و به جای کد پیش‌فرض بچسبون
6. **Save and deploy**

### گام ۳: گرفتن آدرس Worker

Cloudflare بهت یه آدرس میده مثل:

```
https://kimi-proxy.<your-subdomain>.workers.dev
```

تستش کن:

```bash
curl https://kimi-proxy.<your-subdomain>.workers.dev/
# باید برگردونه: {"ok":true,"proxy_for":"https://inference.dahl.global"}
```

### گام ۴: تنظیم Secret تو ریپو

توی ریپوی گیت‌هابت:

- **Settings** → **Secrets and variables** → **Actions**
- Secret به نام `KIMI_BASE_URL` رو **ویرایش** کن (یا اگه نیست، بساز)
- مقدارش رو بذار (توجه: `/v1` رو حتماً بذار):

```
https://kimi-proxy.<your-subdomain>.workers.dev/v1
```

### گام ۵: اجرای مجدد سرور

- **Actions** → **Run Kimi Chat Server** → **Run workflow**
- منتظر بمون تا آدرس Cloudflare Tunnel بیاد
- 🎉 چت الان کار می‌کنه!

---

## ⚙️ نصب — روش ۲ (با wrangler CLI، برای برنامه‌نویسا)

```bash
cd proxy
npm install -g wrangler
npx wrangler login        # مرورگر رو باز می‌کنه برای احراز هویت
npx wrangler deploy       # آدرس worker رو چاپ می‌کنه
```

بعدش گام ۴ و ۵ از روش ۱ رو انجام بده.

---

## ✅ چطور تست کنم کار می‌کنه؟

بعد از اجرای workflow، endpoint دیباگ رو صدا بزن:

```bash
curl https://<your-tunnel>.trycloudflare.com/api/debug/ping
```

باید بگردونه:

```json
{"ok": true, "model": "moonshotai/Kimi-K2.6", "reply": " ...Pong..."}
```

اگه `"ok": true` دیدی، همه چیز راست و ریسته ✅

---

## 📊 محدودیت‌ها

- **Cloudflare Workers Free plan**: 100,000 request/day — خیلی بیشتر از نیاز این پروژه
- **CPU time**: 10ms per request — پروکسی خالص کاری نمی‌کنه، پس مشکلی نیست
- **Streaming (SSE)**: ✅ پشتیبانی می‌شه، Worker استریم رو دست‌نخورده forward می‌کنه
