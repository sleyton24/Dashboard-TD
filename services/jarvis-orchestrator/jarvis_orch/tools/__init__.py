"""Tools del orquestador JARVIS.

Cada tool se registra con `@tool(...)` desde `registry.py`. Importar este
paquete fuerza el side-effect del registro (los submódulos se cargan en
orden y poblan el registry global).
"""
from jarvis_orch.tools import registry  # noqa: F401 — re-export

# Side-effect imports: cargar los módulos hace que sus @tool() se registren.
from jarvis_orch.tools import attachments_tool  # noqa: F401
from jarvis_orch.tools import email_draft  # noqa: F401
from jarvis_orch.tools import sql_servers  # noqa: F401
