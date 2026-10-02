import os
import uuid
import json
import secrets
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for, session, jsonify,
    flash, abort, send_from_directory
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from PIL import Image, ImageOps

import database as db
import whatsapp as wa

APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_local_env():
    env_path = os.path.join(APP_DIR, ".env")
    if not os.path.isfile(env_path):
        return
    with open(env_path, encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if key:
                os.environ.setdefault(key, value)


_load_local_env()

UPLOAD_SERVICES = os.path.join(APP_DIR, "static", "uploads", "services")
UPLOAD_REFS = os.path.join(APP_DIR, "static", "uploads", "referencias")
UPLOAD_CLIENTS = os.path.join(APP_DIR, "static", "uploads", "clientes")
UPLOAD_GALLERY_ORIG = os.path.join(APP_DIR, "static", "uploads", "gallery", "original")
UPLOAD_GALLERY_THUMB = os.path.join(APP_DIR, "static", "uploads", "gallery", "thumb")
ALLOWED_EXT = {"png", "jpg", "jpeg", "webp", "gif"}
MAX_UPLOAD_MB = 4
GALLERY_MAX_FILES_PER_BATCH = 40
GALLERY_FULL_MAX_WIDTH = 1600
GALLERY_THUMB_MAX_WIDTH = 480

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "cambia-esta-clave-en-produccion")
app.config["MAX_CONTENT_LENGTH"] = 120 * 1024 * 1024  # permite lotes grandes de fotos

DOW = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]  # display only
MONTHS = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
          "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]


# ------------------------------------------------------------------ helpers
def get_settings():
    conn = db.get_db()
    row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    conn.close()
    s = dict(row)
    s["owner_whatsapp"] = s.get("owner_whatsapp") or "573057484342"
    s["work_days"] = db.jload(s["work_days"], [1, 2, 3, 4, 5, 6])
    s["blocked_dates"] = db.jload(s["blocked_dates"], [])
    s["vacations"] = db.jload(s["vacations"], [])
    s["blocked_slots"] = db.jload(s["blocked_slots"], [])
    return s


def update_settings(**kwargs):
    if not kwargs:
        return
    conn = db.get_db()
    cols = ", ".join(f"{k} = ?" for k in kwargs)
    values = []
    for v in kwargs.values():
        values.append(db.jdump(v) if isinstance(v, (list, dict)) else v)
    conn.execute(f"UPDATE settings SET {cols} WHERE id = 1", values)
    conn.commit()
    conn.close()


def get_services(active_only=False):
    conn = db.get_db()
    q = "SELECT * FROM services"
    if active_only:
        q += " WHERE active = 1"
    q += " ORDER BY category, sort_order, id"
    rows = [dict(r) for r in conn.execute(q).fetchall()]
    conn.close()
    return rows


