# liquidador-sueldos-argentina
Programa para liquidar sueldos de empleados de comercio, gastronomía y encargados de edificios (FATERYH) en Argentina.

**Estado:** por ahora solo Comercio (CCT 130/75).

## Cómo correrlo

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
flask run            # http://127.0.0.1:5000
pytest -q            # tests
python scripts/generar_ejemplo.py   # genera ejemplos/recibo_ejemplo.pdf
```

## Flujo

1. `GET /escalas/plantilla` baja la plantilla Excel (hoja `Escala`: Categoría | Monto | Vigencia desde).
2. Se completa con los básicos del acuerdo y se sube con `POST /escalas/importar` (campo `archivo`).
   Si alguna fila tiene errores no se carga nada y se devuelve el detalle por fila.
   Para un acuerdo nuevo se agregan filas con la nueva vigencia; el historial queda.
3. `POST /empresas` y `POST /empleados` (JSON).
4. `POST /liquidaciones` con `empleado_id`, `periodo` (AAAA-MM), `inasistencias_injustificadas`,
   `fecha_pago`, `lugar_pago` y los datos del último depósito de aportes
   (`ultimo_deposito_periodo`, `ultimo_deposito_fecha`, `ultimo_deposito_banco`).
   Opcional: `tope_base_imponible`.
5. `GET /liquidaciones/<id>/recibo.pdf` devuelve el recibo (original + duplicado).

## Qué calcula (Comercio)

| Concepto | Regla |
|---|---|
| Básico | Escala de la categoría con la vigencia más reciente al 1° del período |
| Antigüedad | 1% del básico por año cumplido al último día del período |
| Presentismo | 8,33% sobre básico + antigüedad; se pierde con cualquier inasistencia injustificada |
| Inasistencias | (básico + antigüedad) / 30 por día injustificado |
| Jubilación / Ley 19.032 / Obra social | 11% / 3% / 3% del remunerativo (con tope si se informa) |
| FAECYS | 0,5% del remunerativo |
| Cuota sindical | 2% del remunerativo, solo afiliados |

El recibo incluye los datos que exige el art. 140 LCT (empleador, trabajador, categoría,
fecha de ingreso, determinación de cada concepto, último depósito de aportes, totales,
neto en números y letras, lugar y fecha de pago y constancia de recepción del duplicado).

## Pendiente

- Sumas no remunerativas de los acuerdos paritarios.
- Jornada parcial, horas extra, SAC, vacaciones y licencias.
- Retención de ganancias.
- Lectura de escalas directamente desde el PDF de FAECYS.
- Gastronomía y FATERYH.
