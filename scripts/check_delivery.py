"""Current email, navigation and isolated OIDC browser acceptance on PostgreSQL."""
import runpy
from pathlib import Path
runpy.run_path(str(Path(__file__).with_name("browser_final_release.py")),run_name="__main__")
