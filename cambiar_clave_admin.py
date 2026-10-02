from getpass import getpass

import database as db
from werkzeug.security import generate_password_hash

email = input("Correo administrador actual/nuevo: ").strip().lower()
password = getpass("Nueva contraseña (mínimo 8 caracteres): ")
confirm = getpass("Repite la nueva contraseña: ")
if "@" not in email or len(password) < 8 or password != confirm:
    raise SystemExit("Correo inválido, contraseña corta o las contraseñas no coinciden.")
conn = db.get_db()
conn.execute("UPDATE settings SET admin_email = ?, password_hash = ? WHERE id = 1", (email, generate_password_hash(password)))
if conn.total_changes != 1:
    conn.close()
    raise SystemExit("No se encontró la configuración del estudio; no se guardaron cambios.")
conn.commit()
conn.close()
print("Credenciales administrativas actualizadas.")
