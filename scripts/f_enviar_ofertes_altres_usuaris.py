import os
import re
import json
import base64
import psycopg2
import psycopg2.extras
import openai
from dotenv import load_dotenv
from pathlib import Path
from email.mime.text import MIMEText
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from bs4 import BeautifulSoup
import datetime  # <--- NUEVA IMPORTACIÓN

# --- Configuración de Constantes ---

# 1. Configuración de PostgreSQL (cargada desde .env o default)
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_DB   = os.getenv("PG_DB", "ofertes_colpis")
PG_USER = os.getenv("PG_USER", "pi")
PG_PASS = os.getenv("PG_PASS", "")
PG_PORT = int(os.getenv("PG_PORT", "5432"))

# 2. Configuración de Gmail
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
TOKEN_FILE = Path("token.json")
CREDS_FILE = Path("credentials.json")

# !!! IMPORTANTE: Define tu dirección de correo aquí !!!
FROM_ADDR = "oriollarrea111@gmail.com" 

# 3. Configuración de DeepSeek (cargada desde .env)
load_dotenv()
openai.api_key = os.getenv("DEEPSEEK_API_KEY")
openai.base_url = "https://api.deepseek.com"
MODEL = "deepseek-chat"
TEMPERATURE = 0.5 # Un poco de creatividad para un correo amigable

# Prompt para la IA: redactar un correo para notificar a la amiga
ROLE_NOTIFICADOR = (
    "Eres un asistente amigable y entusiasta. Tu objetivo es notificar a una usuaria (mi amiga), la redaccion del texto debe ser en catalan."
    "sobre una oferta de trabajo que mi sistema ha determinado que encaja perfectamente con su perfil. "
    "Tu tono debe ser cercano pero profesional, como un 'headhunter' personal."
    "Devuelve SÓLO un JSON con dos claves: 'asunto' (string) y 'cuerpo' (string, formato de texto plano, usa saltos de línea \n)."
    "En el cuerpo, saluda por su nombre, menciona la oferta, explica brevemente por qué es apta (basado en la justificación), "
    "resume las condiciones clave (puesto, ubicación, remuneración) y proporciona el enlace directo."
    "Firma como 'Tu Asistente de Empleo' (en catalan)."
)

if not openai.api_key:
    raise ValueError("DEEPSEEK_API_KEY no está configurada.")
if FROM_ADDR == "tu-email@gmail.com":
    raise ValueError("Por favor, edita la variable FROM_ADDR en el script.")

# --- Utilidades ---

def strip_html(raw_html) -> str:
    """Limpia HTML para obtener texto plano."""
    return BeautifulSoup(raw_html or "", "html.parser").get_text(" \n", strip=True)

def clean_json(json_str: str) -> dict:
    # ... (igual que antes)
    text = json_str.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        raise ValueError("No se encontró un JSON válido en el texto proporcionado.")
    json_str = m.group(0)
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Error al decodificar JSON: {e}")

def parse_dmy_date(date_str: str) -> datetime.date | None:
    """
    (NUEVA FUNCIÓN HELPER)
    Convierte un string 'dd/mm/YYYY' a un objeto date.
    """
    if not date_str:
        return None
    try:
        return datetime.datetime.strptime(date_str, "%d/%m/%Y").date()
    except ValueError:
        return None

# --- Funciones de Conexión (BBDD y Gmail) ---

def get_conn():
    # ... (igual que antes)
    return psycopg2.connect(
        host=PG_HOST, dbname=PG_DB, user=PG_USER, password=PG_PASS, port=PG_PORT
    )

