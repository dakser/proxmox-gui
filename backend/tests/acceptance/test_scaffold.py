"""Andamiaje: demuestra que el directorio de aceptación se recoge con la configuración real de pytest.

Material del autor de pruebas / humano: el implementador no lo modifica. Regla: ninguna (infraestructura).
"""


def test_scaffold_backend_runs() -> None:
    assert (1 + 1) == 2
