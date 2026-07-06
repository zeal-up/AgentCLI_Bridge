import React, { useState } from 'react';
import { Link, Route, Routes, useLocation, useNavigate } from 'react-router-dom';

import SessionsDrawer from './components/SessionsDrawer';
import Conversation from './pages/Conversation';
import VoiceProbe from './pages/VoiceProbe';
import NotFound from './pages/NotFound/NotFound';
import { versionLabel } from './version';

const APP_NAME = 'AgentCLI Bridge';

const Shell: React.FC = () => {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();

  const m = location.pathname.match(/^\/s\/(.+)$/);
  const currentSessionId = m ? decodeURIComponent(m[1]) : null;

  const selectSession = (id: string) => {
    navigate(`/s/${id}`);
    setDrawerOpen(false);
  };

  return (
    <div className="flex h-[100dvh] w-screen flex-col overflow-hidden bg-background text-foreground">
      <header className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2">
        <button
          onClick={() => setDrawerOpen((v) => !v)}
          className="rounded-md px-2 py-1 text-sm hover:bg-accent"
          aria-label="Sessions"
        >
          ☰ <span className="hidden sm:inline">Sessions</span>
        </button>
        <span className="break-all font-mono text-xs text-muted-foreground">
          {currentSessionId ?? APP_NAME}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-2 font-mono text-[10px] text-muted-foreground/60">
          <Link to="/voice-probe" className="rounded px-1.5 py-0.5 text-primary underline hover:bg-accent" title="Voice-input capability probe">🎤probe</Link>
          <span title="build version (compare across clients to detect stale cache)">
            {versionLabel()}
          </span>
        </span>
      </header>

      <main className="relative min-h-0 flex-1 overflow-hidden">
        <Routes>
          <Route path="/" element={<Placeholder />} />
          <Route path="/s/:sessionId" element={<Conversation />} />
          <Route path="/voice-probe" element={<VoiceProbe />} />
          <Route path="*" element={<NotFound />} />
        </Routes>

        {drawerOpen && (
          <div className="absolute inset-0 z-50 flex">
            <div className="h-full w-80 max-w-[85%] overflow-y-auto border-r border-border bg-background shadow-xl">
              <SessionsDrawer
                selected={currentSessionId}
                onSelect={selectSession}
                onClose={() => setDrawerOpen(false)}
              />
            </div>
            <div className="flex-1 bg-black/40" onClick={() => setDrawerOpen(false)} />
          </div>
        )}
      </main>
    </div>
  );
};

const Placeholder: React.FC = () => (
  <div className="flex h-full items-center justify-center p-6 text-center text-sm text-muted-foreground">
    <div>
      <p className="mb-1">Tap <span className="font-medium">☰ Sessions</span> to pick an agent session.</p>
      <p className="text-xs">Copilot · Claude · Codex — online sessions listed first.</p>
      <p className="mt-3 text-xs">
        <Link to="/voice-probe" className="text-primary underline">🎤 Voice-input capability probe</Link>
      </p>
    </div>
  </div>
);

export default function App() {
  return <Shell />;
}
