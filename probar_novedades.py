# -*- coding: utf-8 -*-
"""
Pruebas del seguimiento de postulaciones ("Novedades").

Uso (bajo Rosetta, igual que ejecutar_buscador.sh):
  arch -x86_64 /Library/Frameworks/Python.framework/Versions/3.10/bin/python3 probar_novedades.py
      Sin red: reglas de cambio de estado (_cambios_para_novedad), prefiltro de
      correos, decodificación de cabeceras y parseo de FETCH. Solo asserts.
  ... probar_novedades.py --correos
      Lee Gmail (solo lectura) y lista los correos candidatos. Sin modelo, sin escrituras.
  ... probar_novedades.py --clasificar [--html]
      Gmail + modelo: muestra la clasificación y los cambios que HARÍA. No escribe
      en Airtable ni en el historial. Con --html guarda novedades_preview.html.
  ... probar_novedades.py --aplicar
      Corrida real de procesar_novedades: ESCRIBE en Airtable y marca los correos
      como procesados en historial_ofertas.json. No manda correo.
"""
import email
import json
import os
import sys
import buscar_empleo_V9 as b

FECHA = "2026-09-14"


def _post(estado, **extra):
    base = {"id": "recTEST000000001", "empresa": "Acme SpA", "puesto": "Marketing Manager",
            "estado": estado, "feedback": "[01-09-2026] Detectada por el buscador automático.",
            "fecha_post": "", "f1": "", "f2": "", "prox_accion": ""}
    base.update(extra)
    return base


def _nov(tipo, **extra):
    base = {"tipo": tipo, "resumen": "Resumen de prueba.", "fecha_entrevista": None,
            "accion": None, "de": "Ana Reclutadora <ana@acme.com>"}
    base.update(extra)
    return base


CASOS_CAMBIOS = [
    # (nombre, registro, novedad, Estado esperado (None = no cambia), otros campos esperados)
    ("Sugerida + recibida => Postulado + fecha", _post("Sugerida"), _nov("recibida"),
     "Postulado", {b.CAMPO_FECHA_POST: FECHA}),
    ("Sugerida + rechazo => Rechazado + fecha", _post("Sugerida"), _nov("rechazo"),
     "Rechazado", {b.CAMPO_FECHA_POST: FECHA}),
    ("Postulado + rechazo => Rechazado", _post("Postulado", fecha_post="2026-09-02"),
     _nov("rechazo"), "Rechazado", {}),
    ("Postulado + entrevista con fecha => 1ª agendada + F1", _post("Postulado"),
     _nov("entrevista", fecha_entrevista="2026-09-20"),
     "1ª entrevista agendada", {b.CAMPO_FECHA_ENT1: "2026-09-20"}),
    ("1ª realizada + entrevista => 2ª agendada + F2",
     _post("1ª entrevista realizada", f1="2026-09-10"),
     _nov("entrevista", fecha_entrevista="2026-09-25"),
     "2ª entrevista agendada", {b.CAMPO_FECHA_ENT2: "2026-09-25"}),
    ("1ª agendada + entrevista => solo F1 si estaba vacía", _post("1ª entrevista agendada"),
     _nov("entrevista", fecha_entrevista="2026-09-21"), None, {b.CAMPO_FECHA_ENT1: "2026-09-21"}),
    ("2ª realizada + entrevista => sin cambio", _post("2ª entrevista realizada"),
     _nov("entrevista"), None, {}),
    ("Postulado + oferta => Oferta recibida", _post("Postulado"), _nov("oferta"),
     "Oferta recibida", {}),
    ("Oferta recibida + rechazo => Rechazado", _post("Oferta recibida"), _nov("rechazo"),
     "Rechazado", {}),
    ("Rechazado + entrevista => cerrado, solo bitácora", _post("Rechazado"),
     _nov("entrevista"), None, {}),
    ("Retirado + oferta => cerrado, solo bitácora", _post("Retirado"), _nov("oferta"), None, {}),
    ("Postulado + test => Próxima acción", _post("Postulado"),
     _nov("test", accion="Completar el assessment antes del viernes"), None,
     {b.CAMPO_PROX_ACCION: "Completar el assessment antes del viernes"}),
    ("Postulado + test con acción ya escrita => no se pisa",
     _post("Postulado", prox_accion="Llamar el lunes"),
     _nov("test", accion="Completar la prueba"), None, {}),
    ("1ª agendada + recibida => no retrocede", _post("1ª entrevista agendada"),
     _nov("recibida"), None, {}),
    ("Aplicado (legacy) + recibida => sin cambio", _post("Aplicado"), _nov("recibida"), None, {}),
    ("Sin estado + otro => Postulado", _post(""), _nov("otro"),
     "Postulado", {b.CAMPO_FECHA_POST: FECHA}),
    ("Estado desconocido => solo bitácora", _post("En pausa"), _nov("rechazo"), None, {}),
]


