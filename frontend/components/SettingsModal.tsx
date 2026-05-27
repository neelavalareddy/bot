'use client';

import { useState } from 'react';

interface Props {
  currentUrl: string;
  currentModel: string;
  models: string[];
  onSave: (url: string, model: string) => void;
  onClose: () => void;
  onFetchModels: (url: string, model: string) => void;
}

export default function SettingsModal({
  currentUrl,
  currentModel,
  models,
  onSave,
  onClose,
  onFetchModels,
}: Props) {
  const [url, setUrl] = useState(currentUrl);
  const [model, setModel] = useState(currentModel);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);

  const testConnection = async () => {
    if (!url.trim()) return;
    setTesting(true);
    setTestResult(null);
    try {
      const clean = url.trim().replace(/\/+$/, '');
      const resp = await fetch(`${clean}/v1/models`, {
        signal: AbortSignal.timeout(5000),
        headers: { 'ngrok-skip-browser-warning': 'true' },
      });
      const data = await resp.json();
      const count = data.data?.length ?? 0;
      setTestResult(`✅ Connected — ${count} model(s) available`);
      onFetchModels(clean, model);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setTestResult(`❌ Failed: ${msg}`);
    } finally {
      setTesting(false);
    }
  };

  const handleSave = () => {
    if (!url.trim()) return;
    onSave(url.trim().replace(/\/+$/, ''), model);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
      <div className="bg-gray-900 border border-gray-700 rounded-2xl p-6 w-full max-w-lg shadow-2xl">
        <h2 className="text-lg font-bold mb-1">Settings</h2>
        <p className="text-xs text-gray-400 mb-5">
          Configure the connection to your local Universal Bot backend.
        </p>

        {/* Backend URL */}
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-300 mb-1.5">
            Backend URL
          </label>
          <div className="flex gap-2">
            <input
              value={url}
              onChange={(e) => { setUrl(e.target.value); setTestResult(null); }}
              placeholder="http://localhost:8080"
              className="flex-1 bg-gray-800 text-gray-100 placeholder-gray-500 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-blue-500 border border-gray-700"
            />
            <button
              onClick={testConnection}
              disabled={testing || !url.trim()}
              className="shrink-0 bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-gray-200 px-3 py-2 rounded-lg text-sm transition-colors"
            >
              {testing ? '…' : 'Test'}
            </button>
          </div>

          {testResult && (
            <p className="mt-1.5 text-xs text-gray-400">{testResult}</p>
          )}

          <div className="mt-2 text-xs text-gray-500 space-y-1">
            <p>• Running locally? Use <code className="text-gray-300">http://localhost:8080</code></p>
            <p>
              • Expose to the internet:{' '}
              <code className="text-gray-300">ngrok http 8080</code>{' '}
              then paste the HTTPS URL here.
            </p>
            <p>
              • Or use{' '}
              <a
                href="https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/"
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-400 hover:underline"
              >
                Cloudflare Tunnel
              </a>{' '}
              for a permanent URL.
            </p>
          </div>
        </div>

        {/* Model picker */}
        {models.length > 0 && (
          <div className="mb-5">
            <label className="block text-sm font-medium text-gray-300 mb-1.5">
              Default Model
            </label>
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="w-full bg-gray-800 text-gray-100 rounded-lg px-3 py-2 text-sm border border-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-500"
            >
              {models.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </div>
        )}

        <div className="flex gap-3">
          <button
            onClick={handleSave}
            disabled={!url.trim()}
            className="flex-1 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white py-2 rounded-lg text-sm font-semibold transition-colors"
          >
            Save
          </button>
          <button
            onClick={onClose}
            className="flex-1 bg-gray-800 hover:bg-gray-700 text-gray-300 py-2 rounded-lg text-sm font-semibold transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
