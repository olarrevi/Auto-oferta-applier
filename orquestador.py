from pathlib import Path
import subprocess
import sys

CARPETA = Path(__file__).parent / "scripts"
scripts = sorted(CARPETA.glob("*.py"))   # orden alfabético. Cámbialo si necesitas otro

for script in scripts:
    print(f"→ Ejecutando {script.name}")
    completed = subprocess.run([sys.executable, script], check=True)
