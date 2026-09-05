# دليل نشر منصة مصر على Render

رابط ثابت `https://misr-platform.onrender.com` يعمل دائمًا من أي جهاز.

الملفات جاهزة: `render.yaml` · `Procfile` · `runtime.txt` · `requirements.txt` · `.gitignore`.

---

## الخطوة 1 — رفع المشروع على GitHub

**مهم:** يجب رفع مجلد `misr-platform` نفسه كمستودع مستقل (جذر المستودع = هذا المجلد)، لأن أمر التشغيل `uvicorn backend.app:app` يفترض أن `backend/` في الجذر.

من داخل مجلد `misr-platform`:

```bash
git init
git add .
git commit -m "منصة مصر متعددة القطاعات — نسخة أولى للنشر"
git branch -M main
git remote add origin https://github.com/<اسم-حسابك>/misr-platform.git
git push -u origin main
```

(أنشئ مستودعًا فارغًا باسم `misr-platform` على GitHub أولًا، بدون README.)

---

## الخطوة 2 — إنشاء الخدمة على Render

### الطريقة (أ) — تلقائية عبر Blueprint (موصى بها)
1. ادخل [dashboard.render.com](https://dashboard.render.com) → **New** → **Blueprint**.
2. اربط مستودع `misr-platform` من GitHub.
3. Render يقرأ `render.yaml` تلقائيًا ويجهّز كل شيء (الخدمة + القرص الدائم + متغيرات البيئة).
4. اضغط **Apply**.

### الطريقة (ب) — يدوية
1. **New** → **Web Service** → اربط المستودع.
2. الإعدادات:
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn backend.app:app --host 0.0.0.0 --port $PORT`
3. أضف متغير بيئة: `DATA_DIR = /var/data`
4. أضف قرصًا دائمًا (Disk): المسار `/var/data`، الحجم `1GB`.

---

## الخطوة 3 — الوصول

بعد اكتمال النشر (دقائق قليلة) يعطيك Render الرابط:
`https://misr-platform.onrender.com`

**الحسابات التجريبية:**
| المستخدم | كلمة المرور | الدور |
|---|---|---|
| admin | admin123 | مدير (مركز القيادة + المالية) |
| doctor | doc123 | طبيب |
| pharmacy | ph123 | صيدلية |
| contractor | con123 | مقاولات |
| realestate | re123 | تسويق عقاري |

قاعدة البيانات تُنشأ تلقائيًا مع البيانات النموذجية عند أول تشغيل.

---

## ملاحظات مهمة

- **القرص الدائم يتطلب خطة مدفوعة** (Starter ≈ 7$/شهر). بدونه (الخطة المجانية) تعمل المنصة لكن **تُمسح البيانات** عند كل إعادة تشغيل أو تحديث — مناسب للتجربة فقط. لاستخدام حقيقي استخدم خطة Starter كما في `render.yaml`.
- الخطة المجانية تُنيّم الخدمة بعد 15 دقيقة خمول (أول طلب بعدها يستغرق ~30 ثانية للاستيقاظ).
- لتحديث المنصة لاحقًا: `git push` فقط — Render يعيد النشر تلقائيًا.
- **غيّر كلمات المرور الافتراضية** قبل الاستخدام الفعلي (خاصة `admin`).
