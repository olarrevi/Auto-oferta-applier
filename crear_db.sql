PRAGMA FOREIGN_KEYS = ON;

CREATE TABLE IF NOT EXISTS ofertas_listado (
    id INTEGER PRIMARY KEY,
    titulo TEXT NOT NULL,
    link_detalle TEXT NOT NULL,
    fecha_oferta TEXT NOT NULL,
    fecha_limite TEXT NULL,
    scraped_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ofertas_detalle (
    id                  INTEGER PRIMARY KEY,
    entidad             TEXT,
    actividad           TEXT,
    sector              TEXT,
    puesto              TEXT,
    jornada             TEXT,
    remuneracion        TEXT,
    ubicacion_trabajo   TEXT,
    perfil_html         TEXT,
    tareas_html         TEXT,
    observaciones_html  TEXT,
    link_oferta_entidad TEXT,      -- ← nueva
    link_entidad        TEXT,
    descripcion_html    TEXT,
    fecha_limite_cv     DATE,
    scraped_at          DATETIME NOT NULL,
    FOREIGN KEY (id) REFERENCES ofertas_listado(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ofertas_archivo (
  id              INTEGER PRIMARY KEY,
  url_original    TEXT,
  fecha_descarga  DATETIME,
  html_raw        TEXT,   -- null si era PDF
  pdf_texto       TEXT,   -- contenido extraído para búsquedas
  score           REAL,
  apta            INTEGER, -- 0/1
  justificacion   TEXT,     -- Justificacion apto/no apto
  FOREIGN KEY (id) REFERENCES ofertas_listado(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cartas (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    oferta_id                   INTEGER NOT NULL,
    carta_texto                 TEXT NOT NULL,
    destinatario                TEXT NULL,
    asunto_email                TEXT NULL,
    cuerpo_email                TEXT NULL,
    fecha_generacion            DATETIME NOT NULL,
    permite_envio_email         INTEGER NOT NULL, -- 0: no permite enviar por email, 1: permite enviar por email
    enviado_email               INTEGER DEFAULT 0, -- 0: no enviado, 1: enviado
    FOREIGN KEY (oferta_id) REFERENCES ofertas_listado(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ofertas_listado_id ON ofertas_listado(id);
CREATE INDEX IF NOT EXISTS idx_ofertas_detalle_id ON ofertas_detalle(id);

----------------------- TRIGGERS I LOGS ---------------------------

CREATE TABLE IF NOT EXISTS acciones_log(
    log_id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    oferta_id               INTEGER NOT NULL,
    tabla_afectada          TEXT NOT NULL,
    accion                  TEXT NOT NULL,
    detalles                TEXT,
    fecha_evento            DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (oferta_id) REFERENCES ofertas_listado(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_acciones_log_oferta ON acciones_log(oferta_id);

-- 1) Inserción en ofertas_listado

CREATE TRIGGER IF NOT EXISTS trg_ofertas_listado_ai
AFTER INSERT ON ofertas_listado
BEGIN
    INSERT INTO acciones_log (oferta_id, tabla_afectada, accion, detalles)
    VALUES (NEW.id, 'ofertas_listado', 'INSERT', 'Oferta listada');
END;

-- 2) Insercion en ofertas_detalle

CREATE TRIGGER IF NOT EXISTS trg_ofertas_detalle_ai
AFTER INSERT ON ofertas_detalle
BEGIN
    INSERT INTO acciones_log (oferta_id, tabla_afectada, accion, detalles)
    VALUES (NEW.id, 'ofertas_detalle', 'INSERT', 'Detalle de oferta scrapeado');
END;

-- 3) Inserción en ofertas_archivo (PDF/HTML + evaluación)
CREATE TRIGGER IF NOT EXISTS trg_ofertas_archivo_ai
AFTER INSERT ON ofertas_archivo
BEGIN
    INSERT INTO acciones_log (oferta_id, tabla_afectada, accion, detalles)
    VALUES (
        NEW.id,
        'ofertas_archivo',
        'INSERT',
        printf(
            'Archivo guardado (apta=%d, score=%.1f)',
            COALESCE(NEW.apta, -1),
            COALESCE(NEW.score, -1)
        )
    );
END;

-- 4) Cambio de apta o score en ofertas_archivo
CREATE TRIGGER IF NOT EXISTS trg_ofertas_archivo_au
AFTER UPDATE OF apta, score ON ofertas_archivo
WHEN OLD.apta IS NOT NEW.apta OR OLD.score IS NOT NEW.score
BEGIN
    INSERT INTO acciones_log (oferta_id, tabla_afectada, accion, detalles)
    VALUES (
        NEW.id,
        'ofertas_archivo',
        'UPDATE',
        printf(
            'apta: %d→%d, score: %.1f→%.1f',
            COALESCE(OLD.apta, -1),
            COALESCE(NEW.apta, -1),
            COALESCE(OLD.score, -1),
            COALESCE(NEW.score, -1)
        )
    );
END;

-- 5) Generación de la carta de presentación
CREATE TRIGGER IF NOT EXISTS trg_cartas_ai
AFTER INSERT ON cartas
BEGIN
    INSERT INTO acciones_log (oferta_id, tabla_afectada, accion, detalles)
    VALUES (NEW.oferta_id, 'cartas', 'INSERT', 'Carta generada');
END;

-- 6) Envío de correo (se marca enviado_email = 1)
CREATE TRIGGER IF NOT EXISTS trg_cartas_email_sent
AFTER UPDATE OF enviado_email ON cartas
WHEN OLD.enviado_email = 0 AND NEW.enviado_email = 1
BEGIN
    INSERT INTO acciones_log (oferta_id, tabla_afectada, accion, detalles)
    VALUES (NEW.oferta_id, 'cartas', 'EMAIL_SENT', 'Correo enviado con carta y CV adjuntos');
END;