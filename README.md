# liquidador-sueldos-argentina
Programa para liquidar sueldos de empleados de comercio, gastronomía y encargados de edificios (FATERYH) en Argentina.

**Estado:** maneja varias empresas, cada una con sus empleados y cada empleado con su convenio.
El único convenio con motor de cálculo es Comercio (CCT 130/75): sueldo mensual y aguinaldo.

## Cómo correrlo

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
flask run            # http://127.0.0.1:5000
pytest -q            # tests
python scripts/generar_ejemplo.py   # genera los archivos de ejemplos/
```

## Datos iniciales

Una base nueva arranca con el convenio de Comercio, sus 21 categorías y las escalas de
julio a septiembre 2026 (`backend/datos_iniciales.py`). **Esas escalas salen de Ignacio
Online, no de la circular de FAECYS**, y quedan marcadas como no verificadas
(`GET /escalas` muestra `fuente` y `verificada`). Solo Auxiliar B está contrastada contra
recibos reales. Las escalas que se importan desde Excel quedan con la fuente del archivo.

## Flujo

1. **Escalas.** `GET /escalas/plantilla` baja la plantilla (hoja `Escala`: Categoría | Monto |
   Vigencia desde | No remunerativo | Asig. única vez) y `POST /escalas/importar` la sube.
   Montos de jornada completa, tal como vienen en la circular. Acepta los nombres publicados
   ("Personal Auxiliar B", "Vendedores A"). Si una fila tiene errores no se carga nada.
   La asignación de única vez se paga solo en el mes exacto de su vigencia.
2. **Empresas.** `POST /empresas` (razón social, CUIT validado, domicilio y lugar de pago
   por defecto). `GET /empresas` lista todas con sus empleados activos.
3. **Empleados.** Uno por uno con `POST /empleados`, o todos juntos: `GET /empleados/plantilla`
   (Legajo | Apellido | Nombre | CUIL | Convenio | Categoría | Fecha ingreso | Jornada (hs) |
   Fecha egreso) y `POST /empresas/<id>/empleados/importar`. Valida CUIL y categoría; si una
   fila falla no se carga nada. Reimportar actualiza por CUIL. Un mismo CUIL puede estar en
   varias empresas. `GET /empresas/<id>/empleados` los lista.
4. **Liquidar el mes.** `POST /empresas/<id>/liquidaciones` liquida a todos los activos del
   período, o `POST /liquidaciones` a uno solo (`empleado_id`). Datos: `periodo` (AAAA-MM),
   `fecha_pago`, `lugar_pago` (si no, el de la empresa), `ultimo_deposito_periodo`,
   `ultimo_deposito_fecha` y `ultimo_deposito_banco`. Opcionales: `inasistencias_injustificadas`,
   `asignacion_extraordinaria` y `tope_base_imponible`.
5. **Aguinaldo.** `POST /empresas/<id>/sac` o `POST /liquidaciones/sac` con `periodo` 2026-06 o
   2026-12 (o el mes del egreso). Usa las liquidaciones mensuales ya guardadas del semestre,
   así que primero hay que liquidar los meses.
6. **Recibos.** `GET /liquidaciones/<id>/recibo.pdf` (uno) o
   `GET /empresas/<id>/recibos/<AAAA-MM>.pdf` (todos los del período; `?tipo=sac` para el SAC).

Convenios sin motor (`POST /convenios` con su lista de categorías) se pueden dar de alta y
asignar a empleados, pero liquidarlos devuelve error hasta que se programe su cálculo.

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
| SAC | 50% de la mejor remuneración mensual del semestre × días trabajados / días del semestre; la parte no remunerativa habitual igual, como "SAC s/ no remunerativo" (sin la asignación de única vez). Mismos aportes que el sueldo |

`tests/test_recibos_reales.py` reproduce al centavo ocho recibos reales de julio a septiembre 2026.
`ejemplos/` trae la escala jul-sep 2026 y la planilla de empleados (ficticios) listas para
importar, más los recibos de septiembre y un SAC proporcional por egreso.

El recibo incluye los datos que exige el art. 140 LCT (empleador, trabajador, categoría,
fecha de ingreso, determinación de cada concepto, último depósito de aportes, totales,
neto en números y letras, lugar y fecha de pago y constancia de recepción del duplicado).

## Pendiente

- Horas extra, vacaciones, licencias y feriados (Día del Empleado de Comercio).
- Contribuciones patronales y costo empleador.
- Retención de ganancias.
- Lectura de escalas directamente desde el PDF de FAECYS.
- Gastronomía y FATERYH.
