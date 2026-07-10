// Browser calls backend directly (avoid Next.js rewrite proxy timeouts on long STT calls)
const BASE = (typeof window !== 'undefined' ? (process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000') : 'http://backend:8000');

export function fileUrl(path: string | null | undefined): string | null {
  if (!path) return null;
  if (path.startsWith('http')) return path;
  return `${BASE}${path.startsWith('/') ? path : '/' + path}`;
}

async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

export interface Question {
  id: number;
  seq: number;
  topic: string;
  question_text: string;
  is_followup: boolean;
  tts_url?: string | null;
  tts_failed?: boolean;
}

export interface Round {
  id: number;
  round_no: number;
  role: string;
  status: string;
  score: number;
  passed: boolean;
  feedback: string;
}

export async function uploadResume(file: File): Promise<{ text: string }> {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch(`${BASE}/upload/resume`, { method: 'POST', body: fd });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function createInterview(body: {
  position_title: string;
  jd_text: string;
  resume_text: string;
  rounds_planned: number;
  start_round?: number;
}) {
  return jsonFetch<{ id: number; status: string }>('/interviews', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export async function startRound(id: number) {
  return jsonFetch<{
    round: Round;
    question: Question;
    planned_count: number;
  }>(`/interviews/${id}/rounds/start`, { method: 'POST' });
}

export async function stt(blob: Blob): Promise<{ text: string; audio_path?: string }> {
  const fd = new FormData();
  fd.append('file', blob, 'answer.webm');
  const res = await fetch(`${BASE}/voice/stt`, { method: 'POST', body: fd });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

const WS_BASE = (typeof window !== 'undefined'
  ? (process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000').replace(/^http/, 'ws')
  : 'ws://backend:8000');

export interface SttStreamHandle {
  send: (chunk: Blob) => void;
  /** 发送 done 信号并等待 final 返回。成功返回 {text, audio_path}，失败/超时 reject。 */
  finish: (timeoutMs?: number) => Promise<{ text: string; audio_path?: string }>;
  close: () => void;
  /** 是否处于可用状态（未失败/未关闭） */
  isAlive: () => boolean;
}

export function sttStream(
  onInterim: (text: string) => void,
  onError?: (msg: string) => void,
): SttStreamHandle {
  const ws = new WebSocket(`${WS_BASE}/voice/stt-stream`);
  ws.binaryType = 'arraybuffer';

  let alive = true;
  let finalResolve: ((v: { text: string; audio_path?: string }) => void) | null = null;
  let finalReject: ((e: Error) => void) | null = null;
  let finalReceived: { text: string; audio_path?: string } | null = null;

  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data as string);
      if (msg.type === 'interim') {
        onInterim(msg.text);
      } else if (msg.type === 'final') {
        finalReceived = { text: msg.text, audio_path: msg.audio_path };
        if (finalResolve) { finalResolve(finalReceived); finalResolve = null; finalReject = null; }
      } else if (msg.type === 'error') {
        alive = false;
        onError?.(msg.message);
        if (finalReject) { finalReject(new Error(msg.message)); finalReject = null; finalResolve = null; }
      }
    } catch {}
  };
  ws.onerror = () => {
    alive = false;
    onError?.('WebSocket 连接失败');
    if (finalReject) { finalReject(new Error('WebSocket 连接失败')); finalReject = null; finalResolve = null; }
  };
  ws.onclose = () => {
    alive = false;
    if (finalReject && !finalReceived) { finalReject(new Error('WebSocket 已关闭')); finalReject = null; finalResolve = null; }
  };

  return {
    send: (chunk: Blob) => {
      if (ws.readyState === WebSocket.OPEN) {
        chunk.arrayBuffer().then(buf => {
          try { ws.send(buf); } catch {}
        });
      }
    },
    finish: (timeoutMs = 15000) => {
      // 已经拿到 final 就直接返回
      if (finalReceived) return Promise.resolve(finalReceived);
      return new Promise((resolve, reject) => {
        finalResolve = resolve;
        finalReject = reject;
        try {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ done: true }));
          }
        } catch {}
        setTimeout(() => {
          if (finalResolve) {
            const err = new Error('等待流式识别结果超时');
            finalReject?.(err);
            finalResolve = null;
            finalReject = null;
          }
        }, timeoutMs);
      });
    },
    close: () => { try { ws.close(); } catch {} },
    isAlive: () => alive,
  };
}



export interface AnswerResult {
  score: { dimensions: Record<string, number>; total: number; comment: string } | null;
  decision: { action: 'followup' | 'next'; followup_question: string; acknowledgment?: string; reason: string };
  acknowledgment?: string;
  acknowledgment_audio_url?: string | null;
  next_question?: Question;
  round_finished?: boolean;
  round?: Round;
  interview_status?: string;
}

export async function submitAnswer(interviewId: number, body: {
  question_id: number; transcript: string; audio_path?: string; duration_ms?: number;
}) {
  return jsonFetch<AnswerResult>(`/interviews/${interviewId}/answer`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export async function generateReport(id: number) {
  return jsonFetch<{ markdown: string; pdf_url: string }>(`/interviews/${id}/report`, {
    method: 'POST',
  });
}

export async function getQuestion(interviewId: number, questionId: number) {
  return jsonFetch<Question>(`/interviews/${interviewId}/questions/${questionId}`);
}

export async function endInterview(id: number) {
  return jsonFetch<{ interview_status: string; round?: Round; ended_by_user: boolean }>(
    `/interviews/${id}/end`,
    { method: 'POST' },
  );
}

export async function getDetail(id: number) {
  return jsonFetch<any>(`/interviews/${id}/detail`);
}

/** Send a log line to the backend so it shows up in `docker compose logs backend`. */
export function backendLog(tag: string, msg: string) {
  try {
    fetch(`${BASE}/debug/log`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tag, msg }),
      keepalive: true,
    }).catch(() => {});
  } catch {}
}
