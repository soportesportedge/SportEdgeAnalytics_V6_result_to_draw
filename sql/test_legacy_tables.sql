-- Only for the disposable test cluster; faithfully mirrors the three inspected contracts.
CREATE TABLE public.fixtures(id bigint PRIMARY KEY);
INSERT INTO public.fixtures SELECT generate_series(1,100);
CREATE TABLE public.adjusted_analytic_inferences (
 id bigserial PRIMARY KEY,fixture_id bigint NOT NULL REFERENCES public.fixtures ON DELETE CASCADE,
 snapshot_minute integer NOT NULL,model_key varchar(64) NOT NULL DEFAULT 'analytics_v3_adjusted',
 input_record jsonb NOT NULL,response_json jsonb NOT NULL,next_signal_state jsonb NOT NULL DEFAULT '[]',
 created_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT uq_adjusted_inference_fixture_minute_model UNIQUE(fixture_id,snapshot_minute,model_key)
);
CREATE TABLE public.adjusted_analytic_signals (
 id bigserial PRIMARY KEY,inference_id bigint NOT NULL REFERENCES public.adjusted_analytic_inferences ON DELETE CASCADE,
 fixture_id bigint NOT NULL REFERENCES public.fixtures ON DELETE CASCADE,snapshot_minute integer NOT NULL,
 target varchar(160) NOT NULL,model_key varchar(64) NOT NULL DEFAULT 'analytics_v3_adjusted',
 model_family varchar(160),model_version varchar(255),integration_version varchar(255),raw_signal boolean,
 operational_signal boolean,actionable_signal boolean,suppressed_due_to_prior_signal boolean,
 confidence double precision,probability double precision,threshold double precision,home_score integer,away_score integer,
 payload jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT uq_adjusted_signal_inference_target UNIQUE(inference_id,target)
);
CREATE TABLE public.telegram_alert_outbox (
 id bigserial PRIMARY KEY,inference_id bigint NOT NULL REFERENCES public.adjusted_analytic_inferences ON DELETE CASCADE,
 fixture_id bigint NOT NULL REFERENCES public.fixtures ON DELETE CASCADE,snapshot_minute integer NOT NULL,
 target varchar(160) NOT NULL,dedup_key varchar(64) NOT NULL UNIQUE,payload jsonb NOT NULL,
 status varchar(16) NOT NULL DEFAULT 'pending',attempts integer NOT NULL DEFAULT 0,
 next_attempt_at timestamptz,last_error text,telegram_message_id varchar(64),sent_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now()
);
