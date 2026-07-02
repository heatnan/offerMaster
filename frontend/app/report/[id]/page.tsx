'use client';
import { useEffect, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import ReactMarkdown from 'react-markdown';
import { generateReport, getDetail, fileUrl } from '@/lib/api';

// Report page also acts as the "interview end" page — if the user came here
// from clicking 结束面试, the backend may still be finalizing the round + the
// report generation LLM call is inflight. We poll getDetail() until the
// interview status is terminal AND the report markdown is present, then stop.
const POLL_INTERVAL_MS = 2000;
const POLL_MAX_MS = 180_000; // 3 minutes hard stop

export default function ReportPage() {
  const params = useParams();
  const id = Number(params.id);
  const [markdown, setMarkdown] = useState('');
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMsg, setLoadingMsg] = useState('面试结果生成中，请稍候…');
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<any>(null);
  const stoppedRef = useRef(false);

  useEffect(() => {
    stoppedRef.current = false;
    const startAt = Date.now();
    let attempts = 0;

    async function poll() {
      if (stoppedRef.current) return;
      attempts += 1;
      try {
        const d = await getDetail(id);
        setDetail(d);
        const terminal = d.status === 'completed' || d.status === 'failed';
        if (d.report?.markdown) {
          // Report is ready.
          setMarkdown(d.report.markdown);
          setPdfUrl(fileUrl(d.report.pdf_url));
          setLoading(false);
          return;
        }
        // Report not yet ready. Update loading message and keep polling.
        if (!terminal) {
          setLoadingMsg('面试结束中，正在总结本轮表现…');
        } else {
          setLoadingMsg('面试结果生成中，正在撰写面评报告…');
          // Terminal but no report — nudge the backend to (re)generate on
          // the FIRST attempt only, to avoid spamming the LLM.
          if (attempts === 1) {
            generateReport(id).catch(() => {});
          }
        }
      } catch (e: any) {
        // network hiccups are OK; keep polling until timeout
        console.warn('poll error:', e);
      }
      if (Date.now() - startAt > POLL_MAX_MS) {
        setError('报告生成超时，请刷新页面重试。');
        setLoading(false);
        return;
      }
      setTimeout(poll, POLL_INTERVAL_MS);
    }

    poll();
    return () => { stoppedRef.current = true; };
  }, [id]);

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-lg shadow p-4 flex items-center justify-between">
        <h1 className="text-xl font-bold">面试报告 · #{id}</h1>
        {pdfUrl && (
          <a href={pdfUrl} target="_blank"
            className="bg-blue-600 text-white px-4 py-2 rounded">下载 PDF</a>
        )}
      </div>

      {loading && (
        <div className="bg-white p-6 rounded shadow flex items-center gap-3">
          <span className="inline-block w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
          <span className="text-gray-700">{loadingMsg}</span>
        </div>
      )}
      {error && <div className="text-red-600">{error}</div>}

      {markdown && (
        <div className="bg-white rounded-lg shadow p-6 prose max-w-none">
          <ReactMarkdown>{markdown}</ReactMarkdown>
        </div>
      )}

      {detail?.rounds?.length > 0 && (
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-lg font-semibold mb-3">详细问答</h2>
          {detail.rounds.map((r: any) => (
            <div key={r.id} className="mb-4">
              <div className="font-medium">
                第 {r.round_no} 轮 · {r.role} · 得分 {r.score} · {r.passed ? '通过' : '未通过'}
              </div>
              <ul className="ml-4 mt-2 text-sm space-y-2">
                {r.questions.map((q: any) => (
                  <li key={q.id}>
                    <div className="font-medium">Q{q.seq}. {q.question_text} {q.is_followup && '(追问)'}</div>
                    <div className="text-gray-600">答: {q.answer || '(未作答)'}</div>
                    {q.answer_audio_url && (
                      <audio
                        controls
                        preload="none"
                        src={fileUrl(q.answer_audio_url) || undefined}
                        className="h-8 mt-1"
                      />
                    )}
                    {q.score != null && <div className="text-blue-600">得分 {q.score} · {q.score_comment}</div>}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
