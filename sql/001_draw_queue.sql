CREATE SCHEMA IF NOT EXISTS v6_result_to_draw;
CREATE TABLE IF NOT EXISTS v6_result_to_draw.settings (
 key text PRIMARY KEY, value jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO v6_result_to_draw.settings(key,value) VALUES ('feed_started_at',to_jsonb(clock_timestamp())) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS v6_result_to_draw.invocations (
 invocation_id uuid PRIMARY KEY,
 source_invocation_id uuid UNIQUE,
 idempotency_key text NOT NULL UNIQUE,
 input_sha256 text NOT NULL,
 input_values jsonb NOT NULL,
 family text NOT NULL DEFAULT 'soft_voting',
 target text NOT NULL DEFAULT 'result_to_draw' CHECK(target='result_to_draw'),
 source text NOT NULL,
 notify boolean NOT NULL DEFAULT true,
 fixture_id bigint NOT NULL,
 snapshot_minute integer NOT NULL,
 captured_at timestamptz NOT NULL,
 received_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 completed_at timestamptz,
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','processing','completed','rejected','failed')),
 attempts integer NOT NULL DEFAULT 0,
 next_attempt_at timestamptz NOT NULL DEFAULT now(),
 lease_token uuid, leased_until timestamptz, last_error text,
 result jsonb, engineered_values jsonb,
 model_version text, artifact_sha256 text,
 probability double precision CHECK(probability BETWEEN 0 AND 1), threshold double precision,
 eligible boolean, signal boolean, actionable_signal boolean,
 backend_inference_id bigint, backend_signal_id bigint,
 notification_allowed boolean, suppressed_due_to_prior_signal boolean, superseded boolean
);
CREATE INDEX IF NOT EXISTS draw_pending ON v6_result_to_draw.invocations(next_attempt_at,received_at) WHERE status='pending';
CREATE INDEX IF NOT EXISTS draw_lease ON v6_result_to_draw.invocations(leased_until) WHERE status='processing';
CREATE INDEX IF NOT EXISTS draw_fixture ON v6_result_to_draw.invocations(fixture_id,captured_at);
CREATE TABLE IF NOT EXISTS v6_result_to_draw.invocation_events (
 event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 invocation_id uuid NOT NULL REFERENCES v6_result_to_draw.invocations,
 event text NOT NULL, detail jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS draw_events ON v6_result_to_draw.invocation_events(invocation_id,event_id);
CREATE TABLE IF NOT EXISTS v6_result_to_draw.telegram_outbox (
 outbox_id uuid PRIMARY KEY,
 invocation_id uuid NOT NULL REFERENCES v6_result_to_draw.invocations,
 notification_key text NOT NULL UNIQUE,
 chat_id text NOT NULL,
 payload jsonb NOT NULL,
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sending','sent','failed','uncertain','suppressed')),
 attempts integer NOT NULL DEFAULT 0,
 next_attempt_at timestamptz NOT NULL DEFAULT now(),
 lease_token uuid, leased_until timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), sent_at timestamptz,
 telegram_message_id text, last_error text,
 backend_mirrored boolean NOT NULL DEFAULT false, backend_outbox_id bigint,
 backend_mirror_attempts integer NOT NULL DEFAULT 0,
 backend_mirror_next_attempt_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS draw_outbox_pending ON v6_result_to_draw.telegram_outbox(next_attempt_at,created_at) WHERE status='pending';
CREATE TABLE IF NOT EXISTS v6_result_to_draw.worker_status (
 component text PRIMARY KEY, heartbeat_at timestamptz NOT NULL DEFAULT now(), detail jsonb NOT NULL
);
