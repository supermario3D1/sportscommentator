import { UploadCloud, X } from 'lucide-react';
import { bytesToSize } from '../lib/format';

interface FileDropProps {
  label: string;
  description: string;
  accept: string;
  file: File | null;
  onChange: (file: File | null) => void;
}

export function FileDrop({ label, description, accept, file, onChange }: FileDropProps) {
  return (
    <label className="file-drop">
      <input
        type="file"
        accept={accept}
        onChange={(event) => onChange(event.target.files?.[0] ?? null)}
        aria-label={label}
      />
      <span className="file-drop__icon" aria-hidden="true">
        <UploadCloud size={28} />
      </span>
      <span className="file-drop__content">
        <strong>{label}</strong>
        <small>{description}</small>
        {file ? (
          <span className="file-pill">
            <span>
              {file.name} · {bytesToSize(file.size)}
            </span>
            <button
              type="button"
              className="icon-button"
              onClick={(event) => {
                event.preventDefault();
                onChange(null);
              }}
              aria-label={`Remove ${file.name}`}
            >
              <X size={16} />
            </button>
          </span>
        ) : (
          <span className="file-drop__hint">Click to browse or drop a file</span>
        )}
      </span>
    </label>
  );
}
