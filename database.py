"""
Capa de base de datos — SQLite puro (sin dependencias extra).
Guarda: settings (config del negocio), services (catálogo), bookings (citas).
"""
import sqlite3
import os
import json
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "instance", "nail_studio.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    business_name TEXT NOT NULL DEFAULT 'Aura',
    business_suffix TEXT NOT NULL DEFAULT 'Estudio de Uñas',
    tagline TEXT NOT NULL DEFAULT 'Manicure, pedicure & nail art',
    hero_title TEXT NOT NULL DEFAULT 'Tu próximo diseño empieza aquí',
    hero_subtitle TEXT NOT NULL DEFAULT 'Descubre nuestros servicios y agenda tu próxima cita de manera fácil y rápida.',
    address TEXT NOT NULL DEFAULT '',
    instagram TEXT NOT NULL DEFAULT '',
    owner_whatsapp TEXT NOT NULL DEFAULT '',
    admin_user TEXT NOT NULL DEFAULT 'Administradora',
    admin_email TEXT NOT NULL DEFAULT 'admin@aura.local',
    password_hash TEXT,
    work_days TEXT NOT NULL DEFAULT '[1,2,3,4,5,6]',
    work_start TEXT NOT NULL DEFAULT '09:00',
    work_end TEXT NOT NULL DEFAULT '18:00',
    slot_duration INTEGER NOT NULL DEFAULT 60,
    blocked_dates TEXT NOT NULL DEFAULT '[]',
    vacations TEXT NOT NULL DEFAULT '[]',
    blocked_slots TEXT NOT NULL DEFAULT '[]',
    wa_cloud_token TEXT NOT NULL DEFAULT '',
    wa_cloud_phone_id TEXT NOT NULL DEFAULT '',
    wa_verify_token TEXT NOT NULL DEFAULT 'aura_verify_token'
);

CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    price INTEGER NOT NULL DEFAULT 0,
    price_note TEXT NOT NULL DEFAULT '',
    duration INTEGER NOT NULL DEFAULT 60,
    description TEXT NOT NULL DEFAULT '',
    image TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bookings (
    id TEXT PRIMARY KEY,
    service_id INTEGER,
    service_name TEXT NOT NULL,
    client_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    add_ons TEXT NOT NULL DEFAULT '[]',
    observations TEXT NOT NULL DEFAULT '',
    reference_image TEXT,
    client_id INTEGER,
    status TEXT NOT NULL DEFAULT 'PENDIENTE',
    created_at TEXT NOT NULL,
    FOREIGN KEY (service_id) REFERENCES services(id)
);

CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    phone TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    preferred_style TEXT NOT NULL DEFAULT '',
    favorite_color TEXT NOT NULL DEFAULT '',
    avatar TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gallery_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    thumb_filename TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    service_id INTEGER,
    caption TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (service_id) REFERENCES services(id)
);
"""

DEFAULT_SERVICES = [
    ("Tradicional", "Manicure tradicional", 22000, "", 45,
     "Limado, cutícula y esmaltado tradicional en el color de tu preferencia."),
    ("Tradicional", "Pedicure tradicional", 30000, "", 60,
     "Cuidado completo de pies con esmaltado tradicional."),
    ("Semipermanente", "Manicure semipermanente con diseños", 55000, "A partir de", 90,
     "Semipermanente con diseños personalizados. El valor final depende de la complejidad."),
    ("Semipermanente", "Manicure semipermanente un solo tono", 50000, "", 75,
     "Aplicación de esmaltado semipermanente en un solo color."),
    ("Semipermanente", "Manicure semipermanente para hombre", 50000, "", 60,
     "Cuidado y semipermanente pensado para manos masculinas."),
    ("Semipermanente", "Base Rubber", 75000, "", 90,
     "Base rubber para fortalecer y nivelar la uña natural."),
    ("Semipermanente", "Pedicure semipermanente", 50000, "", 75,
     "Pedicure con esmaltado semipermanente de larga duración."),
    ("Acrílico", "Acrílicas esculpidas — hasta #3", 115000, "Estructura + semipermanente", 150,
     "Uñas esculpidas en acrílico, longitud hasta el molde #3."),
    ("Acrílico", "Acrílicas esculpidas — hasta #5", 145000, "Estructura + semipermanente", 165,
     "Uñas esculpidas en acrílico, longitud hasta el molde #5."),
    ("Acrílico", "Recubrimiento en acrílico sobre uña natural", 95000, "", 120,
     "Recubrimiento de acrílico aplicado sobre la uña natural."),
    ("Acrílico", "Retoque de acrílico (20 a 30 días)", 85000, "", 90,
     "Mantenimiento entre 20 y 30 días. Después de 30 días el precio varía según diagnóstico."),
    ("Polygel", "Polygel esculpido — hasta #3", 115000, "Estructura + semipermanente", 150,
     "Uñas esculpidas en polygel, longitud hasta el molde #3."),
    ("Polygel", "Polygel esculpido — hasta #5", 145000, "Estructura + semipermanente", 165,
     "Uñas esculpidas en polygel, longitud hasta el molde #5."),
    ("Polygel", "Recubrimiento en polygel sobre uña natural", 95000, "", 120,
     "Recubrimiento de polygel aplicado sobre la uña natural."),
    ("Polygel", "Retoque de polygel (20 a 30 días)", 85000, "", 90,
     "Mantenimiento entre 20 y 30 días. Después de 30 días el precio varía según diagnóstico."),
    ("Soft Gel", "Montura soft gel", 85000, "", 120,
     "Montura de uñas en soft gel, ligera y resistente."),
    ("Soft Gel", "Retoque soft gel", 70000, "", 75,
     "Mantenimiento del sistema en soft gel."),
    ("Adicionales", "Caricaturas", 12000, "Desde", 15, "Diseño de caricaturas en una o varias uñas."),
    ("Adicionales", "Efectos", 3000, "Desde", 10, "Efectos decorativos (cromado, espejo, mármol, etc)."),
    ("Adicionales", "Gemas y perlas", 2000, "Desde", 5, "Aplicación de gemas o perlas decorativas."),
    ("Adicionales", "Dijes y 3D", 5000, "A partir de", 10, "Decoración con dijes o 3D. Sin garantía de durabilidad."),
    ("Adicionales", "Uña encapsulada en semipermanente", 5000, "Desde", 15, "Encapsulado decorativo sobre semipermanente."),
    ("Adicionales", "Uña encapsulada en acrílico/polygel", 12000, "", 15, "Encapsulado decorativo sobre acrílico o polygel."),
    ("Adicionales", "Uña adicional en polygel", 10000, "", 10, "Reparación o uña extra en polygel."),
    ("Adicionales", "Uña adicional en acrílico", 10000, "", 10, "Reparación o uña extra en acrílico."),
    ("Adicionales", "Retiro de semipermanente", 15000, "", 20, "Retiro profesional de esmaltado semipermanente."),
    ("Adicionales", "Retiro de acrílico / polygel / soft gel", 20000, "", 25, "Retiro profesional de sistema de uñas."),
]


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
    conn.executescript(SCHEMA)
    setting_columns = {row["name"] for row in conn.execute("PRAGMA table_info(settings)").fetchall()}
    if "admin_email" not in setting_columns:
        conn.execute("ALTER TABLE settings ADD COLUMN admin_email TEXT NOT NULL DEFAULT 'admin@aura.local'")
    client_columns = {row["name"] for row in conn.execute("PRAGMA table_info(clients)").fetchall()}
    if "avatar" not in client_columns:
        conn.execute("ALTER TABLE clients ADD COLUMN avatar TEXT NOT NULL DEFAULT ''")
    booking_columns = {row["name"] for row in conn.execute("PRAGMA table_info(bookings)").fetchall()}
    if "client_id" not in booking_columns:
        conn.execute("ALTER TABLE bookings ADD COLUMN client_id INTEGER REFERENCES clients(id)")
    row = conn.execute("SELECT id FROM settings WHERE id = 1").fetchone()
    if not row:
        conn.execute("INSERT INTO settings (id) VALUES (1)")
    count = conn.execute("SELECT COUNT(*) c FROM services").fetchone()["c"]
    if count == 0:
        for i, (cat, name, price, note, dur, desc) in enumerate(DEFAULT_SERVICES):
            conn.execute(
                "INSERT INTO services (category, name, price, price_note, duration, description, sort_order) "
                "VALUES (?,?,?,?,?,?,?)",
                (cat, name, price, note, dur, desc, i),
            )
    conn.commit()
    conn.close()


# ---------- helpers to read/write JSON list columns ----------
def jload(s, default):
    try:
        return json.loads(s) if s else default
    except Exception:
        return default


def jdump(obj):
    return json.dumps(obj, ensure_ascii=False)


def now_iso():
    return datetime.now().isoformat()
