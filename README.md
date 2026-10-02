# Aura — Estudio de Uñas (Flask)

Aplicación web para negocio de manicure/uñas: catálogo, sistema de reservas con
disponibilidad en tiempo real, notificación por WhatsApp y panel administrativo
completo. Base de datos SQLite real en el servidor (ya no depende del navegador).

## Requisitos
- Python 3.10 o superior

## Puesta en marcha

El proyecto ya incluye una carpeta `venv/` con el entorno virtual creado
(hereda Flask del sistema). Si prefieres crear uno nuevo desde cero:

```bash
python3 -m venv venv
source venv/bin/activate        # En Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Copia `.env.example` a `.env` y ajusta al menos `SECRET_KEY`:

```bash
cp .env.example .env
```

Ejecutar en desarrollo:

```bash
source venv/bin/activate
python app.py
```

Abre http://127.0.0.1:5000 — la primera vez que entres a "Panel admin" (o a
`/login`) te pedirá crear la contraseña de la administradora.

## Primeros pasos como dueña del negocio
1. Entra a **Panel admin → Configuración** y coloca tu número de WhatsApp real
   (con código de país, ej. `573001234567`).
2. Revisa el catálogo en **Panel admin → Servicios**: los precios ya vienen
   cargados desde tu lista de precios 2026; puedes editarlos, subirles foto o
   desactivarlos.
3. Define tus horarios en **Panel admin → Horarios** (días laborales, horario,
   duración de turnos, días bloqueados, vacaciones).
4. Comparte el link de tu sitio con tus clientas.

## Cómo funciona el aviso por WhatsApp
- **Modo básico (activo ya, sin costo):** al crear una reserva, la clienta ve un
  botón "Notificar por WhatsApp" que abre WhatsApp con el mensaje ya escrito
  dirigido a tu número. Ella (o tú, si prefieres probarlo) solo debe presionar
  enviar.
- **Modo automático / chatbot (opcional):** si consigues credenciales reales de
  WhatsApp Business Platform (Meta), pégalas en Configuración → WhatsApp y el
  sistema empezará a enviar solo, y a responder comandos como "agenda hoy",
  "pendientes" o "clientes" cuando le escribas desde tu número. Esto requiere:
  - Cuenta de Meta Business verificada.
  - Un número de WhatsApp Business conectado (revisión de Meta).
  - Desplegar esta app en un servidor con dominio propio y HTTPS (Meta no
    acepta webhooks apuntando a `localhost`).
  Mientras no tengas esas credenciales, el sistema sigue funcionando
  perfectamente con el modo básico.

## Despliegue en producción
Para producción no uses `python app.py` (servidor de desarrollo). Usa un
servidor WSGI real, por ejemplo con `gunicorn`:

```bash
pip install gunicorn
gunicorn -w 2 -b 0.0.0.0:8000 app:app
```

y ponlo detrás de Nginx con HTTPS (necesario también si activas el chatbot).

## Estructura del proyecto
```
nail_studio/
  app.py            → rutas y lógica de la aplicación
  database.py       → esquema y acceso a la base de datos SQLite
  whatsapp.py       → mensajes, enlaces wa.me y Cloud API / webhook
  requirements.txt
  .env.example
  instance/         → aquí se crea nail_studio.db (no se sube a git)
  static/
    css/style.css
    js/booking.js
    uploads/services/     → fotos de servicios
    uploads/referencias/  → imágenes de referencia subidas por clientas
  templates/
    ...páginas públicas...
    admin/...panel administrativo...
```

## Copia de seguridad
Todo vive en `instance/nail_studio.db`. Haz copia de ese archivo periódicamente
(por ejemplo, con una tarea programada que lo suba a un almacenamiento en la
nube) para no perder tus citas y clientas.