def get_gmail_service():
    # ... (igual que antes)
    creds = None
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE.as_posix(), SCOPES)
        except Exception as e:
            print(f"⚠️ Error leyendo {TOKEN_FILE}: {e}")
            if TOKEN_FILE.exists(): TOKEN_FILE.unlink()
            creds = None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json())
        except Exception as e:
            print(f"⚠️ Error al refrescar token: {e}")
            creds = None # Forzará la re-autenticación

    if not creds or not creds.valid:
        if not CREDS_FILE.exists():
            print(f"❌ No encuentro {CREDS_FILE}. Coloca el fichero de credenciales.")
            return None
        
        flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE.as_posix(), SCOPES)
        
        auth_url, _ = flow.authorization_url(
            access_type='offline',
            prompt='consent',
            include_granted_scopes='true'
        )
        print("\n🌐 Abre esta URL en tu navegador (o con túnel SSH a localhost:8080):\n")
        print(auth_url)
        print("\nIniciando servidor local para capturar la redirección... (puerto 8080)\n")
        
        try:
            creds = flow.run_local_server(host="localhost", port=8080, open_browser=False)
            print("✅ Credenciales obtenidas mediante run_local_server.")
        except Exception as e:
            print(f"⚠️ run_local_server falló: {e}")
            print("🔁 Probando fallback run_console(). Pega el código de la URL aquí.")
            try:
                creds = flow.run_console()
                print("✅ Credenciales obtenidas mediante run_console().")
            except Exception as e2:
                print(f"❌ Ambos métodos de autorización fallaron: {e2}")
                return None
        
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

# --- Funciones de Email y Lógica Principal ---

def build_text_message(from_addr: str, to_addr: str, subject: str, body: str) -> dict:
    # ... (igual que antes)
    msg = MIMEText(body or "", "plain", "utf-8")
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return {"raw": raw}

def get_ofertas_pendientes_notificar(cur):
    """
    (MODIFICADA)
    Busca ofertas aptas (apta=1) para usuarios (id!=1)
    que no hayan sido notificadas (notificado_email=0).
    AHORA INCLUYE FECHAS.
    """
    sql = """
    SELECT 
        s.id_score,         -- PK de la puntuación para actualizar
        s.justificacion,    -- La justificación de por qué es apta
        u.nombre AS user_nombre,
        u.email AS user_email,
        l.titulo,
        d.puesto,
        d.remuneracion,
        d.ubicacion_trabajo,
        d.link_oferta_entidad,
        a.html_raw,         -- Descripción (HTML)
        a.pdf_texto,        -- Descripción (PDF)
        
        -- CAMPOS DE FECHA AÑADIDOS --
        d.fecha_limite_cv,    -- Tipo DATE (YYYY-MM-DD)
        l.fecha_limite,     -- Tipo TEXT (dd/mm/YYYY)
        l.fecha_oferta      -- Tipo TEXT (dd/mm/YYYY)
    FROM 
        ofertas_scores AS s
    JOIN 
        usuarios AS u ON s.usuario_id = u.id
    JOIN 
        ofertas_listado AS l ON s.oferta_id = l.id
    JOIN 
        ofertas_detalle AS d ON s.oferta_id = d.id
    JOIN 
        ofertas_archivo AS a ON s.oferta_id = a.id
    WHERE 
        s.usuario_id != 1           -- Que no sea yo
        AND s.apta = 1              -- Que sea apta
        AND u.email IS NOT NULL     -- Que tenga un email
        AND u.email != ''
        AND (s.notificado_email IS NULL OR s.notificado_email = 0) -- No notificada
    """
    cur.execute(sql)
    return cur.fetchall()

def deepseek_redactar_email(nombre_amiga: str, oferta: dict) -> dict:
    # ... (igual que antes)
    descripcion = strip_html(oferta['html_raw']) if oferta['html_raw'] else oferta['pdf_texto']
    
    prompt_usuario = f"""
    Destinataria: {nombre_amiga}

    Información de la Oferta:
    - Título: {oferta['titulo']}
    - Puesto: {oferta['puesto']}
    - Ubicación: {oferta['ubicacion_trabajo']}
    - Remuneración: {oferta['remuneracion']}
    - Link: {oferta['link_oferta_entidad']}
    - Justificación de Aptitud: {oferta['justificacion']}
    - Descripción Completa:
    \"\"\"
    {descripcion[:1500]}
    \"\"\"
    """
    
    messages = [
        {"role": "system", "content": ROLE_NOTIFICADOR},
        {"role": "user", "content": prompt_usuario}
    ]
    
    response = openai.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=TEMPERATURE,
        max_tokens=1024
    )
    
    data = clean_json(response.choices[0].message.content)
    
    if "asunto" not in data or "cuerpo" not in data:
        raise ValueError("La respuesta de la IA no contiene 'asunto' o 'cuerpo'.")
        
    return data

def marcar_como_notificado(cur, id_score: int):
    """Actualiza la BBDD para marcar la notificación como enviada."""
    sql = "UPDATE ofertas_scores SET notificado_email = 1 WHERE id_score = %s"
    cur.execute(sql, (id_score,))

