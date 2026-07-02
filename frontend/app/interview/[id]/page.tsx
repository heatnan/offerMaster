'use client';
import { useEffect, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import {
  startRound, stt, submitAnswer, getDetail, endInterview, getQuestion,
  fileUrl, Question, Round, backendLog,
} from '@/lib/api';
import InterviewerAvatar from '@/components/InterviewerAvatar';

type Turn = {
  question: Question;
  answer?: string;
  score?: number;
  comment?: string;
};

type MicState = 'idle' | 'listening' | 'countdown' | 'paused';

// End-of-answer detection thresholds.
// Tuned for candidates who pause mid-answer to think. Previous values (5s + 2s)
// often auto-submitted while the candidate was still gathering their thoughts.
const SILENCE_MS = 12000;     // 12s of "quiet mic" -> start countdown
const COUNTDOWN_MS = 4000;    // 4s grace countdown before auto-submit (extendable by speaking)
// Lower threshold so soft speech / trailing words also count as "still talking".
// 0.02 was too aggressive — normal breathing pauses fell below it.
const VOLUME_THRESHOLD = 0.008;

function getSpeechRecognition(): any {
  if (typeof window === 'undefined') return null;
  return (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition || null;
}

export default function InterviewPage() {
  const params = useParams();
  const router = useRouter();
  const interviewId = Number(params.id);

  const [round, setRound] = useState<Round | null>(null);
  const [currentQ, setCurrentQ] = useState<Question | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [transcript, setTranscript] = useState('');
  const [interimText, setInterimText] = useState('');
  const [micState, setMicState] = useState<MicState>('idle');
  const [countdownLeft, setCountdownLeft] = useState(0);
  const [volume, setVolume] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [roundEnd, setRoundEnd] = useState<{ passed: boolean; feedback: string; interview_status: string } | null>(null);
  const [audioReady, setAudioReady] = useState(false);
  const [needGesture, setNeedGesture] = useState(false); // autoplay blocked, waiting for user click
  const [waitingForClick, setWaitingForClick] = useState(true); // show "start" gate to unlock autoplay

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const cameraStreamRef = useRef<MediaStream | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const startedRef = useRef(false);
  // Prevents doSubmit from being invoked twice concurrently (auto-submit +
  // manual click race, React double-invoke in strict mode, etc). setBusy is
  // async so we can't rely on the `busy` state to guard re-entry.
  const submittingRef = useRef(false);

  // Web Speech Recognition
  const recognitionRef = useRef<any>(null);
  // Fallback MediaRecorder
  const mrRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const useWebSpeechRef = useRef<boolean>(false);

  // VAD: audio level monitoring
  const micStreamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const rafRef = useRef<number | null>(null);
  const lastVoiceAtRef = useRef<number>(Date.now());
  const silenceTimerRef = useRef<any>(null);
  const countdownTimerRef = useRef<any>(null);
  const transcriptRef = useRef<string>('');
  // Accumulator for all finalized speech chunks across restart cycles of the recognizer.
  // The recognizer's event.results resets when the session restarts, so we cannot rely on it.
  const finalAccumRef = useRef<string>('');
  const currentQIdRef = useRef<number | null>(null);
  const micStateRef = useRef<MicState>('idle');

  useEffect(() => { transcriptRef.current = transcript; }, [transcript]);
  useEffect(() => { currentQIdRef.current = currentQ?.id ?? null; }, [currentQ?.id]);
  useEffect(() => { micStateRef.current = micState; }, [micState]);

  useEffect(() => {
    useWebSpeechRef.current = !!getSpeechRecognition();
  }, []);

  // Restore or start
  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    (async () => {
      try {
        setBusy(true);
        const detail = await getDetail(interviewId);
        const runningRound = detail.rounds?.find((r: any) => r.status === 'running');
        if (runningRound) {
          const unanswered = runningRound.questions.find((q: any) => !q.answer);
          if (unanswered) {
            setRound(runningRound);
            setCurrentQ({
              id: unanswered.id, seq: unanswered.seq, topic: unanswered.topic,
              question_text: unanswered.question_text, is_followup: unanswered.is_followup,
              tts_url: unanswered.tts_url,
            });
            setTurns(runningRound.questions.map((qq: any) => ({
              question: {
                id: qq.id, seq: qq.seq, topic: qq.topic, question_text: qq.question_text,
                is_followup: qq.is_followup, tts_url: qq.tts_url,
              },
              answer: qq.answer || undefined,
              score: qq.score || undefined,
              comment: qq.score_comment || undefined,
            })));
            return;
          }
        }
        const data = await startRound(interviewId);
        setRound(data.round);
        setCurrentQ(data.question);
        setTurns([{ question: data.question }]);
      } catch (e: any) {
        setError(e.message);
      } finally { setBusy(false); }
    })();
    return () => {
      cleanupMic();
      cameraStreamRef.current?.getTracks().forEach(t => t.stop());
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [interviewId]);

  // Poll TTS URL if not yet ready
  useEffect(() => {
    if (!currentQ || currentQ.tts_url) return;
    let stopped = false;
    let attempts = 0;
    const tick = async () => {
      if (stopped || attempts++ > 30) return;
      try {
        const q = await getQuestion(interviewId, currentQ.id);
        if (q.tts_url) {
          setCurrentQ(cur => (cur && cur.id === q.id ? { ...cur, tts_url: q.tts_url } : cur));
          return;
        }
      } catch {}
      setTimeout(tick, 1500);
    };
    tick();
    return () => { stopped = true; };
  }, [currentQ?.id, currentQ?.tts_url, interviewId]);

  // When a new question arrives with TTS ready: play audio, and auto-start mic when audio ends
  useEffect(() => {
    if (!currentQ?.tts_url || !audioRef.current) return;
    if (waitingForClick) return; // defer until user clicks Start
    // reset answer state
    setTranscript('');
    setInterimText('');
    finalAccumRef.current = '';
    setMicState('idle');
    cleanupMic();

    const a = audioRef.current;
    a.src = fileUrl(currentQ.tts_url) || '';
    const onEnded = () => {
      // auto start listening if we're still on this question and not paused
      if (currentQIdRef.current === currentQ.id && !roundEnd) {
        startListening();
      }
    };
    a.onended = onEnded;
    a.play().catch(() => {
      // Autoplay blocked — show a "click to play" prompt, then start listening anyway
      setNeedGesture(true);
      onEnded();
    });
    return () => { a.onended = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentQ?.tts_url, currentQ?.id, waitingForClick]);

  // Whenever the interviewer audio starts playing (initial autoplay, replay,
  // whatever) we must pause the mic — otherwise the speaker's audio bleeds
  // into the recording and gets transcribed as the candidate's answer.
  // NOTE: we intentionally do NOT hook `ended` here — the effect above already
  // installs an `a.onended` handler that calls startListening. Adding a second
  // ended listener would cause two concurrent MediaRecorders to spin up on the
  // same chunksRef, producing a corrupt interleaved WebM blob that Whisper
  // cannot decode ("Invalid data found when processing input").
  useEffect(() => {
    const a = audioRef.current;
    if (!a) return;
    const onPlay = () => {
      if (micStateRef.current === 'listening' || micStateRef.current === 'countdown') {
        cleanupMic();
      }
    };
    a.addEventListener('play', onPlay);
    return () => { a.removeEventListener('play', onPlay); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [audioReady]);

  // ---------- Mic / listening ----------

  async function startListening() {
    if (micStateRef.current !== 'idle' && micStateRef.current !== 'paused') return;
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      micStreamRef.current = stream;

      // set up Web Audio for VAD
      const AC = (window as any).AudioContext || (window as any).webkitAudioContext;
      const ctx = new AC();
      audioCtxRef.current = ctx;
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
      analyserRef.current = analyser;

      lastVoiceAtRef.current = Date.now();
      monitorVolume();
      startSilenceWatcher();

      // Record whole answer for Whisper (authoritative transcript on submit).
      // No live speech recognition on screen — cleaner UX than the flickering preview.
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : 'audio/webm';
      const mr = new MediaRecorder(stream, { mimeType });
      chunksRef.current = [];
      mr.ondataavailable = e => { if (e.data && e.data.size > 0) chunksRef.current.push(e.data); };
      mrRef.current = mr;
      mr.start(1000); // flush chunk every 1s so onstop has full data

      setMicState('listening');
    } catch (e: any) {
      setError('无法访问麦克风：' + e.message);
      setMicState('idle');
    }
  }

  /**
   * Stop MediaRecorder and wait for the final audio blob.
   * Returns null if there was no audio.
   */
  function stopRecorderAndGetBlob(): Promise<Blob | null> {
    return new Promise(resolve => {
      const mr = mrRef.current;
      if (!mr || mr.state === 'inactive') {
        const blob = chunksRef.current.length ? new Blob(chunksRef.current, { type: 'audio/webm' }) : null;
        resolve(blob);
        return;
      }
      mr.onstop = () => {
        const blob = chunksRef.current.length ? new Blob(chunksRef.current, { type: 'audio/webm' }) : null;
        resolve(blob);
      };
      try { mr.stop(); } catch { resolve(null); }
    });
  }

  function monitorVolume() {
    const analyser = analyserRef.current;
    if (!analyser) return;
    const buf = new Uint8Array(analyser.fftSize);
    const loop = () => {
      analyser.getByteTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) {
        const v = (buf[i] - 128) / 128;
        sum += v * v;
      }
      const rms = Math.sqrt(sum / buf.length);
      setVolume(rms);
      if (rms > VOLUME_THRESHOLD) {
        lastVoiceAtRef.current = Date.now();
        cancelCountdown();
      }
      rafRef.current = requestAnimationFrame(loop);
    };
    rafRef.current = requestAnimationFrame(loop);
  }

  function startSilenceWatcher() {
    if (silenceTimerRef.current) clearInterval(silenceTimerRef.current);
    silenceTimerRef.current = setInterval(() => {
      if (micStateRef.current !== 'listening') return;
      const idle = Date.now() - lastVoiceAtRef.current;
      // Since we no longer show a live transcript, gate auto-submit on "we actually recorded some audio"
      const hasAudio = chunksRef.current.length > 1; // at least 2 seconds of recording
      if (idle >= SILENCE_MS && hasAudio) {
        beginCountdown();
      }
    }, 300);
  }

  function beginCountdown() {
    if (micStateRef.current === 'countdown') return;
    setMicState('countdown');
    let left = COUNTDOWN_MS;
    setCountdownLeft(left);
    countdownTimerRef.current = setInterval(() => {
      left -= 100;
      setCountdownLeft(Math.max(left, 0));
      if (left <= 0) {
        clearInterval(countdownTimerRef.current);
        countdownTimerRef.current = null;
        autoSubmit();
      }
    }, 100);
  }

  function cancelCountdown() {
    if (countdownTimerRef.current) {
      clearInterval(countdownTimerRef.current);
      countdownTimerRef.current = null;
    }
    if (micStateRef.current === 'countdown') {
      setMicState('listening');
      setCountdownLeft(0);
    }
  }

  function pauseListening() {
    // Manually pause auto-submit: keep recording but don't auto-submit
    try { recognitionRef.current?.stop?.(); } catch {}
    try { mrRef.current?.stop?.(); } catch {}
    if (silenceTimerRef.current) clearInterval(silenceTimerRef.current);
    if (countdownTimerRef.current) clearInterval(countdownTimerRef.current);
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    micStreamRef.current?.getTracks().forEach(t => t.stop());
    audioCtxRef.current?.close().catch(() => {});
    setMicState('paused');
  }

  function cleanupMic() {
    try { recognitionRef.current?.abort?.(); } catch {}
    try { mrRef.current?.stop?.(); } catch {}
    recognitionRef.current = null;
    mrRef.current = null;
    if (silenceTimerRef.current) clearInterval(silenceTimerRef.current);
    if (countdownTimerRef.current) clearInterval(countdownTimerRef.current);
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    silenceTimerRef.current = null;
    countdownTimerRef.current = null;
    rafRef.current = null;
    micStreamRef.current?.getTracks().forEach(t => t.stop());
    micStreamRef.current = null;
    audioCtxRef.current?.close().catch(() => {});
    audioCtxRef.current = null;
    analyserRef.current = null;
    setInterimText('');
    setCountdownLeft(0);
    setVolume(0);
    setMicState('idle');
  }

  async function autoSubmit() {
    // Called by the silence-countdown finalizer
    await doSubmit(true);
  }

  async function doSubmit(auto = false) {
    if (!currentQ) return;
    // Guard against double submission. Auto-submit (silence countdown) and a
    // manual click can otherwise both fire; the backend then answers the same
    // question twice, each response inserts a NEW follow-up, and the UI
    // ends up showing multiple "next" questions from one answer.
    if (submittingRef.current) return;
    submittingRef.current = true;
    try {
      await _doSubmitInner(auto);
    } finally {
      submittingRef.current = false;
    }
  }

  async function _doSubmitInner(auto = false) {
    if (!currentQ) return;
    // Text may come from: (a) Whisper on the recorded audio (primary), or
    // (b) manual typing in the textarea (fallback / edit).
    const typedText = (transcriptRef.current || '').trim();
    const hasRecording = mrRef.current !== null || chunksRef.current.length > 0;
    if (!typedText && !hasRecording) {
      if (auto) { cancelCountdown(); return; }
      setError('请先录音或输入回答');
      return;
    }

    setBusy(true); setError(null);

    let finalText = typedText;
    let answerAudioPath: string | undefined;
    try {
      const blob = await stopRecorderAndGetBlob();
      if (blob && blob.size > 1000) {
        try {
          const { text, audio_path } = await stt(blob);
          const whisperText = (text || '').trim();
          if (whisperText) finalText = whisperText;
          if (audio_path) answerAudioPath = audio_path;
        } catch (e: any) {
          console.warn('Whisper failed:', e);
          if (!typedText) {
            setBusy(false);
            setError('语音转写失败，请在下方手动输入回答后再次提交。');
            cleanupMic();
            return;
          }
          setError('语音转写失败，已使用你手动输入的文字。');
        }
      }
    } finally {
      cleanupMic();
    }

    if (!finalText) {
      setBusy(false);
      setError('未识别到语音内容，请重新录音或手动输入');
      return;
    }

    // Reflect final text in the textarea so user sees exactly what will be submitted
    setTranscript(finalText);

    try {
      const res = await submitAnswer(interviewId, {
        question_id: currentQ.id, transcript: finalText,
        audio_path: answerAudioPath,
      });
      setTurns(prev => {
        const arr = [...prev];
        const idx = arr.findIndex(t => t.question.id === currentQ.id);
        if (idx >= 0) {
          arr[idx] = {
            ...arr[idx],
            answer: finalText,
            score: res.score?.total,
            comment: res.score?.comment,
          };
        }
        if (res.next_question) arr.push({ question: res.next_question });
        return arr;
      });
      setTranscript('');
      finalAccumRef.current = '';
      if (res.next_question) {
        setCurrentQ(res.next_question);
      } else if (res.round_finished && res.round) {
        setCurrentQ(null);
        setRound(res.round);
        setRoundEnd({
          passed: res.round.passed,
          feedback: res.round.feedback,
          interview_status: res.interview_status || '',
        });
      }
    } catch (e: any) {
      setError(e.message);
    } finally { setBusy(false); }
  }

  // ---------- misc UI actions ----------

  async function enableCamera() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
      cameraStreamRef.current = stream;
      if (videoRef.current) videoRef.current.srcObject = stream;
    } catch (e: any) {
      setError('摄像头启用失败：' + e.message);
    }
  }

  async function onNextRound() {
    setRoundEnd(null);
    setTurns([]);
    setBusy(true);
    try {
      const data = await startRound(interviewId);
      setRound(data.round);
      setCurrentQ(data.question);
      setTurns([{ question: data.question }]);
    } catch (e: any) {
      setError(e.message);
    } finally { setBusy(false); }
  }

  function onSeeReport() {
    router.push(`/report/${interviewId}`);
  }

  async function onEndInterview() {
    if (!confirm('确定要结束这场面试吗？系统会立即根据已回答的问题生成面评。')) return;
    cleanupMic();
    // Fire-and-forget: kick off end-interview on the backend (it will
    // finalize + generate the report inline, which can take 10-30s), and
    // immediately navigate to the report page. The report page shows a
    // loading state until interview.status is terminal and the report is
    // ready.
    endInterview(interviewId).catch(e => {
      console.warn('endInterview background call failed:', e);
    });
    router.push(`/report/${interviewId}`);
  }

  // ---------- render ----------

  const combined = transcript + (interimText ? (transcript ? ' ' : '') + interimText : '');
  const volumePct = Math.min(100, Math.round(volume * 300));

  // Overlay to unlock autoplay: browsers require a user gesture before audio can play.
  // We show a full-screen start button the first time; once clicked audio is unlocked.
  const startOverlay = waitingForClick ? (
    <div className="fixed inset-0 flex flex-col items-center justify-center bg-white z-50 gap-6">
      <div className="text-2xl font-bold text-gray-800">准备好了吗？</div>
      <div className="text-gray-500 text-sm">点击开始，面试官将立即向你提问（含语音）</div>
      <button
        className="bg-blue-600 hover:bg-blue-700 text-white px-10 py-4 rounded-xl text-lg font-semibold"
        onClick={async () => {
          setWaitingForClick(false);
          // Now we have a user gesture — if audio src is already set, play it
          const a = audioRef.current;
          if (a && a.src && a.src !== window.location.href) {
            try { await a.play(); } catch {}
          }
        }}
      >
        开始面试
      </button>
    </div>
  ) : null;

  return (
    <div className="space-y-4">
      {/* Visible audio player with native controls — bypasses autoplay policy issues.
          The user can always click the browser's play button to hear the question. */}
      <div className="bg-blue-50 border border-blue-200 rounded p-3">
        <div className="text-xs text-blue-800 mb-1">面试官语音（如果没有自动播放，请点击下方播放按钮）</div>
        <audio
          ref={el => { audioRef.current = el; if (el && !audioReady) setAudioReady(true); }}
          controls
          autoPlay
          className="w-full"
        />
      </div>
      {startOverlay}

      {needGesture && (
        <div className="bg-yellow-50 border border-yellow-300 rounded p-3 flex items-center justify-between text-sm">
          <span className="text-yellow-800">浏览器阻止了自动播放。点击按钮播放面试官提问语音。</span>
          <button
            className="ml-4 bg-yellow-500 text-white px-3 py-1 rounded"
            onClick={() => { audioRef.current?.play(); setNeedGesture(false); }}
          >播放语音</button>
        </div>
      )}

      <div className="bg-white rounded-lg shadow p-4 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <div className="w-40 h-32 rounded overflow-hidden border border-gray-200">
            <InterviewerAvatar audioEl={audioRef.current} role={round?.role} className="w-full h-full" />
          </div>
          <div>
            <div className="font-semibold">
              第 {round?.round_no ?? '-'} 轮 · {roleLabel(round?.role)}
            </div>
            <div className="text-sm text-gray-500">
              面试 ID: {interviewId}
              {' · '}
              {useWebSpeechRef.current ? '实时语音识别' : '录音识别（Whisper 兜底）'}
            </div>
          </div>
        </div>
        <div className="flex gap-2 items-center">
          {!roundEnd && (
            <button onClick={onEndInterview} disabled={busy}
              className="text-sm border border-red-300 text-red-600 hover:bg-red-50 px-3 py-1 rounded disabled:opacity-50">
              结束面试
            </button>
          )}
          {!cameraStreamRef.current && (
            <button onClick={enableCamera} className="text-sm border px-3 py-1 rounded">启用摄像头</button>
          )}
          <video ref={videoRef} autoPlay muted className="w-32 h-24 bg-black rounded" />
        </div>
      </div>

      <div className="bg-white rounded-lg shadow p-4 space-y-3">
        {turns.map((t, i) => (
          <div key={t.question.id} className="border-l-4 border-blue-500 pl-3">
            <div className="text-sm text-gray-500">Q{i + 1} {t.question.is_followup && '· 追问'} · {t.question.topic}</div>
            <div className="font-medium">{t.question.question_text}</div>
            {t.answer && (
              <div className="mt-2 text-sm bg-gray-50 rounded p-2">
                <div className="text-gray-500">你的回答：</div>
                <div>{t.answer}</div>
                {typeof t.score === 'number' && (
                  <div className="mt-1 text-blue-600">得分 {t.score} · {t.comment}</div>
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      {currentQ && !roundEnd && (
        <div className="bg-white rounded-lg shadow p-4 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <MicIndicator state={micState} volumePct={volumePct} />
              <div className="text-sm">
                {micState === 'idle' && (currentQ.tts_url ? '面试官讲完后自动开始录音…' : '语音合成中…')}
                {micState === 'listening' && '正在录音，说完停约 12 秒会自动提交；建议答完手动点"立即提交"。'}
                {micState === 'countdown' && (
                  <span className="text-orange-600 font-medium">
                    检测到你已停止说话，{Math.ceil(countdownLeft / 1000)} 秒后自动提交…（继续说可取消）
                  </span>
                )}
                {micState === 'paused' && '已暂停自动提交。'}
              </div>
            </div>
            <div className="flex gap-2">
              {micState === 'listening' && (
                <button onClick={() => doSubmit(false)} disabled={busy}
                  className="bg-blue-600 text-white px-4 py-2 rounded disabled:opacity-50">
                  已答完，立即提交
                </button>
              )}
              {micState === 'countdown' && (
                <>
                  <button onClick={cancelCountdown}
                    className="border px-3 py-2 rounded text-sm">再想想</button>
                  <button onClick={() => doSubmit(false)}
                    className="bg-blue-600 text-white px-4 py-2 rounded">立即提交</button>
                </>
              )}
              {micState === 'paused' && (
                <button onClick={startListening}
                  className="bg-red-600 text-white px-4 py-2 rounded">继续录音</button>
              )}
              {(micState === 'listening' || micState === 'countdown') && (
                <button onClick={pauseListening}
                  className="border px-3 py-2 rounded text-sm">暂停</button>
              )}
              {currentQ.tts_url && (
                <button onClick={() => audioRef.current?.play()}
                  className="border px-3 py-2 rounded text-sm">重放问题</button>
              )}
            </div>
          </div>

          {/* Show textarea only after we have transcript (either Whisper output or user typing). */}
          {(micState === 'idle' || micState === 'paused' || transcript) && (
            <textarea className="w-full border rounded px-3 py-2 h-32"
              value={transcript}
              onChange={e => { setTranscript(e.target.value); lastVoiceAtRef.current = Date.now(); cancelCountdown(); }}
              placeholder="录音提交后，Whisper 转写结果会显示在这里。你也可以在此手动编辑或直接输入。" />
          )}
          {micState === 'listening' && (
            <div className="text-xs text-gray-500 border rounded p-3 bg-gray-50">
              录音中… 屏幕不显示实时字幕（浏览器实时识别不准），提交后会用后端 Whisper 转写完整音频。
            </div>
          )}
        </div>
      )}

      {roundEnd && (
        <div className="bg-white rounded-lg shadow p-4 space-y-3">
          <h2 className="text-lg font-bold">
            本轮{roundEnd.passed ? '通过' : '结束'} · 得分 {round?.score}
          </h2>
          <p className="text-gray-700 whitespace-pre-wrap">{roundEnd.feedback}</p>
          <div className="flex gap-2">
            {roundEnd.interview_status === 'round_finished' && (
              <button onClick={onNextRound}
                className="bg-blue-600 text-white px-4 py-2 rounded">进入下一轮</button>
            )}
            {(roundEnd.interview_status === 'completed' || roundEnd.interview_status === 'failed') && (
              <button onClick={onSeeReport}
                className="bg-green-600 text-white px-4 py-2 rounded">查看面试报告</button>
            )}
          </div>
        </div>
      )}

      {busy && <div className="text-sm text-gray-500">处理中…</div>}
      {error && <div className="text-red-600 text-sm">{error}</div>}
    </div>
  );
}

function MicIndicator({ state, volumePct }: { state: MicState; volumePct: number }) {
  const base = 'w-12 h-12 rounded-full flex items-center justify-center transition-colors';
  const color = {
    idle: 'bg-gray-200 text-gray-500',
    listening: 'bg-red-500 text-white animate-pulse',
    countdown: 'bg-orange-500 text-white',
    paused: 'bg-yellow-300 text-yellow-900',
  }[state];
  return (
    <div className="relative">
      <div className={`${base} ${color}`}>
        {/* mic icon */}
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-6 h-6">
          <path d="M12 14a3 3 0 003-3V6a3 3 0 10-6 0v5a3 3 0 003 3z" />
          <path d="M19 11a1 1 0 10-2 0 5 5 0 01-10 0 1 1 0 10-2 0 7 7 0 006 6.92V21h-3a1 1 0 100 2h8a1 1 0 100-2h-3v-3.08A7 7 0 0019 11z" />
        </svg>
      </div>
      {state === 'listening' && (
        <div className="absolute -bottom-1 left-1/2 -translate-x-1/2 h-1 rounded-full bg-red-500"
          style={{ width: `${Math.max(8, volumePct)}%`, minWidth: 8 }} />
      )}
    </div>
  );
}

function roleLabel(role?: string) {
  return { peer: '一面 · Peer', high_peer: '二面 · High Peer', manager: '三面 · 直属经理' }[role || ''] || role || '-';
}
