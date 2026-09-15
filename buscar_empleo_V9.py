# -*- coding: utf-8 -*-
"""
Buscador de ofertas + generador de CVs adaptados en PDF.

Cambio principal respecto a la versión anterior:
  - El modelo (DeepSeek vía DeepInfra) ya NO devuelve HTML. Devuelve JSON estructurado.
  - El PDF ya NO se arma con fpdf.write_html (que ignora casi todo el formato).
    Se maqueta a mano con ReportLab, replicando el diseño del CV general.

Requisitos:
    pip install reportlab requests
"""

import os
import sys
import re
import json
import html
import email
import email.utils
import hashlib
import imaplib
import smtplib
import time
import datetime
import unicodedata
import urllib.parse

import warnings
# El charset_normalizer de este intérprete está roto (wheel x86_64); requests lo
# avisa al importarse, pero decodifica JSON en utf-8 sin problema. Se silencia.
warnings.filterwarnings("ignore", message="Unable to find acceptable character detection")
import requests
from email.header import decode_header, make_header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether,
)

# ===================== CONFIGURACIÓN =====================
# Carpeta donde vive este archivo. Todo se resuelve desde aquí,
# para que funcione igual si lo lanza cron desde otra ubicación.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _cargar_env():
    """Lee el archivo .env que está junto a este script (una línea CLAVE=valor)."""
    ruta = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(ruta):
        return
    with open(ruta, "r", encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, valor = linea.split("=", 1)
            os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


_cargar_env()

EMAIL_USUARIO = os.environ.get("EMAIL_USUARIO", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
DEEPINFRA_API_KEY = os.environ.get("DEEPINFRA_API_KEY", "")
DEEPINFRA_BASE_URL = "https://api.deepinfra.com/v1/openai"
MODELO_LLM = os.environ.get("MODELO_LLM", "deepseek-ai/DeepSeek-V4.1-Flash")
# Si el modelo principal está saturado, en los dos últimos reintentos se usa este.
# Por defecto es el mismo; se puede cambiar en .env sin tocar el código.
MODELO_RESPALDO = os.environ.get("MODELO_RESPALDO", MODELO_LLM)
REINTENTOS = 5
# 5 CVs en JSON rondan 5-8k tokens; 12k deja margen sin pasar del máximo de DeepInfra (16384).
MAX_TOKENS_SALIDA = 12000
# Timeout de requests (conexión, lectura) en segundos. La entrada puede ser ~60k tokens.
TIMEOUT_LLM = (15, 300)
# Tope de material enviado al modelo (evita peticiones enormes y lentas).
MAX_CARACTERES_OFERTAS = 220000

# --- Airtable (seguimiento de postulaciones) ---
AIRTABLE_TOKEN = os.environ.get("AIRTABLE_TOKEN", "")
AIRTABLE_BASE = os.environ.get("AIRTABLE_BASE", "")
AIRTABLE_TABLA = os.environ.get("AIRTABLE_TABLA", "")
# IDs de los campos del tracker (más estables que los nombres)
CAMPO_EMPRESA = "fldVPI9EyzjVjg7Xj"   # Empresa
CAMPO_PUESTO = "fldHRpRIRWnSCxShv"    # Puesto
CAMPO_ESTADO = "fldwPV6PPL5zZ55ZS"    # Estado
CAMPO_CV = "fldPVpBpW3V5vqv0F"        # CV utilizado
CAMPO_LOG = "fldkRQ4W0wdrznIhk"       # Feedback / Brecha (bitácora)
CAMPO_PAIS = "fldwNyMXqRNCjKdrH"      # País / región
CAMPO_MODALIDAD = "fldZadOA4HRXds2k3" # Remoto / Híbrido / Presencial
CAMPO_FUENTE = "fldqJYguDKdPztt1a"    # De dónde salió la vacante
CAMPO_SUELDO = "fldx9kllUEe7YDwdL"    # Rango salarial publicado (texto, acepta rangos)
CAMPO_FECHA_POST = "fldIhqWmNlUkk6XO1"   # Fecha de postulación (date, YYYY-MM-DD)
CAMPO_FECHA_ENT1 = "fldYYlwKbYrV4R230"   # Fecha 1ª entrevista (date)
CAMPO_FECHA_ENT2 = "fldEcLSF1sqXgK4ng"   # Fecha 2ª entrevista (date)
CAMPO_PROX_ACCION = "fldn9xqcAMcSagXe3"  # Próxima acción (texto de una línea)
CAMPO_FECHA_SEG = "fldjQUDC43U0APbaN"    # Fecha de seguimiento (date; hoy no se escribe)
ESTADO_INICIAL = "Sugerida"

# Embudo del tracker. El número es la posición: el Estado nunca retrocede y
# desde ORDEN_CERRADO en adelante son estados cerrados (solo se agrega bitácora).
ESTADOS_ORDEN = {
    "Sugerida": 0, "Postulado": 1, "Aplicado": 1, "Test previo realizado": 2,
    "1ª entrevista agendada": 3, "1ª entrevista realizada": 4,
    "2ª entrevista agendada": 5, "2ª entrevista realizada": 6,
    "Oferta recibida": 7, "Rechazado": 8, "Sin respuesta": 9, "Retirado": 10,
}
ORDEN_CERRADO = 8

# --- Novedades de postulaciones (respuestas de empresas por correo) ---
DIAS_ATRAS_SEGUIMIENTO = int(os.environ.get("DIAS_ATRAS_SEGUIMIENTO", "4"))
# Conexión a Gmail: sin timeout, un socket muerto (Mac dormido) dejaba la corrida
# colgada durante horas. Con timeout y reintentos falla rápido y vuelve a intentar.
TIMEOUT_IMAP = 60
REINTENTOS_IMAP = 3
ESPERA_IMAP = 30
MAX_CORREOS_SEGUIMIENTO = 40
MAX_EXTRACTO_CORREO = 1500
# Carpeta IMAP donde buscar respuestas. "inbox" por defecto; "todos" busca la
# carpeta "All Mail" de Gmail (incluye archivados) sea cual sea el idioma de la cuenta.
CARPETA_IMAP_SEGUIMIENTO = os.environ.get("CARPETA_IMAP_SEGUIMIENTO", "inbox")

# --- Botón "Ya apliqué" (webhook de Make o n8n) ---
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
WEBHOOK_TOKEN = os.environ.get("WEBHOOK_TOKEN", "")

RUTA_CV_GENERAL = os.path.join(BASE_DIR, "cv.txt")
RUTA_HISTORIAL = os.path.join(BASE_DIR, "historial_ofertas.json")
# Días que una oferta permanece en el historial antes de poder repetirse.
DIAS_HISTORIAL = 90
CARPETA_SALIDA = os.path.join(BASE_DIR, "cvs_generados")
MAX_OFERTAS = 5

# Datos fijos de contacto para la cabecera de cada PDF (no dependen de la oferta).
# Se leen del .env para que el código no contenga datos personales.
DATOS_FIJOS = {
    "nombre": os.environ.get("NOMBRE_CANDIDATA", ""),
    "contacto": os.environ.get("CONTACTO_CANDIDATA", ""),
}

# ===================== ESTILO DEL PDF =====================
PAGINA = LETTER                      # cámbialo a A4 si postulas en LATAM/Europa
MARGEN = 15 * mm
C_PRIMARIO = colors.HexColor("#16283C")   # azul petróleo del nombre y títulos
C_ACENTO = colors.HexColor("#2E86C1")     # línea bajo cada sección
C_TEXTO = colors.HexColor("#1F1F1F")
C_SUAVE = colors.HexColor("#5B6570")


def _rt(texto):
    """Texto plano -> mini-markup de ReportLab, escapando &, < y >.
    Soporta **negrita** y _cursiva_ escritas por el modelo."""
    if not texto:
        return ""
    s = html.escape(str(texto), quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s, flags=re.S)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", s, flags=re.S)
    s = re.sub(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)", r"<i>\1</i>", s, flags=re.S)
    return s


def _estilos():
    base = ParagraphStyle(
        "base", fontName="Helvetica", fontSize=9.3, leading=12.6,
        textColor=C_TEXTO, spaceAfter=0, allowOrphans=0,
    )
    return {
        "nombre": ParagraphStyle(
            "nombre", parent=base, fontName="Helvetica-Bold", fontSize=20,
            leading=23, textColor=C_PRIMARIO, alignment=TA_CENTER, spaceAfter=3),
        "titular": ParagraphStyle(
            "titular", parent=base, fontSize=10, leading=13,
            textColor=C_ACENTO, alignment=TA_CENTER, spaceAfter=4),
        "contacto": ParagraphStyle(
            "contacto", parent=base, fontSize=8.4, leading=11,
            textColor=C_SUAVE, alignment=TA_CENTER),
        "seccion": ParagraphStyle(
            "seccion", parent=base, fontName="Helvetica-Bold", fontSize=10.2,
            leading=12.5, textColor=C_PRIMARIO, spaceBefore=10, spaceAfter=1,
            keepWithNext=1),
        "cuerpo": ParagraphStyle(
            "cuerpo", parent=base, alignment=TA_JUSTIFY, spaceAfter=2),
        "cargo": ParagraphStyle(
            "cargo", parent=base, fontName="Helvetica-Bold", fontSize=9.9,
            leading=12.8, keepWithNext=1),
        "fechas": ParagraphStyle(
            "fechas", parent=base, fontSize=8.7, leading=12.8,
            textColor=C_SUAVE, alignment=2),
        "empresa": ParagraphStyle(
            "empresa", parent=base, fontName="Helvetica-Oblique", fontSize=8.9,
            leading=11.6, textColor=C_SUAVE, spaceAfter=2, keepWithNext=1),
        "bullet": ParagraphStyle(
            "bullet", parent=base, alignment=TA_JUSTIFY, leftIndent=9,
            bulletIndent=1, spaceAfter=2),
    }


# Títulos de sección en los dos idiomas: el CV debe salir en uno solo.
TITULOS = {
    "es": {"resumen": "Resumen profesional", "comp": "Competencias clave",
           "exp": "Experiencia profesional",
           "edu": "Educación, certificaciones e idiomas",
           "extra": "Información adicional"},
    "en": {"resumen": "Professional summary", "comp": "Core skills",
           "exp": "Professional experience",
           "edu": "Education, certifications & languages",
           "extra": "Additional information"},
}


def _idioma_cv(cv):
    """Idioma del CV ("es" o "en") para que los títulos de sección del PDF vayan
    en el mismo idioma que el contenido. Usa el que declaró el modelo en
    cv["idioma"]; si falta o no es válido, lo detecta por el texto."""
    declarado = str(cv.get("idioma") or "").strip().lower()[:2]
    if declarado in ("es", "en"):
        return declarado
    texto = " ".join([
        str(cv.get("resumen", "")), str(cv.get("titular", "")),
        " ".join(str(x) for x in cv.get("competencias") or []),
    ]).lower()
    marcas_en = sum(texto.count(p) for p in
                    (" the ", " and ", " with ", " for ", " of ", " years ",
                     " data ", " across ", " team "))
    marcas_es = sum(texto.count(p) for p in
                    (" de ", " y ", " con ", " para ", " la ", " el ",
                     " años ", " datos ", " equipo "))
    return "en" if marcas_en > marcas_es else "es"


def _titulo_seccion(texto, st):
    """Título de sección seguido de su línea de acento.
    Devuelve una lista plana: el estilo lleva keepWithNext, así que el
    título nunca queda solo al final de una página."""
    return [
        Paragraph(_rt(texto.upper()), st["seccion"]),
        HRFlowable(width="100%", thickness=0.9, color=C_ACENTO,
                   spaceBefore=2, spaceAfter=4.5),
    ]


def _fila_cargo_fechas(cargo, fechas, st, ancho_util):
    """Cargo a la izquierda y fechas a la derecha, en la misma línea."""
    ancho_fechas = 42 * mm
    tabla = Table(
        [[Paragraph(_rt(cargo), st["cargo"]), Paragraph(_rt(fechas), st["fechas"])]],
        colWidths=[ancho_util - ancho_fechas, ancho_fechas],
    )
    tabla.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))
    return tabla


