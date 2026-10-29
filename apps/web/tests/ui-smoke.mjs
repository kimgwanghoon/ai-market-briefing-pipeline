// Local fixtures only. Never contacts Supabase, market sources, or Discord.
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium} from '@playwright/test';
const id='11111111-1111-4111-8111-111111111111';
const event={title:'검증용 반도체 수주 확대',link:'https://example.com/news',image_url:'https://example.com/fixture-news.png',press:'검증용 출처',time:'09:00',why_it_matters:'실제 투자자료가 아닌 UI 테스트 데이터입니다.'};
const markets={kospi:{price:'2,500.00',change:'▲ 10.00 (+0.40%)',as_of:'09-18 09:00 KST',market_status:'장중'},nasdaq:{price:'18,000.00',change:'▼ 90.00 (-0.50%)',as_of:'09-18 05:00 KST',market_status:'최근 종가'}};
const rows=['daily','live','weekly'].map((kind,i)=>({id:id.replace(/^1/,String(i+1)),kind,title:'검증용 시황 · 시장 흐름과 확인할 조건',generated_at:new Date().toISOString(),
 observation_end:'2026-09-18T09:00:00+09:00',payload:{headline:'검증용 시황',summary_items:['[한국 시장]','**검증용 분석**입니다. 실제 투자자료가 아닙니다.','[미국 시장]','외부 시장의 변화를 점검합니다.'],
 key_points:kind==='live'?['검증용 시장 흐름입니다.']:undefined,indexes:markets,news_items:[event],watchpoint:'후속 공시와 지수 흐름을 확인하세요.',
 research:{method:'검증용 관찰 목록',empty_reason:'',sectors:[{name:'반도체',stance:'관찰',basis:'이벤트 기준',evidence:[event]}],stocks:[{name:'검증용 종목',ticker:'TEST',sector:'반도체',status:'신규 관찰',reason:event.title,risk:'실적 기대 변화',invalidation:'근거 변경 시 재검토',basis:'검증용',quote:{price:100000,as_of:'2026-09-18T09:00:00+09:00'},evidence:[event]}]},
 summary:{count:8,trading_days:2},daily_points:[{day:'09-17',score_avg:50,count:4},{day:'09-18',score_avg:55,count:4}],market_performance:[{label:'KOSPI',start:'2,490',end:'2,500',change_text:'+0.40%'}],
 next_week_outlook:{bias:'데이터 축적 중',confidence:'자료 부족',rationale:['관측 자료를 축적 중입니다.'],upside_conditions:[],downside_conditions:[]}}}));
rows[1].payload.sentiment={score:51.7,label:'중립',normalized_components:{market:1,news:0,dart:-1,sector:0},weights:{market:.35,news:.2,dart:.25,sector:.2}};
rows[1].payload.reliability={evaluated:13,hit_rate:'53.8%',false_alarm_rate:'23.1%',status:'검증 표본 부족'};
rows[0].payload.daily_session={session:'morning',title:'미국장 결과와 국내장 준비',facts:['NASDAQ -0.50% · 테스트 관측값'],upside:'국내 개장 후 방향 확인',downside:'하락과 환율 상승 동반 여부 확인',review:[],limits:'조건부 시나리오이며 상승 확률이 아닙니다.'};
rows[0].payload.evidence_assessment={status:'근거 부족',total:1,verified:0,guidance:'원문 근거가 없어 판단 보류'};
rows[0].payload.daily_session.issues=[{id:'issue-1',title:event.title,url:event.link,image_url:event.image_url,source:'테스트 출처',published_at:'2026-10-21 07:00',region:'미국',content_status:'article_verified',fact:'검증용 공급 계획 발표입니다.',korea_impact:'실제 주문 증가 시 국내 공급망 수요를 확인합니다.',next_check:'후속 실적의 주문 규모를 확인합니다.',invalidation:'주문 지연 시 재검토합니다.',analysis_note:'AI 요약 · 인용문·숫자 검증 통과',analysis_mode:'ai',observations:[{ticker:'NVDA',price:'100',change:'+1%',market_status:'최근 종가',as_of:'10-21 05:00 KST'}]}];
rows[0].payload.daily_session.coverage={selected:1,us:1,analyzed:1};
rows[0].payload.cover_image='https://example.com/retired-cover.png';
rows.push({...rows[1],id:'44444444-4444-4444-8444-444444444444',generated_at:new Date(Date.now()-3600000).toISOString(),payload:{...rows[1].payload,sentiment:{...rows[1].payload.sentiment,score:50}}});
let mode='ready'; const seen=[];
const server=createServer((req,res)=>{const url=new URL(req.url,'http://localhost');seen.push(url.searchParams);
 if(mode==='error'){res.writeHead(503);res.end('{}');return;}
 let data=mode==='empty'?[]:rows;
 for(const key of ['kind','id'])if(url.searchParams.has(key))data=data.filter(r=>'eq.'+r[key]===url.searchParams.get(key));
 for(const value of url.searchParams.getAll('generated_at'))if(value.startsWith('lt.'))data=data.filter(r=>Date.parse(r.generated_at)<Date.parse(value.slice(3)));
 if(url.searchParams.get('search_text')?.includes('없는검색어'))data=[];
 data=data.slice(Number(url.searchParams.get('offset')||0),Number(url.searchParams.get('offset')||0)+Number(url.searchParams.get('limit')||12));
 res.setHeader('Content-Type','application/json');res.end(JSON.stringify(data));});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
