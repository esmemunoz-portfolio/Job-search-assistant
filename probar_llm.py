# -*- coding: utf-8 -*-
"""
Prueba mínima del modelo: UNA llamada real a DeepInfra con un CV corto y 3 ofertas
ficticias (dos válidas y una solo para USA, que debe quedar fuera).
No manda correos, no toca Airtable, no genera PDFs.

Uso:  arch -x86_64 /Library/Frameworks/Python.framework/Versions/3.10/bin/python3 probar_llm.py
      (bajo Rosetta, igual que ejecutar_buscador.sh, porque las librerías son x86_64)
"""
import json
import buscar_empleo_V9 as b

CV = """CANDIDATA DE PRUEBA – Marketing Manager
Growth Manager en Acme SpA (Ene 2022 – Presente): campañas B2B, +40% leads, HubSpot.
Marketing Analyst en Beta Corp (Mar 2019 – Dic 2021): reportes, CRM, análisis de datos.
Educación: Ingeniería Comercial, Universidad de Chile. Inglés avanzado."""

OFERTAS = """--- OFERTAS DE PRUEBA ---
PUESTO: Marketing Operations Manager (Remote, LATAM)
EMPRESA: Ejemplo Inc
UBICACION: Remoto - LATAM, Europe
SUELDO: USD 3.500
ENLACE: https://ejemplo.test/1
DESCRIPCION: We are looking for a Marketing Operations Manager to own our HubSpot workflows, lead scoring and campaign reporting for a B2B SaaS product. Fully remote, open to candidates across Latin America. Advanced English required.

PUESTO: Líder de Growth Marketing
EMPRESA: Ejemplo SpA
UBICACION: Híbrido - Santiago, Chile
SUELDO: CLP 2.800.000
ENLACE: https://ejemplo.test/2
DESCRIPCION: Buscamos a un/a líder de growth para diseñar campañas digitales, administrar el CRM y analizar datos de adquisición. Modalidad híbrida en Santiago, tres días en oficina.

PUESTO: Marketing Analyst
EMPRESA: Only US LLC
UBICACION: Remoto - USA
SUELDO: USD 5.000
ENLACE: https://ejemplo.test/3
DESCRIPCION: Must reside in the United States. HubSpot, SQL, dashboards.
"""

print(f"Modelo: {b.MODELO_LLM}  (respaldo: {b.MODELO_RESPALDO})")
datos = b.analizar_con_llm(CV, OFERTAS)
print(json.dumps(datos, ensure_ascii=False, indent=2)[:2500])

ofertas = datos.get("ofertas", [])
print(f"\nOfertas devueltas: {len(ofertas)}")
assert isinstance(ofertas, list) and ofertas, "El modelo no devolvió ofertas"
for campo in ("cargo", "empresa", "match", "cv", "modalidad", "zona"):
    assert campo in ofertas[0], f"Falta el campo '{campo}'"
for campo in ("idioma", "titular", "resumen", "competencias", "experiencia", "educacion"):
    assert campo in ofertas[0]["cv"], f"Falta cv.{campo}"

# Idioma: la oferta 1 está en inglés y la 2 en español; el CV debe seguir a cada una.
ESPERADO = {"https://ejemplo.test/1": "en", "https://ejemplo.test/2": "es"}
for o in ofertas:
    cv = o.get("cv") or {}
    idioma = b._idioma_cv(cv)
    esperado = ESPERADO.get(str(o.get("enlace", "")))
    print(f"  Idioma de {o.get('empresa')}: declarado={cv.get('idioma')!r} usado={idioma}"
          f"{' esperado=' + esperado if esperado else ''} | titular: {str(cv.get('titular', ''))[:70]}")
    assert str(cv.get("idioma", "")).lower()[:2] in ("es", "en"), f"cv.idioma inválido: {cv.get('idioma')!r}"
    if esperado:
        assert idioma == esperado, f"{o.get('empresa')}: CV en {idioma}, la oferta está en {esperado}"

for o in ofertas:
    modalidad, zona = b._etiqueta_llm(o, "modalidad"), b._etiqueta_llm(o, "zona")
    assert modalidad in ("remoto", "hibrido", "presencial", "desconocida"), f"modalidad rara: {o.get('modalidad')!r}"
    assert zona in ("global", "latam", "chile", "otra"), f"zona rara: {o.get('zona')!r}"
    permitida, motivo = b._oferta_permitida(o)
    print(f"  {o.get('cargo')} en {o.get('empresa')}: {modalidad}/{zona} -> "
          f"{'pasa' if permitida else 'omitida (' + motivo + ')'}")
    if "ejemplo.test/3" in str(o.get("enlace", "")):
        print("  AVISO: el modelo incluyó la oferta solo-USA; el filtro Python la omite igual.")
print("OK: estructura correcta.")
