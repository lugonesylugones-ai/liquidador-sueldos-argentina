# liquidador-sueldos-argentina
Programa para liquidar sueldos de empleados de comercio, gastronomía y encargados de edificios (FATERYH) en Argentina.

**Estado:** maneja varias empresas, cada una con sus empleados y cada empleado con su convenio.
Liquida Comercio (CCT 130/75: sueldo, aguinaldo y liquidación final) y encargados de edificio
(CCT 589/10: sueldo, zona fría y aguinaldo, validado contra recibos reales de septiembre 2026).

## Cómo correrlo

**Lo más fácil:** doble clic en `iniciar.bat` (Windows) o `./iniciar.sh` (Mac/Linux).
La primera vez crea el entorno e instala lo necesario (hace falta Python 3.10 o más nuevo).
Después abre el navegador en http://127.0.0.1:5000. La ventana tiene que quedar abierta
mientras lo usás. Los datos se guardan en `liquidador_sueldos.db` dentro de la misma
carpeta: si bajás una versión nueva, copiá ese archivo a la carpeta nueva.

A mano:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
flask --app backend.app:create_app run   # abrir http://127.0.0.1:5000
pytest -q            # tests
python scripts/generar_ejemplo.py   # genera los archivos de ejemplos/
```

## Usarlo desde el navegador

Con el servidor andando, entrá a http://127.0.0.1:5000:

1. **Empresas**: das de alta la empresa (razón social, CUIT, domicilio, lugar de pago).
2. **Empleados**: desde la página de la empresa, de a uno o subiendo la planilla Excel.
   Ahí mismo se editan y se carga la fecha de egreso.
3. **Liquidar**: elegís período y si es sueldo o aguinaldo. El formulario trae los
   datos del último depósito usado y un casillero de faltas sin justificar por empleado.
4. **Recibos**: la pantalla del período muestra los totales y baja el PDF de todos juntos
   o de cada uno.
5. **Liquidación final**: desde el empleado, botón "Liq. final". Cargás fecha de egreso, causa
   y si hubo preaviso; reemplaza al sueldo y al aguinaldo de ese mes.
6. **Escalas**: muestra las de cada convenio (con "Sin verificar" / "Verificada") y sube nuevas.
7. **Sindicatos**: alta de convenios con sus categorías. Los que no tienen motor de cálculo se
   pueden asignar y cargar escalas, pero no se liquidan hasta programar sus reglas.
8. **Consorcios (encargados de edificio)**: cada consorcio es una empresa. Al cargar un empleado
   con el convenio CCT 589/10 aparece la sección "Edificio" (categoría 1 a 4, unidades funcionales,
   zona fría con su base y si va en recibo aparte) y en el empleado se marcan afiliación, retiro de residuos, tareas y título.
   Al liquidar se cargan las horas extra de cada uno.

Está pensado para usarlo en tu propia compu: no tiene usuarios ni contraseña, así que
no lo publiques en internet tal como está.

## Datos iniciales

Una base nueva arranca con el convenio de Comercio, sus 21 categorías y las escalas de
julio a septiembre 2026 (`backend/datos_iniciales.py`). **Esas escalas salen de Ignacio
Online, no de la circular de FAECYS**, y quedan marcadas como no verificadas
(la pantalla de escalas y `GET /api/escalas` muestran la fuente y si está verificada). Solo Auxiliar B está contrastada contra
recibos reales. Las escalas que se importan desde Excel quedan con la fuente del archivo.

## API

Todo lo de la web también se puede hacer por API JSON, bajo `/api`.

1. **Escalas.** `GET /api/escalas/plantilla` baja la plantilla (hoja `Escala`: Categoría | Monto |
   Vigencia desde | No remunerativo | Asig. única vez) y `POST /api/escalas/importar` la sube.
   Montos de jornada completa, tal como vienen en la circular. Acepta los nombres publicados
   ("Personal Auxiliar B", "Vendedores A"). Si una fila tiene errores no se carga nada.
   La asignación de única vez se paga solo en el mes exacto de su vigencia.
2. **Empresas.** `POST /api/empresas` (razón social, CUIT validado, domicilio y lugar de pago
   por defecto). `GET /api/empresas` lista todas con sus empleados activos.
3. **Empleados.** Uno por uno con `POST /api/empleados`, o todos juntos: `GET /api/empleados/plantilla`
   (Legajo | Apellido | Nombre | CUIL | Convenio | Categoría | Fecha ingreso | Jornada (hs) |
   Fecha egreso) y `POST /api/empresas/<id>/empleados/importar`. Valida CUIL y categoría; si una
   fila falla no se carga nada. Reimportar actualiza por CUIL. Un mismo CUIL puede estar en
   varias empresas. `GET /api/empresas/<id>/empleados` los lista.
4. **Liquidar el mes.** `POST /api/empresas/<id>/liquidaciones` liquida a todos los activos del
   período, o `POST /api/liquidaciones` a uno solo (`empleado_id`). Datos: `periodo` (AAAA-MM),
   `fecha_pago`, `lugar_pago` (si no, el de la empresa), `ultimo_deposito_periodo`,
   `ultimo_deposito_fecha` y `ultimo_deposito_banco`. Opcionales: `inasistencias_injustificadas`,
   `asignacion_extraordinaria` y `tope_base_imponible`.
5. **Aguinaldo.** `POST /api/empresas/<id>/sac` o `POST /api/liquidaciones/sac` con `periodo` 2026-06 o
   2026-12 (o el mes del egreso). Usa las liquidaciones mensuales ya guardadas del semestre,
   así que primero hay que liquidar los meses.
6. **Recibos.** `GET /api/liquidaciones/<id>/recibo.pdf` (uno) o
   `GET /api/empresas/<id>/recibos/<AAAA-MM>.pdf` (todos los del período; `?tipo=sac` para el SAC).

Convenios sin motor (`POST /api/convenios` con su lista de categorías) se pueden dar de alta y
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
| Mes incompleto | Mes de ingreso o egreso: sueldo y no remunerativo × días / 30 |

### Liquidación final

| Concepto | Regla |
|---|---|
| Días del mes | Como un mes incompleto, con sus aportes |
| SAC proporcional | Como el SAC, hasta la fecha de egreso |
| Vacaciones no gozadas | Días del art. 150 (14/21/28/35 según antigüedad al 31/12) × días trabajados en el año / 365, menos los ya tomados; valor día = remuneración mensual / 25, también sobre el no remunerativo; más su SAC (1/12) |
| Indemnización art. 245 | Despido sin causa: mejor remuneración mensual normal y habitual del último año (con no remunerativo, sin SAC) × años (fracción > 3 meses = 1 año), mínimo un mes. Con tope opcional y piso del 67% (Vizzoti). Fallecimiento: 50% (art. 248). Nada en período de prueba (6 meses si ingresó desde el 9/7/2024, si no 3) |
| Preaviso art. 231/232 | Despido sin causa sin preaviso: 15 días (prueba), 1 mes (< 5 años) o 2 meses, más su SAC |
| Integración art. 233 | Despido sin causa sin preaviso: días que faltan para fin de mes / 30, más su SAC |
| Aportes | Indemnizaciones, preaviso, integración y vacaciones no gozadas sin aportes ni cuota sindical |

`tests/test_recibos_reales.py` reproduce al centavo ocho recibos reales de julio a septiembre 2026.
`ejemplos/` trae la escala jul-sep 2026 y la planilla de empleados (ficticios) listas para
importar, más los recibos de septiembre y un SAC proporcional por egreso.

El recibo incluye los datos que exige el art. 140 LCT (empleador, trabajador, categoría,
fecha de ingreso, determinación de cada concepto, último depósito de aportes, totales,
neto en números y letras, lugar y fecha de pago y constancia de recepción del duplicado).

## Qué calcula (encargados de edificio, CCT 589/10)

Validado al centavo contra recibos reales de septiembre 2026 de consorcios de Bahía Blanca
(11 de 15; las otras diferencias están explicadas en el PR #3). Escalas jul-sep 2026 de las
planillas de SUTERH (`backend/datos/escalas_suteryh_2026_jul_sep.json`), marcadas sin verificar.

| Concepto | Regla |
|---|---|
| Básico | Escala del cargo según la categoría del edificio (art. 6) |
| Suma fija remunerativa | La de la planilla; 50% en jornada reducida (media jornada y encargado no permanente) |
| Antigüedad | Monto fijo por año: 2% del ayudante sin vivienda de 4ª; la mitad en jornada reducida (art. 11) |
| Vivienda | No se liquida (los recibos no la traen); queda como opción del motor |
| Residuos, tareas, título | Retiro por UF, plus por tarea fijos, título 5% por tramo |
| Horas extra | 50% y 100%; valor hora = (básico + antigüedad + residuos + tareas, sin suma fija ni viáticos) / 200, o / 100 en jornada reducida |
| Zona fría | 50%, por consorcio: sobre todo lo remunerativo o sobre básico + antigüedad, en el mismo recibo o en un recibo aparte con sus propios aportes y redondeo. La del recibo aparte tiene su propio SAC (mejor zona del semestre / 2), en otro recibo, como la hoja SAC de la planilla de los consorcios |
| Aportes | Jubilación 11%, PAMI 3%, obra social 3%, Caja Protección Familia 1%, FMVDD 1%, seguro art. 27 bis 0,75%, cuota sindical 2% solo afiliados |
| Redondeo | El neto se redondea para arriba al peso |
| Contribuciones del convenio | Se muestran aparte (no van en el recibo): CAPAF 4%, FMVDD 1,5%, seguro 0,75%, SERACARH 0,5% |
| SAC | Régimen general con los aportes del convenio |
| Sin hacer | Liquidación final (vacaciones en días hábiles), faltas, suplentes y jornalizados |

## Descuentos varios, cierre de período y copias

- **Descuentos fijos por empleado** (mutual, embargo, préstamo, otro): importe por mes, o porcentaje del
  bruto menos un mínimo no embargable, con "hasta" para las cuotas. Los importes van en el sueldo; los
  porcentajes en cada recibo (el mínimo solo en el sueldo). **Anticipo** por empleado al liquidar el mes.
  Se restan antes de redondear el neto, como en los recibos reales (un embargo y dos mutuales de 09/2026 dan
  igual al centavo).
- **Cerrar período**: en la empresa, una vez presentado el F.931. Cerrado no se puede volver a liquidar
  (sueldo, SAC ni final) hasta reabrirlo; se siguen bajando los recibos y el archivo de ARCA.
- **Copias de seguridad**: una por día en la carpeta `copias/` al lado de la base (quedan las últimas 30;
  `CARPETA_COPIAS` la cambia), y "Bajar copia ahora" en el menú para guardarla en otro lado.

## ARCA: Libro de Sueldos Digital y F.931

El F.931 se arma desde el Libro de Sueldos Digital (LSD) de ARCA, importando dos archivos de texto
con los diseños oficiales ("Diseño de interfaz - conceptos" y "Diseño de interfaz - liquidación",
[micrositio LSD](https://www.arca.gob.ar/LibrodeSueldosDigital), Guía 15):

1. **Conceptos, una sola vez por empleador.** En la empresa, link "conceptos para ARCA"
   (`GET /api/empresas/<id>/arca/conceptos.txt`). Se sube en LSD > Parametrización de conceptos.
   Relaciona cada concepto del liquidador con uno de ARCA y marca a qué bases suma
   (`backend/arca.py`, tabla `CONCEPTOS`).
2. **Liquidación, cada mes.** En la pantalla del período, botón "Archivo para ARCA / F.931"
   (`GET /api/empresas/<id>/arca/<AAAA-MM>.txt`). Se sube en LSD > Liquidaciones > Importar desde archivo.
   Trae un registro '01', y por trabajador un '02', un '03' por concepto y un '04' con las bases
   imponibles 1 a 10. Junta todos los recibos del mes de cada trabajador (sueldo, zona fría aparte,
   SAC, liquidación final).

| Dato | De dónde sale |
|---|---|
| Tipo de empleador, actividad, zona | Por empresa, sección "ARCA" (por defecto 1 = Dec. 814/01 art. 2 inc. b, 049, 04 = resto de Buenos Aires) |
| Obra social, cónyuge, hijos, CBU | Por empleado (obra social por defecto: OSECAC 126205 en Comercio, OSPERYH 106401 en edificios). Con CBU la forma de pago es acreditación en cuenta |
| Modalidad | 008 jornada completa, 001 jornada parcial |
| Bases 1, 2, 3, 5 | Remunerativo |
| Bases 4 y 8 (obra social) | Remunerativo + no remunerativo de Comercio; en jornada parcial de Comercio, a jornada completa |
| Base 9 (ART) | Remunerativo + no remunerativo, sin redondeo |
| Base 10 | Base 3 menos la detracción del art. 22 Ley 27.541 ($ 7.003,68 por trabajador, proporcional en jornada parcial) |
| SAC | Junio y diciembre como SAC; en otro mes o en la liquidación final como SAC proporcional (120003) con sus días |

Con el liquidado de septiembre 2026 de Comercio (3 empleados), las bases dan igual que el F.931 real
salvo las de obra social, que difieren en $ 0,34. No calcula topes de base imponible: si un sueldo pasa
el tope de ANSES, ARCA lo aplica al importar.

## Pendiente

- Horas extra, vacaciones, licencias y feriados (Día del Empleado de Comercio).
- Contribuciones patronales y costo empleador.
- Liquidación final: validar contra un recibo real (hoy sigue la LCT, no se contrastó con un caso real).
- Encargados de edificio: liquidación final; validar el aguinaldo contra un recibo real.
- Motor de cálculo para otros convenios (gastronómicos, etc.).
- Retención de ganancias.
- Lectura de escalas directamente desde el PDF de FAECYS.
- Gastronomía y FATERYH.