def get_service(sid):
    conn = db.get_db()
    row = conn.execute("SELECT * FROM services WHERE id = ?", (sid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_bookings(status=None, date=None):
    conn = db.get_db()
    q, params = "SELECT * FROM bookings WHERE 1=1", []
    if status and status != "TODAS":
        q += " AND status = ?"; params.append(status)
    if date:
        q += " AND date = ?"; params.append(date)
    q += " ORDER BY date, time"
    rows = [dict(r) for r in conn.execute(q, params).fetchall()]
    conn.close()
    for b in rows:
        b["add_ons"] = db.jload(b["add_ons"], [])
    return rows


def get_booking(bid):
    conn = db.get_db()
    row = conn.execute("SELECT * FROM bookings WHERE id = ?", (bid,)).fetchone()
    conn.close()
    if not row:
        return None
    b = dict(row)
    b["add_ons"] = db.jload(b["add_ons"], [])
    return b


def human_date(iso):
    try:
        d = datetime.strptime(iso, "%Y-%m-%d")
        return f"{d.day} de {MONTHS[d.month - 1]} de {d.year}"
    except Exception:
        return iso


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def is_work_day(settings, iso):
    d = datetime.strptime(iso, "%Y-%m-%d")
    weekday_py = d.weekday()  # Monday=0 .. Sunday=6
    if iso in settings["blocked_dates"]:
        return False
    for v in settings["vacations"]:
        if v["start"] <= iso <= v["end"]:
            return False
    return weekday_py in settings["work_days"]


def slots_for_date(settings, iso):
    if not is_work_day(settings, iso):
        return []
    sh, sm = map(int, settings["work_start"].split(":"))
    eh, em = map(int, settings["work_end"].split(":"))
    cur = sh * 60 + sm
    end = eh * 60 + em
    dur = int(settings["slot_duration"]) or 60
    blocked_today = [b["time"] for b in settings["blocked_slots"] if b["date"] == iso]
    taken_today = [b["time"] for b in get_bookings(date=iso) if b["status"] != "CANCELADA"]
    out = []
    while cur + dur <= end:
        t = f"{cur // 60:02d}:{cur % 60:02d}"
        reason = "booked" if t in taken_today else ("blocked" if t in blocked_today else None)
        out.append({"time": t, "taken": reason is not None, "reason": reason})
        cur += dur
    return out


def new_booking_id():
    return "RES-" + uuid.uuid4().hex[:8].upper()


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT


def save_gallery_image(file_storage):
    """
    Guarda una foto para la galería en DOS tamaños (para que la web cargue
    rápido en celular aunque haya cientos de fotos):
      - original redimensionado a máx. 1600px de ancho (calidad completa)
      - miniatura a 480px de ancho (la que se ve en la cuadrícula)
    Corrige la orientación EXIF (fotos tomadas con el celular en vertical).
    Devuelve (filename, thumb_filename) o (None, None) si el archivo no es válido.
    """
    if not file_storage or not file_storage.filename or not allowed_file(file_storage.filename):
        return None, None
    try:
        img = Image.open(file_storage.stream)
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB",):
            img = img.convert("RGB")
    except Exception:
        return None, None

    base = uuid.uuid4().hex
    filename = f"{base}.jpg"
    thumb_filename = f"{base}_thumb.jpg"

    full = img.copy()
    if full.width > GALLERY_FULL_MAX_WIDTH:
        ratio = GALLERY_FULL_MAX_WIDTH / full.width
        full = full.resize((GALLERY_FULL_MAX_WIDTH, int(full.height * ratio)), Image.LANCZOS)
    full.save(os.path.join(UPLOAD_GALLERY_ORIG, filename), "JPEG", quality=85, optimize=True)

    thumb = img.copy()
    if thumb.width > GALLERY_THUMB_MAX_WIDTH:
        ratio = GALLERY_THUMB_MAX_WIDTH / thumb.width
        thumb = thumb.resize((GALLERY_THUMB_MAX_WIDTH, int(thumb.height * ratio)), Image.LANCZOS)
    thumb.save(os.path.join(UPLOAD_GALLERY_THUMB, thumb_filename), "JPEG", quality=78, optimize=True)

    return filename, thumb_filename


def save_client_avatar(file_storage):
    if not file_storage or not file_storage.filename or not allowed_file(file_storage.filename):
        return None
    try:
        image = Image.open(file_storage.stream)
        image = ImageOps.exif_transpose(image).convert("RGB")
        image = ImageOps.fit(image, (320, 320), method=Image.Resampling.LANCZOS)
    except Exception:
        return None
    os.makedirs(UPLOAD_CLIENTS, exist_ok=True)
    filename = f"perfil_{uuid.uuid4().hex}.jpg"
    image.save(os.path.join(UPLOAD_CLIENTS, filename), "JPEG", quality=86, optimize=True)
    return filename


def get_gallery(active_only=False, category=None):
    conn = db.get_db()
    q = "SELECT * FROM gallery_images WHERE 1=1"
    params = []
    if active_only:
        q += " AND active = 1"
    if category and category != "Todos":
        q += " AND category = ?"; params.append(category)
    q += " ORDER BY sort_order DESC, id DESC"
    rows = [dict(r) for r in conn.execute(q, params).fetchall()]
    conn.close()
    return rows


def client_login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if not session.get("client_id"):
            return redirect(url_for("cuenta", next=request.path))
        return view(*a, **kw)
    return wrapped


def get_client(client_id):
    conn = db.get_db()
    row = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def client_bookings(client):
    conn = db.get_db()
    rows = conn.execute("SELECT * FROM bookings WHERE client_id = ? ORDER BY date DESC, time DESC", (client["id"],)).fetchall()
    conn.close()
    result = [dict(row) for row in rows]
    for booking in result:
        booking["add_ons"] = db.jload(booking["add_ons"], [])
    return result


def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if not session.get("is_admin"):
            return redirect(url_for("cuenta", next=request.path))
        return view(*a, **kw)
    return wrapped


@app.context_processor
def inject_globals():
    current_client = get_client(session["client_id"]) if session.get("client_id") and not session.get("is_admin") else None
    return {"settings": get_settings(), "wa_link": wa.wa_link, "digits": wa.digits,
             "current_client": current_client, "current_year": datetime.now().year}


# ------------------------------------------------------------------ public site
@app.route("/")
def index():
    services = get_services(active_only=True)
    categories = []
    for s in services:
        if s["category"] != "Adicionales" and s["category"] not in categories:
            categories.append(s["category"])
    highlights = get_gallery(active_only=True)[:8]
    return render_template("index.html", categories=categories, highlights=highlights, services=services)


@app.route("/catalogo")
def catalogo():
    services = get_services(active_only=True)
    cat_filter = request.args.get("categoria", "Todos")
    categories = ["Todos"] + sorted({s["category"] for s in services})
    if cat_filter != "Todos":
        services = [s for s in services if s["category"] == cat_filter]
    return render_template("catalogo.html", services=services, categories=categories,
                            active_cat=cat_filter)


@app.route("/servicio/<int:sid>")
def servicio_detalle(sid):
    s = get_service(sid)
    if not s or not s["active"]:
        abort(404)
    return render_template("servicio.html", s=s)


@app.route("/politicas")
def politicas():
    policies_image = next(iter(get_gallery(active_only=True)), None)
    return render_template("politicas.html", policies_image=policies_image)


@app.route("/cookies")
def cookies():
    return render_template("cookies.html")


@app.route("/terminos")
def terminos():
    return render_template("terminos.html")


@app.route("/galeria")
def galeria():
    cat_filter = request.args.get("categoria", "Todos")
    all_images = get_gallery(active_only=True)
    categories = ["Todos"] + sorted({g["category"] for g in all_images if g["category"]})
    images = [g for g in all_images if cat_filter == "Todos" or g["category"] == cat_filter]
    return render_template("galeria.html", images=images, categories=categories, active_cat=cat_filter)


@app.route("/reservar")
def reservar():
    services = get_services(active_only=True)
    addons = [s for s in services if s["category"] == "Adicionales"]
    principal = [s for s in services if s["category"] != "Adicionales"]
    categories = []
    for s in principal:
        if s["category"] not in categories:
            categories.append(s["category"])
    preselect = request.args.get("service", type=int)
    return render_template("reservar.html", services=principal, addons=addons,
                            categories=categories, preselect=preselect, today=today_str(),
                            booking_gallery=get_gallery(active_only=True)[:3],
                            client=get_client(session["client_id"]) if session.get("client_id") else None)


@app.route("/api/disponibilidad")
def api_disponibilidad():
    fecha = request.args.get("fecha", "")
    settings = get_settings()
    try:
        datetime.strptime(fecha, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "fecha inválida"}), 400
    return jsonify({"fecha": fecha, "slots": slots_for_date(settings, fecha)})


@app.route("/api/disponibilidad/mes")
def api_disponibilidad_mes():
    """Resumen público de disponibilidad mensual; nunca incluye datos de clientas."""
    try:
        year = int(request.args.get("anio", datetime.now().year))
        month = int(request.args.get("mes", datetime.now().month))
        first = datetime(year, month, 1)
    except (TypeError, ValueError):
        return jsonify({"error": "mes inválido"}), 400
    if year < datetime.now().year or year > datetime.now().year + 2:
        return jsonify({"error": "año fuera de rango"}), 400
    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)
    days = (next_month - first).days
    settings = get_settings()
    today = today_str()
    result = []
    for day in range(1, days + 1):
        iso = f"{year:04d}-{month:02d}-{day:02d}"
        slots = slots_for_date(settings, iso)
        open_count = sum(not slot["taken"] for slot in slots)
        taken_count = sum(slot["taken"] for slot in slots)
        available = open_count if iso >= today else 0
        result.append({"date": iso, "available": available, "taken": taken_count, "total": len(slots),
                       "state": "closed" if not slots or iso < today else ("full" if not available else "available")})
    return jsonify({"year": year, "month": month, "days": result})


