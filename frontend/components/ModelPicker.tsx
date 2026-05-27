'use client';

interface Props {
  models: string[];
  selected: string;
  onChange: (model: string) => void;
}

export default function ModelPicker({ models, selected, onChange }: Props) {
  if (models.length === 0) {
    return (
      <span className="text-xs text-gray-500 italic">No models — check backend URL</span>
    );
  }

  return (
    <select
      value={selected}
      onChange={(e) => onChange(e.target.value)}
      className="bg-gray-800 text-gray-200 text-xs rounded-lg px-2 py-1.5 border border-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-500 max-w-[200px] truncate"
    >
      {models.map((m) => (
        <option key={m} value={m}>
          {m}
        </option>
      ))}
    </select>
  );
}
