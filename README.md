# SportEdge Analytics V6 result_to_draw

Investigación reproducible y servicio HTTP desacoplado para el CSV etiquetado del 11 de octubre de 2026.

El campeón congelado es `soft_voting_054`. En validación alcanzó precisión 77,0% y recall 26,3%, pero la prueba temporal independiente dio **precisión 30,2%, recall 11,0% y F1 16,1%**. Ninguno de los ocho finalistas superó precisión >50% y recall >25% en prueba. El servicio funciona en modo de investigación, con `actionable_signal=false`.

## Definición y contrato

`result_to_draw` exige una igualación futura válida desde diferencia de goles ±1 y empate en el cierre observado del mismo `(id, market_id)`. El cierre es FT o, sin FT, el último registro sólo si el grupo alcanza minuto ≥90. La base no contiene capturas FT. Se conservan 115 etiquetas pendientes; no se entrenan como negativas.

El XLSX solicitado marca 183 predictores SI, pero su última fila define `home_score_to_increase`. Se usaron sus marcas de predictores; el target proviene del CSV y de la auditoría del XLSM específico de result_to_draw. [Protocolo](EXPERIMENT_PLAN.md), [diccionario de ingeniería](docs/feature_catalog.json) y [variables efectivas](docs/effective_model_variables.csv) documentan las decisiones.

## Evidencia

- 23.600 filas y 222 columnas fuente preservadas en el replay completo local.
- Tres ciclos con 136 ingenierías candidatas, MI, Spearman, proxy Extra Trees y control de redundancia dentro de entrenamiento.
- Siete familias, 40 configuraciones de estimador por cinco calibraciones: 200 versiones por familia, 1.400 versiones principales.
- Cinco folds con selección anidada, otras 1.400 versiones y 320 escenarios de soft voting. Búsqueda final de 64 ensambles.
- Partidos disjuntos, purga por disponibilidad de etiquetas y distribución real en calibración, validación y test. Balanceo sólo mediante pesos de entrenamiento.
- 4.582 snapshots en backtesting anidado de desarrollo y 1.826 snapshots de 117 partidos en prueba final. Calentamiento histórico sin predicciones futuras explícitamente identificado.
- Bootstrap por fixture, calibración antes/después, métricas por liga/cluster y revaluación del V4 activo sin ajustar su umbral.

Consulte [métricas](docs/metrics_comparison.csv), [selección congelada](docs/selection_frozen_before_test.json) y [resumen](docs/completion_summary.json). El agregado del replay de toda la base incluye entrenamiento y no estima generalización. Los artefactos de datos completos se guardan en `outputs/v6_result_to_draw_20261011/`, fuera de Git.

## Servicio desplegado

Contenedor `sportedge_v6_result_to_draw`, imagen `sportedge-result-to-draw-v6:1.0.0`, proyecto Compose `sportedge-v6-result-to-draw`, red externa existente `sportedge_v6`. Puerto del host `127.0.0.1:8792`; URL dentro de la red V6 `http://result-to-draw:8788`.

```text
GET  /health/ready
GET  /v1/models/result_to_draw/contract
POST /v1/models/result_to_draw/predict
GET  /v1/models/result_to_draw/results/{request_id}
```

Las rutas de inferencia, contrato y resultados requieren `Authorization: Bearer <clave>`. El [ejemplo](docs/example_request.json) muestra el cuerpo con `request_id` y `snapshot`. `family` es opcional. Se requieren todos los predictores; valores ausentes del proveedor se envían como `null`, no como campos omitidos. Las etiquetas futuras deben omitirse. Se valida la consistencia del marcador y las fechas con zona horaria.

La respuesta identifica target, versión y SHA-256; separa `raw_model_score`, `probability`, `threshold`, `eligible`, `signal` y `actionable_signal`. Cada llamada válida se guarda, incluso fuera del horizonte elegible. Un `request_id` repetido con la misma entrada devuelve el resultado previo; con contenido diferente responde 409. Use el mismo UUID persistido en el backend cuando reintente una llamada.

SQLite WAL con sincronización FULL se guarda en el volumen propio `sportedge_v6_result_to_draw_results`. Se verificó su durabilidad tras reiniciar el contenedor. La clave local se guarda en `secrets/model_api_key.txt` y no se versiona. Configure `RESULT_TO_DRAW_URL` y `RESULT_TO_DRAW_API_KEY_FILE` para usar [el cliente Python](scripts/backend_client.py). Se comprobó una invocación real desde `sportedge-v6-api-1` por la red compartida. Los workers actuales permanecen específicos de home_score_to_increase; esta entrega no altera su target ni activa notificaciones.

```powershell
docker compose build
docker compose up -d
docker compose ps
docker compose stop
```

El Dockerfile principal reutiliza el runtime V6 existente, cuyas versiones se verificaron contra el entorno de entrenamiento. Para otra instalación, use `docker build -f Dockerfile.portable -t sportedge-result-to-draw-v6:1.0.0 .` y cree la red `sportedge_v6` antes de iniciar Compose. Genere una clave con `secrets.token_urlsafe(48)` en el archivo indicado. No copie claves de otra instalación.

## Reproducción de la investigación

Los datos privados y los adjuntos no se incluyen en Git. El flujo de esta cohorte conserva sus rutas en los scripts: el CSV etiquetado en `outputs/etiquetado_result_to_draw_20261011/`, la auditoría en `audit/labeling_validation.json` y los adjuntos en la carpeta de Downloads documentada por `inspect_model_inputs.py`. Para otra ubicación, ajuste esas rutas antes de iniciar un experimento nuevo. No reutilice un test observado para escoger nuevas versiones.

Use Python 3.11 con `requirements-research.txt`. La extracción DOCX/XLSX usa el runtime de artefactos de Codex y sus librerías de lectura. La creación Excel usa `@oai/artifact-tool`, con recalculación, inspección y renderizado de todas las hojas.

```text
python scripts/inspect_model_inputs.py
python scripts/prepare_research.py
python scripts/train_research.py --phase main
python scripts/train_research.py --phase folds
python scripts/finalize_research.py
python scripts/evaluate_incumbent.py
python scripts/feature_diagnostics.py
python scripts/build_report.py
node scripts/build_workbook.mjs
python -m pytest tests/test_model_policy.py -q
python scripts/verify_http.py
python scripts/verify_backend_network.py
python scripts/verify_artifacts.py
```

Los dos comandos de entrenamiento permiten reanudar familias o folds completos. La finalización rechaza cambiar una selección ya congelada. Las métricas del calibrador-fit y de validación seleccionada se etiquetan explícitamente; no se venden como independientes. Los estados fuera de ±1 se identifican como negativos estructurales del target; la precisión operacional se mide sobre la cohorte elegible antes del cierre.