console.log('Fixture server listening');
const port=server.address().port;
const app=spawn(process.execPath,['node_modules/next/dist/bin/next','start','--hostname','127.0.0.1','--port','3219'],{env:{...process.env,SUPABASE_URL:`http://127.0.0.1:${port}`,SUPABASE_PUBLISHABLE_KEY:'sb_publishable_fixture_key'},stdio:'pipe'});
app.stderr.on('data',data=>process.stderr.write(data));
app.stdout.on('data',data=>process.stdout.write(data));
let browser;
try{
 let ready=false;
 for(let i=0;i<15;i++){try{const r=await fetch('http://127.0.0.1:3219',{signal:AbortSignal.timeout(2000)});if(r.ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,250));}
 assert.ok(ready,'Next server must start');
 console.log('Next server ready');
 let launch={headless:true};
 if(process.env.CHROMIUM_MODULE){const mod=await import(process.env.CHROMIUM_MODULE);const c=mod.default;launch={...launch,args:c.args,executablePath:process.env.CHROMIUM_EXECUTABLE || await c.executablePath()};}
 browser=await chromium.launch(launch);
 console.log('Browser ready');
 const page=await browser.newPage(); const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('https://example.com/fixture-news.png', route=>route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j3ioAAAAASUVORK5CYII=','base64')}));
 for(const width of [1440,390]){
   await page.setViewportSize({width,height:1000});
   for(const route of ['/','/live','/daily','/weekly','/archive']){
     await page.goto('http://127.0.0.1:3219'+route);await page.locator('h1').waitFor();
     assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),`${route} overflow at ${width}`);
     assert.ok(!(await page.content()).includes('sb_publishable_fixture_key'),'Server key must not be in document');
     if(['/live','/daily','/weekly'].includes(route)){
       await page.getByRole('navigation',{name:'보고서 바로가기'}).waitFor();
       await page.getByRole('link',{name:'다음 확인',exact:true}).click();
       assert.ok(page.url().endsWith('#report-next'));
       await page.getByText('시장 판단의 상세 설명',{exact:true}).click();
       assert.equal(await page.locator('.story-details').getAttribute('open'),'');
     }
   }
   await page.goto('http://127.0.0.1:3219/daily');
   await page.getByRole('heading',{name:'미국장 결과와 국내장 준비'}).waitFor();
   await page.getByRole('heading',{name:'미국 기술주 아침 브리핑 · 국내장 연결'}).waitFor();
   await page.getByText('이 해석을 다시 봐야 하는 조건').click();
   await page.getByText('주문 지연 시 재검토합니다.').waitFor();
   assert.ok((await page.textContent('.daily-issues')).includes('NVDA'));
   assert.equal(await page.locator('img[src*="retired-cover"]').count(),0);
   assert.ok((await page.textContent('body')).includes('근거 부족'));
   await page.locator('.news-thumbnail').first().scrollIntoViewIfNeeded();
   await page.waitForFunction(()=>Array.from(document.querySelectorAll('.news-thumbnail img')).some(img=>img.complete && img.naturalWidth>0));
   assert.equal(await page.locator('a.news-thumbnail').first().getAttribute('href'),'https://example.com/news');
   const thumbnails=await page.locator('.news-thumbnail').count();
   await page.locator('.news-thumbnail img').first().dispatchEvent('error');
   await page.waitForFunction(count=>document.querySelectorAll('.news-thumbnail').length===count-1,thumbnails);
   await page.getByText('원문 근거 확인').click();
   assert.ok(await page.locator('details[open]').count());
   await page.goto('http://127.0.0.1:3219/live');
   await page.getByRole('heading',{name:'한눈에 보는 장중 브리핑'}).waitFor();
   await page.getByRole('heading',{name:'왜 이렇게 판단했나요?'}).waitFor();
   await page.getByText('분석 근거·전회 변화·데이터 기준 자세히 보기').click();
   await page.getByRole('heading',{name:'전회 대비 변화'}).waitFor();
   assert.ok(await page.locator('details[open]').count()>=1);
   await page.goto('http://127.0.0.1:3219/daily');
   if(process.env.SCREENSHOT_DIR){await mkdir(process.env.SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:`${process.env.SCREENSHOT_DIR}/daily-${width}.png`,fullPage:true});}
 }
 await page.goto('http://127.0.0.1:3219/archive');
 await page.getByLabel('주제·업종·종목').fill('없는검색어');await page.getByRole('button',{name:'조회',exact:true}).click();
 await page.getByText('아직 발행된 자료가 없습니다').waitFor();
 assert.ok(seen.some(p=>p.get('search_text')?.includes('없는검색어')));
 await page.goto('http://127.0.0.1:3219/live.html');assert.ok(page.url().endsWith('/live'));
 mode='error';await page.goto('http://127.0.0.1:3219/live');await page.getByText('자료를 불러오지 못했습니다').waitFor();
 mode='empty';await page.goto('http://127.0.0.1:3219/daily');await page.getByText('아직 발행된 자료가 없습니다').waitFor();
 assert.deepEqual(errors,[]);
 console.log('UI smoke: desktop/mobile, routes, details, search, redirect, empty/error states passed');
}finally{await browser?.close();app.kill();server.closeAllConnections();await new Promise(r=>server.close(r));}
