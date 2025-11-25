import os
import re
import json
import psycopg2
import shutil
from datetime import datetime
from pathlib import Path
from PyPDF2 import PdfReader
from fpdf import FPDF
from openai import OpenAI
from dotenv import load_dotenv
from psycopg2.extras import DictCursor # Para obtener resultados como diccionarios

# ------------------ CONFIG ------------------
load_dotenv()
API_KEY = os.getenv("DEEPSEEK_API_KEY")
DB_PASS = os.getenv("PG_PASS")
# CV_PATH ya no es una constante global, se obtiene por usuario.
PROJECT_ROOT = Path("/home/pi/oferta-applier").resolve() # raíz del proyecto

font_path_dejavu = (PROJECT_ROOT / "fonts" / "DejaVuSans.ttf").resolve()
font_path_dejavu_bold = (PROJECT_ROOT / "fonts" / "DejaVuSans-Bold.ttf").resolve()

client = OpenAI(api_key=API_KEY, base_url="https://api.deepseek.com/v1")

# ------------------ LIMPIEZA JSON ------------------
def limpiar_json(texto: str) -> dict:
    # ... (sin cambios)
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", texto, re.S)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                pass
    return {}

# ------------------ LECTURA CV ------------------
def read_cv(cv_path: Path) -> str:
    # ... (sin cambios)
    if cv_path.suffix.lower() == ".pdf":
        text = ""
        reader = PdfReader(str(cv_path))
        for page in reader.pages:
            text += page.extract_text() or ""
        return text.strip()
    else:
        return cv_path.read_text(encoding="utf-8", errors="ignore")

# ------------------ PDF ------------------
class CartaPDF(FPDF):
    def __init__(self, user_name: str):
        super().__init__()
        self.user_name = user_name
        self.fonts_added = False
        self.add_font("DejaVu", "", str(font_path_dejavu), uni=True)
        self.add_font("DejaVu", "B", str(font_path_dejavu_bold), uni=True)

    def header(self):
        self.set_font("DejaVu", size=11, style="B")
        self.set_left_margin(25)
        self.set_right_margin(25)
        # USA EL NOMBRE DEL USUARIO
        self.cell(0, 10, self.user_name, ln=1)
        self.set_font("DejaVu", size=11)
        self.ln(2)
        self.set_draw_color(100, 100, 100)
        self.line(25, self.get_y(), 185, self.get_y())
        self.ln(8)

    def footer(self):
        # ... (sin cambios)
        self.set_y(-15)
        self.set_font("DejaVu", size=9)
        self.cell(0, 10, f"Página {self.page_no()}", align="C")

def generar_pdf_carta(carta_texto: str, carpeta: Path, user_name: str) -> Path:
    carpeta.mkdir(parents=True, exist_ok=True)
    # NOMBRE DE ARCHIVO PERSONALIZADO
    pdf_name = f"Carta_Presentacio_{user_name.replace(' ', '_')}.pdf"
    pdf_path = carpeta / pdf_name

    pdf = CartaPDF(user_name)
    pdf.add_page()
    pdf.set_font("DejaVu", size=11)
    pdf.multi_cell(0, 10, carta_texto)
    pdf.output(str(pdf_path))

    return pdf_path

# ------------------ API ------------------
def generar_carta(cv_text: str, oferta_texto: str, nombre_usuario: str) -> dict:
    prompt = f"""
Eres un asistente que genera cartas de presentación.
Tienes el siguiente CV:

{cv_text}

Y la siguiente oferta de trabajo:

{oferta_texto}
Recuerda que la carta debe ser personalizada para la oferta y debe estar escrita en el idioma de la oferta (catalán, español o inglés).
Devuelve un JSON con esta estructura:

{{
  "carta_texto": "...",
  "permite_envio_email": 1 o 0,
  "destinatario": "... o null",
  "asunto_email": "... o null",
  "cuerpo_email": "... o null"
}}
En permite_envio_email pon 1 si la oferta especifica que se puede enviar un email, 0 en caso contrario.
No incluyas nada fuera del JSON.
Despidete siempre con "Cordialment, \n{nombre_usuario}"
"Atenciosament" no existe en catalan, no lo incluyas NUNCA
"""
    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.6,
    )
    raw = resp.choices[0].message.content
    return limpiar_json(raw)

