# -*- coding: utf-8 -*-
"""
Revisa el archivo 'ultimas_ofertas_crudas.txt' y resume qué hay adentro.
No conecta a nada. Uso:  python3 revisar_material.py
"""

import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))
RUTA = os.path.join(BASE, "ultimas_ofertas_crudas.txt")

if not os.path.exists(RUTA):
    print("No encontré 'ultimas_ofertas_crudas.txt' en esta carpeta.")
    raise SystemExit(1)

texto = open(RUTA, encoding="utf-8").read()
MARCA_WEB = "--- OFERTAS DESDE REPERTORIO WEB ---"

if MARCA_WEB in texto:
    correos, web = texto.split(MARCA_WEB, 1)
else:
    correos, web = texto, ""

print("=" * 64)
print("DE DÓNDE VIENE EL MATERIAL")
print("=" * 64)
print(f"  Correos:        {len(correos):>8} caracteres")
print(f"  Repertorio web: {len(web):>8} caracteres")
print()

asuntos = [l.replace("--- CORREO: ", "").strip()
           for l in correos.splitlines() if l.startswith("--- CORREO:")]
print(f"  Asuntos de correo capturados ({len(asuntos)}):")
for a in asuntos:
    print(f"    · {a[:100]}")
print()

enlaces = re.findall(r"https?://[^\s\)\"'<>]+", texto)
dominios = {}
for e in enlaces:
    d = re.sub(r"^https?://(www\.)?", "", e).split("/")[0]
    dominios[d] = dominios.get(d, 0) + 1

print("=" * 64)
print(f"ENLACES ENCONTRADOS: {len(enlaces)}  ({len(dominios)} dominios)")
print("=" * 64)
for d, n in sorted(dominios.items(), key=lambda x: -x[1])[:20]:
    print(f"  {n:>5}  {d}")
print()

pistas = ["remote", "remoto", "salary", "sueldo", "apply", "postula",
          "full-time", "analyst", "marketing", "data"]
print("=" * 64)
print("PALABRAS TÍPICAS DE VACANTES")
print("=" * 64)
bajo = texto.lower()
for p in pistas:
    print(f"  {bajo.count(p):>5}  {p}")
print()

print("=" * 64)
print("PRIMEROS 1500 CARACTERES DEL REPERTORIO WEB")
print("=" * 64)
print(web[:1500] if web else "  (el repertorio web vino vacío)")
print()

print("=" * 64)
print("PRIMEROS 1200 CARACTERES DE LOS CORREOS")
print("=" * 64)
print(correos[:1200] if correos.strip() else "  (no se capturó ningún correo)")
