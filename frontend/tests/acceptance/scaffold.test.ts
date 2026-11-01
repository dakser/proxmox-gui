// Andamiaje: demuestra que vitest recoge tests/acceptance/** con la configuración real. Regla: ninguna (infraestructura).
import { describe, expect, it } from 'vitest';

describe('acceptance scaffold', () => {
  it('scaffold_frontend_runs', () => {
    expect(1 + 1).toBe(2);
  });
});