# --- Función Principal ---

def notificar_ofertas():
    """
    (MODIFICADA)
    Añade lógica de filtrado por fechas antes de enviar.
    """
    print("Iniciando script de notificación de ofertas...")
    
    gmail_service = get_gmail_service()
    if not gmail_service:
        print("❌ No se pudo iniciar el servicio de Gmail. Abortando.")
        return

    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        ofertas_pendientes = get_ofertas_pendientes_notificar(cur)
        
        if not ofertas_pendientes:
            print("✅ No hay ofertas nuevas que notificar. Todo al día.")
            return

        print(f"ℹ️ Encontradas {len(ofertas_pendientes)} notificaciones pendientes. Validando fechas...")
        
        today = datetime.date.today()
        limite_15_dias = today - datetime.timedelta(days=15)

        for oferta in ofertas_pendientes:
            print(f"\n--- Procesando oferta '{oferta['titulo']}' para {oferta['user_nombre']} ---")
            
            # --- NUEVA LÓGICA DE VALIDACIÓN DE FECHAS ---
            fecha_lim_cv = oferta['fecha_limite_cv']
            fecha_lim_listado = parse_dmy_date(oferta['fecha_limite'])
            fecha_oferta = parse_dmy_date(oferta['fecha_oferta'])

            # Damos prioridad a la fecha_limite_cv si existe
            fecha_limite_real = fecha_lim_cv or fecha_lim_listado

            debe_enviar = False
            es_antigua = False

            if fecha_limite_real:
                # Caso 1: Hay fecha límite. Comprobar si está vigente.
                if today <= fecha_limite_real:
                    debe_enviar = True
                else:
                    es_antigua = True
                    print(f"   > Descartada: La fecha límite ({fecha_limite_real}) ha pasado.")
            else:
                # Caso 2: No hay fecha límite. Comprobar antigüedad de 15 días.
                if fecha_oferta:
                    if fecha_oferta >= limite_15_dias:
                        debe_enviar = True
                    else:
                        es_antigua = True
                        print(f"   > Descartada: Sin fecha límite y oferta ({fecha_oferta}) es > 15 días.")
                else:
                    # Caso 3: No hay ninguna fecha. Se envía por precaución.
                    print("   > Advertencia: No hay fecha límite ni fecha de oferta. Se procesará.")
                    debe_enviar = True
            
            # --- FIN DE LA LÓGICA DE FECHAS ---

            try:
                if debe_enviar:
                    # 1. Redactar el email con la IA
                    print("   > Solicitando redacción a DeepSeek...")
                    email_data = deepseek_redactar_email(oferta['user_nombre'], oferta)
                    print(f"   > Asunto: {email_data['asunto']}")

                    # 2. Construir el mensaje
                    msg = build_text_message(
                        from_addr=FROM_ADDR,
                        to_addr=oferta['user_email'],
                        subject=email_data['asunto'],
                        body=email_data['cuerpo']
                    )

                    # 3. Enviar el mensaje DIRECTAMENTE
                    print(f"   > Enviando email a {oferta['user_email']}...")
                    gmail_service.users().messages().send(
                        userId="me",
                        body=msg
                    ).execute()
                    
                    print("   ✅ Email enviado con éxito.")

                    # 4. Marcar como enviado en la BBDD
                    marcar_como_notificado(cur, oferta['id_score'])
                    conn.commit() # Guardamos el cambio en la BBDD
                    print("   > Marcado como 'notificado' en la base de datos.")
                
                elif es_antigua:
                    # Lógica de descarte: marcar como notificado sin enviar
                    print("   > Marcando oferta como notificada (caducada/antigua).")
                    marcar_como_notificado(cur, oferta['id_score'])
                    conn.commit()

            except Exception as e:
                print(f"   ❌ ERROR al procesar la oferta ID {oferta['id_score']}: {e}")
                conn.rollback() # Deshacemos cualquier cambio si algo falló

    except (Exception, psycopg2.Error) as error:
        print(f"❌ Error general o de base de datos: {error}")
    finally:
        if conn:
            conn.close()
            print("\nConexión a la base de datos cerrada.")

if __name__ == "__main__":
    # (Asegúrate de tener definidas las constantes al inicio del script)
    # FROM_ADDR = "tu-email@gmail.com" 
    # ...
    
    notificar_ofertas()