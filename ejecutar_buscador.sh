#!/bin/bash
# Lanzador del buscador de empleos.
# Se ubica solo: no hay que editar rutas aquí dentro.

cd "$(dirname "$0")" || exit 1

# Rutas donde macOS y Homebrew guardan python3
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

SCRIPT="buscar_empleo_V9.py"
LOG="registro_ejecuciones.log"

# Conserva solo las últimas 2000 líneas para que el log no crezca sin control
if [ -f "$LOG" ] && [ "$(wc -l < "$LOG")" -gt 2000 ]; then
    tail -n 1000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

echo "" >> "$LOG"
echo "=====================================================" >> "$LOG"
echo "Inicio: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG"
echo "=====================================================" >> "$LOG"

# Las librerías de este Python (Pillow, numpy, etc.) están instaladas en x86_64,
# así que se ejecuta bajo Rosetta con "arch -x86_64". Sin esto falla al importar ReportLab.
# caffeinate -i evita que el Mac entre en reposo por inactividad mientras corre el script
# (no impide el reposo por tapa cerrada: por eso el job corre a las 10:00, con el Mac en uso).
/usr/bin/caffeinate -i /usr/bin/arch -x86_64 /Library/Frameworks/Python.framework/Versions/3.10/bin/python3 -u "$SCRIPT" >> "$LOG" 2>&1
CODIGO=$?

echo "Fin: $(date '+%Y-%m-%d %H:%M:%S') (código $CODIGO)" >> "$LOG"
exit $CODIGO
