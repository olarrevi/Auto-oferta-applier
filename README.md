# Auto-oferta-applier

**Auto-oferta-applier** es un sistema automatizado diseñado para agilizar el proceso de solicitud de empleo. Realiza scraping de ofertas de trabajo de fuentes específicas, las evalúa utilizando IA para determinar su idoneidad, genera cartas de presentación personalizadas y automatiza el proceso de envío de solicitudes por correo electrónico.

## Características

- **Scraping Automatizado**: Obtiene ofertas de trabajo de fuentes como **CIDO** y **COLPIS**.
- **Evaluación con IA**: Utiliza IA (OpenAI/DeepSeek) para analizar las descripciones de los trabajos y determinar si coinciden con el perfil del usuario, asignando una puntuación y una justificación.
- **Generación de Contenido**: Redacta automáticamente cartas de presentación personalizadas y cuerpos de correo electrónico para las ofertas adecuadas.
- **Envío Automatizado**: Envía las solicitudes por correo electrónico utilizando la **API de Gmail**, adjuntando la carta de presentación generada y el CV.
- **Seguimiento y Registro**: Mantiene una base de datos SQLite local (`ofertas.db`) para realizar un seguimiento de las ofertas, su estado y un registro de todas las acciones realizadas.

## Flujo de Trabajo

El sistema opera a través de una secuencia de scripts orquestados por `orquestador.py`:

1.  **Scraping**: `a_scrapper_cido.py` y `b_scrapper_colpis.py` recuperan nuevas ofertas.
2.  **Evaluación**: `c_evaluador.py` analiza las ofertas utilizando IA.
3.  **Redacción**: `d_redactor.py` crea los materiales de solicitud para las ofertas aprobadas.
4.  **Envío**: `e_enviador.py` envía los correos electrónicos.
5.  **Notificación**: `f_enviar_ofertes_altres_usuaris.py` puede reenviar ofertas relevantes a otros usuarios.

## Requisitos Previos

- **Python 3.8+**
- **Google Cloud Project**: API de Gmail habilitada con `credentials.json` para autenticación.
- **Clave API de OpenAI/DeepSeek**: Para la evaluación de IA y generación de texto.

## Instalación

1.  **Clonar el repositorio**:
    ```bash
    git clone <url_del_repositorio>
    cd Auto-oferta-applier
    ```

2.  **Instalar dependencias**:
    ```bash
    pip install -r requirements.txt
    ```

3.  **Configuración de la Base de Datos**:
    Inicializa la base de datos SQLite utilizando el script SQL proporcionado:
    ```bash
    sqlite3 ofertas.db < crear_db.sql
    ```

4.  **Configuración**:
    -   Crea un archivo `.env` en el directorio raíz y añade tus claves API (ej. `OPENAI_API_KEY`).
    -   Coloca tu archivo `credentials.json` de Google Cloud en el directorio raíz.
    -   La primera vez que ejecutes el enviador de correos, se te pedirá autenticarte a través del navegador para generar `token.json`.

## Uso

Para ejecutar todo el flujo de trabajo secuencialmente:

```bash
python orquestador.py
```

Este script ejecutará todos los scripts en el directorio `scripts/` en orden alfabético.

Alternativamente, puedes ejecutar pasos individuales manualmente:

```bash
python scripts/a_scrapper_cido.py
python scripts/c_evaluador.py
# etc.
```

## Estructura del Proyecto

-   `scripts/`: Contiene los scripts de python individuales para cada paso del pipeline.
-   `cartas/`: Directorio donde se almacenan las cartas de presentación generadas.
-   `orquestador.py`: Punto de entrada principal para ejecutar el flujo de trabajo completo.
-   `crear_db.sql`: Esquema SQL para la base de datos SQLite.
-   `requirements.txt`: Dependencias de Python.
-   `credentials.json` / `token.json`: Archivos de autenticación de la API de Google.

## Licencia

[Tu Licencia Aquí]
