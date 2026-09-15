# -*- coding: utf-8 -*-
"""
Diagnóstico: muestra QUÉ material está recibiendo el modelo.
No llama al modelo, no envía correos, no escribe en Airtable. Solo lee y reporta.

Uso:  python3 diagnostico.py
"""

import os
import sys
import importlib

# Encuentra el script principal aunque se llame V3, V4, etc.
CANDIDATOS = ["buscar_empleo_V9", "buscar_empleo_V4", "buscar_empleo_V3", "buscar_empleo"]
modulo = None
for nombre in CANDIDATOS:
    if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   nombre + ".py")):
        modulo = importlib.import_module(nombre)
        print(f"Usando la configuración de {nombre}.py\n")
        break

if modulo is None:
    print("No encontré el script principal en esta carpeta.")
    sys.exit(1)

print("=" * 60)
print("1. CONFIGURACIÓN")
print("=" * 60)
for clave in ["EMAIL_USUARIO", "EMAIL_PASSWORD", "DEEPINFRA_API_KEY",
              "AIRTABLE_TOKEN", "WEBHOOK_URL", "WEBHOOK_TOKEN"]:
    valor = getattr(modulo, clave, "")
    if not valor:
        estado = "VACÍO  <-- revisar"
    elif clave in ("EMAIL_USUARIO", "WEBHOOK_URL"):
        estado = valor
    else:
        estado = f"cargado ({len(valor)} caracteres)"
    print(f"  {clave:<16} {estado}")

print()
print("=" * 60)
print("2. LECTURA DE CORREOS")
print("=" * 60)
try:
    correos = modulo.leer_correos_hoy()
except Exception as e:
    print(f"  Falló la conexión a Gmail: {e}")
    correos = ""

print(f"  Caracteres leídos: {len(correos)}")
print(f"  Correos capturados: {correos.count('--- CORREO:')}")
print("\n  Asuntos encontrados:")
asuntos = [l for l in correos.splitlines() if l.startswith("--- CORREO:")]
if asuntos:
    for a in asuntos:
        print(f"    · {a.replace('--- CORREO: ', '')[:110]}")
else:
    print("    (ninguno) <-- aquí está el problema si aparece vacío")

print()
print("=" * 60)
print("3. REPERTORIO WEB")
print("=" * 60)
web = modulo.leer_repertorio_web()
print(f"  Caracteres recibidos: {len(web)}")

total = correos + "\n" + web
ruta = os.path.join(modulo.BASE_DIR, "ultimas_ofertas_crudas.txt")
with open(ruta, "w", encoding="utf-8") as f:
    f.write(total)

print()
print("=" * 60)
print("RESULTADO")
print("=" * 60)
print(f"  Total que se le manda al modelo: {len(total)} caracteres")
print(f"  Guardado en: {ruta}")
print()
if len(total.strip()) < 500:
    print("  Hay muy poco material. El problema NO son los filtros del modelo,")
    print("  sino que no están llegando ofertas al correo o no se están leyendo.")
else:
    print("  Hay material suficiente. Abre el archivo de texto y revisa si")
    print("  adentro hay vacantes reales o solo correos promocionales.")
