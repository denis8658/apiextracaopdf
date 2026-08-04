import os
import sys
from pathlib import Path


def reexec_with_project_python(module: str) -> None:
    """Use the repository's Python 3.12 environment for direct local commands."""
    project_root = Path(__file__).resolve().parents[2]
    executable_name = "python.exe" if os.name == "nt" else "python"
    scripts_dir = "Scripts" if os.name == "nt" else "bin"
    project_python = project_root / ".venv" / scripts_dir / executable_name

    if project_python.is_file() and Path(sys.executable).resolve() != project_python.resolve():
        print(
            f"Usando ambiente Python do projeto: {project_python}",
            file=sys.stderr,
            flush=True,
        )
        os.execv(
            str(project_python),
            [str(project_python), "-m", module, *sys.argv[1:]],
        )

    if sys.version_info[:2] != (3, 12):
        raise SystemExit(
            "Este projeto requer Python 3.12. Crie .venv com Python 3.12 "
            "ou execute .venv/Scripts/python.exe diretamente."
        )
