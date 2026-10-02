"""
Integración de WhatsApp.

Tiene DOS capas, tal como se especificó:

1) PRIMERA VERSIÓN (funciona ya, sin credenciales de Meta):
   Enlaces wa.me con el mensaje pre-escrito. El botón "Notificar por WhatsApp"
   abre WhatsApp con el mensaje listo para que la dueña (o la clienta) lo envíe.

2) VERSIÓN PROFESIONAL / CHATBOT (arquitectura lista, requiere credenciales reales):
   Envío y recepción automática de mensajes usando la WhatsApp Business
   Cloud API de Meta. Para que esto funcione de verdad hace falta:
     - Una cuenta de Meta Business verificada.
     - Un número de WhatsApp Business conectado a esa cuenta (Meta lo revisa).
     - Un token de acceso permanente (WA_CLOUD_TOKEN) y el Phone Number ID
       (WA_CLOUD_PHONE_ID) que Meta entrega en developers.facebook.com.
     - Un webhook público (este servidor debe estar en un dominio con HTTPS,
       no en localhost) registrado en el panel de Meta.
   Sin esas credenciales reales, send_via_cloud_api() simplemente no hace
   nada (no se simula un envío que no ocurrió). Cuando la dueña obtenga sus
   credenciales, solo debe pegarlas en Configuración → WhatsApp y el envío
   automático (y el chatbot) empieza a funcionar sin tocar código.
"""
import re
import json
import urllib.request
import urllib.error


def digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


import urllib.parse  # noqa: E402


def wa_link(phone: str, text: str) -> str:
    return "https://wa.me/" + digits(phone) + "?text=" + urllib.parse.quote(text)


def owner_message(booking: dict, service_name: str) -> str:
    addons = booking.get("add_ons_names") or []
    return (
        "🔔 NUEVA SOLICITUD DE CITA\n\n"
        f"👤 Cliente:\n{booking['client_name']}\n\n"
        f"📱 Teléfono:\n{booking['phone']}\n\n"
        f"💅 Servicio:\n{service_name}\n\n"
        f"📅 Fecha:\n{booking['date_human']}\n\n"
        f"🕐 Hora:\n{booking['time']}\n\n"
        f"✨ Adicionales:\n{', '.join(addons) if addons else 'Ninguno'}\n\n"
        f"📝 Observaciones:\n{booking.get('observations') or 'Ninguna'}\n\n"
        "📌 Estado:\nPENDIENTE\n\n"
        f"🔗 ID de reserva:\n{booking['id']}"
    )


def client_request_message(booking: dict, service_name: str, business_name: str) -> str:
    addons = booking.get("add_ons_names") or []
    return (
        f"Hola {booking['client_name']} 💅 Recibimos tu solicitud de cita en {business_name}.\n\n"
        f"Servicio: {service_name}\n"
        f"Fecha: {booking['date_human']}\n"
        f"Hora: {booking['time']}\n"
        f"Adicionales: {', '.join(addons) if addons else 'Ninguno'}\n\n"
        "La cita está pendiente de confirmación. Te contactaremos pronto. ¡Gracias! ✨"
    )


def client_confirm_message(booking: dict, service_name: str) -> str:
    return (
        "✨ ¡Tu cita ha sido confirmada!\n\n"
        f"👤 Cliente:\n{booking['client_name']}\n\n"
        f"💅 Servicio:\n{service_name}\n\n"
        f"📅 Fecha:\n{booking['date_human']}\n\n"
        f"🕐 Hora:\n{booking['time']}\n\n"
        "¡Te esperamos! 💅✨"
    )


def client_reminder_message(booking: dict, service_name: str) -> str:
    return (
        "🔔 Recordatorio de tu cita\n\n"
        f"Hola {booking['client_name']}, te recordamos tu cita de "
        f"{service_name} mañana {booking['date_human']} a las {booking['time']}. "
        "¡Te esperamos! 💅✨"
    )


