import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {PGlite} from '@electric-sql/pglite';
const db = new PGlite();
await db.exec(`create role anon; create role authenticated; create role service_role bypassrls;
  create schema storage; create table storage.buckets(id text primary key,name text,public boolean,file_size_limit bigint,allowed_mime_types text[]);`);
await db.exec(await readFile(new URL('../../../supabase/migrations/001_research_desk.sql',import.meta.url),'utf8'));
await db.exec('set role service_role');
const token='11111111-1111-4111-8111-111111111111';
const claim=async()=> (await db.query('select public.claim_run($1,$2,$3) as r',['live','2026-09-18T09:00:00+09:00',token])).rows[0].r;
const r=await claim(); assert.ok(r.id);
assert.equal(await claim(),null,'Concurrent claim must not take over');
const doc={title:'테스트',generated_at:'2026-09-18T09:01:00+09:00',observation_start:'2026-09-18T08:00:00+09:00',observation_end:'2026-09-18T09:00:00+09:00',
 observations:{},analysis:{},versions:{schema:'2'},payload:{timestamp:'2026-09-18 09:01:00'},markets:{kospi:{price:'100'}},
 events:[{id:'event1',event_type:'news',title:'기사',url:'https://example.com',position:0,evidence:{title:'기사'}}]};
await assert.rejects(db.query('select public.publish_briefing($1,$2,$3)',[r.id,'22222222-2222-4222-8222-222222222222',doc]));
await assert.rejects(db.query('select public.publish_briefing($1,$2,$3)',[r.id,token,{...doc,observation_end:'2026-09-19T09:00:00+09:00'}]));
assert.equal((await db.query('select count(*)::int as n from briefings')).rows[0].n,0,'Failed publish must roll back');
await db.query('select public.publish_briefing($1,$2,$3)',[r.id,token,doc]);
assert.equal(await claim(),null,'Completed target cannot be reclaimed');
await db.exec('set role anon');
assert.equal((await db.query('select * from briefings')).rows.length,1);
assert.equal((await db.query('select * from events')).rows.length,1);
assert.equal((await db.query('select * from market_snapshots')).rows.length,1);
await assert.rejects(db.query('select * from pipeline_runs'));
await assert.rejects(db.query("delete from briefings"));
await assert.rejects(claim());
await assert.rejects(db.query('select public.publish_briefing($1,$2,$3)',[r.id,token,doc]));
await db.exec('reset role');
// Unpublished records and unlinked source events must be invisible to public readers.
await db.exec("update briefings set published=false");
await db.exec('set role anon');
for(const table of ['briefings','events','market_snapshots','briefing_events'])
 assert.equal((await db.query(`select * from ${table}`)).rows.length,0,table+' RLS');
await db.exec('reset role');
await db.exec(await readFile(new URL('../../../supabase/migrations/20261020012626_briefing_public_projection.sql',import.meta.url),'utf8'));
assert.equal((await db.query('select * from published_briefings')).rows.length,0,'Unpublished originals not copied');
await db.exec(`update briefings set published=true,payload='{"headline":"public","article_excerpt":"private body","events":{"news":[{"title":"public title","article_excerpt":"raw body","research_insight":{"fact":"short reader fact"}}]}}'::jsonb`);
await db.exec(await readFile(new URL('../../../supabase/migrations/20261020012851_restrict_briefing_sources.sql',import.meta.url),'utf8'));
for (const role of ['anon','authenticated']) {
 await db.exec('set role '+role);
 const published=(await db.query('select * from published_briefings')).rows;
 assert.equal(published.length,1);
 assert.ok(!JSON.stringify(published).includes('private body'));
 assert.ok(!JSON.stringify(published).includes('raw body'));
 assert.equal(published[0].payload.events.news[0].research_insight.fact,'short reader fact');
 for(const table of ['briefings','events','market_snapshots','briefing_events','pipeline_runs'])
  await assert.rejects(db.query(`select * from ${table}`),table+' direct read denied');
 await assert.rejects(db.query('delete from published_briefings'));
 await assert.rejects(db.query("select research_private.reader_json('{}')"));
 await db.exec('reset role');
}
await db.exec('set role service_role');
await db.exec('update briefings set published=false');
assert.equal((await db.query('select * from published_briefings')).rows.length,0,'Unpublish removes public copy atomically');
await db.exec('reset role');
await db.close();
console.log('DB contract: claim, atomic publication, timestamps, public read and write denial passed');