@app.route("/api/reservas", methods=["POST"])
def api_crear_reserva():
    settings = get_settings()
    service_id = request.form.get("service_id", type=int)
    date = request.form.get("date", "")
    time = request.form.get("time", "")
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    observations = request.form.get("observations", "").strip()
    add_on_ids = request.form.getlist("add_ons")

    service = get_service(service_id)
    phone_digits = wa.digits(phone)
    if len(phone_digits) == 10 and phone_digits.startswith("3"):
        phone_digits = "57" + phone_digits
    if not service or not name or len(name) < 2 or len(name) > 100 or len(phone_digits) not in (10, 12) or not date or not time:
        flash("Revisa el servicio, nombre, teléfono, fecha y hora. El celular debe tener 10 dígitos.", "error")
        return redirect(url_for("reservar", service=service_id))
    phone = phone_digits
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        flash("La fecha no es válida.", "error")
        return redirect(url_for("reservar", service=service_id))
    if date < today_str():
        flash("Elige una fecha futura.", "error")
        return redirect(url_for("reservar", service=service_id))
    if not is_work_day(settings, date):
        flash("Ese día no está disponible. Elige otra fecha.", "error")
        return redirect(url_for("reservar", service=service_id))

    allowed_slots = {slot["time"] for slot in slots_for_date(settings, date)}
    if time not in allowed_slots:
        flash("El horario seleccionado no es válido. Elige uno disponible.", "error")
        return redirect(url_for("reservar", service=service_id))
    conflict = get_bookings(date=date)
    if any(b["time"] == time and b["status"] != "CANCELADA" for b in conflict):
        flash("Ese horario ya no está disponible. Elige otro.", "error")
        return redirect(url_for("reservar", service=service_id))

    ref_filename = None
    file = request.files.get("reference_image")
    if file and file.filename and allowed_file(file.filename):
        ext = file.filename.rsplit(".", 1)[1].lower()
        ref_filename = f"{uuid.uuid4().hex}.{ext}"
        file.save(os.path.join(UPLOAD_REFS, ref_filename))

    bid = new_booking_id()
    conn = db.get_db()
    conn.execute(
        "INSERT INTO bookings (id, service_id, service_name, client_name, phone, date, time, "
        "add_ons, observations, reference_image, client_id, status, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (bid, service_id, service["name"], name, phone, date, time,
         db.jdump(add_on_ids), observations, ref_filename, session.get("client_id"), "PENDIENTE", db.now_iso()),
    )
    conn.commit()
    conn.close()

    # Intento de envío real vía Cloud API (si hay credenciales configuradas);
    # si no las hay, no se envía nada automáticamente y se usa el botón wa.me.
    if settings["owner_whatsapp"] and settings["wa_cloud_token"] and settings["wa_cloud_phone_id"]:
        addon_names = [get_service(int(i))["name"] for i in add_on_ids if str(i).isdigit() and get_service(int(i))]
        msg = wa.owner_message({
            "id": bid, "client_name": name, "phone": phone, "date_human": human_date(date),
            "time": time, "observations": observations, "add_ons_names": addon_names,
        }, service["name"])
        wa.send_via_cloud_api(settings["wa_cloud_token"], settings["wa_cloud_phone_id"],
                               settings["owner_whatsapp"], msg)
        if ref_filename:
            image_path = os.path.join(UPLOAD_REFS, ref_filename)
            image_result = wa.send_image_via_cloud_api(settings["wa_cloud_token"], settings["wa_cloud_phone_id"],
                settings["owner_whatsapp"], image_path,
                f"Referencia de {name} para {service['name']} — {human_date(date)} a las {time}")
            if image_result.get("sent"):
                flash("La imagen de referencia fue enviada al WhatsApp de la dueña.", "ok")
            else:
                flash("La cita y la imagen quedaron guardadas, pero WhatsApp no aceptó el envío. Revisa las credenciales de WhatsApp Business.", "error")
        client_msg = wa.client_request_message({
            "client_name": name, "date_human": human_date(date), "time": time,
            "add_ons_names": addon_names,
        }, service["name"], settings["business_name"])
        wa.send_via_cloud_api(settings["wa_cloud_token"], settings["wa_cloud_phone_id"], phone, client_msg)
    elif ref_filename:
        flash("La imagen quedó guardada con la cita. Para enviarla automáticamente al WhatsApp de la dueña configura WhatsApp Business en administración.", "ok")

    return redirect(url_for("reserva_exito", bid=bid))


@app.route("/reserva/<bid>/exito")
def reserva_exito(bid):
    b = get_booking(bid)
    if not b:
        abort(404)
    settings = get_settings()
    addon_names = [get_service(int(i))["name"] for i in b["add_ons"] if get_service(int(i))]
    msg = wa.owner_message({
        "id": b["id"], "client_name": b["client_name"], "phone": b["phone"],
        "date_human": human_date(b["date"]), "time": b["time"],
        "observations": b["observations"], "add_ons_names": addon_names,
    }, b["service_name"])
    link = wa.wa_link(settings["owner_whatsapp"], msg) if settings["owner_whatsapp"] else None
    client_msg = wa.client_request_message({
        "client_name": b["client_name"], "date_human": human_date(b["date"]), "time": b["time"],
        "add_ons_names": addon_names,
    }, b["service_name"], settings["business_name"])
    client_link = wa.wa_link(b["phone"], client_msg)
    return render_template("reserva_exito.html", b=b, link=link, client_link=client_link)


# ------------------------------------------------------------------ auth
GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def _local_next(value):
    return value if value.startswith("/") and not value.startswith("//") and "\\" not in value else ""


