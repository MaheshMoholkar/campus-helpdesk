// Talking to the helpdesk API (anonymous; no cookies are sent cross-origin).

export type Citation = { n: number; slug: string; title: string; issue_date: string };

export type ChatEvent =
  | { type: "meta"; conversation_id: string; turn_id: string; language: string; intent: string }
  | { type: "delta"; text: string }
  | { type: "citations"; items: Citation[] }
  | { type: "done"; outcome: string; turn_id: string; pending_action_id?: string }
  | { type: "error"; message: string };

export type ChatOptions = {
  question: string;
  conversationId?: string;
  pendingActionId?: string;
  signal?: AbortSignal;
};

/** POST /chat and yield its Server-Sent Events as they arrive. */
export async function* streamChat(apiUrl: string, options: ChatOptions): AsyncGenerator<ChatEvent> {
  const response = await fetch(`${apiUrl}/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      question: options.question,
      conversation_id: options.conversationId,
      pending_action_id: options.pendingActionId,
    }),
    signal: options.signal,
  });
  if (!response.ok || !response.body) {
    const detail = await response.text().catch(() => "");
    throw new Error(`chat failed (${response.status}) ${detail}`);
  }

  // EventSource only supports GET, so the stream is parsed by hand:
  // events are separated by a blank line; each has "event:" and "data:" lines.
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      let type = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) type = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (data) yield { type, ...JSON.parse(data) } as ChatEvent;
    }
  }
}

export async function sendFeedback(apiUrl: string, turnId: string, rating: 1 | -1): Promise<void> {
  await fetch(`${apiUrl}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ turn_id: turnId, rating }),
  });
}