def crear_pdf_cv(cv, ruta_salida):
    """Recibe el diccionario del CV y escribe un PDF maquetado en una hoja."""
    st = _estilos()
    doc = SimpleDocTemplate(
        ruta_salida, pagesize=PAGINA,
        leftMargin=MARGEN, rightMargin=MARGEN,
        topMargin=13 * mm, bottomMargin=13 * mm,
        title=f"CV - {cv.get('nombre', '')}", author=cv.get("nombre", ""),
    )
    ancho_util = doc.width
    T = TITULOS[_idioma_cv(cv)]
    fl = []

    # --- Encabezado ---
    fl.append(Paragraph(_rt(cv.get("nombre") or DATOS_FIJOS["nombre"]), st["nombre"]))
    if cv.get("titular"):
        fl.append(Paragraph(_rt(cv["titular"]), st["titular"]))
    fl.append(Paragraph(_rt(cv.get("contacto") or DATOS_FIJOS["contacto"]), st["contacto"]))
    fl.append(Spacer(1, 4))
    fl.append(HRFlowable(width="100%", thickness=1.4, color=C_PRIMARIO, spaceAfter=2))

    # --- Resumen ---
    if cv.get("resumen"):
        fl += _titulo_seccion(T["resumen"], st)
        fl.append(Paragraph(_rt(cv["resumen"]), st["cuerpo"]))

    # --- Competencias, en línea y separadas por · ---
    comps = cv.get("competencias") or []
    if comps:
        fl += _titulo_seccion(T["comp"], st)
        fl.append(Paragraph(_rt(" · ".join(str(c).strip() for c in comps)),
                            st["cuerpo"]))

    # --- Experiencia ---
    exps = cv.get("experiencia") or []
    if exps:
        fl += _titulo_seccion(T["exp"], st)
        for i, exp in enumerate(exps):
            partes = [_fila_cargo_fechas(exp.get("cargo", ""), exp.get("fechas", ""),
                                         st, ancho_util)]
            linea_emp = " · ".join(x for x in [exp.get("empresa"), exp.get("ubicacion")] if x)
            if linea_emp:
                partes.append(Paragraph(_rt(linea_emp), st["empresa"]))
            for lg in exp.get("logros") or []:
                partes.append(Paragraph(_rt(lg), st["bullet"], bulletText="•"))
            if i < len(exps) - 1:
                partes.append(Spacer(1, 5))
            # Un cargo nunca se parte entre dos hojas, pero los cargos
            # sí pueden repartirse entre páginas si no caben todos.
            fl.append(KeepTogether(partes))

    # --- Educación, certificaciones e idiomas ---
    edu = cv.get("educacion") or []
    if edu:
        fl += _titulo_seccion(cv.get("titulo_educacion") or T["edu"], st)
        for item in edu:
            fl.append(Paragraph(_rt(item), st["bullet"], bulletText="•"))

    # --- Secciones adicionales opcionales ---
    for extra in cv.get("secciones_extra") or []:
        items = extra.get("items") or []
        if not items:
            continue
        fl += _titulo_seccion(extra.get("titulo") or T["extra"], st)
        for item in items:
            fl.append(Paragraph(_rt(item), st["bullet"], bulletText="•"))

    doc.build(fl)
    return ruta_salida


def _slug(texto, largo=40):
    txt = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    txt = re.sub(r"[^A-Za-z0-9]+", "_", txt).strip("_")
    return (txt[:largo] or "Oferta")


def _prefijo_cv():
    """Prefijo de los PDF a partir del nombre: 'ANA PÉREZ' -> 'CV_AnaPerez'; sin nombre, 'CV'."""
    txt = unicodedata.normalize("NFKD", DATOS_FIJOS.get("nombre") or "").encode("ascii", "ignore").decode()
    nombre = "".join(p.capitalize() for p in re.split(r"[^A-Za-z0-9]+", txt) if p)
    return f"CV_{nombre}" if nombre else "CV"


# ===================== HISTORIAL (evita duplicados) =====================
def _normaliza(t):
    return re.sub(r"[^a-z0-9]+", "",
                  unicodedata.normalize("NFKD", str(t or ""))
                  .encode("ascii", "ignore").decode().lower())


def _claves_oferta(empresa, cargo, url=""):
    """Identificadores de una vacante: por enlace y por empresa+puesto.
    Se usan los dos, porque una misma vacante puede llegar por caminos
    distintos (portal con enlace, correo sin enlace, fila hecha a mano)."""
    claves = [f"ep:{_normaliza(empresa)}|{_normaliza(cargo)}"]
    if url and url.startswith("http"):
        claves.append("url:" + url.split("?")[0].rstrip("/").lower())
    return claves


def cargar_historial():
    """Devuelve {clave: fecha} de las ofertas ya procesadas, sin las viejas."""
    if not os.path.exists(RUTA_HISTORIAL):
        return {}
    try:
        with open(RUTA_HISTORIAL, encoding="utf-8") as f:
            datos = json.load(f)
    except Exception:
        return {}

    limite = datetime.date.today() - datetime.timedelta(days=DIAS_HISTORIAL)
    vigentes = {}
    for clave, fecha in datos.items():
        try:
            if datetime.date.fromisoformat(fecha) >= limite:
                vigentes[clave] = fecha
        except Exception:
            continue
    return vigentes


def guardar_historial(historial):
    try:
        with open(RUTA_HISTORIAL, "w", encoding="utf-8") as f:
            json.dump(historial, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"  No se pudo guardar el historial: {e}")


HISTORIAL = cargar_historial()


def ya_vista(empresa, cargo, url=""):
    return any(c in HISTORIAL for c in _claves_oferta(empresa, cargo, url))


def marcar_vista(empresa, cargo, url=""):
    hoy = datetime.date.today().isoformat()
    for c in _claves_oferta(empresa, cargo, url):
        HISTORIAL[c] = hoy


def cargar_postulaciones_airtable():
    """Lee TODAS las filas del tracker en una sola pasada paginada.
    - Suma empresa+puesto al HISTORIAL (así no se vuelven a sugerir vacantes
      que ya están registradas, a mano o por el bot).
    - Devuelve la lista de postulaciones con los campos que usa el seguimiento.
    Si algo falla, devuelve lo que alcanzó a leer (posiblemente [])."""
    postulaciones = []
    if not AIRTABLE_TOKEN:
        return postulaciones
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{AIRTABLE_TABLA}"
    cabeceras = {"Authorization": f"Bearer {AIRTABLE_TOKEN}"}
    campos = [CAMPO_EMPRESA, CAMPO_PUESTO, CAMPO_ESTADO, CAMPO_LOG, CAMPO_FECHA_POST,
              CAMPO_FECHA_ENT1, CAMPO_FECHA_ENT2, CAMPO_PROX_ACCION]
    hoy = datetime.date.today().isoformat()
    offset = None
    try:
        while True:
            params = {"pageSize": 100, "fields[]": campos, "returnFieldsByFieldId": "true"}
            if offset:
                params["offset"] = offset
            r = requests.get(url, headers=cabeceras, params=params, timeout=30)
            if r.status_code != 200:
                print(f"  (no se pudo leer el tracker: {r.status_code} {r.text[:200]})")
                break
            datos = r.json()
            for reg in datos.get("records", []):
                f = reg.get("fields", {})
                empresa = str(f.get(CAMPO_EMPRESA) or "").strip()
                puesto = str(f.get(CAMPO_PUESTO) or "").strip()
                if empresa and puesto:
                    HISTORIAL.setdefault(f"ep:{_normaliza(empresa)}|{_normaliza(puesto)}", hoy)
                postulaciones.append({
                    "id": reg.get("id"),
                    "empresa": empresa,
                    "puesto": puesto,
                    "estado": str(f.get(CAMPO_ESTADO) or "").strip(),
                    "feedback": str(f.get(CAMPO_LOG) or ""),
                    "fecha_post": f.get(CAMPO_FECHA_POST) or "",
                    "f1": f.get(CAMPO_FECHA_ENT1) or "",
                    "f2": f.get(CAMPO_FECHA_ENT2) or "",
                    "prox_accion": str(f.get(CAMPO_PROX_ACCION) or "").strip(),
                })
            offset = datos.get("offset")
            if not offset:
                break
            time.sleep(0.25)   # límite de Airtable: 5 peticiones/segundo
    except Exception as e:
        print(f"  (no se pudo leer el tracker: {e})")
    return postulaciones


def precargar_historial_desde_airtable():
    """Compatibilidad con el nombre antiguo: carga el historial y devuelve cuántas filas leyó."""
    return len(cargar_postulaciones_airtable())


