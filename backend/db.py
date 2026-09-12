"""قاعدة بيانات SQLite لمنصة مصر متعددة القطاعات.

المعمارية: نواة مشتركة (core) تخدم كل القطاعات + وحدات (modules) لكل قطاع.
القطاع الرائد المُنفَّذ بالكامل هنا: القطاع الطبي (سلسلة متكاملة).
باقي القطاعات: جداول أساسية جاهزة للتوسّع (مقاولات، عقاري، تسويق، تنقل، لوجستيات، زراعة، محاماة).
"""
import sqlite3
import os
import hashlib
import secrets
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
DB_PATH = os.path.join(DATA_DIR, "misr_platform.db")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ============================================================================
# النواة المشتركة (Core) — تخدم كل القطاعات
# ============================================================================
CORE_SCHEMA = """
-- المستخدمون والصلاحيات (RBAC)
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    full_name TEXT,
    role TEXT NOT NULL DEFAULT 'user',   -- admin / manager / user / doctor / reception / pharmacist / lab / radiology
    sector TEXT,                          -- القطاع الأساسي للمستخدم
    phone TEXT,
    email TEXT,
    permissions TEXT,                     -- قائمة صلاحيات مفصولة بفواصل
    active INTEGER DEFAULT 1,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    action TEXT,
    method TEXT,
    path TEXT,
    at TEXT
);

-- الكيانات (شركات/منشآت) — تخدم كل القطاعات
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sector TEXT NOT NULL,                 -- medical / contracting / realestate / ...
    kind TEXT,                            -- clinic / pharmacy / lab / contractor / developer / agency ...
    name TEXT NOT NULL,
    tax_number TEXT,                      -- الرقم الضريبي / البطاقة الضريبية
    commercial_reg TEXT,                  -- السجل التجاري
    contact_person TEXT,
    phone TEXT,
    email TEXT,
    governorate TEXT,                     -- المحافظة (مصر)
    address TEXT,
    rating REAL DEFAULT 0,                -- متوسط التقييم
    rating_count INTEGER DEFAULT 0,
    verified INTEGER DEFAULT 0,           -- موثّق (أوراق رسمية مكتملة)
    notes TEXT,
    created_at TEXT
);

-- الأوراق الرسمية والتصاريح — تخدم كل القطاعات (رفع ملفات فعلي)
CREATE TABLE IF NOT EXISTS official_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sector TEXT,
    owner_type TEXT,                      -- entity / person / project / property
    owner_id INTEGER,
    doc_type TEXT,                        -- رخصة / تصريح / سجل تجاري / بطاقة ضريبية / شهادة / عقد
    ref_no TEXT,
    title TEXT,
    issuer TEXT,                          -- الجهة المُصدِرة
    issue_date TEXT,
    expiry_date TEXT,
    status TEXT DEFAULT 'ساري',           -- ساري / منتهي / تحت الإصدار
    file_path TEXT,                       -- مسار الملف المرفوع
    notes TEXT,
    created_at TEXT
);

-- المرفقات العامة
CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_type TEXT,
    owner_id INTEGER,
    file_name TEXT,
    file_path TEXT,
    uploaded_by INTEGER,
    created_at TEXT
);

-- التقييمات والمراجعات — للأطباء/الصنايعية/المقاولين/الكيانات
CREATE TABLE IF NOT EXISTS ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT,                     -- doctor / entity / contractor / worker
    target_id INTEGER,
    rater_name TEXT,
    stars INTEGER,                        -- 1..5
    comment TEXT,
    created_at TEXT
);

-- المدفوعات والفواتير — in (وارد) / out (صادر)
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sector TEXT,
    direction TEXT,                       -- in / out
    ref_type TEXT,                        -- appointment / prescription / project / order ...
    ref_id INTEGER,
    payer TEXT,
    amount REAL DEFAULT 0,
    method TEXT,                          -- نقدي / تحويل / فيزا / محفظة
    status TEXT DEFAULT 'مدفوع',
    paid_at TEXT,
    notes TEXT,
    created_at TEXT
);

-- الإشعارات
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    channel TEXT,                         -- system / sms / whatsapp
    title TEXT,
    body TEXT,
    seen INTEGER DEFAULT 0,
    created_at TEXT
);
"""

