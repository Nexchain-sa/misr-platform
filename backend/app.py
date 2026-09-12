"""منصة مصر متعددة القطاعات — FastAPI backend.

النواة المشتركة + وحدة القطاع الطبي (السلسلة المتكاملة):
حجز ← زيارة/تشخيص ← أشعة ← تحاليل ← روشتة تُرسل تلقائيًا للصيدلية ← صرف ← متابعة.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uuid
from datetime import datetime
from typing import Optional, List

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(BASE_DIR, "web")

app = FastAPI(title="منصة مصر متعددة القطاعات")
db.init_db()


def now():
    return datetime.now().isoformat(timespec="seconds")


def rows(cur):
    return [dict(r) for r in cur.fetchall()]


def one(cur):
    r = cur.fetchone()
    return dict(r) if r else None


# ----------------------------------------------------------------------------
# المصادقة والصلاحيات
# ----------------------------------------------------------------------------
def current_user(request: Request):
    token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    if not token:
        token = request.query_params.get("token", "")
    if not token:
        return None
    conn = db.get_conn()
    u = one(conn.execute(
        """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token=?""", (token,)))
    conn.close()
    if u and u["active"]:
        return u
    return None


def require(request: Request):
    u = current_user(request)
    if not u:
        raise HTTPException(status_code=401, detail="غير مصرح — سجّل الدخول")
    return u


def audit(user, method, path):
    try:
        conn = db.get_conn()
        conn.execute("INSERT INTO audit_log (user_id,username,action,method,path,at) VALUES (?,?,?,?,?,?)",
                     (user["id"] if user else None, user["username"] if user else "?", method, method, path, now()))
        conn.commit()
        conn.close()
    except Exception:
        pass


# ----------------------------------------------------------------------------
# تسجيل الدخول
# ----------------------------------------------------------------------------
class LoginIn(BaseModel):
    username: str
    password: str


@app.post("/api/login")
def login(data: LoginIn):
    conn = db.get_conn()
    u = one(conn.execute("SELECT * FROM users WHERE username=? AND active=1", (data.username,)))
    if not u or db._hash_pw(data.password, u["salt"]) != u["password_hash"]:
        conn.close()
        raise HTTPException(status_code=401, detail="بيانات الدخول غير صحيحة")
    token = db.make_token()
    conn.execute("INSERT INTO sessions (token,user_id,created_at) VALUES (?,?,?)", (token, u["id"], now()))
    conn.commit()
    conn.close()
    return {"token": token, "user": {"id": u["id"], "username": u["username"],
            "full_name": u["full_name"], "role": u["role"], "sector": u["sector"]}}


@app.get("/api/me")
def me(request: Request):
    u = require(request)
    return {"id": u["id"], "username": u["username"], "full_name": u["full_name"],
            "role": u["role"], "sector": u["sector"]}


# ----------------------------------------------------------------------------
# لوحة المعلومات (Dashboard)
# ----------------------------------------------------------------------------
@app.get("/api/dashboard")
def dashboard(request: Request):
    require(request)
    conn = db.get_conn()
    def c(q, *a):
        return conn.execute(q, a).fetchone()[0]
    today = datetime.now().strftime("%Y-%m-%d")
    data = {
        "patients": c("SELECT COUNT(*) FROM med_patients"),
        "doctors": c("SELECT COUNT(*) FROM med_doctors WHERE active=1"),
        "appointments_today": c("SELECT COUNT(*) FROM med_appointments WHERE substr(scheduled_at,1,10)=?", today),
        "appointments_total": c("SELECT COUNT(*) FROM med_appointments"),
        "open_encounters": c("SELECT COUNT(*) FROM med_encounters WHERE status='مفتوحة'"),
        "pending_radiology": c("SELECT COUNT(*) FROM med_radiology_orders WHERE status='مطلوب'"),
        "pending_labs": c("SELECT COUNT(*) FROM med_lab_orders WHERE status='مطلوب'"),
        "prescriptions_sent": c("SELECT COUNT(*) FROM med_prescriptions WHERE status='مُرسلة'"),
        "revenue": c("SELECT COALESCE(SUM(amount),0) FROM payments WHERE direction='in'"),
        "sectors": {
            "medical": c("SELECT COUNT(*) FROM entities WHERE sector='medical'"),
            "contracting": c("SELECT COUNT(*) FROM con_workers") + c("SELECT COUNT(*) FROM con_projects"),
            "realestate": c("SELECT COUNT(*) FROM re_projects"),
            "marketing": c("SELECT COUNT(*) FROM mkt_leads"),
            "mobility": c("SELECT COUNT(*) FROM mob_trips"),
            "logistics": c("SELECT COUNT(*) FROM log_shipments"),
            "agriculture": c("SELECT COUNT(*) FROM agr_farms"),
            "law": c("SELECT COUNT(*) FROM law_cases"),
        },
    }
    conn.close()
    return data


# ============================================================================
# مركز القيادة الموحّد — مؤشرات القطاعات التسعة + المالية
# ============================================================================
SECTOR_META = {
    "medical": {"name": "القطاع الطبي", "icon": "🏥"},
    "contracting": {"name": "المقاولات", "icon": "🏗️"},
    "realestate": {"name": "العقاري", "icon": "🏢"},
    "marketing": {"name": "التسويق العام", "icon": "📣"},
    "mobility": {"name": "التنقل", "icon": "🚗"},
    "logistics": {"name": "اللوجستيات", "icon": "🚚"},
    "agriculture": {"name": "الزراعة", "icon": "🌾"},
    "law": {"name": "المحاماة", "icon": "⚖️"},
}


@app.get("/api/overview/kpis")
def overview_kpis(request: Request):
    require(request)
    conn = db.get_conn()
    def c(qq, *a):
        return conn.execute(qq, a).fetchone()[0]

    # إيرادات كل قطاع من جدول المدفوعات الموحّد
    rev_rows = rows(conn.execute(
        "SELECT sector, COALESCE(SUM(amount),0) rev FROM payments WHERE direction='in' GROUP BY sector"))
    rev_by_sector = {r["sector"]: r["rev"] for r in rev_rows}

    sectors = [
        {"key": "medical", "primary": c("SELECT COUNT(*) FROM med_appointments"), "primary_label": "حجز",
         "secondary": c("SELECT COUNT(*) FROM med_patients"), "secondary_label": "مريض"},
        {"key": "contracting", "primary": c("SELECT COUNT(*) FROM con_projects"), "primary_label": "أمر شغل",
         "secondary": c("SELECT COUNT(*) FROM con_workers"), "secondary_label": "فني"},
        {"key": "realestate", "primary": c("SELECT COUNT(*) FROM re_units"), "primary_label": "وحدة",
         "secondary": c("SELECT COUNT(*) FROM re_units WHERE status='مباع'"), "secondary_label": "مباعة"},
        {"key": "marketing", "primary": c("SELECT COUNT(*) FROM mkt_leads WHERE sector='general'"), "primary_label": "عميل محتمل",
         "secondary": c("SELECT COUNT(*) FROM mkt_leads WHERE sector='general' AND stage='تم'"), "secondary_label": "تم"},
        {"key": "mobility", "primary": c("SELECT COUNT(*) FROM mob_trips"), "primary_label": "رحلة",
         "secondary": c("SELECT COUNT(*) FROM mob_trips WHERE status='مكتملة'"), "secondary_label": "مكتملة"},
        {"key": "logistics", "primary": c("SELECT COUNT(*) FROM log_shipments"), "primary_label": "شحنة",
         "secondary": c("SELECT COUNT(*) FROM log_shipments WHERE status='تم التسليم'"), "secondary_label": "مسلّمة"},
        {"key": "agriculture", "primary": c("SELECT COUNT(*) FROM agr_farms"), "primary_label": "مزرعة",
         "secondary": c("SELECT COALESCE(SUM(area_feddan),0) FROM agr_farms"), "secondary_label": "فدان"},
        {"key": "law", "primary": c("SELECT COUNT(*) FROM law_cases"), "primary_label": "قضية",
         "secondary": c("SELECT COUNT(*) FROM law_cases WHERE status='مفتوحة'"), "secondary_label": "مفتوحة"},
    ]
    for s in sectors:
        s.update(SECTOR_META.get(s["key"], {}))
        s["revenue"] = rev_by_sector.get(s["key"], 0)

    total_in = c("SELECT COALESCE(SUM(amount),0) FROM payments WHERE direction='in'")
    total_out = c("SELECT COALESCE(SUM(amount),0) FROM payments WHERE direction='out'")
    data = {
        "sectors": sectors,
        "totals": {
            "revenue": total_in, "expenses": total_out, "net": total_in - total_out,
            "entities": c("SELECT COUNT(*) FROM entities"),
            "users": c("SELECT COUNT(*) FROM users WHERE active=1"),
            "official_docs": c("SELECT COUNT(*) FROM official_documents"),
            "payments_count": c("SELECT COUNT(*) FROM payments"),
        },
        "revenue_by_sector": [
            {"key": k, "name": SECTOR_META.get(k, {}).get("name", k),
             "icon": SECTOR_META.get(k, {}).get("icon", ""), "revenue": v}
            for k, v in sorted(rev_by_sector.items(), key=lambda x: -x[1])
        ],
    }
    conn.close()
    return data


@app.get("/api/payments")
def list_payments(request: Request, sector: Optional[str] = None, direction: Optional[str] = None):
    require(request)
    conn = db.get_conn()
    q = "SELECT * FROM payments WHERE 1=1"
    a = []
    if sector:
        q += " AND sector=?"; a.append(sector)
    if direction:
        q += " AND direction=?"; a.append(direction)
    q += " ORDER BY id DESC LIMIT 200"
    r = rows(conn.execute(q, a))
    for p in r:
        p["sector_name"] = SECTOR_META.get(p["sector"], {}).get("name", p["sector"] or "")
    conn.close()
    return r


class PaymentIn(BaseModel):
    sector: str
    direction: str = "in"
    ref_type: Optional[str] = None
    payer: Optional[str] = None
    amount: float = 0
    method: Optional[str] = None
    notes: Optional[str] = None


@app.post("/api/payments")
def add_payment(request: Request, data: PaymentIn):
    require(request)
    conn = db.get_conn()
    cur = conn.execute(
        """INSERT INTO payments (sector,direction,ref_type,payer,amount,method,status,paid_at,notes,created_at)
           VALUES (?,?,?,?,?,?, 'مدفوع', ?, ?, ?)""",
        (data.sector, data.direction, data.ref_type, data.payer, data.amount, data.method, now(), data.notes, now()))
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return {"id": pid}


# ============================================================================
# بوابة الطلبات الموحّدة — نقطة دخول واحدة لكل خدمات القطاعات
# ============================================================================
SERVICE_TYPES = {
    "medical": ["حجز كشف", "استشارة", "تحاليل", "أشعة", "عملية"],
    "contracting": ["طلب صنايعي", "طلب مقاول", "عرض سعر", "معاينة موقع", "طلب مهندس"],
    "realestate": ["معاينة وحدة", "استفسار سعر", "حجز وحدة", "طلب تطوير"],
    "marketing": ["حملة إعلانية", "هوية بصرية", "إدارة سوشيال ميديا", "استشارة تسويقية"],
    "mobility": ["طلب رحلة", "حجز مشوار", "توصيل"],
    "logistics": ["طلب شحن", "تتبّع شحنة", "استلام من الباب"],
    "agriculture": ["استشارة زراعية", "توريد محصول", "طلب معدات"],
    "law": ["استشارة قانونية", "رفع قضية", "توكيل", "صياغة عقد"],
}


@app.get("/api/requests")
def list_requests(request: Request, sector: Optional[str] = None, status: Optional[str] = None):
    require(request)
    conn = db.get_conn()
    q = """SELECT r.*, u.full_name AS assignee FROM service_requests r
           LEFT JOIN users u ON u.id=r.assigned_to WHERE 1=1"""
    a = []
    if sector:
        q += " AND r.sector=?"; a.append(sector)
    if status:
        q += " AND r.status=?"; a.append(status)
    q += " ORDER BY CASE r.priority WHEN 'عاجل' THEN 0 ELSE 1 END, r.id DESC"
    r = rows(conn.execute(q, a))
    for x in r:
        x["sector_name"] = SECTOR_META.get(x["sector"], {}).get("name", x["sector"])
        x["sector_icon"] = SECTOR_META.get(x["sector"], {}).get("icon", "")
    conn.close()
    return r


@app.get("/api/requests/service-types")
def request_service_types(request: Request):
    require(request)
    return SERVICE_TYPES


@app.get("/api/requests/dashboard")
def requests_dashboard(request: Request):
    require(request)
    conn = db.get_conn()
    def c(qq, *a):
        return conn.execute(qq, a).fetchone()[0]
    data = {
        "total": c("SELECT COUNT(*) FROM service_requests"),
        "new": c("SELECT COUNT(*) FROM service_requests WHERE status='جديد'"),
        "processing": c("SELECT COUNT(*) FROM service_requests WHERE status='قيد المعالجة'"),
        "urgent": c("SELECT COUNT(*) FROM service_requests WHERE priority='عاجل' AND status NOT IN ('مكتمل','ملغي')"),
        "completed": c("SELECT COUNT(*) FROM service_requests WHERE status='مكتمل'"),
        "by_sector": rows(conn.execute(
            "SELECT sector, COUNT(*) c FROM service_requests GROUP BY sector ORDER BY c DESC")),
    }
    for b in data["by_sector"]:
        b["name"] = SECTOR_META.get(b["sector"], {}).get("name", b["sector"])
        b["icon"] = SECTOR_META.get(b["sector"], {}).get("icon", "")
    conn.close()
    return data


class ServiceRequestIn(BaseModel):
    sector: str
    service_type: Optional[str] = None
    requester_name: str
    requester_phone: Optional[str] = None
    governorate: Optional[str] = None
    details: Optional[str] = None
    priority: str = "عادي"


@app.post("/api/requests")
def create_request(request: Request, data: ServiceRequestIn):
    require(request)
    conn = db.get_conn()
    cur = conn.execute(
        """INSERT INTO service_requests (sector,service_type,requester_name,requester_phone,governorate,details,priority,status,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?, 'جديد', ?, ?)""",
        (data.sector, data.service_type, data.requester_name, data.requester_phone,
         data.governorate, data.details, data.priority, now(), now()))
    rid = cur.lastrowid
    # إشعار للمسؤولين المعنيين بالقطاع
    role_map = {"medical": "reception", "contracting": "contractor", "realestate": "agent"}
    role = role_map.get(data.sector)
    if role:
        for u in rows(conn.execute("SELECT id FROM users WHERE role=?", (role,))):
            conn.execute("INSERT INTO notifications (user_id,channel,title,body,created_at) VALUES (?,?,?,?,?)",
                         (u["id"], "system", "طلب خدمة جديد",
                          f"طلب {data.service_type or ''} من {data.requester_name}", now()))
    conn.commit()
    conn.close()
    return {"id": rid}


@app.put("/api/requests/{rid}/status")
def request_status(request: Request, rid: int, status: str = Form(...)):
    require(request)
    conn = db.get_conn()
    conn.execute("UPDATE service_requests SET status=?, updated_at=? WHERE id=?", (status, now(), rid))
    conn.commit()
    conn.close()
    return {"ok": True}


# ============================================================================
# البحث الموحّد — عبر كل القطاعات
# ============================================================================
@app.get("/api/search")
def global_search(request: Request, q: str):
    require(request)
    if not q or len(q.strip()) < 1:
        return {"results": []}
    conn = db.get_conn()
    like = f"%{q.strip()}%"
    results = []

    def add(items, typ, label_key, sub, sector, view):
        for it in items:
            results.append({
                "type": typ, "id": it["id"], "label": it.get(label_key, ""),
                "sub": sub(it), "sector": sector, "view": view})

    add(rows(conn.execute("SELECT * FROM med_patients WHERE full_name LIKE ? OR national_id LIKE ? OR phone LIKE ? LIMIT 6", (like, like, like))),
        "مريض", "full_name", lambda x: f"🧑‍⚕️ {x.get('phone') or ''} · {x.get('governorate') or ''}", "medical", "patients")
    add(rows(conn.execute("SELECT * FROM med_doctors WHERE full_name LIKE ? LIMIT 6", (like,))),
        "طبيب", "full_name", lambda x: "👨‍⚕️ طبيب", "medical", "doctors")
    add(rows(conn.execute("SELECT * FROM con_workers WHERE full_name LIKE ? OR trade LIKE ? LIMIT 6", (like, like))),
        "فني", "full_name", lambda x: f"👷 {x.get('trade') or ''} · {x.get('governorate') or ''}", "contracting", "workers")
    add(rows(conn.execute("SELECT * FROM re_units WHERE unit_code LIKE ? OR unit_type LIKE ? LIMIT 6", (like, like))),
        "وحدة", "unit_code", lambda x: f"🔑 {x.get('unit_type') or ''} · {x.get('status') or ''}", "realestate", "re_units")
    add(rows(conn.execute("SELECT * FROM mkt_leads WHERE name LIKE ? OR phone LIKE ? LIMIT 6", (like, like))),
        "عميل محتمل", "name", lambda x: f"🎯 {x.get('stage') or ''} · {x.get('source') or ''}", "marketing", "re_leads")
    add(rows(conn.execute("SELECT * FROM log_shipments WHERE tracking_no LIKE ? OR sender LIKE ? OR receiver LIKE ? LIMIT 6", (like, like, like))),
        "شحنة", "tracking_no", lambda x: f"🚚 {x.get('sender') or ''} ← {x.get('receiver') or ''}", "logistics", "logistics")
    add(rows(conn.execute("SELECT * FROM law_cases WHERE case_no LIKE ? OR client_name LIKE ? LIMIT 6", (like, like))),
        "قضية", "case_no", lambda x: f"⚖️ {x.get('client_name') or ''} · {x.get('status') or ''}", "law", "law")
    add(rows(conn.execute("SELECT * FROM entities WHERE name LIKE ? LIMIT 6", (like,))),
        "منشأة", "name", lambda x: f"🏢 {x.get('governorate') or ''}", "core", "entities")
    add(rows(conn.execute("SELECT * FROM service_requests WHERE requester_name LIKE ? OR service_type LIKE ? LIMIT 6", (like, like))),
        "طلب خدمة", "requester_name", lambda x: f"📨 {x.get('service_type') or ''} · {x.get('status') or ''}", "core", "requests")

    conn.close()
    return {"results": results, "count": len(results)}


# ============================================================================
# مركز الإشعارات — تعليم كمقروء
# ============================================================================
@app.put("/api/notifications/{nid}/seen")
def notif_seen(request: Request, nid: int):
    u = require(request)
    conn = db.get_conn()
    conn.execute("UPDATE notifications SET seen=1 WHERE id=? AND user_id=?", (nid, u["id"]))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.put("/api/notifications/seen-all")
def notif_seen_all(request: Request):
    u = require(request)
    conn = db.get_conn()
    conn.execute("UPDATE notifications SET seen=1 WHERE user_id=?", (u["id"],))
    conn.commit()
    conn.close()
    return {"ok": True}


# ============================================================================
# النواة: الكيانات والأوراق الرسمية والتقييمات
# ============================================================================
@app.get("/api/entities")
def list_entities(request: Request, sector: Optional[str] = None, kind: Optional[str] = None):
    require(request)
    conn = db.get_conn()
    q = "SELECT * FROM entities WHERE 1=1"
    a = []
    if sector:
        q += " AND sector=?"; a.append(sector)
    if kind:
        q += " AND kind=?"; a.append(kind)
    q += " ORDER BY name"
    r = rows(conn.execute(q, a))
    conn.close()
    return r


class EntityIn(BaseModel):
    sector: str
    kind: Optional[str] = None
    name: str
    tax_number: Optional[str] = None
    commercial_reg: Optional[str] = None
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    governorate: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None


@app.post("/api/entities")
def add_entity(request: Request, data: EntityIn):
    require(request)
    conn = db.get_conn()
    cur = conn.execute(
        """INSERT INTO entities (sector,kind,name,tax_number,commercial_reg,contact_person,phone,email,governorate,address,notes,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (data.sector, data.kind, data.name, data.tax_number, data.commercial_reg, data.contact_person,
         data.phone, data.email, data.governorate, data.address, data.notes, now()))
    conn.commit()
    eid = cur.lastrowid
    conn.close()
    return {"id": eid}


