# -*- coding: utf-8 -*-
"""
Prueba del filtro geográfico SIN red: ofertas sintéticas contra _oferta_permitida
y atributos sintéticos de Get on Board contra _ubicacion_getonboard.

Uso:  arch -x86_64 /Library/Frameworks/Python.framework/Versions/3.10/bin/python3 probar_filtro.py
      Con --gob consulta Get on Board en vivo y muestra las primeras líneas
      UBICACION: (ya no deberían decir "No indicada").
"""
import sys
import buscar_empleo_V9 as b

GOB = "https://www.getonbrd.com/jobs/"

CASOS_GATE = [
    # (nombre, oferta, esperado)
    ("remoto global", {"modalidad": "remoto", "zona": "global",
                       "ubicacion": "Remoto - Worldwide"}, True),
    ("remoto latam", {"modalidad": "remoto", "zona": "latam",
                      "ubicacion": "Remoto - LATAM, Europe, USA"}, True),
    ("remoto solo Chile", {"modalidad": "remoto", "zona": "chile",
                           "ubicacion": "Remoto, solo para residentes en Chile"}, True),
    ("remoto Argentina (Glofy)", {"modalidad": "remoto", "zona": "otra",
                                  "ubicacion": "Remoto restringido a otro país",
                                  "enlace": GOB + "x-glofy-remote"}, False),
    ("remoto USA", {"modalidad": "remoto", "zona": "otra",
                    "ubicacion": "Remoto - USA"}, False),
    ("híbrido Santiago", {"modalidad": "hibrido", "zona": "chile",
                          "ubicacion": "Híbrido - Santiago, Chile",
                          "enlace": GOB + "growth-42labs-santiago"}, True),
    ("híbrido con acentos/mayúsculas", {"modalidad": "Híbrido", "zona": "Chile",
                                        "ubicacion": "Santiago, Chile (híbrido)"}, True),
    ("híbrido Región Metropolitana", {"modalidad": "hibrido", "zona": "chile",
                                      "ubicacion": "Híbrido - Región Metropolitana"}, True),
    ("híbrido Valparaíso", {"modalidad": "hibrido", "zona": "chile",
                            "ubicacion": "Híbrido - Valparaíso, Chile",
                            "enlace": GOB + "x-valparaiso"}, False),
    ("híbrido México (Colectivo23)", {"modalidad": "hibrido", "zona": "otra",
                                      "ubicacion": "Híbrido - Mexico",
                                      "enlace": GOB + "x-colectivo23-ciudad-de-mexico-942e"}, False),
    ("presencial Santiago", {"modalidad": "presencial", "zona": "chile",
                             "ubicacion": "Presencial - Santiago, Chile"}, False),
    ("desconocida", {"modalidad": "desconocida", "zona": "otra",
                     "ubicacion": "No indicada"}, False),
    ("sin campos (correo antiguo)", {"ubicacion": "No indicada",
                                     "enlace": "https://x.test/1"}, False),
    ("inglés: Remote / Worldwide", {"modalidad": "Remote", "zona": "Worldwide"}, True),
    ("zona 'Remoto / Global'", {"modalidad": "remoto", "zona": "Remoto / Global"}, True),
]

CASOS_GOB = [
    # (nombre, attributes, slug, texto esperado)
    ("fully_remote", {"remote_modality": "fully_remote", "countries": ["Remote"]},
     "x-remote", "Remoto global (cualquier país)"),
    ("remote_local Chile (Patrimore)",
     {"remote_modality": "remote_local", "countries": ["Remote"],
      "location_tenants": {"data": [{"id": 1, "type": "location_tenant"}]}},
     "x-patrimore-remote", "Remoto, solo para residentes en Chile"),
    ("remote_local Chile y otros",
     {"remote_modality": "remote_local", "countries": ["Remote"],
      "location_tenants": {"data": [{"id": 1}, {"id": 7}, {"id": 2}]}},
     "x-remote", "Remoto, solo para residentes en Chile y otros países"),
    ("remote_local Argentina (Glofy)",
     {"remote_modality": "remote_local", "countries": ["Remote"],
      "location_tenants": {"data": [{"id": 2, "type": "location_tenant"}]}},
     "marketing-operations-coordinator-direct-mail-glofy-remote",
     "Remoto restringido a otro país (NO admite postulantes desde Chile)"),
    ("hybrid Santiago por ciudad",
     {"remote_modality": "hybrid", "countries": ["Chile"],
      "location_cities": {"data": [{"id": 1, "type": "location_city"}]}},
     "x-42labs-abc1", "Híbrido - Santiago, Chile"),
    ("hybrid Santiago por slug",
     {"remote_modality": "hybrid", "countries": ["Chile"]},
     "growth-42labs-santiago", "Híbrido - Santiago, Chile"),
    ("hybrid México (Colectivo23)",
     {"remote_modality": "hybrid", "countries": ["Mexico"],
      "location_cities": {"data": [{"id": 430}]}},
     "product-marketing-specialist-colectivo23-ciudad-de-mexico-942e", "Híbrido - Mexico"),
    ("hybrid Chile sin ciudad",
     {"remote_modality": "hybrid", "countries": ["Chile"]},
     "x-valparaiso", "Híbrido - Chile"),
    ("no_remote Lima",
     {"remote_modality": "no_remote", "countries": ["Peru"]},
     "x-lima", "Presencial - Peru"),
    ("sin modalidad, remote=True",
     {"remote": True, "countries": ["Remote"]}, "x", "Remoto (alcance no indicado)"),
    ("sin nada", {}, "x", ""),
]

fallos = 0
print("== _oferta_permitida ==")
for nombre, oferta, esperado in CASOS_GATE:
    oferta = {"cargo": "Puesto", "empresa": "Empresa", **oferta}
    ok, motivo = b._oferta_permitida(oferta)
    estado = "OK " if ok == esperado else "FALLO"
    fallos += ok != esperado
    print(f"  {estado} {nombre}: permitida={ok} {motivo}")

print("\n== _ubicacion_getonboard ==")
for nombre, attrs, slug, esperado in CASOS_GOB:
    obtenido = b._ubicacion_getonboard(attrs, slug)
    estado = "OK " if obtenido == esperado else "FALLO"
    fallos += obtenido != esperado
    print(f"  {estado} {nombre}: {obtenido!r}")

print("\n== _deducir_pais ==")
for texto, esperado in [("Remoto - LATAM, Europe", "Remoto / Global"),
                        ("Remoto - South America", "Remoto / Global"),
                        ("Híbrido - Santiago, Chile", "Chile")]:
    obtenido = b._deducir_pais(texto)
    fallos += obtenido != esperado
    print(f"  {'OK ' if obtenido == esperado else 'FALLO'} {texto!r} -> {obtenido}")

if "--gob" in sys.argv:
    print("\n== Get on Board en vivo ==")
    texto = b.fuente_getonboard()
    lineas = [l for l in texto.splitlines() if l.startswith("UBICACION:")]
    print(f"  {len(lineas)} vacantes; 'No indicada': "
          f"{sum('No indicada' in l for l in lineas)}")
    from collections import Counter
    for ubic, n in Counter(lineas).most_common(12):
        print(f"  {n:3d}x {ubic}")

print()
assert fallos == 0, f"{fallos} caso(s) fallaron"
print("OK: filtro geográfico correcto.")
