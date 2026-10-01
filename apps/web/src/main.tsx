import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ChatWidget } from "./ChatWidget";
import "./styles.css";

const apiUrl = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const studentApiUrl = import.meta.env.VITE_STUDENT_API_URL ?? "http://localhost:8001";

document.body.style.margin = "0";
const root = document.getElementById("root")!;
root.style.height = "100vh";

createRoot(root).render(
  <StrictMode>
    <div style={{ maxWidth: 720, height: "100%", margin: "0 auto" }}>
      <ChatWidget apiUrl={apiUrl} studentApiUrl={studentApiUrl} />
    </div>
  </StrictMode>,
);
