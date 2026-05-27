'use client';

interface ProgressData {
  type: 'progress';
  current: number;
  total: number;
  label: string;
}

interface SummaryData {
  type: 'summary';
  title: string;
  stats: [string, string][];
}

interface AlertData {
  type: 'alert';
  level: 'info' | 'warning' | 'error' | 'success';
  message: string;
}

interface ActivityData {
  type: 'activity';
  icon: string;
  label: string;
}

type UiData = ProgressData | SummaryData | AlertData | ActivityData;

export default function UiBlock({ raw }: { raw: string }) {
  let data: UiData;
  try {
    data = JSON.parse(raw.trim());
  } catch {
    return null;
  }

  if (data.type === 'progress') {
    const pct = data.total > 0 ? Math.round((data.current / data.total) * 100) : 0;
    return (
      <div className="my-2 bg-gray-700/50 rounded-xl p-3 border border-gray-600/50">
        <div className="flex justify-between items-center text-xs text-gray-300 mb-2">
          <span className="truncate max-w-[70%] font-mono">📂 {data.label}</span>
          <span className="shrink-0 ml-2 tabular-nums text-gray-400">
            {data.current.toLocaleString()}/{data.total.toLocaleString()} · {pct}%
          </span>
        </div>
        <div className="h-1.5 bg-gray-600 rounded-full overflow-hidden">
          <div
            className="h-full bg-blue-500 rounded-full transition-all duration-500"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>
    );
  }

  if (data.type === 'summary') {
    return (
      <div className="my-3 bg-gray-700/40 rounded-xl p-4 border border-gray-600/50">
        <div className="text-sm font-semibold text-green-400 mb-3">✅ {data.title}</div>
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
          {data.stats.map(([label, value]) => (
            <div key={label} className="bg-gray-800/80 rounded-lg p-2.5 text-center">
              <div className="text-lg font-bold text-white tabular-nums">{value}</div>
              <div className="text-xs text-gray-400 mt-0.5">{label}</div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (data.type === 'activity') {
    return (
      <div className="my-1.5 flex items-center gap-2 text-xs text-gray-400 bg-gray-700/30 rounded-lg px-3 py-2 border border-gray-600/30">
        <span className="text-base leading-none">{data.icon}</span>
        <span className="truncate font-mono">{data.label}</span>
        <span className="ml-auto shrink-0 flex gap-0.5">
          <span className="w-1 h-1 rounded-full bg-blue-400 animate-bounce" style={{ animationDelay: '0ms' }} />
          <span className="w-1 h-1 rounded-full bg-blue-400 animate-bounce" style={{ animationDelay: '150ms' }} />
          <span className="w-1 h-1 rounded-full bg-blue-400 animate-bounce" style={{ animationDelay: '300ms' }} />
        </span>
      </div>
    );
  }

  if (data.type === 'alert') {
    const styles: Record<string, string> = {
      info: 'bg-blue-900/30 border-blue-700/50 text-blue-300',
      warning: 'bg-yellow-900/30 border-yellow-700/50 text-yellow-300',
      error: 'bg-red-900/30 border-red-700/50 text-red-300',
      success: 'bg-green-900/30 border-green-700/50 text-green-300',
    };
    return (
      <div className={`my-2 rounded-xl p-3 border text-sm ${styles[data.level] ?? styles.info}`}>
        {data.message}
      </div>
    );
  }

  return null;
}
