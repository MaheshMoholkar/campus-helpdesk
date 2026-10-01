// Embeddable build: one <script> tag adds a floating "assist" button to any page.
//
//   <script src="campus-helpdesk-widget.js"
//           data-api-url="https://helpdesk.example/api"
//           data-student-api-url="https://erp.example"></script>
//
// or, for full control:  CampusHelpdesk.mount(element, { apiUrl, studentApiUrl })

import { useState } from "react";
import { createRoot } from "react-dom/client";
import { ChatWidget, type ChatWidgetProps } from "./ChatWidget";
import css from "./styles.css?inline";

function Launcher(props: ChatWidgetProps) {
  const [open, setOpen] = useState(false);
  return (
    <>
      {open && (
        <div className="chd-panel">
          <ChatWidget {...props} />
        </div>
      )}
      <button className="chd-launcher" aria-label={open ? "Close helpdesk" : "Open helpdesk"} onClick={() => setOpen(!open)}>
        {open ? "×" : "💬"}
      </button>
    </>
  );
}

/** Mount into `host`. A shadow root keeps the host page's CSS and ours apart. */
export function mount(host: HTMLElement, props: ChatWidgetProps) {
  const shadow = host.shadowRoot ?? host.attachShadow({ mode: "open" });
  const style = document.createElement("style");
  style.textContent = css;
  const container = document.createElement("div");
  shadow.replaceChildren(style, container);
  const root = createRoot(container);
  root.render(<Launcher {...props} />);
  return () => root.unmount();
}

// Auto-mount when loaded through a script tag carrying data attributes.
const script = document.currentScript as HTMLScriptElement | null;
if (script?.dataset.apiUrl) {
  const host = document.createElement("div");
  host.id = "campus-helpdesk-widget";
  document.body.appendChild(host);
  mount(host, {
    apiUrl: script.dataset.apiUrl,
    studentApiUrl: script.dataset.studentApiUrl ?? script.dataset.apiUrl,
    title: script.dataset.title,
  });
}
