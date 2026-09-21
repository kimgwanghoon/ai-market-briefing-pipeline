'use client';
import { useEffect, useTransition } from 'react';
import { useRouter } from 'next/navigation';
export default function Refresh({ auto = false }: { auto?: boolean }) {
  const router = useRouter(); const [pending, start] = useTransition();
  useEffect(() => { if (!auto) return; const timer = setInterval(() => { if (document.visibilityState === 'visible') router.refresh(); }, 60000); return () => clearInterval(timer); }, [auto, router]);
  return <button className="refresh" disabled={pending} onClick={() => start(() => router.refresh())}>{pending ? '확인 중…' : '최신 자료 확인 ↻'}</button>;
}