# ------------------ BBDD ------------------
def get_connection():
    return psycopg2.connect(
        host="localhost",
        dbname="ofertes_colpis",
        user="pi",
        password=DB_PASS
    )

def ensure_tables(con):
    with con.cursor() as cur:
        # MODIFICADO: Añadido usuario_id y UNIQUE constraint
        cur.execute("""
        CREATE TABLE IF NOT EXISTS cartas (
            id SERIAL PRIMARY KEY,
            oferta_id TEXT NOT NULL,
            usuario_id INTEGER NOT NULL,
            carta_texto TEXT NOT NULL,
            destinatario TEXT NULL,
            asunto_email TEXT NULL,
            cuerpo_email TEXT NULL,
            fecha_generacion TIMESTAMP NOT NULL,
            permite_envio_email INTEGER NOT NULL,
            enviado_email INTEGER DEFAULT 0,
            
            FOREIGN KEY (oferta_id) REFERENCES ofertas_listado(id) ON DELETE CASCADE,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE,
            UNIQUE (oferta_id, usuario_id) -- Un usuario solo puede tener una carta por oferta
        )
        """)
    con.commit()

def guardar_carta(con, oferta_id: str, usuario_id: int, data: dict):
    with con.cursor() as cur:
        # MODIFICADO: Añadido usuario_id
        cur.execute("""
            INSERT INTO cartas (
                oferta_id, usuario_id, carta_texto, destinatario, asunto_email, cuerpo_email,
                fecha_generacion, permite_envio_email
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (oferta_id, usuario_id) DO NOTHING -- No sobreescribir si ya existe
        """, (
            oferta_id,
            usuario_id,
            data.get("carta_texto", ""),
            data.get("destinatario"),
            data.get("asunto_email"),
            data.get("cuerpo_email"),
            datetime.now(),
            int(data.get("permite_envio_email", 0)),
        ))
    con.commit()

def get_user_data(cur, user_id: int) -> dict:
    """Obtiene los datos del usuario de la BBDD."""
    cur.execute("SELECT nombre, cv_path FROM usuarios WHERE id = %s", (user_id,))
    user = cur.fetchone()
    if not user:
        print(f"[Usuario {user_id}] ✖ No encontrado en la BBDD.")
        return None
    if not user["cv_path"]:
        print(f"[Usuario {user_id}] ✖ No tiene 'cv_path' configurado en la BBDD.")
        return None
    return user

