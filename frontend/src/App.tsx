import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";

function getOrCreateLearnerId(): string {
  const key = "learner_id";
  let id = localStorage.getItem(key);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(key, id);
  }
  return id;
}

const LEARNER_ID = getOrCreateLearnerId();

type DiagnoseQ = { id: string; prompt: string };
type ChatMsg = { role: "user" | "assistant"; text: string };

function renderInlineMarkdown(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, idx) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={idx}>{part.slice(2, -2)}</strong>;
    }
    return <span key={idx}>{part}</span>;
  });
}

function normalizeCoachMessage(text: string): string {
  let cleaned = text.replace(/\r\n/g, "\n").trim();
  cleaned = cleaned.replace(/([.!?])\s+(\d+\.\s+\*\*)/g, "$1\n$2");
  cleaned = cleaned.replace(/\s+-\s+\*\*/g, "\n- **");
  cleaned = cleaned.replace(/\n{3,}/g, "\n\n");
  return cleaned;
}

function renderCoachMessage(text: string) {
  const normalized = normalizeCoachMessage(text);
  const lines = normalized.split("\n").map((l) => l.trim()).filter(Boolean);
  const blocks: JSX.Element[] = [];

  let currentOl: string[] = [];
  let currentUl: string[] = [];

  const flushLists = () => {
    if (currentOl.length) {
      blocks.push(
        <ol key={`ol-${blocks.length}`} className="ai-md-ol">
          {currentOl.map((item, idx) => (
            <li key={idx}>{renderInlineMarkdown(item)}</li>
          ))}
        </ol>,
      );
      currentOl = [];
    }
    if (currentUl.length) {
      blocks.push(
        <ul key={`ul-${blocks.length}`} className="ai-md-ul">
          {currentUl.map((item, idx) => (
            <li key={idx}>{renderInlineMarkdown(item)}</li>
          ))}
        </ul>,
      );
      currentUl = [];
    }
  };

  for (const line of lines) {
    if (line.startsWith("### ")) {
      flushLists();
      blocks.push(
        <h4 key={`h-${blocks.length}`} className="ai-md-h3">
          {renderInlineMarkdown(line.replace(/^###\s+/, ""))}
        </h4>,
      );
      continue;
    }

    const ordered = line.match(/^\d+\.\s+(.*)$/);
    if (ordered) {
      currentUl = [];
      currentOl.push(ordered[1]);
      continue;
    }

    const bullet = line.match(/^-+\s+(.*)$/);
    if (bullet) {
      currentOl = [];
      currentUl.push(bullet[1]);
      continue;
    }

    flushLists();
    blocks.push(
      <p key={`p-${blocks.length}`} className="ai-md-p">
        {renderInlineMarkdown(line)}
      </p>,
    );
  }

  flushLists();
  return <div className="ai-md">{blocks}</div>;
}

export function App() {
  const [topic, setTopic] = useState("");
  const [goal, setGoal] = useState("Build strong intuition with examples");
  const [language, setLanguage] = useState("English");

  const [sessionId, setSessionId] = useState<string>(localStorage.getItem("session_id") || "");
  const [lessonTaskId, setLessonTaskId] = useState("");
  const [jobId, setJobId] = useState("");

  const [questions, setQuestions] = useState<DiagnoseQ[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});

  const [quiz, setQuiz] = useState<any>(null);
  const [quizAnswers, setQuizAnswers] = useState<Record<string, string>>({});
  const [showQuizArea, setShowQuizArea] = useState(false);

  const [remediation, setRemediation] = useState<any>(null);
  const [remAnswers, setRemAnswers] = useState<Record<string, string>>({});

  const [status, setStatus] = useState("Ask what you want to learn.");
  const [details, setDetails] = useState<any>(null);
  const [lessonReady, setLessonReady] = useState(false);
  const [lessonReadyAnnounced, setLessonReadyAnnounced] = useState(false);

  const [ragCollectionId, setRagCollectionId] = useState("");
  const [ragFiles, setRagFiles] = useState<File[]>([]);

  const [readyChecks, setReadyChecks] = useState<Record<string, boolean> | null>(null);

  const [chat, setChat] = useState<ChatMsg[]>([
    { role: "assistant", text: "What would you like to learn today?" },
  ]);
  const [doubtInput, setDoubtInput] = useState("");
  const [isStarted, setIsStarted] = useState(false);
  const [activeTab, setActiveTab] = useState<"coach" | "quiz">("coach");
  const [quizResult, setQuizResult] = useState<any>(null);
  const [showAnswerReview, setShowAnswerReview] = useState(false);

  // Reinforcement round states
  const [reinforcementPhase, setReinforcementPhase] = useState<"none" | "loading" | "active" | "done">("none");
  const [reinforcementQuiz, setReinforcementQuiz] = useState<any>(null);
  const [reinforcementAnswers, setReinforcementAnswers] = useState<Record<string, string>>({});
  const [reinforcementResult, setReinforcementResult] = useState<any>(null);

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const inlineFileInputRef = useRef<HTMLInputElement | null>(null);
  const chatStreamRef = useRef<HTMLDivElement | null>(null);

  const canSubmitDiagnose = useMemo(
    () => questions.length > 0 && questions.every((q) => answers[q.id]?.trim()),
    [questions, answers],
  );

  const systemReady = readyChecks ? Object.values(readyChecks).every(Boolean) : true;

  function pushChat(role: "user" | "assistant", text: string) {
    setChat((prev) => [...prev, { role, text }]);
  }

  useEffect(() => {
    const node = chatStreamRef.current;
    if (!node) return;
    node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
  }, [chat, questions.length]);

  useEffect(() => {
    if (!lessonTaskId || lessonReady) return;

    let closed = false;
    const timerRef = { current: 0 };

    const poll = async () => {
      try {
        const res = await api.lessonStatus(lessonTaskId);
        if (closed) return;

        const terminalFailure = res.state === "FAILURE" || res.state === "REVOKED";
        if (terminalFailure) {
          closed = true;
          window.clearInterval(timerRef.current);
          const errMsg = res.error || res.result?.error || "Lesson generation failed. Please try again.";
          setStatus(`Error: ${errMsg}`);
          setChat((prev) => [...prev, { role: "assistant", text: `Lesson generation failed: ${errMsg}` }]);
          return;
        }

        setDetails(res.result || res);
        setStatus(res.state === "SUCCESS" || res.state === "PARTIAL" ? "Lesson ready" : "Preparing your lesson...");

        if ((res.state === "SUCCESS" || res.state === "PARTIAL") && !lessonReadyAnnounced) {
          setLessonReady(true);
          setLessonReadyAnnounced(true);
          setChat((prev) => [...prev, { role: "assistant", text: "Your lesson is ready! Watch it, then start your quiz." }]);
        }
      } catch {
        if (!closed) setStatus("Preparing your lesson...");
      }
    };

    void poll();
    timerRef.current = window.setInterval(() => void poll(), 3000);
    return () => {
      closed = true;
      window.clearInterval(timerRef.current);
    };
  }, [lessonTaskId, lessonReady, lessonReadyAnnounced]);

  async function onCheckReady() {
    try {
      const res = await api.healthReady();
      setReadyChecks(res.result.checks);
      setStatus(res.result.ready ? "System is ready" : "System checks failed");
    } catch (err: any) {
      setStatus(`Failed: ${err.message}`);
    }
  }

  async function onUploadRag() {
    if (!ragFiles.length) return;
    try {
      const res = await api.ragUpload(ragFiles);
      setRagCollectionId(res.result.collection_id);
      setDetails(res.result);
      pushChat("assistant", `Attached your notes and indexed ${res.result.num_chunks} chunks for context.`);
      setStatus("Notes attached");
    } catch (err: any) {
      setStatus(`RAG upload failed: ${err.message}`);
    }
  }

  async function startAdaptiveDirect(topicText: string) {
    const clean = topicText.trim();
    if (!clean) return;
    setIsStarted(true);

    try {
      setStatus("Preparing your lesson...");
      setTopic(clean);
      pushChat("user", clean);

      const res = await api.adaptiveStart({
        topic: clean,
        preferred_language: language,
        quality: "low",
        rag_collection_id: ragCollectionId || null,
        learner_id: LEARNER_ID,
      });

      setSessionId(res.result.session_id);
      setJobId(res.result.job_id);
      setLessonTaskId(res.result.task_id);
      localStorage.setItem("session_id", res.result.session_id);
      setLessonReady(false);
      setLessonReadyAnnounced(false);
      setShowQuizArea(false);
      setQuiz(null);
      setRemediation(null);
      setQuizResult(null);
      setReinforcementPhase("none");
      setReinforcementQuiz(null);
      setReinforcementResult(null);

      pushChat("assistant", "Great choice! I'm crafting your personalized lesson now.");
    } catch (err: any) {
      setStatus(`Failed: ${err.message}`);
    }
  }

  async function startDiagnosisFlow(topicText: string) {
    const clean = topicText.trim();
    if (!clean) return;
    setIsStarted(true);

    try {
      setStatus("Starting diagnosis...");
      setTopic(clean);
      pushChat("user", clean);

      const res = await api.startDiagnose({ topic: clean, user_goal: goal, preferred_language: language });
      setSessionId(res.result.session_id);
      localStorage.setItem("session_id", res.result.session_id);
      setQuestions(res.result.questions || []);
      setQuiz(null);
      setRemediation(null);
      setLessonReady(false);
      setLessonReadyAnnounced(false);
      setShowQuizArea(false);

      pushChat("assistant", "Let's do a quick diagnosis first. Answer these and I'll tailor your lesson.");
      setStatus("Diagnosis ready");
    } catch (err: any) {
      setStatus(`Failed: ${err.message}`);
    }
  }

  async function onSubmitDiagnose() {
    if (!sessionId || !topic.trim()) return;
    try {
      setStatus("Preparing your lesson...");
      const payload = {
        session_id: sessionId,
        topic,
        answers: questions.map((q) => ({ question_id: q.id, answer_text: answers[q.id] || "", confidence_1to5: 3 })),
      };
      const res = await api.submitDiagnose(payload);
      setLessonTaskId(res.result.learn_task_id);
      setJobId(res.result.learn_job_id);
      setDetails(res.result);
      setLessonReady(false);
      setLessonReadyAnnounced(false);
      pushChat("assistant", "Perfect. Generating your tailored lesson now.");
    } catch (err: any) {
      setStatus(`Failed: ${err.message}`);
    }
  }

  async function onStartQuiz() {
    if (!sessionId) return;
    try {
      const ready = await api.healthReady();
      setReadyChecks(ready.result?.checks || null);
      if (!ready.result?.checks?.worker) {
        throw new Error("Quiz worker is not running. Start the Celery worker and try again.");
      }
      setShowQuizArea(true);
      setActiveTab("quiz");
      await onAdaptiveStep(false);
    } catch (err: any) {
      setStatus(`Failed: ${err.message}`);
      pushChat("assistant", `I couldn't start the quiz yet: ${err.message}`);
    }
  }

  async function onAdaptiveStep(forceRemediate = false) {
    if (!sessionId) return;
    try {
      const QUIZ_POLL_TIMEOUT_MS = 90_000;
      const QUIZ_POLL_INTERVAL_MS = 1400;
      const res = await api.adaptiveStep({ session_id: sessionId, num_questions: 5, force_remediate: forceRemediate });

      if (res.result?.remediation) {
        setRemediation(res.result.remediation);
        setQuiz(null);
        setShowQuizArea(true);
        setStatus("Remediation ready");
        pushChat("assistant", "I prepared a short remediation pack for weak areas.");
        return;
      }

      const taskId = res.result?.task_id;
      if (taskId) {
        setStatus("Preparing your quiz...");
        const startedAt = Date.now();
        let state = "PENDING";
        while (state === "PENDING" || state === "STARTED" || state === "RETRY") {
          const st = await api.quizStatus(taskId);
          state = st.state;

          if (state === "FAILURE" || state === "REVOKED") {
            const err = st.error || st.result?.error || "Quiz generation failed.";
            throw new Error(err);
          }

          if (Date.now() - startedAt > QUIZ_POLL_TIMEOUT_MS) {
            throw new Error("Quiz generation timed out after 90 seconds. Check worker/Redis and try again.");
          }

          await new Promise((r) => setTimeout(r, QUIZ_POLL_INTERVAL_MS));
        }
        const q = await api.adaptiveQuiz(sessionId);
        if (q.error) throw new Error(q.error);
        setQuiz(q.result);
        setRemediation(null);
        setShowQuizArea(true);
        setStatus("Quiz ready");
        pushChat("assistant", "Quiz is ready whenever you are.");
      }
    } catch (err: any) {
      setStatus(`Failed: ${err.message}`);
    }
  }

  async function onSubmitQuiz() {
    if (!sessionId || !quiz) return;
    try {
      const payload = {
        session_id: sessionId,
        job_id: jobId,
        topic,
        preferred_language: language,
        answers: (quiz.questions || []).map((q: any) => ({ question_id: q.id, answer: quizAnswers[q.id] || "" })),
      };
      const res = await api.submitQuiz(payload);
      setDetails(res.result);
      setQuizResult(res.result);
      setShowAnswerReview(false);
      setReinforcementPhase("none");
      setReinforcementQuiz(null);
      setReinforcementResult(null);

      const summary = res.result?.summary;
      const adaptive = res.result?.adaptive?.adaptive_summary;
      const score = summary?.score_percent ?? 0;
      const correct = summary?.correct ?? 0;
      const total = summary?.total_questions ?? 0;
      const suggestions: string[] = adaptive?.suggestions ?? [];

      let msg = `Quiz complete! You got ${correct}/${total} correct (${score}%).`;
      if (suggestions.length) msg += ` ${suggestions[0]}`;
      pushChat("assistant", msg);
      setStatus("Quiz submitted");
    } catch (err: any) {
      setStatus(`Failed: ${err.message}`);
    }
  }

  async function onSubmitRemediation() {
    if (!sessionId || !remediation) return;
    try {
      const payload = {
        session_id: sessionId,
        answers: (remediation.checks || []).map((c: any) => ({ question_id: c.id, answer: remAnswers[c.id] || "" })),
      };
      const res = await api.remediationSubmit(payload);
      setDetails(res.result);
      pushChat("assistant", "Great. I'll reinforce this in your next adaptive step.");
      setStatus("Remediation submitted");
    } catch (err: any) {
      setStatus(`Failed: ${err.message}`);
    }
  }

  async function onStartReinforcement() {
    if (!sessionId || !jobId || !quizResult) return;
    const wrongIds: string[] = (quizResult.results ?? [])
      .filter((r: any) => !r.correct)
      .map((r: any) => r.question_id as string);
    if (!wrongIds.length) return;

    setReinforcementPhase("loading");
    setReinforcementQuiz(null);
    setReinforcementAnswers({});
    setReinforcementResult(null);

    try {
      const res = await api.reinforcementStart({
        session_id: sessionId,
        job_id: jobId,
        topic,
        preferred_language: language,
        wrong_question_ids: wrongIds,
        num_questions: Math.min(wrongIds.length, 3),
      });
      setReinforcementQuiz(res.result);
      setReinforcementPhase("active");
      pushChat("assistant", `🔥 Reinforcement round ready! ${res.result.questions?.length} harder questions targeting your weak areas.`);
    } catch (err: any) {
      setReinforcementPhase("none");
      setStatus(`Reinforcement failed: ${err.message}`);
    }
  }

  async function onSubmitReinforcement() {
    if (!sessionId || !jobId || !reinforcementQuiz) return;
    try {
      const res = await api.reinforcementSubmit({
        session_id: sessionId,
        job_id: jobId,
        topic,
        preferred_language: language,
        answers: (reinforcementQuiz.questions || []).map((q: any) => ({
          question_id: q.id,
          answer: reinforcementAnswers[q.id] || "",
        })),
      });
      setReinforcementResult(res.result);
      setReinforcementPhase("done");

      const summary = res.result?.summary;
      const adaptive = res.result?.adaptive?.adaptive_summary;
      const score = summary?.score_percent ?? 0;
      const correct = summary?.correct ?? 0;
      const total = summary?.total_questions ?? 0;
      const newMastery = Math.round((adaptive?.avg_mastery ?? 0) * 100);
      pushChat("assistant", `Reinforcement done! ${correct}/${total} correct (${score}%). Mastery now at ${newMastery}%.`);
      setStatus("Reinforcement submitted");
    } catch (err: any) {
      setStatus(`Reinforcement submit failed: ${err.message}`);
    }
  }

  async function onRefreshSession() {
    if (!sessionId) return;
    try {
      const res = await api.getSession(sessionId);
      setDetails(res.result);
      setJobId(res.result.job_id || "");
      setStatus("Session loaded");
    } catch (err: any) {
      setStatus(`Failed: ${err.message}`);
    }
  }

  async function onAskDoubt() {
    const q = doubtInput.trim();
    if (!q) return;

    pushChat("user", q);
    setDoubtInput("");
    setStatus("Coach is thinking...");

    try {
      const recentHistory = chat.slice(-8).map((m) => ({ role: m.role, text: m.text }));
      const res = await api.coachChat({
        question: q,
        topic: topic || undefined,
        preferred_language: language,
        rag_collection_id: ragCollectionId || null,
        history: recentHistory,
      });
      pushChat("assistant", res.result.answer || "I can help with that. Tell me which part you want to break down first.");
      setStatus("Coach replied");
    } catch (err: any) {
      pushChat("assistant", "I hit a temporary issue, but I’m here. Ask again and I’ll explain it step by step.");
      setStatus(`Coach unavailable: ${err.message}`);
    }
  }

  const removeRagFile = (name: string) => {
    setRagFiles((prev) => prev.filter((f) => f.name !== name));
  };

  const videoUrl = lessonTaskId ? api.lessonDownloadUrl(lessonTaskId) : "";

  // ─── Hero View ───────────────────────────────────────────────────────────────
  if (!isStarted) {
    return (
      <div className="view-hero">
        <div className="hero-bg" aria-hidden="true">
          <div className="orb orb-1" />
          <div className="orb orb-2" />
          <div className="orb orb-3" />
          <div className="grid-overlay" />
        </div>

        <div className="floating-pills" aria-hidden="true">
          <div className="pill-float pill-float-1">🧬 Genetics</div>
          <div className="pill-float pill-float-2">⚛️ Quantum Physics</div>
          <div className="pill-float pill-float-3">💻 React Native</div>
          <div className="pill-float pill-float-4">📈 Macroeconomics</div>
        </div>

        <main className="hero-main">
          <div className="hero-badge stagger-1">
            <span className="badge-dot pulse-dot" />
            <span>AI-Powered Adaptive Learning</span>
          </div>

          <div className="hero-card stagger-2">
            <h1 className="hero-title">
              Master any topic<br />
              <span className="text-gradient">in minutes.</span>
            </h1>
            <p className="hero-desc">
              Enter what you want to learn. We generate a personalized video, adaptive quizzes, and a 1-on-1 AI coach specifically for you.
            </p>

            <div className="input-container stagger-3">
              <div className="input-glow-bg" />
              <input
                type="text"
                className="hero-input"
                placeholder="e.g. Backpropagation in Neural Networks"
                value={topic}
                autoComplete="off"
                onChange={(e) => setTopic(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") void startAdaptiveDirect(topic); }}
              />
              <button
                className="input-submit-btn"
                onClick={() => void startAdaptiveDirect(topic)}
                aria-label="Start Learning"
              >
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <line x1="5" y1="12" x2="19" y2="12" />
                  <polyline points="12 5 19 12 12 19" />
                </svg>
              </button>
            </div>

            {/* Notes attach strip */}
            <div style={{ marginTop: 24, display: "flex", alignItems: "center", gap: 8, justifyContent: "center", flexWrap: "wrap" }}>
              <button className="btn-attach-mini" onClick={() => fileInputRef.current?.click()}>
                📎 Attach notes (optional)
              </button>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept=".txt,.md,.pdf"
                className="hidden-input"
                onChange={(e) => setRagFiles(Array.from(e.target.files || []))}
              />
              {ragFiles.length > 0 && (
                <button className="btn-upload-mini" onClick={onUploadRag}>
                  Upload ({ragFiles.length})
                </button>
              )}
            </div>
            {ragFiles.length > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, justifyContent: "center", marginTop: 8 }}>
                {ragFiles.map((f) => (
                  <span key={f.name} className="chip">
                    {f.name}
                    <button className="chip-x" onClick={() => removeRagFile(f.name)}>×</button>
                  </span>
                ))}
              </div>
            )}
          </div>
        </main>
      </div>
    );
  }

  // ─── Workspace View ──────────────────────────────────────────────────────────
  return (
    <div className="view-workspace">
      {/* Header */}
      <header className="workspace-header">
        <div className="nav-logo">
          <div className="logo-icon">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M12 2L2 7l10 5 10-5-10-5z" />
              <path d="M2 17l10 5 10-5" />
              <path d="M2 12l10 5 10-5" />
            </svg>
          </div>
          <span className="logo-text">Adaptive Tutor</span>
        </div>

        <div className="current-topic-badge">
          <span className="recording-dot" />
          <span>{topic || "Learning workspace"}</span>
        </div>

        <div className="avatar">
          <img src="https://api.dicebear.com/7.x/notionists/svg?seed=Felix" alt="User Avatar" />
        </div>
      </header>

      {/* Main layout */}
      <div className="layout-split">
        {/* Left pane */}
        <div className="pane pane-left">
          {/* Video */}
          <div className="video-container panel">
            {lessonReady ? (
              <video className="video-actual" controls src={videoUrl} />
            ) : (
              <>
                <div className="ambient-video-glow" />
                <div className="video-overlay">
                  <div className="spinner-modern" />
                  <p className="loading-text fade-in-out">{status}</p>
                </div>
                <div className="video-controls">
                  <div className="play-btn" />
                  <div className="progress-bar">
                    <div className="progress-fill shimmer" />
                  </div>
                  <div className="time">0:00 / 0:00</div>
                </div>
              </>
            )}
          </div>

          {/* Lesson Blueprint */}
          <div className="lesson-notes panel module-enter">
            <div className="panel-header">
              <h3>Lesson Blueprint</h3>
              <span className="kicker-label">{lessonReady ? "Ready" : "Live"}</span>
            </div>
            <div className="notes-content">
              <div className="note-item">
                <div className="check-circle">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                </div>
                <p>{lessonTaskId ? "Topic analysis complete" : "Waiting to start…"}</p>
              </div>
              <div className="note-item">
                <div className={`check-circle ${lessonReady ? "" : "pending"}`}>
                  {lessonReady && (
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  )}
                </div>
                <p>Rendering visuals and audio</p>
              </div>
              {lessonReady ? (
                <div className="note-item">
                  <div className="check-circle">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  </div>
                  <p>Lesson ready — quiz unlocked</p>
                </div>
              ) : (
                <div className="note-skeleton" />
              )}
            </div>
            {lessonReady && (
              <button
                className="btn btn-primary start-quiz-btn"
                onClick={() => void onStartQuiz()}
                disabled={!sessionId}
              >
                Start Knowledge Check
              </button>
            )}
          </div>
        </div>

        {/* Right pane */}
        <div className="pane pane-right panel module-enter">
          <div className="tabs">
            {/* Tab bar */}
            <div className="tabs-list">
              <div
                className="tabs-indicator"
                style={{ transform: activeTab === "quiz" ? "translateX(calc(100% + 8px))" : "translateX(0)" }}
              />
              <button
                className={`tab-trigger ${activeTab === "coach" ? "active" : ""}`}
                aria-selected={activeTab === "coach"}
                onClick={() => setActiveTab("coach")}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                </svg>
                AI Coach
              </button>
              <button
                className={`tab-trigger ${activeTab === "quiz" ? "active" : ""}`}
                aria-selected={activeTab === "quiz"}
                onClick={() => setActiveTab("quiz")}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                  <polyline points="22 4 12 14.01 9 11.01" />
                </svg>
                Knowledge Check
              </button>
            </div>

            <div className="tab-content-area">
              {/* AI Coach tab */}
              <div className={`tab-panel ${activeTab === "coach" ? "active" : ""}`}>
                <div className="chat-container">
                  <div className="chat-history" ref={chatStreamRef}>
                    {chat.map((m, idx) => (
                      <div
                        key={idx}
                        className={`chat-bubble bubble-enter ${m.role === "user" ? "user-bubble" : "ai-bubble"}`}
                      >
                        {m.role === "assistant" && (
                          <div className="bubble-avatar ai">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                              <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
                            </svg>
                          </div>
                        )}
                        <div className={`bubble-text ${m.role === "user" ? "user-text" : ""}`}>
                          {m.role === "assistant" ? renderCoachMessage(m.text) : m.text}
                        </div>
                      </div>
                    ))}

                    {/* Diagnosis questions */}
                    {questions.length > 0 && (
                      <div className="diagnosis-flow">
                        <h4 className="accent-text">Quick Diagnosis</h4>
                        {questions.map((q) => (
                          <div key={q.id} className="diag-card">
                            <p><b>{q.id}</b> {q.prompt}</p>
                            <textarea
                              rows={2}
                              value={answers[q.id] || ""}
                              onChange={(e) => setAnswers((prev) => ({ ...prev, [q.id]: e.target.value }))}
                            />
                          </div>
                        ))}
                        <button
                          className="btn btn-primary"
                          disabled={!canSubmitDiagnose}
                          onClick={onSubmitDiagnose}
                        >
                          Submit Diagnosis
                        </button>
                      </div>
                    )}
                  </div>

                  {/* File chips */}
                  {ragFiles.length > 0 && (
                    <div className="chip-row-chat">
                      {ragFiles.map((f) => (
                        <span key={f.name} className="chip">
                          {f.name}
                          <button className="chip-x" onClick={() => removeRagFile(f.name)}>×</button>
                        </span>
                      ))}
                    </div>
                  )}

                  {/* Chat input */}
                  <div className="chat-input-block">
                    <div className="chat-attach-row">
                      <button className="btn-attach-mini" onClick={() => inlineFileInputRef.current?.click()}>
                        📎 Attach notes
                      </button>
                      <input
                        ref={inlineFileInputRef}
                        type="file"
                        multiple
                        accept=".txt,.md,.pdf"
                        className="hidden-input"
                        onChange={(e) => setRagFiles(Array.from(e.target.files || []))}
                      />
                      {ragFiles.length > 0 && (
                        <button className="btn-upload-mini" onClick={onUploadRag}>
                          Upload
                        </button>
                      )}
                    </div>
                    <div className="chat-input-wrapper">
                      <input
                        type="text"
                        className="chat-input"
                        placeholder="Ask your AI coach…"
                        value={doubtInput}
                        onChange={(e) => setDoubtInput(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Enter") void onAskDoubt(); }}
                      />
                      <button className="btn btn-icon btn-send" onClick={onAskDoubt} aria-label="Send message">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <line x1="22" y1="2" x2="11" y2="13" />
                          <polygon points="22 2 15 22 11 13 2 9 22 2" />
                        </svg>
                      </button>
                    </div>
                  </div>
                </div>
              </div>

              {/* Knowledge Check tab */}
              <div className={`tab-panel ${activeTab === "quiz" ? "active" : ""}`}>
                <div className="quiz-container">
                  {/* Locked state */}
                  {!showQuizArea && !quizResult && (
                    <div className="quiz-empty-state">
                      <div className="lock-icon pulse-soft">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
                          <path d="M7 11V7a5 5 0 0 1 10 0v4" />
                        </svg>
                      </div>
                      <h4>Assessments Locked</h4>
                      <p>Complete the video lesson to unlock your personalized knowledge check.</p>
                      {lessonReady && (
                        <button
                          className="btn btn-primary"
                          style={{ marginTop: 20 }}
                          onClick={() => void onStartQuiz()}
                          disabled={!sessionId}
                        >
                          Start Quiz Now
                        </button>
                      )}
                    </div>
                  )}

                  {/* Quiz result panel */}
                  {quizResult && (() => {
                    const summary = quizResult.summary ?? {};
                    const adaptive = quizResult.adaptive?.adaptive_summary ?? {};
                    const results: any[] = quizResult.results ?? [];
                    const score: number = summary.score_percent ?? 0;
                    const correct: number = summary.correct ?? 0;
                    const total: number = summary.total_questions ?? 0;
                    const avg: number = adaptive.avg_mastery ?? 0;
                    const nextAction: string = adaptive.next_action ?? "NEXT_QUIZ";
                    const suggestions: string[] = adaptive.suggestions ?? [];
                    const weak: string[] = adaptive.weakest_skills ?? [];
                    const wrongIds = results.filter((r) => !r.correct).map((r) => r.question_id);

                    const scoreLevel = score >= 80 ? "excellent" : score >= 45 ? "good" : "needs-work";
                    const scoreLabelMap: Record<string, string> = { excellent: "Excellent!", good: "Good effort", "needs-work": "Keep practising" };

                    // After reinforcement: use updated mastery from reinforcement result
                    const finalAdaptive = reinforcementResult?.adaptive?.adaptive_summary ?? adaptive;
                    const finalAvg: number = finalAdaptive.avg_mastery ?? avg;
                    const finalAction: string = finalAdaptive.next_action ?? nextAction;
                    const finalSuggestions: string[] = finalAdaptive.suggestions ?? suggestions;

                    return (
                      <div className="quiz-active-content">
                        {/* Score card */}
                        <div className={`result-score-card result-${scoreLevel}`}>
                          <div className="result-score-header">
                            <span className="result-score-icon">
                              {scoreLevel === "excellent" ? "🏆" : scoreLevel === "good" ? "📈" : "💡"}
                            </span>
                            <div>
                              <p className="result-score-label">{scoreLabelMap[scoreLevel]}</p>
                              <p className="result-score-number">{correct}/{total} correct — {score}%</p>
                            </div>
                          </div>
                          <div className="result-mastery-bar">
                            <div className="result-mastery-fill" style={{ width: `${Math.round(avg * 100)}%` }} />
                          </div>
                          <p className="result-mastery-label">
                            Mastery: {Math.round(avg * 100)}% · {adaptive.difficulty_level ?? "—"} level
                          </p>
                        </div>

                        {/* Weak areas */}
                        {weak.length > 0 && reinforcementPhase === "none" && (
                          <div className="result-section">
                            <p className="result-section-title">Areas to strengthen</p>
                            <div className="result-weak-chips">
                              {weak.map((s) => (
                                <span key={s} className="result-weak-chip">{s}</span>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* Suggestions (only before reinforcement) */}
                        {suggestions.length > 0 && reinforcementPhase === "none" && (
                          <div className="result-section">
                            <p className="result-section-title">Suggestions</p>
                            <ul className="result-suggestions">
                              {suggestions.map((s, i) => (
                                <li key={i}>{s}</li>
                              ))}
                            </ul>
                          </div>
                        )}

                        {/* Per-question review (toggle, only before reinforcement) */}
                        {reinforcementPhase === "none" && (
                          <>
                            <button
                              className="btn-toggle-review"
                              onClick={() => setShowAnswerReview((v) => !v)}
                            >
                              {showAnswerReview ? "▲ Hide answer review" : "▼ Review answers"}
                            </button>

                            {showAnswerReview && results.map((r: any) => (
                              <div key={r.question_id} className={`result-question-row ${r.correct ? "row-correct" : "row-wrong"}`}>
                                <div className="result-q-indicator">{r.correct ? "✓" : "✗"}</div>
                                <div className="result-q-body">
                                  <p className="result-q-id">{r.question_id}</p>
                                  {r.feedback && <p className="result-q-feedback">{r.feedback}</p>}
                                </div>
                              </div>
                            ))}
                          </>
                        )}

                        {/* ── Reinforcement: launch button ── */}
                        {reinforcementPhase === "none" && wrongIds.length > 0 && (
                          <>
                            <div className="rein-divider">deepen your understanding</div>
                            <button
                              className="btn-reinforce"
                              onClick={() => void onStartReinforcement()}
                              disabled={!sessionId}
                            >
                              🔥 Practice harder questions on weak areas
                              <span style={{ marginLeft: "auto", fontSize: 12, opacity: 0.8 }}>
                                {Math.min(wrongIds.length, 3)} questions
                              </span>
                            </button>
                          </>
                        )}

                        {/* ── Reinforcement: loading ── */}
                        {reinforcementPhase === "loading" && (
                          <div className="quiz-loading" style={{ padding: "24px 0" }}>
                            <div className="spinner-modern" />
                            <p>Generating harder questions on your weak areas…</p>
                          </div>
                        )}

                        {/* ── Reinforcement: active questions ── */}
                        {reinforcementPhase === "active" && reinforcementQuiz && (
                          <>
                            <div className="reinforcement-header">
                              <span className="rein-icon">🔥</span>
                              <div>
                                <p className="rein-title">
                                  Reinforcement Round
                                  <span className={`difficulty-badge ${reinforcementQuiz.difficulty}`}>
                                    {reinforcementQuiz.difficulty}
                                  </span>
                                </p>
                                <p className="rein-sub">Harder questions targeting the concepts you missed</p>
                              </div>
                            </div>

                            {(reinforcementQuiz.questions || []).map((q: any) => (
                              <div key={q.id} className="question-card">
                                <p>
                                  <b>{q.id}</b>{" "}
                                  {q.difficulty && <span className="difficulty-pill">{q.difficulty}</span>}
                                  {q.prompt}
                                </p>
                                {(q.options || []).map((o: any) => (
                                  <label key={o.id} className="option-line">
                                    <input
                                      type="radio"
                                      name={`rein-${q.id}`}
                                      value={o.id}
                                      checked={reinforcementAnswers[q.id] === o.id}
                                      onChange={() => setReinforcementAnswers((prev) => ({ ...prev, [q.id]: o.id }))}
                                    />
                                    <span>{o.id}. {o.text}</span>
                                  </label>
                                ))}
                              </div>
                            ))}

                            <div className="submit-bar">
                              <button
                                className="btn btn-primary"
                                onClick={() => void onSubmitReinforcement()}
                                disabled={
                                  (reinforcementQuiz.questions || []).some(
                                    (q: any) => !reinforcementAnswers[q.id]
                                  )
                                }
                              >
                                Submit Reinforcement
                              </button>
                            </div>
                          </>
                        )}

                        {/* ── Reinforcement: result + updated mastery ── */}
                        {reinforcementPhase === "done" && reinforcementResult && (() => {
                          const rs = reinforcementResult.summary ?? {};
                          const rScore: number = rs.score_percent ?? 0;
                          const rCorrect: number = rs.correct ?? 0;
                          const rTotal: number = rs.total_questions ?? 0;
                          const rScoreLevel = rScore >= 80 ? "excellent" : rScore >= 45 ? "good" : "needs-work";
                          const rLabelMap: Record<string, string> = { excellent: "Great improvement!", good: "Getting better!", "needs-work": "Keep at it!" };
                          return (
                            <>
                              <div className="reinforcement-result-header">
                                ✅ Reinforcement complete — {rCorrect}/{rTotal} correct ({rScore}%)
                              </div>
                              <div className={`result-score-card result-${rScoreLevel}`}>
                                <div className="result-score-header">
                                  <span className="result-score-icon">
                                    {rScoreLevel === "excellent" ? "🚀" : rScoreLevel === "good" ? "📈" : "💪"}
                                  </span>
                                  <div>
                                    <p className="result-score-label">{rLabelMap[rScoreLevel]}</p>
                                    <p className="result-score-number">Updated mastery: {Math.round(finalAvg * 100)}%</p>
                                  </div>
                                </div>
                                <div className="result-mastery-bar">
                                  <div className="result-mastery-fill" style={{ width: `${Math.round(finalAvg * 100)}%` }} />
                                </div>
                                <p className="result-mastery-label">
                                  Mastery: {Math.round(finalAvg * 100)}% · {finalAdaptive.difficulty_level ?? "—"} level
                                </p>
                              </div>

                              {/* Updated suggestions */}
                              {finalSuggestions.length > 0 && (
                                <div className="result-section">
                                  <p className="result-section-title">What's next</p>
                                  <ul className="result-suggestions">
                                    {finalSuggestions.map((s, i) => (
                                      <li key={i}>{s}</li>
                                    ))}
                                  </ul>
                                </div>
                              )}
                            </>
                          );
                        })()}

                        {/* Next step CTA — only show after reinforcement or if none needed */}
                        {(reinforcementPhase === "done" || (reinforcementPhase === "none" && wrongIds.length === 0)) && (
                          <div className="result-cta">
                            {finalAction === "REMEDIATE" && (
                              <button
                                className="btn btn-primary"
                                onClick={() => { setQuizResult(null); void onAdaptiveStep(false); }}
                                disabled={!sessionId}
                              >
                                📚 Start Review Session
                              </button>
                            )}
                            {finalAction === "NEXT_QUIZ" && (
                              <button
                                className="btn btn-primary"
                                onClick={() => { setQuizResult(null); setQuiz(null); void onAdaptiveStep(false); }}
                                disabled={!sessionId}
                              >
                                → Next Quiz
                              </button>
                            )}
                            {finalAction === "ADVANCE" && (
                              <div className="result-advance">
                                <p>🎉 You've mastered this topic!</p>
                              </div>
                            )}
                          </div>
                        )}

                        {/* If reinforcement phase is "none" and there are wrong answers, show skip CTA below reinforce button */}
                        {reinforcementPhase === "none" && wrongIds.length > 0 && (
                          <div className="result-cta" style={{ marginTop: 8 }}>
                            {nextAction === "REMEDIATE" && (
                              <button
                                className="btn"
                                style={{ background: "transparent", color: "var(--text-subtle)", border: "1px solid var(--border-light)", borderRadius: 10, padding: "8px 16px", fontSize: 13, cursor: "pointer" }}
                                onClick={() => { setQuizResult(null); void onAdaptiveStep(false); }}
                                disabled={!sessionId}
                              >
                                Skip → Start Review Session
                              </button>
                            )}
                            {nextAction === "NEXT_QUIZ" && (
                              <button
                                className="btn"
                                style={{ background: "transparent", color: "var(--text-subtle)", border: "1px solid var(--border-light)", borderRadius: 10, padding: "8px 16px", fontSize: 13, cursor: "pointer" }}
                                onClick={() => { setQuizResult(null); setQuiz(null); void onAdaptiveStep(false); }}
                                disabled={!sessionId}
                              >
                                Skip → Next Quiz
                              </button>
                            )}
                            {nextAction === "ADVANCE" && (
                              <div className="result-advance">
                                <p>🎉 You've mastered this topic!</p>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })()}

                  {/* Active quiz questions */}
                  {showQuizArea && !quizResult && (
                    <div className="quiz-active-content">
                      {!quiz && !remediation && (
                        <div className="quiz-loading">
                          <div className="spinner-modern" />
                          <p>Preparing your quiz…</p>
                        </div>
                      )}

                      {quiz && (
                        <>
                          {(quiz.questions || []).map((q: any) => (
                            <div key={q.id} className="question-card">
                              <p>
                                <b>{q.id}</b>{" "}
                                {q.difficulty && <span className="difficulty-pill">{q.difficulty}</span>}
                                {q.prompt}
                              </p>
                              {(q.options || []).map((o: any) => (
                                <label key={o.id} className="option-line">
                                  <input
                                    type="radio"
                                    name={q.id}
                                    value={o.id}
                                    checked={quizAnswers[q.id] === o.id}
                                    onChange={() => setQuizAnswers((prev) => ({ ...prev, [q.id]: o.id }))}
                                  />
                                  <span>{o.id}. {o.text}</span>
                                </label>
                              ))}
                            </div>
                          ))}
                          <div className="submit-bar">
                            <button className="btn btn-primary" onClick={onSubmitQuiz}>Submit Quiz</button>
                          </div>
                        </>
                      )}

                      {remediation && (
                        <>
                          <ul className="bullets">
                            {(remediation.bullets || []).map((b: string, i: number) => <li key={i}>{b}</li>)}
                          </ul>
                          {(remediation.checks || []).map((c: any) => (
                            <div key={c.id} className="question-card">
                              <p><b>{c.id}</b> {c.prompt}</p>
                              {(c.options || []).map((o: any) => (
                                <label key={o.id} className="option-line">
                                  <input
                                    type="radio"
                                    name={`r-${c.id}`}
                                    value={o.id}
                                    checked={remAnswers[c.id] === o.id}
                                    onChange={() => setRemAnswers((prev) => ({ ...prev, [c.id]: o.id }))}
                                  />
                                  <span>{o.id}. {o.text}</span>
                                </label>
                              ))}
                            </div>
                          ))}
                          <div className="submit-bar">
                            <button className="btn btn-primary" onClick={onSubmitRemediation}>Submit Remediation</button>
                          </div>
                        </>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Developer Tools */}
      <details className="dev-tools">
        <summary>Developer Tools</summary>
        <div className="dev-body">
          <div className="dev-meta">
            <span className="chip">session {sessionId || "none"}</span>
            <span className="chip">job {jobId || "none"}</span>
            <span className="chip">rag {ragCollectionId || "none"}</span>
            <span className={`chip ${systemReady ? "" : ""}`}>system {systemReady ? "ready" : "pending"}</span>
          </div>
          <div className="dev-actions">
            <button className="dev-btn" onClick={onCheckReady}>Check Ready</button>
            <button className="dev-btn" onClick={onRefreshSession} disabled={!sessionId}>Load Session</button>
            <button className="dev-btn" onClick={() => void onAdaptiveStep(false)} disabled={!sessionId}>Run Next Step</button>
            <button className="dev-btn" onClick={() => void onAdaptiveStep(true)} disabled={!sessionId}>Force Remediation</button>
          </div>
          {readyChecks && (
            <pre className="compact-pre">{JSON.stringify(readyChecks, null, 2)}</pre>
          )}
          <pre className="compact-pre">{JSON.stringify({ sessionId, jobId, ragCollectionId, status, details }, null, 2)}</pre>
        </div>
      </details>
    </div>
  );
}
