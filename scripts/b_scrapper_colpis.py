import re
import time
import requests, mimetypes, io, traceback
from bs4 import BeautifulSoup
from datetime import datetime, date, timedelta
import urllib.parse
# import sqlite3
import psycopg2
import psycopg2.extras
from psycopg2.extras import execute_values
from pdfminer.high_level import extract_text
from readability import Document
import os,re

BASE = "https://www.colpis.cat"
LOGIN_PAGE  = f"{BASE}/membres/login/"
LOGIN_ACTION = f"{BASE}/wp-admin/admin-post.php"
LIST_URL    = f"{BASE}/membres/ofertes-vigents"
AJAX_URL    = f"{BASE}/wp-admin/admin-ajax.php"


USER = "3547"
PASS = "48064242"

TODAY          = date.today
TODAY_ISO      = TODAY().isoformat()

DATA_LIMIT = TODAY() - timedelta(days=30)
DATA_LIMIT_ISO = DATA_LIMIT.isoformat()

# DB = "ofertas_colpis.db"

# Config de PostgreSQL
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_DB   = os.getenv("PG_DB", "ofertes_colpis")
PG_USER = os.getenv("PG_USER", "pi")
PG_PASS = os.getenv("PG_PASS", "")
PG_PORT = int(os.getenv("PG_PORT", "5432"))

######################################
##  SCRAPING DE LISTADO DE OFERTAS  ##
######################################

def parse_dmy_date(s):
    """Convierte un string dd/mm/YYYY a un objeto date."""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%d/%m/%Y").date()
    except Exception:
        return None
# --- FIN DE LA FUNCIÓN ---

def to_iso(date_str):
    try:
        return datetime.strptime(date_str, "%d/%m/%Y").date().isoformat()
    except Exception:
        return ""


def parse_offers(html):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for box in soup.select("div.offer"):
        a = box.select_one("a.title-wrapper")
        if not a:
            continue

        link   = a["href"].strip()
        title  = a.select_one("h1.title").get_text(strip=True)

        # Fecha de la oferta
        data_div = next(
            (d for d in box.select("div.data") if "Data de l'oferta" in d.text),
            None
        )
        data = ""
        if data_div:
            data = data_div.get_text(" ", strip=True).split(":", 1)[1].strip()

        m   = re.search(r"/oferta/(\d+)$", link)
        oid = m.group(1) if m else None
        iso_data = to_iso(data)

        # Fecha límite
        limite_div = next(
            (d for d in box.select("div.data") if "Data límit de CV" in d.text),
            None
        )

        limite = ""
        if limite_div:
            limite = limite_div.get_text(" ", strip=True).split(":", 1)[1].strip()

        m   = re.search(r"/oferta/(\d+)$", link)
        oid = m.group(1) if m else None
        iso_lim = to_iso(limite)

        if iso_lim and iso_lim <= DATA_LIMIT_ISO:
            continue

        out.append({
            "id": oid,
            "titulo": title,
            "link": link,
            "fecha_oferta": data,
            "fecha_limite": limite,
            "fecha_oferta_iso": iso_data,
            "fecha_limite_iso": iso_lim
        })
    return out


