# import sqlite3
import json
import datetime as dt
from pathlib import Path
from typing import Tuple, List
from bs4 import BeautifulSoup
import openai
import os, re
from PyPDF2 import PdfReader
from dotenv import load_dotenv

# --- PostgreSQL ---
import psycopg2
import psycopg2.extras

#############################
### Variables de entorno  ###
#############################


# Config Postgres (ajusta a tu entorno)
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_DB   = os.getenv("PG_DB", "ofertes_colpis")
PG_USER = os.getenv("PG_USER", "pi")
PG_PASS = os.getenv("PG_PASS", "")
PG_PORT = int(os.getenv("PG_PORT", "5432"))

MODEL = "deepseek-chat"  # o deepseek-reasoner
TEMPERATURE = 0.0  # Determinista

ROLE_SYSTEM = (
    "Eres un asistente experto en Recursos Humanos y en la gestión de ofertas de empleo. "
    "Devuelve solo un JSON bien estructurado con las claves, nada mas: "
    "'score' (float, 0-1), 'apto' (1/0) y justificacion (string breve en español). Dentro de los criterios ten en cuenta también la distancia del puesto de trabajo, no quiero puestos que esten a mas de 1h lejos de Barcelona."
    "No incluyas texto fuera del JSON."
)

# Carga la clave de API de DeepSeek desde las variables de entorno
load_dotenv()
openai.api_key = os.getenv("DEEPSEEK_API_KEY")
openai.base_url = "https://api.deepseek.com"

if not openai.api_key:
    raise ValueError("DEEPSEEK_API_KEY no está configurada en las variables de entorno.")

############################
##       UTILIDADES         ##
############################

def strip_html(raw_html) -> str:
    return BeautifulSoup(raw_html or "", "html.parser").get_text(" ", strip=True)

def pdf_to_text(pdf_path: Path) -> str:
    """Extrae el texto de un PDF."""
    try:
        reader = PdfReader(str(pdf_path))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages)
    except Exception as e:
        print(f"  [!] Error al leer PDF {pdf_path}: {e}")
        return ""

def read_cv(path: Path) -> str:
    """Lee el CV (PDF o TXT) y lo convierte a texto."""
    if not path.exists():
        print(f"  [!] El archivo de CV no existe: {path}")
        return ""
        
    if path.suffix.lower() == ".pdf":
        return pdf_to_text(path)
    return path.read_text(encoding="utf-8")

def clean_json(json_str: str) -> dict:
    """Limpia un string JSON para evitar errores de formato."""
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

def deepseek_score(cv_text: str, offer_text: str) -> Tuple[float, int, str]:
    """
    Evalúa el CV contra la oferta usando DeepSeek.
    """
    messages = [
        {"role": "system", "content": ROLE_SYSTEM},
        {"role": "user", "content":
            f"CV:\n\"\"\"\n{cv_text}\n\"\"\"\n"
            f"OFERTA:\n\"\"\"\n{offer_text}\n\"\"\""}
    ]
    response = openai.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=TEMPERATURE,
        max_tokens=256
    )
    data = clean_json(response.choices[0].message.content)
    score = float(data["score"])
    apto = int(data["apto"])
    justificacion = data.get("justificacion", "No se proporcionó justificación")
    return score, apto, justificacion

#############################
## LOGICA DE LA APLICACION ##
#############################

def get_conn():
    return psycopg2.connect(
        host=PG_HOST,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASS,
        port=PG_PORT,
    )

def get_usuarios_con_cv(cur) -> List[Tuple[int, str]]:
    """
    (NUEVA FUNCIÓN)
    Obtiene todos los usuarios y la ruta a su CV desde la BD.
    Asume que la columna se llama 'cv_path'.
    """
    # !!! IMPORTANTE: Cambia 'cv_path' si tu columna se llama diferente !!!
    sql = """
    SELECT id, cv_path 
    FROM usuarios 
    WHERE cv_path IS NOT NULL AND cv_path != ''
    """
    cur.execute(sql)
    return cur.fetchall()