# ===================== LECTURA DE OFERTAS =====================
def _limpiar_html(bruto):
    """Convierte el HTML de un correo en texto legible, conservando los enlaces."""
    txt = re.sub(r"(?is)<(script|style).*?</\1>", " ", bruto)
    txt = re.sub(r"(?i)<a [^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
                 r"\2 (\1)", txt, flags=re.S)
    txt = re.sub(r"(?i)<(br|/p|/div|/tr|/h\d)[^>]*>", "\n", txt)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = html.unescape(txt)
    txt = re.sub(r"[ \t]{2,}", " ", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


def _cuerpo_del_correo(msg):
    """Devuelve el texto del correo. Si no hay versión de texto plano,
    usa la versión HTML limpiada (LinkedIn y GetOnBoard solo mandan HTML)."""
    plano, htm = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                continue
            tipo = part.get_content_type()
            try:
                contenido = part.get_payload(decode=True)
            except Exception:
                continue
            if not contenido:
                continue
            contenido = contenido.decode(part.get_content_charset() or "utf-8",
                                         errors="ignore")
            if tipo == "text/plain":
                plano += contenido + "\n"
            elif tipo == "text/html":
                htm += contenido + "\n"
    else:
        contenido = (msg.get_payload(decode=True) or b"").decode(
            msg.get_content_charset() or "utf-8", errors="ignore")
        if msg.get_content_type() == "text/html":
            htm = contenido
        else:
            plano = contenido

    if len(plano.strip()) > 200:
        return plano.strip()
    return _limpiar_html(htm) if htm.strip() else plano.strip()


_MESES_IMAP = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _fecha_imap(d):
    """Fecha en el formato que exige IMAP (14-Sep-2026) sin depender del locale
    (strftime('%b') cambia si el proceso corre con LC_TIME en español)."""
    return f"{d.day:02d}-{_MESES_IMAP[d.month - 1]}-{d.year}"


def _conectar_imap(carpeta="inbox", solo_lectura=False):
    """Abre la sesión IMAP de Gmail y selecciona la carpeta (None = no seleccionar).
    Quien la usa cierra con mail.logout(). Con solo_lectura=True el servidor no
    marca nada como leído aunque se baje el mensaje completo.
    Reintenta la conexión y el login: tras despertar, la red tarda en volver."""
    ultimo_error = None
    for intento in range(1, REINTENTOS_IMAP + 1):
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com", timeout=TIMEOUT_IMAP)
            mail.login(EMAIL_USUARIO, EMAIL_PASSWORD)
            if carpeta:
                mail.select(carpeta, readonly=solo_lectura)
            return mail
        except (OSError, imaplib.IMAP4.error) as e:
            ultimo_error = e
            if intento == REINTENTOS_IMAP:
                break
            print(f"  Gmail no respondió ({type(e).__name__}: {str(e)[:80]}; intento "
                  f"{intento}/{REINTENTOS_IMAP}). Reintentando en {ESPERA_IMAP}s...")
            time.sleep(ESPERA_IMAP)
    raise RuntimeError(f"No se pudo conectar a Gmail tras {REINTENTOS_IMAP} intentos: "
                       f"{type(ultimo_error).__name__}: {ultimo_error}")


def _decodificar_cabecera(valor):
    """Texto legible de una cabecera RFC 2047 (asunto, remitente) uniendo TODOS
    los fragmentos codificados. La versión anterior tomaba solo el primero y
    truncaba los asuntos largos."""
    if not valor:
        return ""
    valor = str(valor)
    try:
        texto = str(make_header(decode_header(valor)))
    except Exception:
        # Charset desconocido o cabecera mal formada: se decodifica a mano.
        try:
            partes = []
            for trozo, cod in decode_header(valor):
                if isinstance(trozo, bytes):
                    try:
                        trozo = trozo.decode(cod or "utf-8", errors="ignore")
                    except LookupError:
                        trozo = trozo.decode("utf-8", errors="ignore")
                partes.append(trozo)
            texto = "".join(partes)
        except Exception:
            texto = valor
    return re.sub(r"\s+", " ", texto).strip()


def _decodificar_asunto(msg):
    return _decodificar_cabecera(msg.get("Subject", ""))


def leer_correos_hoy():
    print("Conectando a Gmail para buscar ofertas de empleo...")
    mail = _conectar_imap("inbox")

    DIAS_ATRAS = int(os.environ.get("DIAS_ATRAS", "3"))
    desde = _fecha_imap(datetime.date.today() - datetime.timedelta(days=DIAS_ATRAS))

    # Se hacen varias búsquedas separadas y se unen los resultados:
    # es más confiable que una sola consulta con muchos OR encadenados.
    criterios = [
        f'(SINCE {desde} SUBJECT "job")',
        f'(SINCE {desde} SUBJECT "empleo")',
        f'(SINCE {desde} SUBJECT "vacante")',
        f'(SINCE {desde} SUBJECT "hiring")',
        f'(SINCE {desde} SUBJECT "opportunity")',
        f'(SINCE {desde} SUBJECT "alerta")',
        f'(SINCE {desde} FROM "linkedin")',
        f'(SINCE {desde} FROM "getonbrd")',
        f'(SINCE {desde} FROM "weworkremotely")',
        f'(SINCE {desde} FROM "wellfound")',
        f'(SINCE {desde} FROM "remotive")',
        f'(SINCE {desde} FROM "indeed")',
    ]
    ids = []
    for criterio in criterios:
        try:
            estado, resultado = mail.search(None, criterio)
            if estado == "OK" and resultado and resultado[0]:
                ids.extend(resultado[0].split())
        except Exception as e:
            print(f"  (búsqueda {criterio} falló: {e})")

    # Quitar repetidos conservando el orden
    vistos, unicos = set(), []
    for i in ids:
        if i not in vistos:
            vistos.add(i)
            unicos.append(i)
    mensajes = [b" ".join(unicos)] if unicos else [b""]
    print(f"  Correos que coinciden: {len(unicos)}")
    status = "OK"

    texto_correos = ""
    if mensajes and mensajes[0]:
        for num in mensajes[0].split():
            status, data = mail.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(data[0][1])
            asunto = _decodificar_asunto(msg)

            remitente = str(msg.get("From", "")).lower()
            asunto_lower = (asunto or "").lower()
            if "job recap" in asunto_lower or "upwork" in remitente or "upwork" in asunto_lower:
                continue

            texto_correos += f"\n--- CORREO: {asunto} (De: {msg.get('From', '')}) ---\n"
            texto_correos += _cuerpo_del_correo(msg) + "\n"

    mail.logout()
    return texto_correos


# Términos de búsqueda usados en los portales. Edítalos si cambia el enfoque.
BUSQUEDAS = [
    "marketing analytics", "data analyst", "marketing automation",
    "growth", "business intelligence", "marketing operations",
]


def _linea_oferta(titulo, empresa, ubicacion, sueldo, url, descripcion=""):
    """Compacta una vacante en pocas líneas legibles para el modelo.
    Devuelve cadena vacía si esa vacante ya se envió en días anteriores."""
    if ya_vista(empresa, titulo, url):
        return ""
    desc = re.sub(r"<[^>]+>", " ", descripcion or "")
    desc = re.sub(r"\s+", " ", html.unescape(desc)).strip()[:600]
    return (f"\nPUESTO: {titulo}\n"
            f"EMPRESA: {empresa or 'No indicada'}\n"
            f"UBICACION: {ubicacion or 'No indicada'}\n"
            f"SUELDO: {sueldo or 'No indicado'}\n"
            f"ENLACE: {url}\n"
            f"DESCRIPCION: {desc}\n")


# Identificadores fijos de Get on Board. No hay endpoint público para resolver
# ids de ciudad/país a nombres, así que solo se usan los dos que importan.
GOB_TENANT_CHILE = 1      # location_tenants: países cuyos residentes pueden postular
GOB_CIUDAD_SANTIAGO = 1   # location_cities


def _ids_gob(campo):
    """Extrae los ids de un campo relacional de Get on Board
    ({'data': [{'id': 1, 'type': ...}, ...]}). Tolera lista o dict."""
    datos = campo.get("data", []) if isinstance(campo, dict) else (campo or [])
    ids = set()
    for d in datos:
        try:
            ids.add(int(d.get("id")))
        except (AttributeError, TypeError, ValueError):
            continue
    return ids


def _ubicacion_getonboard(a, slug=""):
    """Traduce los campos de ubicación de Get on Board a un texto explícito en
    español, para que el modelo no tenga que adivinar el alcance geográfico."""
    modalidad = str(a.get("remote_modality") or "").lower()
    paises = [str(p) for p in (a.get("countries") or [])
              if p and str(p).lower() != "remote"]
    tenants = _ids_gob(a.get("location_tenants"))
    ciudades = _ids_gob(a.get("location_cities"))
    slug = (slug or "").lower()
    en_chile = "chile" in " ".join(paises).lower()
    en_santiago = GOB_CIUDAD_SANTIAGO in ciudades or "santiago" in slug
    lugar = ", ".join(paises) or "ciudad no indicada"

    if modalidad == "fully_remote":
        return "Remoto global (cualquier país)"
    if modalidad == "remote_local":
        if GOB_TENANT_CHILE in tenants:
            extra = " y otros países" if len(tenants) > 1 else ""
            return f"Remoto, solo para residentes en Chile{extra}"
        return "Remoto restringido a otro país (NO admite postulantes desde Chile)"
    if modalidad in ("hybrid", "temporarily_remote"):
        if en_chile and en_santiago:
            return "Híbrido - Santiago, Chile"
        return f"Híbrido - {lugar}"
    if modalidad == "no_remote":
        return f"Presencial - {lugar}"
    if a.get("remote"):
        return "Remoto (alcance no indicado)"
    return ", ".join(paises)


def fuente_getonboard():
    """Get on Board: portal de empleos tech de LATAM. API pública, sin clave."""
    salida, vistos = "", set()
    for termino in BUSQUEDAS:
        try:
            r = requests.get(
                "https://www.getonbrd.com/api/v0/search/jobs",
                params={"query": termino, "per_page": 30, "expand[]": "company"},
                headers={"Accept": "application/json"}, timeout=30,
            )
            if r.status_code != 200:
                continue
            for item in r.json().get("data", []):
                a = item.get("attributes", {})
                if item.get("id") in vistos:
                    continue
                vistos.add(item.get("id"))
                sueldo = ""
                if a.get("min_salary") and a.get("max_salary"):
                    sueldo = f"USD {a['min_salary']} - {a['max_salary']}"
                # a["url"] apunta a la categoría, no a la vacante:
                # el enlace real se arma con el identificador del puesto.
                slug = str(item.get("id", ""))
                enlace = f"https://www.getonbrd.com/jobs/{slug}"
                salida += _linea_oferta(
                    a.get("title", ""),
                    (a.get("company", {}).get("data", {}).get("attributes", {}) or {}).get("name", ""),
                    _ubicacion_getonboard(a, slug),
                    sueldo, enlace, a.get("description", ""),
                )
        except Exception as e:
            print(f"  (Get on Board falló con '{termino}': {e})")
    return salida


def fuente_remotive():
    """Remotive: empleos 100% remotos internacionales. API pública, sin clave."""
    salida = ""
    for categoria in ["marketing", "data", "business"]:
        try:
            r = requests.get("https://remotive.com/api/remote-jobs",
                             params={"category": categoria, "limit": 40}, timeout=30)
            if r.status_code != 200:
                continue
            for j in r.json().get("jobs", []):
                salida += _linea_oferta(
                    j.get("title", ""), j.get("company_name", ""),
                    f"Remoto - {j.get('candidate_required_location', '')}",
                    j.get("salary", ""), j.get("url", ""), j.get("description", ""),
                )
        except Exception as e:
            print(f"  (Remotive falló en '{categoria}': {e})")
    return salida


def fuente_weworkremotely():
    """We Work Remotely: feed RSS de las categorías relevantes."""
    salida = ""
    feeds = [
        "https://weworkremotely.com/categories/remote-marketing-jobs.rss",
        "https://weworkremotely.com/categories/remote-data-jobs.rss",
        "https://weworkremotely.com/categories/remote-business-exec-management-jobs.rss",
    ]
    for feed in feeds:
        try:
            r = requests.get(feed, timeout=30,
                             headers={"User-Agent": "Mozilla/5.0 (buscador personal)"})
            if r.status_code != 200:
                continue
            for item in re.findall(r"<item>(.*?)</item>", r.text, flags=re.S):
                def campo(nombre):
                    m = re.search(rf"<{nombre}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{nombre}>",
                                  item, flags=re.S)
                    return html.unescape(m.group(1)).strip() if m else ""
                titulo = campo("title")
                if not titulo:
                    continue
                salida += _linea_oferta(titulo, "", campo("region"), "",
                                        campo("link"), campo("description"))
        except Exception as e:
            print(f"  (We Work Remotely falló en {feed.split('/')[-1]}: {e})")
    return salida


FUENTES = [
    ("Get on Board", fuente_getonboard),
    ("Remotive", fuente_remotive),
    ("We Work Remotely", fuente_weworkremotely),
]


def leer_fuentes_publicas():
    """Consulta los portales de empleo y devuelve las vacantes ya compactadas."""
    print("Consultando portales de empleo...")
    print(f"  (historial: {len(HISTORIAL)} ofertas ya vistas se van a omitir)")
    total = ""
    for nombre, funcion in FUENTES:
        texto = funcion()
        n = texto.count("PUESTO:")
        print(f"  {nombre}: {n} vacantes nuevas")
        if texto:
            total += f"\n--- OFERTAS DESDE {nombre.upper()} ---\n{texto}"
    return total


def leer_repertorio_web():
    """Se mantiene el nombre anterior para no romper nada que lo llame."""
    return leer_fuentes_publicas()


# ===================== ANÁLISIS CON LLM (DEEPINFRA) =====================
ESQUEMA = """
{
  "ofertas": [
    {
      "cargo": "string",
      "empresa": "string",
      "ubicacion": "string",
      "modalidad": "remoto|hibrido|presencial|desconocida",
      "zona": "global|latam|chile|otra",
      "sueldo": "string",
      "enlace": "string",
      "match": 0,
      "pros": ["string"],
      "contras": ["string"],
      "cv": {
        "idioma": "es|en",
        "titular": "string",
        "resumen": "string",
        "competencias": ["string"],
        "experiencia": [
          {"cargo":"string","empresa":"string","ubicacion":"string",
           "fechas":"string","logros":["string"]}
        ],
        "educacion": ["string"]
      }
    }
  ]
}
"""


def analizar_con_llm(cv_texto, ofertas_texto):
    print("Analizando ofertas y redactando CVs adaptados...")
    if not DEEPINFRA_API_KEY:
        raise RuntimeError("Falta DEEPINFRA_API_KEY en el archivo .env")

    prompt = f"""Analiza las ofertas laborales (pueden estar en español, inglés o ambos)
y selecciona las mejores para esta candidata.

CRITERIOS OBLIGATORIOS
1. UBICACIÓN (filtro excluyente). La candidata vive en Santiago de Chile.
   Solo se aceptan dos tipos de oferta:
   a) REMOTA con alcance global (worldwide / anywhere), abierta a Sudamérica
      o Latinoamérica (LATAM, South America, Americas) o dirigida a Chile.
   b) HÍBRIDA con oficina en Santiago de Chile (Región Metropolitana).
   EXCLUYE todo lo demás y NO lo incluyas en la lista aunque el match sea alto:
   remotas restringidas a otros países o regiones (solo USA, solo Europa, solo
   Argentina, solo México, "US timezones only", etc.), híbridas o presenciales
   fuera de Santiago, y ofertas cuya ubicación no se pueda determinar.
   Los {MAX_OFERTAS} cupos son para ofertas válidas.
2. Clasifica cada oferta elegida con dos campos, en minúsculas y sin acentos:
   - "modalidad": "remoto", "hibrido", "presencial" o "desconocida".
   - "zona": "global" (cualquier país), "latam" (Latinoamérica, Sudamérica o
     Américas, incluye Chile), "chile" (solo Chile o Santiago) u "otra"
     (restringida a un país o región que excluye a Chile, o sin información).
   Si la descripción exige residir o tener permiso de trabajo en un país que no
   es Chile, la zona es "otra" aunque el título diga "remote".
   Si UBICACION dice "No indicada", sé conservadora y deduce del enlace y la
   descripción: enlace que termina en "-santiago" => hibrido/chile; en
   "-ciudad-de-mexico", "-lima", "-buenos-aires", "-bogota" o similar => otra;
   en "-remote" sin más datos => zona "otra", salvo que la descripción diga
   explícitamente global, LATAM o Chile. Escribe en "ubicacion" lo que
   dedujiste (por ejemplo "Híbrido - Santiago, Chile" o "Remoto - LATAM").
3. Fuera de Chile: sueldo mínimo US$3.000, SOLO si la oferta indica sueldo.
4. En Chile: sueldo mínimo CLP 2.000.000, SOLO si la oferta indica sueldo.
5. Si la oferta NO indica sueldo, NO la descartes por eso: ponle
   "No indicado" y bájale algunos puntos de match.
6. Prefiere puestos sin ejecución directa de paid media, pero no los excluyas.
7. EXCLUYE ofertas de Upwork.
8. Máximo {MAX_OFERTAS} ofertas, ordenadas de mayor a menor match.
9. Si el material contiene menos de {MAX_OFERTAS} vacantes válidas, devuelve las
   que haya aunque sean pocas. Devuelve la lista vacía solo si no hay NINGUNA
   vacante válida en el texto.

CV GENERAL DE LA CANDIDATA
{cv_texto}

OFERTAS DISPONIBLES
{ofertas_texto}

FORMATO DE SALIDA
Devuelve ÚNICAMENTE un objeto JSON válido con esta forma exacta:
{ESQUEMA}

REGLAS DEL CONTENIDO
- IDIOMA (regla prioritaria). Cada CV se redacta COMPLETO en el idioma de su oferta:
  si la oferta está escrita en inglés, TODO el "cv" va en inglés (titular, resumen,
  competencias, cargo, ubicacion, fechas, logros y educacion); si está en español,
  todo en español. Nunca mezcles idiomas dentro de un mismo CV. Decide el idioma por
  el texto de la DESCRIPCION de la oferta (no por el título ni por el nombre de la
  empresa); si la oferta es bilingüe, usa el idioma en que está la mayor parte de la
  descripción. Declara la decisión en "cv.idioma" con "es" o "en". Los nombres propios
  (empresas, herramientas, títulos oficiales) se mantienen tal cual. "pros" y
  "contras" van siempre en español, porque son para la candidata.
- Texto plano, sin HTML ni Markdown, salvo **negrita** para destacar 1 o 2 términos por viñeta.
- "titular": una línea, estilo titular de LinkedIn, adaptada al cargo de la vacante.
- "resumen": 4 a 6 líneas, reordenando la experiencia real según lo que pide la vacante.
- "competencias": entre 10 y 14 frases cortas (2 a 5 palabras cada una), sin punto final.
- "experiencia": todos los cargos relevantes del CV general, del más reciente al más antiguo.
  "fechas" en el idioma del CV: "Ene 2022 – Dic 2024" si es español,
  "Jan 2022 – Dec 2024" si es inglés, y "Presente" / "Present" según corresponda. 2 o 3 "logros" por cargo, cada uno de 1 a 2 líneas.
- "educacion": 3 a 5 líneas con título, certificaciones e idiomas.
- NO inventes empresas, cargos, fechas, cifras ni certificaciones: usa solo lo que aparece
  en el CV general, cambiando el énfasis y la redacción, no los hechos.
"""

    mensajes = [
        {"role": "system",
         "content": ("Eres un headhunter y redactor de CVs. Devuelves "
                     "exclusivamente JSON válido, sin texto adicional.")},
        {"role": "user", "content": prompt},
    ]
    return _llamar_llm(mensajes)


def _llamar_llm(mensajes, temperature=0.4, max_tokens=MAX_TOKENS_SALIDA):
    """Llama al modelo (DeepInfra, API compatible con OpenAI) pidiendo un objeto
    JSON y lo devuelve ya parseado. Reintenta ante errores transitorios; en los
    dos últimos intentos usa MODELO_RESPALDO."""
    if not DEEPINFRA_API_KEY:
        raise RuntimeError("Falta DEEPINFRA_API_KEY en el archivo .env")
    url = f"{DEEPINFRA_BASE_URL}/chat/completions"
    cabeceras = {
        "Authorization": f"Bearer {DEEPINFRA_API_KEY}",
        "Content-Type": "application/json",
    }
    estados_recuperables = {429, 500, 502, 503, 504}

    ultimo_error = None
    for intento in range(1, REINTENTOS + 1):
        # En los dos últimos intentos se prueba con el modelo de respaldo.
        modelo = MODELO_LLM if intento <= REINTENTOS - 2 else MODELO_RESPALDO
        cuerpo = {
            "model": modelo,
            "messages": mensajes,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        espera = min(60, 10 * (2 ** (intento - 1)))  # 10s, 20s, 40s, 60s

        try:
            r = requests.post(url, headers=cabeceras, json=cuerpo, timeout=TIMEOUT_LLM)
        except (requests.ConnectionError, requests.Timeout,
                requests.exceptions.ChunkedEncodingError) as e:
            ultimo_error = e
            motivo = type(e).__name__
        else:
            if r.status_code == 200:
                datos = r.json()
                eleccion = (datos.get("choices") or [{}])[0]
                contenido = (eleccion.get("message") or {}).get("content") or ""
                if not contenido:
                    raise RuntimeError(f"Respuesta vacía del modelo {modelo}: "
                                       f"{json.dumps(eleccion)[:300]}")
                if eleccion.get("finish_reason") == "length":
                    print(f"  Aviso: la respuesta se cortó por max_tokens={max_tokens}; "
                          "el JSON podría venir incompleto.")
                uso = datos.get("usage") or {}
                print(f"  Modelo {modelo}: {uso.get('prompt_tokens', '?')} tokens de "
                      f"entrada, {uso.get('completion_tokens', '?')} de salida.")
                return _json_seguro(contenido)

            if r.status_code not in estados_recuperables:
                # 400/401/404...: reintentar no ayuda. Se corta con mensaje claro.
                raise RuntimeError(f"DeepInfra respondió HTTP {r.status_code} con el "
                                   f"modelo {modelo}: {r.text[:300]}")
            ultimo_error = RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            motivo = f"HTTP {r.status_code}"
            # Si el servidor indica cuánto esperar (429), se respeta con tope.
            try:
                espera = max(espera, min(120, int(r.headers.get("Retry-After", 0))))
            except ValueError:
                pass

        if intento == REINTENTOS:
            raise ultimo_error
        print(f"  Modelo no disponible ({motivo}; intento {intento}/{REINTENTOS}, "
              f"modelo {modelo}). Reintentando en {espera}s...")
        time.sleep(espera)

    raise ultimo_error


def _json_seguro(texto):
    """Parsea el JSON aunque venga con <think>, ```json o texto alrededor."""
    t = (texto or "").strip()
    t = re.sub(r"<think>.*?</think>", "", t, flags=re.S).strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        ini, fin = t.find("{"), t.rfind("}")
        if ini != -1 and fin != -1:
            return json.loads(t[ini:fin + 1])
        raise


# ===================== FILTRO GEOGRÁFICO =====================
# Regla de la candidata: solo remotos abiertos a Chile (global / LATAM / Chile)
# o híbridos con oficina en Santiago. Todo lo demás se descarta aunque el
# modelo lo haya elegido.
ZONAS_REMOTO_OK = {"global", "latam", "chile"}

# Sinónimos que el modelo suele devolver pese a la instrucción ("Híbrido",
# "Hybrid", "Remote", "Worldwide"...). Se comparan ya normalizados.
_SINONIMOS_ETIQUETA = {
    "remote": "remoto", "remota": "remoto", "fullyremote": "remoto",
    "hybrid": "hibrido", "hibrida": "hibrido",
    "onsite": "presencial", "insitu": "presencial",
    "worldwide": "global", "anywhere": "global", "mundial": "global",
    "remotoglobal": "global", "remoteglobal": "global",
    "latinoamerica": "latam", "latinamerica": "latam",
    "sudamerica": "latam", "southamerica": "latam", "americas": "latam",
    "santiago": "chile", "cl": "chile",
    "other": "otra", "otro": "otra", "unknown": "desconocida",
}


def _etiqueta_llm(o, campo):
    """Valor de clasificación del modelo en minúsculas, sin acentos ni signos."""
    v = _normaliza(o.get(campo))
    return _SINONIMOS_ETIQUETA.get(v, v)


def _oferta_permitida(o):
    """Devuelve (True, "") si la oferta cumple la regla geográfica, o
    (False, motivo) si hay que omitirla. Es pura: no toca red ni historial."""
    modalidad = _etiqueta_llm(o, "modalidad")
    zona = _etiqueta_llm(o, "zona")
    lugar = _normaliza(f"{o.get('ubicacion', '')} {o.get('enlace', '')}")
    detalle = f"{modalidad or '?'}/{zona or '?'}: {o.get('ubicacion') or 'No indicada'}"

    if modalidad == "remoto":
        if zona in ZONAS_REMOTO_OK:
            return True, ""
        return False, f"remoto restringido ({detalle})"
    if modalidad == "hibrido":
        if zona == "chile" and ("santiago" in lugar or "regionmetropolitana" in lugar):
            return True, ""
        return False, f"híbrido fuera de Santiago ({detalle})"
    if modalidad == "presencial":
        return False, f"presencial ({detalle})"
    return False, f"modalidad desconocida ({detalle})"


# ===================== AIRTABLE =====================
def _deducir_pais(texto):
    t = (texto or "").lower()
    if "chile" in t or "santiago" in t:
        return "Chile"
    if "guatemala" in t:
        return "Guatemala"
    for clave in ("united states", "usa", "ee.uu", "estados unidos", "u.s."):
        if clave in t:
            return "Estados Unidos"
    if "mexico" in t or "méxico" in t:
        return "México"
    if "spain" in t or "españa" in t:
        return "España"
    if "remot" in t or "worldwide" in t or "global" in t or "anywhere" in t:
        return "Remoto / Global"
    for clave in ("latam", "latin america", "latinoam", "sudam", "south america", "americas"):
        if clave in t:
            return "Remoto / Global"
    return "Otro"


def _deducir_modalidad(texto):
    t = (texto or "").lower()
    if "hibrid" in t or "híbrid" in t or "hybrid" in t:
        return "Híbrido"
    if "remot" in t or "anywhere" in t or "worldwide" in t:
        return "Remoto"
    if "presencial" in t or "on-site" in t or "onsite" in t:
        return "Presencial"
    return "Remoto"


def _deducir_fuente(url):
    u = (url or "").lower()
    if "getonbrd" in u or "getonboard" in u:
        return "Get on Board"
    if "remotive" in u:
        return "Remotive"
    if "weworkremotely" in u:
        return "We Work Remotely"
    if "linkedin" in u:
        return "LinkedIn"
    return "Otro"


def crear_registro_airtable(oferta, nombre_cv):
    """Crea la fila de la oferta en el tracker y devuelve su record id.
    Si algo falla, devuelve None y el correo sale igual, solo que sin botón."""
    if not AIRTABLE_TOKEN:
        print("  (sin AIRTABLE_TOKEN: no se registra en Airtable)")
        return None

    hoy = datetime.date.today().strftime("%d-%m-%Y")
    bitacora = (f"[{hoy}] Detectada por el buscador automático. "
                f"Match: {oferta.get('match', '-')}/100. "
                f"Sueldo: {oferta.get('sueldo', 'No indicado')}. "
                f"Enlace: {oferta.get('enlace', 'No indicado')}")

    ubicacion = str(oferta.get("ubicacion", ""))
    enlace = str(oferta.get("enlace", ""))
    # Si el modelo clasificó la oferta, se usa eso; si no, las heurísticas de texto.
    pais = {"chile": "Chile", "global": "Remoto / Global",
            "latam": "Remoto / Global"}.get(_etiqueta_llm(oferta, "zona"))
    modalidad = {"remoto": "Remoto", "hibrido": "Híbrido",
                 "presencial": "Presencial"}.get(_etiqueta_llm(oferta, "modalidad"))

    cuerpo = {
        "fields": {
            CAMPO_EMPRESA: str(oferta.get("empresa", "")).strip(),
            CAMPO_PUESTO: str(oferta.get("cargo", "")).strip(),
            CAMPO_ESTADO: ESTADO_INICIAL,
            CAMPO_CV: nombre_cv.replace(".pdf", ""),
            CAMPO_LOG: bitacora,
            CAMPO_PAIS: pais or _deducir_pais(f"{ubicacion} {enlace}"),
            CAMPO_MODALIDAD: modalidad or _deducir_modalidad(ubicacion),
            CAMPO_FUENTE: _deducir_fuente(enlace),
        },
        "typecast": True,
    }

    # El sueldo casi siempre viene como rango; se guarda tal cual y
    # solo si la vacante lo declara.
    sueldo = str(oferta.get("sueldo", "")).strip()
    if sueldo and sueldo.lower() not in ("no indicado", "no indicada", "n/a", "-"):
        cuerpo["fields"][CAMPO_SUELDO] = sueldo

    try:
        r = requests.post(
            f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{AIRTABLE_TABLA}",
            headers={"Authorization": f"Bearer {AIRTABLE_TOKEN}",
                     "Content-Type": "application/json"},
            json=cuerpo, timeout=30,
        )
        if r.status_code in (200, 201):
            return r.json().get("id")
        print(f"  Airtable respondió {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"  No se pudo registrar en Airtable: {e}")
    return None


def _url_ya_aplique(record_id):
    """Arma el enlace del botón 'Ya apliqué' que apunta al webhook."""
    if not record_id or not WEBHOOK_URL:
        return ""
    params = urllib.parse.urlencode({"rec": record_id, "t": WEBHOOK_TOKEN})
    separador = "&" if "?" in WEBHOOK_URL else "?"
    return f"{WEBHOOK_URL}{separador}{params}"


# ===================== NOVEDADES DE POSTULACIONES =====================
# Las empresas contestan por correo (rechazo, entrevista, prueba, oferta, acuse).
# Cada corrida busca esos correos, los asocia a una fila del tracker con el
# modelo, agrega una línea a la bitácora y, solo en casos claros, avanza Estado.

_ORDEN_NORMALIZADO = {_normaliza(k): v for k, v in ESTADOS_ORDEN.items()}
_CAMPO_A_CLAVE = {CAMPO_ESTADO: "estado", CAMPO_LOG: "feedback",
                  CAMPO_FECHA_POST: "fecha_post", CAMPO_FECHA_ENT1: "f1",
                  CAMPO_FECHA_ENT2: "f2", CAMPO_PROX_ACCION: "prox_accion"}


def _orden_estado(nombre):
    """Posición del estado en el embudo (ESTADOS_ORDEN); -1 si no se reconoce.
    Compara normalizado: '1a entrevista agendada' == '1ª entrevista agendada'."""
    return _ORDEN_NORMALIZADO.get(_normaliza(nombre), -1)


def _ascii_minusculas(t):
    """Minúsculas sin acentos CONSERVANDO espacios (a diferencia de _normaliza)."""
    return (unicodedata.normalize("NFKD", str(t or ""))
            .encode("ascii", "ignore").decode().lower())


# Asuntos de boletines y alertas: nunca son respuestas a una postulación.
_ASUNTOS_DIGEST = (
    "job recap", "alerta de empleo", "alertas de empleo", "job alert", "jobs for you",
    "nuevos empleos", "new jobs", "te pueden interesar", "recomendad", "recommended",
    "newsletter", "unsubscribe", "weekly digest", "resumen semanal", "top jobs",
    "empleos para ti", "oportunidades para ti", "ofertas de empleo", "similar jobs",
)
# Excepción: avisos de estado de MI postulación en portales (sí se leen).
_ASUNTOS_ESTADO_PORTAL = ("tu postulacion", "your application", "candidatura")
# Palabras típicas de correos de reclutamiento (se buscan en remitente y asunto).
_PALABRAS_RECLUTAMIENTO = (
    "postulaci", "candidatur", "application", "applied", "entrevista", "interview",
    "proceso de selecci", "selection process", "assessment", "evaluaci", "prueba",
    "test t", "oferta", "offer", "next steps", "proximos pasos", "lamentamos",
    "unfortunately", "regret", "thank you for applying", "gracias por postular",
    "recruit", "talent", "hiring team", "greenhouse", "lever.co", "workable",
    "teamtailor", "smartrecruiters", "ashby", "hireflix", "myinterview", "workday",
    "calendly",
)
# Primeras palabras de nombre de empresa demasiado genéricas para buscarlas solas.
_PALABRAS_GENERICAS_EMPRESA = {
    "grupo", "group", "labs", "chile", "latam", "digital", "consulting", "company",
    "global", "solutions", "services", "technologies", "software", "agency",
    "agencia", "partners", "studio", "media", "team", "talent", "remote",
}


def _patrones_empresas(postulaciones):
    """Cadenas normalizadas con las que se reconoce a cada empresa en remitente+asunto:
    el nombre completo (4+ caracteres) y su primera palabra (6+ letras y no genérica)."""
    patrones = set()
    for p in postulaciones:
        nombre = _ascii_minusculas(p.get("empresa", "")).strip()
        if not nombre:
            continue
        completo = _normaliza(nombre)
        if len(completo) >= 4:
            patrones.add(completo)
        primera = re.split(r"[^a-z0-9]+", nombre)[0]
        if len(primera) >= 6 and primera not in _PALABRAS_GENERICAS_EMPRESA:
            patrones.add(primera)
    return patrones


def _es_candidato_seguimiento(de, asunto, patrones):
    """Prefiltro barato (sin modelo) sobre remitente y asunto. Es generoso a
    propósito: el modelo decide después; aquí solo se descarta lo obvio."""
    de_txt = _ascii_minusculas(de)
    asunto_txt = _ascii_minusculas(asunto)
    if EMAIL_USUARIO and EMAIL_USUARIO.lower() in de_txt:   # correos enviados por ella (incluye los del bot)
        return False
    if "upwork" in de_txt or "upwork" in asunto_txt:
        return False
    if any(d in asunto_txt for d in _ASUNTOS_DIGEST) and \
            not any(e in asunto_txt for e in _ASUNTOS_ESTADO_PORTAL):
        return False
    compacto = _normaliza(de_txt + " " + asunto_txt)
    if any(p in compacto for p in patrones):
        return True
    texto = de_txt + " " + asunto_txt
    return any(k in texto for k in _PALABRAS_RECLUTAMIENTO)


def _limpiar_message_id(valor):
    return re.sub(r"[<>\s]", "", str(valor or ""))


def _clave_correo(message_id, de, asunto, fecha_bruta):
    """Clave del HISTORIAL para no reprocesar un correo. Sin Message-ID (raro)
    se usa un hash de remitente+asunto+fecha."""
    if message_id:
        return "correo:" + message_id
    resumen = hashlib.sha1(f"{de}|{asunto}|{fecha_bruta}".encode("utf-8", "ignore")).hexdigest()
    return "correo:sha1:" + resumen[:24]


def _fecha_de_cabecera(valor):
    """'Date:' del correo -> 'YYYY-MM-DD' en hora local; hoy si no se puede leer."""
    try:
        dt = email.utils.parsedate_to_datetime(str(valor))
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.date().isoformat()
    except Exception:
        return datetime.date.today().isoformat()


def _remitente_corto(de):
    """'Ana Pérez <ana@acme.com>' -> 'Ana Pérez'; 'noreply@acme.com' -> 'acme.com'."""
    nombre, direccion = email.utils.parseaddr(str(de or ""))
    if nombre:
        return nombre.strip()[:40]
    if "@" in direccion:
        return direccion.split("@", 1)[1][:40]
    return (direccion or str(de or ""))[:40]


def _partes_fetch(data):
    """Recorre la respuesta de FETCH de imaplib, que mezcla tuplas
    (b'12 (BODY[...] {345}', b'<bytes del mensaje>') con separadores b')'.
    Devuelve (número de secuencia, bytes) por mensaje."""
    for item in data or []:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            m = re.match(rb"\s*(\d+)", item[0] or b"")
            yield (m.group(1) if m else b""), bytes(item[1])


def _resumen_calendario(msg):
    """Si el correo trae una invitación (text/calendar) devuelve sus datos clave:
    las invitaciones a entrevista suelen venir con el cuerpo vacío y todo en el .ics."""
    lineas = []
    for part in msg.walk():
        if part.get_content_type() != "text/calendar":
            continue
        try:
            ics = (part.get_payload(decode=True) or b"").decode(
                part.get_content_charset() or "utf-8", errors="ignore")
        except Exception:
            continue
        ics = re.sub(r"\r?\n[ \t]", "", ics)   # une las líneas plegadas de iCalendar
        for clave in ("SUMMARY", "DTSTART", "DTEND", "LOCATION", "ORGANIZER", "DESCRIPTION"):
            m = re.search(rf"(?m)^{clave}[^:]*:(.+)$", ics)
            if m:
                lineas.append(f"{clave}: {m.group(1).strip()[:200]}")
        if lineas:
            break
    return ("[Invitación de calendario] " + " | ".join(lineas)) if lineas else ""


def _seleccionar_carpeta_seguimiento(mail):
    """Selecciona la carpeta configurada en modo solo lectura. 'todos' busca la
    carpeta con atributo \\All ('[Gmail]/All Mail', '[Gmail]/Todos', ...)."""
    nombre = CARPETA_IMAP_SEGUIMIENTO
    if nombre.lower() in ("todos", "all", "allmail"):
        nombre = "inbox"
        _, carpetas = mail.list()
        for linea in carpetas or []:
            if isinstance(linea, bytes) and b"\\All" in linea:
                nombre = linea.decode("utf-8", "ignore").rsplit(' "/" ', 1)[-1].strip()
                break
    try:
        estado, _ = mail.select(nombre, readonly=True)
    except imaplib.IMAP4.error:
        estado = "NO"
    if estado != "OK":
        print(f"  (carpeta {nombre} no disponible; se usa inbox)")
        mail.select("inbox", readonly=True)


def leer_correos_seguimiento(postulaciones):
    """Busca en Gmail correos recientes que puedan ser respuestas a postulaciones.
    1) Trae solo cabeceras (sin marcar como leído) de todo lo recibido en la ventana.
    2) Descarta lo ya procesado, lo enviado por ella, Upwork y boletines.
    3) Baja el cuerpo solo de los candidatos (los MAX_CORREOS_SEGUIMIENTO más nuevos).
    Devuelve dicts {clave, message_id, fecha, de, asunto, extracto}, del más nuevo al más viejo."""
    patrones = _patrones_empresas(postulaciones)
    if not patrones:
        return []
    desde = _fecha_imap(datetime.date.today()
                        - datetime.timedelta(days=DIAS_ATRAS_SEGUIMIENTO))
    print(f"Buscando novedades de postulaciones en Gmail (desde {desde})...")
    mail = _conectar_imap(carpeta=None)
    correos = []
    try:
        _seleccionar_carpeta_seguimiento(mail)
        estado, resultado = mail.search(None, f"(SINCE {desde})")
        ids = resultado[0].split() if estado == "OK" and resultado and resultado[0] else []
        ids.reverse()                                   # más nuevos primero
        print(f"  Correos en la ventana: {len(ids)}")

        candidatos = []
        for i in range(0, len(ids), 50):                # cabeceras en lotes de 50
            lote = ",".join(x.decode() for x in ids[i:i + 50])
            estado, data = mail.fetch(
                lote, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])")
            if estado != "OK":
                continue
            for num, cab in _partes_fetch(data):
                msg = email.message_from_bytes(cab)
                de = _decodificar_cabecera(msg.get("From", ""))
                asunto = _decodificar_asunto(msg)
                mid = _limpiar_message_id(msg.get("Message-ID"))
                clave = _clave_correo(mid, de, asunto, msg.get("Date", ""))
                if clave in HISTORIAL:
                    continue
                if not _es_candidato_seguimiento(de, asunto, patrones):
                    continue
                candidatos.append({"num": num, "clave": clave, "message_id": mid,
                                   "fecha": _fecha_de_cabecera(msg.get("Date")),
                                   "de": de, "asunto": asunto})
            if len(candidatos) >= MAX_CORREOS_SEGUIMIENTO:
                break
        # El servidor responde cada lote en orden ascendente: se reordena.
        candidatos.sort(key=lambda c: int(c["num"] or 0), reverse=True)
        candidatos = candidatos[:MAX_CORREOS_SEGUIMIENTO]

        for c in candidatos:
            try:
                estado, data = mail.fetch(c["num"], "(BODY.PEEK[])")
                bruto = next((b for _, b in _partes_fetch(data)), None)
                if estado != "OK" or not bruto:
                    continue
                msg = email.message_from_bytes(bruto)
                texto = re.sub(r"\n{2,}", "\n", _cuerpo_del_correo(msg))
                calendario = _resumen_calendario(msg)
                if calendario:
                    texto = calendario + "\n" + texto
                c["extracto"] = texto[:MAX_EXTRACTO_CORREO]
            except Exception as e:
                print(f"  (no se pudo leer el correo '{c['asunto'][:50]}': {e})")
                continue
            correos.append(c)
    finally:
        try:
            mail.logout()
        except Exception:
            pass
    return correos


# --- Clasificación con el modelo ---
TIPOS_NOVEDAD = ("rechazo", "entrevista", "test", "oferta", "recibida", "otro", "ninguna")
_SINONIMOS_TIPO = {
    "rejection": "rechazo", "rejected": "rechazo", "rechazada": "rechazo",
    "rechazado": "rechazo", "descartada": "rechazo", "descartado": "rechazo",
    "declined": "rechazo", "negativa": "rechazo",
    "interview": "entrevista", "entrevistas": "entrevista", "reunion": "entrevista",
    "meeting": "entrevista", "call": "entrevista", "llamada": "entrevista",
    "assessment": "test", "prueba": "test", "evaluacion": "test", "challenge": "test",
    "testtecnico": "test", "tarea": "test", "task": "test", "cuestionario": "test",
    "offer": "oferta", "ofertarecibida": "oferta", "propuesta": "oferta",
    "received": "recibida", "recibido": "recibida", "acuse": "recibida",
    "acknowledgment": "recibida", "confirmacion": "recibida", "confirmation": "recibida",
    "other": "otro", "otra": "otro",
    "none": "ninguna", "null": "ninguna", "nada": "ninguna", "irrelevante": "ninguna",
    "": "ninguna",
}


def _tipo_novedad(valor):
    n = _normaliza(valor)
    if n in TIPOS_NOVEDAD:
        return n
    return _SINONIMOS_TIPO.get(n, "otro")


def _texto_o_none(valor):
    """El modelo a veces escribe "null", "None" o "N/A" como texto."""
    if valor is None:
        return None
    t = str(valor).strip()
    return None if t.lower() in ("", "null", "none", "n/a", "nan", "-") else t


def _fecha_iso_o_none(valor):
    t = _texto_o_none(valor)
    if not t:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", t)
    if not m:
        return None
    try:
        datetime.date.fromisoformat(m.group(1))
    except ValueError:
        return None
    return m.group(1)


def _postulaciones_para_prompt(postulaciones, correos):
    """Recorta la lista que ve el modelo: todas las que ya pasaron de 'Sugerida'
    (pueden recibir respuesta) más las 'Sugerida' cuya empresa aparece en algún
    correo candidato. Así el prompt no crece con las cientos de sugerencias
    automáticas que se acumulan en el tracker."""
    texto = _normaliza(" ".join(f"{c['de']} {c['asunto']} {c.get('extracto', '')}"
                                for c in correos))
    elegidas = []
    for p in postulaciones:
        orden = _orden_estado(p.get("estado"))
        if orden >= 1 or (orden == -1 and p.get("estado")):
            elegidas.append(p)
        elif any(pat in texto for pat in _patrones_empresas([p])):
            elegidas.append(p)
    return elegidas


def clasificar_novedades(correos, postulaciones):
    """Pide al modelo asociar cada correo candidato a una postulación y clasificarlo.
    Devuelve dicts ya validados: {correo (1..n), postulacion (record id o None),
    tipo, resumen, fecha_entrevista (YYYY-MM-DD o None), accion (texto o None)}."""
    if not correos or not postulaciones:
        return []
    print(f"Clasificando {len(correos)} correos contra {len(postulaciones)} postulaciones...")
    lista_post = "\n".join(
        f"[{p['id']}] {p['empresa']} — {p['puesto']} — Estado: {p['estado'] or ESTADO_INICIAL}"
        for p in postulaciones)
    bloques = "\n\n".join(
        f"=== CORREO {i} ===\nDe: {c['de']}\nAsunto: {c['asunto']}\nFecha: {c['fecha']}\n"
        f"{c.get('extracto', '')}"
        for i, c in enumerate(correos, 1))

    prompt = f"""Eres el asistente de seguimiento de postulaciones laborales de una candidata.
Abajo tienes (A) sus postulaciones registradas en su tracker y (B) correos recientes de su
bandeja de entrada. Decide qué correos son novedades sobre UNA de esas postulaciones.

REGLAS
1. Asocia un correo a una postulación SOLO si la empresa Y el puesto corresponden de forma
   plausible (remitente, dominio, firma o texto mencionan la empresa; el cargo coincide o al
   menos no se contradice). Si la empresa tiene varias postulaciones y el correo no permite
   distinguir el puesto, elige la de estado más avanzado.
2. Si el correo sí es sobre una postulación de la candidata pero no corresponde a ninguna
   registrada, pon "postulacion": null y clasifica igual el tipo.
3. "tipo" (elige exactamente uno):
   - "rechazo": la empresa descarta la candidatura ("lamentamos", "no seguiremos adelante",
     "unfortunately", "not moving forward", "decided to pursue other candidates"...).
   - "entrevista": invitan, proponen horario o confirman una entrevista, llamada o reunión
     con la empresa o su reclutador (incluye invitaciones de calendario).
   - "test": piden completar una prueba, assessment, case study, tarea técnica o cuestionario.
   - "oferta": envían una oferta laboral o carta de oferta concreta a la candidata.
   - "recibida": confirman que recibieron o registraron la postulación ("gracias por postular",
     "your application was sent/received") sin decidir nada todavía.
   - "otro": comunicación real de la empresa sobre esa postulación que no calza arriba (piden
     documentos o referencias, avisan demoras, preguntan disponibilidad o pretensiones...).
   - "ninguna": boletines, alertas y recomendaciones de empleo, publicidad de portales,
     correos escritos por la propia candidata, notificaciones automáticas que no cambian
     nada ("tu postulación fue vista", "N personas postularon") y cualquier correo que no
     trate sobre UNA postulación de ELLA.
4. Sé conservadora: ante la duda entre "rechazo", "entrevista" u "oferta" y "otro", elige
   "otro". Ante la duda de si el correo trata sobre su postulación, elige "ninguna".
5. "fecha_entrevista": solo si el correo indica una fecha explícita de la entrevista, en
   formato YYYY-MM-DD (deduce el año con la fecha del correo). Si no hay fecha, null.
   Si hay hora, menciónala en el resumen.
6. "accion": qué debe hacer la candidata (responder, elegir horario, completar la prueba
   antes de tal fecha...), en una frase; null si no tiene que hacer nada.
7. "resumen": 1 o 2 frases en español, concretas: qué dice la empresa y qué implica.
8. Devuelve una entrada por cada correo cuyo tipo NO sea "ninguna". Si ningún correo es
   relevante, devuelve {{"novedades": []}}.

(A) POSTULACIONES REGISTRADAS (formato: [id] Empresa — Puesto — Estado)
{lista_post}

(B) CORREOS
{bloques}

FORMATO DE SALIDA
Devuelve ÚNICAMENTE un objeto JSON válido con esta forma exacta:
{{
  "novedades": [
    {{
      "correo": 1,
      "postulacion": "recXXXXXXXXXXXXXX o null",
      "tipo": "rechazo|entrevista|test|oferta|recibida|otro",
      "resumen": "1 o 2 frases",
      "fecha_entrevista": "YYYY-MM-DD o null",
      "accion": "qué debe hacer la candidata, o null"
    }}
  ]
}}
"""
    mensajes = [
        {"role": "system",
         "content": ("Eres un asistente de seguimiento de postulaciones. Devuelves "
                     "exclusivamente JSON válido, sin texto adicional.")},
        {"role": "user", "content": prompt},
    ]
    datos = _llamar_llm(mensajes, temperature=0.1, max_tokens=4000)

    ids_validos = {p["id"] for p in postulaciones}
    salida, vistos = [], set()
    for n in (datos or {}).get("novedades") or []:
        if not isinstance(n, dict):
            continue
        try:
            idx = int(n.get("correo"))
        except (TypeError, ValueError):
            continue
        if not 1 <= idx <= len(correos) or idx in vistos:
            continue
        vistos.add(idx)
        rec = _texto_o_none(n.get("postulacion"))
        salida.append({
            "correo": idx,
            "postulacion": rec if rec in ids_validos else None,
            "tipo": _tipo_novedad(n.get("tipo")),
            "resumen": _texto_o_none(n.get("resumen")) or "",
            "fecha_entrevista": _fecha_iso_o_none(n.get("fecha_entrevista")),
            "accion": _texto_o_none(n.get("accion")),
        })
    return salida


# --- Reglas de actualización del tracker ---
def _fecha_ddmmyyyy(fecha_iso):
    try:
        return datetime.date.fromisoformat(str(fecha_iso)).strftime("%d-%m-%Y")
    except Exception:
        return datetime.date.today().strftime("%d-%m-%Y")


def _linea_feedback(nov, fecha_correo):
    """[dd-mm-yyyy] Correo de {remitente corto} — {Tipo}: {resumen} [Acción: ...]"""
    tipo = nov.get("tipo", "otro")
    resumen = (nov.get("resumen") or "").strip() or "(sin detalle)"
    linea = (f"[{_fecha_ddmmyyyy(fecha_correo)}] Correo de "
             f"{_remitente_corto(nov.get('de', '')) or 'la empresa'} — "
             f"{tipo.capitalize()}: {resumen}")
    if nov.get("accion"):
        linea += f" Acción: {str(nov['accion']).rstrip('.')}."
    return linea


def _cambios_para_novedad(post, nov, fecha_correo):
    """Función PURA (sin red). Dado el registro actual `post` (dict de
    cargar_postulaciones_airtable) y la novedad clasificada `nov` (con 'de'),
    devuelve (campos_a_patchear, descripcion_cambios). Reglas:
      - Siempre se agrega una línea a la bitácora, conservando el texto previo.
      - Estados cerrados (Rechazado / Sin respuesta / Retirado) y estados no
        reconocidos: solo bitácora.
      - 'Sugerida' o sin estado + cualquier correo => 'Postulado' (+ fecha de
        postulación si estaba vacía), y luego se aplica la regla del tipo.
      - rechazo => Rechazado; oferta => Oferta recibida.
      - entrevista => '1ª entrevista agendada' si va antes de eso, '2ª entrevista
        agendada' si la 1ª está realizada; si no, Estado igual. La fecha 1ª/2ª se
        escribe solo si viene explícita y el campo está vacío.
      - test => 'Próxima acción' si estaba vacía; Estado no cambia.
      - recibida / otro => solo bitácora.
      - El Estado nunca retrocede."""
    campos, cambios = {}, []
    tipo = nov.get("tipo", "otro")
    estado_actual = (post.get("estado") or "").strip()
    orden_actual = _orden_estado(estado_actual)

    # 1) Bitácora: siempre, y siempre sobre el texto que ya había.
    previo = (post.get("feedback") or "").rstrip()
    linea = _linea_feedback(nov, fecha_correo)
    campos[CAMPO_LOG] = f"{previo}\n{linea}" if previo else linea

    if orden_actual >= ORDEN_CERRADO:
        return campos, cambios                     # cerrado: no se reabre
    if orden_actual == -1 and estado_actual:
        return campos, cambios                     # estado desconocido: no se toca

    nuevo_estado = None
    orden = orden_actual
    # 2) Si seguía como sugerencia, el correo demuestra que sí postuló.
    if orden <= 0:
        nuevo_estado = "Postulado"
        orden = 1
        if not post.get("fecha_post"):
            campos[CAMPO_FECHA_POST] = fecha_correo
            cambios.append(f"Fecha de postulación: {_fecha_ddmmyyyy(fecha_correo)}")

    # 3) Regla del tipo.
    fecha_ent = nov.get("fecha_entrevista")
    if tipo == "rechazo":
        nuevo_estado = "Rechazado"
    elif tipo == "oferta":
        nuevo_estado = "Oferta recibida"
    elif tipo == "entrevista":
        if orden < 3:
            nuevo_estado = "1ª entrevista agendada"
        elif orden == 4:
            nuevo_estado = "2ª entrevista agendada"
        if fecha_ent and orden <= 3 and not post.get("f1"):
            campos[CAMPO_FECHA_ENT1] = fecha_ent
            cambios.append(f"Fecha 1ª entrevista: {_fecha_ddmmyyyy(fecha_ent)}")
        elif fecha_ent and orden in (4, 5) and not post.get("f2"):
            campos[CAMPO_FECHA_ENT2] = fecha_ent
            cambios.append(f"Fecha 2ª entrevista: {_fecha_ddmmyyyy(fecha_ent)}")
    elif tipo == "test":
        if nov.get("accion") and not post.get("prox_accion"):
            campos[CAMPO_PROX_ACCION] = str(nov["accion"])[:200]
            cambios.append(f"Próxima acción: {str(nov['accion'])[:80]}")
    # recibida / otro: solo bitácora (más el paso 2 si venía de Sugerida).

    # 4) Estado: solo hacia adelante.
    if nuevo_estado and _orden_estado(nuevo_estado) > orden_actual:
        campos[CAMPO_ESTADO] = nuevo_estado
        cambios.insert(0, f"Estado: {estado_actual or ESTADO_INICIAL} → {nuevo_estado}")
    return campos, cambios


def actualizar_registro_airtable(record_id, campos):
    """PATCH de una fila del tracker (solo los campos indicados). True si se guardó."""
    if not AIRTABLE_TOKEN or not record_id or not campos:
        return False
    try:
        r = requests.patch(
            f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{AIRTABLE_TABLA}/{record_id}",
            headers={"Authorization": f"Bearer {AIRTABLE_TOKEN}",
                     "Content-Type": "application/json"},
            json={"fields": campos, "typecast": True}, timeout=30,
        )
        if r.status_code == 200:
            return True
        print(f"  Airtable respondió {r.status_code} al actualizar {record_id}: {r.text[:200]}")
    except Exception as e:
        print(f"  No se pudo actualizar {record_id} en Airtable: {e}")
    return False


def procesar_novedades(postulaciones, escribir=True):
    """Orquesta el seguimiento: lee correos -> clasifica -> aplica cambios en Airtable
    -> marca los correos como procesados y guarda el historial. Con escribir=False
    no toca Airtable ni el historial (modo ensayo).
    Devuelve {"novedades": [...], "sin_match": [...]}."""
    resultado = {"novedades": [], "sin_match": []}
    if not postulaciones:
        print("  (tracker vacío o no leído: se omite el seguimiento de postulaciones)")
        return resultado

    correos = leer_correos_seguimiento(postulaciones)
    print(f"  Correos candidatos a novedad: {len(correos)}")
    if not correos:
        return resultado

    clasificadas = clasificar_novedades(
        correos, _postulaciones_para_prompt(postulaciones, correos))
    # Cronológico: la bitácora queda en orden y los estados avanzan en secuencia
    # si una misma empresa mandó varios correos (acuse -> entrevista).
    clasificadas.sort(key=lambda n: correos[n["correo"] - 1]["fecha"])
    por_id = {p["id"]: p for p in postulaciones}
    fallidos = set()

    for nov in clasificadas:
        c = correos[nov["correo"] - 1]
        if nov["tipo"] == "ninguna":
            continue
        if not nov["postulacion"]:
            resultado["sin_match"].append({
                "asunto": c["asunto"], "de": c["de"], "tipo": nov["tipo"],
                "resumen": nov["resumen"], "message_id": c["message_id"]})
            continue
        post = por_id[nov["postulacion"]]
        nov = dict(nov, de=c["de"], asunto=c["asunto"], message_id=c["message_id"])
        campos, cambios = _cambios_para_novedad(post, nov, c["fecha"])
        if campos.get(CAMPO_ESTADO) not in (None, *ESTADOS_ORDEN):   # nunca inventar opciones
            campos.pop(CAMPO_ESTADO)
            cambios = [x for x in cambios if not x.startswith("Estado:")]

        ok = True
        if escribir:
            ok = actualizar_registro_airtable(post["id"], campos)
            time.sleep(0.25)                          # límite de Airtable: 5 req/s
        if ok:
            for campo, valor in campos.items():       # así el siguiente correo de la
                if campo in _CAMPO_A_CLAVE:           # misma fila parte de lo ya escrito
                    post[_CAMPO_A_CLAVE[campo]] = valor
        else:
            fallidos.add(nov["correo"])
            cambios = ["NO SE PUDO GUARDAR EN AIRTABLE"] + cambios

        resultado["novedades"].append({
            "empresa": post["empresa"], "puesto": post["puesto"], "tipo": nov["tipo"],
            "resumen": nov["resumen"], "accion": nov["accion"], "cambios": cambios,
            "record_id": post["id"], "asunto": c["asunto"], "de": c["de"],
            "message_id": c["message_id"], "fecha": c["fecha"],
        })
        print(f"  {nov['tipo']:<10} {post['empresa']} — {post['puesto']}: "
              f"{'; '.join(cambios) or 'solo bitácora'}")

    if escribir:
        hoy = datetime.date.today().isoformat()
        for i, c in enumerate(correos, 1):
            if i not in fallidos:
                HISTORIAL[c["clave"]] = hoy           # no se vuelve a mirar mañana
        guardar_historial(HISTORIAL)                  # antes del SMTP: si el envío falla,
                                                      # no se duplican bitácoras mañana
    print(f"  Novedades: {len(resultado['novedades'])} asociadas, "
          f"{len(resultado['sin_match'])} sin postulación registrada.")
    return resultado


# ===================== CORREO =====================
_ESTILO_TIPO = {
    "rechazo":    ("#C0392B", "Rechazo"),
    "entrevista": ("#D68910", "Entrevista"),
    "oferta":     ("#1E8449", "Oferta"),
    "test":       ("#2E86C1", "Prueba / test"),
    "recibida":   ("#5B6570", "Postulación recibida"),
    "otro":       ("#5B6570", "Otro"),
}


def _url_registro_airtable(record_id):
    return f"https://airtable.com/{AIRTABLE_BASE}/{AIRTABLE_TABLA}/{record_id}" if record_id else ""


def _url_correo_gmail(message_id):
    if not message_id:
        return ""
    return ("https://mail.google.com/mail/u/0/#search/"
            + urllib.parse.quote(f"rfc822msgid:{message_id}", safe=""))


def _html_novedades(novedades, con_titulo=True):
    """Sección 'Novedades de tus postulaciones': una tarjeta por novedad asociada
    y una lista compacta con los correos relevantes que no calzan con el tracker."""
    items = (novedades or {}).get("novedades") or []
    sin_match = (novedades or {}).get("sin_match") or []
    if not items and not sin_match:
        return ""
    estilo_enlace = "color:#2E86C1;font-size:13px;text-decoration:none;font-weight:bold;"
    tarjetas = []
    for n in items:
        color, etiqueta = _ESTILO_TIPO.get(n.get("tipo"), _ESTILO_TIPO["otro"])
        cambios = "; ".join(n.get("cambios") or []) or "Solo bitácora"
        enlaces = []
        if n.get("record_id"):
            enlaces.append(f'<a href="{html.escape(_url_registro_airtable(n["record_id"]))}" '
                           f'style="{estilo_enlace}">Ver en Airtable</a>')
        if n.get("message_id"):
            enlaces.append(f'<a href="{html.escape(_url_correo_gmail(n["message_id"]))}" '
                           f'style="{estilo_enlace}">Ver correo</a>')
        accion = ""
        if n.get("accion"):
            accion = f'<p class="accion">Qué hacer: {html.escape(str(n["accion"]))}</p>'
        tarjetas.append(f"""
        <div class="job-card" style="border-left:5px solid {color};">
          <span style="display:inline-block;background:{color};color:#ffffff;font-size:11px;font-weight:bold;padding:3px 9px;border-radius:10px;text-transform:uppercase;letter-spacing:.5px;">{etiqueta}</span>
          <h2 class="job-title" style="margin-top:8px;">{html.escape(str(n.get('empresa', '')))} — {html.escape(str(n.get('puesto', '')))}</h2>
          <p class="texto">{html.escape(str(n.get('resumen') or ''))}</p>
          {accion}
          <p class="meta"><b>Cambios en el tracker:</b> {html.escape(cambios)}</p>
          <p class="meta">Correo del {_fecha_ddmmyyyy(n.get('fecha', ''))}: "{html.escape(str(n.get('asunto', '')))}" — {html.escape(_remitente_corto(n.get('de', '')))}</p>
          <p>{' &nbsp;·&nbsp; '.join(enlaces)}</p>
        </div>""")

    lista = ""
    if sin_match:
        filas = []
        for s in sin_match:
            color, etiqueta = _ESTILO_TIPO.get(s.get("tipo"), _ESTILO_TIPO["otro"])
            enlace = ""
            if s.get("message_id"):
                enlace = (f' <a href="{html.escape(_url_correo_gmail(s["message_id"]))}" '
                          f'style="{estilo_enlace}">Ver correo</a>')
            filas.append(f'<li><span style="color:{color};font-weight:bold;">{etiqueta}</span> — '
                         f'{html.escape(str(s.get("asunto", "")))} '
                         f'({html.escape(_remitente_corto(s.get("de", "")))}): '
                         f'{html.escape(str(s.get("resumen") or ""))}{enlace}</li>')
        lista = ('<p class="sub">Correos sobre postulaciones que no están en el tracker</p>'
                 f'<ul>{"".join(filas)}</ul>')

    titulo = '<h2 class="seccion">Novedades de tus postulaciones</h2>' if con_titulo else ""
    return f'{titulo}{"".join(tarjetas)}{lista}'


def _asunto_correo(n_ofertas, n_novedades):
    if n_ofertas and n_novedades:
        return f"Ofertas del día ({n_ofertas}) + novedades ({n_novedades})"
    if n_ofertas:
        return f"Ofertas del día + CVs adaptados ({n_ofertas})"
    return f"Novedades de tus postulaciones ({n_novedades})"


def _html_correo(ofertas, novedades=None):
    tarjetas = []
    for o in ofertas:
        pros = "".join(f"<li>{html.escape(str(p))}</li>" for p in o.get("pros", []))
        contras = "".join(f"<li>{html.escape(str(c))}</li>" for c in o.get("contras", []))
        enlace = o.get("enlace") or ""
        estilo_azul = ("background:#2E86C1;color:#ffffff;padding:9px 16px;border-radius:5px;"
                       "text-decoration:none;display:inline-block;font-size:13px;"
                       "font-family:'Segoe UI',Tahoma,sans-serif;")
        estilo_verde = estilo_azul.replace("#2E86C1", "#1E8449")

        botones = []
        if enlace.startswith("http"):
            botones.append(f'<a href="{html.escape(enlace)}" '
                           f'style="{estilo_azul}">Postular aquí</a>')
        url_aplique = _url_ya_aplique(o.get("_record_id"))
        if url_aplique:
            botones.append(f'<a href="{html.escape(url_aplique)}" '
                           f'style="{estilo_verde}">✅ Ya apliqué</a>')
        boton = ("<p>" + "&nbsp;&nbsp;".join(botones) + "</p>") if botones else ""
        tarjetas.append(f"""
        <div class="job-card">
          <h2 class="job-title">{html.escape(str(o.get('cargo', '')))} en {html.escape(str(o.get('empresa', '')))}</h2>
          <p class="meta"><b>Modalidad:</b> {html.escape(str(o.get('ubicacion', 'No indicada')))} &nbsp;|&nbsp;
             <b>Sueldo:</b> {html.escape(str(o.get('sueldo', 'No indicado')))} &nbsp;|&nbsp;
             <b>Match:</b> {html.escape(str(o.get('match', '-')))}/100</p>
          {boton}
          <p class="sub">A favor</p><ul>{pros}</ul>
          <p class="sub">Riesgos</p><ul>{contras}</ul>
          <p class="adjunto">CV adaptado adjunto en PDF.</p>
        </div>""")

    hoy = f"{datetime.date.today():%d-%m-%Y}"
    seccion_novedades = _html_novedades(novedades, con_titulo=bool(ofertas))
    if ofertas:
        titulo = f"Oportunidades laborales · {hoy}"
        subtitulo = '<h2 class="seccion">Ofertas del día</h2>' if seccion_novedades else ""
    else:
        titulo = f"Novedades de tus postulaciones · {hoy}"
        subtitulo = ""

    return f"""<html><head><meta charset="utf-8"><style>
      body {{ font-family: 'Segoe UI', Tahoma, sans-serif; background:#f4f7f6; padding:20px; color:#1f1f1f; }}
      .container {{ max-width:760px; margin:0 auto; background:#fff; padding:28px; border-radius:10px; }}
      .header-title {{ color:#16283C; border-bottom:3px solid #2E86C1; padding-bottom:10px; text-align:center; }}
      .seccion {{ color:#16283C; font-size:17px; margin:24px 0 12px 0; padding-bottom:6px; border-bottom:1px solid #e3e6e8; }}
      .job-card {{ background:#fafafa; border:1px solid #e3e6e8; border-radius:8px; padding:18px; margin-bottom:22px; }}
      .job-title {{ color:#2E86C1; margin:0 0 6px 0; font-size:18px; }}
      .meta {{ color:#5B6570; font-size:13px; margin:0 0 10px 0; }}
      .texto {{ font-size:14px; margin:6px 0 8px 0; }}
      .accion {{ font-size:13px; font-weight:bold; color:#16283C; margin:0 0 8px 0; }}
      .sub {{ font-weight:bold; margin:10px 0 2px 0; font-size:13px; }}
      ul {{ margin:0; padding-left:20px; font-size:13px; }}
      .btn {{ background:#2E86C1; color:#fff !important; padding:8px 14px; border-radius:5px;
             text-decoration:none; display:inline-block; font-size:13px; }}
      .adjunto {{ color:#1E8449; font-weight:bold; font-size:13px; margin-top:10px; }}
    </style></head><body><div class="container">
      <h1 class="header-title">{titulo}</h1>
      {seccion_novedades}
      {subtitulo}
      {''.join(tarjetas)}
    </div></body></html>"""


def enviar_correo(datos, novedades=None):
    ofertas = (datos or {}).get("ofertas", [])

    # Segundo filtro: aunque el modelo la haya elegido, si ya se envió antes se descarta.
    # Cubre las ofertas que llegan por correo, donde no hay enlace estable.
    # Tercer filtro: regla geográfica (remoto abierto a Chile o híbrido en Santiago).
    # Las omitidas por ubicación NO se marcan como vistas: si la fuente trae mejor
    # información otro día, el modelo puede volver a evaluarlas.
    nuevas, repetidas, omitidas = [], [], []
    for o in ofertas:
        if ya_vista(o.get("empresa"), o.get("cargo"), o.get("enlace", "")):
            repetidas.append(f"{o.get('cargo')} en {o.get('empresa')}")
            continue
        permitida, motivo = _oferta_permitida(o)
        if not permitida:
            omitidas.append(f"{o.get('cargo')} en {o.get('empresa')} ({motivo})")
            continue
        nuevas.append(o)
    if repetidas:
        print(f"  Omitidas por repetidas: {', '.join(repetidas)}")
    for linea in omitidas:
        print(f"  Omitida por ubicación: {linea}")
    ofertas = nuevas[:MAX_OFERTAS]

    n_novedades = (len((novedades or {}).get("novedades") or [])
                   + len((novedades or {}).get("sin_match") or []))
    if not ofertas and not n_novedades:
        print("No hay ofertas nuevas ni novedades que enviar hoy.")
        print("Revisa el archivo 'ultimas_ofertas_crudas.txt' para ver qué "
              "material recibió. Si está casi vacío, el problema es la lectura "
              "de correos, no los filtros.")
        guardar_historial(HISTORIAL)
        return

    if ofertas:
        os.makedirs(CARPETA_SALIDA, exist_ok=True)
        print(f"Generando {len(ofertas)} PDFs y registrando en Airtable...")

    # Paso 1: PDF de cada oferta + fila en el tracker.
    # Se hace ANTES de armar el correo porque el botón necesita el record id.
    for o in ofertas:
        cv = o.get("cv") or {}
        cv.setdefault("nombre", DATOS_FIJOS["nombre"])
        cv.setdefault("contacto", DATOS_FIJOS["contacto"])
        nombre_archivo = f"{_prefijo_cv()}_{_slug(o.get('empresa'))}_{_slug(o.get('cargo'), 30)}.pdf"
        ruta = os.path.join(CARPETA_SALIDA, nombre_archivo)
        try:
            crear_pdf_cv(cv, ruta)
            o["_pdf"] = ruta
            o["_pdf_nombre"] = nombre_archivo
            print(f"  OK: {ruta}")
        except Exception as e:
            print(f"  No se pudo generar el PDF de {o.get('empresa')}: {e}")
        o["_record_id"] = crear_registro_airtable(o, nombre_archivo)
        marcar_vista(o.get("empresa"), o.get("cargo"), o.get("enlace", ""))

    # Paso 2: armar el correo, ya con los botones "Ya apliqué".
    msg = MIMEMultipart("mixed")
    msg["From"] = EMAIL_USUARIO
    msg["To"] = EMAIL_USUARIO
    msg["Subject"] = _asunto_correo(len(ofertas), n_novedades)

    cuerpo = MIMEMultipart("alternative")
    cuerpo.attach(MIMEText(_html_correo(ofertas, novedades), "html", "utf-8"))
    msg.attach(cuerpo)

    # Paso 3: adjuntar los PDFs que sí se generaron.
    for o in ofertas:
        if not o.get("_pdf"):
            continue
        with open(o["_pdf"], "rb") as f:
            adj = MIMEApplication(f.read(), _subtype="pdf")
        adj.add_header("Content-Disposition", "attachment", filename=o["_pdf_nombre"])
        msg.attach(adj)

    server = smtplib.SMTP("smtp.gmail.com", 587, timeout=60)
    server.starttls()
    server.login(EMAIL_USUARIO, EMAIL_PASSWORD)
    server.send_message(msg)
    server.quit()
    guardar_historial(HISTORIAL)
    print(f"Correo enviado: {msg['Subject']}.")
    if ofertas:
        print(f"Los PDFs quedaron en la carpeta '{CARPETA_SALIDA}' "
              "para que los revises antes de postular.")


# ===================== MAIN =====================
def _verificar_configuracion():
    """Corta con un mensaje claro si falta lo imprescindible del .env."""
    faltan = [n for n, v in (("EMAIL_USUARIO", EMAIL_USUARIO), ("EMAIL_PASSWORD", EMAIL_PASSWORD),
                             ("DEEPINFRA_API_KEY", DEEPINFRA_API_KEY)) if not v]
    if faltan:
        raise RuntimeError("Faltan variables en el archivo .env: " + ", ".join(faltan)
                           + " (copia .env.example a .env y complétalo)")
    if not (AIRTABLE_TOKEN and AIRTABLE_BASE and AIRTABLE_TABLA):
        print("  Aviso: AIRTABLE_TOKEN, AIRTABLE_BASE o AIRTABLE_TABLA vacíos; no se usará el tracker.")
    if not DATOS_FIJOS["nombre"]:
        print("  Aviso: NOMBRE_CANDIDATA vacío; la cabecera de los PDF saldrá sin nombre.")
    if not os.path.exists(RUTA_CV_GENERAL):
        raise RuntimeError(f"No existe {RUTA_CV_GENERAL}: crea cv.txt con el CV general en texto plano")


def main():
    _verificar_configuracion()
    with open(RUTA_CV_GENERAL, "r", encoding="utf-8") as f:
        cv_texto = f.read()

    postulaciones = cargar_postulaciones_airtable()
    if postulaciones:
        print(f"Tracker leído: {len(postulaciones)} postulaciones registradas, no se repetirán.")

    # 1) Novedades de postulaciones. Si falla, se avisa y se sigue con las ofertas.
    novedades = None
    try:
        novedades = procesar_novedades(postulaciones)
    except Exception as e:
        print(f"  No se pudieron procesar las novedades: {type(e).__name__}: {e}")

    # 2) Ofertas nuevas.
    ofertas = leer_correos_hoy() + "\n" + leer_fuentes_publicas()

    # Copia de lo que se le manda al modelo, para poder revisarlo después.
    with open(os.path.join(BASE_DIR, "ultimas_ofertas_crudas.txt"),
              "w", encoding="utf-8") as f:
        f.write(ofertas)
    print(f"Material recopilado: {len(ofertas)} caracteres, "
          f"{ofertas.count('--- CORREO:')} correos.")

    datos = None
    if len(ofertas.strip()) <= 10:
        print("No se encontraron ofertas relevantes hoy.")
    else:
        if len(ofertas) > MAX_CARACTERES_OFERTAS:
            print(f"  Recortando material de {len(ofertas)} a "
                  f"{MAX_CARACTERES_OFERTAS} caracteres.")
            ofertas = ofertas[:MAX_CARACTERES_OFERTAS]
        try:
            datos = analizar_con_llm(cv_texto, ofertas)
        except Exception as e:
            # Las novedades ya están en Airtable; que se manden aunque el modelo falle.
            print(f"  No se pudieron analizar las ofertas: {type(e).__name__}: {e}")

    # 3) Un solo correo con ambas cosas (se manda si hay ofertas O novedades).
    enviar_correo(datos, novedades)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Ocurrió un error: {type(e).__name__}: {e}")
        sys.exit(1)   # que launchd y el lanzador registren el fallo
