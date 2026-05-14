"""jarvis-mcp — MCP server que envuelve la API del orquestador JARVIS.

Pensado para Claude Desktop: el usuario dice 'JARVIS, X' y Claude llama a
`ask_jarvis(X)`, este server invoca el orquestador HTTP y devuelve la
respuesta. Modo stdio, sin servidor HTTP propio.
"""