@app.route("/auth/google")
def google_login():
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        flash("El acceso con Google todavía no está configurado. Añade GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET al archivo .env y reinicia la app.", "error")
        return redirect(url_for("cuenta"))
    if (not client_id.endswith(".apps.googleusercontent.com") or
            any(marker in client_id.upper() for marker in ("TU_CLIENT", "YOUR_CLIENT", "PEGA_AQUI", "PLACEHOLDER"))):
        flash("El Client ID de Google no es válido. Usa el ID real del cliente OAuth tipo Aplicación web, terminado en .apps.googleusercontent.com.", "error")
        return redirect(url_for("cuenta"))

    state = secrets.token_urlsafe(32)
    session["google_oauth_state"] = state
    session["google_oauth_next"] = _local_next(request.args.get("next", ""))
    params = {
        "client_id": client_id,
        "redirect_uri": url_for("google_callback", _external=True),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return redirect(GOOGLE_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params))


@app.route("/auth/google/callback")
def google_callback():
    expected_state = session.pop("google_oauth_state", "")
    received_state = request.args.get("state", "")
    if not expected_state or not received_state or not secrets.compare_digest(expected_state, received_state):
        flash("No se pudo validar el inicio de sesión con Google. Inténtalo de nuevo.", "error")
        return redirect(url_for("cuenta"))
    if request.args.get("error"):
        flash("El acceso con Google fue cancelado o rechazado.", "error")
        return redirect(url_for("cuenta"))

    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    code = request.args.get("code", "")
    if not client_id or not client_secret or not code:
        flash("Google no devolvió los datos necesarios para iniciar sesión.", "error")
        return redirect(url_for("cuenta"))

    redirect_uri = url_for("google_callback", _external=True)
    token_data = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode("utf-8")
    try:
        token_request = urllib.request.Request(GOOGLE_TOKEN_URL, data=token_data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(token_request, timeout=12) as response:
            token = json.loads(response.read().decode("utf-8"))
        access_token = token.get("access_token")
        if not access_token:
            raise ValueError("Google no entregó token de acceso")
        profile_request = urllib.request.Request(GOOGLE_USERINFO_URL, headers={"Authorization": "Bearer " + access_token})
        with urllib.request.urlopen(profile_request, timeout=12) as response:
            profile = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, json.JSONDecodeError):
        app.logger.exception("No fue posible validar el acceso con Google")
        flash("No fue posible completar el acceso con Google. Inténtalo de nuevo.", "error")
        return redirect(url_for("cuenta"))

    email = (profile.get("email") or "").strip().casefold()
    name = (profile.get("name") or "").strip()[:100]
    if not profile.get("sub") or not email or not profile.get("email_verified"):
        flash("Google no pudo verificar el correo de esta cuenta.", "error")
        return redirect(url_for("cuenta"))

    settings = get_settings()
    if email == (settings.get("admin_email") or "").casefold():
        next_path = session.pop("google_oauth_next", "")
        session.clear()
        session["is_admin"] = True
        return redirect(next_path or url_for("admin_dashboard"))

    conn = db.get_db()
    row = conn.execute("SELECT id FROM clients WHERE email = ? COLLATE NOCASE", (email,)).fetchone()
    if row:
        client_id_value = row["id"]
    else:
        try:
            cur = conn.execute(
                "INSERT INTO clients (name,email,phone,password_hash,created_at) VALUES (?,?,?,?,?)",
                (name or email.split("@")[0], email, "", generate_password_hash(secrets.token_urlsafe(48)), db.now_iso()),
            )
            conn.commit()
            client_id_value = cur.lastrowid
        except Exception:
            conn.rollback()
            row = conn.execute("SELECT id FROM clients WHERE email = ? COLLATE NOCASE", (email,)).fetchone()
            if not row:
                conn.close()
                flash("No fue posible crear tu perfil. Inténtalo otra vez.", "error")
                return redirect(url_for("cuenta"))
            client_id_value = row["id"]
    conn.close()
    next_path = session.pop("google_oauth_next", "")
    session.clear()
    session["client_id"] = client_id_value
    return redirect(next_path or url_for("perfil"))


