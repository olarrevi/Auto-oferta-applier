#!/usr/bin/env python3
"""
envia_cartas.py
----------------
► Recorre la carpeta cartas/<id>/
► Consulta la base (PostgreSQL):
      - solo procesa si permite_envio_email = 1 y enviado_email = 0
► Adjunta carta_<id>.pdf + Oriol_Larrea_CV.pdf
► CREA UN BORRADOR en Gmail (no envía)
► Marca enviado_email = 1 como “procesado” para no duplicar (ajústalo si prefieres otro flag)
"""

import base64
import mimetypes
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from pathlib import Path
import os
import sys
import time

from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

# PostgreSQL
import psycopg2
import psycopg2.extras

# CONSTANTES
CARTAS_DIR  = Path("cartas")
FROM_ADDR   = "oriollarrea111@gmail.com"

SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.compose',
    'https://www.googleapis.com/auth/gmail.modify'
]

# Hecho: el archivo que descargaste desde la consola de Google
CREDS_FILE  = Path("credentials.json")  # antes: 'client_secret.json' u otro nombre
TOKEN_FILE  = Path("token.json")

# Config Postgres
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_DB   = os.getenv("PG_DB", "ofertes_colpis")
PG_USER = os.getenv("PG_USER", "pi")
PG_PASS = os.getenv("PG_PASS", "ninots45")
PG_PORT = int(os.getenv("PG_PORT", "5432"))


def get_gmail_service():
    """Autenticación con refresco y guardado automático de token."""
    creds = None
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE.as_posix(), SCOPES)
        except Exception as e:
            print(f"⚠️ Error leyendo {TOKEN_FILE}: {e}")
            try:
                TOKEN_FILE.unlink()
            except Exception:
                pass
            creds = None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json())
        except Exception as e:
            print(f"⚠️ Error al refrescar token: {e}")
            creds = None

    if not creds:
        if not CREDS_FILE.exists():
            print(f"❌ No encuentro {CREDS_FILE}. Coloca el fichero de credenciales descargado desde Google Cloud.")
            return None

        flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE.as_posix(), SCOPES)

        # Intento principal: servidor local. Esto permite usar tunel SSH hacia localhost:8080
        try:
            # Nota: run_local_server no siempre expone parámetros de autorización personalizados,
            # así que pre-generamos la URL con offline+consent para asegurarnos refresh_token.
            auth_url, _ = flow.authorization_url(
                access_type='offline',
                prompt='consent',
                include_granted_scopes='true'
            )
            print("\n🌐 Abre esta URL en tu navegador (o con túnel SSH a localhost:8080):\n")
            print(auth_url)
            print("\nIniciando servidor local para capturar la redirección... (puerto 8080)\n")

            # Ejecutamos run_local_server que intentará capturar la redirección en localhost:8080.
            creds = flow.run_local_server(host="localhost", port=8080, open_browser=False)
            print("✅ Credenciales obtenidas mediante run_local_server.")

        except Exception as e:
            print(f"⚠️ run_local_server falló: {e}")
            print("🔁 Probando fallback run_console(). Copia la URL anterior en un navegador y pega el código aquí.")
            try:
                # Si run_local_server falla, run_console pedirá que pegues el código en la consola.
                creds = flow.run_console()
                print("✅ Credenciales obtenidas mediante run_console().")
            except Exception as e2:
                print(f"❌ Ambos métodos de autorización fallaron: {e2}")
                return None

        # Guardar token
        try:
            TOKEN_FILE.write_text(creds.to_json())
            print(f"✅ Token guardado en {TOKEN_FILE}")
        except Exception as e:
            print(f"⚠️ No he podido guardar {TOKEN_FILE}: {e}")

    try:
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return service
    except Exception as e:
        print(f"❌ Error creando servicio Gmail: {e}")
        return None


def build_message(from_addr: str, to_addr: str, subject: str,
                  body: str, attachments: list[Path]) -> dict:
    """Construye el diccionario {'raw': <base64url>} que requiere la API."""
    msg = MIMEMultipart()
    msg["From"], msg["To"], msg["Subject"] = from_addr, to_addr, subject
    msg.attach(MIMEText(body or "", "plain", "utf-8"))

    for path in attachments:
        if not path.exists():
            continue
        guessed = mimetypes.guess_type(path.as_posix())[0] or "application/octet-stream"
        maintype, subtype = guessed.split("/", 1)
        part = MIMEBase(maintype, subtype)
        part.set_payload(path.read_bytes())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{path.name}"')
        msg.attach(part)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return {"raw": raw}


def get_conn():
    return psycopg2.connect(
        host=PG_HOST, dbname=PG_DB, user=PG_USER, password=PG_PASS, port=PG_PORT
    )


def enviar_correos():
    if not CARTAS_DIR.exists():
        print("No existe la carpeta 'cartas/'. Fin.")
        return

    gmail = get_gmail_service()
    if not gmail:
        print("❌ No se obtuvo servicio Gmail. Comprueba credenciales y autorización.")
        return

    conn  = get_conn()

    try:
        with conn:
            with conn.cursor() as cur:
                for carpeta in CARTAS_DIR.iterdir():
                    if not carpeta.is_dir():
                        continue
                    oferta_id = carpeta.name

                    cur.execute("""
                        SELECT destinatario, asunto_email, cuerpo_email,
                               permite_envio_email, enviado_email
                        FROM cartas
                        WHERE oferta_id = %s
                    """, (oferta_id,))
                    row = cur.fetchone()

                    if not row:
                        print(f"[{oferta_id}] Sin registro en 'cartas'.")
                        continue

                    dest, asunto, cuerpo, permite, enviado = row
                    if not permite:
                        print(f"[{oferta_id}] Envío no permitido (permite_envio_email=0).")
                        continue
                    if enviado:
                        print(f"[{oferta_id}] Ya marcado como procesado; se omite.")
                        continue
                    if not dest:
                        print(f"[{oferta_id}] Sin destinatario.")
                        continue

                    carta_pdf = carpeta / "Carta Presentacio Oriol Larrea.pdf"
                    cv_pdf    = carpeta / "Oriol_Larrea_CV.pdf"
                    if not carta_pdf.exists() or not cv_pdf.exists():
                        print(f"[{oferta_id}] Faltan adjuntos; no se procesa.")
                        continue

                    try:
                        msg = build_message(
                            from_addr = FROM_ADDR,
                            to_addr   = dest,
                            subject   = asunto or "Candidatura a l'oferta",
                            body      = cuerpo or "Adjunto carta de presentació i CV.",
                            attachments=[carta_pdf, cv_pdf],
                        )

                        # Antes (envío directo):
                        # gmail.users().messages().send(userId="me", body=msg).execute()

                        # Creamos un borrador con message.raw
                        # Nota: la API espera body = {'message': {'raw': <base64url>}}
                        draft_body = {"message": msg}  # msg es {'raw': '...'}
                        draft = gmail.users().drafts().create(
                            userId="me",
                            body=draft_body
                        ).execute()

                        cur.execute(
                            "UPDATE cartas SET enviado_email = 1 WHERE oferta_id = %s",
                            (oferta_id,)
                        )
                        print(f"[{oferta_id}] Borrador creado (id={draft.get('id')}) para {dest}")

                    except Exception as e:
                        print(f"[{oferta_id}] ERROR al crear borrador: {e}")

    finally:
        conn.close()


if __name__ == "__main__":
    enviar_correos()
