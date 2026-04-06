export type ApiEnvelope<T> = {
  state: string;
  error: string | null;
  result: T;
  warnings?: string[];
};

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

async function req<T>(path: string, init?: RequestInit): Promise<ApiEnvelope<T>> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }
  return (await res.json()) as ApiEnvelope<T>;
}

export const api = {
  healthReady: () => req<{ ready: boolean; checks: Record<string, boolean> }>("/health/ready"),
  adaptiveStart: (payload: {
    topic: string;
    preferred_language?: string;
    quality?: "low" | "medium";
    rag_collection_id?: string | null;
    learner_id?: string | null;
  }) => req<{ session_id: string; job_id: string; task_id: string }>("/adaptive/start", { method: "POST", body: JSON.stringify(payload) }),
  ragUpload: async (files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    const res = await fetch(`${API_BASE}/rag/upload`, { method: "POST", body: form });
    if (!res.ok) throw new Error(await res.text());
    return (await res.json()) as ApiEnvelope<{ collection_id: string; num_chunks: number; uploaded_files: string[]; skipped_files: string[] }>;
  },
  ragQuery: (payload: { collection_id: string; question: string; top_k?: number; max_chars?: number }) =>
    req<{ collection_id: string; context: string; sources: Array<{ source: string; chunk_id: string; score: string }> }>("/rag/query", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  coachChat: (payload: {
    question: string;
    topic?: string;
    preferred_language?: string;
    rag_collection_id?: string | null;
    history?: Array<{ role: "user" | "assistant"; text: string }>;
  }) =>
    req<{ answer: string; used_rag: boolean; sources: Array<{ source: string; chunk_id: string; score: string }> }>("/coach/chat", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  startDiagnose: (payload: { topic: string; user_goal?: string; preferred_language?: string }) =>
    req<{ session_id: string; questions: Array<{ id: string; prompt: string }> }>("/session/start", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  submitDiagnose: (payload: {
    session_id: string;
    topic: string;
    answers: Array<{ question_id: string; answer_text: string; confidence_1to5: number }>;
  }) => req<any>("/diagnose/submit", { method: "POST", body: JSON.stringify(payload) }),
  lessonStatus: (taskId: string) => req<any>(`/learn/status/${taskId}`),
  lessonDownloadUrl: (taskId: string) => `${API_BASE}/learn/download/${taskId}`,
  adaptiveStep: (payload: { session_id: string; num_questions: number; force_remediate?: boolean }) =>
    req<any>("/adaptive/step", { method: "POST", body: JSON.stringify(payload) }),
  quizStatus: (taskId: string) => req<any>(`/quiz/status/${taskId}`),
  adaptiveQuiz: (sessionId: string) => req<any>(`/adaptive/quiz/${sessionId}`),
  submitQuiz: (payload: any) => req<any>("/quiz/submit", { method: "POST", body: JSON.stringify(payload) }),
  getSession: (sessionId: string) => req<any>(`/sessions/${sessionId}`),
  remediationSubmit: (payload: any) => req<any>("/adaptive/remediation/submit", { method: "POST", body: JSON.stringify(payload) }),
  reinforcementStart: (payload: {
    session_id: string;
    job_id: string;
    topic: string;
    preferred_language?: string;
    wrong_question_ids: string[];
    num_questions?: number;
  }) => req<{ topic: string; difficulty: string; questions: any[] }>("/quiz/reinforcement/start", {
    method: "POST",
    body: JSON.stringify(payload),
  }),
  reinforcementSubmit: (payload: {
    session_id: string;
    job_id: string;
    topic: string;
    preferred_language?: string;
    answers: Array<{ question_id: string; answer: string }>;
  }) => req<any>("/quiz/reinforcement/submit", { method: "POST", body: JSON.stringify(payload) }),
};