# ============================================================================
# وحدة القطاع الطبي (Medical Module) — السلسلة المتكاملة
# ============================================================================
MEDICAL_SCHEMA = """
-- التخصصات الطبية
CREATE TABLE IF NOT EXISTS med_specialties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT
);

-- الأطباء (مرتبط بتخصص وعيادة/كيان)
CREATE TABLE IF NOT EXISTS med_doctors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,                      -- حساب المستخدم إن وُجد
    full_name TEXT NOT NULL,
    specialty_id INTEGER,
    clinic_id INTEGER,                    -- entities.id (kind=clinic)
    license_no TEXT,                      -- رقم مزاولة المهنة
    phone TEXT,
    fee REAL DEFAULT 0,                   -- سعر الكشف
    bio TEXT,
    rating REAL DEFAULT 0,
    rating_count INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1,
    created_at TEXT,
    FOREIGN KEY (specialty_id) REFERENCES med_specialties(id)
);

-- المرضى + بيانات السجل الأساسية
CREATE TABLE IF NOT EXISTS med_patients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    national_id TEXT,                     -- الرقم القومي
    full_name TEXT NOT NULL,
    gender TEXT,                          -- ذكر / أنثى
    birth_date TEXT,
    phone TEXT,
    governorate TEXT,
    address TEXT,
    blood_type TEXT,                      -- فصيلة الدم
    chronic_diseases TEXT,                -- أمراض مزمنة
    allergies TEXT,                       -- حساسية من أدوية
    notes TEXT,
    created_at TEXT
);

-- الحجوزات / المواعيد
CREATE TABLE IF NOT EXISTS med_appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    doctor_id INTEGER NOT NULL,
    clinic_id INTEGER,
    scheduled_at TEXT,                    -- موعد الحجز
    status TEXT DEFAULT 'محجوز',          -- محجوز / بالانتظار / قيد الكشف / مكتمل / ملغي
    complaint TEXT,                       -- الشكوى المبدئية
    fee REAL DEFAULT 0,
    created_at TEXT,
    FOREIGN KEY (patient_id) REFERENCES med_patients(id),
    FOREIGN KEY (doctor_id) REFERENCES med_doctors(id)
);

-- الزيارة (Encounter) — العمود الفقري للسلسلة يربط كل شيء
CREATE TABLE IF NOT EXISTS med_encounters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id INTEGER,
    patient_id INTEGER NOT NULL,
    doctor_id INTEGER NOT NULL,
    started_at TEXT,
    -- علامات حيوية
    vitals_bp TEXT,                       -- ضغط الدم
    vitals_temp TEXT,                     -- الحرارة
    vitals_pulse TEXT,                    -- النبض
    vitals_weight TEXT,                   -- الوزن
    examination TEXT,                     -- الفحص السريري
    status TEXT DEFAULT 'مفتوحة',         -- مفتوحة / مكتملة
    closed_at TEXT,
    created_at TEXT,
    FOREIGN KEY (patient_id) REFERENCES med_patients(id),
    FOREIGN KEY (doctor_id) REFERENCES med_doctors(id)
);

-- التشخيصات
CREATE TABLE IF NOT EXISTS med_diagnoses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    encounter_id INTEGER NOT NULL,
    diagnosis TEXT,                       -- التشخيص
    icd_code TEXT,                        -- كود ICD اختياري
    severity TEXT,                        -- بسيط / متوسط / حرج
    notes TEXT,
    created_at TEXT,
    FOREIGN KEY (encounter_id) REFERENCES med_encounters(id)
);

-- طلبات الأشعة (تُرسل لمركز الأشعة)
CREATE TABLE IF NOT EXISTS med_radiology_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    encounter_id INTEGER NOT NULL,
    center_id INTEGER,                    -- entities.id (kind=radiology)
    exam_type TEXT,                       -- نوع الأشعة (رنين/مقطعية/سينية/سونار)
    body_part TEXT,
    status TEXT DEFAULT 'مطلوب',          -- مطلوب / تم التنفيذ / النتيجة جاهزة
    result_text TEXT,
    result_file TEXT,
    ordered_at TEXT,
    result_at TEXT,
    FOREIGN KEY (encounter_id) REFERENCES med_encounters(id)
);

-- طلبات التحاليل (تُرسل لمركز التحاليل)
CREATE TABLE IF NOT EXISTS med_lab_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    encounter_id INTEGER NOT NULL,
    center_id INTEGER,                    -- entities.id (kind=lab)
    test_name TEXT,                       -- اسم التحليل
    status TEXT DEFAULT 'مطلوب',          -- مطلوب / سُحبت العينة / النتيجة جاهزة
    result_value TEXT,
    reference_range TEXT,
    result_file TEXT,
    ordered_at TEXT,
    result_at TEXT,
    FOREIGN KEY (encounter_id) REFERENCES med_encounters(id)
);

-- الروشتة (وصفة العلاج) — تُرسل تلقائيًا للصيدلية
CREATE TABLE IF NOT EXISTS med_prescriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    encounter_id INTEGER NOT NULL,
    patient_id INTEGER NOT NULL,
    doctor_id INTEGER NOT NULL,
    pharmacy_id INTEGER,                  -- entities.id (kind=pharmacy) — الصيدلية المستلمة
    status TEXT DEFAULT 'مُرسلة',         -- مُرسلة / تم الصرف / مرفوضة
    dispensed_at TEXT,
    notes TEXT,
    created_at TEXT,
    FOREIGN KEY (encounter_id) REFERENCES med_encounters(id),
    FOREIGN KEY (patient_id) REFERENCES med_patients(id)
);

CREATE TABLE IF NOT EXISTS med_prescription_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prescription_id INTEGER NOT NULL,
    drug_name TEXT,                       -- اسم الدواء
    dose TEXT,                            -- الجرعة
    frequency TEXT,                       -- عدد المرات
    duration TEXT,                        -- المدة
    instructions TEXT,                    -- تعليمات
    dispensed INTEGER DEFAULT 0,          -- تم صرفه من الصيدلية
    FOREIGN KEY (prescription_id) REFERENCES med_prescriptions(id)
);

-- العمليات الجراحية
CREATE TABLE IF NOT EXISTS med_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    doctor_id INTEGER,
    encounter_id INTEGER,
    operation_name TEXT,
    scheduled_at TEXT,
    status TEXT DEFAULT 'مجدولة',         -- مجدولة / تمت / ملغاة
    hospital_id INTEGER,
    outcome TEXT,
    notes TEXT,
    created_at TEXT,
    FOREIGN KEY (patient_id) REFERENCES med_patients(id)
);
"""

