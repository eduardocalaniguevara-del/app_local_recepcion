-- Proyecto hostal-ovalo (fmlfqupwscymosvwnnzx). Ejecutar con supabase_mcp apply_migration / SQL editor.
-- RLS: AJUSTAR al patron exacto de las demas tablas del Hostal si difiere (no pude leer pg_policies).
CREATE TABLE IF NOT EXISTS public.sandbox_change_log (
  log_id BIGSERIAL PRIMARY KEY,
  tabla TEXT NOT NULL, op TEXT NOT NULL,
  pk1 TEXT NOT NULL DEFAULT '', pk2 TEXT NOT NULL DEFAULT '',
  row_data JSONB, origen_equipo TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.aprendizaje_compartido_hostal (
  id TEXT PRIMARY KEY, fecha_hora TEXT NOT NULL, origen_equipo TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT 'RECEPCIONISTA', categoria TEXT NOT NULL DEFAULT 'OPERATIVO',
  pregunta_o_evento TEXT NOT NULL DEFAULT '', patron_detectado TEXT NOT NULL DEFAULT '',
  regla_aprendida TEXT NOT NULL DEFAULT '', updated_at TEXT DEFAULT ''
);
ALTER TABLE public.sandbox_change_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.aprendizaje_compartido_hostal ENABLE ROW LEVEL SECURITY;
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['sandbox_change_log','aprendizaje_compartido_hostal'] LOOP
    EXECUTE format('DROP POLICY IF EXISTS hostal_token_all ON public.%I', t);
    EXECUTE format($p$CREATE POLICY hostal_token_all ON public.%I FOR ALL TO anon, authenticated
      USING ((current_setting('request.headers', true)::json ->> 'x-hostal-sync-token') = 'ovalo_master_sync_2026_k9x')
      WITH CHECK ((current_setting('request.headers', true)::json ->> 'x-hostal-sync-token') = 'ovalo_master_sync_2026_k9x')$p$, t);
  END LOOP;
END $$;
-- Para el trigger de supabase_change_log (tabla real), replicar el trigger existente sobre aprendizaje_compartido_hostal
-- (copiar de: select pg_get_triggerdef(oid) from pg_trigger where tgrelid='public.yape_conciliacion_hostal'::regclass).