@app.get("/api/official-documents")
def list_official_docs(request: Request, owner_type: Optional[str] = None, owner_id: Optional[int] = None):
    require(request)
    conn = db.get_conn()
    q = "SELECT * FROM official_documents WHERE 1=1"
    a = []
    if owner_type:
        q += " AND owner_type=?"; a.append(owner_type)
    if owner_id:
        q += " AND owner_id=?"; a.append(owner_id)
    q += " ORDER BY id DESC"
    r = rows(conn.execute(q, a))
    conn.close()
    return r


@app.post("/api/official-documents")
async def add_official_doc(request: Request,
                           sector: str = Form(...), owner_type: str = Form(...), owner_id: int = Form(...),
                           doc_type: str = Form(...), ref_no: str = Form(""), title: str = Form(""),
                           issuer: str = Form(""), issue_date: str = Form(""), expiry_date: str = Form(""),
                           file: Optional[UploadFile] = File(None)):
    require(request)
    file_path = None
    if file:
        ext = os.path.splitext(file.filename)[1]
        fname = f"{uuid.uuid4().hex}{ext}"
        dest = os.path.join(db.UPLOAD_DIR, fname)
        with open(dest, "wb") as f:
            f.write(await file.read())
        file_path = fname
    conn = db.get_conn()
    cur = conn.execute(
        """INSERT INTO official_documents (sector,owner_type,owner_id,doc_type,ref_no,title,issuer,issue_date,expiry_date,file_path,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (sector, owner_type, owner_id, doc_type, ref_no, title, issuer, issue_date, expiry_date, file_path, now()))
    conn.commit()
    did = cur.lastrowid
    conn.close()
    return {"id": did, "file_path": file_path}


@app.get("/api/uploads/{fname}")
def get_upload(fname: str):
    path = os.path.join(db.UPLOAD_DIR, fname)
    if not os.path.exists(path):
        raise HTTPException(404, "غير موجود")
    return FileResponse(path)


class RatingIn(BaseModel):
    target_type: str
    target_id: int
    rater_name: Optional[str] = None
    stars: int
    comment: Optional[str] = None


@app.post("/api/ratings")
def add_rating(request: Request, data: RatingIn):
    require(request)
    conn = db.get_conn()
    conn.execute("INSERT INTO ratings (target_type,target_id,rater_name,stars,comment,created_at) VALUES (?,?,?,?,?,?)",
                 (data.target_type, data.target_id, data.rater_name, data.stars, data.comment, now()))
    # تحديث متوسط التقييم على الطبيب أو الكيان
    agg = one(conn.execute("SELECT AVG(stars) a, COUNT(*) c FROM ratings WHERE target_type=? AND target_id=?",
                           (data.target_type, data.target_id)))
    if data.target_type == "doctor":
        conn.execute("UPDATE med_doctors SET rating=?, rating_count=? WHERE id=?",
                     (round(agg["a"], 2), agg["c"], data.target_id))
    elif data.target_type == "worker":
        conn.execute("UPDATE con_workers SET rating=?, rating_count=? WHERE id=?",
                     (round(agg["a"], 2), agg["c"], data.target_id))
    elif data.target_type == "entity":
        conn.execute("UPDATE entities SET rating=?, rating_count=? WHERE id=?",
                     (round(agg["a"], 2), agg["c"], data.target_id))
    conn.commit()
    conn.close()
    return {"ok": True, "avg": round(agg["a"], 2), "count": agg["c"]}


@app.get("/api/ratings")
def list_ratings(request: Request, target_type: str, target_id: int):
    require(request)
    conn = db.get_conn()
    r = rows(conn.execute("SELECT * FROM ratings WHERE target_type=? AND target_id=? ORDER BY id DESC",
                          (target_type, target_id)))
    conn.close()
    return r


# ============================================================================
# القطاع الطبي — التخصصات والأطباء والمرضى
# ============================================================================
@app.get("/api/med/specialties")
def specialties(request: Request):
    require(request)
    conn = db.get_conn()
    r = rows(conn.execute("SELECT * FROM med_specialties ORDER BY name"))
    conn.close()
    return r


@app.get("/api/med/doctors")
def doctors(request: Request, specialty_id: Optional[int] = None):
    require(request)
    conn = db.get_conn()
    q = """SELECT d.*, s.name AS specialty, e.name AS clinic
           FROM med_doctors d
           LEFT JOIN med_specialties s ON s.id=d.specialty_id
           LEFT JOIN entities e ON e.id=d.clinic_id
           WHERE d.active=1"""
    a = []
    if specialty_id:
        q += " AND d.specialty_id=?"; a.append(specialty_id)
    q += " ORDER BY d.rating DESC"
    r = rows(conn.execute(q, a))
    conn.close()
    return r


class DoctorIn(BaseModel):
    full_name: str
    specialty_id: Optional[int] = None
    clinic_id: Optional[int] = None
    license_no: Optional[str] = None
    phone: Optional[str] = None
    fee: float = 0
    bio: Optional[str] = None


@app.post("/api/med/doctors")
def add_doctor(request: Request, data: DoctorIn):
    require(request)
    conn = db.get_conn()
    cur = conn.execute(
        """INSERT INTO med_doctors (full_name,specialty_id,clinic_id,license_no,phone,fee,bio,active,created_at)
           VALUES (?,?,?,?,?,?,?,1,?)""",
        (data.full_name, data.specialty_id, data.clinic_id, data.license_no, data.phone, data.fee, data.bio, now()))
    conn.commit()
    did = cur.lastrowid
    conn.close()
    return {"id": did}


@app.get("/api/med/patients")
def patients(request: Request, q: Optional[str] = None):
    require(request)
    conn = db.get_conn()
    sql = "SELECT * FROM med_patients WHERE 1=1"
    a = []
    if q:
        sql += " AND (full_name LIKE ? OR national_id LIKE ? OR phone LIKE ?)"
        a += [f"%{q}%", f"%{q}%", f"%{q}%"]
    sql += " ORDER BY id DESC"
    r = rows(conn.execute(sql, a))
    conn.close()
    return r


class PatientIn(BaseModel):
    national_id: Optional[str] = None
    full_name: str
    gender: Optional[str] = None
    birth_date: Optional[str] = None
    phone: Optional[str] = None
    governorate: Optional[str] = None
    address: Optional[str] = None
    blood_type: Optional[str] = None
    chronic_diseases: Optional[str] = None
    allergies: Optional[str] = None


@app.post("/api/med/patients")
def add_patient(request: Request, data: PatientIn):
    require(request)
    conn = db.get_conn()
    cur = conn.execute(
        """INSERT INTO med_patients (national_id,full_name,gender,birth_date,phone,governorate,address,blood_type,chronic_diseases,allergies,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (data.national_id, data.full_name, data.gender, data.birth_date, data.phone, data.governorate,
         data.address, data.blood_type, data.chronic_diseases, data.allergies, now()))
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return {"id": pid}


# ============================================================================
# القطاع الطبي — السجل المرضي الكامل (كل شيء عن المريض)
# ============================================================================
@app.get("/api/med/patients/{pid}/record")
def patient_record(request: Request, pid: int):
    require(request)
    conn = db.get_conn()
    patient = one(conn.execute("SELECT * FROM med_patients WHERE id=?", (pid,)))
    if not patient:
        conn.close()
        raise HTTPException(404, "المريض غير موجود")
    appointments = rows(conn.execute(
        """SELECT a.*, d.full_name AS doctor FROM med_appointments a
           LEFT JOIN med_doctors d ON d.id=a.doctor_id
           WHERE a.patient_id=? ORDER BY a.scheduled_at DESC""", (pid,)))
    encounters = rows(conn.execute(
        """SELECT e.*, d.full_name AS doctor FROM med_encounters e
           LEFT JOIN med_doctors d ON d.id=e.doctor_id
           WHERE e.patient_id=? ORDER BY e.id DESC""", (pid,)))
    for e in encounters:
        e["diagnoses"] = rows(conn.execute("SELECT * FROM med_diagnoses WHERE encounter_id=?", (e["id"],)))
        e["radiology"] = rows(conn.execute("SELECT * FROM med_radiology_orders WHERE encounter_id=?", (e["id"],)))
        e["labs"] = rows(conn.execute("SELECT * FROM med_lab_orders WHERE encounter_id=?", (e["id"],)))
        pres = rows(conn.execute("SELECT * FROM med_prescriptions WHERE encounter_id=?", (e["id"],)))
        for p in pres:
            p["items"] = rows(conn.execute("SELECT * FROM med_prescription_items WHERE prescription_id=?", (p["id"],)))
        e["prescriptions"] = pres
    operations = rows(conn.execute("SELECT * FROM med_operations WHERE patient_id=? ORDER BY id DESC", (pid,)))
    conn.close()
    return {"patient": patient, "appointments": appointments, "encounters": encounters, "operations": operations}


# ============================================================================
# القطاع الطبي — الحجوزات
# ============================================================================
@app.get("/api/med/appointments")
def appointments(request: Request, status: Optional[str] = None, date: Optional[str] = None):
    require(request)
    conn = db.get_conn()
    q = """SELECT a.*, p.full_name AS patient, p.phone AS patient_phone,
                  d.full_name AS doctor, s.name AS specialty
           FROM med_appointments a
           LEFT JOIN med_patients p ON p.id=a.patient_id
           LEFT JOIN med_doctors d ON d.id=a.doctor_id
           LEFT JOIN med_specialties s ON s.id=d.specialty_id
           WHERE 1=1"""
    a = []
    if status:
        q += " AND a.status=?"; a.append(status)
    if date:
        q += " AND substr(a.scheduled_at,1,10)=?"; a.append(date)
    q += " ORDER BY a.scheduled_at DESC"
    r = rows(conn.execute(q, a))
    conn.close()
    return r


class AppointmentIn(BaseModel):
    patient_id: int
    doctor_id: int
    scheduled_at: str
    complaint: Optional[str] = None


@app.post("/api/med/appointments")
def add_appointment(request: Request, data: AppointmentIn):
    require(request)
    conn = db.get_conn()
    doc = one(conn.execute("SELECT fee, clinic_id FROM med_doctors WHERE id=?", (data.doctor_id,)))
    fee = doc["fee"] if doc else 0
    clinic_id = doc["clinic_id"] if doc else None
    cur = conn.execute(
        """INSERT INTO med_appointments (patient_id,doctor_id,clinic_id,scheduled_at,complaint,fee,status,created_at)
           VALUES (?,?,?,?,?,?, 'محجوز', ?)""",
        (data.patient_id, data.doctor_id, clinic_id, data.scheduled_at, data.complaint, fee, now()))
    conn.commit()
    aid = cur.lastrowid
    conn.close()
    return {"id": aid, "fee": fee}


@app.put("/api/med/appointments/{aid}/status")
def set_appt_status(request: Request, aid: int, status: str = Form(...)):
    require(request)
    conn = db.get_conn()
    conn.execute("UPDATE med_appointments SET status=? WHERE id=?", (status, aid))
    conn.commit()
    conn.close()
    return {"ok": True}


# ============================================================================
# القطاع الطبي — الزيارة (Encounter): بدء الكشف
# ============================================================================
class EncounterStart(BaseModel):
    appointment_id: Optional[int] = None
    patient_id: int
    doctor_id: int
    vitals_bp: Optional[str] = None
    vitals_temp: Optional[str] = None
    vitals_pulse: Optional[str] = None
    vitals_weight: Optional[str] = None
    examination: Optional[str] = None


@app.post("/api/med/encounters")
def start_encounter(request: Request, data: EncounterStart):
    require(request)
    conn = db.get_conn()
    cur = conn.execute(
        """INSERT INTO med_encounters (appointment_id,patient_id,doctor_id,started_at,vitals_bp,vitals_temp,vitals_pulse,vitals_weight,examination,status,created_at)
           VALUES (?,?,?,?,?,?,?,?,?, 'مفتوحة', ?)""",
        (data.appointment_id, data.patient_id, data.doctor_id, now(), data.vitals_bp, data.vitals_temp,
         data.vitals_pulse, data.vitals_weight, data.examination, now()))
    eid = cur.lastrowid
    if data.appointment_id:
        conn.execute("UPDATE med_appointments SET status='قيد الكشف' WHERE id=?", (data.appointment_id,))
    conn.commit()
    conn.close()
    return {"id": eid}


@app.get("/api/med/encounters/{eid}")
def get_encounter(request: Request, eid: int):
    require(request)
    conn = db.get_conn()
    e = one(conn.execute(
        """SELECT e.*, p.full_name AS patient, p.allergies, p.chronic_diseases, p.blood_type,
                  d.full_name AS doctor FROM med_encounters e
           LEFT JOIN med_patients p ON p.id=e.patient_id
           LEFT JOIN med_doctors d ON d.id=e.doctor_id WHERE e.id=?""", (eid,)))
    if not e:
        conn.close()
        raise HTTPException(404, "الزيارة غير موجودة")
    e["diagnoses"] = rows(conn.execute("SELECT * FROM med_diagnoses WHERE encounter_id=?", (eid,)))
    e["radiology"] = rows(conn.execute("SELECT * FROM med_radiology_orders WHERE encounter_id=?", (eid,)))
    e["labs"] = rows(conn.execute("SELECT * FROM med_lab_orders WHERE encounter_id=?", (eid,)))
    pres = rows(conn.execute("SELECT * FROM med_prescriptions WHERE encounter_id=?", (eid,)))
    for p in pres:
        p["items"] = rows(conn.execute("SELECT * FROM med_prescription_items WHERE prescription_id=?", (p["id"],)))
    e["prescriptions"] = pres
    conn.close()
    return e


class DiagnosisIn(BaseModel):
    encounter_id: int
    diagnosis: str
    icd_code: Optional[str] = None
    severity: Optional[str] = None
    notes: Optional[str] = None


@app.post("/api/med/diagnoses")
def add_diagnosis(request: Request, data: DiagnosisIn):
    require(request)
    conn = db.get_conn()
    cur = conn.execute(
        "INSERT INTO med_diagnoses (encounter_id,diagnosis,icd_code,severity,notes,created_at) VALUES (?,?,?,?,?,?)",
        (data.encounter_id, data.diagnosis, data.icd_code, data.severity, data.notes, now()))
    conn.commit()
    did = cur.lastrowid
    conn.close()
    return {"id": did}


# ---- طلبات الأشعة ----
class RadOrderIn(BaseModel):
    encounter_id: int
    center_id: Optional[int] = None
    exam_type: str
    body_part: Optional[str] = None


@app.post("/api/med/radiology")
def order_radiology(request: Request, data: RadOrderIn):
    require(request)
    conn = db.get_conn()
    cur = conn.execute(
        """INSERT INTO med_radiology_orders (encounter_id,center_id,exam_type,body_part,status,ordered_at)
           VALUES (?,?,?,?, 'مطلوب', ?)""",
        (data.encounter_id, data.center_id, data.exam_type, data.body_part, now()))
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return {"id": rid}


@app.put("/api/med/radiology/{rid}/result")
def radiology_result(request: Request, rid: int, result_text: str = Form(...)):
    require(request)
    conn = db.get_conn()
    conn.execute("UPDATE med_radiology_orders SET result_text=?, status='النتيجة جاهزة', result_at=? WHERE id=?",
                 (result_text, now(), rid))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/med/radiology/queue")
def radiology_queue(request: Request):
    require(request)
    conn = db.get_conn()
    r = rows(conn.execute(
        """SELECT ro.*, p.full_name AS patient, d.full_name AS doctor
           FROM med_radiology_orders ro
           JOIN med_encounters e ON e.id=ro.encounter_id
           LEFT JOIN med_patients p ON p.id=e.patient_id
           LEFT JOIN med_doctors d ON d.id=e.doctor_id
           WHERE ro.status!='النتيجة جاهزة' ORDER BY ro.id DESC"""))
    conn.close()
    return r


# ---- طلبات التحاليل ----
class LabOrderIn(BaseModel):
    encounter_id: int
    center_id: Optional[int] = None
    test_name: str


@app.post("/api/med/labs")
def order_lab(request: Request, data: LabOrderIn):
    require(request)
    conn = db.get_conn()
    cur = conn.execute(
        """INSERT INTO med_lab_orders (encounter_id,center_id,test_name,status,ordered_at)
           VALUES (?,?,?, 'مطلوب', ?)""",
        (data.encounter_id, data.center_id, data.test_name, now()))
    conn.commit()
    lid = cur.lastrowid
    conn.close()
    return {"id": lid}


@app.put("/api/med/labs/{lid}/result")
def lab_result(request: Request, lid: int, result_value: str = Form(...), reference_range: str = Form("")):
    require(request)
    conn = db.get_conn()
    conn.execute("UPDATE med_lab_orders SET result_value=?, reference_range=?, status='النتيجة جاهزة', result_at=? WHERE id=?",
                 (result_value, reference_range, now(), lid))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/med/labs/queue")
def labs_queue(request: Request):
    require(request)
    conn = db.get_conn()
    r = rows(conn.execute(
        """SELECT lo.*, p.full_name AS patient, d.full_name AS doctor
           FROM med_lab_orders lo
           JOIN med_encounters e ON e.id=lo.encounter_id
           LEFT JOIN med_patients p ON p.id=e.patient_id
           LEFT JOIN med_doctors d ON d.id=e.doctor_id
           WHERE lo.status!='النتيجة جاهزة' ORDER BY lo.id DESC"""))
    conn.close()
    return r


# ---- الروشتة: تُرسل تلقائيًا للصيدلية ----
class PrescriptionItemIn(BaseModel):
    drug_name: str
    dose: Optional[str] = None
    frequency: Optional[str] = None
    duration: Optional[str] = None
    instructions: Optional[str] = None


class PrescriptionIn(BaseModel):
    encounter_id: int
    pharmacy_id: Optional[int] = None
    notes: Optional[str] = None
    items: List[PrescriptionItemIn]


@app.post("/api/med/prescriptions")
def create_prescription(request: Request, data: PrescriptionIn):
    require(request)
    conn = db.get_conn()
    enc = one(conn.execute("SELECT patient_id, doctor_id FROM med_encounters WHERE id=?", (data.encounter_id,)))
    if not enc:
        conn.close()
        raise HTTPException(404, "الزيارة غير موجودة")
    # اختيار صيدلية افتراضية لو لم تُحدَّد (أقرب صيدلية موثّقة)
    pharmacy_id = data.pharmacy_id
    if not pharmacy_id:
        ph = one(conn.execute("SELECT id FROM entities WHERE sector='medical' AND kind='pharmacy' ORDER BY verified DESC, id LIMIT 1"))
        pharmacy_id = ph["id"] if ph else None
    cur = conn.execute(
        """INSERT INTO med_prescriptions (encounter_id,patient_id,doctor_id,pharmacy_id,status,notes,created_at)
           VALUES (?,?,?,?, 'مُرسلة', ?, ?)""",
        (data.encounter_id, enc["patient_id"], enc["doctor_id"], pharmacy_id, data.notes, now()))
    presc_id = cur.lastrowid
    for it in data.items:
        conn.execute(
            """INSERT INTO med_prescription_items (prescription_id,drug_name,dose,frequency,duration,instructions)
               VALUES (?,?,?,?,?,?)""",
            (presc_id, it.drug_name, it.dose, it.frequency, it.duration, it.instructions))
    # إشعار تلقائي للصيدلية (السلسلة: إرسال العلاج للصيدلية أوتوماتيك)
    if pharmacy_id:
        pharm_users = rows(conn.execute("SELECT id FROM users WHERE role='pharmacist'"))
        pname = one(conn.execute("SELECT name FROM entities WHERE id=?", (pharmacy_id,)))
        for pu in pharm_users:
            conn.execute(
                "INSERT INTO notifications (user_id,channel,title,body,created_at) VALUES (?,?,?,?,?)",
                (pu["id"], "system", "روشتة جديدة",
                 f"وصلت روشتة رقم {presc_id} إلى {pname['name'] if pname else 'الصيدلية'}", now()))
    conn.commit()
    conn.close()
    return {"id": presc_id, "pharmacy_id": pharmacy_id, "status": "مُرسلة"}


@app.get("/api/med/pharmacy/queue")
def pharmacy_queue(request: Request, pharmacy_id: Optional[int] = None):
    require(request)
    conn = db.get_conn()
    q = """SELECT pr.*, p.full_name AS patient, p.phone AS patient_phone, p.allergies,
                  d.full_name AS doctor, e.name AS pharmacy
           FROM med_prescriptions pr
           LEFT JOIN med_patients p ON p.id=pr.patient_id
           LEFT JOIN med_doctors d ON d.id=pr.doctor_id
           LEFT JOIN entities e ON e.id=pr.pharmacy_id
           WHERE 1=1"""
    a = []
    if pharmacy_id:
        q += " AND pr.pharmacy_id=?"; a.append(pharmacy_id)
    q += " ORDER BY CASE pr.status WHEN 'مُرسلة' THEN 0 ELSE 1 END, pr.id DESC"
    r = rows(conn.execute(q, a))
    for p in r:
        p["items"] = rows(conn.execute("SELECT * FROM med_prescription_items WHERE prescription_id=?", (p["id"],)))
    conn.close()
    return r


@app.put("/api/med/prescriptions/{pid}/dispense")
def dispense_prescription(request: Request, pid: int):
    require(request)
    conn = db.get_conn()
    conn.execute("UPDATE med_prescriptions SET status='تم الصرف', dispensed_at=? WHERE id=?", (now(), pid))
    conn.execute("UPDATE med_prescription_items SET dispensed=1 WHERE prescription_id=?", (pid,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ---- إغلاق الزيارة + تحصيل ----
@app.put("/api/med/encounters/{eid}/close")
def close_encounter(request: Request, eid: int):
    require(request)
    conn = db.get_conn()
    enc = one(conn.execute("SELECT * FROM med_encounters WHERE id=?", (eid,)))
    if not enc:
        conn.close()
        raise HTTPException(404, "الزيارة غير موجودة")
    conn.execute("UPDATE med_encounters SET status='مكتملة', closed_at=? WHERE id=?", (now(), eid))
    if enc["appointment_id"]:
        appt = one(conn.execute("SELECT fee FROM med_appointments WHERE id=?", (enc["appointment_id"],)))
        conn.execute("UPDATE med_appointments SET status='مكتمل' WHERE id=?", (enc["appointment_id"],))
        # تحصيل الكشف
        fee = appt["fee"] if appt else 0
        if fee:
            conn.execute(
                """INSERT INTO payments (sector,direction,ref_type,ref_id,amount,status,paid_at,created_at)
                   VALUES ('medical','in','appointment',?,?, 'مدفوع', ?, ?)""",
                (enc["appointment_id"], fee, now(), now()))
    conn.commit()
    conn.close()
    return {"ok": True}


# ============================================================================
# إشعارات المستخدم
# ============================================================================
@app.get("/api/notifications")
def notifications(request: Request):
    u = require(request)
    conn = db.get_conn()
    r = rows(conn.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 30", (u["id"],)))
    conn.close()
    return r


# ============================================================================
# قطاع المقاولات — دليل العمالة والمهندسين + المقاولين والموردين + أوامر الشغل
# ============================================================================
@app.get("/api/con/workers")
def con_workers(request: Request, category: Optional[str] = None, trade: Optional[str] = None,
                q: Optional[str] = None, available: Optional[int] = None):
    require(request)
    conn = db.get_conn()
    sql = "SELECT * FROM con_workers WHERE 1=1"
    a = []
    if category:
        sql += " AND category=?"; a.append(category)
    if trade:
        sql += " AND trade LIKE ?"; a.append(f"%{trade}%")
    if available is not None:
        sql += " AND available=?"; a.append(available)
    if q:
        sql += " AND (full_name LIKE ? OR trade LIKE ? OR phone LIKE ?)"
        a += [f"%{q}%", f"%{q}%", f"%{q}%"]
    sql += " ORDER BY rating DESC, id DESC"
    r = rows(conn.execute(sql, a))
    conn.close()
    return r


class WorkerIn(BaseModel):
    full_name: str
    category: str = "صنايعي"          # صنايعي / مهندس
    trade: Optional[str] = None
    phone: Optional[str] = None
    governorate: Optional[str] = None
    daily_rate: float = 0
    experience_years: int = 0
    notes: Optional[str] = None


@app.post("/api/con/workers")
def add_worker(request: Request, data: WorkerIn):
    require(request)
    conn = db.get_conn()
    cur = conn.execute(
        """INSERT INTO con_workers (full_name,category,trade,phone,governorate,daily_rate,experience_years,available,notes,created_at)
           VALUES (?,?,?,?,?,?,?,1,?,?)""",
        (data.full_name, data.category, data.trade, data.phone, data.governorate,
         data.daily_rate, data.experience_years, data.notes, now()))
    conn.commit()
    wid = cur.lastrowid
    conn.close()
    return {"id": wid}


@app.put("/api/con/workers/{wid}/availability")
def worker_availability(request: Request, wid: int, available: int = Form(...)):
    require(request)
    conn = db.get_conn()
    conn.execute("UPDATE con_workers SET available=? WHERE id=?", (available, wid))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/con/projects")
def con_projects(request: Request, status: Optional[str] = None, work_type: Optional[str] = None):
    require(request)
    conn = db.get_conn()
    sql = """SELECT p.*, e.name AS contractor
             FROM con_projects p LEFT JOIN entities e ON e.id=p.contractor_id WHERE 1=1"""
    a = []
    if status:
        sql += " AND p.status=?"; a.append(status)
    if work_type:
        sql += " AND p.work_type=?"; a.append(work_type)
    sql += " ORDER BY p.id DESC"
    r = rows(conn.execute(sql, a))
    conn.close()
    return r


class ConProjectIn(BaseModel):
    title: str
    work_type: str
    client_name: Optional[str] = None
    client_phone: Optional[str] = None
    contractor_id: Optional[int] = None
    governorate: Optional[str] = None
    budget: float = 0
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    notes: Optional[str] = None


@app.post("/api/con/projects")
def add_con_project(request: Request, data: ConProjectIn):
    require(request)
    conn = db.get_conn()
    cur = conn.execute(
        """INSERT INTO con_projects (title,work_type,client_name,client_phone,contractor_id,governorate,budget,progress,status,start_date,end_date,notes,created_at)
           VALUES (?,?,?,?,?,?,?,0,'جديد',?,?,?,?)""",
        (data.title, data.work_type, data.client_name, data.client_phone, data.contractor_id,
         data.governorate, data.budget, data.start_date, data.end_date, data.notes, now()))
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return {"id": pid}


@app.put("/api/con/projects/{pid}/progress")
def con_project_progress(request: Request, pid: int, progress: int = Form(...), status: str = Form(...)):
    require(request)
    conn = db.get_conn()
    conn.execute("UPDATE con_projects SET progress=?, status=? WHERE id=?", (progress, status, pid))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/con/dashboard")
def con_dashboard(request: Request):
    require(request)
    conn = db.get_conn()
    def c(qq, *a):
        return conn.execute(qq, a).fetchone()[0]
    data = {
        "workers": c("SELECT COUNT(*) FROM con_workers WHERE category='صنايعي'"),
        "engineers": c("SELECT COUNT(*) FROM con_workers WHERE category='مهندس'"),
        "available": c("SELECT COUNT(*) FROM con_workers WHERE available=1"),
        "contractors": c("SELECT COUNT(*) FROM entities WHERE sector='contracting' AND kind='contractor'"),
        "suppliers": c("SELECT COUNT(*) FROM entities WHERE sector='contracting' AND kind='supplier'"),
        "projects_active": c("SELECT COUNT(*) FROM con_projects WHERE status='قيد التنفيذ'"),
        "projects_total": c("SELECT COUNT(*) FROM con_projects"),
        "budget_total": c("SELECT COALESCE(SUM(budget),0) FROM con_projects"),
        "trades": rows(conn.execute("SELECT trade, COUNT(*) c FROM con_workers GROUP BY trade ORDER BY c DESC")),
    }
    conn.close()
    return data


# ============================================================================
# القطاع العقاري — التطوير + التسويق (مترابطان)
# ============================================================================
@app.get("/api/re/dashboard")
def re_dashboard(request: Request):
    require(request)
    conn = db.get_conn()
    def c(qq, *a):
        return conn.execute(qq, a).fetchone()[0]
    data = {
        "developers": c("SELECT COUNT(*) FROM entities WHERE sector='realestate' AND kind='developer'"),
        "projects": c("SELECT COUNT(*) FROM re_projects"),
        "units_total": c("SELECT COUNT(*) FROM re_units"),
        "units_available": c("SELECT COUNT(*) FROM re_units WHERE status='متاح'"),
        "units_sold": c("SELECT COUNT(*) FROM re_units WHERE status='مباع'"),
        "leads": c("SELECT COUNT(*) FROM mkt_leads WHERE sector='realestate'"),
        "sales_value": c("SELECT COALESCE(SUM(price),0) FROM re_units WHERE status='مباع'"),
        "inventory_value": c("SELECT COALESCE(SUM(price),0) FROM re_units WHERE status='متاح'"),
        "funnel": rows(conn.execute(
            "SELECT stage, COUNT(*) c FROM mkt_leads WHERE sector='realestate' GROUP BY stage")),
    }
    conn.close()
    return data


@app.get("/api/re/projects")
def re_projects(request: Request):
    require(request)
    conn = db.get_conn()
    r = rows(conn.execute(
        """SELECT p.*, e.name AS developer,
                  (SELECT COUNT(*) FROM re_units u WHERE u.project_id=p.id AND u.status='متاح') AS available
           FROM re_projects p LEFT JOIN entities e ON e.id=p.developer_id ORDER BY p.id DESC"""))
    conn.close()
    return r


class ReProjectIn(BaseModel):
    name: str
    developer_id: Optional[int] = None
    governorate: Optional[str] = None
    project_type: str = "سكني"
    delivery_date: Optional[str] = None
    notes: Optional[str] = None


@app.post("/api/re/projects")
def add_re_project(request: Request, data: ReProjectIn):
    require(request)
    conn = db.get_conn()
    cur = conn.execute(
        """INSERT INTO re_projects (name,developer_id,governorate,project_type,units_total,units_sold,status,delivery_date,notes,created_at)
           VALUES (?,?,?,?,0,0,'قيد التطوير',?,?,?)""",
        (data.name, data.developer_id, data.governorate, data.project_type, data.delivery_date, data.notes, now()))
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return {"id": pid}


@app.get("/api/re/units")
def re_units(request: Request, project_id: Optional[int] = None, status: Optional[str] = None):
    require(request)
    conn = db.get_conn()
    q = """SELECT u.*, p.name AS project FROM re_units u
           LEFT JOIN re_projects p ON p.id=u.project_id WHERE 1=1"""
    a = []
    if project_id:
        q += " AND u.project_id=?"; a.append(project_id)
    if status:
        q += " AND u.status=?"; a.append(status)
    q += " ORDER BY u.id DESC"
    r = rows(conn.execute(q, a))
    conn.close()
    return r


class ReUnitIn(BaseModel):
    project_id: int
    unit_code: Optional[str] = None
    unit_type: str = "شقة"
    area: float = 0
    price: float = 0
    listing_channel: Optional[str] = None


def _sync_project_units(conn, project_id):
    tot = conn.execute("SELECT COUNT(*) FROM re_units WHERE project_id=?", (project_id,)).fetchone()[0]
    sold = conn.execute("SELECT COUNT(*) FROM re_units WHERE project_id=? AND status='مباع'", (project_id,)).fetchone()[0]
    conn.execute("UPDATE re_projects SET units_total=?, units_sold=? WHERE id=?", (tot, sold, project_id))


@app.post("/api/re/units")
def add_re_unit(request: Request, data: ReUnitIn):
    require(request)
    conn = db.get_conn()
    cur = conn.execute(
        """INSERT INTO re_units (project_id,unit_code,unit_type,area,price,status,listing_channel,created_at)
           VALUES (?,?,?,?,?, 'متاح', ?, ?)""",
        (data.project_id, data.unit_code, data.unit_type, data.area, data.price, data.listing_channel, now()))
    uid = cur.lastrowid
    _sync_project_units(conn, data.project_id)
    conn.commit()
    conn.close()
    return {"id": uid}


@app.put("/api/re/units/{uid}/status")
def re_unit_status(request: Request, uid: int, status: str = Form(...)):
    require(request)
    conn = db.get_conn()
    u = one(conn.execute("SELECT * FROM re_units WHERE id=?", (uid,)))
    if not u:
        conn.close()
        raise HTTPException(404, "الوحدة غير موجودة")
    conn.execute("UPDATE re_units SET status=? WHERE id=?", (status, uid))
    # عند البيع: سجّل دفعة إيراد
    if status == "مباع" and u["status"] != "مباع":
        conn.execute(
            """INSERT INTO payments (sector,direction,ref_type,ref_id,amount,status,paid_at,created_at)
               VALUES ('realestate','in','unit',?,?, 'مدفوع', ?, ?)""",
            (uid, u["price"], now(), now()))
    _sync_project_units(conn, u["project_id"])
    conn.commit()
    conn.close()
    return {"ok": True}


# ---- التسويق العقاري: العملاء المحتملون (Leads / قمع البيع) ----
@app.get("/api/re/leads")
def re_leads(request: Request, stage: Optional[str] = None):
    require(request)
    conn = db.get_conn()
    q = """SELECT l.*, u.unit_code, p.name AS project, ag.full_name AS agent
           FROM mkt_leads l
           LEFT JOIN re_units u ON u.id=l.unit_id
           LEFT JOIN re_projects p ON p.id=u.project_id
           LEFT JOIN users ag ON ag.id=l.assigned_to
           WHERE l.sector='realestate'"""
    a = []
    if stage:
        q += " AND l.stage=?"; a.append(stage)
    q += " ORDER BY l.id DESC"
    r = rows(conn.execute(q, a))
    conn.close()
    return r


class LeadIn(BaseModel):
    name: str
    phone: Optional[str] = None
    source: Optional[str] = None
    interest: Optional[str] = None
    unit_id: Optional[int] = None


@app.post("/api/re/leads")
def add_lead(request: Request, data: LeadIn):
    u = require(request)
    conn = db.get_conn()
    cur = conn.execute(
        """INSERT INTO mkt_leads (sector,name,phone,source,interest,stage,assigned_to,unit_id,created_at)
           VALUES ('realestate',?,?,?,?, 'جديد', ?, ?, ?)""",
        (data.name, data.phone, data.source, data.interest, u["id"], data.unit_id, now()))
    conn.commit()
    lid = cur.lastrowid
    conn.close()
    return {"id": lid}


@app.put("/api/re/leads/{lid}/stage")
def lead_stage(request: Request, lid: int, stage: str = Form(...), unit_id: Optional[int] = Form(None)):
    require(request)
    conn = db.get_conn()
    lead = one(conn.execute("SELECT * FROM mkt_leads WHERE id=?", (lid,)))
    if not lead:
        conn.close()
        raise HTTPException(404, "العميل غير موجود")
    target_unit = unit_id if unit_id else lead["unit_id"]
    conn.execute("UPDATE mkt_leads SET stage=?, unit_id=? WHERE id=?", (stage, target_unit, lid))
    # إتمام الصفقة: عند مرحلة "تم" مع وحدة مرتبطة → بيع الوحدة تلقائيًا
    if stage == "تم" and target_unit:
        u = one(conn.execute("SELECT * FROM re_units WHERE id=?", (target_unit,)))
        if u and u["status"] != "مباع":
            conn.execute("UPDATE re_units SET status='مباع' WHERE id=?", (target_unit,))
            conn.execute(
                """INSERT INTO payments (sector,direction,ref_type,ref_id,amount,status,paid_at,created_at)
                   VALUES ('realestate','in','unit',?,?, 'مدفوع', ?, ?)""",
                (target_unit, u["price"], now(), now()))
            _sync_project_units(conn, u["project_id"])
    conn.commit()
    conn.close()
    return {"ok": True}


# ============================================================================
# التسويق العام — قمع العملاء (يشارك mkt_leads مع sector='general')
# ============================================================================
@app.get("/api/mkt/leads")
def mkt_leads_list(request: Request, stage: Optional[str] = None):
    require(request)
    conn = db.get_conn()
    q = "SELECT * FROM mkt_leads WHERE sector='general'"
    a = []
    if stage:
        q += " AND stage=?"; a.append(stage)
    q += " ORDER BY id DESC"
    r = rows(conn.execute(q, a))
    conn.close()
    return r


class MktLeadIn(BaseModel):
    name: str
    phone: Optional[str] = None
    source: Optional[str] = None
    interest: Optional[str] = None


@app.post("/api/mkt/leads")
def add_mkt_lead(request: Request, data: MktLeadIn):
    u = require(request)
    conn = db.get_conn()
    cur = conn.execute(
        """INSERT INTO mkt_leads (sector,name,phone,source,interest,stage,assigned_to,created_at)
           VALUES ('general',?,?,?,?, 'جديد', ?, ?)""",
        (data.name, data.phone, data.source, data.interest, u["id"], now()))
    conn.commit()
    lid = cur.lastrowid
    conn.close()
    return {"id": lid}


@app.put("/api/mkt/leads/{lid}/stage")
def mkt_lead_stage(request: Request, lid: int, stage: str = Form(...)):
    require(request)
    conn = db.get_conn()
    conn.execute("UPDATE mkt_leads SET stage=? WHERE id=? AND sector='general'", (stage, lid))
    conn.commit()
    conn.close()
    return {"ok": True}


# ============================================================================
# التنقل — الرحلات
# ============================================================================
@app.get("/api/mob/trips")
def mob_trips(request: Request, status: Optional[str] = None):
    require(request)
    conn = db.get_conn()
    q = "SELECT * FROM mob_trips WHERE 1=1"
    a = []
    if status:
        q += " AND status=?"; a.append(status)
    q += " ORDER BY id DESC"
    r = rows(conn.execute(q, a))
    conn.close()
    return r


class TripIn(BaseModel):
    passenger_name: str
    driver_name: Optional[str] = None
    from_loc: Optional[str] = None
    to_loc: Optional[str] = None
    fare: float = 0


@app.post("/api/mob/trips")
def add_trip(request: Request, data: TripIn):
    require(request)
    conn = db.get_conn()
    cur = conn.execute(
        """INSERT INTO mob_trips (passenger_name,driver_name,from_loc,to_loc,fare,status,requested_at,created_at)
           VALUES (?,?,?,?,?, 'مطلوبة', ?, ?)""",
        (data.passenger_name, data.driver_name, data.from_loc, data.to_loc, data.fare, now(), now()))
    conn.commit()
    tid = cur.lastrowid
    conn.close()
    return {"id": tid}


@app.put("/api/mob/trips/{tid}/status")
def trip_status(request: Request, tid: int, status: str = Form(...)):
    require(request)
    conn = db.get_conn()
    conn.execute("UPDATE mob_trips SET status=? WHERE id=?", (status, tid))
    if status == "مكتملة":
        t = one(conn.execute("SELECT fare FROM mob_trips WHERE id=?", (tid,)))
        if t and t["fare"]:
            conn.execute("""INSERT INTO payments (sector,direction,ref_type,ref_id,amount,status,paid_at,created_at)
                            VALUES ('mobility','in','trip',?,?, 'مدفوع', ?, ?)""", (tid, t["fare"], now(), now()))
    conn.commit()
    conn.close()
    return {"ok": True}


# ============================================================================
# الشحن واللوجستيات — الشحنات
# ============================================================================
@app.get("/api/log/shipments")
def log_shipments(request: Request, status: Optional[str] = None, q: Optional[str] = None):
    require(request)
    conn = db.get_conn()
    sql = "SELECT * FROM log_shipments WHERE 1=1"
    a = []
    if status:
        sql += " AND status=?"; a.append(status)
    if q:
        sql += " AND (tracking_no LIKE ? OR sender LIKE ? OR receiver LIKE ?)"
        a += [f"%{q}%", f"%{q}%", f"%{q}%"]
    sql += " ORDER BY id DESC"
    r = rows(conn.execute(sql, a))
    conn.close()
    return r


class ShipmentIn(BaseModel):
    sender: str
    receiver: str
    from_gov: Optional[str] = None
    to_gov: Optional[str] = None
    weight: float = 0
    cost: float = 0


@app.post("/api/log/shipments")
def add_shipment(request: Request, data: ShipmentIn):
    require(request)
    conn = db.get_conn()
    # توليد رقم تتبّع
    n = conn.execute("SELECT COUNT(*) FROM log_shipments").fetchone()[0] + 1
    trk = f"EG{datetime.now().strftime('%y%m')}{n:04d}"
    cur = conn.execute(
        """INSERT INTO log_shipments (tracking_no,sender,receiver,from_gov,to_gov,weight,cost,status,created_at)
           VALUES (?,?,?,?,?,?,?, 'قيد التجهيز', ?)""",
        (trk, data.sender, data.receiver, data.from_gov, data.to_gov, data.weight, data.cost, now()))
    conn.commit()
    sid = cur.lastrowid
    conn.close()
    return {"id": sid, "tracking_no": trk}


@app.put("/api/log/shipments/{sid}/status")
def shipment_status(request: Request, sid: int, status: str = Form(...)):
    require(request)
    conn = db.get_conn()
    conn.execute("UPDATE log_shipments SET status=? WHERE id=?", (status, sid))
    conn.commit()
    conn.close()
    return {"ok": True}


# ============================================================================
# الزراعة — المزارع والمحاصيل
# ============================================================================
@app.get("/api/agr/farms")
def agr_farms(request: Request, status: Optional[str] = None):
    require(request)
    conn = db.get_conn()
    q = "SELECT * FROM agr_farms WHERE 1=1"
    a = []
    if status:
        q += " AND status=?"; a.append(status)
    q += " ORDER BY id DESC"
    r = rows(conn.execute(q, a))
    conn.close()
    return r


class FarmIn(BaseModel):
    owner_name: str
    governorate: Optional[str] = None
    area_feddan: float = 0
    crop: Optional[str] = None
    season: Optional[str] = None


@app.post("/api/agr/farms")
def add_farm(request: Request, data: FarmIn):
    require(request)
    conn = db.get_conn()
    cur = conn.execute(
        """INSERT INTO agr_farms (owner_name,governorate,area_feddan,crop,season,status,created_at)
           VALUES (?,?,?,?,?, 'قيد الزراعة', ?)""",
        (data.owner_name, data.governorate, data.area_feddan, data.crop, data.season, now()))
    conn.commit()
    fid = cur.lastrowid
    conn.close()
    return {"id": fid}


@app.put("/api/agr/farms/{fid}/status")
def farm_status(request: Request, fid: int, status: str = Form(...)):
    require(request)
    conn = db.get_conn()
    conn.execute("UPDATE agr_farms SET status=? WHERE id=?", (status, fid))
    conn.commit()
    conn.close()
    return {"ok": True}


# ============================================================================
# المحاماة — القضايا والجلسات
# ============================================================================
@app.get("/api/law/cases")
def law_cases(request: Request, status: Optional[str] = None, case_type: Optional[str] = None):
    require(request)
    conn = db.get_conn()
    q = "SELECT * FROM law_cases WHERE 1=1"
    a = []
    if status:
        q += " AND status=?"; a.append(status)
    if case_type:
        q += " AND case_type=?"; a.append(case_type)
    q += " ORDER BY next_session"
    r = rows(conn.execute(q, a))
    conn.close()
    return r


class CaseIn(BaseModel):
    case_no: Optional[str] = None
    client_name: str
    case_type: str = "مدني"
    court: Optional[str] = None
    next_session: Optional[str] = None
    notes: Optional[str] = None


@app.post("/api/law/cases")
def add_case(request: Request, data: CaseIn):
    require(request)
    conn = db.get_conn()
    cur = conn.execute(
        """INSERT INTO law_cases (case_no,client_name,case_type,court,status,next_session,notes,created_at)
           VALUES (?,?,?,?, 'مفتوحة', ?, ?, ?)""",
        (data.case_no, data.client_name, data.case_type, data.court, data.next_session, data.notes, now()))
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    return {"id": cid}


@app.put("/api/law/cases/{cid}")
def update_case(request: Request, cid: int, status: str = Form(...), next_session: str = Form("")):
    require(request)
    conn = db.get_conn()
    conn.execute("UPDATE law_cases SET status=?, next_session=? WHERE id=?", (status, next_session, cid))
    conn.commit()
    conn.close()
    return {"ok": True}


# ============================================================================
# ملخص باقي القطاعات (نظرة عامة — جاهزة للتوسّع)
# ============================================================================
@app.get("/api/sectors/overview")
def sectors_overview(request: Request):
    require(request)
    return {
        "sectors": [
            {"key": "medical", "name": "القطاع الطبي", "icon": "🏥", "status": "مُنفَّذ بالكامل",
             "features": ["عيادات وأطباء وتخصصات", "صيدليات ومراكز أشعة وتحاليل", "تقييم الأطباء",
                          "السجل المرضي لكل مريض", "الحجوزات", "سلسلة: حجز←تشخيص←أشعة←تحاليل←صيدلية←متابعة", "العمليات"]},
            {"key": "contracting", "name": "قطاع المقاولات", "icon": "🏗️", "status": "مُنفَّذ بالكامل",
             "features": ["بناء/تشطيب/ديكور/أثاث ومفروشات/هدم/نظافة", "دليل صنايعية ومهندسين بالتقييم",
                          "مقاولون وموردون (كيانات)", "أوامر الشغل ونسب الإنجاز",
                          "الأوراق الرسمية والتصاريح", "تقييم الصنايعية بالنجوم"]},
            {"key": "realestate", "name": "التطوير العقاري", "icon": "🏢", "status": "مُنفَّذ بالكامل",
             "features": ["المطورون والمشاريع", "الوحدات (شقق/فلل/محلات/مكاتب)", "حالة البيع والمخزون",
                          "قيمة المبيعات والمخزون"]},
            {"key": "re_marketing", "name": "التسويق العقاري", "icon": "🏘️", "status": "مُنفَّذ بالكامل",
             "features": ["قمع العملاء المحتملين (Leads)", "قنوات التسويق", "مراحل البيع",
                          "إتمام الصفقة يبيع الوحدة ويسجّل الإيراد تلقائيًا"]},
            {"key": "marketing", "name": "التسويق العام", "icon": "📣", "status": "مُنفَّذ بالكامل",
             "features": ["قمع العملاء المحتملين", "مصادر العملاء", "مراحل التفاوض"]},
            {"key": "mobility", "name": "قطاع التنقل", "icon": "🚗", "status": "مُنفَّذ بالكامل",
             "features": ["الرحلات والسائقون", "الأجرة والحالة", "تحصيل تلقائي عند الاكتمال"]},
            {"key": "logistics", "name": "الشحن واللوجستيات", "icon": "🚚", "status": "مُنفَّذ بالكامل",
             "features": ["تتبّع الشحنات برقم تتبّع", "المرسل/المستلم/الوزن/التكلفة", "حالة الشحن"]},
            {"key": "agriculture", "name": "قطاع الزراعة", "icon": "🌾", "status": "مُنفَّذ بالكامل",
             "features": ["المزارع والمحاصيل", "المساحة بالفدان", "المواسم وحالة المحصول"]},
            {"key": "law", "name": "قطاع المحاماة", "icon": "⚖️", "status": "مُنفَّذ بالكامل",
             "features": ["القضايا بأنواعها", "المحاكم والجلسات القادمة", "حالة القضية"]},
        ]
    }


# ----------------------------------------------------------------------------
# خدمة الواجهة الثابتة
# ----------------------------------------------------------------------------
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
