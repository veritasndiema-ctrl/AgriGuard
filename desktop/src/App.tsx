import { useState } from "react";
import Dashboard from "./screens/Dashboard";

export default function App() {
  const [apiBase, setApiBase] = useState("http://localhost:8000");

  return (
    <div style={{ fontFamily: "system-ui, sans-serif", minHeight: "100vh", background: "#f5f7f5" }}>
      <header
        style={{
          background: "#1b5e20",
          color: "white",
          padding: "12px 24px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 22 }}>🌾</span>
          <strong style={{ fontSize: 18 }}>AgriGuard Desktop</strong>
        </div>
        <label style={{ fontSize: 13 }}>
          API&nbsp;
          <input
            value={apiBase}
            onChange={(e) => setApiBase(e.target.value)}
            style={{
              padding: "4px 8px",
              borderRadius: 4,
              border: "none",
              width: 220,
            }}
          />
        </label>
      </header>
      <main style={{ padding: 24 }}>
        <Dashboard apiBase={apiBase} />
      </main>
    </div>
  );
}