def agenda_reply_message(bookings_today: list, service_lookup: dict) -> str:
    """Respuesta del 'chatbot' cuando la dueña escribe algo como 'agenda hoy'."""
    if not bookings_today:
        return "📆 No tienes citas registradas para hoy."
    lines = ["📆 Agenda de hoy:\n"]
    for b in bookings_today:
        svc = service_lookup.get(b["service_id"], b["service_name"])
        lines.append(f"🕐 {b['time']} — {b['client_name']} — {svc} — {b['status']}")
    return "\n".join(lines)


# ------------------ WhatsApp Business Cloud API (envío real) ------------------
GRAPH_API_VERSION = "v26.0"


def send_via_cloud_api(token: str, phone_number_id: str, to_phone: str, text: str) -> dict:
    """
    Envía un mensaje real usando la Cloud API de Meta.
    Requiere token y phone_number_id válidos (Meta Business).
    Si no hay credenciales configuradas, devuelve {"sent": False, "reason": "not_configured"}
    en vez de simular un envío exitoso.
    """
    if not token or not phone_number_id:
        return {"sent": False, "reason": "not_configured"}

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": digits(to_phone),
        "type": "text",
        "text": {"body": text},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return {"sent": True, "response": body}
    except urllib.error.HTTPError as e:
        return {"sent": False, "reason": "http_error", "detail": e.read().decode("utf-8", "ignore")}
    except Exception as e:
        return {"sent": False, "reason": "error", "detail": str(e)}


def send_image_via_cloud_api(token: str, phone_number_id: str, to_phone: str, image_path: str, caption: str = "") -> dict:
    """Sube una imagen a WhatsApp Cloud API y la entrega al destinatario."""
    import mimetypes
    import os
    if not token or not phone_number_id:
        return {"sent": False, "reason": "not_configured"}
    mime_type = mimetypes.guess_type(image_path)[0] or "application/octet-stream"
    if not mime_type.startswith("image/"):
        return {"sent": False, "reason": "unsupported_image"}
    boundary = "----AuraWhatsApp" + os.urandom(12).hex()
    file_name = os.path.basename(image_path)
    with open(image_path, "rb") as image_file:
        image_data = image_file.read()
    fields = [("messaging_product", "whatsapp")]
    parts = []
    for name, value in fields:
        parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n").encode())
    parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{file_name}\"\r\nContent-Type: {mime_type}\r\n\r\n").encode() + image_data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    upload_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/media"
    upload_req = urllib.request.Request(upload_url, data=b"".join(parts), headers={
        "Authorization": f"Bearer {token}", "Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
    try:
        with urllib.request.urlopen(upload_req, timeout=25) as response:
            media_id = json.loads(response.read().decode("utf-8"))["id"]
        payload = {"messaging_product": "whatsapp", "to": digits(to_phone), "type": "image",
                   "image": {"id": media_id, "caption": caption[:1024]}}
        send_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/messages"
        req = urllib.request.Request(send_url, data=json.dumps(payload).encode("utf-8"), headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=25) as response:
            return {"sent": True, "response": json.loads(response.read().decode("utf-8"))}
    except urllib.error.HTTPError as error:
        return {"sent": False, "reason": "http_error", "detail": error.read().decode("utf-8", "ignore")}
    except Exception as error:
        return {"sent": False, "reason": "error", "detail": str(error)}


def parse_incoming_webhook(payload: dict):
    """
    Extrae el número remitente y el texto de un evento entrante del webhook
    de WhatsApp Cloud API. Devuelve (from_number, text) o (None, None) si el
    payload no trae un mensaje de texto (ej. es solo una confirmación de
    estado 'delivered').
    """
    try:
        entry = payload["entry"][0]
        change = entry["changes"][0]["value"]
        messages = change.get("messages")
        if not messages:
            return None, None
        msg = messages[0]
        from_number = msg.get("from")
        text = msg.get("text", {}).get("body", "")
        return from_number, text
    except (KeyError, IndexError, TypeError):
        return None, None
