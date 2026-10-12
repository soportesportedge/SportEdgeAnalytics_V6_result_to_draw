# Worker específico V6 result_to_draw

Destino confirmado por el usuario: SportEdge_Latam_Reloaded, chat `-5538403637`.

## Flujo implementado

1. El collector V6 existente obtiene los snapshots y los guarda en `sportedge_v6.v6.match_centre_invocations`.
2. `sportedge_v6_result_to_draw_worker` lee ese diario y crea una invocación idempotente en `v6_result_to_draw.invocations`. No abre otra conexión de extracción a la fuente.
3. Llama al endpoint específico `POST /v1/models/result_to_draw/predict`. Exige target, familia, versión, hash, umbral, identidad del snapshot y 136 ingenierías coincidentes.
4. Una función limitada registra la inferencia y su único evento result_to_draw en `sportedge_v2.public.adjusted_analytic_inferences` y `public.adjusted_analytic_signals`, las mismas tablas de Analytics V3 y Five Models. Clave: `analytics_v6_result_to_draw_20261011`.
5. Sólo después del commit en el backend se completa el job V6 y se crea su outbox experimental. Deduplicación por partido, target, modelo/hash y marcador.
6. `sportedge_v6_result_to_draw_telegram` publica en el chat confirmado y guarda el ID recibido. Los envíos confirmados se reflejan también como `sent` en `public.telegram_alert_outbox`; el worker antiguo nunca recibe un pendiente de este modelo.

## Persistencia y recuperación

Se conservan todas las respuestas y sus inputs en el diario V6, con UUID estable y leases de 60 segundos. Las tablas existentes admiten una inferencia por fixture/minuto/modelo: se conserva allí la captura más reciente; una captura anterior se identifica como superseded y no reemplaza a la nueva. Los eventos negativos también se guardan.

Si falla el commit local después de guardar en el backend, el reintento recupera la respuesta idempotente de la API y los mismos IDs del backend. El outbox no se duplica. Los snapshots atrasados siguen procesándose para auditoría, pero no generan publicaciones en vivo. Los timeouts de lectura o envíos sin confirmación se dejan en `uncertain` y no se reenvían automáticamente.

## Aislamiento de permisos

Se instalaron dos roles exclusivos:

- `sportedge_v6_draw`: lectura del diario/registro de enlaces V6 y operaciones de cola en su propio schema.
- `sportedge_draw_writer`: únicamente EXECUTE sobre `analytics_v6_result_to_draw.persist_result` y `record_delivery`; no puede insertar o actualizar directamente las tablas compartidas. Las funciones fijan target, modelo, versión, hash y estado de investigación.

Las tres tablas compartidas no cambian de estructura. Los registros V3/V4 existentes no se actualizan. Los secretos de Telegram existentes se montan en sólo lectura; el token no se duplica ni se versiona.

## Publicación experimental

El modelo continúa con `production_approved=false`, `research_only=true` y `actionable_signal=false`. La publicación experimental fue solicitada por el usuario y usa `signal`, frescura y deduplicación propias. El mensaje identifica el objetivo correcto y muestra que la prueba independiente dio precisión 30,2% y recall 11,0%. Una prueba técnica histórica se identifica explícitamente como tal.

## Verificación y activación

El PostgreSQL de pruebas es un contenedor temporal exclusivo, con bases `draw_test_v6` y `draw_test_backend`, credenciales nuevas de prueba y Telegram simulado. La preparación de pruebas no modifica roles, bases ni secretos productivos.

La activación exige instalar `sql/001_draw_queue.sql` en V6 y `sql/002_backend_bridge.sql` en el backend, con el hash del artefacto congelado. `scripts/setup_worker.py --mode production` realiza esa instalación tras comprobar la evidencia de pruebas aisladas. Luego `docker compose up -d` activa API y ambos workers. Los contenedores tienen heartbeat y healthcheck.

Para detener sólo la integración nueva: `docker compose stop draw-inference-worker draw-telegram-worker`. La cola y la auditoría permanecen guardadas. El collector, workers y notificaciones del modelo home no cambian.

## Evidencia del despliegue del 12 de octubre de 2026

Los tres contenedores están saludables. Los dos workers arrancaron sin reinicios. Pasaron 32 comprobaciones con bases aisladas, cuatro pruebas de política del modelo y la prueba con modelo real por HTTP y Telegram simulado.

El circuito productivo se verificó con una sola prueba histórica explícita: snapshot 29225, fixture 19609657, capturado el 02/08/2026 a las 01:45:59 UTC. Se conservaron sus valores y timestamp. La inferencia se guardó con ID 21990, el evento con ID 109682 y el registro de envío con ID 1764 en las tablas compartidas. Telegram confirmó el mensaje 3194 en SportEdge_Latam_Reloaded. No es evidencia de una señal en vivo.

`production_pipeline_validation.json` contiene la reconciliación de IDs. El escritor confirmó carecer de permisos INSERT/UPDATE directos sobre las tablas compartidas. Los conteos de los modelos V3 y V4 existentes permanecieron iguales en la comprobación del despliegue. Las nuevas capturas reales se procesan automáticamente desde la activación del feed; los resultados quedan en modo de investigación y las notificaciones tienen identificación experimental.