# ------------------ MAIN ------------------
def main():
    # --- ¡¡AQUÍ PUEDES ALARGAR LA LISTA!! ---
    USUARIOS_PARA_CARTAS = [1] # Ejemplo: [1, 2] si el usuario 2 también quiere cartas
    
    con = get_connection()
    ensure_tables(con)
    
    # Bucle principal por cada usuario admitido
    for user_id in USUARIOS_PARA_CARTAS:
        print(f"\n--- 🚀 Procesando cartas para el Usuario ID: {user_id} ---")
        
        # Usamos DictCursor para acceder a los datos por nombre de columna
        with con.cursor(cursor_factory=DictCursor) as cur:
            # 1. Obtener datos del usuario
            user_data = get_user_data(cur, user_id)
            if not user_data:
                continue # Saltar a la siguiente iteración si el usuario no es válido

            user_name = user_data["nombre"]
            cv_path = Path(user_data["cv_path"])
            
            if not cv_path.exists():
                print(f"[{user_id}] ✖ El CV no existe en la ruta: {cv_path}")
                continue
            
            try:
                cv_text = read_cv(cv_path)
                print(f"[{user_id}] ✔ CV cargado para '{user_name}' desde {cv_path}")
            except Exception as e:
                print(f"[{user_id}] ✖ Error leyendo CV: {e}")
                continue

            # 2. Buscar ofertas NUEVAS (Apta=1 para este user, sin carta para este user)
            cur.execute("""
                SELECT os.oferta_id, oa.html_raw, oa.pdf_texto
                FROM ofertas_scores AS os
                JOIN ofertas_archivo AS oa ON os.oferta_id = oa.id
                LEFT JOIN cartas AS c ON os.oferta_id = c.oferta_id AND c.usuario_id = %s
                WHERE os.apta = 1 
                  AND os.usuario_id = %s 
                  AND c.id IS NULL
            """, (user_id, user_id))
            ofertas = cur.fetchall()

            if not ofertas:
                print(f"[{user_id}] No hay ofertas nuevas para generar cartas.")
            
            # 3. Procesar ofertas NUEVAS
            for oferta in ofertas:
                oferta_id = oferta["oferta_id"]
                oferta_texto_completa = oferta["pdf_texto"] or oferta["html_raw"]
                print(f"[{user_id}][{oferta_id}] Generando carta...")
                
                try:
                    data = generar_carta(cv_text, oferta_texto_completa, user_name)
                    if not data or "carta_texto" not in data:
                        print(f"[{user_id}][{oferta_id}] ✖ No se pudo generar JSON válido")
                        continue

                    guardar_carta(con, oferta_id, user_id, data)

                    carpeta = PROJECT_ROOT / "cartas" / str(oferta_id)
                    pdf_path = generar_pdf_carta(data["carta_texto"], carpeta, user_name)

                    # Copiar CV a la carpeta con nombre de usuario
                    cv_dest_name = f"{user_name.replace(' ', '_')}_CV.pdf"
                    shutil.copy2(cv_path, carpeta / cv_dest_name)

                    print(f"[{user_id}][{oferta_id}] ✔ Carta y CV creados en {pdf_path.parent}")
                
                except Exception as e:
                    print(f"[{user_id}][{oferta_id}] ✖ Error procesando oferta: {e}")

            # 4. Comprobar discrepancias (Cartas en DB pero sin archivos PDF)
            print(f"[{user_id}] Buscando discrepancias (PDFs faltantes)...")
            
            cur.execute("""
                SELECT c.oferta_id, c.carta_texto
                FROM cartas AS c
                JOIN ofertas_scores AS os ON c.oferta_id = os.oferta_id AND c.usuario_id = os.usuario_id
                WHERE os.apta = 1 AND os.usuario_id = %s
            """, (user_id,))
            cartas_db = cur.fetchall()
            
            ids_apta_con_carta_db = {row['oferta_id'] for row in cartas_db}
            
            archivos_faltantes = []
            for oferta_id in ids_apta_con_carta_db:
                carpeta = PROJECT_ROOT / "cartas" / str(oferta_id)
                pdf_name = f"Carta_Presentacio_{user_name.replace(' ', '_')}.pdf"
                cv_name = f"{user_name.replace(' ', '_')}_CV.pdf"
                
                if not (carpeta / pdf_name).exists() or not (carpeta / cv_name).exists():
                    archivos_faltantes.append(oferta_id)

            if archivos_faltantes:
                print(f"[{user_id}] Discrepancias encontradas en IDs: {archivos_faltantes}")
                print(f"[{user_id}] Regenerando archivos para estas ofertas...")

                for oferta_id in archivos_faltantes:
                    # Encontrar el texto de la carta que ya está en la DB
                    carta_texto = next((row['carta_texto'] for row in cartas_db if row['oferta_id'] == oferta_id), None)
                    if not carta_texto:
                        continue # Imposible, pero por si acaso

                    carpeta = PROJECT_ROOT / "cartas" / str(oferta_id)
                    
                    try:
                        pdf_path = generar_pdf_carta(carta_texto, carpeta, user_name)
                        cv_dest_name = f"{user_name.replace(' ', '_')}_CV.pdf"
                        shutil.copy2(cv_path, carpeta / cv_dest_name)
                        print(f"[{user_id}][{oferta_id}] ✔ Archivos (re)creados en {pdf_path.parent}")
                    except Exception as e:
                        print(f"[{user_id}][{oferta_id}] ✖ Error al regenerar archivos: {e}")
            else:
                print(f"[{user_id}] No se encontraron discrepancias.")

    con.close()
    print("\n--- ✅ Proceso de generación de cartas finalizado ---")


if __name__ == "__main__":
    main()