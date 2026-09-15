# Asistente de Empleos

*Español · [English version below](#job-search-assistant-english)*

Buscador diario de ofertas de trabajo que selecciona las mejores vacantes para una candidata, redacta un CV
adaptado a cada una, las registra en un tracker de Airtable y envía un correo con todo listo para postular.
Además lee las respuestas de las empresas (rechazos, entrevistas, tests, ofertas) y las anota en el tracker.

Funciona como un único script de Python programado una vez al día. Todo lo sensible (claves, contraseñas,
datos personales) vive fuera del código, en un archivo `.env` que no se sube al repositorio.

## Qué hace cada corrida

1. **Lee el tracker de Airtable** (tabla `Postulaciones`) para saber qué vacantes ya se registraron y no
   volver a sugerirlas.
2. **Busca novedades en Gmail**: correos de los últimos 4 días que puedan ser respuestas de empresas a
   postulaciones registradas. Un modelo de lenguaje los asocia a una postulación y los clasifica (rechazo,
   entrevista, test, oferta, acuse de recibo, otro). Se agrega una línea con fecha en el campo Feedback y,
   solo en casos inequívocos, se actualiza el Estado (rechazo → Rechazado, invitación a entrevista →
   entrevista agendada con su fecha, oferta → Oferta recibida). Nunca retrocede ni reabre registros cerrados.
3. **Recopila ofertas nuevas** desde tres fuentes: alertas de empleo recibidas por Gmail (LinkedIn, Get on
   Board, etc.), la API pública de Get on Board, la API pública de Remotive y el RSS de We Work Remotely.
   Las vacantes ya enviadas en los últimos 90 días se omiten.
4. **Selecciona y redacta**: el modelo (DeepSeek en DeepInfra, API compatible con OpenAI) recibe el CV
   general y el material recopilado, elige hasta 5 ofertas que cumplan las reglas y devuelve, en JSON, un
   análisis (match, pros, contras) y un CV adaptado por oferta.
5. **Filtro geográfico determinista**: aunque el modelo se equivoque, solo pasan ofertas remotas abiertas a
   Chile (globales, LATAM o específicas de Chile) o híbridas en Santiago. Lo omitido queda en el log.
6. **Genera un PDF por oferta** con ReportLab, replicando el diseño del CV general.
7. **Registra cada oferta en Airtable** (estado inicial `Sugerida`) con país, modalidad, fuente, rango
   salarial publicado y una bitácora.
8. **Envía un correo** con la sección de novedades, una tarjeta por oferta (match, sueldo, pros, contras,
   botón "Postular" y botón "Ya apliqué", que llama a un webhook de Make o n8n para cambiar el estado en
   Airtable) y los PDFs adjuntos. El correo sale aunque no haya ofertas nuevas si hubo novedades.

## Estructura del repositorio

| Archivo | Para qué sirve |
|---|---|
| `buscar_empleo_V9.py` | Todo el flujo. Configuración al inicio del archivo. |
| `ejecutar_buscador.sh` | Lanzador: escribe el log, mantiene el equipo despierto y ejecuta el script. |
| `probar_llm.py` | Una llamada real al modelo con datos ficticios; verifica la clave y el formato de salida. |
| `probar_filtro.py` | Pruebas sin red del filtro geográfico; con `--gob` consulta Get on Board en vivo. |
| `probar_novedades.py` | Pruebas sin red de las reglas de seguimiento; `--correos`, `--clasificar`, `--aplicar`. |
| `probar_fuentes.py` | Muestra qué devuelve cada portal de empleos. |
| `diagnostico.py` | Muestra qué material está llegando (correos y portales) sin llamar al modelo. |
| `revisar_material.py` | Resume el archivo `ultimas_ofertas_crudas.txt` de la última corrida. |
| `.env.example` | Plantilla de configuración. Cópiala a `.env` y completa tus valores. |
| `dashboard/postulaciones-dashboard.html` | Panel de seguimiento en vivo sobre la tabla de Airtable (ver sección Dashboard). |

Archivos que se generan al correr y **no se suben** (están en `.gitignore`): `.env`, `cv.txt` (el CV
general, con datos personales), `historial_ofertas.json`, `cvs_generados/`, `registro_ejecuciones.log`,
`ultimas_ofertas_crudas.txt`, `novedades_preview.html`.

## Requisitos

- Python 3.10 o superior.
- Dependencias: `pip install reportlab requests` (todo lo demás es biblioteca estándar).
- Una cuenta de Gmail con **IMAP habilitado** y una **contraseña de aplicación** (no la contraseña normal).
- Una clave de API de [DeepInfra](https://deepinfra.com) (o cualquier proveedor compatible con la API de
  OpenAI; cambia `DEEPINFRA_BASE_URL` en el script).
- Una base de Airtable con la tabla descrita más abajo y un token personal con permisos
  `data.records:read`, `data.records:write` y `schema.bases:read`.
- Opcional: un webhook en Make o n8n para el botón "Ya apliqué".

## Configuración

1. Copia `.env.example` a `.env` en la misma carpeta del script y completa cada variable.
2. Crea `cv.txt` con el CV general en texto plano. Es el material del que el modelo redacta cada CV
   adaptado; no debe inventar nada que no esté ahí.
3. Ajusta al inicio de `buscar_empleo_V9.py`:
   - `BUSQUEDAS`: términos con los que se consultan los portales.
   - Los criterios del prompt (sueldo mínimo, tipo de puesto, ubicación) en `analizar_con_llm`.
   - Las constantes `CAMPO_*`: los IDs de los campos de tu tabla de Airtable (ver siguiente sección).

### Variables de entorno

| Variable | Descripción |
|---|---|
| `EMAIL_USUARIO` | Cuenta de Gmail que recibe las alertas y a la que se envía el resumen. |
| `EMAIL_PASSWORD` | Contraseña de aplicación de Gmail (16 caracteres). |
| `NOMBRE_CANDIDATA` / `CONTACTO_CANDIDATA` | Nombre y línea de contacto que van en la cabecera de cada PDF; el nombre también forma el prefijo del archivo (`CV_NombreApellido_…`). |
| `DEEPINFRA_API_KEY` | Clave de API del proveedor del modelo. |
| `MODELO_LLM` | Modelo principal. Por defecto `deepseek-ai/DeepSeek-V4.1-Flash`. |
| `MODELO_RESPALDO` | Opcional. Modelo para los dos últimos reintentos si el principal está saturado. |
| `AIRTABLE_TOKEN` | Token personal de Airtable. |
| `AIRTABLE_BASE` / `AIRTABLE_TABLA` | IDs de la base (`app…`) y la tabla (`tbl…`). |
| `WEBHOOK_URL` / `WEBHOOK_TOKEN` | Opcional. Webhook y secreto del botón "Ya apliqué". |
| `DIAS_ATRAS` | Opcional. Días hacia atrás para buscar alertas de empleo (3). |
| `DIAS_ATRAS_SEGUIMIENTO` | Opcional. Días hacia atrás para buscar respuestas de empresas (4). |
| `CARPETA_IMAP_SEGUIMIENTO` | Opcional. `inbox` (por defecto) o `todos` para incluir correos archivados. |

### Tabla de Airtable

Crea una tabla (por ejemplo `Postulaciones`) con estos campos y copia el ID de cada uno (`fld…`) en las
constantes `CAMPO_*` del script. El script escribe por ID, no por nombre, para que puedas renombrar campos.

| Campo | Tipo | Notas |
|---|---|---|
| Empresa | Texto (campo principal) | |
| Puesto | Texto | |
| País | Selección única | Chile, Remoto / Global, Estados Unidos, México, España, Otro… |
| Tipo de trabajo | Selección única | Remoto, Híbrido, Presencial |
| Fuente | Selección única | LinkedIn, Get on Board, Remotive, We Work Remotely, Referido, Otro… |
| CV utilizado | Texto | Nombre del PDF generado |
| Fecha de postulación | Fecha | |
| Estado | Selección única | Sugerida, Postulado, Test previo realizado, 1ª entrevista agendada, 1ª entrevista realizada, 2ª entrevista agendada, 2ª entrevista realizada, Oferta recibida, Rechazado, Sin respuesta, Retirado |
| Fecha 1ª entrevista / Fecha 2ª entrevista | Fecha | |
| Rango salarial publicado | Texto | Lo escribe el buscador si la oferta lo indica |
| Sueldo mensual confirmado | Texto | Lo completas tú |
| Feedback / Brechas identificadas | Texto largo | Bitácora: el buscador agrega líneas con fecha |
| Próxima acción | Texto | |
| Fecha de seguimiento | Fecha | |

El botón "Ya apliqué" del correo llama al webhook con el ID del registro; el escenario de Make o n8n es el
que cambia el Estado a `Postulado` (y conviene que también escriba la Fecha de postulación).

## Cómo ejecutarlo

```bash
python3 buscar_empleo_V9.py          # una corrida completa (envía correo y escribe en Airtable)
bash ejecutar_buscador.sh            # lo mismo, con log en registro_ejecuciones.log
```

Antes de la primera corrida completa conviene probar por partes, sin efectos secundarios:

```bash
python3 probar_llm.py                    # llamada real al modelo con datos ficticios
python3 probar_filtro.py                 # reglas de ubicación, sin red
python3 probar_filtro.py --gob           # Get on Board en vivo: ubicaciones ya explícitas
python3 probar_novedades.py              # reglas de seguimiento, sin red
python3 probar_novedades.py --correos    # qué correos se considerarían (Gmail solo lectura)
python3 probar_novedades.py --clasificar --html   # qué haría el modelo, sin escribir en Airtable
python3 diagnostico.py                   # qué material llega de correos y portales
```

### Programación diaria

En macOS se usa un LaunchAgent (`~/Library/LaunchAgents/<nombre>.plist`) de lunes a viernes. Ejemplo:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.tuusuario.buscarempleo</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>/RUTA/AL/PROYECTO/ejecutar_buscador.sh</string></array>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>0</integer></dict>
    <!-- repetir para Weekday 2, 3, 4 y 5 -->
  </array>
  <key>StandardOutPath</key><string>/tmp/buscarempleo.out</string>
  <key>StandardErrorPath</key><string>/tmp/buscarempleo.err</string>
</dict></plist>
```

Se carga con `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<nombre>.plist`. En Linux basta una
línea de cron. Programa la corrida a una hora en que el equipo esté encendido y despierto: si un portátil
está dormido con la tapa cerrada, el job se ejecuta a medias y las conexiones de red se pierden.

Notas del lanzador: `caffeinate -i` evita el reposo por inactividad mientras corre el script, y el script se
invoca con `arch -x86_64` porque en el equipo original las bibliotecas de Python están instaladas para
x86_64; si tus bibliotecas son nativas, quita ese prefijo.

## Dashboard de seguimiento

`dashboard/postulaciones-dashboard.html` es un panel de una sola página, sin dependencias, que lee la tabla
de Airtable en vivo y muestra lo que importa para dar seguimiento:

- **Acciones pendientes**, en orden de prioridad: ofertas por responder, entrevistas de los próximos 14 días,
  seguimientos vencidos, sugerencias del buscador sin decidir, postulaciones estancadas (más de 14 días sin
  novedad) y registros con datos incompletos.
- **Indicadores**: pipeline activo, próxima entrevista, postulaciones de la semana, tasa de respuesta y de
  rechazo, siempre como fracción (con pocas filas el porcentaje engaña).
- **Estado actual** por etapa del proceso, **postulaciones por semana** (8 semanas) y **efectividad por
  fuente**, en SVG con tooltips y vista de tabla accesible.
- **Tabla completa** con filtros por estado y fuente, búsqueda, orden, enlace a la vacante y al registro de
  Airtable, y el feedback de cada postulación expandible.

Está pensado para publicarse como *Artifact* de claude.ai con la capacidad `mcp`: la página consulta
Airtable a través del conector del usuario que la abre, sin guardar tokens en ningún sitio, y se refresca
cada cinco minutos. Fuera de claude.ai la página se renderiza pero no puede leer datos. Para usarlo con otra
base basta con cambiar los IDs de base, tabla y campos al inicio del script de la página. Las reglas de
cálculo (qué cuenta como activa, en proceso, estancada) están en el pie de la propia página.

## Reglas de negocio (dónde cambiarlas)

- **Ubicación**: criterio 1 del prompt y `_oferta_permitida` (filtro determinista). Hoy: remoto global,
  LATAM o Chile, o híbrido en Santiago. Presencial queda excluido.
- **Sueldo mínimo y tipo de puesto**: criterios 3 a 7 del prompt en `analizar_con_llm`.
- **Idioma del CV**: regla prioritaria del prompt en `analizar_con_llm`; el modelo redacta cada CV en el
  idioma de la descripción de su oferta y lo declara en `cv.idioma`, que el PDF usa para los títulos de
  sección (`_idioma_cv`, con detección por texto como respaldo).
- **Máximo de ofertas por correo**: `MAX_OFERTAS`.
- **Novedades**: `_cambios_para_novedad` (reglas de Estado), `_PALABRAS_RECLUTAMIENTO` y `_ASUNTOS_DIGEST`
  (qué correos se consideran), prompt en `clasificar_novedades`.
- **Duplicados**: `historial_ofertas.json` guarda por 90 días las vacantes enviadas (por empresa+puesto y por
  enlace) y los correos ya revisados.
- **Reintentos**: el modelo se reintenta ante errores 429/5xx con respaldo de modelo; Gmail se reintenta 3
  veces con timeout de 60 s.

## Seguridad

- Nunca subas `.env` ni ningún archivo con claves. El `.gitignore` incluido los excluye, junto con el CV,
  el historial, los PDFs y los logs, que contienen datos personales.
- Si una clave se expone (por ejemplo, pegada en un chat o en un commit), rótala de inmediato en el panel del
  proveedor: contraseña de aplicación de Gmail, clave de DeepInfra, token de Airtable, secreto del webhook.
- El token de Airtable solo necesita permisos sobre la base del tracker.
- Los datos que ve el modelo (CV y ofertas) se envían al proveedor del modelo; revisa su política de datos.

## Limitaciones conocidas

- El cambio a `Postulado` depende del webhook externo; sin él, se actualiza a mano en Airtable.
- Las ofertas que llegan por correo no traen ubicación estructurada; el modelo la deduce del enlace y la
  descripción, y ante la duda la oferta se omite.
- El seguimiento de novedades clasifica con un modelo: la línea que agrega en Feedback nombra remitente y
  asunto para que cualquier error de atribución sea visible y fácil de corregir.

## Licencia

MIT. Ver el archivo [LICENSE](LICENSE).

---

# Job Search Assistant (English)

Daily job-offer finder that picks the best openings for a candidate, writes a tailored CV for each one,
logs them in an Airtable tracker and sends an email with everything ready to apply. It also reads the
companies' replies (rejections, interviews, tests, offers) and records them in the tracker.

It runs as a single Python script scheduled once a day. Everything sensitive (keys, passwords, personal
data) lives outside the code, in a `.env` file that is never committed.

## What each run does

1. **Reads the Airtable tracker** (`Postulaciones` table) to know which openings are already registered
   and avoid suggesting them again.
2. **Looks for updates in Gmail**: emails from the last 4 days that may be company replies to registered
   applications. A language model matches each one to an application and classifies it (rejection,
   interview, test, offer, acknowledgment, other). A dated line is appended to the Feedback field and, only
   in unambiguous cases, the Status is updated (rejection → Rechazado, interview invitation → interview
   scheduled with its date, offer → Oferta recibida). It never moves backwards and never reopens closed
   records.
3. **Collects new offers** from three kinds of sources: job alerts received in Gmail (LinkedIn, Get on
   Board, etc.), the public Get on Board API, the public Remotive API and the We Work Remotely RSS feed.
   Openings already sent in the last 90 days are skipped.
4. **Selects and writes**: the model (DeepSeek on DeepInfra, OpenAI-compatible API) receives the general CV
   and the collected material, picks up to 5 offers that meet the rules and returns, as JSON, an analysis
   (match, pros, cons) and a tailored CV per offer, written in the language of the offer.
5. **Deterministic location filter**: even if the model slips, only remote offers open to Chile (global,
   LATAM or Chile-specific) or hybrid offers in Santiago get through. Whatever is dropped is logged.
6. **Generates one PDF per offer** with ReportLab, replicating the layout of the general CV.
7. **Registers each offer in Airtable** (initial status `Sugerida`) with country, work type, source,
   published salary range and a log line.
8. **Sends an email** with the updates section, one card per offer (match, salary, pros, cons, an "Apply"
   button and an "I applied" button that calls a Make or n8n webhook to change the status in Airtable) and
   the PDFs attached. The email goes out even when there are no new offers, as long as there are updates.

## Repository layout

| File | Purpose |
|---|---|
| `buscar_empleo_V9.py` | The whole flow. Configuration at the top of the file. |
| `ejecutar_buscador.sh` | Launcher: writes the log, keeps the machine awake and runs the script. |
| `probar_llm.py` | One real model call with fake data; checks the key and the output format. |
| `probar_filtro.py` | Offline tests of the location filter; `--gob` queries Get on Board live. |
| `probar_novedades.py` | Offline tests of the follow-up rules; `--correos`, `--clasificar`, `--aplicar`. |
| `probar_fuentes.py` | Shows what each job board returns. |
| `diagnostico.py` | Shows what material is coming in (emails and boards) without calling the model. |
| `revisar_material.py` | Summarizes the `ultimas_ofertas_crudas.txt` file from the last run. |
| `.env.example` | Configuration template. Copy it to `.env` and fill in your values. |
| `dashboard/postulaciones-dashboard.html` | Live tracking dashboard on top of the Airtable table (see the Dashboard section). |

Files generated at runtime that are **never committed** (listed in `.gitignore`): `.env`, `cv.txt` (the
general CV, with personal data), `historial_ofertas.json`, `cvs_generados/`, `registro_ejecuciones.log`,
`ultimas_ofertas_crudas.txt`, `novedades_preview.html`.

## Requirements

- Python 3.10 or newer.
- Dependencies: `pip install reportlab requests` (everything else is the standard library).
- A Gmail account with **IMAP enabled** and an **app password** (not the regular password).
- A [DeepInfra](https://deepinfra.com) API key (or any provider compatible with the OpenAI API; change
  `DEEPINFRA_BASE_URL` in the script).
- An Airtable base with the table described below and a personal access token with the scopes
  `data.records:read`, `data.records:write` and `schema.bases:read`.
- Optional: a Make or n8n webhook for the "I applied" button.

## Setup

1. Copy `.env.example` to `.env` in the same folder as the script and fill in each variable.
2. Create `cv.txt` with the general CV in plain text. It is the only material the model may use to write
   each tailored CV; it must not invent anything that is not there.
3. Adjust at the top of `buscar_empleo_V9.py`:
   - `BUSQUEDAS`: search terms used to query the job boards.
   - The prompt criteria (minimum salary, role type, location) in `analizar_con_llm`.
   - The `CAMPO_*` constants: the field IDs of your Airtable table (see next section).

### Environment variables

| Variable | Description |
|---|---|
| `EMAIL_USUARIO` | Gmail account that receives the alerts and the daily summary. |
| `EMAIL_PASSWORD` | Gmail app password (16 characters). |
| `NOMBRE_CANDIDATA` / `CONTACTO_CANDIDATA` | Name and contact line printed in the header of every PDF; the name also forms the file prefix (`CV_NameSurname_…`). |
| `DEEPINFRA_API_KEY` | API key of the model provider. |
| `MODELO_LLM` | Main model. Defaults to `deepseek-ai/DeepSeek-V4.1-Flash`. |
| `MODELO_RESPALDO` | Optional. Model used for the last two retries when the main one is overloaded. |
| `AIRTABLE_TOKEN` | Airtable personal access token. |
| `AIRTABLE_BASE` / `AIRTABLE_TABLA` | Base (`app…`) and table (`tbl…`) IDs. |
| `WEBHOOK_URL` / `WEBHOOK_TOKEN` | Optional. Webhook and secret for the "I applied" button. |
| `DIAS_ATRAS` | Optional. Days back to search for job alerts (3). |
| `DIAS_ATRAS_SEGUIMIENTO` | Optional. Days back to search for company replies (4). |
| `CARPETA_IMAP_SEGUIMIENTO` | Optional. `inbox` (default) or `todos` to include archived mail. |

### Airtable table

Create a table (for example `Postulaciones`) with these fields and copy each field ID (`fld…`) into the
`CAMPO_*` constants of the script. The script writes by ID, not by name, so you can rename fields freely.

| Field | Type | Notes |
|---|---|---|
| Empresa | Text (primary field) | Company |
| Puesto | Text | Role |
| País | Single select | Chile, Remoto / Global, Estados Unidos, México, España, Otro… |
| Tipo de trabajo | Single select | Remoto, Híbrido, Presencial |
| Fuente | Single select | LinkedIn, Get on Board, Remotive, We Work Remotely, Referido, Otro… |
| CV utilizado | Text | Name of the generated PDF |
| Fecha de postulación | Date | Application date |
| Estado | Single select | Sugerida, Postulado, Test previo realizado, 1ª entrevista agendada, 1ª entrevista realizada, 2ª entrevista agendada, 2ª entrevista realizada, Oferta recibida, Rechazado, Sin respuesta, Retirado |
| Fecha 1ª entrevista / Fecha 2ª entrevista | Date | Interview dates |
| Rango salarial publicado | Text | Written by the finder when the offer states it |
| Sueldo mensual confirmado | Text | Filled in by you |
| Feedback / Brechas identificadas | Long text | Log: the finder appends dated lines |
| Próxima acción | Text | Next action |
| Fecha de seguimiento | Date | Follow-up date |

The "I applied" button in the email calls the webhook with the record ID; the Make or n8n scenario is what
changes the Status to `Postulado` (it should also write the application date).

## How to run it

```bash
python3 buscar_empleo_V9.py          # one full run (sends the email and writes to Airtable)
bash ejecutar_buscador.sh            # same, with a log in registro_ejecuciones.log
```

Before the first full run it is worth testing piece by piece, with no side effects:

```bash
python3 probar_llm.py                    # real model call with fake data
python3 probar_filtro.py                 # location rules, offline
python3 probar_filtro.py --gob           # Get on Board live: locations are now explicit
python3 probar_novedades.py              # follow-up rules, offline
python3 probar_novedades.py --correos    # which emails would be considered (Gmail read-only)
python3 probar_novedades.py --clasificar --html   # what the model would do, without writing to Airtable
python3 diagnostico.py                   # what material arrives from emails and boards
```

### Daily schedule

On macOS a LaunchAgent (`~/Library/LaunchAgents/<name>.plist`) runs it Monday to Friday. Example:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.youruser.jobfinder</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>/PATH/TO/PROJECT/ejecutar_buscador.sh</string></array>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>0</integer></dict>
    <!-- repeat for Weekday 2, 3, 4 and 5 -->
  </array>
  <key>StandardOutPath</key><string>/tmp/jobfinder.out</string>
  <key>StandardErrorPath</key><string>/tmp/jobfinder.err</string>
</dict></plist>
```

Load it with `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<name>.plist`. On Linux a single cron
line is enough. Schedule the run at a time when the machine is on and awake: if a laptop is asleep with the
lid closed, the job runs in fits and starts and network connections die.

Launcher notes: `caffeinate -i` prevents idle sleep while the script runs, and the script is invoked with
`arch -x86_64` because on the original machine the Python libraries are installed for x86_64; if your
libraries are native, drop that prefix.

## Tracking dashboard

`dashboard/postulaciones-dashboard.html` is a single-page, dependency-free panel that reads the Airtable
table live and shows what matters for follow-up:

- **Pending actions**, in priority order: offers awaiting an answer, interviews in the next 14 days,
  overdue follow-ups, finder suggestions not yet decided, stale applications (more than 14 days without
  news) and records with incomplete data.
- **KPIs**: active pipeline, next interview, applications this week, response and rejection rates, always
  shown as fractions (with few rows a percentage misleads).
- **Current status** by pipeline stage, **applications per week** (8 weeks) and **effectiveness by
  source**, drawn in SVG with tooltips and an accessible table view.
- **Full table** with status and source filters, search, sorting, links to the job posting and to the
  Airtable record, and each application's expandable feedback.

It is meant to be published as a claude.ai *Artifact* with the `mcp` capability: the page queries Airtable
through the connector of whoever opens it, stores no tokens anywhere, and refreshes every five minutes.
Outside claude.ai the page renders but cannot read data. To use it with another base, change the base, table
and field IDs at the top of the page script. The calculation rules (what counts as active, in progress,
stale) are in the page footer.

## Business rules (where to change them)

- **Location**: criterion 1 of the prompt and `_oferta_permitida` (deterministic filter). Today: remote
  global, LATAM or Chile, or hybrid in Santiago. On-site is excluded.
- **Minimum salary and role type**: criteria 3 to 7 of the prompt in `analizar_con_llm`.
- **CV language**: top-priority rule of the prompt in `analizar_con_llm`; the model writes each CV in the
  language of the offer's description and declares it in `cv.idioma`, which the PDF uses for its section
  titles (`_idioma_cv`, with text-based detection as a fallback).
- **Maximum offers per email**: `MAX_OFERTAS`.
- **Updates**: `_cambios_para_novedad` (status rules), `_PALABRAS_RECLUTAMIENTO` and `_ASUNTOS_DIGEST`
  (which emails are considered), prompt in `clasificar_novedades`.
- **Duplicates**: `historial_ofertas.json` keeps for 90 days the openings already sent (by company+role and
  by link) and the emails already reviewed.
- **Retries**: the model is retried on 429/5xx errors with a fallback model; Gmail is retried 3 times with a
  60 s timeout.

## Security

- Never commit `.env` or any file with keys. The included `.gitignore` excludes them, together with the CV,
  the history, the PDFs and the logs, which contain personal data.
- If a key is exposed (for example pasted in a chat or in a commit), rotate it immediately in the provider's
  dashboard: Gmail app password, DeepInfra key, Airtable token, webhook secret.
- The Airtable token only needs permissions on the tracker base.
- The data the model sees (CV and offers) is sent to the model provider; review its data policy.

## Known limitations

- The change to `Postulado` depends on the external webhook; without it, update it by hand in Airtable.
- Offers that arrive by email carry no structured location; the model infers it from the link and the
  description, and when in doubt the offer is dropped.
- Update tracking relies on a model: the line it appends to Feedback names the sender and subject so that
  any misattribution is visible and easy to fix.

## License

MIT. See the [LICENSE](LICENSE) file.
