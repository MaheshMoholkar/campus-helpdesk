import { useEffect, useRef, useState } from "react";
import { type Citation, type Session, login, sendFeedback, streamChat } from "./api";

export type ChatWidgetProps = {
  apiUrl: string;
  studentApiUrl: string;
  title?: string;
};

type Message = {
  id: string;
  from: "user" | "assistant";
  text: string;
  citations: Citation[];
  turnId?: string;
  outcome?: string;
  pendingActionId?: string;
  rated?: 1 | -1;
  streaming?: boolean;
};

const DEMO_USERS = [
  { id: "S1001", label: "Aarav (student, Engineering)" },
  { id: "S1002", label: "Meera (student, Arts & Science)" },
  { id: "S1003", label: "Kabir (student, Commerce)" },
  { id: "T2001", label: "Dr. Deshmukh (staff, Engineering)" },
];

const SUGGESTIONS = ["What is the revaluation fee?", "hostel ka gate kitne baje band hota hai?", "What is my fee due?"];

let nextId = 0;
const newId = () => `m${++nextId}`;

/** The one chat component: used full-page and, wrapped, as the embeddable widget. */
export function ChatWidget({ apiUrl, studentApiUrl, title = "Campus Helpdesk" }: ChatWidgetProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [session, setSession] = useState<Session | null>(null);
  const [conversationId, setConversationId] = useState<string>();
  const [loginError, setLoginError] = useState("");
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [messages]);

  const update = (id: string, change: Partial<Message> | ((m: Message) => Partial<Message>)) =>
    setMessages((all) =>
      all.map((m) => (m.id === id ? { ...m, ...(typeof change === "function" ? change(m) : change) } : m)),
    );

  async function ask(question: string, pendingActionId?: string) {
    if (!question.trim() || busy) return;
    const replyId = newId();
    setMessages((all) => [
      ...all,
      { id: newId(), from: "user", text: question, citations: [] },
      { id: replyId, from: "assistant", text: "", citations: [], streaming: true },
    ]);
    setInput("");
    setBusy(true);
    try {
      for await (const event of streamChat(apiUrl, {
        question,
        token: session?.token,
        conversationId,
        pendingActionId,
      })) {
        if (event.type === "meta") setConversationId(event.conversation_id);
        else if (event.type === "delta") update(replyId, (m) => ({ text: m.text + event.text }));
        else if (event.type === "citations") update(replyId, { citations: event.items });
        else if (event.type === "done")
          update(replyId, { turnId: event.turn_id, outcome: event.outcome, pendingActionId: event.pending_action_id });
        else if (event.type === "error") throw new Error(event.message);
      }
    } catch (error) {
      update(replyId, { text: `Sorry, something went wrong. ${(error as Error).message}` });
    } finally {
      update(replyId, { streaming: false });
      setBusy(false);
    }
  }

  async function signIn(username: string) {
    setLoginError("");
    try {
      setSession(await login(studentApiUrl, username, "password"));
      setConversationId(undefined); // a new identity starts a new conversation
      setMessages([]);
    } catch (error) {
      setLoginError((error as Error).message);
    }
  }

  function signOut() {
    setSession(null);
    setConversationId(undefined);
    setMessages([]);
  }

  async function rate(message: Message, rating: 1 | -1) {
    if (!message.turnId || message.rated) return;
    update(message.id, { rated: rating });
    await sendFeedback(apiUrl, message.turnId, rating).catch(() => undefined);
  }

  return (
    <div className="chd-root">
      <header className="chd-header">
        <div>
          <div className="chd-title">{title}</div>
          <div className="chd-subtitle">
            {session ? `${session.user.name} · ${session.user.role} · ${session.user.college.toUpperCase()}` : "Guest"}
          </div>
        </div>
        {session ? (
          <button className="chd-link" onClick={signOut}>
            Log out
          </button>
        ) : (
          <select
            className="chd-login"
            value=""
            onChange={(e) => e.target.value && signIn(e.target.value)}
            aria-label="Log in as a demo user"
          >
            <option value="">Log in as…</option>
            {DEMO_USERS.map((u) => (
              <option key={u.id} value={u.id}>
                {u.label}
              </option>
            ))}
          </select>
        )}
      </header>
      {loginError && <div className="chd-error">{loginError}</div>}

      <div className="chd-messages" ref={listRef}>
        {messages.length === 0 && (
          <div className="chd-empty">
            <p>Ask about fees, exams, hostels, admissions or placements. Answers come with their source.</p>
            {SUGGESTIONS.map((s) => (
              <button key={s} className="chd-chip" onClick={() => ask(s)}>
                {s}
              </button>
            ))}
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`chd-row chd-${m.from}`}>
            <div className="chd-bubble">
              <div className="chd-text">{m.text || (m.streaming ? <span className="chd-typing">typing…</span> : "")}</div>
              {m.citations.length > 0 && (
                <ol className="chd-sources">
                  {m.citations.map((c) => (
                    <li key={c.n} value={c.n}>
                      {c.title} <span className="chd-date">· issued {c.issue_date}</span>
                    </li>
                  ))}
                </ol>
              )}
              {m.pendingActionId && (
                <button
                  className="chd-confirm"
                  disabled={busy}
                  onClick={() => {
                    const id = m.pendingActionId;
                    update(m.id, { pendingActionId: undefined });
                    ask("Yes, go ahead.", id);
                  }}
                >
                  Confirm
                </button>
              )}
              {m.from === "assistant" && m.turnId && !m.streaming && (
                <div className="chd-feedback">
                  <button aria-label="Helpful" className={m.rated === 1 ? "chd-on" : ""} onClick={() => rate(m, 1)}>
                    👍
                  </button>
                  <button aria-label="Not helpful" className={m.rated === -1 ? "chd-on" : ""} onClick={() => rate(m, -1)}>
                    👎
                  </button>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      <form
        className="chd-input"
        onSubmit={(e) => {
          e.preventDefault();
          ask(input);
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Type a message"
          aria-label="Your question"
          maxLength={2000}
        />
        <button type="submit" disabled={busy || !input.trim()} aria-label="Send">
          ➤
        </button>
      </form>
    </div>
  );
}
