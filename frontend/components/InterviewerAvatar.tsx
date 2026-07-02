'use client';
import { useEffect, useRef, useState } from 'react';

interface Props {
  audioEl: HTMLAudioElement | null;
  role?: string; // peer | high_peer | manager
  className?: string;
}

/**
 * Stylized SVG interviewer avatar with a simple time-based mouth animation.
 * We deliberately DO NOT touch the audio element via Web Audio, so nothing
 * we do here can prevent the browser from playing sound out of the speakers.
 */
export default function InterviewerAvatar({ audioEl, role = 'peer', className = '' }: Props) {
  const [mouthOpen, setMouthOpen] = useState(0); // 0..1
  const [eyesClosed, setEyesClosed] = useState(false);
  const [speaking, setSpeaking] = useState(false);

  const rafRef = useRef<number | null>(null);
  const speakingRef = useRef(false);

  useEffect(() => {
    if (!audioEl) return;
    let phase = 0;
    const tick = () => {
      if (!speakingRef.current) return;
      phase += 0.18;
      setMouthOpen(0.3 + Math.abs(Math.sin(phase)) * 0.7);
      rafRef.current = requestAnimationFrame(tick);
    };
    const onPlay = () => {
      speakingRef.current = true;
      setSpeaking(true);
      phase = 0;
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = requestAnimationFrame(tick);
    };
    const onEnd = () => {
      speakingRef.current = false;
      setSpeaking(false);
      setMouthOpen(0);
      if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
    };
    audioEl.addEventListener('play', onPlay);
    audioEl.addEventListener('pause', onEnd);
    audioEl.addEventListener('ended', onEnd);
    return () => {
      audioEl.removeEventListener('play', onPlay);
      audioEl.removeEventListener('pause', onEnd);
      audioEl.removeEventListener('ended', onEnd);
      speakingRef.current = false;
      if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
    };
  }, [audioEl]);

  useEffect(() => {
    let t: any;
    const loop = () => {
      t = setTimeout(() => {
        setEyesClosed(true);
        setTimeout(() => setEyesClosed(false), 140);
        loop();
      }, 2500 + Math.random() * 3000);
    };
    loop();
    return () => clearTimeout(t);
  }, []);

  const style = ROLE_STYLE[role] || ROLE_STYLE.peer;
  const mouthH = 2 + mouthOpen * 14;
  const mouthW = 22 + mouthOpen * 6;

  return (
    <div className={`relative ${className}`}>
      <svg viewBox="0 0 120 96" className="w-full h-full">
        <rect x="0" y="0" width="120" height="96" fill={style.bg} rx="6" />
        <line x1="0" y1="68" x2="120" y2="68" stroke="#00000020" strokeWidth="1" />
        <path d={`M20 96 Q60 60 100 96 Z`} fill={style.shirt} />
        <rect x="52" y="58" width="16" height="10" fill={style.skin} />
        <ellipse cx="60" cy="40" rx="22" ry="26" fill={style.skin} />
        <path d={style.hair} fill={style.hairColor} />
        {style.glasses && (
          <g stroke="#333" strokeWidth="1.2" fill="none">
            <circle cx="50" cy="40" r="6" />
            <circle cx="70" cy="40" r="6" />
            <line x1="56" y1="40" x2="64" y2="40" />
          </g>
        )}
        {eyesClosed ? (
          <>
            <line x1="46" y1="40" x2="54" y2="40" stroke="#222" strokeWidth="1.5" />
            <line x1="66" y1="40" x2="74" y2="40" stroke="#222" strokeWidth="1.5" />
          </>
        ) : (
          <>
            <circle cx="50" cy="40" r="1.6" fill="#222" />
            <circle cx="70" cy="40" r="1.6" fill="#222" />
          </>
        )}
        <line x1="45" y1="34" x2="55" y2="33" stroke="#333" strokeWidth="1.5" strokeLinecap="round" />
        <line x1="65" y1="33" x2="75" y2="34" stroke="#333" strokeWidth="1.5" strokeLinecap="round" />
        <path d="M60 42 L58 48 L62 48 Z" fill="#00000015" />
        <ellipse cx="60" cy="54" rx={mouthW / 2} ry={mouthH / 2} fill="#7a2a2a" />
        {speaking && mouthOpen > 0.2 && (
          <ellipse cx="60" cy={54 + mouthH / 4} rx={mouthW / 3} ry={mouthH / 4} fill="#c04a4a" />
        )}
      </svg>
      <div className="absolute bottom-1 left-1 right-1 flex items-center justify-between text-[10px] font-medium px-1">
        <span className={`px-1.5 py-0.5 rounded ${speaking ? 'bg-red-600 text-white' : 'bg-black/40 text-white'}`}>
          {speaking ? '● 讲话中' : style.label}
        </span>
      </div>
    </div>
  );
}

const ROLE_STYLE: Record<string, {
  label: string;
  bg: string;
  shirt: string;
  skin: string;
  hair: string;
  hairColor: string;
  glasses: boolean;
}> = {
  peer: {
    label: '一面 · Peer',
    bg: '#e8f1fb',
    shirt: '#4b6b9a',
    skin: '#f4d4b5',
    hair: 'M38 30 Q42 14 60 12 Q78 14 82 30 Q78 22 60 22 Q42 22 38 30 Z',
    hairColor: '#2b2b2b',
    glasses: false,
  },
  high_peer: {
    label: '二面 · High Peer',
    bg: '#eef4ea',
    shirt: '#3f6b4f',
    skin: '#efc9a4',
    hair: 'M36 32 Q40 12 60 10 Q80 12 84 32 Q80 18 60 20 Q40 18 36 32 Z',
    hairColor: '#3a2a1c',
    glasses: true,
  },
  manager: {
    label: '三面 · 直属经理',
    bg: '#f6ede0',
    shirt: '#3a3a3a',
    skin: '#e8c5a0',
    hair: 'M40 30 Q46 16 60 16 Q74 16 80 30 Q74 24 60 26 Q46 24 40 30 Z',
    hairColor: '#4a3a2a',
    glasses: true,
  },
};