# ============================================================================
# جداول أساسية لباقي القطاعات (جاهزة للتوسّع)
# ============================================================================
OTHER_SECTORS_SCHEMA = """
-- قطاع المقاولات: صنايعية/عمالة فنية
CREATE TABLE IF NOT EXISTS con_workers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    trade TEXT,                           -- التخصص: نقاش/سباك/كهربائي/نجار/محارة...
    phone TEXT,
    governorate TEXT,
    daily_rate REAL DEFAULT 0,
    rating REAL DEFAULT 0,
    rating_count INTEGER DEFAULT 0,
    available INTEGER DEFAULT 1,
    notes TEXT,
    created_at TEXT
);

-- قطاع المقاولات: المشاريع/أوامر الشغل
CREATE TABLE IF NOT EXISTS con_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    work_type TEXT,                       -- بناء/تشطيب/ديكور/أثاث/هدم/نظافة
    client_id INTEGER,
    contractor_id INTEGER,                -- entities.id (kind=contractor)
    governorate TEXT,
    budget REAL DEFAULT 0,
    progress INTEGER DEFAULT 0,
    status TEXT DEFAULT 'جديد',
    start_date TEXT,
    end_date TEXT,
    notes TEXT,
    created_at TEXT
);

-- قطاع التطوير العقاري: المشاريع
CREATE TABLE IF NOT EXISTS re_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    developer_id INTEGER,                 -- entities.id (kind=developer)
    governorate TEXT,
    project_type TEXT,                    -- سكني/تجاري/إداري/ساحلي
    units_total INTEGER DEFAULT 0,
    units_sold INTEGER DEFAULT 0,
    status TEXT DEFAULT 'قيد التطوير',
    delivery_date TEXT,
    notes TEXT,
    created_at TEXT
);

-- قطاع التطوير/التسويق العقاري: الوحدات
CREATE TABLE IF NOT EXISTS re_units (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    unit_code TEXT,
    unit_type TEXT,                       -- شقة/فيلا/محل/مكتب
    area REAL DEFAULT 0,
    price REAL DEFAULT 0,
    status TEXT DEFAULT 'متاح',           -- متاح/محجوز/مباع
    listing_channel TEXT,                 -- قناة التسويق
    notes TEXT,
    created_at TEXT
);

-- قطاع التسويق العقاري/العام: العملاء المحتملون (Leads)
CREATE TABLE IF NOT EXISTS mkt_leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sector TEXT,                          -- realestate / general
    name TEXT,
    phone TEXT,
    source TEXT,                          -- فيسبوك/جوجل/إحالة
    interest TEXT,
    stage TEXT DEFAULT 'جديد',            -- جديد/تواصل/مهتم/تفاوض/تم/خسارة
    assigned_to INTEGER,
    unit_id INTEGER,
    notes TEXT,
    created_at TEXT
);

-- قطاع التنقل: الرحلات
CREATE TABLE IF NOT EXISTS mob_trips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    passenger_name TEXT,
    driver_name TEXT,
    from_loc TEXT,
    to_loc TEXT,
    fare REAL DEFAULT 0,
    status TEXT DEFAULT 'مطلوبة',
    requested_at TEXT,
    created_at TEXT
);

-- قطاع الشحن واللوجستيات: الشحنات
CREATE TABLE IF NOT EXISTS log_shipments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracking_no TEXT,
    sender TEXT,
    receiver TEXT,
    from_gov TEXT,
    to_gov TEXT,
    weight REAL DEFAULT 0,
    cost REAL DEFAULT 0,
    status TEXT DEFAULT 'قيد التجهيز',    -- قيد التجهيز/في الطريق/تم التسليم
    created_at TEXT
);

-- قطاع الزراعة: المزارع/المحاصيل
CREATE TABLE IF NOT EXISTS agr_farms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_name TEXT,
    governorate TEXT,
    area_feddan REAL DEFAULT 0,           -- المساحة بالفدان
    crop TEXT,                            -- المحصول
    season TEXT,
    status TEXT DEFAULT 'قيد الزراعة',
    notes TEXT,
    created_at TEXT
);

-- قطاع المحاماة: القضايا
CREATE TABLE IF NOT EXISTS law_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_no TEXT,
    client_name TEXT,
    lawyer_id INTEGER,
    case_type TEXT,                       -- مدني/جنائي/تجاري/أحوال شخصية
    court TEXT,
    status TEXT DEFAULT 'مفتوحة',
    next_session TEXT,                    -- الجلسة القادمة
    notes TEXT,
    created_at TEXT
);
"""


