'use client';

interface Props {
  onConfirm: (answer: 'yes' | 'no') => void;
}

export default function ConfirmBar({ onConfirm }: Props) {
  return (
    <div className="shrink-0 border-t border-yellow-800 bg-yellow-950/60 px-4 py-3">
      <div className="flex items-center justify-center gap-4 max-w-4xl mx-auto">
        <span className="text-yellow-400 text-sm font-medium">
          ⚠️ Waiting for your confirmation
        </span>
        <button
          onClick={() => onConfirm('yes')}
          className="bg-green-600 hover:bg-green-500 active:bg-green-700 text-white px-5 py-2 rounded-lg text-sm font-semibold transition-colors shadow"
        >
          ✓ Yes, proceed
        </button>
        <button
          onClick={() => onConfirm('no')}
          className="bg-red-700 hover:bg-red-600 active:bg-red-800 text-white px-5 py-2 rounded-lg text-sm font-semibold transition-colors shadow"
        >
          ✗ Cancel
        </button>
      </div>
    </div>
  );
}
