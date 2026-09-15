# -*- coding: utf-8 -*-
"""
Prueba cada portal de empleos por separado y muestra ejemplos de lo que devuelve.
No usa Gemini, no manda correos, no toca Airtable.

Uso:  python3 probar_fuentes.py
"""

import os
import sys
import importlib

CANDIDATOS = ["buscar_empleo_V7", "buscar_empleo_V6", "buscar_empleo"]
modulo = None
for nombre in CANDIDATOS:
    if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   nombre + ".py")):
        modulo = importlib.import_module(nombre)
        print(f"Usando {nombre}.py\n")
        break

if modulo is None:
    print("No encontré el script principal en esta carpeta.")
    sys.exit(1)

total_general = 0
for etiqueta, funcion in modulo.FUENTES:
    print("=" * 64)
    print(etiqueta.upper())
    print("=" * 64)
    try:
        texto = funcion()
    except Exception as e:
        print(f"  ERROR: {e}\n")
        continue

    n = texto.count("PUESTO:")
    total_general += n
    print(f"  Vacantes obtenidas: {n}")

    if n:
        bloques = texto.split("\nPUESTO:")[1:4]
        print("\n  Ejemplos:")
        for b in bloques:
            lineas = ("PUESTO:" + b).strip().splitlines()
            for l in lineas[:5]:
                print(f"    {l[:100]}")
            print()
    else:
        print("  Esta fuente no devolvió nada. Puede estar caída o haber "
              "cambiado su dirección.\n")

print("=" * 64)
print(f"TOTAL DE VACANTES DISPONIBLES: {total_general}")
print("=" * 64)
if total_general == 0:
    print("Ninguna fuente respondió. Revisa tu conexión antes de seguir.")
elif total_general < 20:
    print("Hay pocas vacantes. Conviene agregar alertas por correo.")
else:
    print("Material suficiente. Ya puedes correr el script principal.")