def encontrar_casos(cur, usuario_id: int):
    """
    (MODIFICADA)
    Busca ofertas que están en 'ofertas_archivo' PERO que 
    aún NO tienen una entrada en 'ofertas_scores' PARA ESE USUARIO.
    """
    sql = """
    SELECT 
        a.id, -- Este es el oferta_id
        t.titulo,
        d.actividad, d.sector, d.puesto, d.jornada,
        d.remuneracion, d.ubicacion_trabajo,
        d.perfil_html, d.tareas_html, d.descripcion_html
        
    FROM ofertas_archivo AS a
    JOIN ofertas_detalle AS d USING(id)
    JOIN ofertas_listado AS t USING(id)
    
    -- Hacemos un LEFT JOIN contra 'ofertas_scores' PARA ESTE USUARIO
    LEFT JOIN ofertas_scores AS s 
        ON a.id = s.oferta_id AND s.usuario_id = %s
    
    -- Nos quedamos solo con las filas donde el JOIN falló 
    -- (es decir, no hay puntuación para este usuario)
    WHERE s.id_score IS NULL
    
    ORDER BY a.id
    """
    cur.execute(sql, (usuario_id,))
    return cur.fetchall()

def insertar_score_db(cur, oferta_id: str, usuario_id: int, score: float, apto: int, justificacion: str, now_timestamp: dt.datetime):
    """
    (MODIFICADA)
    Inserta o actualiza la puntuación en la nueva tabla 'ofertas_scores'.
    Usa la clave compuesta (oferta_id, usuario_id) para el ON CONFLICT.
    """
    sql = """
        INSERT INTO ofertas_scores (
            oferta_id, usuario_id, score, apta, justificacion, fecha_evaluacion
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (oferta_id, usuario_id) DO UPDATE SET
            score = EXCLUDED.score,
            apta = EXCLUDED.apta,
            justificacion = EXCLUDED.justificacion,
            fecha_evaluacion = EXCLUDED.fecha_evaluacion
    """
    cur.execute(sql, (oferta_id, usuario_id, score, apto, justificacion, now_timestamp))

def main():
    """
    (MODIFICADA)
    Bucle principal ahora itera por usuario y luego por ofertas pendientes
    para ese usuario.
    """
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                
                # 1. Obtener todos los usuarios de la BD
                usuarios = get_usuarios_con_cv(cur)
                if not usuarios:
                    print("No se encontraron usuarios con CVs en la base de datos.")
                    return
                
                print(f"Encontrados {len(usuarios)} usuarios para procesar.")

                # 2. Bucle principal por cada USUARIO
                for usuario_id, cv_path_str in usuarios:
                    print(f"\n--- Procesando Usuario ID: {usuario_id} ---")
                    
                    # 3. Leer el CV de este usuario
                    cv_path = Path(cv_path_str)
                    cv_text = read_cv(cv_path)
                    if not cv_text:
                        print(f"  [!] No se pudo leer el CV '{cv_path_str}', saltando usuario.")
                        continue
                    
                    print(f"  CV cargado desde: {cv_path_str}")

                    # 4. Encontrar ofertas pendientes SÓLO PARA ESTE USUARIO
                    offers = list(encontrar_casos(cur, usuario_id))
                    if not offers:
                        print("  No hay ofertas pendientes de evaluación para este usuario.")
                        continue
                    
                    print(f"  {len(offers)} ofertas pendientes encontradas para este usuario.")

                    # 5. Bucle interno por cada OFERTA (para este usuario)
                    for offer in offers:
                        (id_, titulo, actividad, sector, puesto, jornada,
                         remuneracion, ubicacion, perfil, tareas, descripcion) = offer

                        offer_text = "\n".join(filter(None, [
                            f"Título: {titulo}",
                            f"Actividad: {actividad}",
                            f"Sector: {sector}",
                            f"Puesto: {puesto}",
                            f"Jornada: {jornada}",
                            f"Remuneración: {remuneracion}",
                            f"Ubicación: {ubicacion}",
                            strip_html(perfil),
                            strip_html(tareas),
                            strip_html(descripcion)
                        ]))

                        try:
                            # 6. Evaluar el CV del usuario contra la oferta
                            score, apro, justificacion = deepseek_score(cv_text, offer_text)
                        except Exception as e:
                            print(f"  [!] Error procesando oferta {id_} para usuario {usuario_id}: {e}")
                            continue

                        # 7. Insertar el score en la tabla 'ofertas_scores'
                        now_ts = dt.datetime.now() # Usamos un timestamp de psycopg2
                        insertar_score_db(cur, id_, usuario_id, score, apro, justificacion, now_ts)

                        print(f"  > Oferta {id_} procesada (Usr {usuario_id}): Score={score}, Apto={apro}, Just='{justificacion}'")

    finally:
        conn.close()
        print("\nProceso de evaluación finalizado.")

if __name__ == "__main__":
    main()