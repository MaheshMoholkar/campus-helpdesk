import { useEffect, useRef, useState } from "react";
import { type Citation, sendFeedback, streamChat } from "./api";

export type ChatWidgetProps = {
  apiUrl: string;
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

const SUGGESTIONS = ["What is the revaluation fee?", "hostel ka gate kitne baje band hota hai?", "When do 2027 admissions open?"];

let nextId = 0;
const newId = () => `m${++nextId}`;

/**
 * The public chat: full page, or wrapped as the embeddable widget for a college's website.
 * It is anonymous; logged-in users chat from the Assist panel inside CampusERP, which
 * carries their CampusERP session.
 */
export function ChatWidget({ apiUrl, title = "Campus Helpdesk" }: ChatWidgetProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [conversationId, setConversationId] = useState<string>();
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
          <div className="chd-subtitle">Answers from your college's notices, with sources</div>
        </div>
      </header>

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
