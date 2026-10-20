'use client';

import {useState} from 'react';

export function NewsThumbnail({src, href, title}: {src?:string; href?:string; title:string}) {
  const [failed, setFailed] = useState<string>();
  let image: string;
  try {
    const parsed = new URL(src || '');
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password) return null;
    image = parsed.href;
  } catch { return null; }
  if (failed === image) return null;
  // Publisher thumbnails are loaded directly, without proxying arbitrary URLs
  // through the application server. A failed image leaves a text-only article.
  const thumbnail = <img src={image} alt="" width={112} height={84} loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={()=>setFailed(image)}/>;
  return href ? <a className="news-thumbnail" href={href} target="_blank" rel="noopener noreferrer" aria-label={`${title} 원문 보기`}>{thumbnail}</a> : <span className="news-thumbnail">{thumbnail}</span>;
}
