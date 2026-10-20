begin;
-- Dedicated public read model: no source observations, raw article bodies or run tokens.
create table public.published_briefings (
  id uuid primary key references public.briefings(id) on delete cascade,
  kind text not null,
  title text not null,
  generated_at timestamptz not null,
  observation_end timestamptz,
  search_text text not null,
  published boolean not null default true,
  payload jsonb not null
);
alter table public.published_briefings enable row level security;
revoke all on public.published_briefings from anon,authenticated;
grant select on public.published_briefings to anon,authenticated;
grant all on public.published_briefings to service_role;
create policy published_read on public.published_briefings for select to anon,authenticated using(published);
create index published_briefings_history on public.published_briefings(kind,generated_at desc) where published;

create schema if not exists research_private;
revoke all on schema research_private from public,anon,authenticated;
grant usage on schema research_private to service_role;
create function research_private.reader_json(value jsonb) returns jsonb
language plpgsql immutable set search_path='' as $$
declare result jsonb;
begin
  if jsonb_typeof(value)='object' then
    select coalesce(jsonb_object_agg(key,research_private.reader_json(val)),'{}'::jsonb) into result
    from jsonb_each(value) as e(key,val)
    where key not in ('article_excerpt','listing_summary','description','source_payload','source_samples','calibration','cover_image','generate_ai_image','execution','raw','dt');
    return result;
  elsif jsonb_typeof(value)='array' then
    select coalesce(jsonb_agg(research_private.reader_json(val) order by n),'[]'::jsonb) into result
    from jsonb_array_elements(value) with ordinality as a(val,n);
    return result;
  end if;
  return value;
end $$;
revoke all on function research_private.reader_json(jsonb) from public,anon,authenticated;
grant execute on function research_private.reader_json(jsonb) to service_role;

create function research_private.public_payload(value jsonb) returns jsonb
language sql immutable set search_path='' as $$
  select research_private.reader_json(coalesce(jsonb_object_agg(key,val),'{}'::jsonb))
  from jsonb_each(value) as e(key,val) where key = any(array[
    'headline','summary_items','key_points','watchpoint','daily_overview','daily_session','generation',
    'window_start','collection_cutoff','edition_title','indexes','market_signals','news_items','events',
    'sentiment','market_participation','market_trend','evidence_quality','evidence_assessment',
    'comparison','reliability','market_snapshot','cross_source_checks','risk_events','opportunity_events',
    'research','summary','daily_points','market_performance','weekly_timeline','weekly_highlights',
    'weekly_takeaway','next_week_outlook'
  ]);
$$;
revoke all on function research_private.public_payload(jsonb) from public,anon,authenticated;
grant execute on function research_private.public_payload(jsonb) to service_role;

create function research_private.sync_reader() returns trigger
language plpgsql set search_path='' as $$
declare cleaned jsonb;
begin
  if not new.published then
    delete from public.published_briefings where id=new.id;
    return new;
  end if;
  cleaned := research_private.public_payload(new.payload);
  insert into public.published_briefings(id,kind,title,generated_at,observation_end,search_text,published,payload)
  values(new.id,new.kind,new.title,new.generated_at,new.observation_end,new.title || ' ' || cleaned::text,true,cleaned)
  on conflict(id) do update set kind=excluded.kind,title=excluded.title,generated_at=excluded.generated_at,
    observation_end=excluded.observation_end,search_text=excluded.search_text,published=true,payload=excluded.payload;
  return new;
end $$;
revoke all on function research_private.sync_reader() from public,anon,authenticated;
grant execute on function research_private.sync_reader() to service_role;
create trigger sync_briefing_reader after insert or update on public.briefings
for each row execute function research_private.sync_reader();
insert into public.published_briefings(id,kind,title,generated_at,observation_end,search_text,payload)
select id,kind,title,generated_at,observation_end,title || ' ' || research_private.public_payload(payload)::text,research_private.public_payload(payload)
from public.briefings where published;
-- Revoke legacy reads in the follow-up migration AFTER the frontend switches.
commit;
