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

1. `GET /escalas/plantilla` baja la plantilla Excel (hoja `Escala`: Categoría | Monto | Vigencia desde | No remunerativo | Asig. única vez).
   Se cargan los montos de jornada completa tal como vienen en la circular de FAECYS: básico y
   "aumento no remunerativo" por categoría y por mes. Las dos últimas columnas son opcionales;
   la asignación de única vez se paga solo en el mes exacto de su vigencia.
2. Se sube con `POST /escalas/importar` (campo `archivo`).
   Si alguna fila tiene errores no se carga nada y se devuelve el detalle por fila.
   Para un acuerdo nuevo se agregan filas con la nueva vigencia; el historial queda.
3. `POST /empresas` y `POST /empleados` (JSON). El empleado lleva `jornada_horas` (1 a 8, default 8).
4. `POST /liquidaciones` con `empleado_id`, `periodo` (AAAA-MM), `inasistencias_injustificadas`,
   `fecha_pago`, `lugar_pago` y los datos del último depósito de aportes
   (`ultimo_deposito_periodo`, `ultimo_deposito_fecha`, `ultimo_deposito_banco`).
   Opcionales: `asignacion_extraordinaria` (pisa la de la escala; monto de jornada completa) y
   `tope_base_imponible`.
5. `GET /liquidaciones/<id>/recibo.pdf` devuelve el recibo (original + duplicado).

## Qué calcula (Comercio)

| Concepto | Regla |
|---|---|
| Básico | Escala de la categoría con la vigencia más reciente al 1° del período, × horas/8 |
| No remunerativo | Suma no remunerativa de la misma fila de escala, × horas/8 |
| Antigüedad | 1% por año cumplido al último día del período, sobre básico y sobre no remunerativo |
| Presentismo | 8,33% sobre (básico + antig.) y sobre (no rem. + antig.); se pierde con cualquier falta injustificada |
| Inasistencias | 1/30 por día injustificado de cada bloque |
| Asignación de única vez | De la escala (solo en su mes) o informada; no remunerativa, × horas/8, sin antigüedad ni presentismo |
| Jubilación / Ley 19.032 | 11% / 3% del remunerativo (con tope si se informa) |
| Obra social / Art. 100 / Art. 101 / FAECYS | 3% / 2% / 2% / 0,5% del remunerativo + no remunerativo |
| Jornada parcial | Obra social sobre el equivalente a jornada completa y "Compl. Art. 101" |
| Redondeo | El neto se redondea para arriba al peso; la diferencia va como no remunerativo |

`tests/test_recibos_reales.py` reproduce al centavo ocho recibos reales de julio a septiembre 2026.
`ejemplos/escala_comercio_2026_jul_sep.xlsx` trae las escalas de julio a septiembre 2026
(fuente: Ignacio Online, no la circular de FAECYS) listas para importar.

El recibo incluye los datos que exige el art. 140 LCT (empleador, trabajador, categoría,
fecha de ingreso, determinación de cada concepto, último depósito de aportes, totales,
neto en números y letras, lugar y fecha de pago y constancia de recepción del duplicado).

## Pendiente

- Horas extra, SAC, vacaciones, licencias y feriados (Día del Empleado de Comercio).
- Retención de ganancias.
- Lectura de escalas directamente desde el PDF de FAECYS.
- Gastronomía y FATERYH.