def login_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/126.0",
        "Origin": BASE,
        "Referer": LOGIN_PAGE,
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    })

    r = s.get(LOGIN_PAGE, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    nonce_input = soup.find("input", attrs={"name": re.compile("(security|nonce)", re.I)})
    nonce = nonce_input["value"] if nonce_input and nonce_input.has_attr("value") else ""

    payload = {
        "action": "login_colegiat",
        "redirect": "/membres/ofertes-vigents",
        "num_colegiat": USER,
        "password":     PASS,
    }
    if nonce:
        payload["security"] = nonce

    resp = s.post(LOGIN_ACTION, data=payload, allow_redirects=False, timeout=15)
    loc  = resp.headers.get("location", "")
    ok   = resp.status_code == 302 and loc.startswith("/membres/")
    if not ok:
        raise RuntimeError(f"Login no reconocido (status {resp.status_code}, location {loc})")

    s.get(BASE + loc, timeout=15)
    return s


def ajax_page(session, n):
    data = {"action": "get_offers_page", "page": str(n)}
    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Origin":  BASE,
        "Referer": LIST_URL + "/",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept": "*/*",
    }
    r = session.post(AJAX_URL, data=data, headers=headers, timeout=15)
    r.raise_for_status()
    return r.text

def scrape_list_ofertas(session, page=1):
    ofertas = []
    vistos  = set()
    while True:
        html = ajax_page(session, page)
        page_offers = parse_offers(html)
        if not page_offers:
            break

        for o in page_offers:
            if o["fecha_limite_iso"] <= TODAY_ISO:
                continue
            if o["id"] and o["id"] in vistos:
                continue
            ofertas.append(o)
            if o["id"]:
                vistos.add(o["id"])

        if page_offers[-1]["fecha_oferta_iso"] <= DATA_LIMIT_ISO:
            break

        page += 1
        time.sleep(0.6)
    return ofertas


def insert_offer_list_into_db(con, offers):
    """
    Inserción en lote con UPSERT.
    """
    # SQL para execute_values con ON CONFLICT
    sql = """
    INSERT INTO ofertas_listado
        (id, titulo, link_detalle, fecha_oferta, fecha_limite, scraped_at)
    VALUES %s
    ON CONFLICT (id) DO UPDATE SET
        titulo = EXCLUDED.titulo,
        link_detalle = EXCLUDED.link_detalle,
        fecha_oferta = EXCLUDED.fecha_oferta,
        fecha_limite = EXCLUDED.fecha_limite,
        scraped_at = EXCLUDED.scraped_at
    """
    now = datetime.now().isoformat(sep=' ', timespec='seconds')
    rows = [
        (
            str(o["id"]),
            o["titulo"],
            o["link"],
            o["fecha_oferta"],
            o["fecha_limite"],
            now
        )
        for o in offers
    ]
    with con, con.cursor() as cur:
        execute_values(cur, sql, rows)


def limpiar_ofertas(con, ofertas):
    """
    Devuelve solo las ofertas que no existen en la tabla.
    Antes: modificaba la lista en iteración.
    """
    sql = "SELECT 1 FROM ofertas_listado WHERE id = %s"
    restantes = []
    with con, con.cursor() as cur:
        for o in ofertas:
            oid = o.get("id")
            if not oid:
                continue
            cur.execute(sql, (str(oid),))
            if cur.fetchone():
                continue
            restantes.append(o)
    return restantes


########################################
## EXTRACCIÓN DE DETALLES DE OFERTAS  ##
########################################

def crear_lista_ofertas_links(con):
    """
    Links de ofertas que están en listado pero no en detalle.
    """
    q = """
    SELECT l.link_detalle
    FROM ofertas_listado AS l
    LEFT JOIN ofertas_detalle AS d ON d.id = l.id
    WHERE d.id IS NULL
    """
    with con.cursor() as cur:
        cur.execute(q)
        rows = cur.fetchall()
    return [r[0] for r in rows]


def link_oferta_entidad(soup, session):
    print("  > link_oferta_entidad(): buscando <form id='formOffer'>…")
    form = soup.find("form", id="formOffer")
    if not form or not form.has_attr("action"):
        print("    ⚠ No se encontró el formulario o el atributo action")
        return ""

    action_url = urllib.parse.urljoin(BASE, form["action"])
    print(f"    action_url        = {action_url}")

    payload = {
        inp["name"]: inp.get("value", "")
        for inp in form.select('input[name]')
    }
    print(f"    payload enviado   = {payload}")

    try:
        resp = session.post(
            action_url,
            data=payload,
            allow_redirects=True,
            timeout=15
        )
        print(f"    respuesta status  = {resp.status_code}")
        print(f"    historial redirs  = {[h.status_code for h in resp.history]}")
    except Exception as e:
        print(f"    ✖ Error al hacer POST: {e}")
        return ""

    final_url = resp.url
    print(f"    url final         = {final_url}")

    if urllib.parse.urlparse(final_url).hostname.endswith("colpis.cat"):
        print("    → No se redirigió fuera, quizá requiere CV. Devolviendo cadena vacía.")
        return ""

    print("    → Enlace externo resuelto con éxito")
    return final_url


def extraer_detalle(session, url):
    print(f"\nScrapeando ficha: {url}")
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print(f"  ✖ Error HTTP al descargar la ficha: {e}")
        return None

    soup = BeautifulSoup(r.text, "html.parser")

    def pair(lbl):
        tag = soup.find("b", string=re.compile(lbl, re.I))
        val = tag.next_sibling.strip(" :\u00A0") if tag and tag.next_sibling else ""
        print(f"  · {lbl:<22}= {val}")
        return val

    def html_block(h4txt):
        h4 = soup.find("h4", string=re.compile(h4txt, re.I))
        has_block = bool(h4)
        print(f"  · bloque {h4txt:<12}= {'OK' if has_block else 'NO ENCONTRADO'}")
        return str(h4.find_next("div")) if has_block else ""

    m = re.search(r"/([^/?#]+)$", url)
    if not m:
        print("  ⚠ No se pudo extraer el ID de la URL")
        return None
    oid = m.group(1)
    print(f"  · id                 = {oid}")

    h3_entidad = soup.select_one("h3")
    if not h3_entidad:
        print("  ⚠ No se encontró la entidad (primer <h3>)")
        return None
    entidad = h3_entidad.get_text(strip=True)
    print(f"  · entidad            = {entidad}")

    h3_puesto = soup.select_one("h3 + hr + h3")
    puesto = h3_puesto.get_text(" ", strip=True) if h3_puesto else ""
    print(f"  · puesto             = {puesto}")

    link_externo = link_oferta_entidad(soup, session)

    datos = {
        "id":                  oid,
        "entidad":             entidad,
        "actividad":           pair("ACTIVITAT"),
        "sector":              pair("SECTOR"),
        "puesto":              puesto,
        "jornada":             pair("Tipus de jornada"),
        "remuneracion":        pair("REMUNERACIÓ"),
        "ubicacion_trabajo":   pair("Ubicació lloc de treball"),
        "perfil_html":         html_block("PERFIL"),
        "tareas_html":         html_block("Tasques"),
        "observaciones_html":  str(soup.find("b", string="Observacions:").parent)
                               if soup.find("b", string="Observacions:") else "",
        "fecha_limite_cv":     parse_dmy_date(pair("Data límit")),
        "link_oferta_entidad": link_externo,
        "scraped_at":          datetime.utcnow().isoformat(timespec="seconds"),
    }

    print("  ✓ Ficha extraída correctamente")
    return datos


def get_pair(soup, label):
    tag = soup.find("b", string=re.compile(label, re.I))
    if not tag:
        return ""
    return tag.next_sibling.strip(" : ")


def guardar_detalle(con, d):
    """
    Guardar detalles con UPSERT. Placeholders de psycopg2: %(campo)s
    """
    sql = """
        INSERT INTO ofertas_detalle (
            id, entidad, actividad, sector, puesto,
            jornada, remuneracion, ubicacion_trabajo,
            perfil_html, tareas_html, observaciones_html,
            link_oferta_entidad,
            fecha_limite_cv, scraped_at
        ) VALUES (
            %(id)s, %(entidad)s, %(actividad)s, %(sector)s, %(puesto)s,
            %(jornada)s, %(remuneracion)s, %(ubicacion_trabajo)s,
            %(perfil_html)s, %(tareas_html)s, %(observaciones_html)s,
            %(link_oferta_entidad)s,
            %(fecha_limite_cv)s, %(scraped_at)s
        )
        ON CONFLICT (id) DO UPDATE SET
            entidad             = EXCLUDED.entidad,
            actividad           = EXCLUDED.actividad,
            sector              = EXCLUDED.sector,
            puesto              = EXCLUDED.puesto,
            jornada             = EXCLUDED.jornada,
            remuneracion        = EXCLUDED.remuneracion,
            ubicacion_trabajo   = EXCLUDED.ubicacion_trabajo,
            perfil_html         = EXCLUDED.perfil_html,
            tareas_html         = EXCLUDED.tareas_html,
            observaciones_html  = EXCLUDED.observaciones_html,
            link_oferta_entidad = EXCLUDED.link_oferta_entidad,
            fecha_limite_cv     = EXCLUDED.fecha_limite_cv,
            scraped_at          = EXCLUDED.scraped_at
    """
    with con, con.cursor() as cur:
        cur.execute(sql, d)


###############################################
##  DESCARGA Y PROCESADO DE OFERTAS FINALES  ##
###############################################

def obtener_links_archivos(con):
    sql = """
    SELECT d.id, d.link_oferta_entidad
    FROM   ofertas_detalle d
    LEFT   JOIN ofertas_archivo a USING(id)
    WHERE  d.link_oferta_entidad != '' AND a.id IS NULL
    """
    with con.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def _enlaces_criticos(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith(("mailto:", "tel:")):
            links.append(href)
        elif any(k in href for k in ("drive.google", "dropbox", "wetransfer")):
            links.append(href)
    correos = re.findall(r"[\w\.-]+@[\w\.-]+\.\w{2,}", soup.get_text(" "))
    links.extend(f"mailto:{e}" for e in correos)
    return list(dict.fromkeys(links))


def _clean_html(raw_html: str) -> str:
    try:
        doc_html = Document(raw_html).summary(html_partial=True)
        soup = BeautifulSoup(doc_html, "html.parser")
    except Exception:
        soup = BeautifulSoup(raw_html, "html.parser")

    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")

    text = soup.get_text("\n")
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}",   "\n\n", text)
    return text.strip()


def descargar_archivo(session, url, oid):
    print(f"Descargando archivo de la oferta {oid} desde {url}")

    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
    except Exception:
        print("   ✖ Error al descargar"); traceback.print_exc(); return None

    ctype = resp.headers.get("Content-Type", "").lower()
    ext = mimetypes.guess_extension(ctype) or ".html"
    es_pdf = "pdf" in ctype or ext == ".pdf" or url.lower().endswith(".pdf")
    # OJO: La fecha debe ser compatible con TIMESTAMPTZ de Postgres
    fecha = datetime.now() # psycopg2 se encarga de formatearla

    if es_pdf:
        try:
            pdf_text = extract_text(io.BytesIO(resp.content))
        except Exception as e:
            print(f"   ✖ Error al extraer texto del PDF: {e}")
            pdf_text = ""
        # ANTES: return (oid, url, fecha, None, pdf_text, None, None, None)
        # NUEVO: Devolvemos solo 5 elementos
        return (oid, url, fecha, None, pdf_text)
    else:
        print(" HTML recibido, guardando como html_raw")
        raw_html = resp.text
        links_extra = _enlaces_criticos(raw_html)
        clean_txt = _clean_html(raw_html)
        if links_extra:
            clean_txt += "\n\nCONTACTOS:\n" + "\n".join(links_extra)
        
        # ANTES: return (oid, url, fecha, clean_txt, None, None, None, None)
        # NUEVO: Devolvemos solo 5 elementos
        return (oid, url, fecha, clean_txt, None)


def guardar_archivo_db(con, tupla):
    # La tupla ahora tiene 5 elementos: 
    # (id, url_original, fecha_descarga, html_raw, pdf_texto)
    
    sql = """
    INSERT INTO ofertas_archivo (
        id, url_original, fecha_descarga,
        html_raw, pdf_texto
    ) 
    -- Solo debe haber 5 placeholders, uno por columna
    VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (id) DO UPDATE SET
        url_original   = EXCLUDED.url_original,
        fecha_descarga = EXCLUDED.fecha_descarga,
        html_raw       = EXCLUDED.html_raw,
        pdf_texto      = EXCLUDED.pdf_texto
    """
    with con, con.cursor() as cur:
        # La 'tupla' de 5 elementos ahora coincide con los 5 '%s' del VALUES
        cur.execute(sql, tupla)
        print(f"   → Archivo de oferta {tupla[0]} guardado/actualizado. Filas afectadas: {cur.rowcount}")

############################################
##            FUNCION MAIN                ##
############################################

def main():
    conn = create_db()
    s = login_session()
    ofertas = []
    vistos = set()
    for page in range(1, 4):
        ofertas_page = scrape_list_ofertas(s, page=page)
        
        print(f"Página {page}: {len(ofertas_page)} ofertas encontradas")
        print("id de Ofertas en esta página:")
        print([o['id'] for o in ofertas_page])
        for o in ofertas_page:
            if o.get("id") and o["id"] not in vistos:
                ofertas.append(o)
                vistos.add(o["id"])
    
    ofertas_limpias = limpiar_ofertas(conn, ofertas)

    if ofertas_limpias:
        insert_offer_list_into_db(conn, ofertas_limpias)
    else:
        print("No hay ofertas nuevas o todas ya están en la base de datos.")

    links = crear_lista_ofertas_links(conn)

    for link in links:
        print(f"Scrapeando detalles de la oferta: {link}")
        try:
            oferta_detalle = extraer_detalle(s, link)
            if oferta_detalle:
                guardar_detalle(conn, oferta_detalle)
                print(f"Oferta {oferta_detalle['id']} guardada correctamente.")
        except Exception as e:
            print(f"Error al procesar {link}: {e}")
            continue

    links_archivos = obtener_links_archivos(conn)
    for oid, link_externo in links_archivos:
        print(f"Descargando archivos de la oferta {oid}")
        datos_tupla = descargar_archivo(s, link_externo, oid)
        if datos_tupla:
            guardar_archivo_db(conn, datos_tupla)
            print(f"Archivo de la oferta {oid} guardado correctamente.")

    print("Proceso de scraping y guardado finalizado.")
    conn.close()
    s.close()



def create_db():
    # conn = sqlite3.connect(database)
    return psycopg2.connect(
        host=PG_HOST,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASS,
        port=PG_PORT,
    )


def dump(ofertas):
    for o in ofertas:
        print(f"{o['id']}: {o['titulo']} | {o['link']} | límite {o['fecha_oferta']}")


if __name__ == "__main__":
    main()