# ============================================================================
# طبقة التكامل (Integration) — بوابة الطلبات الموحّدة عبر كل القطاعات
# ============================================================================
INTEGRATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS service_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sector TEXT NOT NULL,                 -- القطاع المستهدف
    service_type TEXT,                    -- نوع الخدمة المطلوبة
    requester_name TEXT NOT NULL,         -- مقدّم الطلب
    requester_phone TEXT,
    governorate TEXT,
    details TEXT,
    priority TEXT DEFAULT 'عادي',         -- عادي / عاجل
    status TEXT DEFAULT 'جديد',           -- جديد / قيد المعالجة / محوّل / مكتمل / ملغي
    assigned_to INTEGER,                  -- المستخدم المسؤول
    linked_type TEXT,                     -- نوع السجل الناتج (appointment/trip/shipment...)
    linked_id INTEGER,                    -- معرّف السجل الناتج بعد التحويل
    created_at TEXT,
    updated_at TEXT
);
"""


def _hash_pw(password, salt):
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def make_token():
    return secrets.token_hex(24)


def _add_column(conn, table, col, decl):
    """إضافة عمود بشكل آمن (idempotent) للترقية دون فقدان بيانات."""
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def _migrate(conn):
    # تصنيف العمالة: صنايعي / مهندس / مقاول-فرد
    _add_column(conn, "con_workers", "category", "TEXT DEFAULT 'صنايعي'")
    _add_column(conn, "con_workers", "experience_years", "INTEGER DEFAULT 0")
    # اسم العميل الحر لأوامر الشغل + نوع العمل الفرعي
    _add_column(conn, "con_projects", "client_name", "TEXT")
    _add_column(conn, "con_projects", "client_phone", "TEXT")
    conn.commit()


def init_db():
    conn = get_conn()
    conn.executescript(CORE_SCHEMA)
    conn.executescript(MEDICAL_SCHEMA)
    conn.executescript(OTHER_SECTORS_SCHEMA)
    conn.executescript(INTEGRATION_SCHEMA)
    conn.commit()
    _migrate(conn)
    _seed(conn)
    conn.close()


def _seed(conn):
    now = datetime.now().isoformat(timespec="seconds")
    # مستخدمون افتراضيون (يُضاف الناقص منهم فقط — idempotent)
    for username, pw, name, role, sector in [
        ("admin", "admin123", "مدير النظام", "admin", None),
        ("doctor", "doc123", "د. أحمد محمود", "doctor", "medical"),
        ("reception", "rec123", "استقبال العيادة", "reception", "medical"),
        ("pharmacy", "ph123", "صيدلية النور", "pharmacist", "medical"),
        ("contractor", "con123", "مكتب المقاولات", "contractor", "contracting"),
        ("realestate", "re123", "مكتب التسويق العقاري", "agent", "realestate"),
    ]:
        exists = conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone()
        if not exists:
            salt = secrets.token_hex(8)
            conn.execute(
                "INSERT INTO users (username,password_hash,salt,full_name,role,sector,active,created_at) VALUES (?,?,?,?,?,?,1,?)",
                (username, _hash_pw(pw, salt), salt, name, role, sector, now))

    # تخصصات طبية
    cur = conn.execute("SELECT COUNT(*) c FROM med_specialties")
    if cur.fetchone()["c"] == 0:
        for s in ["باطنة", "أطفال", "عظام", "قلب وأوعية", "جلدية", "نساء وتوليد",
                  "أنف وأذن", "عيون", "مخ وأعصاب", "جراحة عامة", "أسنان", "صدر"]:
            conn.execute("INSERT INTO med_specialties (name) VALUES (?)", (s,))

    # كيانات طبية (عيادة/صيدلية/أشعة/تحاليل)
    cur = conn.execute("SELECT COUNT(*) c FROM entities WHERE sector='medical'")
    if cur.fetchone()["c"] == 0:
        med_entities = [
            ("clinic", "عيادات النيل التخصصية", "القاهرة", 1),
            ("pharmacy", "صيدلية النور", "القاهرة", 1),
            ("pharmacy", "صيدلية الشفاء", "الجيزة", 1),
            ("radiology", "مركز الأشعة المتقدم", "القاهرة", 1),
            ("lab", "معامل البرج للتحاليل", "القاهرة", 1),
        ]
        for kind, name, gov, verified in med_entities:
            conn.execute(
                "INSERT INTO entities (sector,kind,name,governorate,verified,created_at) VALUES ('medical',?,?,?,?,?)",
                (kind, name, gov, verified, now))

    # أطباء
    cur = conn.execute("SELECT COUNT(*) c FROM med_doctors")
    if cur.fetchone()["c"] == 0:
        clinic = conn.execute("SELECT id FROM entities WHERE kind='clinic' LIMIT 1").fetchone()
        clinic_id = clinic["id"] if clinic else None
        doctors = [
            ("د. أحمد محمود", "باطنة", 300, 4.7, 120),
            ("د. سارة عبد الله", "أطفال", 250, 4.9, 210),
            ("د. خالد فؤاد", "عظام", 400, 4.5, 88),
            ("د. منى إبراهيم", "جلدية", 350, 4.8, 156),
        ]
        for name, spec, fee, rating, rc in doctors:
            sp = conn.execute("SELECT id FROM med_specialties WHERE name=?", (spec,)).fetchone()
            conn.execute(
                """INSERT INTO med_doctors (full_name,specialty_id,clinic_id,fee,rating,rating_count,active,created_at)
                   VALUES (?,?,?,?,?,?,1,?)""",
                (name, sp["id"] if sp else None, clinic_id, fee, rating, rc, now))

    # مرضى نموذجيون
    cur = conn.execute("SELECT COUNT(*) c FROM med_patients")
    if cur.fetchone()["c"] == 0:
        patients = [
            ("29801011234567", "محمد علي حسن", "ذكر", "1998-01-01", "01001234567", "القاهرة", "A+", "", ""),
            ("28503051234567", "فاطمة سيد أحمد", "أنثى", "1985-03-05", "01112345678", "الجيزة", "O+", "سكري", "بنسلين"),
        ]
        for nid, name, g, bd, ph, gov, blood, chronic, allergy in patients:
            conn.execute(
                """INSERT INTO med_patients (national_id,full_name,gender,birth_date,phone,governorate,blood_type,chronic_diseases,allergies,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (nid, name, g, bd, ph, gov, blood, chronic, allergy, now))

    # ---- بيانات قطاع المقاولات النموذجية ----
    cur = conn.execute("SELECT COUNT(*) c FROM entities WHERE sector='contracting'")
    if cur.fetchone()["c"] == 0:
        con_entities = [
            ("contractor", "شركة البناء الحديث للمقاولات", "القاهرة", 1),
            ("contractor", "مؤسسة الإتقان للتشطيبات", "الجيزة", 1),
            ("supplier", "معرض النور للأثاث والمفروشات", "القاهرة", 1),
            ("supplier", "توريدات مواد البناء - العز", "القليوبية", 0),
        ]
        for kind, name, gov, verified in con_entities:
            conn.execute(
                "INSERT INTO entities (sector,kind,name,governorate,verified,created_at) VALUES ('contracting',?,?,?,?,?)",
                (kind, name, gov, verified, now))

    cur = conn.execute("SELECT COUNT(*) c FROM con_workers")
    if cur.fetchone()["c"] == 0:
        workers = [
            # (الاسم, التصنيف, التخصص, الهاتف, المحافظة, اليومية, تقييم, عدد, خبرة)
            ("معلم رمضان عبد الله", "صنايعي", "محارة ومباني", "01001112222", "القاهرة", 700, 4.6, 34, 18),
            ("أسطى محمود نجار", "صنايعي", "نجارة وأثاث", "01002223333", "الجيزة", 800, 4.8, 51, 22),
            ("عم صابر السباك", "صنايعي", "سباكة", "01003334444", "القاهرة", 600, 4.4, 27, 15),
            ("كهربائي وليد", "صنايعي", "كهرباء", "01004445555", "القليوبية", 650, 4.7, 40, 12),
            ("نقاش حسام", "صنايعي", "نقاشة وديكور", "01005556666", "الجيزة", 550, 4.3, 19, 9),
            ("م. تامر شحاتة", "مهندس", "مهندس مدني", "01006667777", "القاهرة", 2000, 4.9, 12, 14),
            ("م. دينا فؤاد", "مهندس", "مهندس ديكور", "01007778888", "القاهرة", 1800, 4.8, 8, 7),
        ]
        for name, cat, trade, phone, gov, rate, rating, rc, exp in workers:
            conn.execute(
                """INSERT INTO con_workers (full_name,category,trade,phone,governorate,daily_rate,rating,rating_count,experience_years,available,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,1,?)""",
                (name, cat, trade, phone, gov, rate, rating, rc, exp, now))

    cur = conn.execute("SELECT COUNT(*) c FROM con_projects")
    if cur.fetchone()["c"] == 0:
        contractor = conn.execute("SELECT id FROM entities WHERE sector='contracting' AND kind='contractor' LIMIT 1").fetchone()
        cid = contractor["id"] if contractor else None
        projects = [
            ("تشطيب شقة 180م بالتجمع", "تشطيبات", "أحمد سمير", "01010101010", "القاهرة", 350000, 45, "قيد التنفيذ"),
            ("بناء فيلا من الصفر - الشيخ زايد", "بناء من الصفر", "شركة أمان", "01020202020", "الجيزة", 2500000, 20, "قيد التنفيذ"),
            ("ديكور معرض تجاري", "ديكورات", "مؤسسة رؤية", "01030303030", "القاهرة", 180000, 100, "مكتمل"),
            ("أعمال هدم وتكسير", "هدم", "مالك العقار", "01040404040", "القليوبية", 60000, 0, "جديد"),
        ]
        for title, wtype, client, cphone, gov, budget, progress, status in projects:
            conn.execute(
                """INSERT INTO con_projects (title,work_type,client_name,client_phone,contractor_id,governorate,budget,progress,status,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (title, wtype, client, cphone, cid, gov, budget, progress, status, now))

    # ---- بيانات القطاع العقاري النموذجية ----
    cur = conn.execute("SELECT COUNT(*) c FROM entities WHERE sector='realestate'")
    if cur.fetchone()["c"] == 0:
        for kind, name, gov, verified in [
            ("developer", "مجموعة النخبة للتطوير العقاري", "القاهرة الجديدة", 1),
            ("developer", "شركة الساحل للاستثمار العقاري", "الساحل الشمالي", 1),
            ("agency", "دار العقار للتسويق", "القاهرة", 1),
        ]:
            conn.execute(
                "INSERT INTO entities (sector,kind,name,governorate,verified,created_at) VALUES ('realestate',?,?,?,?,?)",
                (kind, name, gov, verified, now))

    cur = conn.execute("SELECT COUNT(*) c FROM re_projects")
    if cur.fetchone()["c"] == 0:
        dev1 = conn.execute("SELECT id FROM entities WHERE sector='realestate' AND kind='developer' ORDER BY id LIMIT 1").fetchone()
        dev2 = conn.execute("SELECT id FROM entities WHERE sector='realestate' AND kind='developer' ORDER BY id DESC LIMIT 1").fetchone()
        d1 = dev1["id"] if dev1 else None
        d2 = dev2["id"] if dev2 else None
        # (اسم, مطوّر, محافظة, نوع, وحدات كلية, تسليم)
        re_projects = [
            ("كمبوند نخبة هايتس", d1, "القاهرة الجديدة", "سكني", "2027-06-01"),
            ("مول النخبة التجاري", d1, "القاهرة الجديدة", "تجاري", "2026-12-01"),
            ("قرية بلو باي الساحلية", d2, "الساحل الشمالي", "ساحلي", "2028-05-01"),
        ]
        proj_ids = []
        for name, dev, gov, ptype, delivery in re_projects:
            c2 = conn.execute(
                """INSERT INTO re_projects (name,developer_id,governorate,project_type,units_total,units_sold,status,delivery_date,created_at)
                   VALUES (?,?,?,?,0,0,'قيد التطوير',?,?)""",
                (name, dev, gov, ptype, delivery, now))
            proj_ids.append(c2.lastrowid)

        # وحدات لكل مشروع
        unit_defs = [
            # project_index, code, type, area, price, status, channel
            (0, "A-101", "شقة", 145, 3200000, "متاح", "فيسبوك"),
            (0, "A-102", "شقة", 160, 3600000, "محجوز", "معرض"),
            (0, "B-201", "شقة", 200, 4500000, "مباع", "إحالة"),
            (0, "V-01", "فيلا", 320, 9500000, "متاح", "جوجل"),
            (1, "S-01", "محل", 60, 4200000, "متاح", "معرض"),
            (1, "S-02", "محل", 45, 3100000, "مباع", "فيسبوك"),
            (1, "O-11", "مكتب", 90, 3800000, "متاح", "لينكدإن"),
            (2, "CH-01", "شاليه", 110, 2800000, "متاح", "فيسبوك"),
            (2, "CH-02", "شاليه", 95, 2400000, "محجوز", "معرض"),
        ]
        for pidx, code, utype, area, price, status, channel in unit_defs:
            conn.execute(
                """INSERT INTO re_units (project_id,unit_code,unit_type,area,price,status,listing_channel,created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (proj_ids[pidx], code, utype, area, price, status, channel, now))
        # تحديث إجماليات المشاريع
        for pid in proj_ids:
            tot = conn.execute("SELECT COUNT(*) FROM re_units WHERE project_id=?", (pid,)).fetchone()[0]
            sold = conn.execute("SELECT COUNT(*) FROM re_units WHERE project_id=? AND status='مباع'", (pid,)).fetchone()[0]
            conn.execute("UPDATE re_projects SET units_total=?, units_sold=? WHERE id=?", (tot, sold, pid))

    cur = conn.execute("SELECT COUNT(*) c FROM mkt_leads WHERE sector='realestate'")
    if cur.fetchone()["c"] == 0:
        leads = [
            ("خالد منصور", "01011112222", "فيسبوك", "شقة بالقاهرة الجديدة", "مهتم"),
            ("منى سعيد", "01022223333", "جوجل", "فيلا", "تفاوض"),
            ("أحمد رفعت", "01033334444", "إحالة", "محل تجاري", "جديد"),
            ("سلمى حسن", "01044445555", "معرض", "شاليه ساحلي", "تواصل"),
        ]
        for name, phone, source, interest, stage in leads:
            conn.execute(
                """INSERT INTO mkt_leads (sector,name,phone,source,interest,stage,created_at)
                   VALUES ('realestate',?,?,?,?,?,?)""",
                (name, phone, source, interest, stage, now))

    # ---- التسويق العام ----
    cur = conn.execute("SELECT COUNT(*) c FROM mkt_leads WHERE sector='general'")
    if cur.fetchone()["c"] == 0:
        for name, phone, source, interest, stage in [
            ("شركة الأمل التجارية", "01055556666", "لينكدإن", "حملة سوشيال ميديا", "مهتم"),
            ("مطعم بيت الكشري", "01066667777", "إحالة", "هوية بصرية", "تفاوض"),
            ("متجر إلكتروني ناشئ", "01077778888", "جوجل", "إدارة إعلانات", "جديد"),
        ]:
            conn.execute("INSERT INTO mkt_leads (sector,name,phone,source,interest,stage,created_at) VALUES ('general',?,?,?,?,?,?)",
                         (name, phone, source, interest, stage, now))

    # ---- التنقل ----
    cur = conn.execute("SELECT COUNT(*) c FROM mob_trips")
    if cur.fetchone()["c"] == 0:
        for pax, drv, frm, to, fare, status in [
            ("محمد سمير", "كابتن أحمد", "مدينة نصر", "المهندسين", 85, "مكتملة"),
            ("سارة علي", "كابتن محمود", "المعادي", "التجمع الخامس", 120, "جارية"),
            ("خالد فؤاد", "", "مصر الجديدة", "وسط البلد", 60, "مطلوبة"),
        ]:
            conn.execute("INSERT INTO mob_trips (passenger_name,driver_name,from_loc,to_loc,fare,status,requested_at,created_at) VALUES (?,?,?,?,?,?,?,?)",
                         (pax, drv, frm, to, fare, status, now, now))

    # ---- الشحن واللوجستيات ----
    cur = conn.execute("SELECT COUNT(*) c FROM log_shipments")
    if cur.fetchone()["c"] == 0:
        for trk, snd, rcv, fg, tg, w, cost, status in [
            ("EG202601", "متجر النور", "أحمد سعيد", "القاهرة", "الإسكندرية", 3.5, 90, "في الطريق"),
            ("EG202602", "مصنع الأثاث", "شركة المعارض", "دمياط", "القاهرة", 250, 1800, "قيد التجهيز"),
            ("EG202603", "صيدلية الشفاء", "مستشفى الأمل", "الجيزة", "الفيوم", 12, 150, "تم التسليم"),
        ]:
            conn.execute("INSERT INTO log_shipments (tracking_no,sender,receiver,from_gov,to_gov,weight,cost,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                         (trk, snd, rcv, fg, tg, w, cost, status, now))

    # ---- الزراعة ----
    cur = conn.execute("SELECT COUNT(*) c FROM agr_farms")
    if cur.fetchone()["c"] == 0:
        for owner, gov, area, crop, season, status in [
            ("مزرعة الدلتا", "البحيرة", 120, "قمح", "شتوي", "قيد الزراعة"),
            ("مزرعة الوادي", "المنيا", 80, "قصب سكر", "صيفي", "قيد الحصاد"),
            ("مزرعة النيل", "الشرقية", 45, "طماطم", "صيفي", "قيد الزراعة"),
        ]:
            conn.execute("INSERT INTO agr_farms (owner_name,governorate,area_feddan,crop,season,status,created_at) VALUES (?,?,?,?,?,?,?)",
                         (owner, gov, area, crop, season, status, now))

    # ---- المحاماة ----
    cur = conn.execute("SELECT COUNT(*) c FROM law_cases")
    if cur.fetchone()["c"] == 0:
        for cno, client, ctype, court, status, nxt in [
            ("2026/145", "شركة الإنشاءات الحديثة", "تجاري", "محكمة القاهرة الاقتصادية", "مفتوحة", "2026-09-15"),
            ("2026/210", "أحمد عبد الله", "مدني", "محكمة الجيزة الابتدائية", "مفتوحة", "2026-09-22"),
            ("2025/980", "ورثة المرحوم سعيد", "أحوال شخصية", "محكمة الأسرة", "مؤجلة", "2026-10-05"),
        ]:
            conn.execute("INSERT INTO law_cases (case_no,client_name,case_type,court,status,next_session,created_at) VALUES (?,?,?,?,?,?,?)",
                         (cno, client, ctype, court, status, nxt, now))

    # ---- طلبات موحّدة نموذجية (تكامل عبر القطاعات) ----
    cur = conn.execute("SELECT COUNT(*) c FROM service_requests")
    if cur.fetchone()["c"] == 0:
        reqs = [
            ("medical", "حجز كشف باطنة", "سمر أحمد", "01001234567", "القاهرة", "ألم بالمعدة منذ يومين", "عادي", "جديد"),
            ("contracting", "طلب صنايعي سباكة", "محمد فؤاد", "01112223334", "الجيزة", "تسريب في المطبخ", "عاجل", "قيد المعالجة"),
            ("realestate", "معاينة شقة", "خالد منصور", "01055556666", "القاهرة الجديدة", "مهتم بشقة 145م", "عادي", "محوّل"),
            ("mobility", "طلب رحلة", "منى سعيد", "01099998888", "القاهرة", "من المعادي للمطار", "عاجل", "جديد"),
            ("logistics", "طلب شحن", "متجر النور", "01077778888", "الإسكندرية", "شحنة 5 كجم للقاهرة", "عادي", "قيد المعالجة"),
            ("law", "استشارة قانونية", "شركة الأمل", "01033334444", "القاهرة", "نزاع تجاري مع مورد", "عادي", "جديد"),
        ]
        for sector, stype, name, phone, gov, details, prio, status in reqs:
            conn.execute(
                """INSERT INTO service_requests (sector,service_type,requester_name,requester_phone,governorate,details,priority,status,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (sector, stype, name, phone, gov, details, prio, status, now, now))

    conn.commit()


if __name__ == "__main__":
    init_db()
    print("تم إنشاء قاعدة البيانات:", DB_PATH)
