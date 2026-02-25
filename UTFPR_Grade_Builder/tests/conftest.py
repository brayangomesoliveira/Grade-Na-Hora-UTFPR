from pathlib import Path
import sys

# Permite imports a partir da raiz do repositório sem instalar pacote.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
