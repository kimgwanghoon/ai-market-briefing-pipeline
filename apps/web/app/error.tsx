'use client';
export default function ErrorPage({reset}:{reset:()=>void}){return <div className="empty" role="alert"><h2>자료를 표시하지 못했습니다.</h2><p>잠시 후 다시 시도해 주세요.</p><button onClick={reset}>다시 시도</button></div>}