def probar_cambios():
    for nombre, post, nov, estado_esp, otros in CASOS_CAMBIOS:
        campos, cambios = b._cambios_para_novedad(post, nov, FECHA)
        assert b.CAMPO_LOG in campos, nombre
        assert campos[b.CAMPO_LOG].startswith(post["feedback"]), f"{nombre}: se pisó la bitácora"
        assert (f"[14-09-2026] Correo de Ana Reclutadora — {nov['tipo'].capitalize()}:"
                in campos[b.CAMPO_LOG]), f"{nombre}: {campos[b.CAMPO_LOG]!r}"
        assert campos.get(b.CAMPO_ESTADO) == estado_esp, \
            f"{nombre}: Estado {campos.get(b.CAMPO_ESTADO)!r} != {estado_esp!r}"
        for campo, valor in otros.items():
            assert campos.get(campo) == valor, \
                f"{nombre}: {campo} = {campos.get(campo)!r}, esperado {valor!r}"
        extra = set(campos) - {b.CAMPO_LOG, b.CAMPO_ESTADO} - set(otros)
        assert not extra, f"{nombre}: campos inesperados {extra}"
        print(f"  OK  {nombre}: {'; '.join(cambios) or 'solo bitácora'}")
    campos, _ = b._cambios_para_novedad(_post("Postulado", feedback=""), _nov("otro"), FECHA)
    assert not campos[b.CAMPO_LOG].startswith("\n"), "bitácora vacía con salto inicial"
    print("  OK  bitácora vacía: sin salto de línea inicial")


POSTULACIONES_PRUEBA = [
    {"id": "rec1", "empresa": "Toptal", "puesto": "Senior Marketing Analyst", "estado": "Postulado"},
    {"id": "rec2", "empresa": "Decision Point LATAM", "puesto": "Business Analyst", "estado": "Sugerida"},
    {"id": "rec3", "empresa": "Grupo Digital", "puesto": "Growth Lead", "estado": "Postulado"},
    {"id": "rec4", "empresa": "Iconstruye", "puesto": "Business Analyst", "estado": "1ª entrevista agendada"},
    {"id": "rec5", "empresa": "42 Labs", "puesto": "Growth Manager", "estado": "Sugerida"},
]

CASOS_PREFILTRO = [
    # (nombre, De, Asunto, ¿candidato?)
    ("ATS Greenhouse", "no-reply@greenhouse.io", "Your application to Toptal", True),
    ("empresa en el dominio", "María Pérez <maria@iconstruye.cl>", "Re: Reunión de esta semana", True),
    ("primera palabra de la empresa", "Talent <talent@decisionpoint.com>", "Próximos pasos", True),
    ("portal: estado de MI postulación", "jobs-noreply@linkedin.com",
     "Tu postulación fue enviada a Atomic", True),
    ("palabra clave en inglés", "hr@unknown-corp.com", "Interview invitation - Growth role", True),
    ("42 Labs por nombre completo", "People <people@42labs.io>", "Nos gustaría conocerte", True),
    ("alerta de empleo LinkedIn", "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>",
     "Alerta de empleo: 12 nuevos empleos de Marketing", False),
    ("digest Get on Board", "Get on Board <team@getonbrd.com>",
     "New jobs for you: Marketing Manager", False),
    ("correo propio (el del bot)", f"Candidata <{b.EMAIL_USUARIO}>",
     "Novedades de tus postulaciones (2)", False),
    ("Upwork", "Upwork <donotreply@upwork.com>", "Your proposal was viewed", False),
    ("newsletter", "Medium Daily Digest <noreply@medium.com>", "Weekly digest: growth stories", False),
    ("primera palabra genérica no basta", "hola@grupo-x.cl", "Hola", False),
    ("nada que ver", "Banco Estado <info@bancoestado.cl>", "Tu cartola de agosto", False),
]


def probar_prefiltro():
    patrones = b._patrones_empresas(POSTULACIONES_PRUEBA)
    assert {"toptal", "decisionpointlatam", "decision", "grupodigital", "iconstruye", "42labs"} <= patrones, patrones
    assert "grupo" not in patrones, patrones
    for nombre, de, asunto, esperado in CASOS_PREFILTRO:
        obtenido = b._es_candidato_seguimiento(de, asunto, patrones)
        assert obtenido == esperado, f"{nombre}: {obtenido} != {esperado} ({de} / {asunto})"
        print(f"  OK  {nombre}: {'candidato' if obtenido else 'descartado'}")