@app.route("/login", methods=["GET", "POST"])
def login():
    settings = get_settings()
    first_time = not settings["password_hash"]
    if not first_time:
        return redirect(url_for("cuenta"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if first_time:
            p2 = request.form.get("password2", "")
            if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
                flash("Ingresa un correo válido para la cuenta administradora.", "error")
            elif len(password) < 8:
                flash("La contraseña debe tener mínimo 8 caracteres.", "error")
            elif password != p2:
                flash("Las contraseñas no coinciden.", "error")
            else:
                update_settings(admin_email=email, password_hash=generate_password_hash(password))
                session["is_admin"] = True
                return redirect(url_for("admin_dashboard"))
        elif (email == (settings.get("admin_email") or "").casefold() or
              email == (settings.get("admin_user") or "").casefold()) and check_password_hash(settings["password_hash"], password):
            session["is_admin"] = True
            session.pop("client_id", None)
            return redirect(request.args.get("next") or url_for("admin_dashboard"))
        else:
            flash("Correo o contraseña incorrectos.", "error")

    return render_template("login.html", first_time=first_time)


@app.route("/cuenta", methods=["GET", "POST"])
def cuenta():
    settings = get_settings()
    if request.method == "POST":
        action = request.form.get("action", "login")
        identity = request.form.get("identity", "").strip()
        password = request.form.get("password", "")

        # El acceso administrativo también funciona desde el login de clientas.
        admin_email = (settings.get("admin_email") or "").casefold()
        admin_user = (settings.get("admin_user") or "").casefold()
        if action == "login" and identity.casefold() in (admin_email, admin_user) and settings["password_hash"] and check_password_hash(settings["password_hash"], password):
            session["is_admin"] = True
            session.pop("client_id", None)
            return redirect(url_for("admin_dashboard"))

        conn = db.get_db()
        if action == "register":
            name = request.form.get("name", "").strip()
            email = identity.lower()
            phone = wa.digits(request.form.get("phone", ""))
            if len(phone) == 10 and phone.startswith("3"):
                phone = "57" + phone
            confirm = request.form.get("password2", "")
            if len(name) < 2 or len(name) > 100 or "@" not in email or len(phone) not in (10, 12) or len(password) < 8 or password != confirm:
                flash("Completa nombre, correo, celular válido y una contraseña de 8 caracteres que coincida.", "error")
            else:
                try:
                    cur = conn.execute("INSERT INTO clients (name,email,phone,password_hash,created_at) VALUES (?,?,?,?,?)",
                        (name, email, phone, generate_password_hash(password), db.now_iso()))
                    conn.commit()
                    conn.close()
                    session["client_id"] = cur.lastrowid
                    session.pop("is_admin", None)
                    return redirect(url_for("perfil"))
                except Exception:
                    flash("Ese correo ya tiene una cuenta. Inicia sesión.", "error")
        elif action == "login":
            row = conn.execute("SELECT * FROM clients WHERE email = ? COLLATE NOCASE", (identity,)).fetchone()
            if row and check_password_hash(row["password_hash"], password):
                session["client_id"] = row["id"]
                session.pop("is_admin", None)
                conn.close()
                return redirect(request.args.get("next") or url_for("perfil"))
            flash("Correo o contraseña incorrectos.", "error")
        conn.close()
    return render_template("cuenta.html", client=get_client(session["client_id"]) if session.get("client_id") else None,
                           gallery_images=get_gallery(active_only=True)[:3])


@app.route("/perfil", methods=["GET", "POST"])
@client_login_required
def perfil():
    client = get_client(session["client_id"])
    if not client:
        session.pop("client_id", None)
        return redirect(url_for("cuenta"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = wa.digits(request.form.get("phone", ""))
        if len(phone) == 10 and phone.startswith("3"):
            phone = "57" + phone
        if len(name) < 2 or len(name) > 100 or len(phone) not in (10, 12):
            flash("Revisa tu nombre y número de celular.", "error")
        else:
            avatar_file = request.files.get("avatar")
            avatar = client.get("avatar", "")
            if avatar_file and avatar_file.filename:
                saved_avatar = save_client_avatar(avatar_file)
                if not saved_avatar:
                    flash("No se pudo procesar la foto. Sube un JPG, PNG, WEBP o GIF válido.", "error")
                    return redirect(url_for("perfil"))
                avatar = saved_avatar
            conn = db.get_db()
            conn.execute("UPDATE clients SET name=?,phone=?,preferred_style=?,favorite_color=?,avatar=? WHERE id=?",
                (name, phone, request.form.get("preferred_style", "").strip()[:300], request.form.get("favorite_color", "").strip()[:80], avatar, client["id"]))
            conn.commit()
            conn.close()
            flash("Tu perfil quedó actualizado.", "ok")
            return redirect(url_for("perfil"))
    return render_template("cuenta.html", client=get_client(session["client_id"]),
                           bookings=client_bookings(get_client(session["client_id"])),
                           gallery_images=get_gallery(active_only=True)[:3])


@app.route("/logout")
def logout():
    session.pop("is_admin", None)
    session.pop("client_id", None)
    return redirect(url_for("index"))


# ------------------------------------------------------------------ admin
@app.route("/panel")
@login_required
def admin_dashboard():
    today = today_str()
    all_bookings = get_bookings()
    todays = [b for b in all_bookings if b["date"] == today and b["status"] != "CANCELADA"]
    pending = [b for b in all_bookings if b["status"] == "PENDIENTE"]
    confirmed = [b for b in all_bookings if b["status"] == "CONFIRMADA"]
    completed = [b for b in all_bookings if b["status"] == "COMPLETADA"]
    clients = {wa.digits(b["phone"]) for b in all_bookings}
    month_prefix = today[:7]
    revenue = 0
    services_by_id = {s["id"]: s for s in get_services()}
    for b in all_bookings:
        if b["status"] in ("CONFIRMADA", "COMPLETADA") and b["date"].startswith(month_prefix):
            svc = services_by_id.get(b["service_id"])
            revenue += svc["price"] if svc else 0
            for aid in b["add_ons"]:
                a = services_by_id.get(int(aid)) if str(aid).isdigit() else None
                revenue += a["price"] if a else 0
    recent = sorted(all_bookings, key=lambda b: b["created_at"], reverse=True)[:6]
    kpis = [
        ("Citas hoy", len(todays)), ("Pendientes", len(pending)),
        ("Confirmadas", len(confirmed)), ("Completadas", len(completed)),
        ("Clientes", len(clients)), ("Ingresos del mes", f"${revenue:,.0f}".replace(",", ".")),
    ]
    return render_template("admin/dashboard.html", kpis=kpis, recent=recent,
                            services_by_id=services_by_id, human_date=human_date)


@app.route("/admin/referencias/<path:filename>")
@login_required
def admin_reference_image(filename):
    return send_from_directory(UPLOAD_REFS, filename, as_attachment=False)


@app.route("/admin/citas")
@login_required
def admin_citas():
    estado = request.args.get("estado", "TODAS")
    bookings = get_bookings(status=estado)
    services_by_id = {s["id"]: s for s in get_services()}
    settings = get_settings()
    contact_links, confirm_links = {}, {}
    for b in bookings:
        svc_name = services_by_id.get(b["service_id"], {}).get("name", b["service_name"])
        contact_links[b["id"]] = wa.wa_link(
            b["phone"], f"Hola {b['client_name']}, te escribimos de {settings['business_name']} 💅")
        if b["status"] == "CONFIRMADA":
            msg = wa.client_confirm_message({
                "client_name": b["client_name"], "date_human": human_date(b["date"]), "time": b["time"],
            }, svc_name)
            confirm_links[b["id"]] = wa.wa_link(b["phone"], msg)
    return render_template("admin/citas.html", bookings=bookings, estado=estado,
                            services_by_id=services_by_id, settings=settings,
                            human_date=human_date, contact_links=contact_links, confirm_links=confirm_links,
                            estados=["TODAS", "PENDIENTE", "CONFIRMADA", "COMPLETADA", "CANCELADA", "NO_ASISTIO"])


@app.route("/admin/citas/<bid>/estado", methods=["POST"])
@login_required
def admin_cita_estado(bid):
    nuevo = request.form.get("estado")
    if nuevo not in ("PENDIENTE", "CONFIRMADA", "COMPLETADA", "CANCELADA", "NO_ASISTIO"):
        abort(400)
    conn = db.get_db()
    conn.execute("UPDATE bookings SET status = ? WHERE id = ?", (nuevo, bid))
    conn.commit()
    conn.close()
    flash(f"Cita actualizada a {nuevo}.", "ok")
    return redirect(request.referrer or url_for("admin_citas"))


@app.route("/admin/citas/<bid>/editar", methods=["POST"])
@login_required
def admin_cita_editar(bid):
    conn = db.get_db()
    conn.execute(
        "UPDATE bookings SET client_name=?, phone=?, date=?, time=?, observations=? WHERE id=?",
        (request.form.get("client_name", "").strip(), request.form.get("phone", "").strip(),
         request.form.get("date"), request.form.get("time"),
         request.form.get("observations", "").strip(), bid),
    )
    conn.commit()
    conn.close()
    flash("Cita actualizada.", "ok")
    return redirect(url_for("admin_citas"))


@app.route("/admin/calendario")
@login_required
def admin_calendario():
    try:
        anio = int(request.args.get("anio", datetime.now().year))
        mes = int(request.args.get("mes", datetime.now().month))
    except ValueError:
        anio, mes = datetime.now().year, datetime.now().month
    dia_sel = request.args.get("dia", today_str())

    first_weekday, days_in_month = _month_grid(anio, mes)
    all_bookings = get_bookings()
    counts = {}
    for b in all_bookings:
        if b["status"] != "CANCELADA":
            counts[b["date"]] = counts.get(b["date"], 0) + 1
    day_bookings = [b for b in all_bookings if b["date"] == dia_sel]
    services_by_id = {s["id"]: s for s in get_services()}

    prev_mes, prev_anio = (12, anio - 1) if mes == 1 else (mes - 1, anio)
    next_mes, next_anio = (1, anio + 1) if mes == 12 else (mes + 1, anio)

    return render_template(
        "admin/calendario.html", anio=anio, mes=mes, month_name=MONTHS[mes - 1],
        first_weekday=first_weekday, days_in_month=days_in_month, counts=counts,
        dia_sel=dia_sel, day_bookings=day_bookings, services_by_id=services_by_id,
        human_date=human_date, prev_mes=prev_mes, prev_anio=prev_anio,
        next_mes=next_mes, next_anio=next_anio,
    )


def _month_grid(anio, mes):
    first = datetime(anio, mes, 1)
    first_weekday = (first.weekday() + 1) % 7  # convert to Sunday-first for template grid
    if mes == 12:
        days = (datetime(anio + 1, 1, 1) - first).days
    else:
        days = (datetime(anio, mes + 1, 1) - first).days
    return first_weekday, days


@app.route("/admin/clientes")
@login_required
def admin_clientes():
    all_bookings = get_bookings()
    by_phone = {}
    for b in all_bookings:
        k = wa.digits(b["phone"])
        by_phone.setdefault(k, {"name": b["client_name"], "phone": b["phone"], "bookings": []})
        by_phone[k]["bookings"].append(b)
    today = today_str()
    clients = []
    for k, c in by_phone.items():
        c["bookings"].sort(key=lambda b: b["date"])
        upcoming = [b for b in c["bookings"] if b["date"] >= today and b["status"] != "CANCELADA"]
        past = [b for b in c["bookings"] if b["date"] < today]
        clients.append({
            "key": k, "name": c["name"], "phone": c["phone"], "count": len(c["bookings"]),
            "last": past[-1]["date"] if past else "—",
            "next": upcoming[0]["date"] if upcoming else "—",
        })
    return render_template("admin/clientes.html", clients=clients)


@app.route("/admin/clientes/<telefono>")
@login_required
def admin_cliente_detalle(telefono):
    all_bookings = get_bookings()
    client_bookings = [b for b in all_bookings if wa.digits(b["phone"]) == telefono]
    if not client_bookings:
        abort(404)
    services_by_id = {s["id"]: s for s in get_services()}
    return render_template("admin/cliente_detalle.html", bookings=client_bookings,
                            telefono=telefono, name=client_bookings[0]["client_name"],
                            phone=client_bookings[0]["phone"], services_by_id=services_by_id,
                            human_date=human_date)


@app.route("/admin/servicios")
@login_required
def admin_servicios():
    services = get_services()
    categories = []
    for s in services:
        if s["category"] not in categories:
            categories.append(s["category"])
    return render_template("admin/servicios.html", services=services, categories=categories)


@app.route("/admin/servicios/nuevo", methods=["POST"])
@login_required
def admin_servicio_nuevo():
    name = request.form.get("name", "").strip()
    category = request.form.get("category", "General").strip() or "General"
    price = request.form.get("price", type=int) or 0
    duration = request.form.get("duration", type=int) or 60
    description = request.form.get("description", "").strip()
    if not name:
        flash("Escribe un nombre para el servicio.", "error")
        return redirect(url_for("admin_servicios"))
    conn = db.get_db()
    conn.execute(
        "INSERT INTO services (category, name, price, price_note, duration, description, active) "
        "VALUES (?,?,?,?,?,?,1)",
        (category, name, price, "", duration, description),
    )
    conn.commit()
    conn.close()
    flash("Servicio agregado.", "ok")
    return redirect(url_for("admin_servicios"))


@app.route("/admin/servicios/<int:sid>/editar", methods=["POST"])
@login_required
def admin_servicio_editar(sid):
    conn = db.get_db()
    conn.execute(
        "UPDATE services SET name=?, price=?, duration=?, description=?, active=? WHERE id=?",
        (request.form.get("name", "").strip(), request.form.get("price", type=int) or 0,
         request.form.get("duration", type=int) or 0, request.form.get("description", "").strip(),
         1 if request.form.get("active") else 0, sid),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("admin_servicios"))


@app.route("/admin/servicios/<int:sid>/eliminar", methods=["POST"])
@login_required
def admin_servicio_eliminar(sid):
    conn = db.get_db()
    conn.execute("DELETE FROM services WHERE id = ?", (sid,))
    conn.commit()
    conn.close()
    flash("Servicio eliminado.", "ok")
    return redirect(url_for("admin_servicios"))


@app.route("/admin/servicios/<int:sid>/imagen", methods=["POST"])
@login_required
def admin_servicio_imagen(sid):
    file = request.files.get("image")
    if not file or not file.filename or not allowed_file(file.filename):
        flash("Sube una imagen válida (png, jpg, jpeg, webp o gif).", "error")
        return redirect(url_for("admin_servicios"))
    ext = file.filename.rsplit(".", 1)[1].lower()
    filename = f"servicio_{sid}_{uuid.uuid4().hex[:8]}.{ext}"
    file.save(os.path.join(UPLOAD_SERVICES, filename))
    conn = db.get_db()
    conn.execute("UPDATE services SET image = ? WHERE id = ?", (filename, sid))
    conn.commit()
    conn.close()
    flash("Imagen actualizada.", "ok")
    return redirect(url_for("admin_servicios"))


@app.route("/admin/galeria")
@login_required
def admin_galeria():
    cat_filter = request.args.get("categoria", "Todos")
    all_images = get_gallery()
    categories = sorted({g["category"] for g in all_images if g["category"]})
    images = [g for g in all_images if cat_filter == "Todos" or g["category"] == cat_filter]
    services = get_services(active_only=True)
    return render_template("admin/galeria.html", images=images, categories=categories,
                            active_cat=cat_filter, services=services)


@app.route("/admin/galeria/subir", methods=["POST"])
@login_required
def admin_galeria_subir():
    files = request.files.getlist("images")
    category = request.form.get("category", "").strip()
    service_id = request.form.get("service_id", type=int)
    caption = request.form.get("caption", "").strip()

    if len(files) > GALLERY_MAX_FILES_PER_BATCH:
        flash(f"Puedes subir máximo {GALLERY_MAX_FILES_PER_BATCH} fotos por lote. "
              f"Súbelas en varias tandas.", "error")
        return redirect(url_for("admin_galeria"))

    conn = db.get_db()
    ok, failed = 0, 0
    for f in files:
        if not f or not f.filename:
            continue
        filename, thumb = save_gallery_image(f)
        if not filename:
            failed += 1
            continue
        conn.execute(
            "INSERT INTO gallery_images (filename, thumb_filename, category, service_id, caption, "
            "active, sort_order, created_at) VALUES (?,?,?,?,?,1,?,?)",
            (filename, thumb, category, service_id, caption, int(datetime.now().timestamp()), db.now_iso()),
        )
        ok += 1
    conn.commit()
    conn.close()

    if ok:
        flash(f"{ok} foto(s) subida(s) correctamente." + (f" {failed} no se pudieron procesar." if failed else ""), "ok")
    else:
        flash("No se pudo subir ninguna foto. Verifica que sean imágenes (jpg, png, webp).", "error")
    return redirect(url_for("admin_galeria"))


@app.route("/admin/galeria/<int:gid>/editar", methods=["POST"])
@login_required
def admin_galeria_editar(gid):
    conn = db.get_db()
    conn.execute(
        "UPDATE gallery_images SET category=?, caption=?, active=? WHERE id=?",
        (request.form.get("category", "").strip(), request.form.get("caption", "").strip(),
         1 if request.form.get("active") else 0, gid),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("admin_galeria"))


@app.route("/admin/galeria/<int:gid>/eliminar", methods=["POST"])
@login_required
def admin_galeria_eliminar(gid):
    conn = db.get_db()
    row = conn.execute("SELECT * FROM gallery_images WHERE id = ?", (gid,)).fetchone()
    if row:
        for path, fname in ((UPLOAD_GALLERY_ORIG, row["filename"]), (UPLOAD_GALLERY_THUMB, row["thumb_filename"])):
            fp = os.path.join(path, fname)
            if os.path.exists(fp):
                os.remove(fp)
        conn.execute("DELETE FROM gallery_images WHERE id = ?", (gid,))
        conn.commit()
    conn.close()
    flash("Foto eliminada.", "ok")
    return redirect(url_for("admin_galeria"))


@app.route("/admin/horarios")
@login_required
def admin_horarios():
    settings = get_settings()
    return render_template("admin/horarios.html", settings=settings, dow_labels=DOW)


@app.route("/admin/horarios/guardar", methods=["POST"])
@login_required
def admin_horarios_guardar():
    work_days = [int(d) for d in request.form.getlist("work_days")]
    update_settings(
        work_days=work_days,
        work_start=request.form.get("work_start", "09:00"),
        work_end=request.form.get("work_end", "18:00"),
        slot_duration=int(request.form.get("slot_duration", 60) or 60),
    )
    flash("Horario guardado.", "ok")
    return redirect(url_for("admin_horarios"))


@app.route("/admin/horarios/dia-bloqueado/agregar", methods=["POST"])
@login_required
def admin_dia_bloqueado_agregar():
    settings = get_settings()
    fecha = request.form.get("fecha")
    if fecha and fecha not in settings["blocked_dates"]:
        settings["blocked_dates"].append(fecha)
        update_settings(blocked_dates=settings["blocked_dates"])
    return redirect(url_for("admin_horarios"))


@app.route("/admin/horarios/dia-bloqueado/<fecha>/eliminar", methods=["POST"])
@login_required
def admin_dia_bloqueado_eliminar(fecha):
    settings = get_settings()
    settings["blocked_dates"] = [d for d in settings["blocked_dates"] if d != fecha]
    update_settings(blocked_dates=settings["blocked_dates"])
    return redirect(url_for("admin_horarios"))


@app.route("/admin/horarios/vacaciones/agregar", methods=["POST"])
@login_required
def admin_vacaciones_agregar():
    settings = get_settings()
    inicio, fin = request.form.get("inicio"), request.form.get("fin")
    if inicio and fin:
        settings["vacations"].append({"start": inicio, "end": fin})
        update_settings(vacations=settings["vacations"])
    return redirect(url_for("admin_horarios"))


@app.route("/admin/horarios/vacaciones/<int:idx>/eliminar", methods=["POST"])
@login_required
def admin_vacaciones_eliminar(idx):
    settings = get_settings()
    if 0 <= idx < len(settings["vacations"]):
        settings["vacations"].pop(idx)
        update_settings(vacations=settings["vacations"])
    return redirect(url_for("admin_horarios"))


@app.route("/admin/horarios/slot-bloqueado/agregar", methods=["POST"])
@login_required
def admin_slot_bloqueado_agregar():
    settings = get_settings()
    fecha, hora = request.form.get("fecha"), request.form.get("hora")
    if fecha and hora:
        settings["blocked_slots"].append({"date": fecha, "time": hora})
        update_settings(blocked_slots=settings["blocked_slots"])
    return redirect(url_for("admin_horarios"))


@app.route("/admin/horarios/slot-bloqueado/<int:idx>/eliminar", methods=["POST"])
@login_required
def admin_slot_bloqueado_eliminar(idx):
    settings = get_settings()
    if 0 <= idx < len(settings["blocked_slots"]):
        settings["blocked_slots"].pop(idx)
        update_settings(blocked_slots=settings["blocked_slots"])
    return redirect(url_for("admin_horarios"))


@app.route("/admin/config")
@login_required
def admin_config():
    settings = get_settings()
    webhook_url = url_for("whatsapp_webhook_verify", _external=True)
    return render_template("admin/config.html", settings=settings, webhook_url=webhook_url)


@app.route("/admin/config/negocio", methods=["POST"])
@login_required
def admin_config_negocio():
    update_settings(
        business_name=request.form.get("business_name", "").strip() or "Aura",
        business_suffix=request.form.get("business_suffix", "").strip(),
        tagline=request.form.get("tagline", "").strip(),
        address=request.form.get("address", "").strip(),
        instagram=request.form.get("instagram", "").strip(),
        hero_title=request.form.get("hero_title", "").strip(),
        hero_subtitle=request.form.get("hero_subtitle", "").strip(),
    )
    flash("Información del negocio guardada.", "ok")
    return redirect(url_for("admin_config"))


@app.route("/admin/config/whatsapp", methods=["POST"])
@login_required
def admin_config_whatsapp():
    numero = wa.digits(request.form.get("owner_whatsapp", ""))
    if len(numero) < 10:
        flash("Ingresa un número válido con código de país (ej. 573001234567).", "error")
        return redirect(url_for("admin_config"))
    update_settings(
        owner_whatsapp=numero,
        wa_cloud_token=request.form.get("wa_cloud_token", "").strip(),
        wa_cloud_phone_id=request.form.get("wa_cloud_phone_id", "").strip(),
        wa_verify_token=request.form.get("wa_verify_token", "").strip() or "aura_verify_token",
    )
    flash("Configuración de WhatsApp guardada.", "ok")
    return redirect(url_for("admin_config"))


@app.route("/admin/config/password", methods=["POST"])
@login_required
def admin_config_password():
    p1 = request.form.get("new_password", "")
    p2 = request.form.get("new_password2", "")
    if not p1:
        flash("No se realizaron cambios.", "ok")
        return redirect(url_for("admin_config"))
    if len(p1) < 6:
        flash("Mínimo 6 caracteres.", "error")
    elif p1 != p2:
        flash("Las contraseñas no coinciden.", "error")
    else:
        update_settings(password_hash=generate_password_hash(p1))
        flash("Contraseña actualizada.", "ok")
    return redirect(url_for("admin_config"))


# ------------------------------------------------------------------ WhatsApp webhook (chatbot)
@app.route("/webhook/whatsapp", methods=["GET"])
def whatsapp_webhook_verify():
    """Verificación exigida por Meta al registrar el webhook."""
    settings = get_settings()
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == settings["wa_verify_token"]:
        return challenge or "", 200
    return "Token de verificación inválido", 403


@app.route("/webhook/whatsapp", methods=["POST"])
def whatsapp_webhook_receive():
    """
    Recibe mensajes entrantes reales de WhatsApp (Cloud API de Meta).
    Si el remitente es el número de la dueña, responde comandos simples:
      "agenda hoy", "pendientes", "clientes"
    Requiere WA_CLOUD_TOKEN y WA_CLOUD_PHONE_ID configurados para poder
    contestar (si no están configurados, solo se registra el mensaje).
    """
    settings = get_settings()
    payload = request.get_json(silent=True) or {}
    from_number, text = wa.parse_incoming_webhook(payload)
    if not from_number:
        return jsonify({"status": "ignored"}), 200

    is_owner = wa.digits(from_number) == settings["owner_whatsapp"]
    reply = None
    if is_owner and text:
        t = text.strip().lower()
        services_by_id = {s["id"]: s["name"] for s in get_services()}
        if "agenda" in t or "hoy" in t:
            reply = wa.agenda_reply_message(get_bookings(date=today_str()), services_by_id)
        elif "pendiente" in t:
            pend = get_bookings(status="PENDIENTE")
            reply = wa.agenda_reply_message(pend, services_by_id) if pend else "✅ No tienes solicitudes pendientes."
        elif "cliente" in t:
            total = len({wa.digits(b["phone"]) for b in get_bookings()})
            reply = f"👥 Tienes {total} clientas registradas en el sistema."
        else:
            reply = ("🤖 Comandos disponibles:\n"
                     "- \"agenda hoy\"\n- \"pendientes\"\n- \"clientes\"")

    if reply and settings["wa_cloud_token"] and settings["wa_cloud_phone_id"]:
        wa.send_via_cloud_api(settings["wa_cloud_token"], settings["wa_cloud_phone_id"], from_number, reply)

    return jsonify({"status": "ok"}), 200


# ------------------------------------------------------------------ entrypoint
if __name__ == "__main__":
    db.init_db()
    os.makedirs(UPLOAD_SERVICES, exist_ok=True)
    os.makedirs(UPLOAD_REFS, exist_ok=True)
    os.makedirs(UPLOAD_GALLERY_ORIG, exist_ok=True)
    os.makedirs(UPLOAD_GALLERY_THUMB, exist_ok=True)
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=debug)
