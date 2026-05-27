'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import MessageBubble from './MessageBubble';
import ConfirmBar from './ConfirmBar';
import SettingsModal from './SettingsModal';
import ModelPicker from './ModelPicker';

export interface Message {
  role: 'user' | 'assistant';
  content: string;
}

const STORAGE_KEY_URL = 'bot_backend_url';
const STORAGE_KEY_MODEL = 'bot_selected_model';
const STORAGE_KEY_HISTORY = 'bot_chat_history';

// Baked in at build time — set NEXT_PUBLIC_BACKEND_URL in Vercel env vars
const DEFAULT_BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? '';

function isConfirmationPrompt(text: string) {
  return text.includes('Reply **yes**') || (text.includes('⚠️ **') && text.includes('cancel'));
}

export default function ChatInterface() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [currentChunk, setCurrentChunk] = useState('');
  const [backendUrl, setBackendUrl] = useState('');
  const [model, setModel] = useState('');
  const [models, setModels] = useState<string[]>([]);
  const [showSettings, setShowSettings] = useState(false);
  const [awaitingConfirmation, setAwaitingConfirmation] = useState(false);
  const [error, setError] = useState('');

  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Load persisted state on mount
  useEffect(() => {
    const url = localStorage.getItem(STORAGE_KEY_URL) ?? DEFAULT_BACKEND_URL;
    const savedModel = localStorage.getItem(STORAGE_KEY_MODEL) ?? '';
    const savedHistory = localStorage.getItem(STORAGE_KEY_HISTORY);

    setBackendUrl(url);
    setModel(savedModel);

    if (savedHistory) {
      try { setMessages(JSON.parse(savedHistory)); } catch {}
    }

    if (!url) {
      setShowSettings(true);
    } else {
      fetchModels(url, savedModel);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-scroll to bottom
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, currentChunk]);

  async function fetchModels(url: string, preferredModel: string) {
    try {
      const resp = await fetch(`${url}/v1/models`, {
        signal: AbortSignal.timeout(5000),
        headers: { 'ngrok-skip-browser-warning': 'true' },
      });
      const data = await resp.json();
      const list: string[] = data.data?.map((m: { id: string }) => m.id) ?? [];
      setModels(list);
      if (list.length > 0 && !list.includes(preferredModel)) {
        setModel(list[0]);
        localStorage.setItem(STORAGE_KEY_MODEL, list[0]);
      }
    } catch {
      // Ollama might not be running yet; that's OK
    }
  }

  const sendMessage = useCallback(async (text: string) => {
    if (!text.trim() || streaming || !backendUrl) return;

    setError('');
    setAwaitingConfirmation(false);
    setInput('');

    const next: Message[] = [...messages, { role: 'user', content: text }];
    setMessages(next);
    setStreaming(true);
    setCurrentChunk('');

    let accumulated = '';

    try {
      const resp = await fetch(`${backendUrl}/v1/chat/completions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': 'true' },
        body: JSON.stringify({
          model,
          messages: next.map(m => ({ role: m.role, content: m.content })),
          stream: true,
        }),
      });

      if (!resp.ok) throw new Error(`Server returned ${resp.status} ${resp.statusText}`);
      if (!resp.body) throw new Error('Response has no body');

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop() ?? '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const raw = line.slice(6).trim();
          if (raw === '[DONE]') break;
          try {
            const parsed = JSON.parse(raw);
            const delta: string = parsed.choices?.[0]?.delta?.content ?? '';
            if (delta) {
              accumulated += delta;
              setCurrentChunk(accumulated);
            }
          } catch {}
        }
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      accumulated = `❌ **Connection error:** ${msg}\n\nMake sure the backend is running and the URL in Settings is correct.`;
      setError(msg);
    }

    const finalMessages: Message[] = [
      ...next,
      { role: 'assistant', content: accumulated },
    ];
    setMessages(finalMessages);
    setCurrentChunk('');
    setStreaming(false);

    if (isConfirmationPrompt(accumulated)) {
      setAwaitingConfirmation(true);
    }

    localStorage.setItem(STORAGE_KEY_HISTORY, JSON.stringify(finalMessages));
    textareaRef.current?.focus();
  }, [messages, streaming, backendUrl, model]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  };

  const saveSettings = (url: string, selectedModel: string) => {
    const clean = url.trim().replace(/\/+$/, '');
    setBackendUrl(clean);
    setModel(selectedModel);
    localStorage.setItem(STORAGE_KEY_URL, clean);
    localStorage.setItem(STORAGE_KEY_MODEL, selectedModel);
    setShowSettings(false);
    fetchModels(clean, selectedModel);
  };

  const clearHistory = () => {
    setMessages([]);
    setCurrentChunk('');
    setAwaitingConfirmation(false);
    setError('');
    localStorage.removeItem(STORAGE_KEY_HISTORY);
  };

  const isReady = !!backendUrl && !!model;

  return (
    <div className="flex flex-col h-screen">
      {/* ── Header ── */}
      <header className="flex items-center justify-between px-4 py-2.5 border-b border-gray-800 bg-gray-900 shrink-0">
        <div className="flex items-center gap-3">
          <span className="font-bold text-base tracking-tight">🤖 Universal Bot</span>
          <ModelPicker
            models={models}
            selected={model}
            onChange={(m) => {
              setModel(m);
              localStorage.setItem(STORAGE_KEY_MODEL, m);
            }}
          />
        </div>
        <div className="flex items-center gap-1.5">
          <button
            onClick={clearHistory}
            title="Clear conversation"
            className="text-xs text-gray-400 hover:text-gray-100 px-2 py-1 rounded hover:bg-gray-800 transition-colors"
          >
            Clear
          </button>
          <button
            onClick={() => setShowSettings(true)}
            title="Settings"
            className="text-gray-400 hover:text-gray-100 p-1.5 rounded hover:bg-gray-800 transition-colors text-lg leading-none"
          >
            ⚙
          </button>
        </div>
      </header>

      {/* ── Messages ── */}
      <div className="flex-1 overflow-y-auto px-4 py-6 space-y-6">
        {messages.length === 0 && !streaming && (
          <div className="flex flex-col items-center justify-center h-full text-center text-gray-500 pb-16">
            <div className="text-5xl mb-4 select-none">🤖</div>
            <p className="text-xl font-semibold text-gray-300">Universal Bot</p>
            <p className="text-sm mt-2 max-w-sm">
              Ask me anything. I can search the web, run Python, manage files,
              create presentations, and answer questions from your documents.
            </p>
            {!isReady && (
              <p className="mt-4 text-yellow-500 text-sm">
                ⚠️ Configure your backend URL in{' '}
                <button className="underline" onClick={() => setShowSettings(true)}>
                  Settings
                </button>
              </p>
            )}
          </div>
        )}

        {messages.map((msg, i) => (
          <MessageBubble key={i} message={msg} />
        ))}

        {(streaming || currentChunk) && (
          <MessageBubble
            message={{ role: 'assistant', content: currentChunk || '​' }}
            streaming={streaming && !currentChunk}
          />
        )}

        <div ref={bottomRef} />
      </div>

      {/* ── Confirmation bar ── */}
      {awaitingConfirmation && !streaming && (
        <ConfirmBar onConfirm={(answer) => sendMessage(answer)} />
      )}

      {/* ── Input ── */}
      <div className="shrink-0 border-t border-gray-800 bg-gray-900 px-4 py-3">
        {error && (
          <p className="text-xs text-red-400 mb-2 text-center">{error}</p>
        )}
        <div className="flex gap-2 items-end max-w-4xl mx-auto">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              isReady
                ? 'Message the bot… (Enter to send, Shift+Enter for newline)'
                : 'Set backend URL in Settings ⚙ first'
            }
            disabled={streaming || !isReady}
            rows={1}
            className="flex-1 bg-gray-800 text-gray-100 placeholder-gray-500 rounded-xl px-4 py-3 text-sm resize-none focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-40 max-h-48 overflow-auto"
          />
          <button
            onClick={() => sendMessage(input)}
            disabled={streaming || !input.trim() || !isReady}
            className="shrink-0 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:opacity-50 text-white rounded-xl px-4 py-3 font-medium transition-colors text-sm"
          >
            {streaming ? (
              <span className="inline-block animate-pulse">●●●</span>
            ) : (
              '↑ Send'
            )}
          </button>
        </div>
      </div>

      {showSettings && (
        <SettingsModal
          currentUrl={backendUrl}
          currentModel={model}
          models={models}
          onSave={saveSettings}
          onClose={() => setShowSettings(false)}
          onFetchModels={fetchModels}
        />
      )}
    </div>
  );
}
