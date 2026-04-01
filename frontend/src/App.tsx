import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";

type DiagnoseQ = { id: string; prompt: string };
type ChatMsg = { role: "user" | "assistant"; text: string };

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

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const inlineFileInputRef = useRef<HTMLInputElement | null>(null);
  const chatStreamRef = useRef<HTMLDivElement | null>(null);

  const canSubmitDiagnose = useMemo(
    () => questions.length > 0 && questions.every((q) => answers[q.id]?.trim()),
    [questions, answers],
  );

  const systemReady = readyChecks ? Object.values(readyChecks).every(Boolean) : true;
  const lessonPhase = lessonReady ? "Lesson ready" : lessonTaskId ? "Preparing lesson" : "Planning lesson";
  const lessonHelperText = lessonReady
    ? "Watch the lesson first, then move into the quiz below."
    : "Your video appears here as soon as the lesson render finishes.";
  const tutorHelperText = questions.length
    ? "Answer the quick diagnosis and I will tailor the lesson."
    : "Ask for explanations, attach notes, or use the lesson as your anchor.";

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
    const poll = async () => {
      try {
        const res = await api.lessonStatus(lessonTaskId);
        if (closed) return;

        setDetails(res.result || res);
        setStatus(res.state === "SUCCESS" || res.state === "PARTIAL" ? "Lesson ready" : "Preparing your lesson...");

        if ((res.state === "SUCCESS" || res.state === "PARTIAL") && !lessonReadyAnnounced) {
          setLessonReady(true);
          setLessonReadyAnnounced(true);
          setChat((prev) => [...prev, { role: "assistant", text: "Your lesson is ready. Watch it on the left, then start your quiz." }]);
        }
      } catch {
        if (!closed) setStatus("Preparing your lesson...");
      }
    };

    void poll();
    const timer = window.setInterval(() => void poll(), 3000);
    return () => {
      closed = true;
      window.clearInterval(timer);
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

      pushChat("assistant", "Great choice. I'm preparing your lesson now.");
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
      pushChat("assistant", "Perfect. I'm generating your lesson now.");
    } catch (err: any) {
      setStatus(`Failed: ${err.message}`);
    }
  }

  async function onStartQuiz() {
    if (!sessionId) return;
    setShowQuizArea(true);
    await onAdaptiveStep(false);
  }

  async function onAdaptiveStep(forceRemediate = false) {
    if (!sessionId) return;
    try {
      const res = await api.adaptiveStep({ session_id: sessionId, num_questions: 8, force_remediate: forceRemediate });

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
        let state = "PENDING";
        while (state === "PENDING" || state === "STARTED" || state === "RETRY") {
          const st = await api.quizStatus(taskId);
          state = st.state;
          await new Promise((r) => setTimeout(r, 1400));
        }
        const q = await api.adaptiveQuiz(sessionId);
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
        attempt_no: 1,
        job_id: jobId,
        topic,
        preferred_language: language,
        answers: (quiz.questions || []).map((q: any) => ({ question_id: q.id, answer: quizAnswers[q.id] || "" })),
      };
      const res = await api.submitQuiz(payload);
      setDetails(res.result);
      pushChat("assistant", "Nice work. I'll use this to adjust your next step.");
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

    if (ragCollectionId) {
      try {
        const res = await api.ragQuery({ collection_id: ragCollectionId, question: q, top_k: 4, max_chars: 1200 });
        pushChat("assistant", res.result.context || "I couldn't find direct context, but I can explain it another way.");
        return;
      } catch {
        // fallback
      }
    }

    pushChat("assistant", "Got it. I'll reinforce this during the lesson.");
  }

  const removeRagFile = (name: string) => {
    setRagFiles((prev) => prev.filter((f) => f.name !== name));
  };

  const videoUrl = lessonTaskId ? api.lessonDownloadUrl(lessonTaskId) : "";

  if (!isStarted) {
    return (
      <div className="prestartWrap">
        <div className="heroGlow heroGlowLeft" />
        <div className="heroGlow heroGlowRight" />
        <div className="heroCard">
          <div className="heroTop">
            <p className="kicker">Adaptive Tutor</p>
            <span className={`heroStatus ${systemReady ? "ok" : "off"}`}>{systemReady ? "Ready" : "Checks pending"}</span>
          </div>

          <div className="heroCopy">
            <h1>What would you like to learn today?</h1>
            <p className="subtle">Start with a topic. The workspace opens after you send it.</p>
          </div>

          <div className="heroComposer">
            <input
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void startAdaptiveDirect(topic);
              }}
              placeholder="What would you like to learn today?"
            />
            <div className="heroRow">
              <button onClick={() => void startAdaptiveDirect(topic)}>Start Learning</button>
              <button className="ghost" onClick={() => void startDiagnosisFlow(topic)}>Start with Diagnosis</button>
            </div>
          </div>

          <div className="attachStrip">
            <button className="ghost attachGhost" onClick={() => fileInputRef.current?.click()}>Attach notes (optional)</button>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".txt,.md,.pdf"
              className="hiddenInput"
              onChange={(e) => setRagFiles(Array.from(e.target.files || []))}
            />
            <button className="ghost" disabled={!ragFiles.length} onClick={onUploadRag}>Upload</button>
          </div>

          {ragFiles.length ? (
            <div className="chipRow">
              {ragFiles.map((f) => (
                <span key={f.name} className="chip">
                  {f.name}
                  <button className="chipX" onClick={() => removeRagFile(f.name)}>x</button>
                </span>
              ))}
            </div>
          ) : null}

          <div className="suggestRow">
            <span className="subtle">Popular starts</span>
            <button className="suggestChip" onClick={() => void startAdaptiveDirect("Binary Search")}>Binary Search</button>
            <button className="suggestChip" onClick={() => void startAdaptiveDirect("Photosynthesis")}>Photosynthesis</button>
            <button className="suggestChip" onClick={() => void startAdaptiveDirect("Quadratic Formula")}>Quadratic Formula</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="uiRoot">
      <header className="workspaceHeader">
        <div>
          <p className="kicker">Adaptive Tutor</p>
          <h1>{topic || "Learning workspace"}</h1>
          <p className="subtle">{status}</p>
        </div>
        <div className="workspaceBadge">
          <span className={`statusDot ${systemReady ? "ok" : "off"}`} aria-hidden="true" />
          <span>{lessonPhase}</span>
        </div>
      </header>

      <div className="layoutSplit">
        <section className="leftPane">
          <div className={`lessonSticky ${showQuizArea ? "inactive" : ""}`}>
            <article className="panel lessonPanel">
              <div className="panelHead lessonHead">
                <div>
                  <p className="kicker">Lesson</p>
                  <h2>{topic || "Personalized lesson"}</h2>
                  <p className="subtle">{lessonHelperText}</p>
                </div>
                <span className={`workspacePill ${lessonReady ? "ready" : "pending"}`}>{lessonReady ? "Ready" : "Rendering"}</span>
              </div>

              <div className="videoShell">
                {lessonReady ? (
                  <video className="videoLarge" controls src={videoUrl} />
                ) : (
                  <div className="videoLoading">
                    <span className="spinner" />
                    <p>Preparing your lesson...</p>
                  </div>
                )}
              </div>

              <div className="lessonActions">
                <div className="lessonMeta">
                  <span className="metaLabel">{lessonReady ? "Video is ready" : "Generating visuals and audio"}</span>
                  <span className="subtle">{showQuizArea ? "Quiz is open below the lesson." : "Start the quiz when you finish the lesson."}</span>
                </div>
                <button onClick={onStartQuiz} disabled={!sessionId || !lessonReady}>Start Quiz</button>
              </div>
            </article>
          </div>

          {showQuizArea ? (
            <article className="panel quizPanel">
              <div className="panelHead quizHead">
                <div>
                  <p className="kicker">Quiz</p>
                  <h2>Check understanding</h2>
                  <p className="subtle">Questions and remediation stay in the normal page flow so they remain fully readable.</p>
                </div>
              </div>

              {quiz ? (
                <div>
                  {(quiz.questions || []).map((q: any) => (
                    <div key={q.id} className="questionCard">
                      <p><b>{q.id}</b> <span className="pill">{q.difficulty}</span> {q.prompt}</p>
                      {(q.options || []).map((o: any) => (
                        <label key={o.id} className="optionLine">
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
                  <div className="submitBar"><button onClick={onSubmitQuiz}>Submit Quiz</button></div>
                </div>
              ) : null}

              {remediation ? (
                <div>
                  <ul className="bullets">{(remediation.bullets || []).map((b: string, i: number) => <li key={i}>{b}</li>)}</ul>
                  {(remediation.checks || []).map((c: any) => (
                    <div key={c.id} className="questionCard">
                      <p><b>{c.id}</b> {c.prompt}</p>
                      {(c.options || []).map((o: any) => (
                        <label key={o.id} className="optionLine">
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
                  <div className="submitBar"><button onClick={onSubmitRemediation}>Submit Remediation</button></div>
                </div>
              ) : null}

              {!quiz && !remediation ? <p className="subtle">Quiz will appear here once ready.</p> : null}
            </article>
          ) : null}
        </section>

        <section className="rightPane">
          <article className="panel chatPanel">
            <div className="panelHead">
              <div>
                <p className="kicker">AI Tutor</p>
                <h2>Ask while you learn</h2>
                <p className="subtle">{tutorHelperText}</p>
              </div>
              <span className="workspaceBadge compact">
                <span className={`statusDot ${systemReady ? "ok" : "off"}`} aria-label="system status" />
                <span>{ragCollectionId ? "Notes connected" : "Tutor ready"}</span>
              </span>
            </div>

            <div ref={chatStreamRef} className="chatStream">
              {chat.map((m, idx) => (
                <div key={idx} className={`bubble ${m.role}`}>{m.text}</div>
              ))}
            </div>

            {questions.length > 0 ? (
              <div className="diagnosisFlow">
                <h3>Quick diagnosis</h3>
                {questions.map((q) => (
                  <div key={q.id} className="diagCard">
                    <p><b>{q.id}</b> {q.prompt}</p>
                    <textarea rows={2} value={answers[q.id] || ""} onChange={(e) => setAnswers((prev) => ({ ...prev, [q.id]: e.target.value }))} />
                  </div>
                ))}
                <button disabled={!canSubmitDiagnose} onClick={onSubmitDiagnose}>Submit Diagnosis</button>
              </div>
            ) : null}

            <div className="assistRow">
              <span className="subtle">Bring in your notes if you want the tutor grounded to them.</span>
              <div className="attachRow inlineAttach">
                <button className="ghost" onClick={() => inlineFileInputRef.current?.click()}>Attach notes</button>
                <input
                  ref={inlineFileInputRef}
                  type="file"
                  multiple
                  accept=".txt,.md,.pdf"
                  className="hiddenInput"
                  onChange={(e) => setRagFiles(Array.from(e.target.files || []))}
                />
                <button className="ghost" disabled={!ragFiles.length} onClick={onUploadRag}>Upload</button>
              </div>
            </div>

            {ragFiles.length ? (
              <div className="chipRow compact">
                {ragFiles.map((f) => (
                  <span key={f.name} className="chip">
                    {f.name}
                    <button className="chipX" onClick={() => removeRagFile(f.name)}>x</button>
                  </span>
                ))}
              </div>
            ) : null}

            <div className="chatComposer">
              <p className="subtle">Pinned tutor input</p>
              <div className="chatInputRow">
                <input
                  value={doubtInput}
                  onChange={(e) => setDoubtInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void onAskDoubt();
                  }}
                  placeholder="Ask your tutor a doubt..."
                />
                <button onClick={onAskDoubt}>Send</button>
              </div>
            </div>
          </article>

          <details className="panel devTools">
            <summary>Developer Tools</summary>
            <div className="devBody">
              <div className="devMeta">
                <span className="chip">session {sessionId || "none"}</span>
                <span className="chip">job {jobId || "none"}</span>
                <span className="chip">rag {ragCollectionId || "none"}</span>
              </div>
              <div className="rowBtns">
                <button className="ghost" onClick={onCheckReady}>Check Ready</button>
                <button className="ghost" onClick={onRefreshSession}>Load Session</button>
                <button className="ghost" onClick={() => void onAdaptiveStep(false)} disabled={!sessionId}>Run Next Step</button>
                <button className="ghost" onClick={() => void onAdaptiveStep(true)} disabled={!sessionId}>Force Remediation</button>
              </div>
              {readyChecks ? (
                <pre className="compactPre">{JSON.stringify(readyChecks, null, 2)}</pre>
              ) : null}
              <pre className="compactPre">{JSON.stringify({ sessionId, jobId, ragCollectionId, status, details }, null, 2)}</pre>
            </div>
          </details>
        </section>
      </div>
    </div>
  );
}