def probar_utilidades():
    bruto = (b"From: =?UTF-8?Q?Mar=C3=ADa_P=C3=A9rez?= <maria@acme.com>\r\n"
             b"Subject: =?UTF-8?Q?Re:_Tu_postulaci=C3=B3n_a?= =?UTF-8?Q?_Marketing_Manager?=\r\n"
             b"Date: Mon, 14 Sep 2026 12:00:00 -0300\r\n"
             b"Message-ID: <abc.123@mail.acme.com>\r\n\r\n")
    msg = email.message_from_bytes(bruto)
    assert b._decodificar_asunto(msg) == "Re: Tu postulación a Marketing Manager", b._decodificar_asunto(msg)
    assert b._decodificar_cabecera(msg["From"]).startswith("María Pérez"), b._decodificar_cabecera(msg["From"])
    assert b._decodificar_asunto(email.message_from_bytes(b"From: x@y.z\r\n\r\n")) == ""
    assert b._fecha_de_cabecera(msg["Date"]) == "2026-09-14"
    assert b._limpiar_message_id(msg["Message-ID"]) == "abc.123@mail.acme.com"
    assert b._clave_correo("abc.123@mail.acme.com", "", "", "") == "correo:abc.123@mail.acme.com"
    assert b._clave_correo("", "a", "b", "c").startswith("correo:sha1:")
    assert b._remitente_corto("Ana Pérez <ana@acme.com>") == "Ana Pérez"
    assert b._remitente_corto("noreply@acme.com") == "acme.com"
    assert b._fecha_imap(__import__("datetime").date(2026, 9, 14)) == "14-Sep-2026"
    # Respuesta de FETCH por lotes, con la forma que entrega imaplib.
    data = [(b"1 (BODY[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)] {40}", bruto), b")",
            (b"2 (BODY[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)] {40}", bruto), b")"]
    assert [n for n, _ in b._partes_fetch(data)] == [b"1", b"2"]
    # Tipos y valores "null" del modelo.
    assert b._tipo_novedad("Rejected") == "rechazo" and b._tipo_novedad("INTERVIEW") == "entrevista"
    assert b._tipo_novedad(None) == "ninguna" and b._tipo_novedad("cosa rara") == "otro"
    assert b._texto_o_none("null") is None and b._texto_o_none(" ok ") == "ok"
    assert b._fecha_iso_o_none("2026-09-20T15:00") == "2026-09-20"
    assert b._fecha_iso_o_none("mañana") is None and b._fecha_iso_o_none("2026-13-40") is None
    assert b._orden_estado("1a entrevista agendada") == 3 and b._orden_estado("Sugerida") == 0
    assert b._orden_estado("Rechazado") >= b.ORDEN_CERRADO and b._orden_estado("raro") == -1
    assert "rfc822msgid%3Aabc.123%40mail.acme.com" in b._url_correo_gmail("abc.123@mail.acme.com")
    print("  OK  cabeceras, fechas, claves, FETCH, tipos y enlaces")


def main():
    modo = next((a for a in sys.argv[1:] if a in ("--correos", "--clasificar", "--aplicar")), "")
    if not modo:
        print("Reglas de cambio de estado:")
        probar_cambios()
        print("Prefiltro de correos:")
        probar_prefiltro()
        print("Utilidades:")
        probar_utilidades()
        print("OK: todas las pruebas sin red pasaron.")
        return

    postulaciones = b.cargar_postulaciones_airtable()
    print(f"Postulaciones en el tracker: {len(postulaciones)}")
    if modo == "--correos":
        correos = b.leer_correos_seguimiento(postulaciones)
        for c in correos:
            print(f"- {c['fecha']} | {b._remitente_corto(c['de']):<28} | {c['asunto'][:60]:<60} "
                  f"| {len(c.get('extracto', ''))} chars | {c['clave']}")
        print(f"{len(correos)} correos candidatos (nada se marcó como procesado).")
        return

    escribir = modo == "--aplicar"
    resultado = b.procesar_novedades(postulaciones, escribir=escribir)
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
    if "--html" in sys.argv:
        ruta = os.path.join(b.BASE_DIR, "novedades_preview.html")
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(b._html_correo([], resultado))
        print(f"Vista previa del correo: {ruta}")
    print("ESCRITO en Airtable y en historial_ofertas.json." if escribir
          else "Ensayo: no se escribió nada (ni Airtable ni historial).")


if __name__ == "__main__":
    main()
