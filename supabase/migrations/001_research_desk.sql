begin;
create table public.pipeline_runs (
  id uuid primary key default gen_random_uuid(),
  kind text not null check (kind in ('live','daily','weekly')),
  target_at timestamptz not null,
  token uuid not null,
  status text not null check (status in ('running','complete','failed')),
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  error_code text,
  notification_status text not null default 'pending'
    check (notification_status in ('pending','sending','sent','failed','skipped','unknown')),
  unique(kind,target_at)
);
create table public.briefings (
  id uuid primary key references public.pipeline_runs(id),
  kind text not null check (kind in ('live','daily','weekly')),
  target_at timestamptz not null,
  generated_at timestamptz not null,
  observation_start timestamptz,
  observation_end timestamptz,
  title text not null,
  search_text text not null,
  observations jsonb not null,
  analysis jsonb not null,
  versions jsonb not null,
  payload jsonb not null,
  published boolean not null default false,
  unique(kind,target_at),
  check(observation_start is null or observation_start <= observation_end),
  check(observation_end is null or observation_end <= generated_at)
);
create index briefings_history on public.briefings(kind,generated_at desc) where published;
create table public.market_snapshots (
  briefing_id uuid not null references public.briefings(id) on delete cascade,
  symbol text not null,
  observation jsonb not null,
  primary key(briefing_id,symbol)
);
create table public.events (
  id text primary key,
  event_type text not null check (event_type in ('news','dart')),
  title text not null,
  url text not null,
  source_payload jsonb not null,
  first_seen_at timestamptz not null default now()
);
create table public.briefing_events (
  briefing_id uuid not null references public.briefings(id) on delete cascade,
  event_id text not null references public.events(id),
  position integer not null,
  evidence jsonb not null,
  primary key(briefing_id,event_id)
);
alter table public.pipeline_runs enable row level security;
alter table public.briefings enable row level security;
alter table public.market_snapshots enable row level security;
alter table public.events enable row level security;
alter table public.briefing_events enable row level security;
revoke all on public.pipeline_runs,public.briefings,public.market_snapshots,public.events,public.briefing_events from anon,authenticated;
grant select on public.briefings,public.market_snapshots,public.events,public.briefing_events to anon,authenticated;
grant all on public.pipeline_runs,public.briefings,public.market_snapshots,public.events,public.briefing_events to service_role;
create policy published_briefings on public.briefings for select to anon,authenticated using(published);
create policy published_markets on public.market_snapshots for select to anon,authenticated using(exists(select 1 from public.briefings b where b.id=briefing_id and b.published));
create policy published_links on public.briefing_events for select to anon,authenticated using(exists(select 1 from public.briefings b where b.id=briefing_id and b.published));
create policy published_events on public.events for select to anon,authenticated using(exists(select 1 from public.briefing_events e where e.event_id=events.id));

-- Only the batch role may execute RPCs. No SECURITY DEFINER escalation.
create function public.claim_run(p_kind text,p_target timestamptz,p_token uuid)
returns jsonb language plpgsql set search_path=public as $$
declare r public.pipeline_runs;
begin
  insert into public.pipeline_runs(kind,target_at,token,status)
  values(p_kind,p_target,p_token,'running')
  on conflict(kind,target_at) do update set token=excluded.token,status='running',
    started_at=now(),finished_at=null,error_code=null
  where pipeline_runs.status='failed' or
    (pipeline_runs.status='running' and pipeline_runs.started_at < now()-interval '45 minutes')
  returning * into r;
  if r.id is null then return null; end if;
  return to_jsonb(r);
end $$;

create function public.publish_briefing(p_run uuid,p_token uuid,p_document jsonb)
returns uuid language plpgsql set search_path=public as $$
declare r public.pipeline_runs; e jsonb; kv record;
begin
  select * into r from public.pipeline_runs where id=p_run for update;
  if r.id is null or r.token<>p_token or r.status<>'running' then
    raise exception 'run claim is no longer valid';
  end if;
  insert into public.briefings(id,kind,target_at,generated_at,observation_start,observation_end,title,search_text,observations,analysis,versions,payload)
  values(r.id,r.kind,r.target_at,(p_document->>'generated_at')::timestamptz,
    (p_document->>'observation_start')::timestamptz,(p_document->>'observation_end')::timestamptz,
    p_document->>'title',(p_document->>'title') || ' ' || (p_document->'analysis')::text,p_document->'observations',p_document->'analysis',p_document->'versions',p_document->'payload');
  for kv in select * from jsonb_each(p_document->'markets') loop
    insert into public.market_snapshots values(r.id,kv.key,kv.value);
  end loop;
  for e in select * from jsonb_array_elements(p_document->'events') loop
    insert into public.events(id,event_type,title,url,source_payload)
    values(e->>'id',e->>'event_type',e->>'title',e->>'url',e->'evidence') on conflict(id) do nothing;
    insert into public.briefing_events values(r.id,e->>'id',(e->>'position')::integer,e->'evidence') on conflict do nothing;
  end loop;
  update public.briefings set published=true where id=r.id;
  update public.pipeline_runs set status='complete',finished_at=now() where id=r.id;
  return r.id;
end $$;
revoke all on function public.claim_run(text,timestamptz,uuid) from public,anon,authenticated;
revoke all on function public.publish_briefing(uuid,uuid,jsonb) from public,anon,authenticated;
grant execute on function public.claim_run(text,timestamptz,uuid) to service_role;
grant execute on function public.publish_briefing(uuid,uuid,jsonb) to service_role;

insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
values('research-covers','research-covers',true,10485760,array['image/png','image/jpeg','image/webp'])
on conflict(id) do nothing;
-- Public bucket serves images. Upload/delete use service_role only; no public write policy.
commit;
