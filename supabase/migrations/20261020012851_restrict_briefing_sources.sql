-- Apply only after the deployed frontend reads published_briefings.
begin;
revoke select on public.briefings,public.events,public.market_snapshots,public.briefing_events from anon,authenticated;
commit;
