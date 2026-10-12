-- Installed by the backend owner. The worker only receives EXECUTE on these functions.
CREATE SCHEMA IF NOT EXISTS analytics_v6_result_to_draw;
CREATE TABLE IF NOT EXISTS analytics_v6_result_to_draw.requests (
 request_id uuid PRIMARY KEY,
 input_sha256 text NOT NULL, result_sha256 text NOT NULL,
 input_values jsonb NOT NULL, result_values jsonb NOT NULL,
 source text NOT NULL,
 inference_id bigint NOT NULL REFERENCES public.adjusted_analytic_inferences(id) ON DELETE CASCADE,
 signal_id bigint NOT NULL REFERENCES public.adjusted_analytic_signals(id) ON DELETE CASCADE,
 notification_key text NOT NULL,
 notification_allowed boolean NOT NULL,
 suppressed boolean NOT NULL, superseded boolean NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS analytics_v6_result_to_draw.publication_claims (
 notification_key text PRIMARY KEY, request_id uuid NOT NULL UNIQUE
   REFERENCES analytics_v6_result_to_draw.requests(request_id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
 created_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION analytics_v6_result_to_draw.persist_result(
 p_request uuid,p_input jsonb,p_result jsonb,p_source text,p_notify boolean,p_max_age integer
) RETURNS TABLE(inference_id bigint,signal_id bigint,notification_key text,
 notification_allowed boolean,suppressed boolean,superseded boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE
 v_model text:='analytics_v6_result_to_draw_20261011';
 v_fixture bigint:=(p_input->>'id')::bigint;
 v_minute integer:=(p_input->>'snapshot_minute')::integer;
 v_capture timestamptz:=(p_input->>'captured_at')::timestamptz;
 v_ih text:=encode(sha256(convert_to(p_input::text,'UTF8')),'hex');
 v_rh text:=encode(sha256(convert_to(p_result::text,'UTF8')),'hex');
 v_key text; v_inference bigint; v_signal bigint;
 v_allowed boolean:=false; v_suppressed boolean:=false; v_superseded boolean:=false;
 v_claim uuid; v_raw boolean:=(p_result->>'signal')::boolean;
 v_fresh boolean; v_event jsonb; v_response jsonb; v_prior record;
BEGIN
 IF p_source NOT IN ('v6_collector','technical_test') OR p_source IS NULL THEN RAISE EXCEPTION 'invalid_source'; END IF;
 IF p_result->>'target' IS DISTINCT FROM 'result_to_draw'
 OR p_result->>'request_id' IS DISTINCT FROM p_request::text
 OR (p_result->>'fixture_id')::bigint IS DISTINCT FROM v_fixture
 OR p_result->>'captured_at' IS DISTINCT FROM p_input->>'captured_at'
 OR p_result->>'family' IS DISTINCT FROM 'soft_voting'
 OR p_result->>'model_version' IS DISTINCT FROM 'v6_result_to_draw_20261011'
 OR p_result->>'candidate_id' IS DISTINCT FROM 'soft_voting_054'
 OR p_result->>'artifact_sha256' IS DISTINCT FROM '__ARTIFACT_SHA256__'
 OR (p_result->>'production_approved')::boolean IS DISTINCT FROM false
 OR (p_result->>'research_only')::boolean IS DISTINCT FROM true
 OR (p_result->>'actionable_signal')::boolean IS DISTINCT FROM false
 OR (p_result->>'threshold')::double precision IS DISTINCT FROM 0.4756640432007652
 THEN RAISE EXCEPTION 'invalid_result_contract'; END IF;
 IF v_fixture IS NULL OR v_minute IS NULL OR v_capture IS NULL THEN RAISE EXCEPTION 'missing_snapshot_identity'; END IF;
 IF (p_result->>'eligible')::boolean AND ((p_result->>'probability')::double precision NOT BETWEEN 0 AND 1 OR p_result->>'probability' IS NULL) THEN RAISE EXCEPTION 'invalid_probability'; END IF;
 IF v_raw IS DISTINCT FROM ((p_result->>'eligible')::boolean AND COALESCE((p_result->>'probability')::double precision >= (p_result->>'threshold')::double precision,false)) THEN RAISE EXCEPTION 'invalid_signal'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(v_model||':'||v_fixture::text,0));
 SELECT * INTO v_prior FROM analytics_v6_result_to_draw.requests q WHERE q.request_id=p_request;
 IF FOUND THEN
   IF v_prior.input_sha256<>v_ih OR v_prior.result_sha256<>v_rh THEN RAISE EXCEPTION 'idempotency_conflict'; END IF;
   RETURN QUERY SELECT v_prior.inference_id,v_prior.signal_id,v_prior.notification_key,v_prior.notification_allowed,v_prior.suppressed,v_prior.superseded;
   RETURN;
 END IF;
 v_key:=encode(sha256(convert_to(concat_ws(':',v_fixture,'result_to_draw',v_model,'__ARTIFACT_SHA256__',p_input->>'home_score',p_input->>'away_score'),'UTF8')),'hex');
 SELECT i.id INTO v_inference FROM public.adjusted_analytic_inferences i
 WHERE i.fixture_id=v_fixture AND i.snapshot_minute=v_minute AND i.model_key=v_model
 AND (i.input_record->>'captured_at')::timestamptz>v_capture;
 IF FOUND THEN
   v_superseded:=true;
   SELECT s.id INTO v_signal FROM public.adjusted_analytic_signals s WHERE s.inference_id=v_inference AND s.target='result_to_draw';
 ELSE
   v_fresh:=v_capture<=clock_timestamp() AND v_capture>=clock_timestamp()-make_interval(secs=>GREATEST(30,LEAST(3600,p_max_age)));
   IF p_notify AND v_raw AND (v_fresh OR p_source='technical_test') THEN
     INSERT INTO analytics_v6_result_to_draw.publication_claims(notification_key,request_id)
     VALUES(v_key,p_request) ON CONFLICT DO NOTHING RETURNING request_id INTO v_claim;
     v_allowed:=v_claim IS NOT NULL; v_suppressed:=NOT v_allowed;
   END IF;
   v_event:=p_result||jsonb_build_object('target','result_to_draw','model_key',v_model,
     'model_family',p_result->>'family','integration_version','draw_worker_v1.0.0',
     'raw_signal',v_raw,'operational_signal',v_allowed,'actionable_signal',false,
     'suppressed_due_to_prior_signal',v_suppressed,'publication_mode','experimental',
     'notification_allowed',v_allowed,'notification_key',v_key,'source',p_source);
   v_response:=jsonb_build_object('model_key',v_model,'model_version',p_result->>'model_version',
     'integration_version','draw_worker_v1.0.0','request_id',p_request,'research_only',true,
     'production_approved',false,'predictions',jsonb_build_array(jsonb_build_object(
       'id',v_fixture,'fixture_id',v_fixture,'snapshot_minute',v_minute,'captured_at',v_capture,
       'targets',jsonb_build_object('result_to_draw',v_event))),
     'next_signal_state','[]'::jsonb,'v6_result',p_result,'source',p_source);
   INSERT INTO public.adjusted_analytic_inferences(fixture_id,snapshot_minute,model_key,input_record,response_json,next_signal_state)
   VALUES(v_fixture,v_minute,v_model,p_input,v_response,'[]'::jsonb)
   ON CONFLICT ON CONSTRAINT uq_adjusted_inference_fixture_minute_model DO UPDATE
   SET input_record=EXCLUDED.input_record,response_json=EXCLUDED.response_json,next_signal_state=EXCLUDED.next_signal_state
   RETURNING id INTO v_inference;
   INSERT INTO public.adjusted_analytic_signals(inference_id,fixture_id,snapshot_minute,target,model_key,
     model_family,model_version,integration_version,raw_signal,operational_signal,actionable_signal,
     suppressed_due_to_prior_signal,confidence,probability,threshold,home_score,away_score,payload)
   VALUES(v_inference,v_fixture,v_minute,'result_to_draw',v_model,p_result->>'family',p_result->>'model_version',
     'draw_worker_v1.0.0',v_raw,v_allowed,false,v_suppressed,NULL,
     (p_result->>'probability')::double precision,(p_result->>'threshold')::double precision,
     (p_input->>'home_score')::integer,(p_input->>'away_score')::integer,v_event)
   ON CONFLICT ON CONSTRAINT uq_adjusted_signal_inference_target DO UPDATE SET
     model_family=EXCLUDED.model_family,model_version=EXCLUDED.model_version,integration_version=EXCLUDED.integration_version,
     raw_signal=EXCLUDED.raw_signal,operational_signal=EXCLUDED.operational_signal,actionable_signal=false,
     suppressed_due_to_prior_signal=EXCLUDED.suppressed_due_to_prior_signal,confidence=NULL,
     probability=EXCLUDED.probability,threshold=EXCLUDED.threshold,
     home_score=EXCLUDED.home_score,away_score=EXCLUDED.away_score,payload=EXCLUDED.payload
   RETURNING id INTO v_signal;
 END IF;
 INSERT INTO analytics_v6_result_to_draw.requests(request_id,input_sha256,result_sha256,input_values,result_values,
   source,inference_id,signal_id,notification_key,notification_allowed,suppressed,superseded)
 VALUES(p_request,v_ih,v_rh,p_input,p_result,p_source,v_inference,v_signal,v_key,v_allowed,v_suppressed,v_superseded);
 RETURN QUERY SELECT v_inference,v_signal,v_key,v_allowed,v_suppressed,v_superseded;
END $$;

CREATE OR REPLACE FUNCTION analytics_v6_result_to_draw.record_delivery(
 p_request uuid,p_message_id text,p_sent_at timestamptz,p_payload jsonb
) RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_request record;v_id bigint;
BEGIN
 SELECT * INTO v_request FROM analytics_v6_result_to_draw.requests WHERE request_id=p_request;
 IF NOT FOUND OR NOT v_request.notification_allowed OR p_message_id IS NULL OR p_message_id!~'^[0-9]+(,[0-9]+)*$'
 THEN RAISE EXCEPTION 'invalid_delivery_identity'; END IF;
 INSERT INTO public.telegram_alert_outbox(inference_id,fixture_id,snapshot_minute,target,dedup_key,payload,
   status,attempts,next_attempt_at,telegram_message_id,sent_at,updated_at)
 VALUES(v_request.inference_id,(v_request.input_values->>'id')::bigint,
   (v_request.input_values->>'snapshot_minute')::integer,'result_to_draw',v_request.notification_key,
   p_payload||jsonb_build_object('model_key','analytics_v6_result_to_draw_20261011','request_id',p_request,'source',v_request.source),
   'sent',1,NULL,p_message_id,p_sent_at,now())
 ON CONFLICT(dedup_key) DO UPDATE SET status='sent',telegram_message_id=EXCLUDED.telegram_message_id,
   sent_at=EXCLUDED.sent_at,updated_at=now() RETURNING id INTO v_id;
 RETURN v_id;
END $$;
REVOKE ALL ON FUNCTION analytics_v6_result_to_draw.persist_result(uuid,jsonb,jsonb,text,boolean,integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION analytics_v6_result_to_draw.record_delivery(uuid,text,timestamptz,jsonb) FROM PUBLIC;
